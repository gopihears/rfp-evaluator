"""Real-browser tests (Playwright + Chromium) against a live Streamlit server.

The server runs with an offline LLM (RFP_FAKE_LLM), so these tests are deterministic, free and
need no network. Skip them with: pytest -m "not browser"
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.browser

ROOT = Path(__file__).resolve().parents[2]
LONG = 60_000  # ms: generous because every click is a full Streamlit rerun


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class Server:
    def __init__(self, mode: str, tmp: Path, prepare: Callable[[Any], None] | None = None) -> None:
        self.port = _free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        db_path = tmp / f"{mode}.db"
        if prepare is not None:  # build a database with custom criteria before the app starts
            from core import db
            from core.seed import seed_criteria

            conn = db.connect(str(db_path))
            db.init_db(conn)
            seed_criteria(conn)
            prepare(conn)
            conn.close()
        env = {
            **os.environ,
            "RFP_FAKE_LLM": mode,
            "RFP_DB_PATH": str(db_path),
            "PYTHONIOENCODING": "utf-8",
        }
        self.proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "streamlit",
                "run",
                "app.py",
                "--server.port",
                str(self.port),
                "--server.headless",
                "true",
                "--browser.gatherUsageStats",
                "false",
                "--server.address",
                "127.0.0.1",
            ],
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"{self.url}/_stcore/health", timeout=2) as r:
                    if r.status == 200:
                        return
            except OSError:
                time.sleep(0.5)
        self.stop()
        raise RuntimeError("Streamlit did not start")

    def stop(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()


@pytest.fixture(scope="module")
def demo_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Server]:
    server = Server("1", tmp_path_factory.mktemp("demo"))
    yield server
    server.stop()


@pytest.fixture(scope="module")
def knockout_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Server]:
    """Own database: Security & Compliance is a knock-out with minimum score 5."""
    from core import db

    def prepare(conn: Any) -> None:
        crit = [
            c.model_copy(update={"is_mandatory": True, "min_score": 5})
            if c.criterion_id == 4
            else c
            for c in db.load_criteria(conn)
        ]
        db.save_criteria(conn, crit)

    server = Server("1", tmp_path_factory.mktemp("knockout"), prepare)
    yield server
    server.stop()


@pytest.fixture(scope="module")
def formula_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Server]:
    """Own database: adds Total Price and Delivery Time as calculated criteria."""
    from tests.run_helpers import with_extra_criteria

    server = Server("1", tmp_path_factory.mktemp("formula"), with_extra_criteria)
    yield server
    server.stop()


@pytest.fixture(scope="module")
def keyed_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Server]:
    server = Server("keyed", tmp_path_factory.mktemp("keyed"))
    yield server
    server.stop()


@pytest.fixture
def app(page: Page, demo_server: Server) -> Page:
    page.set_default_timeout(LONG)
    page.goto(demo_server.url)
    return page


def btn(page: Page, name: str) -> Any:
    return page.get_by_role("button", name=name, exact=True)


def start_demo(page: Page) -> None:
    btn(page, "Start demo session").click()
    expect(page.get_by_role("heading", name="Evaluation criteria")).to_be_visible()


def go_to_suppliers(page: Page) -> None:
    start_demo(page)
    btn(page, "Continue to suppliers").click()
    expect(page.get_by_role("heading", name="Supplier proposals")).to_be_visible()


def run_demo_evaluation(page: Page, with_error_sample: bool = False) -> None:
    go_to_suppliers(page)
    btn(page, "Load demo data").click()
    expect(page.get_by_text("apex_systems.pdf")).to_be_visible()
    if with_error_sample:
        btn(page, "Add error sample").click()
        expect(page.get_by_text("corrupt_co.pdf")).to_be_visible()
    btn(page, "Evaluate suppliers").click()
    expect(page.get_by_role("tab", name="Leaderboard")).to_be_visible()


def leaderboard_names(page: Page, expected: int = 4) -> list[str]:
    rows = page.locator("[class*='st-key-lb_row_']")
    expect(rows).to_have_count(expected)  # rows render progressively after the tab appears
    return [rows.nth(i).locator("strong").first.inner_text() for i in range(rows.count())]


# ------------------------------------------------------------------ gate and navigation


def test_e2e_fresh_load_shows_connect_and_locks_steps(app: Page) -> None:
    headline = "Evaluate supplier proposals with explainable AI scoring"
    expect(app.get_by_role("heading", name=headline)).to_be_visible()
    for step in ("2  Criteria", "3  Suppliers", "4  Results"):
        expect(app.get_by_role("button", name=step)).to_be_disabled()
    expect(app.get_by_text("Demo mode")).to_be_visible()


def test_e2e_refresh_resets_session(app: Page) -> None:
    start_demo(app)
    app.reload()
    expect(btn(app, "Start demo session")).to_be_visible()
    expect(app.get_by_role("button", name="2  Criteria")).to_be_disabled()


def test_e2e_disconnect_returns_to_gate(app: Page) -> None:
    start_demo(app)
    btn(app, "Disconnect").first.click()
    expect(btn(app, "Start demo session")).to_be_visible()
    expect(app.get_by_role("button", name="2  Criteria")).to_be_disabled()


def test_e2e_invalid_then_valid_key(page: Page, keyed_server: Server) -> None:
    page.set_default_timeout(LONG)
    page.goto(keyed_server.url)
    key_input = page.locator("input[type='password']")
    key_input.fill("sk-wrong-key-123456789")
    btn(page, "Connect").click()
    expect(page.get_by_text("The API key was rejected by OpenAI.")).to_be_visible()
    expect(page.get_by_text("Traceback")).to_have_count(0)
    expect(page.get_by_role("button", name="2  Criteria")).to_be_disabled()
    page.locator("input[type='password']").fill("sk-test-valid-0123456789abcd")
    btn(page, "Connect").click()
    expect(page.get_by_role("heading", name="Evaluation criteria")).to_be_visible()
    chip = page.locator(".rfp-chip")
    expect(chip).to_contain_text("gpt-4o")
    expect(chip).to_contain_text("abcd")
    assert "0123456789" not in page.content()  # the key is never echoed back into the page


# ------------------------------------------------------------------------------ happy path


def test_e2e_criteria_screen(app: Page) -> None:
    start_demo(app)
    expect(app.get_by_text("Active weights total:")).to_contain_text("100")
    expect(app.get_by_text("Ready to save")).to_be_visible()
    expect(btn(app, "Save criteria")).to_be_enabled()


def test_e2e_full_run_leaderboard_and_demo_data(app: Page) -> None:
    go_to_suppliers(app)
    expect(btn(app, "Evaluate suppliers")).to_be_disabled()  # nothing uploaded yet
    btn(app, "Load demo data").click()
    for name in ("apex_systems.pdf", "brightpath_tech.pdf", "nexaworks.pdf", "orbit_digital.pdf"):
        expect(app.get_by_text(name)).to_be_visible()
    btn(app, "Evaluate suppliers").click()
    expect(app.get_by_role("tab", name="Leaderboard")).to_be_visible()
    names = leaderboard_names(app)
    assert len(names) == 4 and set(names[:2]) == {"Apex Systems", "NexaWorks"}
    assert names[2:] == ["Orbit Digital", "BrightPath Tech"]
    expect(app.locator(".rfp-pod")).to_have_count(3)


def test_e2e_scorecard_dialog(app: Page) -> None:
    run_demo_evaluation(app)
    app.locator("[class*='st-key-open_']").first.get_by_role("button").click()
    dialog = app.get_by_role("dialog")
    expect(dialog.get_by_text("Evidence and reasoning")).to_be_visible()
    expect(dialog.get_by_text("Absolute score")).to_be_visible()
    dialog.get_by_text("Technical Capability - ").first.click()
    expect(dialog.get_by_text("Verified in document").first).to_be_visible()
    app.keyboard.press("Escape")
    expect(dialog).to_have_count(0)


def test_e2e_compare_tab_toggle(app: Page) -> None:
    run_demo_evaluation(app)
    app.get_by_role("tab", name="Compare").click()
    expect(app.locator(".js-plotly-plot").first).to_be_visible()
    app.get_by_text("Score vs maximum").click()
    expect(app.locator(".js-plotly-plot").first).to_be_visible()


def test_e2e_run_details_and_download(app: Page, tmp_path: Path) -> None:
    run_demo_evaluation(app)
    app.get_by_role("tab", name="Run details").click()
    expect(app.get_by_text("RFP_RUN_ID")).to_be_visible()
    expect(app.get_by_text("Ranking robustness")).to_be_visible()
    expect(app.get_by_text("Tie-break explanations")).to_be_visible()
    with app.expect_download() as dl:
        btn(app, "Download full result (JSON)").click()
    path = tmp_path / "run.json"
    dl.value.save_as(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["schema_version"] == "1.0" and data["status"] == "COMPLETED"
    assert [r["supplier_name"] for r in data["leaderboard"]][2:] == [
        "Orbit Digital",
        "BrightPath Tech",
    ]
    assert "sk-" not in path.read_text(encoding="utf-8")
    assert data["criteria_snapshot"] and data["tie_break_explanations"]


def test_e2e_error_case_listed_as_failed(app: Page) -> None:
    run_demo_evaluation(app, with_error_sample=True)
    expect(app.get_by_text("Partial", exact=True).first).to_be_visible()
    expect(app.get_by_text("Corrupt Co")).to_be_visible()
    expect(app.get_by_text("probably scanned images")).to_be_visible()
    assert len(leaderboard_names(app)) == 4  # the readable four are still ranked


def test_e2e_history_reopens_previous_run(app: Page) -> None:
    run_demo_evaluation(app)
    btn(app, "New evaluation").click()
    expect(app.get_by_role("heading", name="Supplier proposals")).to_be_visible()
    app.get_by_test_id("stExpandSidebarButton").click()
    app.locator("[class*='st-key-hist_']").first.get_by_role("button").click()
    expect(app.get_by_role("tab", name="Leaderboard")).to_be_visible()
    assert len(leaderboard_names(app)) == 4


# ------------------------------------------------------------------- layout and a11y


def test_e2e_mobile_viewport_has_no_horizontal_scroll(page: Page, demo_server: Server) -> None:
    page.set_viewport_size({"width": 390, "height": 844})
    page.set_default_timeout(LONG)
    page.goto(demo_server.url)
    expect(btn(page, "Start demo session")).to_be_visible()

    def overflow() -> int:
        return int(page.evaluate("document.documentElement.scrollWidth - window.innerWidth"))

    assert overflow() <= 1
    start_demo(page)
    assert overflow() <= 1
    page.screenshot(path=str(ROOT / "test-results" / "mobile_criteria.png"), full_page=True)


def test_e2e_no_critical_accessibility_violations(app: Page) -> None:
    from axe_playwright_python.sync_playwright import Axe

    def critical(page: Page) -> list[str]:
        result = Axe().run(page, options={"runOnly": ["wcag2a", "wcag2aa"]})
        return [
            f"{v['id']}: {v['help']}"
            for v in result.response["violations"]
            if v["impact"] == "critical"
        ]

    assert critical(app) == []
    run_demo_evaluation(app)
    assert critical(app) == []


# ============================================================== Should-tier features


def open_scorecard(page: Page, rank_index: int = 0) -> Any:
    page.locator("[class*='st-key-open_']").nth(rank_index).get_by_role("button").click()
    dialog = page.get_by_role("dialog")
    expect(dialog.get_by_text("Evidence and reasoning")).to_be_visible()
    return dialog


def test_e2e_override_changes_score_ranking_and_audit_trail(app: Page) -> None:
    run_demo_evaluation(app)
    before = leaderboard_names(app)
    last = len(before) - 1
    dialog = open_scorecard(app, last)  # the lowest-ranked supplier
    dialog.get_by_text("Technical Capability - ").first.click()
    form = dialog.locator("[class*='st-key-ovr_score_']").first
    form.get_by_role("spinbutton").fill("10")
    dialog.get_by_role("button", name="Apply change").first.click()
    expect(dialog.get_by_text("Give a short reason").first).to_be_visible()  # reason is required
    dialog.locator("[class*='st-key-ovr_reason_']").first.get_by_role("textbox").fill(
        "Verified architecture review on site"
    )
    dialog.locator("[class*='st-key-ovr_score_']").first.get_by_role("spinbutton").fill("10")
    dialog.get_by_role("button", name="Apply change").first.click()
    expect(app.get_by_text("Score updated.")).to_be_visible()
    expect(app.get_by_text("edited", exact=True).first).to_be_visible()
    app.get_by_role("tab", name="Run details").click()
    expect(app.get_by_text("Analyst overrides (audit trail)")).to_be_visible()
    expect(app.get_by_text("Verified architecture review on site").first).to_be_attached()


def test_e2e_override_is_kept_in_history_and_can_be_reverted(app: Page) -> None:
    run_demo_evaluation(app)
    dialog = open_scorecard(app, 1)
    dialog.get_by_text("Technical Capability - ").first.click()
    dialog.locator("[class*='st-key-ovr_reason_']").first.get_by_role("textbox").fill(
        "Initial change"
    )
    dialog.locator("[class*='st-key-ovr_score_']").first.get_by_role("spinbutton").fill("1")
    dialog.get_by_role("button", name="Apply change").first.click()
    expect(app.get_by_text("Score updated.")).to_be_visible()
    dialog = open_scorecard(app, 0)
    dialog.get_by_text("Technical Capability - ").first.click()
    expect(dialog.get_by_text("Analyst override").first).to_be_visible()


def test_e2e_knockout_disqualifies_below_minimum(page: Page, knockout_server: Server) -> None:
    page.set_default_timeout(LONG)
    page.goto(knockout_server.url)
    go_to_suppliers(page)
    btn(page, "Load demo data").click()
    expect(page.get_by_text("apex_systems.pdf")).to_be_visible()
    page.get_by_text("Advanced", exact=True).click()
    expect(page.get_by_text("Apply knock-out rules")).to_be_visible()
    btn(page, "Evaluate suppliers").click()
    expect(page.get_by_role("tab", name="Leaderboard")).to_be_visible()
    assert len(leaderboard_names(page, expected=2)) == 2
    expect(page.get_by_text("Disqualified", exact=True)).to_have_count(2)
    expect(page.get_by_text("minimum 5").first).to_be_visible()
    page.get_by_role("tab", name="Run details").click()
    expect(page.get_by_text("knock-out rules: on")).to_be_visible()


def test_e2e_long_document_uses_passage_search(app: Page) -> None:
    go_to_suppliers(app)
    btn(app, "Load demo data").click()
    btn(app, "Add long sample (~90 pages)").click()
    expect(app.get_by_text("long_apex_80p.pdf")).to_be_visible()
    expect(app.get_by_text("Long: passages are searched")).to_be_visible()
    btn(app, "Evaluate suppliers").click()
    expect(app.get_by_role("tab", name="Leaderboard")).to_be_visible()
    names = leaderboard_names(app, expected=5)
    assert "Apex Systems (long version)" in names
    idx = names.index("Apex Systems (long version)")
    dialog = open_scorecard(app, idx)
    expect(dialog.get_by_text("Long document: each criterion was scored")).to_be_visible()
    dialog.get_by_text("Technical Capability - ").first.click()
    expect(dialog.get_by_text("Passages from pages").first).to_be_visible()


def test_e2e_formula_criteria_show_figures_and_calculated_scores(
    page: Page, formula_server: Server
) -> None:
    page.set_default_timeout(LONG)
    page.goto(formula_server.url)
    go_to_suppliers(page)
    btn(page, "Load demo data").click()
    expect(page.get_by_text("apex_systems.pdf")).to_be_visible()
    page.get_by_text("Advanced", exact=True).click()
    expect(page.get_by_text("calculated from figures")).to_be_visible()
    btn(page, "Evaluate suppliers").click()
    expect(page.get_by_role("tab", name="Leaderboard")).to_be_visible()
    names = leaderboard_names(page)
    dialog = open_scorecard(page, names.index("BrightPath Tech"))
    expect(dialog.get_by_text("Figures read from the proposal")).to_be_visible()
    dialog.get_by_text("Total Price - ").first.click()
    expect(dialog.get_by_text("Calculated from a figure").first).to_be_visible()
    expect(dialog.get_by_text("lowest submitted is 690,000").first).to_be_visible()
    page.keyboard.press("Escape")
    page.get_by_role("tab", name="Run details").click()
    expect(page.get_by_text("Calculated from a figure").first).to_be_attached()  # grid cell


def test_e2e_criteria_screen_formula_setup_is_validated(page: Page, demo_server: Server) -> None:
    page.set_default_timeout(LONG)
    page.goto(demo_server.url)
    start_demo(page)
    page.get_by_text("Scoring method and knock-out rules (optional)").click()
    page.locator("[class*='st-key-adv_type_3']").get_by_text("Calculated from a figure").click()
    expect(page.get_by_text("describe the figure").first).to_be_visible()
    expect(btn(page, "Save criteria")).to_be_disabled()

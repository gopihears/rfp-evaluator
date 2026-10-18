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
    def __init__(self, mode: str, tmp: Path) -> None:
        self.port = _free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        env = {**os.environ, "RFP_FAKE_LLM": mode, "RFP_DB_PATH": str(tmp / f"{mode}.db"),
               "PYTHONIOENCODING": "utf-8"}
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "streamlit", "run", "app.py", "--server.port", str(self.port),
             "--server.headless", "true", "--browser.gatherUsageStats", "false",
             "--server.address", "127.0.0.1"],
            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
    assert [r["supplier_name"] for r in data["leaderboard"]][2:] == ["Orbit Digital",
                                                                      "BrightPath Tech"]
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
        return [f"{v['id']}: {v['help']}" for v in result.response["violations"]
                if v["impact"] == "critical"]

    assert critical(app) == []
    run_demo_evaluation(app)
    assert critical(app) == []


_ = Callable  # imported for typing helpers in future tests

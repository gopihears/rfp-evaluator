"""Streamlit flow tests with AppTest (no browser). Real-browser tests live in test_browser.py."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from core.tools.fake_provider import FakeProvider
from core.tools.provider_factory import DEMO_MODEL

APP = Path(__file__).resolve().parents[2] / "app.py"


def new_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, demo: bool = True) -> AppTest:
    monkeypatch.setenv("RFP_DB_PATH", str(tmp_path / "app.db"))
    if demo:
        monkeypatch.setenv("RFP_FAKE_LLM", "1")
    else:
        monkeypatch.delenv("RFP_FAKE_LLM", raising=False)
    at = AppTest.from_file(str(APP), default_timeout=90)
    at.run()
    assert not at.exception, at.exception
    return at


def click(at: AppTest, key: str) -> None:
    at.button(key=key).click().run()
    assert not at.exception, at.exception


def connect_button(at: AppTest):  # type: ignore[no-untyped-def]
    return next(b for b in at.button if b.label == "Connect")


@pytest.fixture
def demo_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[AppTest]:
    yield new_app(tmp_path, monkeypatch)


def test_at01_fresh_load_locks_later_steps(demo_app: AppTest) -> None:
    assert demo_app.session_state["step"] == "connect"
    assert not demo_app.button(key="nav_connect").disabled
    for step in ("criteria", "suppliers", "results"):
        assert demo_app.button(key=f"nav_{step}").disabled
    assert any("Demo mode" in w.value for w in demo_app.warning)


def test_at03_demo_connect_advances_and_shows_chip(demo_app: AppTest) -> None:
    click(demo_app, "start_demo")
    assert demo_app.session_state["step"] == "criteria"
    assert demo_app.session_state["model"] == DEMO_MODEL
    assert not demo_app.button(key="nav_suppliers").disabled
    assert demo_app.button(key="nav_results").disabled  # no results yet
    assert any("Demo mode" in m.value for m in demo_app.markdown)


def test_at04_disconnect_clears_everything(demo_app: AppTest) -> None:
    click(demo_app, "start_demo")
    click(demo_app, "disconnect_top")
    for key in ("provider", "models", "key_hint", "outcome", "viewing_export"):
        assert key not in demo_app.session_state
    assert demo_app.session_state["step"] == "connect"
    assert demo_app.button(key="nav_criteria").disabled


def test_criteria_screen_shows_seed_and_continues(demo_app: AppTest) -> None:
    click(demo_app, "start_demo")
    assert any("Active weights total" in m.value and "100" in m.value for m in demo_app.markdown)
    assert not demo_app.button(key="save_crit").disabled
    click(demo_app, "to_suppliers")
    assert demo_app.session_state["step"] == "suppliers"


def test_suppliers_screen_validation_and_demo_data(demo_app: AppTest) -> None:
    click(demo_app, "start_demo")
    click(demo_app, "to_suppliers")
    assert demo_app.button(key="evaluate").disabled  # nothing uploaded yet
    click(demo_app, "load_demo")
    assert len(demo_app.session_state["uploads"]) == 4
    assert not demo_app.button(key="evaluate").disabled
    entry = demo_app.session_state["uploads"][0]
    demo_app.text_input(key=f"name_{entry.id}").set_value("").run()
    assert demo_app.button(key="evaluate").disabled  # empty name blocks the run
    assert any("supplier name" in c.value for c in demo_app.caption)


def test_full_demo_run_reaches_results(demo_app: AppTest) -> None:
    click(demo_app, "start_demo")
    click(demo_app, "to_suppliers")
    click(demo_app, "load_demo")
    click(demo_app, "add_error")
    assert len(demo_app.session_state["uploads"]) == 5
    click(demo_app, "evaluate")
    assert demo_app.session_state["step"] == "results"
    export = demo_app.session_state["viewing_export"]
    assert export["status"] == "PARTIAL" and len(export["leaderboard"]) == 4
    assert not demo_app.button(key="nav_results").disabled
    assert any("Corrupt Co" in m.value for m in demo_app.markdown)  # failed supplier is listed
    assert any(b.key and b.key.startswith("open_") for b in demo_app.button)
    assert any(b.key and b.key.startswith("hist_") for b in demo_app.button)


def test_history_reopens_a_past_run(demo_app: AppTest) -> None:
    click(demo_app, "start_demo")
    click(demo_app, "to_suppliers")
    click(demo_app, "load_demo")
    click(demo_app, "evaluate")
    run_id = demo_app.session_state["viewing_export"]["rfp_run_id"]
    click(demo_app, "new_eval")
    assert demo_app.session_state["step"] == "suppliers"
    demo_app.session_state["viewing_export"] = None
    click(demo_app, f"hist_{run_id}")
    assert demo_app.session_state["step"] == "results"
    assert demo_app.session_state["viewing_export"]["rfp_run_id"] == run_id


def test_at02_invalid_key_stays_locked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ui.connect.make_provider", lambda key: FakeProvider(valid_key=False))
    at = new_app(tmp_path, monkeypatch, demo=False)
    at.text_input[0].set_value("sk-not-a-real-key-123456")  # form: submit in the same run
    connect_button(at).click().run()
    assert at.session_state["step"] == "connect" and "provider" not in at.session_state
    assert any("rejected" in e.value for e in at.error)
    assert at.button(key="nav_criteria").disabled


def test_at02_empty_key_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    at = new_app(tmp_path, monkeypatch, demo=False)
    connect_button(at).click().run()
    assert any("Enter your API key" in e.value for e in at.error)


def test_at03_valid_key_connects_and_never_stores_plaintext(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "sk-test-VALIDKEYVALIDKEY123456"
    monkeypatch.setattr("ui.connect.make_provider",
                        lambda key: FakeProvider(models=["gpt-4o", "gpt-4o-mini", "whisper-1"]))
    at = new_app(tmp_path, monkeypatch, demo=False)
    at.text_input[0].set_value(secret)  # form: submit in the same run
    connect_button(at).click().run()
    assert at.session_state["step"] == "criteria"
    assert at.session_state["models"] == ["gpt-4o", "gpt-4o-mini"]  # chat models only
    assert at.session_state["model"] == "gpt-4o"
    hint = at.session_state["key_hint"]
    assert hint.endswith("3456") and secret not in hint
    rendered = " ".join(m.value for m in at.markdown) + " ".join(c.value for c in at.caption)
    assert secret not in rendered
    assert secret not in repr(at.session_state["provider"])
    assert secret.encode() not in (tmp_path / "app.db").read_bytes()


def test_cancelled_run_is_reported_on_next_load(demo_app: AppTest) -> None:
    click(demo_app, "start_demo")
    demo_app.session_state["running"] = True  # what an interrupted run leaves behind
    demo_app.run()
    assert any("cancelled" in w.value for w in demo_app.warning)
    assert demo_app.session_state["running"] is False


def test_knockout_flow_through_the_criteria_and_suppliers_screens(demo_app: AppTest) -> None:
    click(demo_app, "start_demo")
    demo_app.checkbox(key="adv_mand_4").set_value(True).run()
    assert demo_app.number_input(key="adv_min_4").value == 5
    click(demo_app, "save_crit")
    assert not demo_app.exception
    click(demo_app, "to_suppliers")
    click(demo_app, "load_demo")
    ko = demo_app.toggle(key="opt_ko")
    assert ko.value is True and not ko.disabled
    click(demo_app, "evaluate")
    export = demo_app.session_state["viewing_export"]
    assert export["options"]["enable_knockouts"] is True
    statuses = {s["supplier_name"]: s["computed"]["status"] for s in export["suppliers"]}
    assert statuses == {"Apex Systems": "RANKED", "NexaWorks": "RANKED",
                        "BrightPath Tech": "DISQUALIFIED", "Orbit Digital": "DISQUALIFIED"}
    assert any("Disqualified" in m.value for m in demo_app.markdown)


def test_knockout_toggle_is_disabled_when_no_rule_exists(demo_app: AppTest) -> None:
    click(demo_app, "start_demo")
    click(demo_app, "to_suppliers")
    assert demo_app.toggle(key="opt_ko").disabled
    assert any("No criterion is marked as a knock-out" in c.value for c in demo_app.caption)


def test_formula_setup_is_validated_before_saving(demo_app: AppTest) -> None:
    click(demo_app, "start_demo")
    demo_app.radio(key="adv_type_3").set_value("Calculated from a figure").run()
    assert any("describe the figure" in c.value for c in demo_app.caption)
    assert demo_app.button(key="save_crit").disabled
    demo_app.text_input(key="adv_label_3").set_value("Total price over the term").run()
    demo_app.text_input(key="adv_unit_3").set_value("USD").run()
    assert not demo_app.button(key="save_crit").disabled


def test_long_sample_and_retrieval_options_on_the_suppliers_screen(demo_app: AppTest) -> None:
    click(demo_app, "start_demo")
    click(demo_app, "to_suppliers")
    click(demo_app, "load_demo")
    click(demo_app, "add_long")
    assert len(demo_app.session_state["uploads"]) == 5
    assert any("Long: passages are searched" in m.value for m in demo_app.markdown)
    assert any("long document(s) detected" in c.value for c in demo_app.caption)
    click(demo_app, "evaluate")
    export = demo_app.session_state["viewing_export"]
    modes = {s["supplier_name"]: s["scorecard"]["evaluation_mode"] for s in export["suppliers"]}
    assert modes["Apex Systems (long version)"] == "retrieval" and modes["Apex Systems"] == "full"

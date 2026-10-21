"""Regression tests for defects found in the code review."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from core import db
from core.config import load_settings
from core.models import ExtractedFact
from core.orchestrator import RunOptions
from core.tools.document_tool import extract
from core.tools.formula_scoring import _to_number, score_formulas
from tests.run_helpers import new_conn, run, uploads, with_extra_criteria

APP = Path(__file__).resolve().parents[2] / "app.py"


# ---- D1: every browser session must have its own database connection / data


def test_db_mode_setting() -> None:
    assert load_settings({}).db_mode == "file"
    assert load_settings({"RFP_DB_MODE": "Session"}).db_mode == "session"
    assert load_settings({"RFP_DB_MODE": "bogus"}).db_mode == "file"


def _session_app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: str) -> AppTest:
    monkeypatch.setenv("RFP_FAKE_LLM", "1")
    monkeypatch.setenv("RFP_DB_PATH", str(tmp_path / "shared.db"))
    monkeypatch.setenv("RFP_DB_MODE", mode)
    at = AppTest.from_file(str(APP), default_timeout=90)
    at.run()
    at.button(key="start_demo").click().run()
    return at


def _finish_a_run(at: AppTest) -> None:
    at.button(key="to_suppliers").click().run()
    at.button(key="load_demo").click().run()
    at.button(key="evaluate").click().run()
    assert at.session_state["step"] == "results" and not at.exception


def _history_buttons(at: AppTest) -> list[str]:
    return [b.key for b in at.button if b.key and b.key.startswith("hist_")]


def test_session_mode_keeps_runs_private_to_each_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    a = _session_app(monkeypatch, tmp_path, "session")
    _finish_a_run(a)
    assert len(_history_buttons(a)) == 1
    b = _session_app(monkeypatch, tmp_path, "session")  # a different visitor
    assert _history_buttons(b) == []
    assert not (tmp_path / "shared.db").exists()  # nothing was written to disk


def test_session_mode_criteria_edits_do_not_leak_between_sessions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    a = _session_app(monkeypatch, tmp_path, "session")
    a.checkbox(key="adv_mand_4").set_value(True).run()
    a.button(key="save_crit").click().run()
    b = _session_app(monkeypatch, tmp_path, "session")
    b.button(key="to_suppliers").click().run()
    assert b.toggle(key="opt_ko").disabled  # B never got A's knock-out rule


def test_file_mode_still_shares_history_across_sessions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    a = _session_app(monkeypatch, tmp_path, "file")
    _finish_a_run(a)
    b = _session_app(monkeypatch, tmp_path, "file")
    assert len(_history_buttons(b)) == 1  # single-user local use keeps its history


def test_disconnect_wipes_the_session_database(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    a = _session_app(monkeypatch, tmp_path, "session")
    _finish_a_run(a)
    a.button(key="disconnect_top").click().run()
    assert "_db" not in a.session_state
    a.button(key="start_demo").click().run()
    assert _history_buttons(a) == []


# ---- D2: PDF extraction from several worker threads


def test_extraction_is_safe_from_many_threads(synthetic_dir: Path) -> None:
    data = [(synthetic_dir / f).read_bytes() for f in ("apex_systems.pdf", "orbit_digital.pdf")]
    expected = [extract(d).text for d in data]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda i: extract(data[i % 2]).text, range(40)))
    assert all(r == expected[i % 2] for i, r in enumerate(results))


# ---- D4: European number formats must not be read as small decimals


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1.420.000", 1_420_000.0),
        ("1.420,50", 1420.5),
        ("1420,5", 1420.5),
        ("1,420,000 USD", 1_420_000.0),
        ("12.5", 12.5),
        ("1.5 weeks", 1.5),
        ("about 28 weeks.", 28.0),
    ],
)
def test_number_parsing_handles_regional_formats(raw: str, expected: float) -> None:
    assert _to_number(raw) == expected


# ---- D5: a disqualified supplier must not set the formula benchmark


def test_formula_scoring_excludes_named_suppliers_from_the_benchmark() -> None:
    from tests.unit.test_formula_scoring import price

    facts = {
        "cheap": [ExtractedFact(criterion_id=7, label="p", value=100, unit="USD")],
        "mid": [ExtractedFact(criterion_id=7, label="p", value=200, unit="USD")],
    }
    normal, _ = score_formulas([price()], facts)
    assert [normal[s][0].score for s in ("cheap", "mid")] == [10, 5]
    fair, _ = score_formulas([price()], facts, exclude_from_benchmark={"cheap"})
    assert fair["mid"][0].score == 10  # now the best eligible price
    assert fair["cheap"][0].score == 10  # clipped: cannot exceed full marks


def test_disqualified_cheapest_supplier_does_not_depress_everyone_elses_price_score(
    synthetic_dir: Path,
) -> None:
    conn = new_conn()
    with_extra_criteria(conn)
    crit = [
        c.model_copy(update={"is_mandatory": True, "min_score": 5}) if c.criterion_id == 4 else c
        for c in db.load_criteria(conn)
    ]
    db.save_criteria(conn, crit)
    _, out = run(
        conn,
        uploads(synthetic_dir),
        options=RunOptions(model="m", enable_knockouts=True, sensitivity=False),
    )
    by = {s.supplier_name: s for s in out.ranked.suppliers}
    assert by["BrightPath Tech"].status == "DISQUALIFIED"  # the cheapest bid
    price = {n: next(c for c in s.criteria if c.criterion_id == 6) for n, s in by.items()}
    assert price["NexaWorks"].score == 10  # cheapest ELIGIBLE bid (1,050,000)
    assert price["Apex Systems"].score == 7  # 10 * 1.05M / 1.42M = 7.39
    assert price["BrightPath Tech"].score == 10  # clipped, for display only
    assert price["NexaWorks"].benchmark == 10


# ---- embeddings through the OpenAI wrapper (batching and retry)


def test_openai_embed_batches_requests_and_retries() -> None:
    import random
    from types import SimpleNamespace

    from core.tools.llm_provider import EMBED_BATCH, OpenAIProvider

    calls: list[int] = []

    def create(*, model: str, input: list[str]) -> SimpleNamespace:  # noqa: A002
        calls.append(len(input))
        if len(calls) == 1:
            import httpx2 as httpx
            import openai

            req = httpx.Request("POST", "https://api.openai.com/v1/embeddings")
            raise openai.RateLimitError(
                "slow", response=httpx.Response(429, request=req), body=None
            )
        return SimpleNamespace(data=[SimpleNamespace(embedding=[float(len(t))]) for t in input])

    client = SimpleNamespace(embeddings=SimpleNamespace(create=create))
    provider = OpenAIProvider(
        "sk-test-ABCDEFGHIJKLMNOP", client=client, sleep=lambda s: None, rng=random.Random(1)
    )
    texts = ["a" * i for i in range(1, EMBED_BATCH + 5)]
    vectors = provider.embed(texts)
    assert [v[0] for v in vectors] == [float(len(t)) for t in texts]
    assert calls == [EMBED_BATCH, EMBED_BATCH, 4]  # first batch retried once after a 429


# ---- hosted default: any non-localhost address gets private per-session data


@pytest.mark.parametrize(
    ("env", "local", "expected"),
    [
        (None, True, "file"),  # running on your own machine
        (None, False, "session"),  # e.g. xyz.streamlit.app
        ("file", False, "file"),  # an explicit choice always wins
        ("session", True, "session"),
        ("bogus", False, "session"),  # unknown value is ignored, host decides
    ],
)
def test_effective_db_mode_follows_the_host(
    monkeypatch: pytest.MonkeyPatch, env: str | None, local: bool, expected: str
) -> None:
    from ui import state

    if env is None:
        monkeypatch.delenv("RFP_DB_MODE", raising=False)
    else:
        monkeypatch.setenv("RFP_DB_MODE", env)
    monkeypatch.setattr(state, "_is_local_host", lambda: local)
    assert state.effective_db_mode() == expected


def test_host_detection_treats_localhost_variants_as_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from ui import state

    def with_host(host: str) -> None:
        monkeypatch.setattr(state.st, "context", SimpleNamespace(headers={"Host": host}))

    for host in ("localhost:8501", "127.0.0.1:8501", "[::1]:8501", ""):
        with_host(host)
        assert state._is_local_host()
    for host in ("my-app.streamlit.app", "rfp.example.com:443"):
        with_host(host)
        assert not state._is_local_host()
    monkeypatch.setattr(state.st, "context", SimpleNamespace(headers=None))
    assert state._is_local_host()  # unknown host (e.g. test harness) is treated as local


# ---- shared history: deleting a run, and the warning visitors see


def test_delete_run_removes_everything_stored_under_it(synthetic_dir: Path) -> None:
    from core.overrides import OverrideRequest, apply_overrides

    conn = new_conn()
    _, keep = run(conn, uploads(synthetic_dir, ["nexaworks.pdf"]))
    _, gone = run(conn, uploads(synthetic_dir))
    apply_overrides(conn, gone.run_id, [OverrideRequest("s0", 1, 9, "checked on site")])
    assert db.delete_run(conn, gone.run_id) is True
    for table in ("supplier_results", "criterion_scores", "run_warnings", "score_overrides",
                  "run_criteria_snapshot"):
        left = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE rfp_run_id = ?", (gone.run_id,)
        ).fetchone()[0]
        assert left == 0, table
    assert db.load_run_export(conn, gone.run_id) is None
    assert db.load_run_export(conn, keep.run_id) is not None  # other runs untouched
    assert db.delete_run(conn, gone.run_id) is False  # already gone


def test_delete_button_needs_confirmation_and_removes_the_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    at = _session_app(monkeypatch, tmp_path, "file")
    _finish_a_run(at)
    assert len(_history_buttons(at)) == 1
    assert at.button(key="delete_run").disabled  # not until the box is ticked
    at.checkbox(key="confirm_delete").set_value(True).run()
    assert not at.button(key="delete_run").disabled
    at.button(key="delete_run").click().run()
    assert not at.exception
    assert at.session_state["step"] == "suppliers" and "viewing_export" not in at.session_state
    assert _history_buttons(at) == []
    assert any("was deleted" in s.value for s in at.success)


def test_shared_history_on_a_hosted_address_shows_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from ui import state

    monkeypatch.setattr(state, "_is_local_host", lambda: False)
    monkeypatch.delenv("RFP_FAKE_LLM", raising=False)  # demo mode skips the key panel
    monkeypatch.setenv("RFP_DB_PATH", str(tmp_path / "shared.db"))
    monkeypatch.setenv("RFP_DB_MODE", "file")  # the explicit opt-in to shared history
    at = AppTest.from_file(str(APP), default_timeout=90)
    at.run()
    assert not at.exception, at.exception
    assert any("Shared history is on" in w.value for w in at.warning)
    monkeypatch.setenv("RFP_DB_MODE", "session")
    again = AppTest.from_file(str(APP), default_timeout=90)
    again.run()
    assert not any("Shared history is on" in w.value for w in again.warning)
    assert any("private to this browser session" in c.value for c in again.caption)

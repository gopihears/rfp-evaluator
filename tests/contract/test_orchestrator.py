from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from core import db
from core.config import Settings
from core.models import SupplierInput
from core.orchestrator import (
    EvaluationInputError,
    ProgressEvent,
    RunOptions,
    SupplierUpload,
    collect,
    run_evaluation,
)
from core.seed import seed_criteria
from core.tools.demo_responder import demo_responder
from core.tools.evaluation_agent import build_schema
from core.tools.fake_provider import FakeProvider, canned_response
from core.tools.llm_provider import ProviderAuthError, ProviderRateLimitError
from core.tools.provider_factory import demo_mode, make_provider

FILES = {  # file -> (supplier name, submission date, experience)
    "apex_systems.pdf": ("Apex Systems", date(2025, 3, 3), 4.5),
    "brightpath_tech.pdf": ("BrightPath Tech", date(2025, 3, 1), 2.0),
    "nexaworks.pdf": ("NexaWorks", date(2025, 3, 4), 4.0),
    "orbit_digital.pdf": ("Orbit Digital", date(2025, 3, 4), 4.5),
}


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    c = db.connect(":memory:", check_same_thread=False)
    db.init_db(c)
    seed_criteria(c)
    yield c
    c.close()


def uploads(synthetic_dir: Path, files: list[str] | None = None) -> list[SupplierUpload]:
    out = []
    for i, name in enumerate(files or list(FILES)):
        supplier, submitted, experience = FILES.get(name, (name, date(2025, 3, 2), 3.0))
        out.append(SupplierUpload(
            SupplierInput(supplier_id=f"s{i}", supplier_name=supplier, submission_date=submitted,
                          experience_rating=experience, filename=name),
            (synthetic_dir / name).read_bytes()))
    return out


def run(conn: sqlite3.Connection, ups: list[SupplierUpload], provider: Any = None,
        **kw: Any) -> tuple[list[ProgressEvent], Any]:
    provider = provider or FakeProvider(demo_responder)
    options = kw.pop("options", RunOptions(model="demo-model", sensitivity_draws=50))
    return collect(run_evaluation(ups, provider=provider, conn=conn, options=options, **kw))


def by_name(outcome: Any) -> dict[str, Any]:
    return {s.supplier_name: s for s in outcome.ranked.suppliers}


# ---------------------------------------------------------------------------- happy path


def test_ct01_full_run_matches_expected_tiers(
    conn: sqlite3.Connection, synthetic_dir: Path
) -> None:
    events, outcome = run(conn, uploads(synthetic_dir))
    assert outcome.status == "COMPLETED"
    ranked = [s.supplier_name for s in outcome.ranked.suppliers if s.status == "RANKED"]
    assert set(ranked[:2]) == {"Apex Systems", "NexaWorks"}
    assert ranked[2:] == ["Orbit Digital", "BrightPath Tech"]
    assert [s.rank for s in outcome.ranked.suppliers] == [1, 2, 3, 4]
    # persisted: run row, 4 suppliers, 20 criterion rows, export equals what the caller got
    assert db.load_run_export(conn, outcome.run_id) == json.loads(json.dumps(outcome.export))
    assert conn.execute("SELECT COUNT(*) FROM supplier_results").fetchone()[0] == 4
    assert conn.execute("SELECT COUNT(*) FROM criterion_scores").fetchone()[0] == 20
    assert outcome.export["sensitivity"]["draws"] == 50


def test_demo_scores_fall_inside_ground_truth_tiers_not_bands(synthetic_dir: Path) -> None:
    truth = json.loads((synthetic_dir / "ground_truth.json").read_text(encoding="utf-8"))
    assert set(truth["expected_tiers"]["top"]) == {"apex_systems.pdf", "nexaworks.pdf"}


def test_ct12_progress_events_are_ordered_per_supplier(conn: sqlite3.Connection,
                                                        synthetic_dir: Path) -> None:
    events, outcome = run(conn, uploads(synthetic_dir))
    order = ["QUEUED", "READING", "SCORING", "VALIDATING", "DONE"]
    for sid in ("s0", "s1", "s2", "s3"):
        stages = [e.stage for e in events if e.supplier_id == sid]
        assert stages == order
    run_stages = [e.stage for e in events if e.supplier_id is None]
    assert run_stages == ["RANKING", "SAVING", "COMPLETE"]
    pcts = [e.overall_pct for e in events]
    assert pcts == sorted(pcts) and pcts[-1] == 1.0


def test_ct14_concurrency_gives_same_result_as_sequential(conn: sqlite3.Connection,
                                                           synthetic_dir: Path) -> None:
    _, a = run(conn, uploads(synthetic_dir), settings=Settings(concurrency=1))
    _, b = run(conn, uploads(synthetic_dir), settings=Settings(concurrency=4))
    assert [s.model_dump() for s in a.ranked.suppliers] == [
        s.model_dump() for s in b.ranked.suppliers]
    assert a.run_id != b.run_id


def test_ct09_latest_criteria_are_used_and_snapshotted(conn: sqlite3.Connection,
                                                        synthetic_dir: Path) -> None:
    _, first = run(conn, uploads(synthetic_dir, ["nexaworks.pdf"]))
    crit = db.load_criteria(conn)
    crit[0] = crit[0].model_copy(update={"weight": 10})
    crit[4] = crit[4].model_copy(update={"weight": 30})
    db.save_criteria(conn, crit)
    _, second = run(conn, uploads(synthetic_dir, ["nexaworks.pdf"]))
    weights = {c["criterion_id"]: c["weight"] for c in second.export["criteria_snapshot"]}
    assert weights[1] == 10 and weights[5] == 30
    old = {c["criterion_id"]: c["weight"] for c in first.export["criteria_snapshot"]}
    assert old[1] == 30 and old[5] == 10
    stored = db.load_run_export(conn, first.run_id)
    assert stored is not None and stored["criteria_snapshot"][0]["weight"] == 30


# --------------------------------------------------------------------------- failures


def test_ct07_corrupt_pdf_fails_alone(conn: sqlite3.Connection, synthetic_dir: Path) -> None:
    ups = uploads(synthetic_dir, ["apex_systems.pdf", "corrupt_co.pdf", "nexaworks.pdf"])
    events, outcome = run(conn, ups)
    assert outcome.status == "PARTIAL"
    r = by_name(outcome)
    assert r["corrupt_co.pdf"].status == "FAILED" and r["corrupt_co.pdf"].rank is None
    assert r["Apex Systems"].rank in (1, 2) and r["NexaWorks"].rank in (1, 2)
    codes = [w["code"] for w in outcome.export["warnings"]]
    assert "E_NO_TEXT" in codes
    failed = [e for e in events if e.stage == "FAILED"]
    assert len(failed) == 1 and "scanned" in failed[0].message


def test_non_pdf_bytes_fail_cleanly(conn: sqlite3.Connection, synthetic_dir: Path) -> None:
    ups = uploads(synthetic_dir, ["nexaworks.pdf"])
    ups.append(SupplierUpload(
        SupplierInput(supplier_id="x", supplier_name="Fake", submission_date=date(2025, 3, 1),
                      experience_rating=1), b"MZ not a pdf"))
    _, outcome = run(conn, ups)
    assert outcome.status == "PARTIAL"
    assert "E_NOT_PDF" in [w["code"] for w in outcome.export["warnings"]]


def test_ct08_all_suppliers_fail(conn: sqlite3.Connection, synthetic_dir: Path) -> None:
    _, outcome = run(conn, uploads(synthetic_dir, ["corrupt_co.pdf"]))
    assert outcome.status == "FAILED" and outcome.export["leaderboard"] == []
    assert conn.execute("SELECT status FROM rfp_runs").fetchone()[0] == "FAILED"


def test_ct02_malformed_json_repaired(conn: sqlite3.Connection, synthetic_dir: Path) -> None:
    calls = {"n": 0}

    def responder(system: str, user: str, schema: dict[str, Any]) -> Any:
        calls["n"] += 1
        return "oops {" if calls["n"] == 1 else demo_responder(system, user, schema)

    _, outcome = run(conn, uploads(synthetic_dir, ["nexaworks.pdf"]),
                     provider=FakeProvider(responder))
    assert outcome.status == "COMPLETED" and calls["n"] == 2
    assert "I_REPAIRED" in [w["code"] for w in outcome.export["warnings"]]


def test_ct03_malformed_twice_fails_that_supplier(conn: sqlite3.Connection,
                                                   synthetic_dir: Path) -> None:
    def responder(system: str, user: str, schema: dict[str, Any]) -> Any:
        if "fourteen supplier-management" in user:  # only Orbit's proposal says this
            return "broken"
        return demo_responder(system, user, schema)

    _, outcome = run(conn, uploads(synthetic_dir), provider=FakeProvider(responder))
    assert outcome.status == "PARTIAL"
    assert by_name(outcome)["Orbit Digital"].status == "FAILED"
    assert "E_LLM_JSON" in [w["code"] for w in outcome.export["warnings"]]


def test_provider_error_marks_supplier_failed_with_code(conn: sqlite3.Connection,
                                                         synthetic_dir: Path) -> None:
    def responder(system: str, user: str, schema: dict[str, Any]) -> Any:
        if "most competitive price" in user:
            return ProviderRateLimitError("OpenAI rate limit reached")
        return demo_responder(system, user, schema)

    _, outcome = run(conn, uploads(synthetic_dir), provider=FakeProvider(responder))
    assert by_name(outcome)["BrightPath Tech"].status == "FAILED"
    assert "E_LLM_RATE" in [w["code"] for w in outcome.export["warnings"]]


def test_auth_error_for_every_supplier(conn: sqlite3.Connection, synthetic_dir: Path) -> None:
    provider = FakeProvider(lambda s, u, sch: ProviderAuthError("The API key was rejected."))
    _, outcome = run(conn, uploads(synthetic_dir), provider=provider)
    assert outcome.status == "FAILED"
    assert {w["code"] for w in outcome.export["warnings"]} == {"E_LLM_AUTH"}


def test_unexpected_worker_exception_is_contained(conn: sqlite3.Connection,
                                                   synthetic_dir: Path) -> None:
    def responder(system: str, user: str, schema: dict[str, Any]) -> Any:
        if "most competitive price" in user:
            raise RuntimeError("boom with secret sk-ABCDEFGHIJKLMNOP")
        return demo_responder(system, user, schema)

    _, outcome = run(conn, uploads(synthetic_dir), provider=FakeProvider(responder))
    assert outcome.status == "PARTIAL"
    text = json.dumps(outcome.export)
    assert "E_UNEXPECTED" in text and "sk-ABCDEFGH" not in text


def test_ct11_cancellation_ranks_nothing(conn: sqlite3.Connection, synthetic_dir: Path) -> None:
    cancel = threading.Event()

    def slow(system: str, user: str, schema: dict[str, Any]) -> Any:
        time.sleep(0.15)
        cancel.set()
        return demo_responder(system, user, schema)

    _, outcome = run(conn, uploads(synthetic_dir), provider=FakeProvider(slow), cancel=cancel,
                     settings=Settings(concurrency=1))
    assert outcome.cancelled and outcome.status == "FAILED"
    assert all(s.rank is None for s in outcome.ranked.suppliers)
    assert "E_CANCELLED" in [w["code"] for w in outcome.export["warnings"]]


def test_persist_failure_marks_run_failed(conn: sqlite3.Connection, synthetic_dir: Path,
                                          monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: Any, **k: Any) -> None:
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(db, "persist_results", boom)
    with pytest.raises(sqlite3.OperationalError):
        run(conn, uploads(synthetic_dir, ["nexaworks.pdf"]))
    assert conn.execute("SELECT status FROM rfp_runs").fetchone()[0] == "FAILED"


# ------------------------------------------------------------------------ input checks


def test_input_validation(conn: sqlite3.Connection, synthetic_dir: Path) -> None:
    with pytest.raises(EvaluationInputError) as e:
        run(conn, [])
    assert e.value.code == "E_NO_SUPPLIERS"
    with pytest.raises(EvaluationInputError) as e2:
        run(conn, uploads(synthetic_dir), settings=Settings(max_suppliers=2))
    assert e2.value.code == "E_TOO_MANY_SUPPLIERS"
    dup = uploads(synthetic_dir, ["nexaworks.pdf", "nexaworks.pdf"])
    dup[1] = SupplierUpload(dup[1].supplier.model_copy(update={"supplier_id": "s0"}),
                            dup[1].pdf_bytes)
    with pytest.raises(EvaluationInputError) as e3:
        run(conn, dup)
    assert e3.value.code == "E_DUPLICATE_SUPPLIER"


def test_bad_weights_block_the_run_before_it_starts(conn: sqlite3.Connection,
                                                     synthetic_dir: Path) -> None:
    conn.execute("UPDATE evaluation_criteria SET weight = 50 WHERE criterion_id = 1")
    conn.commit()
    with pytest.raises(db.CriteriaError) as e:
        run(conn, uploads(synthetic_dir, ["nexaworks.pdf"]))
    assert e.value.code == "E_WEIGHTS_TOTAL"
    assert conn.execute("SELECT COUNT(*) FROM rfp_runs").fetchone()[0] == 0


# ---------------------------------------------------------------- prompt / security


def test_ct10_prompts_have_active_criteria_and_no_supplier_names(conn: sqlite3.Connection,
                                                                  synthetic_dir: Path) -> None:
    fake = FakeProvider(demo_responder)
    run(conn, uploads(synthetic_dir), provider=fake)
    assert len(fake.calls) == 4
    for call in fake.calls:
        assert "Technical Capability" in call.user and "Support & Experience" in call.user
        for name in ("Apex Systems:", "BrightPath Tech:", "supplier_name"):
            assert name not in call.system
    assert fake.calls[0].schema == build_schema(db.load_active_criteria(conn))


def test_ct13_api_key_never_reaches_db_export_or_events(conn: sqlite3.Connection,
                                                         synthetic_dir: Path) -> None:
    secret = "sk-test-SECRETSECRETSECRET123456"
    from core.tools.llm_provider import OpenAIProvider

    class Client:
        """OpenAI client stand-in that always answers like the demo responder."""

        def __init__(self) -> None:
            self.chat = type("C", (), {"completions": type("X", (), {"create": self._create})()})()

        def _create(self, **kwargs: Any) -> Any:
            user = kwargs["messages"][1]["content"]
            schema = kwargs["response_format"]["json_schema"]["schema"]
            content = json.dumps(demo_responder("", user, schema))
            msg = type("M", (), {"content": content, "refusal": None})()
            return type("R", (), {"choices": [type("Ch", (), {"message": msg})()]})()

    provider = OpenAIProvider(secret, client=Client())
    events, outcome = run(conn, uploads(synthetic_dir), provider=provider)
    blob = json.dumps(outcome.export) + repr(events) + repr(outcome) + repr(provider)
    for table in ("rfp_runs", "supplier_results", "run_warnings", "run_criteria_snapshot"):
        for row in conn.execute(f"SELECT * FROM {table}"):
            blob += repr(tuple(row))
    assert secret not in blob and "SECRETSECRET" not in blob


def test_injection_probe_run_flags_warning_and_does_not_win(conn: sqlite3.Connection,
                                                            synthetic_dir: Path) -> None:
    ups = uploads(synthetic_dir, ["nexaworks.pdf", "injection_probe.pdf"])
    _, outcome = run(conn, ups)
    codes = [(w["code"], w["supplier_id"]) for w in outcome.export["warnings"]]
    assert ("W_INJECTION_SUSPECTED", "s1") in codes
    twin = by_name(outcome)
    scores = {n: s.absolute_score for n, s in twin.items()}
    assert abs(scores["injection_probe.pdf"] - scores["NexaWorks"]) < 12  # not inflated to 100


# ------------------------------------------------------------------------ factory


def test_provider_factory_modes() -> None:
    assert demo_mode({"RFP_FAKE_LLM": "1"}) and not demo_mode({}) and not demo_mode(
        {"RFP_FAKE_LLM": "0"})
    assert isinstance(make_provider("", {"RFP_FAKE_LLM": "1"}), FakeProvider)
    with pytest.raises(ProviderAuthError):
        make_provider("", {})
    assert make_provider("sk-abc12345678", {}).__class__.__name__ == "OpenAIProvider"


def test_demo_responder_matches_schema_and_quotes_document() -> None:
    from core.tools.evaluation_agent import build_user_prompt
    from core.tools.validation_tool import validate
    from tests.helpers import make_criteria, make_supplier

    crit = make_criteria((50, 50))
    crit = [c.model_copy(update={"name": n}) for c, n in zip(crit, ["Technical Capability",
                                                                    "Security & Compliance"],
                                                             strict=True)]
    doc = "[p.1]\nThe API gateway uses TLS 1.3 and AES-256 encryption with an audit log."
    raw = demo_responder("", build_user_prompt(crit, doc), build_schema(crit))
    card = validate(raw, crit, make_supplier("S"), doc)
    assert card.ok and all(r.evidence_verified for r in card.criteria if r.evidence)
    assert re.search(r"audit log", raw["criteria"][1]["evidence"])
    assert canned_response(build_schema(crit), [1, 2])["criteria"][1]["score"] == 2

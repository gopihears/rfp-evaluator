"""Should-tier features end to end: retrieval (OPT-5), formula criteria (OPT-3),
knock-outs (OPT-2) and analyst overrides (OPT-1)."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from core import db
from core.config import Settings
from core.models import Criterion
from core.orchestrator import RunOptions
from core.overrides import OverrideError, OverrideRequest, apply_overrides
from core.tools.demo_responder import demo_responder
from core.tools.document_tool import extract
from core.tools.evaluation_agent import evaluate_supplier_retrieval
from core.tools.fake_provider import FakeProvider
from core.tools.llm_provider import ProviderRateLimitError
from core.tools.retrieval import Index, chunk_document
from tests.helpers import make_criteria, make_supplier
from tests.run_helpers import BRIEF, by_name, new_conn, run, uploads, with_extra_criteria

NOW = "2025-06-01T12:00:00Z"


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    c = new_conn()
    yield c
    c.close()


def modes(outcome: Any) -> dict[str, str]:
    return {
        s["supplier_name"]: s["scorecard"]["evaluation_mode"] for s in outcome.export["suppliers"]
    }


# =============================================================== OPT-5 retrieval


def test_auto_mode_uses_retrieval_only_for_the_long_document(
    conn: sqlite3.Connection, synthetic_dir: Path
) -> None:
    fake = FakeProvider(demo_responder)
    events, outcome = run(
        conn, uploads(synthetic_dir, ["long_apex_80p.pdf", "nexaworks.pdf"]), provider=fake
    )
    assert modes(outcome) == {"Apex Systems": "retrieval", "NexaWorks": "full"}
    assert len(fake.calls) == 5 + 1  # one per criterion for the long proposal, one for the short
    assert any("Long document" in e.message for e in events)
    cards = {s["supplier_name"]: s["scorecard"] for s in outcome.export["suppliers"]}
    assert all(r["retrieved_pages"] for r in cards["Apex Systems"]["criteria"])
    assert cards["NexaWorks"]["criteria"][0]["retrieved_pages"] is None
    assert outcome.export["options"]["retrieval"] == "auto"


def test_retrieval_prompts_are_a_small_slice_of_the_document(
    conn: sqlite3.Connection, synthetic_dir: Path
) -> None:
    fake = FakeProvider(demo_responder)
    run(conn, uploads(synthetic_dir, ["long_apex_80p.pdf"]), provider=fake)
    doc_chars = len(extract((synthetic_dir / "long_apex_80p.pdf").read_bytes()).text)
    assert len(fake.calls) == 5
    for call in fake.calls:
        assert len(call.user) < doc_chars * 0.25
        assert call.user.count("criterion_id") <= 4  # a single criterion per prompt
    assert fake.embed_calls[1] > 100  # all chunks embedded once (after the one-text probe)
    assert len(fake.embed_calls) > 5  # then at least one query embedding per criterion


def test_forced_modes_and_threshold(conn: sqlite3.Connection, synthetic_dir: Path) -> None:
    long_up, short_up = (
        uploads(synthetic_dir, [f]) for f in ("long_apex_80p.pdf", "nexaworks.pdf")
    )
    f1 = FakeProvider(demo_responder)
    _, o1 = run(
        conn,
        long_up,
        provider=f1,
        options=RunOptions(model="m", retrieval="full", sensitivity=False),
    )
    assert modes(o1) == {"Apex Systems": "full"} and len(f1.calls) == 1
    f2 = FakeProvider(demo_responder)
    _, o2 = run(
        conn,
        short_up,
        provider=f2,
        options=RunOptions(model="m", retrieval="retrieve", sensitivity=False),
    )
    assert modes(o2) == {"NexaWorks": "retrieval"} and len(f2.calls) == 5
    f3 = FakeProvider(demo_responder)
    _, o3 = run(
        conn,
        short_up,
        provider=f3,
        settings=Settings(full_context_max_tokens=500),
        options=RunOptions(model="m", sensitivity=False),
    )
    assert modes(o3) == {"NexaWorks": "retrieval"}


def test_evidence_is_verified_against_the_whole_document(
    conn: sqlite3.Connection, synthetic_dir: Path
) -> None:
    _, outcome = run(conn, uploads(synthetic_dir, ["long_apex_80p.pdf"]))
    results = outcome.export["suppliers"][0]["scorecard"]["criteria"]
    quoted = [r for r in results if r["evidence"]]
    assert quoted and all(r["evidence_verified"] is True for r in quoted)


def test_provider_without_embeddings_still_works(
    conn: sqlite3.Connection, synthetic_dir: Path
) -> None:
    class NoEmbed:
        def __init__(self) -> None:
            self.inner = FakeProvider(demo_responder)

        def complete_json(self, **kw: Any) -> str:
            return self.inner.complete_json(**kw)

        def validate_key(self) -> list[str]:
            return []

    _, outcome = run(conn, uploads(synthetic_dir, ["long_apex_80p.pdf"]), provider=NoEmbed())
    assert outcome.status == "COMPLETED" and modes(outcome) == {"Apex Systems": "retrieval"}


def test_embedding_failure_degrades_to_keyword_search_with_a_note(
    conn: sqlite3.Connection, synthetic_dir: Path
) -> None:
    class FlakyEmbed(FakeProvider):
        def embed(self, texts: list[str], *, model: str = "x") -> list[list[float]]:
            raise ProviderRateLimitError("embeddings busy")

    _, outcome = run(
        conn, uploads(synthetic_dir, ["long_apex_80p.pdf"]), provider=FlakyEmbed(demo_responder)
    )
    assert outcome.status == "COMPLETED"
    assert "I_NO_EMBEDDINGS" in [w["code"] for w in outcome.export["warnings"]]


def test_low_retrieval_is_flagged_and_marked_low_confidence(synthetic_dir: Path) -> None:
    doc = extract((synthetic_dir / "long_apex_80p.pdf").read_bytes())
    index = Index.build(chunk_document(doc))
    odd = Criterion(criterion_id=9, name="Zebra giraffe", description="quokka wombat", weight=10)
    result = evaluate_supplier_retrieval(
        FakeProvider(demo_responder),
        model="m",
        criteria=[odd],
        supplier=make_supplier("S"),
        doc=doc,
        index=index,
    )
    card = result.scorecard
    assert card.criteria[0].confidence == "low"
    assert "W_LOW_RETRIEVAL" in [w.code for w in card.warnings]
    assert card.evaluation_mode == "retrieval"


def test_retrieval_repairs_a_bad_answer_for_one_criterion(synthetic_dir: Path) -> None:
    doc = extract((synthetic_dir / "long_apex_80p.pdf").read_bytes())
    index = Index.build(chunk_document(doc))
    crit = make_criteria((50, 50))
    state = {"n": 0}

    def responder(system: str, user: str, schema: dict[str, Any]) -> Any:
        state["n"] += 1
        return "garbage" if state["n"] == 1 else demo_responder(system, user, schema)

    result = evaluate_supplier_retrieval(
        FakeProvider(responder),
        model="m",
        criteria=crit,
        supplier=make_supplier("S"),
        doc=doc,
        index=index,
    )
    assert result.repaired and result.attempts == 3 and result.scorecard.ok
    assert "I_REPAIRED" in [w.code for w in result.scorecard.warnings]


def test_retrieval_total_failure_marks_the_criterion_missing(synthetic_dir: Path) -> None:
    doc = extract((synthetic_dir / "long_apex_80p.pdf").read_bytes())
    index = Index.build(chunk_document(doc))
    result = evaluate_supplier_retrieval(
        FakeProvider(lambda s, u, sch: "nope"),
        model="m",
        criteria=make_criteria((100,)),
        supplier=make_supplier("S"),
        doc=doc,
        index=index,
    )
    assert not result.scorecard.ok
    assert "E_LLM_JSON" in [w.code for w in result.scorecard.warnings]


def test_injection_warning_is_reported_once_not_once_per_criterion(
    conn: sqlite3.Connection, synthetic_dir: Path
) -> None:
    doc = extract((synthetic_dir / "injection_probe.pdf").read_bytes())
    index = Index.build(chunk_document(doc))
    result = evaluate_supplier_retrieval(
        FakeProvider(demo_responder),
        model="m",
        criteria=db.load_active_criteria(conn),
        supplier=make_supplier("S"),
        doc=doc,
        index=index,
        top_k=60,
    )
    codes = [w.code for w in result.scorecard.warnings]
    assert codes.count("W_INJECTION_SUSPECTED") == 1


# ============================================================= OPT-3 formula criteria


def test_ut_f07_formula_and_llm_criteria_are_scored_together(
    conn: sqlite3.Connection, synthetic_dir: Path
) -> None:
    with_extra_criteria(conn)
    fake = FakeProvider(demo_responder)
    _, outcome = run(conn, uploads(synthetic_dir), provider=fake)
    assert outcome.status == "COMPLETED" and len(fake.calls) == 8  # judge + facts per supplier
    ranked = by_name(outcome)
    price = {n: next(c for c in s.criteria if c.criterion_id == 6).score for n, s in ranked.items()}
    weeks = {n: next(c for c in s.criteria if c.criterion_id == 7).score for n, s in ranked.items()}
    # lowest price 690,000 and shortest schedule 14 weeks score full marks
    assert price == {"Apex Systems": 5, "BrightPath Tech": 10, "NexaWorks": 7, "Orbit Digital": 6}
    assert weeks == {"Apex Systems": 5, "BrightPath Tech": 10, "NexaWorks": 6, "Orbit Digital": 5}
    card = next(s for s in outcome.export["suppliers"] if s["supplier_name"] == "NexaWorks")
    formula_rows = [r for r in card["scorecard"]["criteria"] if r["source"] == "formula"]
    assert [r["criterion_id"] for r in formula_rows] == [6, 7]
    assert (
        "1,050,000" in formula_rows[0]["justification"]
        and "690,000" in formula_rows[0]["justification"]
    )
    assert [f["value"] for f in card["scorecard"]["facts"]] == [1_050_000.0, 22.0]
    assert all(f["evidence_verified"] for f in card["scorecard"]["facts"])
    assert len(outcome.export["criteria_snapshot"]) == 7
    assert sum(c["weight"] for c in outcome.export["criteria_snapshot"]) == 100


def test_formula_run_is_deterministic(conn: sqlite3.Connection, synthetic_dir: Path) -> None:
    with_extra_criteria(conn)
    _, a = run(conn, uploads(synthetic_dir))
    _, b = run(conn, uploads(synthetic_dir))
    assert [s.model_dump() for s in a.ranked.suppliers] == [
        s.model_dump() for s in b.ranked.suppliers
    ]


def test_price_changes_the_outcome_when_it_carries_weight(
    conn: sqlite3.Connection, synthetic_dir: Path
) -> None:
    _, base = run(conn, uploads(synthetic_dir))
    with_extra_criteria(conn, price_weight=12, weeks_weight=8)
    _, with_formula = run(conn, uploads(synthetic_dir))
    base_order = [s.supplier_name for s in base.ranked.suppliers]
    new_scores = {s.supplier_name: s.absolute_score for s in with_formula.ranked.suppliers}
    assert new_scores["BrightPath Tech"] > by_name(base)["BrightPath Tech"].absolute_score
    assert base_order[0] in {"Apex Systems", "NexaWorks"}


def test_only_formula_criteria_is_allowed(conn: sqlite3.Connection, synthetic_dir: Path) -> None:
    crit = [
        Criterion(criterion_id=1, name="Technical Capability", weight=0, is_active=False),
        Criterion(criterion_id=2, name="Implementation Plan", weight=0, is_active=False),
        Criterion(criterion_id=3, name="Commercial Value", weight=0, is_active=False),
        Criterion(criterion_id=4, name="Security & Compliance", weight=0, is_active=False),
        Criterion(criterion_id=5, name="Support & Experience", weight=0, is_active=False),
        Criterion(
            criterion_id=6,
            name="Total Price",
            weight=100,
            criterion_type="formula",
            formula_kind="lowest_is_best",
            formula_params={"label": "Total price", "unit": "USD"},
        ),
    ]
    db.save_criteria(conn, crit)
    fake = FakeProvider(demo_responder)
    _, outcome = run(conn, uploads(synthetic_dir), provider=fake)
    assert [s.supplier_name for s in outcome.ranked.suppliers][0] == "BrightPath Tech"
    assert len(fake.calls) == 4  # facts only; the judging call is skipped


def test_ut_f04_missing_figure_scores_as_missing_but_run_continues(
    conn: sqlite3.Connection, synthetic_dir: Path
) -> None:
    with_extra_criteria(conn)

    def responder(system: str, user: str, schema: dict[str, Any]) -> Any:
        out = demo_responder(system, user, schema)
        if "facts" in out and "most competitive price" in user:  # BrightPath: price not stated
            out["facts"][0].update(found=False, value=None, evidence="")
        return out

    _, outcome = run(conn, uploads(synthetic_dir), provider=FakeProvider(responder))
    bright = next(s for s in outcome.export["suppliers"] if s["supplier_name"] == "BrightPath Tech")
    price_row = next(r for r in bright["scorecard"]["criteria"] if r["criterion_id"] == 6)
    assert price_row["score"] is None and price_row["status"] == "missing"
    assert any(
        w["code"] == "W_FACT_MISSING" and w["supplier_id"] == "s1"
        for w in outcome.export["warnings"]
    )
    assert bright["computed"]["status"] == "RANKED"
    # the cheapest remaining supplier now sets the benchmark
    nexa = next(s for s in outcome.ranked.suppliers if s.supplier_name == "NexaWorks")
    assert next(c for c in nexa.criteria if c.criterion_id == 6).score == 10


def test_ut_f05_currency_mismatch_is_excluded_not_converted(
    conn: sqlite3.Connection, synthetic_dir: Path
) -> None:
    with_extra_criteria(conn)

    def responder(system: str, user: str, schema: dict[str, Any]) -> Any:
        out = demo_responder(system, user, schema)
        if "facts" in out and "fourteen supplier-management" in user:  # Orbit quotes EUR
            out["facts"][0]["unit"] = "EUR"
        return out

    _, outcome = run(conn, uploads(synthetic_dir), provider=FakeProvider(responder))
    orbit = by_name(outcome)["Orbit Digital"]
    assert next(c for c in orbit.criteria if c.criterion_id == 6).score is None
    assert "W_CURRENCY_MISMATCH" in [w["code"] for w in outcome.export["warnings"]]


def test_misconfigured_formula_blocks_the_run(
    conn: sqlite3.Connection, synthetic_dir: Path
) -> None:
    with_extra_criteria(conn)
    conn.execute("UPDATE evaluation_criteria SET formula_kind = 'median' WHERE criterion_id = 6")
    conn.commit()
    with pytest.raises(db.CriteriaError) as e:
        run(conn, uploads(synthetic_dir, ["nexaworks.pdf"]))
    assert e.value.code == "E_FORMULA_CONFIG"
    assert conn.execute("SELECT COUNT(*) FROM rfp_runs").fetchone()[0] == 0


def test_saving_invalid_formula_or_knockout_config_is_rejected(conn: sqlite3.Connection) -> None:
    crit = db.load_criteria(conn)
    bad = crit[0].model_copy(update={"is_mandatory": True, "min_score": None})
    with pytest.raises(db.CriteriaError) as e:
        db.save_criteria(conn, [bad, *crit[1:]])
    assert e.value.code == "E_KNOCKOUT_CONFIG"


def test_formula_criteria_round_trip_through_the_database(conn: sqlite3.Connection) -> None:
    with_extra_criteria(conn)
    stored = {c.criterion_id: c for c in db.load_criteria(conn)}
    assert stored[6].criterion_type == "formula" and stored[6].formula_kind == "lowest_is_best"
    assert stored[6].formula_params == {"label": "Total price over the term", "unit": "USD"}


# ================================================================ OPT-2 knock-outs


def set_security_mandatory(conn: sqlite3.Connection, minimum: int = 5) -> None:
    crit = [
        c.model_copy(update={"is_mandatory": True, "min_score": minimum})
        if c.criterion_id == 4
        else c
        for c in db.load_criteria(conn)
    ]
    db.save_criteria(conn, crit)


def test_knockouts_disqualify_when_enabled(conn: sqlite3.Connection, synthetic_dir: Path) -> None:
    set_security_mandatory(conn)
    _, outcome = run(
        conn,
        uploads(synthetic_dir),
        options=RunOptions(model="m", enable_knockouts=True, sensitivity=False),
    )
    statuses = {n: s.status for n, s in by_name(outcome).items()}
    assert statuses == {
        "Apex Systems": "RANKED",
        "NexaWorks": "RANKED",
        "BrightPath Tech": "DISQUALIFIED",
        "Orbit Digital": "DISQUALIFIED",
    }
    assert outcome.status == "COMPLETED"  # disqualified is a decision, not a failure
    reasons = by_name(outcome)["BrightPath Tech"].disqualified_reasons
    assert reasons and "Security & Compliance" in reasons[0] and "minimum 5" in reasons[0]
    assert [r["supplier_name"] for r in outcome.export["leaderboard"]] == [
        s.supplier_name for s in outcome.ranked.suppliers if s.status == "RANKED"
    ]
    assert outcome.export["options"]["enable_knockouts"] is True


def test_knockouts_are_ignored_unless_enabled(
    conn: sqlite3.Connection, synthetic_dir: Path
) -> None:
    set_security_mandatory(conn)
    _, outcome = run(conn, uploads(synthetic_dir), options=RunOptions(model="m", sensitivity=False))
    assert {s.status for s in outcome.ranked.suppliers} == {"RANKED"}


# ================================================================ OPT-1 overrides


@pytest.fixture
def finished(conn: sqlite3.Connection, synthetic_dir: Path) -> Any:
    _, outcome = run(conn, uploads(synthetic_dir))
    return outcome


def order(export: dict[str, Any]) -> list[str]:
    return [r["supplier_name"] for r in export["leaderboard"]]


def test_override_can_change_the_ranking_and_keeps_the_audit_trail(
    conn: sqlite3.Connection, finished: Any
) -> None:
    before = order(finished.export)
    assert before[-1] == "BrightPath Tech"
    reqs = [
        OverrideRequest("s1", cid, 10, "Verified on site: exceptional in every area")
        for cid in (1, 2, 3, 4, 5)
    ]
    export = apply_overrides(conn, finished.run_id, reqs, now=lambda: NOW)
    assert order(export)[0] == "BrightPath Tech" and order(export) != before
    bright = next(s for s in export["suppliers"] if s["supplier_name"] == "BrightPath Tech")
    assert all(c["overridden"] and c["score"] == 10 for c in bright["computed"]["criteria"])
    assert [c["original_score"] for c in bright["computed"]["criteria"]] == [4, 3, 4, 3, 2]
    # the model's own scorecard is untouched
    assert [r["score"] for r in bright["scorecard"]["criteria"]] == [4, 3, 4, 3, 2]
    assert len(export["overrides"]) == 5 and export["overrides"][0]["created_at"] == NOW
    rows = db.list_overrides(conn, finished.run_id)
    assert len(rows) == 5 and rows[0]["new_score"] == 10 and rows[0]["original_score"] in (4, 3)
    assert db.load_run_export(conn, finished.run_id) == json.loads(json.dumps(export))
    stored_rank = conn.execute("SELECT final_rank FROM supplier_results WHERE supplier_id='s1'")
    assert stored_rank.fetchone()[0] == 1


def test_overrides_preserve_warnings_status_and_sensitivity(
    conn: sqlite3.Connection, synthetic_dir: Path
) -> None:
    _, out = run(
        conn, uploads(synthetic_dir, ["nexaworks.pdf", "injection_probe.pdf", "corrupt_co.pdf"])
    )
    codes_before = sorted(w["code"] for w in out.export["warnings"])
    export = apply_overrides(
        conn, out.run_id, [OverrideRequest("s0", 1, 9, "Reviewed the architecture diagrams")]
    )
    assert sorted(w["code"] for w in export["warnings"]) == codes_before
    assert export["status"] == out.status == "PARTIAL"
    assert export["sensitivity"]["draws"] == 50 and export["sensitivity"]["seed"] == 42
    failed = next(s for s in export["suppliers"] if s["supplier_name"] == "corrupt_co.pdf")
    assert failed["computed"]["status"] == "FAILED"


def test_latest_override_wins_and_every_change_is_logged(
    conn: sqlite3.Connection, finished: Any
) -> None:
    apply_overrides(conn, finished.run_id, [OverrideRequest("s1", 4, 8, "first look")])
    export = apply_overrides(conn, finished.run_id, [OverrideRequest("s1", 4, 6, "after the call")])
    cell = next(
        c
        for s in export["suppliers"]
        if s["supplier_id"] == "s1"
        for c in s["computed"]["criteria"]
        if c["criterion_id"] == 4
    )
    assert cell["score"] == 6 and cell["original_score"] == 3
    assert [(o["new_score"], o["reason"]) for o in export["overrides"]] == [
        (8, "first look"),
        (6, "after the call"),
    ]
    assert len(db.list_overrides(conn, finished.run_id)) == 2


def test_reverting_to_the_model_score_restores_the_original_order(
    conn: sqlite3.Connection, finished: Any
) -> None:
    before = order(finished.export)
    apply_overrides(conn, finished.run_id, [OverrideRequest("s1", 1, 10, "test change")])
    export = apply_overrides(
        conn, finished.run_id, [OverrideRequest("s1", 1, 4, "Reverted to the model's score")]
    )
    assert order(export) == before


def test_overriding_a_disqualified_supplier_can_reinstate_it(
    conn: sqlite3.Connection, synthetic_dir: Path
) -> None:
    set_security_mandatory(conn)
    _, out = run(
        conn,
        uploads(synthetic_dir),
        options=RunOptions(model="m", enable_knockouts=True, sensitivity=False),
    )
    assert "BrightPath Tech" not in order(out.export)
    export = apply_overrides(
        conn, out.run_id, [OverrideRequest("s1", 4, 6, "Certificate provided after bid")]
    )
    assert "BrightPath Tech" in order(export)


def test_identical_overrides_give_identical_results(synthetic_dir: Path) -> None:
    exports = []
    for _ in range(2):
        c = new_conn()
        _, out = run(c, uploads(synthetic_dir), now=lambda: NOW)
        exports.append(
            apply_overrides(
                c, out.run_id, [OverrideRequest("s2", 3, 9, "negotiated")], now=lambda: NOW
            )
        )
        c.close()
    for e in exports:
        e["rfp_run_id"] = "x"
    assert json.dumps(exports[0], sort_keys=True) == json.dumps(exports[1], sort_keys=True)


@pytest.mark.parametrize(
    ("request_", "code"),
    [
        (OverrideRequest("s0", 1, 11, "too high"), "E_SCORE_RANGE"),
        (OverrideRequest("s0", 1, -1, "too low"), "E_SCORE_RANGE"),
        (OverrideRequest("s0", 1, 5, "  "), "E_REASON_REQUIRED"),
        (OverrideRequest("zzz", 1, 5, "reason"), "E_UNKNOWN_SUPPLIER"),
        (OverrideRequest("s0", 99, 5, "reason"), "E_UNKNOWN_CRITERION"),
    ],
)
def test_override_validation(
    conn: sqlite3.Connection, finished: Any, request_: OverrideRequest, code: str
) -> None:
    with pytest.raises(OverrideError) as e:
        apply_overrides(conn, finished.run_id, [request_])
    assert e.value.code == code
    assert db.list_overrides(conn, finished.run_id) == []  # nothing half-applied


def test_override_rejections_for_runs_and_batches(
    conn: sqlite3.Connection, finished: Any, synthetic_dir: Path
) -> None:
    with pytest.raises(OverrideError) as e:
        apply_overrides(conn, finished.run_id, [])
    assert e.value.code == "E_NO_OVERRIDES"
    dup = [
        OverrideRequest("s0", 1, 5, "first reason"),
        OverrideRequest("s0", 1, 6, "second reason"),
    ]
    with pytest.raises(OverrideError) as e2:
        apply_overrides(conn, finished.run_id, dup)
    assert e2.value.code == "E_DUPLICATE"
    with pytest.raises(OverrideError) as e3:
        apply_overrides(conn, "no-such-run", [OverrideRequest("s0", 1, 5, "reason")])
    assert e3.value.code == "E_RUN_NOT_FOUND"
    _, partial = run(conn, uploads(synthetic_dir, ["nexaworks.pdf", "corrupt_co.pdf"]))
    with pytest.raises(OverrideError) as e4:
        apply_overrides(conn, partial.run_id, [OverrideRequest("s1", 1, 5, "reason")])
    assert e4.value.code == "E_SUPPLIER_FAILED"
    _, dead = run(conn, uploads(synthetic_dir, ["corrupt_co.pdf"]))
    with pytest.raises(OverrideError) as e5:
        apply_overrides(conn, dead.run_id, [OverrideRequest("s0", 1, 5, "reason")])
    assert e5.value.code == "E_RUN_FAILED"


def test_overrides_work_on_formula_criteria_too(
    conn: sqlite3.Connection, synthetic_dir: Path
) -> None:
    with_extra_criteria(conn)
    _, out = run(conn, uploads(synthetic_dir))
    export = apply_overrides(
        conn, out.run_id, [OverrideRequest("s0", 6, 10, "Negotiated a lower price")]
    )
    apex = next(s for s in export["suppliers"] if s["supplier_name"] == "Apex Systems")
    cell = next(c for c in apex["computed"]["criteria"] if c["criterion_id"] == 6)
    assert cell["score"] == 10 and cell["original_score"] == 5 and cell["overridden"]
    assert BRIEF  # fixture list is used by other tests in this module

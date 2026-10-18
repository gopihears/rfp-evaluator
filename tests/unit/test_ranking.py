from __future__ import annotations

from datetime import date

import pytest

from core.models import RankedRun, SupplierComputed
from core.tools.ranking_tool import RankOptions, rank, round_half_up
from core.tools.sensitivity import sensitivity
from tests.helpers import make_card, make_criteria, make_supplier, run_ranking

T = make_criteria((60, 40))


def by_id(run: RankedRun) -> dict[str, SupplierComputed]:
    return {s.supplier_id: s for s in run.suppliers}


def gt1() -> RankedRun:
    entries = [
        (make_supplier("A", "A"), [8, 6]),
        (make_supplier("B", "B"), [6, 9]),
        (make_supplier("C", "C"), [8, 9]),
    ]
    return run_ranking(T, entries)[0]


def test_ut_r01_golden_example() -> None:
    run = by_id(gt1())
    assert run["A"].absolute_score == 72.0 and run["B"].absolute_score == 72.0
    assert run["C"].absolute_score == 84.0
    assert run["A"].ppi == pytest.approx(86.666667, abs=1e-9)
    assert run["B"].ppi == 85.0 and run["C"].ppi == 100.0
    assert [s.supplier_id for s in gt1().suppliers] == ["C", "A", "B"]
    assert [s.rank for s in gt1().suppliers] == [1, 2, 3]


def test_ut_r01_gaps_and_relative() -> None:
    a = by_id(gt1())["A"]
    assert [c.gap for c in a.criteria] == [0, -3]
    assert [c.benchmark for c in a.criteria] == [8, 9]
    assert a.criteria[1].relative_pct == pytest.approx(66.666667, abs=1e-9)


def test_ut_r02_different_max_scores() -> None:
    crit = make_criteria((50, 50), (5, 10))
    run, *_ = run_ranking(crit, [(make_supplier("X"), [5, 10]), (make_supplier("Y"), [2, 5])])
    r = by_id(run)
    assert r["X"].absolute_score == 100.0
    assert r["Y"].absolute_score == 45.0


def test_ut_r03_benchmark_ignores_none() -> None:
    run, *_ = run_ranking(T, [(make_supplier("A"), [None, 6]), (make_supplier("B"), [7, 9])])
    assert by_id(run)["A"].criteria[0].benchmark == 7


def test_ut_r05_shared_benchmark() -> None:
    run, *_ = run_ranking(T, [(make_supplier("A"), [8, 8]), (make_supplier("B"), [8, 8])])
    for s in run.suppliers:
        assert [c.gap for c in s.criteria] == [0, 0]
        assert [c.relative_pct for c in s.criteria] == [100.0, 100.0]


def test_ut_r06_zero_benchmark_is_safe() -> None:
    run, *_ = run_ranking(T, [(make_supplier("A"), [0, 0]), (make_supplier("B"), [0, 0])])
    for s in run.suppliers:
        assert s.ppi == 100.0 and s.absolute_score == 0.0


def test_ut_r07_none_score_counts_zero_and_skips_benchmark() -> None:
    run, *_ = run_ranking(T, [(make_supplier("A"), [None, 10]), (make_supplier("B"), [5, 5])])
    r = by_id(run)
    assert r["A"].criteria[0].relative_pct == 0.0 and r["A"].criteria[0].gap is None
    assert r["A"].absolute_score == 40.0
    assert r["B"].criteria[0].benchmark == 5


def test_ut_r08_all_none_on_a_criterion() -> None:
    run, *_ = run_ranking(T, [(make_supplier("A"), [None, 4]), (make_supplier("B"), [None, 8])])
    for s in run.suppliers:
        assert s.criteria[0].benchmark is None
        assert s.criteria[0].relative_pct == 0.0


def test_ut_r09_single_supplier() -> None:
    run, *_ = run_ranking(T, [(make_supplier("A"), [3, 4])])
    s = run.suppliers[0]
    assert s.rank == 1 and s.ppi == 100.0
    assert [w.code for w in run.warnings] == ["W_SINGLE_SUPPLIER"]


def _tied(**kwargs: object) -> list[SupplierComputed]:
    a = make_supplier("A", **kwargs.get("a", {}))  # type: ignore[arg-type]
    b = make_supplier("B", **kwargs.get("b", {}))  # type: ignore[arg-type]
    run, *_ = run_ranking(T, [(b, [7, 7]), (a, [7, 7])])
    return run.suppliers


def test_ut_r10_earlier_submission_wins() -> None:
    order = _tied(a={"submitted": date(2025, 3, 5)}, b={"submitted": date(2025, 3, 1)})
    assert [s.supplier_id for s in order] == ["B", "A"]


def test_ut_r11_higher_experience_wins() -> None:
    order = _tied(a={"experience": 4.5}, b={"experience": 2.0})
    assert [s.supplier_id for s in order] == ["A", "B"]


def test_ut_r12_name_ascending_case_insensitive() -> None:
    order = _tied(a={"name": "Banana"}, b={"name": "apple"})
    assert [s.supplier_name for s in order] == ["apple", "Banana"]


def test_ut_r13_total_order_with_duplicate_names() -> None:
    order = _tied(a={"name": "Same"}, b={"name": "Same"})
    assert [s.supplier_id for s in order] == ["A", "B"]


def test_ut_r14_float_noise_does_not_decide() -> None:
    crit = make_criteria((50.0000001, 49.9999999))
    x = make_supplier("X", submitted=date(2025, 3, 9))
    y = make_supplier("Y", submitted=date(2025, 3, 1))
    run, *_ = run_ranking(crit, [(x, [10, 9]), (y, [9, 10])])
    assert [s.supplier_id for s in run.suppliers] == ["Y", "X"]
    assert run.tie_breaks[0].deciding_rule == "SUBMISSION_DATE"


def test_ut_r15_ranks_contiguous() -> None:
    entries = [(make_supplier(str(i)), [i, 10 - i]) for i in range(1, 6)]
    run, *_ = run_ranking(T, entries)
    assert [s.rank for s in run.suppliers] == [1, 2, 3, 4, 5]


def test_ut_r16_failed_supplier_excluded() -> None:
    good, bad = make_supplier("G"), make_supplier("F")
    cards = [make_card(good, T, [5, 5]), make_card(bad, T, [10, 10], ok=False)]
    run = rank(cards, [good, bad], T)
    r = by_id(run)
    assert r["F"].status == "FAILED" and r["F"].rank is None
    assert r["G"].criteria[0].benchmark == 5 and r["G"].rank == 1


def test_supplier_without_scorecard_is_failed() -> None:
    a, b = make_supplier("A"), make_supplier("B")
    run = rank([make_card(a, T, [5, 5])], [a, b], T)
    assert by_id(run)["B"].status == "FAILED"


def test_ut_r17_weights_not_100_are_normalised() -> None:
    crit = make_criteria((40, 40))
    run, *_ = run_ranking(crit, [(make_supplier("A"), [10, 10])])
    assert run.suppliers[0].absolute_score == 100.0
    assert run.weights_normalised
    assert "W_WEIGHTS_NORMALISED" in [w.code for w in run.warnings]


def test_ut_r18_zero_weight_criterion() -> None:
    crit = make_criteria((100, 0))
    run, *_ = run_ranking(crit, [(make_supplier("A"), [5, 0]), (make_supplier("B"), [10, 10])])
    r = by_id(run)
    assert r["A"].absolute_score == 50.0 and r["B"].rank == 1


def test_no_positive_weight_raises() -> None:
    crit = make_criteria((0, 0))
    with pytest.raises(ValueError):
        run_ranking(crit, [(make_supplier("A"), [1, 1])])


def test_inactive_criteria_ignored() -> None:
    crit = make_criteria((60, 40))
    crit[1] = crit[1].model_copy(update={"is_active": False})
    run, *_ = run_ranking(crit, [(make_supplier("A"), [10])])
    assert run.suppliers[0].absolute_score == 100.0
    assert len(run.suppliers[0].criteria) == 1


def test_ut_r19_explanations() -> None:
    run = gt1()
    assert [t.deciding_rule for t in run.tie_breaks] == ["PPI", "PPI"]
    assert run.tie_breaks[0].higher_name == "C" and run.tie_breaks[0].lower_name == "A"
    assert "100" in run.tie_breaks[0].detail


def test_explanation_rules_for_each_level() -> None:
    entries = [
        (make_supplier("A", "Alpha", date(2025, 1, 1), 4.0), [7, 7]),
        (make_supplier("B", "Beta", date(2025, 1, 2), 4.0), [7, 7]),
        (make_supplier("C", "Gamma", date(2025, 1, 2), 3.0), [7, 7]),
        (make_supplier("D", "Zeta", date(2025, 1, 2), 3.0), [7, 7]),
    ]
    run, *_ = run_ranking(T, entries)
    assert [t.deciding_rule for t in run.tie_breaks] == ["SUBMISSION_DATE", "EXPERIENCE", "NAME"]


def test_ut_r20_display_rounding_half_up() -> None:
    assert round_half_up(72.125) == 72.13
    assert round_half_up(2.675) == 2.68
    assert round_half_up(0.5, 0) == 1.0


def test_ut_r21_knockout_disqualifies() -> None:
    crit = make_criteria((60, 40))
    crit[1] = crit[1].model_copy(update={"is_mandatory": True, "min_score": 5})
    entries = [(make_supplier("A"), [10, 4]), (make_supplier("B"), [6, 6])]
    run, *_ = run_ranking(crit, entries, RankOptions(enable_knockouts=True))
    r = by_id(run)
    assert r["A"].status == "DISQUALIFIED" and r["A"].rank is None
    assert r["A"].disqualified_reasons
    assert r["B"].rank == 1 and r["B"].criteria[0].benchmark == 6


def test_knockouts_off_by_default() -> None:
    crit = make_criteria((60, 40))
    crit[1] = crit[1].model_copy(update={"is_mandatory": True, "min_score": 5})
    run, *_ = run_ranking(crit, [(make_supplier("A"), [10, 1])])
    assert run.suppliers[0].status == "RANKED"


def test_ut_r22_override_recomputes_and_keeps_original() -> None:
    entries = [(make_supplier("A"), [2, 2]), (make_supplier("B"), [5, 5])]
    base, *_ = run_ranking(T, entries)
    assert base.suppliers[0].supplier_id == "B"
    run, *_ = run_ranking(T, entries, RankOptions(overrides={("A", 1): 10, ("A", 2): 10}))
    top = run.suppliers[0]
    assert top.supplier_id == "A"
    assert top.criteria[0].overridden and top.criteria[0].original_score == 2


def test_override_out_of_range_rejected() -> None:
    with pytest.raises(ValueError):
        run_ranking(T, [(make_supplier("A"), [1, 1])], RankOptions(overrides={("A", 1): 11}))


def test_duplicate_supplier_ids_rejected() -> None:
    a = make_supplier("A")
    with pytest.raises(ValueError):
        rank([make_card(a, T, [1, 1])], [a, a], T)


def _sens_fixture():
    crit = make_criteria((55, 45))
    entries = [(make_supplier("A", "A"), [10, 4]), (make_supplier("B", "B"), [4, 10])]
    run, cards, sup = run_ranking(crit, entries)
    return crit, cards, sup, run


def test_ut_r23_one_at_a_time_detects_flip() -> None:
    crit, cards, sup, run = _sens_fixture()
    assert run.suppliers[0].supplier_id == "A"
    res = sensitivity(cards, sup, crit, draws=50)
    assert any(o.changed for o in res.one_at_a_time)
    assert res.base_order == ["A", "B"]


def test_ut_r24_monte_carlo_is_reproducible() -> None:
    crit, cards, sup, _ = _sens_fixture()
    a = sensitivity(cards, sup, crit, draws=200, seed=7)
    b = sensitivity(cards, sup, crit, draws=200, seed=7)
    assert a.model_dump() == b.model_dump()
    assert sum(r.p_rank1 for r in a.robustness) == pytest.approx(1.0)

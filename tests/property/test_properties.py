from __future__ import annotations

import json
import random
from datetime import date, timedelta
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from core.export import build_export, export_json, run_status
from core.models import Scorecard, SupplierInput
from core.tools.ranking_tool import rank
from core.tools.validation_tool import coerce_score, validate
from tests.helpers import make_card, make_criteria, make_supplier

NAMES = ["alpha", "Alpha", "beta", "Gamma", "delta"]


@st.composite
def scenarios(draw: st.DrawFn) -> tuple[Any, list[SupplierInput], list[Scorecard]]:
    n_crit = draw(st.integers(1, 5))
    weights = draw(st.lists(st.integers(1, 50), min_size=n_crit, max_size=n_crit))
    maxes = draw(st.lists(st.sampled_from([5, 10]), min_size=n_crit, max_size=n_crit))
    crit = make_criteria(weights, maxes)
    n_sup = draw(st.integers(1, 6))
    suppliers, cards = [], []
    for i in range(n_sup):
        s = make_supplier(
            f"S{i}",
            draw(st.sampled_from(NAMES)),
            date(2025, 3, 1) + timedelta(days=draw(st.integers(0, 2))),
            draw(st.sampled_from([0.0, 2.5, 4.0, 5.0])),
        )
        scores = [draw(st.one_of(st.none(), st.integers(0, m))) for m in maxes]
        suppliers.append(s)
        cards.append(make_card(s, crit, scores))
    return crit, suppliers, cards


@settings(max_examples=150, deadline=None)
@given(scenarios(), st.randoms(use_true_random=False))
def test_pt01_input_order_does_not_change_output(scn: Any, rnd: random.Random) -> None:
    crit, suppliers, cards = scn
    base = rank(cards, suppliers, crit)
    sup2, cards2 = suppliers[:], cards[:]
    rnd.shuffle(sup2)
    rnd.shuffle(cards2)
    again = rank(cards2, sup2, crit)
    assert [s.model_dump() for s in base.suppliers] == [s.model_dump() for s in again.suppliers]


@settings(max_examples=100, deadline=None)
@given(scenarios())
def test_pt02_identical_inputs_give_byte_identical_export(scn: Any) -> None:
    crit, suppliers, cards = scn

    def export() -> str:
        ranked = rank(cards, suppliers, crit)
        return export_json(build_export(
            run_id="r", created_at="t", status=run_status(ranked), model=None, prompt_hash=None,
            criteria=crit, scorecards=cards, ranked=ranked))

    assert export() == export()


@settings(max_examples=150, deadline=None)
@given(scenarios())
def test_pt03_ranks_are_sequential(scn: Any) -> None:
    crit, suppliers, cards = scn
    ranked = [s for s in rank(cards, suppliers, crit).suppliers if s.status == "RANKED"]
    assert [s.rank for s in ranked] == list(range(1, len(ranked) + 1))


@settings(max_examples=150, deadline=None)
@given(scenarios())
def test_pt04_bounds_and_pt05_order(scn: Any) -> None:
    crit, suppliers, cards = scn
    ranked = [s for s in rank(cards, suppliers, crit).suppliers if s.status == "RANKED"]
    for s in ranked:
        assert s.absolute_score is not None and 0 <= s.absolute_score <= 100
        assert s.ppi is not None and 0 <= s.ppi <= 100
        for c in s.criteria:
            assert 0 <= c.relative_pct <= 100
            assert c.gap is None or c.gap <= 0
    ppis = [s.ppi for s in ranked]
    assert ppis == sorted(ppis, reverse=True)


@settings(max_examples=150, deadline=None)
@given(scenarios(), st.data())
def test_pt06_raising_own_score_never_hurts_own_rank(scn: Any, data: st.DataObject) -> None:
    crit, suppliers, cards = scn
    idx = data.draw(st.integers(0, len(suppliers) - 1))
    cidx = data.draw(st.integers(0, len(crit) - 1))
    current = cards[idx].criteria[cidx].score or 0
    new = data.draw(st.integers(current, crit[cidx].max_score))
    before = {s.supplier_id: s.rank for s in rank(cards, suppliers, crit).suppliers}
    raised = [c.model_copy(deep=True) for c in cards]
    raised[idx].criteria[cidx].score = new
    after = {s.supplier_id: s.rank for s in rank(raised, suppliers, crit).suppliers}
    sid = suppliers[idx].supplier_id
    assert after[sid] is not None and before[sid] is not None
    assert after[sid] <= before[sid]  # type: ignore[operator]


@settings(max_examples=100, deadline=None)
@given(scenarios(), st.integers(2, 5))
def test_pt07_scaling_weights_keeps_order(scn: Any, k: int) -> None:
    crit, suppliers, cards = scn
    scaled = [c.model_copy(update={"weight": c.weight * k}) for c in crit]
    a = [s.supplier_id for s in rank(cards, suppliers, crit).suppliers]
    b = [s.supplier_id for s in rank(cards, suppliers, scaled).suppliers]
    assert a == b


json_values = st.recursive(
    st.none() | st.booleans() | st.integers() | st.floats(allow_nan=True) | st.text(max_size=20),
    lambda children: st.lists(children, max_size=4)
    | st.dictionaries(st.sampled_from(["criteria", "criterion_id", "score", "evidence",
                                        "justification", "risks", "max_score", "x"]),
                      children, max_size=5),
    max_leaves=25,
)


@settings(max_examples=300, deadline=None)
@given(json_values)
def test_pt08_validate_never_raises_and_returns_one_per_criterion(raw: Any) -> None:
    crit = make_criteria((50, 30, 20))
    card = validate(raw, crit, make_supplier("S"), "some source text")
    assert [r.criterion_id for r in card.criteria] == [1, 2, 3]


@settings(max_examples=200, deadline=None)
@given(st.one_of(st.integers(), st.floats(allow_nan=True, allow_infinity=True),
                 st.text(max_size=12), st.none(), st.booleans()))
def test_pt09_scores_always_within_range(value: Any) -> None:
    score, status, _ = coerce_score(value, 10)
    assert score is None or 0 <= score <= 10
    assert (score is None) == (status == "malformed")


@settings(max_examples=50, deadline=None)
@given(st.text(max_size=200))
def test_validate_accepts_arbitrary_strings(raw: str) -> None:
    assert len(validate(raw, make_criteria((100,)), make_supplier("S")).criteria) == 1
    json.dumps(validate(raw, make_criteria((100,)), make_supplier("S")).model_dump(mode="json"))

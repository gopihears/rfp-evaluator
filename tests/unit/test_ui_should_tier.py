from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from core.models import Criterion
from core.overrides import OverrideRequest, apply_overrides
from core.seed import SEED_CRITERIA
from tests.run_helpers import new_conn, run, uploads, with_extra_criteria
from ui import criteria as crit_ui
from ui import results, suppliers

# ------------------------------------------------------------------ criteria: advanced settings


def stored() -> list[Criterion]:
    return list(SEED_CRITERIA)


def test_advanced_defaults_for_new_and_existing_criteria() -> None:
    d = crit_ui.advanced_defaults(None)
    assert d["type"] == "llm_judged" and d["mandatory"] is False and d["min_score"] == 5
    price = Criterion(
        criterion_id=9,
        name="Price",
        weight=10,
        criterion_type="formula",
        formula_kind="highest_is_best",
        formula_params={"label": "Uptime", "unit": "%"},
        is_mandatory=True,
        min_score=7,
    )
    d2 = crit_ui.advanced_defaults(price)
    assert (d2["type"], d2["kind"], d2["label"], d2["unit"], d2["mandatory"], d2["min_score"]) == (
        "formula",
        "highest_is_best",
        "Uptime",
        "%",
        True,
        7,
    )


def test_advanced_from_state_reads_widgets_and_falls_back() -> None:
    state = {
        "adv_type_1": crit_ui.METHODS[1],
        "adv_kind_1": "lowest_is_best",
        "adv_label_1": "Total price",
        "adv_unit_1": "USD",
        "adv_mand_4": True,
        "adv_min_4": 6,
    }
    out = crit_ui.advanced_from_state(state, [1, 2, 4], stored())
    assert out[1]["type"] == "formula" and out[1]["label"] == "Total price"
    assert out[2]["type"] == "llm_judged" and out[2]["mandatory"] is False
    assert out[4]["mandatory"] is True and out[4]["min_score"] == 6
    assert crit_ui.advanced_from_state({}, [3], stored())[3]["type"] == "llm_judged"


def test_frame_to_criteria_applies_formula_and_knockout_settings() -> None:
    df = crit_ui.to_frame(stored())
    advanced = {
        1: {
            "type": "formula",
            "kind": "lowest_is_best",
            "label": " Total price ",
            "unit": "USD",
            "mandatory": False,
            "min_score": 5,
        },
        4: {
            "type": "llm_judged",
            "kind": "lowest_is_best",
            "label": "",
            "unit": "",
            "mandatory": True,
            "min_score": 6,
        },
    }
    out = {c.criterion_id: c for c in crit_ui.frame_to_criteria(df, stored(), {}, advanced)}
    assert out[1].criterion_type == "formula" and out[1].formula_kind == "lowest_is_best"
    assert out[1].formula_params == {"label": "Total price", "unit": "USD"}
    assert out[1].is_mandatory is False and out[1].min_score is None
    assert out[4].criterion_type == "llm_judged" and out[4].formula_kind is None
    assert out[4].is_mandatory and out[4].min_score == 6
    assert out[2].criterion_type == "llm_judged"  # untouched rows keep their defaults


def test_frame_to_criteria_preserves_threshold_band_params() -> None:
    banded = Criterion(
        criterion_id=1,
        name="Weeks",
        weight=100,
        criterion_type="formula",
        formula_kind="threshold_bands",
        formula_params={"label": "Weeks", "unit": "weeks", "bands": [{"up_to": 14, "score": 10}]},
    )
    df = crit_ui.to_frame([banded])
    adv = crit_ui.advanced_from_state({"adv_unit_1": "wk"}, [1], [banded])
    out = crit_ui.frame_to_criteria(df, [banded], {}, adv)[0]
    assert out.formula_kind == "threshold_bands" and out.formula_params["unit"] == "wk"
    assert out.formula_params["bands"] == [{"up_to": 14, "score": 10}]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (
            {
                "criterion_type": "formula",
                "formula_kind": "lowest_is_best",
                "formula_params": {"label": "", "unit": "USD"},
            },
            "describe the figure",
        ),
        ({"criterion_type": "formula", "formula_kind": None}, "choose a formula"),
        ({"is_mandatory": True, "min_score": None}, "minimum score"),
        ({"is_mandatory": True, "min_score": 11}, "minimum score"),
    ],
)
def test_advanced_problems(change: dict[str, Any], message: str) -> None:
    bad = SEED_CRITERIA[0].model_copy(update=change)
    assert any(message in p for p in crit_ui.advanced_problems([bad]))


def test_advanced_problems_ignores_inactive_and_valid() -> None:
    off = SEED_CRITERIA[0].model_copy(
        update={"is_active": False, "is_mandatory": True, "min_score": None}
    )
    ok = SEED_CRITERIA[1].model_copy(update={"is_mandatory": True, "min_score": 4})
    assert crit_ui.advanced_problems([off, ok]) == []


# ------------------------------------------------------------------- suppliers: long sample


def test_long_sample_is_a_long_document() -> None:
    entry = suppliers.long_sample()
    assert entry.error is None and entry.pages and entry.pages > 70
    assert entry.tokens and entry.tokens > 60_000
    assert entry.name == "Apex Systems (long version)"
    assert set(suppliers.RETRIEVAL_CHOICES.values()) == {"auto", "full", "retrieve"}


# ------------------------------------------------------------------------ results helpers


def test_compress_pages() -> None:
    assert results.compress_pages([3, 5, 6, 7, 71]) == "3, 5-7, 71"
    assert results.compress_pages([9, 9, 8]) == "8-9"
    assert results.compress_pages([]) == ""


@pytest.fixture(scope="module")
def rich_export(synthetic_dir: Path) -> dict[str, Any]:
    conn = new_conn()
    with_extra_criteria(conn)
    _, out = run(conn, uploads(synthetic_dir))
    export = apply_overrides(
        conn,
        out.run_id,
        [OverrideRequest("s1", 6, 10, "Price confirmed in the BAFO letter")],
        now=lambda: "2025-06-01T09:00:00Z",
    )
    conn.close()
    return export


def test_facts_rows_and_overrides_helpers(rich_export: dict[str, Any]) -> None:
    rows = results.facts_rows(rich_export, "s2")
    assert [r["Figure"] for r in rows] == ["Total price over the term", "Delivery time in weeks"]
    assert rows[0]["Value"] == "1,050,000" and rows[1]["Value"] == "22"
    assert {r["Quote check"] for r in rows} == {"Verified"}
    assert results.edited_supplier_ids(rich_export) == {"s1"}
    audit = results.overrides_table(rich_export)
    assert audit[0]["Supplier"] == "BrightPath Tech" and audit[0]["New score"] == 10
    assert audit[0]["Reason"] == "Price confirmed in the BAFO letter"
    assert results.overrides_for(rich_export, "s1", 6) and not results.overrides_for(
        rich_export, "s1", 1
    )


def test_helpers_are_safe_on_exports_without_the_new_fields(sample_outcome: Any) -> None:
    export = dict(sample_outcome.export)
    export.pop("overrides", None)
    assert results.edited_supplier_ids(export) == set()
    assert results.overrides_table(export) == []
    first = next(s["supplier_id"] for s in export["suppliers"] if s["computed"]["rank"] == 1)
    assert results.facts_rows(export, first) == []

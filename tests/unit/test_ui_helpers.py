from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pandas as pd
import pytest

from core.models import Criterion
from core.seed import SEED_CRITERIA
from ui import charts, results, suppliers, theme
from ui import criteria as crit_ui
from ui.connect import default_model

TODAY = date(2025, 6, 1)


# ------------------------------------------------------------------------ criteria


def frame() -> pd.DataFrame:
    return crit_ui.to_frame(list(SEED_CRITERIA))


def test_seed_frame_is_valid() -> None:
    df = frame()
    assert list(df.columns) == crit_ui.COLUMNS and len(df) == 5
    assert crit_ui.problems(df) == []
    assert crit_ui.active_total(df) == 100


def test_at05_total_not_100_is_reported() -> None:
    df = frame()
    df.loc[0, "Weight (%)"] = 25.0
    issues = crit_ui.problems(df)
    assert any("total 100%" in i and "95" in i for i in issues)


def _set_names_same(d: pd.DataFrame) -> None:
    d["Name"] = ["Same"] * 5


def _blank_name(d: pd.DataFrame) -> None:
    d.loc[0, "Name"] = " "


def _negative_weight(d: pd.DataFrame) -> None:
    d.loc[1, "Weight (%)"] = -5.0


def _zero_max(d: pd.DataFrame) -> None:
    d.loc[1, "Max score"] = 0


def _none_active(d: pd.DataFrame) -> None:
    d["Active"] = False


@pytest.mark.parametrize(("mutate", "message"), [
    (_set_names_same, "unique"),
    (_blank_name, "needs a name"),
    (_negative_weight, "negative"),
    (_zero_max, "at least 1"),
    (_none_active, "at least one"),
])
def test_criteria_problems(mutate: Any, message: str) -> None:
    df = frame()
    mutate(df)
    assert any(message in i for i in crit_ui.problems(df))


def test_normalise_makes_total_exactly_100() -> None:
    df = frame()
    df["Weight (%)"] = [33.0, 17.0, 11.0, 29.0, 7.0]  # totals 97
    out = crit_ui.normalise(df)
    assert crit_ui.active_total(out) == 100 and crit_ui.problems(out) == []


def test_normalise_ignores_inactive_and_zero_total() -> None:
    df = frame()
    df.loc[4, "Active"] = False
    out = crit_ui.normalise(df)
    assert out.loc[4, "Weight (%)"] == df.loc[4, "Weight (%)"]
    assert crit_ui.active_total(out) == 100
    zero = frame()
    zero["Weight (%)"] = 0.0
    assert crit_ui.normalise(zero)["Weight (%)"].sum() == 0


def test_presets_apply_only_to_standard_names() -> None:
    for name in crit_ui.PRESETS:
        out = crit_ui.apply_preset(frame(), name)
        assert out is not None and crit_ui.active_total(out) == 100
    custom = frame()
    custom.loc[0, "Name"] = "Something Else"
    assert crit_ui.apply_preset(custom, "Balanced") is None


def test_frame_to_criteria_keeps_hidden_fields_and_rubrics() -> None:
    existing = [c.model_copy(update={"is_mandatory": True, "min_score": 4}) if c.criterion_id == 1
                else c for c in SEED_CRITERIA]
    df = crit_ui.to_frame(existing)
    df.loc[0, "Weight (%)"] = 35.0
    out = crit_ui.frame_to_criteria(df, existing, {1: {2: "weak", 5: " ", 8: "strong"}})
    first = out[0]
    assert first.weight == 35.0 and first.is_mandatory and first.min_score == 4
    assert first.rubric == {2: "weak", 8: "strong"}
    assert isinstance(out[1], Criterion) and out[1].criterion_id == 2


# ------------------------------------------------------------------------ suppliers


@pytest.mark.parametrize(("filename", "expected"), [
    ("apex_systems.pdf", "Apex Systems"),
    ("Brightpath-Tech_RFP_Response_v2.pdf", "Brightpath Tech"),
    ("NEXAWORKS final proposal.pdf", "Nexaworks"),
])
def test_guess_name(filename: str, expected: str) -> None:
    assert suppliers.guess_name(filename) == expected


def entry(i: str, name: str = "Acme", submitted: date = TODAY, rating: float = 3.0,
          error: str | None = None) -> suppliers.Entry:
    return suppliers.Entry(id=i, filename=f"{i}.pdf", data=b"", name=name, submitted=submitted,
                           rating=rating, pages=2, tokens=900, error=error)


def test_at07_entry_problems() -> None:
    entries = [entry("a", ""), entry("b", "Same"), entry("c", "same"),
               entry("d", "D", submitted=TODAY + timedelta(days=1)), entry("e", "E", rating=6.0)]
    p = suppliers.entry_problems(entries, TODAY, 10)
    assert any("supplier name" in m for m in p["a"])
    assert any("already uses" in m for m in p["b"]) and any("already uses" in m for m in p["c"])
    assert any("future" in m for m in p["d"]) and any("between 0 and 5" in m for m in p["e"])
    assert p["_run"] == []


def test_run_level_problems() -> None:
    assert suppliers.entry_problems([], TODAY, 10)["_run"] == ["Add at least one supplier PDF."]
    many = [entry(str(i), f"S{i}") for i in range(4)]
    assert "at most 3" in suppliers.entry_problems(many, TODAY, 3)["_run"][0]
    unreadable = [entry("a", "A", error="scan"), entry("b", "B", error="scan")]
    assert "None of the files" in suppliers.entry_problems(unreadable, TODAY, 10)["_run"][0]
    ok = suppliers.entry_problems([entry("a", "A")], TODAY, 10)
    assert not any(ok.values())


def test_at09_valid_batch_has_no_problems_and_converts() -> None:
    entries = [entry("a", " Apex "), entry("b", "Nexa")]
    assert not any(suppliers.entry_problems(entries, TODAY, 10).values())
    ups = suppliers.to_uploads(entries)
    assert [u.supplier.supplier_name for u in ups] == ["Apex", "Nexa"]
    assert ups[0].supplier.supplier_id == "a"


def test_demo_entries_are_the_four_brief_suppliers() -> None:
    entries = suppliers.demo_entries()
    assert [e.name for e in entries] == ["Apex Systems", "BrightPath Tech", "NexaWorks",
                                         "Orbit Digital"]
    assert all(e.error is None and e.pages and e.tokens for e in entries)
    bad = suppliers.error_sample()
    assert bad.error and "scanned" in bad.error


# -------------------------------------------------------------------------- results


def test_results_view_model(sample_outcome: Any) -> None:
    export = sample_outcome.export
    rows = results.leaderboard_rows(export)
    assert [r["rank"] for r in rows] == [1, 2, 3, 4]
    others = results.other_suppliers(export)
    assert [o["name"] for o in others] == ["Corrupt Co"] and others[0]["status"] == "FAILED"
    assert any("scanned" in r.lower() or "readable" in r.lower() for r in others[0]["reasons"])
    first = next(s["supplier_id"] for s in export["suppliers"] if s["computed"]["rank"] == 1)
    table = results.criterion_rows(export, first)
    assert len(table) == 5
    assert set(table[0]) == {"Criterion", "Score", "Benchmark", "Gap", "Relative %", "Weight %",
                             "Contribution"}
    texts = results.tie_break_text(export)
    assert texts and "decided by" in texts[0]


def test_page_refs_and_escaping() -> None:
    assert results.page_refs("see [p.3] and [p. 12], again [p.3]") == [3, 12]
    assert results.page_refs("nothing") == []
    assert theme.esc("<script>alert(1)</script>") == "&lt;script&gt;alert(1)&lt;/script&gt;"
    assert "&lt;b&gt;" in theme.pill("<b>x</b>")


def test_charts_build_from_export(sample_outcome: Any) -> None:
    export = sample_outcome.export
    names = charts.criteria_names(export)
    top = charts.ranked(export)[0]
    assert len(charts.radar(top, names).data) == 2
    assert len(charts.score_bars(export).data) == 2
    for metric in ("score", "relative"):
        assert len(charts.heatmap(export, metric).data[0].y) == 4
    sup = {s["supplier_id"]: s["supplier_name"] for s in export["suppliers"]}
    assert len(charts.robustness(export["sensitivity"], sup).data) == 4


def test_default_model_prefers_flagship_base() -> None:
    assert default_model(["gpt-4o-mini", "gpt-4o", "gpt-4o-2024-08-06"]) == "gpt-4o"
    assert default_model(["o3-mini", "gpt-4.1-mini", "gpt-4.1"]) == "gpt-4.1"
    assert default_model(["weird-model"]) == "weird-model"

"""Analyst score overrides (OPT-1).

An analyst can replace one criterion score with a reason. The model's original answer is never
altered: overrides are an audit trail applied on top, and the ranking is recomputed with the
same deterministic tools. Everything is rebuilt from the saved run export, so it also works
on runs reopened from history.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from core import db
from core.export import build_export, run_status
from core.models import (
    Criterion,
    CriterionResult,
    ExtractedFact,
    RunWarning,
    Scorecard,
    SupplierInput,
)
from core.tools.ranking_tool import RankOptions, rank
from core.tools.sensitivity import sensitivity

MIN_REASON_CHARS = 3


class OverrideError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class OverrideRequest:
    supplier_id: str
    criterion_id: int
    new_score: int
    reason: str


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def rebuild_inputs(
    export: dict[str, Any],
) -> tuple[list[Criterion], list[SupplierInput], list[Scorecard]]:
    """Reconstruct the ranking inputs from a saved run export."""
    criteria = [Criterion(**c) for c in export["criteria_snapshot"]]
    by_supplier_warnings: dict[str, list[RunWarning]] = {}
    for w in export["warnings"]:
        if w["supplier_id"]:
            by_supplier_warnings.setdefault(w["supplier_id"], []).append(RunWarning(**w))
    suppliers: list[SupplierInput] = []
    cards: list[Scorecard] = []
    for s in export["suppliers"]:
        comp, card = s["computed"], s["scorecard"]
        suppliers.append(
            SupplierInput(
                supplier_id=s["supplier_id"],
                supplier_name=s["supplier_name"],
                submission_date=comp["submission_date"],
                experience_rating=comp["experience_rating"],
            )
        )
        if card is None:
            continue
        cards.append(
            Scorecard(
                supplier_id=s["supplier_id"],
                supplier_name=s["supplier_name"],
                criteria=[CriterionResult(**r) for r in card["criteria"]],
                risks=card["risks"],
                overall_summary=card["overall_summary"],
                warnings=by_supplier_warnings.get(s["supplier_id"], []),
                ok=card.get("ok", True),
                facts=[ExtractedFact(**f) for f in card.get("facts", [])],
                evaluation_mode=card.get("evaluation_mode", "full"),
            )
        )
    return criteria, suppliers, cards


def _validate(
    export: dict[str, Any], requests: Sequence[OverrideRequest], criteria: Sequence[Criterion]
) -> None:
    if export["status"] == "FAILED":
        raise OverrideError("E_RUN_FAILED", "A failed run has no scores to override.")
    if not requests:
        raise OverrideError("E_NO_OVERRIDES", "Nothing to apply.")
    by_id = {c.criterion_id: c for c in criteria}
    status = {s["supplier_id"]: s["computed"]["status"] for s in export["suppliers"]}
    seen: set[tuple[str, int]] = set()
    for r in requests:
        if r.supplier_id not in status:
            raise OverrideError("E_UNKNOWN_SUPPLIER", "That supplier is not part of this run.")
        if status[r.supplier_id] == "FAILED":
            raise OverrideError(
                "E_SUPPLIER_FAILED",
                "A supplier that could not be scored has no scores to override.",
            )
        c = by_id.get(r.criterion_id)
        if c is None:
            raise OverrideError("E_UNKNOWN_CRITERION", "That criterion is not part of this run.")
        if not 0 <= r.new_score <= c.max_score:
            raise OverrideError(
                "E_SCORE_RANGE", f"The score for '{c.name}' must be 0-{c.max_score}."
            )
        if len(r.reason.strip()) < MIN_REASON_CHARS:
            raise OverrideError("E_REASON_REQUIRED", "Give a short reason for the change.")
        key = (r.supplier_id, r.criterion_id)
        if key in seen:
            raise OverrideError(
                "E_DUPLICATE", "Only one change per supplier and criterion at a time."
            )
        seen.add(key)


def apply_overrides(
    conn: sqlite3.Connection,
    run_id: str,
    requests: Sequence[OverrideRequest],
    *,
    now: Callable[[], str] = _utc_now,
) -> dict[str, Any]:
    """Apply analyst overrides, recompute the ranking, persist and return the new export."""
    export = db.load_run_export(conn, run_id)
    if export is None:
        raise OverrideError("E_RUN_NOT_FOUND", "That run has no saved result.")
    criteria, suppliers, cards = rebuild_inputs(export)
    _validate(export, requests, criteria)

    names = {c.criterion_id: c.name for c in criteria}
    supplier_names = {s.supplier_id: s.supplier_name for s in suppliers}
    original = {(c.supplier_id, r.criterion_id): r.score for c in cards for r in c.criteria}
    stamp = now()
    new_rows = [
        {
            "supplier_id": r.supplier_id,
            "supplier_name": supplier_names[r.supplier_id],
            "criterion_id": r.criterion_id,
            "criterion_name": names[r.criterion_id],
            "original_score": original[(r.supplier_id, r.criterion_id)],
            "new_score": r.new_score,
            "reason": r.reason.strip(),
            "created_at": stamp,
        }
        for r in requests
    ]
    history = list(export.get("overrides", [])) + new_rows
    effective = {(o["supplier_id"], o["criterion_id"]): o["new_score"] for o in history}

    opts = export.get("options", {})
    rank_options = RankOptions(
        enable_knockouts=bool(opts.get("enable_knockouts")), overrides=effective
    )
    ranked = rank(cards, suppliers, criteria, rank_options)
    old_sens = export.get("sensitivity")
    sens: dict[str, Any] | None = None
    ranked_count = sum(1 for s in ranked.suppliers if s.status == "RANKED")
    if old_sens and ranked_count >= 2:
        sens = sensitivity(
            cards,
            suppliers,
            criteria,
            rank_options,
            draws=int(old_sens["draws"]),
            seed=int(old_sens["seed"]),
        ).model_dump(mode="json")
    status = run_status(ranked)
    new_export = build_export(
        run_id=run_id,
        created_at=export["created_at"],
        status=status,
        model=export["model"],
        prompt_hash=export["prompt_hash"],
        criteria=criteria,
        scorecards=cards,
        ranked=ranked,
        sensitivity=sens,
        overrides=history,
        options=opts,
    )
    db.replace_results(
        conn,
        run_id=run_id,
        status=status,
        scorecards=cards,
        ranked=ranked,
        export=new_export,
        new_overrides=new_rows,
    )
    return new_export

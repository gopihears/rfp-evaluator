"""Build the downloadable run JSON. Never includes API keys or PDF bytes."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from core.models import Criterion, RankedRun, Scorecard

SCHEMA_VERSION = "1.0"


def run_status(ranked: RankedRun) -> str:
    """COMPLETED when nothing failed, PARTIAL when some did, FAILED when none was scored."""
    statuses = [s.status for s in ranked.suppliers]
    scored = [s for s in statuses if s != "FAILED"]
    if not scored:
        return "FAILED"
    return "PARTIAL" if len(scored) < len(statuses) else "COMPLETED"


def build_export(
    *,
    run_id: str,
    created_at: str,
    status: str,
    model: str | None,
    prompt_hash: str | None,
    criteria: Sequence[Criterion],
    scorecards: Sequence[Scorecard],
    ranked: RankedRun,
    sensitivity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cards = {sc.supplier_id: sc for sc in scorecards}
    leaderboard = [
        {
            "rank": s.rank,
            "supplier_name": s.supplier_name,
            "absolute_score": s.absolute_score,
            "ppi": s.ppi,
            "submission_date": s.submission_date.isoformat(),
            "experience_rating": s.experience_rating,
        }
        for s in ranked.suppliers
        if s.status == "RANKED"
    ]
    suppliers: list[dict[str, Any]] = []
    for s in ranked.suppliers:
        card = cards.get(s.supplier_id)
        suppliers.append({
            "supplier_id": s.supplier_id,
            "supplier_name": s.supplier_name,
            "scorecard": None if card is None else {
                "supplier_name": card.supplier_name,
                "criteria": [r.model_dump(mode="json") for r in card.criteria],
                "risks": card.risks,
                "overall_summary": card.overall_summary,
            },
            "computed": s.model_dump(mode="json"),
        })
    warnings = [w.model_dump(mode="json") for sc in scorecards for w in sc.warnings]
    warnings += [w.model_dump(mode="json") for w in ranked.warnings]
    return {
        "schema_version": SCHEMA_VERSION,
        "rfp_run_id": run_id,
        "created_at": created_at,
        "status": status,
        "model": model,
        "prompt_hash": prompt_hash,
        "criteria_snapshot": [c.model_dump(mode="json") for c in criteria if c.is_active],
        "leaderboard": leaderboard,
        "suppliers": suppliers,
        "tie_break_explanations": [t.model_dump(mode="json") for t in ranked.tie_breaks],
        "warnings": warnings,
        "sensitivity": sensitivity,
    }


def export_json(export: dict[str, Any]) -> str:
    """Stable serialisation: sorted keys, so identical inputs give byte-identical output."""
    return json.dumps(export, sort_keys=True, indent=2, ensure_ascii=False)

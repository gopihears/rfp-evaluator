"""Shared builders for tests."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any

from core.models import Criterion, CriterionResult, RankedRun, Scorecard, SupplierInput
from core.tools.ranking_tool import RankOptions, rank


def make_criteria(
    weights: Sequence[float] = (60, 40),
    max_scores: Sequence[int] | None = None,
    **overrides: Any,
) -> list[Criterion]:
    maxes = list(max_scores) if max_scores else [10] * len(weights)
    return [
        Criterion(
            criterion_id=i + 1,
            name=f"Criterion {i + 1}",
            description=f"Description {i + 1}",
            weight=w,
            max_score=m,
            **overrides,
        )
        for i, (w, m) in enumerate(zip(weights, maxes, strict=True))
    ]


def make_supplier(
    sid: str,
    name: str | None = None,
    submitted: date = date(2025, 3, 1),
    experience: float = 3.0,
) -> SupplierInput:
    return SupplierInput(
        supplier_id=sid,
        supplier_name=name or f"Supplier {sid}",
        submission_date=submitted,
        experience_rating=experience,
    )


def make_card(
    supplier: SupplierInput,
    criteria: Sequence[Criterion],
    scores: Sequence[int | None],
    *,
    ok: bool = True,
) -> Scorecard:
    active = [c for c in criteria if c.is_active]
    return Scorecard(
        supplier_id=supplier.supplier_id,
        supplier_name=supplier.supplier_name,
        criteria=[
            CriterionResult(
                criterion_id=c.criterion_id,
                score=s,
                max_score=c.max_score,
                justification="because",
                evidence="quote",
                status="ok" if s is not None else "missing",
            )
            for c, s in zip(active, scores, strict=True)
        ],
        ok=ok,
    )


def run_ranking(
    criteria: Sequence[Criterion],
    entries: Sequence[tuple[SupplierInput, Sequence[int | None]]],
    options: RankOptions | None = None,
) -> tuple[RankedRun, list[Scorecard], list[SupplierInput]]:
    suppliers = [s for s, _ in entries]
    cards = [make_card(s, criteria, scores) for s, scores in entries]
    return rank(cards, suppliers, criteria, options), cards, suppliers


def raw_response(
    criteria: Sequence[Criterion],
    scores: Sequence[Any],
    evidence: str = "The platform supports SSO and audit logging",
    **extra: Any,
) -> dict[str, Any]:
    return {
        "supplier_name": "ignored",
        "criteria": [
            {
                "criterion_id": c.criterion_id,
                "score": s,
                "max_score": c.max_score,
                "justification": f"Reasoning for {c.name}",
                "evidence": evidence,
            }
            for c, s in zip(criteria, scores, strict=True)
        ],
        "risks": ["Tight schedule"],
        "overall_summary": "Solid proposal.",
        **extra,
    }

"""Ranking tool: deterministic scoring, benchmarking, PPI, tie-breaks and ranks.

Pure functions only: no I/O, no clock, no randomness. All arithmetic uses Decimal and
comparisons use values fixed to PPI_PRECISION places, so float noise can never flip a rank.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from core.models import (
    Criterion,
    CriterionComputed,
    RankedRun,
    RunWarning,
    Scorecard,
    SupplierComputed,
    SupplierInput,
    TieBreak,
)

PPI_PLACES = 6
_QUANT = Decimal(1).scaleb(-PPI_PLACES)
_HUNDRED = Decimal(100)
_ZERO = Decimal(0)

Overrides = Mapping[tuple[str, int], int]


@dataclass(frozen=True)
class RankOptions:
    """Optional behaviours; defaults reproduce the brief exactly."""

    enable_knockouts: bool = False
    overrides: Overrides = field(default_factory=dict)


def q6(value: Decimal) -> Decimal:
    return value.quantize(_QUANT, rounding=ROUND_HALF_UP)


def round_half_up(value: float | Decimal, places: int = 2) -> float:
    """Display rounding (half up), independent of binary float quirks."""
    dec = value if isinstance(value, Decimal) else Decimal(repr(value))
    return float(dec.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))


def _dec(value: float | int) -> Decimal:
    return Decimal(repr(value)) if isinstance(value, float) else Decimal(value)


def _scores_for(
    card: Scorecard,
    active: Sequence[Criterion],
    overrides: Overrides,
) -> tuple[dict[int, int | None], dict[int, int | None]]:
    """Return (effective scores, original scores for overridden criteria)."""
    by_cid = {r.criterion_id: r.score for r in card.criteria}
    effective: dict[int, int | None] = {}
    originals: dict[int, int | None] = {}
    for c in active:
        original = by_cid.get(c.criterion_id)
        key = (card.supplier_id, c.criterion_id)
        if key in overrides:
            new = overrides[key]
            if not 0 <= new <= c.max_score:
                raise ValueError(f"Override {new} outside 0-{c.max_score} for criterion {c.name}")
            effective[c.criterion_id] = new
            originals[c.criterion_id] = original
        else:
            effective[c.criterion_id] = original
    return effective, originals


def rank(
    scorecards: Sequence[Scorecard],
    suppliers: Sequence[SupplierInput],
    criteria: Sequence[Criterion],
    options: RankOptions | None = None,
) -> RankedRun:
    """Score, benchmark and rank suppliers. Raises ValueError on unusable configuration."""
    opts = options or RankOptions()
    active = sorted((c for c in criteria if c.is_active), key=lambda c: c.criterion_id)
    if not active:
        raise ValueError("No active criteria.")
    ids = [s.supplier_id for s in suppliers]
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate supplier_id in suppliers.")

    raw_weights = {c.criterion_id: _dec(c.weight) for c in active}
    total_weight = sum(raw_weights.values(), _ZERO)
    if total_weight <= 0:
        raise ValueError("Active criteria have no positive weight.")
    warnings: list[RunWarning] = []
    normalised = total_weight != _HUNDRED
    weights = {cid: w * _HUNDRED / total_weight for cid, w in raw_weights.items()}
    if normalised:
        warnings.append(RunWarning.make(
            "W_WEIGHTS_NORMALISED",
            f"Active weights totalled {total_weight}; they were rescaled to 100.",
        ))

    cards = {sc.supplier_id: sc for sc in scorecards}
    max_by_cid = {c.criterion_id: c.max_score for c in active}

    scores: dict[str, dict[int, int | None]] = {}
    originals: dict[str, dict[int, int | None]] = {}
    failed: list[SupplierInput] = []
    for s in suppliers:
        card = cards.get(s.supplier_id)
        if card is None or not card.ok:
            failed.append(s)
            continue
        scores[s.supplier_id], originals[s.supplier_id] = _scores_for(card, active, opts.overrides)

    # Knock-out (OPT-2): mandatory criteria below their minimum disqualify a supplier.
    disqualified: dict[str, list[str]] = {}
    if opts.enable_knockouts:
        for s in suppliers:
            if s.supplier_id not in scores:
                continue
            reasons = []
            for c in active:
                if c.is_mandatory and c.min_score is not None:
                    got = scores[s.supplier_id][c.criterion_id]
                    if got is None or got < c.min_score:
                        shown = "no valid score" if got is None else f"scored {got}"
                        reasons.append(f"{c.name}: {shown}, minimum {c.min_score}")
            if reasons:
                disqualified[s.supplier_id] = reasons

    eligible_ids = [s.supplier_id for s in suppliers
                    if s.supplier_id in scores and s.supplier_id not in disqualified]

    # Benchmark: highest valid score per criterion among eligible suppliers.
    benchmark: dict[int, int | None] = {}
    for c in active:
        valid = [scores[sid][c.criterion_id] for sid in eligible_ids
                 if scores[sid][c.criterion_id] is not None]
        benchmark[c.criterion_id] = max(v for v in valid if v is not None) if valid else None

    by_supplier = {s.supplier_id: s for s in suppliers}
    computed: dict[str, SupplierComputed] = {}
    ppi_exact: dict[str, Decimal] = {}
    for sid, crit_scores in scores.items():
        s = by_supplier[sid]
        absolute = _ZERO
        ppi_sum = _ZERO
        rows: list[CriterionComputed] = []
        for c in active:
            score = crit_scores[c.criterion_id]
            bench = benchmark[c.criterion_id]
            w = weights[c.criterion_id]
            if score is None:
                rel = _ZERO
                contribution = _ZERO
                gap = None
            else:
                contribution = Decimal(score) / Decimal(max_by_cid[c.criterion_id]) * w
                gap = score - bench if bench is not None else None
                if bench is None:
                    rel = _ZERO
                elif bench == 0:
                    rel = _HUNDRED
                else:
                    rel = q6(Decimal(score) / Decimal(bench) * _HUNDRED)
            absolute += contribution
            ppi_sum += rel * w
            overridden = c.criterion_id in originals[sid]
            rows.append(CriterionComputed(
                criterion_id=c.criterion_id,
                score=score,
                max_score=c.max_score,
                weight=float(q6(w)),
                benchmark=bench,
                gap=gap,
                relative_pct=float(q6(rel)),
                weighted_contribution=float(q6(contribution)),
                overridden=overridden,
                original_score=originals[sid].get(c.criterion_id) if overridden else None,
            ))
        ppi = q6(ppi_sum / _HUNDRED)  # weights are normalised to sum 100
        ppi_exact[sid] = ppi
        computed[sid] = SupplierComputed(
            supplier_id=sid,
            supplier_name=s.supplier_name,
            submission_date=s.submission_date,
            experience_rating=s.experience_rating,
            status="DISQUALIFIED" if sid in disqualified else "RANKED",
            absolute_score=float(q6(absolute)),
            ppi=float(ppi),
            criteria=rows,
            disqualified_reasons=disqualified.get(sid, []),
        )

    def sort_key(sid: str) -> tuple[Decimal, object, Decimal, str, str]:
        s = by_supplier[sid]
        return (-ppi_exact[sid], s.submission_date, -_dec(s.experience_rating),
                s.supplier_name.casefold(), sid)

    ordered = sorted(eligible_ids, key=sort_key)
    for position, sid in enumerate(ordered, start=1):
        computed[sid].rank = position

    tie_breaks = [_explain(by_supplier, ppi_exact, a, b, i + 1)
                  for i, (a, b) in enumerate(zip(ordered, ordered[1:], strict=False))]

    if len(ordered) == 1:
        warnings.append(RunWarning.make(
            "W_SINGLE_SUPPLIER",
            "Only one supplier was ranked, so peer benchmarks are trivial (PPI = 100).",
        ))

    dq_sorted = sorted(
        disqualified, key=lambda sid: (by_supplier[sid].supplier_name.casefold(), sid)
    )
    failed_sorted = sorted(failed, key=lambda s: (s.supplier_name.casefold(), s.supplier_id))
    result_suppliers = [computed[sid] for sid in ordered] + [computed[sid] for sid in dq_sorted]
    for s in failed_sorted:
        result_suppliers.append(SupplierComputed(
            supplier_id=s.supplier_id,
            supplier_name=s.supplier_name,
            submission_date=s.submission_date,
            experience_rating=s.experience_rating,
            status="FAILED",
        ))

    return RankedRun(
        suppliers=result_suppliers,
        tie_breaks=tie_breaks,
        warnings=warnings,
        weights_normalised=normalised,
    )


def _explain(
    by_supplier: Mapping[str, SupplierInput],
    ppi: Mapping[str, Decimal],
    a: str,
    b: str,
    higher_rank: int,
) -> TieBreak:
    """Name the first rule that separates two adjacent suppliers in the final order."""
    sa, sb = by_supplier[a], by_supplier[b]
    if ppi[a] != ppi[b]:
        rule = "PPI"
        detail = f"Higher PPI: {ppi[a]} vs {ppi[b]}."
    elif sa.submission_date != sb.submission_date:
        rule = "SUBMISSION_DATE"
        detail = (f"Tied on PPI {ppi[a]}; earlier submission "
                  f"({sa.submission_date.isoformat()} before {sb.submission_date.isoformat()}).")
    elif _dec(sa.experience_rating) != _dec(sb.experience_rating):
        rule = "EXPERIENCE"
        detail = (f"Tied on PPI {ppi[a]} and submission date; higher experience rating "
                  f"({sa.experience_rating} vs {sb.experience_rating}).")
    elif sa.supplier_name.casefold() != sb.supplier_name.casefold():
        rule = "NAME"
        detail = (f"Tied on PPI, date and experience; name ascending "
                  f"('{sa.supplier_name}' before '{sb.supplier_name}').")
    else:
        rule = "SUPPLIER_ID"
        detail = "Fully tied, including name; ordered by supplier id for a stable result."
    return TieBreak(
        higher_rank=higher_rank,
        lower_rank=higher_rank + 1,
        higher_name=sa.supplier_name,
        lower_name=sb.supplier_name,
        deciding_rule=rule,
        detail=detail,
    )

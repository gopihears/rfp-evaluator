"""Weight sensitivity / rank robustness (OPT-7). Deterministic: seeded, pure."""

from __future__ import annotations

import random
from collections.abc import Sequence

from pydantic import BaseModel

from core.models import Criterion, Scorecard, SupplierInput
from core.tools.ranking_tool import RankOptions, rank

DEFAULT_SEED = 42


class OneAtATime(BaseModel):
    criterion_id: int
    delta: float
    order: list[str]
    changed: bool


class SupplierRobustness(BaseModel):
    supplier_id: str
    p_rank1: float
    rank_distribution: dict[int, float]


class SensitivityResult(BaseModel):
    base_order: list[str]
    one_at_a_time: list[OneAtATime]
    robustness: list[SupplierRobustness]
    draws: int
    seed: int


def _order(
    scorecards: Sequence[Scorecard],
    suppliers: Sequence[SupplierInput],
    criteria: Sequence[Criterion],
    options: RankOptions | None,
) -> list[str]:
    run = rank(scorecards, suppliers, criteria, options)
    return [s.supplier_id for s in run.suppliers if s.status == "RANKED"]


def _with_weights(criteria: Sequence[Criterion], weights: dict[int, float]) -> list[Criterion]:
    return [c.model_copy(update={"weight": weights[c.criterion_id]}) if c.is_active else c
            for c in criteria]


def sensitivity(
    scorecards: Sequence[Scorecard],
    suppliers: Sequence[SupplierInput],
    criteria: Sequence[Criterion],
    options: RankOptions | None = None,
    *,
    delta: float = 10.0,
    draws: int = 1000,
    seed: int = DEFAULT_SEED,
) -> SensitivityResult:
    active = [c for c in criteria if c.is_active]
    total = sum(c.weight for c in active)
    base = {c.criterion_id: c.weight * 100.0 / total for c in active}
    base_order = _order(scorecards, suppliers, criteria, options)

    oat: list[OneAtATime] = []
    for c in active:
        others = 100.0 - base[c.criterion_id]
        for sign in (1.0, -1.0):
            shifted = min(max(base[c.criterion_id] + sign * delta, 0.0), 100.0)
            weights = dict(base)
            weights[c.criterion_id] = shifted
            if others > 0:
                scale = (100.0 - shifted) / others
                for other in active:
                    if other.criterion_id != c.criterion_id:
                        weights[other.criterion_id] = base[other.criterion_id] * scale
            if sum(weights.values()) <= 0:
                continue
            order = _order(scorecards, suppliers, _with_weights(criteria, weights), options)
            oat.append(OneAtATime(criterion_id=c.criterion_id, delta=sign * delta,
                                  order=order, changed=order != base_order))

    rng = random.Random(seed)
    counts: dict[str, dict[int, int]] = {sid: {} for sid in base_order}
    for _ in range(draws):
        gammas = {cid: rng.gammavariate(max(w / 10.0, 0.1), 1.0) for cid, w in base.items()}
        norm = sum(gammas.values()) or 1.0
        weights = {cid: g / norm * 100.0 for cid, g in gammas.items()}
        order = _order(scorecards, suppliers, _with_weights(criteria, weights), options)
        for position, sid in enumerate(order, start=1):
            counts[sid][position] = counts[sid].get(position, 0) + 1

    robustness = []
    for sid in base_order:
        dist = {pos: n / draws for pos, n in sorted(counts[sid].items())} if draws else {}
        robustness.append(SupplierRobustness(
            supplier_id=sid, p_rank1=dist.get(1, 0.0), rank_distribution=dist))
    return SensitivityResult(base_order=base_order, one_at_a_time=oat,
                             robustness=robustness, draws=draws, seed=seed)

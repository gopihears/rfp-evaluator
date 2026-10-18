"""Shared helpers for tests that run the whole pipeline on the synthetic proposals."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

from core import db
from core.models import Criterion, SupplierInput
from core.orchestrator import (
    ProgressEvent,
    RunOptions,
    RunOutcome,
    SupplierUpload,
    collect,
    run_evaluation,
)
from core.seed import seed_criteria
from core.tools.demo_responder import demo_responder
from core.tools.fake_provider import FakeProvider

META = {  # file -> (supplier name, submission date, experience rating)
    "apex_systems.pdf": ("Apex Systems", date(2025, 3, 3), 4.5),
    "brightpath_tech.pdf": ("BrightPath Tech", date(2025, 3, 1), 2.0),
    "nexaworks.pdf": ("NexaWorks", date(2025, 3, 4), 4.0),
    "orbit_digital.pdf": ("Orbit Digital", date(2025, 3, 4), 4.5),
    "long_apex_80p.pdf": ("Apex Systems", date(2025, 3, 3), 4.5),
}
BRIEF = ["apex_systems.pdf", "brightpath_tech.pdf", "nexaworks.pdf", "orbit_digital.pdf"]


def new_conn() -> sqlite3.Connection:
    conn = db.connect(":memory:", check_same_thread=False)
    db.init_db(conn)
    seed_criteria(conn)
    return conn


def uploads(synthetic_dir: Path, files: list[str] | None = None) -> list[SupplierUpload]:
    out = []
    for i, name in enumerate(files or BRIEF):
        supplier, submitted, experience = META.get(name, (name, date(2025, 3, 2), 3.0))
        out.append(
            SupplierUpload(
                SupplierInput(
                    supplier_id=f"s{i}",
                    supplier_name=supplier,
                    submission_date=submitted,
                    experience_rating=experience,
                    filename=name,
                ),
                (synthetic_dir / name).read_bytes(),
            )
        )
    return out


def run(
    conn: sqlite3.Connection, ups: list[SupplierUpload], provider: Any = None, **kw: Any
) -> tuple[list[ProgressEvent], RunOutcome]:
    provider = provider or FakeProvider(demo_responder)
    options = kw.pop("options", RunOptions(model="demo-model", sensitivity_draws=50))
    return collect(run_evaluation(ups, provider=provider, conn=conn, options=options, **kw))


def with_extra_criteria(
    conn: sqlite3.Connection, *, price_weight: float = 12, weeks_weight: float = 8
) -> None:
    """Seed criteria shrunk to make room for a Price and a Delivery-time formula criterion."""
    crit = db.load_criteria(conn)
    shrink = {1: 25, 2: 15, 3: 10, 4: 20, 5: 10}
    crit = [c.model_copy(update={"weight": shrink[c.criterion_id]}) for c in crit]
    crit.append(
        Criterion(
            criterion_id=6,
            name="Total Price",
            weight=price_weight,
            criterion_type="formula",
            formula_kind="lowest_is_best",
            formula_params={"label": "Total price over the term", "unit": "USD"},
        )
    )
    crit.append(
        Criterion(
            criterion_id=7,
            name="Delivery Time",
            weight=weeks_weight,
            criterion_type="formula",
            formula_kind="lowest_is_best",
            formula_params={"label": "Delivery time in weeks", "unit": "weeks"},
        )
    )
    db.save_criteria(conn, crit)


def by_name(outcome: RunOutcome) -> dict[str, Any]:
    return {s.supplier_name: s for s in outcome.ranked.suppliers}

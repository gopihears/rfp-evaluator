"""Create the SQLite database and seed the sample evaluation criteria.

Usage: python -m core.seed [db_path]
"""

from __future__ import annotations

import sqlite3
import sys

from core.config import load_settings
from core.db import connect, init_db
from core.models import Criterion

SEED_CRITERIA: list[Criterion] = [
    Criterion(
        criterion_id=1,
        name="Technical Capability",
        description="Architecture, integrations, scalability, technical fit",
        weight=30,
        rubric={
            2: "Generic claims; no architecture or integration detail.",
            5: "Credible architecture; integrations or scalability only partly addressed.",
            8: "Detailed architecture, named integrations, scalability evidence, clear fit.",
        },
    ),
    Criterion(
        criterion_id=2,
        name="Implementation Plan",
        description="Timeline, milestones, staffing, risk plan",
        weight=20,
        rubric={
            2: "Vague timeline; no milestones or staffing.",
            5: "Timeline and milestones given; staffing or risk plan thin.",
            8: "Phased plan with milestones, named roles, staffing levels and a risk plan.",
        },
    ),
    Criterion(
        criterion_id=3,
        name="Commercial Value",
        description="Pricing clarity, total cost, assumptions",
        weight=20,
        rubric={
            2: "No price table or unclear total cost.",
            5: "Price table present; assumptions or recurring costs unclear.",
            8: "Transparent itemised pricing, total cost of ownership and explicit assumptions.",
        },
    ),
    Criterion(
        criterion_id=4,
        name="Security & Compliance",
        description="Controls, certifications, privacy, auditability",
        weight=20,
        rubric={
            2: "No certifications or controls described.",
            5: "Some controls or certifications; privacy or audit detail missing.",
            8: "Named certifications, controls, privacy handling and audit rights.",
        },
    ),
    Criterion(
        criterion_id=5,
        name="Support & Experience",
        description="Support model, similar projects, references",
        weight=10,
        rubric={
            2: "No support model or references.",
            5: "Support model or some relevant experience described.",
            8: "Defined support tiers/SLAs, similar projects and verifiable references.",
        },
    ),
]


def seed_criteria(conn: sqlite3.Connection) -> int:
    """Insert the sample criteria if absent (idempotent). Returns rows inserted."""
    import json

    inserted = 0
    with conn:
        for c in SEED_CRITERIA:
            cur = conn.execute(
                "INSERT OR IGNORE INTO evaluation_criteria (criterion_id, name, description,"
                " weight, max_score, is_active, rubric_json) VALUES (?,?,?,?,?,1,?)",
                (
                    c.criterion_id,
                    c.name,
                    c.description,
                    c.weight,
                    c.max_score,
                    json.dumps(c.rubric, sort_keys=True) if c.rubric else None,
                ),
            )
            inserted += cur.rowcount
    return inserted


def main(argv: list[str]) -> None:
    path = argv[1] if len(argv) > 1 else load_settings().db_path
    conn = connect(path)
    init_db(conn)
    n = seed_criteria(conn)
    conn.close()
    print(f"Database ready at {path} ({n} criteria inserted)")


if __name__ == "__main__":
    main(sys.argv)

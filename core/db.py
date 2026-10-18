"""SQLite access layer. The only module that touches the database."""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

from core.models import Criterion, RankedRun, Scorecard
from core.tools.formula_scoring import check_formula_config, formula_criteria

_SCHEMA = Path(__file__).with_name("schema.sql")
_ASSUMED_TOTAL = Decimal(100)


class CriteriaError(ValueError):
    """Raised when a criteria set cannot be saved or used; `code` is a stable error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def connect(path: str, *, check_same_thread: bool = True) -> sqlite3.Connection:
    """Open the database. Streamlit reruns scripts on varying threads, so the UI passes False."""
    conn = sqlite3.connect(path, check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if path != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA.read_text(encoding="utf-8"))
    conn.commit()


def new_run_id() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------- criteria


def check_criteria(criteria: Sequence[Criterion]) -> None:
    """Validate a criteria set for use in a run (raises CriteriaError)."""
    names = [c.name.strip().casefold() for c in criteria]
    if len(set(names)) != len(names):
        raise CriteriaError("E_DUPLICATE_NAME", "Criterion names must be unique.")
    ids = [c.criterion_id for c in criteria]
    if len(set(ids)) != len(ids):
        raise CriteriaError("E_DUPLICATE_ID", "Criterion ids must be unique.")
    active = [c for c in criteria if c.is_active]
    if not active:
        raise CriteriaError("E_NO_ACTIVE_CRITERIA", "At least one criterion must be active.")
    total = sum((Decimal(str(c.weight)) for c in active), Decimal(0))
    if total != _ASSUMED_TOTAL:
        raise CriteriaError(
            "E_WEIGHTS_TOTAL", f"Active weights must total 100% (currently {total}%)."
        )
    problems = [p for c in formula_criteria(active) for p in check_formula_config(c)]
    if problems:
        raise CriteriaError("E_FORMULA_CONFIG", " ".join(problems))
    mandatory = [c for c in active if c.is_mandatory and c.min_score is None]
    if mandatory:
        raise CriteriaError(
            "E_KNOCKOUT_CONFIG", f"'{mandatory[0].name}' is mandatory but has no minimum score."
        )


def _row_to_criterion(row: sqlite3.Row) -> Criterion:
    rubric = json.loads(row["rubric_json"]) if row["rubric_json"] else None
    params = json.loads(row["formula_params_json"]) if row["formula_params_json"] else None
    return Criterion(
        criterion_id=row["criterion_id"],
        name=row["name"],
        description=row["description"],
        weight=row["weight"],
        max_score=row["max_score"],
        is_active=bool(row["is_active"]),
        rubric=rubric,
        is_mandatory=bool(row["is_mandatory"]),
        min_score=row["min_score"],
        criterion_type=row["criterion_type"],
        formula_kind=row["formula_kind"],
        formula_params=params,
    )


def load_criteria(conn: sqlite3.Connection, *, active_only: bool = False) -> list[Criterion]:
    sql = "SELECT * FROM evaluation_criteria"
    if active_only:
        sql += " WHERE is_active = 1"
    sql += " ORDER BY criterion_id"
    return [_row_to_criterion(r) for r in conn.execute(sql)]


def load_active_criteria(conn: sqlite3.Connection) -> list[Criterion]:
    return load_criteria(conn, active_only=True)


def _criterion_params(c: Criterion) -> dict[str, Any]:
    return {
        "criterion_id": c.criterion_id,
        "name": c.name.strip(),
        "description": c.description,
        "weight": c.weight,
        "max_score": c.max_score,
        "is_active": int(c.is_active),
        "rubric_json": json.dumps(c.rubric, sort_keys=True) if c.rubric else None,
        "is_mandatory": int(c.is_mandatory),
        "min_score": c.min_score,
        "criterion_type": c.criterion_type,
        "formula_kind": c.formula_kind,
        "formula_params_json": (
            json.dumps(c.formula_params, sort_keys=True) if c.formula_params else None
        ),
    }


_UPSERT_CRITERION = """
INSERT INTO evaluation_criteria
  (criterion_id, name, description, weight, max_score, is_active, rubric_json,
   is_mandatory, min_score, criterion_type, formula_kind, formula_params_json)
VALUES
  (:criterion_id, :name, :description, :weight, :max_score, :is_active, :rubric_json,
   :is_mandatory, :min_score, :criterion_type, :formula_kind, :formula_params_json)
ON CONFLICT(criterion_id) DO UPDATE SET
  name = excluded.name, description = excluded.description, weight = excluded.weight,
  max_score = excluded.max_score, is_active = excluded.is_active,
  rubric_json = excluded.rubric_json, is_mandatory = excluded.is_mandatory,
  min_score = excluded.min_score, criterion_type = excluded.criterion_type,
  formula_kind = excluded.formula_kind, formula_params_json = excluded.formula_params_json
"""


def save_criteria(conn: sqlite3.Connection, criteria: Sequence[Criterion]) -> None:
    """Validate then upsert the full criteria set atomically."""
    check_criteria(criteria)
    with conn:
        for c in criteria:
            conn.execute(_UPSERT_CRITERION, _criterion_params(c))


# -------------------------------------------------------------------- runs


def start_run(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    created_at: str,
    model: str | None = None,
    prompt_hash: str | None = None,
    options: dict[str, Any] | None = None,
    schema_version: str = "1.0",
) -> None:
    with conn:
        conn.execute(
            "INSERT INTO rfp_runs (rfp_run_id, created_at, status, model, prompt_hash,"
            " schema_version, options_json) VALUES (?, ?, 'RUNNING', ?, ?, ?, ?)",
            (
                run_id,
                created_at,
                model,
                prompt_hash,
                schema_version,
                json.dumps(options or {}, sort_keys=True),
            ),
        )


def fail_run(conn: sqlite3.Connection, run_id: str) -> None:
    """Mark a run FAILED (used when an unexpected error interrupts it)."""
    with conn:
        conn.execute("UPDATE rfp_runs SET status = 'FAILED' WHERE rfp_run_id = ?", (run_id,))


def _write_children(
    conn: sqlite3.Connection,
    run_id: str,
    status: str,
    scorecards: Sequence[Scorecard],
    ranked: RankedRun,
    export: dict[str, Any],
) -> None:
    """Insert supplier/criterion/warning rows and finalise the run.

    The caller owns the transaction (use `with conn:`), so this is atomic with its caller.
    """
    cards = {sc.supplier_id: sc for sc in scorecards}
    export_suppliers = {s["supplier_id"]: s for s in export["suppliers"]}
    for s in ranked.suppliers:
        result = export_suppliers[s.supplier_id]
        conn.execute(
            "INSERT INTO supplier_results (rfp_run_id, supplier_id, supplier_name,"
            " submission_date, experience_rating, status, absolute_score, ppi, final_rank,"
            " result_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                run_id,
                s.supplier_id,
                s.supplier_name,
                s.submission_date.isoformat(),
                s.experience_rating,
                s.status,
                s.absolute_score,
                s.ppi,
                s.rank,
                json.dumps(result, sort_keys=True, ensure_ascii=False),
            ),
        )
        card = cards.get(s.supplier_id)
        by_cid = {r.criterion_id: r for r in card.criteria} if card else {}
        for cc in s.criteria:
            res = by_cid.get(cc.criterion_id)
            verified = res.evidence_verified if res else None
            conn.execute(
                "INSERT INTO criterion_scores (rfp_run_id, supplier_id, criterion_id, score,"
                " benchmark, gap, relative_pct, weighted_contribution, status,"
                " evidence_verified) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    s.supplier_id,
                    cc.criterion_id,
                    cc.score,
                    cc.benchmark,
                    cc.gap,
                    cc.relative_pct,
                    cc.weighted_contribution,
                    res.status if res else None,
                    None if verified is None else int(verified),
                ),
            )
    all_warnings = [w for sc in scorecards for w in sc.warnings] + list(ranked.warnings)
    for w in all_warnings:
        conn.execute(
            "INSERT INTO run_warnings (rfp_run_id, supplier_id, criterion_id, code, severity,"
            " message) VALUES (?,?,?,?,?,?)",
            (run_id, w.supplier_id, w.criterion_id, w.code, w.severity, w.message),
        )
    conn.execute(
        "UPDATE rfp_runs SET status = ?, export_json = ? WHERE rfp_run_id = ?",
        (status, json.dumps(export, sort_keys=True, ensure_ascii=False), run_id),
    )


def persist_results(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    status: str,
    criteria: Sequence[Criterion],
    scorecards: Sequence[Scorecard],
    ranked: RankedRun,
    export: dict[str, Any],
) -> None:
    """Write every child row and finalise the run in ONE transaction (all or nothing)."""
    with conn:
        for c in criteria:
            p = _criterion_params(c)
            conn.execute(
                "INSERT INTO run_criteria_snapshot (rfp_run_id, criterion_id, name, description,"
                " weight, max_score, rubric_json, is_mandatory, min_score, criterion_type,"
                " formula_kind, formula_params_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    p["criterion_id"],
                    p["name"],
                    p["description"],
                    p["weight"],
                    p["max_score"],
                    p["rubric_json"],
                    p["is_mandatory"],
                    p["min_score"],
                    p["criterion_type"],
                    p["formula_kind"],
                    p["formula_params_json"],
                ),
            )
        _write_children(conn, run_id, status, scorecards, ranked, export)


def replace_results(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    status: str,
    scorecards: Sequence[Scorecard],
    ranked: RankedRun,
    export: dict[str, Any],
    new_overrides: Sequence[dict[str, Any]],
) -> None:
    """Rewrite a finished run's results after an analyst override, atomically.

    The criteria snapshot and the model's original scorecards are kept; overrides are appended
    to the audit table and nothing is ever deleted from it.
    """
    with conn:
        conn.execute("DELETE FROM supplier_results WHERE rfp_run_id = ?", (run_id,))
        conn.execute("DELETE FROM run_warnings WHERE rfp_run_id = ?", (run_id,))
        _write_children(conn, run_id, status, scorecards, ranked, export)
        for o in new_overrides:
            conn.execute(
                "INSERT INTO score_overrides (rfp_run_id, supplier_id, criterion_id,"
                " original_score, new_score, reason, created_at) VALUES (?,?,?,?,?,?,?)",
                (run_id, o["supplier_id"], o["criterion_id"], o["original_score"],
                 o["new_score"], o["reason"], o["created_at"]),
            )


def list_overrides(conn: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
    """The audit trail for a run, oldest first."""
    rows = conn.execute(
        "SELECT supplier_id, criterion_id, original_score, new_score, reason, created_at"
        " FROM score_overrides WHERE rfp_run_id = ? ORDER BY id", (run_id,))
    return [dict(r) for r in rows]


def list_runs(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Newest first; ties broken by run id for a stable order."""
    rows = conn.execute(
        "SELECT rfp_run_id, created_at, status, model FROM rfp_runs"
        " ORDER BY created_at DESC, rfp_run_id DESC"
    )
    return [dict(r) for r in rows]


def load_run_export(conn: sqlite3.Connection, run_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT export_json FROM rfp_runs WHERE rfp_run_id = ?", (run_id,)
    ).fetchone()
    if row is None or row["export_json"] is None:
        return None
    loaded: dict[str, Any] = json.loads(row["export_json"])
    return loaded

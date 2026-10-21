"""Backup and restore of saved runs (and criteria) as a JSON bundle or a SQLite file.

The bundle is what the browser keeps for automatic restore and what the user can download.
Imports are untrusted input: they are size-limited, structure-checked, and every run is rebuilt
and re-ranked through the normal deterministic code, never trusted as stored rows.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from core import db
from core.models import Criterion
from core.overrides import rebuild_inputs
from core.tools.ranking_tool import RankOptions, rank

FORMAT = "rfp-evaluator-backup"
VERSION = 1
MAX_RUNS = 200
MAX_BYTES = 40_000_000
_SQLITE_MAGIC = b"SQLite format 3\x00"
_RUN_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_STATUSES = {"COMPLETED", "PARTIAL", "FAILED"}
_REQUIRED_RUN_KEYS = (
    "rfp_run_id",
    "created_at",
    "status",
    "criteria_snapshot",
    "suppliers",
    "warnings",
    "leaderboard",
    "tie_break_explanations",
)


class BackupError(ValueError):
    """The backup cannot be read at all (wrong file, too big, damaged)."""


@dataclass
class ImportReport:
    imported: int = 0
    skipped: int = 0
    failed: list[str] = field(default_factory=list)
    criteria_restored: bool = False

    def summary(self) -> str:
        parts = [f"{self.imported} run(s) restored"]
        if self.skipped:
            parts.append(f"{self.skipped} already present")
        if self.failed:
            parts.append(f"{len(self.failed)} could not be read")
        if self.criteria_restored:
            parts.append("criteria restored")
        return ", ".join(parts)


# ----------------------------------------------------------------------------- export


def export_bundle(conn: sqlite3.Connection) -> dict[str, Any]:
    """Every finished run (oldest first) plus the current criteria."""
    runs = []
    for row in reversed(db.list_runs(conn)):
        export = db.load_run_export(conn, row["rfp_run_id"])
        if export is not None:
            runs.append(export)
    return {
        "format": FORMAT,
        "version": VERSION,
        "runs": runs,
        "criteria": [c.model_dump(mode="json") for c in db.load_criteria(conn)],
    }


def bundle_json(bundle: dict[str, Any]) -> str:
    return json.dumps(bundle, sort_keys=True, ensure_ascii=False)


def sqlite_bytes(conn: sqlite3.Connection) -> bytes:
    """A complete, standalone SQLite database file of everything in this session."""
    return bytes(conn.serialize())


# ----------------------------------------------------------------------------- parsing


def parse_bundle(data: bytes | str) -> dict[str, Any]:
    """Read and validate an uploaded or browser-stored JSON bundle."""
    raw = data.encode("utf-8") if isinstance(data, str) else data
    if len(raw) > MAX_BYTES:
        raise BackupError("This backup is too large to restore.")
    try:
        bundle = json.loads(raw)
    except (ValueError, RecursionError) as exc:
        raise BackupError("This file is not a valid backup (it is not readable JSON).") from exc
    if not isinstance(bundle, dict) or bundle.get("format") != FORMAT:
        raise BackupError("This file is not an RFP Evaluator backup.")
    if bundle.get("version") != VERSION:
        raise BackupError("This backup was made by an incompatible version.")
    runs = bundle.get("runs")
    if not isinstance(runs, list) or len(runs) > MAX_RUNS:
        raise BackupError(f"A backup may contain at most {MAX_RUNS} runs.")
    return bundle


def bundle_from_sqlite(data: bytes) -> dict[str, Any]:
    """Read runs out of a SQLite file made by this app, without trusting anything else in it."""
    if len(data) > MAX_BYTES:
        raise BackupError("This database file is too large to restore.")
    if not data.startswith(_SQLITE_MAGIC):
        raise BackupError("This file is not a SQLite database.")
    tmp = sqlite3.connect(":memory:")
    tmp.row_factory = sqlite3.Row
    try:
        tmp.deserialize(data)
        rows = tmp.execute(
            "SELECT export_json FROM rfp_runs WHERE export_json IS NOT NULL"
            " ORDER BY created_at, rfp_run_id"
        ).fetchall()
        runs = []
        for r in rows[:MAX_RUNS]:
            try:
                runs.append(json.loads(r["export_json"]))
            except ValueError:
                continue
        try:
            criteria = [c.model_dump(mode="json") for c in db.load_criteria(tmp)]
        except Exception:
            criteria = []
    except sqlite3.DatabaseError as exc:
        raise BackupError("This database was not made by RFP Evaluator.") from exc
    finally:
        tmp.close()
    return {"format": FORMAT, "version": VERSION, "runs": runs, "criteria": criteria}


# ----------------------------------------------------------------------------- import


def _check_run_shape(export: Any) -> str | None:
    """Reason a run cannot be imported, or None when it looks usable."""
    if not isinstance(export, dict):
        return "not a run"
    missing = [k for k in _REQUIRED_RUN_KEYS if k not in export]
    if missing:
        return f"missing {missing[0]}"
    run_id = export["rfp_run_id"]
    if not isinstance(run_id, str) or not _RUN_ID.match(run_id):
        return "invalid run id"
    if export["status"] not in _STATUSES:
        return "invalid status"
    if not isinstance(export["suppliers"], list) or not export["suppliers"]:
        return "no suppliers"
    return None


def _import_run(conn: sqlite3.Connection, export: dict[str, Any]) -> None:
    criteria, suppliers, cards = rebuild_inputs(export)
    history = list(export.get("overrides", []))
    effective = {(o["supplier_id"], int(o["criterion_id"])): int(o["new_score"]) for o in history}
    options = export.get("options") or {}
    ranked = rank(
        cards,
        suppliers,
        criteria,
        RankOptions(enable_knockouts=bool(options.get("enable_knockouts")), overrides=effective),
    )
    db.start_run(
        conn,
        run_id=export["rfp_run_id"],
        created_at=str(export["created_at"]),
        model=export.get("model"),
        prompt_hash=export.get("prompt_hash"),
        options=options,
    )
    try:
        db.persist_results(
            conn,
            run_id=export["rfp_run_id"],
            status=export["status"],
            criteria=criteria,
            scorecards=cards,
            ranked=ranked,
            export=export,
        )
        db.add_override_rows(conn, export["rfp_run_id"], history)
    except Exception:
        db.delete_run(conn, export["rfp_run_id"])  # never leave a half-imported run behind
        raise


def import_bundle(
    conn: sqlite3.Connection, bundle: dict[str, Any], *, restore_criteria: bool = True
) -> ImportReport:
    """Add the runs of a validated bundle to this session; existing runs are never overwritten."""
    report = ImportReport()
    existing = {r["rfp_run_id"] for r in db.list_runs(conn)}
    for export in bundle["runs"]:
        problem = _check_run_shape(export)
        if problem:
            report.failed.append(problem)
            continue
        if export["rfp_run_id"] in existing:
            report.skipped += 1
            continue
        try:
            _import_run(conn, export)
        except Exception as exc:  # untrusted data: report, never crash the app
            report.failed.append(f"{export['rfp_run_id'][:8]}: {type(exc).__name__}")
            continue
        existing.add(export["rfp_run_id"])
        report.imported += 1
    if restore_criteria and bundle.get("criteria"):
        try:
            restored = [Criterion(**c) for c in bundle["criteria"]]
            db.save_criteria(conn, restored)  # validates weights, formulas and knock-outs
            report.criteria_restored = True
        except Exception:
            pass  # an invalid criteria set is ignored; the session keeps its own
    return report

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest

from core import db
from core.config import load_settings
from core.export import build_export, export_json, run_status
from core.models import Criterion, RankedRun, Scorecard, SupplierComputed, SupplierInput
from core.seed import SEED_CRITERIA, seed_criteria
from core.tools.ranking_tool import rank
from tests.helpers import make_card, make_criteria, make_supplier

NOW = "2025-03-01T10:00:00Z"


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    c = db.connect(":memory:")
    db.init_db(c)
    seed_criteria(c)
    yield c
    c.close()


def count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def finished_run(
    crit: list[Criterion], names: tuple[str, str] = ("Alpha", "Beta")
) -> tuple[list[Scorecard], RankedRun, list[SupplierInput]]:
    suppliers = [make_supplier("A", names[0]), make_supplier("B", names[1])]
    cards = [make_card(suppliers[0], crit, [8, 6, 7, 9, 5]),
             make_card(suppliers[1], crit, [6, 9, 8, 7, 7])]
    return cards, rank(cards, suppliers, crit), suppliers


def persist(conn: sqlite3.Connection, run_id: str = "run-1", **kw: Any) -> dict[str, Any]:
    crit = db.load_active_criteria(conn)
    cards, ranked, _ = finished_run(crit, **kw)
    db.start_run(conn, run_id=run_id, created_at=NOW, model="m", prompt_hash="h")
    export = build_export(run_id=run_id, created_at=NOW, status=run_status(ranked), model="m",
                          prompt_hash="h", criteria=crit, scorecards=cards, ranked=ranked)
    db.persist_results(conn, run_id=run_id, status=run_status(ranked), criteria=crit,
                       scorecards=cards, ranked=ranked, export=export)
    return export


def test_b01_init_and_seed_idempotent(conn: sqlite3.Connection) -> None:
    before = count(conn, "evaluation_criteria")
    assert seed_criteria(conn) == 0
    db.init_db(conn)
    assert count(conn, "evaluation_criteria") == before == len(SEED_CRITERIA)


def test_b02_seed_weights_total_100(conn: sqlite3.Connection) -> None:
    active = db.load_active_criteria(conn)
    assert sum(c.weight for c in active) == 100
    assert all(c.rubric and set(c.rubric) == {2, 5, 8} for c in active)
    db.check_criteria(active)


def test_b03_updated_weights_used_by_next_load(conn: sqlite3.Connection) -> None:
    crit = db.load_criteria(conn)
    crit[0] = crit[0].model_copy(update={"weight": 40})
    crit[4] = crit[4].model_copy(update={"weight": 0})
    db.save_criteria(conn, crit)
    assert [c.weight for c in db.load_active_criteria(conn)] == [40, 20, 20, 20, 0]


def test_b04_deactivate_excluded_but_snapshot_unchanged(conn: sqlite3.Connection) -> None:
    persist(conn, "old")
    crit = db.load_criteria(conn)
    crit[4] = crit[4].model_copy(update={"is_active": False})
    crit[0] = crit[0].model_copy(update={"weight": 40})
    db.save_criteria(conn, crit)
    assert len(db.load_active_criteria(conn)) == 4
    snap = conn.execute("SELECT COUNT(*) FROM run_criteria_snapshot WHERE rfp_run_id='old'")
    assert snap.fetchone()[0] == 5


@pytest.mark.parametrize(("mutate", "code"), [
    (lambda c: [x.model_copy(update={"weight": x.weight + 1}) if x.criterion_id == 1 else x
                for x in c], "E_WEIGHTS_TOTAL"),
    (lambda c: [x.model_copy(update={"is_active": False}) for x in c], "E_NO_ACTIVE_CRITERIA"),
    (lambda c: [x.model_copy(update={"name": "Same"}) for x in c], "E_DUPLICATE_NAME"),
])
def test_save_criteria_rejects_invalid(conn: sqlite3.Connection, mutate: Any, code: str) -> None:
    before = db.load_criteria(conn)
    with pytest.raises(db.CriteriaError) as err:
        db.save_criteria(conn, mutate(before))
    assert err.value.code == code
    assert db.load_criteria(conn) == before


def test_b05_persist_populates_all_tables(conn: sqlite3.Connection) -> None:
    persist(conn)
    assert count(conn, "supplier_results") == 2
    assert count(conn, "criterion_scores") == 10
    assert count(conn, "run_criteria_snapshot") == 5
    status = conn.execute("SELECT status FROM rfp_runs").fetchone()[0]
    assert status == "COMPLETED"
    row = conn.execute("SELECT final_rank, ppi FROM supplier_results WHERE supplier_id='B'")
    assert row.fetchone()["final_rank"] in (1, 2)


def test_b06_failed_persist_rolls_back(conn: sqlite3.Connection) -> None:
    crit = db.load_active_criteria(conn)
    cards, ranked, _ = finished_run(crit)
    dup = ranked.model_copy(update={"suppliers": [ranked.suppliers[0], ranked.suppliers[0]]})
    export = build_export(run_id="r", created_at=NOW, status="COMPLETED", model=None,
                          prompt_hash=None, criteria=crit, scorecards=cards, ranked=dup)
    db.start_run(conn, run_id="r", created_at=NOW)
    with pytest.raises(sqlite3.IntegrityError):
        db.persist_results(conn, run_id="r", status="COMPLETED", criteria=crit,
                           scorecards=cards, ranked=dup, export=export)
    for table in ("supplier_results", "criterion_scores", "run_criteria_snapshot", "run_warnings"):
        assert count(conn, table) == 0
    assert conn.execute("SELECT status FROM rfp_runs").fetchone()[0] == "RUNNING"


def test_b07_cascade_delete(conn: sqlite3.Connection) -> None:
    persist(conn)
    with conn:
        conn.execute("DELETE FROM rfp_runs")
    for table in ("supplier_results", "criterion_scores", "run_criteria_snapshot", "run_warnings"):
        assert count(conn, table) == 0


def test_b08_sql_metacharacters_stored_literally(conn: sqlite3.Connection) -> None:
    evil = "x'); DROP TABLE rfp_runs;--"
    persist(conn, names=(evil, "Beta"))
    assert count(conn, "rfp_runs") == 1
    names = [r[0] for r in conn.execute("SELECT supplier_name FROM supplier_results")]
    assert evil in names


def test_b09_history_newest_first(conn: sqlite3.Connection) -> None:
    stamps = ["2025-01-01T00:00:00Z", "2025-03-01T00:00:00Z", "2025-02-01T00:00:00Z"]
    for i, ts in enumerate(stamps):
        db.start_run(conn, run_id=f"r{i}", created_at=ts)
    assert [r["rfp_run_id"] for r in db.list_runs(conn)] == ["r1", "r2", "r0"]


def test_b10_reopen_equals_original_export(conn: sqlite3.Connection) -> None:
    export = persist(conn)
    assert db.load_run_export(conn, "run-1") == json.loads(export_json(export))
    assert db.load_run_export(conn, "missing") is None


def test_export_is_stable_and_key_free(conn: sqlite3.Connection) -> None:
    export = persist(conn)
    text = export_json(export)
    assert text == export_json(json.loads(text))
    assert "sk-" not in text and "api_key" not in text.lower()
    assert [row["rank"] for row in export["leaderboard"]] == [1, 2]
    assert export["schema_version"] == "1.0"


def test_run_status_values() -> None:
    def comp(status: str) -> SupplierComputed:
        return SupplierComputed(
            supplier_id=status,
            supplier_name=status,
            submission_date=date(2025, 1, 1),
            experience_rating=1,
            status=status,  # type: ignore[arg-type]
        )
    assert run_status(RankedRun(suppliers=[comp("RANKED")])) == "COMPLETED"
    assert run_status(RankedRun(suppliers=[comp("RANKED"), comp("FAILED")])) == "PARTIAL"
    assert run_status(RankedRun(suppliers=[comp("FAILED")])) == "FAILED"


def test_new_run_ids_unique() -> None:
    assert len({db.new_run_id() for _ in range(100)}) == 100


def test_c01_settings_env_overrides() -> None:
    s = load_settings({"RFP_MAX_PAGES": "80", "RFP_CONCURRENCY": "abc", "RFP_DB_PATH": " x.db "})
    assert s.max_pages == 80 and s.concurrency == 3 and s.db_path == "x.db"
    assert load_settings({"RFP_MAX_PAGES": "-5"}).max_pages == 150


def test_supplier_input_validation() -> None:
    with pytest.raises(ValueError):
        make_supplier("A", experience=5.5)
    with pytest.raises(ValueError):
        make_supplier("A", name="   ")
    assert make_supplier("A", name="  Padded  ").supplier_name == "Padded"
    assert make_criteria((100,))[0].max_score == 10

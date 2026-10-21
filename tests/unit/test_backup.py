from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from core import backup, db
from core.backup import BackupError
from core.overrides import OverrideRequest, apply_overrides
from tests.run_helpers import new_conn, run, uploads, with_extra_criteria


@pytest.fixture
def source(synthetic_dir: Path) -> Any:
    """A session with two finished runs, one carrying an analyst override, plus custom criteria."""
    conn = new_conn()
    with_extra_criteria(conn)
    _, first = run(conn, uploads(synthetic_dir))
    _, second = run(conn, uploads(synthetic_dir, ["nexaworks.pdf", "corrupt_co.pdf"]))
    apply_overrides(conn, first.run_id, [OverrideRequest("s1", 1, 9, "Verified on site")])
    yield conn, first.run_id, second.run_id
    conn.close()


def test_bundle_round_trip_restores_runs_overrides_and_criteria(source: Any) -> None:
    conn, first_id, second_id = source
    bundle = backup.parse_bundle(backup.bundle_json(backup.export_bundle(conn)))
    assert [r["rfp_run_id"] for r in bundle["runs"]] == [first_id, second_id] or {
        r["rfp_run_id"] for r in bundle["runs"]
    } == {first_id, second_id}
    fresh = new_conn()  # a brand-new session
    report = backup.import_bundle(fresh, bundle)
    assert report.imported == 2 and report.skipped == 0 and not report.failed
    assert report.criteria_restored
    assert {c.name for c in db.load_active_criteria(fresh)} >= {"Total Price", "Delivery Time"}
    for run_id in (first_id, second_id):
        assert db.load_run_export(fresh, run_id) == db.load_run_export(conn, run_id)
    rows = db.list_overrides(fresh, first_id)
    assert len(rows) == 1 and rows[0]["reason"] == "Verified on site" and rows[0]["new_score"] == 9
    # the restored run behaves like a real one: counts, ranks and further overrides all work
    assert fresh.execute("SELECT COUNT(*) FROM supplier_results").fetchone()[0] == 4 + 2
    apply_overrides(fresh, first_id, [OverrideRequest("s0", 2, 8, "second look")])
    assert len(db.list_overrides(fresh, first_id)) == 2
    fresh.close()


def test_restoring_twice_never_duplicates_or_overwrites(source: Any) -> None:
    conn, first_id, _ = source
    bundle = backup.export_bundle(conn)
    fresh = new_conn()
    backup.import_bundle(fresh, bundle)
    again = backup.import_bundle(fresh, bundle)
    assert again.imported == 0 and again.skipped == 2
    assert len(db.list_runs(fresh)) == 2
    db.delete_run(fresh, first_id)
    third = backup.import_bundle(fresh, bundle)  # a deleted run can be restored again
    assert third.imported == 1 and third.skipped == 1
    fresh.close()


def test_sqlite_file_export_and_restore(source: Any) -> None:
    conn, first_id, second_id = source
    data = backup.sqlite_bytes(conn)
    assert data.startswith(b"SQLite format 3")
    bundle = backup.bundle_from_sqlite(data)
    assert {r["rfp_run_id"] for r in bundle["runs"]} == {first_id, second_id}
    fresh = new_conn()
    report = backup.import_bundle(fresh, bundle)
    assert report.imported == 2
    assert db.load_run_export(fresh, first_id) == db.load_run_export(conn, first_id)
    fresh.close()


def test_the_downloaded_sqlite_file_is_a_normal_database(source: Any, tmp_path: Path) -> None:
    conn, first_id, _ = source
    path = tmp_path / "backup.db"
    path.write_bytes(backup.sqlite_bytes(conn))
    opened = sqlite3.connect(path)  # what the user can open in any SQLite tool
    assert opened.execute("SELECT COUNT(*) FROM rfp_runs").fetchone()[0] == 2
    assert opened.execute("SELECT COUNT(*) FROM score_overrides").fetchone()[0] == 1
    opened.close()


# ---------------------------------------------------------------------- hostile or damaged input


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"not json at all", "not readable JSON"),
        (b"[]", "not an RFP Evaluator backup"),
        (b'{"format": "other"}', "not an RFP Evaluator backup"),
        (b'{"format": "rfp-evaluator-backup", "version": 99, "runs": []}', "incompatible"),
        (b'{"format": "rfp-evaluator-backup", "version": 1, "runs": "x"}', "at most"),
    ],
)
def test_bad_bundles_are_rejected_with_a_clear_message(data: bytes, message: str) -> None:
    with pytest.raises(BackupError) as e:
        backup.parse_bundle(data)
    assert message in str(e.value)


def test_size_and_run_count_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backup, "MAX_BYTES", 50)
    with pytest.raises(BackupError):
        backup.parse_bundle(b"x" * 100)
    with pytest.raises(BackupError):
        backup.bundle_from_sqlite(b"x" * 100)
    monkeypatch.setattr(backup, "MAX_BYTES", 10_000_000)
    monkeypatch.setattr(backup, "MAX_RUNS", 1)
    too_many = json.dumps({"format": backup.FORMAT, "version": 1, "runs": [{}, {}]})
    with pytest.raises(BackupError):
        backup.parse_bundle(too_many)


def test_non_sqlite_and_foreign_sqlite_files_are_refused(tmp_path: Path) -> None:
    with pytest.raises(BackupError):
        backup.bundle_from_sqlite(b"hello, I am a text file" * 10)
    other = sqlite3.connect(":memory:")
    other.execute("CREATE TABLE unrelated (x)")
    with pytest.raises(BackupError):
        backup.bundle_from_sqlite(bytes(other.serialize()))
    other.close()


def test_malformed_runs_are_reported_and_do_not_stop_the_import(source: Any) -> None:
    conn, first_id, _ = source
    good = db.load_run_export(conn, first_id)
    assert good is not None
    bundle = {
        "format": backup.FORMAT,
        "version": 1,
        "criteria": [],
        "runs": [
            "a string",
            {"rfp_run_id": "x"},
            {**good, "rfp_run_id": "../../etc/passwd"},
            {**good, "rfp_run_id": "validid12345", "status": "HACKED"},
            {**good, "rfp_run_id": "validid67890", "suppliers": [{"supplier_id": "s0"}]},
            good,
        ],
    }
    fresh = new_conn()
    report = backup.import_bundle(fresh, bundle)
    assert report.imported == 1 and len(report.failed) == 5
    assert [r["rfp_run_id"] for r in db.list_runs(fresh)] == [first_id]
    # the broken run left nothing behind
    assert (
        fresh.execute("SELECT COUNT(*) FROM rfp_runs WHERE status = 'RUNNING'").fetchone()[0] == 0
    )
    fresh.close()


def test_invalid_criteria_in_a_backup_are_ignored_not_applied(source: Any) -> None:
    conn, _, _ = source
    bundle = backup.export_bundle(conn)
    bundle["criteria"][0]["weight"] = 999  # active weights no longer total 100
    fresh = new_conn()
    before = db.load_criteria(fresh)
    report = backup.import_bundle(fresh, bundle)
    assert not report.criteria_restored and db.load_criteria(fresh) == before
    fresh.close()


def test_script_in_a_backup_is_stored_as_data_only(source: Any) -> None:
    conn, first_id, _ = source
    export = db.load_run_export(conn, first_id)
    assert export is not None
    export = json.loads(json.dumps(export).replace("Apex Systems", "<script>alert(1)</script>"))
    export["rfp_run_id"] = "scriptrun0001"
    fresh = new_conn()
    report = backup.import_bundle(
        fresh, {"format": backup.FORMAT, "version": 1, "runs": [export], "criteria": []}
    )
    assert report.imported == 1
    names = [r[0] for r in fresh.execute("SELECT supplier_name FROM supplier_results")]
    assert "<script>alert(1)</script>" in names  # kept verbatim; the UI escapes on display
    fresh.close()


def test_summary_text() -> None:
    r = backup.ImportReport(imported=2, skipped=1, failed=["x"], criteria_restored=True)
    assert r.summary() == (
        "2 run(s) restored, 1 already present, 1 could not be read, criteria restored"
    )

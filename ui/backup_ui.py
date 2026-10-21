"""Backup and restore: automatic in-browser history plus downloadable files."""

from __future__ import annotations

import streamlit as st

from core import backup
from ui import browser_store, state


def browser_saving_enabled() -> bool:
    ss = st.session_state
    if "browser_save" not in ss:
        # On a hosted address data is private and temporary, so keep a copy in the visitor's browser.
        ss["browser_save"] = state.effective_db_mode() == "session"
    return bool(ss["browser_save"])


def _import_bundle(bundle: dict) -> backup.ImportReport:  # type: ignore[type-arg]
    report = backup.import_bundle(state.conn(), bundle)
    if report.imported or report.criteria_restored:
        state.drop_criteria_cache()
        state.mark_dirty()
    return report


def sync() -> None:
    """Restore the browser's saved history once per session, then save after every change.

    Runs before the page is drawn. Without a real browser (tests) the component never answers,
    so nothing happens and the app is unaffected.
    """
    ss = st.session_state
    if not browser_saving_enabled():
        return
    if not ss.get("_restored"):
        reply = browser_store.call("load", token="load")
        if reply is None:
            return  # the browser has not answered yet; it will, and the script reruns
        ss["_restored"] = True
        if reply.get("ok") and reply.get("found") and reply.get("data"):
            try:
                report = _import_bundle(backup.parse_bundle(reply["data"]))
            except backup.BackupError:
                return  # a damaged browser copy is ignored; the next save replaces it
            if report.imported:
                state.notice(f"Restored from this browser: {report.summary()}.", "success")
        return
    version = ss.get("_db_version", 0)
    if version != ss.get("_saved_version", 0):
        payload = backup.bundle_json(backup.export_bundle(state.conn()))
        reply = browser_store.call("save", token=f"save{version}", payload=payload)
        if reply is not None and reply.get("ok"):
            ss["_saved_version"] = version


def sidebar() -> None:
    """The Backup and restore panel."""
    ss = st.session_state
    with st.sidebar, st.expander("Backup and restore"):
        conn = state.conn()
        if st.checkbox(
            "Keep my history in this browser",
            value=browser_saving_enabled(),
            key="browser_save",
            help="Saves your runs in this browser on this device after every change and "
            "restores them when you come back. It does not leave your device.",
        ):
            st.caption("Saved automatically in this browser; restored when you return.")
        else:
            st.caption("Off: history is lost when the session ends.")
        if st.button("Clear history saved in this browser", key="clear_browser_copy"):
            browser_store.call("clear", token=f"clear{ss.get('_clear_n', 0)}")
            ss["_clear_n"] = ss.get("_clear_n", 0) + 1
            state.notice("The copy saved in this browser will be removed.", "info")

        st.markdown("**Download a backup**")
        bundle = backup.export_bundle(conn)
        st.download_button(
            "Backup file (JSON)",
            data=backup.bundle_json(bundle),
            file_name="rfp_evaluator_backup.json",
            mime="application/json",
            key="download_backup_json",
            width="stretch",
        )
        st.download_button(
            "Database file (SQLite)",
            data=backup.sqlite_bytes(conn),
            file_name="rfp_evaluator.db",
            mime="application/vnd.sqlite3",
            key="download_backup_db",
            width="stretch",
        )

        st.markdown("**Restore from a file**")
        upload = st.file_uploader(
            "Backup file (.json) or database file (.db)",
            type=["json", "db", "sqlite"],
            key="restore_upload",
            label_visibility="collapsed",
        )
        if upload is not None and upload.file_id != ss.get("_restore_seen"):
            ss["_restore_seen"] = upload.file_id
            _restore_upload(upload.name, upload.getvalue())


def _restore_upload(name: str, data: bytes) -> None:
    try:
        if data.startswith(b"SQLite format 3"):
            bundle = backup.bundle_from_sqlite(data)
        else:
            bundle = backup.parse_bundle(data)
    except backup.BackupError as exc:
        st.error(str(exc))
        return
    report = _import_bundle(bundle)
    kind = "success" if report.imported or report.criteria_restored else "info"
    state.notice(f"{name}: {report.summary()}.", kind)
    st.rerun()

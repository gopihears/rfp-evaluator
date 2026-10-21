"""Session state helpers. All secrets live here and nowhere else (memory only)."""

from __future__ import annotations

import os
import sqlite3
from typing import Any

import streamlit as st

from core import db
from core.config import load_settings
from core.seed import seed_criteria

STEPS = ["connect", "criteria", "suppliers", "results"]
STEP_LABELS = {"connect": "Connect", "criteria": "Criteria", "suppliers": "Suppliers",
               "results": "Results"}

# Keys that make up an authenticated session; cleared together on Disconnect.
SESSION_KEYS = ["provider", "models", "model", "key_hint", "uploads", "seen_files", "outcome",
                "viewing_export", "running", "notice", "step", "demo_loaded", "crit_base",
                "crit_ver", "_db"]


@st.cache_resource(show_spinner=False)
def _prepare_file_db(path: str) -> bool:
    """Create the schema and seed criteria once per process (idempotent)."""
    conn = db.connect(path)
    db.init_db(conn)
    seed_criteria(conn)
    conn.close()
    return True


def _is_local_host() -> bool:
    """True when the browser reached the app via localhost (or the host is unknown)."""
    try:
        raw = str(st.context.headers.get("Host", "")).strip().lower()
        host = raw.split("]")[0] + "]" if raw.startswith("[") else raw.split(":")[0]
    except Exception:
        return True
    return host in ("", "localhost", "127.0.0.1", "[::1]", "::1")


def is_hosted() -> bool:
    """True when the app is reached through a non-localhost address."""
    return not _is_local_host()


def effective_db_mode() -> str:
    """'session' (private, in memory) or 'file' (shared SQLite file).

    An explicit RFP_DB_MODE always wins. Otherwise local use keeps a file so runs persist, and
    any other host (a hosted deployment) defaults to private per-session data.
    """
    if os.environ.get("RFP_DB_MODE", "").strip().lower() in ("file", "session"):
        return load_settings().db_mode
    return "file" if _is_local_host() else "session"


def conn() -> sqlite3.Connection:
    """The database for THIS browser session.

    Every session gets its own connection, so one user's transactions can never be committed by
    another. In 'session' mode (RFP_DB_MODE=session, recommended for a hosted app) the database
    is private to the session and in memory: criteria, runs and results are never shared with
    other visitors and disappear when the session ends.
    """
    ss = st.session_state
    existing: sqlite3.Connection | None = ss.get("_db")
    if existing is not None:
        return existing
    cfg = load_settings()
    if effective_db_mode() == "session":
        fresh = db.connect(":memory:", check_same_thread=False)
        db.init_db(fresh)
        seed_criteria(fresh)
    else:
        _prepare_file_db(cfg.db_path)
        fresh = db.connect(cfg.db_path, check_same_thread=False)
    ss["_db"] = fresh
    return fresh


def init() -> None:
    ss = st.session_state
    ss.setdefault("step", "connect")
    ss.setdefault("uploads", [])
    ss.setdefault("seen_files", set())
    ss.setdefault("running", False)


def connected() -> bool:
    return st.session_state.get("provider") is not None


def has_results() -> bool:
    ss = st.session_state
    return ss.get("outcome") is not None or ss.get("viewing_export") is not None


def disconnect() -> None:
    """Forget the key and everything derived from it for this session."""
    existing: sqlite3.Connection | None = st.session_state.get("_db")
    if existing is not None:
        existing.close()
    for key in SESSION_KEYS:
        st.session_state.pop(key, None)
    init()


def goto(step: str) -> None:
    st.session_state["step"] = step


def unlocked(step: str) -> bool:
    if step == "connect":
        return True
    if not connected():
        return False
    if step == "results":
        return has_results()
    return True


def notice(message: str, kind: str = "info") -> None:
    st.session_state["notice"] = (kind, message)


def show_notice() -> None:
    item: Any = st.session_state.pop("notice", None)
    if not item:
        return
    kind, message = item
    {"error": st.error, "warning": st.warning, "success": st.success}.get(kind, st.info)(message)

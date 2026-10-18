"""Session state helpers. All secrets live here and nowhere else (memory only)."""

from __future__ import annotations

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
                "viewing_export", "running", "notice", "step", "demo_loaded"]


@st.cache_resource(show_spinner=False)
def get_conn(path: str) -> sqlite3.Connection:
    """One shared SQLite connection per process; schema and seed are created idempotently."""
    conn = db.connect(path, check_same_thread=False)
    db.init_db(conn)
    seed_criteria(conn)
    return conn


def conn() -> sqlite3.Connection:
    return get_conn(load_settings().db_path)


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

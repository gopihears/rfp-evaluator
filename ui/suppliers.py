"""Step 3: add supplier PDFs, enter metadata, and run the evaluation with live progress."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import streamlit as st

from core import db
from core.config import load_settings
from core.models import SupplierInput
from core.orchestrator import (
    EvaluationInputError,
    ProgressEvent,
    RunOptions,
    RunOutcome,
    SupplierUpload,
    run_evaluation,
)
from core.tools.document_tool import DocumentError, extract
from core.tools.llm_provider import scrub
from ui import state, theme

DEMO_DIR = Path(__file__).resolve().parent.parent / "data" / "synthetic_rfps"
_STAGE_ICON = {"QUEUED": "⏳", "READING": "📄", "SCORING": "🤖", "VALIDATING": "🔎",
               "DONE": "✅", "FAILED": "⛔"}


@dataclass
class Entry:
    id: str
    filename: str
    data: bytes
    name: str
    submitted: date
    rating: float
    pages: int | None = None
    tokens: int | None = None
    error: str | None = None  # human message when the PDF cannot be read


# ------------------------------------------------------------- pure helpers (tested)


def guess_name(filename: str) -> str:
    stem = Path(filename).stem
    spaced = re.sub(r"[_\-.]+", " ", stem)  # underscores are word characters, so split first
    cleaned = re.sub(r"(?i)\b(rfp|response|proposal|final|v\d+)\b", " ", spaced)
    words = cleaned.split()
    return " ".join(w.capitalize() for w in words) or stem


def entry_problems(entries: list[Entry], today: date, max_suppliers: int) -> dict[str, list[str]]:
    """Blocking validation messages per entry id; key '_run' holds run-level messages."""
    out: dict[str, list[str]] = {e.id: [] for e in entries}
    out["_run"] = []
    seen: dict[str, str] = {}
    for e in entries:
        name = e.name.strip()
        if not name:
            out[e.id].append("Enter the supplier name.")
        elif len(name) > 80:
            out[e.id].append("Supplier name must be 80 characters or fewer.")
        elif name.casefold() in seen:
            out[e.id].append("Another file already uses this supplier name.")
            out[seen[name.casefold()]].append("Another file already uses this supplier name.")
        else:
            seen[name.casefold()] = e.id
        if e.submitted > today:
            out[e.id].append("The submission date cannot be in the future.")
        if not 0 <= e.rating <= 5:
            out[e.id].append("Experience rating must be between 0 and 5.")
    if not entries:
        out["_run"].append("Add at least one supplier PDF.")
    if len(entries) > max_suppliers:
        out["_run"].append(f"A run can include at most {max_suppliers} suppliers.")
    if entries and all(e.error for e in entries):
        out["_run"].append("None of the files can be read. Upload text-based PDFs.")
    return out


def to_uploads(entries: list[Entry]) -> list[SupplierUpload]:
    return [SupplierUpload(
        SupplierInput(supplier_id=e.id, supplier_name=e.name.strip(), submission_date=e.submitted,
                      experience_rating=e.rating, filename=e.filename), e.data) for e in entries]


def make_entry(filename: str, data: bytes, *, name: str | None = None,
               submitted: date | None = None, rating: float = 3.0) -> Entry:
    """Create an entry and preflight the PDF so problems show before the run."""
    cfg = load_settings()
    entry = Entry(id=uuid.uuid4().hex[:10], filename=filename, data=data,
                  name=name or guess_name(filename), submitted=submitted or date.today(),
                  rating=rating)
    try:
        doc = extract(data, max_pages=cfg.max_pages, max_bytes=cfg.max_bytes)
        entry.pages, entry.tokens = doc.pages, doc.est_tokens
    except DocumentError as exc:
        entry.error = exc.message
    return entry


def demo_entries() -> list[Entry]:
    manifest = json.loads((DEMO_DIR / "manifest.json").read_text(encoding="utf-8"))
    out = []
    for f in manifest["files"]:
        if f["role"] != "brief":
            continue
        out.append(make_entry(f["file"], (DEMO_DIR / f["file"]).read_bytes(), name=f["supplier_name"],
                              submitted=date.fromisoformat(f["submission_date"]),
                              rating=float(f["experience_rating"])))
    return out


def error_sample() -> Entry:
    return make_entry("corrupt_co.pdf", (DEMO_DIR / "corrupt_co.pdf").read_bytes(),
                      name="Corrupt Co", submitted=date(2025, 3, 2), rating=1.0)


# --------------------------------------------------------------------------- screen


def _sync_uploads(files: list[Any]) -> None:
    ss = st.session_state
    for f in files:
        if f.file_id in ss["seen_files"]:
            continue
        ss["seen_files"].add(f.file_id)
        ss["uploads"].append(make_entry(f.name, f.getvalue()))


def _read_widgets(entries: list[Entry]) -> None:
    ss = st.session_state
    for e in entries:
        e.name = ss.get(f"name_{e.id}", e.name)
        e.submitted = ss.get(f"date_{e.id}", e.submitted)
        e.rating = float(ss.get(f"rate_{e.id}", e.rating))


def _card(e: Entry, problems: list[str]) -> None:
    with st.container(border=True, key=f"card_{e.id}"):
        top, rm = st.columns([6, 1])
        info = (theme.pill("Can't read this PDF", "err") if e.error
                else theme.pill(f"{e.pages} pages · ~{e.tokens:,} tokens", "info"))
        top.markdown(f"**{theme.esc(e.filename)}** &nbsp; {info}", unsafe_allow_html=True)
        if rm.button("Remove", key=f"rm_{e.id}"):
            st.session_state["uploads"] = [u for u in st.session_state["uploads"] if u.id != e.id]
            st.rerun()
        if e.error:
            st.error(f"{e.error} It will be listed as failed in the results.")
        c1, c2, c3 = st.columns([3, 2, 2])
        c1.text_input("Supplier name", value=e.name, key=f"name_{e.id}", max_chars=80)
        c2.date_input("Submission date", value=e.submitted, max_value=date.today(),
                      key=f"date_{e.id}")
        c3.slider("Historical experience (0-5)", 0.0, 5.0, value=float(e.rating), step=0.5,
                  key=f"rate_{e.id}", help="Used only as a tie-break when scores are equal.")
        for p in problems:
            st.caption(f"⚠️ {p}")


def render() -> None:
    ss = st.session_state
    cfg = load_settings()
    conn = state.conn()
    st.subheader("Supplier proposals")
    st.caption("Add each supplier's RFP response as a text-based PDF, then confirm the details. "
               "Submission date and experience rating are used for tie-breaks.")

    up_col, demo_col = st.columns([3, 2], gap="large")
    with up_col:
        files = st.file_uploader("Drop PDFs here", type=["pdf"], accept_multiple_files=True,
                                 key="uploader", help=f"Up to {cfg.max_pages} pages each.")
        _sync_uploads(files or [])
    with demo_col:
        st.markdown('<div class="rfp-card"><b>No files handy?</b><div class="rfp-muted">Load four '
                    "fictional proposals with different strengths, or add an unreadable scan to "
                    "see how failures are handled.</div></div>", unsafe_allow_html=True)
        if st.button("Load demo data", key="load_demo", width="stretch"):
            ss["uploads"] = demo_entries()
            st.rerun()
        if st.button("Add error sample", key="add_error", width="stretch"):
            ss["uploads"].append(error_sample())
            st.rerun()

    entries: list[Entry] = ss["uploads"]
    _read_widgets(entries)
    problems = entry_problems(entries, date.today(), cfg.max_suppliers)

    for e in entries:
        _card(e, problems[e.id])
    for msg in problems["_run"]:
        st.caption(f"⚠️ {msg}")
    if len(entries) == 1:
        st.info("With a single supplier there is no peer to benchmark against, so PPI will be 100.")

    st.divider()
    readable = sum(1 for e in entries if not e.error)
    total_tokens = sum(e.tokens or 0 for e in entries)
    criteria_ok = _criteria_ok(conn)
    blocked = any(problems[k] for k in problems) or not entries or readable == 0 or not criteria_ok
    m1, m2, m3 = st.columns(3)
    m1.metric("Suppliers", len(entries))
    m2.metric("Readable PDFs", readable)
    m3.metric("Estimated input tokens", f"{total_tokens:,}",
              help="Rough size of the text sent to the model; cost depends on your model.")
    if not criteria_ok:
        st.warning("Fix the evaluation criteria first (active weights must total 100%).")

    with st.expander("Advanced"):
        sens = st.toggle("Weight sensitivity analysis", value=True, key="opt_sens",
                         help="Shows how stable the ranking is if weights change.")
    b1, b2 = st.columns([1, 1])
    if b1.button("Evaluate suppliers", type="primary", disabled=blocked, key="evaluate"):
        _run(entries, RunOptions(model=ss["model"], sensitivity=sens), conn, cfg)
    if b2.button("Back to criteria", key="back_criteria"):
        state.goto("criteria")
        st.rerun()


def _criteria_ok(conn: Any) -> bool:
    try:
        db.check_criteria(db.load_active_criteria(conn))
    except db.CriteriaError:
        return False
    return True


def _run(entries: list[Entry], options: RunOptions, conn: Any, cfg: Any) -> None:
    ss = st.session_state
    ss["running"] = True
    st.button("Cancel run", key="cancel_run", help="Stops the evaluation.")
    outcome: RunOutcome | None = None
    try:
        with st.status("Evaluating proposals...", expanded=True) as status:
            bar = st.progress(0.0)
            lines = {e.id: st.empty() for e in entries}
            for e in entries:
                lines[e.id].markdown(f"⏳ **{theme.esc(e.name)}** - waiting")
            for item in run_evaluation(to_uploads(entries), provider=ss["provider"], conn=conn,
                                       options=options, settings=cfg):
                if isinstance(item, RunOutcome):
                    outcome = item
                    continue
                _show(item, lines, bar, status)
            status.update(label="Evaluation finished", state="complete", expanded=False)
    except EvaluationInputError as exc:
        ss["running"] = False
        st.error(str(exc))
        return
    except db.CriteriaError as exc:
        ss["running"] = False
        st.error(f"Cannot start: {exc}")
        return
    except Exception as exc:
        ss["running"] = False
        st.error(f"The run stopped unexpectedly: {scrub(type(exc).__name__)}. "
                 "Your documents were not changed; please try again.")
        return
    ss["running"] = False
    if outcome is None:
        return
    ss["outcome"], ss["viewing_export"] = outcome, outcome.export
    state.goto("results")
    st.rerun()


def _show(ev: ProgressEvent, lines: dict[str, Any], bar: Any, status: Any) -> None:
    bar.progress(min(ev.overall_pct, 1.0))
    if ev.supplier_id and ev.supplier_id in lines:
        icon = _STAGE_ICON.get(ev.stage, "•")
        lines[ev.supplier_id].markdown(
            f"{icon} **{theme.esc(ev.supplier_name or '')}** - {theme.esc(ev.message)}")
    else:
        status.update(label=ev.message + "...")

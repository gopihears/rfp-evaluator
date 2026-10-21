"""Agentic RFP Evaluation: Streamlit entry point.

Run with: streamlit run app.py
"""

from __future__ import annotations

import streamlit as st

from core import db
from core.tools.provider_factory import demo_mode
from ui import backup_ui, connect, criteria, results, state, suppliers, theme

st.set_page_config(page_title="RFP Evaluator", page_icon="📊", layout="wide",
                   initial_sidebar_state="collapsed")
theme.inject()
state.init()


def _top_bar() -> None:
    left, right = st.columns([3, 2], vertical_alignment="center")
    left.markdown(
        '<div class="rfp-brand"><div class="rfp-logo">R</div><div><div class="rfp-title">'
        'RFP Evaluator</div><div class="rfp-sub">Explainable supplier scoring and ranking</div>'
        "</div></div>", unsafe_allow_html=True)
    with right:
        if state.connected():
            chip = ("Demo mode" if demo_mode()
                    else f"{st.session_state['model']} · {st.session_state['key_hint']}")
            c1, c2 = st.columns([3, 1], vertical_alignment="center")
            c1.markdown(
                f'<div style="text-align:right"><span class="rfp-chip"><span class="rfp-dot"></span>'
                f"{theme.esc(chip)}</span></div>", unsafe_allow_html=True)
            if c2.button("Disconnect", key="disconnect_top"):
                state.disconnect()
                st.rerun()


def _stepper() -> None:
    ss = st.session_state
    cols = st.columns(len(state.STEPS))
    for i, (col, step) in enumerate(zip(cols, state.STEPS, strict=True), start=1):
        done = state.STEPS.index(ss["step"]) > i - 1 and state.unlocked(step)
        label = f"{'✓' if done else i}  {state.STEP_LABELS[step]}"
        if col.button(label, key=f"nav_{step}", disabled=not state.unlocked(step),
                      type="primary" if ss["step"] == step else "secondary", width="stretch"):
            state.goto(step)
            st.rerun()


def _sidebar() -> None:
    if state.connected():
        with st.sidebar:
            st.markdown("### Past runs")
            conn = state.conn()
            runs = db.list_runs(conn)[:15]
            if not runs:
                st.caption("No runs yet.")
            for r in runs:
                label = f"{r['created_at'][:16].replace('T', ' ')} · {r['status'].title()}"
                if st.button(label, key=f"hist_{r['rfp_run_id']}", width="stretch"):
                    export = db.load_run_export(conn, r["rfp_run_id"])
                    if export is None:
                        state.notice("That run is still in progress or has no saved result.",
                                     "warning")
                    else:
                        st.session_state["viewing_export"] = export
                        state.goto("results")
                    st.rerun()
    backup_ui.sidebar()


def main() -> None:
    ss = st.session_state
    if ss.get("running"):  # a previous run was interrupted (for example by the Cancel button)
        ss["running"] = False
        state.notice("The previous evaluation was cancelled before it finished.", "warning")
    backup_ui.sync()  # restore saved history from this browser, save after changes
    _top_bar()
    _stepper()
    _sidebar()
    state.show_notice()
    if ss["step"] != "connect" and not state.connected():
        ss["step"] = "connect"
    if ss["step"] == "results" and not state.has_results():
        ss["step"] = "suppliers"
    {"connect": connect.render, "criteria": criteria.render, "suppliers": suppliers.render,
     "results": results.render}[ss["step"]]()


main()

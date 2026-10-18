"""Step 2: view and edit the evaluation criteria and weights (stored in SQLite)."""

from __future__ import annotations

from decimal import Decimal

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core import db
from core.models import Criterion
from ui import state, theme

COLUMNS = ["id", "Active", "Name", "Description", "Weight (%)", "Max score"]

PRESETS: dict[str, dict[str, float]] = {
    "Balanced": {"Technical Capability": 30, "Implementation Plan": 20, "Commercial Value": 20,
                 "Security & Compliance": 20, "Support & Experience": 10},
    "Security first": {"Technical Capability": 25, "Implementation Plan": 15, "Commercial Value": 15,
                       "Security & Compliance": 35, "Support & Experience": 10},
    "Cost first": {"Technical Capability": 20, "Implementation Plan": 20, "Commercial Value": 35,
                   "Security & Compliance": 15, "Support & Experience": 10},
}


# ------------------------------------------------------------- pure helpers (tested)


def to_frame(criteria: list[Criterion]) -> pd.DataFrame:
    return pd.DataFrame(
        [{"id": c.criterion_id, "Active": c.is_active, "Name": c.name, "Description": c.description,
          "Weight (%)": float(c.weight), "Max score": int(c.max_score)} for c in criteria],
        columns=COLUMNS)


def active_total(df: pd.DataFrame) -> Decimal:
    total = Decimal(0)
    for _, row in df.iterrows():
        if bool(row["Active"]):
            total += Decimal(str(row["Weight (%)"] if pd.notna(row["Weight (%)"]) else 0))
    return total


def problems(df: pd.DataFrame) -> list[str]:
    """Human-readable reasons the current edit cannot be saved (empty list = OK)."""
    out: list[str] = []
    names = [str(n).strip() for n in df["Name"]]
    if any(not n or n == "nan" for n in names):
        out.append("Every criterion needs a name.")
    if len({n.casefold() for n in names}) != len(names):
        out.append("Criterion names must be unique.")
    if (df["Weight (%)"].fillna(0) < 0).any():
        out.append("Weights cannot be negative.")
    if (df["Max score"].fillna(0) < 1).any():
        out.append("Max score must be at least 1.")
    if not df["Active"].astype(bool).any():
        out.append("Activate at least one criterion.")
    elif active_total(df) != Decimal(100):
        out.append(f"Active weights must total 100% (currently {active_total(df)}%).")
    return out


def normalise(df: pd.DataFrame) -> pd.DataFrame:
    """Rescale active weights proportionally so they total exactly 100 (one decimal)."""
    out = df.copy()
    mask = out["Active"].astype(bool)
    total = out.loc[mask, "Weight (%)"].sum()
    if total <= 0:
        return out
    scaled = (out.loc[mask, "Weight (%)"] / total * 100).round(1)
    drift = round(100 - scaled.sum(), 1)
    scaled.iloc[scaled.values.argmax()] += drift
    out.loc[mask, "Weight (%)"] = scaled
    return out


def apply_preset(df: pd.DataFrame, preset: str) -> pd.DataFrame | None:
    """Apply a named weight preset; None when the criteria names do not match the preset."""
    weights = PRESETS[preset]
    if set(df["Name"]) != set(weights):
        return None
    out = df.copy()
    out["Weight (%)"] = [float(weights[n]) for n in out["Name"]]
    out["Active"] = True
    return out


def frame_to_criteria(df: pd.DataFrame, existing: list[Criterion],
                      rubrics: dict[int, dict[int, str]]) -> list[Criterion]:
    by_id = {c.criterion_id: c for c in existing}
    out = []
    for _, row in df.iterrows():
        cid = int(row["id"])
        prior = by_id.get(cid)
        rubric = {k: v.strip() for k, v in rubrics.get(cid, {}).items() if v.strip()} or None
        out.append(Criterion(
            criterion_id=cid, name=str(row["Name"]).strip(),
            description=str(row["Description"] or "").strip(),
            weight=float(row["Weight (%)"]), max_score=int(row["Max score"]),
            is_active=bool(row["Active"]), rubric=rubric,
            is_mandatory=prior.is_mandatory if prior else False,
            min_score=prior.min_score if prior else None,
            criterion_type=prior.criterion_type if prior else "llm_judged"))
    return out


# ----------------------------------------------------------------------- screen


def _donut(df: pd.DataFrame) -> go.Figure:
    active = df[df["Active"].astype(bool)]
    palette = ["#4f46e5", "#0d9488", "#d97706", "#9333ea", "#2563eb", "#db2777", "#65a30d", "#6b7280"]
    fig = go.Figure(go.Pie(labels=list(active["Name"]), values=list(active["Weight (%)"]), hole=0.62,
                           textinfo="percent", sort=False, marker={"colors": palette},
                           hovertemplate="%{label}: %{value}%<extra></extra>"))
    fig.update_layout(height=290, margin=dict(l=0, r=0, t=10, b=0), showlegend=True,
                      legend=dict(orientation="h", y=-0.12), paper_bgcolor="rgba(0,0,0,0)")
    return fig


def _reset_editor(df: pd.DataFrame) -> None:
    st.session_state["crit_base"] = df
    st.session_state["crit_ver"] = st.session_state.get("crit_ver", 0) + 1


def render() -> None:
    conn = state.conn()
    stored = db.load_criteria(conn)
    if "crit_base" not in st.session_state:
        st.session_state["crit_base"] = to_frame(stored)
        st.session_state["crit_ver"] = 0
    base: pd.DataFrame = st.session_state["crit_base"]

    st.subheader("Evaluation criteria")
    st.caption("Each criterion is scored by the model; weights decide how much it counts. "
               "Active weights must total 100%.")

    left, right = st.columns([3, 2], gap="large")
    with left:
        edited = st.data_editor(
            base, key=f"crit_editor_{st.session_state['crit_ver']}", hide_index=True,
            width="stretch", num_rows="fixed",
            disabled=["id"],
            column_config={
                "id": None,
                "Active": st.column_config.CheckboxColumn("Active", width="small"),
                "Name": st.column_config.TextColumn("Criterion", required=True),
                "Description": st.column_config.TextColumn("What the model inspects", width="large"),
                "Weight (%)": st.column_config.NumberColumn("Weight %", min_value=0.0, max_value=100.0,
                                                           step=0.5, format="%.1f"),
                "Max score": st.column_config.NumberColumn("Max", min_value=1, max_value=100, step=1),
            })
        total = active_total(edited)
        issues = problems(edited)
        ok = not issues
        st.markdown(
            f'Active weights total: <b class="rfp-num">{total}%</b> '
            + theme.pill("Ready to save" if ok else "Needs attention", "ok" if ok else "err"),
            unsafe_allow_html=True)
        for issue in issues:
            st.caption(f"⚠️ {issue}")

        b1, b2, b3 = st.columns(3)
        if b1.button("Normalise to 100%", key="normalise", help="Rescale active weights proportionally"):
            _reset_editor(normalise(edited))
            st.rerun()
        preset = b2.selectbox("Preset", ["Custom"] + list(PRESETS), label_visibility="collapsed",
                              key="preset_pick")
        if preset != "Custom" and b3.button(f"Apply {preset}", key="apply_preset"):
            applied = apply_preset(edited, preset)
            if applied is None:
                st.warning("Presets only apply to the standard five criteria.")
            else:
                _reset_editor(applied)
                st.rerun()

        with st.expander("Rubric anchors (optional, improves scoring consistency)"):
            st.caption("Describe what a low (2), medium (5) and high (8) score looks like.")
            for c in stored:
                if c.criterion_id not in set(base["id"]):
                    continue
                st.markdown(f"**{theme.esc(c.name)}**", unsafe_allow_html=True)
                cols = st.columns(3)
                for col, anchor in zip(cols, (2, 5, 8), strict=True):
                    col.text_area(f"{anchor} =", value=(c.rubric or {}).get(anchor, ""), height=90,
                                  key=f"rub_{c.criterion_id}_{anchor}")

        with st.expander("Add a criterion"):
            n = st.text_input("Name", key="new_crit_name")
            d = st.text_input("What the model should inspect", key="new_crit_desc")
            if st.button("Add (inactive, weight 0)", key="add_crit"):
                if not n.strip():
                    st.error("Give the new criterion a name.")
                else:
                    new_id = int(max(base["id"].max(), *(c.criterion_id for c in stored))) + 1
                    row = pd.DataFrame([{"id": new_id, "Active": False, "Name": n.strip(),
                                         "Description": d.strip(), "Weight (%)": 0.0,
                                         "Max score": 10}])
                    _reset_editor(pd.concat([edited, row], ignore_index=True))
                    st.rerun()

        if st.button("Save criteria", type="primary", disabled=not ok, key="save_crit"):
            rubrics = {int(r["id"]): {a: st.session_state.get(f"rub_{int(r['id'])}_{a}", "")
                                      for a in (2, 5, 8)} for _, r in edited.iterrows()}
            try:
                db.save_criteria(conn, frame_to_criteria(edited, stored, rubrics))
            except db.CriteriaError as exc:
                st.error(str(exc))
            else:
                _reset_editor(to_frame(db.load_criteria(conn)))
                state.notice("Criteria saved. New runs will use them.", "success")
                st.rerun()
    with right:
        if ok:
            st.plotly_chart(_donut(edited), width="stretch", key="weights_donut")
        else:
            st.markdown('<div class="rfp-card rfp-muted">Fix the issues on the left to preview the '
                        "weight split.</div>", unsafe_allow_html=True)

    st.divider()
    saved_ok = _saved_ok(stored)
    if not saved_ok:
        st.info("Save valid criteria (active weights = 100%) to continue.")
    if st.button("Continue to suppliers", type="primary", disabled=not saved_ok, key="to_suppliers"):
        state.goto("suppliers")
        st.rerun()


def _saved_ok(stored: list[Criterion]) -> bool:
    try:
        db.check_criteria(stored)
    except db.CriteriaError:
        return False
    return True

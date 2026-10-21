"""Step 2: view and edit the evaluation criteria and weights (stored in SQLite)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core import db
from core.models import Criterion
from core.tools.formula_scoring import check_formula_config
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


METHODS = ["Judged by the model", "Calculated from a figure"]
KIND_LABELS = {
    "lowest_is_best": "Lowest is best (price, delivery time)",
    "highest_is_best": "Highest is best (coverage, uptime)",
    "threshold_bands": "Threshold bands (set in the database)",
}


def advanced_defaults(c: Criterion | None) -> dict[str, Any]:
    params = (c.formula_params or {}) if c else {}
    return {
        "type": c.criterion_type if c else "llm_judged",
        "kind": (c.formula_kind if c and c.formula_kind else "lowest_is_best"),
        "label": str(params.get("label", "")),
        "unit": str(params.get("unit", "")),
        "mandatory": bool(c.is_mandatory) if c else False,
        "min_score": c.min_score if c and c.min_score is not None else 5,
    }


def advanced_from_state(
    state_: Mapping[str, Any], ids: Sequence[int], existing: Sequence[Criterion]
) -> dict[int, dict[str, Any]]:
    """Read the per-criterion advanced widgets (falling back to the stored values)."""
    by_id = {c.criterion_id: c for c in existing}
    out: dict[int, dict[str, Any]] = {}
    for cid in ids:
        d = advanced_defaults(by_id.get(cid))
        method = state_.get(f"adv_type_{cid}")
        if method is not None:
            d["type"] = "formula" if method == METHODS[1] else "llm_judged"
        if state_.get(f"adv_kind_{cid}") in KIND_LABELS:
            d["kind"] = state_[f"adv_kind_{cid}"]
        d["label"] = state_.get(f"adv_label_{cid}", d["label"])
        d["unit"] = state_.get(f"adv_unit_{cid}", d["unit"])
        d["mandatory"] = bool(state_.get(f"adv_mand_{cid}", d["mandatory"]))
        d["min_score"] = int(state_.get(f"adv_min_{cid}", d["min_score"]))
        out[cid] = d
    return out


def frame_to_criteria(
    df: pd.DataFrame,
    existing: list[Criterion],
    rubrics: dict[int, dict[int, str]],
    advanced: dict[int, dict[str, Any]] | None = None,
) -> list[Criterion]:
    by_id = {c.criterion_id: c for c in existing}
    out = []
    for _, row in df.iterrows():
        cid = int(row["id"])
        prior = by_id.get(cid)
        adv = (advanced or {}).get(cid) or advanced_defaults(prior)
        rubric = {k: v.strip() for k, v in rubrics.get(cid, {}).items() if v.strip()} or None
        formula = adv["type"] == "formula"
        params: dict[str, object] | None = None
        if formula:
            params = {**((prior.formula_params or {}) if prior else {}),
                      "label": str(adv["label"]).strip(), "unit": str(adv["unit"]).strip()}
        out.append(Criterion(
            criterion_id=cid, name=str(row["Name"]).strip(),
            description=str(row["Description"] or "").strip(),
            weight=float(row["Weight (%)"]), max_score=int(row["Max score"]),
            is_active=bool(row["Active"]), rubric=rubric,
            is_mandatory=bool(adv["mandatory"]),
            min_score=int(adv["min_score"]) if adv["mandatory"] else None,
            criterion_type="formula" if formula else "llm_judged",
            formula_kind=str(adv["kind"]) if formula else None, formula_params=params))
    return out


def advanced_problems(criteria: Sequence[Criterion]) -> list[str]:
    """Problems with formula or knock-out settings on active criteria."""
    out: list[str] = []
    for c in criteria:
        if not c.is_active:
            continue
        if c.criterion_type == "formula":
            out += check_formula_config(c)
        if c.is_mandatory and (c.min_score is None or not 0 <= c.min_score <= c.max_score):
            out.append(f"'{c.name}': the minimum score must be between 0 and {c.max_score}.")
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
        ids = [int(i) for i in edited["id"]]
        rubrics = {cid: {a: st.session_state.get(f"rub_{cid}_{a}", (_stored_rubric(stored, cid)).get(a, ""))
                         for a in (2, 5, 8)} for cid in ids}
        advanced = advanced_from_state(st.session_state, ids, stored)
        candidate = frame_to_criteria(edited, stored, rubrics, advanced)
        issues = problems(edited) + advanced_problems(candidate)
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

        with st.expander("Scoring method and knock-out rules (optional)"):
            st.caption("Price and delivery time are better calculated than judged: the model only "
                       "reads the figure from each proposal and Python scores it. A knock-out "
                       "disqualifies any supplier below a minimum score.")
            by_stored = {c.criterion_id: c for c in stored}
            for _, row in edited.iterrows():
                cid = int(row["id"])
                prior = by_stored.get(cid)
                d = advanced_defaults(prior)
                st.markdown(f"**{theme.esc(str(row['Name']))}**", unsafe_allow_html=True)
                kind_options = [k for k in KIND_LABELS if k != "threshold_bands" or d["kind"] == k]
                a1, a2 = st.columns([3, 2])
                a1.radio("Scoring method", METHODS, index=1 if d["type"] == "formula" else 0,
                         key=f"adv_type_{cid}", horizontal=True, label_visibility="collapsed")
                if advanced[cid]["type"] == "formula":
                    b1_, b2_, b3_ = st.columns([2, 3, 1])
                    b1_.selectbox("Formula", kind_options, format_func=KIND_LABELS.get,
                                  index=kind_options.index(d["kind"]) if d["kind"] in kind_options else 0,
                                  key=f"adv_kind_{cid}")
                    b2_.text_input("Figure to extract", value=d["label"], key=f"adv_label_{cid}",
                                   placeholder="e.g. Total price over the term")
                    b3_.text_input("Unit", value=d["unit"], key=f"adv_unit_{cid}",
                                   placeholder="USD")
                k1, k2 = st.columns([2, 1])
                k1.checkbox("Knock-out: disqualify below a minimum", value=d["mandatory"],
                            key=f"adv_mand_{cid}")
                if advanced[cid]["mandatory"]:
                    k2.number_input("Minimum score", min_value=0, max_value=int(row["Max score"]),
                                    value=min(int(d["min_score"]), int(row["Max score"])), step=1,
                                    key=f"adv_min_{cid}")

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
            try:
                db.save_criteria(conn, candidate)
            except db.CriteriaError as exc:
                st.error(str(exc))
            else:
                _reset_editor(to_frame(db.load_criteria(conn)))
                state.notice("Criteria saved. New runs will use them.", "success")
                state.mark_dirty()
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


def _stored_rubric(stored: Sequence[Criterion], cid: int) -> dict[int, str]:
    return next(((c.rubric or {}) for c in stored if c.criterion_id == cid), {})


def _saved_ok(stored: list[Criterion]) -> bool:
    try:
        db.check_criteria(stored)
    except db.CriteriaError:
        return False
    return True

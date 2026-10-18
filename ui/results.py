"""Step 4: leaderboard, scorecard drill-down, comparison and run details (built from the export)."""

from __future__ import annotations

import re
from typing import Any

import pandas as pd
import streamlit as st

from core.export import export_json
from core.overrides import OverrideError, OverrideRequest, apply_overrides
from ui import charts, state, theme

_PAGE_REF = re.compile(r"\[p\.\s*(\d+)\]")


# ------------------------------------------------------------- pure helpers (tested)


def leaderboard_rows(export: dict[str, Any]) -> list[dict[str, Any]]:
    return export["leaderboard"]


def supplier_by_id(export: dict[str, Any], supplier_id: str) -> dict[str, Any]:
    return next(s for s in export["suppliers"] if s["supplier_id"] == supplier_id)


def other_suppliers(export: dict[str, Any]) -> list[dict[str, Any]]:
    """Suppliers that are not ranked (failed or disqualified), with their reasons."""
    out = []
    for s in export["suppliers"]:
        comp = s["computed"]
        if comp["status"] == "RANKED":
            continue
        reasons = comp["disqualified_reasons"] or [
            w["message"] for w in export["warnings"]
            if w["supplier_id"] == s["supplier_id"] and w["severity"] == "error"
        ] or ["No details available."]
        out.append({"supplier_id": s["supplier_id"], "name": s["supplier_name"],
                    "status": comp["status"], "reasons": reasons})
    return out


def page_refs(text: str) -> list[int]:
    return sorted({int(m) for m in _PAGE_REF.findall(text)})


def warnings_for(export: dict[str, Any], supplier_id: str | None) -> list[dict[str, Any]]:
    return [w for w in export["warnings"] if w["supplier_id"] == supplier_id]


def criterion_rows(export: dict[str, Any], supplier_id: str) -> list[dict[str, Any]]:
    names = charts.criteria_names(export)
    comp = supplier_by_id(export, supplier_id)["computed"]
    return [{"Criterion": names[c["criterion_id"]],
             "Score": "n/a" if c["score"] is None else f"{c['score']}/{c['max_score']}",
             "Benchmark": "n/a" if c["benchmark"] is None else c["benchmark"],
             "Gap": "n/a" if c["gap"] is None else c["gap"],
             "Relative %": c["relative_pct"], "Weight %": c["weight"],
             "Contribution": c["weighted_contribution"]} for c in comp["criteria"]]


_RULE_LABEL = {"PPI": "PPI", "SUBMISSION_DATE": "submission date", "EXPERIENCE": "experience rating",
               "NAME": "supplier name", "SUPPLIER_ID": "supplier id"}


def tie_break_text(export: dict[str, Any]) -> list[str]:
    return [f"#{t['higher_rank']} {t['higher_name']} over #{t['lower_rank']} {t['lower_name']} "
            f"- decided by {_RULE_LABEL.get(t['deciding_rule'], t['deciding_rule'])}: {t['detail']}"
            for t in export["tie_break_explanations"]]


def compress_pages(pages: list[int]) -> str:
    """[3, 5, 6, 7, 71] -> '3, 5-7, 71'."""
    out: list[str] = []
    for p in sorted(set(pages)):
        if out and out[-1].split("-")[-1] == str(p - 1):
            out[-1] = f"{out[-1].split('-')[0]}-{p}"
        else:
            out.append(str(p))
    return ", ".join(out)


def facts_rows(export: dict[str, Any], supplier_id: str) -> list[dict[str, Any]]:
    card = supplier_by_id(export, supplier_id)["scorecard"] or {}
    names = charts.criteria_names(export)
    rows = []
    for f in card.get("facts", []):
        value = "not found" if f["value"] is None else f"{f['value']:,.2f}".rstrip("0").rstrip(".")
        verified = {True: "Verified", False: "Not found in document", None: ""}[f["evidence_verified"]]
        rows.append({"Criterion": names.get(f["criterion_id"], ""), "Figure": f["label"],
                     "Value": value, "Unit": f["unit"], "Quote check": verified})
    return rows


def overrides_for(export: dict[str, Any], supplier_id: str, criterion_id: int) -> list[dict[str, Any]]:
    return [o for o in export.get("overrides", [])
            if o["supplier_id"] == supplier_id and o["criterion_id"] == criterion_id]


def edited_supplier_ids(export: dict[str, Any]) -> set[str]:
    return {o["supplier_id"] for o in export.get("overrides", [])}


def overrides_table(export: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"When": o["created_at"], "Supplier": o["supplier_name"], "Criterion": o["criterion_name"],
             "Model score": o["original_score"], "New score": o["new_score"], "Reason": o["reason"]}
            for o in export.get("overrides", [])]


def _apply_override(export: dict[str, Any], supplier_id: str, criterion_id: int, score: int,
                    reason: str) -> None:
    try:
        new_export = apply_overrides(state.conn(), export["rfp_run_id"],
                                     [OverrideRequest(supplier_id, criterion_id, score, reason)])
    except OverrideError as exc:
        st.error(str(exc))
        return
    st.session_state["viewing_export"] = new_export
    state.notice("Score updated. The ranking was recalculated and the change was logged.", "success")
    st.rerun()


def _override_form(export: dict[str, Any], supplier_id: str, c: dict[str, Any],
                   res: dict[str, Any]) -> None:
    cid = c["criterion_id"]
    key = f"{supplier_id}_{cid}"
    st.markdown("**Analyst override**")
    with st.form(f"ovr_form_{key}", clear_on_submit=False):
        f1, f2 = st.columns([1, 3])
        new = f1.number_input("New score", min_value=0, max_value=int(c["max_score"]), step=1,
                              value=int(c["score"] or 0), key=f"ovr_score_{key}")
        reason = f2.text_input("Reason (required)", key=f"ovr_reason_{key}",
                               placeholder="Why does the model's score need changing?")
        applied = st.form_submit_button("Apply change")
    if applied:
        _apply_override(export, supplier_id, cid, int(new), reason)
    reverting = (
        c["overridden"]
        and c["original_score"] is not None
        and st.button("Revert to the model's score", key=f"ovr_revert_{key}")
    )
    if reverting:
        _apply_override(export, supplier_id, cid, int(c["original_score"]),
                        "Reverted to the model's score")


# ---------------------------------------------------------------------- scorecard


@st.dialog("Scorecard", width="large")
def scorecard_dialog(export: dict[str, Any], supplier_id: str) -> None:
    sup = supplier_by_id(export, supplier_id)
    comp, card = sup["computed"], sup["scorecard"]
    st.markdown(f"### {theme.esc(sup['supplier_name'])}", unsafe_allow_html=True)
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Rank", f"#{comp['rank']}" if comp["rank"] else "-")
    m2.metric("Absolute score", f"{comp['absolute_score']:.2f}" if comp["absolute_score"] is not None else "-")
    m3.metric("PPI", f"{comp['ppi']:.2f}" if comp["ppi"] is not None else "-")
    m4.metric("Submitted", comp["submission_date"])
    if comp["status"] != "RANKED":
        st.warning("; ".join(comp["disqualified_reasons"] or ["This supplier could not be scored."]))
    if not card or not comp["criteria"]:
        return

    names = charts.criteria_names(export)
    if card.get("evaluation_mode") == "retrieval":
        st.info("Long document: each criterion was scored from the best-matching passages, not the "
                "whole text. Quotes are still checked against the full document.")
    left, right = st.columns([3, 2], gap="medium")
    with left:
        st.dataframe(pd.DataFrame(criterion_rows(export, supplier_id)), hide_index=True,
                     width="stretch")
    with right:
        st.plotly_chart(charts.radar(comp, names), width="stretch",
                        key=f"radar_{supplier_id}")

    facts = facts_rows(export, supplier_id)
    if facts:
        st.markdown("#### Figures read from the proposal")
        st.caption("The model only reads these numbers; Python turns them into scores.")
        st.dataframe(pd.DataFrame(facts), hide_index=True, width="stretch")

    st.markdown("#### Evidence and reasoning")
    by_cid = {r["criterion_id"]: r for r in card["criteria"]}
    can_edit = comp["status"] in ("RANKED", "DISQUALIFIED") and export["status"] != "FAILED"
    for c in comp["criteria"]:
        res = by_cid[c["criterion_id"]]
        label = names[c["criterion_id"]]
        score = "n/a" if c["score"] is None else f"{c['score']}/{c['max_score']}"
        suffix = " ✎" if c["overridden"] else ""
        with st.expander(f"{label} - {score}{suffix}"):
            pills = []
            if res.get("source") == "formula":
                pills.append(theme.pill("Calculated from a figure", "info"))
            if c["overridden"]:
                pills.append(theme.pill("Analyst override", "warn"))
            if res["status"] != "ok":
                pills.append(theme.pill(f"Score {res['status']}", "warn"))
            if res.get("retrieved_pages"):
                pills.append(theme.pill(f"Passages from pages {compress_pages(res['retrieved_pages'])}", "info"))
            if pills:
                st.markdown(" ".join(pills), unsafe_allow_html=True)
            st.markdown(f"**Why:** {res['justification'] or '_No justification given._'}")
            if res["evidence"]:
                bad = res["evidence_verified"] is False
                pages = ", ".join(f"p.{p}" for p in page_refs(res["evidence"]))
                badge = theme.pill("Not found in document", "warn") if bad else (
                    theme.pill("Verified in document", "ok") if res["evidence_verified"] else "")
                st.markdown(
                    f'<div class="rfp-quote{" unverified" if bad else ""}">{theme.esc(res["evidence"])}'
                    f"</div>{badge} {theme.esc(pages)}", unsafe_allow_html=True)
            else:
                st.caption("No supporting evidence quoted.")
            for o in overrides_for(export, supplier_id, c["criterion_id"]):
                st.caption(f"✎ {o['created_at']}: model score {o['original_score']} → {o['new_score']}. "
                           f"Reason: {o['reason']}")
            if can_edit:
                _override_form(export, supplier_id, c, res)
    if card["risks"]:
        st.markdown("#### Risks noted")
        for r in card["risks"]:
            st.markdown(f"- {r}")
    if card["overall_summary"]:
        st.markdown("#### Summary")
        st.write(card["overall_summary"])
    ws = warnings_for(export, supplier_id)
    if ws:
        st.markdown("#### Warnings")
        for w in ws:
            st.caption(f"{theme.severity_icon(w['severity'])} {w['message']}")


# ---------------------------------------------------------------------- leaderboard


def _podium(rows: list[dict[str, Any]]) -> None:
    order = [1, 0, 2] if len(rows) >= 3 else list(range(len(rows)))
    cells = []
    for i in order:
        r = rows[i]
        cells.append(
            f'<div class="rfp-pod r{r["rank"]}"><div class="rfp-rank">{r["rank"]}</div>'
            f'<div class="name">{theme.esc(r["supplier_name"])}</div>'
            f'<div class="score">{r["ppi"]:.1f}</div><div class="label">PPI</div>'
            f'<div class="rfp-muted rfp-num">Absolute {r["absolute_score"]:.1f}</div></div>')
    st.markdown(f'<div class="rfp-podium">{"".join(cells)}</div>', unsafe_allow_html=True)


def _leaderboard(export: dict[str, Any]) -> None:
    rows = leaderboard_rows(export)
    if not rows:
        st.error("No supplier could be scored in this run.")
    else:
        _podium(rows[:3])
        widths = [0.7, 3, 2.4, 2.4, 2, 2, 1.6]
        head = st.columns(widths)
        for col, text in zip(head, ["#", "Supplier", "Absolute score", "PPI", "Submitted", "Experience",
                                    ""], strict=True):
            col.caption(text)
        ids = {s["supplier_name"]: s["supplier_id"] for s in export["suppliers"]}
        for r in rows:
            with st.container(border=True, key=f"lb_row_{r['rank']}"):
                c = st.columns(widths, vertical_alignment="center")
                c[0].markdown(f'<span class="rfp-rank">{r["rank"]}</span>', unsafe_allow_html=True)
                edited_mark = (" " + theme.pill("edited", "warn")
                               if ids[r["supplier_name"]] in edited_supplier_ids(export) else "")
                c[1].markdown(f"**{theme.esc(r['supplier_name'])}**{edited_mark}",
                              unsafe_allow_html=True)
                c[2].progress(min(r["absolute_score"] / 100, 1.0), text=f"{r['absolute_score']:.1f}")
                c[3].progress(min(r["ppi"] / 100, 1.0), text=f"{r['ppi']:.1f}")
                c[4].write(r["submission_date"])
                c[5].write("★" * int(r["experience_rating"]) + f" {r['experience_rating']}")
                if c[6].button("Open", key=f"open_{ids[r['supplier_name']]}", width="stretch",
                               help=f"Open the scorecard for {r['supplier_name']}"):
                    scorecard_dialog(export, ids[r["supplier_name"]])
        for line in tie_break_text(export):
            st.caption("↕ " + line)
    for o in other_suppliers(export):
        with st.container(border=True):
            st.markdown(f"{theme.status_pill(o['status'])} &nbsp; **{theme.esc(o['name'])}**",
                        unsafe_allow_html=True)
            for reason in o["reasons"]:
                st.caption(reason)


# ------------------------------------------------------------------ compare / details


def _compare(export: dict[str, Any]) -> None:
    if not charts.ranked(export):
        st.info("Nothing to compare yet.")
        return
    metric = st.radio("Show", ["Relative performance %", "Score vs maximum"], horizontal=True,
                      key="heat_metric")
    key = "relative" if metric.startswith("Relative") else "score"
    st.plotly_chart(charts.heatmap(export, key), width="stretch", key=f"heat_{key}")
    st.markdown("##### Absolute score vs Peer Performance Index")
    st.plotly_chart(charts.score_bars(export), width="stretch", key="score_bars")
    st.caption("PPI measures each supplier against the best peer on every criterion; the absolute "
               "score measures it against the maximum. When everyone scores high, PPI compresses.")


def _details(export: dict[str, Any]) -> None:
    c1, c2, c3 = st.columns([3, 1, 2])
    c1.markdown("**RFP_RUN_ID**")
    c1.code(export["rfp_run_id"], language=None)
    c2.markdown("**Status**")
    c2.markdown(theme.status_pill(export["status"]), unsafe_allow_html=True)
    c3.markdown("**Model**")
    c3.write(f"{export.get('model') or '-'} · prompt {export.get('prompt_hash') or '-'}")
    st.caption(f"Created {export['created_at']}")

    st.download_button("Download full result (JSON)", data=export_json(export),
                       file_name=f"rfp_run_{export['rfp_run_id'][:8]}.json", mime="application/json",
                       type="primary", key="download_json")

    opts = export.get("options", {})
    mode = {"auto": "automatic", "full": "full document", "retrieve": "passage search"}.get(
        opts.get("retrieval", "auto"), "automatic")
    st.caption(f"Long proposals: {mode} · knock-out rules: "
               f"{'on' if opts.get('enable_knockouts') else 'off'}")

    st.markdown("##### Criteria used")
    st.dataframe(pd.DataFrame([{
        "Criterion": c["name"], "Weight %": c["weight"], "Max": c["max_score"],
        "Scored by": "Calculated from a figure" if c.get("criterion_type") == "formula" else "Model",
        "Knock-out": f"minimum {c['min_score']}" if c.get("is_mandatory") else ""}
        for c in export["criteria_snapshot"]]), hide_index=True, width="stretch")

    st.markdown("##### Tie-break explanations")
    texts = tie_break_text(export)
    for t in texts or ["No adjacent suppliers to compare."]:
        st.write("- " + t)

    audit = overrides_table(export)
    if audit:
        st.markdown("##### Analyst overrides (audit trail)")
        st.caption("The model's original scores are kept; these changes were applied on top and "
                   "the ranking was recalculated.")
        st.dataframe(pd.DataFrame(audit), hide_index=True, width="stretch")

    st.markdown("##### Warnings")
    names = {s["supplier_id"]: s["supplier_name"] for s in export["suppliers"]}
    cnames = charts.criteria_names(export)
    levels = st.multiselect("Severity", ["error", "warn", "info"], default=["error", "warn", "info"],
                            key="warn_filter")
    shown = [w for w in export["warnings"] if w["severity"] in levels]
    if shown:
        st.dataframe(pd.DataFrame([{
            "": theme.severity_icon(w["severity"]), "Code": w["code"],
            "Supplier": names.get(w["supplier_id"], "Run"),
            "Criterion": cnames.get(w["criterion_id"], ""), "Message": w["message"]} for w in shown]),
            hide_index=True, width="stretch")
    else:
        st.caption("No warnings at these levels.")

    sens = export.get("sensitivity")
    if sens:
        st.markdown("##### Ranking robustness")
        st.caption(f"{sens['draws']} random weightings around yours (seed {sens['seed']}). "
                   "Each bar shows how often a supplier lands at each rank.")
        st.plotly_chart(charts.robustness(sens, names), width="stretch", key="robust_chart")
        changed = [o for o in sens["one_at_a_time"] if o["changed"]]
        if changed:
            cn = charts.criteria_names(export)
            st.warning("The ranking changes if you shift: " + "; ".join(
                f"{cn[o['criterion_id']]} by {o['delta']:+.0f} points" for o in changed))
        else:
            st.success("Moving any single weight by ±10 points does not change the order.")


# ------------------------------------------------------------------------- screen


def render() -> None:
    export = st.session_state.get("viewing_export")
    if export is None:
        st.info("Run an evaluation to see results.")
        return
    head, side = st.columns([4, 1], vertical_alignment="center")
    head.subheader("Results")
    head.markdown(f"{theme.status_pill(export['status'])} &nbsp; "
                  f"<span class='rfp-muted'>Run {theme.esc(export['rfp_run_id'][:8])} · "
                  f"{theme.esc(export['created_at'])}</span>", unsafe_allow_html=True)
    if side.button("New evaluation", key="new_eval"):
        state.goto("suppliers")
        st.rerun()
    t1, t2, t3 = st.tabs(["Leaderboard", "Compare", "Run details"])
    with t1:
        _leaderboard(export)
    with t2:
        _compare(export)
    with t3:
        _details(export)

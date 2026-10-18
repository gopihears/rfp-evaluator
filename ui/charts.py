"""Plotly figures built from an export dict. Pure: no Streamlit calls."""

from __future__ import annotations

from typing import Any

import plotly.graph_objects as go

from ui.theme import ACCENT, AMBER, TEAL

LAYOUT: dict[str, Any] = {"paper_bgcolor": "rgba(0,0,0,0)", "plot_bgcolor": "rgba(0,0,0,0)",
                          "margin": {"l": 10, "r": 10, "t": 30, "b": 10}}


def criteria_names(export: dict[str, Any]) -> dict[int, str]:
    return {c["criterion_id"]: c["name"] for c in export["criteria_snapshot"]}


def ranked(export: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [s["computed"] for s in export["suppliers"] if s["computed"]["status"] == "RANKED"]
    return sorted(rows, key=lambda c: c["rank"])


def radar(computed: dict[str, Any], names: dict[int, str]) -> go.Figure:
    labels = [names[c["criterion_id"]] for c in computed["criteria"]]
    values = [c["relative_pct"] for c in computed["criteria"]]
    fig = go.Figure()
    fig.add_trace(go.Scatterpolar(r=[100] * (len(labels) + 1), theta=labels + labels[:1],
                                  name="Best peer (100%)", line={"color": TEAL, "dash": "dot"}))
    fig.add_trace(go.Scatterpolar(r=values + values[:1], theta=labels + labels[:1],
                                  name=computed["supplier_name"], fill="toself",
                                  line={"color": ACCENT}, fillcolor="rgba(79,70,229,.25)"))
    layout = {**LAYOUT, "margin": {"l": 70, "r": 70, "t": 30, "b": 20}}
    fig.update_layout(polar={"radialaxis": {"range": [0, 100], "ticksuffix": "%"}},
                      legend={"orientation": "h", "y": -0.1}, height=360, **layout)
    return fig


def score_bars(export: dict[str, Any]) -> go.Figure:
    rows = ranked(export)
    names = [r["supplier_name"] for r in rows]
    fig = go.Figure()
    fig.add_trace(go.Bar(name="Absolute score", x=names, y=[r["absolute_score"] for r in rows],
                         marker_color=ACCENT, hovertemplate="%{y:.2f}<extra>Absolute score</extra>"))
    fig.add_trace(go.Bar(name="Peer Performance Index", x=names, y=[r["ppi"] for r in rows],
                         marker_color=TEAL, hovertemplate="%{y:.2f}<extra>PPI</extra>"))
    fig.update_layout(barmode="group", yaxis={"range": [0, 100]}, height=320,
                      legend={"orientation": "h", "y": 1.12}, **LAYOUT)
    return fig


def heatmap(export: dict[str, Any], metric: str) -> go.Figure:
    """metric: 'score' (raw score as % of max) or 'relative' (relative performance %)."""
    names = criteria_names(export)
    rows = ranked(export)
    z, text = [], []
    for r in rows:
        zr, tr = [], []
        for c in r["criteria"]:
            if metric == "score":
                value = None if c["score"] is None else c["score"] / c["max_score"] * 100
                label = "n/a" if c["score"] is None else f"{c['score']}/{c['max_score']}"
            else:
                value, label = c["relative_pct"], f"{c['relative_pct']:.0f}%"
            zr.append(value)
            tr.append(label)
        z.append(zr)
        text.append(tr)
    fig = go.Figure(go.Heatmap(
        z=z, x=[names[c["criterion_id"]] for c in rows[0]["criteria"]] if rows else [],
        y=[r["supplier_name"] for r in rows], text=text, texttemplate="%{text}", zmin=0, zmax=100,
        colorscale=[[0, "#fde68a"], [0.5, "#a5b4fc"], [1, ACCENT]], showscale=False,
        hovertemplate="%{y} - %{x}: %{text}<extra></extra>"))
    fig.update_layout(height=max(220, 70 * len(rows) + 90), yaxis={"autorange": "reversed"}, **LAYOUT)
    return fig


def robustness(sens: dict[str, Any], suppliers: dict[str, str]) -> go.Figure:
    colors = [ACCENT, TEAL, AMBER, "#9ca3af", "#f472b6", "#60a5fa"]
    n = len(sens["robustness"])
    fig = go.Figure()
    for place in range(1, n + 1):
        fig.add_trace(go.Bar(
            name=f"Rank {place}", orientation="h", marker_color=colors[(place - 1) % len(colors)],
            y=[suppliers.get(r["supplier_id"], r["supplier_id"]) for r in sens["robustness"]],
            x=[r["rank_distribution"].get(str(place), r["rank_distribution"].get(place, 0)) * 100
               for r in sens["robustness"]],
            hovertemplate="%{x:.0f}% of weightings<extra>Rank " + str(place) + "</extra>"))
    fig.update_layout(barmode="stack", xaxis={"range": [0, 100], "ticksuffix": "%"}, height=90 + 55 * n,
                      yaxis={"autorange": "reversed"}, legend={"orientation": "h", "y": 1.15}, **LAYOUT)
    return fig

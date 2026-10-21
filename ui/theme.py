"""Visual theme: CSS injected once, plus small HTML helpers. Works in light and dark mode."""

from __future__ import annotations

import html

import streamlit as st

ACCENT = "#4f46e5"
TEAL = "#0d9488"
AMBER = "#d97706"
RED = "#dc2626"
GREEN = "#16a34a"

CSS = f"""
<style>
:root {{
  --rfp-accent: {ACCENT};
  --rfp-border: rgba(128, 128, 128, .28);
  --rfp-soft: rgba(128, 128, 128, .08);
  --rfp-radius: 14px;
}}
.block-container {{ padding-top: 3.6rem; max-width: 1180px; }}
h1, h2, h3 {{ letter-spacing: -0.01em; }}
[data-testid="stMetricValue"], .rfp-num {{ font-variant-numeric: tabular-nums; }}

/* top bar */
.rfp-top {{ display:flex; align-items:center; justify-content:space-between; gap:1rem;
  flex-wrap:wrap; margin-bottom:.4rem; }}
.rfp-brand {{ display:flex; align-items:center; gap:.7rem; }}
.rfp-logo {{ width:38px; height:38px; border-radius:11px; display:grid; place-items:center;
  background:linear-gradient(135deg, {ACCENT}, {TEAL}); color:#fff; font-weight:700; font-size:18px; }}
.rfp-title {{ font-size:1.25rem; font-weight:700; line-height:1.1; }}
.rfp-sub {{ font-size:.82rem; opacity:.7; }}

/* chips and pills */
.rfp-chip {{ display:inline-flex; align-items:center; gap:.4rem; padding:.28rem .7rem;
  border:1px solid var(--rfp-border); border-radius:999px; font-size:.8rem; background:var(--rfp-soft); }}
.rfp-dot {{ width:8px; height:8px; border-radius:50%; background:{GREEN}; display:inline-block; }}
.rfp-pill {{ display:inline-block; padding:.12rem .6rem; border-radius:999px; font-size:.74rem;
  font-weight:600; border:1px solid transparent; }}
.rfp-pill.ok {{ color:{GREEN}; background:rgba(22,163,74,.12); border-color:rgba(22,163,74,.35); }}
.rfp-pill.warn {{ color:{AMBER}; background:rgba(217,119,6,.12); border-color:rgba(217,119,6,.35); }}
.rfp-pill.err {{ color:{RED}; background:rgba(220,38,38,.12); border-color:rgba(220,38,38,.35); }}
.rfp-pill.info {{ color:{ACCENT}; background:rgba(79,70,229,.12); border-color:rgba(79,70,229,.35); }}
.rfp-pill.demo {{ color:#fff; background:{AMBER}; }}

/* cards */
.rfp-card {{ border:1px solid var(--rfp-border); border-radius:var(--rfp-radius);
  padding:1rem 1.15rem; background:var(--rfp-soft); }}
.rfp-hero {{ border:1px solid var(--rfp-border); border-radius:18px; padding:1.6rem 1.8rem;
  background:linear-gradient(135deg, rgba(79,70,229,.10), rgba(13,148,136,.08)); }}
.rfp-hero h2 {{ margin:0 0 .35rem 0; }}
.rfp-muted {{ opacity:.72; font-size:.9rem; }}

/* stepper */
.rfp-steps {{ display:flex; gap:.5rem; margin:.4rem 0 1.1rem 0; flex-wrap:wrap; }}
.rfp-step {{ display:flex; align-items:center; gap:.5rem; padding:.45rem .85rem;
  border:1px solid var(--rfp-border); border-radius:999px; font-size:.88rem; }}
.rfp-step .n {{ width:22px; height:22px; border-radius:50%; display:grid; place-items:center;
  font-size:.75rem; font-weight:700; background:var(--rfp-soft); }}
.rfp-step.active {{ border-color:var(--rfp-accent); background:rgba(79,70,229,.10); font-weight:600; }}
.rfp-step.active .n {{ background:var(--rfp-accent); color:#fff; }}
.rfp-step.done .n {{ background:{GREEN}; color:#fff; }}
.rfp-step.locked {{ opacity:.5; }}

/* podium */
.rfp-podium {{ display:grid; grid-template-columns:repeat(3, 1fr); gap:.9rem; align-items:end;
  margin:.4rem 0 1.2rem 0; }}
.rfp-pod {{ border:1px solid var(--rfp-border); border-radius:16px; padding:1rem 1.1rem;
  background:var(--rfp-soft); text-align:center; }}
.rfp-pod.r1 {{ border-color:var(--rfp-accent); background:linear-gradient(180deg, rgba(79,70,229,.16), rgba(79,70,229,.04));
  padding-top:1.4rem; padding-bottom:1.4rem; }}
.rfp-rank {{ display:inline-grid; place-items:center; width:34px; height:34px; border-radius:50%;
  font-weight:700; background:var(--rfp-accent); color:#fff; margin-bottom:.35rem; }}
.rfp-pod.r2 .rfp-rank {{ background:{TEAL}; }}
.rfp-pod.r3 .rfp-rank {{ background:{AMBER}; }}
.rfp-pod .name {{ font-weight:700; font-size:1.05rem; }}
.rfp-pod .score {{ font-size:1.6rem; font-weight:700; font-variant-numeric:tabular-nums; }}
.rfp-pod .label {{ font-size:.74rem; opacity:.7; text-transform:uppercase; letter-spacing:.06em; }}

/* evidence */
.rfp-quote {{ border-left:3px solid var(--rfp-accent); padding:.4rem .8rem; margin:.3rem 0;
  background:var(--rfp-soft); border-radius:0 8px 8px 0; font-size:.9rem; }}
.rfp-quote.unverified {{ border-left-color:{AMBER}; }}

@media (max-width: 700px) {{
  .rfp-podium {{ grid-template-columns:1fr; }}
  .block-container {{ padding-left:1rem; padding-right:1rem; }}
}}
iframe[title="rfp_local_store.rfp_local_store"] {{ height:0; width:0; border:0; display:block; }}
@media (prefers-reduced-motion: reduce) {{ * {{ transition:none !important; animation:none !important; }} }}
</style>
"""


def inject() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def esc(value: object) -> str:
    """HTML-escape anything that came from a document, a model or a user."""
    return html.escape(str(value), quote=True)


def pill(text: str, kind: str = "info") -> str:
    return f'<span class="rfp-pill {kind}">{esc(text)}</span>'


def status_pill(status: str) -> str:
    kind = {"COMPLETED": "ok", "RANKED": "ok", "PARTIAL": "warn", "DISQUALIFIED": "warn",
            "FAILED": "err", "RUNNING": "info"}.get(status, "info")
    return pill(status.title(), kind)


def severity_icon(severity: str) -> str:
    return {"error": "⛔", "warn": "⚠️", "info": "ℹ️"}.get(severity, "•")

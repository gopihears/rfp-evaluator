"""Step 1: connect with an API key (OpenAI or OpenRouter) for this session only."""

from __future__ import annotations

import streamlit as st

from core.tools.llm_provider import ProviderError, is_chat_model, mask_key, scrub
from core.tools.provider_factory import DEMO_MODEL, demo_mode, make_provider, provider_kind
from ui import state, theme

_PREFERRED = ("gpt-5", "gpt-4.1", "gpt-4o", "o4", "o3")


PROVIDERS = {"OpenAI": "openai", "OpenRouter": "openrouter"}
_ROUTER_PREFERRED = ("openai/gpt-4o-mini", "openai/gpt-4.1-mini", "openai/gpt-5-mini")


def default_model(models: list[str], kind: str = "openai") -> str:
    """Prefer a flagship base model; otherwise the first available."""
    if kind == "openrouter":
        for wanted in _ROUTER_PREFERRED:
            if wanted in models:
                return wanted
        return sorted(models)[0]
    for prefix in _PREFERRED:
        candidates = [m for m in models if m.startswith(prefix)]
        if candidates:
            return min(candidates, key=lambda m: (len(m), m))
    return models[0]


def _finish(provider: object, models: list[str], hint: str, kind: str = "openai") -> None:
    ss = st.session_state
    ss["provider"], ss["models"], ss["model"], ss["key_hint"] = (
        provider,
        models,
        default_model(models, kind),
        hint,
    )
    state.goto("criteria")


def _connect(key: str, kind: str) -> None:
    try:
        provider = make_provider(key, kind=kind)
        models = provider.validate_key()
    except ProviderError as exc:
        st.error(scrub(str(exc)))
        return
    except Exception as exc:  # never show a raw trace that might contain the key
        st.error(f"Could not connect: {scrub(type(exc).__name__)}")
        return
    chat = [m for m in models if is_chat_model(m, routed=kind == "openrouter")] or models
    if not chat:
        st.error("This key works, but no chat models are available to it.")
        return
    hint = mask_key(key) if kind == "openai" else f"OpenRouter {mask_key(key)}"
    _finish(provider, chat, hint, kind)
    st.rerun()


def render() -> None:
    st.markdown(
        '<div class="rfp-hero"><h2>Evaluate supplier proposals with explainable AI scoring</h2>'
        '<div class="rfp-muted">Upload RFP responses, let the model judge each proposal against your '
        "criteria, and get a transparent, reproducible ranking. The model reads and scores; "
        "Python does all the arithmetic, benchmarking and tie-breaking.</div></div>",
        unsafe_allow_html=True,
    )
    st.write("")

    if demo_mode():
        st.warning(
            "**Demo mode** is on (RFP_FAKE_LLM=1). Scoring uses an offline keyword-based "
            "stand-in, not an AI model. No API key is needed or used."
        )
        if st.button("Start demo session", type="primary", key="start_demo"):
            _finish(make_provider(""), [DEMO_MODEL], "demo")
            st.rerun()
        return

    left, right = st.columns([3, 2], gap="large")
    with left:
        if state.connected():
            st.success(f"Connected ({st.session_state['key_hint']}). Choose a model and continue.")
            st.selectbox(
                "Model",
                st.session_state["models"],
                key="model",
                help="Models available to your key.",
            )
            c1, c2 = st.columns(2)
            if c1.button("Continue", type="primary", key="continue_btn"):
                state.goto("criteria")
                st.rerun()
            if c2.button("Disconnect", key="disconnect_here"):
                state.disconnect()
                st.rerun()
        else:
            choice = st.radio(
                "Provider",
                list(PROVIDERS),
                horizontal=True,
                key="provider_choice",
                index=1 if provider_kind(None) == "openrouter" else 0,
                help="OpenRouter gives one key for many models from many vendors.",
            )
            kind = PROVIDERS[choice]
            if kind == "openrouter":
                st.caption(
                    "OpenRouter forwards your documents to the model vendor it routes to, and "
                    "free models often log or train on what they receive. Use it for "
                    "testing with fictional proposals, not for confidential bids."
                )
            with st.form("connect_form", clear_on_submit=True):
                key = st.text_input(
                    "API key",
                    type="password",
                    placeholder="sk-... (OpenAI) or sk-or-... (OpenRouter)",
                    help="Used only for this browser session.",
                )
                submitted = st.form_submit_button("Connect", type="primary")
            if submitted:
                if not key.strip():
                    st.error("Enter your API key to continue.")
                else:
                    with st.spinner("Checking your key..."):
                        _connect(key.strip(), kind)
    with right:
        st.markdown(
            '<div class="rfp-card"><b>How your key is handled</b><ul style="margin:.4rem 0 0 1.1rem">'
            "<li>Typed here, held in this session's memory only</li>"
            "<li>Never written to disk, the database, logs or exports</li>"
            "<li>Cleared when you disconnect or close the tab</li>"
            "<li>Sent only to the provider you choose, to score your documents</li></ul></div>",
            unsafe_allow_html=True,
        )
        if state.effective_db_mode() == "session":
            st.caption(
                "Your criteria, runs and results are private to this browser session and "
                "are not stored on the server. They disappear when the session ends."
            )
        else:
            st.caption("Runs and criteria are saved in a local database file on this machine.")
        if not state.connected():
            st.caption(f"Status: {theme.pill('Not connected', 'warn')}", unsafe_allow_html=True)

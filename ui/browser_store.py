"""Python side of the in-browser history store (see ui/local_store/index.html)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import streamlit.components.v1 as components

_component = components.declare_component(
    "rfp_local_store", path=str(Path(__file__).parent / "local_store")
)


def call(action: str, *, token: str, payload: str | None = None) -> dict[str, Any] | None:
    """Ask the browser to load, save or clear the stored history.

    Returns None until the browser has answered (and always, outside a real browser, e.g. in
    tests), so callers must work without ever getting a reply.
    """
    reply = _component(
        action=action, token=token, payload=payload, key=f"rfp_store_{token}", default=None
    )
    return reply if isinstance(reply, dict) and reply.get("token") == token else None

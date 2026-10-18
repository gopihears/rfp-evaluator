"""Runtime settings. None of these values is a secret; the API key never lives here."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any


@dataclass(frozen=True)
class Settings:
    db_path: str = "rfp.db"
    db_mode: str = "file"  # "file" (shared on disk) or "session" (private, in memory)
    max_pages: int = 150
    max_bytes: int = 25_000_000
    full_context_max_tokens: int = 60_000
    retrieval_top_k: int = 8
    max_suppliers: int = 10
    concurrency: int = 3
    llm_timeout: int = 60
    ppi_precision: int = 6


_INT_FIELDS = {
    "RFP_MAX_PAGES": "max_pages",
    "RFP_MAX_BYTES": "max_bytes",
    "RFP_FULL_CONTEXT_MAX_TOKENS": "full_context_max_tokens",
    "RFP_RETRIEVAL_TOP_K": "retrieval_top_k",
    "RFP_MAX_SUPPLIERS": "max_suppliers",
    "RFP_CONCURRENCY": "concurrency",
    "RFP_LLM_TIMEOUT": "llm_timeout",
}


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Build settings from environment variables; invalid values fall back to defaults."""
    source = os.environ if env is None else env
    defaults = Settings()
    values: dict[str, Any] = {}
    if source.get("RFP_DB_MODE", "").strip().lower() in ("file", "session"):
        values["db_mode"] = source["RFP_DB_MODE"].strip().lower()
    if "RFP_DB_PATH" in source and source["RFP_DB_PATH"].strip():
        values["db_path"] = source["RFP_DB_PATH"].strip()
    for var, field in _INT_FIELDS.items():
        raw = source.get(var)
        if raw is None:
            continue
        try:
            parsed = int(raw)
        except ValueError:
            continue
        if parsed > 0:
            values[field] = parsed
    return replace(defaults, **values)

"""Chooses the LLM provider for a session.

Normal use: an OpenAIProvider built from the key the user typed this session.

Offline modes, selected only by the RFP_FAKE_LLM environment variable (never by the UI):
  RFP_FAKE_LLM=1       demo mode: no key needed, deterministic offline scorer
  RFP_FAKE_LLM=keyed   test hook: the key form is shown; keys starting with "sk-test-valid"
                       are accepted and everything else is rejected (used by browser tests)
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from core.tools.demo_responder import demo_responder
from core.tools.fake_provider import FakeProvider
from core.tools.llm_provider import LLMProvider, OpenAIProvider

DEMO_MODEL = "demo-model"
VALID_TEST_KEY_PREFIX = "sk-test-valid"


def _mode(env: Mapping[str, str] | None) -> str:
    source = os.environ if env is None else env
    return source.get("RFP_FAKE_LLM", "")


def demo_mode(env: Mapping[str, str] | None = None) -> bool:
    """True when the app runs without any API key (offline demo scorer)."""
    return _mode(env) == "1"


def make_provider(api_key: str, env: Mapping[str, str] | None = None) -> LLMProvider:
    mode = _mode(env)
    if mode == "1":
        return FakeProvider(demo_responder, models=[DEMO_MODEL])
    if mode == "keyed":
        return FakeProvider(demo_responder, models=["gpt-4o", "gpt-4o-mini", "whisper-1"],
                            valid_key=api_key.startswith(VALID_TEST_KEY_PREFIX))
    return OpenAIProvider(api_key)

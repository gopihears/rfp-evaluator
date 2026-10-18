"""Chooses the LLM provider for a session.

Normal use: an OpenAIProvider built from the key the user typed this session.
Demo mode (environment RFP_FAKE_LLM=1): a deterministic offline provider, for demos and tests.
The fake is never selected unless that variable is set explicitly.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from core.tools.demo_responder import demo_responder
from core.tools.fake_provider import FakeProvider
from core.tools.llm_provider import LLMProvider, OpenAIProvider

DEMO_MODEL = "demo-model"


def demo_mode(env: Mapping[str, str] | None = None) -> bool:
    source = os.environ if env is None else env
    return source.get("RFP_FAKE_LLM") == "1"


def make_provider(api_key: str, env: Mapping[str, str] | None = None) -> LLMProvider:
    if demo_mode(env):
        return FakeProvider(demo_responder, models=[DEMO_MODEL])
    return OpenAIProvider(api_key)

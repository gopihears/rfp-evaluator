from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import pytest

from core.orchestrator import RunOptions
from core.tools.demo_responder import demo_responder
from core.tools.fake_provider import FakeProvider
from core.tools.local_embeddings import (
    LOCAL_CHUNK_TOKENS,
    LOCAL_TOP_K_FACTOR,
    LocalEmbedder,
    LocalEmbeddingsUnavailable,
    WithLocalEmbeddings,
)
from core.tools.provider_factory import local_embeddings_requested, make_provider
from core.tools.recording import RecordingProvider
from tests.run_helpers import new_conn, run, uploads


class StubModel:
    """Stands in for SentenceTransformer: deterministic 4-d vectors, records its calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def encode(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
        self.calls.append((list(texts), kwargs))
        return [[float(len(t)), 1.0, 0.0, 0.5] for t in texts]


def test_embedder_uses_the_local_model_and_returns_plain_floats() -> None:
    stub = StubModel()
    vectors = LocalEmbedder(model=stub).embed(["abc", "de"])
    assert vectors == [[3.0, 1.0, 0.0, 0.5], [2.0, 1.0, 0.0, 0.5]]
    assert all(isinstance(x, float) for v in vectors for x in v)
    assert stub.calls[0][1]["normalize_embeddings"] is True


def test_missing_dependency_gives_an_actionable_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)  # simulate "not installed"
    with pytest.raises(LocalEmbeddingsUnavailable) as e:
        LocalEmbedder().embed(["x"])
    assert "requirements-local.txt" in str(e.value)


def test_model_name_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    fake_module = types.ModuleType("sentence_transformers")
    fake_module.SentenceTransformer = lambda name: seen.append(name) or StubModel()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)
    monkeypatch.setenv("RFP_LOCAL_EMBED_MODEL", "my/model")
    LocalEmbedder().embed(["x"])
    assert seen == ["my/model"]
    assert LocalEmbedder().model_name == "my/model"


def test_wrapper_sends_scoring_to_the_inner_provider_but_embeds_locally() -> None:
    inner = FakeProvider(demo_responder, models=["m1"])
    stub = StubModel()
    provider = WithLocalEmbeddings(inner, LocalEmbedder(model=stub))
    assert provider.validate_key() == ["m1"]
    assert inner.embed_calls == []
    provider.embed(["hello"])
    assert inner.embed_calls == [] and stub.calls  # OpenAI was not asked for embeddings
    assert provider.chunk_tokens == LOCAL_CHUNK_TOKENS
    assert provider.top_k_factor == LOCAL_TOP_K_FACTOR
    assert "MiniLM" in repr(provider)


def test_factory_wraps_only_when_requested() -> None:
    assert not local_embeddings_requested({})
    assert local_embeddings_requested({"RFP_EMBEDDINGS": " Local "})
    plain = make_provider("", {"RFP_FAKE_LLM": "1"})
    wrapped = make_provider("", {"RFP_FAKE_LLM": "1", "RFP_EMBEDDINGS": "local"})
    assert isinstance(plain, FakeProvider) and isinstance(wrapped, WithLocalEmbeddings)


def test_recording_wrapper_forwards_the_chunk_settings() -> None:
    provider = WithLocalEmbeddings(FakeProvider(), LocalEmbedder(model=StubModel()))
    recorder = RecordingProvider(provider, Path("unused.json"))
    assert recorder.chunk_tokens == LOCAL_CHUNK_TOKENS
    assert not hasattr(recorder, "no_such_setting")


def test_long_document_run_uses_small_passages_and_more_of_them(synthetic_dir: Path) -> None:
    stub = StubModel()
    inner = FakeProvider(demo_responder)
    provider = WithLocalEmbeddings(inner, LocalEmbedder(model=stub))
    conn = new_conn()
    _, local = run(
        conn,
        uploads(synthetic_dir, ["long_apex_80p.pdf"]),
        provider=provider,
        options=RunOptions(model="m", sensitivity=False),
    )
    assert local.status == "COMPLETED"
    assert inner.embed_calls == []  # no embedding requests went to the scoring provider
    chunk_texts = max(stub.calls, key=lambda c: len(c[0]))[0]
    assert len(chunk_texts) > 400  # ~150-token passages: many more than the ~136 default chunks
    plain = FakeProvider(demo_responder)
    _, default = run(
        new_conn(),
        uploads(synthetic_dir, ["long_apex_80p.pdf"]),
        provider=plain,
        options=RunOptions(model="m", sensitivity=False),
    )
    assert plain.embed_calls[1] < len(chunk_texts)
    pages_local = local.export["suppliers"][0]["scorecard"]["criteria"][0]["retrieved_pages"]
    assert pages_local and default.status == "COMPLETED"


def test_provider_factory_chooses_the_routed_provider() -> None:
    from core.tools.llm_provider import OpenAIProvider, OpenRouterProvider
    from core.tools.provider_factory import provider_kind

    assert provider_kind(None, {}) == "openai"
    assert provider_kind("OpenRouter", {}) == "openrouter"
    assert provider_kind(None, {"RFP_PROVIDER": "openrouter"}) == "openrouter"
    assert provider_kind("openai", {"RFP_PROVIDER": "openrouter"}) == "openai"  # explicit wins
    assert isinstance(
        make_provider("sk-or-v1-ABCDEFGHIJKL", {}, kind="openrouter"), OpenRouterProvider
    )
    plain = make_provider("sk-test-ABCDEFGHIJKL", {})
    assert isinstance(plain, OpenAIProvider) and not isinstance(plain, OpenRouterProvider)
    demo = make_provider("", {"RFP_FAKE_LLM": "1"}, kind="openrouter")
    assert isinstance(demo, FakeProvider)  # demo mode never touches a real provider

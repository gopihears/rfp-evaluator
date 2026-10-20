"""Optional local embeddings (sentence-transformers) for the long-document passage search.

Nothing is sent to OpenAI for the search step. Only the passage search uses this; scoring still
needs a chat model. Install the extra dependency with: pip install -r requirements-local.txt

all-MiniLM-L6-v2 reads only about the first 256 word-pieces of a text, so passages must be small
(about 150 tokens) for it; LOCAL_CHUNK_TOKENS and LOCAL_TOP_K_FACTOR adapt the retrieval.
"""

from __future__ import annotations

import os
from typing import Any

from core.tools.llm_provider import LLMProvider

DEFAULT_LOCAL_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
LOCAL_CHUNK_TOKENS = 150
LOCAL_TOP_K_FACTOR = 4  # four times as many (four times smaller) passages per criterion


class LocalEmbeddingsUnavailable(RuntimeError):
    """sentence-transformers is not installed."""


class LocalEmbedder:
    def __init__(self, model_name: str | None = None, *, model: Any | None = None) -> None:
        self.model_name = model_name or os.environ.get("RFP_LOCAL_EMBED_MODEL", DEFAULT_LOCAL_MODEL)
        self._model = model

    def _load(self) -> Any:
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise LocalEmbeddingsUnavailable(
                    "Local embeddings need sentence-transformers. "
                    "Run: pip install -r requirements-local.txt"
                ) from exc
            self._model = SentenceTransformer(self.model_name)
        return self._model

    def embed(self, texts: list[str], *, model: str = "") -> list[list[float]]:
        vectors = self._load().encode(
            texts, normalize_embeddings=True, show_progress_bar=False, batch_size=32
        )
        return [[float(x) for x in v] for v in vectors]


class WithLocalEmbeddings:
    """Delegates everything to `inner`, except embeddings, which are computed locally."""

    chunk_tokens = LOCAL_CHUNK_TOKENS
    top_k_factor = LOCAL_TOP_K_FACTOR

    def __init__(self, inner: LLMProvider, embedder: LocalEmbedder | None = None) -> None:
        self._inner = inner
        self._embedder = embedder or LocalEmbedder()

    def validate_key(self) -> list[str]:
        return self._inner.validate_key()

    def complete_json(self, **kwargs: Any) -> str:
        return self._inner.complete_json(**kwargs)

    def embed(self, texts: list[str], *, model: str = "") -> list[list[float]]:
        return self._embedder.embed(texts)

    def __repr__(self) -> str:
        return f"WithLocalEmbeddings({self._embedder.model_name})"

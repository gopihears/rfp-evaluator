"""LLM provider boundary. The only module that imports the OpenAI SDK.

The API key is held in a private attribute, never appears in repr/pickles/errors, and is
never written anywhere. All error text passes through scrub().
"""

from __future__ import annotations

import random
import re
import time
from collections.abc import Callable
from typing import Any, Protocol

import openai

_KEY_PATTERN = re.compile(r"(sk-[A-Za-z0-9_\-]{6,}|Bearer\s+[A-Za-z0-9._\-]{12,})")
_NON_CHAT = ("embedding", "whisper", "tts", "dall-e", "audio", "realtime", "transcribe",
             "moderation", "image", "davinci", "babbage", "search", "similarity", "instruct")
_CHAT_PREFIXES = ("gpt-", "chatgpt", "o1", "o3", "o4", "o5")


def scrub(text: object) -> str:
    """Mask anything that looks like an API key or bearer token."""
    return _KEY_PATTERN.sub("[redacted-key]", str(text))


def mask_key(key: str) -> str:
    """Display form for the UI chip, e.g. 'sk-...wxyz'. Never the full key."""
    return f"sk-…{key[-4:]}" if len(key) >= 8 else "…"


def is_chat_model(model_id: str) -> bool:
    lowered = model_id.lower()
    return lowered.startswith(_CHAT_PREFIXES) and not any(bad in lowered for bad in _NON_CHAT)


class ProviderError(Exception):
    code = "E_LLM"


class ProviderAuthError(ProviderError):
    code = "E_LLM_AUTH"


class ProviderRateLimitError(ProviderError):
    code = "E_LLM_RATE"


class ProviderTimeoutError(ProviderError):
    code = "E_LLM_TIMEOUT"


class ProviderRefusalError(ProviderError):
    code = "E_LLM_REFUSAL"


class LLMProvider(Protocol):
    def validate_key(self) -> list[str]:
        """Return available model ids; raise ProviderAuthError for a bad key."""
        ...

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        json_schema: dict[str, Any],
        model: str,
        temperature: float | None = 0.0,
    ) -> str:
        """Return the model's JSON text for the given schema."""
        ...

    def embed(
        self, texts: list[str], *, model: str = "text-embedding-3-small"
    ) -> list[list[float]]:
        """Return one embedding vector per text (used only for long-document retrieval)."""
        ...


EMBEDDING_MODEL = "text-embedding-3-small"
EMBED_BATCH = 96


class OpenAIProvider:
    def __init__(
        self,
        api_key: str,
        *,
        client: Any | None = None,
        max_retries: int = 3,
        timeout: float = 60.0,
        sleep: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
    ) -> None:
        if not api_key or not api_key.strip():
            raise ProviderAuthError("An API key is required.")
        self._api_key = api_key.strip()
        self._client = client or openai.OpenAI(api_key=self._api_key, timeout=timeout,
                                               max_retries=0)
        self._max_retries = max_retries
        self._sleep = sleep
        self._rng = rng or random.Random()

    def __repr__(self) -> str:
        return "OpenAIProvider(<redacted>)"

    def __reduce__(self) -> Any:
        raise TypeError("OpenAIProvider cannot be pickled (it holds a secret).")

    # ------------------------------------------------------------ internals

    def _backoff(self, attempt: int) -> None:
        self._sleep(min(1.0 * (2 ** attempt), 20.0) + self._rng.uniform(0, 0.5))

    def _call(self, fn: Callable[[], Any]) -> Any:
        """Run one API call with retry on rate limits, timeouts and 5xx."""
        last: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                return fn()
            except (openai.AuthenticationError, openai.PermissionDeniedError) as exc:
                raise ProviderAuthError(scrub("The API key was rejected by OpenAI.")) from exc
            except (openai.RateLimitError, openai.APITimeoutError, openai.APIConnectionError,
                    openai.InternalServerError) as exc:
                last = exc
                if attempt < self._max_retries:
                    self._backoff(attempt)
                    continue
            except openai.BadRequestError as exc:
                raise ProviderError(scrub(f"OpenAI rejected the request: {exc}")) from exc
            except openai.APIError as exc:
                raise ProviderError(scrub(f"OpenAI error: {exc}")) from exc
        if isinstance(last, openai.RateLimitError):
            raise ProviderRateLimitError("OpenAI rate limit reached; please retry shortly.")
        if isinstance(last, (openai.APITimeoutError, openai.APIConnectionError)):
            raise ProviderTimeoutError("Could not reach OpenAI in time.")
        raise ProviderError(scrub(f"OpenAI service error: {last}"))

    # ------------------------------------------------------------- interface

    def validate_key(self) -> list[str]:
        page = self._call(lambda: self._client.models.list())
        ids = sorted({m.id for m in page})
        return ids

    def embed(self, texts: list[str], *, model: str = EMBEDDING_MODEL) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), EMBED_BATCH):
            batch = texts[start : start + EMBED_BATCH]

            def request(items: list[str] = batch) -> Any:
                return self._client.embeddings.create(model=model, input=items)

            response = self._call(request)
            vectors.extend([list(item.embedding) for item in response.data])
        return vectors

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        json_schema: dict[str, Any],
        model: str,
        temperature: float | None = 0.0,
    ) -> str:
        def request(with_temperature: bool) -> Any:
            kwargs: dict[str, Any] = {
                "model": model,
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": user}],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": "rfp_scorecard", "strict": True,
                                    "schema": json_schema},
                },
            }
            if with_temperature and temperature is not None:
                kwargs["temperature"] = temperature
            return self._call(lambda: self._client.chat.completions.create(**kwargs))

        try:
            response = request(True)
        except ProviderError as exc:
            # Some models (e.g. reasoning models) only accept the default temperature.
            if "temperature" in str(exc).lower():
                response = request(False)
            else:
                raise
        message = response.choices[0].message
        content = getattr(message, "content", None)
        if getattr(message, "refusal", None) or not content:
            raise ProviderRefusalError("The model declined or returned an empty answer.")
        return str(content)

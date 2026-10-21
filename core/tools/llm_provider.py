"""LLM provider boundary. The only module that imports the OpenAI SDK.

The API key is held in a private attribute, never appears in repr/pickles/errors, and is
never written anywhere. All error text passes through scrub().
"""

from __future__ import annotations

import json
import random
import re
import time
from collections.abc import Callable
from typing import Any, Protocol

import openai

_KEY_PATTERN = re.compile(r"(sk-[A-Za-z0-9_\-]{6,}|Bearer\s+[A-Za-z0-9._\-]{12,})")
_NON_CHAT = (
    "embedding",
    "whisper",
    "tts",
    "dall-e",
    "audio",
    "realtime",
    "transcribe",
    "moderation",
    "image",
    "davinci",
    "babbage",
    "search",
    "similarity",
    "instruct",
)
_CHAT_PREFIXES = ("gpt-", "chatgpt", "o1", "o3", "o4", "o5")


def scrub(text: object) -> str:
    """Mask anything that looks like an API key or bearer token."""
    return _KEY_PATTERN.sub("[redacted-key]", str(text))


def mask_key(key: str) -> str:
    """Display form for the UI chip, e.g. 'sk-...wxyz'. Never the full key."""
    return f"sk-…{key[-4:]}" if len(key) >= 8 else "…"


_NON_CHAT_ROUTED = ("embedding", "whisper", "tts", "dall-e", "audio", "transcribe",
                    "moderation", "image", "rerank")


def is_chat_model(model_id: str, *, routed: bool = False) -> bool:
    """Heuristic filter for the model dropdown.

    OpenAI ids start with a known chat prefix (gpt-, o3 ...). A router such as OpenRouter lists
    ids like 'vendor/model' (and real chat models are often called '...-instruct'), so there only
    the clearly-not-chat kinds (embeddings, audio, images, moderation) are filtered out.
    """
    lowered = model_id.lower()
    if routed:
        return not any(bad in lowered for bad in _NON_CHAT_ROUTED)
    return lowered.startswith(_CHAT_PREFIXES) and not any(bad in lowered for bad in _NON_CHAT)


class ProviderError(Exception):
    code = "E_LLM"


class ProviderAuthError(ProviderError):
    code = "E_LLM_AUTH"


class ProviderRateLimitError(ProviderError):
    code = "E_LLM_RATE"


class ProviderQuotaError(ProviderError):
    code = "E_LLM_QUOTA"


def _is_quota_error(exc: Exception) -> bool:
    """OpenAI uses the same 429 for 'too fast' and 'no credit'; the error code tells them apart."""
    body = getattr(exc, "body", None)
    code = getattr(exc, "code", None) or (body.get("code") if isinstance(body, dict) else None)
    return code == "insufficient_quota"


def _retry_after(exc: Exception) -> float | None:
    try:
        return float(exc.response.headers["retry-after"])  # type: ignore[attr-defined]
    except (AttributeError, KeyError, ValueError, TypeError):
        return None


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


OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
_UNSUPPORTED_HINTS = (
    "response_format",
    "json_schema",
    "structured",
    "require_parameters",
    "no endpoints",
    "not support",
    "unsupported",
)


class OpenAIProvider:
    """Chat provider for OpenAI and any OpenAI-compatible endpoint (see OpenRouterProvider)."""

    label = "OpenAI"
    json_fallback = False  # retry in plain JSON mode when strict schemas are not supported
    require_parameters = False  # router option: only use endpoints that honour every parameter

    def __init__(
        self,
        api_key: str,
        *,
        client: Any | None = None,
        max_retries: int = 3,
        timeout: float = 60.0,
        sleep: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
        base_url: str | None = None,
        default_headers: dict[str, str] | None = None,
    ) -> None:
        if not api_key or not api_key.strip():
            raise ProviderAuthError("An API key is required.")
        self._api_key = api_key.strip()
        self._client = client or openai.OpenAI(
            api_key=self._api_key,
            timeout=timeout,
            max_retries=0,
            base_url=base_url,
            default_headers=default_headers,
        )
        self._max_retries = max_retries
        self._sleep = sleep
        self._rng = rng or random.Random()

    def __repr__(self) -> str:
        return f"{type(self).__name__}(<redacted>)"

    def __reduce__(self) -> Any:
        raise TypeError(f"{type(self).__name__} cannot be pickled (it holds a secret).")

    # ------------------------------------------------------------ internals

    def _backoff(self, attempt: int, base: float = 1.0, retry_after: float | None = None) -> None:
        delay = min(base * (2**attempt), 60.0)
        if retry_after is not None:  # the server said how long to wait
            delay = max(delay, min(retry_after, 60.0))
        self._sleep(delay + self._rng.uniform(0, 0.5))

    def _call(self, fn: Callable[[], Any]) -> Any:
        """Run one API call with retry on rate limits, timeouts and 5xx."""
        last: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                return fn()
            except (openai.AuthenticationError, openai.PermissionDeniedError) as exc:
                raise ProviderAuthError(
                    scrub(f"The API key was rejected by {self.label}.")
                ) from exc
            except openai.RateLimitError as exc:
                if _is_quota_error(exc):  # retrying cannot help: there is no credit left
                    raise ProviderQuotaError(
                        f"{self.label} reports no remaining quota or credit for this key. "
                        "Add credit or raise the spend limit in your billing page, then retry."
                    ) from exc
                last = exc
                if attempt < self._max_retries:
                    # Per-minute limits need a longer pause than a network blip does.
                    self._backoff(attempt, base=5.0, retry_after=_retry_after(exc))
                    continue
            except (
                openai.APITimeoutError,
                openai.APIConnectionError,
                openai.InternalServerError,
            ) as exc:
                last = exc
                if attempt < self._max_retries:
                    self._backoff(attempt)
                    continue
            except openai.BadRequestError as exc:
                raise ProviderError(scrub(f"{self.label} rejected the request: {exc}")) from exc
            except openai.APIError as exc:
                raise ProviderError(scrub(f"{self.label} error: {exc}")) from exc
        if isinstance(last, openai.RateLimitError):
            raise ProviderRateLimitError(
                f"{self.label} rate limit reached (too many requests or tokens per minute for this "
                "account). Retry in a minute, or score fewer suppliers at once (RFP_CONCURRENCY=1)."
            )
        if isinstance(last, (openai.APITimeoutError, openai.APIConnectionError)):
            raise ProviderTimeoutError(f"Could not reach {self.label} in time.")
        raise ProviderError(scrub(f"{self.label} service error: {last}"))

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
        def request(mode: str, with_temperature: bool) -> Any:
            instructions = system
            if mode == "schema":
                response_format: dict[str, Any] = {
                    "type": "json_schema",
                    "json_schema": {"name": "rfp_scorecard", "strict": True, "schema": json_schema},
                }
            else:  # plain JSON mode: the schema travels in the prompt instead
                response_format = {"type": "json_object"}
                instructions += (
                    "\n\nReturn one JSON object that matches this JSON Schema "
                    "exactly:\n" + json.dumps(json_schema)
                )
            kwargs: dict[str, Any] = {
                "model": model,
                "messages": [
                    {"role": "system", "content": instructions},
                    {"role": "user", "content": user},
                ],
                "response_format": response_format,
            }
            if self.require_parameters and mode == "schema":
                kwargs["extra_body"] = {"provider": {"require_parameters": True}}
            if with_temperature and temperature is not None:
                kwargs["temperature"] = temperature
            return self._call(lambda: self._client.chat.completions.create(**kwargs))

        def attempt(mode: str) -> Any:
            try:
                return request(mode, True)
            except ProviderError as exc:
                # Some models (e.g. reasoning models) only accept the default temperature.
                if "temperature" in str(exc).lower():
                    return request(mode, False)
                raise

        try:
            response = attempt("schema")
        except ProviderError as exc:
            hinted = any(h in str(exc).lower() for h in _UNSUPPORTED_HINTS)
            if not (self.json_fallback and hinted):
                raise
            response = attempt("json")
        message = response.choices[0].message
        content = getattr(message, "content", None)
        if getattr(message, "refusal", None) or not content:
            raise ProviderRefusalError("The model declined or returned an empty answer.")
        return str(content)


class OpenRouterProvider(OpenAIProvider):
    """OpenRouter (https://openrouter.ai): one key for many models, OpenAI-compatible API.

    Not every routed model supports strict JSON schemas, so this falls back to plain JSON mode
    (the validation tool and the repair attempt cope with imperfect output). OpenRouter has no
    embeddings endpoint here, so passage search uses keyword search or RFP_EMBEDDINGS=local.
    """

    label = "OpenRouter"
    json_fallback = True
    require_parameters = True

    def __init__(self, api_key: str, **kwargs: Any) -> None:
        kwargs.setdefault("base_url", OPENROUTER_BASE_URL)
        kwargs.setdefault(
            "default_headers",
            {
                "HTTP-Referer": "https://github.com/gopihears/rfp-evaluator",
                "X-Title": "RFP Evaluator",
            },
        )
        super().__init__(api_key, **kwargs)

    def embed(self, texts: list[str], *, model: str = EMBEDDING_MODEL) -> list[list[float]]:
        raise ProviderError(
            "OpenRouter has no embeddings endpoint here; passage search will use keywords "
            "(or set RFP_EMBEDDINGS=local)."
        )

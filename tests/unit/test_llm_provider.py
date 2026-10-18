from __future__ import annotations

import pickle
import random
from types import SimpleNamespace
from typing import Any

import openai
import pytest

try:  # newer openai SDKs ship on httpx2
    import httpx2 as httpx
except ImportError:  # pragma: no cover
    import httpx  # type: ignore[no-redef]

from core.tools.fake_provider import FakeProvider
from core.tools.llm_provider import (
    OpenAIProvider,
    ProviderAuthError,
    ProviderError,
    ProviderRateLimitError,
    ProviderRefusalError,
    ProviderTimeoutError,
    is_chat_model,
    mask_key,
    scrub,
)

KEY = "sk-test-ABCDEFGHIJKLMNOP1234"
REQ = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")


def http_error(cls: type[openai.APIStatusError], status: int, msg: str) -> openai.APIStatusError:
    return cls(msg, response=httpx.Response(status, request=REQ), body=None)


def completion(content: str | None, refusal: str | None = None) -> Any:
    message = SimpleNamespace(content=content, refusal=refusal)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class FakeClient:
    """Minimal stand-in for openai.OpenAI that plays back scripted results."""

    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.requests: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self.models = SimpleNamespace(list=self._list)

    def _next(self) -> Any:
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def _create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        return self._next()

    def _list(self) -> Any:
        return self._next()


def provider(script: list[Any], **kw: Any) -> tuple[OpenAIProvider, FakeClient, list[float]]:
    client = FakeClient(script)
    sleeps: list[float] = []
    p = OpenAIProvider(KEY, client=client, sleep=sleeps.append, rng=random.Random(1), **kw)
    return p, client, sleeps


def call(p: OpenAIProvider, **kw: Any) -> str:
    return p.complete_json(system="s", user="u", json_schema={"type": "object"},
                           model="gpt-test", **kw)


def test_p01_scrub_masks_keys() -> None:
    text = f"Incorrect API key provided: {KEY}. Authorization: Bearer abcdefghijklmnop123456"
    out = scrub(text)
    assert KEY not in out and "abcdefghijklmnop" not in out and "[redacted-key]" in out
    assert scrub(ValueError(f"boom {KEY}")).count("sk-") == 0


def test_p02_repr_and_pickle_never_expose_key() -> None:
    p, _, _ = provider([])
    assert KEY not in repr(p) and KEY not in str(p)
    with pytest.raises(TypeError):
        pickle.dumps(p)
    assert KEY not in mask_key(KEY) and mask_key(KEY).endswith("1234")


def test_empty_key_rejected() -> None:
    with pytest.raises(ProviderAuthError):
        OpenAIProvider("   ")


def test_p03_rate_limit_then_success_backs_off() -> None:
    p, client, sleeps = provider([http_error(openai.RateLimitError, 429, "slow down"),
                                  completion('{"ok": true}')])
    assert call(p) == '{"ok": true}'
    assert len(client.requests) == 2 and len(sleeps) == 1 and sleeps[0] >= 1.0


def test_rate_limit_exhausted() -> None:
    p, client, sleeps = provider([http_error(openai.RateLimitError, 429, "x")] * 4)
    with pytest.raises(ProviderRateLimitError):
        call(p)
    assert len(client.requests) == 4 and len(sleeps) == 3
    assert sleeps[1] > sleeps[0]  # exponential


def test_server_error_is_retried() -> None:
    p, client, _ = provider([http_error(openai.InternalServerError, 500, "oops"),
                             completion("{}")])
    assert call(p) == "{}" and len(client.requests) == 2


def test_timeout_maps_to_timeout_error() -> None:
    p, _, _ = provider([openai.APITimeoutError(request=REQ)] * 4)
    with pytest.raises(ProviderTimeoutError):
        call(p)


def test_p04_auth_error_not_retried_and_key_not_leaked() -> None:
    p, client, sleeps = provider(
        [http_error(openai.AuthenticationError, 401, f"Incorrect API key: {KEY}")])
    with pytest.raises(ProviderAuthError) as e:
        call(p)
    assert len(client.requests) == 1 and not sleeps
    assert KEY not in str(e.value) and e.value.code == "E_LLM_AUTH"


def test_bad_request_message_is_scrubbed() -> None:
    p, _, _ = provider([http_error(openai.BadRequestError, 400, f"bad {KEY}")])
    with pytest.raises(ProviderError) as e:
        call(p)
    assert KEY not in str(e.value)


def test_temperature_rejected_retries_without_it() -> None:
    p, client, _ = provider([
        http_error(openai.BadRequestError, 400,
                   "Unsupported value: 'temperature' does not support 0.0"),
        completion('{"a": 1}')])
    assert call(p, temperature=0.0) == '{"a": 1}'
    assert "temperature" in client.requests[0] and "temperature" not in client.requests[1]


def test_request_shape_uses_strict_json_schema() -> None:
    p, client, _ = provider([completion("{}")])
    call(p)
    req = client.requests[0]
    assert req["model"] == "gpt-test" and req["temperature"] == 0.0
    assert [m["role"] for m in req["messages"]] == ["system", "user"]
    fmt = req["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True


@pytest.mark.parametrize("message", [completion(None), completion(""),
                                     completion("{}", refusal="I cannot help with that")])
def test_refusal_or_empty_raises(message: Any) -> None:
    p, _, _ = provider([message])
    with pytest.raises(ProviderRefusalError):
        call(p)


def test_validate_key_returns_sorted_unique_ids() -> None:
    page = [SimpleNamespace(id="gpt-b"), SimpleNamespace(id="gpt-a"), SimpleNamespace(id="gpt-a")]
    p, _, _ = provider([page])
    assert p.validate_key() == ["gpt-a", "gpt-b"]


def test_validate_key_bad_key() -> None:
    p, _, _ = provider([http_error(openai.AuthenticationError, 401, "nope")])
    with pytest.raises(ProviderAuthError):
        p.validate_key()


@pytest.mark.parametrize(("model", "expected"), [
    ("gpt-4o", True), ("gpt-4.1-mini", True), ("o3-mini", True), ("chatgpt-4o-latest", True),
    ("text-embedding-3-small", False), ("whisper-1", False), ("tts-1", False),
    ("dall-e-3", False), ("gpt-4o-realtime-preview", False), ("gpt-4o-audio-preview", False),
    ("omni-moderation-latest", False), ("gpt-image-1", False),
])
def test_chat_model_filter(model: str, expected: bool) -> None:
    assert is_chat_model(model) is expected


def test_fake_provider_records_calls_and_raises_errors() -> None:
    from core.tools.llm_provider import ProviderRateLimitError as RL

    fake = FakeProvider(lambda s, u, sch: RL("busy"))
    with pytest.raises(RL):
        fake.complete_json(system="s", user="u", json_schema={}, model="m")
    assert len(fake.calls) == 1 and fake.calls[0].model == "m"
    assert FakeProvider(valid_key=False, models=["a"]).validate_key.__name__
    with pytest.raises(ProviderAuthError):
        FakeProvider(valid_key=False).validate_key()
    assert FakeProvider(models=["a", "b"]).validate_key() == ["a", "b"]

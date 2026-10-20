"""Record and replay LLM answers so a live evaluation run can be repeated offline for free.

RecordingProvider wraps a real provider and saves every answer keyed by a hash of the request.
ReplayProvider answers from that file and fails loudly on a request it has never seen.
The file contains model answers about the (fictional) proposals only; never the API key.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from core.tools.llm_provider import LLMProvider


def request_key(system: str, user: str, schema: dict[str, Any], model: str) -> str:
    payload = json.dumps([system, user, schema, model], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class RecordingProvider:
    def __init__(self, inner: LLMProvider, path: Path) -> None:
        self._inner, self._path = inner, path
        self.records: dict[str, str] = {}

    def __getattr__(self, name: str) -> Any:
        """Forward optional settings such as chunk_tokens to the wrapped provider."""
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._inner, name)

    def validate_key(self) -> list[str]:
        return self._inner.validate_key()

    def embed(
        self, texts: list[str], *, model: str = "text-embedding-3-small"
    ) -> list[list[float]]:
        return self._inner.embed(texts, model=model)

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        json_schema: dict[str, Any],
        model: str,
        temperature: float | None = 0.0,
    ) -> str:
        answer = self._inner.complete_json(
            system=system, user=user, json_schema=json_schema, model=model, temperature=temperature
        )
        self.records[request_key(system, user, json_schema, model)] = answer
        return answer

    def save(self) -> None:
        self._path.write_text(json.dumps(self.records, indent=1, sort_keys=True), encoding="utf-8")


class ReplayError(KeyError):
    """The replay file has no answer for this request (the prompt or schema changed)."""


class ReplayProvider:
    def __init__(self, path: Path) -> None:
        self.records: dict[str, str] = json.loads(path.read_text(encoding="utf-8"))

    def validate_key(self) -> list[str]:
        return ["replay"]

    def embed(self, texts: list[str], *, model: str = "x") -> list[list[float]]:
        from core.tools.fake_provider import FakeProvider

        return FakeProvider().embed(texts)

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        json_schema: dict[str, Any],
        model: str,
        temperature: float | None = 0.0,
    ) -> str:
        key = request_key(system, user, json_schema, model)
        if key not in self.records:
            raise ReplayError("No recorded answer for this request; re-record with a live run.")
        return self.records[key]

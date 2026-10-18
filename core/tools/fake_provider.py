"""Deterministic stand-in for the LLM, used by tests, CI and the offline demo mode.

A responder maps (system, user, schema) to the JSON text the 'model' should return. It may
also raise a ProviderError to simulate API failures.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

Response = str | dict[str, Any] | BaseException
Responder = Callable[[str, str, dict[str, Any]], Response]


@dataclass
class Call:
    system: str
    user: str
    schema: dict[str, Any]
    model: str
    temperature: float | None


def criterion_ids_from_schema(schema: Mapping[str, Any]) -> list[int]:
    items = schema["properties"]["criteria"]["items"]
    return [int(i) for i in items["properties"]["criterion_id"]["enum"]]


def canned_response(
    schema: Mapping[str, Any],
    scores: Sequence[int | str | None] | Mapping[int, Any],
    *,
    evidence: str = "",
    justification: str = "Reasoned from the document.",
    risks: Sequence[str] = ("Delivery risk",),
    summary: str = "Summary.",
) -> dict[str, Any]:
    ids = criterion_ids_from_schema(schema)
    values = dict(scores) if isinstance(scores, Mapping) else dict(zip(ids, scores, strict=True))
    return {
        "criteria": [
            {"criterion_id": cid, "evidence": evidence, "justification": justification,
             "score": values[cid], "addressed": True, "confidence": "medium"}
            for cid in ids
        ],
        "risks": list(risks),
        "overall_summary": summary,
    }


class FakeProvider:
    def __init__(
        self,
        responder: Responder | None = None,
        *,
        models: Sequence[str] = ("gpt-fake-1",),
        valid_key: bool = True,
    ) -> None:
        self._responder = responder
        self._models = list(models)
        self._valid_key = valid_key
        self.calls: list[Call] = []
        self.embed_calls: list[int] = []  # number of texts per embed() call

    def validate_key(self) -> list[str]:
        from core.tools.llm_provider import ProviderAuthError

        if not self._valid_key:
            raise ProviderAuthError("The API key was rejected by OpenAI.")
        return list(self._models)

    def embed(self, texts: list[str], *, model: str = "fake-embedding") -> list[list[float]]:
        """Hashed bag-of-words vectors: deterministic, lexical rather than semantic."""
        import hashlib
        import math

        self.embed_calls.append(len(texts))
        out = []
        for text in texts:
            vec = [0.0] * 128
            for word in re.findall(r"[a-z0-9]+", text.lower()):
                vec[int(hashlib.md5(word.encode()).hexdigest(), 16) % 128] += 1.0
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            out.append([v / norm for v in vec])
        return out

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        json_schema: dict[str, Any],
        model: str,
        temperature: float | None = 0.0,
    ) -> str:
        self.calls.append(Call(system, user, json_schema, model, temperature))
        if self._responder is None:
            raise AssertionError("FakeProvider has no responder configured")
        result = self._responder(system, user, json_schema)
        if isinstance(result, BaseException):
            raise result
        return result if isinstance(result, str) else json.dumps(result)


def by_document_marker(table: Mapping[str, Sequence[Response]]) -> Responder:
    """Route by a marker string found in the prompt; each match consumes its next response.

    The last response for a marker repeats, so a single-element list answers every call.
    """
    queues = {marker: list(items) for marker, items in table.items()}

    def respond(system: str, user: str, schema: dict[str, Any]) -> Response:
        for marker, queue in queues.items():
            if re.search(re.escape(marker), user):
                return queue.pop(0) if len(queue) > 1 else queue[0]
        raise AssertionError("No canned response matches this prompt")

    return respond

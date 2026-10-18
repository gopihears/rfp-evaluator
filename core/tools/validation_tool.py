"""Validation tool: turns raw LLM output into a normalised Scorecard.

Pure and deterministic: never raises on arbitrary input, always returns exactly one
result per active criterion, and records every correction as a warning.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from core.models import (
    Criterion,
    CriterionResult,
    ResultStatus,
    RunWarning,
    Scorecard,
    SupplierInput,
)

MAX_TEXT_CHARS = 4000
EVIDENCE_MATCH_THRESHOLD = 0.85
_HUGE = Decimal(1_000_000)

_FENCE_START = re.compile(r"^\s*```[a-zA-Z]*\s*")
_FENCE_END = re.compile(r"\s*```\s*$")
_PAGE_MARK = re.compile(r"\[p\.\s*\d+\]", re.IGNORECASE)
_HYPHEN_BREAK = re.compile(r"-\s*\n\s*")
_WORD = re.compile(r"[^\W_]+")

_INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(?:all\s+|any\s+)?(?:previous|prior|above|earlier)\s+"
               r"(?:instructions?|prompts?|rules?)", re.IGNORECASE),
    re.compile(r"disregard\s+(?:the\s+|all\s+)?(?:above|previous|prior|system)", re.IGNORECASE),
    re.compile(r"(?:system|developer)\s+prompt", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+(?:a|an|the)\b", re.IGNORECASE),
    re.compile(r"(?:give|assign|award|score)\s+(?:this|us|all|every|each)[^.\n]{0,60}"
               r"(?:10\s*/\s*10|maximum|full\s+(?:marks|score)|highest|perfect)", re.IGNORECASE),
]


# ------------------------------------------------------------------ parsing


def _parse(raw: str | dict[str, Any] | Any) -> dict[str, Any] | None:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return None
    text = _FENCE_END.sub("", _FENCE_START.sub("", raw.strip(), count=1))
    try:
        data = json.loads(text)
    except (ValueError, RecursionError):
        return None
    return data if isinstance(data, dict) else None


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value) and value == int(value):
        return int(value)
    if isinstance(value, str) and re.fullmatch(r"\s*-?\d+\s*", value):
        return int(value)
    return None


# ------------------------------------------------------------------ scores


def coerce_score(value: Any, max_score: int) -> tuple[int | None, ResultStatus, list[str]]:
    """Return (score, status, info/warn codes) for one raw score value."""
    codes: list[str] = []
    if isinstance(value, bool) or value is None:
        return None, "malformed", ["W_MALFORMED_SCORE"]
    try:
        if isinstance(value, int):
            number = Decimal(value)
        elif isinstance(value, float):
            if not math.isfinite(value):
                return None, "malformed", ["W_MALFORMED_SCORE"]
            number = Decimal(repr(value))
        elif isinstance(value, str):
            number = Decimal(value.strip())
            codes.append("I_COERCED")
        else:
            return None, "malformed", ["W_MALFORMED_SCORE"]
    except (InvalidOperation, ValueError):
        return None, "malformed", ["W_MALFORMED_SCORE"]
    if not number.is_finite():
        return None, "malformed", ["W_MALFORMED_SCORE"]
    if abs(number) > _HUGE:  # far outside any score range; rounding would overflow Decimal
        return (0 if number < 0 else max_score), "clipped", [*codes, "W_CLIPPED"]
    rounded = number.quantize(Decimal(1), rounding=ROUND_HALF_UP)
    if rounded != number:
        codes.append("I_ROUNDED")
    score = int(rounded)
    if score < 0 or score > max_score:
        codes.append("W_CLIPPED")
        return min(max(score, 0), max_score), "clipped", codes
    return score, "ok", codes


# ---------------------------------------------------------------- evidence


def _words(text: str) -> list[str]:
    cleaned = _PAGE_MARK.sub(" ", _HYPHEN_BREAK.sub("", text))
    return _WORD.findall(cleaned.lower())


def _shingles(words: Sequence[str], n: int = 3) -> set[tuple[str, ...]]:
    return {tuple(words[i : i + n]) for i in range(len(words) - n + 1)}


class _SourceIndex:
    """Precomputed lookups so many evidence quotes can be checked cheaply."""

    def __init__(self, source_text: str) -> None:
        self.words = _words(source_text)
        self.shingles = _shingles(self.words)
        self.joined = " " + " ".join(self.words) + " "

    def supports(self, evidence: str) -> bool:
        ev = _words(evidence)
        if not ev:
            return False
        if len(ev) < 3:
            return " " + " ".join(ev) + " " in self.joined
        grams = _shingles(ev)
        hits = sum(1 for g in grams if g in self.shingles)
        return hits / len(grams) >= EVIDENCE_MATCH_THRESHOLD


# ------------------------------------------------------------------- helpers


def _clean_text(value: Any) -> tuple[str, bool]:
    """Coerce to string, trim, and cap length. Returns (text, truncated)."""
    if isinstance(value, str):
        text = value.strip()
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        text = str(value)
    else:
        text = ""
    if len(text) > MAX_TEXT_CHARS:
        return text[:MAX_TEXT_CHARS] + " …[truncated]", True
    return text, False


def _coerce_risks(value: Any) -> tuple[list[str], bool]:
    if isinstance(value, list) and all(isinstance(x, str) for x in value):
        return [x.strip() for x in value if x.strip()], False
    if isinstance(value, str):
        return ([value.strip()] if value.strip() else []), True
    if isinstance(value, list):
        out = [str(x).strip() for x in value if isinstance(x, (str, int, float))
               and not isinstance(x, bool) and str(x).strip()]
        return out, True
    return [], value is not None


def _missing_result(c: Criterion, why: str) -> CriterionResult:
    return CriterionResult(
        criterion_id=c.criterion_id,
        score=None,
        max_score=c.max_score,
        justification=why,
        evidence="",
        status="missing",
    )


def _empty_card(
    supplier: SupplierInput, active: Sequence[Criterion], warnings: list[RunWarning], why: str
) -> Scorecard:
    return Scorecard(
        supplier_id=supplier.supplier_id,
        supplier_name=supplier.supplier_name,
        criteria=[_missing_result(c, why) for c in active],
        warnings=warnings,
        ok=False,
    )


# --------------------------------------------------------------------- main


def validate(
    raw: str | dict[str, Any] | Any,
    criteria: Sequence[Criterion],
    supplier: SupplierInput,
    source_text: str = "",
) -> Scorecard:
    """Normalise one raw LLM response into a Scorecard for the active criteria."""
    sid = supplier.supplier_id
    active = sorted((c for c in criteria if c.is_active), key=lambda c: c.criterion_id)
    warnings: list[RunWarning] = []

    def warn(code: str, message: str, criterion_id: int | None = None) -> None:
        warnings.append(
            RunWarning.make(code, message, supplier_id=sid, criterion_id=criterion_id)
        )

    data = _parse(raw)
    if data is None:
        warn("E_JSON_PARSE", "Model output was not a valid JSON object.")
        return _empty_card(supplier, active, warnings, "Model output could not be parsed.")

    entries_raw = data.get("criteria")
    if not isinstance(entries_raw, list):
        warn("W_NO_CRITERIA", "Model output has no 'criteria' list; all criteria marked missing.")
        entries_raw = []

    valid_ids = {c.criterion_id for c in active}
    by_id: dict[int, dict[str, Any]] = {}
    for entry in entries_raw:
        if not isinstance(entry, dict):
            warn("W_MALFORMED_ENTRY", "A criteria entry was not an object and was dropped.")
            continue
        cid = _as_int(entry.get("criterion_id"))
        if cid is None or cid not in valid_ids:
            warn("W_UNKNOWN_CRITERION",
                 f"Dropped result for unknown criterion_id {entry.get('criterion_id')!r}.")
            continue
        if cid in by_id:
            warn("W_DUPLICATE", f"Duplicate result for criterion {cid}; first one kept.", cid)
            continue
        by_id[cid] = entry

    index = _SourceIndex(source_text) if source_text.strip() else None
    results: list[CriterionResult] = []
    for c in active:
        entry = by_id.get(c.criterion_id)
        if entry is None:
            warn("W_MISSING_CRITERION", f"No result returned for '{c.name}'.", c.criterion_id)
            results.append(_missing_result(c, "Not returned by model."))
            continue

        score, status, codes = coerce_score(entry.get("score"), c.max_score)
        messages = {
            "W_MALFORMED_SCORE": f"Score for '{c.name}' was not a usable number.",
            "I_COERCED": f"Score for '{c.name}' was text and was converted to a number.",
            "I_ROUNDED": f"Score for '{c.name}' was not an integer and was rounded.",
            "W_CLIPPED": f"Score for '{c.name}' was outside 0-{c.max_score} and was clipped.",
        }
        for code in codes:
            warn(code, messages[code], c.criterion_id)

        reported_max = _as_int(entry.get("max_score"))
        if reported_max is not None and reported_max != c.max_score:
            warn("I_MAX_OVERRIDDEN",
                 f"Model reported max_score {reported_max}; configured {c.max_score} was used.",
                 c.criterion_id)

        justification, cut_j = _clean_text(entry.get("justification"))
        evidence, cut_e = _clean_text(entry.get("evidence"))
        if cut_j or cut_e:
            warn("I_TRUNCATED", f"Text for '{c.name}' was truncated.", c.criterion_id)
        if not justification or not evidence:
            warn("W_NO_EVIDENCE", f"'{c.name}' is missing justification or evidence.",
                 c.criterion_id)

        verified: bool | None = None
        if evidence and index is not None:
            verified = index.supports(evidence)
            if not verified:
                warn("W_UNVERIFIED_EVIDENCE",
                     f"Evidence for '{c.name}' was not found in the supplier document.",
                     c.criterion_id)

        addressed = entry.get("addressed")
        confidence = entry.get("confidence")
        results.append(
            CriterionResult(
                criterion_id=c.criterion_id,
                score=score,
                max_score=c.max_score,
                justification=justification,
                evidence=evidence,
                status=status,
                evidence_verified=verified,
                addressed=addressed if isinstance(addressed, bool) else None,
                confidence=confidence if confidence in ("low", "medium", "high") else None,
            )
        )

    valid_scores = [r.score for r in results if r.score is not None]
    if len(valid_scores) >= 3 and len(set(valid_scores)) == 1:
        warn("W_OUTLIER_PATTERN", f"Every criterion received the same score ({valid_scores[0]}).")

    if any(p.search(source_text) for p in _INJECTION_PATTERNS):
        warn("W_INJECTION_SUSPECTED",
             "The supplier document contains text that looks like instructions to an AI.")

    risks, risks_changed = _coerce_risks(data.get("risks"))
    if risks_changed:
        warn("I_RISKS_COERCED", "The 'risks' field was not a list of strings and was adjusted.")
    summary, _ = _clean_text(data.get("overall_summary"))

    return Scorecard(
        supplier_id=sid,
        supplier_name=supplier.supplier_name,
        criteria=results,
        risks=risks,
        overall_summary=summary,
        warnings=warnings,
        ok=True,
    )

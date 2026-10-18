"""Domain models shared across the core engine (no UI or I/O imports)."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Severity = Literal["info", "warn", "error"]
ResultStatus = Literal["ok", "missing", "clipped", "malformed"]
SupplierStatus = Literal["RANKED", "FAILED", "DISQUALIFIED"]
CriterionType = Literal["llm_judged", "formula"]


def severity_for(code: str) -> Severity:
    """Severity is encoded in the code prefix: I_ info, W_ warn, E_ error."""
    prefix = code[:1]
    if prefix == "I":
        return "info"
    if prefix == "E":
        return "error"
    return "warn"


class RunWarning(BaseModel):
    code: str
    severity: Severity
    message: str
    supplier_id: str | None = None
    criterion_id: int | None = None

    @classmethod
    def make(
        cls,
        code: str,
        message: str,
        *,
        supplier_id: str | None = None,
        criterion_id: int | None = None,
    ) -> RunWarning:
        return cls(
            code=code,
            severity=severity_for(code),
            message=message,
            supplier_id=supplier_id,
            criterion_id=criterion_id,
        )


class Criterion(BaseModel):
    criterion_id: int
    name: str = Field(min_length=1)
    description: str = ""
    weight: float = Field(ge=0)
    max_score: int = Field(default=10, ge=1)
    is_active: bool = True
    rubric: dict[int, str] | None = None
    is_mandatory: bool = False
    min_score: int | None = None
    criterion_type: CriterionType = "llm_judged"
    formula_kind: str | None = None
    formula_params: dict[str, object] | None = None


class SupplierInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    supplier_id: str = Field(min_length=1)
    supplier_name: str = Field(min_length=1, max_length=80)
    submission_date: date
    experience_rating: float = Field(ge=0, le=5)
    filename: str = ""


class CriterionResult(BaseModel):
    """One normalised, post-validation criterion result."""

    criterion_id: int
    score: int | None
    max_score: int
    justification: str = ""
    evidence: str = ""
    status: ResultStatus = "ok"
    evidence_verified: bool | None = None
    addressed: bool | None = None
    confidence: Literal["low", "medium", "high"] | None = None


class Scorecard(BaseModel):
    supplier_id: str
    supplier_name: str
    criteria: list[CriterionResult]
    risks: list[str] = Field(default_factory=list)
    overall_summary: str = ""
    warnings: list[RunWarning] = Field(default_factory=list)
    ok: bool = True


class CriterionComputed(BaseModel):
    criterion_id: int
    score: int | None
    max_score: int
    weight: float
    benchmark: int | None
    gap: int | None
    relative_pct: float
    weighted_contribution: float
    overridden: bool = False
    original_score: int | None = None


class SupplierComputed(BaseModel):
    supplier_id: str
    supplier_name: str
    submission_date: date
    experience_rating: float
    status: SupplierStatus
    rank: int | None = None
    absolute_score: float | None = None
    ppi: float | None = None
    criteria: list[CriterionComputed] = Field(default_factory=list)
    disqualified_reasons: list[str] = Field(default_factory=list)


class TieBreak(BaseModel):
    higher_rank: int
    lower_rank: int
    higher_name: str
    lower_name: str
    deciding_rule: Literal["PPI", "SUBMISSION_DATE", "EXPERIENCE", "NAME", "SUPPLIER_ID"]
    detail: str


class RankedRun(BaseModel):
    suppliers: list[SupplierComputed]
    tie_breaks: list[TieBreak] = Field(default_factory=list)
    warnings: list[RunWarning] = Field(default_factory=list)
    weights_normalised: bool = False

"""Orchestrator: runs the evaluation pipeline and streams progress.

Steps (brief section 4): reload criteria -> create run -> per supplier [extract -> prompt ->
LLM -> validate] in a bounded thread pool -> score/benchmark/rank (deterministic) -> persist
in one transaction -> final outcome.

The generator is meant to be consumed on the caller's thread (Streamlit's script thread).
Workers never touch the database or any UI object; they only put events on a queue.
"""

from __future__ import annotations

import queue
import sqlite3
import threading
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from core import db
from core.config import Settings
from core.export import build_export, run_status
from core.models import (
    Criterion,
    CriterionResult,
    RankedRun,
    RunWarning,
    Scorecard,
    SupplierInput,
)
from core.tools.document_tool import DocumentError, ExtractedDoc, extract
from core.tools.evaluation_agent import (
    evaluate_supplier,
    evaluate_supplier_retrieval,
    extract_facts,
    llm_criteria,
    prompt_hash,
)
from core.tools.formula_scoring import formula_criteria, score_formulas
from core.tools.llm_provider import LLMProvider, ProviderError
from core.tools.ranking_tool import Overrides, RankOptions, rank
from core.tools.retrieval import DEFAULT_TARGET_TOKENS, EmbedFn, Index, chunk_document
from core.tools.sensitivity import sensitivity

RETRIEVAL_MODES = ("auto", "full", "retrieve")


def choose_mode(setting: str, est_tokens: int, threshold: int) -> str:
    """'full' (whole document in one prompt) or 'retrieval' (best passages per criterion)."""
    if setting == "full":
        return "full"
    if setting == "retrieve":
        return "retrieval"
    return "retrieval" if est_tokens > threshold else "full"


# Stages of one supplier, in order, with the share of that supplier's work completed.
STAGE_PROGRESS = {
    "QUEUED": 0.0,
    "READING": 0.1,
    "SCORING": 0.3,
    "VALIDATING": 0.9,
    "DONE": 1.0,
    "FAILED": 1.0,
}


class EvaluationInputError(ValueError):
    """The request cannot start (no suppliers, too many, duplicate ids...)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SupplierUpload:
    supplier: SupplierInput
    pdf_bytes: bytes = field(repr=False)


@dataclass(frozen=True)
class RunOptions:
    model: str
    temperature: float | None = 0.0
    enable_knockouts: bool = False
    overrides: Overrides = field(default_factory=dict)
    sensitivity: bool = True
    sensitivity_draws: int = 500
    retrieval: str = "auto"  # auto | full | retrieve

    def as_json(self) -> dict[str, Any]:
        return {
            "temperature": self.temperature,
            "enable_knockouts": self.enable_knockouts,
            "overrides": len(self.overrides),
            "sensitivity": self.sensitivity,
            "sensitivity_draws": self.sensitivity_draws,
            "retrieval": self.retrieval,
        }


@dataclass(frozen=True)
class ProgressEvent:
    stage: str  # QUEUED READING SCORING VALIDATING DONE FAILED | RANKING SAVING
    message: str
    overall_pct: float
    supplier_id: str | None = None
    supplier_name: str | None = None


@dataclass
class RunOutcome:
    run_id: str
    status: str
    ranked: RankedRun
    scorecards: list[Scorecard]
    export: dict[str, Any]
    criteria: list[Criterion]
    cancelled: bool = False


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def failed_scorecard(
    supplier: SupplierInput, criteria: Sequence[Criterion], code: str, message: str
) -> Scorecard:
    return Scorecard(
        supplier_id=supplier.supplier_id,
        supplier_name=supplier.supplier_name,
        criteria=[
            CriterionResult(
                criterion_id=c.criterion_id,
                score=None,
                max_score=c.max_score,
                justification="Not evaluated.",
                status="missing",
            )
            for c in criteria
        ],
        warnings=[RunWarning.make(code, message, supplier_id=supplier.supplier_id)],
        ok=False,
    )


class _Worker:
    """Evaluates one supplier on a pool thread. Pure with respect to shared state."""

    def __init__(
        self,
        provider: LLMProvider,
        options: RunOptions,
        criteria: Sequence[Criterion],
        settings: Settings,
        events: queue.Queue[tuple[str, str, str]],
        cancel: threading.Event,
    ) -> None:
        self.provider, self.options, self.criteria = provider, options, criteria
        self.settings, self.events, self.cancel = settings, events, cancel

    def _emit(self, upload: SupplierUpload, stage: str, message: str) -> None:
        self.events.put((upload.supplier.supplier_id, stage, message))

    def _embedder(self, card_warnings: list[RunWarning], supplier_id: str) -> EmbedFn | None:
        embed = getattr(self.provider, "embed", None)
        if embed is None:
            return None

        def run(texts: list[str]) -> list[list[float]]:
            return embed(texts)  # type: ignore[no-any-return]

        try:
            run(["probe"])
        except ProviderError:
            card_warnings.append(
                RunWarning.make(
                    "I_NO_EMBEDDINGS",
                    "Embeddings were unavailable; retrieval used keyword search only.",
                    supplier_id=supplier_id,
                )
            )
            return None
        return run

    def _score(self, upload: SupplierUpload, doc: ExtractedDoc) -> Scorecard:
        """LLM-judged criteria (full text or retrieval), then formula facts. No arithmetic here."""
        sup = upload.supplier
        judged = llm_criteria(self.criteria)
        formula = formula_criteria(self.criteria)
        mode = choose_mode(
            self.options.retrieval, doc.est_tokens, self.settings.full_context_max_tokens
        )
        extra_warnings: list[RunWarning] = []
        if not judged:
            card = Scorecard(
                supplier_id=sup.supplier_id, supplier_name=sup.supplier_name, criteria=[]
            )
        elif mode == "retrieval":
            self._emit(
                upload,
                "SCORING",
                f"Long document ({doc.pages} pages): searching for the best passages",
            )
            # A provider may need smaller passages (the local MiniLM model reads ~256 tokens).
            chunk_tokens = int(getattr(self.provider, "chunk_tokens", DEFAULT_TARGET_TOKENS))
            top_k = self.settings.retrieval_top_k * int(getattr(self.provider, "top_k_factor", 1))
            index = Index.build(
                chunk_document(doc, target_tokens=chunk_tokens),
                self._embedder(extra_warnings, sup.supplier_id),
            )
            card = evaluate_supplier_retrieval(
                self.provider,
                model=self.options.model,
                criteria=judged,
                supplier=sup,
                doc=doc,
                index=index,
                top_k=top_k,
                temperature=self.options.temperature,
            ).scorecard
        else:
            self._emit(upload, "SCORING", f"Scoring {doc.pages} pages with the model")
            card = evaluate_supplier(
                self.provider,
                model=self.options.model,
                criteria=judged,
                supplier=sup,
                document=doc.text,
                temperature=self.options.temperature,
            ).scorecard
        card.warnings.extend(extra_warnings)
        if formula and card.ok:
            self._emit(upload, "SCORING", "Reading price and schedule figures")
            facts, fact_warnings = extract_facts(
                self.provider,
                model=self.options.model,
                criteria=formula,
                supplier=sup,
                document=doc.text,
                temperature=self.options.temperature,
            )
            card.facts, card.warnings = facts, card.warnings + fact_warnings
        return card

    def __call__(self, upload: SupplierUpload) -> Scorecard:
        sup = upload.supplier
        if self.cancel.is_set():
            return failed_scorecard(sup, self.criteria, "E_CANCELLED", "The run was cancelled.")
        try:
            self._emit(upload, "READING", "Reading the PDF")
            doc = extract(
                upload.pdf_bytes,
                max_pages=self.settings.max_pages,
                max_bytes=self.settings.max_bytes,
            )
            if self.cancel.is_set():
                return failed_scorecard(sup, self.criteria, "E_CANCELLED", "The run was cancelled.")
            card = self._score(upload, doc)
            self._emit(upload, "VALIDATING", "Validating the scorecard")
            for w in doc.warnings:
                card.warnings.append(w.model_copy(update={"supplier_id": sup.supplier_id}))
            return card
        except DocumentError as exc:
            return failed_scorecard(sup, self.criteria, exc.code, exc.message)
        except ProviderError as exc:
            return failed_scorecard(sup, self.criteria, exc.code, str(exc))
        except Exception as exc:  # never let one supplier take the batch down
            return failed_scorecard(
                sup, self.criteria, "E_UNEXPECTED", f"Unexpected error: {type(exc).__name__}"
            )


def run_evaluation(
    uploads: Sequence[SupplierUpload],
    *,
    provider: LLMProvider,
    conn: sqlite3.Connection,
    options: RunOptions,
    settings: Settings | None = None,
    cancel: threading.Event | None = None,
    now: Callable[[], str] = _utc_now,
) -> Iterator[ProgressEvent | RunOutcome]:
    """Run a batch. Yields ProgressEvents, then exactly one RunOutcome as the last item."""
    cfg = settings or Settings()
    cancel = cancel or threading.Event()

    if not uploads:
        raise EvaluationInputError("E_NO_SUPPLIERS", "Add at least one supplier PDF.")
    if len(uploads) > cfg.max_suppliers:
        raise EvaluationInputError(
            "E_TOO_MANY_SUPPLIERS", f"A run can include at most {cfg.max_suppliers} suppliers."
        )
    ids = [u.supplier.supplier_id for u in uploads]
    if len(set(ids)) != len(ids):
        raise EvaluationInputError("E_DUPLICATE_SUPPLIER", "Supplier ids must be unique.")

    # FR-3: always evaluate against the LATEST active criteria from the database.
    all_active = db.load_active_criteria(conn)
    db.check_criteria(all_active)
    criteria = list(all_active)  # LLM-judged and formula criteria, both scored below
    suppliers = [u.supplier for u in uploads]

    run_id = db.new_run_id()
    db.start_run(
        conn,
        run_id=run_id,
        created_at=now(),
        model=options.model,
        prompt_hash=prompt_hash(criteria),
        options=options.as_json(),
    )
    try:
        progress = {s.supplier_id: 0.0 for s in suppliers}
        names = {s.supplier_id: s.supplier_name for s in suppliers}

        def pct() -> float:
            return round(sum(progress.values()) / len(progress) * 0.9, 4)

        for s in suppliers:
            yield ProgressEvent("QUEUED", "Waiting to start", pct(), s.supplier_id, s.supplier_name)

        events: queue.Queue[tuple[str, str, str]] = queue.Queue()
        worker = _Worker(provider, options, criteria, cfg, events, cancel)
        cards: dict[str, Scorecard] = {}

        def drain() -> Iterator[ProgressEvent]:
            while True:
                try:
                    sid, stage, message = events.get_nowait()
                except queue.Empty:
                    return
                progress[sid] = max(progress[sid], STAGE_PROGRESS[stage])
                yield ProgressEvent(stage, message, pct(), sid, names[sid])

        with ThreadPoolExecutor(max_workers=max(1, cfg.concurrency)) as pool:
            futures: dict[Future[Scorecard], SupplierUpload] = {
                pool.submit(worker, u): u for u in uploads
            }
            pending = set(futures)
            while pending:
                done, pending = wait(pending, timeout=0.05)
                yield from drain()
                for fut in done:
                    sid = futures[fut].supplier.supplier_id
                    cards[sid] = fut.result()
                    progress[sid] = 1.0
                    stage = "DONE" if cards[sid].ok else "FAILED"
                    message = "Scorecard ready" if cards[sid].ok else _first_error(cards[sid])
                    yield ProgressEvent(stage, message, pct(), sid, names[sid])
            yield from drain()

        cancelled = cancel.is_set()
        if cancelled:
            cards = {
                s.supplier_id: failed_scorecard(
                    s, criteria, "E_CANCELLED", "The run was cancelled."
                )
                for s in suppliers
            }

        yield ProgressEvent("RANKING", "Calculating scores and ranking", 0.93)
        formula_warnings: list[RunWarning] = []
        has_formula = not cancelled and bool(formula_criteria(criteria))
        judged = {sid: list(c.criteria) for sid, c in cards.items()}  # before formula results

        def apply_formulas(exclude: set[str]) -> list[RunWarning]:
            """Score formula criteria across suppliers; `exclude` stays out of the benchmark."""
            scored, warns = score_formulas(
                criteria, {sid: c.facts for sid, c in cards.items() if c.ok}, exclude
            )
            for sid, extra in scored.items():
                cards[sid].criteria = sorted(judged[sid] + extra, key=lambda r: r.criterion_id)
            return warns

        if (
            has_formula
        ):  # formula criteria compare suppliers with each other, so score them together
            formula_warnings = apply_formulas(set())
        ordered_cards = [cards[s.supplier_id] for s in suppliers]
        rank_opts = RankOptions(
            enable_knockouts=options.enable_knockouts, overrides=options.overrides
        )
        ranked = rank(ordered_cards, suppliers, criteria, rank_opts)
        disqualified = {s.supplier_id for s in ranked.suppliers if s.status == "DISQUALIFIED"}
        if has_formula and disqualified:
            # A disqualified supplier must not set the price/time benchmark everyone else is
            # scored against, so recompute without it and rank again.
            formula_warnings = apply_formulas(disqualified)
            ranked = rank(ordered_cards, suppliers, criteria, rank_opts)
        ranked.warnings.extend(formula_warnings)
        if cancelled:
            ranked.warnings.append(RunWarning.make("E_CANCELLED", "The run was cancelled."))

        sens: dict[str, Any] | None = None
        ranked_count = sum(1 for s in ranked.suppliers if s.status == "RANKED")
        if options.sensitivity and ranked_count >= 2 and not cancelled:
            sens = sensitivity(
                ordered_cards, suppliers, criteria, rank_opts, draws=options.sensitivity_draws
            ).model_dump(mode="json")

        yield ProgressEvent("SAVING", "Saving the run", 0.97)
        status = "FAILED" if cancelled else run_status(ranked)
        export = build_export(
            run_id=run_id,
            created_at=_created_at(conn, run_id),
            status=status,
            model=options.model,
            prompt_hash=prompt_hash(criteria),
            criteria=criteria,
            scorecards=ordered_cards,
            ranked=ranked,
            sensitivity=sens,
            options=options.as_json(),
        )
        db.persist_results(
            conn,
            run_id=run_id,
            status=status,
            criteria=criteria,
            scorecards=ordered_cards,
            ranked=ranked,
            export=export,
        )
    except BaseException:
        db.fail_run(conn, run_id)
        raise

    yield ProgressEvent("COMPLETE", f"Run {status.lower()}", 1.0)
    yield RunOutcome(
        run_id=run_id,
        status=status,
        ranked=ranked,
        scorecards=ordered_cards,
        export=export,
        criteria=criteria,
        cancelled=cancelled,
    )


def _first_error(card: Scorecard) -> str:
    for w in card.warnings:
        if w.severity == "error":
            return w.message
    return "Evaluation failed"


def _created_at(conn: sqlite3.Connection, run_id: str) -> str:
    row = conn.execute("SELECT created_at FROM rfp_runs WHERE rfp_run_id = ?", (run_id,)).fetchone()
    return str(row["created_at"])


def collect(events: Iterator[ProgressEvent | RunOutcome]) -> tuple[list[ProgressEvent], RunOutcome]:
    """Drain a run generator (handy for tests and scripts)."""
    seen: list[ProgressEvent] = []
    outcome: RunOutcome | None = None
    for item in events:
        if isinstance(item, RunOutcome):
            outcome = item
        else:
            seen.append(item)
    if outcome is None:
        raise RuntimeError("Run produced no outcome")
    return seen, outcome

# Agents and tools

The brief asks for an *agentic* workflow: an orchestrator that calls tools in order, an LLM that judges proposal content, and
deterministic code that does everything else. This page documents each agent and tool: what it does, what goes in and out,
whether an LLM is involved, what it is **not** allowed to do, and how it fails. Diagrams are in [DIAGRAMS.md](DIAGRAMS.md).

> **The rule that shapes everything.** The LLM may *judge content* (read, quote, justify, score a criterion). It never computes totals,
> benchmarks, relative percentages, PPI, tie-breaks or ranks. Those are deterministic Python. Given the same validated scorecards, the
> output is byte-for-byte identical.

## 1. Roster

| # | Component | Kind | Module | LLM? |
|---|---|---|---|---|
| 1 | **Orchestrator** | Controller (deterministic code) | `core/orchestrator.py` | No |
| 2 | **Document Tool** | Tool | `core/tools/document_tool.py` | No |
| 3 | **Evaluation Agent** | LLM agent (judges criteria) | `core/tools/evaluation_agent.py` | **Yes** |
| 4 | **Fact Extraction Agent** | LLM agent (reads figures only) | `core/tools/evaluation_agent.py`, `formula_scoring.py` | **Yes** |
| 5 | **Retrieval Tool** | Tool | `core/tools/retrieval.py` | Embeddings only |
| 6 | **Validation Tool** | Tool | `core/tools/validation_tool.py` | No |
| 7 | **Formula Scoring Tool** | Tool | `core/tools/formula_scoring.py` | No |
| 8 | **Ranking Tool** | Tool | `core/tools/ranking_tool.py` | No |
| 9 | **Sensitivity Tool** | Tool | `core/tools/sensitivity.py` | No |
| 10 | **Override Engine** | Tool | `core/overrides.py` | No |
| 11 | **Backup / Restore** | Tool | `core/backup.py` | No |
| 12 | **Provider layer** | Boundary to the outside world | `core/tools/llm_provider.py` and friends | Calls the LLM |
| 13 | **Eval Harness** | Meta-agent (tests the others) | `core/evals.py` | Uses a provider |

"Agent" is used only for the two components that call a language model to make a judgement. The orchestrator is deliberately **not** an
LLM: a model deciding control flow would make runs non-reproducible, and the brief requires the same inputs to give the same ordering.

## 2. Orchestrator

`run_evaluation(uploads, *, provider, conn, options, settings=None, cancel=None, now=...)` returns a generator of `ProgressEvent`s and
finishes with exactly one `RunOutcome`.

Steps, in order:

1. Validate the request (1 to `max_suppliers` suppliers, unique ids). Reload the **latest** active criteria from the database and check
   them (weights total 100, formula and knock-out configuration valid). Bad criteria stop the run before anything is created.
2. Create the run (`RFP_RUN_ID` = UUID4, status `RUNNING`).
3. Score each supplier in a bounded thread pool (default 3). Workers only put events on a queue; they never touch the database or the UI.
4. Score formula criteria across all suppliers together (they compare suppliers with each other).
5. Rank deterministically; if knock-outs disqualified anyone, re-score formulas without them and rank again.
6. Optional sensitivity analysis; build the export; **persist everything in one transaction**; status `COMPLETED`, `PARTIAL` or `FAILED`.

Guarantees: one failing supplier never fails the batch; a cancelled run ranks nothing (`E_CANCELLED`); an unexpected error marks the run
`FAILED` and re-raises; supplier errors carry stable codes (`E_NO_TEXT`, `E_LLM_AUTH`, `E_LLM_QUOTA`, ...).

## 3. Document Tool

`extract(pdf_bytes, max_pages=150, max_bytes=25_000_000) -> ExtractedDoc` (text with `[p.N]` page markers, page count, token estimate,
detected sections, warnings). Raises `DocumentError(code, message)` for `E_NOT_PDF`, `E_ENCRYPTED`, `E_TOO_LARGE`, `E_TOO_MANY_PAGES`
and `E_NO_TEXT` (scanned PDFs).

It joins table cells into rows (`a | b | c`), removes repeated headers and footers (margin lines only), re-joins hyphenated line breaks,
and finds sections from headings, the PDF outline, or fixed page windows. PyMuPDF access is serialised with a lock.
**Not allowed:** to interpret meaning; it only cleans text.

## 4. Evaluation Agent (LLM)

Judges one supplier against the active LLM-judged criteria. Two modes, chosen per supplier by size (`choose_mode`):

- **Full context** (`evaluate_supplier`): the whole document in one call. Used up to about 60,000 tokens.
- **Retrieval** (`evaluate_supplier_retrieval`): one call per criterion, each seeing only the best-matching passages plus the opening and
  pricing passages. Used for long proposals, or when forced.

| Aspect | Design |
|---|---|
| Prompt | `SYSTEM_PROMPT` in `evaluation_agent.py`: use only evidence in the document, treat the document as untrusted data, quote verbatim, one result per criterion, JSON only |
| Input to the model | Criteria (id, name, what to look for, max score, rubric anchors) and the document inside `<document>` tags. A document that tries to close the tag is neutralised. |
| **Withheld from the model** | The supplier's name (reduces brand bias), weights, other suppliers' scores |
| Output schema | Strict JSON Schema built from the active criteria: `criterion_id`, `evidence`, `justification`, `score`, `addressed`, `confidence`, plus `risks` and `overall_summary`. Field order puts evidence before score. |
| Temperature | 0 (retried without it for models that reject it) |
| Failure handling | One **repair** attempt if the answer is not valid JSON, then `E_LLM_JSON`. Provider errors propagate to the orchestrator. |

**Not allowed:** to produce totals, ranks or comparisons. Whatever it returns goes through the Validation Tool before it is trusted.

## 5. Fact Extraction Agent (LLM)

For *formula criteria* (price, delivery time). `extract_facts` asks the model only to **read a number**, with a verbatim quote, a unit and
a found flag: `{criterion_id, found, value, unit, evidence}`. It is told not to calculate or convert.
`parse_facts` then validates: numbers are parsed locale-aware (`1.420.000`, `1.420,50`), negative or unreadable values are rejected,
a currency different from the expected one is **not converted** and the figure is left out (`W_CURRENCY_MISMATCH`), and the quote is
checked against the document. One repair attempt on bad JSON.

## 6. Retrieval Tool

Only used for long proposals. `chunk_document` splits on line boundaries (about 600 tokens, 150 for local embeddings), keeps tables whole,
keeps page markers and labels each chunk with its section. `Index` combines **BM25** (stemmed, query terms deduplicated) with
**embedding similarity** by reciprocal-rank fusion; if embeddings are unavailable it runs on keywords alone.
`retrieve_for_criterion` returns the best passages in page order, plus the opening chunk and the best pricing chunk, and flags a weak match
(`W_LOW_RETRIEVAL`). Embeddings come from OpenAI, or locally with sentence-transformers; OpenRouter has no embeddings endpoint.

## 7. Validation Tool

`validate(raw, criteria, supplier, source_text, source_index=None) -> Scorecard`. The gatekeeper between the model and the arithmetic.
It never raises on arbitrary input and always returns exactly one result per active criterion.

| Problem in the model's answer | What happens |
|---|---|
| Not JSON, or not an object | Failed scorecard (`E_JSON_PARSE`); the agent may repair once |
| Missing criterion | Marked `missing`, score `None` (`W_MISSING_CRITERION`) |
| Score out of range, huge, or a number as text | Clipped, coerced or rounded half-up, each with a warning |
| Non-numeric score (`"high"`, `null`, `true`, `NaN`) | Score `None`, status `malformed` |
| Duplicate or unknown criterion id | First kept; unknown dropped |
| Quote not found in the document | `evidence_verified = False`, `W_UNVERIFIED_EVIDENCE` |
| Text in the proposal that looks like instructions to an AI | `W_INJECTION_SUSPECTED` (scores unchanged) |
| Every criterion identical | `W_OUTLIER_PATTERN` |

## 8. Formula Scoring Tool

`score_formulas(criteria, facts, exclude_from_benchmark=())` turns figures into scores for every supplier at once:
`lowest_is_best` (`max * lowest / value`), `highest_is_best`, or `threshold_bands`. Scores are integers rounded half-up and clipped to the
maximum. Missing or unusable figures score as missing. Suppliers passed in `exclude_from_benchmark` (disqualified ones) are scored but cannot
set the benchmark. Each result carries a plain-language justification showing the arithmetic.

## 9. Ranking Tool

`rank(scorecards, suppliers, criteria, options) -> RankedRun`. Pure `Decimal` arithmetic, fixed to 6 decimal places.

1. Normalise weights if they do not total 100 (warns).
2. Optional **knock-outs**: a mandatory criterion below its minimum disqualifies a supplier.
3. **Benchmark** per criterion: the highest valid score among eligible suppliers.
4. Per supplier: absolute weighted score, gap to benchmark, relative percentage (a zero benchmark gives 100), **PPI** (weighted average of relative percentages).
5. **Stable sort**, then sequential ranks: higher PPI, earlier submission date, higher experience rating, supplier name (case-insensitive), supplier id.
6. A plain-language **tie-break explanation** for each adjacent pair, naming the rule that separated them.

Optional **overrides** are applied here as score replacements; the model's original scores are kept alongside.
**Not allowed:** to call the LLM, read the clock, or use randomness.

## 10. Sensitivity Tool

`sensitivity(...)` shows how stable the ranking is: shift each weight by plus or minus 10 points, and draw 500 random weightings around the
current ones (fixed seed 42, so repeatable). Output: whether any order changes, and the share of weightings in which each supplier lands at
each rank.

## 11. Override Engine

`apply_overrides(conn, run_id, requests)`: an analyst replaces a criterion score with a **required reason**. The engine rebuilds the ranking
inputs from the saved run, applies all overrides (the latest per cell wins), re-ranks with the same Ranking Tool, rewrites the results in one
transaction and appends to an audit trail. The model's scorecard is never edited. Refuses failed suppliers and failed runs, out-of-range
scores, empty reasons and duplicate cells.

## 12. Backup / Restore

`export_bundle`, `sqlite_bytes`, `parse_bundle`, `bundle_from_sqlite`, `import_bundle`. Backups are **untrusted input**: size and run-count
limits, structure checks, safe run ids, and every run is **rebuilt and re-ranked** by the normal code instead of being copied as stored rows.
Existing runs are never overwritten. The browser keeps the same bundle in IndexedDB for automatic restore (`ui/local_store`).

## 13. Provider layer

All model access sits behind one `LLMProvider` interface (`validate_key`, `complete_json`, `embed`). Only `llm_provider.py` imports the
OpenAI SDK.

| Class | Purpose |
|---|---|
| `OpenAIProvider` | OpenAI. Strict JSON schema, retry with back-off and `Retry-After`, no-credit detection (`E_LLM_QUOTA`), key never in `repr`, pickles or errors |
| `OpenRouterProvider` | OpenRouter (OpenAI-compatible); falls back to plain JSON mode when a model lacks strict schemas; no embeddings |
| `WithLocalEmbeddings` | Wraps any provider; embeddings computed locally (all-MiniLM-L6-v2) |
| `FakeProvider` + `demo_responder` | Offline, deterministic keyword scorer for demos and tests; never selected unless `RFP_FAKE_LLM` is set |
| `RecordingProvider` / `ReplayProvider` | Record live answers once, replay them offline for free |

## 14. Eval Harness

`python -m core.evals` scores the fictional proposals against `data/synthetic_rfps/ground_truth.json` and reports: band accuracy, ranking
agreement (ignoring near-ties the ground truth does not order), JSON validity without repair, evidence grounding, prompt-injection
resistance, a metamorphic check (deleting the security section must lower the security score), figure accuracy, and retrieval-versus-full
agreement. It reports why a model fails instead of crashing.

## 15. Trust boundaries

| Source | Treated as | Controls |
|---|---|---|
| Supplier PDF text | Untrusted data | Delimited in the prompt; injection scan; never executed; rendered as plain text |
| LLM output | Untrusted claims | Schema, validation, clipping, quote verification; no arithmetic delegated |
| Uploaded backup | Untrusted input | Limits, structure checks, rebuild and re-rank |
| API key | Secret | Session memory only; scrubbed from errors; never in the database, exports, logs or backups |
| Analyst override | Trusted but audited | Reason required; original kept; fully logged |

## 16. Error codes by component

| Component | Codes |
|---|---|
| Document Tool | `E_NOT_PDF`, `E_ENCRYPTED`, `E_TOO_LARGE`, `E_TOO_MANY_PAGES`, `E_NO_TEXT`, `W_EMPTY_PAGE` |
| Provider | `E_LLM_AUTH`, `E_LLM_RATE`, `E_LLM_QUOTA`, `E_LLM_TIMEOUT`, `E_LLM_REFUSAL`, `E_LLM` |
| Evaluation Agent | `E_LLM_JSON`, `I_REPAIRED`, `W_LOW_RETRIEVAL`, `I_NO_EMBEDDINGS` |
| Validation | `E_JSON_PARSE`, `W_MISSING_CRITERION`, `W_CLIPPED`, `W_MALFORMED_SCORE`, `W_UNVERIFIED_EVIDENCE`, `W_INJECTION_SUSPECTED`, `W_OUTLIER_PATTERN` |
| Formula | `W_FACT_MISSING`, `W_FACT_INVALID`, `W_CURRENCY_MISMATCH`, `E_FORMULA_CONFIG` |
| Ranking | `W_WEIGHTS_NORMALISED`, `W_SINGLE_SUPPLIER`, `E_CANCELLED` |
| Overrides | `E_SCORE_RANGE`, `E_REASON_REQUIRED`, `E_RUN_FAILED`, `E_SUPPLIER_FAILED`, `E_DUPLICATE` |

## 17. Adding a new agent or tool

1. Decide whether it judges content (LLM, so it needs validation) or computes (deterministic, so it must be pure and testable).
2. Put it in `core/tools/`, with no UI imports; talk to models only through the provider interface.
3. Give it a Pydantic or typed contract, stable error codes, and a failure path that cannot take the batch down.
4. Test it with `FakeProvider` (contract tests), add property tests if it computes, and add a metric to the eval harness if it judges.
5. Wire it into the orchestrator and document it here and in [DIAGRAMS.md](DIAGRAMS.md).

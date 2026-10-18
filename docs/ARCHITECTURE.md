# Architecture

## 1. Goals and constraints

| # | Goal | Source |
|---|---|---|
| G1 | LLM judges content; Python does all arithmetic, benchmarks, tie-breaks, rank | Brief §3 |
| G2 | Criteria and weights live in SQLite, editable without code changes | Brief §5 |
| G3 | Same validated scorecards always produce identical scores and ordering | Brief success condition |
| G4 | Every score traceable to criterion, weight, evidence, rule | Brief success condition |
| G5 | OpenAI API key entered per session, held in memory only | User requirement |
| G6 | Rich, smooth UX inside Streamlit | User requirement |
| G7 | Runs locally with one command; deployable to Streamlit Community Cloud | Brief §10 |

Non-goals: multi-user accounts, server-side key storage, OCR of scanned PDFs (detected and rejected).

Optional features (behind toggles, default **off** so default behaviour equals the brief): analyst override,
knock-out criteria, formula-based criteria, self-consistency, long-document retrieval fallback,
requirements compliance matrix.

## 2. System context

```
 Analyst (browser)
      │  Streamlit session (key held in st.session_state only)
      ▼
┌──────────────────────────── Streamlit process ─────────────────────────────┐
│  app.py ── ui/ (screens)                                                   │
│        │                                                                   │
│        ▼                                                                   │
│  core/orchestrator.py  ──►  tools/document_tool     (PyMuPDF)              │
│        │               ──►  tools/evaluation_agent  ──► llm_provider ──► OpenAI API
│        │               ──►  tools/validation_tool   (Pydantic + rules)     │
│        │               ──►  tools/ranking_tool      (pure deterministic)   │
│        ▼                                                                   │
│  core/db.py ──► SQLite file (WAL)                                          │
└────────────────────────────────────────────────────────────────────────────┘
```

The only outbound network call is to the OpenAI API, authenticated with the user's session key.

## 3. Layering rules

1. `core/` imports nothing from `ui/` or `streamlit`. It is testable headless.
2. `core/tools/ranking_tool.py` and `validation_tool.py` are **pure functions**: no I/O, no clock, no randomness
   (Monte Carlo uses a fixed seed passed in).
3. Only `llm_provider.py` imports `openai`. Everything else depends on the `LLMProvider` protocol, so tests inject a fake.
4. Only `db.py` touches SQLite.
5. `ui/` modules read/write `st.session_state` and call `core.orchestrator`; they hold no business rules.

## 4. Components

| Component | Responsibility | Deterministic? |
|---|---|---|
| **Orchestrator** | Runs the 10-step pipeline; concurrency; progress events; failure isolation; single-transaction persist | Yes (given tool outputs) |
| **Document Tool** | PDF → clean text with `[p.N]` markers; detects encrypted/empty/scanned/oversized; token estimate | Yes |
| **Evaluation Agent** | Builds prompt from active criteria (+ rubric anchors); calls LLM with a dynamic JSON Schema; one repair retry | No (LLM) |
| **LLM Provider** | OpenAI wrapper: key validation, model list, structured-output call, backoff, error scrubbing | No |
| **Validation Tool** | Parse JSON → Pydantic; fill missing criteria; clip scores; drop unknown ids; verify evidence against source text; flag injection patterns and outliers; emit warnings | Yes |
| **Ranking Tool** | Absolute score, benchmarks, gaps, relative %, PPI, stable sort with tie-break, tie-break explanations, sensitivity | Yes |
| **DB layer** | Schema, seed, criteria CRUD, run persistence, history | Yes |
| **UI** | Stepper, session gate, criteria editor, supplier input, live progress, leaderboard, scorecard dialog, compare, run details | n/a |

## 5. Data flow (brief §4 mapped to code)

| Step | Action | Module |
|---|---|---|
| 1 Setup | Start app, ensure DB, load active criteria | `app.py`, `db.py` |
| 2 Input | Upload PDFs + metadata, validate | `ui/suppliers.py`, `document_tool` |
| 3 Batch | Create run, generate `RFP_RUN_ID` (UUID4), status `RUNNING` | `orchestrator` |
| 4 Evaluate | **Reload** criteria, extract text, build prompt, call LLM (parallel, bounded) | `evaluation_agent` |
| 5 Validate | Parse + normalise each scorecard; record warnings | `validation_tool` |
| 6 Score | Absolute weighted score per supplier | `ranking_tool` |
| 7 Benchmark | Per-criterion best valid score; gaps; relative % | `ranking_tool` |
| 8 Rank | PPI; stable sort; sequential ranks; tie-break explanations | `ranking_tool` |
| 9 Persist | One transaction: run, criteria snapshot, supplier results, warnings; status `COMPLETED`/`PARTIAL`/`FAILED` | `db` |
| 10 Present | Leaderboard, scorecards, run details, JSON download | `ui/results.py` |

Failure isolation: a supplier whose PDF is unreadable or whose LLM call fails is marked `FAILED`, excluded from
benchmarks and ranking, and listed in run warnings. The run is `PARTIAL` if at least one supplier succeeded
(minimum two succeeded suppliers are needed for a meaningful peer benchmark; with one, relative % is 100 and a warning is shown).

## 6. Concurrency model

Streamlit calls must stay on the script thread. The orchestrator exposes a generator `run(...)` that submits
supplier jobs to a `ThreadPoolExecutor(max_workers=3)` and yields `ProgressEvent`s from `as_completed` back in the
caller's thread. The UI consumes events and updates `st.status`. Workers never call `st.*`.

## 7. Key handling (security architecture)

| Aspect | Design |
|---|---|
| Entry | Password field on Connect screen |
| Storage | `st.session_state["llm_key"]` only (per browser session; Streamlit isolates sessions) |
| Transport | Passed in-process to `LLMProvider`; never serialised |
| Never in | disk, SQLite, logs, exports, run JSON, URL, widget labels |
| Validation | Cheap `models.list()` call; model dropdown populated from it |
| Errors | Exceptions pass through `scrub()` which masks anything matching `sk-[A-Za-z0-9_\-]{8,}` |
| Disconnect | Clears key and dependent state |
| Cloud | Works on Community Cloud because secrets are never needed server-side |

## 8. Other security / safety controls

- **Upload guardrails:** `%PDF` magic-byte check, size cap (25 MB), page cap (default 40), encrypted-PDF rejection, no-text detection.
- **Prompt injection:** document inserted inside delimiters and declared untrusted data; pattern scanner flags injection-like text as a warning; scores validated regardless.
- **Evidence grounding:** each evidence quote fuzzy-matched against source text; failures flagged `unverified_evidence`.
- **Cost guard:** token estimate × supplier count shown before Evaluate; hard cap configurable.
- **SQL:** parameterised queries only.
- **PII:** synthetic data only; docs warn against uploading confidential real proposals to third-party APIs without approval.

## 9. Persistence

SQLite, WAL mode, foreign keys on. Tables: `evaluation_criteria`, `rfp_runs`, `supplier_results` (brief minimum)
plus `run_criteria_snapshot`, `run_warnings`, `criterion_scores`, `score_overrides`. Full DDL in DESIGN_SPEC §3.
`result_json` is the source of truth for export; the denormalised tables serve queries and the history view.
On Community Cloud the filesystem is ephemeral: the seed script runs at startup and the README states that runs are not durable there.

## 10. Reproducibility

- Run stores a criteria snapshot (ids, names, weights, max scores, rubric text) so past runs recompute identically after criteria edits.
- All ranking math in `Decimal` or fixed-precision rounding (PPI compared at 6 decimals) to avoid float tie-break flips.
- Export JSON has stable key ordering and includes `schema_version`, `model`, `prompt_hash`, `criteria_snapshot`.

## 11. Technology choices

| Concern | Choice | Reason |
|---|---|---|
| UI | Streamlit | Required by brief; fragments, dialogs, status, theming |
| Charts | Plotly | Radar, bars, heatmap with hover |
| PDF | PyMuPDF | Fast, page-aware text |
| Validation | Pydantic v2 | Schema + custom validators |
| LLM | OpenAI SDK, structured outputs (JSON Schema) | Chosen by user |
| DB | SQLite stdlib `sqlite3` | Required by brief; no ORM needed |
| Tests | pytest, Hypothesis, Streamlit `AppTest`, Playwright, axe | See TEST_CASES |
| Quality | ruff, mypy | Fast lint/type gate |

## 12. Deployment

**Local:** `scripts/run_local.ps1` creates the venv, installs, seeds DB, runs `streamlit run app.py` on `127.0.0.1:8501`.
**Community Cloud:** `requirements.txt` + `app.py` at root; DB seeded on first run; no secrets required.

## 13. Key risks and mitigations

| Risk | Mitigation |
|---|---|
| LLM score inconsistency | Rubric anchors, low temperature, optional self-consistency (median), eval suite tracks variance |
| Hallucinated evidence | Substring/fuzzy verification, visible flag |
| Prompt injection in PDFs | Delimiting, scanner, validator, injection eval |
| Streamlit rerun side effects | Orchestrator in explicit button handler; results cached in `session_state`; fragments |
| Float tie-break noise | Fixed-precision comparison |
| Cost overruns | Pre-run estimate and cap |
| Scanned PDFs | Detected, rejected with actionable message |

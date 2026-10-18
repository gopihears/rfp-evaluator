# Agentic RFP Evaluation & Supplier Ranking

An AI-assisted Streamlit application that reads supplier RFP proposals (PDF), scores them against
configurable criteria with an LLM, benchmarks suppliers against their peers, and produces an
explainable, deterministic leaderboard.

> **Principle:** the LLM judges proposal *content*. Python owns all arithmetic, benchmarks,
> tie-breaks and ranking. Given the same validated scorecards, output is always identical.

**Status:** Milestone 1 complete (core models, SQLite schema + seed, validation tool, ranking tool,
sensitivity, export; 113 tests, 97% core coverage). Next: synthetic PDFs, document tool, OpenAI provider,
orchestrator, then the Streamlit UI. See `docs/` for the full design.

```powershell
pytest              # run the test suite
ruff check . ; mypy # lint and type-check core/
```

## Documentation

| Doc | Purpose |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | System architecture, components, data flow, security, deployment |
| [docs/DESIGN_SPEC.md](docs/DESIGN_SPEC.md) | Detailed design: schemas, prompts, formulas, UI screens, module contracts |
| [docs/TEST_CASES.md](docs/TEST_CASES.md) | Test strategy and enumerated test cases (unit, property, contract, evals, E2E) |

## Priority tiers

- **Must:** the brief, rubric anchors, evidence verification, golden-set evals, Playwright E2E.
- **Should:** formula-based criteria, long-document retrieval fallback (proposals up to ~150 pages), analyst overrides, knock-out criteria, sensitivity panel.
- **Could:** self-consistency scoring, requirements compliance matrix.

All optional features are toggles that default off, so default behaviour matches the brief exactly.

## Planned stack

Streamlit · SQLite · PyMuPDF · Pydantic · OpenAI SDK (structured outputs) · Plotly · pytest · Hypothesis · Playwright

## Quick start (once implemented)

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m core.seed          # create SQLite DB + seed criteria
streamlit run app.py
```

The OpenAI API key is entered at run time on the Connect screen, held only in session memory,
and never written to disk, the database, logs or exports.

## Repository layout

```
app.py                  Streamlit entry point
ui/                     One module per screen
core/                   UI-free engine (models, db, tools, orchestrator)
core/tools/             document, llm_provider, evaluation_agent, validation, ranking
data/synthetic_rfps/    Fictional supplier PDFs (incl. one deliberately broken)
docs/                   Architecture, design spec, test cases
tests/                  unit, property, contract, evals, e2e
scripts/                Dev helpers (PDF generation, run_local.ps1)
.streamlit/config.toml  Theme
```

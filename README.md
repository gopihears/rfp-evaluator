# Agentic RFP Evaluation & Supplier Ranking

An AI-assisted Streamlit application that reads supplier RFP proposals (PDF), scores them against
configurable criteria with an LLM, benchmarks suppliers against their peers, and produces an
explainable, deterministic leaderboard.

> **Principle:** the LLM judges proposal *content*. Python owns all arithmetic, benchmarks,
> tie-breaks and ranking. Given the same validated scorecards, output is always identical.

**Status:** Milestones 1-3 complete and working end to end: core engine, diversified synthetic RFPs with
ground truth, document tool, OpenAI provider, evaluation agent, orchestrator, and the Streamlit app
(connect, criteria, suppliers with live progress, leaderboard, scorecards, compare, run details,
history, JSON export). 266 tests incl. 13 real-browser Playwright tests; 98% core coverage.
Not yet done: live-model evals against the golden bands, long-document retrieval (OPT-5), formula
criteria (OPT-3), analyst overrides UI, Community Cloud deployment notes.

## Try it now

```powershell
.\scripts\run_local.ps1 -Demo    # offline demo, no API key; open http://localhost:8501
.\scripts\run_local.ps1          # real run: you type your OpenAI key in the app
```

In demo mode click **Start demo session**, then **Continue**, **Load demo data** and **Evaluate**.
The demo scorer is a keyword stand-in for the model and is labelled as such in the UI.

## Develop

```powershell
pytest                       # everything (unit, property, contract, AppTest, browser)
pytest -m "not browser"      # skip the Playwright tests (no Chromium needed)
python -m playwright install chromium   # one-time, for the browser tests
ruff check . ; mypy          # lint and type-check core/
python -m scripts.make_synthetic_pdfs   # regenerate the fictional PDFs (deterministic)
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

## Manual start

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

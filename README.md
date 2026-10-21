# Agentic RFP Evaluation & Supplier Ranking

An AI-assisted Streamlit application that reads supplier RFP proposals (PDF), scores them against
configurable criteria with an LLM, benchmarks suppliers against their peers, and produces an
explainable, deterministic leaderboard.

> **Principle:** the LLM judges proposal *content*. Python owns all arithmetic, benchmarks,
> tie-breaks and ranking. Given the same validated scorecards, output is always identical.

**Status:** feature-complete for the brief and the Should tier, working end to end. Streamlit app (connect, criteria,
suppliers with live progress, leaderboard, scorecards, compare, run details, history, JSON export), long-document retrieval,
formula criteria, knock-outs, analyst overrides with an audit trail, an eval harness, and hosting support.
443 tests (24 in a real browser), 98 % core coverage, lint and strict types clean.

**Honest caveats:** the OpenAI path has not been run against the live API from this repository (it is tested with stand-ins and an
offline scorer), so scoring quality on real models is unmeasured; run `python -m core.evals --provider openai` with your key to measure it.
Self-consistency scoring (OPT-4) and the requirements compliance matrix (OPT-6) are not built.

## Try it now

```powershell
.\scripts\run_local.ps1 -Demo    # offline demo, no API key; open http://localhost:8501
.\scripts\run_local.ps1          # real run: you type your OpenAI key in the app
```

In demo mode click **Start demo session**, then **Continue**, **Load demo data** and **Evaluate**.
The demo scorer is a keyword stand-in for the model and is labelled as such in the UI.

## Develop

```powershell
pip install -r requirements-dev.txt   # runtime + test tooling
pytest                       # everything (unit, property, contract, AppTest, browser)
pytest -m "not browser"      # skip the Playwright tests (no Chromium needed)
python -m playwright install chromium   # one-time, for the browser tests
ruff check . ; mypy          # lint and type-check core/
python -m scripts.make_synthetic_pdfs   # regenerate the fictional PDFs (deterministic)
python -m core.evals --provider demo    # eval harness against the ground-truth bands
```

## Documentation

| Doc | Purpose |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | System architecture, components, data flow, security, deployment |
| [docs/DESIGN_SPEC.md](docs/DESIGN_SPEC.md) | Detailed design: schemas, prompts, formulas, UI screens, module contracts |
| [docs/TEST_CASES.md](docs/TEST_CASES.md) | Test strategy, enumerated cases and the as-built status |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Run locally and host on Streamlit Community Cloud; data handling; configuration |

## Priority tiers

- **Must:** the brief, rubric anchors, evidence verification, golden-set evals, Playwright E2E.
- **Should:** formula-based criteria, long-document retrieval fallback (proposals up to ~150 pages), analyst overrides, knock-out criteria, sensitivity panel.
- **Could:** self-consistency scoring, requirements compliance matrix.

All optional features are toggles that default off, so default behaviour matches the brief exactly.

## Hosting

See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md). On Streamlit Community Cloud each visitor gets private, in-memory data and types their own
OpenAI key; nothing is shared between visitors or stored on the server.

## Stack

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

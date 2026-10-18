# Deployment guide

Two ways to run the app: locally (recommended while developing) and on Streamlit Community Cloud.
Both work the same way for the user: they type their own OpenAI key on the first screen; the key
lives only in that browser session's memory.

## 1. Run locally

```powershell
.\scripts\run_local.ps1 -Demo     # offline demo, no key; http://localhost:8501
.\scripts\run_local.ps1           # real runs: type your OpenAI key in the app
```

Locally, runs and criteria are saved in `rfp.db` next to the app (SQLite, WAL mode).

## 2. Host on Streamlit Community Cloud

You need a GitHub account and a Streamlit Community Cloud account (free). Nobody else can do
the sign-in for you, so these are the exact steps:

1. Go to https://share.streamlit.io and sign in with GitHub. Authorise access to the
   `gopihears/rfp-evaluator` repository (it is private; Community Cloud can deploy private repos
   for their owner).
2. Click **Create app → Deploy a public app from GitHub** and fill in:
   - Repository: `gopihears/rfp-evaluator`
   - Branch: `main`
   - Main file path: `app.py`
3. Open **Advanced settings**:
   - Python version: **3.12** (the code needs 3.11 or newer).
   - Secrets: **leave empty**. No API key is stored anywhere; each visitor types their own.
   - Optional, to be explicit: `RFP_DB_MODE = "session"` (see below; it is already the default on
     any non-localhost address).
   - Do **not** set `RFP_FAKE_LLM`. That switches on the offline demo scorer.
4. Click **Deploy**. The first build installs `requirements.txt` (runtime packages only) and takes a
   few minutes. Share the `*.streamlit.app` URL afterwards.
5. In the app's **Settings → Sharing**, choose who may open it. For proposals that may be
   confidential, restrict it to named viewers.

### What happens to data on a hosted app

| Item | Behaviour |
|---|---|
| OpenAI API key | Typed by each visitor, held in that session's memory, cleared on Disconnect or tab close. Never written to disk, the database, logs, or the exported JSON. |
| Criteria, runs, results | **Session mode** (automatic on a hosted URL): each visitor gets a private in-memory database, nothing is shared between visitors and nothing is stored on the server. History is lost when the session ends, so download the JSON you want to keep. |
| Uploaded PDFs | Held in memory for the session, sent to OpenAI for scoring, never written to disk. |
| Server disk | Community Cloud storage is ephemeral anyway; a shared SQLite file there would also mix visitors' data, which is why session mode is the default off-localhost. |

### Limits to know about

- Community Cloud apps have limited memory (about 1 GB) and sleep after a period of inactivity; the
  first visitor afterwards waits for a restart.
- Several suppliers at once means several model calls at once. OpenAI rate limits for the visitor's
  key apply; the app retries with back-off and marks a supplier as failed if the limit persists.
- Proposals of up to 150 pages and 25 MB per file are accepted. Above about 60,000 tokens the app
  searches for relevant passages per criterion instead of sending the whole document.

### Updating

Push to `main`; Community Cloud redeploys automatically.

## 3. Configuration reference

Environment variables (or Cloud **Secrets**, which are exposed as environment variables):

| Variable | Default | Meaning |
|---|---|---|
| `RFP_DB_MODE` | `file` on localhost, `session` elsewhere | `session` = private in-memory data per visitor; `file` = shared SQLite file |
| `RFP_DB_PATH` | `rfp.db` | SQLite file used in `file` mode |
| `RFP_MAX_PAGES` | 150 | Page limit per PDF |
| `RFP_MAX_BYTES` | 25000000 | Size limit per PDF |
| `RFP_FULL_CONTEXT_MAX_TOKENS` | 60000 | Above this, passage retrieval is used |
| `RFP_RETRIEVAL_TOP_K` | 8 | Passages per criterion in retrieval mode |
| `RFP_MAX_SUPPLIERS` | 10 | Suppliers per run |
| `RFP_CONCURRENCY` | 3 | Suppliers scored in parallel |
| `RFP_FAKE_LLM` | unset | `1` = offline demo scorer (never on a real deployment); `keyed` = test hook |

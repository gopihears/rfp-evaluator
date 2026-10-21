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
| `RFP_EMBEDDINGS` | unset (OpenAI) | `local` = compute passage-search embeddings on this machine with sentence-transformers (see below) |
| `RFP_LOCAL_EMBED_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` | Which local embedding model to load |

## 4. Optional: local embeddings (nothing sent to OpenAI for passage search)

Only the long-document passage search uses embeddings; scoring still needs a chat model. To keep that search step local:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-local.txt
$env:RFP_EMBEDDINGS = "local"
.\scripts\run_local.ps1
```

`all-MiniLM-L6-v2` reads only about 256 word-pieces per text, so in this mode passages are about 150 tokens and four times as many are
shown per criterion. The first run downloads the model (about 90 MB). The install pulls in PyTorch, so use it locally, not on Community Cloud.
The eval harness accepts the same choice: `python -m core.evals --provider openai --model YOUR_MODEL_ID --embeddings local`.

## 5. Using OpenRouter instead of OpenAI

OpenRouter (https://openrouter.ai) gives one key for many models and offers some free ones. The Connect screen has a **Provider**
choice (OpenAI or OpenRouter); the key you type is used for that session only, exactly as with OpenAI.

- Model ids look like `vendor/model` (for example `openai/gpt-4o-mini`). Models ending in `:free` cost nothing but have tight rate limits.
- Not every routed model supports strict JSON schemas. The app asks for one first and, if the model cannot, falls back to plain JSON mode
  with the schema in the prompt; the validator and the repair attempt then handle imperfect output. Smaller or free models will fail
  the eval more often, which is exactly what the eval is for.
- OpenRouter has no embeddings endpoint here, so passage search for long proposals uses keywords, or `RFP_EMBEDDINGS=local`
  (section 4) for local semantic search.
- **Privacy:** OpenRouter forwards your documents to whichever vendor serves the model, and free models often log or train on inputs.
  Use it for testing with the fictional proposals, not for confidential bids.
- Run the eval with it:

```powershell
$env:OPENROUTER_API_KEY = Read-Host "OpenRouter API key"
.\.venv\Scripts\python.exe -m core.evals --provider openrouter --model openai/gpt-4o-mini --embeddings local --record rec.json --out report.json
Remove-Item Env:\OPENROUTER_API_KEY
```

Set `RFP_PROVIDER=openrouter` to make OpenRouter the default choice in the app.

## 6. Optional: shared, persistent history on a hosted app

By default a hosted app keeps each visitor's data private and temporary (section 2). To keep one **shared** history that every visitor
can see, add these to the app's **Secrets** (Advanced settings):

```toml
RFP_DB_MODE = "file"
RFP_DB_PATH = "/tmp/rfp.db"
```

What this means, so you can decide knowingly:

- **Everyone with the link sees every run**, including quotes taken from uploaded proposals, and can edit the shared criteria and
  weights. Only use it for non-confidential material, or restrict the app to named viewers in its sharing settings. The Connect screen
  shows a warning while this mode is on.
- **It is not permanent.** The SQLite file lives on the app server's disk. It survives while the app keeps running, but it is lost when
  the app restarts, sleeps long enough to be recycled, or is redeployed. Download the JSON of anything you must keep.
- Anyone can remove a run: **Results → Run details → Delete this run**.
- For history that survives restarts, the app would need an external database such as hosted Postgres; the code is written for SQLite today.

## 7. Saving your history on your own laptop (backup and restore)

A hosted server cannot write to your laptop, and it gets no warning before a restart, so history is kept **on your side** instead. Open the
sidebar and expand **Backup and restore**:

| Feature | What it does |
|---|---|
| **Keep my history in this browser** (on by default on a hosted app) | After every change (a finished run, an override, a deletion, saved criteria) the app stores your runs and criteria in this browser's IndexedDB. When you open the app again, even after the server restarted or was redeployed, they are restored automatically and a "Restored from this browser" message appears. |
| **Backup file (JSON)** | Downloads everything as a JSON file you can keep anywhere. |
| **Database file (SQLite)** | Downloads a real `.db` file you can open in any SQLite tool. |
| **Restore from a file** | Upload either file into any new session to bring the runs back. Runs already present are never overwritten or duplicated. |
| **Clear history saved in this browser** | Removes the browser's copy. |

Things to know:

- The browser copy belongs to **that browser on that device**. A different browser, a private window, or cleared site data starts empty; restore from a downloaded file instead.
- The saved history contains the quotes taken from your proposals. On a shared or public computer, switch **Keep my history in this browser** off and use the download buttons.
- It never contains your API key.
- Restored data is treated as untrusted input: files are size-limited and structure-checked, every run is rebuilt and re-ranked by the normal code, and a damaged file is refused with a message.
- It is the same mechanism whether the app is hosted or local; locally the SQLite file on disk already keeps your history, so browser saving is off by default there.

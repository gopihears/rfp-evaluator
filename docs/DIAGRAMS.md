# Architecture diagrams

All diagrams are [Mermaid](https://mermaid.js.org/) and render directly on GitHub. Component details are in [AGENTS.md](AGENTS.md),
the reasoning in [ARCHITECTURE.md](ARCHITECTURE.md), data definitions in [DESIGN_SPEC.md](DESIGN_SPEC.md).

Colour key used in the diagrams: **blue** = LLM involved, **green** = deterministic Python, **grey** = storage, **amber** = outside the system.

## 1. System context

Who and what talks to the app. Only the model provider is reached over the internet.

```mermaid
flowchart LR
    user(["Procurement analyst"])
    subgraph device["Analyst's laptop"]
        browser["Browser tab"]
        idb[("Browser storage<br/>IndexedDB: saved history")]
        local["Local embeddings<br/>optional, sentence-transformers"]
    end
    subgraph server["Streamlit app server"]
        app["RFP Evaluator<br/>Streamlit app + core engine"]
        db[("SQLite<br/>per-session in memory, or a shared file")]
    end
    llm["LLM provider<br/>OpenAI or OpenRouter"]

    user --> browser
    browser <-->|"WebSocket"| app
    browser <-->|"save / restore history"| idb
    app <--> db
    app -->|"prompts with proposal text,<br/>the user's own API key"| llm
    llm -->|"scores, quotes, figures"| app
    app -.->|"RFP_EMBEDDINGS=local"| local

    classDef outside fill:#fde68a,stroke:#b45309,color:#000
    classDef store fill:#e5e7eb,stroke:#6b7280,color:#000
    classDef green fill:#bbf7d0,stroke:#15803d,color:#000
    class llm,user outside
    class db,idb store
    class app green
```

## 2. Layered architecture

Dependencies point downwards only. `core/` never imports Streamlit, so the whole engine runs and is tested without a UI.

```mermaid
flowchart TB
    subgraph ui["UI layer: app.py and ui/"]
        direction LR
        connect["connect"] --- criteria["criteria"] --- suppliers["suppliers"] --- results["results"] --- backupui["backup_ui"]
    end
    subgraph engine["Engine: core/"]
        orch["orchestrator"]
        overrides["overrides"]
        backup["backup"]
        evals["evals"]
    end
    subgraph tools["Agents and tools: core/tools/"]
        direction LR
        doc["document_tool"]
        evalagent["evaluation_agent"]
        retr["retrieval"]
        val["validation_tool"]
        form["formula_scoring"]
        rank["ranking_tool"]
        sens["sensitivity"]
    end
    subgraph edge["Boundary"]
        prov["llm_provider<br/>OpenAI, OpenRouter,<br/>Fake, Replay, Recording"]
    end
    dbmod[("db.py and schema.sql<br/>SQLite")]
    models["models.py<br/>shared Pydantic types"]

    ui --> orch
    ui --> overrides
    ui --> backup
    orch --> tools
    overrides --> rank
    backup --> rank
    evals --> orch
    evalagent --> prov
    retr --> prov
    orch --> dbmod
    overrides --> dbmod
    backup --> dbmod
    tools -.-> models
    engine -.-> models

    classDef llmc fill:#bfdbfe,stroke:#1d4ed8,color:#000
    classDef det fill:#bbf7d0,stroke:#15803d,color:#000
    classDef store fill:#e5e7eb,stroke:#6b7280,color:#000
    class evalagent,prov llmc
    class doc,retr,val,form,rank,sens,orch,overrides,backup det
    class dbmod store
```

## 3. The agentic pipeline: what the model does and what Python does

The model is used in exactly two places. Everything after it is deterministic.

```mermaid
flowchart LR
    pdf["Supplier PDFs<br/>+ name, date, experience"] --> doc["Document Tool<br/>PyMuPDF text, sections"]
    doc --> mode{"Long<br/>document?"}
    mode -->|"no"| full["Evaluation Agent<br/>full context, one call"]
    mode -->|"yes"| ret["Retrieval Tool<br/>BM25 + embeddings"]
    ret --> evalr["Evaluation Agent<br/>one call per criterion"]
    doc --> facts["Fact Extraction Agent<br/>reads price and weeks only"]
    full --> val["Validation Tool<br/>schema, clip, verify quotes"]
    evalr --> val
    facts --> parse["Fact parser<br/>locale numbers, currency check"]
    val --> formula["Formula Scoring Tool<br/>figures to scores"]
    parse --> formula
    formula --> rank["Ranking Tool<br/>benchmark, gap, relative %, PPI,<br/>knock-outs, stable sort, tie-breaks"]
    rank --> sens["Sensitivity Tool<br/>weight robustness"]
    sens --> save[("Persist in one<br/>transaction")]
    save --> ui["Leaderboard, scorecards,<br/>JSON export"]
    ui --> ovr["Override Engine<br/>analyst changes, re-rank"]
    ovr --> save

    classDef llmc fill:#bfdbfe,stroke:#1d4ed8,color:#000
    classDef det fill:#bbf7d0,stroke:#15803d,color:#000
    classDef store fill:#e5e7eb,stroke:#6b7280,color:#000
    class full,evalr,facts llmc
    class doc,ret,val,parse,formula,rank,sens,ovr det
    class save store
```

## 4. One evaluation run, step by step

```mermaid
sequenceDiagram
    autonumber
    actor A as Analyst
    participant UI as Streamlit UI
    participant O as Orchestrator
    participant W as Worker thread
    participant D as Document Tool
    participant E as Evaluation Agent
    participant P as Provider
    participant V as Validation Tool
    participant R as Ranking Tool
    participant S as SQLite

    A->>UI: Evaluate suppliers
    UI->>O: run_evaluation(uploads, options)
    O->>S: load active criteria, check weights
    O->>S: start run (status RUNNING)
    par for each supplier, up to 3 at once
        O->>W: submit supplier
        W->>D: extract(pdf bytes)
        D-->>W: text with page markers, sections
        W->>E: evaluate(criteria, text)
        E->>P: complete_json(prompt, strict schema)
        P-->>E: JSON answer
        E->>V: validate(answer, source text)
        V-->>E: scorecard + warnings
        W-->>O: scorecard, progress events
    end
    O-->>UI: progress events (live)
    O->>R: rank(scorecards, suppliers, criteria)
    R-->>O: ranked run, tie-break explanations
    O->>S: persist results in ONE transaction
    O-->>UI: RunOutcome (leaderboard, export)
    UI-->>A: leaderboard and scorecards
```

## 5. Evaluation Agent: choosing a mode and recovering from bad output

```mermaid
flowchart TD
    start(["Supplier text received"]) --> size{"Estimated tokens above<br/>full-context limit<br/>or retrieval forced?"}
    size -->|"no"| one["Build one prompt:<br/>all criteria + whole document"]
    size -->|"yes"| per["For each criterion:<br/>retrieve best passages"]
    per --> onec["Build a prompt with one criterion<br/>and its passages"]
    one --> ask["Call model with<br/>strict JSON schema, temperature 0"]
    onec --> ask
    ask --> err{"Provider error?"}
    err -->|"auth, quota, rate, timeout"| fail1["Supplier FAILED<br/>with error code"]
    err -->|"no"| parse{"Valid JSON?"}
    parse -->|"yes"| ok["Validation Tool"]
    parse -->|"no"| rep["Repair attempt:<br/>same prompt + reminder"]
    rep --> parse2{"Valid JSON now?"}
    parse2 -->|"yes"| ok
    parse2 -->|"no"| fail2["E_LLM_JSON<br/>criterion or supplier failed"]
    ok --> done(["Scorecard with warnings"])

    classDef bad fill:#fecaca,stroke:#b91c1c,color:#000
    classDef good fill:#bbf7d0,stroke:#15803d,color:#000
    class fail1,fail2 bad
    class ok,done good
```

## 6. Long-document retrieval

```mermaid
flowchart LR
    text["Document text<br/>with page markers"] --> chunk["Chunker<br/>~600 tokens, tables kept whole,<br/>section label per chunk"]
    chunk --> bm25["BM25 index<br/>stemmed terms"]
    chunk --> emb["Embeddings<br/>OpenAI or local MiniLM<br/>optional"]
    crit["Criterion<br/>name + description + rubric"] --> q["Query<br/>deduplicated terms"]
    q --> bm25
    q --> emb
    bm25 --> fuse["Reciprocal-rank fusion"]
    emb --> fuse
    fuse --> pick["Top-k chunks<br/>+ opening chunk + pricing chunk"]
    pick --> order["Sort by page"]
    order --> prompt["Prompt for one criterion"]
    bm25 -.->|"no match at all"| weak["W_LOW_RETRIEVAL<br/>confidence low"]

    classDef det fill:#bbf7d0,stroke:#15803d,color:#000
    class chunk,bm25,emb,fuse,pick,order,q det
```

## 7. Ranking algorithm

```mermaid
flowchart TD
    sc["Validated scorecards"] --> w["Normalise weights to 100"]
    w --> ko{"Knock-outs enabled?"}
    ko -->|"yes"| dq["Disqualify suppliers below a<br/>mandatory minimum"]
    ko -->|"no"| elig
    dq --> elig["Eligible suppliers"]
    elig --> bench["Benchmark per criterion:<br/>highest valid score"]
    bench --> abs["Absolute score =<br/>sum of score/max x weight"]
    bench --> rel["Relative % =<br/>score / benchmark x 100<br/>zero benchmark gives 100"]
    rel --> ppi["PPI = weighted average<br/>of relative %"]
    abs --> sort
    ppi --> sort["Stable sort by:<br/>1 higher PPI<br/>2 earlier submission date<br/>3 higher experience<br/>4 name A to Z<br/>5 supplier id"]
    sort --> ranks["Sequential ranks 1..n"]
    ranks --> why["Tie-break explanation<br/>for each adjacent pair"]
    dq --> out
    why --> out["RankedRun: ranked,<br/>disqualified, failed"]
```

## 8. Data model

```mermaid
erDiagram
    evaluation_criteria {
        int criterion_id PK
        text name
        real weight
        int max_score
        int is_active
        text rubric_json
        int is_mandatory
        int min_score
        text criterion_type
        text formula_kind
    }
    rfp_runs {
        text rfp_run_id PK
        text created_at
        text status
        text model
        text prompt_hash
        text options_json
        text export_json
    }
    run_criteria_snapshot {
        text rfp_run_id FK
        int criterion_id
        real weight
    }
    supplier_results {
        text rfp_run_id FK
        text supplier_id
        text supplier_name
        text status
        real absolute_score
        real ppi
        int final_rank
        text result_json
    }
    criterion_scores {
        text rfp_run_id FK
        text supplier_id FK
        int criterion_id
        int score
        int benchmark
        int gap
        real relative_pct
    }
    run_warnings {
        int id PK
        text rfp_run_id FK
        text code
        text severity
        text message
    }
    score_overrides {
        int id PK
        text rfp_run_id FK
        text supplier_id
        int criterion_id
        int original_score
        int new_score
        text reason
    }
    rfp_runs ||--|{ run_criteria_snapshot : "criteria used"
    rfp_runs ||--|{ supplier_results : "has"
    supplier_results ||--|{ criterion_scores : "scored on"
    rfp_runs ||--o{ run_warnings : "raised"
    rfp_runs ||--o{ score_overrides : "audit trail"
    evaluation_criteria ||--o{ run_criteria_snapshot : "copied at run start"
```

## 9. Status lifecycles

```mermaid
stateDiagram-v2
    direction LR
    state "Run" as run {
        [*] --> RUNNING
        RUNNING --> COMPLETED: all suppliers scored
        RUNNING --> PARTIAL: some suppliers failed
        RUNNING --> FAILED: none scored, cancelled or error
    }
    state "Supplier in a run" as sup {
        [*] --> RANKED
        [*] --> FAILED_S: unreadable PDF or model error
        [*] --> DISQUALIFIED: below a knock-out minimum
        DISQUALIFIED --> RANKED: analyst override lifts the score
    }
```

## 10. API key handling

The key exists only in the visitor's session memory.

```mermaid
sequenceDiagram
    actor U as Visitor
    participant B as Browser
    participant S as Streamlit session memory
    participant P as Provider object
    participant L as LLM provider

    U->>B: types key in password field
    B->>S: form submit
    S->>P: create provider with the key
    P->>L: list models (validates the key)
    L-->>P: model list
    P-->>S: provider kept, only a masked hint is shown
    Note over S,P: Never written to the database, exports, logs, backups or the URL.<br/>Errors pass through scrub() which masks key-like strings.
    U->>B: Disconnect or close tab
    B->>S: session ends
    S--xP: provider and key discarded
```

## 11. Where data lives: local versus hosted

```mermaid
flowchart TB
    subgraph local["Running on your own machine"]
        l_app["Streamlit app"] --> l_db[("rfp.db file<br/>kept between runs")]
    end
    subgraph hosted["Hosted on Streamlit Community Cloud"]
        direction TB
        v1["Visitor A session"] --> m1[("In-memory SQLite A<br/>private")]
        v2["Visitor B session"] --> m2[("In-memory SQLite B<br/>private")]
        shared["Optional: RFP_DB_MODE=file<br/>one shared file, everyone sees all runs"]
    end
    m1 <-.->|"auto save and restore"| b1[("Visitor A browser storage")]
    m2 <-.->|"auto save and restore"| b2[("Visitor B browser storage")]

    classDef store fill:#e5e7eb,stroke:#6b7280,color:#000
    classDef warn fill:#fde68a,stroke:#b45309,color:#000
    class l_db,m1,m2,b1,b2 store
    class shared warn
```

## 12. Backup and restore

```mermaid
sequenceDiagram
    autonumber
    participant UI as Streamlit UI
    participant C as Browser component
    participant IDB as IndexedDB in the browser
    participant K as core/backup.py
    participant S as Session SQLite

    Note over UI,IDB: New session starts
    UI->>C: load
    C->>IDB: read saved history
    IDB-->>C: bundle JSON, or nothing
    C-->>UI: reply
    UI->>K: parse_bundle (size, structure checks)
    K->>K: rebuild each run and re-rank it
    K->>S: insert runs, skip ones already present
    UI-->>UI: "Restored from this browser"

    Note over UI,IDB: After any change
    UI->>K: export_bundle
    K->>S: read runs and criteria
    UI->>C: save(bundle)
    C->>IDB: write

    Note over UI,K: Manual files
    UI->>K: sqlite_bytes or bundle_json for download
    UI->>K: uploaded .json or .db is parsed and imported
```

## 13. Analyst override

```mermaid
flowchart TD
    open["Open a scorecard"] --> pick["Pick a criterion,<br/>enter new score and a reason"]
    pick --> check{"Valid?<br/>score in range, reason given,<br/>supplier scored, run not failed"}
    check -->|"no"| msg["Show the reason; nothing is changed"]
    check -->|"yes"| rebuild["Rebuild inputs from the saved run"]
    rebuild --> apply["Apply all overrides<br/>latest per cell wins"]
    apply --> rerank["Ranking Tool re-ranks<br/>same deterministic code"]
    rerank --> tx["One transaction:<br/>rewrite results, append audit row"]
    tx --> show["Leaderboard shows an edited badge;<br/>Run details lists the audit trail"]
    show -.-> revert["Revert sets the model score back,<br/>logged as a new change"]
    revert -.-> check

    classDef good fill:#bbf7d0,stroke:#15803d,color:#000
    classDef bad fill:#fecaca,stroke:#b91c1c,color:#000
    class tx,show good
    class msg bad
```

## 14. Screen navigation

```mermaid
stateDiagram-v2
    [*] --> Connect
    Connect --> Criteria: key accepted
    Criteria --> Suppliers: criteria saved and valid
    Suppliers --> Results: evaluation finished
    Results --> Suppliers: New evaluation
    Suppliers --> Criteria: Back to criteria
    Results --> Results: open scorecard, compare, run details
    Results --> Suppliers: Delete this run
    Criteria --> Connect: Disconnect
    Suppliers --> Connect: Disconnect
    Results --> Connect: Disconnect
    note right of Results
        Past runs in the sidebar reopen
        any saved run from here
    end note
```

## 15. How the system is tested

```mermaid
flowchart LR
    subgraph fast["Every commit, no network, no browser"]
        unit["Unit tests<br/>formulas, validation, parsing"]
        prop["Property tests<br/>Hypothesis invariants"]
        contract["Contract tests<br/>whole pipeline with a fake model"]
        apptest["Streamlit AppTest<br/>screen logic and gating"]
    end
    subgraph browser["Real browser, offline model"]
        pw["Playwright on Chromium<br/>full flows, downloads, axe a11y,<br/>mobile viewport, backup restore"]
    end
    subgraph quality["Model quality"]
        ev["Eval harness<br/>ground-truth bands, grounding,<br/>injection, metamorphic"]
        live["Live run with a real key<br/>record once, replay free"]
    end
    unit --> prop --> contract --> apptest --> pw
    ev --> live
    contract -.-> ev

    classDef det fill:#bbf7d0,stroke:#15803d,color:#000
    classDef llmc fill:#bfdbfe,stroke:#1d4ed8,color:#000
    class unit,prop,contract,apptest,pw det
    class ev,live llmc
```

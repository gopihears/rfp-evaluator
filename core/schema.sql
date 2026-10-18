CREATE TABLE IF NOT EXISTS evaluation_criteria (
  criterion_id        INTEGER PRIMARY KEY,
  name                TEXT NOT NULL UNIQUE,
  description         TEXT NOT NULL DEFAULT '',
  weight              REAL NOT NULL CHECK (weight >= 0),
  max_score           INTEGER NOT NULL DEFAULT 10 CHECK (max_score >= 1),
  is_active           INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
  rubric_json         TEXT,
  is_mandatory        INTEGER NOT NULL DEFAULT 0 CHECK (is_mandatory IN (0, 1)),
  min_score           INTEGER,
  criterion_type      TEXT NOT NULL DEFAULT 'llm_judged' CHECK (criterion_type IN ('llm_judged', 'formula')),
  formula_kind        TEXT,
  formula_params_json TEXT
);

CREATE TABLE IF NOT EXISTS rfp_runs (
  rfp_run_id     TEXT PRIMARY KEY,
  created_at     TEXT NOT NULL,
  status         TEXT NOT NULL CHECK (status IN ('RUNNING', 'COMPLETED', 'PARTIAL', 'FAILED')),
  model          TEXT,
  prompt_hash    TEXT,
  schema_version TEXT,
  options_json   TEXT,
  export_json    TEXT
);

CREATE TABLE IF NOT EXISTS run_criteria_snapshot (
  rfp_run_id          TEXT NOT NULL REFERENCES rfp_runs(rfp_run_id) ON DELETE CASCADE,
  criterion_id        INTEGER NOT NULL,
  name                TEXT NOT NULL,
  description         TEXT NOT NULL,
  weight              REAL NOT NULL,
  max_score           INTEGER NOT NULL,
  rubric_json         TEXT,
  is_mandatory        INTEGER NOT NULL DEFAULT 0,
  min_score           INTEGER,
  criterion_type      TEXT NOT NULL DEFAULT 'llm_judged',
  formula_kind        TEXT,
  formula_params_json TEXT,
  PRIMARY KEY (rfp_run_id, criterion_id)
);

CREATE TABLE IF NOT EXISTS supplier_results (
  rfp_run_id        TEXT NOT NULL REFERENCES rfp_runs(rfp_run_id) ON DELETE CASCADE,
  supplier_id       TEXT NOT NULL,
  supplier_name     TEXT NOT NULL,
  submission_date   TEXT NOT NULL,
  experience_rating REAL NOT NULL,
  status            TEXT NOT NULL CHECK (status IN ('RANKED', 'FAILED', 'DISQUALIFIED')),
  absolute_score    REAL,
  ppi               REAL,
  final_rank        INTEGER,
  result_json       TEXT NOT NULL,
  PRIMARY KEY (rfp_run_id, supplier_id)
);

CREATE TABLE IF NOT EXISTS criterion_scores (
  rfp_run_id            TEXT NOT NULL,
  supplier_id           TEXT NOT NULL,
  criterion_id          INTEGER NOT NULL,
  score                 INTEGER,
  benchmark             INTEGER,
  gap                   INTEGER,
  relative_pct          REAL,
  weighted_contribution REAL,
  status                TEXT,
  evidence_verified     INTEGER,
  PRIMARY KEY (rfp_run_id, supplier_id, criterion_id),
  FOREIGN KEY (rfp_run_id, supplier_id) REFERENCES supplier_results(rfp_run_id, supplier_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS run_warnings (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  rfp_run_id   TEXT NOT NULL REFERENCES rfp_runs(rfp_run_id) ON DELETE CASCADE,
  supplier_id  TEXT,
  criterion_id INTEGER,
  code         TEXT NOT NULL,
  severity     TEXT NOT NULL,
  message      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS score_overrides (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  rfp_run_id     TEXT NOT NULL REFERENCES rfp_runs(rfp_run_id) ON DELETE CASCADE,
  supplier_id    TEXT NOT NULL,
  criterion_id   INTEGER NOT NULL,
  original_score INTEGER,
  new_score      INTEGER NOT NULL,
  reason         TEXT NOT NULL,
  created_at     TEXT NOT NULL
);

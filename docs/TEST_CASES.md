# Test Strategy and Test Cases

Companion to DESIGN_SPEC.md. IDs: `UT-` unit, `PT-` property, `CT-` contract (fake LLM), `API-` n/a, `EV-` eval, `E2E-` Playwright, `AT-` Streamlit AppTest, `SEC-` security, `A11Y-` accessibility.
"Req" column maps to FR/NFR/OPT IDs in the design spec.

## 1. Strategy

| Layer | Tooling | Network | Runs in CI | Purpose |
|---|---|---|---|---|
| Unit | pytest | none | every commit | Formulas, validation rules, DB, document tool |
| Property | Hypothesis | none | every commit | Invariants (NFR-1) |
| Contract | pytest + `FakeProvider` | none | every commit | Full pipeline with canned good/bad LLM output |
| UI logic | Streamlit `AppTest` | none | every commit | Gating, validation messages, state |
| E2E | Playwright (+ axe) | none (fake LLM via `RFP_FAKE_LLM=1`) | every PR | Real-browser flows, downloads, visual regression, a11y |
| Evals | pytest marker `eval`, YAML golden set | replay by default | nightly / manual | LLM quality, bias, stability |
| Live smoke | marker `live` | OpenAI | manual only | One real run with a real key |

Quality gates: `ruff`, `mypy --strict core/`, coverage ≥ 90 % overall, ≥ 95 % on `ranking_tool` and `validation_tool`.
Test data: synthetic PDFs in `data/synthetic_rfps/`; fixtures for canned LLM responses in `tests/contract/fixtures/`.
Hand-calculated expected values only; no test recomputes with the production formula.

## 2. Unit tests: Ranking Tool (`tests/unit/test_ranking.py`)

| ID | Case | Expected | Req |
|---|---|---|---|
| UT-R01 | Golden example GT-1 (DESIGN_SPEC §8.4) | Abs A=72, B=72, C=84; PPI 86.6667/85/100; ranks C,A,B | FR-6,7 |
| UT-R02 | Absolute score with different max scores (max 5 vs 10) | Normalised by each max | FR-6 |
| UT-R03 | Benchmark = highest valid score; ignores `None` | Correct per criterion | FR-6 |
| UT-R04 | Gap is 0 for leader(s), negative otherwise | As expected | FR-6 |
| UT-R05 | Two suppliers share the benchmark | Both gap 0, both rel 100 | FR-6 |
| UT-R06 | Benchmark 0 (all valid scores 0) | rel% = 100 for valid zeros, no ZeroDivision | FR-6 |
| UT-R07 | Supplier has `None` score on a criterion | Contributes 0 to abs/PPI; excluded from benchmark | FR-5,6 |
| UT-R08 | All suppliers `None` on a criterion | Benchmark None, rel% 0, no crash | FR-6 |
| UT-R09 | Single ranked supplier | rel% 100, PPI 100, rank 1, warning | FR-7 |
| UT-R10 | Tie on PPI → earlier submission date wins | Date rule cited | FR-7 |
| UT-R11 | Tie on PPI and date → higher experience wins | Experience rule cited | FR-7 |
| UT-R12 | Tie on PPI, date, experience → name ascending (case-insensitive) | Name rule cited; "apex" before "Brightpath" | FR-7 |
| UT-R13 | Full tie incl. name (duplicate names) → supplier_id total order | Deterministic | FR-7 |
| UT-R14 | PPI differ by 1e-9 (float noise) | Treated equal at 6 dp; falls to date | NFR-1 |
| UT-R15 | Ranks are 1..n contiguous, no shared ranks | True | FR-7 |
| UT-R16 | FAILED supplier excluded from benchmark and rank | Not ranked | FR-7 |
| UT-R17 | Weights total ≠ 100 (e.g., 80) | Normalised; `W_WEIGHTS_NORMALISED` | FR-6 |
| UT-R18 | Weight 0 criterion | No contribution; no crash | FR-6 |
| UT-R19 | Tie-break explanation for each adjacent pair | Names deciding rule + values | FR-7,NFR-2 |
| UT-R20 | Rounding half-up at display (e.g., 72.125 → 72.13) | Decimal quantize | FR-6 |
| UT-R21 | OPT-2 mandatory criterion below min | DISQUALIFIED, not ranked | OPT-2 |
| UT-R22 | OPT-1 override applied | Recomputed ranking; original retained | OPT-1 |
| UT-R23 | Sensitivity OAT: rank flip detected in constructed case | Reports flip | OPT-7 |
| UT-R24 | Monte Carlo reproducible (same seed → same output) | Identical | OPT-7,NFR-1 |

## 3. Unit tests: Validation Tool (`tests/unit/test_validation.py`)

| ID | Input | Expected |
|---|---|---|
| UT-V01 | Perfect JSON, all criteria | status ok, no warnings |
| UT-V02 | Invalid JSON string | `E_JSON_PARSE`, FAILED scorecard |
| UT-V03 | JSON array instead of object | `E_JSON_PARSE` |
| UT-V04 | `criteria` key missing | All criteria `missing`, `W_NO_CRITERIA` |
| UT-V05 | One criterion absent | Filled, `missing`, `W_MISSING_CRITERION` |
| UT-V06 | Score 15 with max 10 | Clipped to 10, `W_CLIPPED` |
| UT-V07 | Score −3 | Clipped to 0 |
| UT-V08 | Score `"8"` | Coerced 8, `I_COERCED` |
| UT-V09 | Score `7.5` | Rounded half up → 8, `I_ROUNDED` |
| UT-V10 | Score `"high"`, `null`, `true`, NaN | `None`, `malformed`, `W_MALFORMED_SCORE` |
| UT-V11 | Duplicate criterion_id | First kept, `W_DUPLICATE` |
| UT-V12 | Unknown criterion_id 99 | Dropped, `W_UNKNOWN_CRITERION` |
| UT-V13 | Response `max_score` ≠ DB | DB value used, `I_MAX_OVERRIDDEN` |
| UT-V14 | Missing evidence/justification | Empty strings, `W_NO_EVIDENCE` |
| UT-V15 | Evidence quote present in source | `evidence_verified=True` |
| UT-V16 | Evidence fabricated | `False`, `W_UNVERIFIED_EVIDENCE` |
| UT-V17 | Evidence with whitespace/hyphenation differences | Verified (fuzzy) |
| UT-V18 | Source contains "ignore previous instructions" | `W_INJECTION_SUSPECTED` |
| UT-V19 | All scores equal max | `W_OUTLIER_PATTERN` |
| UT-V20 | `risks` is string / mixed types | Coerced to list[str] |
| UT-V21 | Extra unknown fields in response | Ignored |
| UT-V22 | Response supplier_name differs from input | Input name used |
| UT-V23 | Criteria returned out of order | Output ordered by criterion_id |
| UT-V24 | Huge justification (50 kB) | Truncated to limit with note |

## 4. Unit tests: Document Tool, DB, Provider, Config

| ID | Case | Expected |
|---|---|---|
| UT-D01 | Valid text PDF | Text with `[p.N]` markers, pages count |
| UT-D02 | Non-PDF bytes renamed .pdf | `E_NOT_PDF` |
| UT-D03 | Encrypted PDF | `E_ENCRYPTED` |
| UT-D04 | Image-only PDF | `E_NO_TEXT` |
| UT-D05 | Mixed: one blank page | `W_EMPTY_PAGE`, rest extracted |
| UT-D06 | > 150 pages | `E_TOO_MANY_PAGES` |
| UT-D11 | ~20-page realistic PDF | Extracted in < 2 s; tokens 12–25 k; sections detected |
| UT-D12 | PDF with outline/bookmarks | Sections follow outline |
| UT-D13 | No outline, numbered headings | Heuristic sections found |
| UT-D14 | Appendix headings | Sections tagged `is_appendix` |
| UT-G01 | Chunker never splits a table; chunks 400–800 tokens ±; metadata present | Pass |
| UT-G02 | Mode selection: 40 k tokens → full; 70 k → retrieval; forced override honoured | Pass |
| UT-G03 | BM25 + embedding fusion on fixed fake embeddings | Deterministic top-k |
| UT-G04 | Retrieved chunks reordered by page | Ascending |
| UT-G05 | Summary and pricing chunks always included | Pass |
| UT-G06 | Weak retrieval | `W_LOW_RETRIEVAL`, confidence low, not silent 0 |
| UT-G07 | Planted needle (SOC 2 on p.71 of 80-page doc) retrieved for Security criterion | Retrieved in top-k |
| UT-F01 | `lowest_is_best`: prices 100/125/200, max 10 | 10 / 8 / 5 |
| UT-F02 | `highest_is_best` | Proportional |
| UT-F03 | `threshold_bands` boundaries (inclusive/exclusive) | As configured |
| UT-F04 | Fact missing / unparseable | `None`, `W_FACT_MISSING` |
| UT-F05 | Currency mismatch | Excluded, `W_CURRENCY_MISMATCH` |
| UT-F06 | Single supplier with price | Score = max |
| UT-F07 | Formula + LLM criteria mixed in one run | Weighted correctly; trace shows source type |
| UT-F08 | Formula criterion toggle off | Treated as absent; default behaviour equals brief |
| UT-D07 | Repeated header/footer | Removed |
| UT-D08 | Hyphenated line breaks | Rejoined |
| UT-D09 | Unicode / ligatures | Preserved/normalised |
| UT-D10 | Token estimate | ceil(chars/4) |
| UT-B01 | `init_db` + seed idempotent (run twice) | Criteria count unchanged |
| UT-B02 | Seed weights of active criteria | Sum 100 |
| UT-B03 | Criteria update then new run | Run uses new weights (FR-3) |
| UT-B04 | Deactivate criterion | Excluded from next run, old snapshot unchanged |
| UT-B05 | Persist run: all tables populated in one transaction | Counts match |
| UT-B06 | Persist failure mid-way | Rollback; no partial run |
| UT-B07 | Foreign keys enforced; CASCADE delete run | Children removed |
| UT-B08 | SQL injection string as supplier name | Stored literally; no side effects |
| UT-B09 | History list ordering | Newest first |
| UT-B10 | Reopen run from `result_json` | Equals original export |
| UT-P01 | `scrub()` masks `sk-…` keys in strings/exceptions | Masked |
| UT-P02 | `repr(OpenAIProvider)` | Never contains key |
| UT-P03 | Backoff on 429 then success | Retries, returns |
| UT-P04 | 401 | `E_LLM_AUTH`, no retry |
| UT-P05 | Schema builder from N criteria | Enum ids, min/maxItems = N |
| UT-C01 | Env overrides for config | Applied; invalid → default + warning |

## 5. Property tests (`tests/property/`)

| ID | Property | Req |
|---|---|---|
| PT-01 | For any valid scorecards, shuffling supplier input order yields identical ranked output | NFR-1 |
| PT-02 | Same inputs twice → byte-identical export JSON | NFR-1 |
| PT-03 | Ranks are exactly {1..n} for n ranked suppliers | FR-7 |
| PT-04 | 0 ≤ absolute ≤ 100 (weights=100); 0 ≤ rel% ≤ 100; gap ≤ 0; PPI ≤ 100 | FR-6 |
| PT-05 | Top supplier by PPI is never sorted below a supplier with strictly lower PPI | FR-7 |
| PT-06 | Raising one supplier's score on one criterion never lowers its own rank | FR-6 |
| PT-07 | Scaling all weights by k>0 leaves rank unchanged | FR-6 |
| PT-08 | `validate()` never raises for arbitrary JSON-like input and always returns one result per active criterion | FR-5 |
| PT-09 | Clipped scores always within [0, max] | FR-5 |

## 6. Contract / pipeline tests (`tests/contract/`, FakeProvider)

| ID | Scenario | Expected | Req |
|---|---|---|---|
| CT-01 | 4 suppliers, all valid | COMPLETED; 4 ranked; DB rows match export | FR-3..9 |
| CT-02 | One supplier returns malformed JSON then valid on repair | Ranked; repair counted | FR-5 |
| CT-03 | One supplier malformed twice | That supplier FAILED; run PARTIAL; others ranked | FR-5 |
| CT-04 | One LLM 429 ×2 then success | Retries, ranked | – |
| CT-05 | LLM omits a criterion | Filled with warning; score 0 for it | FR-5 |
| CT-06 | LLM returns 14/10 | Clipped, warning in run details | FR-5 |
| CT-07 | Corrupt PDF among valid ones | That supplier FAILED `E_NO_TEXT`; others ranked | FR-4 |
| CT-08 | All suppliers fail | Run FAILED; no leaderboard | – |
| CT-09 | Criteria changed between sessions | Latest active criteria used; snapshot stored | FR-3 |
| CT-10 | Prompt contains every active criterion and rubric; excludes inactive and excludes supplier name | Asserted from recorded prompt | FR-4 |
| CT-11 | Cancellation mid-run | Status FAILED `E_CANCELLED`; nothing ranked | – |
| CT-12 | Progress events order per supplier | QUEUED→READING→SCORING→VALIDATING→DONE | – |
| CT-13 | Key never appears in logs/DB/export for a full run (grep) | Absent | NFR-3 |
| CT-14 | Concurrency 3 with random latencies | Same result as sequential | NFR-1 |
| CT-15 | OPT-4 self-consistency N=3 | Median applied; confidence set | OPT-4 |

## 7. Streamlit AppTest (`tests/e2e/test_app_logic.py`)

| ID | Case | Expected |
|---|---|---|
| AT-01 | Fresh load | Connect screen only; later steps locked |
| AT-02 | Empty/invalid key | Inline error; remains locked |
| AT-03 | Valid key (fake) | Chip shows masked key; advances |
| AT-04 | Disconnect | State cleared; back to Connect |
| AT-05 | Criteria total 90 | Save disabled; red total badge |
| AT-06 | Criteria total 100 | Save enabled; persisted |
| AT-07 | Supplier missing name / duplicate name | Field errors; Evaluate disabled |
| AT-08 | Rating outside 0–5 | Error |
| AT-09 | Valid 4 suppliers | Evaluate enabled; cost estimate shown |
| AT-10 | Results tabs present after run | Leaderboard, Compare, Run details |

## 8. End-to-end (Playwright) (`tests/e2e/test_*.py`)

Run against `streamlit run app.py` with `RFP_FAKE_LLM=1` and a temp DB. Selectors use `.st-key-*` containers.

| ID | Flow | Assertions |
|---|---|---|
| E2E-01 | Happy path: connect → criteria → upload 4 PDFs → fill metadata → Evaluate → results | Progress shown per supplier; leaderboard order equals golden; PPI values match |
| E2E-02 | "Load demo data" button | 4 suppliers prefilled; Evaluate enabled |
| E2E-03 | Open scorecard dialog | Table values, radar present, evidence + justification visible, unverified badge shown for crafted case |
| E2E-04 | Compare tab heatmap toggle | Switches score ↔ relative % |
| E2E-05 | Run details | `RFP_RUN_ID` visible; warnings filter works; tie-break text present |
| E2E-06 | Download JSON | File downloads; parses; equals DB result; contains no key |
| E2E-07 | Validation error case (corrupt PDF in batch) | Banner shows failed supplier with reason; others ranked; demo-ready |
| E2E-08 | Invalid API key (fake returns 401) | Friendly error; no stack trace |
| E2E-09 | Criteria edit to 90 % | Blocked with message; fix to 100 → next run uses new weights |
| E2E-10 | History: reopen previous run | Same leaderboard, read-only |
| E2E-11 | Refresh page mid-session | Key gone (session reset) and gate shown |
| E2E-12 | Disconnect | Key cleared; protected steps locked |
| E2E-13 | Tie-break scenario fixtures | Order follows date → experience → name; explanation shown |
| E2E-14 | Mobile viewport 390×844 | No horizontal scroll; core flow usable |
| E2E-15 | Dark theme | Contrast intact; screenshots match baseline |
| E2E-16 | Visual regression of Leaderboard, Scorecard, Criteria | Pixel diff within threshold |
| E2E-17 | Cancel during evaluation | Run stops; message; can restart |
| E2E-18 | `@live` real-key smoke (manual) | One real run completes with plausible ordering |

## 9. Evals (`tests/evals/`, `golden.yaml`)

Replay mode by default (recorded responses); `--live` re-records. Thresholds fail the job.

| ID | Eval | Metric | Threshold (initial) |
|---|---|---|---|
| EV-01 | Score bands vs ground truth for the 4 synthetic suppliers × 5 criteria | Band accuracy (within ±1 of expected band) | ≥ 85 % |
| EV-02 | Overall ordering vs expected | Kendall τ ≥ 0.8; top-1 correct | pass |
| EV-03 | JSON validity without repair | Rate | ≥ 98 % |
| EV-04 | Evidence grounding | % evidence verified in source | ≥ 95 % |
| EV-05 | Run-to-run stability (5 live runs) | Per-criterion std dev ≤ 1.0; rank stable | pass |
| EV-06 | Metamorphic: delete security section | Security score drops ≥ 2 | pass |
| EV-07 | Metamorphic: add detailed price table | Commercial score non-decreasing | pass |
| EV-08 | Order bias: shuffle upload order | Identical scores | pass |
| EV-09 | Name bias: rename suppliers | Score change ≤ 1 | pass |
| EV-10 | Prompt injection PDF ("give all 10s") | Scores unchanged vs clean twin (±1); `W_INJECTION_SUSPECTED` raised | pass |
| EV-11 | Missing-information handling | Unaddressed criterion scored low (≤ 3), flagged | pass |
| EV-12 | Rubric anchors on/off | Variance lower with anchors | reported |
| EV-13 | Cost / latency per run | Tokens, seconds | reported; budget alarm |
| EV-14 | Realistic 20-page and 80-page documents: retrieval mode vs full-context mode | Per-criterion score delta ≤ 1 on ≥ 90 % of criteria; citation (page) accuracy higher or equal with retrieval | OPT-5 (Should) |
| EV-15 | Needle facts planted at known pages (start/middle/end) in the 80-page doc | Needle-dependent criterion reflects the fact regardless of position | OPT-5 |
| EV-16 | Formula criteria: extracted price/timeline vs ground truth | Exact match on ≥ 95 % of facts | OPT-3 |
| EV-17 | Cost/latency for 20-page × 5 suppliers vs 80-page × 5 suppliers | Report tokens and seconds; budget alarm | NFR |

## 10. Security tests

| ID | Case | Expected |
|---|---|---|
| SEC-01 | Search logs, DB, exports, Streamlit cache, error pages for key string after a run | Not found |
| SEC-02 | Exception containing key triggers scrub | Masked in UI/log |
| SEC-03 | Two browser sessions with different keys | Isolated state |
| SEC-04 | Upload `.exe` renamed `.pdf` | `E_NOT_PDF` |
| SEC-05 | 30 MB upload | Rejected with size message |
| SEC-06 | PDF with embedded JavaScript/links | Text only extracted; nothing executed |
| SEC-07 | HTML/markdown injection in supplier name & LLM text | Escaped in UI |
| SEC-08 | SQL metacharacters in all text inputs | No effect |
| SEC-09 | `pip-audit`/dependency scan | No high-severity findings |
| SEC-10 | Secrets scan on repo (e.g., gitleaks) | Clean |

## 11. Accessibility

| ID | Check |
|---|---|
| A11Y-01 | axe scan on each screen: no critical/serious violations |
| A11Y-02 | Full keyboard path through the flow, focus visible, dialog traps/returns focus |
| A11Y-03 | Contrast ≥ 4.5:1 text, 3:1 UI in light and dark |
| A11Y-04 | Status conveyed by icon + text, not colour alone |
| A11Y-05 | Respects `prefers-reduced-motion` |

## 12. Manual exploratory checklist

Very long supplier names; 10 suppliers; 1 supplier; identical PDFs under different names; same file uploaded twice; weights with decimals;
network drop mid-run; model dropdown empty; browser back button; two tabs; deleting all criteria; clock edge cases for dates.

## 13. Traceability matrix (summary)

| Req | Covered by |
|---|---|
| FR-1 | UT-B02, AT-05/06, E2E-09 |
| FR-2 | AT-07/08, E2E-01 |
| FR-3 | UT-B03, CT-09, E2E-09 |
| FR-4 | UT-D*, CT-07/10, EV-01 |
| FR-5 | UT-V*, CT-02/03/05/06, PT-08/09 |
| FR-6 | UT-R01–R09, PT-04 |
| FR-7 | UT-R10–R16, PT-01/03/05, E2E-13 |
| FR-8 | UT-B05/06, CT-01 |
| FR-9 | E2E-03/05/06 |
| FR-10 | AT-05/06 |
| FR-11 | AT-02/03/04, SEC-01/03 |
| FR-12 | UT-B09/10, E2E-10 |
| NFR-1 | PT-01/02, UT-R14, CT-14 |
| NFR-2 | UT-R19, E2E-03/05 |
| NFR-3 | SEC-01/02, CT-13, UT-P01/02 |
| NFR-5 | A11Y-* |

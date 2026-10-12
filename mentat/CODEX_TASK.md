# Codex task: Mentat R pipeline — skeleton + crypto test path

Read `mentat/MENTAT_SEQUENCING_v2.md` first (the governing spec; decisions and field names in it are
final). Build the R implementation described below inside `mentat/`. Input files are in
`mentat/inputs/`. Target: R 4.6.0 on Windows, run from RStudio or `Rscript`.

## Scope of THIS task
Phase 0 (ingest) + the Phase 1/2 collector + fixture reconciliation + reporting — wired for a
**crypto-industry test run**. Later phases (3-5) are OUT of scope except where the spec's schema
anticipates them. You cannot reach the network: do NOT attempt live scraping; prove everything that
is provable offline (ingest + reconciliation + report against the provided fixture).

## Allowed dependencies (all installed; add NOTHING else)
chromote, data.table, DBI, RSQLite, readxl, writexl, zip, jsonlite + base R. `stringdist` is NOT
installed — do not use it (nickname matching uses a static table; no fuzzy-string deps).

## File plan
```
mentat/
  mentat.Rproj
  README.md                  # how to run: RStudio + Rscript, env vars, resume behavior
  R/paths.R                  # MENTAT_DATA_ROOT (default file.path(Sys.getenv("LOCALAPPDATA"), "Mentat"));
                             # dir bootstrap; db_connect() -> RSQLite at <root>/mentat.db; WAL mode
  R/schema.R                 # CREATE TABLE IF NOT EXISTS: contributions (sub_id TEXT PRIMARY KEY, ...),
                             # scrape_progress, registers (companies, categories, leadership, trade, executives),
                             # review_queue_identity, canary_log, run_log
  R/ingest.R                 # Phase 0 per spec: parse all 6 input files -> register tables + a plain-text
                             # roster summary (print + write rosters_summary.txt for Paul's approval)
  R/fec_page.R               # chromote driver (see contract below)
  R/collect.R                # per (company, variant-set, cycle): drive fec_page, dedup by sub_id
                             # (INSERT OR IGNORE), tag legs, checkpoint per page
  R/reconcile.R              # fixture reconciliation (see below)
  R/report.R                 # writes spending_report.xlsx (template contract) + accuracy_report.md +
                             # reliability_report.md into <MENTAT_DATA_ROOT>/reports/
  scripts/run_crypto_test.R  # entry point: Rscript scripts/run_crypto_test.R [--cycles=2022,2024,2026]
                             # [--data-root=...] [--skip-scrape] ; --skip-scrape runs ingest+reconcile+report
                             # against whatever is already in the DB (used for offline testing)
  tests/test_ingest.R        # plain stopifnot() scripts runnable via Rscript (no testthat)
  tests/test_reconcile.R
```

## Phase 0 ingest contracts (input structures, all verified)
1. `Company Name.xlsx` — 5 sheets. **Crypto sheet: variants across COLUMNS** of each non-empty row
   (e.g. A="BINANCE US", B="Binance"); blank rows separate companies. **Other 4 sheets: variants in
   ROWS**, blank-row-separated blocks; first row of block = canonical. Cell hygiene rules:
   - split "A / B" cells into two variants;
   - `NOT <X>` entries (e.g. "NOT SEATTLE KRAKEN") are exclusion notes, never search terms;
   - parenthetical commentary (e.g. "(distinctive enough to be safe)", "(⚠️ ... spot-check ...)",
     "(holdco — ...)", "(legacy name pre-2014)") is stripped from the variant string; a "⚠️" or
     "spot-check" marks `confidence='spot_check'`, otherwise 'safe' when the note says
     safe/distinctive, else 'normal';
   - `CIK <digits>` and `Just "GEMINI"`-style notes → metadata, not variants;
   - prose rows (no company-name content, e.g. "But the insurance activity mostly files under the
     subsidiaries, so you need:") are dropped; rows after them stay variants of the same block;
   - subsidiary blocks roll up: parent field per spec (Berkshire/Hyundai Capital/PACCAR examples).
   For the crypto test only the Crypto sheet must parse perfectly; the other sheets must parse
   without error (best-effort flags OK) — assert counts in tests.
2. `Super PAC, 527, Carey, 501c.xlsx` — sheets `2021-2022` (cols: Group, View, Super PAC, 527s,
   Carey, 501c) and `2023-2024` (extra cols Total, Independent Expenditures, Elec Comm). Strip
   trailing non-breaking spaces from Group. View: C→R, L→D, X→none. Category = x-marked column;
   ignore 527s/501c (excluded); multi-marked rows keep Super PAC or Carey; unmarked rows →
   `party_committee_candidate` register rows (no lean from View).
3. `Leadership PACs to candidates, *.csv` — cols: PAC Name, Affiliate, Lean. Lean "Solidly
   Republican/Conservative"→R, "Solidly Democrat/Liberal"→D, anything else → lean='REVIEW'.
   2025-26: copy of 2023-24 register with provisional=TRUE.
4. `Trade Associations.txt` — category-header lines followed by "- Name" bullet lines; dedupe exact
   names; strip the "[...]][1]" wiki artifact.
5. `Credit Union Cross Industry Spending.xlsx` — Sheet1: industry asset denominators (skip the
   "ABS issuers" row — out of scope). Sheet2 (header on row 2): known-executives register (Industry,
   Name=company, CEO); normalize spellings via a small mapping (Krakken→Kraken,
   Winkelvoss→Winklevoss), keep originals in a source column. Sheet "Crypto" = THE FIXTURE:
   columns are (contributor name, recipient display name, date, amount) with NO header row; ~1,189
   rows. Sheet "Company Names" is a mirror — ignore it.

## fec_page.R contract (chromote)
- One `ChromoteSession`; navigate to
  `https://www.fec.gov/data/receipts/individual-contributions/?two_year_transaction_period=<cycle>&contributor_employer=<url-encoded variant>`
  (multiple employer params allowed for a variant set). Wait until the results table is populated
  (poll a JS readiness expression, timeout 60s, 3 retries with backoff).
- Set results-per-page to 100 via the page's per-page control, then read each page with ONE JS
  evaluation (this exact approach is live-verified):
  `JSON.stringify((function(){var dt=window.jQuery('#results').DataTable();var info=dt.page.info();var rows=dt.rows().data().toArray();return {info:info,rows:rows};})())`
- Persist this field subset per row (verified present): sub_id, transaction_id, original_sub_id,
  contributor_name, contributor_first_name, contributor_last_name, contributor_suffix,
  contributor_city, contributor_state, contributor_zip, contributor_employer,
  contributor_occupation, contributor_aggregate_ytd, contribution_receipt_amount,
  contribution_receipt_date, committee_id, committee_name (from `committee.name` or
  `committee_name`), recipient_committee_type, recipient_committee_designation, entity_type,
  election_type, fec_election_type_desc, receipt_type_full, memo_code, memoed_subtotal, memo_text,
  conduit_committee_id, conduit_committee_name, amendment_indicator, report_year, report_type,
  two_year_transaction_period, line_number, pdf_url, load_date. Plus provenance columns: industry,
  company, matched_variant, phase, scraped_at (Sys.time()).
- Paginate: click `.next` until `info.page + 1 >= info.pages`; between pages sleep 1.5-3.5s
  (uniform jitter). Canaries per page (log to canary_log; STOP the run on failure): serverSide
  true; recordsTotal unchanged within a (variant-set, cycle) walk; rows length > 0 while pages
  remain; every row has non-empty sub_id.
- Checkpoint after each page: write rows in one transaction (INSERT OR IGNORE by sub_id) + update
  scrape_progress(company, cycle, variant_set, last_page, records_total, updated_at). Resume =
  restart the walk but skip pages <= last_page only if records_total matches; else restart at 0
  (INSERT OR IGNORE makes it idempotent).
- Leg tagging at insert time (columns is_conduit_leg, is_memo_leg, is_passthrough_leg):
  memo_code=='X' or memoed_subtotal==TRUE → memo_leg; committee_id in ('C00401224','C00694323') →
  conduit_leg; memo_text matching earmark/attribution pass-through patterns per spec →
  passthrough_leg. Countable = none of the three.

## collect.R
- For the crypto test: companies from the Crypto register; variant subsumption: drop a variant if a
  kept variant is a token-subsequence prefix/substring of it (keyword-match semantics, spec §Phase 1);
  employer pass = minimal covering set (Phase 1), leftover variants = Phase 2 variant pass (same
  mechanics, phase='P2_variant'). The Phase 2 contributor-name pass and company-as-contributor pass
  run by default. The company canonical name must be the first company-as-contributor query, followed
  by its variants. `--skip-phase2-names` is the explicit opt-out.
- Exclusion post-filter: rows whose employer/occupation matches an exclusion note pattern (e.g.
  employer containing 'SEATTLE KRAKEN' or occupation 'HOCKEY') → flagged excluded_reason, kept in DB,
  out of extracts.
- Company attribution is exact-alias-first, not substring-based. Official legal/operating aliases are
  accepted; ambiguous short names such as employer `GEMINI` require an identity cross-reference;
  unrelated employers such as `GEMINI TRANSPORTATION` are rejected and retained for audit.

## reconcile.R (the acceptance gate — this must be excellent)
- Fixture rows lack sub_id. Normalize both sides: name uppercased/whitespace-collapsed; date →
  Date; amount → numeric rounded 2dp; recipient uppercased.
- Staged matching, each fixture row consumed at most once (greedy, stage order):
  S1 exact (name, date, amount, recipient); S2 (name, date, amount) + recipient prefix-25 match;
  S3 (name, date, amount) only; S4 (name, amount) within ±3 days.
- Output `accuracy_report.md`: per-stage counts, total match %, fixture-only rows (with nearest-miss
  diagnosis: which key component failed), pipeline-only rows (expected: post-2026-05-31 rows,
  amendments, memo legs — bucket them by probable cause), duplicate-tuple counts. Plus CSVs:
  fixture_unmatched.csv, pipeline_only.csv.
- `reliability_report.md`: pages walked, rows/pages per company-cycle, canary log summary, retry
  count, determinism spot-check result (re-read of one page: identical sub_id set y/n), amendment
  counts, max load_date (data-through), wall-clock per cycle, and the DB row counts by leg flags.

## report.R — spending_report.xlsx
Match `Spending Report template.xlsx` exactly:
- `Stats (XXX Company)`: the template's six year blocks (2026 through 2021), ten company/summary rows
  per block, and all 83 metric headers in their original order.
- `Employees (XXX Company)`: header row `Employee Name | Employee Title | Company | Zipcode | State`.
- `Raw Inputs (XXX Company)`: NO header row; six columns in this order:
  `Name | Recipient | State | Employer | Date | Amount`.
- `Sheet4`: normalized calculation facts, exact Raw Input/Employee row pointers, classification
  provenance, and plain-language formula definitions.

Only countable, accepted company-attribution rows enter the employee and raw-input sheets. Review and
rejected matches remain in the database and are exported to review CSVs.

The Stats grid contains live formulas over one exact, bounded Sheet4 fact range. Denominators are
uniform: total-industry share = company / ten-company annual total; category share = category /
relevant company or employee-tier total; Partisan Split = R / (R + D), blank at zero denominator;
Average = arithmetic mean of the ten companies; Change = current average minus the prior-year
average in the same units. Unsupported Phase 3 Company-PAC facts stay blank.

## House rules
- Every file UTF-8; every DB/file write atomic (write temp, rename); no absolute paths outside
  paths.R; no secrets; no network calls anywhere except inside fec_page.R's chromote driver; scripts
  must run headless via Rscript (no RStudio-only calls); loud stop() on any contract violation —
  never silently skip.
- Tests: `Rscript tests/test_ingest.R`, `Rscript tests/test_reconcile.R`,
  `Rscript tests/test_names.R`, and `Rscript tests/test_report.R` must pass offline, using
  `mentat/inputs/` and a temp data root. test_reconcile must include a synthetic round-trip: derive a
  fake collected table from the fixture itself (+ noise rows + a deliberate mismatch) and assert the
  stage counts and reports come out exactly as constructed.
- Style: data.table idiom, small pure functions, one file one concern. Comments only for contracts.

## Done =
Both tests pass via Rscript in this sandbox; `run_crypto_test.R --skip-scrape` runs end-to-end
offline (ingest → empty-DB reconcile degrades gracefully with a clear message → reports written);
README accurate. Report what you built, what you tested, and anything you had to interpret.

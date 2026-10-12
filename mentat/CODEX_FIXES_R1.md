# Codex fix batch R1 — confirmed review findings (2026-07-18)

Context: the pipeline in this worktree ran its live crypto test successfully (99.33% fixture match).
A 12-agent adversarial review confirmed the defects below. Fix ONLY these, minimally — do not
refactor working code. After each fix, both tests must still pass
(`Rscript tests/test_ingest.R`, `Rscript tests/test_reconcile.R`; set
`R_LIBS_USER=C:/Users/pgoff/AppData/Local/R/win-library/4.6` first; Rscript at
"C:\Program Files\R\R-4.6.0\bin\Rscript.exe"). No live scraping. No git. Stay in mentat/.

## M1 — collect.R ~440: person_name_parts crash (CONFIRMED by execution)
`strsplit(first_block, " ", fixed=TRUE)[[1L]][[1L]]` throws "subscript out of bounds" for any
single-token, empty, or trailing-comma contributor name ("SMITH", "ACTBLUE", "SMITH JR" after
suffix strip) — the error fires while forcing the left operand, so `%||%` never runs. This
deterministically aborts the whole P2_name walk and resume re-hits it forever.
Fix: `[1]` indexing (NA-safe) + `%||%`/nzchar guard; empty first name is valid (last-name-only
person). Add a tests/test_ingest.R-style stopifnot unit block (new tests/test_names.R, plain
Rscript) covering: "SMITH", "SMITH,", "", "ACTBLUE", "SMITH JR", "SMITH, JOHN A", "O'BRIEN, TIM".

## M2 — fec_page.R ~35: URLencode %-hex early-return (CONFIRMED on this R build)
`utils::URLencode(x, reserved=TRUE)` returns input UNCHANGED if it already contains a %-hex pair
("100%EF LLC & CO" passes through raw; the raw & then truncates the query). Fix: call with
`repeated=TRUE` (inputs are raw names, never pre-encoded).

## M3 — fec_page.R ~204: canary gap for short-served pages (CONFIRMED by execution)
A non-final page with fewer rows than `info$length`, or an empty final page, passes all four
canaries → silent row loss. Add canary `page_row_count`: non-final pages must have exactly
`info$length` rows; the final page must have exactly `recordsDisplay - start` rows.

## M4 — fec_page.R ~254-301: resume-skip can permanently miss rows (CONFIRMED)
Resume validity is judged by records_total equality alone; an FEC amendment swap that keeps the
total equal but changes composition is skipped forever. Since resume must physically re-click
through every page anyway (serverSide pagination), the skip saves almost nothing. Fix: DELETE the
resume-skip behavior — always invoke page_callback on every page (INSERT OR IGNORE makes it
idempotent); keep scrape_progress writes as a progress/telemetry record only. Remove the
now-unused `resume` parameter plumbing (fec_walk_variant_set, scrape_resume_row callers) and the
`resumed_through` field.

## M5 — collect.R ~128: passthrough memo pattern over-broad (CONFIRMED)
Bare REATTRIBUT*/REDESIGNAT*/TRANSFER* memo patterns exclude COUNTABLE money (reattributed spousal
gifts are real contributions). Fix: is_passthrough_memo flags ONLY earmark/JFC-attribution
pass-throughs: EARMARKED THROUGH / EARMARKED FOR / ATTRIBUTION FROM|TO / JOINT FUNDRAISING / JFC
(word-boundary, case-insensitive). Bare REATTRIBUT*/REDESIGNAT*/TRANSFER(RED)? rows: countable,
but set a new `memo_review` flag column (contributions table; schema.R DDL + collect.R insert)
so they land in the hand-check sample.
ALSO write scripts/retag_legs.R: recomputes is_conduit_leg/is_memo_leg/is_passthrough_leg/
memo_review from stored memo_text/memo_code/memoed_subtotal/committee_id via SQL UPDATE on an
existing DB (no re-scrape), prints before/after leg counts. It must be runnable standalone.

## M6 — collect.R ~256-291 + schema.R: silent cross-company attribution loss (CONFIRMED, calibrated)
sub_id is the global PK (correct dedup — KEEP IT; industry totals depend on it). But when a second
(company, phase) matches an already-stored sub_id, the insert is IGNOREd invisibly. Fix minimally:
insert_rows_or_ignore compares intended rows vs stored rows for conflicting `company` (not phase);
any sub_id whose incoming company differs from the stored company gets one row in a new
`attribution_conflicts` table (sub_id, stored_company, incoming_company, incoming_phase,
created_at) — created in schema.R, surfaced as a count in report.R's reliability section and
exported as reports/attribution_conflicts.csv when non-empty. Same-company re-finds stay silent
(specified dedup).

## Minors to include (cheap, same files)
- fec_page.R ~24: drop NA/blank variants from the set (filter, don't abort) — abort only if the
  set is empty AFTER filtering.
- collect.R ~599: P2_name seed rows with blank contributor_name are skipped with a log line, not
  crashed on.
- reconcile.R ~141: a scraped row with a blank name/recipient/date/amount key must not abort
  reconciliation — exclude it from the match pool and count it in the accuracy report as
  `invalid_key_rows`.
- report.R ~164: wall-clock parse drops the %z offset (parses local-time strings as UTC) — parse
  with the offset or use the epoch difference.

## Done =
Both existing tests + new tests/test_names.R pass via Rscript; `run_crypto_test.R --skip-scrape`
still completes; retag_legs.R runs against a copy of an existing DB without error. Report what you
changed per finding, with test output tails.

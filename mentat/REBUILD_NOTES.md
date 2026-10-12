# Mentat rebuild notes — fix batch R3

## Scope and safety

This rebuild implements every item in FIX_TASK.md Groups A through D. No commit was created. No live network request was made, no API key was read or persisted, and no database outside this workspace was opened or changed. All executable verification used synthetic fixtures or package-independent unit tests.

## Changes that alter reported dollar amounts

The following changes can change report totals or their allocation:

- Downstream `EARMARKED THROUGH` attribution legs are removed while the ActBlue/WinRed conduit receipt is retained. This removes the confirmed conduit/downstream double count and can reduce totals that previously included both legs.
- The squashed-prefix matcher now accepts only an exact squashed alias or a remainder composed entirely of known corporate qualifiers. False associations such as `G.E. MINISTRIES INC.` → Gemini and `CRYPTO COMPANY` → Crypto.com move to review, reducing affected company totals. Load-bearing exact forms such as `BINANCEUS` and `COIN BASE` remain accepted.
- Roster exclusions now inspect contributor name, employer, and occupation on every pass, including the corporate-name pass. Rows such as excluded tZero corporate names are rejected and no longer contribute dollars.
- Legacy `corporate_giving` rows with no computed match reason are excluded. A migration that adds the status field marks existing rows `review` with `migration_requires_revalidation`; databases already carrying the older accepted-by-default field are protected by the report's nonblank-reason requirement. Corporate totals can decrease until those rows are explicitly revalidated.
- The restored corporate API projection allows valid corporate pages to be stored. Relative to the broken phase that stored no corporate rows, valid corporate and total-giving dollars can increase.
- Party names abbreviated `Cmte`, `Cmte.`, or `Comm` are classified as `Party Committee`; other unmarked rows become `Unclassified` instead of `Candidate`. This changes the underlying category allocation. Both categories deliberately roll into the Stats Candidate column, so this relabeling alone does not change that aggregate column.
- Leadership PAC dollars now map to the Company PAC Stats column. Party Committee, JFC, and Unclassified dollars map to Candidate. This fixes category allocation and the reconciliation blocker.
- API pagination now refuses count shortfalls, partial cursors, cursor cycles, count changes, overruns, and page-ceiling overruns. A successful API result therefore cannot silently omit rows; complete totals may be higher than a previously truncated run.
- Data-driven diminutives can establish an identity-name equivalence only when the rest of the identity ladder also qualifies the row, such as matching ZIP or city. This can restore qualifying personal giving that an exact-first-name comparison missed.
- Unverified bare `GEMINI` employer hits go to review; a known-person cross-reference is required for acceptance. This can remove false Gemini dollars while retaining verified people.
- Blank or missing `entity_type` now defaults to a person unless the contributor name itself is company-shaped. Report generation recomputes this flag instead of preserving a stale stored flag. Total Giving is unchanged, but Corporate Giving can decrease while person and Chief/Middle/Employee columns increase.

The legitimate ActBlue split-receipt change does not itself add or remove dollars: distinct `sub_id` receipts are retained and no longer trigger a false hard stop. Retry, checkpoint, batching, BOM, geometry, and query-cache changes do not intentionally change dollar values.

## Group A — dollar correctness

- **A1:** `spending_downstream_earmark_mask()` identifies downstream legs from `receipt_type_full` and `conduit_committee_id`. The report keeps the conduit receipt and drops downstream and memo-X legs. The regression uses the confirmed three-row SILVERBERG anatomy.
- **A2:** The kept-receipt invariant uses nonblank, unique FEC `sub_id` values and separately asserts that no downstream earmark leg survived. A ten-row BALACHANDER/ActBlue split fixture proves legitimate equal-value rows are allowed.
- **A3:** Squashed-prefix matching parses the entire remainder as known corporate qualifier tokens; other remainders are review-only.
- **A4:** Exclusion evaluation includes contributor name, employer, and occupation. Exclusions take precedence over persistent accept overrides.
- **A5:** New corporate rows default to `review`/non-entity until computed, migrated rows require revalidation, and reports require an explicit accepted status plus a nonblank match reason.
- **A6:** Party detection accepts `COMMITTEE`, `CMTE`, and `COMM` after intervening words; the unmarked default is `Unclassified`.

## Group B — blockers

- **B1:** Category reconciliation uses `rep(names(groups), lengths(groups))`, aligned to the flattened category values, rather than indexing the five group names with nine category positions.
- **B2:** Header-only company-match override columns are coerced to character before normalization.
- **B3:** Corporate API records retain `recipient_committee_type` for checkpoint storage.
- **B4:** The rebuilt Stats contract is strict at 86 headers, 85 formula metrics, and an 84 × 87 grid. The synthetic report fixture has six sheets: two template sheets, three dynamic raw-input sheets, and `Sheet4`. Its post-header-restoration formula count is 6,035. The old 83/82/84 × 84/four-sheet/5,822 expectations were updated because they described the pre-rebuild artifact.

## Category to Stats-column mapping

| Source category | Stats column |
| --- | --- |
| Super PAC | Super PAC |
| Carey | Carey |
| Conduit | Conduit |
| Company PAC | Company PAC |
| Leadership PAC | Company PAC |
| Trade Association | Trade Association |
| Candidate | Candidate |
| Party Committee | Candidate |
| JFC | Candidate |
| Unclassified | Candidate |

Registered `527` and `501c` rows remain visible in support data as `EXCLUDED_527_501C` and are excluded from the Stats giving range.

Conduit (2026-10-09, Paul's ruling): the registered earmark conduits ActBlue (`C00401224`) and WinRed (`C00694323`) are filed under their own `Conduit` category (`category_source` `fec:registered_conduit:earmark_platform`) instead of Carey. Only the label moved; the receipts count once, to the same recipient, as before. The Stats sheet gives Conduit its own column group directly after each Carey group (company block, the three role blocks) and its own Company PAC allocation group, so the contract in B4 is now 101 headers, 100 formula metrics, an 84 × 102 grid and 7,100 formulas. The industry workbooks give Conduit its own Spending Overview row and Master Overview channel (directly after Carey). A book built before this ruling still files them under Carey (or, before 2026-09-08, Super PAC).

## Group C — API correctness

- **C1:** Each work unit establishes and enforces `pagination.count`; count completion stops without an unnecessary empty request, while a missing cursor before count completion is a hard error.
- **C2:** Both keyset cursor members must be present, scalar, non-null, and nonblank in responses and requests.
- **C3:** Every cursor signature is retained for the work unit, so A→B→A cycles fail; a 10,000-page ceiling is also enforced.
- **C4:** `Retry-After` is honored for all retryable responses, capped at 120 seconds, and scheduled per request rather than as one batch-wide maximum.
- **C5:** Parallel httr2 calls use `on_error = "continue"`; successful responses survive and only failed requests retry. Transport errors are normalized without exposing the API key.

## Group D — optimization and resilience

- **D1:** Contributor-name identity collection defaults off and is enabled with `--phase2-names`. Raw API pages, cursors, seen keysets, fetched/stored state, and a persistent 1,000-request hourly budget are stored in the local Mentat database. Budget exhaustion or sustained 429 responses records a paused run and exits before reconciliation/reporting; a later run resumes checkpoints. Logs report requests used and remaining.
- **D2:** Roster overrides and trusted-name keys are cached per company/work phase. Data inserts use temporary staging tables and set-based `INSERT OR IGNORE`; completed API work units are stored in one transaction. API concurrency halves after a 429 and increments toward the configured maximum after a clean batch.
- **D3:** `inputs/name_diminutives.csv` contains 168 unique canonical/variant seed rows, is validated and loaded at ingest, and drives `person_identity_key()` without code edits. This is a seed, not an exhaustive authority.
- **D4:** Bare Gemini employer matches require a trusted person key; verified Gemini legal aliases remain accepted and unverified extensions are rejected or reviewed.
- **D5:** CSV output again uses UTF-8 with a BOM through `data.table::fwrite(..., bom = TRUE)` followed by atomic replacement.
- **D6:** Only explicit `ORG`, `COM`, `PAC`, or `CCM` types, or a company-shaped contributor name, mark an entity. Blank/NA types default to individual.

### Ambiguous diminutive seed values

These variants occur under more than one canonical and therefore normalize only to themselves; they are never a sole identity-match basis:

- `chris`: CHRISTOPHER / CHRISTINE
- `harry`: HAROLD / HENRY
- `jon`: JOHN / JONATHAN
- `kate`, `katie`: CATHERINE / KATHERINE
- `pat`: PATRICK / PATRICIA
- `patty`: MARTHA / PATRICIA
- `rick`: FREDERICK / PATRICK / RICHARD
- `sam`, `sammy`: SAMUEL / SAMANTHA
- `sandy`: ALEXANDER / SANDRA
- `steve`: STEPHEN / STEVEN
- `ted`, `teddy`: EDWARD / THEODORE

## Verification and live follow-up

Offline regressions cover the real earmark anatomy, legitimate split receipts, false squashed prefixes, exclusion precedence, category mapping, cursor count/cycle/ceiling behavior, capped per-request retries, partial-batch recovery, adaptive concurrency, Gemini trust gating, ambiguous diminutives, blank entity types, corporate report formulas, and BOM bytes.

No live database or API verification was performed by design. Before publishing production reports, run the rebuild against an authorized scratch copy of the live Mentat database and compare, by company/year/category: dropped downstream earmark dollars, newly reviewed squashed-prefix and Gemini dollars, exclusion removals, corporate rows awaiting revalidation, corporate rows restored by the API phase, and person-versus-corporate reclassification. Confirm that the corporate migration and checkpoint resume behave as expected on that copy.

In this managed session, `test_fec_api.R`, `test_fec_page.R`, and `test_rules.R` pass. The prescribed external R 4.6 user library exists but is not traversable from the sandbox, so R sees only its base library; `test_ingest.R` and `test_reconcile.R` stop before assertions because `data.table` is unavailable, while `test_names.R` and `test_report.R` stop before assertions because `DBI` is unavailable. No network install or sandbox bypass was attempted.

## R4 — unservable employer queries and bulk completeness audit

### Evidence and fallback design

The live evidence supplied for this batch showed that `contributor_employer=Binance` in cycle 2024 reproducibly returned 504 while the same term served in adjacent cycles. After one slow 504, openFEC also returned an immediate circuit-breaker 504. The API retry layer now classifies a query as `unservable_query` after no more than two 504 attempts; ordinary 503/other transient responses retain the existing `Retry-After` and retry behavior.

An unservable employer unit is decomposed into spellings observed in the optional FEC bulk supersets. The failed normalized term is excluded, each narrower spelling is queried independently, and every returned API row is unioned by exact `sub_id` and sorted before checkpoint/storage. If no observed register is available, roster variants provide the fallback candidates. The console emits `unservable_query: <term> cycle <n> -> decomposed into k queries, m rows`, and the same event—including its spelling list and union row count—is stored as JSON in that cycle's `run_log.decomposition_log`.

### Bulk register, rescue, and audit

Phase 0 reads the 2022/2024/2026 pipe-delimited 21-column bulk supersets from `MENTAT_BULK_DIR` or `<MENTAT_DATA_ROOT>/bulk`. Missing files warn and do not block API collection. Only rows accepted by `company_employer_match`, with normal roster exclusions applied, enter the row-level `bulk_candidates` register. `observed_employers` stores distinct cycle/spelling counts and is the decomposition source. In particular, an excluded `SEATTLE KRAKEN` row cannot seed a Kraken fallback query.

After employer collection, each still-missing bulk `sub_id` is queried by contributor name and ZIP when available. Pagination continues only until the exact target ID is found or the narrow query completes. Near-miss IDs are discarded; the matching API row—not the whole-dollar bulk row—is flattened through the normal matcher/exclusion path with phase `P3_bulk_rescue`. `MENTAT_RESCUE_CAP` defaults to 50 actual requests, including retries and additional pages. Rows not started because the cap is exhausted are written to `reports/bulk_rescue_overflow.csv` without another request.

`reports/bulk_audit.csv` contains every Crypto company × supported cycle with bulk candidate, ordinary API capture, rescued, and unreachable counts. `reports/bulk_audit_unmatched.csv` lists the remaining IDs and bulk provenance fields. Unreachable rows are reported and printed but do not fail the run.

### Offline verification and live follow-up

Synthetic tests cover two 504s followed by observed-spelling decomposition, cross-spelling `sub_id` deduplication and sort order, unchanged 503 `Retry-After` behavior, exact rescue acceptance versus a same-name near miss, the 50-request boundary with candidate 51 never sent, a pipe-delimited register fixture whose `SEATTLE KRAKEN` row is excluded, and captured/rescued/unreachable audit counts. No live network call was made.

Before publishing refreshed reports, verify against an authorized scratch copy that the three real supersets ingest with expected company-cycle counts; Binance 2024 decomposes specifically to the serving observed spellings; bare-employer sub_id `4051020251191476176` is recovered by the name/ZIP query with the cent-exact API amount; the decomposition JSON survives a resume; and the audit totals reconcile to independent bulk `sub_id` sets. Also confirm whether any narrow rescue query needs more than one page and whether the default cap leaves overflow in the real run.

## R5 — Paul's recipient-as-shown convention and amendment versioning

Paul's ruling is the design authority: every current FEC row counts to the recipient shown on that row. Memo-code-X, conduit, passthrough, negative, refund, and redesignation rows therefore remain in report raw sheets, `Sheet4`, Stats inputs, and reconciliation. The leg columns remain analytical flags only. The kept-row invariant is now a nonblank, unique FEC `sub_id`, plus exclusion of filing versions actually superseded by an amendment; `spending_downstream_earmark_mask()` is retained only as an unused analytical helper.

Amendment versioning runs identically over `contributions` and `corporate_giving`. Collection now persists `transaction_id`, `original_sub_id`, `amendment_indicator`, `report_year`, `report_type`, `file_num`, `pdf_url`, and `load_date` into both stores. Explicit original-sub-ID chains take precedence, followed by committee/report/transaction identity. `file_num` determines the latest filing when present, then the FEC document ID, load timestamp, and the filing date encoded in `sub_id`; all rows tied within the current filing remain because distinct current-view `sub_id` rows are facts. For legacy corporate rows whose older schema discarded transaction fields, an exact row identity seen across distinct encoded filing dates is versioned to the latest filing date, while multiple rows in that latest filing are all retained. Earlier rows are marked `superseded_by_amendment=1`, remain in SQLite, and are the only amendment rows omitted from reports and reconciliation. Reliability output and the console log rows superseded and before/current dollar totals by company.

On the supplied offline scratch database, Coinbase corporate giving changes from **$307,890,854.93 across 457 rows** to **$213,890,859.93 across 453 current rows**: four older filing rows and $93,999,995.00 are superseded. Coinbase-to-FAIRSHAKE changes from **$293,974,208.87 across 22 rows** to **$199,974,213.87 across 18 current rows**. This is the intended $100M-plus order of magnitude rather than the amendment-stacked roughly $300M result. No live request was made for this verification.

Full-row fixture reconciliation adds the 93 previously suppressed memo-X rows. Strict one-to-one matching is **1,180 / 1,189 (99.24%)**; the three explicitly named rows present in SQLite bring database coverage to **1,183 / 1,189 (99.50%)**, above the 99.33% gate. They remain named as residuals rather than being silently counted as one-to-one matches:

- Fixture 562 — OBRIEN, MOLLY → ACTBLUE, 2025-10-07, $100: exact database row present; the single candidate was already consumed by a duplicate fixture tuple.
- Fixture 1177 — PETERS, ERIC → HIMES FOR CONGRESS, 2025-01-06, $3,300: exact database row present; the single candidate was already consumed by a duplicate fixture tuple.
- Fixture 1182 — ARMSTRONG, BRIAN → NRCC, 2025-05-06, $44,300: contributor/date/amount are present, but the row's displayed recipient differs.

The six genuinely absent fixture rows are:

- Fixture 1183 — CHOI, EMILIE → NRSC, 2026-02-10, $44,300.
- Fixture 1184 — CHOI, EMILIE → NRSC, 2026-02-10, $5,700.
- Fixture 1185 — CHOI, EMILIE MONICA → TOMORROW IS MEANINGFUL PAC, 2025-04-11, $5,000.
- Fixture 1186 — TUSAR, GREG → DEMOCRATIC GRASSROOTS VICTORY FUND, 2026-05-01, $44,300.
- Fixture 1187 — TUSAR, GREG → DNC SERVICES CORP / DEMOCRATIC NATIONAL COMMITTEE, 2026-05-01, $44,300.
- Fixture 1189 — SHIRZAD, FARYAR → CLUB FOR GROWTH ACTION, 2025-02-18, $2,850.

The updated offline regressions retain and independently classify all three SILVERBERG recipient rows, keep both signed redesignation rows and prove they net to zero in Stats facts, and verify that original/amended transactions in both contribution stores report only the amended amount. The seven-file suite remains the required final verification surface.

## R7 — cross-cycle corporate filing versions

The confirmed Coinbase-to-FAIRSHAKE receipt on 2024-12-20 for $23,000,000 was stored five times under `sub_id` values `4020420251141540904`, `4030420261386079659`, `4030420261386079661`, `4081520251218411849`, and `4081520251218411851`. The first capture came from the 2024 query and the other four from the 2026 query. All eight amendment/version columns on these legacy `corporate_giving` rows are null, so transaction-ID amendment versioning cannot connect them.

R7 adds a filing-capture identity independent of `sub_id`: normalized `(company, committee_id, contribution_receipt_date, contribution_receipt_amount)` for corporate rows. Each identity keeps one deterministic row, preferring the highest available `file_num` and latest amendment metadata, then the lexicographically highest `sub_id`; every loser remains in SQLite with `excluded_reason='duplicate_filing_version'`. Corporate checkpoint writes carry `transaction_id`, `original_sub_id`, `amendment_indicator`, `report_year`, `report_type`, `file_num`, `pdf_url`, and `load_date` end to end and backfill those fields when a previously stored `sub_id` is seen again with version metadata. The run summary reports duplicate filing rows and their dollar delta for every company.

The person-contribution identity additionally includes normalized `contributor_name` and ZIP. It collapses only rows connected by a shared nonblank `transaction_id` or an explicit `original_sub_id` chain. Unlinked same-person/same-day/same-amount receipts therefore remain separate facts: all ten BALACHANDER-to-ActBlue $1.67 rows survive, as do equal receipts from two different people. This is the split-versus-version boundary; the broader four-field collapse is confined to the corporate store where the cross-cycle damage is proven.

A read-only parity calculation over the supplied live scratch database projects **$130,005,470.68** of accepted Coinbase corporate giving after R7, including **$117,092,475.00** to FAIRSHAKE. Across all Coinbase corporate rows, independent of match-status report filters, filing-identity collapse flags 83 rows and $133,144,778.72, changing the raw $307,890,854.93 to $174,746,076.21. The managed sandbox could not execute the required final R report run because it cannot traverse `C:/Users/pgoff/AppData/Local/R/win-library/4.6`; these projected report totals must be replaced or confirmed by the prescribed `--skip-scrape` run once that library is visible.

## R8 — no-culling law, location-aware brand matching, and optimization

Paul's standing law is that raw/extract output contains every row returned by the FEC. Memo-X, conduit, negative/refund, redesignation, amendment, and duplicate filing-version rows are never filtered from raw sheets. `excluded_reason`, `is_memo_leg`, `is_conduit_leg`, `superseded_by_amendment`, and `duplicate_filing_version` are visible audit columns. Deduplication is a flag, never a raw-row filter.

Analysis uses one duplicate rule and no date/amount collapse: a memo-X row is flagged only when a non-memo row exists with the same `(company, committee_id, date, amount, contributor_name)`. A memo row naming a different committee is a real earmark leg and counts. This preserves Coinbase → FAIRSHAKE sub-ID `4020420251141540904` as the real line-11AI receipt while flagging the four same-recipient line-17 memo re-descriptions. The resulting real total is **$161,474,209**, not **$293,974,209**. Split gifts remain independent facts; all ten BALACHANDER $1.67 rows survive raw output and analysis. Each netting sheet states this rule and the flagged-row count.

Ambiguous single-token brands are disambiguated from file-backed roster metadata rather than a company exception in R. `inputs/company_home_states.csv` declares one or more home states; Gemini declares **NY**. A bare-brand row in a home state is accepted when its occupation is outside the manual-trades denylist. A home-state manual-trade row remains review, while an out-of-state manual-trade row is rejected. `company_match_overrides.csv` now supports optional `state` and `occupation_pattern` scopes while legacy three-column files remain valid, and the supplied 31-row override register takes precedence over automatic rules. This restores the verified **86 Gemini rows / $681,565** in New York and prevents roughly **$240,000** of unrelated Gemini-brand accepts in NH, TX, KS, and MN.

Run-level lookup caches now load company rosters, company-match overrides, home states, trusted names, and report committee-master registers once. Page matching reuses one precomputed company context, and completed API work units retain one insert transaction across all stored pages. Collection and report generation both emit rows/second benchmark lines. No live network verification was performed for R8.

## R9 — fail-closed pagination and production-size report speed

The API page ceiling is fail-closed again: exceeding `FEC_API_MAX_PAGES_PER_UNIT` raises `FEC API page ceiling exceeded` instead of warning and marking an incomplete work unit complete. The existing ceiling regression now passes with the full API suite.

Report preparation now applies amendment/filing/memo version flags once per `write_reports()` call and shares the prepared lookup, raw-row, and analysis-row objects across the workbook, reliability report, and unclassified-recipient export. The normal and retag-only entry paths also avoid a second company/identity retag inside `write_reports()`. Standalone report helpers retain safe defaults and still prepare their own state when called directly. `test_report.R` pins the one-versioning-pass contract.

Raw-sheet date and amount formatting no longer reconstructs an entire worksheet XML string once per cell. Each column is rewritten in one linear pass, preserving the same style IDs and workbook contract. On a disposable copy of the 15.6 MB scratch database, the no-network pipeline completed in **175.6 seconds**; report generation processed **12,934 rows in 60.56 seconds (213.57 rows/second)**. The prior implementation exceeded a 304-second command ceiling. Reconciliation finished at **1,184/1,189 strict matches (99.58%)** and **1,189/1,189 database coverage (100.00%)**. All seven offline test scripts and all 16 R-file parse checks pass. No live database or network request was used.

## R10 - Spending Report visibility, Excel compatibility, and corporate coverage

The missing Stats display had four independent causes. First, all 6,035 formula caches were zero, so non-calculating viewers showed an empty report. Second, 82 legacy `corporate_giving` rows were stranded at `migration_requires_revalidation`, so no corporate facts reached Stats. Third, qualified legal/trade names accepted by the corporate matcher were later downgraded by the report's older exact-only entity recomputation. Fourth, 180 generated partisan formulas exceeded Excel's hard 8,192-character limit (maximum 8,932), causing Excel itself to reject the workbook.

Report generation now computes and bulk-patches exact formula caches, preserves validated corporate-source provenance, and keeps automatic calculation enabled without forcing a full rebuild on open. The partisan formula uses an algebraically equivalent `IFERROR` guard and the production maximum is **5,365 characters**. Calculation metadata inherits the template's current `calcId=191029`; cached values remain immediately visible, while edits still recalculate automatically. The Stats Super PAC value column is widened just enough to eliminate the twelve production `####` cells found by the visual pass.

Corporate revalidation now runs in normal, retag-only, and standalone report flows. Literal aliases, vetted corporate qualifiers, and the scoped `COINBASE COMMERCE EXCHANGE` persistent override restore all **82/82 corporate rows** to Sheet4 as `CORPORATE`; unrelated `KRAKEN COMMERCE` remains review. `--retag-only` reloads file-backed rosters and overrides before applying tags, so newly approved register changes are no longer silently ignored. Reliability output includes corporate accepted/review/rejected coverage, the company review queue includes corporate contributor names, and run wall-clock is reported once per `run_id` rather than duplicated across cycles.

Final offline receipts on the disposable production-size database: SQLite `integrity_check=ok`, no foreign-key violations, **12,934/12,934 raw rows** present across company-cycle sheets, **5,410 contribution + 82 corporate facts** in Sheet4, 34 sheets, 6,035 formulas, and no ZIP/XML corruption. Excel 16 opened the 2.40 MB workbook read-only in **0.923 seconds**, reported `xlDone`, returned the cached Coinbase 2026 total **$1,370,221.74** and corporate total **$500,000**, and found **0 formula errors / 0 clipped Stats cells**. The full clean no-network pipeline completed in **135.585 seconds**; the final report-only regeneration completed in **47.296 seconds**. Reconciliation remained **1,184/1,189 strict (99.58%)** and **1,189/1,189 database coverage (100.00%)**. All seven offline test files passed in **14.968 seconds**. No live database or network request was used for these receipts.

## R11 - checkpointed live augmentation and completion telemetry

With no registered `FEC_API_KEY` present at process, user, or machine scope, the authorized live augmentation used Mentat's documented Chrome fallback against the isolated R10 scratch database. `--resume-after=2026-07-18T00:00:00-0500` activated the 1,066 July 19 browser checkpoints, so completed employer and identity walks were skipped while the previously interrupted Gemini/Kraken/MIAXdx/tZero tail resumed after the strict retag fix. Run `20260722T150443-27692` completed all three cycles with zero retries in **1,480.7 seconds (24m41s)**. The collection phase reported **1,302.940 seconds**, **14,818 contribution rows**, and **11.37 rows/second**; report generation processed **15,840 raw rows** in **65.570 seconds (241.57 rows/second)**.

The augmented SQLite database is **20,271,104 bytes**, passes `PRAGMA integrity_check`, has no foreign-key violations and no freelist pages, and contains **14,818 contribution rows + 1,022 corporate-query rows**. Corporate revalidation leaves **91 accepted corporate facts**, **928 rejected individual-entity query hits**, and **3 review rows**. The three review rows are the deliberately ambiguous Gemini names `GEMINI G.E.L.` and `GEMINI ELECTRIC, INC`; unrelated `KRAKEN COMMERCE` remains review in the contribution store. Fixture reconciliation remains **1,184/1,189 strict (99.58%)** and **1,189/1,189 database coverage (100.00%)**.

The promoted workbook is **2,868,485 bytes** with 35 sheets, a clean ZIP, valid XML, **6,035 formulas**, maximum formula length **5,365**, and **5,980 contribution + 91 corporate + 16 method** facts in Sheet4. Excel 16 opened it read-only in **1.780 seconds**, reached `xlDone`, returned Coinbase 2026 C3 **$1,370,221.74** and F3 **$500,000**, and found **0 formula errors / 0 clipped Stats cells**. The complete seven-file regression suite passed in **15.384 seconds** after augmentation; after the telemetry correction, all 16 R files parsed and the suite passed again in **17.529 seconds**. The promoted SHA-256 is `C15AF41FADAE883EED0EBD26BD340E1662693F16D2E98729A93ACC3046E8AB06`.

The run exposed a completion-telemetry bug: Chrome collection was recorded as `requests=0` because only the API engine returns an exact request count. Future completion messages now distinguish `engine=api; network_requests=N`, `engine=offline; network_requests=0`, and `engine=chrome; network_requests=n/a` while persisting Chrome row and collection-wall metrics; all three cases have a regression assertion.

One external coverage limitation remains explicit: the optional `indiv22_crypto_superset.psv`, `indiv24_crypto_superset.psv`, and `indiv26_crypto_superset.psv` files are absent from the isolated data root. Browser collection and fixture reconciliation completed, but the independent bulk-candidate/API-capture/rescue audit has no candidate population until those supersets are supplied. The pipeline warned and used roster variants as designed; this is missing optional source coverage, not database or workbook corruption.

## R12 - audit-batch reconciliation (2026-07-27)

A 2026-07-27 audit produced a batch of uncommitted fixes that was never committed or documented, left the report path crashing, and reached no workbook. This round reconciles that batch: sound fixes kept, one policy reversal reverted, one crash fixed, one detector wired, and the largest finding completed at its source.

### Conduit legs keep counting (F5 reverted - policy, not a bug)

The batch added a second de-duplication rule: an ActBlue/WinRed conduit leg whose earmark target already appears as a settled row was flagged `duplicate_memo` and dropped from analysis totals (~$12.4k in the credit-union book). That reverses Paul's standing R5/R8 ruling - recipient-as-shown, a memo/earmark row naming a DIFFERENT committee is a real leg that COUNTS, `is_conduit_leg` is a visible audit flag and never a filter. Ruling re-affirmed 2026-07-27: **conduit legs count**. Reverted `mentat_memo_duplicate_plan()`'s conduit branch and the `apply_amendment_versioning()` column widening that existed only to feed it (the padding was load-bearing for nothing else - the seven base columns exist in both `contributions` and `corporate_giving`, so the rbind binds without it). The same-committee memo dedup and every `duplicate_memo` reader are untouched.

Export guard G3 (`mentat_guard_conduit_double_count`) was deleted with it: under recipient-as-shown a conduit leg pairing with its target is the EXPECTED state, so the guard could only ever fire on correct data - as a fail-severity guard it would have refused to publish every book containing earmarked legs.

### Report path no longer crashes (F8)

`resolve_recipient_register_committee_ids()` had been given a trade-override fold that referenced `trade_overrides` and `trade_override_applied` - neither of which was ever assigned - plus an orphan `input_dir` parameter no caller passes. Every invocation that reached the `trade` register aborted with `object 'trade_overrides' not found`, taking `write_reports()` down with it (all three callers: report.R, fec_api.R, ingest_all). The blocks are removed rather than wired: report.R already folds the same overrides in at read time (`spending_report_lookups()` -> `apply_trade_overrides`), which is the path every consumer uses because the exporter opens the database `SQLITE_RO` and never calls the resolver. One implementation, not two that must stay in sync. `read_trade_committee_overrides()` and the input-file seeding are kept.

### Trade Association resolves, and it is the same defect as F6

The register holds all 335 associations from the trade-groups list, and **every one of them resolved to nothing** in the shipped books - the resolver compared the association's name to the committee's name, and a trade PAC essentially never files under its association's bare name. Hence Trade Association = $0.00 in all six books.

The money did not disappear. A trade PAC is committee_type `Q` with designation `B` or `U`: it matches no fallback rule (not `H/S/P` or `A/P` for Candidate, not `D` for Leadership PAC, not `X/Y/Z` for Party), so it keeps the default `Unclassified` category - and `Unclassified` folds into the **Candidate** Stats column. Verified against the live snapshots: NRA, NAB, NAR, ABA BankPAC, NAHB BUILDPAC, Consumer Bankers, AFSA, USTelecom, AEM and NSSGA all carry type `Q`, designation `B`/`U`, and together account for **$162,458.32** of accepted money across the measurable books.

That is the audit's "~$163k, identical across all six books". It was never a Candidate/Leadership PAC swap - it is trade-association money filed as candidate money, which is why a re-implementation of the classifier found the Candidate and Leadership columns perfectly consistent and could not reproduce a swap anywhere. **F6 and F8 are one defect**, and resolving the trade register moves the money to the column it belongs in: Candidate down ~$163k, Trade Association up ~$163k.

Resolution now happens in two layers, curated first:

1. `inputs/trade_committee_overrides.csv` - 102 curated association-to-committee-ID rows.
2. **Broad net** (`trade_resolve_broad_net`, report.R) for everything the CSV does not name. The filing pattern is regular - committee name = association name plus a committee suffix, sometimes behind a leading article or a `<BRAND> PAC OF THE <ASSOCIATION>` wrapper - so the association's full normalized name is matched anywhere inside the committee name. Guards: names under 10 characters are never netted (acronym-like, they collide), and an association matching more than one committee ID is left unresolved rather than guessed, per the register's standing rule that ambiguity goes to review. Matching the full name rather than acronyms is what keeps American Bankers Association and American Bar Association apart.

The broad net is what makes the register self-resolving: a newly collected industry picks up its trade PACs without anyone regenerating a CSV. On the current snapshots it adds American Medical Association -> AMA PAC and American Pharmacists Association -> APhA PAC beyond the curated file, with no ambiguous or false hits. Guard G7 still reports any category the template renders that nothing populates.

### Category cross-view agreement wired (F2 + F6)

`mentat_guard_category_agreement` (G6) was defined but never called - it had no call site anywhere, so the only check capable of catching the historical Candidate/Leadership PAC swap never ran, and the shipped `spending_assert_category_reconciliation` remained the tautology it always was (it re-sums its own inputs and cannot fail).

G6 is now wired to two genuinely independent views: `view_a` is post-reshape, the category joined onto raw rows by `sub_id` - the path the per-cycle sheets' SUMIFS actually sum - and `view_b` is the classification frame itself, where category and amount sit on one row and were never joined. A `sub_id` join that multiplies rows, drops a match, or collides on a column suffix moves dollars between categories while leaving the cycle grand total intact; that is the exact F6 signature, and it is invisible to every total-level check.

On the F6 root cause: there is no swap. A read-only re-implementation of the classifier over the live 2026-07-23 snapshots found the data, the classifier and the reference registers **clean** - Candidate exceeds Leadership PAC by 4x-14x in every cycle of every measurable book, no cycle is anomalous, the leadership lookup contains zero candidate committees, and neither the classifier nor the workbook builder changed after those books were built. The "~$163k identical across all six books" reproduces nowhere as a Candidate/Leadership difference (measured spreads are $217k-$7.5M and book-specific).

**CORRECTION (2026-07-27, from the R12 audit).** An earlier draft of this section claimed the ~$163k WAS the trade-association money, on the basis that ten national trade PACs total $162,458.32. That reasoning does not hold and the claim is withdrawn. $162,458.32 is a sum ACROSS books; the audited figure was described as identical WITHIN each of six books, which would total roughly $975k. The regeneration falsifies it directly: trade-association money is book-specific, not constant - Credit Union $47,297.91, Community Banks $43,859.85, Credit Arms $36,247.08, Crypto $1,916.60, and Fintech **$0.00**. A quantity that is $0 in one book cannot be a per-book-identical $163k.

So: resolving the trade register is a real and separately justified fix (see the Trade Association section below), but it is NOT an explanation of F6. **What the original audit measured as "~$163k Candidate/Leadership, identical across all six books" remains unexplained.** What IS established is narrower and still worth having: the classifier and the reference registers are clean (Candidate exceeds Leadership PAC 4x-14x in every cycle of every measurable book, zero candidate committees in the leadership lookup), the workbook builder is transposition-free, and neither changed after those books were built - so no un-swap was applied, because nothing in the current code path swaps anything. G6 is now the standing detector: if any regeneration produces a real cross-view divergence it fails the build with the exact cycle and category named. F6 should be treated as OPEN pending either a reproduction against the audited artifact or a decision to close it on the strength of G6 passing.

### Opposition spending split at the source (F3)

The batch corrected the party columns in the Excel finalizer only, which reached exactly two cells. The source stayed unsigned: `preliminary_downstream_amount` summed support and oppose together, so the headline Downstream Activity total, Rank (and the top-100 selection itself), the Party Coverage denominator and the Beneficiary Bridge all still presented money spent to DEFEAT a candidate as activity benefiting them. The Excel guard was also all-or-nothing: `[Directions]` is a joined-unique string, so a candidate reached by both support and oppose spending had their genuine SUPPORT dollars discarded as well.

Direction is now split where it still exists as a per-row fact, in `fec_aggregate_beneficiary_downstream()`: `support_amount` and `oppose_amount` are carried as their own components through the bridge and the summary. They are **not netted** - support minus oppose would hide the magnitude of both. The Beneficiary Summary gains `Support Activity` and `Oppose Activity` columns (22 -> 24).

**The party columns went through two iterations; the first was wrong and was caught by the R12 audit.** Counting `Support Activity` alone treated only 24E/24F independent expenditures as pro-candidate money, but 24K contributions to a nonaffiliated committee, 24Z in-kind, 24C coordinated party expenditures, 24P, 24R and 24H are all unambiguously FOR the candidate and are the ordinary case for a corporate PAC. That stripped **$151.4M** out of the party columns - David Perdue's $2,995,566 of "Contribution / Other" money in the 2022 Bakkt view read as $0 Republican Activity at 0% coverage. It also turned `Other / Unclassified Activity` into a dump bucket holding **$1.99B (Crypto) / $2.55B (Credit Union)** of opposition money under a header that means "party is neither D nor R" on every other sheet, and it left Party Coverage using a different denominator from the focused sheets, so one candidate reported two different coverage numbers in one workbook (Sherrod Brown 100.0% against 0.6%) with three rows exceeding 100%.

The shipped design is **four buckets that sum to the total**: Democratic and Republican Activity are `Downstream - Oppose` for that party (money FOR the candidate is everything not spent against them), Oppose Activity is its own column and is never folded into a party total, `Other / Unclassified Activity` is reserved for candidates whose party really is neither D nor R, and Party Coverage divides by the mixed total on BOTH the Beneficiary Summary and the focused sheets. The Integrity Check asserts `Downstream - Dem - Rep - Other - Oppose = 0`; that is a real check rather than an algebraic tautology, because Oppose Activity is computed in R from per-row transaction codes while Downstream Activity is an Excel SUMIFS over the bridge, so the identity closes only if the two independent paths agree. The focused sheets subtract the same opposition via a Section-guarded SUMIFS, since only beneficiary rows have a fourth bucket.

Verified on the Crypto rebuild: Perdue reads $2,995,566 Republican Activity at 100% coverage; Sherrod Brown 2024 reads Oppose $63,771,343 with Other / Unclassified $0.00; across all 2,044 rows, zero D/R rows carry a nonzero Other / Unclassified and zero rows exceed 100% coverage.

Ranking and top-100 selection deliberately still use total downstream activity - it measures total money moving at a candidate in either direction - but the sheet now exposes the split behind that number.

### Kept from the batch

- **F1/F4 (429-as-$0)**: `fec_results_ready()` no longer accepts a completed `schedule_a` resource entry as proof of a genuine empty result. A 429 creates one exactly like a 200 does, which published Farmers Insurance at $0.00 across all three cycles. Readiness now requires DataTables' confirmed-empty message row and refuses on an explicit non-2xx; failure times out loudly. The stale header comment that still described the deleted shortcut was corrected.
- **F7 (fread date corruption)**: `transaction_date` is read as character by column index (`col.names` is applied after type inference, so a named `colClasses` silently no-ops), and `FEC_PAS2_PARSER_VERSION` is folded into the `.rds` cache signature - without that bump the corrected parse would have been served straight back out of the stale cache.
- **`export_guards.R`** (G1/G2/G4/G5/G6/G7) and its runner, now also fingerprinted by `build_industry_report.ps1`. Guards gate publication rather than mutating the payload, so on a cache hit the export never runs and a newly added or tightened guard is silently skipped.

### Changes that move numbers but map to no audit finding

Both were bundled into the batch without a finding behind them. Both are R8-compliant and reviewed as sound; recorded here so neither is mistaken for an audit fix:

- **`collect.R` `match_variants`**: the FEC *query* variant subset is now decoupled from the *matching* roster, so an employer string equal to a subsumed roster variant matches exactly instead of falling to `review` as an unverified prefix. Shifts employee dollars from `review` to `accepted` (an undercount correction). Brings the scrape lane to parity with the name-pass and API lanes, which already matched on the full roster.
- **`identity_components.R` business addresses**: an employer-HQ address cluster can now attach to a person's home identity when exactly one personal component remains and a corroborating signal agrees. Purely additive (it only unites, so it cannot fragment an identity) and it changes unique-donor counts, not dollars.

### Verification

All 10 changed R files parse; `crypto_workbook_builder.mjs` passes `node --check`. The full offline suite is **12/12 PASS**, including `test_report.R`, which failed outright before this round on the `trade_overrides` crash. The beneficiary parity test's reference implementation was updated in lockstep with the direction split so the optimized-vs-reference guarantee still means something, and a new regression pins the mixed-direction case: 24E support $1,000 + 24A oppose $250 + 24K neither $40 must report support 1,000, oppose 250, total 1,290.

### Regeneration 2026-07-27

Five books rebuilt clean into `reports/r12-regen/`, each **0 formula-column failures, 0 integrity failures**: Credit Union (17.6 MB, 403,908 formulas, 26 sheets), Credit Arms, Community Banks, Crypto, Fintech. Every one passed `roster_coverage`, `category_agreement` (24-27 category x cycle totals agree across both views - F6 confirmed clean on live data), `date_sanity` (12 distinct months, no impossible years - F7 confirmed fixed) and, except Fintech, `category_population`.

**Trade Association went from $0.00 in every book to $129,321.44 across the five**, sourced entirely from `Unclassified`. Verified committee by committee on the Credit Union rebuild: America's Credit Unions PAC of CUNA $24,933.35, CULAC $13,114.56, NAHB BUILDPAC $5,250.00, US Chamber $3,500.00, Mortgage Bankers MORPAC $500.00 - all `type=Q, designation=B`, all previously `Unclassified`, which folds into the Candidate column. The two largest are `PAC OF <ASSOCIATION>` wrappers that only the broad net's containment match reaches.

Opposition exposure, measured by `direction_integrity` across all six books: **86.2%-94.6% of downstream dollars**, roughly $19.3B in total (P&C $6.31B/94.6%, Credit Arms $5.49B/93.0%, Credit Union $2.46B/89.0%, Crypto $2.15B/89.3%, Fintech $2.20B/88.8%, Community Banks $726M/86.2%). The audited $1.87B was one book's slice of it.

**Property & Casualty is BLOCKED and correctly so.** `roster_coverage` fails on Farmers Insurance: zero collected rows in every cycle, and no P&C database on disk contains a single Farmers row (all carry 9 companies, not 10). The shipped book has been publishing Farmers at $0.00 as though it were a real zero - exactly the F1/F4 failure the guard exists to stop. It needs a Farmers collection run, not a code change. Telecom was not rebuilt; it ships as three per-cycle files under a different name and needs its own sharded invocation.

### A false invariant, caught by the build

The first Credit Union rebuild failed with 272 integrity failures - the new `Support + Oppose <= Downstream Activity` assertion. It is not an invariant. FEC correction and refund rows are negative and carry neither a support nor an oppose transaction code, so they land in the third direction bucket and pull the unsigned total BELOW support + oppose. All 272 residuals were negative, to -$735,710 (Herschel Walker: $4,913,993 downstream against $5,649,703 of opposition). The assertion was removed and the behaviour documented on the sheet's own source note; the party-split identity check remains. The Support and Oppose columns are unaffected - only the false assertion about them was wrong.

Two remaining `category_population` warnings are unresolved questions, not failures: Community Banks reports no Company PAC dollars and Fintech no Trade Association dollars. Both may be genuine zeros; both are surfaced rather than silently rendered as $0.00.

The previously shipped books are untouched in `reports/`; the rebuilds are staged in `reports/r12-regen/` for comparison before any promotion.

## R13 - final collection, versioning, regeneration, and QA (2026-07-28)

This pass completed the R12 recovery through publication-ready staged artifacts. It
used six private collection workers, reconciled every shard into the seven canonical
databases, applied amendment/duplicate versioning, regenerated all nine expected
workbooks, and validated the published workbook/database fingerprints.

### Collection receipt

- The 70-company inventory is terminal: 20 companies are preserved in the original
  serial-sweep receipt and the remaining 50/50 private company shards reached
  `Company shard complete` with `quick_check=ok`.
- Reconciliation added 1,244 contribution rows and 40 corporate-giving rows
  (+1,284 total) in this pass. Earlier recovery work in the same run had already
  restored 84,036 P&C contribution rows and 87,886 Comcast rows.
- The final canonical databases all return `quick_check=ok`.
- These receipts prove worker execution and SQLite health. They do not convert an
  incomplete FEC walk into a complete one. Export guards still report:
  Zurich Insurance 2024 contributions 2,325/2,434 and 2026 contributions
  944/984; Lumen Technologies 2024 contributions 24,877/26,754 and 2026
  contributions 10,354/11,489; Comcast 2026 corporate rows 163/183; and the
  previously documented Crypto and Fintech completeness gaps.
- Unverified zeros remain visible rather than silently certified: Berkshire
  Hathaway 2026, Bitnomial 2022, and Polymarket 2022.

### Versioning receipt

Versioning was applied after reconciliation and before every final export:

| Industry | Superseded by amendment | Duplicate filing version | Duplicate memo | SQLite |
| --- | ---: | ---: | ---: | --- |
| Credit Union | 22 | 521 | 56 | `quick_check=ok` |
| Credit Arms | 0 | 206 | 22 | `quick_check=ok` |
| Community Banks | 0 | 2 | 0 | `quick_check=ok` |
| Crypto | 1 | 104 | 16 | `quick_check=ok` |
| Fintech | 2 | 55 | 9 | `quick_check=ok` |
| Telecom | 8 | 879 | 92 | `quick_check=ok` |
| Property & Casualty Insurance | 20 | 581 | 31 | `quick_check=ok` |

### Workbook regeneration receipt

All nine workbooks were regenerated through the guarded wrapper with the final
builder fingerprint. Every file is under the 45-minute performance budget, every
manifest matches its published SHA-256, and all 2,851,573 formulas report zero
formula-column failures and zero integrity failures.

| Workbook | Bytes | Worksheets | Formulas | Workbook SHA-256 | Database SHA-256 |
| --- | ---: | ---: | ---: | --- | --- |
| Credit Union | 17,607,431 | 26 | 404,314 | `948c1f02d90e15aeff2f9dd81bc8c2b86a8a6308ee8885ae16a4db38e9958909` | `12deab3410b2735beb15174761d303a8c8ac89d11b043fee22a4d3a21aa8e886` |
| Credit Arms | 15,273,615 | 27 | 418,234 | `0f025b4072737a352ab186a393bbe979c02bc3a55be95c13b7a4aea5f89c2681` | `57dd4e77781c28c2f90d974e061e380d6088e8a0eb1554bdfccaa0465a9a2de7` |
| Community Banks | 4,128,173 | 26 | 111,890 | `bf9932ba5bc4e520f3d4d68e18e1060b4c1d56ab2e0d2824dbad9740f23fb74e` | `9d2b397a6cb8fcdcd51a967431cefd59450904402a2c6c577e11b41fe50e624b` |
| Crypto | 6,197,598 | 26 | 166,633 | `423b5283b5586c39b5b437814a0496eaec044d0a25e81d503d9ef664ffe4ec62` | `9adb494ec77695e8286bfd3d9633fd679921a27f68a26ae3d01de715bfc058bc` |
| Fintech | 14,186,270 | 26 | 337,356 | `2a14580610ace5eb007da0b0a5b26488007bc88c17fdd1502b551ae6fb3cfe67` | `a821437f75de2825aadbda1788393711607d6a94ecbc3ae20a06b5d5aa062428` |
| Property & Casualty Insurance | 19,971,582 | 27 | 538,110 | `9cca8e05b62fb61b2670a1a59ff7fbfe27ffe645c84040d0789d5ba4aff4d9e6` | `82d152a197e0bcaa3a882b7425022121fc0231e31151d990da465cebbdcc2c14` |
| Telecom 2021-2022 | 11,340,228 | 23 | 313,734 | `d7bde8affd0101dd2ed1265eef091ee817eb41b152b8c2243d3921c5e99779db` | `5277eea80472d9a2dc76742c18b71d9915462a1e65de71b724541f97b2cdf11f` |
| Telecom 2023-2024 | 12,908,037 | 23 | 349,495 | `244e4136292f70ad04b1d285a38c98c1a3926d4113ac26a9390e2508b4755cbd` | `5277eea80472d9a2dc76742c18b71d9915462a1e65de71b724541f97b2cdf11f` |
| Telecom 2025-2026 | 7,405,681 | 23 | 211,807 | `0849b29060688f1ff90c72809487c964cf63fed08f17f2e62c85fa220ee00f47` | `5277eea80472d9a2dc76742c18b71d9915462a1e65de71b724541f97b2cdf11f` |

The final package audit also opened every workbook as Open XML, matched worksheet
and table counts to the manifests, rejected header-only tables, and recomputed each
source-database hash. A read-only Excel render exported all 227 worksheets to PNG;
the visual receipt matches each workbook SHA and rejects missing or monochrome
previews. The artifact-tool importer was also attempted at bounded 6 GB and 7 GB
heaps, but exhausted V8 while materializing even the smallest final workbook, so
the render pass used isolated Excel after preserving both failure receipts. The
Telecom build uses compact raw rows plus formula facts because its full JSON
payload would exceed the safe V8 envelope. The empty
`CycleExceptionsTable` path now emits one `Include in Analysis = No`, amount-zero
placeholder so Excel and the fail-closed table guard agree without changing totals.

### Code and test receipt

- Workbook heap planning is measured immediately before Node execution, capped at
  70% of available memory, and fails closed when the safe cap is below 8,192 MB.
- Sweep completion counts contribution and corporate checkpoints together; partial
  resources no longer create a false-complete receipt.
- The large-payload threshold is named and set to 120 MB so Telecom selects compact
  mode before a V8 out-of-memory failure.
- PowerShell runtime guards pass: heap plan, workbook-builder guards, and 15/15
  last-good guards.
- The full offline R suite passes 12/12. The Python verification harness passes
  39 unit tests plus the Telecom regression assertion. Node syntax checks,
  PowerShell parsing, and `git diff --check` pass.

### F6 disposition

**OPEN - not reproduced.** No Candidate/Leadership PAC swap reproduces in the live
snapshots. Candidate exceeds Leadership PAC by 4x-14x in every measurable cycle,
the classifier/reference registers are clean, the workbook mapping is
transposition-free, and the independent G6 category-agreement guard passes on every
final regeneration. The $162,458 trade-association total is across books and
book-specific, so it cannot explain an identical per-book amount near $163k.
Closure requires either reproduction against the originally audited artifacts or
an explicit policy decision to close as non-reproduced while retaining G6 as the
standing detector.

### Promotion status

The regenerated files are structurally and computationally clean and remain staged
under `reports/r12-regen/`. Data-completeness and zero-provenance warnings above are
not hidden; promotion is a policy decision about those disclosed source gaps, not
an assertion that the FEC delivery was complete.

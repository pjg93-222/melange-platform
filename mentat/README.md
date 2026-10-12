# Mentat R pipeline

Mentat has one entry point: `mentat.R`. It ingests the registers, collects FEC Schedule A receipts, reconciles the Crypto acceptance fixture, and writes the spending and QA reports. The modules in `R/` are implementation details and are sourced by the entry point.

The governing process is **Define → Collect → Identify → Connect → Count → Verify and report**. Its purpose is a coherent account of original employee and corporate giving counted once, with PAC outgoing activity shown separately. [METHODOLOGY.md](METHODOLOGY.md#the-six-step-process) defines each step, its evidence requirements and current implementation gaps. The user's request for near-perfect accuracy is a quality standard, not a literal numerical certification target.

The authoritative folder for viewing report documents and saving final Excel reports is **`C:/Users/pgoff/OneDrive/Desktop/Mentat Complete Files`**, configured in `config/report-directory.json`. The viewer must not substitute workbooks from Downloads, historical report directories or build caches. `MENTAT_REPORT_DIRECTORY` carries this same setting into isolated runtimes. Internal collection data, temporary build files and test fixtures remain separate from published reports.

The report builder now runs this process automatically before each workbook export, including cache hits. The published report workbook is the accounting of record, so the console's **Cross-reference** tab publishes no totals: it shows what the newest run covered and lists clear refunds, evidence-backed contradictions and unusually large counted receipts for inspection. Receipt size is informational only and never excludes a row; only contradictory evidence can remove its amount. Each run's full before/after accounting is saved with the run under `state/cross-reference/<run-id>/`. A saved run is an auditable computation; it does not certify that every relationship or missing disclosure has been resolved.

Cross-reference resolves observed receipts automatically when their evidence answers the accounting question. A row loses its amount only where evidence contradicts it: an explicit adjudicated attribution rejection or source exclusion, a non-giving receipt role, a proven memo or earmark duplicate of a counted parent, a supported superseded filing version, or reported identity evidence naming another organization. An automated search rejection, generic occupation, receipt size, or missing metadata is not a contradiction; the amount remains provisional and flagged. Text naming the listed company counts as `company_named_unverified`, and the recipient and role routes raise a row's attribution status without moving dollars. Receipt verification, company attribution and complete filing coverage remain separate. Filing checks are grouped in `coverage.csv` and `coverage_queue`; genuine receipt questions remain in `review.csv` and `review_queue`.

Historical individual-employer heuristic holds, including automated rejected matches, are rechecked against the same R matcher used during collection. Evidence-backed exact aliases live in `inputs/verified_employer_aliases.csv`; the matcher evaluates them before prefix heuristics and records the supporting URL and note. Equivalent punctuation/spacing can resolve an exact alias automatically. Explicit adjudications/source exclusions, conflicting source facts, organization ownership, typo-only strings and prefix similarities do not qualify. The runner fingerprints and snapshots the alias register, saves the old decision and matching result, and never edits the source database. It uses `MENTAT_RSCRIPT`, an installed `Rscript`, or the standard Windows R path; unavailable matching leaves the original holds intact.

Explicit non-giving receipt events are evaluated field by field before attribution that cannot affect giving. The R and Python paths share exact descriptions and anchored event patterns from `inputs/fec_receipt_rules.json`; a separate contribution statement and refund memo remains a conflict for review. The default dated PAC register is `inputs/verified_company_affiliations.json`. It accepts an exact committee ID, exact normalized canonical name, or listed exact alias during the evidence dates. Near names stay unresolved, and PAC activity remains separate from original employee or treasury giving.

Run the same process independently against an explicit quiescent database:

```powershell
python tools/run_cross_reference.py --database C:/path/to/mentat.db --industry Fintech --cycles 2022,2024,2026
```

It reuses the indexed evidence archive, then makes at most 100 new requests per run by default, prioritizing reusable filing headers and high-dollar missing receipt evidence. `--max-requests` changes this limit; `--offline` uses saved evidence only. `--evidence-dir` and `--header-dir` import existing verified FEC cache envelopes. Exhausted request budgets defer recovery instead of waiting for an hourly reset. Subsequent runs reuse completed recovery and the recorded attempt queue.

Every run writes `state/cross-reference/<run-id>/`: `report.json`, progress, an immutable `ledger.sqlite` containing original observations, decisions, links, used evidence documents and query provenance, source comparison, rule snapshots, and full change/review CSVs. The shared `evidence.sqlite` is an indexed reusable cache. Source databases and accepted workbooks are not rewritten by reconciliation. Missing or changed accepted source facts stop publication with a saved explanation; unresolved interpretation remains visible and provisional.

The CLI loads `inputs/verified_company_affiliations.json` by default and supports another dated affiliation package through `--affiliations`; filing evidence is supplied through `--filing-manifest`. Those packages contain evidence assertions and source URLs; loading them does not independently verify the assertion. Exact matching and effective dates are required. Full filing inventories, cross-stage completeness, corporate ownership evidence, Schedule B spending/refund coverage, and an independent accuracy audit remain requirements before asserting verified unique totals. The existing workbook still presents its separately labeled receipt accounting basis.

`MENTAT_CROSS_REFERENCE_ROOT`, `MENTAT_ACCEPTED_SOURCE_ROOT`, and `MENTAT_API_BUDGET_DB` pin shared state when the console creates isolated collection runtimes. The builder also accepts `-CrossReferenceOffline` and `-CrossReferenceMaxRequests`; `generate_industry_report.ps1`, `generate_crypto_report.ps1`, `parallel\Invoke-MentatDirectReport.ps1` and both parallel launchers declare and forward both, the launchers carrying them to `Invoke-MentatIndustryWorker.ps1` in the run spec as `cross_reference_max_requests` and `cross_reference_offline`. When an online cross-reference exits non-zero the builder retries it once with `--offline`, and if that succeeds it warns, records `cross_reference_mode: offline_fallback` in the build receipt and continues, so a lost network cannot block publication; a failure that also fails offline still stops the build. Restart an existing console server to load the new API; a separate `server.py --read-only --port 8811` preview can display results without interrupting collectors.

## Requirements

- Windows with R 4.6.0.
- Installed packages: `chromote`, `data.table`, `DBI`, `RSQLite`, `readxl`, `writexl`, `zip`, `jsonlite`, `digest`, and `curl`. If `httr2` is installed, the API engine may use it. `digest` provides SHA-256 identifiers for preserved source evidence.
- A free registered openFEC API key from https://api.data.gov/signup for the default API engine.
- No report workbook open in Excel while Mentat replaces it.

Set the R library before starting R or RStudio:

```powershell
$env:R_LIBS_USER = "C:/Users/pgoff/AppData/Local/R/win-library/4.6"
```

Set the API key in the environment. Mentat never prints or persists its value:

```powershell
$env:FEC_API_KEY = "your-registered-key"
```

`MENTAT_DATA_ROOT` holds all mutable state: `mentat.db`, checkpoints, the roster summary, optional `bulk/` supersets, and `reports/`. It defaults to `%LOCALAPPDATA%/Mentat`; set it to another local, nonsynchronized directory if desired. `MENTAT_BULK_DIR` can point the optional bulk inputs elsewhere.

## Standard from-scratch run

Use the resilient industry runner for production collection. It discovers
industries from `inputs/Company Name.xlsx`, gives each active lane its own API
key and SQLite shard, retries a failed R lane in a fresh process, then splits
unresolved work into fresh company-cycle processes before the gated merge.

```powershell
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  tools/parallel/Start-MentatResilientIndustryReports.ps1 `
  -Industries Fintech -NoCache
```

Omit `-Industries` to run every roster-discovered industry. Every run writes
atomic fanout, coverage, warehouse, generator, and phase-timing receipts below
`.cache/resilient-industry-runs/`, including one summary timing line per
industry.

`tools/parallel/Start-MentatIndustryReports.ps1` retains its historical CLI,
but now delegates from-scratch work to the resilient runner. Its long-lived
serial Chrome collection is reachable only with the explicit
`-LegacySerialCollection` switch. Report-only `-SkipCollection` behavior is
unchanged.

Directly sourcing `mentat.R` is intended for focused development and explicit
single-process diagnostics:

```powershell
& "C:/Program Files/R/R-4.6.0/bin/Rscript.exe" mentat.R --skip-scrape
```

The R entry point defaults to the FEC JSON API, all three cycles, up to eight
adaptive concurrent work units, and the contributor-name identity pass
disabled. Use `--phase2-names` to opt in to that request-heavy pass.

Supported flags are documented at the top of `mentat.R` and by `Rscript mentat.R --help`:

```text
--cycles=2022,2024,2026
--data-root=PATH
--engine=api|chrome
--concurrency=N
--skip-scrape
--phase2-names
--skip-phase2-names
--resume-after=YYYY-MM-DDTHH:MM:SS...
--retag-only
--help
```

The equivalent environment controls are `MENTAT_ENGINE` (default `api`), `MENTAT_CONCURRENCY` (default `8`), `MENTAT_DATA_ROOT`, `MENTAT_BULK_DIR`, `MENTAT_RESCUE_CAP` (default `50` requests), and optional `MENTAT_SPENDING_TEMPLATE`. The Chrome engine remains available as a fallback with `MENTAT_ENGINE=chrome` or `--engine=chrome`.

`--skip-scrape` performs ingest, fixture reconciliation, and reporting without opening a browser or making an API request. `--retag-only` recomputes stored leg and company tags and rewrites reports without ingest or collection.

## Add an industry

Industry onboarding is data-driven:

1. Add a worksheet to `inputs/Company Name.xlsx`. The worksheet name is the
   industry name. Put one company block per blank-line-delimited section; the
   first parsed variant is the canonical company. Prefix exclusion patterns
   with `NOT `.
2. Run the single offline readiness command:

   ```powershell
   & "$env:LOCALAPPDATA/Mentat/venv/Scripts/python.exe" `
     tools/validate_industry_coverage.py `
     --roster "inputs/Company Name.xlsx" `
     --output ".cache/industry-roster-readiness.json"
   ```

   The receipt reports parsed industries, companies, variants, exclusion
   coverage, duplicate/ambiguous variants, and a fail-closed `ready` result.
3. Run the standard resilient command with the new worksheet name:

   ```powershell
   & powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
     tools/parallel/Start-MentatResilientIndustryReports.ps1 `
     -Industries "New Industry" -NoCache
   ```

No script-maintained industry list or per-industry runner is required.

## Collection and correctness rules

The cross-industry accounting standard is [METHODOLOGY.md](METHODOLOGY.md). It defines the measurement, evidence requirements, recovery strategy and validation limits. Current receipt totals must not be described as unique contributions across filings or end-to-end spending.

The API engine uses `/v1/schedules/schedule_a/`, 100 rows per request, descending receipt-date sort, and openFEC keyset cursors. It requires an exact, stable `pagination.count`, rejects partial or cyclic cursors, and adapts concurrency downward after a 429 and upward after successful batches. Counts that move during a crawl leave the snapshot unverified, even if its final size matches an earlier count. Failed pages remain checkpointed; recollect that scope in a fresh shard. Retryable responses honor a per-request `Retry-After` capped at 120 seconds. Raw pages, cursors, and a persistent 1,000-request hourly budget are checkpointed; the resilient runner shares that budget across processes. Logs include requests used and remaining. The API key is never checkpointed.

Every roster spelling is searched literally. Completed work carries its request and attribution-input contract; older or changed contracts cannot resume under the new rules. The coverage gate verifies every employer and corporate alias, not just the presence of one completed search of each kind. An empty query must also have completed evidence.

The optional `bulk/indiv22_crypto_superset.psv`, `indiv24_crypto_superset.psv`, and `indiv26_crypto_superset.psv` files build an observed-employer register through the same matcher used for API rows. A broad query that repeatedly times out is split into exhaustive date windows using the same terms. Their union must preserve observed IDs and have no overlap. Narrower observed spellings can recover evidence inside a failed window, but cannot certify the broad query's coverage. Bulk candidate `sub_id` values still missing afterward receive exact-ID name rescue, capped by `MENTAT_RESCUE_CAP`; never-fetched overflow is written to `reports/bulk_rescue_overflow.csv`. `reports/bulk_audit.csv` and `reports/bulk_audit_unmatched.csv` provide company-cycle completeness counts and remaining IDs.

Employer attribution uses exact aliases, prefix-anchored corporate qualifiers, and narrowly qualified squashed prefixes for forms such as `BINANCEUS`. Nonqualifying anchored extensions go to `reports/review_queue_company_match.csv`. Decisions in `inputs/company_match_overrides.csv` persist across runs. Roster exclusions inspect contributor name, employer, and occupation on every pass. The data-driven nickname seed is `inputs/name_diminutives.csv`; variants shared by more than one canonical name are not used alone to establish identity.

Organization entities and company-name contributors are emitted to `Sheet4` as `CORPORATE` / `Corporate`; they never enter Chief, Middle Management, or Employee tiers. This is a grouping, not proof of corporate treasury funding. The Funding Cohorts audit additionally separates individual, corporate treasury, and PAC/committee sources and identifies money received by a company PAC. Company PAC receipts and that PAC's outgoing spending are different stages.

FEC form, receipt line and transaction evidence determine whether a receipt represents a contribution, transfer, or other economic event. Form 3X line 17 includes genuine hybrid-PAC and national-party special-account gifts; its generic “other receipts” label is not a reason to exclude them. A company name never establishes the transaction role. Missing or ambiguous role evidence retains a provisional amount and an uncertainty flag, not a fabricated zero. Missing or invalid amounts and contradictory source identities still stop publication. The exporter writes a `.receipt-review.csv` beside the pending payload with unresolved records. See `inputs/fec_receipt_rules.json` for shared rules and FEC sources.

Conduit and intermediary legs retain the recipient-as-shown treatment. OTH transfer index v6 and PAS2 parser v9 default to `count_all`: distinct source observations remain provisional when their filing relationship is unknown. Equal attributes, reused transaction labels and a larger filing number do not prove duplication or supersession. Exact repeated observations can be deduplicated; verified filing-family headers can establish supersession. Conflicting facts for one source ID stop indexing. Legacy `count_latest` and `withhold` remain separately keyed sensitivity policies, not the default. Memo exclusions require an explicit compatible reference to a retained parent; matching amounts or disclosure wording alone are insufficient. Raw records are preserved. The console labels saved downstream PAC activity as observed activity, not traced donor dollars; it displays source coverage, measurement differences and signed amounts, and withholds uninterpretable percentage shares. See the [September 20 correction audit](../audits/mentat_contradictions_only_20260920.md).

## Source and dollar reconciliation

Downstream reports traverse the complete saved positive-transfer network with cycle detection. Beneficiary views count each reachable spending committee once per cycle/view/company, including committees reached through multiple funded roots; transfer amounts are never added to candidate spending. Associated receipt amounts remain at their actual recipient. The console can follow complete workbook Transfer Paths when no API snapshot is saved. PAC snapshot collection follows recipient PACs automatically within its explicit committee/cycle budget and records any uncollected frontier. Downstream Reach, Downstream Measurement and Downstream Sources preserve route associations, same-input before/after algorithm ledgers and source hashes in the Overview, Data workbook and audit CSV bundle. These changes apply to subsequent builds; existing reports keep their saved measurements. See the [systemic downstream correction](../audits/mentat_downstream_system_20260920/REVIEW.md).

Report-only builds require an explicit `-DatabasePath`. Before export, the builder compares every scoped FEC source ID and its financial facts with the last accepted source. Missing or changed facts block publication and produce a source-change ledger. A first cycle-specific build also checks the accepted full-report baseline. Added records remain visible in the ledger.

Before installing a workbook, including on cache hits, the builder compares the full payload with those source facts and records every changed inclusion, company, tier, party or category decision and its dollar effect. New evidence-policy payloads retain all raw attribution decisions. Every scoped source requires an explicit accounting decision; omitted rows, altered amounts, unexplained exclusions and memo exclusions without a counted counterpart block publication. The accepted pointer advances only after the workbook is installed.

The final payload check rejects established non-giving or contradictory counted roles, invalid inclusion decisions, source-cycle mismatches and incomplete memo links. Missing form/line/role evidence remains provisional and visible in the ledger. This applies equally to newly exported and cached payloads. Passing these checks verifies source and decision consistency under the declared receipt view; it does not establish unique end-to-end dollars. Historical reports retain their original rules until explicitly rebuilt.

Durable comparison state lives in `state/accepted-report-sources/`. **Back up this directory with the source databases; it is not a disposable cache.** Each report has a `.source-changes.json` companion pointing to immutable snapshots and line-level source/dollar ledgers. The baseline is a historical comparison point, not a certification that its old classifications were correct. Do not erase it to force a smaller report through.

Legacy receipt metadata can be recovered into a private copy using saved FEC API pages and, optionally, exact source-ID API requests:

```powershell
python tools/hydrate_receipt_metadata.py C:/source/mentat.db C:/recovery/mentat.db
python tools/hydrate_receipt_metadata.py C:/source/mentat.db C:/recovery/mentat.db `
  --fetch --max-requests 100 --company Stripe --cycle 2024
```

`--source-id` can be repeated for targeted recovery. Requests use the existing encrypted FEC key store. The tool validates source ID, committee, donor, date and amount before changing metadata, records each change in `receipt_metadata_history`, and leaves the original database intact. Its hydration receipt identifies the requested scope and remaining IDs; `complete` applies only to that scope. Run without filters to audit the whole copy. A changed FEC financial fact requires reconciliation, not a metadata overwrite.

The existing partisan percentages remain conditional on D+R receipts, with Party Coverage showing (D+R)/total. Funding and recipient views describe the same money and must not be added together. Receipt eligibility and QA countability use the same predicate.

## Spending workbook

`reports/spending_report.xlsx` contains:

- `Stats (XXX Company)`, preserving the supplied formula layout and its repeated-Super-PAC/Carey header quirk, with Corporate Giving appended and, since 2026-10-09, a Conduit (ActBlue, WinRed) column group directly after each Carey group (100 metrics; the template supplies only the column widths, and each Conduit group borrows the widths of the Carey group it follows);
- `Employees (XXX Company)` for individual contributors only;
- one presentation sheet for every organization/cycle with rows;
- one `Crypto Industry <cycle>` presentation rollup for every cycle with rows;
- `Sheet4`, the single A:G fact table used by every Stats formula.

Presentation sheet columns are `Name | Recipient | Date | Amount | CSZ | Employer | Occupation`. Excel names are sanitized to 31 characters. Case-insensitive collisions use deterministic `~2`, `~3`, … suffixes after truncating the base to make room.

Recipient registers retain 527 and 501c classifications, but their Sheet4 records use `EXCLUDED_527_501C` and do not enter reported totals. Unclassified recipients are exported to `reports/unclassified_recipients.csv`; the report fails if its counted category columns do not reconcile to Total Giving.

All CSV report writes are UTF-8 with a BOM for Excel on Windows and are installed atomically. Workbook output is rebuilt with stable entry order, normalized timestamps, and atomic replacement.

## Offline tests

Each test uses synthetic fixtures or temporary data roots and makes no network call. One exception announces itself: `test_collection_console.py` skips its R-ingest test unless `inputs/fec_beneficiary` and `inputs/fec_committee_master` are present, because it stages the real project; those official bulk snapshots are not kept in git, and `python tools/refresh_fec_sources.py --root <folder>` downloads them.

```powershell
Get-ChildItem tests/test_*.R | ForEach-Object {
  & "C:/Program Files/R/R-4.6.0/bin/Rscript.exe" $_.FullName
  if ($LASTEXITCODE -ne 0) { throw "Test failed: $($_.Name)" }
}
```

PowerShell-specific runtime guards are separate from the R fixture suite:

```powershell
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  tests/Test-MentatNodeHeapPlan.ps1
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  tests/Test-MentatWorkbookBuilderGuards.ps1
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  tests/Test-MentatFundingLabels.ps1
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  tests/Test-MentatReportDirectory.ps1
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  tests/Test-MentatLastGoodGuard.ps1
```

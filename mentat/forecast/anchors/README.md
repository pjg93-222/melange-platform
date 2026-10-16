# Giving Forecast anchors

Mentat's own levels that the Giving Forecast applies its estimated relationships to (owner rulings of 2026-09-30: the forecast applies to
MENTAT data; OpenSecrets only estimated the relationships). One read-only builder writes one snapshot; the console reads it.

```
& "$env:LOCALAPPDATA\Mentat\venv\Scripts\python.exe" mentat\forecast\anchors\build_giving_anchors.py            # build all four datasets
  ... build_giving_anchors.py --industry credit_unions                                                          # one registry id
  ... build_giving_anchors.py --check                                                                          # fresh (exit 0) or stale / missing (exit 2)
```

Output: `mentat\state\giving-anchors\giving-anchors-v1.json` and `giving-anchors-v1.receipt.json` (runtime state, brain D-048: never in git;
`MENTAT_GIVING_ANCHORS` overrides the path the console reads). The schema and a real example live in `mentat/forecast/contracts`.

## What it reads (everything read-only)

| Input | Where | Used for |
|---|---|---|
| Published workbook | report directory (`config/report-directory.json` or `MENTAT_REPORT_DIRECTORY`) | its sha256 is the freshness key |
| Contribution-analytics index | `state/contribution-analytics/*.sqlite`, the newest whose `provenance.workbook_sha256` equals the workbook's | general giving (`receipt_cents`, the workbook's Analysis Amount, less the non_giving receipts: see the first rule below), funding-source split, included-receipt D/R by cycle |
| Pinned source database | `.cache/...` (registry globs), the file whose sha256 equals the index's `database_sha256` | FEC `recipient_committee_type` (H / S) joined on sub_id; the collection date (`MAX(scraped_at)`); work-unit progress |
| FEC pas2 / cn / cm bulk files | `inputs/active_fec_sources.json` -> `fec_beneficiary/pas2YY`, `fec_beneficiary/cnYY`, `fec_committee_master/cmYY` | the roster companies' PACs' own giving to House / Senate candidates (D-049 `count_latest`) |
| Melange LDA | `%LOCALAPPDATA%\Melange\lda_rebuild\derived\sector_spend\sector_spend.csv` | `income_total` and `expenses_total`, never summed; bank TOTAL for banks |
| CPI | `sector_growth_study/data/00_controls.xlsx` | `cpi[2025] / mean(cpi[t-1], cpi[t])` per cycle (2026 has none: face value) |

Every SQLite file is opened `mode=ro&immutable=1` with `PRAGMA query_only=ON`. No receipt-counting or collection code is imported or run.
The two output files are the only thing written, through a temporary file and an atomic replace, and only after the snapshot validates
against the shared schema and invariants; an invalid build leaves the previous snapshot in place. Every run emits Glass Box events
(`ANCHORS_GLASSBOX_DIR` overrides the folder; lane `claude`, source `giving-forecast-anchors`).

## Rules it implements

The command-line builder emits flushed JSON stage start/finish/failure records to stdout, including elapsed time. Per-industry workbook, index selection, pinned-source hashing, collection date, index aggregation and progress reads are separately timed. This diagnoses a timeout without changing its deadline, retry policy or any model gate; library calls remain quiet unless `progress=True`.

The legacy `employee_candidate` contract key names included receipts to House/Senate candidates across all funding sources except `non_giving`; it does not certify employee-only giving or verified funding identity. Relevant conflicting committee types make this candidate basis unavailable, with signed/gross exposure in its reason; general giving retains the money.

* **Non-giving receipts are left out of the forecast** (owner ruling 2026-10-01, forecast-integrity decision D4 option b). Receipts that Mentat's accounting
  engine buckets as `non_giving` (the index's `funding_source` is the engine's bucket; the FEC records them as interest, refunds, insurance claims, loans)
  are in the workbook's Analysis Amount but are not giving. In the forecast only, `mentat_sources.FORECAST_EXCLUDED_FUNDING_SOURCES` (`{"non_giving"}`) leaves
  them out of (1) each cycle's `general_giving.cycles`, (2) the monthly `observed` cuts and the observed-to-date amounts, and (3) the House / Senate included-receipt
  `candidate_receipts` (cycles, observed cuts, thin-base flags). The workbook, the index and every money-collection view are untouched.
  `general_giving.by_funding_source` stays the full split of the Analysis Amount; `general_giving.excluded_non_giving` and `candidate_receipts.excluded_non_giving`
  report what was left out, per cycle (levels + excluded = the published Analysis Amount), and the industry flag prints the amounts. The index's own
  receipt-total check still compares the full Analysis Amount with the index manifest. A partial build (`--industry`) carries over only industries whose
  published general-giving and candidate blocks both name exactly `FORECAST_EXCLUDED_FUNDING_SOURCES` as the sources they left out (anchors from before the
  rule name none, and an empty list names none either); the others are listed as `unavailable` until rebuilt.
* **Coverage end** (by rule, because none of the pinned databases has a verified coverage-through date): the last calendar quarter-end whose
  report due date (the 15th of the following month) was on or before the collection date. Banks, P&C, Community Banks: 2026-06-30. Credit
  unions are cut back to 2026-05-31 ("June incomplete") for as long as the rule end is 2026-06-30. Observed amounts are cumulative month-end
  cuts of the open cycle up to the coverage end; rows dated later stay in the remainder window.
* **Lobbying nowcast** (owner rulings 2026-10-08): each LDA series carries `partial.nowcast` = `O + (1 - s) B` (`lda.nowcast`): `O` = filed so far this year through the latest complete quarter,
  `B` = the prior full-year total, `s` = the geometric mean of the share filed by that quarter in up to the 3 prior years. It is the remaining-share form of the PAC panel's R8 nowcast, not the pace
  `O / s`. The snapshot's `lda_nowcast_errors` is the blind-test table that bounds it: for each quarter cut, the errors `log(nowcast / actual)` of every complete year with a complete prior year,
  both series, the pool sectors bank, credit union, telecom and fintech (n = 32 with the 2022-2025 targets; crypto is left out, its early years are too thin), as `{n, q80, max}`. They are computed here, at
  anchor-build time, from the same `sector_spend.csv` (the console cannot see the other pool sectors); the LobbyView persistence errors that bound the years after the nowcast year come from the refit bundle.
  A quarter counts only once it has a filing AND its LD-2 deadline (Q1 Apr 20, Q2 Jul 20, Q3 Oct 20, Q4 Jan 20 of the next year) is on or before the producer's sweep date (`scope_sweeps.<year>.sweep_through_dt_posted`
  of `fullscope_receipts.json` in Melange's data root), so a few early filings never move the cut (`lda.quarter_table`; the Melange industry page's rule). Without that date the LDA block is unavailable.
* **Lobbying history** (Paul, 2026-10-09, "visualized in the same manner" as Melange's lobbying card): each ready `lda` block also carries `counts`, `{year: {organizations, lobbyists}}`, the `distinct_organizations` and
  `distinct_named_lobbyists` of the same `annual` rows of `sector_spend.csv` (the partial year's too), so the console can print what Melange prints under the card. The catalog serves the history per industry as
  `catalog.anchors.industries.<id>.lda_history` (`giving_forecast._lda_history`): annual rows `{year, hired_firms, in_house, orgs, lobbyists}` (`income_total`, `expenses_total`), the partial row with its `through_quarter`,
  and the source `sha256`. `tests/test_giving_lda_history.py` reconciles it to Melange's `sector_spend.json` to the cent. The two series are never added anywhere (D-064: an in-house filer's expenses already include
  what it pays outside firms); `tests/test_lobby_series_never_summed.py` pins that.
* **Own presidential-cycle ratio** (display only; the forecast keeps the owner rule, D-121): the catalog adds `catalog.anchors.industries.<id>.own_cycle_ratio` (`giving_forecast._industry_cycle_ratio`), the latest presidential cycle's
  `Total Giving` over the midterm cycle before it, read from the `Spending Overview` sheet (column A cycle, B scope, C total) of the published workbook the anchors cite. It is absent when either total cannot be read.
* **Thin base**: under $100,000 of D + R per chamber, per cycle, per basis (`thin_base`).
* **Roster PACs**: `registry.json`. Trade-association PACs are not counted. Credit unions have one PAC (SchoolsFirst), flagged `single_pac`.
* **Separate datasets**: `community_banks` is its own entry (`forecast_industry: null`), never merged into banks. Auto has no Mentat workbook
  and no entry. P&C has general giving and candidate receipts, no roster PACs and no Melange LDA sector (both `unavailable`, with the reason).
* **Refusal**: an industry whose published workbook has no matching index, or whose pinned source or FEC input cannot be verified, is listed in
  `unavailable` with the reason, never invented. The console refuses (`anchor_stale`) when `mentat.workbook_sha256` differs from the workbook
  published now.

## Running it on a schedule

The build takes about a minute on this box (Banking's 861 MB source database join is most of it; it ran locally, so the RunPod pod was not
needed). Run it nightly and after each Mentat publish, FEC refresh or Melange LDA refresh. A scheduled task is Paul's to create: this lane
changed no publish or refresh script. `--check` exits 2 when a workbook was republished since the last build.

## Tests

`python -m pytest -p no:cacheprovider mentat/tests/test_giving_anchors.py` builds against a synthetic Mentat (hand-computed expectations,
D-049 cases, the non_giving exclusion in every sum, coverage rule table, read-only and refusal guarantees, the shared negative controls).
`MENTAT_ANCHORS_REAL=1` also re-derives the credit-union numbers of ADDENDUM section F (less the non_giving receipts) from the real tree, `=all` the
banks and property & casualty too (about four minutes).

## Odd-year split (cycle model, 2026-10-07)

`general_giving.odd_year` and `pac_to_candidates.odd_year` give, for every COMPLETE cycle, the amount dated on or before December 31 of the cycle's first year (same population and
basis as `cycles`: general giving after the non_giving exclusion; the roster PACs through the same D-049 aggregation, cut at that date; an undated row is in the cycle total and in no
odd-year amount). The cycle model's industry budget change needs the anchor cycle's share `odd_year[a] / cycles[a]` per measure. Optional fields: a snapshot built before them stays valid,
but the cycle model then offers no default budget change for that measure ("Rebuild the anchors").

## Party split of the credit unions' company PACs (2026-10-08)

The credit unions' company PACs are the published PAC panel's 16-PAC cohort (D-117), not the snapshot's single committee. The panel bundle holds that cohort's House+Senate totals only, so the D/R
split the "Company PACs" row of the party split divides is pinned here: `panel_cohort_split.json`, written by `build_panel_cohort_split.py` from the partisan-dollar lane's own canonical ledgers
(basis `canonical_cycle_office_nominal_pac_D_R`; the 2023-24 cycle by chamber, and 2025-26 D/R to the panel's cut). Only the rows the panel counts (`hs` == 1) are split, so D + R + other / unknown
recipients + the lane's unresolved-office pool equals the panel's 2023-24 total and its observed amount to the cent. The calculator reads the file only when its cohort, cut and both totals match the
published panel (otherwise the row says so and the dollars are unaffected). When the panel's cut or totals move, rerun the builder (`--check` exits 1 when the file is stale); the snapshot itself is not touched.

## Telecom's company PACs (2026-10-08)

Telecom's company PACs are the published PAC panel's 8 telecom PACs (`COHORTS['telecom']` in `app/pac_giving_forecast.py`; the snapshot has no roster PAC for Telecom: its `pac_to_candidates` stays
unavailable). The calculator reads them through the same code path as the credit unions' (`PANEL_COHORT`, `_r8`: the panel's own nowcast O + (1 - s) B, then x1.09 for 2027-28), labelled
"Telecom: 8 PACs (published panel cohort)". Their D/R split is pinned in `panel_cohort_split_telecom.json` (one file per cohort; the credit unions' `panel_cohort_split.json` is untouched),
written by the same `build_panel_cohort_split.py` (it now writes and `--check`s both) from the partisan-dollar lane's ledgers, with the same to-the-cent reconciliation against the panel: the
2023-24 total $7,834,150 and the observed $4,427,500 to the cut 2026-09-26 (D + R + other / unknown recipients + the lane's unresolved-office pool). One of the cohort's 8 ids (C00377044) has no
row in either ledger; the file lists it in `silent_committees`. The chamber split itself needs a House / Senate share model, which Telecom does not have of its own (registry `withheld_by_model.control`: no B09 recipient capture in the
refit). Since the owner ruling of 2026-10-09 (option A) it is SCORED on the pooled share models every industry uses (`POOLED_SHARE_SCORED` in `app/giving_forecast.py`): the included-receipt basis divides
Telecom's Mentat candidate receipts, the company-PAC basis this cohort's D / R; the pooled coefficients, the pooled residual variance and the reference level of the sector term (the models are
applied as a change from the anchor cycle, so Intercept and the industry terms are 0 in it) are all that is used, and the Methods fold says so.

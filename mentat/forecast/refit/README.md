# Giving Forecast refit pipeline (Phase 1 parity, Phase 2 bundle v2, Phase 5 refit agent)

R pipeline that rebuilds every number of `mentat/app/data/giving-models-v1.json` from **raw inputs** and proves it reproduces the
frozen v1 bundle and every Codex design file to the spec's tolerances. Phase 1 makes **no methodology change**: the v1 variable
selections are frozen, there is no DL blend in the bundle output, no anchor change, no seats removal and no v2 schema. The DL
blend, backtests, prediction intervals and the rule auto-pick exist as tested library code and are not used by the v1 projection.

Design spec: `SPEC.md` of the Phase 1 architect (scratchpad `phase1_spec`). Rulings applied: clubSandwich from a pipeline-owned
library outside git with a hash manifest, every input pinned (including `interpretable_effects.csv` and `analyze.py`, which the
v1 builder read but did not list), lobbying sample = all complete years from 2008, ordinary p primary, Paul's 2026-09-30 slope-rule
auto-pick.

## Run

```
Rscript run_refit.R --mode parity [--run-id <id>] [--no-tests]      # rebuild + compare + run folder; never publishes
Rscript tests/testthat.R                                            # the parity suite (RF_TEST_FILTER=<regex> for a subset)
Rscript tools/parity_report.R                                       # measured max deviation of every parity target -> parity_deviations.csv
Rscript tools/make_inputs_manifest.R                                # regenerate registry/inputs.json (only for a new input vintage)
Rscript tools/record_rlib_manifest.R                                # record the vendored package hashes of this platform
```

Windows: `& "C:\Program Files\R\R-4.6.0\bin\Rscript.exe" ...` (R 4.6.0 or later).

### Environment variables

| Variable | Meaning |
|---|---|
| `RF_RUNTIME` | runtime folder: raw vault, runs (default `%LOCALAPPDATA%\Mentat\forecast\refit`, `<MENTAT_DATA_ROOT>\forecast\refit`, or `~/.local/share/Mentat/forecast/refit` off Windows) |
| `RF_RLIB` | vendored package library (default `<RUNTIME>\Rlib`): clubSandwich 0.7.0, sandwich 3.1-3 |
| `RF_INPUT_ROOT` | one folder mirroring the input roots: `LOO`, `CHE`, `SGSD`, `SGSA`, `RLIBX`, `House_Senate_Control_Forecasts_CSV.zip` |
| `RF_ROOT_<ALIAS>` | override one alias (`LOO CHE SGSD SGSA ZIPC RLIBX BUNDLE_V1`); wins over `RF_INPUT_ROOT` |
| `RF_GLASSBOX_DIR` | Glass Box event folder (default `C:\Users\pgoff\.claude\glassbox\events`) |
| `REFIT_OUT` | where run folders go (default `<RUNTIME>`) |
| `REFIT_CORES` | workers of the parallel batteries; `1` = serial (default: serial on Windows, detectCores()-2 elsewhere) |
| `RF_TEST_RUNTIME` | tests only: keep the raw vault between test runs |

Parallel runs are **bit-identical** to serial runs: no unit of work uses R's global RNG. Every stream comes from explicit entropy
through `R/rf_rng.R` (numpy SeedSequence of `c(seed, crc32(model), crc32(term), crc32(cluster))`, or the stepwise case counter),
results are combined in input order, bootstrap blocks return integer hit counts. `tests/testthat/test-13-parallel.R` proves it
(`REFIT_CORES=1` vs 2 on a bootstrap enumeration, the backtest folds and the stepwise grid). Set `OPENBLAS_NUM_THREADS=1` on a
machine with a threaded BLAS so a matrix product cannot differ in its last bits between runs.

## Inputs

`registry/inputs.json` pins 121 files by sha256 (OpenSecrets captures, FEC candidate masters, IBISWorld / CPI / control / LobbyView
workbooks, the IEM zip, the four 2026 daily market files, and the Codex design files as **parity references only**). Aliases
resolve through `R/rf_config.R`. On first sight each file is copied into `<RUNTIME>/raw/<sha256>/<name>` and read from there, so
a Downloads / Temp clean-up cannot break a refit. A pinned file whose hash changes fails gate G1 until the manifest is regenerated.
`registry/industries.json` lists the six fitted industries and four declined ones (with reasons); property and casualty is a ready pilot using the owner-approved F09 (all-insurance) baseline. Its scope caveat and
2024-11 current-year revenue estimate are explicit; F3400 is retained only as a superseded, unavailable page candidate.
`registry/models.json` holds the frozen specifications and the package version pins.

## What a parity run does

1. G0 environment (R >= 4.6.0, package pins from the vendored library, `rf_rng.R` hash).
2. G1 inputs (every pinned hash, vault copy byte-equal).
3. Panels from raw inputs: PAC growth panel (21 rows), congressional money panel (42 rows; 16,916 recipients, 64 presidential
   overlaps, 22 FEC identities), IEM Jan-Jun control score, seat calls, 48 stepwise cases, lobbying levels (77) and differences (71). G3 panel checks.
4. The frozen lm() fits, PAC forward-AIC selection-repeated p (22 of 216), lobbying wild p (1,432 of 46,656).
5. The 48-case stepwise grid: masks, CR2, selection-repeated bootstrap on the reproduced numpy stream, Holm, nested validation.
6. The v1 bundle projection, compared leaf by leaf with `giving-models-v1.json`.
7. Review files: `regressions.txt` (real `summary(lm())` plus the labelled robustness block of every model), backtests, the rule-selection table.
8. G2 parity: the parity test files run inside the run.

Run folder `<runs>/<run_id>/`: `panels/*.csv`, `bundle/giving-models-v1.projection.json` (never published), `review/{regressions.txt,
backtests.csv, rule_selection.csv, selected_models.csv, nested_validation_metrics.csv, inputs.csv, gates.json}`, `receipt.json`
(every input hash, every code file hash, packages, gates, parity counts, bundle hash, decision), `log.txt`. Decision: `PARITY_GREEN` or `FAIL`.
Every stage and gate emits a Glass Box event (`kind` run.start / stage.start / stage.finish / gate.result / run.finish, lane `claude`,
source `giving-forecast-refit`).

## Library code for later phases (tested, not wired into the v1 output)

* `rf_partial_pool`, `rf_dl`: DerSimonian-Laird partial pooling toward the pooled slope (spec 3.8).
* `rf_backtest`, `rf_backtest_metrics`: leave-one-election-out, chronological and rolling-origin backtests with R0 / R1 / R2 rules, fold-restandardized growth, anchored-step prediction intervals.
* `rf_select_rule`, `rf_select_rules`, `rf_choose_rule`: Paul's auto-pick: lowest MAE in both designs, blend when they disagree, pooled reported when the blend resolves to it. `RF$RULE_MIN_GAIN` (default 0 = literal rule) sets an optional clear-winner margin.
* `rf_step_sigma2`, `rf_interval`: prediction intervals beside the coefficient interval.

## Tests

`tests/testthat/test-01 ... test-13`: registry, pinned inputs and vault, panels vs Codex design files, coefficients vs the v1 bundle,
bootstrap / selection-repeated p-values, nested validation, partial pooling, backtests vs `SP/tailoring/summary.csv` and the SPEC section 8 table,
rule auto-pick, the whole v1 projection, the run (read-only, events, receipt, failure path), no-network scan, parallel == serial.
Fixtures in `tests/testthat/fixtures/` are copies of the earlier audit lanes' outputs (`robustness_pvalues.csv`, `fold_coefficients.csv`,
`full_sample_slopes.csv`, `summary.csv`, `pac_prevchg_reconcile.csv`), used as numerical references.

## Phase 2: bundle v2 (asserted against the shared contract)

`rf_v2_context()` computes everything a refit needs; `rf_bundle_v2()` writes `giving-models-v2.json` exactly as `mentat/forecast/contracts/giving-models-v2.schema.json`
demands (objects are closed; the schema, the example and the 16 shared negative controls are the contract and are read-only here). `R/rf_schema.R` validates with a hand-written
validator of the contract's keyword subset (no JSON Schema package is installed); `tools/check_bundle_contract.py` runs the contract's own Python validator on a bundle as a second
opinion, and `test-15-bundle-v2.R` asserts: the example validates, every shared control is rejected on the example AND on the writer's output, the writer's output validates in R and
in Python, plus the numbers of ADDENDUM section F.

* PAC: the auto-pick (`rf_select_rule`, a non-pooled rule must beat the pooled slope by 5% in every design) chose own slopes (R1). `pac_model.slopes[id].applied` is the slope the
  calculator uses; `prediction.industries[id]` holds each industry's anchored-step residual (pairs (t-2, t)); `evidence` (open object) records the rule-selection table and the
  calculator-path leave-one-election-out MAE per industry, which a later refit's G6 compares against.
* Shares: pooled slope (R0); the anchor is selected per industry and chamber, using same-phase only after a >=5% win over previous-cycle in both validation designs; Polymarket-to-Polymarket control inputs (`political_inputs` 2024, `saved_outlook` 2026, Kalshi and the equal weight kept as sensitivities).
* Lobbying: `lobby_model.log_refit` (`dly ~ dg + factor(year)`, -0.811% per point, p 0.029), the dollar model kept as evidence.
* Seats are evidence only (`evidence_models`, `calculator: false`). `pac_complete` and the lobbying differenced-controls regressions stay in `regressions.txt`: the contract's evidence model requires a chamber.

## Phase 5: the refit agent

```
Rscript run_refit.R --mode detect                                  # read-only scan of Mentat's report directory; queue industries the registry does not know
Rscript run_refit.R --mode refit [--industry <id>] [--publish auto|never]
Rscript run_refit.R --rollback <model_version>
```
* **Watcher** (`rf_agent_detect`): lists `* Industry Spending Overview.xlsx` in the report directory (mentat/config/report-directory.json, or `RF_REPORT_DIR`), reads each workbook's
  export receipt (`<name>.audit/audit-manifest.json`) read-only, compares with the registry and with what it saw last (`<runtime>/agent/seen.json`). A workbook the registry does not know is queued
  once (`<runtime>/agent/queue/<slug>.json`) with a `DATA_REQUEST.md` and a registry stub; declined industries are not re-flagged.
* **Waiting for data**: an industry whose inputs are missing ends as
  `WAITING_FOR_DATA` (exit 0, success event, `DATA_REQUEST.md`), never as a failure; the packet lists the gaps. The supplied P&C pilot is now ready; negative tests deliberately remove its inputs.
* **Gates** (`R/rf_gates.R`): G0 environment, G1 inputs, G2 parity (the parity suite, in the same run), G3 panels, G4 sample adequacy (WARN, capability withheld with a stated reason),
  G5 stability (HOLD), **G6 hard backtest gate: no existing industry's calculator-path leave-one-election-out MAE may exceed the published calculator's (new <= published + 1e-9)** (HOLD),
  G7 bundle schema and completeness (FAIL), G8 consumer smoke (`tools/g8_consumer_smoke.py`: the console's calculator `mentat/app/giving_forecast.py` loads the staged bundle and projects every offered industry; the command defaults to that script when `mentat/app` sits beside the pipeline, `MENTAT_G8_CMD` overrides it; HOLD when no calculator is reachable, as on a pod snapshot that carries `mentat/forecast` only), G9 beats last-period naive (WARN only).
  Decision: any FAIL, else any HOLD, else PUBLISH.
* **Publish** (`R/rf_publish.R`): only on PUBLISH. Under the refit lock, write and validate immutable files under
  `versions/<bundle-sha256>/`, archive the bundle and receipt, then atomically switch `giving-models-current.json`.
  The Python consumer and R baseline reader both validate and honor this pointer. A pointer failure preserves the prior active version.
  Compatibility copies are updated only after the switch. v1 stays available for rollback; a version name cannot be reused with different bytes.
  `RF_PUBLISH_DIR` (default `mentat/app/data`) names the target. No Git commit is made by the publisher.
* **Review packet** (`<run>/review/`): `REVIEW.md` (decision, gate table, rule-selection table, slopes, G6 table, log refit, data gaps, 2026 inputs, platform), `regressions.txt` (real
  `summary(lm())` blocks plus the robustness block of every model, including the lobbying log refit), backtests, rule selection, robustness p-values, G4/G5/G6 tables, bundle diff, inputs.
* **Glass Box**: run.start, stage.start/finish per stage, gate.result per gate, publish.start/done/hold, run.finish or a failure event.
* **Platform**: every receipt carries `platform` (R version, BLAS, LAPACK, worker count). Results on another platform agree to ~1e-13 (a pod's ATLAS BLAS against a laptop's reference
  BLAS); bundles are compared with `rf_bundle_diff()` (tolerances, run-identity fields ignored), never byte equality. Parallel == serial bit-for-bit holds within one platform.

## Not built

The IEM `max_contract_trade_age_days` column (no bundle number depends on it), the `lobby_differenced_controls` evidence regression, and an R port of the LobbyView raw builder.
The Python consumer and anchor builder are present in sibling app/anchors folders. Copy that source alongside the refit for G8 to run in the pod: `python mentat/forecast/refit/tools/g8_consumer_smoke.py <giving-models-v2.json>` (`tests/test_g8_smoke.py` is its own test).

## Completed pod refit (2026-09-30)

`pod-final-20260930` correctly held publication because House-share LOEO MAE rose from
1.053 to 1.550 for banks, 1.522 to 1.836 for credit unions, and 1.338 to 1.766 for auto.
Paul explicitly authorized: "Waive these three cells and publish". The registry now contains
those three visible House warnings plus the pre-existing quiet banks-lobbying waiver.
Every other G6 cell retains the original no-degradation requirement; the initial HOLD receipt
is retained. G4 warns about withheld auto lobbying; G9 warns that the log-lobbying refit
(MAE 4.050) does not beat naive (3.936).

The refit was published in the RunPod after those explicit waivers. See the final run receipt
and `giving-models-current.json` for the active immutable version and SHA256. The actual
Python consumer and Chromium were exercised using real aggregate anchors and measured
current workbook hashes. Missing or stale anchors refuse projection. Local console restart
and Windows scheduled-task registration remain dormant under the owner's pod-only instruction.

## Phase 6 (candidate, scratch): phase, trajectory, inflation, the consistency rule, BLEND, the party-share layer

Owner rulings of 2026-10-01 (brain D-078 / D-079): every projected quantity except lobbying accounts for the midterm / presidential disparity, the trajectory from past cycles and inflation; the open-cycle roster PAC projection is BLEND. **Nothing here is published and nothing here is wired into the v1 parity output.**

* `R/rf_phase.R`: first-difference candidates for the PAC and general-giving level models (`PA` same-phase anchor, `PB` previous-cycle anchor + `phi` x change in the presidential flag, `PBL`, `PT-<G|noG>-<full|rec3|damp>-<pool|ind>` = phase + drift (trajectory), `PBLT`), the 2022 shock handlings (none / drop / indicator), 95% intervals (published anchored-step construction), the Senate share model with the Pres term (`rf_phase_ctx`), parameters, chains and projections (`rf_ph_chain`, with the growth-projection error variance), the inflation convention (`rf_ph_cycle_factor`), the phase block and its bundle evidence (`rf_phase_block`, `rf_v2_phase_evidence`).
* `R/rf_consistency.R`: the coordinator's consistency rule. A candidate replaces the published rule only if (a) pooled MAE >= 5% lower in both designs, (b) every industry within +2% of the published MAE in both designs, (c) 95% coverage >= 90% in both designs; the owner-mandated components stay in, so a failing family is a CONFLICT carried with both versions.
* `R/rf_blend.R`: BLEND (precision-weighted log-scale average of the model prior and the pace projection, truncated at the giving observed) on the pace test's frozen roster series, and the same construction on the roster PACs' party share (logit scale). Reproduces the pace test's python implementation to 1e-12.
* Tests: `test-20-phase.R`, `test-21-consistency.R`, `test-22-blend.R` (helper `helper-synth.R`).
* Drivers and outputs: `work/scripts/`, `work/out/`; the report is `REPORT.md`.

## Phase 7: the cycle-aware 50/50 model (owner rulings of 2026-10-07)

Paul, 2026-10-07: "1. Yes 2. Keep senate in 3. We use the 50/50 4. Yes; Use the IBIS defaults, auto-recalculate". The phase, trajectory, inflation and BLEND code of phase 6 is now ported from the scratch rebuild and wired into the bundle.

* `R/rf_bundle2.R` `rf_v2_cycle_model()` writes the optional top-level `cycle_model` (contracts/giving-models-v2.schema.json): per measure (roster PAC `PT-noG-damp-pool@x22`, general giving `PT-noG-damp-pool`) the phase coefficient with its standard error and df, the damped drift `d_h = b0 + b1 (k_max + sum 0.8^j)`, the anchored-step variance per offered industry; the REAL IBISWorld growth paths 2023-2027 of the 50/50 budget rule (banks 52211 and credit unions 52213 from the 2026 captured levels, property and casualty 52412 from the 2024-11 edition's own reported change), the prior-cycle growth of the share models for 2028, and the roster pace series of BLEND. It STOPS if the consistency rule picks anything but the two adopted specifications, and the bundle gets no `cycle_model` (the refit run refuses that outcome) when the context lacks an offered industry (the frozen v1 sample has no property and casualty pilot).
* `RF_CYCLE_MODEL=1` (default) builds all of it and gives the Senate share model the presidential-cycle term; `RF_CYCLE_MODEL=0` reproduces the bundle published before it (tested against `versions/7bc7bcf1...` in test-23).
* Inputs: `inputs/cycle_model/*.csv`, pinned in `registry/inputs.json` (roles `cycle:*`, alias `{CYCLE}`, override `RF_ROOT_CYCLE`).
* `R/rf_schema.R` mirrors `contracts/contract.py::check_cycle_model`; `tools/check_bundle_contract.py` also applies the `models_cycle` negative controls when a bundle carries a `cycle_model`.
* G6 with a `cycle_model`: the `pac` cell scores the selected candidate's leave-one-election-out MAE against the published rule on common rows, and a `general` cell (general giving, against PUBPAC-R1) joins; G9 scores the same two candidates against last-cycle naive. `registry/gate_waivers.json` carries the owner's two rulings as cell waivers ("2. Keep senate in": every `control_Senate` cell; "1. Yes": `pac / auto_light_duty_manufacturing`).
* Tests: `test-23-cycle-model.R` (oracles: the build spec's independent re-fit), the cycle cases of `test-16-gates.R`.
* Final run (staging): `run_final_refit.ps1` at the staging root = `Rscript mentat/forecast/refit/run_refit.R --mode refit --publish auto --no-tests` with `RF_CYCLE_MODEL=1` (`-PublishDir <copy of mentat/app/data>` makes a trial run that cannot touch the staged pointer); `run_staged_suite.ps1` runs the whole testthat suite on the staged code. A HOLD publishes nothing.

## Phase 8: lobbying nowcast bands and Telecom (owner rulings of 2026-10-08)

* `R/rf_lobbying.R` `rf_lobby_persistence()`: the persistence errors of the LobbyView level series `log(level[t+h]) - log(level[t])`, h = 1..3, over the blind targets 2014-2023, per study sector and pooled
  (split-conformal quantiles: the ceil((n + 1) p)-th smallest |e|). `rf_v2_lobby_model()` writes them as the optional `lobby_model.log_refit.persistence` (per bundle industry: q80; pooled: q80, q95);
  the console adds them in quadrature to the in-year nowcast's blind error (the anchors carry the nowcast and its pooled blind errors) for the years after the nowcast year. The old year-effect
  `log_refit.prediction` interval is not read any more (it never beat last-period naive: G9 WARN).
* (Superseded by Phase 9, which makes Telecom a full industry; the `lobbying_only` mechanism below stays for future industries.) Telecom was a lobbying-only calculator industry (`registry/industries.json`: `calculator` and `lobbying_only` true, a Mentat key, `models {pac false, control false, lobbying true}`, a stated `reason`).
  The writer emits it per industry as the bundle key `lobbying_only`; `rf_cycle_model_offered()`, `contract.py::check_cycle_model` and its mirror in `rf_schema.R` all skip the cycle model's checks (no general-giving step
  block, no IBIS growth path) for exactly the industries flagged `lobbying_only`, never for one whose PAC capability is merely withheld (negative control C09).
* Tests: `test-01-registry.R` (telecom), `test-15-bundle-v2.R` (the persistence helper on a hand-computed case, the bundle's table against the study, telecom in `industries`), `test-23-cycle-model.R` (a lobbying-only industry is not checked for giving inputs).

## Phase 9: Telecom giving (owner ruling of 2026-10-08: "use B09 and wireless+cable for Telecom giving")

Telecom is now a full forecast industry (general giving + lobbying) and no longer carries `lobbying_only` (the flag and the contract's exemption stay as a mechanism for future industries).

* **OpenSecrets scope = category B09 only** (Telecom Services & Equipment; B08 Telephone Utilities is not used), wired like the F09 all-insurance ruling of property and casualty: `registry/industries.json` telecom
  `opensecrets {codes ["B09"], code_status "owner_ruling_b09_only", baseline_scope_caveat, totals "{RFDATA}/B09_totals.json"}` and a `caveats` entry that reaches the bundle and the console. The totals table is the
  one the 2026-10-01 standardized collector saved (`outputs/opensecrets-2008-2026-20261001/raw/<sha>.html`, B09 page); `tools/make_telecom_inputs.R` parses it into the capture envelope `rf_os_totals` already reads
  (cycles 1990-2024, the identity total = individuals + PACs + outside money asserted) and pins it as input `os_totals:telecom`. No recipient / presidential captures of B09 exist or are asked for: Telecom has no control (share) model.
* **IBISWorld = wireless (51721, edition 2024-10) + cable (51711a, edition 2024-12), revenue-weighted** (both constant 2024 USD, both prices "inflation adjusted to 2024"). Each year's growth is the average of the
  two components' growth weighted by their PREVIOUS-year revenue (the registry's "same-vintage baseline revenue" convention), which is the growth of the summed revenue: 2023 -2.94%, 2024 +4.99%, 2025 +4.28%, 2026 +4.32%,
  2027 +4.11% (wireless weight 78-81%), `g_prior` 2028 +9.13% (`R/rf_bundle2.R` `RF_CYCLE_IBIS$telecom`, `rf_ibis_weighted()`, input `cycle:ibis_telecom_2024` = the two editions' pages 87/89 and 45/46). The ACTUAL growth of the
  panel (workbook 07 `telecom_wireless` + `telecom_cable`, registry `ibisworld.actual_components`, `rf_combine_components()` in `R/rf_growth.R`) is the same revenue-weighted combination, with 2024 taken from the same two editions'
  current-year estimates (input `ibis_actual_current:telecom`; editions are never spliced and the estimate drops out when a 2025+ edition puts 2024 in the main series).
* **Scored-only, not pooled** (`registry: estimation "scored_only"`, `status pilot`, `models {pac true, control false, lobbying true}`): Telecom's panel (`ctx$scored`, built by `rf_scored_panel()`, 7 cycles 2012-2024) NEVER joins the
  pooled phase / drift / slope estimation or the share models. The phase block (`rf_ph_scored_step_variance()`) gives it the pooled model's anchored-step residual variance on its own history, with the pooled phase coefficient and
  drift of the fitted candidate (the adopted `PT-noG-damp-pool` candidates have no growth term, so this is defined). Hence phi, drift, the panel hash and every other industry's step variance, projections and gates are identical to the
  bundle built without Telecom. Telecom's own step blocks: PAC measure 4 pairs (2022 dropped), general giving 6 pairs. Its roster PACs stay unavailable (empty roster, the anchors' existing reason); the calculator (`app/giving_forecast.py`, not this pipeline) takes Telecom's company PACs from the published PAC panel's 8 telecom PACs instead (`PANEL_COHORT`, the credit unions' code path of D-117: the panel's nowcast, x1.09 for 2027-28; their D/R split is pinned in `forecast/anchors/panel_cohort_split_telecom.json`). The chamber (party) split needs a House / Senate share model, which Telecom does not have: it stays withheld with `withheld_by_model.control`.
  `rf_scored_backtest()` writes its one-step errors against last-cycle naive (review packet section "Scored-only industries", `review/scored_only.csv`); G4 counts its cycles.
* Tests: `test-01-registry.R` (the registry entry, the mode checks, readiness), `test-03-panels.R` (the scored panel: B09 numbers, the revenue-weighted growth re-computed from workbook 07), `test-23-cycle-model.R` (the full cycle-model
  check for Telecom, the IBIS path = the wireless + cable combination, the OpenSecrets scope = B09, the independent step variance, the flag mechanism kept), `test-15-bundle-v2.R`.

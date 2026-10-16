# Giving Forecast contracts (brain DE-021)

Two JSON documents cross a lane boundary. Each has **one JSON Schema, one example payload and shared negative controls**, and
every producer and consumer is asserted against them (a prose contract failed here before: DE-021).

| Document | Schema | Example | Producer | Consumer |
|---|---|---|---|---|
| Model bundle v2 | `giving-models-v2.schema.json` | `giving-models-v2.example.json` | R refit pipeline `mentat/forecast/refit` (`rf_bundle.R`) | `mentat/app/giving_forecast.py` |
| Anchor snapshot v1 | `giving-anchors-v1.schema.json` | `giving-anchors-v1.example.json` | `mentat/forecast/anchors/build_giving_anchors.py` (Python, read-only over Mentat) | `mentat/app/giving_forecast.py` |

Shared: `negative-controls.json` (mutations that MUST fail), `contract.py` (Python validator, loader, mutation helper, anchor invariants).
Tests: `mentat/tests/test_giving_contracts.py` (validator and controls), `test_giving_forecast.py` (consumer), `test_giving_anchors.py` (producer).

## For the R pipeline builder: how to assert `rf_bundle_v2()` against this

1. Validate the writer's output against `giving-models-v2.schema.json` with a hand-written validator of the keyword subset below.
   `jsonvalidate` is not installed; `contract.py::_check` is 60 lines and is the reference implementation.
2. Validate `giving-models-v2.example.json` with the same validator (proves the validator accepts a good document).
3. Apply every `target: "models"` control of `negative-controls.json` to the example **and** to the writer's output; every one must be
   rejected. Ops: `delete` a key, `set` a value; `{"$nonfinite": "NaN"}` as a value means R's `NaN` / `Inf`.
4. Fixture note: the example is a **contract fixture**, not a refit output (`producer.pipeline` says so). Its numbers are the frozen
   v1 numbers plus the v2 fields computed from the phase-2 design (`SP\tailoring`, the PAC panel, the LobbyView pairs, the Polymarket
   daily files); the console reproduces ADDENDUM section F from it. Do not assert the writer's numbers against it, assert the shape.

### Keyword subset (both validators implement exactly this)

`$schema $id $defs $ref(#/$defs/x only) title description type(string|list) enum const properties required additionalProperties(bool|schema)
propertyNames(pattern only) minProperties items minItems minimum maximum exclusiveMinimum minLength pattern`. No `oneOf/anyOf/allOf/if`:
a nullable value is `"type": ["number","null"]`; cross-field rules live in code (`check_anchor_invariants`), not in the schema.
`type: number` and `integer` reject booleans and non-finite values; an integral double is an integer (R writes `2024` as `2024.0`).
Objects are closed (`additionalProperties: false`) except the `evidence` blocks, which are open by design.

## Model bundle v2: what the consumer reads (and therefore what the writer must emit)

Top level: `schema_version` (2), `model_version`, `built_on`, `built_at_utc`, `previous_model_version`, `producer`, `currency`, `deflator`,
`inference`, `validation`, `interval_labels`, `anchor_contract`, `industries`, `pac_model`, `share_models`, `evidence_models`, `lobby_model`,
`political_inputs`, `evidence`, `industry_slopes`, `validation_metrics`, `backtests`, `saved_outlook`, `sources`, `gates`.

Availability date: `built_on` is the UTC date of `built_at_utc`. The calculator does not compare it with the as-of date directly (the page's calendar is Eastern, America/New_York, one day behind UTC from 20:00 to 24:00);
`giving_forecast._model_vintage` reads `built_at_utc` in the Eastern calendar and takes the earlier of that day and `built_on`, so a bundle built at 23:30 Eastern is usable that same Eastern day (2026-10-09). Bundles
stamped before the rule need no change; a bundle with no `built_at_utc` (schema 1) keeps its `built_on`.

* **`anchor_contract`** names which Mentat measure each model applies to, and the change kind the consumer must apply. The consumer
  refuses a bundle whose contract it does not implement (unknown measure ids or change kinds).
  * `pac`: `pac_model` applied as a **relative (log) change** to the **previous cycle's Mentat level**, twice, side by side:
    measure `pac_to_candidates` (the roster companies' PACs' own giving to House and Senate candidates, FEC pas2, D-049, direct +
    in-kind, refunds netted, IE excluded) and measure `general_giving` (the console's included receipt amount = workbook Analysis Amount, all channels; since the
    owner ruling of 2026-10-01 the anchors' level leaves out the receipts the accounting engine buckets as non_giving: see "Anchor snapshot v1" below).
  * `share`: `control_House` / `control_Senate` give a **pp change** added to the previous cycle's Mentat R share, on two bases side by
    side: `employee_candidate` (included receipts to House/Senate candidates across all funding sources except non-giving; legacy key retained) and `pac_candidate` (the roster PACs' own giving to candidates).
  * `lobbying`: the in-year **nowcast** of the anchors (`lda.series[sid].partial.nowcast`, owner rulings 2026-10-08) carried to the later years; a growth-forecast change that is
    entered moves it by `lobby_model.log_refit` (the relative change of the old calculator, now applied on top of the nowcast). The bands are empirical, not the model's interval:
    the anchors' pooled blind errors of the nowcast (`lda_nowcast_errors`) and, for the years after the nowcast year, `log_refit.persistence` in quadrature. Both LDA series
    (`income_total` outside-firm fees, `expenses_total` in-house) side by side, never summed.
* **`industries[id]`**: v1 fields (`id label code growth_mean growth_sd growth_min growth_max history scope`) plus `opensecrets_codes`,
  `mentat_key` (null = no Mentat data: the console does not offer the industry, it stays in estimation), `ibisworld_code`,
  `lobbyview_sector`, `calculator`, `capabilities {pac, control, lobbying}`, `withheld {model: reason}`, `growth_scaling_cycles`, `lobbying_history`.
  An industry without a capability carries a `withheld` reason; the console shows it, never guesses.
* **`pac_model`**: pooled values as in v1 (`beta ordinary_se ordinary_p critical_95 n cycles selection_repeated_p`) plus `formula`, `industries`,
  `residual_df`, `residual_sd`, `slope_rule` (R0 pooled / R1 own / R2 DerSimonian-Laird, what the auto-pick chose) with `slope_rule_reason`,
  `tau2`, `Q`, `k`, `df_interaction`, `anchor_rule` (`previous_cycle`), and two per-industry maps:
  * `slopes[id] = {own_beta, own_se, own_p, dl_weight, dl_beta, dl_se, applied}` where **`applied` is the slope the calculator uses**
    (`rule, beta, se, df, critical_95 = qt(.975, df), ordinary_p`). Each industry's own slope is `applied` when the rule is R1. The consumer
    never re-derives a rule: it labels R0 "pooled slope", R1 "industry-specific slope", R2 "partially pooled slope".
  * `prediction.industries[id] = {sigma2, pairs, df, critical_95}`: the residual variance of the anchored step (PAC: pairs (t-2, t) per
    industry, `e = dlog_pac - applied.beta * dG`; `sigma2 = sum(e^2)/(pairs-1)`, `critical_95 = qt(.975, pairs-1)`). Interval:
    `exp(b dG +/- crit * sqrt(dG^2 se^2 + steps * sigma2))`, `steps` = cycles from the anchor (1; 2 for a chained next cycle).
* **`share_models.control_House|control_Senate`**: v1 keys (`terms beta covariance ordinary_p critical_95 residual_df n adjusted_r2 residual_sd
  training_cycles history specification id signal chamber`) plus `formula`, `slope_rule(_reason)`, `tau2`, `slopes`, `prediction` (same shape;
  `prediction.industries[id]` repeats the residual block of the anchor the industry's `anchors` entry chose; pairs are (t-2, t) for `previous_cycle` and
  (t-4, t) for `same_phase`, full dx over the non-industry terms), `anchor_rule` (`by_industry`: each industry x chamber names its anchor in `anchors`;
  the default is `previous_cycle`, NOT v1's same-phase), `anchors`,
  `calculator: true`, `evidence`, and `application`: `pooled` = use the top-level pooled fit; `offset` = non-focal terms from `offset`
  {terms, beta, covariance}, focal Forecast slope from `slopes[id].applied` (variance `dx_o V_o dx_o + dF^2 se^2`). `offset` is null when pooled.
  Only the two control models live here: seats and the `*_industry` variants move to `evidence_models` with `calculator: false`.
  * **`anchors[id]`** (owner ruling 2026-09-30, anchor auto-pick): `anchor` (`previous_cycle` | `same_phase`, what the calculator uses), `default` (`previous_cycle`),
    `slope_rule` (the rule both anchors were backtested under), `min_gain` (0.05), `reason`, `backtest` (`previous_cycle` / `same_phase`: `loeo_mae`, `chrono_mae`,
    `loeo_n`, `chrono_n` in share pp, and `same_phase_gain {loeo, chrono}` = 1 - MAE(same_phase)/MAE(previous_cycle)), and `prediction` (`previous_cycle` and `same_phase`
    residual blocks). The anchor is chosen per industry and chamber by no-leakage backtests in both designs: `same_phase` replaces the default only when it beats
    `previous_cycle` by `min_gain` in EVERY design. **`previous_cycle`**: Mentat's cycle A = open cycle - 2 (2024) for both horizons, the next cycle chained (steps = 2).
    **`same_phase`**: Mentat's cycle of the same phase, target - 4 (2026 -> 2022, 2028 -> 2024), one step each and no chaining; its anchor-cycle inputs come from
    `political_inputs.anchor_cycles` when the cycle is listed (2024) and otherwise from the share model's history row (2022). The anchor snapshot already carries the 2022,
    2024 and 2026 cycles of every candidate measure. PAC and lobbying keep their anchors. `anchor_contract.share.anchor_options` lists the two anchors the consumer must
    implement (`cycle_offset`, `chained`). Cross-field rules a schema cannot say live in `contract.py::check_model_invariants` (`validate_model` = schema + invariants; the R
    writer has the same check in `rf_schema.R`): every industry with the control capability has an entry; `prediction.industries[id]` equals the chosen anchor's block;
    the recorded gain follows from the two MAEs; the choice follows the rule.
* **`lobby_model`**: v1 dollar fields kept as evidence; `log_refit` is what the calculator applies: `formula dly ~ dg + factor(year)`,
  `unit`, `beta se ordinary_p critical_95 residual_df n`, `slope_rule`, `slopes[id]` (as above, `applied` per industry), `prediction.industries[id]`
  (F.4: one pooled residual variance over 71 pairs repeated per industry, `critical_95 = qt(.975, 70)`), `provenance`.
  A relative change `exp(beta * dg) - 1` per +1 pp of the change in predicted growth; the level it multiplies is Mentat's (Melange LDA).
  `log_refit.prediction` (the year-effect residual variance) is no longer read for the bands (it never beat last-period naive: G9). **`log_refit.persistence`** (optional; owner rulings
  2026-10-08) is the empirical persistence error of the LobbyView level series, `e = log(level[t + h]) - log(level[t])` over the blind targets 2014-2023: `industries[id][h] = {n, q80}`
  (the split-conformal 80th percentile of `|e|`, h = 1..3) per bundle industry and `pooled[h] = {n, q80, q95}` over every LobbyView sector; the console adds the persistence error of
  the years after the nowcast year to the nowcast's blind error in quadrature (the industry's q80 for the 80% band, the pooled q95 for the ~95% band). A bundle without it leaves those years without bands.
* **Lobbying-only industries** (a mechanism kept for future industries; Telecom was the first, until the second owner ruling of 2026-10-08 made it a full giving + lobbying industry, B09 and wireless + cable): an industry with `calculator`, a `mentat_key` and `capabilities {pac false, control false, lobbying true}` (a stated `withheld`
  reason for the two giving models) is offered by the console for lobbying alone and carries `lobbying_only: true` (optional key, absent = false; the writer emits it from the registry). `check_cycle_model`
  checks the general-giving step block and the IBIS growth path for every offered industry EXCEPT one flagged `lobbying_only`; a PAC capability that is merely withheld does not exempt an industry.
* **`political_inputs.anchor_cycles["2024"]`**: the expected-control input of the anchor cycle, which has no row in the model history:
  `current_control {House, Senate}` (RMajNow), `window`, and per chamber `{p_rep, market, days, expected_days, first_day, partial_window, note}`.
  **Polymarket January-June 2024** (Kalshi has no 2024 January-June data; House covers 131 of 182 days, from 2024-02-21; `partial_window` must be true
  and the console flags it). The 2026 input is **Polymarket January-June 2026**: `saved_outlook.control.{House,Senate}.value` (score x100, primary),
  with `markets[]` (both markets, day counts, file sha256) and `sensitivity {kalshi_score, equal_weight_score}`. Polymarket-to-Polymarket is primary.
* **`saved_outlook`**: `control` only; `seats` is gone.
* **`evidence_models`**: seat regressions (`seats_House`, `seats_Senate`, ...) kept as evidence, every one `calculator: false`; the console lists them as "no signal".
* **`backtests`**, **`gates`**, **`sources`**, **`producer`**: as SPEC 3.12 (`sources[]` adds `role`, `pinned`).

### Deviations from phase-1 SPEC 3.12 that the R writer must follow (owner rulings of 2026-09-30)

1. `share_models.*.anchor_rule` is `by_industry` with the default anchor `previous_cycle` (SPEC said `same_phase_latest`); the step residual for shares uses (t-2, t) pairs
   under `previous_cycle` and (t-4, t) pairs under `same_phase`.
2. `saved_outlook.control.*.value` is the **Polymarket** Jan-Jun 2026 score (SPEC: the Kalshi/Polymarket equal weight 19.03 / 56.40); the equal weight is a sensitivity.
3. `pac_model.slopes[id].applied` (and the same in `share_models`, `lobby_model.log_refit`) carries the rule the auto-pick chose; own slope = R1.
4. `lobby_model.log_refit` is required (the lobbying calculator is relative).
5. `political_inputs` is new (the 2024 anchor-cycle control input).

## Anchor snapshot v1: what the builder writes and the consumer reads

File `MENTAT\mentat\state\giving-anchors\giving-anchors-v1.json` (+ `giving-anchors-v1.receipt.json`), runtime state (D-048: not in git), rebuilt nightly
and after each Mentat publish. A separate file from the model bundle, because anchors move whenever Mentat republishes, independent of refits.
`MENTAT_GIVING_ANCHORS` overrides the path. The console **refuses** (`status: anchor_stale`) when `industries[id].mentat.workbook_sha256` differs from
the sha256 of the workbook file now published in the report directory.

* The example `giving-anchors-v1.example.json` is **real builder output** (the four Mentat datasets built from the published workbooks, indexes,
  FEC files and Melange LDA file on 2026-09-30), not hand-made: every number in ADDENDUM section F comes from it. It was built BEFORE the owner ruling of
  2026-10-01 (non_giving receipts left out of the forecast): its levels still include them and it carries none of the optional exclusion fields. It stays
  as the fixture of the calculator's tests (`test_giving_forecast_v2.py` asserts the section F numbers against it) and proves that snapshots from before
  the ruling remain valid. The live snapshot is built by the current builder.
  `MENTAT_ANCHORS_REAL=all python -m pytest mentat/tests/test_giving_anchors.py` re-derives credit unions, banks and property & casualty from the real
  tree and compares them with the section F numbers less the non_giving receipts.
* Top: `schema_version` 1, `kind`, `built_at_utc`, `builder`, `unavailable` ({id: reason} for industries it could not anchor), `cpi` (`multipliers`: cycle end year -> `cpi[2025] / mean(cpi[t-1], cpi[t])`; a missing
  year means face value: 2026 CPI does not exist), `thin_base_threshold_usd` (100,000), `industries`.
* `industries[id]` (`id` is the forecast industry id: `commercial_banks` = Mentat Banking, `credit_unions` = Credit Union, `property_casualty`;
  `community_banks` is a separate entry with `forecast_industry: null`, never merged into banks; auto has no Mentat data and no entry):
  * `mentat`: dataset, workbook file, `workbook_sha256`, `index_token`, `database_sha256`, `payload_sha256`.
  * `coverage`: `rule`, `collection_date`, `rule_coverage_end` (last quarter-end whose report deadline, the 15th of the next month, was on or before the
    collection date), `coverage_end` (== the rule end, or earlier when a cut-back applies: credit unions 2026-05-31, reason "June incomplete"), `reason`, `flags`.
  * `general_giving`: `cycles {"2022","2024","2026"}` (dollars, nominal, net of negative adjustments; the open cycle holds every row to date),
    `by_funding_source`, `observed` = cumulative month-end cuts of the open cycle up to `coverage_end` ({through, amount}); rows dated after the
    coverage end belong to the remainder window, never to observed. **Owner ruling 2026-10-01 (D4 option b): `cycles` and `observed` leave out the receipts
    the accounting engine buckets as `non_giving`** (the FEC records them as interest, refunds, insurance claims, loans; the workbook still counts them in its
    Analysis Amount). `by_funding_source` stays the full split of the Analysis Amount. Optional `excluded_funding_sources` (the buckets left out) and
    `excluded_non_giving` ({cycle: dollars}) report what was left out: `cycles` + `excluded_non_giving` = the sum of `by_funding_source`, per cycle.
  * `candidate_receipts`: included receipts to House / Senate candidate committees across all funding sources except non-giving; funding identity is not necessarily verified (index joined to the pinned source DB on `sub_id`;
    `recipient_committee_type` H or S; presidential P excluded), `{D, R, other}` per chamber per cycle; `observed` cuts; `thin_base` flags ($100K of D+R per chamber).
    The same ruling applies: non_giving receipts are out of the cycles, the cuts and the thin-base totals; optional `excluded_funding_sources` and
    `excluded_non_giving` ({cycle: {House, Senate}: {D, R, other}}) report them.
  * `pac_to_candidates`: the roster companies' PACs' own giving to candidates (FEC pas2, D-049 `count_latest`, 24K + 24Z signed, 24A/24E excluded;
    chamber from the CAND_ID prefix, party from the candidate master), `committees`, `inputs` (file sha256 + FEC last_modified), per cycle `{House, Senate, to_house_senate,
    presidential, no_candidate, all_recipients}`, `observed` cuts, `thin_base`, `single_pac` (true for credit unions: SchoolsFirst only).
  * `lda`: Melange LDA `income_total` and `expenses_total` (two series, never summed; bank total for banks) annual + the partial year's quarters, `source_sha256`.
    `counts` (optional; 2026-10-09) is `{year: {organizations, lobbyists}}` from the same annual rows, the partial year's included; a snapshot built before it has none and the lobbying card leaves the counts out.
    `status: unavailable` with a `reason` for industries without a Melange-LDA sector (P&C). `partial.nowcast` (optional; owner rulings 2026-10-08) is the in-year nowcast
    `point = observed + (1 - share) * prior_total`: `observed` = filed through the latest complete quarter cut, `prior_total` = the prior full-year total, `share` = the geometric mean of
    `shares`, the share filed by the same cut in `share_years` (up to the 3 prior years, at least 1); null when no prior year allows it. The snapshot's top-level `lda_nowcast_errors`
    (optional) pools the blind errors `log(point / actual)` of that nowcast over the complete years, both series and the pool sectors (bank, credit_union, telecom, fintech) by quarter
    cut: `{n, q80, max}`, the half-widths of the 80% band (split-conformal 80th percentile of |error|) and of the ~95% band (the largest blind miss).
  * Every measure has `status` ready | unavailable (+ `reason`); a consumer never reads numbers from an unavailable block.
* Cross-field invariants (`contract.py::check_anchor_invariants`): cut dates strictly increasing and ending on `coverage_end`; `to_house_senate` equals the House +
  Senate parts; `all_recipients` equals its parts; thin-base flags agree with the dollars; `single_pac` agrees with the committee list; partial LDA totals equal their quarters; a nowcast equals `observed + (1 - share) * prior_total` with `share` the geometric mean of `shares`, `observed` the filed quarters through its cut and `prior_total` the prior year's annual value;
  a ready block that reports one of `excluded_funding_sources` / `excluded_non_giving` reports both, and the general-giving and candidate blocks either both report them or neither;
  when a general-giving block reports them, its levels + the excluded amounts equal the by-funding-source total and the excluded amount equals the excluded sources' share of
  that split; the candidate block then has one `excluded_non_giving` entry per candidate cycle and the same `excluded_funding_sources` as the general-giving block (its amounts
  have no by-source split to be checked against). Each industry's anchor cycle (`current_cycle - 2`) has a CPI multiplier (a missing one must never read as 1.0); a ready LDA block carries both series over the same complete years.

## Optional `cycle_model` (owner rulings 2026-10-07: "1. Yes 2. Keep senate in 3. We use the 50/50 4. Yes; Use the IBIS defaults, auto-recalculate")

`giving-models-v2` keeps `schema_version` 2 and its file name. One OPTIONAL top-level object, `cycle_model`, switches the console calculator to the cycle-aware
50/50 projection; a bundle without it is served by the earlier calculator path unchanged, so a pointer rollback to the previous version needs no code rollback.

* Fixture: `giving-models-v2.cycle.example.json` is the models example carrying a real-valued `cycle_model` (and the presidential-cycle term in the Senate model);
  `contract.example('models_cycle')` loads it. The plain example stays without it. Negative controls `C01`-`C08` have `"target": "models_cycle"`: apply them to the cycle example
  AND to a writer's output, every one must be rejected.
* `cycle_model.measures.{pac_to_candidates,general_giving}`: `phi` (presidential-cycle coefficient, with `phi_se`, `phi_df`), the damped linear drift
  `d_h = b0 + b1 (k_max + sum_{j<=h} damp^j)` for the first and second step ahead (`d` = {"1","2"}), and per industry the anchored-step block (`sigma2`, `pairs`, `df` = pairs - 1,
  `critical_95`); `open_cycle_layer` is `BLEND` (roster PAC: model and pace, D-099) or `P1` (the model conditioned on the giving already observed; general giving: Mentat holds dated
  general giving for two complete cycles only).
* `cycle_model.budget_rule`: weight_model 0.5 (the 50/50 rule), one-year lag, and per industry the IBISWorld REAL annual revenue growth 2023-2027 (report, edition, price basis,
  source sha256). The calculator turns the lag window of a horizon (c-3 .. c-1) into the industry budget change of each measure with that measure's odd/even-year mix of the anchor
  cycle (`odd_year` of the anchor snapshot); the ONE input of a horizon, "Industry revenue growth" (request field `growth_pct`, average annual %, blank = the default), shifts every
  annual growth of the window by the same amount.
* `cycle_model.inflation`, `g_prior` (the share models' prior-cycle growth of the horizon that has no history row), `pace` (completed cycles' full-cycle and to-date amounts at March 31 /
  June 30 / September 30 of the election year), `notes` (Methods: no backtest of the 50/50 overlay or of the IBIS path error; the 2026-edition price basis is inferred as constant dollars;
  the 50/50 rule is untested on general giving; the general-giving phase coefficient).
* Invariants (`contract.py::check_cycle_model`, run by `check_model_invariants` when the block is present): drift `d` follows `b0`, `b1`, `k_max`, `damp`; every step block has
  `df = pairs - 1`; every offered industry has a general-giving step block and a complete IBIS path 2023-2027 (each above -100%); `weight_model` is 0.5; every pace cycle has
  `0 < O15 <= O18 <= O21 <= F`; the Senate share model carries `Pres`.
* Anchor snapshot (`giving-anchors-v1`): optional `general_giving.odd_year` ({cycle: dollars}) and `pac_to_candidates.odd_year` ({cycle: {House, Senate, to_house_senate}}) = the
  forecast-basis receipts of each COMPLETE cycle dated on or before December 31 of its first year. The old contract closes the measure blocks, so APPLY THE CODE BEFORE THE ANCHORS.

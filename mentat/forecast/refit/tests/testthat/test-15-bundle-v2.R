# Bundle v2 against the SHARED CONTRACT (mentat/forecast/contracts, brain DE-021): the schema, the example payload and the negative
# controls are read-only here. The validator is hand-written (no JSON Schema package), so it is proven three ways: it accepts the
# example, it rejects every shared negative control applied to the example, and the writer's own output passes and is rejected by the
# same mutations. Shape is asserted against the contract; numbers are asserted against ADDENDUM section F and the frozen v1 bundle.

example_path <- function() file.path(rf_contracts_dir(), "giving-models-v2.example.json")
read_example <- function() jsonlite::fromJSON(example_path(), simplifyVector = FALSE)
model_controls <- function() Filter(function(c) identical(c$target, "models"), rf_negative_controls())

test_that("the shared contract files are present and the schema stays inside the agreed keyword subset", {
  for (f in c("giving-models-v2.schema.json", "giving-models-v2.example.json", "negative-controls.json", "README.md"))
    expect_true(file.exists(file.path(rf_contracts_dir(), f)), info = f)
  expect_gte(length(model_controls()), 23L)        # B01-B23; keywords outside the subset make the validator stop (test below)
})

test_that("the validator accepts the contract example", {
  problems <- rf_validate_parsed(read_example())
  if (length(problems)) print(utils::head(problems, 10))
  expect_identical(problems, character(0))
})

test_that("every shared negative control applied to the example is rejected (the validator checks what it claims)", {
  ex <- read_example()
  ctls <- model_controls()
  for (ctl in ctls) {
    bad <- rf_apply_control(ex, ctl)
    problems <- rf_validate_parsed(bad)
    expect_gt(length(problems), 0L, label = paste(ctl$id, ctl$why))
  }
})

test_that("a keyword outside the agreed subset is an error, not silently ignored", {
  expect_error(rf_schema_check(1, list(type = "number", oneOf = list()), list()), "outside the supported subset")
  expect_error(rf_schema_check(1, list("$ref" = "http://x/y"), list()), "local")
})

test_that("the writer's bundle validates against the contract schema", {
  b <- test_bundle2()
  problems <- rf_validate_bundle(b)
  if (length(problems)) print(utils::head(problems, 15))
  expect_identical(problems, character(0))
  # and as JSON on disk, the way the consumer reads it
  p <- file.path(tempdir(), "bundle_v2_unit.json")
  h <- rf_write_json_atomic(b, p)
  expect_identical(h, rf_sha256(p))
  expect_identical(rf_validate_parsed(jsonlite::fromJSON(p, simplifyVector = FALSE)), character(0))
})

test_that("every shared negative control applied to the writer's output is rejected", {
  parsed <- rf_canonical_json(test_bundle2())
  for (ctl in model_controls()) {
    bad <- rf_apply_control(parsed, ctl)
    expect_gt(length(rf_validate_parsed(bad)), 0L, label = paste(ctl$id, ctl$why))
  }
  # the same mutations leave the untouched output valid (the controls are what fail it, not the copy)
  expect_identical(rf_validate_parsed(parsed), character(0))
})

test_that("the writer refuses NaN and a seat model in share_models before it reaches the schema (in-memory negative controls)", {
  b <- test_bundle2()
  bad <- b; bad$pac_model$beta <- NaN
  expect_true(any(grepl("non-finite", rf_validate_bundle(bad))))
  expect_error(rf_write_json_atomic(bad, file.path(tempdir(), "nan_bundle.json")), "non-finite")
  bad <- b; bad$share_models$seats_House <- list(id = "seats_House")
  expect_true(any(grepl("seats_House", rf_validate_bundle(bad))))
  bad <- b; bad$industries$commercial_banks$capabilities <- NULL
  expect_true(any(grepl("capabilities", rf_validate_bundle(bad))))
})

test_that("header, inputs and sources: model version, producer, deflator, interval labels and the pinned raw inputs", {
  b <- test_bundle2()
  expect_identical(b$schema_version, 2L)
  expect_match(b$model_version, "^refit-20260930-[0-9a-f]{8}-[0-9a-f]{12}$")
  expect_identical(b$previous_model_version, "2026-09-27-association-v1")
  expect_identical(b$producer$pipeline, "giving-forecast-refit")
  expect_match(b$producer$code_sha256, "^[0-9a-f]{64}$")
  expect_identical(b$producer$packages$clubSandwich, "0.7.0")
  expect_identical(b$deflator$base_year, 2025L)
  roles <- vapply(b$sources, function(s) s$role, "")
  expect_true(all(c("cpi", "ibis_actual", "lobbyview_sector_year", "iem_zip", "market_daily:polymarket:house:2024", "market_daily:kalshi:senate:2026") %in% roles))
  expect_true(any(startsWith(roles, "code:")))
  expect_false(any(startsWith(roles, "parity_ref:")))
  expect_true(all(vapply(b$sources, function(s) grepl("^[0-9a-f]{64}$", s$sha256), logical(1))))
})

test_that("seats are evidence only: share_models holds the two control models, evidence_models every seat model with calculator false", {
  b <- test_bundle2()
  expect_identical(names(b$share_models), c("control_House", "control_Senate"))
  expect_true(all(c("seats_House", "seats_Senate", "seats_House_industry", "seats_Senate_industry", "control_House_industry") %in% names(b$evidence_models)))
  expect_false(any(vapply(b$evidence_models, function(m) isTRUE(m$calculator), logical(1))))
  expect_true(all(vapply(b$share_models, function(m) isTRUE(m$calculator), logical(1))))
  expect_null(b$saved_outlook$seats)
})

test_that("PAC model: the auto-pick chose own slopes; the applied slope, its df, critical value and the per-industry step residual are in the bundle", {
  b <- test_bundle2(); pm <- b$pac_model
  expect_identical(pm$slope_rule, "R1")
  expect_match(pm$slope_rule_reason, "R1")
  expect_identical(pm$anchor_rule, "previous_cycle")
  expect_equal(pm$beta, -0.1683565110914325, tolerance = 1e-10)              # pooled values are the frozen v1 fit
  expect_identical(pm$df_interaction, 9L); expect_identical(pm$residual_df, 11L)
  for (id in c("commercial_banks", "credit_unions", "auto_light_duty_manufacturing")) {
    s <- pm$slopes[[id]]
    expect_identical(s$applied$rule, "R1", info = id)
    expect_identical(s$applied$df, 9L)
    expect_equal(s$applied$critical_95, qt(0.975, 9), tolerance = 1e-12)
    expect_equal(s$applied$beta, s$own_beta)
    expect_true(s$dl_weight >= 0 && s$dl_weight <= 1)
    expect_identical(pm$prediction$industries[[id]]$pairs, 6L)
    expect_equal(pm$prediction$industries[[id]]$critical_95, qt(0.975, 5), tolerance = 1e-12)
  }
  expect_equal(pm$slopes$commercial_banks$applied$beta, -0.05914, tolerance = 1e-3)
  expect_equal(pm$slopes$credit_unions$applied$beta, -0.06975, tolerance = 1e-3)
  # what a later refit compares against (G6): the MAE of the published calculator path, per industry
  mae <- pm$evidence$calculator_path_loeo_mae_by_industry
  expect_equal(mae$commercial_banks, 10.81, tolerance = 1e-3); expect_equal(mae$credit_unions, 6.35, tolerance = 1e-3)
  expect_true(all(c("ordinary_p", "HC1_p", "CR2_sector_p", "wild_webb_sector_p") %in% names(pm$evidence)))
})

test_that("share models: pooled slope (R0, tau2 = 0), per-industry anchors (default previous-cycle), no offset, per-industry own slopes kept as evidence", {
  b <- test_bundle2()
  for (ch in c("House", "Senate")) {
    m <- b$share_models[[paste0("control_", ch)]]
    expect_identical(m$slope_rule, "R0"); expect_identical(m$application, "pooled"); expect_null(m$offset)
    expect_identical(m$anchor_rule, "by_industry"); expect_identical(m$tau2, 0)
    expect_identical(unlist(m$terms)[1], "Intercept")
    expect_true("Forecast" %in% unlist(m$terms))
    s <- m$slopes$commercial_banks
    expect_identical(s$applied$rule, "R0")
    k <- match("Forecast", unlist(m$terms))
    expect_equal(s$applied$beta, unlist(m$beta)[k], tolerance = 1e-12)
    expect_equal(s$applied$critical_95, m$critical_95, tolerance = 1e-12)
    expect_false(is.null(s$own_beta))
    expect_gt(m$prediction$industries$commercial_banks$pairs, 10L)
  }
  expect_identical(b$share_models$control_House$formula, "RShare ~ sector + RMajNow + Forecast + G_prior + Pres")
  expect_identical(b$share_models$control_Senate$formula, "RShare ~ sector + RMajNow + Forecast + G_prior + Pres")     # the owner-mandated Pres term (phase) is in the refit Senate model
})

test_that("lobbying: the log refit is in the bundle and reproduces ADDENDUM F.4 and the contract example's values", {
  b <- test_bundle2(); lr <- b$lobby_model$log_refit; ex <- read_example()$lobby_model$log_refit
  expect_lte(abs(lr$beta - (-0.00814763405915353)), 1e-11)
  expect_lte(abs(lr$se - 0.003636721200861857), 1e-11)
  expect_lte(abs(lr$ordinary_p - 0.029125763521828413), 1e-10)
  expect_lte(abs(lr$critical_95 - ex$critical_95), 1e-12)
  expect_identical(lr$residual_df, 55L); expect_identical(lr$n, 71L)
  expect_identical(lr$slope_rule, "R0")
  expect_true(all(c("commercial_banks", "credit_unions") %in% names(lr$slopes)))
  p <- lr$prediction$industries$commercial_banks
  expect_lte(abs(p$sigma2 - 0.023714457190841767), 1e-10)
  expect_identical(p$pairs, 71L); expect_identical(p$df, 70L)
  expect_lte(abs(p$critical_95 - 1.994437111771186), 1e-10)
  expect_equal(b$lobby_model$beta_million, -0.5367134383271494, tolerance = 1e-10)   # the dollar model stays as evidence
  expect_identical(b$lobby_model$anchor_rule, "last_year")
  expect_equal(b$lobby_model$wild_p, 0.0306927297668038, tolerance = 1e-12)
})

test_that("political inputs and saved outlook: Polymarket to Polymarket, the partial 2024 House window flagged, sensitivities kept", {
  b <- test_bundle2(); ex <- read_example()
  a <- b$political_inputs$anchor_cycles$`2024`
  expect_identical(b$political_inputs$primary_market, "polymarket")
  expect_identical(a$current_control$House, 1L); expect_identical(a$current_control$Senate, 0L)
  expect_identical(a$House$days, 131L); expect_identical(a$House$expected_days, 182L); expect_true(a$House$partial_window); expect_identical(a$House$first_day, "2024-02-21")
  expect_true(nzchar(a$House$note))
  expect_identical(a$Senate$days, 182L); expect_false(a$Senate$partial_window)
  expect_lte(abs(a$House$p_rep - ex$political_inputs$anchor_cycles$`2024`$House$p_rep), 1e-12)
  expect_lte(abs(a$Senate$p_rep - ex$political_inputs$anchor_cycles$`2024`$Senate$p_rep), 1e-12)
  expect_identical(vapply(a$sources, function(s) s$file, ""), vapply(ex$political_inputs$anchor_cycles$`2024`$sources, function(s) s$file, ""))
  expect_identical(vapply(a$sources, function(s) s$sha256, ""), vapply(ex$political_inputs$anchor_cycles$`2024`$sources, function(s) s$sha256, ""))
  so <- b$saved_outlook$control
  expect_lte(abs(so$House$value - ex$saved_outlook$control$House$value), 1e-8)       # Polymarket Jan-Jun 2026 score, primary
  expect_lte(abs(so$Senate$value - ex$saved_outlook$control$Senate$value), 1e-8)
  expect_lte(abs(so$House$sensitivity$equal_weight_score - 19.03485027624309), 1e-8)
  expect_lte(abs(so$Senate$sensitivity$equal_weight_score - 56.395296132596684), 1e-8)
  expect_identical(so$House$market, "polymarket")
  expect_identical(vapply(so$House$markets, function(m) m$market, ""), c("polymarket", "kalshi"))
  expect_identical(so$House$current_control, 1L); expect_identical(so$Senate$current_control, 1L)
})

test_that("industries: banks and credit unions are offered, auto stays in estimation, the frozen v1 estimation sample excludes the ready property and casualty pilot", {
  ind <- test_bundle2()$industries
  expect_true(ind$commercial_banks$calculator && ind$credit_unions$calculator)
  expect_false(ind$auto_light_duty_manufacturing$calculator)
  expect_null(ind$auto_light_duty_manufacturing$mentat_key)
  expect_identical(ind$commercial_banks$mentat_key, "banking")
  expect_false(is.null(ind$property_casualty))
  expect_false(ind$property_casualty$capabilities$pac); expect_false(ind$property_casualty$capabilities$control)
  expect_identical(ind$property_casualty$withheld$pac, "model 'pac' is not configured for this industry")
  expect_true(ind$property_casualty$capabilities$lobbying)
  expect_length(ind$property_casualty$history, 0L)
  expect_null(ind$life_insurance)
  # telecom (owner ruling 2026-10-08) is a scored-only industry, no longer lobbying-only: a Mentat key, OpenSecrets B09, the lobbying capability; the frozen v1 sample has no scored panel, so its giving capability is withheld here
  expect_true(ind$telecom$calculator); expect_identical(ind$telecom$mentat_key, "telecom"); expect_identical(ind$telecom$lobbyview_sector, "telecom")
  expect_false(ind$telecom$lobbying_only); expect_false(ind$commercial_banks$lobbying_only); expect_false(ind$property_casualty$lobbying_only)       # the registry's flag, emitted for every industry
  expect_identical(unlist(ind$telecom$opensecrets_codes), "B09"); expect_identical(ind$telecom$code, "B09"); expect_identical(ind$telecom$ibisworld_code, "51721+51711a")
  expect_identical(ind$telecom$capabilities, list(pac = FALSE, control = FALSE, lobbying = TRUE))
  expect_identical(ind$telecom$withheld$pac, "model 'pac' is not configured for this industry"); expect_match(ind$telecom$withheld$control, "general-giving model only")
  expect_length(ind$telecom$history, 0L)
  # auto has one lobbying pair: sample adequacy withholds lobbying (a stated reason, never silent)
  expect_false(ind$auto_light_duty_manufacturing$capabilities$lobbying)
  expect_match(ind$auto_light_duty_manufacturing$withheld$lobbying, "sample adequacy")
  expect_identical(unlist(ind$commercial_banks$growth_scaling_cycles), seq(2012L, 2024L, 2L))
  expect_equal(length(ind$commercial_banks$history), 7L)
})

test_that("lobbying persistence: the split-conformal quantiles of the h-year log differences over the 2014-2023 targets, per sector and pooled (hand-computed case)", {
  k <- 0:17
  lv <- data.frame(study_sector = rep(c("a", "b"), each = 18), year = rep(2008:2025, 2), y_real_m = c(exp(0.005 * k * (k + 1)), exp(-0.005 * k * (k + 1))), stringsAsFactors = FALSE)
  pe <- rf_lobby_persistence(lv)
  # log level = +-0.005 k (k + 1), k = year - 2008: the one-year difference at target t is +-0.01 (t - 2008), t = 2014..2023, i.e. |e| = 0.06 ... 0.15
  expect_identical(pe$sector$a$`1`$n, 10L)
  expect_equal(pe$sector$a$`1`$q80, 0.14, tolerance = 1e-12)                       # ceil(11 x 0.8) = the 9th smallest of 10
  expect_equal(pe$sector$b$`1`$q80, 0.14, tolerance = 1e-12)
  expect_identical(pe$pooled$`1`$n, 20L)
  expect_equal(pe$pooled$`1`$q80, 0.14, tolerance = 1e-12)                          # ceil(21 x 0.8) = 17 of 20 sorted |e| (each value twice)
  expect_equal(pe$pooled$`1`$q95, 0.15, tolerance = 1e-12)                          # ceil(21 x 0.95) = 20: the largest
  expect_equal(pe$sector$a$`2`$q80, 0.01 * (2 * 2022 - 2 * 2008 - 1) , tolerance = 1e-9)   # h = 2: e = 0.01 (2 t - 2 x 2008 - 1); the 9th smallest target is 2022
  # a sector too short for the quantile has no entry rather than a made-up one; targets outside the window are not used
  short <- rf_lobby_persistence(lv[lv$study_sector == "a" & lv$year %in% 2012:2015, ])
  expect_true(is.na(short$sector$a$`1`$q80)); expect_identical(short$sector$a$`1`$n, 2L)
})

test_that("the bundle carries the lobbying persistence errors (owner rulings 2026-10-08): the blind-test table of the study, per offered industry and pooled over every LobbyView sector", {
  pe <- test_bundle2()$lobby_model$log_refit$persistence
  expect_match(pe$window, "target years 2014-2023")
  expect_true(all(c("commercial_banks", "credit_unions", "telecom") %in% names(pe$industries)))
  expect_false("life_insurance" %in% names(pe$industries))                           # not an industry of the bundle; it still counts in the pooled table
  b1 <- pe$industries$commercial_banks$`1`
  expect_identical(b1$n, 10L); expect_equal(b1$q80, 0.11332135689014677, tolerance = 1e-9)
  expect_equal(pe$industries$telecom$`1`$q80, 0.12977393486852762, tolerance = 1e-9)
  expect_equal(pe$industries$credit_unions$`2`$q80, 0.17338143912568071, tolerance = 1e-9)
  expect_identical(pe$pooled$`1`$n, 60L)                                             # six sectors x ten targets
  expect_equal(pe$pooled$`1`$q80, 0.1852509758693479, tolerance = 1e-9); expect_equal(pe$pooled$`1`$q95, 0.25920943482670467, tolerance = 1e-9)
  expect_equal(pe$pooled$`2`$q95, 0.3364104872238478, tolerance = 1e-9); expect_equal(pe$pooled$`3`$q95, 0.43865571483590315, tolerance = 1e-9)
})

test_that("backtests rows carry exactly the contract's keys and the chosen-rule comparisons", {
  b <- test_bundle2()
  keys <- c("model", "design", "path", "rule", "n", "folds", "targets", "MAE", "RMSE", "units", "coverage_95", "beats_naive")
  expect_true(all(vapply(b$backtests, function(r) setequal(names(r), keys), logical(1))))
  models <- unique(vapply(b$backtests, function(r) r$model, ""))
  expect_setequal(models, c("pac", "control_House", "control_Senate", "lobby", "lobby_log"))
  pr <- Filter(function(r) r$model == "pac" && r$design == "loeo" && r$path == "prevchg" && r$rule == "R1", b$backtests)[[1]]
  expect_equal(pr$MAE, 10.765, tolerance = 1e-4)
  expect_true(isTRUE(pr$beats_naive))
})

test_that("the contract's own Python validator (an independent implementation) accepts the writer's bundle and rejects every control", {
  py <- Sys.which(c("python3", "python"))
  py <- py[nzchar(py)][1]
  skip_if(is.na(py), "no Python on this machine")
  p <- file.path(tempdir(), "bundle_v2_py.json")
  rf_write_json_atomic(test_bundle2(), p)
  out <- suppressWarnings(system2(py, c(shQuote(file.path(.RF_HOME, "tools", "check_bundle_contract.py")), shQuote(p), shQuote(rf_contracts_dir())), stdout = TRUE, stderr = TRUE))
  status <- attr(out, "status") %||% 0L
  if (status != 0L) print(out)
  expect_identical(status, 0L)
  expect_true(any(grepl("problems: 0", out, fixed = TRUE)))
  expect_true(any(grepl("negative controls accepted (must be none): []", out, fixed = TRUE)) || any(grepl("negative controls accepted", out)))
})

test_that("an empty withheld map is written as an object, never an array (caught by the independent validator)", {
  b <- test_bundle2()
  expect_identical(jsonlite::toJSON(b$industries$commercial_banks$withheld, auto_unbox = TRUE), jsonlite::toJSON(stats::setNames(list(), character(0)), auto_unbox = TRUE))
  bad <- b; bad$industries$commercial_banks$withheld <- list()
  expect_true(any(grepl("withheld", rf_validate_bundle(bad))))
})

# ---- the anchor auto-pick in the bundle (owner ruling 2026-09-30) ----------------------------------------------------------------------

test_that("every share industry carries its chosen anchor, both anchors' backtest MAEs and residual blocks; prediction.industries repeats the chosen block", {
  b <- test_bundle2()
  expect_identical(b$anchor_contract$share$anchor_cycle_rule, "by_industry_chamber")
  expect_identical(vapply(b$anchor_contract$share$anchor_options, function(o) o$id, ""), c("previous_cycle", "same_phase"))
  for (ch in c("House", "Senate")) {
    m <- b$share_models[[paste0("control_", ch)]]
    expect_setequal(names(m$anchors), names(m$prediction$industries))
    for (id in names(m$anchors)) {
      a <- m$anchors[[id]]
      expect_true(a$anchor %in% c("previous_cycle", "same_phase")); expect_identical(a$default, "previous_cycle"); expect_equal(a$min_gain, 0.05)
      expect_identical(a$slope_rule, m$slope_rule)
      chosen <- a$prediction[[a$anchor]]
      expect_equal(m$prediction$industries[[id]][c("sigma2", "pairs", "df", "critical_95")], chosen[c("sigma2", "pairs", "df", "critical_95")], info = paste(ch, id))
      expect_identical(a$prediction$previous_cycle$pairs, 15L)                   # pairs (t-2, t): 5 per industry
      expect_identical(a$prediction$same_phase$pairs, 12L)                       # pairs (t-4, t): 4 per industry (2016<-2012 ... 2022<-2018), never the fallback
      expect_equal(a$prediction$same_phase$critical_95, qt(0.975, 11), tolerance = 1e-12)
      expect_equal(a$backtest$same_phase_gain$loeo, 1 - a$backtest$same_phase$loeo_mae / a$backtest$previous_cycle$loeo_mae, tolerance = 1e-12)
      expect_identical(a$backtest$previous_cycle$loeo_n, 5L); expect_identical(a$backtest$previous_cycle$chrono_n, 2L)
    }
  }
})

test_that("today's anchor table: banks House favours same-phase in leave-one-election-out (-32%) but not chronologically, so the default previous-cycle stays everywhere", {
  b <- test_bundle2()
  h <- b$share_models$control_House$anchors$commercial_banks
  expect_equal(h$backtest$previous_cycle$loeo_mae, 1.5439, tolerance = 1e-3); expect_equal(h$backtest$same_phase$loeo_mae, 1.0535, tolerance = 1e-3)
  expect_equal(h$backtest$previous_cycle$chrono_mae, 5.082, tolerance = 1e-3); expect_equal(h$backtest$same_phase$chrono_mae, 5.958, tolerance = 1e-3)
  expect_equal(h$backtest$same_phase_gain$loeo, 0.3177, tolerance = 1e-3); expect_lt(h$backtest$same_phase_gain$chrono, 0)
  expect_identical(h$anchor, "previous_cycle"); expect_match(h$reason, "previous_cycle kept")
  for (ch in c("House", "Senate")) for (a in b$share_models[[paste0("control_", ch)]]$anchors) expect_identical(a$anchor, "previous_cycle")
  # the per-industry calculator-path MAE G6 compares is that of the chosen anchor
  expect_equal(b$share_models$control_House$evidence$calculator_path_loeo_mae_by_industry$commercial_banks, 1.5439, tolerance = 1e-3)
  expect_identical(b$share_models$control_House$evidence$calculator_path, "by_industry")
})

test_that("the cross-field invariants reject a bundle whose chosen anchor breaks the selection rule, and accept the consistent flip", {
  parsed <- rf_canonical_json(test_bundle2())
  flip <- parsed
  flip$share_models$control_House$anchors$commercial_banks$anchor <- "same_phase"
  flip$share_models$control_House$prediction$industries$commercial_banks <- flip$share_models$control_House$anchors$commercial_banks$prediction$same_phase
  expect_true(any(grepl("same_phase chosen without beating", rf_validate_parsed(flip))))
  a <- parsed$share_models$control_House$anchors$commercial_banks
  win <- parsed                                                                   # same-phase wins in both designs but previous-cycle is kept
  w <- win$share_models$control_House$anchors$commercial_banks
  w$backtest$same_phase$loeo_mae <- a$backtest$previous_cycle$loeo_mae * 0.5; w$backtest$same_phase$chrono_mae <- a$backtest$previous_cycle$chrono_mae * 0.5
  w$backtest$same_phase_gain <- list(loeo = 0.5, chrono = 0.5)
  win$share_models$control_House$anchors$commercial_banks <- w
  expect_true(any(grepl("previous_cycle kept although", rf_validate_parsed(win))))
  w$anchor <- "same_phase"; win$share_models$control_House$anchors$commercial_banks <- w
  win$share_models$control_House$prediction$industries$commercial_banks <- w$prediction$same_phase
  expect_identical(rf_validate_parsed(win), character(0))
})


test_that("publication identities distinguish same-day rebuilds without changing the input vintage", {
  args <- list(input_hash = "01234567", run_id = "run-a", built_at_utc = "2026-09-30T12:00:00Z", code_sha256 = strrep("a", 64))
  first <- do.call(rf_model_version, args)
  expect_identical(first, do.call(rf_model_version, args))
  for (field in c("run_id", "built_at_utc", "code_sha256")) {
    changed <- args
    changed[[field]] <- switch(field, run_id = "run-b", built_at_utc = "2026-09-30T12:00:01Z", code_sha256 = strrep("b", 64))
    expect_false(identical(first, do.call(rf_model_version, changed)), info = field)
  }
})

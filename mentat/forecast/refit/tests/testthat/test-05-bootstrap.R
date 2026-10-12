# Wild-cluster and selection-repeated p-values versus the Codex numbers (spec 3.9, 4.4). The numpy PCG64 stream is
# reproduced by rf_rng.R, so the 9,999-draw selection bootstraps agree EXACTLY, not just statistically.

test_that("PAC selection-repeated global p is 22 of the 216 enumerated Webb vectors (controls-only null)", {
  ctx <- test_ctx()
  s <- rf_selection_repeated_pac(ctx$pac_panel)
  expect_identical(s$hits, 22L)
  expect_identical(s$draws, 216L)
  expect_equal(s$p, 22 / 216, tolerance = 1e-15)
  expect_lte(abs(s$p - v1_bundle()$pac_model$selection_repeated_p), 1e-12)
  expect_identical(s$observed_selection, "G_current")
})

test_that("PAC conditional sector wild-cluster p equals 22/216 and the enumeration needs no seed", {
  ctx <- test_ctx()
  m <- ctx$fits$pac
  X <- model.matrix(m); y <- model.response(model.frame(m)); k <- match("G_current", colnames(X))
  w <- rf_wcr(X, y, ctx$pac_panel$sector[order(ctx$pac_panel$sector, ctx$pac_panel$cycle_end)], k, "webb", entropy = c(1, 2, 3, 4))
  expect_true(w$enumerated)
  expect_identical(w$draws, 216L)
  expect_lte(abs(w$p - 0.101852), 1e-6)
  w2 <- rf_wcr(X, y, ctx$pac_panel$sector[order(ctx$pac_panel$sector, ctx$pac_panel$cycle_end)], k, "webb", entropy = c(9, 9, 9, 9))
  expect_identical(w2$p, w$p)   # enumerated draws do not depend on the entropy
})

test_that("lobbying sector wild-cluster Webb p is 1,432 of 46,656 enumerated draws; sector CR2 p matches", {
  ctx <- test_ctx()
  m <- ctx$fits$lobby
  d <- ctx$lobby$diffs
  X <- model.matrix(m); y <- model.response(model.frame(m)); k <- match("dg", colnames(X))
  w <- rf_wcr(X, y, d$study_sector, k, "webb", entropy = c(1, 2, 3, 4))
  expect_true(w$enumerated)
  expect_identical(w$hits, 1432L)
  expect_identical(w$draws, 46656L)
  expect_equal(w$p, 1432 / 46656, tolerance = 1e-15)
  b <- v1_bundle()$lobby_model
  expect_lte(abs(w$p - b$wild_p), 1e-15)
  cr2 <- rf_cr2(m, "dg", d$study_sector)
  expect_lte(abs(cr2$p - b$CR2_p), 1e-10)
  expect_lte(abs(cr2$p - 0.0840463212361729), 1e-10)
})

test_that("random wild draws are reproducible from their entropy and differ across entropies (negative control)", {
  ctx <- test_ctx()
  m <- ctx$fits$lobby
  d <- ctx$lobby$diffs
  X <- model.matrix(m); y <- model.response(model.frame(m)); k <- match("dg", colnames(X))
  yr <- d$year
  a <- rf_wcr(X, y, yr, k, "webb", entropy = c(20260929, 11, 22, 33), B = 2999)
  b <- rf_wcr(X, y, yr, k, "webb", entropy = c(20260929, 11, 22, 33), B = 2999)
  c2 <- rf_wcr(X, y, yr, k, "webb", entropy = c(20260929, 11, 22, 34), B = 2999)
  expect_false(a$enumerated)
  expect_identical(a$p, b$p)
  expect_identical(a$draws, 2999L)
  expect_false(identical(a$p, c2$p))
  # Rademacher is enumerated when 2^G is small: G = 15 years -> 32,768 vectors, no seed
  r <- rf_wcr(X, y, yr, k, "rademacher", entropy = c(1, 1, 1, 1))
  expect_true(r$enumerated)
  expect_identical(r$draws, 32768L)
})

test_that("Holm adjustment matches analyze.py (unit)", {
  expect_equal(rf_holm(c(0.01, 0.04, 0.03, 0.005)), c(0.03, 0.06, 0.06, 0.02), tolerance = 1e-15)
  expect_equal(rf_holm(c(NA, 0.5, 1)), c(1, 1, 1))
  expect_identical(length(rf_holm(numeric())), 0L)
  # monotone in the sorted order and never above 1
  set.seed(1)
  p <- runif(48)
  h <- rf_holm(p)
  expect_true(all(h >= p - 1e-15) && all(h <= 1))
  expect_false(is.unsorted(h[order(p)]))
})

test_that("the stepwise grid reproduces selected_models.csv for all 48 cases: masks, selection-repeated p, Holm", {
  ctx <- test_ctx()
  new <- ctx$stepwise$selected
  ref <- ref_csv("selected_models.csv")
  expect_equal(nrow(new), 48L)
  new <- new[match(ref$case, new$case), ]
  expect_identical(as.integer(new$selected_mask), as.integer(ref$selected_mask))
  expect_identical(as.logical(new$forecast_selected), as.logical(ref$forecast_selected))
  expect_identical(as.integer(new$n), as.integer(ref$n))
  expect_identical(as.integer(new$parameters), as.integer(ref$parameters))
  expect_identical(as.integer(new$residual_df), as.integer(ref$residual_df))
  expect_identical(as.numeric(new$selection_repeated_p), as.numeric(ref$selection_repeated_p))   # exact, all 48
  expect_lte(max_abs(new$Holm_all_48, ref$Holm_all_48), 1e-12)
  expect_lte(max_abs(new$Holm_outcome_family, ref$Holm_outcome_family), 1e-12)
  for (col in c("AICc", "adjusted_R2", "forced_only_AICc", "full_model_AICc", "full_model_adjusted_R2"))
    expect_lte(max_abs(new[[col]], ref[[col]]), 1e-9, label = col)
  expect_identical(as.character(new$selected_terms), ref$selected_terms)
  expect_identical(as.integer(new$global_best_mask), as.integer(ref$global_best_mask))
  expect_identical(as.logical(new$stepwise_is_global_best), as.logical(ref$stepwise_is_global_best))
  # selected-model Forecast coefficients where the Forecast term is retained
  sel <- as.logical(ref$forecast_selected)
  for (col in c("forecast_estimate", "forecast_ordinary_p", "forecast_CR2_p", "forecast_CR2_low", "forecast_CR2_high", "forecast_CR2_df")) {
    ok <- sel & !is.na(ref[[col]]) & (ref$scope == "pooled")
    expect_lte(max_abs(new[[col]][ok], ref[[col]][ok]), 1e-9, label = col)
  }
  # the twelve pooled cases quoted in the spec
  pooled <- ref$scope == "pooled"
  expect_identical(sort(unique(ref$selection_repeated_p[pooled])), sort(unique(new$selection_repeated_p[pooled])))
})

test_that("interpretable_effects pooled rows (the 12 bundle evidence rows) reproduce effect, CR2 interval and p", {
  ctx <- test_ctx()
  new <- ctx$stepwise$effects
  ref <- ref_csv("interpretable_effects.csv")
  ref <- ref[ref$scope == "pooled", ]
  expect_equal(nrow(new), 12L)
  new <- new[match(ref$case, new$case), ]
  worst <- 0
  for (col in c("effect", "conditional_CR2_low", "conditional_CR2_high", "ordinary_p", "conditional_CR2_p", "selection_repeated_p", "Holm_outcome_family", "Holm_all_48")) {
    ok <- !is.na(ref[[col]])
    d <- max_abs(new[[col]][ok], ref[[col]][ok])
    worst <- max(worst, d)
    expect_lte(d, 1e-9, label = col)
  }
  expect_identical(as.character(new$contrast), ref$contrast)
  expect_identical(as.character(new$effect_unit), ref$effect_unit)
  message("effects max deviation: ", formatC(worst, format = "e", digits = 2))
})

test_that("robustness blocks agree with the enumerated rows of robustness_pvalues.csv (self-consistency)", {
  ctx <- test_ctx()
  fx <- utils::read.csv(file.path(testthat::test_path(), "fixtures", "robustness_pvalues.csv"), stringsAsFactors = FALSE)
  fx <- fx[fx$note %in% c("lm summary", "sandwich::vcovHC HC1", "vcovCL HC1, t(G-1)", "clubSandwich Satterthwaite", "full enumeration"), ]
  checks <- list(
    list(id = "pac", m = ctx$fits$pac, d = ctx$fits_data$pac, key = "G_current", clusters = c("sector", "cycle_end")),
    list(id = "control_House", m = ctx$fits$control_House, d = ctx$fits_data$control_House, key = "P_R", clusters = c("cycle_end", "sector")),
    list(id = "control_Senate", m = ctx$fits$control_Senate, d = ctx$fits_data$control_Senate, key = "P_R", clusters = c("cycle_end", "sector")),
    list(id = "seats_House", m = ctx$fits$seats_House, d = ctx$fits_data$seats_House, key = "pred_rep_net_change", clusters = c("cycle_end", "sector")))
  worst <- 0
  for (ck in checks) {
    blk <- rf_robustness_block(ck$id, ck$m, ck$d, ck$key, ck$clusters, fold_vars = NULL, b_random = 999L)
    tab <- blk$table
    ref <- fx[fx$model == ck$id, ]
    expect_gt(nrow(ref), 5L)
    for (i in seq_len(nrow(ref))) {
      r <- tab[tab$method == ref$method[i], ]
      expect_equal(nrow(r), 1L, info = paste(ck$id, ref$method[i]))
      for (col in c("SE", "df", "p", "draws")) {
        if (is.na(ref[[col]][i])) next
        d <- abs(r[[col]] - ref[[col]][i])
        worst <- max(worst, if (col == "draws") 0 else d)
        expect_lte(d, 1e-9, label = paste(ck$id, ref$method[i], col))
      }
    }
    expect_true(any(grepl("ROBUSTNESS", blk$text)))
  }
  message("robustness enumerated rows max deviation: ", formatC(worst, format = "e", digits = 2))
})

test_that("leave-one-election-out and leave-one-cluster-out estimates agree with fold_coefficients.csv", {
  ctx <- test_ctx()
  fc <- utils::read.csv(file.path(testthat::test_path(), "fixtures", "fold_coefficients.csv"), stringsAsFactors = FALSE)
  for (spec in list(list(id = "pac", key = "G_current", vars = c("cycle_end", "sector")),
                    list(id = "control_House", key = "P_R", vars = c("cycle_end", "sector")))) {
    for (v in spec$vars) {
      new <- rf_drop_folds(ctx$fits[[spec$id]], ctx$fits_data[[spec$id]], v, spec$key)
      ref <- fc[fc$model == spec$id & fc$dropped_var == v & fc$fold_type != "forecast window", ]
      expect_equal(nrow(new), nrow(ref))
      new <- new[match(as.character(ref$dropped), new$dropped), ]
      expect_lte(max_abs(new$estimate, ref$estimate), 1e-9)
      expect_lte(max_abs(new$ordinary_p, ref$ordinary_p), 1e-9)
      expect_identical(as.integer(new$residual_df), as.integer(ref$residual_df))
      expect_identical(as.integer(new$n), as.integer(ref$n))
    }
  }
})

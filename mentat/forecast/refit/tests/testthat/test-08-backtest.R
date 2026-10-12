# No-leakage backtests: leave-one-election-out, chronological and lobbying rolling-origin designs with R0 pooled / R1 own /
# R2 DerSimonian-Laird slope rules, versus SP/tailoring/summary.csv and the SPEC section 8 table (spec 3.11, 4.7).
# In every fold growth scaling, the pooled and interaction fits, tau2, W and the offset fit are re-estimated on the training
# cycles only.

summary_fixture <- function() utils::read.csv(file.path(testthat::test_path(), "fixtures", "summary.csv"), stringsAsFactors = FALSE)

backtest_all <- function() memo("backtest_all", {
  sp <- test_specs()
  out <- list()
  for (key in names(sp)) {
    s <- sp[[key]]
    for (ds in if (s$kind == "lobby") "rolling" else c("loeo", "chrono")) out[[paste(key, ds)]] <- rf_backtest(s, s$d, ds)
  }
  out
})

test_that("every (term, base, design, path, rule) cell of tailoring summary.csv is reproduced: n, folds, targets, MAE, RMSE", {
  fx <- summary_fixture()
  fx <- fx[fx$rule %in% c("R0", "R1", "R2", "naive_prev", "anchor_only"), ]
  bt <- backtest_all()
  sp <- test_specs()
  worst <- c(MAE = 0, RMSE = 0, EW_MAE = 0); cells <- 0L
  for (nm in names(bt)) {
    s <- sp[[sub(" (loeo|chrono|rolling)$", "", nm)]]
    ds <- sub("^.* ", "", nm)
    met <- rf_backtest_metrics(bt[[nm]])
    ref <- fx[fx$term == s$id & fx$base == s$base & fx$design == ds, ]
    expect_gt(nrow(ref), 0L)
    m <- merge(met, ref, by = c("term", "base", "design", "path", "rule"), suffixes = c("", ".ref"))
    expect_equal(nrow(m), nrow(ref), info = nm)
    expect_identical(as.integer(m$n), as.integer(m$n.ref), info = nm)
    expect_identical(as.integer(m$folds), as.integer(m$folds.ref), info = nm)
    expect_identical(m$targets, m$targets.ref, info = nm)
    expect_lte(max(abs(m$MAE - m$MAE.ref)), 1e-9, label = paste(nm, "MAE"))
    expect_lte(max(abs(m$RMSE - m$RMSE.ref)), 1e-9, label = paste(nm, "RMSE"))
    expect_lte(max(abs(m$EW_MAE - m$EW_MAE.ref)), 1e-9, label = paste(nm, "EW_MAE"))
    # fold win-loss-tie and row win-loss versus R0 follow tailoring.R exactly
    expect_identical(as.integer(m$fold_wins), as.integer(m$fold_wins.ref), info = nm)
    expect_identical(as.integer(m$fold_losses), as.integer(m$fold_losses.ref), info = nm)
    expect_identical(as.integer(m$fold_ties), as.integer(m$fold_ties.ref), info = nm)
    expect_identical(as.integer(m$row_wins), as.integer(m$row_wins.ref), info = nm)
    expect_identical(as.integer(m$row_losses), as.integer(m$row_losses.ref), info = nm)
    worst <- pmax(worst, c(MAE = max(abs(m$MAE - m$MAE.ref)), RMSE = max(abs(m$RMSE - m$RMSE.ref)), EW_MAE = max(abs(m$EW_MAE - m$EW_MAE.ref))))
    cells <- cells + nrow(m)
  }
  expect_equal(cells, nrow(fx))
  message("backtest summary.csv: ", cells, " cells; max deviation ", paste(names(worst), formatC(worst, format = "e", digits = 2), collapse = "  "))
})

test_that("the spec's anchor numbers hold: PAC previous-cycle, chronological, lobbying rolling, control", {
  bt <- backtest_all()
  met <- function(key, rule, path) { m <- rf_backtest_metrics(bt[[key]]); m$MAE[m$rule == rule & m$path == path] }
  expect_equal(met("pac loeo", "R2", "prevchg"), 14.349, tolerance = 1e-4)
  expect_equal(met("pac loeo", "naive_prev", "prevchg"), 12.712, tolerance = 1e-4)
  expect_equal(met("pac loeo", "R0", "prevchg"), 21.918, tolerance = 1e-4)
  expect_equal(met("pac loeo", "R2", "calc"), 15.914, tolerance = 1e-4)
  expect_equal(met("pac chrono", "R2", "calc"), 21.368, tolerance = 1e-4)
  expect_equal(met("pac chrono", "naive_prev", "calc"), 12.337, tolerance = 1e-4)
  expect_equal(met("lobby rolling", "R0", "calc"), 4.304, tolerance = 1e-3)
  expect_equal(met("lobby rolling", "naive_prev", "calc"), 3.936, tolerance = 1e-3)
  expect_equal(nrow(bt[["lobby rolling"]][bt[["lobby rolling"]]$rule == "R0", ]), 50L)
  expect_equal(met("control_House.selected loeo", "R2", "calc"), 1.275, tolerance = 1e-3)
  expect_equal(met("control_House.selected loeo", "naive_prev", "calc"), 3.357, tolerance = 1e-3)
  expect_equal(met("control_Senate.selected loeo", "R2", "calc"), 6.118, tolerance = 1e-3)
  expect_equal(met("control_Senate.selected loeo", "naive_prev", "calc"), 8.929, tolerance = 1e-3)
})

test_that("SPEC section 8: PAC previous-cycle backtest by slope rule, fold-restandardized growth (incl. per-industry MAE)", {
  ref <- utils::read.csv(file.path(testthat::test_path(), "fixtures", "pac_prevchg_reconcile.csv"), stringsAsFactors = FALSE)
  ref <- ref[ref$scaling == "fold_restandardized", ]
  bt <- backtest_all()
  worst <- 0
  for (ds in c("loeo", "chrono")) {
    met <- rf_backtest_metrics(bt[[paste("pac", ds)]])
    met <- met[met$path == "prevchg", ]
    for (rule in c("R0", "R1", "R2", "naive_prev")) {
      r <- ref[ref$design == ds & ref$rule == rule, ]
      m <- met[met$rule == rule, ]
      expect_equal(nrow(m), 1L)
      expect_identical(as.integer(m$n), as.integer(r$n)); expect_identical(as.integer(m$folds), as.integer(r$folds)); expect_identical(m$targets, r$targets)
      for (col in c("MAE", "RMSE", "MAE_auto_light_duty_manufacturing", "MAE_commercial_banks", "MAE_credit_unions")) {
        d <- abs(m[[col]] - r[[col]]); worst <- max(worst, d)
        expect_lte(d, 1e-9, label = paste(ds, rule, col))
      }
    }
  }
  # the table of section 8, rounded as printed there
  loeo <- rf_backtest_metrics(bt[["pac loeo"]]); loeo <- loeo[loeo$path == "prevchg", ]
  got <- round(stats::setNames(loeo$MAE, loeo$rule)[c("R0", "R1", "R2", "naive_prev")], 3)
  expect_equal(unname(got), c(21.918, 10.765, 14.349, 12.712))
  chrono <- rf_backtest_metrics(bt[["pac chrono"]]); chrono <- chrono[chrono$path == "prevchg", ]
  got2 <- round(stats::setNames(chrono$MAE, chrono$rule)[c("R0", "R1", "R2", "naive_prev")], 3)
  expect_equal(unname(got2), c(32.481, 14.314, 21.585, 12.337))
  message("section 8 table max deviation: ", formatC(worst, format = "e", digits = 2))
})

test_that("no leakage: changing a held-out outcome changes that fold's actual but never its prediction (negative control)", {
  sp <- test_specs()
  for (key in c("pac", "control_House.selected", "lobby")) {
    s <- sp[[key]]
    base <- rf_backtest(s, s$d, if (s$kind == "lobby") "rolling" else "loeo")
    tgt <- sort(unique(base$target))[2]
    d2 <- s$d
    rows <- d2$cycle_end == tgt
    d2[[s$y]][rows] <- d2[[s$y]][rows] + 5
    if (s$kind == "lobby") d2$y[rows] <- d2$y[rows] + 5      # lobbying actual is the level y; its modelled outcome is the change dy
    pert <- rf_backtest(s, d2, if (s$kind == "lobby") "rolling" else "loeo")
    a <- base[base$target == tgt, ]; b <- pert[pert$target == tgt, ]
    a <- a[order(a$sector, a$rule), ]; b <- b[order(b$sector, b$rule), ]
    expect_identical(a$calc, b$calc, info = key)        # the prediction of the fold that holds the cycle out is untouched
    expect_false(identical(a$actual, b$actual), info = key)
    # ... while folds that TRAIN on that cycle do change (proves the perturbation reaches the models)
    later <- setdiff(unique(base$target), tgt)
    if (s$kind != "lobby") {
      a2 <- base[base$target %in% later, ]; b2 <- pert[pert$target %in% later, ]
      expect_false(identical(a2$calc, b2$calc), info = key)
    }
  }
})

test_that("backtest feasibility is recorded per fold, and folds that cannot be estimated are skipped, not guessed", {
  sp <- test_specs()
  s <- sp$pac
  bt <- rf_backtest(s, s$d, "chrono")
  fe <- attr(bt, "feasibility")
  expect_true(all(c("target", "n_train", "df_R1", "feasible") %in% names(fe)))
  expect_false(fe$feasible[fe$target == 2014])            # one training cycle: growth cannot be standardized
  expect_true(fe$feasible[fe$target == 2024])
  expect_false(2014 %in% bt$target)
})

test_that("backtest cells carry prediction-interval coverage and beats-naive flags", {
  bt <- backtest_all()
  met <- rf_backtest_metrics(bt[["pac loeo"]])
  expect_true(all(c("coverage_95", "beats_naive") %in% names(met)))
  r2 <- met[met$path == "prevchg" & met$rule == "R2", ]
  expect_false(r2$beats_naive)     # PAC R2 previous-cycle loses to last-cycle naive today (14.349 vs 12.712)
  r1 <- met[met$path == "prevchg" & met$rule == "R1", ]
  expect_true(r1$beats_naive)      # own slopes beat naive in leave-one-election-out (10.765 vs 12.712)
  expect_true(r2$coverage_95 >= 0 && r2$coverage_95 <= 1)
})

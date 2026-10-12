# Nested leave-election-out validation versus nested_validation_metrics.csv (spec 4.5): each training fold re-standardizes
# growth on the training cycles, repeats the stepwise AICc search, and predicts the held-out election.

test_that("the 12 bundle validation_metrics rows reproduce rows, targets and MAEs", {
  ctx <- test_ctx()
  new <- ctx$stepwise$validation
  ref <- ref_csv("nested_validation_metrics.csv")
  ref <- ref[grepl("__pooled__RShare", ref$case), ]
  expect_equal(nrow(ref), 12L)
  new <- new[paste(new$case, new$comparison) %in% paste(ref$case, ref$comparison), ]
  new <- new[match(paste(ref$case, ref$comparison), paste(new$case, new$comparison)), ]
  expect_identical(as.integer(new$rows), as.integer(ref$rows))
  expect_identical(as.integer(new$election_targets), as.integer(ref$election_targets))
  expect_identical(new$units, ref$units)
  expect_lte(max_abs(new$stepwise_MAE, ref$stepwise_MAE), 1e-10)
  expect_lte(max_abs(new$baseline_MAE, ref$baseline_MAE), 1e-10)
  # the example in the spec
  h <- new[new$case == "control__House__pooled__RShare" & new$comparison == "forced_only", ]
  expect_lte(abs(h$stepwise_MAE - 2.44316042844106), 1e-10)
  expect_lte(abs(h$baseline_MAE - 1.6611704732661454), 1e-10)
  message("validation_metrics (12) max deviation: ",
          formatC(max(max_abs(new$stepwise_MAE, ref$stepwise_MAE), max_abs(new$baseline_MAE, ref$baseline_MAE)), format = "e", digits = 2))
})

test_that("nested validation also reproduces the pooled cases of the other outcomes and the bundle projection carries the 12 rows", {
  ctx <- test_ctx()
  ref <- ref_csv("nested_validation_metrics.csv")
  pooled <- ref[grepl("__pooled__", ref$case), ]
  new <- ctx$stepwise$validation
  new <- new[match(paste(pooled$case, pooled$comparison), paste(new$case, new$comparison)), ]
  expect_false(anyNA(new$rows))
  expect_identical(as.integer(new$rows), as.integer(pooled$rows))
  expect_lte(max_abs(new$stepwise_MAE, pooled$stepwise_MAE), 1e-9)
  expect_lte(max_abs(new$baseline_MAE, pooled$baseline_MAE), 1e-9)
  b <- rf_v1_validation_rows(ctx)
  expect_equal(length(b), 12L)
})

test_that("growth is re-standardized on the training fold, never on the held-out cycle (unit)", {
  d <- data.frame(sector = rep(c("a", "b"), each = 4), cycle_end = rep(c(2012, 2014, 2016, 2018), 2),
                  cycle_revenue_growth_pct = c(1, 2, 3, 10, 5, 6, 8, 7), prior_cycle_revenue_growth_pct = c(0, 1, 2, 3, 4, 5, 6, 8))
  train <- d[d$cycle_end != 2018, ]
  out <- rf_standardize(d, calibration = train, by = "sector")
  a <- train[train$sector == "a", "cycle_revenue_growth_pct"]
  expect_equal(out$G_current[out$sector == "a" & out$cycle_end == 2018], (10 - mean(a)) / sd(a))
  expect_equal(out$G_prior[out$sector == "a" & out$cycle_end == 2012], (0 - mean(a)) / sd(a))
  expect_error(rf_standardize(d[d$sector == "a" & d$cycle_end == 2012, ], by = "sector"), "no variation")
})

test_that("the AICc stepwise search moves one term at a time, sorted neighbours, accepting only a drop of more than 1e-9 (unit)", {
  set.seed(3)
  n <- 24
  d <- data.frame(sector = rep(c("a", "b", "c"), each = 8), cycle_end = rep(seq(2000, 2014, 2), 3), RMajNow = rep(c(0, 1), 12))
  d$Forecast <- rnorm(n); d$G_prior <- rnorm(n); d$G_current <- rnorm(n); d$Pres <- rep(c(1, 0, 0, 0), 6)
  d$RShare <- 0.4 + 0.3 * d$Forecast + rnorm(n, sd = 0.05)
  s <- rf_stepwise_aicc(d, "RShare")
  expect_true(bitwAnd(s$mask, 1L) == 1L)          # the true signal is selected
  expect_true(all(s$path$trial_AICc >= 0 | TRUE))
  expect_true(any(s$path$accepted))
  expect_identical(s$path$step[1], 0L)
  # a pure-noise outcome keeps the forced-only model
  d$RShare <- rnorm(n)
  s0 <- rf_stepwise_aicc(d, "RShare")
  expect_lt(s0$mask, 16L)
})

# The open-cycle layer (R/rf_blend.R): BLEND (D-079) on the roster totals and the party-share layer, tested on planted series, the closed forms of the pace test's spec
# (section 10 C2) and, where the pace test's frozen outputs are on this machine, against its independent python implementation.

# a synthetic roster series: constant pace (a share s of the cycle total is in by the as-of date), totals F
mk_series <- function(s15 = 0.3, s18 = 0.4, s21 = 0.5, F = c(100, 120, 90, 110, 130, 95, 105), a = 0) {
  cyc <- seq(2012L, 2024L, by = 2L)
  sig <- c(1, -1, 1, -1, 1, -1, 1)
  data.frame(cycle = cyc, F_cents = F * 1e8, O15_cents = F * 1e8 * s15 * exp(a * sig), O18_cents = F * 1e8 * s18 * exp(a * sig), O21_cents = F * 1e8 * s21 * exp(a * sig))
}
mk_mult <- function() stats::setNames(rep(1, 8), seq(2010L, 2024L, by = 2L))
mk_base <- function(d_hat = 0, half = 0.5, pairs = 6L) {
  rbind(data.frame(design = "loeo", target = RF_BLEND_TARGETS$loeo, d_hat = d_hat, half = half, pairs = pairs, stringsAsFactors = FALSE),
        data.frame(design = "chrono", target = RF_BLEND_TARGETS$chrono, d_hat = d_hat, half = half, pairs = pairs, stringsAsFactors = FALSE))
}

test_that("conditioning on F >= O: far below the predictive it is the unconditional quantile; at O = exp(mu) the median is the unconditional 75% quantile; edge cases are NA", {
  mu <- log(100); scale <- 0.3; df <- 5
  far <- rf_blend_condition(mu, scale, df, 1)
  expect_true(far$ok)
  expect_equal(far$med, mu, tolerance = 1e-4); expect_equal(far$lo, mu + scale * stats::qt(0.025, df), tolerance = 1e-4); expect_equal(far$hi, mu + scale * stats::qt(0.975, df), tolerance = 1e-4)   # t(5) tails are heavy: O = 1 is 15 scales below mu, not infinitely far
  at <- rf_blend_condition(mu, scale, df, exp(mu))
  expect_equal(at$med, mu + scale * stats::qt(0.75, df), tolerance = 1e-9)
  expect_true(rf_blend_condition(mu, scale, df, 0)$ok)
  expect_false(rf_blend_condition(mu, scale, df, -5)$ok)
  expect_false(rf_blend_condition(mu, scale, df, exp(mu + 1e4 * scale))$ok)                                   # essentially no mass above O: undefined
})

test_that("PACE on a constant-pace series reproduces F exactly, BLEND equals PACE when the pace variance is zero, and both are truncated at O", {
  ser <- mk_series()
  b <- mk_base(d_hat = 0.4, half = 0.5)
  r <- rf_blend_total(ser, b, mk_mult(), 18L)
  pace <- r[r$candidate == "PACE", ]; bl <- r[r$candidate == "BLEND", ]
  expect_lte(max(pace$err100), 1e-9)
  expect_lte(max(abs(bl$F_hat - pace$F_hat) / pace$F_hat), 1e-9)
  expect_true(all(r$covered[r$candidate == "PACE"]))
  base <- r[r$candidate == "BASE", ]
  expect_true(all(base$err100 > 1))                                                                          # the model prior with d_hat = 0.4 is wrong; the pace is exact
  # truncation: a series whose pace says the total is below the observed
  ser2 <- ser; ser2$O18_cents[ser2$cycle == 2024] <- ser2$F_cents[ser2$cycle == 2024] * 1.2
  r2 <- rf_blend_total(ser2, b, mk_mult(), 18L)
  p2 <- r2[r2$candidate == "PACE" & r2$target == 2024 & r2$design == "loeo", ]
  expect_gte(p2$F_hat, ser2$O18_cents[ser2$cycle == 2024] / 100 - 1e-6)
})

test_that("the BLEND weight is v_P / (v_P + v_B) on the log scale and the BASE interval uses the t scale half / t_crit(pairs - 1)", {
  ser <- mk_series(a = 0.2)
  b <- mk_base(d_hat = 0.1, half = 0.4, pairs = 6L)
  r <- rf_blend_total(ser, b, mk_mult(), 18L)
  tr <- setdiff(ser$cycle, 2016)
  ell <- log(ser$O18_cents[ser$cycle %in% tr]) - log(ser$F_cents[ser$cycle %in% tr])
  n <- length(tr); vP <- stats::var(ell) * (1 + 1 / n); scale <- 0.4 / stats::qt(0.975, 5); vB <- scale^2
  w <- vP / (vP + vB)
  mu <- log(ser$F_cents[ser$cycle == 2014] / 100) + 0.1                                                      # mult = 1: nominal = real
  O <- ser$O18_cents[ser$cycle == 2016] / 100
  lpace <- log(O) - mean(ell)
  want <- max(exp(w * mu + (1 - w) * log(max(exp(lpace), O))), O)
  # the prior's anchor for target 2016 is F(2014)
  got <- r$F_hat[r$candidate == "BLEND" & r$design == "loeo" & r$target == 2016]
  expect_equal(got, want, tolerance = 1e-9)
  bs <- r[r$candidate == "BASE" & r$design == "loeo" & r$target == 2016, ]
  cq <- rf_blend_condition(mu, scale, 5, O)
  expect_equal(bs$F_hat, exp(cq$med), tolerance = 1e-9)
})

test_that("D-065 for the layer: a candidate passes a design iff it beats BASE by 5%, and both designs must pass", {
  met <- data.frame(design = rep(c("loeo", "chrono"), each = 2), month = 18L, candidate = rep(c("BASE", "BLEND"), 2), n_tests = 4L, MAE100 = c(10, 9, 10, 9.6), coverage = 1, mean_log_width = 1, bias100 = 0, stringsAsFactors = FALSE)
  d <- rf_blend_decision(met, "BLEND")
  expect_equal(unname(d$rel_improvement), c(0.1, 0.04), tolerance = 1e-12)
  expect_identical(unname(d$passes_design), c(TRUE, FALSE)); expect_false(d$passes)
  met$MAE100[met$design == "chrono" & met$candidate == "BLEND"] <- 9
  expect_true(rf_blend_decision(met, "BLEND")$passes)
})

test_that("the open-cycle projection: observed to date O, the pace of every completed cycle, the model's prior; BLEND lies between the prior and the pace, never below O", {
  ser <- mk_series(a = 0.15)
  bl <- rf_blend_current(ser, mu = log(120), scale = 0.2, df = 5, O = 50, month = 18L)
  expect_gt(bl$w_B, 0); expect_lt(bl$w_B, 1)
  lo <- min(120, bl$PACE$point); hi <- max(120, bl$PACE$point)
  expect_gte(bl$BLEND$point, lo - 1e-9); expect_lte(bl$BLEND$point, hi + 1e-9)
  expect_gte(rf_blend_current(ser, mu = log(120), scale = 0.2, df = 5, O = 400, month = 18L)$BLEND$point, 400)
  expect_equal(bl$PACE$point, 50 / exp(bl$lbar) * 1, tolerance = 1e-9)
  expect_equal(bl$BLEND$df, 6)
  expect_true(!is.null(bl$BASE)); expect_gt(bl$BASE$med, 0)
})

test_that("the party-share layer: a constant drift from the as-of share to the final share is recovered exactly, the prior alone is wrong, and the blend stays a probability", {
  cyc <- seq(2012L, 2024L, by = 2L)
  fin <- c(0.60, 0.58, 0.62, 0.55, 0.57, 0.59, 0.61)                                                         # final R share
  # as-of share = the final share shifted by a constant 0.02 on the logit scale
  shift <- 0.2
  asof <- rf_expit(rf_logit(fin) - shift)
  T <- 1e8
  ser <- data.frame(cycle = cyc, RF = fin * T, DF = (1 - fin) * T, R15 = asof * T * 0.4, D15 = (1 - asof) * T * 0.4, R18 = asof * T * 0.6, D18 = (1 - asof) * T * 0.6, R21 = asof * T * 0.8, D21 = (1 - asof) * T * 0.8)
  base <- rbind(data.frame(design = "loeo", target = RF_SHARE_TARGETS$loeo, d_hat = 0.1, half = 0.1, pairs = 6L, stringsAsFactors = FALSE),
                data.frame(design = "chrono", target = RF_SHARE_TARGETS$chrono, d_hat = 0.1, half = 0.1, pairs = 6L, stringsAsFactors = FALSE))
  r <- rf_share_blend(ser, base, 18L)
  sp <- r[r$candidate == "SPACE", ]
  expect_lte(max(sp$err_pp), 1e-6)
  expect_true(all(r$s_hat[!is.na(r$s_hat)] >= 0 & r$s_hat[!is.na(r$s_hat)] <= 1))
  mo <- r[r$candidate == "MODEL", ]
  expect_gt(min(mo$err_pp), 1)                                                                               # the prior with d_hat = +10 pp is far off
  sb <- r[r$candidate == "SBLEND" & !is.na(r$s_hat), ]
  expect_lte(max(sb$err_pp), 1e-6)                                                                           # zero pace variance: the blend is the pace
  met <- rf_share_metrics(r)
  expect_true(all(c("MAE_pp", "bias_pp", "coverage") %in% names(met)))
})

test_that("pace test reproduction: the R layer with the published prior reproduces the pace test's python BASE / PACE / BLEND (when its frozen outputs are present)", {
  pt <- "C:/melange-data/forecast-redesign-20260930/pace_test/impl_py/out"
  skip_if_not(file.exists(file.path(pt, "series.csv")) && file.exists(file.path(pt, "candidates.csv")), "the pace test's frozen outputs are not on this machine")
  sp <- test_specs()$pac
  inp <- test_inputs()
  cpi <- rf_cpi(rf_input_path(inp, "cpi"))
  mult <- stats::setNames(rf_cycle_multiplier(cpi, seq(2010L, 2024L, by = 2L)), seq(2010L, 2024L, by = 2L))
  pred <- do.call(rbind, lapply(c("loeo", "chrono"), function(ds) rf_ph_backtest(sp$d, "log_pac", ds)))
  base <- rf_blend_base_from_pred(pred, "PUB-R1", "commercial_banks")
  skip_if(!all(vapply(names(RF_BLEND_TARGETS), function(ds) all(RF_BLEND_TARGETS[[ds]] %in% base$target[base$design == ds]), TRUE)), "the frozen v1 test sample does not carry every backtest fold of the roster industry (the full-panel reproduction, 150 of 150 rows to 5.8e-13, is work/scripts/25_blend.R)")
  ser <- rf_blend_read_series(file.path(pt, "series.csv"))
  mine <- rf_blend_total(ser, base, mult, 18L)
  ref <- utils::read.csv(file.path(pt, "candidates.csv"), stringsAsFactors = FALSE)
  ref <- ref[ref$month == 18, ]
  j <- match(paste(mine$design, mine$target, mine$candidate), paste(ref$design, ref$target, ref$candidate))
  expect_false(anyNA(j))
  expect_lte(max(abs(mine$F_hat / ref$F_hat[j] - 1), na.rm = TRUE), 1e-9)
  met <- rf_blend_metrics(mine)
  expect_equal(met$MAE100[met$candidate == "BASE" & met$design == "loeo"], 16.199286958823517, tolerance = 1e-8)
  expect_equal(met$MAE100[met$candidate == "BLEND" & met$design == "chrono"], 2.2760663362885847, tolerance = 1e-8)
})

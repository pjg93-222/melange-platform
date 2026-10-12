# Partial pooling of the focal slope across industries: the DerSimonian-Laird rule as prototyped in SP/tailoring/tailoring.R
# (spec 3.8, 4.6). Library code only: the v1 parity bundle does not use it.

test_that("PAC: tau2, weights, own and blended slopes reproduce the spec numbers", {
  sp <- test_specs()
  pp <- rf_partial_pool(sp$pac, sp$pac$d)
  expect_identical(pp$status, "partial")
  expect_lte(abs(pp$tau2 - 0.0120246), 1e-6)
  expect_lte(abs(pp$pooled$beta - (-0.168357)), 1e-6)
  w <- pp$weights
  expect_lte(abs(w[["auto_light_duty_manufacturing"]] - 0.771202), 1e-6)
  expect_lte(abs(w[["commercial_banks"]] - 0.742358), 1e-6)
  expect_lte(abs(w[["credit_unions"]] - 0.730387), 1e-6)
  s <- pp$slopes
  expect_lte(abs(s[["auto_light_duty_manufacturing"]] - (-0.252368)), 1e-6)
  expect_lte(abs(s[["commercial_banks"]] - (-0.087281)), 1e-6)
  expect_lte(abs(s[["credit_unions"]] - (-0.096333)), 1e-6)
  # the pooled fit is the frozen v1 fit
  expect_lte(abs(pp$pooled$beta - v1_bundle()$pac_model$beta), 1e-10)
  expect_identical(pp$pooled$df, 11L)
  expect_true(inherits(pp$offset_fit, "lm"))
})

test_that("every (term, base) of full_sample_slopes.csv reproduces: pooled, own, tau2, W, blend, approximate SE", {
  fx <- utils::read.csv(file.path(testthat::test_path(), "fixtures", "full_sample_slopes.csv"), stringsAsFactors = FALSE)
  sp <- test_specs()
  worst <- 0
  for (key in names(sp)) {
    s <- sp[[key]]
    ref <- fx[fx$term == s$id & fx$base == s$base, ]
    expect_gt(nrow(ref), 0L)
    pp <- rf_partial_pool(s, s$d)
    expect_identical(pp$status, "partial", info = key)
    ord <- match(ref$sector, pp$own$industry)
    expect_false(anyNA(ord), info = key)
    cmp <- list(R0_pooled = rep(pp$pooled$beta, nrow(ref)), R0_se = rep(pp$pooled$se, nrow(ref)), R1_own = pp$own$beta[ord], R1_se = pp$own$se[ord],
                R1_p = pp$own$p[ord], tau2_DL = rep(pp$tau2, nrow(ref)), w_DL = unname(pp$weights[ref$sector]), R2_blend = unname(pp$slopes[ref$sector]),
                R2_approx_se = unname(pp$slope_se[ref$sector]))
    for (nm in names(cmp)) {
      d <- max_abs(cmp[[nm]], ref[[nm]])
      worst <- max(worst, d)
      expect_lte(d, 1e-10, label = paste(key, nm))
    }
    expect_identical(as.integer(pp$df_interaction), as.integer(ref$df_R1[1]), info = key)
    expect_identical(as.integer(pp$pooled$df + 0L), as.integer(nrow(s$d) - 0L) - as.integer(length(stats::coef(rf_lm(paste(s$y, "~", s$others, "+", s$f), s$d, "x")))), info = key)
  }
  message("full-sample slopes (", length(sp), " term/base cells) max deviation: ", formatC(worst, format = "e", digits = 2))
})

test_that("control (both bases) and lobbying have tau2 == 0, so the blended slope IS the pooled slope", {
  sp <- test_specs()
  for (key in names(sp)) {
    s <- sp[[key]]
    if (!(grepl("^control", s$id) || s$id == "lobby")) next
    pp <- rf_partial_pool(s, s$d)
    expect_identical(pp$tau2, 0, info = key)
    expect_true(all(pp$weights == 0), info = key)
    expect_identical(unname(pp$slopes), rep(pp$pooled$beta, length(pp$slopes)), info = key)   # R2 == R0 exactly
  }
})

test_that("rf_dl is the DerSimonian-Laird estimator (unit cases)", {
  # identical slopes: no heterogeneity
  expect_identical(rf_dl(c(0.5, 0.5, 0.5), c(0.1, 0.2, 0.3))$tau2, 0)
  # textbook example computed by hand: b = (0, 1, 2), se = (1, 1, 1): Q = 2, C = 3 - 3/3 = 2, tau2 = (2 - 2)/2 = 0
  expect_identical(rf_dl(c(0, 1, 2), c(1, 1, 1))$tau2, 0)
  # b = (0, 3, 6), se = 1: Q = 18, C = 2, tau2 = (18 - 2) / 2 = 8
  r <- rf_dl(c(0, 3, 6), c(1, 1, 1))
  expect_equal(r$Q, 18); expect_equal(r$C, 2); expect_equal(r$tau2, 8); expect_equal(r$mu, 3); expect_identical(r$k, 3L)
  # unequal precision: mu is the inverse-variance mean
  r2 <- rf_dl(c(1, 4), c(1, 2))
  expect_equal(r2$mu, (1 / 1 * 1 + 4 / 4 * 1) / (1 + 0.25))
  # non-finite or non-positive SEs are dropped; a single usable slope gives tau2 = 0
  expect_identical(rf_dl(c(1, 2, 3), c(1, NA, 0))$tau2, 0)
})

test_that("partial pooling falls back to the pooled fit with a stated reason when it is infeasible", {
  sp <- test_specs()
  s <- sp$pac
  one <- s$d[s$d$sector == "commercial_banks", ]
  pp1 <- rf_partial_pool(s, one)
  expect_identical(pp1$status, "pooled")
  expect_match(pp1$reason, "fewer than 2 industries")
  expect_true(all(pp1$weights == 0))
  # too few training rows for the interaction fit: residual df below 2
  small <- s$d[s$d$cycle_end %in% c(2012, 2014), ]
  pp2 <- rf_partial_pool(s, small)
  expect_identical(pp2$status, "pooled")
  expect_match(pp2$reason, "residual df|unidentified")
})

test_that("blended slope variance combines the own and pooled uncertainty as W se^2 + (1-W)^2 se0^2", {
  sp <- test_specs()
  pp <- rf_partial_pool(sp$pac, sp$pac$d)
  W <- pp$weights
  se <- pp$own$se[match(names(W), pp$own$industry)]
  expect_equal(unname(pp$slope_se), unname(sqrt(W * se^2 + (1 - W)^2 * pp$pooled$se^2)), tolerance = 1e-14)
})

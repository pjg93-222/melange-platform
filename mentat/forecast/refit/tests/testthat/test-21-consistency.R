# The coordinator's consistency rule (R/rf_consistency.R) and the 95% intervals of the phase backtests (R/rf_phase.R), tested on planted prediction tables and against the refit's own backtest.

# a prediction table in the layout of rf_ph_backtest: 4 industries x 2 targets x 2 designs, one candidate per call; err = 100 x (pred - actual), half = the 95% half-width (log units)
mk_pred <- function(cand, err_loeo, err_chrono, half = 1, secs = c("a", "b", "c", "d")) {
  one <- function(ds, e) data.frame(series = "y", design = ds, target = rep(c(2020, 2022), each = length(secs)), sector = rep(secs, 2), candidate = cand, actual = 0, pred = e / 100, half = half,
                                    err = e, stringsAsFactors = FALSE)
  rbind(one("loeo", rep(err_loeo, length.out = 2 * length(secs))), one("chrono", rep(err_chrono, length.out = 2 * length(secs))))
}

test_that("the mandated family is the candidates that carry the phase AND the trajectory", {
  cand <- c("PT-G-full-pool", "PT-noG-damp-ind@x22", "PT-G-damp-pool-R2", "PBLT-R1-damp-pool@i22", "PA-R1-T", "PA-R1", "PB-R0", "PUB-R1", "PUBPAC-R1", "naive_prev", "PBL-R2", "PB2T", "PCT-L0.5", "PT-noG-damp-pool@x22")
  expect_setequal(rf_cons_family(cand), c("PT-G-full-pool", "PT-noG-damp-ind@x22", "PT-G-damp-pool-R2", "PBLT-R1-damp-pool@i22", "PA-R1-T", "PT-noG-damp-pool@x22"))
})

test_that("criteria a, b, c: pooled 5% in both designs, every industry within +2%, coverage >= 90% in both designs", {
  base <- mk_pred("B", 10, 10)
  pred <- rbind(base,
    mk_pred("PT-good", c(8, 8, 8, 8), c(8, 8, 8, 8)),                                                      # 20% better everywhere, half = 1 covers everything
    mk_pred("PT-oneworse", c(7, 7, 7, 10.3), c(7, 7, 7, 10.3)),                                            # pooled 11% better but industry d is 3% worse
    mk_pred("PT-edge", c(7, 7, 7, 10.2), c(7, 7, 7, 10.2)),                                                # industry d exactly 2% worse: inside the tolerance
    mk_pred("PT-lowcov", c(8, 8, 8, 8), c(8, 8, 8, 8), half = 0.05),                                       # half-width 0.05 < |err| / 100 = 0.08: nothing covered
    mk_pred("PT-chronoonly", c(10, 10, 10, 10), c(8, 8, 8, 8)))                                            # better only chronologically
  pooled <- rf_cons_pooled(pred, "B"); industry <- rf_cons_industry(pred, "B")
  v <- rf_cons_verdict(pooled[pooled$candidate != "B", ], industry[industry$candidate != "B", ])
  g <- function(cd, col) v[[col]][v$candidate == cd]
  expect_true(g("PT-good", "qualifies"))
  expect_true(g("PT-good", "a_pooled5") && g("PT-good", "b_every_industry") && g("PT-good", "c_coverage90"))
  expect_false(g("PT-oneworse", "b_every_industry")); expect_true(g("PT-oneworse", "a_pooled5")); expect_false(g("PT-oneworse", "qualifies"))
  expect_equal(g("PT-oneworse", "worst_ratio"), 1.03, tolerance = 1e-12); expect_identical(g("PT-oneworse", "worst_industry"), "d")
  expect_true(g("PT-edge", "b_every_industry"))                                                              # ratio 1.02 passes (tolerance +2%)
  expect_false(g("PT-lowcov", "c_coverage90")); expect_false(g("PT-lowcov", "qualifies")); expect_true(g("PT-lowcov", "a_pooled5"))
  expect_false(g("PT-chronoonly", "a_pooled5")); expect_equal(g("PT-chronoonly", "gain_loeo"), 0, tolerance = 1e-12); expect_equal(g("PT-chronoonly", "gain_chrono"), 0.2, tolerance = 1e-12)
  expect_equal(g("PT-good", "cov_loeo"), 1)
})

test_that("selection: the lowest mean MAE among the qualifiers, else a CONFLICT whose primary keeps the mandate and prefers adequate coverage", {
  base <- mk_pred("B", 10, 10)
  pred <- rbind(base, mk_pred("PT-a", 8, 8), mk_pred("PT-b", 7, 7), mk_pred("PT-c", 9, 9.5))
  s <- rf_cons_select(pred, "B", c("PT-a", "PT-b", "PT-c"))
  expect_identical(s$status, "QUALIFIES"); expect_identical(s$choice, "PT-b"); expect_setequal(s$qualifying, c("PT-a", "PT-b", "PT-c"))
  expect_match(s$reason, "qualifies against B")
  # nobody qualifies: every candidate is 12% worse pooled in LOEO; the one with coverage keeps the primary even though another has a lower MAE
  pred2 <- rbind(base, mk_pred("PT-lowmae-nocov", 11, 6, half = 0.01), mk_pred("PT-cov", 11.2, 7, half = 1))
  s2 <- rf_cons_select(pred2, "B", c("PT-lowmae-nocov", "PT-cov"))
  expect_identical(s2$status, "CONFLICT"); expect_length(s2$qualifying, 0L); expect_identical(s2$choice, "PT-cov")
  expect_match(s2$reason, "mandate stays in"); expect_match(s2$reason, "published form is carried beside it")
  s3 <- rf_cons_select(rbind(base, mk_pred("PT-nocov1", 11, 6, half = 0.01), mk_pred("PT-nocov2", 12, 7, half = 0.01)), "B", c("PT-nocov1", "PT-nocov2"))
  expect_identical(s3$choice, "PT-nocov1"); expect_match(s3$reason, "no candidate reaches 90% coverage")
})

test_that("candidates are compared on the rows they share with the baseline, same folds", {
  base <- mk_pred("B", 10, 10)
  short <- mk_pred("PT-short", 8, 8)
  short <- short[!(short$design == "chrono" & short$target == 2020), ]                                      # the chronological fold of 2020 is infeasible for this candidate
  pooled <- rf_cons_pooled(rbind(base, short), "B")
  r <- pooled[pooled$candidate == "PT-short" & pooled$design == "chrono", ]
  expect_equal(r$n_common, 4L); expect_equal(r$MAE_base, 10)
  industry <- rf_cons_industry(rbind(base, short), "B")
  expect_equal(sum(industry$n[industry$candidate == "PT-short" & industry$design == "chrono"]), 4L)
})

test_that("the interval of a candidate is the published anchored-step construction: t(m - 1) x sqrt(coefficient variance + residual variance of the industry's own pairs)", {
  pairs <- data.frame(sector = c("s", "s", "s", "t", "t"), dy = c(0.5, 0.3, 0.2, 1, 2), dG = c(1, 0, 1, 1, 1), dPres = c(1, -1, 1, 0, 0), stringsAsFactors = FALSE)
  sg <- rf_ph_sg(pairs, "s", b = 0.1, phi = 0.4, dl = 0.05)
  e <- c(0.5 - 0.1 - 0.4 - 0.05, 0.3 - 0 + 0.4 - 0.05, 0.2 - 0.1 - 0.4 - 0.05)
  expect_equal(sg[["sigma2"]], sum(e^2) / 2, tolerance = 1e-12); expect_equal(sg[["crit"]], stats::qt(0.975, 2), tolerance = 1e-12)
  expect_equal(rf_ph_hf(0.04, sg), stats::qt(0.975, 2) * sqrt(0.04 + sum(e^2) / 2), tolerance = 1e-12)
  expect_true(is.na(rf_ph_hf(0.04, rf_ph_sg(pairs[1, ], "s", 0, 0, 0))))                                     # one pair: no interval
  expect_true(is.na(rf_ph_sg(pairs, "zzz", 0, 0, 0)[["sigma2"]]))
})

test_that("the PUB-R1 interval of the phase engine equals the refit's own backtest interval, coverage included", {
  sp <- test_specs()$pac
  for (ds in c("loeo", "chrono")) {
    bt <- rf_backtest(sp, sp$d, ds)
    a <- bt[bt$rule == "R1", ]
    m <- rf_ph_backtest(sp$d, "log_pac", ds)
    m <- m[m$candidate == "PUB-R1", ]
    k <- intersect(paste(a$target, a$sector), paste(m$target, m$sector))
    expect_gt(length(k), 0L)
    ha <- ((a$high_live - a$low_live) / 2)[match(k, paste(a$target, a$sector))]
    hm <- m$half[match(k, paste(m$target, m$sector))]
    expect_identical(is.na(ha), is.na(hm))
    expect_lte(max(abs(ha - hm), na.rm = TRUE), 1e-12)
    expect_true(all(c("pairs", "half") %in% names(m)))
  }
})

test_that("the growth-projection error enters the chain variance as slope^2 x (z_first^2 + z_last^2); a typed-in growth keeps today's band", {
  d <- synth_panel(phi = 0.5, beta = -0.2, drift = 0.1)
  pr <- rf_ph_params(d, "y", "PT-G-full-pool")
  sv <- rf_ph_step_variance(d, "y", pr)
  G <- c(`2024` = 0.2, `2026` = 0.5, `2028` = -0.3)
  plain <- rf_ph_chain(pr, sv, "alpha", c(2024, 2026), G)
  with <- rf_ph_chain(pr, sv, "alpha", c(2024, 2026), G, gz = c(`2026` = 0.23, `2028` = 1.01))
  s <- sv[sv$sector == "alpha", ]
  v0 <- (plain[["pred_half"]] / s$crit)^2
  expect_equal((with[["pred_half"]] / s$crit)^2 - v0, pr$slope[["alpha"]]^2 * 0.23^2, tolerance = 1e-10)
  two <- rf_ph_chain(pr, sv, "alpha", c(2024, 2026, 2028), G, gz = c(`2026` = 0.23, `2028` = 1.01))
  two0 <- rf_ph_chain(pr, sv, "alpha", c(2024, 2026, 2028), G)
  expect_equal((two[["pred_half"]] / s$crit)^2 - (two0[["pred_half"]] / s$crit)^2, pr$slope[["alpha"]]^2 * 1.01^2, tolerance = 1e-10)        # the 2026 error telescopes out of a chain that starts at an actual cycle
  step2 <- rf_ph_chain(pr, sv, "alpha", c(2026, 2028), G, gz = c(`2026` = 0.23, `2028` = 1.01))
  step20 <- rf_ph_chain(pr, sv, "alpha", c(2026, 2028), G)
  expect_equal((step2[["pred_half"]] / s$crit)^2 - (step20[["pred_half"]] / s$crit)^2, pr$slope[["alpha"]]^2 * (0.23^2 + 1.01^2), tolerance = 1e-10)
  expect_identical(plain[["pred_half"]], rf_ph_chain(pr, sv, "alpha", c(2024, 2026), G, gz = NULL)[["pred_half"]])
  pn <- rf_ph_params(d, "y", "PT-noG-full-pool")                                                         # slope 0: growth is inert, no projection error
  svn <- rf_ph_step_variance(d, "y", pn)
  expect_equal(rf_ph_chain(pn, svn, "alpha", c(2024, 2026), G, gz = c(`2026` = 0.23))[["pred_half"]], rf_ph_chain(pn, svn, "alpha", c(2024, 2026), G)[["pred_half"]], tolerance = 1e-12)
})

test_that("PBLT: the published levels slope with a pooled phase shift and a drift estimated net of it", {
  d <- synth_panel(phi = 0.5, beta = -0.2, drift = 0.1)
  pr <- rf_ph_fold(d, "y", "loeo", 2024L, sort(unique(d$cycle_end)))$pred
  q <- pr[pr$sector == "alpha" & grepl("^PBLT-R0-", pr$candidate), ]
  expect_equal(nrow(q), 6L)                                                                                 # three drift kinds x pooled / own
  expect_true(all(is.finite(q$pred)))
  expect_lte(max(abs(q$pred[q$candidate == "PBLT-R0-full-pool"] - q$actual[1])), 0.05)                      # a planted process: the full-window drift candidate recovers the step
})

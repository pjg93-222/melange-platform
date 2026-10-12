# Phase-aware candidates (R/rf_phase.R): the owner ruling of 2026-10-01 (midterm / presidential disparity, trajectory, inflation) and the 2022 shock.
# Algebra and selection logic are tested on synthetic data with planted answers; the engine against the refit's own backtests; the Senate v2 layer against the frozen v1 fit.

# a synthetic industry x cycle panel in the refit's layout (sector factor, cycle_end, growth, presidential flag) with a planted level process:
#   y_t = y_(t-2) + drift + beta * dG + phi * dPres  (+ a level shock at 2022, + a tiny noise so no fit is perfect)
synth_panel <- function(phi = 0.5, beta = -0.2, drift = 0.1, shock = 0, noise = 1e-7) {
  cyc <- seq(2012L, 2024L, by = 2L)
  secs <- c("alpha", "beta", "gamma")
  set.seed(11)
  d <- do.call(rbind, lapply(seq_along(secs), function(i) {
    g <- switch(i, c(0.3, -0.5, 1.1, 0.2, -0.9, 0.7, -0.1), c(-0.1, 0.7, -0.9, 0.2, 1.1, -0.5, 0.3), c(-0.4, 0.9, 0.1, -1.2, 0.6, 0.3, 0.5))      # different shapes: growth is not collinear with the cycle effects
    data.frame(sector = secs[i], cycle_end = cyc, G_current = g, presidential_cycle = as.integer(cyc %% 4 == 0), cycle_revenue_growth_pct = 5 * g + 3 + i,
               prior_cycle_revenue_growth_pct = 5 * c(0, g[-length(g)]) + 3 + i, stringsAsFactors = FALSE)
  }))
  d$y <- NA_real_
  for (s in secs) {
    ix <- which(d$sector == s)
    y <- 10 + match(s, secs)
    for (k in seq_along(ix)) {
      if (k > 1L) y <- y + drift + beta * (d$G_current[ix[k]] - d$G_current[ix[k - 1L]]) + phi * (d$presidential_cycle[ix[k]] - d$presidential_cycle[ix[k - 1L]])
      d$y[ix[k]] <- y + shock * (d$cycle_end[ix[k]] == 2022L) + noise * stats::rnorm(1)
    }
  }
  d$sector <- factor(d$sector)
  d
}

test_that("first differences, the phase fit and the planted phase shift are recovered", {
  d <- synth_panel(phi = 0.5, beta = -0.2, drift = 0)
  fd <- rf_ph_fd_panel(d, "y")
  expect_equal(nrow(fd), 18L)
  expect_true(all(abs(fd$dPres) == 1))
  fp <- rf_ph_fit(fd, "pooled")
  expect_true(fp$ok)
  expect_equal(unname(fp$phi$R0[1]), 0.5, tolerance = 1e-4)
  expect_equal(unname(fp$fa$b0), -0.2, tolerance = 1e-4)
  fs <- rf_ph_fit(fd, "industry")
  expect_true(fs$ok)
  expect_equal(unname(fs$phi$R0), rep(0.5, 3), tolerance = 1e-4)
})

test_that("PC at lambda 0 is the two-step chain PB2, PC is linear in lambda, and every candidate family is produced", {
  d <- synth_panel()
  pr <- rf_ph_fold(d, "y", "loeo", 2024L, sort(unique(d$cycle_end)))$pred
  a <- pr[pr$sector == "alpha", ]
  get <- function(cand) a$pred[a$candidate == cand]
  expect_equal(get("PC-R0-L0"), get("PB2-R0"), tolerance = 1e-12)
  expect_equal(get("PC-R0-L0.9") - get("PC-R0-L0"), 9 * (get("PC-R0-L0.1") - get("PC-R0-L0")), tolerance = 1e-10)
  expect_equal(get("PCT-L0") , get("PB2T"), tolerance = 1e-12)
  expect_true(all(c("PA-R0", "PUB-R1", "PAfb-R0", "PBL-R0", "PT-G-full-pool", "PT-noG-damp-pool", "PT-G-damp-pool-R1", "PB2T", "PCT-L0.5", "PA-R0-T", "naive_prev", "anchor_only") %in% a$candidate))
})

test_that("with a deterministic process the phase-aware candidates are exact where the algebra says so (beta = 0, no drift)", {
  d <- synth_panel(phi = 0.5, beta = 0, drift = 0)
  pr <- rf_ph_fold(d, "y", "loeo", 2024L, sort(unique(d$cycle_end)))$pred
  a <- pr[pr$candidate == "PB-R0", ]
  expect_lte(max(abs(a$pred - a$actual)), 1e-4)                                   # one PB step from the actual previous cycle
  b <- pr[pr$candidate == "PB2-R0", ]
  expect_lte(max(abs(b$pred - b$actual)), 1e-4)                                   # two chained steps: the +phi and -phi cancel
  n <- pr[pr$candidate == "naive_prev", ]
  expect_gt(max(abs(n$pred - n$actual)), 0.4)                                     # the unadjusted previous cycle misses by the phase shift
})

test_that("the phase engine reproduces the refit's own PAC backtests (published prevchg path and same-phase calc path)", {
  sp <- test_specs()$pac
  for (ds in c("loeo", "chrono")) {
    bt <- rf_backtest(sp, sp$d, ds)
    mine <- rf_ph_backtest(sp$d, "log_pac", ds)
    for (rl in c("R0", "R1", "R2")) {
      a <- bt[bt$rule == rl, ]
      for (pair in list(c("PUB", "prevchg"), c("PAfb", "calc"))) {
        m <- mine[mine$candidate == paste0(pair[1], "-", rl), ]
        k <- intersect(paste(a$target, a$sector), paste(m$target, m$sector))
        expect_gt(length(k), 0L)
        ea <- (a[[pair[2]]] - a$actual)[match(k, paste(a$target, a$sector))]
        em <- (m$pred - m$actual)[match(k, paste(m$target, m$sector))]
        expect_lte(max(abs(ea - em)), 1e-12, label = paste(ds, rl, pair[1]))
      }
    }
  }
})

test_that("D-065: the default stays unless a candidate beats it by 5% in BOTH designs; qualifiers are ranked by mean MAE", {
  mk <- function(cand, mae_loeo, mae_chrono) rbind(
    data.frame(series = "y", design = "loeo", target = c(2016, 2018), sector = "s", candidate = cand, actual = 0, pred = mae_loeo / 100, err = mae_loeo, stringsAsFactors = FALSE),
    data.frame(series = "y", design = "chrono", target = c(2020, 2022), sector = "s", candidate = cand, actual = 0, pred = mae_chrono / 100, err = mae_chrono, stringsAsFactors = FALSE))
  pred <- rbind(mk("D", 10, 10), mk("A", 9, 9), mk("B", 8, 9.6), mk("C", 8.9, 8.9))
  s <- rf_ph_select(pred, c("D", "A", "B", "C"), "D")
  expect_setequal(s$qualifying, c("A", "C"))                                       # B wins 20% in LOEO but only 4% chronologically
  expect_identical(s$choice, "C")                                                  # C and A both qualify, C has the lower mean MAE
  expect_identical(rf_ph_select(pred, c("D", "B"), "D")$choice, "D")
  expect_match(rf_ph_select(pred, c("D", "B"), "D")$reason, "kept")
  p2 <- rbind(pred, data.frame(series = "y", design = "loeo", target = 2024, sector = "s", candidate = "A", actual = 0, pred = 0.5, err = 50, stringsAsFactors = FALSE))
  expect_equal(rf_ph_pair(p2, "A", "D", "loeo")$n, 2L)                              # rows that only one of the two has are ignored
  m <- rf_ph_metrics(pred, "D")
  expect_equal(m$gain_vs_default[m$candidate == "A" & m$design == "loeo"], 0.1, tolerance = 1e-12)
})

test_that("the next-cycle selection: PC is the default and loses only at the lambdas where a challenger wins by 5% in both designs", {
  mk <- function(cand, mae_loeo, mae_chrono) rbind(
    data.frame(series = "y", design = "loeo", target = c(2016, 2018), sector = "s", candidate = cand, actual = 0, pred = 0, err = mae_loeo, stringsAsFactors = FALSE),
    data.frame(series = "y", design = "chrono", target = c(2020, 2022), sector = "s", candidate = cand, actual = 0, pred = 0, err = mae_chrono, stringsAsFactors = FALSE))
  pred <- rbind(mk("PB-R0", 5, 5), mk("PA-R0", 20, 20), mk("PA-R1", 20, 20), mk("PA-R2", 20, 20), mk("PB2-R0", 6, 6), mk("PB2-R1", 6, 6), mk("PB2-R2", 6, 6),
                do.call(rbind, lapply(RF_PH_LAMBDAS, function(l) mk(sprintf("PC-R0-L%s", format(l)), 4 + 10 * (1 - l), 4 + 10 * (1 - l)))))
  sn <- rf_ph_select_next(pred)
  expect_true(sn$table$kept[sn$table$lambda == 1])
  expect_false(sn$table$kept[sn$table$lambda == 0])
  expect_equal(sn$break_even, 0.8)                                                  # PC error 6.0 at lambda 0.8 ties PB2 (6): kept; at 0.7 (7.0) PB2 wins by 14%
})

test_that("drift estimators: full mean, last three earlier pairs, damped trend, and their fallbacks", {
  rp <- data.frame(sector = "s", cycle_end = seq(2014L, 2024L, by = 2L), r = 0.5 - 0.1 * (0:5), stringsAsFactors = FALSE)
  expect_equal(rf_ph_drift_est(rp, "s", 2026L, "full", "ind"), mean(rp$r))
  expect_equal(rf_ph_drift_est(rp, "s", 2026L, "rec3", "ind"), mean(rp$r[4:6]))
  expect_equal(rf_ph_drift_est(rp, "s", 2026L, "damp", "ind"), 0.5 - 0.1 * (5 + RF_PH_DAMP), tolerance = 1e-12)
  expect_equal(rf_ph_drift_est(rp, "s", 2026L, "damp", "ind", h = 2L), 0.5 - 0.1 * (5 + RF_PH_DAMP + RF_PH_DAMP^2), tolerance = 1e-12)
  expect_equal(rf_ph_drift_est(rp, "s", 2014L, "rec3", "ind"), mean(rp$r))                                              # nothing earlier: the full mean
  expect_equal(rf_ph_drift_est(rp, "s", 2018L, "damp", "ind"), mean(rp$r[1:2]))                                         # fewer than 3 earlier pairs: their mean
  pool <- rbind(rp, transform(rp, sector = "t", r = r + 1))
  expect_equal(rf_ph_drift_est(pool, "s", 2026L, "full", "pool"), mean(pool$r))
  expect_equal(rf_ph_drift_est(pool, "s", 2026L, "full", "ind"), mean(rp$r))
})

test_that("the 2022 shock: nothing is estimated without a pair that touches it, and a planted level shock is recovered and de-adjusted", {
  d <- synth_panel(phi = 0.3, beta = 0, drift = 0, shock = -0.3)
  cyc <- sort(unique(d$cycle_end))
  d1 <- rf_restandardize(d, cyc)
  expect_equal(rf_ph_shock_gamma(d1, "y"), -0.3, tolerance = 1e-4)
  expect_identical(rf_ph_shock_gamma(d1[d1$cycle_end != 2022L, ], "y"), 0)           # the shock cycle is not in the training panel: gamma = 0, nothing leaks
  pr <- rf_ph_fold(d, "y", "loeo", 2024L, cyc, shock = "indicator")$pred
  b <- pr[pr$candidate == "PB-R0", ]
  expect_lte(max(abs(b$pred - b$actual)), 1e-3)                                      # with the shock handled the recovery step is predicted
  pr0 <- rf_ph_fold(d, "y", "loeo", 2024L, cyc, shock = "none")$pred
  n0 <- pr0[pr0$candidate == "naive_prev", ]
  expect_gt(max(abs(n0$pred - n0$actual)), 0.25)                                     # unhandled, the depressed anchor misses by about the shock
  pt <- rf_ph_backtest(d, "y", "loeo", shock = "drop")
  expect_true(all(grepl("@x22$", pt$candidate)))
  expect_false(any(grepl("@", rf_ph_backtest(d, "y", "loeo")$candidate, fixed = TRUE)))
})

test_that("trajectory parameters: noG has no growth slope, the drift is the residual mean, the growth variants estimate a growth slope", {
  d <- synth_panel(phi = 0.5, beta = -0.2, drift = 0.1)
  p <- rf_ph_params(d, "y", "PT-noG-full-pool")
  expect_identical(unname(p$slope), rep(0, 3))
  expect_equal(unname(p$drift_by_industry[1]), mean(p$resid$r), tolerance = 1e-12)
  expect_equal(unname(p$phi[1]), 0.5, tolerance = 0.2)
  g <- rf_ph_params(d, "y", "PT-G-full-pool")
  expect_lt(g$slope[[1]], 0)
  expect_equal(g$drift_kind, "full")
  fd <- rf_ph_fd_panel(d, "y")
  expect_equal(unname(stats::coef(stats::lm(dy ~ dG + dPres, fd))[["(Intercept)"]]), 0.1, tolerance = 1e-4)      # the planted drift is the intercept of the first differences
})

test_that("projection chain: two chained steps cancel the phase terms, a same-phase step carries one gap-4 change", {
  d <- synth_panel(phi = 0.5, beta = -0.2, drift = 0)
  pr <- rf_ph_params(d, "y", "PB-R0")
  sv <- rf_ph_step_variance(d, "y", pr)
  expect_true(all(is.finite(sv$sigma2)))
  G <- c(`2024` = 0.2, `2026` = 0.5, `2028` = -0.3)
  one <- rf_ph_chain(pr, sv, "alpha", c(2024, 2026), G)
  two <- rf_ph_chain(pr, sv, "alpha", c(2024, 2026, 2028), G)
  expect_equal(unname(one[["log_change"]]), pr$slope[["alpha"]] * 0.3 + pr$phi[["alpha"]] * (0 - 1), tolerance = 1e-12)
  expect_equal(unname(two[["log_change"]]), pr$slope[["alpha"]] * (-0.5), tolerance = 1e-12)       # phase: -phi then +phi
  expect_equal(unname(two[["k"]]), 2)
  pa <- rf_ph_params(d, "y", "PA-R0")
  expect_identical(pa$gap, 4L)
  c4 <- rf_ph_chain(pa, rf_ph_step_variance(d, "y", pa), "alpha", c(2024, 2028), G)
  expect_equal(unname(c4[["log_change"]]), pa$slope[["alpha"]] * (-0.5), tolerance = 1e-12)
  pt <- rf_ph_params(d, "y", "PT-noG-full-pool")
  s2 <- rf_ph_step_variance(d, "y", pt)
  t2 <- rf_ph_chain(pt, s2, "alpha", c(2024, 2026, 2028), G)
  expect_equal(unname(t2[["log_change"]]), 2 * pt$drift("alpha", 1L), tolerance = 1e-12)         # no growth term, phase cancels: only the two drifts remain
})

test_that("inflation: the cycle factor is the mean of its two calendar-year price levels; Paul's rate and source are the defaults", {
  expect_equal(RF_INFLATION$rate, 0.025726436)
  expect_match(RF_INFLATION$source, "Federal Reserve 10-year expected inflation, 2.5726436%/yr, supplied by Paul 2026-10-01", fixed = TRUE)
  r <- RF_INFLATION$rate
  expect_equal(rf_ph_cycle_factor(2026), 1 + r / 2, tolerance = 1e-12)
  expect_equal(rf_ph_cycle_factor(2028), mean((1 + r)^c(2, 3)), tolerance = 1e-12)
  expect_equal(rf_ph_cycle_factor(c(2026, 2028), 0), c(1, 1))
  expect_equal(rf_ph_to_nominal(100, 2028), 100 * mean((1 + r)^c(2, 3)))
  expect_equal(rf_ph_to_nominal(100, 2028, path = c(`2026` = r, `2027` = r, `2028` = r)), rf_ph_to_nominal(100, 2028), tolerance = 1e-12)       # a constant path equals the rate
  expect_equal(rf_ph_to_nominal(100, 2028, path = c(`2026` = 0.01, `2027` = 0.02, `2028` = 0.03)), 100 * mean(c(1.01 * 1.02, 1.01 * 1.02 * 1.03)), tolerance = 1e-12)
  cpi <- data.frame(year = 2015:2025, cpi = 100 * 1.02^(0:10))
  expect_equal(rf_ph_trailing_inflation(cpi), 0.02, tolerance = 1e-12)
})

test_that("the Senate v2 layer carries Pres, the frozen v1 layer is untouched, and the House spec is unchanged", {
  ctx <- test_ctx()
  expect_false("Pres" %in% names(stats::coef(ctx$fits$control_Senate)))
  c2 <- rf_phase_ctx(ctx)
  expect_true("Pres" %in% names(stats::coef(c2$fits$control_Senate)))
  expect_true(c2$senate_phase)
  expect_identical(names(stats::coef(c2$fits$control_Senate_v1)), names(stats::coef(ctx$fits$control_Senate)))
  expect_identical(rf_phase_ctx(c2)$fits$control_Senate, c2$fits$control_Senate)               # idempotent
  frozen <- rf_backtest_specs(ctx)
  v2 <- rf_backtest_specs(ctx, phase = TRUE)
  expect_identical(frozen$control_Senate.selected$others, "sector + RMajNow + G_prior")
  expect_identical(v2$control_Senate.selected$others, "sector + RMajNow + G_prior + Pres")
  expect_identical(frozen$control_House.selected$others, v2$control_House.selected$others)
  expect_identical(frozen$pac$others, v2$pac$others)
  s <- summary(c2$fits$control_Senate)$coefficients
  expect_true(all(is.finite(s["Pres", ])))
  expect_lt(s["P_R", 1], 0)
})

test_that("the v2 context carries the phase block and the evidence record serialises", {
  v <- test_v2()
  expect_true(!is.null(v$phase$pac) && !is.null(v$phase$general))
  for (nm in c("pac", "general")) {
    b <- v$phase[[nm]]
    expect_true(b$selection$choice %in% RF_PH_CANDIDATES)
    expect_true(b$trajectory$single$choice %in% RF_PH_TRAJ_CANDIDATES)
    expect_true(b$spec$handling %in% c("none", "x22", "i22"))
    expect_true(all(is.finite(b$params$phi)))
    expect_true(is.data.frame(b$next_cycle$table) && is.data.frame(b$next_cycle_trajectory$table))
  }
  ev <- rf_v2_phase_evidence(v)
  js <- jsonlite::toJSON(ev, auto_unbox = TRUE, null = "null", na = "null", digits = NA)
  expect_gt(nchar(js), 1000L)
  back <- jsonlite::fromJSON(js, simplifyVector = FALSE)
  expect_identical(back$pac$selected, v$phase$pac$consistency$selection$choice)
  expect_identical(back$pac$consistency$status, v$phase$pac$consistency$selection$status)
  expect_true(back$pac$consistency$status %in% c("QUALIFIES", "CONFLICT"))
  expect_identical(back$general_giving$consistency$baseline, v$phase$general$consistency$baseline)
  expect_true(length(back$pac$consistency$top_candidates) > 5L && length(back$pac$consistency$by_industry) > 8L)
  expect_identical(back$pac$selected_parameters$candidate, v$phase$pac$spec$candidate)
  expect_true(grepl("^PT-", back$pac$growth_retaining_parameters$candidate) && grepl("^PT-G-", v$phase$pac$spec_growth$label))
  expect_equal(back$inflation$annual_rate, 0.025726436)
  expect_match(back$status, "do not apply it yet")
})

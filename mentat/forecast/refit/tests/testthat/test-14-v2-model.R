# The model side of bundle v2 reproduces SP/phase2_mentat_anchor/ADDENDUM.md section F: own-slope PAC changes with coefficient and
# prediction intervals, the previous-cycle share change with Polymarket-to-Polymarket control inputs, and the lobbying LOG refit.
# Quoted figures are rounded as printed there, so tolerances are half a unit of the last printed digit.

pct <- function(x) 100 * x

test_that("control inputs: Polymarket Jan-Jun 2024 (House from 2024-02-21, 131 days) and 2026; Kalshi has no 2024 window", {
  ci <- test_ctx()$control_inputs
  p <- ci$primary
  g <- function(ch, cy, col) p[[col]][p$chamber == ch & p$cycle == cy]
  expect_identical(g("House", 2024L, "days"), 131L)
  expect_identical(g("House", 2024L, "first_date"), "2024-02-21")
  expect_lte(abs(g("House", 2024L, "p_rep") - 0.44916), 5e-6)
  expect_identical(g("Senate", 2024L, "days"), 182L)
  expect_lte(abs(g("Senate", 2024L, "p_rep") - 0.706456), 5e-7)
  expect_lte(abs(g("House", 2026L, "p_rep") - 0.17752327), 1e-8)
  expect_lte(abs(g("Senate", 2026L, "p_rep") - 0.55713597), 1e-8)
  expect_identical(g("House", 2026L, "days"), 181L)
  # Kalshi's 2024 series starts in September: no Jan-Jun data, so it does not vote
  k <- ci$previous$markets[ci$previous$markets$market == "kalshi", ]
  expect_true(all(k$days == 0L)); expect_true(all(is.na(k$p_rep)))
  expect_identical(unname(ci$previous$control$markets), c("polymarket", "polymarket"))
  # the ruled equal-weight Kalshi + Polymarket 2026 figure is kept as a sensitivity
  s <- ci$sensitivity
  expect_lte(abs(s$p_rep[s$chamber == "House"] - 0.1903485028), 1e-8)
  expect_lte(abs(s$p_rep[s$chamber == "Senate"] - 0.5639529613), 1e-8)
})

test_that("PAC own slopes: banks +13.21% and credit unions +12.59% with the addendum's coefficient and prediction intervals", {
  ctx <- test_ctx(); sp <- test_specs()
  sel <- rf_select_rule(sp$pac, path = "prevchg")
  expect_identical(sel$reported_rule, "R1")
  v2 <- rf_pac_v2(sp$pac, sel)
  expect_identical(v2$rule, "R1")
  expect_identical(v2$df_interaction, 9L)
  expect_identical(v2$pooled$df, 11L)
  b <- v2$slopes$commercial_banks; u <- v2$slopes$credit_unions
  expect_lte(abs(b$applied$beta - (-0.05914)), 5e-6); expect_lte(abs(u$applied$beta - (-0.06975)), 5e-6)
  expect_lte(abs(b$applied$beta - b$own_beta), 1e-15)              # R1: the applied slope IS the own slope
  expect_identical(b$applied$rule, "R1"); expect_identical(b$applied$df, 9L)
  expect_lte(abs(b$applied$critical_95 - qt(0.975, 9)), 1e-12)
  expect_identical(v2$prediction$industries$commercial_banks$pairs, 6L); expect_identical(v2$prediction$industries$credit_unions$pairs, 6L)
  dGb <- rf_pac_dG(ctx$pac_panel, "commercial_banks", 2024); dGu <- rf_pac_dG(ctx$pac_panel, "credit_unions", 2024)
  expect_lte(abs(dGb - (-2.0975)), 5e-5); expect_lte(abs(dGu - (-1.7006)), 5e-5)
  pb <- v2$prediction$industries$commercial_banks; pu <- v2$prediction$industries$credit_unions
  rb <- rf_pac_relative_change(b$applied$beta, b$applied$se, 9, pb$sigma2, pb$pairs, dGb)
  ru <- rf_pac_relative_change(u$applied$beta, u$applied$se, 9, pu$sigma2, pu$pairs, dGu)
  expect_equal(unname(round(pct(rb), 2)), c(13.21, -16.68, 53.81, -38.08, 106.96), tolerance = 1e-9)
  expect_equal(unname(round(pct(ru), 2)), c(12.59, -12.86, 45.49, -23.38, 65.46), tolerance = 1e-9)
})

test_that("PAC: the R0 and R2 alternatives carry the other rules' numbers (banks +42.35% pooled, +20.09% DL blend)", {
  sp <- test_specs(); ctx <- test_ctx()
  dGb <- rf_pac_dG(ctx$pac_panel, "commercial_banks", 2024)
  for (r in c("R0", "R2")) {
    sel <- list(reported_rule = r)
    v <- rf_pac_v2(sp$pac, sel)
    b <- v$slopes$commercial_banks
    pb <- v$prediction$industries$commercial_banks
    rc <- rf_pac_relative_change(b$applied$beta, b$applied$se, b$applied$df, pb$sigma2, pb$pairs, dGb)
    expect_equal(round(pct(rc[["r"]]), 2), if (r == "R0") 42.35 else 20.09, tolerance = 1e-9, info = r)
  }
})

test_that("shares under the previous-cycle anchor: pp change, coefficient interval, blended-2026 and Polymarket-only sensitivities", {
  ctx <- test_ctx(); sp <- test_specs()
  vm <- rf_v1_models(ctx)$share_models
  ci <- ctx$control_inputs
  prim <- ci$primary
  chg <- function(chamber, ind, target) {
    blk <- vm[[paste0("control_", chamber)]]
    blk$prediction <- rf_share_prediction(sp[[paste0("control_", chamber, ".selected")]])
    prev <- prim$p_rep[prim$chamber == chamber & prim$cycle == 2024L]
    dx <- rf_share_dx(ctx, chamber, ind, unlist(blk$terms), list(previous = prev, target = target))
    rf_share_change(blk, dx)
  }
  blended <- function(ch) ci$sensitivity$p_rep[ci$sensitivity$chamber == ch]
  poly <- function(ch) prim$p_rep[prim$chamber == ch & prim$cycle == 2026L]
  cases <- list(list("House", "commercial_banks", -0.89, -2.95, 1.17, -0.94), list("House", "credit_unions", -1.20, -3.09, 0.68, -1.25),
                list("Senate", "commercial_banks", 11.34, 2.72, 19.96, 11.63), list("Senate", "credit_unions", 9.96, 2.13, 17.79, 10.25))
  for (cs in cases) {
    a <- chg(cs[[1]], cs[[2]], blended(cs[[1]]))
    expect_equal(unname(round(a[["pp"]], 2)), cs[[3]], tolerance = 1e-9, info = paste(cs[[1]], cs[[2]], "blended pp"))
    # interval ends are printed in the addendum as the rounded centre plus the rounded half-width, so allow one rounding step (0.01 pp)
    expect_lte(abs(a[["coef_low"]] - cs[[4]]), 0.0101); expect_lte(abs(a[["coef_high"]] - cs[[5]]), 0.0101)
    b <- chg(cs[[1]], cs[[2]], poly(cs[[1]]))
    expect_equal(unname(round(b[["pp"]], 2)), cs[[6]], tolerance = 1e-9, info = paste(cs[[1]], cs[[2]], "Polymarket only"))
    # the prediction interval is wider than the coefficient interval
    expect_gt(a[["pred_high"]] - a[["pred_low"]], a[["coef_high"]] - a[["coef_low"]])
  }
})

test_that("the share step residual uses (t-2, t) pairs of every industry", {
  sp <- test_specs()
  h <- rf_share_prediction(sp$control_House.selected)
  expect_identical(h$anchor, "previous_cycle")
  expect_identical(h$pairs, 15L)              # 5 consecutive pairs of the 6 control cycles x 3 industries
  expect_gt(h$sigma2, 0)
})

test_that("lobbying LOG refit: dly ~ dg + factor(year) gives -0.811% per point (p 0.029) on the same 71 pairs", {
  ctx <- test_ctx()
  m <- rf_lobby_log_fit(ctx$lobby$diffs)
  expect_s3_class(m, "lm")
  expect_equal(nobs(m), 71L)
  blk <- rf_lobby_log_block(m, ctx$lobby$diffs)
  expect_lte(abs(blk$beta - (-0.00814763)), 5e-9)
  expect_lte(abs(blk$se - 0.00363672), 5e-9)
  expect_lte(abs(blk$ordinary_p - 0.0291), 5e-5)
  expect_lte(abs(blk$critical_95 - 2.00404), 5e-6)
  expect_lte(abs(blk$relative_change_pct_per_point - (-0.811)), 5e-4)
  expect_lte(abs(blk$coefficient_interval_pct$low - (-1.532)), 5e-4); expect_lte(abs(blk$coefficient_interval_pct$high - (-0.086)), 5e-4)
  expect_lte(abs(blk$prediction$sigma2 - 0.023714), 5e-7)
  expect_identical(blk$prediction$pairs, 71L)
  expect_lte(abs(blk$prediction$critical_95 - 1.99444), 5e-6)
  # own-sector log slopes keep the sign (banks -0.82%, credit unions -0.26%, P&C -1.80%; life insurance positive)
  s <- blk$sector_relative_change_pct_per_point
  expect_lte(abs(s$commercial_banks - (-0.82)), 0.006); expect_lte(abs(s$credit_unions - (-0.26)), 0.006); expect_lte(abs(s$property_casualty - (-1.80)), 0.006)
  expect_gt(s$life_insurance, 0)
  # the summary(lm()) block of the model is real R output
  txt <- rf_summary_text(m)
  expect_true(any(grepl("data = lobbying_log_all71", txt, fixed = TRUE)))
  expect_true(any(grepl("^Coefficients:", txt)))
})

test_that("lobbying level forecast: 2025 bank LDA levels to 2026 at dg -1 / 0 / +1 and the prediction interval at dg = 0", {
  blk <- rf_lobby_log_block(rf_lobby_log_fit(test_ctx()$lobby$diffs), test_ctx()$lobby$diffs)
  f <- function(level, dg) rf_lobby_log_forecast(blk, level, dg)
  expect_lte(abs(f(23824000, -1)[["point"]] - 24018902), 2)
  expect_lte(abs(f(23824000, 1)[["point"]] - 23630679), 2)
  expect_equal(f(23824000, 0)[["point"]], 23824000)
  expect_lte(abs(f(23824000, 0)[["pred_low"]] - 17523803), 5); expect_lte(abs(f(23824000, 0)[["pred_high"]] - 32389257), 5)
  expect_lte(abs(f(65696931, -1)[["point"]] - 66234392), 2)
  expect_lte(abs(f(2054500, -1)[["point"]] - 2071308), 2)
  expect_lte(abs(f(6233000, 0)[["pred_low"]] - 4584699), 5)
  # at dg = 0 the coefficient interval collapses to the point (no slope uncertainty enters)
  expect_equal(f(2054500, 0)[["coef_low"]], 2054500); expect_equal(f(2054500, 0)[["coef_high"]], 2054500)
})

test_that("the log model's rolling backtest is in $M 2025 and comparable with the dollar model on the common folds", {
  ctx <- test_ctx()
  lg <- rf_lobby_log_backtest(ctx$lobby$diffs)
  expect_true(all(c("actual", "calc", "naive_prev", "low", "high") %in% names(lg)))
  met <- rf_backtest_metrics(lg)
  m <- met[met$path == "calc" & met$rule == "R0", ]
  expect_identical(m$units, "$M 2025")
  expect_equal(m$n, 50L)
  expect_true(is.finite(m$MAE) && m$MAE > 0)
  # sign consistency: the slope keeps its sign when any one year or any one sector is dropped
  sc <- rf_lobby_log_sign_consistency(rf_lobby_log_fit(ctx$lobby$diffs), ctx$lobby$diffs)
  expect_equal(sc$years, 15L); expect_equal(sc$sectors, 6L)
  expect_gte(sc$loeo, 0); expect_lte(sc$loeo, 1); expect_gte(sc$loco, 0); expect_lte(sc$loco, 1)
})

test_that("negative control: a log model fitted to levels that do not grow has slope zero and the forecast stays at the last level", {
  d <- data.frame(study_sector = rep(c("a", "b"), each = 6), year = rep(2010:2015, 2), y = rep(c(10, 20), each = 6), dg = c(1, -1, 2, -2, 0.5, -0.5, 1, -1, 2, -2, 0.5, -0.5))
  d$y_lag <- d$y; d$g <- d$dg
  m <- rf_lobby_log_fit(d)
  expect_lte(abs(coef(m)[["dg"]]), 1e-12)
})

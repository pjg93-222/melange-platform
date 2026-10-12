# No-leakage backtests (port of SP/tailoring/tailoring.R:85-200, which reproduces SP/regressions/mae_decomposition.R).
# Designs: loeo (train on every other cycle), chrono (train on cycles < t), rolling (lobbying: train on years < T, targets 2014-2023).
# In each fold: re-standardize growth on the TRAINING cycles only (analyze.py prepare()), refit the pooled and interaction
# models, tau2 and W, the offset fit, and the anchored-step residual variance. Slope rules: R0 pooled, R1 own, R2 DL blend;
# baselines naive_prev (last cycle / year actual) and anchor_only.
# Paths: prevchg (previous-cycle anchor + slope * dG; the ruled PAC calculator path), calc (same-phase reference + model change for
# shares; last year + slope * dg for lobbying), level (the model's fitted level).
#
# Every unit of work (spec x design x fold) is deterministic and independent, so folds can run on parallel workers (rf_pmap) with
# results bit-identical to the serial run: there is no random number anywhere in this file.

RF_FULL_RHS <- "sector + RMajNow + G_prior + G_current + Pres"   # the bundle *_industry base (mask 15, "Original full model")

# The backtest specs of tailoring.R, built from the rebuilt panels. Keys: "pac", "lobby" and "<id>.<base>" for the share models.
# phase = TRUE: the v2 specs (owner ruling 2026-10-01: the Senate share model carries the presidential-cycle term like the House model). The v1 parity layer
# and the G6 baseline of the published v1 calculator use phase = FALSE (the frozen selections).
rf_backtest_specs <- function(ctx, phase = FALSE) {
  lob <- ctx$lobby$diffs
  lob$sector <- factor(lob$study_sector)
  lob$cycle_end <- lob$year
  pac <- ctx$pac_panel
  pac$sector <- factor(pac$sector)
  sh <- function(id) { d <- ctx$share[[id]]; d$sector <- factor(d$sector); d }
  mk <- function(id, base, kind, d, y, f, others, restd, scale, unit, calc_path) list(id = id, base = base, kind = kind, d = d, y = y, f = f, others = others,
                                                                                  restd = restd, scale = scale, unit = unit, calc_path = calc_path)
  specs <- list(
    control_House.full = mk("control_House", "full", "share", sh("control_House"), "RShare", "P_R", RF_FULL_RHS, TRUE, 100, "share pp", "prevchg"),
    control_House.selected = mk("control_House", "selected", "share", sh("control_House"), "RShare", "P_R", "sector + RMajNow + G_prior + Pres", TRUE, 100, "share pp", "prevchg"),
    control_Senate.full = mk("control_Senate", "full", "share", sh("control_Senate"), "RShare", "P_R", RF_FULL_RHS, TRUE, 100, "share pp", "prevchg"),
    control_Senate.selected = mk("control_Senate", "selected", "share", sh("control_Senate"), "RShare", "P_R", if (phase) "sector + RMajNow + G_prior + Pres" else "sector + RMajNow + G_prior", TRUE, 100, "share pp", "prevchg"),
    seats_House.full = mk("seats_House", "full", "share", sh("seats_House"), "RShare", "pred_rep_net_change", RF_FULL_RHS, TRUE, 100, "share pp", "prevchg"),
    seats_Senate.full = mk("seats_Senate", "full", "share", sh("seats_Senate"), "RShare", "pred_rep_net_change", RF_FULL_RHS, TRUE, 100, "share pp", "prevchg"),
    pac = mk("pac", "bundle", "pac", pac, "log_pac", "G_current", "sector + factor(cycle_end)", TRUE, 100, "log points x100", "prevchg"),
    lobby = mk("lobby", "bundle", "lobby", lob, "dy", "dg", "factor(year)", FALSE, 1, "$M 2025", "calc"))
  specs
}

# tailoring.R restandardize(): growth scaling from the training cycles only (NaN when a sector has < 2 training cycles, so the fold
# is then found infeasible rather than guessed).
rf_restandardize <- function(d, train_cycles) {
  for (s in levels(d$sector)) {
    ix <- d$sector == s
    tx <- ix & d$cycle_end %in% train_cycles
    mu <- mean(d$cycle_revenue_growth_pct[tx])
    sdv <- stats::sd(d$cycle_revenue_growth_pct[tx])
    d$G_current[ix] <- (d$cycle_revenue_growth_pct[ix] - mu) / sdv
    d$G_prior[ix] <- (d$prior_cycle_revenue_growth_pct[ix] - mu) / sdv
  }
  d
}

# One prediction of the three paths (tailoring.R pred_one).
.rf_pred_one <- function(fo, spec, slope, a, r, pv, first_level) {
  g <- stats::coef(fo); g[is.na(g)] <- 0
  if (spec$kind == "share") {
    tt <- stats::delete.response(stats::terms(fo))
    Xa <- stats::model.matrix(tt, a, xlev = fo$xlevels); Xr <- stats::model.matrix(tt, r, xlev = fo$xlevels); Xp <- stats::model.matrix(tt, pv, xlev = fo$xlevels)
    return(c(calc = r[[spec$y]] + sum((Xa - Xr) * g) + slope * (a[[spec$f]] - r[[spec$f]]), level = sum(Xa * g) + slope * a[[spec$f]],
             prevchg = pv[[spec$y]] + sum((Xa - Xp) * g) + slope * (a[[spec$f]] - pv[[spec$f]])))
  }
  if (spec$kind == "pac") {
    s <- as.character(a$sector)
    sec <- if (s == first_level) 0 else g[[paste0("sector", s)]]
    cyc <- g[grep("^factor\\(cycle_end\\)", names(g))]
    return(c(calc = r[[spec$y]] + slope * (a[[spec$f]] - r[[spec$f]]), level = g[["(Intercept)"]] + sec + mean(c(0, cyc)) + slope * a[[spec$f]],
             prevchg = pv[[spec$y]] + slope * (a[[spec$f]] - pv[[spec$f]])))
  }
  yr <- g[grep("^factor\\(year\\)", names(g))]      # lobbying: console rule = last year + slope * dg (no year effect)
  c(calc = a$y_lag + slope * a$dg, level = a$y_lag + g[["(Intercept)"]] + mean(c(0, yr)) + slope * a$dg, prevchg = a$y_lag + slope * a$dg)
}

# Coefficient variance of the modelled change of the calculator path (spec 3.10), by slope rule.
.rf_vcoef <- function(spec, fa, fo, rule, s, a, r, pv) {
  var_s <- switch(rule, R0 = fa$se0^2, R1 = fa$se[[s]]^2, R2 = fa$W[[s]] * fa$se[[s]]^2 + (1 - fa$W[[s]])^2 * fa$se0^2)
  if (spec$kind == "lobby") return(a$dg^2 * var_s)
  if (spec$kind == "pac") return((a[[spec$f]] - pv[[spec$f]])^2 * var_s)
  ref <- if (spec$calc_path == "prevchg") pv else r            # the anchor of the calculator path: previous cycle, or the same-phase reference
  dF <- a[[spec$f]] - ref[[spec$f]]
  if (rule == "R0") {
    dx <- as.vector(.rf_mm(fa$f0, a) - .rf_mm(fa$f0, ref))
    return(as.numeric(t(dx) %*% fa$V0 %*% dx))
  }
  dx <- as.vector(.rf_mm(fo, a) - .rf_mm(fo, ref))
  as.numeric(t(dx) %*% stats::vcov(fo) %*% dx) + dF^2 * var_s
}

# Run one fold: -> list(pred rows, slope rows, feasibility row).
.rf_fold <- function(spec, d0, cy, cycles, design, rules) {
  trc <- if (spec$kind == "lobby" || design == "chrono") cycles[cycles < cy] else setdiff(cycles, cy)
  d <- if (spec$restd) rf_restandardize(d0, trc) else d0
  tr <- d[d$cycle_end %in% trc, ]
  fa <- tryCatch(rf_fit_rules(tr, spec), error = function(e) list(ok = FALSE, df1 = NA_integer_, reason = conditionMessage(e)))
  feas <- data.frame(term = spec$id, base = spec$base, design = design, target = cy, train = paste(range(trc), collapse = "-"), n_train = nrow(tr),
                     df_R1 = fa$df1, feasible = isTRUE(fa$ok), note = if (isTRUE(fa$ok)) "" else fa$reason, stringsAsFactors = FALSE)
  if (!isTRUE(fa$ok)) return(list(pred = NULL, slopes = NULL, feas = feas))
  slopes <- do.call(rbind, lapply(fa$secs, function(s) data.frame(term = spec$id, base = spec$base, design = design, target = cy, sector = s,
    pooled = fa$b0, pooled_se = fa$se0, own = fa$own[[s]], own_se = fa$se[[s]], own_p = fa$p[[s]], tau2_DL = fa$tau2, w_DL = fa$W[[s]],
    R2 = fa$rules$R2[[s]], df_R1 = fa$df1, stringsAsFactors = FALSE)))
  tg <- d[d$cycle_end == cy, ]
  first_level <- levels(droplevels(tr$sector))[1]
  rows <- list()
  for (rule in rules) {
    sl <- fa$rules[[rule]]
    fo <- rf_offset_fit(tr, spec, sl)
    sg <- rf_step_sigma2(spec, tr, sl, fo, trc, anchor = if (spec$calc_path == "prevchg") "previous_cycle" else "same_phase")
    # The PUBLISHED PAC interval uses each industry's own anchored-step residual (rf_pac_v2: rf_step_sigma2(..., sector = s), df = pairs - 1),
    # not the pooled one above; score that construction too, so the recorded coverage is the coverage of the interval the console draws.
    sg_s <- if (spec$kind == "pac") lapply(stats::setNames(names(sl), names(sl)), function(s) rf_step_sigma2(spec, tr, sl, NULL, trc, sector = s)) else NULL
    for (i in seq_len(nrow(tg))) {
      a <- tg[i, ]; s <- as.character(a$sector)
      if (!(s %in% names(sl))) next
      if (spec$kind == "lobby") { r <- a; pvr <- a; prev <- a$y_lag; anchor <- a$y_lag; actual <- a$y }
      else {
        r <- d[d$cycle_end == rf_ref_cycle(cy, cycles) & d$sector == s, ]
        pvr <- d[d$cycle_end == cy - 2 & d$sector == s, ]
        prev <- pvr[[spec$y]]; anchor <- r[[spec$y]]; actual <- a[[spec$y]]
      }
      pr <- .rf_pred_one(fo, spec, sl[[s]], a, r, pvr, first_level)
      v <- .rf_vcoef(spec, fa, fo, rule, s, a, r, pvr)
      half <- if (is.na(sg$sigma2)) NA_real_ else sg$critical_95 * sqrt(v + sg$sigma2)
      half_live <- if (is.null(sg_s)) half else if (is.na(sg_s[[s]]$sigma2)) NA_real_ else sg_s[[s]]$critical_95 * sqrt(v + sg_s[[s]]$sigma2)
      rows[[length(rows) + 1L]] <- data.frame(term = spec$id, base = spec$base, design = design, target = cy, sector = s, rule = rule,
        actual = actual, calc = pr[["calc"]], level = pr[["level"]], prevchg = pr[["prevchg"]], naive_prev = prev, anchor_only = anchor, slope = sl[[s]],
        scale = spec$scale, calc_path = spec$calc_path, low = pr[[spec$calc_path]] - half, high = pr[[spec$calc_path]] + half, sigma2_step = sg$sigma2,
        low_live = pr[[spec$calc_path]] - half_live, high_live = pr[[spec$calc_path]] + half_live, stringsAsFactors = FALSE)
    }
  }
  list(pred = if (length(rows)) do.call(rbind, rows) else NULL, slopes = slopes, feas = feas)
}

# Backtest of one spec under one design ("loeo", "chrono" or, for lobbying, "rolling").
# -> predictions (one row per target x industry x rule) with attr "feasibility" (per fold) and attr "slopes" (per fold and industry)
rf_backtest <- function(spec, panel = spec$d, design = c("loeo", "chrono", "rolling"), rules = c("R0", "R1", "R2")) {
  design <- match.arg(design)
  d0 <- panel
  d0$sector <- factor(d0$sector)
  cycles <- sort(unique(d0$cycle_end))
  times <- if (spec$kind == "lobby") RF$LOBBY_TARGETS[RF$LOBBY_TARGETS %in% cycles] else cycles[-1]
  folds <- rf_pmap(times, function(cy) .rf_fold(spec, d0, cy, cycles, design, rules))
  pred <- do.call(rbind, lapply(folds, `[[`, "pred"))
  attr(pred, "feasibility") <- do.call(rbind, lapply(folds, `[[`, "feas"))
  attr(pred, "slopes") <- do.call(rbind, lapply(folds, `[[`, "slopes"))
  attr(pred, "spec") <- list(id = spec$id, base = spec$base, unit = spec$unit, calc_path = spec$calc_path)
  pred
}

# Cell summaries (tailoring.R cell_summary without the bootstrap / sign tests): one row per (term, base, design, path, rule) with
# n, folds, targets, MAE, RMSE (scaled: log points x100, share pp, $M), equal-weight industry MAE, fold win-loss-tie and row win-loss
# versus R0, per-industry MAE, prediction-interval coverage of the calculator path, and whether the rule beats last-period naive.
rf_backtest_metrics <- function(pred) {
  unit <- attr(pred, "spec")$unit %||% NA_character_
  if (!"low_live" %in% names(pred)) { pred$low_live <- pred$low; pred$high_live <- pred$high }     # e.g. the lobbying log backtest
  long <- do.call(rbind, lapply(c("calc", "level", "prevchg"), function(pth) {
    m <- pred
    if (pth == "prevchg" && all(m$term == "lobby")) return(NULL)
    m$path <- pth; m$pred <- m[[pth]]
    nv <- m[m$rule == "R0", ]
    n1 <- nv; n1$rule <- "naive_prev"; n1$pred <- n1$naive_prev; n1$low <- NA_real_; n1$high <- NA_real_; n1$low_live <- NA_real_; n1$high_live <- NA_real_
    n2 <- nv; n2$rule <- "anchor_only"; n2$pred <- n2$anchor_only; n2$low <- NA_real_; n2$high <- NA_real_; n2$low_live <- NA_real_; n2$high_live <- NA_real_
    keep <- c("term", "base", "design", "path", "target", "sector", "rule", "actual", "pred", "scale", "calc_path", "low", "high", "low_live", "high_live")
    m$low[m$path != m$calc_path] <- NA_real_; m$high[m$path != m$calc_path] <- NA_real_
    m$low_live[m$path != m$calc_path] <- NA_real_; m$high_live[m$path != m$calc_path] <- NA_real_
    rbind(m[, keep], n1[, keep], n2[, keep])
  }))
  long$err <- (long$pred - long$actual) * long$scale
  cells <- split(long, list(long$term, long$base, long$design, long$path), drop = TRUE)
  out <- lapply(cells, function(Q) {
    secs <- sort(unique(Q$sector))
    R0 <- Q[Q$rule == "R0", ]
    fm0 <- tapply(abs(R0$err), R0$target, mean)
    pm0 <- tapply(abs(R0$err), R0$sector, mean)
    k0_all <- paste(R0$target, R0$sector)
    rows <- lapply(unique(Q$rule), function(rl) {
      q <- Q[Q$rule == rl, ]
      fm <- tapply(abs(q$err), q$target, mean)[names(fm0)]
      dd <- fm - fm0
      pm <- tapply(abs(q$err), q$sector, mean)[secs]
      pn <- table(factor(q$sector, levels = secs))
      k0 <- match(paste(q$target, q$sector), k0_all)
      inside <- !is.na(q$low) & !is.na(q$high)
      live <- !is.na(q$low_live) & !is.na(q$high_live)
      o <- data.frame(term = q$term[1], base = q$base[1], design = q$design[1], path = q$path[1], rule = rl, n = nrow(q), folds = length(fm0),
        targets = paste(range(as.integer(names(fm0))), collapse = "-"), MAE = mean(abs(q$err)), RMSE = sqrt(mean(q$err^2)), EW_MAE = mean(pm),
        EW_rel_R0 = mean(pm / pm0[secs]), dMAE_vs_R0 = mean(abs(q$err)) - mean(abs(R0$err)),
        fold_wins = sum(dd < -1e-12), fold_losses = sum(dd > 1e-12), fold_ties = length(dd) - sum(dd < -1e-12) - sum(dd > 1e-12),
        row_wins = sum(abs(q$err) < abs(R0$err[k0]) - 1e-12), row_losses = sum(abs(q$err) > abs(R0$err[k0]) + 1e-12),
        coverage_95 = if (any(inside)) mean(q$actual[inside] >= q$low[inside] & q$actual[inside] <= q$high[inside]) else NA_real_,
        coverage_95_n = sum(inside),                                    # rows that HAVE an interval (aliased early folds have none)
        coverage_95_live = if (any(live)) mean(q$actual[live] >= q$low_live[live] & q$actual[live] <= q$high_live[live]) else NA_real_,
        coverage_95_live_n = sum(live),
        units = unit, stringsAsFactors = FALSE)
      for (s in secs) { o[[paste0("MAE_", s)]] <- pm[[s]]; o[[paste0("n_", s)]] <- as.integer(pn[[s]]) }
      o
    })
    res <- do.call(rbind, rows)
    naive <- res$MAE[res$rule == "naive_prev"]
    res$beats_naive <- ifelse(res$rule %in% c("naive_prev", "anchor_only"), NA, res$MAE < naive)
    res
  })
  res <- do.call(rbind, out)
  rownames(res) <- NULL
  res
}

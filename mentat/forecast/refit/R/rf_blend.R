# The current-cycle layer (D-079): the roster PAC projection of the open cycle is BLEND, a precision-weighted average (log scale) of the MODEL's prediction (the prior) and the
# PACE projection (giving observed to date divided by the historical share given by that point of the cycle), truncated at the giving already observed.
# Specification: C:\melange-data\forecast-redesign-20260930\pace_test\SPEC.md sections 5-8 (frozen before any series value existed); this file re-implements sections 5-8 in R so
# the prior can be ANY model of this refit (the selected phase / trajectory candidate), not only the published one. The roster series (F = full cycle, O15 / O18 / O21 = to the
# end of March / June / September of the election year, cents) are the pace test's frozen output files and are only read.
#
# Per target cycle t and design (loeo: every other completed cycle trains; chrono: earlier cycles train):
#   mu_F   = ln(F(t-2)) + ln m(t-2) - ln m(t) + d_hat       the model's predicted log full-cycle total in nominal dollars of cycle t (d_hat = model change in real log dollars)
#   BASE   = the console's conditioning of the log-t predictive (scale = half / t_crit(df), df = pairs - 1) on F >= O (the giving already observed)
#   PACE   = ln O - mean(ln(O_c / F_c)) over the training cycles, truncated at O; predictive variance v_P = S2 (1 + 1/n), t with n - 1 df
#   BLEND  = w_B mu_F + (1 - w_B) ln F_pace, w_B = v_P / (v_P + v_B) (v_B = scale^2), truncated at O.

RF_BLEND_MONTHS <- c(`15` = "O15_cents", `18` = "O18_cents", `21` = "O21_cents")
RF_BLEND_TARGETS <- list(loeo = c(2014L, 2016L, 2018L, 2020L, 2022L, 2024L), chrono = c(2018L, 2020L, 2022L, 2024L))

rf_blend_read_series <- function(path) {
  s <- utils::read.csv(path, stringsAsFactors = FALSE, colClasses = "numeric")
  s[order(s$cycle), c("cycle", "F_cents", "O15_cents", "O18_cents", "O21_cents")]
}

# Conditioned quantiles of the console's _given_observed (giving_forecast.py): the log-t predictive conditioned on F >= O. -> list(ok, lo, med, hi) in log dollars
rf_blend_condition <- function(mu, scale, df, O) {
  if (O < 0) return(list(ok = FALSE))
  u0 <- if (O == 0) 1 else stats::pt((log(O) - mu) / scale, df, lower.tail = FALSE)
  if (!(u0 > 1e-12)) return(list(ok = FALSE))
  z <- vapply(c(0.025, 0.5, 0.975), function(q) stats::qt((1 - q) * u0, df, lower.tail = FALSE), 0)
  if (any(!is.finite(z)) || any(abs(z) > 9000)) return(list(ok = FALSE))
  list(ok = TRUE, lo = mu + scale * z[1], med = mu + scale * z[2], hi = mu + scale * z[3])
}

# base: data.frame(design, target, d_hat, half, pairs) of ONE industry (d_hat = the model's real log change, half = its 95% half-width in log units, pairs = the training pairs of its step variance)
# mult: named vector of the cycle price multipliers m(c) (cpi[2025] / mean(cpi[c-1], cpi[c])) by cycle.
# -> data.frame(design, month, target, candidate, F_hat, lo, hi, F, err100, covered, interval_kind)   candidate in BASE, LEGACY, NAIVE, PACE, BLEND
rf_blend_total <- function(series, base, mult, month = 18L, targets = RF_BLEND_TARGETS) {
  col <- RF_BLEND_MONTHS[[as.character(month)]]
  Fd <- stats::setNames(series$F_cents / 100, series$cycle)
  Od <- stats::setNames(series[[col]] / 100, series$cycle)
  rows <- list()
  put <- function(ds, t, cand, fh, lo, hi, kind) {
    rows[[length(rows) + 1L]] <<- data.frame(design = ds, month = month, target = t, candidate = cand, F_hat = fh, lo = lo, hi = hi, F = Fd[[as.character(t)]],
                                             err100 = 100 * abs(log(fh) - log(Fd[[as.character(t)]])), covered = if (is.na(lo) || is.na(hi)) NA else (Fd[[as.character(t)]] >= lo && Fd[[as.character(t)]] <= hi),
                                             interval_kind = kind, stringsAsFactors = FALSE)
  }
  for (ds in names(targets)) for (t in targets[[ds]]) {
    b <- base[base$design == ds & base$target == t, ]
    if (nrow(b) != 1L) stop("rf_blend_total: no unique base row for ", ds, " ", t, call. = FALSE)
    tr <- if (ds == "loeo") setdiff(series$cycle, t) else series$cycle[series$cycle < t]
    tr <- tr[tr %in% series$cycle]
    mc <- function(c) mult[[as.character(c)]]
    mu <- log(Fd[[as.character(t - 2)]]) + log(mc(t - 2)) - log(mc(t)) + b$d_hat
    df <- b$pairs - 1; tcb <- stats::qt(0.975, df); scale <- b$half / tcb; vB <- scale^2
    O <- Od[[as.character(t)]]
    put(ds, t, "NAIVE", exp(mu - b$d_hat), NA_real_, NA_real_, "")
    put(ds, t, "LEGACY", exp(mu), exp(mu - b$half), exp(mu + b$half), "context")
    cq <- rf_blend_condition(mu, scale, df, O)
    if (cq$ok) put(ds, t, "BASE", exp(cq$med), exp(cq$lo), exp(cq$hi), "pre-registered") else put(ds, t, "BASE", NA_real_, NA_real_, NA_real_, "pre-registered")
    n <- length(tr)
    if (n < 2L || any(Fd[as.character(tr)] <= 0) || any(Od[as.character(tr)] <= 0)) {
      put(ds, t, "PACE", NA_real_, NA_real_, NA_real_, "pre-registered"); put(ds, t, "BLEND", NA_real_, NA_real_, NA_real_, "descriptive")
      next
    }
    ell <- log(Od[as.character(tr)]) - log(Fd[as.character(tr)])
    lbar <- mean(ell); S2 <- sum((ell - lbar)^2) / (n - 1)
    lpace <- log(O) - lbar
    vP <- S2 * (1 + 1 / n); tcp <- stats::qt(0.975, n - 1); hP <- tcp * sqrt(vP)
    fpace <- max(exp(lpace), O)
    put(ds, t, "PACE", fpace, exp(lpace - hP), exp(lpace + hP), "pre-registered")
    wB <- if (vP + vB > 0) vP / (vP + vB) else 0
    lbl <- wB * mu + (1 - wB) * log(fpace)
    h <- tcp * sqrt(vP * vB / (vP + vB))
    put(ds, t, "BLEND", max(exp(lbl), O), exp(lbl - h), exp(lbl + h), "descriptive")
  }
  r <- do.call(rbind, rows)
  rownames(r) <- NULL
  r
}

# MAE100, coverage and mean log width per design x candidate (a metric is NA if any test cycle is NA, as the spec's section 6 NA policy).
rf_blend_metrics <- function(cand) {
  out <- lapply(split(cand, list(cand$design, cand$month, cand$candidate), drop = TRUE), function(q) {
    has <- !is.na(q$lo) & !is.na(q$hi)
    data.frame(design = q$design[1], month = q$month[1], candidate = q$candidate[1], n_tests = nrow(q), MAE100 = mean(q$err100), coverage = if (all(has)) mean(q$covered) else NA_real_,
               mean_log_width = if (all(has)) mean(log(q$hi) - log(q$lo)) else NA_real_, bias100 = mean(100 * (log(q$F_hat) - log(q$F))), stringsAsFactors = FALSE)
  })
  r <- do.call(rbind, out)
  rownames(r) <- NULL
  r
}

# D-065 for a candidate against BASE at one month: passes a design iff MAE < MAE_BASE - tol and MAE <= (1 - min_gain) MAE_BASE + tol; passes iff both designs pass.
rf_blend_decision <- function(metrics, cand, month = 18L, base = "BASE", min_gain = RF$RULE_MIN_GAIN, tol = RF$RULE_TIE_TOL) {
  g <- function(c, ds) metrics$MAE100[metrics$candidate == c & metrics$design == ds & metrics$month == month]
  pass <- vapply(c("loeo", "chrono"), function(ds) { a <- g(cand, ds); b <- g(base, ds); length(a) == 1L && length(b) == 1L && !is.na(a) && !is.na(b) && a < b - tol && a <= (1 - min_gain) * b + tol }, NA)
  list(MAE_base = vapply(c("loeo", "chrono"), function(ds) g(base, ds), 0), MAE = vapply(c("loeo", "chrono"), function(ds) g(cand, ds), 0),
       rel_improvement = vapply(c("loeo", "chrono"), function(ds) 1 - g(cand, ds) / g(base, ds), 0), passes_design = pass, passes = all(pass))
}

# The model's inputs for BASE from a phase backtest table (rf_ph_backtest rows): one industry, one candidate -> data.frame(design, target, d_hat, half, pairs). d_hat = pred - naive_prev.
rf_blend_base_from_pred <- function(pred, cand, sector) {
  a <- pred[pred$candidate == cand & pred$sector == sector, ]
  n <- pred[pred$candidate == "naive_prev" & pred$sector == sector, ]
  k <- paste(a$design, a$target)
  data.frame(design = a$design, target = a$target, d_hat = a$pred - n$pred[match(k, paste(n$design, n$target))], half = a$half, pairs = a$pairs, stringsAsFactors = FALSE)
}

# ---- the party-share layer: the same pace / BLEND construction on the Republican share of the roster PACs' two-party giving to one chamber -------------------------------------
# Series (the pace test's frozen roster totals split by party; work/share/roster_party_series.csv, reconciled to the pace series to the cent): per cycle c, chamber and cut m,
# R_m(c), D_m(c) in cents, s_m(c) = R_m / (R_m + D_m), s(c) = the full-cycle share. Candidates for the full-cycle share s(t) at the as-of month m (logit scale):
#   MODEL  = the published share model's change applied to the roster's previous-cycle share: s(t-2) + d_hat (interval: its half-width, share units)   [the prior]
#   SPACE  = logit s_m(t) + the mean over the training cycles of logit s(c) - logit s_m(c) (the historical drift from the as-of share to the final share), t(n-1) interval
#   SBLEND = the precision-weighted average of MODEL and SPACE (weights = inverse predictive variances), truncated so that the implied remaining money of each party is non-negative
#            with the full-cycle two-party total projected by the chamber's own pace (O_m / mean historical O_m / F)
#   OBSERVED (the as-of share, no drift) and NAIVE (previous cycle's final share) are context only.
RF_SHARE_TARGETS <- list(loeo = c(2014L, 2016L, 2018L, 2020L, 2022L), chrono = c(2018L, 2020L, 2022L))     # the share panels end in 2022

rf_logit <- function(p) log(p / (1 - p))
rf_expit <- function(x) 1 / (1 + exp(-x))

rf_share_read <- function(path) utils::read.csv(path, stringsAsFactors = FALSE)

# series of one industry x chamber -> data.frame(cycle, RF, DF, R15, D15, R18, D18, R21, D21) (two-party cents)
rf_share_series <- function(df, industry, chamber) {
  q <- df[df$industry == industry & df$chamber == chamber & df$party %in% c("R", "D"), ]
  cyc <- sort(unique(q$cycle))
  do.call(rbind, lapply(cyc, function(c) {
    r <- q[q$cycle == c & q$party == "R", ]; d <- q[q$cycle == c & q$party == "D", ]
    g <- function(x, col) if (nrow(x)) x[[col]][1] else 0
    data.frame(cycle = c, RF = g(r, "F_cents"), DF = g(d, "F_cents"), R15 = g(r, "O15_cents"), D15 = g(d, "O15_cents"), R18 = g(r, "O18_cents"), D18 = g(d, "O18_cents"),
               R21 = g(r, "O21_cents"), D21 = g(d, "O21_cents"), stringsAsFactors = FALSE)
  }))
}

# base: data.frame(design, target, d_hat, half, pairs) of the share model (share units 0-1). -> candidates (design, month, target, candidate, s_hat, lo, hi, s, err_pp, covered)
rf_share_blend <- function(ser, base, month = 18L, targets = RF_SHARE_TARGETS, eps = 1e-4) {
  rc <- paste0("R", month); dc <- paste0("D", month)
  s_fin <- stats::setNames(ser$RF / (ser$RF + ser$DF), ser$cycle)
  s_m <- stats::setNames(ser[[rc]] / (ser[[rc]] + ser[[dc]]), ser$cycle)
  T_fin <- stats::setNames(ser$RF + ser$DF, ser$cycle); T_m <- stats::setNames(ser[[rc]] + ser[[dc]], ser$cycle)
  cl <- function(p) pmin(pmax(p, eps), 1 - eps)
  rows <- list()
  put <- function(ds, t, cand, sh, lo, hi) rows[[length(rows) + 1L]] <<- data.frame(design = ds, month = month, target = t, candidate = cand, s_hat = sh, lo = lo, hi = hi, s = s_fin[[as.character(t)]],
                                                                                    err_pp = 100 * abs(sh - s_fin[[as.character(t)]]), bias_pp = 100 * (sh - s_fin[[as.character(t)]]),
                                                                                    covered = if (is.na(lo) || is.na(hi)) NA else (s_fin[[as.character(t)]] >= lo && s_fin[[as.character(t)]] <= hi), stringsAsFactors = FALSE)
  for (ds in names(targets)) for (t in targets[[ds]]) {
    b <- base[base$design == ds & base$target == t, ]
    if (nrow(b) != 1L || !(as.character(t) %in% names(s_fin)) || !(as.character(t - 2) %in% names(s_fin))) next
    tr <- if (ds == "loeo") setdiff(ser$cycle, t) else ser$cycle[ser$cycle < t]
    tr <- tr[tr %in% ser$cycle & is.finite(s_fin[as.character(tr)]) & is.finite(s_m[as.character(tr)]) & s_m[as.character(tr)] > 0 & s_m[as.character(tr)] < 1]
    n <- length(tr)
    mu <- s_fin[[as.character(t - 2)]] + b$d_hat                              # the prior: the previous cycle's roster share plus the model's change
    crit <- stats::qt(0.975, b$pairs - 1); scale_s <- b$half / crit
    sm <- s_m[[as.character(t)]]
    put(ds, t, "NAIVE", s_fin[[as.character(t - 2)]], NA_real_, NA_real_)
    put(ds, t, "OBSERVED", sm, NA_real_, NA_real_)
    pm <- cl(mu)
    put(ds, t, "MODEL", min(max(mu, 0), 1), if (is.na(b$half)) NA_real_ else max(mu - b$half, 0), if (is.na(b$half)) NA_real_ else min(mu + b$half, 1))
    if (n < 2L || sm <= 0 || sm >= 1) { put(ds, t, "SPACE", NA_real_, NA_real_, NA_real_); put(ds, t, "SBLEND", NA_real_, NA_real_, NA_real_); next }
    drift <- rf_logit(cl(s_fin[as.character(tr)])) - rf_logit(cl(s_m[as.character(tr)]))
    dbar <- mean(drift); S2 <- sum((drift - dbar)^2) / (n - 1)
    lsp <- rf_logit(cl(sm)) + dbar
    vP <- S2 * (1 + 1 / n); tcp <- stats::qt(0.975, n - 1)
    # the chamber's own pace for the two-party total: the truncation bounds of the implied remaining split
    Fh <- T_m[[as.character(t)]] / exp(mean(log(T_m[as.character(tr)]) - log(T_fin[as.character(tr)])))
    Rm <- ser[[rc]][ser$cycle == t]; Dm <- ser[[dc]][ser$cycle == t]
    lo_b <- min(Rm / Fh, 1); hi_b <- max(1 - Dm / Fh, 0)
    trunc <- function(x) if (lo_b <= hi_b) min(max(x, lo_b), hi_b) else x
    put(ds, t, "SPACE", trunc(rf_expit(lsp)), rf_expit(lsp - tcp * sqrt(vP)), rf_expit(lsp + tcp * sqrt(vP)))
    if (is.na(scale_s)) { put(ds, t, "SBLEND", NA_real_, NA_real_, NA_real_); next }                  # the prior has no variance in this fold (too few training pairs): no precision weight, NA by the spec's NA policy
    sd_logit <- scale_s / (pm * (1 - pm)); vB <- sd_logit^2                     # the prior's variance on the logit scale (delta method)
    wB <- if (vP + vB > 0) vP / (vP + vB) else 0
    lb <- wB * rf_logit(pm) + (1 - wB) * lsp
    h <- tcp * sqrt(vP * vB / (vP + vB))
    put(ds, t, "SBLEND", trunc(rf_expit(lb)), rf_expit(lb - h), rf_expit(lb + h))
  }
  r <- do.call(rbind, rows)
  rownames(r) <- NULL
  r
}

rf_share_metrics <- function(cand) {
  out <- lapply(split(cand, list(cand$design, cand$month, cand$candidate), drop = TRUE), function(q) {
    has <- !is.na(q$lo) & !is.na(q$hi)
    data.frame(design = q$design[1], month = q$month[1], candidate = q$candidate[1], n_tests = nrow(q), MAE_pp = mean(q$err_pp), bias_pp = mean(q$bias_pp),
               coverage = if (all(has)) mean(q$covered) else NA_real_, mean_width_pp = if (all(has)) 100 * mean(q$hi - q$lo) else NA_real_, stringsAsFactors = FALSE)
  })
  r <- do.call(rbind, out); rownames(r) <- NULL; r
}

# The current-cycle projection of one open cycle (no truth available): the prior (log full-cycle total of the model, nominal dollars of the open cycle, with its t scale and df), the giving
# observed to date O (nominal dollars) and the completed cycles' pace series (all of them train). -> list(O, BASE = conditioned model, PACE, BLEND, n) in nominal dollars, with BLEND's
# log scale / df so that a chained projection can draw from it. BLEND = max(exp(w mu + (1 - w) ln F_pace), O) as in the backtest (SPEC section 6).
rf_blend_current <- function(series, mu, scale, df, O, month = 18L) {
  col <- RF_BLEND_MONTHS[[as.character(month)]]
  Fd <- series$F_cents / 100; Od <- series[[col]] / 100
  n <- length(Fd)
  ell <- log(Od) - log(Fd); lbar <- mean(ell); S2 <- sum((ell - lbar)^2) / (n - 1)
  vP <- S2 * (1 + 1 / n); tcp <- stats::qt(0.975, n - 1); hP <- tcp * sqrt(vP)
  lpace <- log(O) - lbar
  vB <- scale^2
  wB <- if (vP + vB > 0) vP / (vP + vB) else 0
  lbl <- wB * mu + (1 - wB) * log(max(exp(lpace), O))
  sB <- sqrt(vP * vB / (vP + vB))
  cq <- rf_blend_condition(mu, scale, df, O)
  list(O = O, n = n, lbar = lbar, S2 = S2, w_B = wB, mu = mu, scale = scale, df = df,
       BASE = if (cq$ok) list(lo = exp(cq$lo), med = exp(cq$med), hi = exp(cq$hi)) else NULL,
       PACE = list(point = max(exp(lpace), O), lo = exp(lpace - hP), hi = exp(lpace + hP)),
       BLEND = list(point = max(exp(lbl), O), lo = exp(lbl - tcp * sB), hi = exp(lbl + tcp * sB), log_center = lbl, log_scale = sB, df = n - 1))
}

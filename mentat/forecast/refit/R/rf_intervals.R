# Prediction intervals beside the coefficient interval (spec 3.10, ruling of 2026-09-30: "anchored-step residual").
# For a calculator model compute, on the TRAINING panel, the residuals of the calculator's own step
#     e = (y_t - y_anchor(t)) - (modelled change)
# PAC: pairs (t-2, t) per industry, modelled change = slope_s * dG;  share: pairs (ref(t), t) under the same-phase rule, modelled
# change = the offset-fit terms plus slope_s * dF;  lobbying: each annual pair, modelled change = slope_s * dg.
#     sigma2_step = sum(e^2) / (m - 1),  crit = qt(.975, m - 1)
#     interval = point +/- crit * sqrt(v_coef + sigma2_step)     (PAC works in logs; exponentiate for display)
# v_coef is the coefficient variance of the modelled change. Library code: the v1 parity bundle does not use it.

# Same-phase reference cycle of `cy` among `cycles` (latest earlier cycle of the same presidential phase, else the latest earlier one).
rf_ref_cycle <- function(cy, cycles) {
  e <- cycles[cycles < cy]
  if (!length(e)) return(NA_real_)
  same <- e[e %% 4 == cy %% 4]
  if (length(same)) max(same) else max(e)
}

# Model-matrix rows of a one-row (or many-row) data frame under a fitted model's terms.
.rf_mm <- function(fit, d) stats::model.matrix(stats::delete.response(stats::terms(fit)), d, xlev = fit$xlevels)

# Residuals of the calculator's anchored step on a training panel. `slopes` named by industry; `offset_fit` is the lm of
# rf_offset_fit (share models). -> list(sigma2, pairs, df, critical_95, residuals)
rf_step_sigma2 <- function(spec, panel, slopes, offset_fit = NULL, cycles = sort(unique(panel$cycle_end)), sector = NULL, anchor = c("same_phase", "previous_cycle", "same_phase_strict")) {
  anchor <- match.arg(anchor)
  d <- panel
  d$sector <- droplevels(factor(d$sector))
  if (!is.null(sector)) d <- d[d$sector == sector, ]
  d$sector <- droplevels(d$sector)
  e <- numeric()
  if (spec$kind == "lobby") {
    e <- d$dy - slopes[as.character(d$sector)] * d$dg
  } else if (spec$kind == "pac") {
    for (s in levels(d$sector)) for (t in cycles) {
      a <- d[d$sector == s & d$cycle_end == t, ]; b <- d[d$sector == s & d$cycle_end == t - 2, ]
      if (nrow(a) == 1L && nrow(b) == 1L) e <- c(e, (a[[spec$y]] - b[[spec$y]]) - slopes[[s]] * (a[[spec$f]] - b[[spec$f]]))
    }
  } else {
    if (is.null(offset_fit)) stop("rf_step_sigma2: share models need the offset fit", call. = FALSE)
    g <- stats::coef(offset_fit); g[is.na(g)] <- 0
    for (s in levels(d$sector)) for (t in cycles) {
      r0 <- switch(anchor, previous_cycle = if ((t - 2) %in% cycles) t - 2 else NA_real_,
                   same_phase_strict = if ((t - 4) %in% cycles) t - 4 else NA_real_,                # the calculator's same-phase anchor is always a full four years back
                   rf_ref_cycle(t, cycles))
      if (is.na(r0)) next
      a <- d[d$sector == s & d$cycle_end == t, ]; r <- d[d$sector == s & d$cycle_end == r0, ]
      if (nrow(a) == 1L && nrow(r) == 1L) {
        dx <- sum((.rf_mm(offset_fit, a) - .rf_mm(offset_fit, r)) * g)
        e <- c(e, (a[[spec$y]] - r[[spec$y]]) - (dx + slopes[[s]] * (a[[spec$f]] - r[[spec$f]])))
      }
    }
  }
  m <- length(e)
  if (m < 2L) return(list(sigma2 = NA_real_, pairs = as.integer(m), df = as.integer(max(m - 1L, 0L)), critical_95 = NA_real_, residuals = e))
  list(sigma2 = sum(e^2) / (m - 1), pairs = as.integer(m), df = as.integer(m - 1L), critical_95 = stats::qt(0.975, m - 1), residuals = e)
}

# point +/- crit * sqrt(v_coef + sigma2); on the log scale the endpoints are exponentiated. -> c(low, high)
rf_interval <- function(point, v_coef, sigma2, crit, log_scale = FALSE) {
  half <- crit * sqrt(v_coef + sigma2)
  out <- c(low = point - half, high = point + half)
  if (log_scale) out <- exp(out)
  out
}

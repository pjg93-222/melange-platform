# Model fitters: every regression is a real lm() whose summary() is printed as R prints it. The v1 variable selections are
# FROZEN in registry/models.json; selection (rf_stepwise.R, rf_forward_aic) re-runs only as a diagnostic.
# Term naming follows the v1 bundle: (Intercept) -> Intercept, sector<x> -> industry:<x>, the focal variable -> Forecast,
# focal:sector<x> / sector<x>:focal -> Forecast:<x>.

# lm() evaluated with the data under a label, so summary() prints `data = <label>` (as SP/regressions/regressions.R did).
rf_lm <- function(formula, data, label) {
  env <- new.env(parent = globalenv())
  assign(label, data, envir = env)
  f <- if (is.character(formula)) stats::as.formula(formula, env = env) else formula
  eval(bquote(lm(.(f), data = .(as.name(label)))), envir = env)   # `lm`, not `stats::lm`, so the printed Call reads as in R
}

# Sector as a factor with sorted (reference-first) levels.
rf_prep_panel <- function(d) {
  if ("sector" %in% names(d)) d$sector <- factor(d$sector, levels = rf_sorted(d$sector))
  rownames(d) <- NULL
  d
}

# R coefficient names -> bundle term names (see the header comment).
rf_bundle_terms <- function(m, focal) {
  nm <- names(stats::coef(m))
  nm <- ifelse(nm == "(Intercept)", "Intercept", nm)
  nm <- sub(paste0("^sector(.+):", focal, "$"), "Forecast:\\1", nm)
  nm <- sub(paste0("^", focal, ":sector(.+)$"), "Forecast:\\1", nm)
  nm <- ifelse(nm == focal, "Forecast", nm)
  nm <- sub("^sector(.+)$", "industry:\\1", nm)
  nm
}

# A v1-shaped model block: terms, beta, covariance (rows), ordinary_p (by term), critical_95, residual_df, n, adjusted_r2, residual_sd.
rf_bundle_model <- function(m, focal) {
  s <- summary(m)
  terms <- rf_bundle_terms(m, focal)
  V <- stats::vcov(m)
  list(terms = as.list(terms), beta = as.list(unname(stats::coef(m))),
       covariance = lapply(seq_len(nrow(V)), function(i) as.list(unname(V[i, ]))),
       ordinary_p = stats::setNames(as.list(unname(s$coefficients[, 4])), terms),
       critical_95 = stats::qt(0.975, stats::df.residual(m)), residual_df = as.integer(stats::df.residual(m)),
       n = as.integer(stats::nobs(m)), adjusted_r2 = s$adj.r.squared, residual_sd = s$sigma)
}

# The per-term data a fitted model was built from (kept beside the model for robustness blocks and printing).
rf_fit_panels <- function(ctx, models) {
  ms <- models$models
  fits <- list(); data <- list()
  add <- function(id, formula, d, label) {
    d <- rf_prep_panel(d)
    data[[id]] <<- d
    fits[[id]] <<- rf_lm(formula, d, label)
  }
  pac <- ctx$pac_panel[order(ctx$pac_panel$sector, ctx$pac_panel$cycle_end), ]
  add("pac", ms$pac$formula, pac, "pac_panel_2012_2024")
  add("pac_complete", ms$pac_complete$formula, pac, "pac_panel_2012_2024")
  for (id in c("control_House", "control_Senate", "seats_House", "seats_Senate")) {
    focal <- ms[[id]]$focal
    d <- ctx$share[[id]]
    yrs <- range(d$cycle_end)
    label <- sprintf("%s_%d_%d", id, yrs[1], yrs[2])
    add(id, ms[[id]]$formula, d, label)
    # the v1 industry-slope variant: original full model (mask 15) plus one forecast slope per industry
    full <- if (grepl("^control", id)) ms[[paste0(id, "_full")]]$formula else ms[[id]]$formula
    add(paste0(id, "_industry"), paste0(full, " + ", focal, ":sector"), d, label)
    if (grepl("^control", id)) add(paste0(id, "_full"), ms[[paste0(id, "_full")]]$formula, d, label)
  }
  lob <- ctx$lobby$diffs
  add("lobby", ms$lobby$formula, lob, "lobbying_all71")
  list(fits = fits, data = data)
}

# v1-shaped pooled PAC, share and lobbying model blocks (built_models of build_giving_models.py:49-68).
# `selected` = the frozen wild / selection p-values of the PAC and lobbying models, computed by the caller.
rf_v1_models <- function(ctx) {
  ms <- ctx$models$models
  f <- ctx$fits
  # ---- PAC
  s <- summary(f$pac)$coefficients["G_current", ]
  pac <- list(beta = unname(s[1]), ordinary_se = unname(s[2]), ordinary_p = unname(s[4]),
              critical_95 = stats::qt(0.975, stats::df.residual(f$pac)), n = as.integer(stats::nobs(f$pac)),
              cycles = as.list(as.integer(sort(unique(ctx$pac_panel$cycle_end)))),
              specification = "Forward-selected PAC growth model with industry and cycle fixed effects; the reference cycle effect is held fixed for scenarios",
              selection_repeated_p = ctx$pac_selection$p)
  # ---- share models (order of the v1 bundle)
  share <- list()
  for (signal in c("control", "seats")) for (chamber in c("House", "Senate")) {
    base <- paste(signal, chamber, sep = "_")
    d <- ctx$fits_data[[base]]
    for (varying in c(FALSE, TRUE)) {
      id <- if (varying) paste0(base, "_industry") else base
      m <- f[[id]]
      focal <- ms[[base]]$focal
      blk <- rf_bundle_model(m, focal)
      blk$id <- id; blk$signal <- signal; blk$chamber <- chamber; blk$industry_slopes <- varying
      blk$specification <- if (varying) "Original full model with industry-specific forecast slopes" else if (signal == "control") "AICc-selected pooled model" else
        "Original full seat-gain sensitivity; seat gain was not retained by selected pooled share model"
      blk$training_cycles <- as.list(as.integer(sort(unique(d$cycle_end))))
      dd <- ctx$share[[base]]
      blk$history <- lapply(seq_len(nrow(dd)), function(i) list(
        industry = as.character(dd$sector[i]), cycle = as.integer(dd$cycle_end[i]), share = dd$RShare[i], forecast = dd$Forecast[i],
        current_control = as.integer(dd$RMajNow[i]), growth_pct = dd$cycle_revenue_growth_pct[i],
        prior_growth_pct = dd$prior_cycle_revenue_growth_pct[i], pres = as.integer(dd$Pres[i]),
        candidate_total_real_2025 = (dd$D_nominal[i] + dd$R_nominal[i]) * dd$multiplier_to_2025_dollars[i]))
      share[[id]] <- blk[c("terms", "beta", "covariance", "ordinary_p", "critical_95", "residual_df", "n", "adjusted_r2", "residual_sd", "id",
                           "signal", "chamber", "industry_slopes", "specification", "training_cycles", "history")]
    }
  }
  # ---- lobbying
  sl <- summary(f$lobby)$coefficients["dg", ]
  pairs <- table(ctx$lobby$diffs$study_sector)
  lobby <- list(beta_million = unname(sl[1]), ordinary_se_million = unname(sl[2]), ordinary_p = unname(sl[4]),
                critical_95 = stats::qt(0.975, stats::df.residual(f$lobby)), n = as.integer(stats::nobs(f$lobby)),
                industry_pair_counts = stats::setNames(as.list(as.integer(pairs)), names(pairs)),
                specification = "Change in annual real lobbying on change in forecast annual growth, year effects; same pooled slope per industry, calendar effects held fixed",
                wild_p = ctx$lobby_wild$p, CR2_p = ctx$lobby_cr2$p)
  list(pac_model = pac, share_models = share, lobby_model = lobby)
}

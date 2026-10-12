# Phase-aware candidates for the PAC and the general-giving level models (owner ruling 2026-10-01: every projected quantity except lobbying
# accounts for the midterm / presidential difference; D-065 decides HOW among phase-aware candidates, DE-049 re-runs the slope-rule comparison).
#
# Candidates, for a log-level series y (log_pac: OpenSecrets PAC money; log_individual: OpenSecrets Individuals, the relationship series of
# general giving, D-064) on the industry x cycle panel of the refit:
#   PUB  published form: previous-cycle anchor, y_t = y_(t-2) + slope * (G_t - G_(t-2)); slope from the levels model with industry and cycle
#        fixed effects (the cycle effects absorb the phase and are NOT carried into the projection).  [reference, not a candidate]
#   PA   same-phase anchor: y_t = y_(t-4) + slope * (G_t - G_(t-4)); same levels-model slope. Strict: no row when cycle t-4 is not in the panel.
#   PB   previous-cycle anchor plus an estimated phase shift: y_t = y_(t-2) + slope * (G_t - G_(t-2)) + phi * (Pres_t - Pres_(t-2));
#        slope and phi from the first-difference model  dy ~ 0 + dG + dPres  (no intercept, no cycle effects), pooled phi or one phi per industry.
# Slope rules R0 pooled / R1 own / R2 DerSimonian-Laird blend are the refit's (rf_fit_rules, rf_dl); the folds are the refit's no-leakage folds
# (leave-one-election-out and chronological, growth re-standardized on the training cycles, rf_restandardize).
# General giving today borrows the PAC term: the published form PUBPAC applies the PAC slope (fitted on log_pac in the same fold) to the
# individual-giving anchor; that is the reference the general candidates are compared with.
#
# D-065 (rf_ph_select): a candidate replaces the default only if its MAE is at least RF$RULE_MIN_GAIN lower than the default's in BOTH designs on
# the rows both have. Library code: nothing here is wired into the v1 parity output.

RF_PH_SERIES <- c(pac = "log_pac", general = "log_individual")
RF_PH_DEFAULT <- "PB-R0"                                                    # PB, pooled slope, pooled phi
RF_PH_CANDIDATES <- c("PA-R0", "PA-R1", "PA-R2", "PB-R0", "PB-R1", "PB-R2", "PB-R0-phiS", "PB-R1-phiS", "PB-R2-phiS")
RF_PH_FAMILIES <- list(PA = c("PA-R0", "PA-R1", "PA-R2"), PB = c("PB-R0", "PB-R1", "PB-R2"), `PB-phiS` = c("PB-R0-phiS", "PB-R1-phiS", "PB-R2-phiS"))
# Supplementary family added AFTER the first backtest (disclosed in the report): PBL = PB with the PUBLISHED levels-model slope (industry and cycle fixed effects,
# rules R0/R1/R2) and phi estimated from the first differences net of that slope (pooled, or one per industry): y_t = y_(t-2) + slope_lv * dG + phi * dPres.
RF_PH_FAMILIES_EXT <- c(RF_PH_FAMILIES, list(PBL = c("PBL-R0", "PBL-R1", "PBL-R2"), `PBL-phiS` = c("PBL-R0-phiS", "PBL-R1-phiS", "PBL-R2-phiS")))
RF_PH_CANDIDATES_EXT <- c(RF_PH_CANDIDATES, unlist(RF_PH_FAMILIES_EXT[c("PBL", "PBL-phiS")], use.names = FALSE))

# Next-cycle horizon (coordinator addition 2026-10-01): lambdas of the PC(lambda) bracket and the challengers of the PC default.
RF_PH_LAMBDAS <- seq(0, 0.9, by = 0.1)
RF_PH_NEXT_CHALLENGERS <- c("PA-R0", "PA-R1", "PA-R2", "PB2-R0", "PB2-R1", "PB2-R2")
RF_PH_NEXT_CHALLENGERS_T <- c("PA-R0-T", "PA-R1-T", "PA-R2-T", "PB2T")

# First differences over one cycle step of a panel (sector factor, cycle_end, series y, G_current, presidential_cycle).
rf_ph_fd_panel <- function(d, y) {
  a <- d[, c("sector", "cycle_end", y, "G_current", "presidential_cycle")]
  names(a) <- c("sector", "cycle_end", "y", "G", "P")
  b <- a
  b$cycle_end <- b$cycle_end + 2L
  m <- merge(a, b, by = c("sector", "cycle_end"), suffixes = c("", ".lag"))
  fd <- data.frame(sector = m$sector, cycle_end = m$cycle_end, dy = m$y - m$y.lag, dG = m$G - m$G.lag, dPres = m$P - m$P.lag, stringsAsFactors = FALSE)
  fd$sector <- droplevels(factor(fd$sector, levels = levels(factor(d$sector))))
  fd[order(fd$sector, fd$cycle_end), ]
}

# The first-difference fit under R0 / R1 / R2: slopes by industry and phi by industry (phi pooled = the same value for every industry).
# -> list(ok, reason, df1, secs, fa (rf_fit_rules result on the differences), f1, phi = list(R0, R1, R2), spec)
rf_ph_fit <- function(fd, phi = c("pooled", "industry")) {
  phi <- match.arg(phi)
  fd$sector <- droplevels(fd$sector)
  spec <- list(y = "dy", f = "dG", others = if (phi == "pooled") "0 + dPres" else "0 + dPres:sector")
  fa <- tryCatch(rf_fit_rules(fd, spec), error = function(e) list(ok = FALSE, reason = conditionMessage(e), df1 = NA_integer_))
  if (!isTRUE(fa$ok)) return(list(ok = FALSE, reason = fa$reason %||% "infeasible", df1 = fa$df1 %||% NA_integer_))
  secs <- fa$secs
  phi_of <- function(m) {
    g <- stats::coef(m)
    v <- if (phi == "pooled") rep(unname(g["dPres"]), length(secs)) else unname(g[paste0("dPres:sector", secs)])
    stats::setNames(v, secs)
  }
  f1 <- stats::lm(rf_spec_formula(spec, "dG:sector"), data = fd)
  ph <- list(R0 = phi_of(fa$f0), R1 = phi_of(f1), R2 = phi_of(rf_offset_fit(fd, spec, fa$rules$R2)))
  if (any(vapply(ph, function(p) any(!is.finite(p)), NA))) return(list(ok = FALSE, reason = "phi not identified", df1 = fa$df1))
  list(ok = TRUE, reason = "", df1 = fa$df1, secs = secs, fa = fa, f1 = f1, phi = ph, spec = spec, fd = fd)
}

# phi of the PBL candidates: net of the levels-model slope of each rule, regress the first differences on dPres (no intercept), pooled or per industry.
rf_ph_hybrid_phi <- function(fd, fa) {
  pooled <- list(); industry <- list()
  for (rl in RF$RULES) {
    q <- fd; q$yo <- q$dy - fa$rules[[rl]][as.character(q$sector)] * q$dG
    pooled[[rl]] <- stats::setNames(rep(unname(stats::coef(stats::lm(yo ~ 0 + dPres, q))[["dPres"]]), length(fa$secs)), fa$secs)
    g <- stats::coef(stats::lm(yo ~ 0 + dPres:sector, q))
    industry[[rl]] <- stats::setNames(unname(g[paste0("dPres:sector", fa$secs)]), fa$secs)
  }
  list(pooled = pooled, industry = industry)
}

# ---- trajectory (owner ruling 2026-10-01, widened): the long-run drift of the level from past cycles ------------------------------------------------------
# Projected change = drift + phase shift + slope x growth change. No national series is local (census in REPORT.md), so the drift is the PANEL's own: per-cycle change net of
# the phase and growth terms, estimated from the training pairs either pooled across industries (a panel-common drift: a proxy for the national component, NOT a national series)
# or per industry (industry level, which includes whatever national component the industry carries). Estimators: full = mean over every training pair; rec3 = mean of the last
# three pairs before the target (a pair that ends before the target, so it is causal in both designs); damp = linear trend of the pair residuals over those earlier pairs, evaluated
# RF_PH_DAMP (0.8, fixed in advance) of a cycle step beyond the last of them (the damped extrapolation). Fewer than 3 earlier pairs (damp) or none (rec3) fall back to the full
# mean, so every candidate has the same rows. `base` G = with the slope on growth change, noG = without (double-counting test).
RF_PH_DAMP <- 0.8
RF_PH_DRIFT_KINDS <- c("full", "rec3", "damp")
RF_PH_TRAJ_DEFAULT <- "PT-G-full-pool"                        # fixed in advance: with growth, full-window mean, pooled (pooled is the default of D-065)
RF_PH_TRAJ_CANDIDATES <- as.vector(outer(c("G", "noG"), as.vector(outer(RF_PH_DRIFT_KINDS, c("pool", "ind"), paste, sep = "-")), function(b, k) paste("PT", b, k, sep = "-")))

# The 2021-22 cycle (owner-supplied finding 2026-10-01): the banks roster PAC drop 2018 -> 2022 is a pause plus an industry-wide dip, not an artefact. Handling modes of the backtests:
#   none (the cycle is used as any other), drop (pairs into and out of the cycle, and its levels, are not used to estimate anything; it stays an anchor), indicator (a common
#   level shock gamma is estimated on the training pairs -- dy ~ 0 + dG + dPres + dI, dI = I(2022) - I(2020 -> pairs ending 2024) -- the 2022 level is adjusted, predictions are de-adjusted).
RF_PH_SHOCK_CYCLE <- 2022L
rf_ph_shock_gamma <- function(tr, y, shock = RF_PH_SHOCK_CYCLE) {
  fd <- rf_ph_fd_panel(tr, y)
  if (!nrow(fd)) return(0)
  dI <- as.numeric(fd$cycle_end == shock) - as.numeric(fd$cycle_end == shock + 2L)
  if (all(dI == 0)) return(0)
  fit <- tryCatch(stats::lm(dy ~ 0 + dG + dPres + dI, cbind(fd, dI = dI)), error = function(e) NULL)
  g <- if (is.null(fit)) NA_real_ else unname(stats::coef(fit)["dI"])
  if (is.finite(g)) g else 0
}

# pairs of a panel at a gap: sector (character), cycle_end (the later cycle), dy, dG, dPres
rf_ph_pairs <- function(d, y, gap = 2L) {
  a <- d[, c("sector", "cycle_end", y, "G_current", "presidential_cycle")]
  names(a) <- c("sector", "cycle_end", "y", "G", "P")
  b <- a; b$cycle_end <- b$cycle_end + gap
  m <- merge(a, b, by = c("sector", "cycle_end"), suffixes = c("", ".lag"))
  data.frame(sector = as.character(m$sector), cycle_end = m$cycle_end, dy = m$y - m$y.lag, dG = m$G - m$G.lag, dPres = m$P - m$P.lag, stringsAsFactors = FALSE)
}

# Drift estimate for industry `s` at target `cy` from a residual table rp = data.frame(sector, cycle_end, r).
rf_ph_drift_est <- function(rp, s, cy, kind = c("full", "rec3", "damp"), scope = c("pool", "ind"), damp = RF_PH_DAMP, h = 1L) {
  kind <- match.arg(kind); scope <- match.arg(scope)
  all_ <- if (scope == "ind") rp[rp$sector == s, ] else rp
  if (!nrow(all_)) return(0)
  if (kind == "full") return(mean(all_$r))
  early <- all_[all_$cycle_end < cy, ]
  ser <- if (nrow(early)) { a <- stats::aggregate(r ~ cycle_end, early, mean); a[order(a$cycle_end), ] } else early
  if (kind == "rec3") return(if (nrow(ser)) mean(utils::tail(ser$r, 3)) else mean(all_$r))
  if (nrow(ser) < 3L) return(if (nrow(ser)) mean(ser$r) else mean(all_$r))
  k <- (ser$cycle_end - min(ser$cycle_end)) / 2
  f <- stats::lm(r ~ k, data.frame(r = ser$r, k = k))
  unname(stats::predict(f, data.frame(k = max(k) + sum(damp^seq_len(h)))))
}

# Residual tables of the two bases on the training first-difference pairs: G = pooled R0 slope and pooled phi of rf_ph_fit; noG = phi only.
rf_ph_resid_tables <- function(fp) {
  fdq <- fp$fd
  bG <- unname(fp$fa$rules$R0[[1]]); phG <- unname(fp$phi$R0[[1]])
  fN <- stats::lm(dy ~ 0 + dPres, fdq)
  phN <- unname(stats::coef(fN)[["dPres"]])
  list(G = list(b = bG, phi = phG, rp = data.frame(sector = as.character(fdq$sector), cycle_end = fdq$cycle_end, r = fdq$dy - bG * fdq$dG - phG * fdq$dPres, stringsAsFactors = FALSE)),
       noG = list(b = 0, phi = phN, vphi = stats::vcov(fN)[1, 1], rp = data.frame(sector = as.character(fdq$sector), cycle_end = fdq$cycle_end, r = fdq$dy - phN * fdq$dPres, stringsAsFactors = FALSE)))
}

# ---- 95% intervals of the backtest candidates (consistency rule, criterion c) ----------------------------------------------------------------------------------
# point +/- crit * sqrt(v_coef + sigma2_step) on the log scale: v_coef = coefficient variance of the modelled change, sigma2_step = the industry's own anchored-step residual
# variance on the training pairs (the published construction: sum(e^2) / (m - 1), crit = qt(.975, m - 1)); NA when the industry has fewer than two training pairs.
rf_ph_sg <- function(pairs, s, b, phi, dl) {
  q <- pairs[pairs$sector == s, , drop = FALSE]
  m <- nrow(q)
  if (m < 2L) return(c(sigma2 = NA_real_, crit = NA_real_))
  e <- q$dy - b * q$dG - phi * q$dPres - dl
  c(sigma2 = sum(e^2) / (m - 1), crit = stats::qt(0.975, m - 1))
}
rf_ph_hf <- function(v, sg) if (is.na(sg[["sigma2"]]) || !is.finite(v)) NA_real_ else sg[["crit"]] * sqrt(max(v, 0) + sg[["sigma2"]])
# standard error of the levels-model slope of rule rl (fa = rf_fit_rules result)
rf_ph_se_levels <- function(fa, rl, s) switch(rl, R0 = fa$se0, R1 = fa$se[[s]], R2 = sqrt(fa$W[[s]] * fa$se[[s]]^2 + (1 - fa$W[[s]])^2 * fa$se0^2))
# coefficient variance of  slope*dG + phi*dPres  under the first-difference fit fp of rule rl (fp$f2 = the R2 offset fit, set by the caller)
rf_ph_vcoef <- function(fp, rl, s, dG, dP) {
  if (rl == "R0") { V <- stats::vcov(fp$fa$f0); x <- c(dG = dG, dPres = dP); return(as.numeric(t(x) %*% V[names(x), names(x)] %*% x)) }
  if (rl == "R1") { V <- stats::vcov(fp$f1); x <- stats::setNames(c(dG, dP), c(paste0("dG:sector", s), "dPres")); return(as.numeric(t(x) %*% V[names(x), names(x)] %*% x)) }
  se_b <- sqrt(fp$fa$W[[s]] * fp$fa$se[[s]]^2 + (1 - fp$fa$W[[s]])^2 * fp$fa$se0^2)
  dG^2 * se_b^2 + dP^2 * stats::vcov(fp$f2)["dPres", "dPres"]
}

# One fold. d0 = panel (sector factor), y = series, `borrow` = the series whose slope the published general-giving rule borrows (log_pac), or NULL.
# -> list(pred = data.frame of predictions (column `half` = the 95% half-width on the log scale, NA where no interval is defined), feas = one row)
rf_ph_fold <- function(d0, y, design, cy, cycles, borrow = NULL, shock = "none") {
  trc <- if (design == "chrono") cycles[cycles < cy] else setdiff(cycles, cy)
  g22 <- 0
  if (shock == "indicator") {              # the 2021-22 cycle as a known shock: estimate the common level shock gamma on the training pairs, adjust the 2022 level, de-adjust the predictions
    d1 <- rf_restandardize(d0, trc)
    g22 <- rf_ph_shock_gamma(d1[d1$cycle_end %in% trc, ], y)
    d0[[y]] <- d0[[y]] - g22 * (d0$cycle_end == RF_PH_SHOCK_CYCLE)
  }
  trc_est <- if (shock == "drop") setdiff(trc, RF_PH_SHOCK_CYCLE) else trc          # drop: the shock cycle is not used to estimate anything (it stays an anchor)
  d <- rf_restandardize(d0, trc_est)
  tr <- d[d$cycle_end %in% trc_est, ]
  tr$sector <- droplevels(tr$sector)
  lv <- function(yy) tryCatch(rf_fit_rules(tr, list(y = yy, f = "G_current", others = "sector + factor(cycle_end)")), error = function(e) list(ok = FALSE, df1 = NA_integer_, reason = conditionMessage(e)))
  fa <- lv(y)
  fb <- if (!is.null(borrow)) lv(borrow) else NULL
  fd <- rf_ph_fd_panel(tr, y)
  fp <- if (nrow(fd)) rf_ph_fit(fd, "pooled") else list(ok = FALSE, reason = "no first-difference pairs")
  fs <- if (nrow(fd)) rf_ph_fit(fd, "industry") else list(ok = FALSE, reason = "no first-difference pairs")
  if (isTRUE(fp$ok)) fp$f2 <- rf_offset_fit(fd, fp$spec, fp$fa$rules$R2)
  hy <- if (isTRUE(fa$ok) && nrow(fd)) rf_ph_hybrid_phi(fd, fa) else NULL
  traj <- if (isTRUE(fp$ok)) rf_ph_resid_tables(fp) else NULL
  trajR <- if (isTRUE(fp$ok)) lapply(stats::setNames(c("R1", "R2"), c("R1", "R2")), function(rl) { fdq <- fp$fd; k <- as.character(fdq$sector)
    data.frame(sector = k, cycle_end = fdq$cycle_end, r = fdq$dy - fp$fa$rules[[rl]][k] * fdq$dG - fp$phi[[rl]][k] * fdq$dPres, stringsAsFactors = FALSE) }) else NULL
  p4 <- if (isTRUE(fa$ok)) rf_ph_pairs(tr, y, 4L) else NULL
  pp2 <- rf_ph_pairs(tr, y, 2L)
  ppb <- if (!is.null(borrow)) rf_ph_pairs(tr, borrow, 2L) else NULL
  # PBLT: the published levels-model slope (rule rl), a pooled phase shift and a drift, both estimated from the first differences net of that slope
  pblt <- if (isTRUE(fa$ok) && nrow(fd)) lapply(stats::setNames(RF$RULES, RF$RULES), function(rl) {
    k <- as.character(fd$sector); b <- fa$rules[[rl]]
    yo <- fd$dy - b[k] * fd$dG
    fo <- stats::lm(yo ~ 0 + dPres, data.frame(yo = yo, dPres = fd$dPres))
    ph <- unname(stats::coef(fo)[["dPres"]])
    list(b = b, phi = ph, vphi = stats::vcov(fo)[1, 1], rp = data.frame(sector = k, cycle_end = fd$cycle_end, r = yo - ph * fd$dPres, stringsAsFactors = FALSE))
  }) else NULL
  feas <- data.frame(series = y, design = design, target = cy, n_train = nrow(tr), n_pairs = nrow(fd), levels_ok = isTRUE(fa$ok), pooled_phi_ok = isTRUE(fp$ok),
                     industry_phi_ok = isTRUE(fs$ok), stringsAsFactors = FALSE)
  rows <- list()
  tg <- d[d$cycle_end == cy, ]
  ref_cycle <- rf_ref_cycle(cy, cycles)
  for (i in seq_len(nrow(tg))) {
    a <- tg[i, ]; s <- as.character(a$sector)
    a2 <- d[d$cycle_end == cy - 2 & d$sector == s, ]
    if (nrow(a2) != 1L) next
    a4 <- d[d$cycle_end == cy - 4 & d$sector == s, ]
    ar <- d[d$cycle_end == ref_cycle & d$sector == s, ]
    dG2 <- a$G_current - a2$G_current
    dG4 <- if (nrow(a4) == 1L) a$G_current - a4$G_current else NA_real_
    dP <- a$presidential_cycle - a2$presidential_cycle
    gadj <- g22 * (cy == RF_PH_SHOCK_CYCLE)
    add <- function(cand, pred, slope, phi = NA_real_, anchor = NA_real_, half = NA_real_) {
      rows[[length(rows) + 1L]] <<- data.frame(series = y, design = design, target = cy, sector = s, candidate = cand, actual = a[[y]] + gadj, pred = pred + gadj, slope = slope, phi = phi,
                                               anchor_cycle = anchor, half = half, pairs = sum(pp2$sector == s), stringsAsFactors = FALSE)
    }
    add("naive_prev", a2[[y]], NA_real_, NA_real_, cy - 2)
    if (nrow(a4) == 1L) add("anchor_only", a4[[y]], NA_real_, NA_real_, cy - 4)
    if (isTRUE(fa$ok) && s %in% fa$secs) for (rl in RF$RULES) {
      b <- fa$rules[[rl]][[s]]; se2 <- rf_ph_se_levels(fa, rl, s)^2
      add(paste0("PUB-", rl), a2[[y]] + b * dG2, b, NA_real_, cy - 2, half = rf_ph_hf(dG2^2 * se2, rf_ph_sg(pp2, s, b, 0, 0)))
      if (nrow(a4) == 1L) add(paste0("PA-", rl), a4[[y]] + b * dG4, b, NA_real_, cy - 4, half = rf_ph_hf(dG4^2 * se2, rf_ph_sg(p4, s, b, 0, 0)))
      if (nrow(ar) == 1L) add(paste0("PAfb-", rl), ar[[y]] + b * (a$G_current - ar$G_current), b, NA_real_, ref_cycle)     # fallback to t-2 when t-4 is absent (all-target sensitivity)
    }
    if (!is.null(fb) && isTRUE(fb$ok) && s %in% fb$secs) for (rl in RF$RULES) {
      b <- fb$rules[[rl]][[s]]
      add(paste0("PUBPAC-", rl), a2[[y]] + b * dG2, b, NA_real_, cy - 2, half = rf_ph_hf(dG2^2 * rf_ph_se_levels(fb, rl, s)^2, rf_ph_sg(ppb, s, b, 0, 0)))   # the console applies the PAC term, its variance included
    }
    if (isTRUE(fp$ok) && s %in% fp$secs) for (rl in RF$RULES) {
      b <- fp$fa$rules[[rl]][[s]]; ph <- fp$phi[[rl]][[s]]
      add(paste0("PB-", rl), a2[[y]] + b * dG2 + ph * dP, b, ph, cy - 2, half = rf_ph_hf(rf_ph_vcoef(fp, rl, s, dG2, dP), rf_ph_sg(pp2, s, b, ph, 0)))
    }
    if (!is.null(traj) && s %in% fp$secs) {
      for (bs in names(traj)) for (kd in RF_PH_DRIFT_KINDS) for (sc in c("pool", "ind")) {
        t <- traj[[bs]]
        dl <- rf_ph_drift_est(t$rp, s, cy, kd, sc)
        v <- if (bs == "G") rf_ph_vcoef(fp, "R0", s, dG2, dP) else dP^2 * t$vphi
        add(paste("PT", bs, kd, sc, sep = "-"), a2[[y]] + t$b * dG2 + t$phi * dP + dl, t$b, t$phi, cy - 2, half = rf_ph_hf(v, rf_ph_sg(pp2, s, t$b, t$phi, dl)))
      }
      for (rl in c("R1", "R2")) for (kd in c("full", "damp")) {                       # DE-049 for the trajectory: own / blended slopes under the same drift estimators
        b <- fp$fa$rules[[rl]][[s]]; ph <- fp$phi[[rl]][[s]]
        dl <- rf_ph_drift_est(trajR[[rl]], s, cy, kd, "pool")
        add(sprintf("PT-G-%s-pool-%s", kd, rl), a2[[y]] + b * dG2 + ph * dP + dl, b, ph, cy - 2, half = rf_ph_hf(rf_ph_vcoef(fp, rl, s, dG2, dP), rf_ph_sg(pp2, s, b, ph, dl)))
      }
      # next-cycle horizon with the default trajectory (G, full, pooled): PB2T = two chained steps (phase cancels, drift twice); PCT(lambda) as PC with the drift in both steps
      if (nrow(a4) == 1L) {
        t <- traj$G; dl <- rf_ph_drift_est(t$rp, s, cy, "full", "pool")
        add("PB2T", a4[[y]] + t$b * (a$G_current - a4$G_current) + 2 * dl, t$b, t$phi, cy - 4)
        yc0 <- a4[[y]] + t$b * (a2$G_current - a4$G_current) + t$phi * (a2$presidential_cycle - a4$presidential_cycle) + dl
        for (lam in RF_PH_LAMBDAS) add(sprintf("PCT-L%s", format(lam)), (lam * a2[[y]] + (1 - lam) * yc0) + t$b * dG2 + t$phi * dP + dl, t$b, t$phi, cy - 2)
      }
    }
    if (!is.null(p4) && isTRUE(fa$ok) && s %in% fa$secs && nrow(a4) == 1L) for (rl in RF$RULES) {          # PA with its own drift (mean of the four-year pair residuals, pooled)
      b <- fa$rules[[rl]][[s]]
      dl4 <- if (nrow(p4)) mean(p4$dy - fa$rules[[rl]][p4$sector] * p4$dG) else 0
      add(paste0("PA-", rl, "-T"), a4[[y]] + b * dG4 + dl4, b, NA_real_, cy - 4, half = rf_ph_hf(dG4^2 * rf_ph_se_levels(fa, rl, s)^2, rf_ph_sg(p4, s, b, 0, dl4)))
    }
    if (isTRUE(fs$ok) && s %in% fs$secs) for (rl in RF$RULES) {
      b <- fs$fa$rules[[rl]][[s]]; ph <- fs$phi[[rl]][[s]]
      add(paste0("PB-", rl, "-phiS"), a2[[y]] + b * dG2 + ph * dP, b, ph, cy - 2)
    }
    # next-cycle horizon (target = the cycle AFTER the open cycle c = cy - 2; the last complete cycle is cy - 4): PB2 = two chained PB steps from cy - 4 (the phase terms cancel);
    # PC(lambda) = start from a projected value of the open cycle that has absorbed a share lambda of its error (lambda = 1: the actual open-cycle total, the upper bound that the
    # historical data can test; lambda = 0: no information beyond the anchor, which IS PB2), then one PB step to the target.
    if (isTRUE(fp$ok) && s %in% fp$secs && nrow(a4) == 1L) {
      for (rl in RF$RULES) add(paste0("PB2-", rl), a4[[y]] + fp$fa$rules[[rl]][[s]] * (a$G_current - a4$G_current), fp$fa$rules[[rl]][[s]], NA_real_, cy - 4)
      b <- fp$fa$rules$R0[[s]]; ph <- fp$phi$R0[[s]]
      yc0 <- a4[[y]] + b * (a2$G_current - a4$G_current) + ph * (a2$presidential_cycle - a4$presidential_cycle)
      for (lam in RF_PH_LAMBDAS) {
        yc <- lam * a2[[y]] + (1 - lam) * yc0
        add(sprintf("PC-R0-L%s", format(lam)), yc + b * dG2 + ph * dP, b, ph, cy - 2)
      }
    }
    if (!is.null(hy) && s %in% names(hy$pooled$R0)) for (rl in RF$RULES) {
      b <- fa$rules[[rl]][[s]]
      add(paste0("PBL-", rl), a2[[y]] + b * dG2 + hy$pooled[[rl]][[s]] * dP, b, hy$pooled[[rl]][[s]], cy - 2)
      if (!is.na(hy$industry[[rl]][s])) add(paste0("PBL-", rl, "-phiS"), a2[[y]] + b * dG2 + hy$industry[[rl]][[s]] * dP, b, hy$industry[[rl]][[s]], cy - 2)
    }
    if (!is.null(pblt) && isTRUE(fa$ok) && s %in% fa$secs) for (rl in RF$RULES) {
      q <- pblt[[rl]]; b <- q$b[[s]]; se2 <- rf_ph_se_levels(fa, rl, s)^2
      for (kd in RF_PH_DRIFT_KINDS) for (sc in c("pool", "ind")) {
        dl <- rf_ph_drift_est(q$rp, s, cy, kd, sc)
        add(paste("PBLT", rl, kd, sc, sep = "-"), a2[[y]] + b * dG2 + q$phi * dP + dl, b, q$phi, cy - 2, half = rf_ph_hf(dG2^2 * se2 + dP^2 * q$vphi, rf_ph_sg(pp2, s, b, q$phi, dl)))
      }
    }
  }
  list(pred = if (length(rows)) do.call(rbind, rows) else NULL, feas = feas)
}

# Backtest of one series in one design -> predictions with attr "feasibility". Targets = every cycle after the first (the refit's rule).
rf_ph_backtest <- function(panel, y, design = c("loeo", "chrono"), borrow = NULL, shock = c("none", "drop", "indicator")) {
  design <- match.arg(design); shock <- match.arg(shock)
  d0 <- panel
  d0$sector <- factor(d0$sector)
  cycles <- sort(unique(d0$cycle_end))
  folds <- rf_pmap(cycles[-1], function(cy) rf_ph_fold(d0, y, design, cy, cycles, borrow, shock))
  pred <- do.call(rbind, lapply(folds, `[[`, "pred"))
  if (shock != "none") pred$candidate <- paste0(pred$candidate, "@", c(drop = "x22", indicator = "i22")[[shock]])
  pred$err <- (pred$pred - pred$actual) * 100                      # log points x 100, as the refit's PAC cells
  attr(pred, "feasibility") <- do.call(rbind, lapply(folds, `[[`, "feas"))
  pred
}

# MAE of two candidates on the (target, sector) rows both have, in one design.
rf_ph_pair <- function(pred, cand, default, design) {
  a <- pred[pred$design == design & pred$candidate == cand, ]
  b <- pred[pred$design == design & pred$candidate == default, ]
  k <- intersect(paste(a$target, a$sector), paste(b$target, b$sector))
  a <- a[match(k, paste(a$target, a$sector)), ]; b <- b[match(k, paste(b$target, b$sector)), ]
  list(n = length(k), mae_c = if (length(k)) mean(abs(a$err)) else NA_real_, mae_d = if (length(k)) mean(abs(b$err)) else NA_real_, keys = k)
}

# Per candidate x design: own feasible rows (n, targets, MAE, RMSE, equal-weight industry MAE) and, against the default, the rows both have
# (n_common, MAE of both, relative gain 1 - MAE_c / MAE_default, and whether the gain clears min_gain).
rf_ph_metrics <- function(pred, default = RF_PH_DEFAULT, min_gain = RF$RULE_MIN_GAIN, tol = RF$RULE_TIE_TOL) {
  out <- list()
  for (ds in unique(pred$design)) for (cand in unique(pred$candidate)) {
    q <- pred[pred$design == ds & pred$candidate == cand, ]
    if (!nrow(q)) next
    pr <- rf_ph_pair(pred, cand, default, ds)
    gain <- if (is.finite(pr$mae_d) && pr$mae_d > 0) 1 - pr$mae_c / pr$mae_d else NA_real_
    out[[length(out) + 1L]] <- data.frame(design = ds, candidate = cand, n = nrow(q), targets = paste(range(q$target), collapse = "-"), n_targets = length(unique(q$target)),
      MAE = mean(abs(q$err)), RMSE = sqrt(mean(q$err^2)), EW_MAE = mean(tapply(abs(q$err), q$sector, mean)), n_common = pr$n, MAE_common = pr$mae_c, MAE_default_common = pr$mae_d,
      gain_vs_default = gain, clears = !is.na(gain) && pr$mae_c < pr$mae_d - tol && pr$mae_c <= pr$mae_d * (1 - min_gain) + tol, stringsAsFactors = FALSE)
  }
  r <- do.call(rbind, out)
  rownames(r) <- NULL
  r
}

# D-065 among the phase-aware candidates: the default stays unless a candidate clears min_gain in EVERY design on the rows it shares with the default.
# Several qualifiers: the lowest mean (over designs) MAE on the rows common to all qualifiers and the default. -> list(choice, qualifying, table, reason)
rf_ph_select <- function(pred, candidates = RF_PH_CANDIDATES, default = RF_PH_DEFAULT, min_gain = RF$RULE_MIN_GAIN, tol = RF$RULE_TIE_TOL, designs = c("loeo", "chrono")) {
  m <- rf_ph_metrics(pred, default, min_gain, tol)
  m <- m[m$candidate %in% candidates, ]
  qual <- Filter(function(cd) all(vapply(designs, function(ds) { r <- m[m$candidate == cd & m$design == ds, ]; nrow(r) == 1L && isTRUE(r$clears) }, NA)), setdiff(candidates, default))
  gains <- function(cd) paste(vapply(designs, function(ds) { r <- m[m$candidate == cd & m$design == ds, ]; if (nrow(r) && !is.na(r$gain_vs_default)) sprintf("%s %+.1f%%", ds, 100 * r$gain_vs_default) else sprintf("%s n/a", ds) }, ""), collapse = ", ")
  if (!length(qual)) {
    return(list(choice = default, qualifying = character(), table = m,
                reason = sprintf("%s kept: no candidate beats it by %g%% in both designs (best gains: %s)", default, 100 * min_gain,
                                 paste(vapply(setdiff(candidates, default), function(cd) paste0(cd, " [", gains(cd), "]"), ""), collapse = "; "))))
  }
  score <- vapply(qual, function(cd) mean(vapply(designs, function(ds) {
    keys <- Reduce(intersect, c(list(rf_ph_pair(pred, default, default, ds)$keys), lapply(c(qual, default), function(z) rf_ph_pair(pred, z, default, ds)$keys)))
    a <- pred[pred$design == ds & pred$candidate == cd, ]
    mean(abs(a$err[match(keys, paste(a$target, a$sector))]))
  }, 0)), 0)
  ch <- names(score)[which.min(score)]
  list(choice = ch, qualifying = qual, table = m, score = score,
       reason = sprintf("%s replaces %s: beats it by %g%% or more in both designs (%s); qualifiers: %s", ch, default, 100 * min_gain, gains(ch), paste(qual, collapse = ", ")))
}

# DE-049: the slope-rule comparison (R0 pooled / R1 own / R2 blend; >= min_gain in every design) inside each structure family, on the rows the three rules share.
rf_ph_slope_rules <- function(pred, families = RF_PH_FAMILIES, min_gain = RF$RULE_MIN_GAIN, designs = c("loeo", "chrono")) {
  res <- list(); tabs <- list()
  for (fam in names(families)) {
    cands <- families[[fam]]
    tab <- do.call(rbind, lapply(designs, function(ds) {
      keys <- Reduce(intersect, lapply(cands, function(cd) { q <- pred[pred$design == ds & pred$candidate == cd, ]; paste(q$target, q$sector) }))
      do.call(rbind, lapply(cands, function(cd) { q <- pred[pred$design == ds & pred$candidate == cd, ]; q <- q[match(keys, paste(q$target, q$sector)), ]
        data.frame(family = fam, design = ds, rule = sub("^.*-(R[012]).*$", "\\1", cd), candidate = cd, n = length(keys), MAE = mean(abs(q$err)), stringsAsFactors = FALSE) }))
    }))
    ok <- all(tab$n > 0)
    ch <- if (ok) rf_choose_rule(tab[, c("design", "rule", "MAE")], designs, min_gain) else list(choice = NA_character_, reason = "no common rows")
    res[[fam]] <- list(choice = ch$choice, reason = ch$reason)
    tabs[[fam]] <- tab
  }
  list(table = do.call(rbind, tabs), choice = res)
}

# Per-target (election) MAE over industries: candidates as columns, one row per design x target.
rf_ph_per_target <- function(pred, candidates) {
  q <- pred[pred$candidate %in% candidates, ]
  q$ae <- abs(q$err)
  a <- stats::aggregate(ae ~ design + target + candidate, q, mean)
  w <- stats::reshape(a, idvar = c("design", "target"), timevar = "candidate", direction = "wide")
  names(w) <- sub("^ae\\.", "", names(w))
  w[order(w$design, w$target), ]
}

# Share of leave-one-election-out fold fits whose phi keeps the sign of the full-sample phi (pooled phi, R0): the sign-consistency of the phase effect.
rf_ph_phi_folds <- function(panel, y) {
  d0 <- panel; d0$sector <- factor(d0$sector)
  cycles <- sort(unique(d0$cycle_end))
  rows <- lapply(cycles, function(cy) {
    d <- rf_restandardize(d0, setdiff(cycles, cy)); tr <- d[d$cycle_end != cy, ]; tr$sector <- droplevels(tr$sector)
    fd <- rf_ph_fd_panel(tr, y)
    f <- rf_ph_fit(fd, "pooled")
    if (!isTRUE(f$ok)) return(NULL)
    data.frame(left_out = cy, beta = unname(f$fa$b0), phi = unname(f$phi$R0[1]), stringsAsFactors = FALSE)
  })
  do.call(rbind, rows)
}

# ---- full-sample parameters, intervals and projections ---------------------------------------------------------------------------------------------------

# Parameters of one candidate on the full panel (growth scaling as supplied = the bundle's full-sample scaling).
# candidate "PUB-R1" / "PA-R1" / "PB-R0" / "PB-R1-phiS" / "PBL-R2" ...; PUBPAC-* takes its slope from `borrow` (log_pac) and applies it to y.
# -> list(candidate, structure, rule, phi_mode, secs, slope, slope_se, phi, phi_se, gap, V = function(sector, dG, dP) coefficient variance of the modelled change)
# Variances: PUB/PA dG^2 se^2; PB the model covariance (R0: pooled fit, R1: own-slope fit; R2: DL slope variance + phi variance, covariance ignored);
# PBL dG^2 se_levels^2 + dP^2 var(phi), covariance ignored (stated in the report).
rf_ph_params <- function(panel, y, candidate, borrow = NULL) {
  panel$sector <- factor(panel$sector)
  if (grepl("^PT-", candidate)) return(rf_ph_params_traj(panel, y, candidate))
  m <- regmatches(candidate, regexec("^(PUBPAC|PUB|PAfb|PA|PBL|PB)-(R[012])(-phiS)?$", candidate))[[1]]
  if (!length(m)) stop("rf_ph_params: unknown candidate ", candidate, call. = FALSE)
  st <- m[2]; rl <- m[3]; phiS <- nzchar(m[4])
  lvspec <- function(yy) list(y = yy, f = "G_current", others = "sector + factor(cycle_end)")
  se_of <- function(fa) switch(rl, R0 = stats::setNames(rep(fa$se0, length(fa$secs)), fa$secs), R1 = fa$se,
                               R2 = sqrt(fa$W * fa$se^2 + (1 - fa$W)^2 * fa$se0^2))
  out <- list(candidate = candidate, structure = st, rule = rl, phi_mode = if (phiS) "industry" else "pooled")
  if (st %in% c("PUB", "PUBPAC", "PA", "PAfb")) {
    fa <- rf_fit_rules(panel, lvspec(if (st == "PUBPAC") borrow else y))
    out <- c(out, list(secs = fa$secs, slope = fa$rules[[rl]], slope_se = se_of(fa), phi = stats::setNames(rep(0, length(fa$secs)), fa$secs), phi_se = stats::setNames(rep(0, length(fa$secs)), fa$secs),
                       gap = if (st %in% c("PA", "PAfb")) 4L else 2L, fa = fa, df = if (rl == "R0") fa$df0 else fa$df1))
    out$V <- local({ se <- out$slope_se; function(sector, dG, dP) dG^2 * se[[sector]]^2 })
    return(out)
  }
  fd <- rf_ph_fd_panel(panel, y)
  if (st == "PB") {
    fp <- rf_ph_fit(fd, if (phiS) "industry" else "pooled")
    secs <- fp$secs
    sl <- fp$fa$rules[[rl]]; ph <- fp$phi[[rl]]
    f <- switch(rl, R0 = fp$fa$f0, R1 = fp$f1, R2 = rf_offset_fit(fd, fp$spec, fp$fa$rules$R2))
    V <- stats::vcov(f)
    se_b <- se_of(fp$fa)
    ph_se <- stats::setNames(vapply(secs, function(s) { k <- if (phiS) paste0("dPres:sector", s) else "dPres"; sqrt(V[k, k]) }, 0), secs)
    vf <- function(sector, dG, dP) {
      if (rl == "R2") return(dG^2 * se_b[[sector]]^2 + dP^2 * ph_se[[sector]]^2)
      kb <- if (rl == "R0") "dG" else paste0("dG:sector", sector)
      kp <- if (phiS) paste0("dPres:sector", sector) else "dPres"
      x <- stats::setNames(c(dG, dP), c(kb, kp))
      as.numeric(t(x) %*% V[names(x), names(x)] %*% x)
    }
    return(c(out, list(secs = secs, slope = sl, slope_se = se_b, phi = ph, phi_se = ph_se, gap = 2L, V = vf, fa = fp$fa, fit = f, df = if (rl == "R0") fp$fa$df0 else fp$fa$df1)))
  }
  # PBL: published levels slope, phi net of it
  fa <- rf_fit_rules(panel, lvspec(y))
  se_b <- se_of(fa)
  q <- fd; q$yo <- q$dy - fa$rules[[rl]][as.character(q$sector)] * q$dG
  fo <- if (phiS) stats::lm(yo ~ 0 + dPres:sector, q) else stats::lm(yo ~ 0 + dPres, q)
  g <- stats::coef(fo); Vp <- stats::vcov(fo)
  secs <- fa$secs
  ph <- stats::setNames(vapply(secs, function(s) unname(if (phiS) g[paste0("dPres:sector", s)] else g[["dPres"]]), 0), secs)
  ph_se <- stats::setNames(vapply(secs, function(s) { k <- if (phiS) paste0("dPres:sector", s) else "dPres"; sqrt(Vp[k, k]) }, 0), secs)
  c(out, list(secs = secs, slope = fa$rules[[rl]], slope_se = se_b, phi = ph, phi_se = ph_se, gap = 2L, fa = fa, fit = fo, df = if (rl == "R0") fa$df0 else fa$df1,
              V = function(sector, dG, dP) dG^2 * se_b[[sector]]^2 + dP^2 * ph_se[[sector]]^2))
}

# Anchored-step residual variance of a candidate per industry on the full panel (the published construction: sum(e^2) / (m - 1) over that industry's pairs,
# crit = qt(.975, m - 1)); gap 4 for PA (pairs (t-4, t)), gap 2 otherwise. -> data.frame(sector, sigma2, pairs, crit)
rf_ph_step_variance <- function(panel, y, pr) {
  panel$sector <- factor(panel$sector)
  gap <- pr$gap
  do.call(rbind, lapply(pr$secs, function(s) {
    d <- panel[panel$sector == s, ]
    e <- numeric()
    for (t in d$cycle_end) {
      a <- d[d$cycle_end == t, ]; b <- d[d$cycle_end == t - gap, ]
      if (nrow(b) != 1L) next
      dP <- if (gap == 2L) a$presidential_cycle - b$presidential_cycle else 0
      dr <- if (!is.null(pr$drift)) pr$drift(s, 1L) else 0
      e <- c(e, (a[[y]] - b[[y]]) - (pr$slope[[s]] * (a$G_current - b$G_current) + pr$phi[[s]] * dP + dr))
    }
    m <- length(e)
    data.frame(sector = s, sigma2 = if (m >= 2L) sum(e^2) / (m - 1) else NA_real_, pairs = m, crit = if (m >= 2L) stats::qt(0.975, m - 1) else NA_real_, stringsAsFactors = FALSE)
  }))
}

# Anchored-step residual variance of an industry SCORED on the pooled model without being estimated in it (registry `estimation: "scored_only"`; Telecom, owner ruling 2026-10-08): the same
# construction as rf_ph_step_variance, on the scored panel, with the pooled phase coefficient and the pooled drift of the fitted candidate and no growth term. Only the pooled no-growth
# trajectory candidates (PT-noG-*-pool) are defined outside the pool (their slope is zero and their phi and drift do not depend on the industry), and only with the 2022 handling none / drop.
# Nothing in the pooled fit changes, so every other industry's parameters, residuals and step variance stay exactly what they were. -> data.frame(sector, sigma2, pairs, crit)
rf_ph_scored_step_variance <- function(scored_panel, y, pr, handling = "none") {
  if (!identical(pr$base, "noG") || !identical(pr$drift_scope, "pool") || !identical(pr$gap, 2L))
    stop("rf_ph_scored_step_variance: only the pooled no-growth trajectory candidates (PT-noG-*-pool) can score an industry outside the pool; the selected candidate is ", pr$candidate, call. = FALSE)
  if (!handling %in% c("none", "x22")) stop("rf_ph_scored_step_variance: the 2022 handling '", handling, "' needs the pool (only none and drop are defined for a scored industry)", call. = FALSE)
  sp <- rf_ph_panel_handling(scored_panel, y, handling)
  sp$sector <- factor(sp$sector)
  secs <- levels(sp$sector)
  pr$secs <- secs
  pr$slope <- stats::setNames(rep(0, length(secs)), secs)
  pr$phi <- stats::setNames(rep(unname(pr$phi[[1]]), length(secs)), secs)
  rf_ph_step_variance(sp, y, pr)
}

# Projection of one industry's level over a chain of cycles (anchor -> ... -> target). `G` = named numeric of the standardized growth of every cycle in the chain
# (anchor included); `chain` = cycle vector anchor, ..., target. A same-phase candidate (PA) takes ONE four-year step (k = 2 cycle steps of variance: the empirical
# gap-4 residual variance already contains both); the others chain two-year steps (phase terms cancel over a full phase cycle) with k x sigma2.
# -> c(log_change, coef_half, pred_half, k)
rf_ph_chain <- function(pr, sv, sector, chain, G, h0 = 0L, gz = NULL) {
  stopifnot(length(chain) >= 2L)
  steps <- if (pr$gap == 4L) list(c(chain[1], chain[length(chain)])) else lapply(seq_len(length(chain) - 1L), function(i) chain[c(i, i + 1L)])
  v <- 0; ch <- 0
  if (!is.null(pr$drift)) for (i in seq_along(steps)) ch <- ch + pr$drift(sector, h0 + i)            # trajectory: the drift of each two-year step
  for (st in steps) {
    dG <- G[[as.character(st[2])]] - G[[as.character(st[1])]]
    dP <- as.numeric(st[2] %% 4 == 0) - as.numeric(st[1] %% 4 == 0)
    ch <- ch + pr$slope[[sector]] * dG + pr$phi[[sector]] * dP
  }
  # coefficient variance: one common coefficient (the slope is the same across the steps), so the dG and dP of the chain add before squaring
  dGt <- sum(vapply(steps, function(st) G[[as.character(st[2])]] - G[[as.character(st[1])]], 0))
  dPt <- sum(vapply(steps, function(st) as.numeric(st[2] %% 4 == 0) - as.numeric(st[1] %% 4 == 0), 0))
  v <- pr$V(sector, dGt, dPt)
  # projection error of the growth input (IBIS report section 4): extra log variance = slope^2 x (z_first^2 + z_last^2), z = the RMSE of the projected cycle growth in standardized units
  # at the cycle's horizon (`gz`, named by cycle; a cycle that is not in gz carries no projection error). The chained steps share one slope, so only the ends of the chain count.
  if (!is.null(gz) && pr$gap == 2L) {
    zl <- function(cy) { z <- gz[as.character(cy)]; if (is.na(z)) 0 else unname(z) }
    v <- v + pr$slope[[sector]]^2 * (zl(chain[length(chain)])^2 + zl(chain[1])^2)
  }
  s <- sv[sv$sector == sector, ]
  k <- length(steps)
  kk <- if (pr$gap == 4L) 1L else k                                   # k x sigma2 (the published construction: k chained steps)
  c(log_change = ch, coef_half = stats::qt(0.975, pr$df) * sqrt(v), pred_half = s$crit * sqrt(v + kk * s$sigma2), k = k)
}

# ---- the Senate share model with the presidential-cycle term (owner ruling 2026-10-01) -------------------------------------------------------------------

RF_SENATE_V2_FORMULA <- "RShare ~ sector + RMajNow + P_R + G_prior + Pres"

# The v2 layer of a parity context: the Senate pooled control fit carries Pres (the House model already does). The frozen v1 fit stays in the frozen context.
# Idempotent. The selection-repeated p-value of the stepwise grid belongs to the frozen v1 selection (mask 3, without Pres) and is carried with that label.
rf_phase_ctx <- function(ctx) {
  if (isTRUE(ctx$senate_phase)) return(ctx)
  d <- ctx$fits_data$control_Senate
  yrs <- range(d$cycle_end)
  ctx$fits$control_Senate_v1 <- ctx$fits$control_Senate
  ctx$fits$control_Senate <- rf_lm(RF_SENATE_V2_FORMULA, d, sprintf("control_Senate_%d_%d", yrs[1], yrs[2]))
  ctx$senate_phase <- TRUE
  ctx
}

# ---- the phase block of a v2 context ---------------------------------------------------------------------------------------------------------------------------

# The core candidates compared across the 2022-shock handlings.
RF_PH_SHOCK_CORE <- c("PB-R0", "PUB-R1", "PA-R1", "PA-R0", "PT-G-damp-pool", "PT-noG-damp-pool", "PT-G-full-pool", "PT-noG-full-pool", "PT-G-rec3-pool", "PT-noG-rec3-pool", "PB2T", "PCT-L0.5", "naive_prev")

# D-065 for the 2022 handling of one candidate: `drop` or `indicator` replaces no handling only if it beats it by min_gain in both designs.
# -> list(handling = "none" | "x22" | "i22", table)
rf_ph_shock_choice <- function(pred, cand, min_gain = RF$RULE_MIN_GAIN, tol = RF$RULE_TIE_TOL) {
  m <- rf_ph_metrics(pred, cand, min_gain, tol)
  rows <- lapply(c("x22", "i22"), function(h) {
    q <- m[m$candidate == paste0(cand, "@", h), ]
    data.frame(candidate = cand, handling = h, design = q$design, MAE_none = q$MAE_default_common, MAE_handled = q$MAE_common, gain = q$gain_vs_default, clears = q$clears,
               n_common = q$n_common, stringsAsFactors = FALSE)
  })
  tab <- do.call(rbind, rows)
  win <- Filter(function(h) { r <- tab[tab$handling == h, ]; nrow(r) == 2L && all(r$clears) }, c("x22", "i22"))
  ch <- if (!length(win)) "none" else win[which.min(vapply(win, function(h) mean(tab$MAE_handled[tab$handling == h]), 0))]
  list(handling = ch, table = tab)
}

rf_ph_split_handling <- function(x) list(id = sub("@.*$", "", x), handling = if (grepl("@x22", x, fixed = TRUE)) "x22" else if (grepl("@i22", x, fixed = TRUE)) "i22" else "none")
rf_ph_panel_handling <- function(pac, y, h) {
  if (h == "x22") return(pac[pac$cycle_end != RF_PH_SHOCK_CYCLE, ])
  if (h == "i22") { p <- pac; p[[y]] <- p[[y]] - rf_ph_shock_gamma(pac, y) * (p$cycle_end == RF_PH_SHOCK_CYCLE); return(p) }
  pac
}

# Backtests (both designs, three 2022 handlings, 95% intervals), the consistency rule against the published form (rf_cons_select: the owner-mandated family, D-065 plus per-industry
# and coverage criteria), DE-049 slope-rule tables, the earlier within-family D-065 selections and the full-sample parameters of the selected specification, of its growth-retaining
# reading and of the published form for the PAC and the general-giving series. `today_rule` = the published PAC slope rule (the reference PUB-<rule> / PUBPAC-<rule>).
#   structure grid (PA / PB / PBL), trajectory grid (PT: drift x growth term), PBLT (published levels slope + phase + drift), the 2022 handling, the next-cycle horizon (PC / PCT brackets).
rf_phase_block <- function(ctx, today_rule = "R1", shocks = c("none", "drop", "indicator")) {
  pac <- ctx$pac_panel
  pac$sector <- factor(pac$sector)
  out <- list()
  for (nm in names(RF_PH_SERIES)) {
    y <- RF_PH_SERIES[[nm]]
    borrow <- if (nm == "general") "log_pac" else NULL
    bts <- lapply(shocks, function(sh) lapply(c("loeo", "chrono"), function(ds) rf_ph_backtest(pac, y, ds, borrow, sh)))
    pred_all <- do.call(rbind, unlist(bts, recursive = FALSE))
    pred <- pred_all[!grepl("@", pred_all$candidate, fixed = TRUE), ]
    feas <- do.call(rbind, lapply(bts[[1]], attr, "feasibility"))
    sel <- rf_ph_select(pred)
    sel_ext <- rf_ph_select(pred, RF_PH_CANDIDATES_EXT)
    sr <- rf_ph_slope_rules(pred, RF_PH_FAMILIES_EXT)
    tsel <- rf_ph_select(pred, RF_PH_TRAJ_CANDIDATES, RF_PH_TRAJ_DEFAULT)                     # the earlier single-stage D-065 selection against the fixed default (kept as evidence)
    wg <- grep("^PT-G-", RF_PH_TRAJ_CANDIDATES, value = TRUE)
    s1 <- rf_ph_select(pred, wg, RF_PH_TRAJ_DEFAULT)
    m1 <- rf_ph_metrics(pred, s1$choice)
    nog <- sub("^PT-G-", "PT-noG-", s1$choice)
    gdrop <- m1[m1$candidate == nog, c("design", "candidate", "MAE", "MAE_default_common", "gain_vs_default", "clears")]
    alt <- if (all(gdrop$clears)) nog else s1$choice
    # the consistency rule against the published form
    base_cand <- if (nm == "pac") paste0("PUB-", today_rule) else paste0("PUBPAC-", today_rule)
    fam <- rf_cons_family(unique(pred_all$candidate))
    csel <- rf_cons_select(pred_all, base_cand, fam)
    cselG <- rf_cons_select(pred_all, base_cand, fam[grepl("^PT-G-", fam)])
    pick <- rf_ph_split_handling(csel$choice); pickG <- rf_ph_split_handling(cselG$choice)
    if (!grepl("^PT-", pick$id) || !grepl("^PT-", pickG$id)) stop("rf_phase_block: the consistency rule picked a candidate the projection layer cannot run (", csel$choice, " / ", cselG$choice, "); implement its parameters first", call. = FALSE)
    h <- pick$handling
    shock <- if (length(shocks) > 1L) rf_ph_shock_choice(pred_all, pick$id) else list(handling = "none", table = NULL)
    panel_h <- rf_ph_panel_handling(pac, y, h)
    params <- rf_ph_params(panel_h, y, pick$id, borrow)
    params_g <- rf_ph_params(rf_ph_panel_handling(pac, y, pickG$handling), y, pickG$id, borrow)
    sv <- rf_ph_step_variance(panel_h, y, params)
    if (!is.null(ctx$scored)) sv <- rbind(sv, rf_ph_scored_step_variance(ctx$scored$panel, y, params, h))         # scored-only industries (Telecom): rows beside the pool's, which are untouched
    shock_cmp <- if (length(shocks) > 1L) do.call(rbind, lapply(RF_PH_SHOCK_CORE, function(cd) rf_ph_shock_choice(pred_all, cd)$table)) else NULL
    out[[nm]] <- list(series = y, pred = pred_all, feasibility = feas, metrics = rf_ph_metrics(pred), selection = sel, selection_ext = sel_ext, slope_rules = sr,
                      trajectory = list(single = tsel, stage1 = s1, growth_drop = gdrop, growth_retaining = alt, default = RF_PH_TRAJ_DEFAULT),
                      consistency = list(baseline = base_cand, selection = csel, growth_retaining = cselG), shock = shock, shock_cmp = shock_cmp,
                      spec = list(candidate = pick$id, handling = h, label = csel$choice), spec_growth = list(candidate = pickG$id, handling = pickG$handling, label = cselG$choice),
                      params = params, params_growth = params_g, step_variance = sv,
                      step_variance_growth = rf_ph_step_variance(rf_ph_panel_handling(pac, y, pickG$handling), y, params_g),
                      today = base_cand, phi_folds = rf_ph_phi_folds(pac, y),
                      next_cycle = rf_ph_select_next(pred), next_cycle_trajectory = rf_ph_select_next(pred, RF_PH_NEXT_CHALLENGERS_T, trajectory = TRUE))
  }
  out
}

# The JSON-able evidence record of a phase block (written into pac_model.evidence.phase: the contract's open evidence object; the calculator does not read it).
rf_v2_phase_evidence <- function(v) {
  ph <- v$phase
  par_rec <- function(p, h, sv) list(candidate = p$candidate, handling_2022 = h, anchor = "previous_cycle", slope_rule = p$rule, phi_mode = p$phi_mode,
                                     slopes = as.list(p$slope), slope_se = as.list(p$slope_se), phi = as.list(p$phi), phi_se = as.list(p$phi_se),
                                     drift_per_cycle = as.list(if (!is.null(p$drift_by_industry)) p$drift_by_industry else stats::setNames(rep(0, length(p$secs)), p$secs)),
                                     step_variance = rf_records(sv, ints = "pairs"))
  cons_rec <- function(b) {
    cs <- b$consistency$selection; csG <- b$consistency$growth_retaining; bs <- b$consistency$baseline
    vd <- cs$verdict[order(cs$verdict$mean_MAE), ]
    vd <- utils::head(vd, 30)
    show <- unique(c(bs, cs$choice, csG$choice))
    pt <- cs$pooled[cs$pooled$candidate %in% show, c("candidate", "design", "n_common", "MAE", "MAE_base", "gain", "coverage", "bias")]
    it <- cs$industry[cs$industry$candidate %in% show, c("candidate", "design", "sector", "n", "MAE", "MAE_base", "ratio", "bias")]
    list(rule = paste0("a candidate replaces the published rule only if (a) pooled MAE >= ", 100 * RF_CONS$min_gain, "% lower in BOTH designs, (b) in EVERY industry MAE <= ", 100 * (1 + RF_CONS$ind_tol),
                       "% of the published rule's in both designs, (c) 95% coverage >= ", 100 * RF_CONS$cov_min, "% in both designs; the owner-mandated components (phase, trajectory, inflation) stay in: a failing family is a CONFLICT carried with both versions"),
         baseline = bs, status = cs$status, selected = cs$choice, reason = cs$reason, qualifying = as.list(cs$qualifying), mandated_family_size = nrow(cs$verdict),
         growth_retaining = list(selected = csG$choice, status = csG$status, reason = csG$reason),
         top_candidates = rf_records(vd[, c("candidate", "a_pooled5", "b_every_industry", "c_coverage90", "qualifies", "gain_loeo", "gain_chrono", "cov_loeo", "cov_chrono", "worst_ratio", "worst_industry", "worst_design", "n_industries_worse")]),
         pooled = rf_records(pt, ints = "n_common"), by_industry = rf_records(it, ints = "n"))
  }
  ser <- function(nm) {
    b <- ph[[nm]]; m <- b$metrics; p <- b$params
    keep <- m[m$candidate %in% c(RF_PH_CANDIDATES_EXT, b$today, "naive_prev", "anchor_only"), c("design", "candidate", "n", "MAE", "RMSE", "n_common", "MAE_common", "MAE_default_common", "gain_vs_default")]
    mt <- rf_ph_metrics(b$pred[!grepl("@", b$pred$candidate, fixed = TRUE), ], RF_PH_TRAJ_DEFAULT)
    tk <- mt[mt$candidate %in% c(RF_PH_TRAJ_CANDIDATES, "PB-R0"), c("design", "candidate", "n", "MAE", "RMSE", "n_common", "MAE_common", "MAE_default_common", "gain_vs_default")]
    list(series = b$series, default = RF_PH_DEFAULT, selected = b$consistency$selection$choice, selection_status = b$consistency$selection$status, selection_reason = b$consistency$selection$reason,
         d065_within_family_selected = b$selection$choice, d065_within_family_reason = b$selection$reason,
         supplementary_candidate = b$selection_ext$choice, supplementary_reason = b$selection_ext$reason,
         published_reference = b$today, designs = list("loeo", "chrono"), min_gain = RF$RULE_MIN_GAIN,
         consistency = cons_rec(b),
         candidates = rf_records(keep, ints = c("n", "n_common")),
         slope_rule_by_family = lapply(b$slope_rules$choice, function(x) list(choice = x$choice, reason = x$reason)),
         trajectory = list(default = b$trajectory$default, selected_single_stage = b$trajectory$single$choice, reason = b$trajectory$single$reason,
                           growth_retaining_reading = b$trajectory$growth_retaining,
                           national_series = "none local: the drift is the panel's own (pooled = panel-common proxy, per-industry), see the report census",
                           candidates = rf_records(tk, ints = c("n", "n_common"))),
         shock_2022 = list(cycle = RF_PH_SHOCK_CYCLE, handling_selected = b$spec$handling,
                           rule = "the handling is part of the mandated family's HOW: each (candidate x handling) is scored against the published form by the consistency rule",
                           table = if (is.null(b$shock$table)) list() else rf_records(b$shock$table, ints = "n_common")),
         selected_parameters = par_rec(p, b$spec$handling, b$step_variance),
         growth_retaining_parameters = par_rec(b$params_growth, b$spec_growth$handling, b$step_variance_growth),
         phi_loeo_sign_consistency = mean(sign(b$phi_folds$phi) == sign(unname(p$phi[1]))),
         next_cycle_horizon = list(default = "PC (start from the conditioned open-cycle projection, then one step)", challengers = as.list(RF_PH_NEXT_CHALLENGERS),
                                   break_even_lambda = if (is.na(b$next_cycle$break_even)) NULL else b$next_cycle$break_even,
                                   break_even_lambda_trajectory = if (is.na(b$next_cycle_trajectory$break_even)) NULL else b$next_cycle_trajectory$break_even,
                                   scope = paste("The month-18 conditioning cannot be backtested by this table (the OpenSecrets panel has no timing; the roster pace test covers banks only). lambda = 1 is the upper bound",
                                                 "(the actual open-cycle total is the start; PC = one step from it); lambda < 1 shrinks the start's error by lambda; lambda = 0 is the two-step chain from the last complete cycle."),
                                   by_lambda = rf_records(b$next_cycle$table, ints = c("n_loeo", "n_chrono")),
                                   by_lambda_trajectory = rf_records(b$next_cycle_trajectory$table, ints = c("n_loeo", "n_chrono"))))
  }
  out <- list(ruling = paste("Owner 2026-10-01: the forecast accounts for the midterm / presidential disparity (every projected quantity except lobbying), the trajectory from past cycles",
                      "(national and industry level) and natural inflation; D-065 and the coordinator's consistency rule choose how each is estimated, not whether it is included."),
       status = "candidate evidence; the v2 calculator and the contract (anchor_rule is the constant previous_cycle; slope rows carry no phase, drift or inflation term) do not apply it yet",
       inflation = list(annual_rate = RF_INFLATION$rate, source = RF_INFLATION$source,
                        convention = "nominal = real 2025 USD x mean of the two calendar-year price levels of the cycle, price level (1 + rate)^(year - 2025)"),
       pac = ser("pac"), general_giving = ser("general"))
  if (!is.null(v$current_cycle_layer)) out$current_cycle_layer <- v$current_cycle_layer
  out
}

# D-065 for the next-cycle horizon: the default is PC (the chained candidate that starts from the conditioned open-cycle projection) and a phase-aware challenger replaces it only
# if it beats it by >= min_gain in BOTH designs. The history can only test the upper bound lambda = 1 (the actual open-cycle total; PC = PB one step) and the information
# bracket lambda < 1 (the start's error shrunk by lambda; lambda = 0 is PB2), so the selection is reported at every lambda together with the break-even lambda: the smallest lambda
# at which PC is kept. -> list(table (one row per lambda: default, choice, qualifying, gains), break_even, metrics)
# `trajectory = TRUE`: the same with the mandated drift (default PCT; lambda = 1 is the default trajectory candidate itself; challengers carry the drift too).
rf_ph_select_next <- function(pred, challengers = RF_PH_NEXT_CHALLENGERS, lambdas = c(RF_PH_LAMBDAS, 1), min_gain = RF$RULE_MIN_GAIN, tol = RF$RULE_TIE_TOL, trajectory = FALSE, suffix = "") {
  rows <- list()
  challengers <- paste0(challengers, suffix)
  for (lam in lambdas) {
    default <- if (trajectory) { if (lam == 1) RF_PH_TRAJ_DEFAULT else sprintf("PCT-L%s", format(lam)) } else { if (lam == 1) "PB-R0" else sprintf("PC-R0-L%s", format(lam)) }
    default <- paste0(default, suffix)
    sel <- rf_ph_select(pred, c(default, challengers), default, min_gain, tol)
    g <- function(cd, ds) { r <- sel$table[sel$table$candidate == cd & sel$table$design == ds, ]; if (nrow(r)) r$gain_vs_default else NA_real_ }
    best <- challengers[which.max(vapply(challengers, function(cd) min(g(cd, "loeo"), g(cd, "chrono"), na.rm = TRUE), 0))]
    rows[[length(rows) + 1L]] <- data.frame(lambda = lam, default = default, choice = sel$choice, kept = identical(sel$choice, default), qualifying = paste(sel$qualifying, collapse = ","),
      best_challenger = best, best_gain_loeo = g(best, "loeo"), best_gain_chrono = g(best, "chrono"),
      MAE_default_loeo = sel$table$MAE[sel$table$candidate == default & sel$table$design == "loeo"], MAE_default_chrono = sel$table$MAE[sel$table$candidate == default & sel$table$design == "chrono"],
      n_loeo = sel$table$n[sel$table$candidate == default & sel$table$design == "loeo"], n_chrono = sel$table$n[sel$table$candidate == default & sel$table$design == "chrono"], stringsAsFactors = FALSE)
  }
  tab <- do.call(rbind, rows)
  kept <- tab$lambda[tab$kept]
  list(table = tab, break_even = if (length(kept)) min(kept) else NA_real_)
}

# ---- trajectory candidates: full-sample parameters ---------------------------------------------------------------------------------------------------------------
# candidate "PT-<G|noG>-<full|rec3|damp>-<pool|ind>[-R1|-R2]" (default slope rule R0). Drift per two-year step; h-th step ahead of the last complete cycle for the damped trend.
rf_ph_params_traj <- function(panel, y, candidate, target = 2026L) {
  m <- regmatches(candidate, regexec("^PT-(G|noG)-(full|rec3|damp)-(pool|ind)(-R[12])?$", candidate))[[1]]
  if (!length(m)) stop("rf_ph_params_traj: unknown candidate ", candidate, call. = FALSE)
  bs <- m[2]; kd <- m[3]; sc <- m[4]; rl <- if (nzchar(m[5])) sub("^-", "", m[5]) else "R0"
  panel$sector <- factor(panel$sector)
  fd <- rf_ph_fd_panel(panel, y)
  fp <- rf_ph_fit(fd, "pooled")
  secs <- fp$secs
  k <- as.character(fd$sector)
  if (bs == "G") {
    sl <- fp$fa$rules[[rl]]; ph <- fp$phi[[rl]]
    f <- switch(rl, R0 = fp$fa$f0, R1 = fp$f1, R2 = rf_offset_fit(fd, fp$spec, fp$fa$rules$R2))
    V <- stats::vcov(f)
    se_b <- switch(rl, R0 = stats::setNames(rep(fp$fa$se0, length(secs)), secs), R1 = fp$fa$se, R2 = sqrt(fp$fa$W * fp$fa$se^2 + (1 - fp$fa$W)^2 * fp$fa$se0^2))
    ph_se <- stats::setNames(rep(sqrt(V["dPres", "dPres"]), length(secs)), secs)
    vf <- function(sector, dG, dP) {
      if (rl == "R2") return(dG^2 * se_b[[sector]]^2 + dP^2 * ph_se[[sector]]^2)
      kb <- if (rl == "R0") "dG" else paste0("dG:sector", sector)
      x <- stats::setNames(c(dG, dP), c(kb, "dPres"))
      as.numeric(t(x) %*% V[names(x), names(x)] %*% x)
    }
    r <- fd$dy - sl[k] * fd$dG - ph[k] * fd$dPres
    df <- if (rl == "R0") fp$fa$df0 else fp$fa$df1
  } else {
    f <- stats::lm(dy ~ 0 + dPres, fd)
    sl <- stats::setNames(rep(0, length(secs)), secs); ph <- stats::setNames(rep(unname(stats::coef(f)[["dPres"]]), length(secs)), secs)
    se_b <- stats::setNames(rep(0, length(secs)), secs); ph_se <- stats::setNames(rep(sqrt(stats::vcov(f)[1, 1]), length(secs)), secs)
    vf <- function(sector, dG, dP) dP^2 * ph_se[[sector]]^2
    r <- fd$dy - ph[k] * fd$dPres
    df <- stats::df.residual(f)
  }
  rp <- data.frame(sector = k, cycle_end = fd$cycle_end, r = r, stringsAsFactors = FALSE)
  drift <- function(sector, h = 1L) rf_ph_drift_est(rp, sector, target, kd, sc, h = h)
  list(candidate = candidate, structure = "PT", rule = rl, phi_mode = "pooled", base = bs, drift_kind = kd, drift_scope = sc, secs = secs, slope = sl, slope_se = se_b, phi = ph, phi_se = ph_se,
       gap = 2L, V = vf, df = df, fa = fp$fa, fit = f, resid = rp, drift = drift, drift_by_industry = stats::setNames(vapply(secs, function(s) drift(s, 1L), 0), secs))
}

# ---- inflation: real 2025 dollars -> nominal dollars of the target cycle -------------------------------------------------------------------------------------------
# Paul 2026-10-01: 10-year expected inflation 2.5726436 percent per year (Federal Reserve), the default annual rate. The refit's deflator convention is real = nominal x cpi[2025] /
# mean(cpi[t-1], cpi[t]) for the two-year cycle ending in t, so the conversion back is nominal = real x mean(P[t-1], P[t]) with the price level P[y] = (1 + rate)^(y - 2025) (2025 = 1):
# the cycle's two calendar years carry equal weight (the cycle mid-point convention, used consistently). Configurable: pass `rate`, or a named vector of annual rates in `path`.
RF_INFLATION <- list(rate = 0.025726436, base_year = 2025L,
                     source = "Federal Reserve 10-year expected inflation, 2.5726436%/yr, supplied by Paul 2026-10-01")
rf_ph_price_level <- function(year, rate = RF_INFLATION$rate, base_year = RF_INFLATION$base_year, path = NULL) {
  if (is.null(path)) return((1 + rate)^(year - base_year))
  vapply(year, function(y) if (y <= base_year) 1 else prod(1 + vapply(base_year + seq_len(y - base_year), function(z) { v <- path[[as.character(z)]]; if (is.null(v)) rate else v }, 0)), 0)
}
rf_ph_cycle_factor <- function(cycle, rate = RF_INFLATION$rate, base_year = RF_INFLATION$base_year, path = NULL) {
  vapply(cycle, function(t) mean(rf_ph_price_level(c(t - 1, t), rate, base_year, path)), 0)
}
rf_ph_to_nominal <- function(real, cycle, rate = RF_INFLATION$rate, path = NULL) real * rf_ph_cycle_factor(cycle, rate, path = path)

# Trailing n-year average annual CPI-U inflation of a CPI table (year, cpi) ending at `to` (a sensitivity, no longer the default).
rf_ph_trailing_inflation <- function(cpi, to = 2025L, n = 10L) (cpi$cpi[cpi$year == to] / cpi$cpi[cpi$year == to - n])^(1 / n) - 1

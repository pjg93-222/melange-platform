# The model side of bundle v2 (Phase 2): own-slope PAC through the auto-pick, the previous-cycle anchor for shares with
# Polymarket-to-Polymarket control inputs, prediction and coefficient intervals, and the lobbying LOG refit (first differences and year
# effects in logs). The numbers reproduce SP/phase2_mentat_anchor/ADDENDUM.md section F.
#
# Nothing here decides what is published: the gates (rf_gates.R) and the publisher (rf_publish.R) do. These functions build the blocks
# the bundle writer (rf_bundle2.R) puts into giving-models-v2.json and the worked examples of the review packet.

# ---- PAC: relative change under the previous-cycle anchor ---------------------------------------------------------------------

# One industry's relative PAC change for a growth change dG (in standard deviations of that industry's cycle growth):
#   r = exp(slope * dG) - 1;  coefficient 95% = exp(slope dG +/- crit_df se |dG|) - 1;
#   prediction 95% = exp(slope dG +/- crit_m sqrt(dG^2 se^2 + sigma2_step)) - 1  (m anchored-step pairs of that industry).
rf_pac_relative_change <- function(slope, se, df, sigma2, pairs, dG) {
  centre <- slope * dG
  ch <- stats::qt(0.975, df) * se * abs(dG)
  ph <- stats::qt(0.975, pairs - 1) * sqrt(dG^2 * se^2 + sigma2)
  c(r = exp(centre) - 1, coef_low = exp(centre - ch) - 1, coef_high = exp(centre + ch) - 1, pred_low = exp(centre - ph) - 1, pred_high = exp(centre + ph) - 1)
}

# Per-industry slope rows of the contract (slope_row): the industry's own slope and SE, the DerSimonian-Laird blend (weight, slope, SE)
# and `applied`, the slope the calculator uses under the chosen rule (R0 pooled / R1 own / R2 blend) with its degrees of freedom, 95%
# critical value and ordinary p. `pp` = rf_partial_pool() result; infeasible partial pooling (status "pooled") applies the pooled slope.
rf_slope_rows <- function(pp, rule, secs) {
  pooled <- pp$pooled
  out <- list()
  for (s in secs) {
    k <- match(s, pp$own$industry)
    has <- pp$status == "partial" && !is.na(k)
    own_b <- if (has) pp$own$beta[k] else NA_real_; own_se <- if (has) pp$own$se[k] else NA_real_; own_p <- if (has) pp$own$p[k] else NA_real_
    w <- if (has) unname(pp$weights[s]) else NA_real_; db <- if (has) unname(pp$slopes[s]) else NA_real_; dse <- if (has) unname(pp$slope_se[s]) else NA_real_
    if (!has) rule <- "R0"
    a <- switch(rule,
      R0 = list(beta = pooled$beta, se = pooled$se, df = pooled$df, p = pooled$p),
      R1 = list(beta = own_b, se = own_se, df = pp$df_interaction, p = own_p),
      R2 = list(beta = db, se = dse, df = pp$df_interaction, p = 2 * stats::pt(-abs(db / dse), pp$df_interaction)))
    out[[s]] <- list(own_beta = own_b, own_se = own_se, own_p = own_p, dl_weight = w, dl_beta = db, dl_se = dse,
                     applied = list(rule = rule, beta = a$beta, se = a$se, df = as.integer(a$df), critical_95 = stats::qt(0.975, a$df), ordinary_p = a$p))
  }
  out
}

# The PAC block of bundle v2: slope rows per industry under the chosen rule and the anchored-step residual of each industry's own
# (t-2, t) pairs (computed with the applied slope). `selection` = rf_select_rule() result of the PAC term.
rf_pac_v2 <- function(spec, selection) {
  pp <- rf_partial_pool(spec, spec$d)
  rule <- selection$reported_rule
  d <- spec$d
  d$sector <- factor(d$sector)
  secs <- levels(d$sector)
  rows <- rf_slope_rows(pp, rule, secs)
  applied <- stats::setNames(vapply(rows, function(r) r$applied$beta, 0), secs)
  pred <- list()
  for (s in secs) {
    sg <- rf_step_sigma2(spec, d, applied, NULL, sector = s)
    pred[[s]] <- list(sigma2 = sg$sigma2, pairs = sg$pairs, df = sg$df, critical_95 = sg$critical_95)
  }
  list(rule = rule, status = pp$status, reason = selection$reason, tau2 = pp$tau2, Q = pp$Q, k = length(secs), df_interaction = pp$df_interaction,
       pooled = pp$pooled, slopes = rows, prediction = list(method = "anchored_step_residual", industries = pred), pp = pp)
}

# Scenario growth change of an industry: G_current of the target cycle (default = the industry's mean growth, i.e. 0) minus that of the
# anchor cycle, from the rebuilt PAC panel.
rf_pac_dG <- function(pac_panel, industry, anchor_cycle, target_growth = NULL) {
  p <- pac_panel[pac_panel$sector == industry, ]
  sc <- attr(pac_panel, "scaling")
  ga <- p$G_current[p$cycle_end == anchor_cycle]
  gt <- if (is.null(target_growth)) 0 else (target_growth - sc$mean[sc$sector == industry]) / sc$sd[sc$sector == industry]
  gt - ga
}

# ---- shares: the previous-cycle anchor --------------------------------------------------------------------------------------

# Delta x of a chamber's share model between the previous cycle A and the target cycle T for one industry, over the model's terms:
#   RMajNow  (03 current control of the two years), Forecast (Jan-Jun Republican-control probability, fractions), G_prior (standardized
#   growth of the cycle before each), Pres (presidential-cycle flag). Industry dummies and the intercept cancel (same industry).
# `control` = list(previous = p_rep of A, target = p_rep of T) for the chamber. -> named numeric over the bundle terms.
rf_share_dx <- function(ctx, chamber, industry, terms, control, previous_cycle = 2024L, target_cycle = 2026L) {
  cc <- ctx$current_control
  rm <- function(y) cc$RMajNow[cc$year == y & cc$chamber == chamber]
  p <- ctx$pac_panel[ctx$pac_panel$sector == industry, ]
  gprior_prev <- p$G_prior[p$cycle_end == previous_cycle]              # growth of the cycle before A
  gprior_target <- p$G_current[p$cycle_end == previous_cycle]          # growth of A = the cycle before T
  dx <- stats::setNames(rep(0, length(terms)), terms)
  if ("RMajNow" %in% terms) dx["RMajNow"] <- rm(target_cycle) - rm(previous_cycle)
  if ("Forecast" %in% terms) dx["Forecast"] <- control$target - control$previous
  if ("G_prior" %in% terms) dx["G_prior"] <- gprior_target - gprior_prev
  if ("G_current" %in% terms) dx["G_current"] <- NA_real_               # the target cycle's own growth is a user scenario input
  if ("Pres" %in% terms) dx["Pres"] <- as.numeric(target_cycle %% 4 == 0) - as.numeric(previous_cycle %% 4 == 0)
  dx
}

# Share change in percentage points from a bundle share block (pooled slope, terms / beta / covariance / critical_95 / prediction):
# pp = 100 dx'beta; coefficient 95% = +/- 100 crit sqrt(dx'V dx); prediction 95% = +/- 100 crit_m sqrt(dx'V dx + sigma2_step).
rf_share_change <- function(block, dx) {
  terms <- unlist(block$terms)
  x <- stats::setNames(rep(0, length(terms)), terms)
  x[names(dx)] <- dx
  b <- unlist(block$beta)
  V <- do.call(rbind, lapply(block$covariance, unlist))
  v <- as.numeric(t(x) %*% V %*% x)
  pp <- 100 * sum(x * b)
  ch <- 100 * block$critical_95 * sqrt(v)
  pr <- block$prediction
  ph <- if (is.null(pr) || is.na(pr$sigma2)) c(NA_real_, NA_real_) else rep(100 * stats::qt(0.975, pr$pairs - 1) * sqrt(v + pr$sigma2), 2)
  c(pp = pp, coef_low = pp - ch, coef_high = pp + ch, pred_low = pp - ph[1], pred_high = pp + ph[2])
}

# Anchored-step residual of a share model, pooled slope. anchor = "previous_cycle" (pairs (t-2, t) of every industry; the default anchor) or
# "same_phase_strict" (pairs (t-4, t): the calculator's same-phase anchor is always a full four years back).
rf_share_prediction <- function(spec, anchor = c("previous_cycle", "same_phase_strict")) {
  anchor <- match.arg(anchor)
  d <- spec$d
  d$sector <- factor(d$sector)
  pp <- rf_partial_pool(spec, d)
  slopes <- stats::setNames(rep(pp$pooled$beta, nlevels(d$sector)), levels(d$sector))
  fo <- rf_offset_fit(d, spec, slopes)
  sg <- rf_step_sigma2(spec, d, slopes, fo, anchor = anchor)
  list(method = "anchored_step_residual", anchor = anchor, sigma2 = sg$sigma2, pairs = sg$pairs, df = sg$df, critical_95 = sg$critical_95)
}

# ---- lobbying: the log refit ---------------------------------------------------------------------------------------------------

# First differences in logs on the same pairs as the dollar model: dly = log(level_t) - log(level_{t-1}).
rf_lobby_log_panel <- function(diffs) {
  d <- diffs
  d$dly <- log(d$y) - log(d$y_lag)
  d
}

# The log model dly ~ dg + factor(year) and its block: slope, SE, ordinary p, the relative change per +1 point of predicted growth
# (100 (exp(b) - 1) %) and its coefficient interval, the anchored-step residual (e = dly - b dg, every pair), and the sector slopes.
rf_lobby_log_fit <- function(diffs) rf_lm("dly ~ dg + factor(year)", rf_lobby_log_panel(diffs), "lobbying_log_all71")

rf_lobby_log_block <- function(m, diffs) {
  d <- rf_lobby_log_panel(diffs)
  s <- summary(m)$coefficients["dg", ]
  b <- unname(s[1]); se <- unname(s[2]); df <- stats::df.residual(m); crit <- stats::qt(0.975, df)
  e <- d$dly - b * d$dg
  mm <- length(e)
  V <- stats::vcov(m)
  # own-sector log slopes (evidence): dly ~ dg:study_sector + factor(year)
  d$sector <- factor(d$study_sector)
  mi <- stats::lm(dly ~ factor(year) + dg:sector, data = d)
  ci <- summary(mi)$coefficients
  own <- vapply(levels(d$sector), function(sc) { k <- paste0("dg:sector", sc); if (k %in% rownames(ci)) 100 * (exp(ci[k, 1]) - 1) else NA_real_ }, 0)
  own_rows <- do.call(rbind, lapply(levels(d$sector), function(sc) { k <- paste0("dg:sector", sc)
    if (k %in% rownames(ci)) data.frame(sector = sc, beta = ci[k, 1], se = ci[k, 2], p = ci[k, 4], stringsAsFactors = FALSE) else data.frame(sector = sc, beta = NA_real_, se = NA_real_, p = NA_real_, stringsAsFactors = FALSE) }))
  list(formula = "dly ~ dg + factor(year)", n = as.integer(stats::nobs(m)), residual_df = as.integer(df), residual_sd = summary(m)$sigma,
       beta = b, se = se, ordinary_p = unname(s[4]), critical_95 = crit,
       relative_change_pct_per_point = 100 * (exp(b) - 1),
       coefficient_interval_pct = list(low = 100 * (exp(b - crit * se) - 1), high = 100 * (exp(b + crit * se) - 1)),
       terms = as.list(names(stats::coef(m))), coefficients = as.list(unname(stats::coef(m))),
       covariance = lapply(seq_len(nrow(V)), function(i) as.list(unname(V[i, ]))),
       prediction = list(method = "anchored_step_residual", sigma2 = sum(e^2) / (mm - 1), pairs = as.integer(mm), df = as.integer(mm - 1L),
                         critical_95 = stats::qt(0.975, mm - 1)),
       form = "level_t = level_(t-1) * exp(beta * dg_t); prediction interval on the log scale, exponentiated",
       anchor_rule = "last_year", sector_relative_change_pct_per_point = as.list(own), own = own_rows)
}

# Level forecast from the last observed level: point and intervals at a predicted-growth change dg (log model).
rf_lobby_log_forecast <- function(block, level_prev, dg) {
  b <- block$beta; se <- block$se; pr <- block$prediction
  centre <- b * dg
  ch <- block$critical_95 * se * abs(dg)
  ph <- pr$critical_95 * sqrt(dg^2 * se^2 + pr$sigma2)
  c(point = level_prev * exp(centre), coef_low = level_prev * exp(centre - ch), coef_high = level_prev * exp(centre + ch),
    pred_low = level_prev * exp(centre - ph), pred_high = level_prev * exp(centre + ph))
}

# Rolling-origin backtest of the log model in $M 2025 (exp back-transform x last level): train on pairs with year < T, predict year T.
# Same predictions layout as rf_backtest() (term "lobby", base "log") so rf_backtest_metrics() and the gates can compare it with the
# dollar model on the common folds.
rf_lobby_log_backtest <- function(diffs, targets = RF$LOBBY_TARGETS) {
  d <- rf_lobby_log_panel(diffs)
  d$sector <- factor(d$study_sector)
  rows <- list()
  for (T in targets[targets %in% unique(d$year)]) {
    tr <- d[d$year < T, ]
    if (nrow(tr) < 3L || length(unique(tr$year)) < 2L) next
    m <- stats::lm(dly ~ dg + factor(year), data = tr)
    b <- stats::coef(m)[["dg"]]
    se <- summary(m)$coefficients["dg", 2]
    e <- tr$dly - b * tr$dg
    s2 <- sum(e^2) / (length(e) - 1); crit <- stats::qt(0.975, length(e) - 1)
    for (i in which(d$year == T)) {
      a <- d[i, ]
      half <- crit * sqrt(a$dg^2 * se^2 + s2)
      pred <- a$y_lag * exp(b * a$dg)
      rows[[length(rows) + 1L]] <- data.frame(term = "lobby", base = "log", design = "rolling", target = T, sector = as.character(a$sector), rule = "R0",
        actual = a$y, calc = pred, level = pred, prevchg = pred, naive_prev = a$y_lag, anchor_only = a$y_lag, slope = b, scale = 1, calc_path = "calc",
        low = a$y_lag * exp(b * a$dg - half), high = a$y_lag * exp(b * a$dg + half), sigma2_step = s2, stringsAsFactors = FALSE)
    }
  }
  pred <- do.call(rbind, rows)
  attr(pred, "spec") <- list(id = "lobby", base = "log", unit = "$M 2025", calc_path = "calc")
  pred
}

# Share of leave-one-out folds whose slope keeps the sign of the full-sample slope: drop each year, and drop each sector.
rf_lobby_log_sign_consistency <- function(m, diffs) {
  d <- rf_lobby_log_panel(diffs)
  full <- stats::coef(m)[["dg"]]
  fold <- function(var) vapply(sort(unique(d[[var]])), function(v) {
    dd <- droplevels(d[d[[var]] != v, ]); f <- stats::lm(dly ~ dg + factor(year), data = dd); unname(stats::coef(f)[["dg"]])
  }, 0)
  ey <- fold("year"); es <- fold("study_sector")
  list(loeo = mean(sign(ey) == sign(full)), loco = mean(sign(es) == sign(full)), years = length(ey), sectors = length(es),
       loeo_range = range(ey), loco_range = range(es))
}

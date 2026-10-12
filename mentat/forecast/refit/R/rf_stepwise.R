# The stepwise AICc grid of the Codex review workspace (che/work/stepwise/analyze.py and extend.py), ported to R:
# design matrices for the 16 optional-term masks, the one-bit-at-a-time AICc search, CR2 uncertainty, the selection-repeated
# wild bootstrap (numpy PCG64 stream reproduced bit for bit by rf_rng.R), Holm adjustment, nested leave-election-out
# validation, per-industry Forecast slopes and the interpretable effects. Variable selection is a DIAGNOSTIC here: the
# fitted v1 models use the frozen masks of registry/models.json, and the grid proves the search still lands on them.

RF_OPT_BITS <- c(1L, 2L, 4L, 8L)

# ---- design matrices -------------------------------------------------------------------------------------------------

# One candidate model: design X, its inverse cross-product and projection when full rank (else NULL), eligibility for AICc.
rf_make_model <- function(X, names, groups) {
  n <- nrow(X); p <- ncol(X); rk <- qr(X)$rank
  full <- rk == p
  A <- if (full) solve(crossprod(X)) else NULL
  list(X = X, p = p, rank = as.integer(rk), eligible = full && n - p >= 2 && n - (p + 1) - 1 > 0, A = A,
       pinv = if (full) A %*% t(X) else NULL, M = if (full) diag(n) - X %*% A %*% t(X) else NULL,
       names = names, n = n, groups = groups)
}

# analyze.py matrices(): base = Intercept + industry dummies (sorted, first dropped) + RMajNow; optional Forecast, G_prior,
# G_current, Pres by mask bits 1, 2, 4, 8. Returns a list of the 16 models (index mask + 1) with attr "sectors".
rf_matrices <- function(d, optional = RF$OPTIONAL_TERMS) {
  sectors <- rf_sorted(as.character(d$sector))
  n <- nrow(d)
  dummies <- if (length(sectors) > 1L) do.call(cbind, lapply(sectors[-1], function(s) as.numeric(as.character(d$sector) == s))) else NULL
  base <- cbind(rep(1, n), dummies, as.numeric(d$RMajNow))
  base_names <- c("Intercept", if (length(sectors) > 1L) paste0("industry:", sectors[-1]), "RMajNow")
  groups <- lapply(sort(unique(d$cycle_end)), function(cy) which(d$cycle_end == cy))
  models <- lapply(0:15, function(mask) {
    terms <- optional[bitwAnd(mask, RF_OPT_BITS) > 0L]
    X <- cbind(base, if (length(terms)) as.matrix(d[, terms, drop = FALSE]) else NULL)
    rf_make_model(unname(X), c(base_names, terms), groups)
  })
  attr(models, "sectors") <- sectors
  models
}

# analyze.py fit(): AIC = n log(SSE/n) + 2K (K = p + 1), AICc when eligible, adjusted R2.
rf_fitmask <- function(m, y) {
  if (is.null(m$pinv)) return(list(beta = NULL, u = NULL, sse = NaN, AICc = Inf, AIC = Inf, adjR2 = NaN))
  b <- as.vector(m$pinv %*% y)
  u <- y - as.vector(m$X %*% b)
  sse <- sum(u^2); n <- m$n; p <- m$p; K <- p + 1
  aic <- n * log(max(sse / n, 1e-300)) + 2 * K
  aicc <- if (m$eligible) aic + 2 * K * (K + 1) / (n - K - 1) else Inf
  adj <- if (n > p) 1 - (sse / (n - p)) / (sum((y - mean(y))^2) / (n - 1)) else NaN
  list(beta = b, u = u, sse = sse, AICc = aicc, AIC = aic, adjR2 = adj)
}

# analyze.py search(): from mask 0 flip one bit at a time, the best of the sorted neighbours, accept if AICc falls by more than 1e-9.
rf_search <- function(models, y, log = FALSE, optional = RF$OPTIONAL_TERMS) {
  fs <- lapply(models, rf_fitmask, y = y)
  cur <- 0L; step <- 0L; path <- list()
  if (!is.finite(fs[[1]]$AICc)) return(list(mask = NA_integer_, fits = fs, path = path))
  repeat {
    choices <- sort(bitwXor(cur, RF_OPT_BITS))
    a <- vapply(choices, function(m) fs[[m + 1L]]$AICc, 0)
    best <- choices[which.min(a)]
    move <- fs[[best + 1L]]$AICc < fs[[cur + 1L]]$AICc - RF$TOL_AICC
    if (log) for (trial in choices) {
      bit <- which(bitwAnd(bitwXor(cur, trial), RF_OPT_BITS) > 0L)
      path[[length(path) + 1L]] <- data.frame(step = step, current_mask = cur, trial_mask = trial,
        action = paste0(if (trial > cur) "add " else "remove ", optional[bit]), prior_AICc = fs[[cur + 1L]]$AICc,
        trial_AICc = fs[[trial + 1L]]$AICc, accepted = move && trial == best, stringsAsFactors = FALSE)
    }
    if (!move) break
    cur <- best; step <- step + 1L
    if (step >= 32L) stop("rf_search: search did not converge", call. = FALSE)
  }
  list(mask = cur, fits = fs, path = if (length(path)) do.call(rbind, path) else data.frame())
}

# Public form of the search on a case data frame (columns sector, cycle_end, RMajNow, the optional terms, the outcome).
rf_stepwise_aicc <- function(d, outcome, optional = RF$OPTIONAL_TERMS) {
  mm <- rf_matrices(d, optional)
  s <- rf_search(mm, d[[outcome]], log = TRUE, optional = optional)
  list(mask = s$mask, path = s$path, models = mm, fits = s$fits)
}

# ---- uncertainty: ordinary SE / p and CR2 (Bell-McCaffrey, Satterthwaite) by cycle ------------------------------------

# analyze.py uncertainty(): one row per term. CR2 is NaN when a cluster's residual-maker block is singular (as in Codex).
rf_uncertainty <- function(m, y) {
  f <- rf_fitmask(m, y)
  b <- f$beta; n <- m$n; p <- m$p; u <- f$u; A <- m$A; M <- m$M
  ordinary <- if (n > p) sqrt(diag(A) * f$sse / (n - p)) else rep(NaN, p)
  ls <- list(); cr2ok <- TRUE
  for (ix in m$groups) {
    e <- eigen(M[ix, ix, drop = FALSE], symmetric = TRUE)
    if (min(e$values) <= 1e-10) { cr2ok <- FALSE; break }
    adj <- e$vectors %*% diag(1 / sqrt(e$values), nrow = length(e$values)) %*% t(e$vectors)
    l <- matrix(0, n, p)
    l[ix, ] <- adj %*% m$X[ix, , drop = FALSE] %*% A
    ls[[length(ls) + 1L]] <- l
  }
  cov <- if (cr2ok) Reduce(`+`, lapply(ls, function(l) tcrossprod(crossprod(l, u)))) else NULL
  rows <- lapply(seq_len(p), function(j) {
    r <- data.frame(term = m$names[j], estimate = b[j], ordinary_SE = ordinary[j],
                    ordinary_p = if (n > p) 2 * stats::pt(-abs(b[j] / ordinary[j]), n - p) else NaN,
                    CR2_SE = NaN, CR2_df = NaN, CR2_p = NaN, CR2_low = NaN, CR2_high = NaN, stringsAsFactors = FALSE)
    if (cr2ok) {
      ll <- vapply(ls, function(l) as.vector(M %*% l[, j]), numeric(n))
      gram <- crossprod(ll)
      df <- sum(diag(gram))^2 / sum(gram^2)
      se <- sqrt(max(cov[j, j], 0)); crit <- stats::qt(0.975, df)
      r$CR2_SE <- se; r$CR2_df <- df; r$CR2_p <- 2 * stats::pt(-abs(b[j] / se), df)
      r$CR2_low <- b[j] - crit * se; r$CR2_high <- b[j] + crit * se
    }
    r
  })
  do.call(rbind, rows)
}

# ---- the selection-repeated wild bootstrap -----------------------------------------------------------------------------

rf_cr1_t <- function(m, y, j) {
  f <- rf_fitmask(m, y)
  w <- as.vector(m$X %*% m$A[, j])
  G <- length(m$groups)
  fac <- G / (G - 1) * (m$n - 1) / (m$n - m$p)
  ss <- vapply(m$groups, function(ix) sum(w[ix] * f$u[ix]), 0)
  f$beta[j] / sqrt(fac * sum(ss^2))
}

# analyze.py bootstrap_select(): null = mask 14 (nuisance terms, no Forecast); Webb weights by cycle drawn with the numpy stream
# for entropy (always random draws, never enumerated); each draw re-runs the whole AICc search; statistic |CR1 t| of Forecast when
# selected; p = (1 + #{t >= observed}) / (draws + 1).
rf_bootstrap_select <- function(models, y, chosen, entropy, draws = RF$B_SELECT) {
  null <- models[[15L]]
  if (is.na(chosen)) return(list(selection_repeated_p = NaN, null_residual_df = NaN, null_draws = 0L, null_selection_rate = NaN, observed_abs_CR1_t = NaN))
  if (bitwAnd(chosen, 1L) == 0L) return(list(selection_repeated_p = 1, null_residual_df = null$n - null$rank, null_draws = 0L, null_selection_rate = NaN, observed_abs_CR1_t = NaN))
  if (is.null(null$pinv) || null$n <= null$p) return(list(selection_repeated_p = NaN, null_residual_df = null$n - null$rank, null_draws = 0L, null_selection_rate = NaN, observed_abs_CR1_t = NaN))
  n <- null$n; G <- length(null$groups)
  cl <- integer(n)
  for (g in seq_len(G)) cl[null$groups[[g]]] <- g
  f0 <- rf_fitmask(null, y)
  W <- matrix(SGS_WEBB[sgs_numpy_integers6(entropy, draws * G) + 1L], nrow = draws, ncol = G, byrow = TRUE)   # numpy rng.choice(WEBB, (draws, G))
  Y <- (y - f0$u) + f0$u * t(W)[cl, , drop = FALSE]
  sc <- matrix(Inf, 16L, draws)
  for (mk in 0:15) {
    md <- models[[mk + 1L]]
    if (!md$eligible) next
    K <- md$p + 1
    ss <- colSums((md$M %*% Y)^2)
    sc[mk + 1L, ] <- n * log(pmax(ss / n, 1e-300)) + 2 * K + 2 * K * (K + 1) / (n - K - 1)
  }
  cur <- integer(draws); idx <- seq_len(draws); converged <- FALSE
  for (it in 1:32) {
    nb <- vapply(RF_OPT_BITS, function(b) bitwXor(cur, b), integer(draws))
    if (!is.matrix(nb)) nb <- matrix(nb, nrow = draws)
    vals <- vapply(1:4, function(j) sc[cbind(nb[, j] + 1L, idx)], numeric(draws))
    if (!is.matrix(vals)) vals <- matrix(vals, nrow = draws)
    bj <- max.col(-vals, ties.method = "first")
    best <- nb[cbind(idx, bj)]
    acc <- sc[cbind(best + 1L, idx)] < sc[cbind(cur + 1L, idx)] - RF$TOL_AICC
    if (!any(acc)) { converged <- TRUE; break }
    cur[acc] <- best[acc]
  }
  if (!converged) stop("rf_bootstrap_select: bootstrap search failed to converge", call. = FALSE)
  ts <- numeric(draws)
  for (mk in unique(cur[bitwAnd(cur, 1L) > 0L])) {
    md <- models[[mk + 1L]]; use <- which(cur == mk); k <- match("Forecast", md$names)
    Yb <- Y[, use, drop = FALSE]
    q <- as.vector(md$X %*% md$A[, k]); bk <- colSums(q * Yb); U <- md$M %*% Yb
    fac <- G / (G - 1) * (n - 1) / (n - md$p)
    ts[use] <- abs(bk / sqrt(fac * colSums(rowsum(q * U, cl)^2)))
  }
  obs <- abs(rf_cr1_t(models[[chosen + 1L]], y, match("Forecast", models[[chosen + 1L]]$names)))
  list(selection_repeated_p = (1 + sum(ts >= obs * (1 - RF$TOL_T))) / (draws + 1), null_residual_df = n - null$p, null_draws = as.integer(draws),
       null_selection_rate = mean(bitwAnd(cur, 1L) > 0L), observed_abs_CR1_t = obs)
}

# Public form for a case data frame.
rf_selection_repeated_share <- function(d, outcome, chosen_mask, entropy, draws = RF$B_SELECT) {
  rf_bootstrap_select(rf_matrices(d), d[[outcome]], chosen_mask, entropy, draws)
}

rf_holm <- function(values) {
  a <- as.numeric(values)
  if (!length(a)) return(numeric())
  a[is.na(a)] <- 1
  idx <- order(a)
  out <- numeric(length(a))
  out[idx] <- pmin(1, cummax(a[idx] * seq(length(a), 1)))
  out
}

# ---- nested leave-election-out validation -----------------------------------------------------------------------------

# analyze.py prepare(): standardize growth with the mean / sd of a calibration set (the training fold).
rf_prepare <- function(d, calibration) rf_standardize(d, calibration, "sector")

# For each election: re-standardize growth on the training cycles, repeat the stepwise search, predict the held-out cycle with the
# stepwise, forced-only (mask 0) and original-full (mask 15) models; previous_cycle = the same industry two years earlier.
rf_nested_predictions <- function(d, outcome, case = NA_character_) {
  d <- d[order(d$cycle_end, d$sector), ]
  d$sector <- as.character(d$sector)
  pred <- list(); stab <- list()
  for (cy in sort(unique(d$cycle_end))) {
    train0 <- d[d$cycle_end != cy, ]; test <- d[d$cycle_end == cy, ]
    train <- rf_prepare(train0, train0)
    test <- rf_prepare(test, train0)
    mm <- rf_matrices(train)
    s <- rf_search(mm, train[[outcome]])
    cc <- s$mask; ff <- s$fits
    stab[[length(stab) + 1L]] <- data.frame(case = case, omitted_cycle = cy, selected_mask = cc,
      forecast_selected = !is.na(cc) && bitwAnd(cc, 1L) > 0L,
      forecast_estimate = if (!is.na(cc) && bitwAnd(cc, 1L) > 0L) ff[[cc + 1L]]$beta[match("Forecast", mm[[cc + 1L]]$names)] else NaN, stringsAsFactors = FALSE)
    for (mdl in list(list("stepwise", cc), list("forced_only", 0L), list("original_full", 15L))) {
      mask <- mdl[[2]]
      pr <- rep(NaN, nrow(test))
      if (!is.na(mask) && !is.null(mm[[mask + 1L]]$pinv) && nrow(train) > mm[[mask + 1L]]$p) {
        nm <- mm[[mask + 1L]]$names
        Xt <- vapply(nm, function(name) {
          if (name == "Intercept") rep(1, nrow(test)) else if (startsWith(name, "industry:")) as.numeric(test$sector == sub("^industry:", "", name)) else as.numeric(test[[name]])
        }, numeric(nrow(test)))
        pr <- as.vector(matrix(Xt, nrow = nrow(test)) %*% ff[[mask + 1L]]$beta)
      }
      pred[[length(pred) + 1L]] <- data.frame(case = case, heldout_cycle = cy, sector = test$sector, model = mdl[[1]], actual = test[[outcome]],
                                              predicted = pr, error = pr - test[[outcome]], stringsAsFactors = FALSE)
    }
    prev <- d[d$cycle_end == cy - 2, ]
    v <- prev[[outcome]][match(test$sector, prev$sector)]
    pred[[length(pred) + 1L]] <- data.frame(case = case, heldout_cycle = cy, sector = test$sector, model = "previous_cycle", actual = test[[outcome]],
                                            predicted = v, error = v - test[[outcome]], stringsAsFactors = FALSE)
  }
  list(predictions = do.call(rbind, pred), stability = do.call(rbind, stab))
}

# nested_validation_metrics.csv rows of one case: three comparisons of the stepwise model with a baseline.
rf_validation_metrics <- function(case, predictions, outcome) {
  scale <- if (outcome == "RShare") 100 else 1
  step <- predictions[predictions$model == "stepwise", ]
  do.call(rbind, lapply(c("forced_only", "previous_cycle", "original_full"), function(cmp) {
    base <- predictions[predictions$model == cmp, ]
    x <- merge(step, base, by = c("heldout_cycle", "sector"), suffixes = c("_step", "_baseline"))
    x <- x[!is.na(x$error_step) & !is.na(x$error_baseline), ]
    data.frame(case = case, comparison = cmp, rows = nrow(x), election_targets = length(unique(x$heldout_cycle)),
               units = if (outcome == "RShare") "share percentage points" else "log dollars",
               stepwise_MAE = mean(abs(x$error_step)) * scale, baseline_MAE = mean(abs(x$error_baseline)) * scale, stringsAsFactors = FALSE)
  }))
}

# ---- the whole grid ----------------------------------------------------------------------------------------------------

# `cases` = rf_case_panels(): one long data.frame with case, signal, chamber, scope, outcome, counter.
# Returns list(selected = selected_models.csv rows, effects = interpretable_effects.csv rows, industry_slopes, validation, stability).
rf_stepwise_grid <- function(cases, entropy_root = c(RF$STEPWISE_SEED, RF$STEPWISE_TAG), draws = RF$B_SELECT) {
  ids <- unique(cases$case)
  # each case is independent and seeds its own stream from its counter, so cases run on parallel workers with identical results
  per_case <- rf_pmap(ids, function(cs) .rf_grid_case(cs, cases, entropy_root, draws))
  sel <- do.call(rbind, lapply(per_case, `[[`, "row"))
  rownames(sel) <- NULL
  sel$Holm_all_48 <- rf_holm(sel$selection_repeated_p)
  sel$Holm_outcome_family <- NA_real_
  share <- sel$outcome == "RShare"
  sel$Holm_outcome_family[share] <- rf_holm(sel$selection_repeated_p[share])
  sel$Holm_outcome_family[!share] <- rf_holm(sel$selection_repeated_p[!share])
  validation <- do.call(rbind, lapply(per_case, `[[`, "val")); rownames(validation) <- NULL
  list(selected = sel, effects = rf_effects(sel), industry_slopes = rf_industry_slopes(cases, sel), validation = validation,
       stability = do.call(rbind, lapply(per_case, `[[`, "stab")))
}

.rf_grid_case <- function(cs, cases, entropy_root, draws) {
  d <- cases[cases$case == cs, ]
  meta <- d[1, c("case", "signal", "chamber", "scope", "outcome")]
  y <- d[[meta$outcome]]
  counter <- d$counter[1]
  mm <- rf_matrices(d)
  s <- rf_search(mm, y, log = TRUE)
  if (is.na(s$mask)) stop("rf_stepwise_grid: no eligible base model for ", cs, call. = FALSE)
  chosen <- s$mask
  sel <- mm[[chosen + 1L]]; fs <- s$fits
  aicc <- vapply(fs, function(f) f$AICc, 0)
  best <- which.min(aicc) - 1L
  sr <- rf_uncertainty(sel, y)
  fc <- sr[sr$term == "Forecast", ]
  boot <- rf_bootstrap_select(mm, y, chosen, c(entropy_root, counter), draws)
  n <- nrow(d)
  row <- data.frame(meta, n = n, elections = length(unique(d$cycle_end)), selected_mask = chosen, selected_terms = paste(sel$names, collapse = " + "),
    forecast_selected = bitwAnd(chosen, 1L) > 0L, parameters = sel$p, residual_df = n - sel$p, AICc = fs[[chosen + 1L]]$AICc,
    adjusted_R2 = fs[[chosen + 1L]]$adjR2, forced_only_AICc = fs[[1]]$AICc, full_model_AICc = fs[[16]]$AICc, full_model_rank = mm[[16]]$rank,
    full_model_columns = mm[[16]]$p, full_model_residual_df = n - mm[[16]]$rank, full_model_adjusted_R2 = fs[[16]]$adjR2,
    global_best_mask = best, stepwise_is_global_best = chosen == best,
    forecast_estimate = if (nrow(fc)) fc$estimate else NA_real_, forecast_ordinary_p = if (nrow(fc)) fc$ordinary_p else NA_real_,
    forecast_CR2_p = if (nrow(fc)) fc$CR2_p else NA_real_, forecast_CR2_low = if (nrow(fc)) fc$CR2_low else NA_real_,
    forecast_CR2_high = if (nrow(fc)) fc$CR2_high else NA_real_, forecast_CR2_df = if (nrow(fc)) fc$CR2_df else NA_real_,
    selection_repeated_p = boot$selection_repeated_p, null_residual_df = boot$null_residual_df, null_draws = boot$null_draws,
    null_selection_rate = boot$null_selection_rate, observed_abs_CR1_t = boot$observed_abs_CR1_t, stringsAsFactors = FALSE)
  nested <- rf_nested_predictions(d, meta$outcome, cs)
  list(row = row, val = rf_validation_metrics(cs, nested$predictions, meta$outcome), stab = nested$stability)
}

# extend.py: effects at the declared contrasts (+0.1 IEM = +10 score points, +10 House seats, +1 Senate seat).
rf_effects <- function(sel) {
  contrast <- ifelse(sel$signal == "control", 0.1, ifelse(sel$chamber == "House", 10, 1))
  label <- ifelse(sel$signal == "control", "+10 IEM score points", ifelse(sel$chamber == "House", "+10 projected R House seats", "+1 projected R Senate seat"))
  tr <- function(v) ifelse(sel$outcome == "RShare", 100 * v * contrast, 100 * expm1(v * contrast))
  out <- data.frame(case = sel$case, signal = sel$signal, chamber = sel$chamber, scope = sel$scope, outcome = sel$outcome, contrast = label,
    forecast_selected = sel$forecast_selected, effect = tr(sel$forecast_estimate), conditional_CR2_low = tr(sel$forecast_CR2_low),
    conditional_CR2_high = tr(sel$forecast_CR2_high),
    effect_unit = ifelse(sel$outcome == "RShare", "percentage points of Republican share", "percent change in real party dollars"),
    ordinary_p = sel$forecast_ordinary_p, conditional_CR2_p = sel$forecast_CR2_p, selection_repeated_p = sel$selection_repeated_p,
    Holm_outcome_family = sel$Holm_outcome_family, Holm_all_48 = sel$Holm_all_48, stringsAsFactors = FALSE)
  out[out$scope == "pooled", ]
}

# extend.py:16-41: the full model (mask 15) with one Forecast slope per industry; CR2 by cycle; effect scale 10 (control),
# 1000 (seats, House) or 100 (seats, Senate). One row per industry of each pooled RShare case.
rf_industry_slopes <- function(cases, sel) {
  out <- list()
  for (cs in sel$case[sel$scope == "pooled" & sel$outcome == "RShare"]) {
    d <- cases[cases$case == cs, ]
    y <- d$RShare
    m0 <- rf_matrices(d)[[16]]
    sectors <- rf_sorted(as.character(d$sector))
    j <- match("Forecast", m0$names)
    keep <- setdiff(seq_len(m0$p), j)
    X <- cbind(m0$X[, keep, drop = FALSE], vapply(sectors, function(s) d$Forecast * (as.character(d$sector) == s), numeric(nrow(d))))
    nm <- c(m0$names[keep], paste0("Forecast:", sectors))
    mm <- rf_make_model(unname(X), nm, m0$groups)
    if (is.null(mm$pinv)) stop("rf_industry_slopes: industry-slope design is rank deficient for ", cs, call. = FALSE)
    u <- rf_uncertainty(mm, y)
    u <- u[startsWith(u$term, "Forecast:"), ]
    scale <- if (d$signal[1] == "control") 10 else if (d$chamber[1] == "House") 1000 else 100
    out[[length(out) + 1L]] <- data.frame(case = cs, signal = d$signal[1], chamber = d$chamber[1], sector = sub("^Forecast:", "", u$term),
      effect_pp = u$estimate * scale, conditional_CR2_low_pp = u$CR2_low * scale, conditional_CR2_high_pp = u$CR2_high * scale, u, stringsAsFactors = FALSE)
  }
  res <- do.call(rbind, out)
  rownames(res) <- NULL
  res
}

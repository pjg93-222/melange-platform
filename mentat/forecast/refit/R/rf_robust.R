# The robustness block printed under every regression (port of SP/regressions/regressions.R:42-141, 191-249):
# ordinary OLS, HC1, per-cluster CR1 and CR2 (clubSandwich, Satterthwaite), wild cluster RESTRICTED bootstrap-t (null imposed,
# CR1-studentized, symmetric two-sided) with Webb six-point and Rademacher weights, leave-one-election-out and
# leave-one-cluster-out folds, and the PAC selection-repeated global p. Enumerate all vectors when 6^G (2^G) <= 300,000, otherwise
# draw B from the numpy-compatible stream of rf_rng.R with entropy c(SEED, crc32(model), crc32(term), crc32(cluster variable)).
# Bootstrap matrices are built in blocks so a 71 x 279,936 problem never needs ~160 MB at once.

rf_hc1 <- function(m, key) {
  V <- sandwich::vcovHC(m, type = "HC1")
  est <- unname(stats::coef(m)[key]); df <- stats::df.residual(m)
  se <- sqrt(V[key, key]); t <- est / se
  list(SE = se, t = t, df = df, p = 2 * stats::pt(-abs(t), df))
}

rf_cr1 <- function(m, key, cluster) {
  G <- length(unique(cluster))
  V <- sandwich::vcovCL(m, cluster = cluster, type = "HC1")
  se <- sqrt(V[key, key]); t <- unname(stats::coef(m)[key] / se)
  list(SE = se, t = t, df = G - 1L, G = G, p = 2 * stats::pt(-abs(t), G - 1))
}

rf_cr2 <- function(m, key, cluster) {
  ct <- tryCatch(as.data.frame(clubSandwich::coef_test(m, vcov = "CR2", cluster = cluster, test = "Satterthwaite")),
                 error = function(e) NULL)
  if (is.null(ct)) return(list(SE = NA_real_, df = NA_real_, p = NA_real_))
  r <- ct[ct$Coef == key, ]
  list(SE = r$SE, df = r$df_Satt, p = r$p_Satt)
}

# Wild cluster restricted bootstrap-t for coefficient k of y ~ X. weights: "webb" (6-point) or "rademacher".
# Enumerates every weight vector when the count is <= max_enum (no entropy needed), else B random draws.
# -> t, se, p, hits, draws, enumerated, G, weights
rf_wcr <- function(X, y, cluster, k, weights = c("webb", "rademacher"), entropy = c(0, 0, 0, 0), max_enum = RF$MAX_ENUM, B = RF$B_RANDOM) {
  weights <- match.arg(weights)
  cl <- as.integer(factor(cluster)); G <- max(cl); n <- nrow(X); p <- ncol(X)
  XtXi <- solve(crossprod(X)); q <- as.vector(X %*% XtXi[, k])
  b <- as.vector(XtXi %*% crossprod(X, y)); u <- as.vector(y - X %*% b)
  cfac <- G / (G - 1) * (n - 1) / (n - p)
  se0 <- sqrt(cfac * sum(rowsum(q * u, cl)^2)); t0 <- b[k] / se0
  fr <- stats::lm.fit(X[, -k, drop = FALSE], y); ur <- as.vector(fr$residuals); yr <- y - ur
  M <- diag(n) - X %*% XtXi %*% t(X)
  n_all <- if (weights == "webb") 6^G else 2^G
  enumerated <- n_all <= max_enum
  if (!enumerated && B > max_enum) stop("rf_wcr: B must not exceed max_enum", call. = FALSE)
  V <- if (weights == "webb") {
    if (enumerated) sgs_webb_draws(G, n_all, entropy)$V else sgs_webb_draws(G, B, entropy)$V
  } else if (enumerated) {
    as.matrix(expand.grid(rep(list(c(-1, 1)), G)))
  } else {
    matrix(c(-1, 1)[(sgs_numpy_integers6(entropy, B * G) %% 2L) + 1L], nrow = B, ncol = G, byrow = TRUE)   # parity of the stream index: uniform on {-1, 1}
  }
  total <- nrow(V)
  chunk <- RF$CHUNK                     # captured here: a worker must use the master's block size, not its own copy of RF
  starts <- seq(1L, total, by = chunk)
  # blocks of draws are independent and their hit counts are integers: summing them in any order is exact, so the blocks run on
  # parallel workers (REFIT_CORES) with a result identical to the serial run
  counts <- rf_pmap(starts, function(a) {
    idx <- a:min(total, a + chunk - 1L)
    W <- t(V[idx, , drop = FALSE])[cl, , drop = FALSE]
    Ys <- yr + ur * W
    bk <- colSums(q * Ys)
    U <- M %*% Ys
    se <- sqrt(cfac * colSums(rowsum(q * U, cl)^2))
    ts <- bk / se
    ts[!is.finite(ts)] <- Inf
    sum(abs(ts) >= abs(t0) * (1 - RF$TOL_T))
  })
  hits <- as.integer(sum(unlist(counts)))
  list(t = t0, se = se0, p = if (enumerated) hits / total else (1 + hits) / (total + 1), hits = hits, draws = as.integer(total),
       enumerated = enumerated, G = G, weights = weights)
}

# Refit the model without each value of `var`; growth scaling is held fixed in the fold (as in regressions.R).
rf_drop_folds <- function(m, d, var, key) {
  vals <- sort(unique(d[[var]]))
  do.call(rbind, lapply(vals, function(v) {
    dd <- droplevels(d[d[[var]] != v, ])
    fit <- tryCatch(stats::lm(stats::formula(m), data = dd), error = function(e) NULL)
    ok <- !is.null(fit) && key %in% names(stats::coef(fit)) && !is.na(stats::coef(fit)[key]) && stats::df.residual(fit) > 0
    data.frame(dropped_var = var, dropped = as.character(v), n = nrow(dd),
               estimate = if (ok) unname(stats::coef(fit)[key]) else NA_real_,
               ordinary_p = if (ok) summary(fit)$coefficients[key, 4] else NA_real_,
               residual_df = if (ok) as.integer(stats::df.residual(fit)) else NA_integer_, stringsAsFactors = FALSE)
  }))
}

rf_fold_line <- function(fr, full) {
  e <- fr$estimate[!is.na(fr$estimate)]
  sprintf("%d/%d folds same sign as full-sample (%+.6g); range [%+.6g, %+.6g]; median %+.6g; fold/full ratio range [%.2f, %.2f]",
          sum(sign(e) == sign(full)), length(e), full, min(e), max(e), stats::median(e), min(e / full), max(e / full))
}

# The block for one coefficient of one fitted model.
#   clusters   cluster variables of d (each gets CR1, CR2, Webb and, when 2^G >= 64, Rademacher wild p-values)
#   fold_vars  NULL, or c(election variable, cluster variable) for the two leave-one-out folds
# -> list(table = data.frame(model, key, method, G, SE, df, p, draws, note), folds = data.frame, text = character)
rf_robustness_block <- function(id, m, d, key, clusters, fold_vars = NULL, b_random = RF$B_RANDOM, seed = RF$SEED) {
  s <- summary(m)$coefficients
  est <- s[key, 1]
  X <- stats::model.matrix(m); y <- stats::model.response(stats::model.frame(m)); k <- match(key, colnames(X))
  tab <- list(); txt <- character()
  add <- function(method, G, SE, df, p, draws, note) tab[[length(tab) + 1L]] <<- data.frame(model = id, key = key, method = method, G = G, SE = SE, df = df,
                                                                                       p = p, draws = draws, note = note, stringsAsFactors = FALSE)
  txt <- c(txt, sprintf("---------------- ROBUSTNESS / BOOTSTRAP BLOCK: %s | key term: %s ----------------", id, key))
  txt <- c(txt, sprintf("Ordinary OLS (as printed above): estimate %+.6g  SE %.6g  t %.3f  p %.6g  (df %d)", est, s[key, 2], s[key, 3], s[key, 4], stats::df.residual(m)))
  h <- rf_hc1(m, key)
  txt <- c(txt, sprintf("HC1 heteroskedasticity-robust (sandwich::vcovHC type HC1, t on %d df): SE %.6g  t %.3f  p %.6g", h$df, h$SE, h$t, h$p))
  add("ordinary", NA_integer_, s[key, 2], stats::df.residual(m), s[key, 4], NA_integer_, "lm summary")
  add("HC1", NA_integer_, h$SE, stats::df.residual(m), h$p, NA_integer_, "sandwich::vcovHC HC1")
  for (cn in clusters) {
    cl <- d[[cn]]
    c1 <- rf_cr1(m, key, cl); c2 <- rf_cr2(m, key, cl); G <- c1$G
    ent <- c(seed, sgs_crc32(id), sgs_crc32(key), sgs_crc32(cn))
    ww <- rf_wcr(X, y, cl, k, "webb", entropy = ent, B = b_random)
    wr <- if (2^G >= 64) rf_wcr(X, y, cl, k, "rademacher", entropy = ent, B = b_random) else NULL
    txt <- c(txt, sprintf("Cluster = %s (G = %d):", cn, G),
      sprintf("   CR1 (sandwich::vcovCL HC1, t on G-1 = %d df): SE %.6g  t %.3f  p %.6g", G - 1L, c1$SE, c1$t, c1$p),
      sprintf("   CR2 (clubSandwich %s, Satterthwaite): SE %.6g  df %.3f  p %.6g", as.character(utils::packageVersion("clubSandwich")), c2$SE, c2$df, c2$p),
      sprintf("   Wild cluster restricted bootstrap-t, Webb 6-point weights, %s %d draws%s: p %.6g", if (ww$enumerated) "ALL" else "random", ww$draws,
              if (ww$enumerated) " (full enumeration, no seed needed)" else sprintf(" (numpy-stream entropy %s)", paste(format(ent, scientific = FALSE), collapse = ",")), ww$p),
      if (!is.null(wr)) sprintf("   Wild cluster restricted bootstrap-t, Rademacher weights, %s %d draws: p %.6g", if (wr$enumerated) "ALL" else "random", wr$draws, wr$p)
      else sprintf("   Rademacher weights not reported: only 2^%d = %d distinct draws.", G, 2^G))
    add(paste0("CR1_", cn), G, c1$SE, G - 1L, c1$p, NA_integer_, "vcovCL HC1, t(G-1)")
    add(paste0("CR2_", cn), G, c2$SE, c2$df, c2$p, NA_integer_, "clubSandwich Satterthwaite")
    add(paste0("wildWebb_", cn), G, NA_real_, NA_real_, ww$p, ww$draws, if (ww$enumerated) "full enumeration" else paste("random, numpy-stream entropy", paste(format(ent, scientific = FALSE), collapse = ",")))
    if (!is.null(wr)) add(paste0("wildRademacher_", cn), G, NA_real_, NA_real_, wr$p, wr$draws, if (wr$enumerated) "full enumeration" else "random, numpy-stream parity")
  }
  folds <- NULL
  if (!is.null(fold_vars)) {
    folds <- list()
    for (v in fold_vars) {
      fr <- rf_drop_folds(m, d, v, key)
      lab <- if (v == fold_vars[1]) "Leave-one-election-out" else "Leave-one-cluster-out"
      txt <- c(txt, sprintf("%s (drop each %s; same specification; growth scaling held fixed):", lab, v),
               utils::capture.output(print(fr[, c("dropped", "n", "estimate", "ordinary_p", "residual_df")], row.names = FALSE, digits = 6)),
               paste("   ", rf_fold_line(fr, est)))
      folds[[length(folds) + 1L]] <- cbind(model = id, key = key, fold_type = lab, fr)
    }
    folds <- do.call(rbind, folds)
  }
  list(table = do.call(rbind, tab), folds = folds, text = txt)
}

# ---- PAC forward-AIC selection and its selection-repeated global p ---------------------------------------------------------

# forward.R:12-36: forced sector + factor(cycle_end); add the candidate that lowers AIC most while AIC falls by more than 1e-9.
rf_forward_aic <- function(d, outcome, candidates = c("G_prior", "G_current", "Neg_prior")) {
  d$sector <- factor(d$sector, levels = rf_sorted(as.character(d$sector)))
  X <- stats::model.matrix(~ sector + factor(cycle_end), d)
  y <- d[[outcome]]
  aicf <- function(X) { r <- stats::lm.fit(X, y); n <- nrow(X); n * (log(2 * pi) + 1 + log(sum(r$residuals^2) / n)) + 2 * (r$rank + 1) }
  cur <- character(); a <- aicf(X); path <- data.frame(step = 0L, added = NA_character_, AIC = a, stringsAsFactors = FALSE)
  repeat {
    rem <- setdiff(candidates, cur)
    if (!length(rem)) break
    tr <- vapply(rem, function(t) { XX <- cbind(X, d[[t]]); if (qr(XX)$rank < ncol(XX)) Inf else aicf(XX) }, 0)
    if (min(tr) < a - RF$TOL_AICC) {
      add <- rem[which.min(tr)]
      cur <- c(cur, add); X <- cbind(X, d[[add]]); a <- min(tr)
      path <- rbind(path, data.frame(step = length(cur), added = add, AIC = a, stringsAsFactors = FALSE))
    } else break
  }
  list(selected = cur, mask = sum(2^(match(cur, candidates) - 1L)), path = path)
}

# regressions.R pac_selection(): the forward search is repeated inside every wild draw of the controls-only null (industry and
# cycle effects only); statistic = max |CR1 t| (by sector) over whichever candidate terms are selected, 0 when none is;
# p = share of the 6^G enumerated Webb vectors whose statistic is at least the observed |CR1 t| of G_current.
rf_selection_repeated_pac <- function(d, candidates = c("G_prior", "G_current", "Neg_prior"), focal = "G_current") {
  d <- d[order(d$sector, d$cycle_end), ]
  d$sector <- factor(d$sector, levels = rf_sorted(as.character(d$sector)))
  base <- stats::model.matrix(~ sector + factor(cycle_end), d)
  cand <- as.matrix(d[, candidates])
  y <- d$log_pac; n <- length(y); cl <- as.integer(d$sector); G <- max(cl)
  nb <- length(candidates); nmask <- 2^nb
  bits <- 2^(seq_len(nb) - 1L)
  mods <- lapply(0:(nmask - 1L), function(mask) {
    X <- cbind(base, cand[, bitwAnd(mask, bits) > 0, drop = FALSE]); XtXi <- solve(crossprod(X))
    list(X = X, p = ncol(X), XtXi = XtXi, M = diag(n) - X %*% XtXi %*% t(X))
  })
  aic_all <- function(Y) do.call(rbind, lapply(mods, function(md) n * log(colSums((md$M %*% Y)^2) / n) + 2 * (md$p + 1)))
  forward <- function(A) {
    D <- ncol(A); cur <- rep(0L, D); idx <- seq_len(D)
    for (step in seq_len(nb)) {
      curv <- A[cbind(cur + 1L, idx)]; bestv <- rep(Inf, D); bestm <- cur
      for (j in seq_len(nb) - 1L) {
        free <- bitwAnd(cur, as.integer(2^j)) == 0L; cm <- bitwOr(cur, as.integer(2^j)); v <- A[cbind(cm + 1L, idx)]
        take <- free & v < bestv; bestv[take] <- v[take]; bestm[take] <- cm[take]
      }
      acc <- bestv < curv - RF$TOL_AICC
      if (!any(acc)) break
      cur[acc] <- bestm[acc]
    }
    cur
  }
  kfocal <- function(md) match(focal, colnames(md$X))
  cr1 <- function(md, Y, k) {
    q <- as.vector(md$X %*% md$XtXi[, k]); bk <- colSums(q * Y); U <- md$M %*% Y
    abs(bk / sqrt(G / (G - 1) * (n - 1) / (n - md$p) * colSums(rowsum(q * U, cl)^2)))
  }
  obs_sel <- forward(aic_all(matrix(y)))
  fbit <- match(focal, candidates)
  if (bitwAnd(obs_sel, as.integer(2^(fbit - 1L))) == 0L) stop("rf_selection_repeated_pac: the observed forward selection does not retain ", focal, call. = FALSE)
  t_obs <- cr1(mods[[obs_sel + 1L]], matrix(y), kfocal(mods[[obs_sel + 1L]]))
  md0 <- mods[[1L]]                               # controls-only null
  b0 <- md0$XtXi %*% t(md0$X) %*% y; fit0 <- as.vector(md0$X %*% b0); u0 <- y - fit0
  V <- if (6^G <= RF$MAX_ENUM) sgs_webb_draws(G, 6^G, c(0, 0, 0))$V else sgs_webb_draws(G, RF$B_RANDOM, c(RF$SEED, sgs_crc32("pac_selection"), 0))$V
  Yw <- fit0 + u0 * t(V)[cl, , drop = FALSE]
  selw <- forward(aic_all(Yw))
  tglob <- numeric(ncol(Yw))
  for (mask in unique(selw[selw > 0L])) {
    md <- mods[[mask + 1L]]; use <- which(selw == mask)
    ks <- which(colnames(md$X) %in% candidates)
    tt <- vapply(ks, function(k) cr1(md, Yw[, use, drop = FALSE], k), numeric(length(use)))
    tglob[use] <- if (is.matrix(tt)) apply(tt, 1, max) else max(tt)
  }
  hits <- sum(tglob >= t_obs * (1 - RF$TOL_T))
  list(hits = as.integer(hits), draws = as.integer(ncol(Yw)), p = hits / ncol(Yw), observed_selection = paste(candidates[bitwAnd(obs_sel, bits) > 0], collapse = "+"),
       observed_abs_t_cr1 = t_obs, rate_selects_focal = mean(bitwAnd(selw, as.integer(2^(fbit - 1L))) > 0))
}

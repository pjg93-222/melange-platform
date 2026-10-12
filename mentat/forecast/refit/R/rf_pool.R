# Partial pooling of the focal slope across industries (spec 3.8), as prototyped in SP/tailoring/tailoring.R:47-83.
# Library code: the v1 parity bundle does not use it.
#
# For a model with focal F and industries s = 1..K:
#   R0  pooled slope b0 from the frozen formula
#   R1  own slopes b_s from the interaction fit (F:sector, no F main effect)
#   R2  DerSimonian-Laird blend toward the POOLED slope:  W_s = tau2 / (tau2 + se_s^2);  slope_s = W_s b_s + (1 - W_s) b0;
#       variance   var_s = W_s se_s^2 + (1 - W_s)^2 se0^2
# where tau2 is the DL between-industry variance of the own slopes. The non-focal coefficients the calculator uses when any
# W_s > 0 come from the offset fit  lm((y - slope_s F) ~ others)  (Frisch-Waugh: it reproduces R0 and R1 exactly).
# A `spec` is a list(id, base, kind ["share"|"pac"|"lobby"], y, f, others [formula right-hand side without the focal term], restd,
# scale, unit, d [the panel]); the panel needs a factor column `sector`.

# DerSimonian-Laird between-industry variance. Slopes with a non-finite value or a non-positive SE are dropped; fewer than two
# usable slopes give tau2 = 0. -> list(tau2, Q, C, k, mu)
rf_dl <- function(own_beta, own_se) {
  ok <- is.finite(own_beta) & is.finite(own_se) & own_se > 0
  b <- own_beta[ok]; v <- own_se[ok]^2; k <- length(b)
  if (k < 2L) return(list(tau2 = 0, Q = 0, C = NA_real_, k = as.integer(k), mu = if (k == 1L) b else NA_real_))
  w <- 1 / v
  mu <- sum(w * b) / sum(w)
  Q <- sum(w * (b - mu)^2)
  C <- sum(w) - sum(w^2) / sum(w)
  list(tau2 = max(0, (Q - (k - 1)) / C), Q = Q, C = C, k = as.integer(k), mu = mu)
}

.rf_slope_key <- function(nm, f, s) {
  k <- c(paste0(f, ":sector", s), paste0("sector", s, ":", f))
  k[k %in% nm][1]
}

rf_spec_formula <- function(spec, rhs_extra = NULL) {
  stats::as.formula(paste(spec$y, "~", spec$others, if (!is.null(rhs_extra)) paste("+", rhs_extra)))
}

# Pooled fit + interaction fit + DL on one training panel (tailoring.R fit_all). Returns list(ok, reason, ...) and, when ok,
# b0, se0, p0, df0, own, se, p, tau2, Q, W, rules = list(R0, R1, R2), df1, secs, f0, V0.
rf_fit_rules <- function(tr, spec, min_df = RF$MIN_DF_OWN) {
  tr$sector <- droplevels(tr$sector)
  secs <- levels(tr$sector)
  if (length(secs) < 2L) return(list(ok = FALSE, reason = "fewer than 2 industries in the training panel", df1 = NA_integer_, secs = secs))
  f0 <- stats::lm(rf_spec_formula(spec, spec$f), data = tr)
  c0 <- summary(f0)$coefficients
  b0 <- if (spec$f %in% rownames(c0)) c0[spec$f, 1] else NA_real_
  se0 <- if (spec$f %in% rownames(c0)) c0[spec$f, 2] else NA_real_
  p0 <- if (spec$f %in% rownames(c0)) c0[spec$f, 4] else NA_real_
  f1 <- stats::lm(rf_spec_formula(spec, paste0(spec$f, ":sector")), data = tr)
  c1 <- summary(f1)$coefficients
  own <- se <- pv <- stats::setNames(rep(NA_real_, length(secs)), secs)
  for (s in secs) {
    k <- .rf_slope_key(rownames(c1), spec$f, s)
    if (!is.na(k)) { own[s] <- c1[k, 1]; se[s] <- c1[k, 2]; pv[s] <- c1[k, 4] }
  }
  df1 <- stats::df.residual(f1)
  ok <- is.finite(b0) && all(is.finite(own)) && all(is.finite(se)) && df1 >= min_df
  if (!ok) return(list(ok = FALSE, reason = sprintf("R1 residual df %d (need >= %d) or an unidentified slope", df1, min_df), df1 = df1, secs = secs,
                       f0 = f0, b0 = b0, se0 = se0, p0 = p0))
  dl <- rf_dl(own, se)
  W <- dl$tau2 / (dl$tau2 + se^2)
  blend <- W * own + (1 - W) * b0
  list(ok = TRUE, reason = "", df1 = df1, secs = secs, b0 = b0, se0 = se0, p0 = p0, df0 = stats::df.residual(f0), own = own, se = se, p = pv,
       tau2 = dl$tau2, Q = dl$Q, C = dl$C, k = dl$k, W = W, f0 = f0, V0 = stats::vcov(f0),
       rules = list(R0 = stats::setNames(rep(b0, length(secs)), secs), R1 = own, R2 = blend))
}

# The offset fit: regress y - slope_s * F on the non-focal terms only.
rf_offset_fit <- function(tr, spec, slopes) {
  tr$sector <- droplevels(tr$sector)
  tr$.yo <- tr[[spec$y]] - slopes[as.character(tr$sector)] * tr[[spec$f]]
  stats::lm(stats::as.formula(paste(".yo ~", spec$others)), data = tr)
}

# Partial pooling on the full panel as given (growth scaling as supplied, i.e. the bundle's full-sample scaling).
# -> list(status "partial" | "pooled", reason, pooled = list(beta, se, p, df), own = data.frame(industry, beta, se, p), tau2, Q, weights,
#         slopes, slope_se, df_interaction, offset_fit)
rf_partial_pool <- function(spec, panel) {
  panel$sector <- droplevels(factor(panel$sector))
  fr <- tryCatch(rf_fit_rules(panel, spec), error = function(e) list(ok = FALSE, reason = conditionMessage(e), df1 = NA_integer_, secs = levels(panel$sector)))
  secs <- fr$secs
  if (!isTRUE(fr$ok)) {
    f0 <- fr$f0 %||% tryCatch(stats::lm(rf_spec_formula(spec, spec$f), data = panel), error = function(e) NULL)
    c0 <- if (!is.null(f0)) summary(f0)$coefficients else NULL
    has <- !is.null(c0) && spec$f %in% rownames(c0)
    b0 <- if (has) c0[spec$f, 1] else NA_real_; se0 <- if (has) c0[spec$f, 2] else NA_real_
    return(list(status = "pooled", reason = fr$reason,
                pooled = list(beta = unname(b0), se = unname(se0), p = if (has) unname(c0[spec$f, 4]) else NA_real_, df = if (!is.null(f0)) as.integer(stats::df.residual(f0)) else NA_integer_),
                own = data.frame(industry = secs, beta = NA_real_, se = NA_real_, p = NA_real_, stringsAsFactors = FALSE),
                tau2 = 0, Q = NA_real_, weights = stats::setNames(rep(0, length(secs)), secs), slopes = stats::setNames(rep(unname(b0), length(secs)), secs),
                slope_se = stats::setNames(rep(unname(se0), length(secs)), secs), df_interaction = fr$df1, offset_fit = NULL))
  }
  slopes <- fr$rules$R2
  list(status = "partial", reason = "",
       pooled = list(beta = unname(fr$b0), se = unname(fr$se0), p = unname(fr$p0), df = as.integer(fr$df0)),
       own = data.frame(industry = secs, beta = unname(fr$own), se = unname(fr$se), p = unname(fr$p), stringsAsFactors = FALSE),
       tau2 = fr$tau2, Q = fr$Q, weights = fr$W, slopes = slopes,
       slope_se = sqrt(fr$W * fr$se^2 + (1 - fr$W)^2 * fr$se0^2), df_interaction = as.integer(fr$df1),
       offset_fit = rf_offset_fit(panel, spec, slopes))
}

# The coordinator's consistency rule (2026-10-01) for replacing a published rule, applied to a backtest prediction table.
#
# A candidate replaces the baseline B (the CURRENT PUBLISHED rule) only if, on the (design, target, industry) rows both have (same folds):
#   (a) its pooled MAE is >= RF_CONS$min_gain lower than B's in BOTH designs (leave-one-election-out and chronological);
#   (b) in EVERY industry its MAE is <= (1 + RF_CONS$ind_tol) x B's MAE in BOTH designs (tolerance for noise);
#   (c) its 95% interval coverage is >= RF_CONS$cov_min in BOTH designs (own rows that carry an interval).
# Owner-mandated components (phase, trajectory, inflation) stay in whatever the rule says; the rule only picks HOW (rf_cons_select), and when no mandated-family
# candidate qualifies the result is a CONFLICT that is reported with its numbers, with the baseline carried beside the mandated candidate.
#
# `pred` = rows of rf_ph_backtest (series, design, target, sector, candidate, actual, pred, half, err). err = 100 x (pred - actual) (log points x100; or share pp x100 never here).

RF_CONS <- list(min_gain = 0.05, ind_tol = 0.02, cov_min = 0.90, designs = c("loeo", "chrono"))

# The mandated family: candidates that carry the phase AND the trajectory component (handling suffix @x22 / @i22 allowed).
RF_CONS_FAMILY_RE <- "^(PT-(G|noG)-(full|rec3|damp)-(pool|ind)(-R[12])?|PBLT-R[012]-(full|rec3|damp)-(pool|ind)|PA-R[012]-T)(@(x22|i22))?$"
rf_cons_family <- function(candidates) candidates[grepl(RF_CONS_FAMILY_RE, candidates)]

# Common rows of a candidate and the baseline in one design: list(c = rows of the candidate, b = rows of the baseline, aligned by (target, sector)).
rf_cons_common <- function(pred, cand, base, design) {
  a <- pred[pred$design == design & pred$candidate == cand, ]
  b <- pred[pred$design == design & pred$candidate == base, ]
  k <- intersect(paste(a$target, a$sector), paste(b$target, b$sector))
  list(c = a[match(k, paste(a$target, a$sector)), ], b = b[match(k, paste(b$target, b$sector)), ])
}

# Per candidate x design: n, pooled MAE of both on the common rows, gain, coverage (own rows with an interval), bias (mean of pred - actual in log points x100 on the common rows).
rf_cons_pooled <- function(pred, base, candidates = unique(pred$candidate), designs = RF_CONS$designs) {
  out <- list()
  for (cd in candidates) for (ds in designs) {
    cm <- rf_cons_common(pred, cd, base, ds)
    if (!nrow(cm$c)) next
    own <- pred[pred$design == ds & pred$candidate == cd, ]
    has <- !is.na(own$half)
    cover <- if (any(has)) mean(abs(own$pred[has] - own$actual[has]) <= own$half[has] + 1e-12) else NA_real_
    mc <- mean(abs(cm$c$err)); mb <- mean(abs(cm$b$err))
    out[[length(out) + 1L]] <- data.frame(candidate = cd, design = ds, n_common = nrow(cm$c), MAE = mc, MAE_base = mb, gain = if (mb > 0) 1 - mc / mb else NA_real_,
                                          coverage = cover, coverage_n = sum(has), bias = mean(cm$c$err), n_own = nrow(own), MAE_own = mean(abs(own$err)), stringsAsFactors = FALSE)
  }
  r <- do.call(rbind, out)
  rownames(r) <- NULL
  r
}

# Per candidate x design x industry: MAE of both on the common rows and the ratio.
rf_cons_industry <- function(pred, base, candidates = unique(pred$candidate), designs = RF_CONS$designs) {
  out <- list()
  for (cd in candidates) for (ds in designs) {
    cm <- rf_cons_common(pred, cd, base, ds)
    if (!nrow(cm$c)) next
    for (s in sort(unique(as.character(cm$c$sector)))) {
      ix <- as.character(cm$c$sector) == s
      mc <- mean(abs(cm$c$err[ix])); mb <- mean(abs(cm$b$err[ix]))
      out[[length(out) + 1L]] <- data.frame(candidate = cd, design = ds, sector = s, n = sum(ix), MAE = mc, MAE_base = mb, ratio = if (mb > 0) mc / mb else NA_real_,
                                            bias = mean(cm$c$err[ix]), stringsAsFactors = FALSE)
    }
  }
  r <- do.call(rbind, out)
  rownames(r) <- NULL
  r
}

# Verdict per candidate: criteria a, b, c, the worst industry ratio, and QUALIFIES (all three).
rf_cons_verdict <- function(pooled, industry, cfg = RF_CONS) {
  tol <- RF$RULE_TIE_TOL
  rows <- lapply(unique(pooled$candidate), function(cd) {
    p <- pooled[pooled$candidate == cd, ]; i <- industry[industry$candidate == cd, ]
    have <- all(cfg$designs %in% p$design)
    a <- have && all(vapply(cfg$designs, function(ds) { r <- p[p$design == ds, ]; r$MAE < r$MAE_base - tol && r$MAE <= r$MAE_base * (1 - cfg$min_gain) + tol }, NA))
    b <- have && nrow(i) > 0L && all(i$ratio <= 1 + cfg$ind_tol + 1e-12, na.rm = TRUE)
    cv <- vapply(cfg$designs, function(ds) { r <- p[p$design == ds, ]; if (nrow(r)) r$coverage else NA_real_ }, 0)
    cc <- if (anyNA(cv)) NA else all(cv >= cfg$cov_min - 1e-12)
    worst <- if (nrow(i)) i[which.max(i$ratio), ] else NULL
    data.frame(candidate = cd, a_pooled5 = a, b_every_industry = b, c_coverage90 = cc, qualifies = isTRUE(a) && isTRUE(b) && isTRUE(cc),
               gain_loeo = p$gain[p$design == "loeo"][1], gain_chrono = p$gain[p$design == "chrono"][1], cov_loeo = cv[[1]], cov_chrono = cv[[2]],
               worst_ratio = if (!is.null(worst)) worst$ratio else NA_real_, worst_industry = if (!is.null(worst)) worst$sector else NA_character_,
               worst_design = if (!is.null(worst)) worst$design else NA_character_, n_industries_worse = sum(i$ratio > 1 + cfg$ind_tol + 1e-12, na.rm = TRUE),
               mean_MAE = mean(p$MAE), stringsAsFactors = FALSE)
  })
  r <- do.call(rbind, rows)
  rownames(r) <- NULL
  r
}

# Select the mandated-family candidate under the consistency rule. -> list(choice, status ("QUALIFIES" | "CONFLICT"), qualifying, verdict, pooled, industry, reason)
rf_cons_select <- function(pred, base, family = rf_cons_family(unique(pred$candidate)), cfg = RF_CONS) {
  pooled <- rf_cons_pooled(pred, base, c(base, family), cfg$designs)
  industry <- rf_cons_industry(pred, base, c(base, family), cfg$designs)
  vd <- rf_cons_verdict(pooled[pooled$candidate != base, ], industry[industry$candidate != base, ], cfg)
  qual <- vd$candidate[vd$qualifies]
  if (length(qual)) {
    ch <- vd$candidate[vd$candidate %in% qual][which.min(vd$mean_MAE[vd$candidate %in% qual])]
    return(list(choice = ch, status = "QUALIFIES", qualifying = qual, verdict = vd, pooled = pooled, industry = industry,
                reason = sprintf("%s qualifies against %s (>= %g%% pooled in both designs, every industry within +%g%%, coverage >= %g%%); %d mandated-family candidates qualify", ch, base, 100 * cfg$min_gain, 100 * cfg$ind_tol, 100 * cfg$cov_min, length(qual))))
  }
  cov_ok <- vd$candidate[!is.na(vd$c_coverage90) & vd$c_coverage90]
  pool <- if (length(cov_ok)) cov_ok else vd$candidate
  ch <- pool[which.min(vd$mean_MAE[match(pool, vd$candidate)])]
  r <- vd[vd$candidate == ch, ]
  list(choice = ch, status = "CONFLICT", qualifying = character(), verdict = vd, pooled = pooled, industry = industry,
       reason = sprintf("no mandated-family candidate satisfies the rule against %s; carried as the primary (mandate stays in): %s [%s, gain LOEO %+.1f%% / chrono %+.1f%%, %d industr%s worse than +%g%%, coverage %.0f%% / %.0f%%]; the published form is carried beside it",
                        base, ch, if (length(cov_ok)) "lowest mean MAE among coverage >= 90% candidates" else "lowest mean MAE (no candidate reaches 90% coverage)", 100 * r$gain_loeo, 100 * r$gain_chrono,
                        r$n_industries_worse, if (r$n_industries_worse == 1L) "y" else "ies", 100 * cfg$ind_tol, 100 * r$cov_loeo, 100 * r$cov_chrono))
}

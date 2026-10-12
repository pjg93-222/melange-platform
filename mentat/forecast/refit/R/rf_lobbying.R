# Lobbying level sample and first differences rebuilt from workbooks 01 (LobbyView sector-year) and 06 (IBISWorld predicted
# growth). Replaces the SGS-study dependency of the Codex chain (spec 1.6); proven in lobby_sample_probe.R, telecom_probe.R
# and tel_y.R: y <= 4.8e-13, g exact, telecom g <= 3.6e-15, first differences <= 5.3e-15.

# LobbyView sector-year rows the fit may use: study sector in the registry, confidence in the registry's list for that
# sector, status 'observed', coverage flag without 'thin'. y_real_m = de-duplicated real 2025 amount in $ million.
# -> study_sector, year, y_real_m, coverage_flag
rf_lobbyview_rows <- function(lobby_xlsx, reg) {
  lv <- rf_read_xlsx(lobby_xlsx, "lobbyview_sector_year_all")
  need <- c("study_sector", "confidence", "year", "status", "coverage_flag", "lobby_amount_dedup_real_2025_usd")
  if (!all(need %in% names(lv))) rf_raise("PanelError", paste("workbook 01 lacks columns:", paste(setdiff(need, names(lv)), collapse = ", ")))
  keep <- rep(FALSE, nrow(lv))
  for (i in reg$industries) {
    if (!isTRUE(i$models$lobbying)) next
    ss <- i$lobbyview$study_sector
    keep <- keep | (lv$study_sector == ss & lv$confidence %in% unlist(i$lobbyview$confidence))
  }
  thin <- grepl("thin", lv$coverage_flag, fixed = TRUE)
  ok <- keep & lv$status == "observed" & !thin & !is.na(lv$lobby_amount_dedup_real_2025_usd)
  out <- data.frame(study_sector = lv$study_sector[ok], year = as.integer(lv$year[ok]),
                    y_real_m = lv$lobby_amount_dedup_real_2025_usd[ok] / 1e6, coverage_flag = lv$coverage_flag[ok], stringsAsFactors = FALSE)
  out <- out[order(out$study_sector, out$year), ]
  if (anyDuplicated(out[c("study_sector", "year")])) rf_raise("PanelError", "duplicate LobbyView sector-year rows")
  rownames(out) <- NULL
  out
}

# IBISWorld predicted annual growth per study sector and year. 'direct' sectors read workbook 06 sheet data (status observed);
# revenue_weighted_components sectors (telecom) read sheet component_forecasts (record_source telecom_component_selected):
# g = sum_c g_c w_c / sum_c w_c over the registry's components, w_c = baseline_revenue_same_vintage.
# -> study_sector, year, g, publication_month
rf_predicted_growth <- function(pred_xlsx, reg) {
  d <- rf_read_xlsx(pred_xlsx, "data")
  cf <- rf_read_xlsx(pred_xlsx, "component_forecasts")
  out <- list()
  for (i in reg$industries) {
    if (!isTRUE(i$models$lobbying)) next
    ss <- i$ibisworld$predicted_study_sector
    rule <- i$ibisworld$predicted_rule
    if (identical(rule, "direct")) {
      q <- d[d$study_sector == ss & d$status == "observed" & !is.na(d$predicted_growth_pct), ]
      if (nrow(q)) out[[length(out) + 1L]] <- data.frame(study_sector = ss, year = as.integer(q$year), g = q$predicted_growth_pct,
                                                          publication_month = as.character(q$forecast_publication_month), stringsAsFactors = FALSE)
    } else if (identical(rule, "revenue_weighted_components")) {
      comps <- unlist(i$ibisworld$components)
      q <- cf[cf$record_source == "telecom_component_selected" & cf$component_code %in% comps, ]
      for (yr in sort(unique(q$target_year))) {
        z <- q[q$target_year == yr, ]
        if (!setequal(z$component_code, comps) || anyDuplicated(z$component_code)) next
        w <- z$baseline_revenue_same_vintage
        if (anyNA(w) || anyNA(z$predicted_growth_pct) || sum(w) <= 0) next
        out[[length(out) + 1L]] <- data.frame(study_sector = ss, year = as.integer(yr), g = sum(z$predicted_growth_pct * w) / sum(w),
                                              publication_month = paste(sort(unique(as.character(z$publication_month))), collapse = "+"), stringsAsFactors = FALSE)
      }
    }
  }
  do.call(rbind, out)
}

# Complete-case level sample: LobbyView y and predicted g both present, years 2008-2025 -> study_sector, year, y, g, publication_month
rf_lobby_levels <- function(lv, g, years = 2008:2025) {
  m <- merge(lv[, c("study_sector", "year", "y_real_m")], g, by = c("study_sector", "year"))
  m <- m[m$year %in% years, ]
  names(m)[names(m) == "y_real_m"] <- "y"
  m <- m[order(m$study_sector, m$year), c("study_sector", "year", "y", "g", "publication_month")]
  rownames(m) <- NULL
  m
}

# First differences over consecutive calendar years present in both years of the level sample.
# -> study_sector, year, y (level), g, y_lag, g_lag, dy, dg
rf_lobby_diffs <- function(levels) {
  lag <- levels[, c("study_sector", "year", "y", "g")]
  lag$year <- lag$year + 1L
  names(lag)[3:4] <- c("y_lag", "g_lag")
  m <- merge(levels[, c("study_sector", "year", "y", "g")], lag, by = c("study_sector", "year"))
  m$dy <- m$y - m$y_lag
  m$dg <- m$g - m$g_lag
  m <- m[order(m$study_sector, m$year), ]
  rownames(m) <- NULL
  m
}

# Persistence errors of the LobbyView level series (owner rulings 2026-10-08): e = log(level[t + h]) - log(level[t]) for h = 1..3 and target years `targets` (the blind backtest
# window), by study sector and pooled over every sector in `lv` (before the merge with predicted growth, so every sector the registry lists is in). A quantile is split-conformal:
# the ceiling((n + 1) p)-th smallest |e|, NA when n is too small for it. -> list(sector = list(<study sector> = list(<h> = list(n, q80))), pooled = list(<h> = list(n, q80, q95))).
rf_lobby_persistence <- function(lv, targets = 2014:2023, horizons = 1:3) {
  q <- function(e, p) { a <- sort(abs(e)); k <- ceiling((length(a) + 1) * p); if (k >= 1 && k <= length(a)) unname(a[k]) else NA_real_ }
  lv <- lv[lv$year >= 2008L & lv$year <= 2025L, ]
  err <- lapply(split(lv, lv$study_sector), function(g) {
    y <- stats::setNames(log(g$y_real_m), g$year)
    stats::setNames(lapply(horizons, function(h) { t <- targets[targets %in% g$year & (targets - h) %in% g$year]; unname(y[as.character(t)] - y[as.character(t - h)]) }), horizons)
  })
  list(sector = lapply(err, function(by_h) lapply(by_h, function(e) list(n = length(e), q80 = q(e, 0.80)))),
       pooled = stats::setNames(lapply(as.character(horizons), function(h) { e <- unlist(lapply(err, function(x) x[[h]])); list(n = length(e), q80 = q(e, 0.80), q95 = q(e, 0.95)) }), horizons))
}

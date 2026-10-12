# Cycle growth and the CPI deflator: port of loo/work/opensecrets_baseline/build_cycles.py:10-55 and build_test.py:36-52.
# Inputs: workbook 07 (actual industry revenue, IBISWorld, USD million) and workbook 00 (CPI-U annual averages).

# CPI table: year, cpi (annual average, 1982-84 = 100) and multiplier_2025 = cpi[2025] / cpi[year].
rf_cpi <- function(controls_xlsx, base_year = RF$BASE_YEAR) {
  d <- rf_read_xlsx(controls_xlsx, "data")
  stopifnot(all(c("year", "cpi_annual_average") %in% names(d)))
  out <- data.frame(year = as.integer(d$year), cpi = as.numeric(d$cpi_annual_average), stringsAsFactors = FALSE)
  out <- out[order(out$year), ]
  if (!(base_year %in% out$year)) rf_raise("PanelError", sprintf("CPI has no %d row", base_year))
  out$multiplier_2025 <- out$cpi[out$year == base_year] / out$cpi
  attr(out, "inputs") <- list(controls = controls_xlsx)
  out
}

# Multiplier of an election cycle: cpi[2025] / mean(cpi[t-1], cpi[t])  (build_test.py:37).
rf_cycle_multiplier <- function(cpi, cycle_end, base_year = RF$BASE_YEAR) {
  v <- stats::setNames(cpi$cpi, cpi$year)
  base <- v[[as.character(base_year)]]
  vapply(cycle_end, function(t) {
    a <- v[as.character(t - 1)]; b <- v[as.character(t)]
    if (is.na(a) || is.na(b)) NA_real_ else base / ((a + b) / 2)
  }, 0)
}

# Cycle revenue growth per sector (build_cycles.py):
#   level      = revenue_million_constant_2026_usd, else revenue_million_report_price_basis
#   cycle(t)   = level(t-1) + level(t), NA if either year is missing
#   growth(t)  = (cycle(t) / cycle(t-2) - 1) * 100 ; prior(t) = growth(t-2)          cycles 2008 ... 2024
# -> sector, cycle_end, cycle_total_million, cycle_revenue_growth_pct, prior_cycle_revenue_growth_pct, price_basis,
#    report_vintage, source_url, ibisworld_code
#
# `supplement` = 07-shaped rows of a current-year ESTIMATE (property and casualty 2024, IBISWorld 52412 edition 2024-11; owner ruling 2026-09-30). A supplement row is
# used only for a (sector, year) that workbook 07 does not hold, and only from the SAME report vintage as that sector's series (vintages are never spliced): the day a later
# edition puts the year in the main series the estimate drops out. attr "estimates" lists the supplement rows that were used.
rf_cycle_growth <- function(actual_xlsx, sectors = NULL, cycles = seq(2008, 2024, 2), supplement = NULL, components = NULL) {
  g <- rf_read_xlsx(actual_xlsx, "data")
  need <- c("sector", "year", "revenue_million_constant_2026_usd", "revenue_million_report_price_basis", "status")
  if (!all(need %in% names(g))) rf_raise("PanelError", paste("workbook 07 lacks columns:", paste(setdiff(need, names(g)), collapse = ", ")))
  if (!all(g$status == "observed")) rf_raise("PanelError", "workbook 07 has rows whose status is not 'observed'")
  if (!is.null(sectors)) g <- g[g$sector %in% c(sectors, unlist(components)), ]
  used <- NULL
  if (!is.null(supplement) && nrow(supplement)) {
    sp <- supplement[supplement$sector %in% unique(g$sector) & !(paste(supplement$sector, supplement$year) %in% paste(g$sector, g$year)), , drop = FALSE]
    for (s in unique(sp$sector)) {
      main <- unique(g$report_vintage[g$sector == s]); mine <- unique(sp$report_vintage[sp$sector == s])
      if (length(main) != 1L || !identical(main, mine)) rf_raise("PanelError", sprintf("the estimate row of %s is from vintage %s but its series is %s: editions are never spliced", s, paste(mine, collapse = "/"), paste(main, collapse = "/")))
    }
    if (nrow(sp)) {
      cols <- intersect(names(g), names(sp))
      g <- rf_rbind_fill(list(g, sp[, cols, drop = FALSE]))
      used <- sp[, intersect(c("sector", "year", "report_vintage", "value_type", "source_pdf_sha256"), names(sp)), drop = FALSE]
    }
  }
  if (length(components)) {
    g <- rf_combine_components(g, components)
    if (!is.null(used)) for (s in names(components)) used$sector[used$sector %in% components[[s]]] <- s     # an estimate row of a component is the combined industry's
  }
  out <- lapply(split(g, g$sector), function(q) {
    lvl <- ifelse(is.na(q$revenue_million_constant_2026_usd), q$revenue_million_report_price_basis, q$revenue_million_constant_2026_usd)
    at <- function(y) { v <- lvl[q$year == y]; if (length(v) == 1L) v else NA_real_ }
    ct <- function(e) at(e - 1) + at(e)
    rate <- function(e) (ct(e) / ct(e - 2) - 1) * 100
    data.frame(sector = q$sector[1], cycle_end = as.integer(cycles), cycle_total_million = vapply(cycles, ct, 0),
               cycle_revenue_growth_pct = vapply(cycles, rate, 0), prior_cycle_revenue_growth_pct = vapply(cycles - 2, rate, 0),
               price_basis = q$revenue_price_basis[!is.na(q$revenue_price_basis)][1] %||% NA_character_,
               report_vintage = q$report_vintage[1], source_url = q$source_url[1], ibisworld_code = as.character(q$ibisworld_code[1]),
               stringsAsFactors = FALSE)
  })
  out <- do.call(rbind, out)
  rownames(out) <- NULL
  attr(out, "inputs") <- list(actual = actual_xlsx)
  attr(out, "estimates") <- used
  out
}

# A combined industry as the REVENUE-WEIGHTED sum of workbook-07 components (Telecom, owner ruling 2026-10-08: wireless + cable): its revenue in a year is the sum of the components' levels, defined
# only for the years every component has. The cycle growth of the sum equals the average of the components' cycle growths weighted by each one's baseline-cycle revenue. The components must be on
# one price basis; their editions may differ (each is a single vintage of its own). `components` = list(<combined sector> = c(<07 sector>, ...)). The component rows are replaced by the combined ones.
rf_combine_components <- function(g, components) {
  level <- function(q) ifelse(is.na(q$revenue_million_constant_2026_usd), q$revenue_million_report_price_basis, q$revenue_million_constant_2026_usd)
  keep <- g[!(g$sector %in% unlist(components)), , drop = FALSE]
  for (s in names(components)) {
    cs <- components[[s]]
    parts <- lapply(cs, function(cc) g[g$sector == cc, , drop = FALSE])
    if (any(vapply(parts, nrow, 0L) == 0L)) rf_raise("PanelError", sprintf("workbook 07 has no rows for a component of %s (%s)", s, paste(cs, collapse = ", ")))
    basis <- unique(unlist(lapply(parts, function(q) q$revenue_price_basis[!is.na(q$revenue_price_basis)])))
    if (length(basis) != 1L) rf_raise("PanelError", sprintf("the components of %s are on different price bases (%s): their revenue cannot be weighted", s, paste(basis, collapse = " / ")))
    yrs <- sort(Reduce(intersect, lapply(parts, function(q) q$year)))
    tot <- rowSums(do.call(cbind, lapply(parts, function(q) level(q)[match(yrs, q$year)])))
    row <- parts[[1]][match(yrs, parts[[1]]$year), , drop = FALSE]
    for (cl in setdiff(names(row), c("year", "status"))) row[[cl]] <- NA
    row$sector <- s; row$revenue_million_report_price_basis <- tot; row$revenue_million_constant_2026_usd <- NA_real_; row$revenue_price_basis <- basis
    row$report_vintage <- paste(vapply(parts, function(q) q$report_vintage[1], ""), collapse = "+")
    row$source_url <- paste(vapply(parts, function(q) q$source_url[1], ""), collapse = " ; ")
    row$ibisworld_code <- paste(vapply(parts, function(q) as.character(q$ibisworld_code[1]), ""), collapse = "+")
    keep <- rf_rbind_fill(list(keep, row))
  }
  keep
}

# The 07-shaped estimate rows of the manifest (roles "ibis_actual_current:<industry>"), for the industries of `ids`. NULL when there are none.
rf_growth_supplement <- function(inputs, ids) {
  roles <- paste0("ibis_actual_current:", ids)
  roles <- roles[roles %in% inputs$role]
  if (!length(roles)) return(NULL)
  rows <- lapply(roles, function(r) utils::read.csv(rf_input_path(inputs, r), stringsAsFactors = FALSE, na.strings = c("", "NA"), colClasses = c(ibisworld_code = "character", report_vintage = "character")))
  out <- rf_rbind_fill(rows)
  attr(out, "roles") <- roles
  out
}

# Standardize growth per sector with the mean / sd (ddof = 1) of the calibration rows (build_test.py:44-52; analyze.py prepare()).
# The prior-cycle value uses the same mu and sd. attr "scaling" holds sector, mean, sd, cycles.
rf_standardize <- function(d, calibration = d, by = "sector") {
  d$G_current <- NA_real_
  d$G_prior <- NA_real_
  sc <- list()
  for (s in unique(d[[by]])) {
    q <- calibration[calibration[[by]] == s, ]
    mu <- mean(q$cycle_revenue_growth_pct)
    sdv <- stats::sd(q$cycle_revenue_growth_pct)
    if (!is.finite(sdv) || sdv <= 0) rf_raise("PanelError", paste("growth has no variation for", s))
    ix <- d[[by]] == s
    d$G_current[ix] <- (d$cycle_revenue_growth_pct[ix] - mu) / sdv
    d$G_prior[ix] <- (d$prior_cycle_revenue_growth_pct[ix] - mu) / sdv
    sc[[length(sc) + 1L]] <- data.frame(sector = s, mean = mu, sd = sdv, cycles = nrow(q), stringsAsFactors = FALSE)
  }
  attr(d, "scaling") <- do.call(rbind, sc)
  d
}

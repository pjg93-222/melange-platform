# Panel assembly: the PAC growth panel (= regression_panel.csv), the share-model panels (= analysis_case_panels.csv), the
# 48 stepwise cases, and the lobbying design (= year_effects__all71.csv), each rebuilt from raw inputs by the builders of
# rf_growth.R, rf_opensecrets.R, rf_political.R and rf_lobbying.R. Every panel carries attr "inputs" and attr "checks".
# Sector factor levels are always sorted alphabetically (reference = first); cycle and year factors reference the earliest.

# PAC growth panel: OpenSecrets totals (nominal) + cycle growth (IBISWorld) + CPI deflator, for every PAC-capable industry.
# Cycles 2008-2024 are inner-joined to growth and kept when both current and prior growth exist; money is deflated to 2025 USD
# with cpi[2025] / mean(cpi[t-1], cpi[t]); growth is standardized per industry over the panel's own cycles.
rf_pac_panel <- function(reg, inputs) {
  cpi <- rf_cpi(rf_input_path(inputs, "cpi"))
  ids <- rf_capable(reg, "pac")
  actual <- vapply(ids, function(id) reg$industries[[id]]$ibisworld$actual_sector, "")
  supp <- rf_growth_supplement(inputs, ids)
  growth <- rf_cycle_growth(rf_input_path(inputs, "ibis_actual"), sectors = actual, supplement = supp, components = rf_actual_components(reg, ids))
  estimates <- attr(growth, "estimates")
  used <- c("cpi", "ibis_actual", if (!is.null(estimates)) attr(supp, "roles"))
  parts <- lapply(ids, function(id) {
    role <- paste0("os_totals:", id)
    used <<- c(used, role)
    tot <- rf_os_totals(rf_input_path(inputs, role))
    tot <- tot[tot$cycle_end >= 2008 & tot$cycle_end <= 2024, ]
    g <- growth[growth$sector == reg$industries[[id]]$ibisworld$actual_sector, ]
    g$sector <- id
    names(g)[names(g) == "source_url"] <- "ibis_source_url"   # keep the OpenSecrets capture URL as source_url
    cbind(data.frame(opensecrets_code = reg$industries[[id]]$opensecrets$codes[[1]], sector = id, stringsAsFactors = FALSE),
          merge(tot, g[, setdiff(names(g), "sector")], by = "cycle_end"))
  })
  p <- do.call(rbind, parts)
  p <- p[!is.na(p$cycle_revenue_growth_pct) & !is.na(p$prior_cycle_revenue_growth_pct), ]
  p <- p[order(p$sector, p$cycle_end), ]
  p$multiplier_to_2025_dollars <- rf_cycle_multiplier(cpi, p$cycle_end)
  for (ch in c("total", "individual", "pac", "outside")) {
    p[[paste0(ch, "_real_2025")]] <- p[[paste0(ch, "_nominal")]] * p$multiplier_to_2025_dollars
    p[[paste0("log_", ch)]] <- log(p[[paste0(ch, "_real_2025")]])
  }
  p <- rf_standardize(p, p, "sector")
  sc <- attr(p, "scaling")
  p$Neg_prior <- as.integer(p$prior_cycle_revenue_growth_pct < 0)
  p$presidential_cycle <- as.integer(p$cycle_end %% 4 == 0)
  rownames(p) <- NULL
  attr(p, "scaling") <- sc
  attr(p, "estimates") <- estimates              # growth rows that are IBISWorld current-year estimates (property and casualty 2024), or NULL
  attr(p, "inputs") <- rf_input_records(inputs, used)
  attr(p, "checks") <- rf_panel_checks(p, "pac")
  p
}

# The workbook-07 components of the industries whose actual growth is a revenue-weighted combination (Telecom: wireless + cable), as rf_cycle_growth takes them: list(<actual sector> = c(<07 sector>, ...)), or NULL.
rf_actual_components <- function(reg, ids) {
  cs <- stats::setNames(lapply(ids, function(id) unlist(reg$industries[[id]]$ibisworld$actual_components)), vapply(ids, function(id) reg$industries[[id]]$ibisworld$actual_sector %||% id, ""))
  cs <- Filter(length, cs)
  if (length(cs)) cs else NULL
}

# The panel of the SCORED-ONLY industries (registry `estimation: "scored_only"`, ready data; Telecom): the same construction as the PAC panel (OpenSecrets totals, IBISWorld cycle growth, CPI deflator),
# built for those industries alone. It never enters the pooled estimation; the phase block scores it on the pooled parameters (rf_ph_scored_step_variance) and the bundle writer reads its history.
# -> list(ids, panel) or NULL when the registry names none.
rf_scored_panel <- function(reg, inputs) {
  ids <- reg$scored
  if (!length(ids)) return(NULL)
  only <- reg
  for (id in names(only$industries)) only$industries[[id]]$models$pac <- id %in% ids
  list(ids = ids, panel = rf_pac_panel(only, inputs))
}

# Share-model panel for one signal ("control": IEM Jan-Jun score; "seats": Crystal Ball final call) and chamber.
# money (congressional account) joined to the signal and to RMajNow; rows missing any model column are dropped
# (audit_forecast_files.py:52-57). -> sorted by cycle_end, sector
rf_share_panel <- function(money, signal, chamber, iem_jj = NULL, seat_calls = NULL, current_control) {
  m <- money[money$chamber == chamber, ]
  if (signal == "control") {
    s <- iem_jj[iem_jj$chamber == chamber, c("cycle_end", "P_R", "P_other", "days")]
    p <- merge(m, s, by = "cycle_end")
    p$Forecast <- p$P_R
  } else if (signal == "seats") {
    s <- seat_calls[seat_calls$chamber == chamber, c("cycle_end", "pred_rep_net_change")]
    p <- merge(m, s, by = "cycle_end")
    p$Forecast <- p$pred_rep_net_change
  } else rf_raise("PanelError", paste("unknown signal", signal))
  cc <- current_control[current_control$chamber == chamber, ]
  p$RMajNow <- cc$RMajNow[match(p$cycle_end, cc$year)]
  p <- p[stats::complete.cases(p[, c("Forecast", "G_current", "G_prior", "RShare", "RMajNow")]), ]
  p$signal <- signal
  p <- p[order(p$cycle_end, p$sector), ]
  rownames(p) <- NULL
  attr(p, "checks") <- rf_panel_checks(p, "share")
  p
}

# The stepwise grid: {control, seats} x {House, Senate} x {pooled + each industry} x {RShare, log_R, log_D}, in the loop order of
# analyze.py (signal, chamber, scope, outcome); `counter` is the 1-based position that seeds the selection bootstrap.
# `shares` = list(control_House=, control_Senate=, seats_House=, seats_Senate=). Returns one long data.frame with a `case` column.
rf_case_panels <- function(shares, outcomes = c("RShare", "log_R", "log_D")) {
  out <- list(); counter <- 0L
  for (signal in c("control", "seats")) for (chamber in c("House", "Senate")) {
    z <- shares[[paste(signal, chamber, sep = "_")]]
    scopes <- c("pooled", rf_sorted(z$sector))
    for (scope in scopes) for (oc in outcomes) {
      counter <- counter + 1L
      d <- if (scope == "pooled") z else z[z$sector == scope, ]
      d <- d[order(d$cycle_end, d$sector), ]
      d$case <- paste(signal, chamber, scope, oc, sep = "__")
      d$scope <- scope
      d$outcome <- oc
      d$counter <- counter
      out[[length(out) + 1L]] <- d
    }
  }
  rf_rbind_fill(out)
}

# Lobbying design: level sample (77 rows), first differences (71 pairs), and the dy ~ dg + factor(year) design matrix.
rf_lobby_panel <- function(reg, inputs, years = 2008:2025) {
  lv <- rf_lobbyview_rows(rf_input_path(inputs, "lobbyview_sector_year"), reg)
  g <- rf_predicted_growth(rf_input_path(inputs, "ibis_predicted"), reg)
  levels <- rf_lobby_levels(lv, g, years)
  diffs <- rf_lobby_diffs(levels)
  d <- diffs
  d$year_f <- factor(d$year)
  X <- stats::model.matrix(~ dg + year_f, d)
  colnames(X) <- sub("^year_f", "factor(year)", colnames(X))
  attr(diffs, "checks") <- rf_panel_checks(diffs, "lobby")
  attr(diffs, "inputs") <- rf_input_records(inputs, c("lobbyview_sector_year", "ibis_predicted"))
  list(lv = lv, g = g, levels = levels, diffs = diffs, design_matrix = X)
}

# Named checks of a panel: -> data.frame(name, rows_examined, failures, detail). failures == 0 everywhere means the panel is valid.
rf_panel_checks <- function(panel, kind) {
  n <- nrow(panel)
  chk <- list()
  add <- function(name, failures, detail = "") chk[[length(chk) + 1L]] <<- rf_check(name, n, failures, detail)
  key <- switch(kind, pac = c("sector", "cycle_end"), money = c("sector", "cycle_end", "chamber"), share = c("sector", "cycle_end"),
                lobby = c("study_sector", "year"), stop("unknown panel kind ", kind))
  add("unique keys", sum(duplicated(panel[key])), paste(key, collapse = "+"))
  model_cols <- switch(kind,
    pac = c("log_pac", "G_current", "G_prior", "Neg_prior", "cycle_end"),
    money = c("RShare", "log_R", "log_D", "G_current", "G_prior", "Pres"),
    share = c("RShare", "log_R", "log_D", "Forecast", "RMajNow", "Pres", "G_prior", "G_current"),
    lobby = c("dy", "dg"))
  add("no NA in model columns", sum(!stats::complete.cases(panel[, model_cols])), paste(model_cols, collapse = ", "))
  if (kind == "pac") {
    add("total = individual + PAC + outside", sum(panel$total_nominal != panel$individual_nominal + panel$pac_nominal + panel$outside_nominal))
    add("positive money for logs", sum(!(panel$total_real_2025 > 0 & panel$individual_real_2025 > 0 & panel$pac_real_2025 > 0 & panel$outside_real_2025 > 0)))
    sds <- tapply(panel$cycle_revenue_growth_pct, panel$sector, stats::sd)
    add("growth varies within industry", sum(!(sds > 0)))
  } else if (kind == "money") {
    add("D+R+other = total", sum(panel$D_nominal + panel$R_nominal + panel$other_nominal != panel$total_nominal))
    add("0 <= RShare <= 1", sum(!(panel$RShare >= 0 & panel$RShare <= 1)))
    add("positive money for logs", sum(!(panel$D_nominal > 0 & panel$R_nominal > 0)))
  } else if (kind == "share") {
    add("0 <= RShare <= 1", sum(!(panel$RShare >= 0 & panel$RShare <= 1)))
    add("positive money for logs", sum(!(panel$D_nominal > 0 & panel$R_nominal > 0)))
    add("forecast varies", as.integer(!(stats::sd(panel$Forecast) > 0)))
  } else if (kind == "lobby") {
    add("finite differences", sum(!(is.finite(panel$dy) & is.finite(panel$dg))))
    add("growth change varies", as.integer(!(stats::sd(panel$dg) > 0)))
  }
  do.call(rbind, chk)
}

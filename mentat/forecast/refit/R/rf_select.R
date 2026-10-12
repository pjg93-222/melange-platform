# Slope-rule auto-pick (Paul's rulings of 2026-09-30):
#
#   For each term (PAC, control House, control Senate, lobbying) run the no-leakage backtests for R0 pooled / R1 own / R2 DL blend in
#   BOTH designs (leave-one-election-out and chronological, fold-restandardized growth, the calculator path with that term's anchor).
#   A non-pooled rule is chosen only if it beats the POOLED rule R0 by at least RF$RULE_MIN_GAIN (5%) in EVERY design; lobbying has a
#   single rolling-origin design, which then must clear the bar alone. If both R1 and R2 clear it, the one with the lower MAE in both
#   designs is chosen and the DL blend R2 is used when the designs disagree. If neither clears it, the pooled slope R0 is used.
#   The full comparison table and the choice are recorded for the receipt and the review packet.
#
# On today's data: PAC -> R1 (own slopes, 51% and 56% better than pooled); control House, control Senate and lobbying -> R0 (no rule
# beats pooled by 5% in both designs: e.g. Senate R1 is 1.2% better in leave-one-election-out, lobbying R1 0.8%).
# Library code. Interpretation notes:
#   * a rule "beats" R0 in a design when MAE_rule < MAE_R0 * (1 - min_gain) - tol; min_gain = 0 means strictly lower MAE;
#   * R2 choosing pooled slopes (tau2 = 0 on the full sample) is flagged as resolved_to_pooled and reported as R0.

# The choice from a comparison table (design, rule, MAE). Pure function, unit-tested.
# -> list(choice, winner_by_design, qualifying, reason)
rf_choose_rule <- function(table, designs = unique(table$design), min_gain = RF$RULE_MIN_GAIN, tol = RF$RULE_TIE_TOL) {
  mae <- function(ds, rule) table$MAE[table$design == ds & table$rule == rule]
  # a rule beats pooled in a design when it is strictly lower AND at least min_gain (relative) lower
  clears <- function(rule) all(vapply(designs, function(ds) mae(ds, rule) < mae(ds, "R0") - tol && mae(ds, rule) <= mae(ds, "R0") * (1 - min_gain) + tol, logical(1)))
  winner <- vapply(designs, function(ds) {
    m <- c(R0 = mae(ds, "R0"), R1 = mae(ds, "R1"), R2 = mae(ds, "R2"))
    tied <- names(m)[m <= min(m) + tol]
    c("R0", "R2", "R1")[c("R0", "R2", "R1") %in% tied][1]          # simplest tied rule: R0, then R2, then R1
  }, "")
  names(winner) <- designs
  qual <- Filter(clears, c("R1", "R2"))
  if (!length(qual)) {
    return(list(choice = "R0", winner_by_design = winner, qualifying = character(),
                reason = sprintf("no rule beats the pooled slope R0 by %g%% in %s: the pooled slope is used", 100 * min_gain,
                                 if (length(designs) > 1L) "both designs" else paste("the", designs, "design"))))
  }
  if (length(qual) == 1L) {
    return(list(choice = qual, winner_by_design = winner, qualifying = qual,
                reason = sprintf("%s beats the pooled slope by %g%% or more in %s and is the only rule that does", qual, 100 * min_gain,
                                 if (length(designs) > 1L) "both designs" else paste("the", designs, "design"))))
  }
  best_everywhere <- all(vapply(designs, function(ds) mae(ds, "R1") < mae(ds, "R2") - tol, logical(1)))
  r2_everywhere <- all(vapply(designs, function(ds) mae(ds, "R2") < mae(ds, "R1") - tol, logical(1)))
  if (best_everywhere) return(list(choice = "R1", winner_by_design = winner, qualifying = qual, reason = "R1 has the lowest MAE in every design among the rules that beat pooled"))
  if (r2_everywhere) return(list(choice = "R2", winner_by_design = winner, qualifying = qual, reason = "R2 has the lowest MAE in every design among the rules that beat pooled"))
  list(choice = "R2", winner_by_design = winner, qualifying = qual, reason = "the designs disagree on R1 and R2: the DL blend R2 is used")
}

# Backtest one spec in its designs and pick the rule. `path` = the calculator path of the term's anchor ("prevchg" for PAC and the
# previous-cycle share anchor, "calc" for lobbying). -> list(term, base, path, designs, table, winner_by_design, choice, reported_rule,
# resolved_to_pooled, tau2_full_sample, reason, min_gain)
rf_select_rule <- function(spec, path = spec$calc_path, designs = NULL, min_gain = RF$RULE_MIN_GAIN, tol = RF$RULE_TIE_TOL) {
  designs <- designs %||% if (spec$kind == "lobby") "rolling" else c("loeo", "chrono")
  tabs <- lapply(designs, function(ds) {
    m <- rf_backtest_metrics(rf_backtest(spec, spec$d, ds))
    m[m$path == path & m$rule %in% c(RF$RULES, "naive_prev"), c("term", "base", "design", "path", "rule", "n", "folds", "targets", "MAE", "RMSE", "units", "beats_naive")]
  })
  tab <- do.call(rbind, tabs)
  rownames(tab) <- NULL
  ch <- rf_choose_rule(tab, designs, min_gain, tol)
  pp <- rf_partial_pool(spec, spec$d)
  reported <- ch$choice
  resolved <- FALSE
  if (ch$choice == "R2" && identical(pp$tau2, 0)) { reported <- "R0"; resolved <- TRUE }
  list(term = spec$id, base = spec$base, path = path, designs = designs, table = tab, winner_by_design = ch$winner_by_design, choice = ch$choice,
       qualifying = ch$qualifying, reported_rule = reported, resolved_to_pooled = resolved, tau2_full_sample = pp$tau2, reason = ch$reason, min_gain = min_gain)
}

# The calculator path of each term under the ruled anchors: PAC and the two shares use the previous-cycle anchor (prevchg), lobbying last year.
RF_TERM_PATHS <- c(pac = "prevchg", control_House = "prevchg", control_Senate = "prevchg", lobby = "calc")

# Selection for every calculator term (the four terms of the ruling) -> named list of rf_select_rule() results.
rf_select_rules <- function(specs, min_gain = RF$RULE_MIN_GAIN, paths = RF_TERM_PATHS) {
  keys <- c(pac = "pac", control_House = "control_House.selected", control_Senate = "control_Senate.selected", lobby = "lobby")
  out <- lapply(names(keys), function(nm) rf_select_rule(specs[[keys[[nm]]]], path = paths[[nm]], min_gain = min_gain))
  names(out) <- names(keys)
  out
}

# One long comparison table (every term, design, rule) for the receipt and the review packet.
rf_selection_table <- function(selections) {
  do.call(rbind, lapply(names(selections), function(nm) {
    s <- selections[[nm]]
    t <- s$table
    t$choice <- s$choice; t$reported_rule <- s$reported_rule; t$selection_term <- nm
    t
  }))
}

# ---- anchor auto-pick (owner ruling 2026-09-30) ------------------------------------------------------------------------------------------
#
# The share models carry their change from Mentat's previous cycle by default. For each share model x industry x chamber the previous-cycle
# anchor is backtested against the same-phase anchor (no leakage, both designs, the slope rule the auto-pick chose for that chamber) and the
# default is replaced only when same-phase beats it by at least RF$RULE_MIN_GAIN (5%) in EVERY design. Ties and folds whose same-phase
# reference falls back to the previous cycle (no cycle four years earlier) count as they are: identical predictions, identical errors.
# The slope rule is chosen first (under the default anchor); the anchor second (under that rule).
RF_ANCHORS <- c("previous_cycle", "same_phase")
RF_ANCHOR_PATH <- c(previous_cycle = "prevchg", same_phase = "calc")       # the backtest path of each anchor

# prev / same: MAE under the previous-cycle / same-phase anchor, named by design. -> list(anchor, gain, wins, reason)
rf_choose_anchor <- function(prev, same, min_gain = RF$RULE_MIN_GAIN, tol = RF$RULE_TIE_TOL) {
  designs <- names(prev)
  gain <- stats::setNames(ifelse(is.finite(prev) & prev > 0 & is.finite(same), 1 - same / prev, NA_real_), designs)
  wins <- stats::setNames(!is.na(gain) & same < prev - tol & same <= prev * (1 - min_gain) + tol, designs)
  fmt <- function(ds) if (is.na(gain[[ds]])) sprintf("%s n/a", ds) else sprintf("%s %+.1f%%", ds, 100 * gain[[ds]])
  what <- paste(vapply(designs, fmt, ""), collapse = ", ")
  if (length(wins) && all(wins)) {
    return(list(anchor = "same_phase", gain = gain, wins = wins,
                reason = sprintf("same_phase beats previous_cycle by %g%% or more in every design (%s)", 100 * min_gain, what)))
  }
  list(anchor = "previous_cycle", gain = gain, wins = wins,
       reason = sprintf("previous_cycle kept: same_phase does not beat it by %g%% in every design (%s; positive = same_phase better)", 100 * min_gain, what))
}

# Per-industry count of backtest rows (n_<industry>) of a cell.
.rf_cell_n <- function(bt, term, base, design, path, rule) {
  r <- bt[bt$term == term & bt$base == base & bt$design == design & bt$path == path & bt$rule == rule, ]
  if (!nrow(r)) return(integer())
  cols <- grep("^n_", names(r), value = TRUE)
  stats::setNames(as.integer(unlist(r[1, cols])), sub("^n_", "", cols))
}

# The anchor of every share model x industry. `bt` = the backtest cells (rf_backtest_metrics rows of both designs, both paths, control
# models "selected" base), `selection` = rf_select_rules() output (the rule per chamber), `sectors` = industries of the share panels.
# -> list(entries = list(control_House = list(<industry> = entry), ...), table = one row per chamber x industry x design)
rf_select_anchors <- function(bt, selection, sectors, min_gain = RF$RULE_MIN_GAIN, designs = c("loeo", "chrono")) {
  entries <- list(); rows <- list()
  for (id in c("control_House", "control_Senate")) {
    rule <- selection[[id]]$reported_rule
    mae <- function(ds, anchor) .rf_cell_mae(bt, id, "selected", ds, RF_ANCHOR_PATH[[anchor]], rule)
    cnt <- function(ds, anchor) .rf_cell_n(bt, id, "selected", ds, RF_ANCHOR_PATH[[anchor]], rule)
    entries[[id]] <- list()
    for (s in sectors[[id]]) {
      pick <- function(anchor) stats::setNames(vapply(designs, function(ds) { m <- mae(ds, anchor); if (s %in% names(m)) unname(m[[s]]) else NA_real_ }, 0), designs)
      npick <- function(anchor) stats::setNames(vapply(designs, function(ds) { m <- cnt(ds, anchor); if (s %in% names(m)) unname(m[[s]]) else 0L }, 0L), designs)
      prev <- pick("previous_cycle"); same <- pick("same_phase")
      ch <- rf_choose_anchor(prev, same, min_gain)
      entries[[id]][[s]] <- list(anchor = ch$anchor, rule = rule, reason = ch$reason, gain = ch$gain, wins = ch$wins, min_gain = min_gain,
                                 mae = list(previous_cycle = prev, same_phase = same), n = list(previous_cycle = npick("previous_cycle"), same_phase = npick("same_phase")))
      for (ds in designs) rows[[length(rows) + 1L]] <- data.frame(term = id, industry = s, rule = rule, design = ds, n = npick("previous_cycle")[[ds]],
        MAE_previous_cycle = prev[[ds]], MAE_same_phase = same[[ds]], same_phase_gain = ch$gain[[ds]], beats_margin = ch$wins[[ds]], choice = ch$anchor, stringsAsFactors = FALSE)
    }
  }
  list(entries = entries, table = do.call(rbind, rows))
}

# The backtest path the calculator follows for one industry of a share model, given the anchor entries.
rf_anchor_path_of <- function(anchors, id, industry) RF_ANCHOR_PATH[[anchors$entries[[id]][[industry]]$anchor %||% "previous_cycle"]]

# Per-industry MAE of a share model's calculator path under each industry's chosen anchor (named numeric by industry).
rf_chosen_anchor_mae <- function(bt, anchors, id, design, rule, base = "selected") {
  sectors <- names(anchors$entries[[id]])
  stats::setNames(vapply(sectors, function(s) { m <- .rf_cell_mae(bt, id, base, design, rf_anchor_path_of(anchors, id, s), rule); if (s %in% names(m)) unname(m[[s]]) else NA_real_ }, 0), sectors)
}

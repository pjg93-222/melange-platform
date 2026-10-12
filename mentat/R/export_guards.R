# Export guards
#
# Every bug this file guards against shipped a workbook that looked correct. The
# common shape is a failure that renders as a plausible number - a rate-limited
# HTTP 429 becoming "$0.00", money spent AGAINST a candidate becoming activity
# favouring their party, an earmarked gift counted at both legs. None of them
# tripped an existing check, because the existing reconciliation re-sums the very
# rows that produced its total and therefore cannot fail.
#
# Rules for anything added here:
#   * Industry-agnostic. No company, committee or cycle may be named. These run
#     for every organisation the pipeline is pointed at.
#   * O(n) aggregate work on data already in memory. The build has a 15-minute
#     budget dominated by collection and Excel finalisation; guards must cost
#     seconds. No row-by-row loops, no re-reads, no joins beyond a keyed merge.
#   * A guard must be able to FAIL. If it cannot express a state that is wrong,
#     it is decoration.
#
# Severity:
#   "fail"  -> refuse to publish. Reserved for "this artifact would state
#              something untrue", not "this looks unusual".
#   "warn"  -> record in the receipt and print, but publish.

# Keep a failure readable. A guard that prints 150 company/cycle pairs buries the
# one line an operator needs; the full set stays in the returned data frame.
mentat_guard_summarise <- function(items, limit = 12L) {
  items <- as.character(items)
  if (length(items) <= limit) {
    return(paste(items, collapse = "; "))
  }
  paste0(
    paste(items[seq_len(limit)], collapse = "; "),
    sprintf(" ... and %d more", length(items) - limit)
  )
}

mentat_guard_result <- function(id, severity, passed, detail, metric = NA_real_) {
  data.frame(
    id = id,
    severity = severity,
    passed = passed,
    detail = detail,
    metric = as.numeric(metric),
    stringsAsFactors = FALSE
  )
}

# G1 - roster coverage.
# A company on the roster with zero collected rows in a cycle is published as
# $0.00 with a competition rank, arithmetically identical to "gave nothing".
# Farmers Insurance shipped that way for three cycles while FEC held 16,432 rows
# for it, because a rate-limited query was accepted as an empty result.
# Zero rows of ANY status in EVERY cycle means the company was never collected.
mentat_guard_roster_coverage <- function(roster, rows, cycles) {
  roster <- unique(roster[nzchar(roster)])
  if (!length(roster)) {
    return(mentat_guard_result(
      "roster_coverage", "warn", TRUE, "roster is empty; nothing to check"
    ))
  }
  seen <- unique(rows$company[rows$cycle %in% cycles])
  missing <- setdiff(roster, seen)
  if (!length(missing)) {
    return(mentat_guard_result(
      "roster_coverage", "fail", TRUE,
      sprintf("all %d roster companies have collected rows", length(roster)),
      length(roster)
    ))
  }
  mentat_guard_result(
    "roster_coverage", "fail", FALSE,
    paste0(
      "roster companies with ZERO collected rows in every cycle - these would ",
      "publish as $0.00 and be indistinguishable from a real zero: ",
      mentat_guard_summarise(missing)
    ),
    length(missing)
  )
}

# G2 - per-cell coverage.
# Weaker than G1: the company exists but one cycle is empty. Legitimate (a young
# company, a cycle it sat out) so this warns rather than fails, but it must be
# visible because the cell renders as $0.00 either way.
mentat_guard_cell_coverage <- function(roster, rows, cycles) {
  roster <- unique(roster[nzchar(roster)])
  if (!length(roster) || !length(cycles)) {
    return(mentat_guard_result("cell_coverage", "warn", TRUE, "nothing to check"))
  }
  present <- unique(rows[cycle %in% cycles, .(company, cycle)])
  present[, seen := TRUE]
  grid <- data.table::CJ(company = roster, cycle = cycles, unique = TRUE)
  grid <- merge(grid, present, by = c("company", "cycle"), all.x = TRUE)
  empty <- grid[is.na(seen)]
  if (!nrow(empty)) {
    return(mentat_guard_result(
      "cell_coverage", "warn", TRUE, "every company x cycle cell has rows"
    ))
  }
  mentat_guard_result(
    "cell_coverage", "warn", FALSE,
    paste0(
      "company x cycle cells with no collected rows (publish as $0.00): ",
      mentat_guard_summarise(sprintf("%s/%s", empty$company, empty$cycle))
    ),
    nrow(empty)
  )
}

# G3 was a conduit double-count guard. It is deliberately absent: under the
# R5/R8 recipient-as-shown ruling a conduit leg is a real row that COUNTS, so
# "conduit leg also appears on its earmark target" is the expected state, not a
# defect. The guard could only ever fire on correct data. Removed 2026-07-27.

# G3 - unverified zeros.
# A company-cycle that collected nothing is only trustworthy if the collector
# CONFIRMED the emptiness. Farmers Insurance recorded 24 variant sets and 0 rows
# in 16 seconds under an FEC 429 and published as $0.00 in every P&C book;
# Bitnomial recorded a real 0 at the same ~0.7s per variant set. Nothing stored
# after the fact separates them, so the collector now stamps zero_verified on
# every checkpoint it writes under the hardened readiness rule. A zero without
# that stamp predates the source-specific readiness rule (browser confirmed-empty
# rendering or API HTTP-success plus count/cursor completion) and must be
# recollected before it can be believed.
# Warn, not fail: it describes provenance of existing data, not a defect in this
# build, and every affected cell is also visible through G1/G2.
mentat_guard_zero_provenance <- function(progress) {
  if (is.null(progress) || !nrow(progress)) {
    return(mentat_guard_result(
      "zero_provenance", "warn", TRUE, "no collection checkpoints to inspect"
    ))
  }
  dt <- data.table::as.data.table(progress)
  if (!"zero_verified" %in% names(dt)) {
    return(mentat_guard_result(
      "zero_provenance", "warn", FALSE,
      "checkpoints predate zero verification entirely; every zero is unverified"
    ))
  }
  dt[, zero_verified := suppressWarnings(as.integer(zero_verified))]
  dt[is.na(zero_verified), zero_verified := 0L]
  by_cell <- dt[, .(
    rows = sum(as.numeric(records_total), na.rm = TRUE),
    unverified = sum(zero_verified == 0L)
  ), by = .(company, cycle)]
  suspect <- by_cell[rows == 0 & unverified > 0]
  if (!nrow(suspect)) {
    return(mentat_guard_result(
      "zero_provenance", "warn", TRUE,
      sprintf("%d company x cycle checkpoints, every zero verified", nrow(by_cell))
    ))
  }
  mentat_guard_result(
    "zero_provenance", "warn", FALSE,
    paste0(
      "company x cycle cells collected ZERO rows without a verified-empty ",
      "checkpoint - recollect before treating these as real zeros: ",
      mentat_guard_summarise(sprintf("%s/%s", suspect$company, suspect$cycle))
    ),
    nrow(suspect)
  )
}

# Reconcile each modern API search against its own fetched/stored checkpoint.
# Summing legacy and API query counts against UNIQUE stored rows counts both
# overlapping aliases and old search generations twice. Keep legacy evidence,
# but supersede its count comparison only when every corresponding API unit has
# a successful-response stamp, equal counts, complete storage, and newer dates.
mentat_guard_progress_views <- function(progress, api) {
  p <- data.table::as.data.table(data.table::copy(progress))
  a <- data.table::as.data.table(data.table::copy(api))
  empty <- list(completeness = p, zero = p, api_guard = NULL)
  if (!nrow(a) || !all(c("variant_set", "zero_verified", "updated_at") %in% names(p))) return(empty)
  p[, destination := ifelse(grepl("^P2_company_name", variant_set), "corporate", "contributions")]
  a <- a[kind %in% c("employer", "corporate")]
  if (!nrow(a)) return(empty)
  # Date windows are fetch checkpoints; their union is stored once by the
  # parent transaction. Reconcile complete, count-matching children to that
  # parent, retaining orphaned or partial windows so they still fail the guard.
  windows <- a[grepl("::window::", unit_id, fixed = TRUE), .(
    child_rows = sum(rows_seen), children_fetched = all(fetched_complete == 1L)
  ), by = .(company, cycle, kind, unit_id = sub("::window::.*$", "", unit_id))]
  parents <- merge(a, windows, by = c("company", "cycle", "kind", "unit_id"))[
    fetched_complete == 1L & stored_complete == 1L & children_fetched &
      !is.na(child_rows) & child_rows == rows_seen,
    .(company, cycle, kind, parent_id = unit_id)]
  if (nrow(parents)) {
    a[, parent_id := ifelse(grepl("::window::", unit_id, fixed = TRUE),
      sub("::window::.*$", "", unit_id), NA_character_)]
    a <- a[!parents, on = .(company, cycle, kind, parent_id)]
    a[, parent_id := NULL]
  }
  stamps <- p[, .(company, cycle, unit_id = variant_set, zero_verified)]
  a <- merge(a, stamps, by = c("company", "cycle", "unit_id"), all.x = TRUE)
  a[, destination := ifelse(kind == "corporate", "corporate", "contributions")]
  a[, valid := !is.na(zero_verified) & zero_verified == 1L & fetched_complete == 1L &
      stored_complete == 1L & rows_seen == records_total & records_total >= 0]
  a[is.na(valid), valid := FALSE]
  timestamp <- function(x) as.numeric(as.POSIXct(x, format = "%Y-%m-%dT%H:%M:%OS%z", tz = "UTC"))
  eligible <- a[, .(valid = all(valid), latest_api = max(timestamp(updated_at))), by = .(company, cycle, destination)]
  old <- p[, .(latest_legacy = max(timestamp(updated_at))), by = .(company, cycle, destination)]
  eligible <- merge(eligible, old, by = c("company", "cycle", "destination"))[
    valid & !is.na(latest_api) & !is.na(latest_legacy) & latest_api >= latest_legacy,
    .(company, cycle, destination)]
  legacy <- p[!eligible, on = .(company, cycle, destination)]
  verified <- a[eligible, on = .(company, cycle, destination), nomatch = 0L]
  list(completeness = legacy,
    zero = data.table::rbindlist(list(legacy, verified), fill = TRUE),
    api_guard = mentat_guard_result("api_checkpoint_integrity", "warn", all(a$valid),
      sprintf("%d of %d API search checkpoints have a successful-response stamp, matching fetched counts and complete storage; %d legacy cell/destination comparisons superseded. This does not verify unrequested aliases or missing employer data.",
        sum(a$valid), nrow(a), nrow(eligible))))
}

# G8 - collection completeness.
# G1/G2/G3 only see a cell that collected NOTHING. A walk that stopped halfway
# leaves a cell that looks populated and is quietly short - the failure mode that
# actually occurred in Property & Casualty, where Chubb and Liberty Mutual each
# held rows in every cycle while missing tens of thousands of them.
# scrape_progress.records_total is what FEC itself reported for each query, so
# comparing it against rows actually stored measures the gap directly.
# Destination matters: the employer and variant passes land in `contributions`
# while the company-as-contributor pass (P2_company_name) lands in
# `corporate_giving`. Comparing a company's whole reported total against
# `contributions` alone reads a complete Chubb as 65% collected.
# Stored may sit a little UNDER reported without fault, because separate variant
# sets return overlapping rows that collapse on sub_id - Farmers legitimately
# stores 9,516 against a reported 9,520. Only a material gap is reported.
# The CALLER must exclude identity passes (P2_name / P2_name_state) from the
# reported side: those search a contributor name, so records_total counts every
# person nationally with that name. Including them reported a complete Coinbase
# 2024 as 1,085,852 rows short, because one seed alone returns 1,049,000.
# A flagged cell is a CANDIDATE for recollection, not proven loss - heavy overlap
# between variant sets can also depress the ratio. Re-collect to settle it.
mentat_guard_collection_completeness <- function(progress, stored, tolerance = 0.02) {
  if (is.null(progress) || !nrow(progress) || is.null(stored) || !nrow(stored)) {
    return(mentat_guard_result(
      "collection_completeness", "warn", TRUE, "no checkpoints to reconcile"
    ))
  }
  p <- data.table::as.data.table(progress)
  s <- data.table::as.data.table(stored)
  p[, reported := suppressWarnings(as.numeric(records_total))]
  p[is.na(reported), reported := 0]
  merged <- merge(
    p[, .(reported = sum(reported)), by = .(company, cycle, destination)],
    s[, .(rows = sum(as.numeric(rows))), by = .(company, cycle, destination)],
    by = c("company", "cycle", "destination"), all.x = TRUE
  )
  merged[is.na(rows), rows := 0]
  short <- merged[reported > 0 & rows < reported - pmax(10, reported * tolerance)]
  if (!nrow(short)) {
    return(mentat_guard_result(
      "collection_completeness", "warn", TRUE,
      sprintf("%d company x cycle walks stored what FEC reported", nrow(merged))
    ))
  }
  short[, gap := reported - rows]
  data.table::setorder(short, -gap)
  mentat_guard_result(
    "collection_completeness", "warn", FALSE,
    paste0(
      "walks stored materially fewer rows than FEC reported - the cell looks ",
      "populated but is short, recollect before trusting it: ",
      # Every short cell is itemized, not the first 12 and "... and N more": the
      # console reads this detail (app/collection_gaps.py) to say which cells are
      # short, and a count of the rest leaves it nothing to name.
      mentat_guard_summarise(sprintf(
        "%s/%s %s %.0f of %.0f", short$company, short$cycle, short$destination,
        short$rows, short$reported
      ), limit = nrow(short))
    ),
    sum(short$gap)
  )
}

# G4 - direction integrity.
# Independent expenditures carry a direction. Summed without a sign, money spent
# AGAINST a candidate reads as money benefiting them: 89% of one shipped
# beneficiary sheet, including $1.87B of anti-Republican spending booked under
# "Republican Activity". Any row whose direction mentions opposition must be
# excluded from party-support attribution.
mentat_guard_direction_integrity <- function(beneficiary) {
  if (is.null(beneficiary) || !nrow(beneficiary)) {
    return(mentat_guard_result("direction_integrity", "warn", TRUE, "no beneficiary rows"))
  }
  required <- c(
    "directions",
    "preliminary_downstream_amount",
    "support_amount",
    "oppose_amount"
  )
  missing <- setdiff(required, names(beneficiary))
  if (length(missing)) {
    return(mentat_guard_result(
      "direction_integrity", "fail", FALSE,
      paste0(
        "beneficiary direction components are missing: ",
        paste(missing, collapse = ", ")
      ),
      length(missing)
    ))
  }
  dt <- data.table::as.data.table(beneficiary)
  downstream <- suppressWarnings(as.numeric(dt$preliminary_downstream_amount))
  support <- suppressWarnings(as.numeric(dt$support_amount))
  oppose <- suppressWarnings(as.numeric(dt$oppose_amount))
  invalid_numeric <- !is.finite(downstream) |
    !is.finite(support) |
    !is.finite(oppose)
  has_support <- grepl("support", dt$directions, ignore.case = TRUE)
  has_oppose <- grepl("oppose", dt$directions, ignore.case = TRUE)
  tolerance <- 1e-9
  inconsistent <- invalid_numeric |
    (abs(support) > tolerance & !has_support) |
    (abs(oppose) > tolerance & !has_oppose)
  if (any(inconsistent)) {
    return(mentat_guard_result(
      "direction_integrity", "fail", FALSE,
      paste0(
        sum(inconsistent),
        " beneficiary rows have missing/non-finite direction components or ",
        "nonzero support/oppose dollars without the matching direction"
      ),
      sum(inconsistent)
    ))
  }
  opposition_rows <- abs(oppose) > tolerance
  amount <- sum(abs(oppose[opposition_rows]))
  total <- sum(abs(downstream))
  share <- if (total > 0) amount / total else 0
  mentat_guard_result(
    "direction_integrity", "fail", TRUE,
    sprintf(
      paste0(
        "%d of %d beneficiary rows carry opposition activity ($%.0f, %.1f%% ",
        "of absolute downstream dollars); support/oppose components are present ",
        "and direction-consistent"
      ),
      sum(opposition_rows), nrow(dt), amount, share * 100
    ),
    share
  )
}

# G5 - date sanity.
# FEC writes dates as MMDDYYYY. Read as an integer the leading zero is lost, so
# every January-September date mis-parses; one shipped set had no Jan-Sep date at
# all and 30,331 years before 1990. Both symptoms are cheap to detect: a missing
# month range, or impossible years.
mentat_guard_date_sanity <- function(dates, label = "dates", min_year = 1990L) {
  parsed <- suppressWarnings(as.Date(dates, format = "%Y-%m-%d"))
  invalid <- sum(nzchar(trimws(dates)) & is.na(parsed), na.rm = TRUE)
  if (invalid) return(mentat_guard_result(
    paste0("date_sanity:", label), "fail", FALSE,
    sprintf("%d nonempty dates cannot be parsed", invalid), invalid
  ))
  parsed <- parsed[!is.na(parsed)]
  if (!length(parsed)) {
    return(mentat_guard_result(
      paste0("date_sanity:", label), "warn", TRUE, "no parseable dates"
    ))
  }
  years <- as.integer(format(parsed, "%Y"))
  months <- unique(format(parsed, "%m"))
  bogus <- sum(years < min_year, na.rm = TRUE)
  if (bogus > 0L) {
    return(mentat_guard_result(
      paste0("date_sanity:", label), "fail", FALSE,
      sprintf(
        "%d dates fall before %d - the MMDDYYYY integer-coercion signature",
        bogus, min_year
      ),
      bogus
    ))
  }
  if (length(parsed) >= 1000L && length(months) < 12L) {
    return(mentat_guard_result(
      paste0("date_sanity:", label), "info", TRUE,
      sprintf(
        paste0("%d distinct months across %d dates (present: %s); ",
               "partial-year coverage does not imply date corruption."),
        length(months), length(parsed), paste(sort(months), collapse = ",")
      ),
      length(months)
    ))
  }
  mentat_guard_result(
    paste0("date_sanity:", label), "fail", TRUE,
    sprintf("%d dates, %d months, no impossible years", length(parsed), length(months))
  )
}

# G6 - category cross-view agreement.
# The shipped reconciliation re-sums its own inputs and cannot fail. This one
# can: the same category total is computed from two independent views and they
# must agree. A real defect showed here - Candidate and Leadership PAC totals
# were swapped in one cycle of all six books, offsetting exactly so every
# total-level check still passed.
mentat_guard_category_agreement <- function(view_a, view_b, tolerance = 0.02) {
  rows_a <- if (is.null(view_a)) 0L else nrow(view_a)
  rows_b <- if (is.null(view_b)) 0L else nrow(view_b)
  if (!rows_a && !rows_b) {
    return(mentat_guard_result(
      "category_agreement", "warn", TRUE, "both category views are empty"
    ))
  }
  if (!rows_a || !rows_b) {
    return(mentat_guard_result(
      "category_agreement", "fail", FALSE,
      sprintf(
        "one independent category view disappeared (view_a=%d, view_b=%d)",
        rows_a,
        rows_b
      )
    ))
  }
  merged <- merge(
    data.table::as.data.table(view_a),
    data.table::as.data.table(view_b),
    by = c("cycle_label", "category"), all = TRUE, suffixes = c("_a", "_b")
  )
  merged[is.na(amount_a), amount_a := 0]
  merged[is.na(amount_b), amount_b := 0]
  bad <- merged[abs(amount_a - amount_b) > tolerance]
  if (!nrow(bad)) {
    return(mentat_guard_result(
      "category_agreement", "fail", TRUE,
      sprintf("%d category x cycle totals agree across both views", nrow(merged))
    ))
  }
  mentat_guard_result(
    "category_agreement", "fail", FALSE,
    paste0(
      "category totals disagree between views (same rows, two code paths): ",
      mentat_guard_summarise(sprintf(
        "%s/%s %.2f vs %.2f", bad$cycle_label, bad$category, bad$amount_a, bad$amount_b
      ))
    ),
    nrow(bad)
  )
}

# G7 - declared-but-empty categories.
# A category the template renders but nothing ever populates is invisible: the
# Trade Association row read $0.00 in all six shipped books for months because
# no committee could ever match it. Warn, because a genuine zero is possible.
# An `optional` category is rendered but is allowed to be empty without a note:
# Conduit (ActBlue, WinRed) is a real zero for any industry or cycle whose
# people never gave through an earmark platform.
mentat_guard_category_population <- function(declared, populated,
                                             optional = character()) {
  declared <- unique(declared[nzchar(declared)])
  empty <- setdiff(setdiff(declared, optional), unique(populated))
  if (!length(empty)) {
    return(mentat_guard_result(
      "category_population", "warn", TRUE, "every declared category is populated"
    ))
  }
  mentat_guard_result(
    "category_population", "warn", FALSE,
    paste0(
      "categories rendered by the template that no row was ever assigned - ",
      "verify this is a real zero and not an unmatched register: ",
      mentat_guard_summarise(empty)
    ),
    length(empty)
  )
}

# G8 - detail sheet reconciliation.
# The two fine-grain sheets regroup the same accepted rows a third and fourth
# way (by recipient committee, and by contributor geography/employer), then cap
# and fold them. A fold that drops a group instead of summing it, a grouping key
# that splits on an NA, or a cap applied before the fold all publish a chartable
# sheet whose totals quietly disagree with the cycle totals printed one tab away
# - and a chart built on the smaller number is wrong in a way no reader can see.
# Every dollar has to survive both regroupings, in every cycle.
mentat_guard_detail_reconciliation <- function(frames, expected, tolerance = 0.005) {
  expected <- unlist(expected)
  sheet_totals <- function(name) {
    frame <- data.table::as.data.table(frames[[name]])
    if (!nrow(frame)) return(stats::setNames(numeric(), character()))
    totals <- frame[, .(total = sum(total_giving, na.rm = TRUE)), by = cycle_label]
    stats::setNames(totals$total, as.character(totals$cycle_label))
  }
  observed <- list(
    recipient_detail = sheet_totals("recipient_detail"),
    contributor_detail = sheet_totals("contributor_detail")
  )
  labels <- unique(c(names(expected), unlist(lapply(observed, names))))
  labels <- labels[!is.na(labels)]
  bad <- character()
  for (label in labels) {
    want <- if (label %in% names(expected)) as.numeric(expected[[label]]) else 0
    if (is.na(want)) want <- 0
    for (name in names(observed)) {
      got <- if (label %in% names(observed[[name]])) unname(observed[[name]][[label]]) else 0
      if (abs(got - want) > tolerance) {
        bad <- c(bad, sprintf("%s/%s %.2f vs cycle total %.2f", label, name, got, want))
      }
    }
  }
  if (!length(bad)) {
    return(mentat_guard_result(
      "detail_reconciliation", "fail", TRUE,
      sprintf(
        "detail sheets reconcile to the cycle totals in %d cycle(s)",
        length(labels)
      ),
      length(labels)
    ))
  }
  mentat_guard_result(
    "detail_reconciliation", "fail", FALSE,
    paste0(
      "fine-grain detail sheets do not sum to their cycle totals - a chart ",
      "built on them would understate or overstate the published figure: ",
      mentat_guard_summarise(bad)
    ),
    length(bad)
  )
}

# Runner. Collects results, prints a compact report, and stops on any failed
# "fail"-severity guard so a build cannot publish an artifact that would state
# something untrue.
mentat_run_export_guards <- function(guards, context = "export") {
  results <- do.call(rbind, Filter(Negate(is.null), guards))
  if (is.null(results) || !nrow(results)) {
    return(invisible(results))
  }
  cat(sprintf("\nEXPORT GUARDS (%s)\n", context))
  for (index in seq_len(nrow(results))) {
    status <- if (isTRUE(results$passed[[index]])) {
      "PASS"
    } else if (identical(results$severity[[index]], "fail")) {
      "FAIL"
    } else {
      "WARN"
    }
    cat(sprintf("  [%s] %-26s %s\n", status, results$id[[index]], results$detail[[index]]))
  }
  flush(stdout())
  blocking <- results[!results$passed & results$severity == "fail", , drop = FALSE]
  if (nrow(blocking)) {
    stop(
      paste0(
        "Export guards failed (", nrow(blocking), "); refusing to publish.\n",
        paste(sprintf("  - %s: %s", blocking$id, blocking$detail), collapse = "\n")
      ),
      call. = FALSE
    )
  }
  invisible(results)
}

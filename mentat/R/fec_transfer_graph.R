# OTH cash-transfer indexes are built by tools/fec_transfer_graph.py.
# Discover the whole observed network; candidate activity stays separate from
# donor receipts and conditional allocation estimates.

# publish_index() writes transfer-edges.csv with Python's csv module, which
# quotes the JSON `records` field and doubles every quote inside it (RFC 4180).
# data.table::fread returns such a field with the doubled quotes still in it
# (measured on 1.18.4: the field "[{""a"":1}]" reads as [{""a"":1}]), so the
# payload carried [{""file_num"":...}] and enrich_downstream.py's json.loads
# refused it. A value that is already JSON is kept as it is; otherwise exactly
# the CSV escape is undone; a value that is still not JSON stops the export.
# Record evidence is never dropped.
fec_transfer_edge_records <- function(values, path) {
  values <- as.character(values)
  is_json <- function(text) vapply(text, function(value) isTRUE(jsonlite::validate(value)),
    logical(1), USE.NAMES = FALSE)
  pending <- which(!is.na(values) & nzchar(values))
  pending <- pending[!is_json(values[pending])]
  if (!length(pending)) return(values)
  decoded <- gsub('""', '"', values[pending], fixed = TRUE)
  broken <- pending[!is_json(decoded)]
  if (length(broken))
    stop("Transfer edge records are not JSON in ", length(broken), " of ", length(values),
      " edge(s) read from ", path, "; the first begins: ", substr(values[[broken[[1L]]]], 1L, 80L),
      call. = FALSE)
  values[pending] <- decoded
  values
}

# F12 (Paul approved 2026-09-25): the downstream walk follows dates, with one-cycle
# carryover (option (b)). tools/arrival_gates.py is the rule; these functions are its
# R port, pinned case for case against it by tests/fixtures/arrival_gates_cases.json.
#   - gate(root) is its cycle window's first day, and a root's transfers all count;
#   - a transfer X -> Y dated t opens Y only if t is on or after gate(X) and X's
#     counted transfers to Y net above zero; gate(Y) is the earliest such t (an
#     undated transfer, or a root's transfer dated before its window, arrives on the
#     source's own gate); gates only fall, so loops end; a root is never re-opened;
#   - carryover: walking from the roots at the PREVIOUS cycle's first day over the
#     previous cycle's transfers, every committee reached opens at this cycle's first
#     day; a previous-cycle transfer only opens a gate and is never counted as a flow;
#   - a reached committee's rows count on or after its gate; an undated row counts,
#     flagged date_unverified (D-040).
fec_cycle_start <- function(cycle) {
  text <- as.character(cycle)
  digits <- regmatches(text, gregexpr("[0-9]{4}", text))[[1L]]
  if (!length(digits)) stop(text, " is not a cycle", call. = FALSE)
  year <- as.integer(digits[[length(digits)]])
  year <- year + year %% 2L
  sprintf("%d-01-01", year - 1L)
}

fec_previous_cycle <- function(cycle) {
  as.character(as.integer(substr(fec_cycle_start(cycle), 1L, 4L)) - 1L)
}

# An ISO date as a sortable number; '' (undated) and NA stay NA. The rule compares
# dates, never strings in the session's collation.
fec_day <- function(value) {
  value <- as.character(value)
  suppressWarnings(as.integer(ifelse(is.na(value) | !nzchar(value), NA_character_,
                                     gsub("-", "", value, fixed = TRUE))))
}

# roots: named character vector, committee key -> pinned gate, in the order the
# Python dict would hold them. legs(key): list(destination, when, value) of the
# key's outgoing transfers ('' when undated; value exact, e.g. cents), or NULL.
# Returns every gate, roots first, in the order each key was first opened, with
# attr "opened_by": the key whose transfer set each non-root gate.
fec_earliest_arrival <- function(roots, legs) {
  gates <- new.env(hash = TRUE, parent = emptyenv())
  read <- new.env(hash = TRUE, parent = emptyenv())
  queued <- new.env(hash = TRUE, parent = emptyenv())
  openers <- new.env(hash = TRUE, parent = emptyenv())
  keys <- names(roots)
  for (key in keys) { assign(key, unname(roots[[key]]), envir = gates); assign(key, TRUE, envir = queued) }
  pending <- keys
  while (length(pending)) {
    key <- pending[[length(pending)]]
    pending <- pending[-length(pending)]
    if (exists(key, envir = queued, inherits = FALSE)) rm(list = key, envir = queued)
    gate <- get(key, envir = gates, inherits = FALSE)
    gate_day <- fec_day(gate)
    root <- key %in% names(roots)
    if (!exists(key, envir = read, inherits = FALSE)) {
      found <- legs(key)
      if (is.null(found)) found <- list(destination = character(), when = character(), value = numeric())
      assign(key, found, envir = read)
    }
    leg <- get(key, envir = read, inherits = FALSE)
    if (!length(leg$destination)) next
    when <- as.character(leg$when); when[is.na(when)] <- ""
    day <- fec_day(when)
    counted <- if (root) rep(TRUE, length(when)) else !(nzchar(when) & day < gate_day)
    if (!any(counted)) next
    destination <- as.character(leg$destination)[counted]
    value <- as.numeric(leg$value)[counted]
    when <- when[counted]; day <- day[counted]
    # A positive transfer arrives on its own date, or on the source's gate when it
    # is undated or (a root's) dated before the gate.
    at <- ifelse(nzchar(when) & !is.na(day) & day >= gate_day, when, gate)
    groups <- split(seq_along(destination), destination)
    for (target in sort(names(groups), method = "radix")) {
      members <- groups[[target]]
      if (sum(value[members]) <= 0) next
      positive <- members[value[members] > 0]
      if (!length(positive) || target %in% names(roots)) next
      arrival <- at[positive][[which.min(fec_day(at[positive]))]]
      current <- if (exists(target, envir = gates, inherits = FALSE)) get(target, envir = gates) else NA_character_
      if (is.na(current) || fec_day(arrival) < fec_day(current)) {
        if (is.na(current)) keys <- c(keys, target)
        assign(target, arrival, envir = gates)
        assign(target, key, envir = openers)
        if (!exists(target, envir = queued, inherits = FALSE)) {
          assign(target, TRUE, envir = queued)
          pending <- c(pending, target)
        }
      }
    }
  }
  result <- stats::setNames(vapply(keys, function(key) get(key, envir = gates, inherits = FALSE), character(1L)), keys)
  # Which committee's transfer set each opened gate (the last, earliest arrival):
  # a route to explain the gate by, never a flow. Roots have none.
  opened <- keys[!keys %in% names(roots)]
  attr(result, "opened_by") <- stats::setNames(vapply(opened, function(key)
    get(key, envir = openers, inherits = FALSE), character(1L)), opened)
  result
}

# Previous-cycle keys the roots' previous-cycle transfers reach ('onward' follows
# every time-respecting transfer; 'direct' only the roots' own), roots excluded.
fec_carryover_arrivals <- function(roots, legs, onward = TRUE) {
  walk <- if (isTRUE(onward)) legs else function(key) if (key %in% names(roots)) legs(key) else NULL
  gates <- fec_earliest_arrival(roots, walk)
  reached <- gates[!names(gates) %in% names(roots)]
  attr(reached, "opened_by") <- attr(gates, "opened_by")
  reached
}

# The export's F12 network for one cycle, above the rule functions: what the roots'
# money could reach and when (with carryover from the previous cycle's gates), then
# the walk that follows and counts only transfers dated on or after their source's
# gate. `pinned` are the roots (never candidate committees); `saved` answers whether
# a previous-cycle committee's filings are available at all.
fec_arrival_network <- function(cycle, pinned, legs, previous_legs = NULL,
                                previous_saved = function(key) FALSE, carryover = "onward") {
  if (!carryover %in% c("onward", "direct", "off")) stop("Unknown carryover rule ", carryover, call. = FALSE)
  start <- fec_cycle_start(cycle)
  roots <- stats::setNames(rep(start, length(pinned)), pinned)
  seeds <- character(); status <- "off"; missing_units <- character(); opened <- character()
  previous_openers <- character()
  if (!identical(carryover, "off")) {
    saved <- vapply(pinned, function(key) isTRUE(previous_saved(key)), logical(1L))
    known <- stats::setNames(rep(fec_cycle_start(fec_previous_cycle(cycle)), sum(saved)), pinned[saved])
    gaps <- pinned[!saved]
    if (length(known)) {
      reached <- fec_carryover_arrivals(known, previous_legs, onward = identical(carryover, "onward"))
      previous_openers <- attr(reached, "opened_by")
      attr(reached, "opened_by") <- NULL
      if (identical(carryover, "onward"))
        gaps <- c(gaps, names(reached)[!vapply(names(reached), function(key) isTRUE(previous_saved(key)), logical(1L))])
      targets <- sort(names(reached), method = "radix")
      targets <- targets[!targets %in% pinned]
      seeds <- stats::setNames(rep(start, length(targets)), targets)
      opened <- reached[targets]
    }
    status <- if (!length(known)) "unknown" else if (length(gaps)) "partial" else "known"
    missing_units <- sort(unique(gaps), method = "radix")
  }
  gates <- fec_earliest_arrival(c(roots, seeds), legs)
  openers <- attr(gates, "opened_by")
  attr(gates, "opened_by") <- NULL
  source <- ifelse(names(gates) %in% pinned, "root", ifelse(names(gates) %in% names(seeds), "carryover", "in-cycle"))
  # opened_by explains a gate (in-cycle: this cycle's opener; carryover: the previous
  # cycle's openers back to a root). It names routes; it never moves an amount.
  list(gates = gates, gate_source = stats::setNames(source, names(gates)), seeds = seeds, opened_by = openers,
       carryover = list(rule = carryover, status = status, missing_units = missing_units, opened = opened,
                        opened_by = previous_openers))
}

# The previous-cycle route that opened a carried-over committee's gate, from the root
# whose previous-cycle transfers began it: a label that explains the gate, never a
# flow. The committee is marked with the cycle its gate was opened in.
fec_carryover_route <- function(key, network, cycle) {
  openers <- network$carryover$opened_by
  route <- key
  while (!is.na(opener <- unname(openers[route[[1L]]])) && !opener %in% route) route <- c(opener, route)
  words <- paste0(as.integer(fec_previous_cycle(cycle)) - 1L, "-", fec_previous_cycle(cycle))
  route[[length(route)]] <- paste0(key, " [gate opened by ", words, " transfers]")
  route
}

# Does a row dated `when` count under `gate`? No gate (a root) and no date (D-040)
# both count. arrival_gates.counts().
fec_gate_counts <- function(when, gate) {
  when <- as.character(when); when[is.na(when)] <- ""
  is.na(gate) | !nzchar(when) | fec_day(when) >= fec_day(gate)
}

# One source's published edges as dated legs: a row per counted transfer record
# (position in `rows`, destination, date '' when undated, cents, record number). An
# edge published without records (a fixture, an older export) is one undated leg for
# its amount, which passes at its source's own gate (D-040).
fec_transfer_edge_legs <- function(rows, positions, path) {
  if (!length(positions)) return(NULL)
  edges <- rows[positions]
  texts <- if ("records" %in% names(edges)) fec_transfer_edge_records(edges$records, path) else
    rep(NA_character_, length(positions))
  has <- !is.na(texts) & nzchar(texts)
  frames <- vector("list", length(positions))
  if (any(has)) {
    # One parse per source: an array of the edges' record arrays comes back as one
    # data frame per edge. Any other shape falls back to one parse per edge.
    parsed <- tryCatch(jsonlite::fromJSON(paste0("[", paste(texts[has], collapse = ","), "]"),
                                          simplifyDataFrame = TRUE), error = function(error) NULL)
    if (!is.list(parsed) || is.data.frame(parsed) || length(parsed) != sum(has))
      parsed <- lapply(texts[has], jsonlite::fromJSON, simplifyDataFrame = TRUE)
    frames[has] <- parsed
  }
  counts <- vapply(frames, NROW, integer(1L))
  full <- counts > 0L
  legs <- data.table::rbindlist(c(
    lapply(which(full), function(i) list(position = positions[[i]], destination = edges$destination[[i]],
      date = as.character(frames[[i]]$date), cents = as.numeric(frames[[i]]$cents), record = seq_len(counts[[i]]))),
    lapply(which(!full), function(i) list(position = positions[[i]], destination = edges$destination[[i]],
      date = "", cents = round(as.numeric(edges$amount[[i]]) * 100), record = NA_integer_))))
  legs[is.na(date), date := ""]
  data.table::setorderv(legs, c("position", "record"), na.last = TRUE)
  legs
}

# One cycle's published edges as the walks read them: each source's dated legs,
# parsed once and shared by every view's walk over the cycle (F15).
fec_transfer_reader <- function(index, stop_ids) {
  by_source <- split(seq_len(nrow(index$rows)), index$rows$source)
  cache <- new.env(hash = TRUE, parent = emptyenv())
  records_of <- function(key) {
    if (!exists(key, envir = cache, inherits = FALSE))
      assign(key, fec_transfer_edge_legs(index$rows, by_source[[key]], index$edge_path), envir = cache)
    get(key, envir = cache, inherits = FALSE)
  }
  # A payment to a candidate committee is terminal: it opens no gate.
  legs <- function(key) {
    found <- records_of(key)
    if (is.null(found)) return(NULL)
    found <- found[!destination %in% stop_ids]
    list(destination = found$destination, when = found$date, value = found$cents)
  }
  list(records_of = records_of, legs = legs)
}

# `now`/`before`: readers to share across views (built here when absent). With
# detail = FALSE only the walk's reach is returned - gates, the counted positive
# source -> destination pairs and the carryover routes - not the published edges.
fec_transfer_network <- function(target_cycle, pinned, current, stops, previous = NULL,
                                 previous_stops = character(), carryover = "onward",
                                 now = NULL, before = NULL, detail = TRUE) {
  if (is.null(now)) now <- fec_transfer_reader(current, stops)
  if (is.null(before) && !is.null(previous)) before <- fec_transfer_reader(previous, previous_stops)
  network <- fec_arrival_network(target_cycle, pinned, now$legs,
    previous_legs = if (is.null(before)) NULL else before$legs,
    previous_saved = function(key) !is.null(before), carryover = carryover)
  gates <- network$gates
  visited <- new.env(hash = TRUE, parent = emptyenv())
  pending <- c(rev(names(network$seeds)), pinned)
  counted_legs <- list(); excluded <- list(); undated <- list(); pairs <- list()
  while (length(pending)) {
    key <- pending[[length(pending)]]
    pending <- pending[-length(pending)]
    if (exists(key, envir = visited, inherits = FALSE)) next
    assign(key, TRUE, envir = visited)
    records <- now$records_of(key)
    if (is.null(records) || !nrow(records)) next
    gate <- if (key %in% pinned) NA_character_ else unname(gates[key])
    counted <- fec_gate_counts(records$date, gate)
    if (!all(counted)) excluded[[length(excluded) + 1L]] <- records[!counted]
    if (!is.na(gate) && any(counted & !nzchar(records$date)))
      undated[[length(undated) + 1L]] <- records[counted & !nzchar(date)]
    net <- records[counted, .(net = sum(cents)), by = .(position, destination)][net > 0]
    if (!nrow(net)) next
    pairs[[length(pairs) + 1L]] <- list(source = key, destination = net$destination)
    if (detail) counted_legs[[length(counted_legs) + 1L]] <- records[counted & position %in% net$position]
    onward <- setdiff(net$destination, stops)
    if (length(onward)) pending <- c(pending, rev(onward))
  }
  pairs <- unique(data.table::rbindlist(pairs))
  if (!nrow(pairs)) pairs <- data.table::data.table(source = character(), destination = character())
  kept <- data.table::rbindlist(counted_legs)
  edges <- current$rows[0L]
  if (detail && nrow(kept)) {
    totals <- kept[, .(net = sum(cents), count = .N,
      first = if (any(nzchar(date))) min(date[nzchar(date)]) else "",
      last = if (any(nzchar(date))) max(date[nzchar(date)]) else ""), by = position]
    edges <- data.table::copy(current$rows[totals$position])
    if ("records" %in% names(edges)) edges[, records := fec_transfer_edge_records(records, current$edge_path)]
    # fread types an ISO date column as IDate; a rewritten bound keeps that type.
    as_date_column <- function(column, value) {
      if (inherits(column, "IDate")) return(data.table::as.IDate(if (nzchar(value)) value else NA_character_))
      if (inherits(column, "Date")) return(as.Date(if (nzchar(value)) value else NA_character_))
      value
    }
    full <- vapply(seq_len(nrow(totals)), function(i) {
      position <- totals$position[[i]]
      all <- now$records_of(current$rows$source[[position]])
      totals$count[[i]] == sum(all$position == position)
    }, logical(1L))
    for (i in which(!full)) {
      keep <- kept[position == totals$position[[i]], record]
      data.table::set(edges, i, "amount", totals$net[[i]] / 100)
      if ("record_count" %in% names(edges)) data.table::set(edges, i, "record_count", totals$count[[i]])
      if ("first_date" %in% names(edges)) data.table::set(edges, i, "first_date", as_date_column(edges$first_date, totals$first[[i]]))
      if ("last_date" %in% names(edges)) data.table::set(edges, i, "last_date", as_date_column(edges$last_date, totals$last[[i]]))
      if ("records" %in% names(edges) && !anyNA(keep)) {
        parsed <- jsonlite::fromJSON(edges$records[[i]], simplifyDataFrame = TRUE)
        data.table::set(edges, i, "records", as.character(jsonlite::toJSON(parsed[keep, , drop = FALSE],
          dataframe = "rows", auto_unbox = TRUE, digits = NA)))
      }
    }
    # F14: each published edge names its source's gate, where the gate came from, the
    # records the date rule left out of it, and - for a carried-over source - the
    # previous-cycle route that opened it. The Transfer Paths sheet prints them.
    edges[, `:=`(excluded_before_gate_records = 0L, excluded_before_gate_amount = 0)]
    if (length(excluded)) {
      dropped <- data.table::rbindlist(excluded)[, .(records = .N, amount = sum(cents) / 100), by = position]
      at <- match(totals$position, dropped$position)
      hit <- which(!is.na(at))
      data.table::set(edges, hit, "excluded_before_gate_records", dropped$records[at[hit]])
      data.table::set(edges, hit, "excluded_before_gate_amount", dropped$amount[at[hit]])
    }
    rooted <- edges$source %in% pinned
    edges[, `:=`(source_gate = ifelse(rooted, fec_cycle_start(target_cycle), unname(gates[source])),
                 source_gate_source = ifelse(rooted, "root", unname(network$gate_source[source])),
                 source_carryover_route = "")]
    carried <- unique(edges[source_gate_source == "carryover", source])
    if (length(carried)) {
      routes <- vapply(carried, function(key) paste(fec_carryover_route(key, network, target_cycle), collapse = " > "), "")
      edges[source_gate_source == "carryover", source_carryover_route := unname(routes[source])]
    }
    data.table::setorderv(edges, c("source", "destination"))
  }
  excluded <- data.table::rbindlist(excluded); undated <- data.table::rbindlist(undated)
  reached <- ls(visited)
  census <- list(rule = "earliest_arrival", carryover = network$carryover$rule,
    carryover_status = network$carryover$status,
    carryover_committees = length(network$seeds),
    carryover_missing_units = length(network$carryover$missing_units),
    gated_committees = sum(!reached %in% pinned),
    excluded_before_gate_edges = if (nrow(excluded)) data.table::uniqueN(excluded$position) else 0L,
    excluded_before_gate_rows = nrow(excluded),
    excluded_before_gate_amount = if (nrow(excluded)) sum(excluded$cents) / 100 else 0,
    date_unverified_rows = nrow(undated),
    date_unverified_amount = if (nrow(undated)) sum(undated$cents) / 100 else 0,
    walk_matches_gates = setequal(reached, names(gates)))
  list(edges = edges, gates = gates[reached], gate_source = network$gate_source[reached], census = census,
       pairs = pairs, seeds = network$seeds, carryover = network$carryover)
}

# One view's reach as fec_beneficiary_reach and the company bridge read it: every
# committee the view's walk reached with its gate and where the gate came from, the
# counted positive transfers it followed, and each carried-over committee's route.
fec_view_network_rows <- function(target_cycle, view, company, network) {
  keys <- names(network$gates)
  carried <- names(network$seeds)[names(network$seeds) %in% keys]
  routes <- vapply(carried, function(key) paste(fec_carryover_route(key, network, target_cycle), collapse = " > "),
                   character(1L))
  list(
    gates = data.table::data.table(cycle = target_cycle, view = view, company = company, committee_id = keys,
      arrival_gate = unname(network$gates), gate_source = unname(network$gate_source)),
    pairs = if (nrow(network$pairs)) data.table::data.table(cycle = target_cycle, view = view, company = company,
      source = network$pairs$source, destination = network$pairs$destination) else NULL,
    carryover = if (length(carried)) data.table::data.table(cycle = target_cycle, view = view, company = company,
      committee_id = carried, representative_root = vapply(carried, function(key)
        fec_carryover_route(key, network, target_cycle)[[1L]], character(1L), USE.NAMES = FALSE),
      representative_path = unname(routes)) else NULL,
    census = c(list(cycle = target_cycle, view = view, company = company,
      roots = sum(network$gate_source == "root"), reached_committees = length(keys)),
      network$census[c("carryover_status", "carryover_committees", "carryover_missing_units", "gated_committees")]))
}

# One cycle's published index as fec_transfer_context trusts it: VERSION 6
# count_all, built from the raw OTH file beside it, edges matching their checksum.
# Returns its coverage entry and, when indexed, its edges and their path.
fec_transfer_index_read <- function(directory, target_cycle) {
  path <- file.path(directory, paste0("oth", substr(target_cycle, 3, 4)), "transfer-info.json")
  if (!file.exists(path)) return(list(coverage = list(cycle = target_cycle, status = "missing_transfer_source")))
  index <- jsonlite::fromJSON(path)
  # VERSION 6 preserves ambiguous distinct observations by default and counts
  # an FEC reload once (F11). Older indexes and sensitivity-policy results
  # cannot stand in for this view.
  if (is.null(index$version) || index$version != 6L || !identical(index$ambiguous_version_policy, "count_all"))
    return(list(coverage = list(cycle = target_cycle, status = "stale_transfer_index", unresolved_all_sources = TRUE)))
  raw_path <- file.path(dirname(path), "itoth.txt")
  if (file.exists(raw_path) && (is.null(index$source_sha256) ||
      digest::digest(file = raw_path, algo = "sha256") != index$source_sha256))
    return(list(coverage = list(cycle = target_cycle, status = "stale_transfer_index", unresolved_all_sources = TRUE,
      reason = "Transfer index does not match the current raw OTH source")))
  edge_path <- file.path(dirname(path), "transfer-edges.csv")
  if (is.null(index$edges_sha256) ||
      digest::digest(file = edge_path, algo = "sha256") != index$edges_sha256)
    stop("Transfer edge checksum failed: ", edge_path)
  rows <- data.table::fread(edge_path, showProgress = FALSE)
  list(rows = rows, edge_path = edge_path, coverage = list(cycle = target_cycle, status = "indexed",
    source_sha256 = index$source_sha256, counts = index$counts,
    raw_source_available = file.exists(raw_path),
    ledger_path = index$ledger_path, max_depth = NULL,
    traversal = "earliest arrival (F12) over the observed network",
    unresolved_sources = as.list(index$unresolved_sources),
    # Per-source withheld dollars travel beside the source list: the list says
    # WHICH committees hold an unplaceable row, this says HOW MUCH, and only
    # the amount lets the allocator keep that committee's clean edges.
    unresolved_source_amounts = as.list(index$unresolved_source_amounts),
    unresolved_all_sources = isTRUE(index$unresolved_all_sources)))
}

# `views` (F15, Paul approved 2026-09-26): data.table(cycle, view, company,
# committee_id) of each company view's funded roots. Every view gets its own walk
# from its own roots, with the same rule and carryover, sharing the cycle's parsed
# legs; the industry view keeps the industry-wide walk. `networks` returns every
# view's reach (gates, followed pairs, carried-over routes) and census.
fec_transfer_context <- function(directory, roots, candidate_links, cycles, max_depth = Inf, carryover = "onward",
                                 views = NULL) {
  # F12 follows every transfer the roots' money could have made, by date; the
  # cycle-by-cycle depth limit is gone with the undated walk it bounded.
  if (is.finite(max_depth)) stop("The date-aware transfer walk (F12) takes no depth limit.", call. = FALSE)
  networks <- list()
  edges <- list(); coverage <- list(); expanded <- data.table::copy(roots)
  expanded[, `:=`(arrival_gate = NA_character_, gate_source = "recipient")]
  loaded <- list()
  trusted <- function(target) {
    key <- as.character(target)
    if (is.null(loaded[[key]])) loaded[[key]] <<- fec_transfer_index_read(directory, target)
    loaded[[key]]
  }
  for (target_cycle in cycles) {
    index <- trusted(target_cycle)
    coverage[[length(coverage) + 1L]] <- index$coverage
    if (!identical(index$coverage$status, "indexed") || !nrow(index$rows)) next
    stops <- candidate_links[cycle == target_cycle & designation %in% c("A", "P"), committee_id]
    seed_mask <- if ("traversal_seed" %in% names(roots)) roots$traversal_seed else roots$preliminary_crypto_funding > 0
    pinned <- sort(unique(setdiff(roots$committee_id[roots$cycle == target_cycle & seed_mask], stops)),
                   decreasing = TRUE, method = "radix")
    # Carryover reads the previous cycle's index; when it is not trusted (2020 is
    # never indexed) the carryover is unknown and the in-cycle rule stands alone.
    previous_cycle <- as.integer(fec_previous_cycle(target_cycle))
    previous <- if (identical(carryover, "off")) NULL else trusted(previous_cycle)
    previous_status <- if (is.null(previous)) "off" else previous$coverage$status
    if (!identical(previous_status, "indexed")) previous <- NULL
    # The previous-cycle walk stops at THAT cycle's candidate committees. A build's
    # candidate_links cover only its own cycles (every single-cycle build, and the
    # earliest cycle of a multi-cycle one), so the previous cycle's list is read here.
    # Without it the walk would run through candidate committees: the carryover is
    # then unknown, never widened (D-040).
    previous_links <- candidate_links[cycle == previous_cycle]
    if (!is.null(previous) && !nrow(previous_links)) {
      previous_links <- tryCatch(fec_read_candidate_committee_links(directory, previous_cycle),
                                 error = function(error) NULL)
      if (is.null(previous_links)) {
        previous <- NULL
        previous_status <- "missing_candidate_links"
      }
    }
    previous_stops <- if (is.null(previous_links)) character() else
      previous_links[designation %in% c("A", "P"), committee_id]
    now <- fec_transfer_reader(index, stops)
    before <- if (is.null(previous)) NULL else fec_transfer_reader(previous, previous_stops)
    network <- fec_transfer_network(target_cycle, pinned, index, stops, previous, previous_stops, carryover,
      now = now, before = before)
    network$census$previous_cycle <- previous_cycle
    network$census$previous_index <- previous_status
    coverage[[length(coverage)]]$date_rule <- network$census
    edges[[length(edges) + 1L]] <- network$edges
    networks[[length(networks) + 1L]] <- fec_view_network_rows(target_cycle, "Industry", "Industry Total", network)
    scopes <- if (is.null(views)) NULL else unique(views[cycle == target_cycle, .(view, company)])
    for (i in seq_len(NROW(scopes))) {
      view_pinned <- sort(unique(setdiff(views[cycle == target_cycle & view == scopes$view[[i]] &
        company == scopes$company[[i]], committee_id], stops)), decreasing = TRUE, method = "radix")
      if (!length(view_pinned)) next
      found <- fec_transfer_network(target_cycle, view_pinned, index, stops, previous, previous_stops, carryover,
        now = now, before = before, detail = FALSE)
      networks[[length(networks) + 1L]] <- fec_view_network_rows(target_cycle, scopes$view[[i]], scopes$company[[i]], found)
    }
    rm(now, before)
    reached <- names(network$gates)
    expanded[cycle == target_cycle & committee_id %in% pinned,
             `:=`(arrival_gate = fec_cycle_start(target_cycle), gate_source = "root")]
    missing <- setdiff(reached, expanded[cycle == target_cycle, committee_id])
    if (length(missing)) expanded <- data.table::rbindlist(list(expanded,
      data.table::data.table(cycle = target_cycle, committee_id = missing,
        committee_name = missing, recipient_category = "", preliminary_crypto_funding = 0,
        arrival_gate = unname(network$gates[missing]), gate_source = unname(network$gate_source[missing]))),
      fill = TRUE)
    # Keep at most this cycle and the one before in memory.
    loaded[[as.character(previous_cycle - 2L)]] <- NULL
  }
  part <- function(name) data.table::rbindlist(lapply(networks, `[[`, name), fill = TRUE)
  list(universe = expanded, edges = data.table::rbindlist(edges, fill = TRUE), coverage = coverage,
       networks = list(gates = part("gates"), pairs = part("pairs"), carryover = part("carryover"),
                       census = lapply(networks, `[[`, "census")))
}

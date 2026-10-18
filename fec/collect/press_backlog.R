# Separate untouched/deadline-cut work from completed network attempts. The
# per-member ledger persists fair retry order across daily checkpoint trees.
press_backlog_empty <- function() data.frame(
  url = character(0), attempts = integer(0), outcome = character(0),
  run_key = character(0), source_key = character(0), evidence_key = character(0),
  last_attempt_date = character(0), last_exhausted_date = character(0),
  first_seen_date = character(0), stringsAsFactors = FALSE)

press_backlog_ledger_path <- function(bg) {
  if (length(bg) != 1L || !grepl("^[A-Z][0-9]{6}$", bg)) stop("invalid press backlog member identity")
  file.path(PRESS_BACKLOG_DIRECTORY, paste0(bg, ".rds"))
}

press_backlog_read <- function(bg) {
  path <- press_backlog_ledger_path(bg)
  if (!file.exists(path)) return(press_backlog_empty())
  saved <- readRDS(path)
  if (!identical(saved$schema, "press-body-attempts-v1") || !is.data.frame(saved$rows) ||
      !all(c("url", "attempts", "outcome", "run_key", "source_key") %in% names(saved$rows)))
    stop("invalid press body attempt ledger: ", bg)
  # Older ledgers did not bind listing evidence. They reopen once; never infer
  # a completed attempt for an evidence generation they did not record. A legacy
  # row has no recorded first sighting either; "" is kept as-is and ordered as the
  # OLDEST sighting (see press_backlog_order), never stamped or inferred.
  for (name in c("evidence_key", "last_attempt_date", "last_exhausted_date", "first_seen_date"))
    if (!name %in% names(saved$rows)) saved$rows[[name]] <- rep("", nrow(saved$rows))
  for (name in c("last_attempt_date", "last_exhausted_date", "first_seen_date")) {
    value <- saved$rows[[name]][nzchar(saved$rows[[name]])]
    parsed <- suppressWarnings(as.Date(value, format = "%Y-%m-%d"))
    if (anyNA(parsed) || any(!grepl("^[0-9]{4}-[0-9]{2}-[0-9]{2}$", value)) ||
        any(as.character(parsed) != value)) stop("invalid press attempt date in ledger: ", bg)
  }
  if (
      anyNA(saved$rows) || anyDuplicated(saved$rows$url) || any(!nzchar(saved$rows$url)) ||
      !is.numeric(saved$rows$attempts) || any(saved$rows$attempts < 0) ||
      any(saved$rows$attempts != floor(saved$rows$attempts)) ||
      any(!saved$rows$outcome %in% c("pending", "completed", "exhausted")))
    stop("invalid press body attempt ledger: ", bg)
  # Canonical aliases belong to one member/URL retry history. Keep the latest
  # recorded evidence/outcome and all actual attempts when migrating aliases.
  keys <- press_backlog_url_key(saved$rows$url)
  if (anyDuplicated(keys)) {
    totals <- tapply(saved$rows$attempts, keys, sum)
    # The earliest sighting of any alias is the URL's first sighting.
    firsts <- vapply(split(saved$rows$first_seen_date, keys), function(values) {
      values <- values[nzchar(values)]
      if (length(values)) min(values) else ""
    }, character(1))
    keep <- !duplicated(keys, fromLast = TRUE)
    saved$rows <- saved$rows[keep, , drop = FALSE]
    saved$rows$attempts <- as.integer(totals[keys[keep]])
    saved$rows$first_seen_date <- as.character(firsts[keys[keep]])
  }
  saved$rows
}

press_backlog_cached <- function(bg) {
  press_backlog_cache_evidence(bg)$url
}

press_backlog_run_date <- function() {
  name <- if (exists("run_root", inherits = TRUE)) basename(run_root) else ""
  if (grepl("^press_refresh_[0-9]{8}$", name)) {
    value <- as.Date(sub("^press_refresh_", "", name), format = "%Y%m%d")
    if (is.na(value)) stop("invalid press run date")
    return(value)
  }
  Sys.Date()
}

# A long catch-up may finish after its transaction date. Maintenance age uses
# the actual local attempt day, so crossing midnight cannot shorten the wait.
press_backlog_attempt_date <- function() Sys.Date()

press_backlog_usable_body <- function(value) {
  value <- trimws(as.character(value))
  usable <- !is.na(value) & nchar(value) >= 200L
  usable[is.na(usable)] <- FALSE
  for (number in which(usable))
    usable[[number]] <- length(strsplit(value[[number]], "[[:space:]]+")[[1L]]) >= 20L
  usable
}

press_cached_body_complete <- function(frame) {
  if (is.null(frame) || !all(c("body", "date_detail") %in% names(frame)))
    return(rep(FALSE, if (is.null(frame)) 0L else nrow(frame)))
  value <- as.character(frame$date_detail)
  dates <- suppressWarnings(as.Date(value, format = "%Y-%m-%d"))
  # Old short shells remain stored as evidence but no longer suppress a real
  # detail request. The unchanged member fetcher retains prior rows if that
  # request fails, and a usable recovered article replaces the shell.
  complete <- press_backlog_usable_body(frame$body) & !is.na(dates) &
    grepl("^[0-9]{4}-[0-9]{2}-[0-9]{2}$", value) & as.character(dates) == value &
    dates >= as.Date("1900-01-01") & dates <= press_backlog_run_date()
  complete[is.na(complete)] <- FALSE
  complete
}

press_backlog_url_key <- function(value) {
  value <- trimws(as.character(value))
  if (anyNA(value) || any(!grepl("^https?://[^/[:space:]]+", value, ignore.case = TRUE)))
    stop("invalid press cache URL identity")
  tolower(sub("^https?://(www\\.)?", "", sub("/$", "", sub("#.*$", "", value)), ignore.case = TRUE))
}

press_backlog_cache_evidence <- function(bg) {
  empty <- data.frame(url = character(0), date_detail = as.Date(character(0)),
                      url_key = character(0), stringsAsFactors = FALSE)
  path <- file.path(CHECKPOINT_DIR, "fulltext", paste0(bg, ".rds"))
  if (!file.exists(path)) return(empty)
  frame <- readRDS(path)
  if (!all(c("url", "body", "date_detail") %in% names(frame))) return(empty)
  # File identity is the upstream member checkpoint key. If rows additionally
  # declare an identity, it must agree; a shared URL never transfers authorship.
  if ("bioguide" %in% names(frame) && any(is.na(frame$bioguide) | toupper(trimws(frame$bioguide)) != bg))
    stop("press cached body member identity mismatch: ", bg)
  # An implausible date is not evidence of age or completion. Keep that URL
  # unresolved so the actual HTTP retry can correct or honestly exhaust it.
  frame <- frame[press_cached_body_complete(frame), , drop = FALSE]
  if (!nrow(frame)) return(empty)
  dates <- as.Date(as.character(frame$date_detail))
  keys <- press_backlog_url_key(frame$url)
  if (any(vapply(split(as.character(dates), keys), function(values) length(unique(values)) > 1L, logical(1))))
    stop("conflicting cached press publication dates: ", bg)
  data.frame(url = frame$url, date_detail = dates, url_key = keys, stringsAsFactors = FALSE)
}

press_backlog_restore_dates <- function(index) {
  evidence <- list()
  for (bg in unique(index$bioguide)) {
    press_backlog_ledger_path(bg) # validate filename identity before reading
    cache <- press_backlog_cache_evidence(bg)
    candidates <- which(index$bioguide == bg & is.na(index$date))
    if (!length(candidates) || !nrow(cache)) next
    # Match the existing production canonical URL within this member's own
    # checkpoint. Never infer dates from slugs, redirects, CDX capture time, or
    # another member's cache. Canonical aliases with conflicting dates reject.
    matched <- match(press_backlog_url_key(index$url[candidates]), cache$url_key)
    take <- candidates[!is.na(matched)]
    matched <- matched[!is.na(matched)]
    if (!length(take)) next
    if (!identical(press_backlog_url_key(index$url[take]), cache$url_key[matched]))
      stop("press cached date URL identity mismatch: ", bg)
    candidate_urls <- index$url[take]
    index$date[take] <- cache$date_detail[matched]
    # Request the already-fetched equivalent URL so upstream's exact-URL cache
    # join preserves its usable body rather than fetching an alias again.
    index$url[take] <- cache$url[matched]
    evidence[[length(evidence) + 1L]] <- data.frame(
      bioguide = bg, url = candidate_urls, cached_url = cache$url[matched], date_detail = cache$date_detail[matched],
      evidence = "canonical_url_member_body_checkpoint", stringsAsFactors = FALSE)
  }
  proof <- if (length(evidence)) do.call(rbind, evidence) else data.frame(
    bioguide = character(0), url = character(0), cached_url = character(0), date_detail = as.Date(character(0)), evidence = character(0))
  list(index = index, evidence = proof)
}

press_backlog_listing_evidence <- function(index) {
  fields <- c("title", "date", "lastmod", "last_modified", "modified", "updated_at", "published_at")
  vapply(seq_len(nrow(index)), function(row) {
    values <- lapply(fields, function(name) {
      value <- if (name %in% names(index)) as.character(index[[name]][row]) else ""
      if (is.na(value)) "" else gsub("[[:space:]]+", " ", trimws(value))
    })
    names(values) <- fields
    digest::digest(jsonlite::toJSON(values, auto_unbox = TRUE), algo = "sha256", serialize = FALSE)
  }, character(1))
}

press_backlog_evidence_for <- function(bg, urls) {
  index <- PRESS_BACKLOG_CONTEXT$index
  index <- index[index$bioguide == bg, , drop = FALSE]
  evidence <- if ("evidence_key" %in% names(index)) index$evidence_key else press_backlog_listing_evidence(index)
  keys <- press_backlog_url_key(index$url)
  if (any(vapply(split(evidence, keys), function(values) length(unique(values)) > 1L, logical(1))))
    stop("conflicting press listing evidence for member/URL: ", bg)
  evidence[match(press_backlog_url_key(urls), keys)]
}

press_backlog_current <- function(rows, bg) {
  evidence <- press_backlog_evidence_for(bg, rows$url)
  !is.na(evidence) & nzchar(rows$evidence_key) & rows$evidence_key == evidence &
    rows$source_key == PRESS_BACKLOG_CONTEXT$source_key &
    (rows$outcome != "exhausted" | (rows$attempts > 0L & nzchar(rows$last_exhausted_date)))
}

press_member_network_result <- function(result) {
  bg <- result$bioguide
  rows <- press_backlog_read(bg)
  completed <- intersect(result$completed_urls, press_backlog_cached(bg))
  requested <- unique(result$requested_urls)
  evidence_keys <- press_backlog_evidence_for(bg, requested)
  if (anyNA(evidence_keys)) stop("press attempt lacks bound listing evidence: ", bg)
  for (number in seq_along(requested)) {
    url <- requested[number]
    index <- match(press_backlog_url_key(url), press_backlog_url_key(rows$url))
    if (is.na(index)) {
      rows <- rbind(rows, data.frame(url = url, attempts = 0L, outcome = "pending",
        run_key = "", source_key = "", evidence_key = "", last_attempt_date = "",
        last_exhausted_date = "", first_seen_date = as.character(press_backlog_run_date()),
        stringsAsFactors = FALSE))
      index <- nrow(rows)
    }
    attempted <- url %in% result$attempted_urls
    maintenance <- isTRUE(PRESS_BACKLOG_CONTEXT$maintenance_active)
    retain_exhaustion <- maintenance && rows$outcome[index] == "exhausted" &&
      isTRUE(press_backlog_current(rows[index, , drop = FALSE], bg))
    rows$attempts[index] <- rows$attempts[index] + as.integer(attempted)
    # Three attempts end a URL's turn even if this run deferred it or never reached it;
    # otherwise a URL the budget keeps cutting short would hold the member pending forever.
    outcome <- if (url %in% completed) "completed" else
      if (rows$attempts[index] >= 3L) "exhausted" else
      if (url %in% result$budget_deferred_urls || !attempted) "pending" else "exhausted"
    rows$outcome[index] <- if (retain_exhaustion && outcome != "completed") "exhausted" else outcome
    if (attempted) rows$last_attempt_date[index] <- as.character(press_backlog_attempt_date())
    if (attempted && outcome == "exhausted") rows$last_exhausted_date[index] <- as.character(press_backlog_attempt_date())
    rows$url[index] <- url
    rows$run_key[index] <- PRESS_BACKLOG_CONTEXT$run_key
    rows$source_key[index] <- PRESS_BACKLOG_CONTEXT$source_key
    rows$evidence_key[index] <- evidence_keys[number]
  }
  dir.create(PRESS_BACKLOG_DIRECTORY, recursive = TRUE, showWarnings = FALSE)
  write_rds_atomic(list(schema = "press-body-attempts-v1", rows = rows), press_backlog_ledger_path(bg))
  invisible(NULL)
}

press_backlog_order <- function(bg, urls, force = FALSE) {
  urls <- unique(urls)
  rows <- press_backlog_read(bg)
  # A completed chain remains settled across days for the same canonical URL,
  # listing evidence, and collector generation. Age alone never blocks PASS.
  settled <- rows$url[press_backlog_current(rows, bg) & rows$outcome == "exhausted"]
  if (!force && !isTRUE(PRESS_BACKLOG_CONTEXT$maintenance_active))
    urls <- urls[!press_backlog_url_key(urls) %in% press_backlog_url_key(settled)]
  counts <- rows$attempts[match(press_backlog_url_key(urls), press_backlog_url_key(rows$url))]
  counts[is.na(counts)] <- 0L
  index <- PRESS_BACKLOG_CONTEXT$index
  index <- index[index$bioguide == bg, , drop = FALSE]
  dates <- as.numeric(as.Date(index$date[match(urls, index$url)]))
  dates[is.na(dates)] <- -Inf
  matched <- match(press_backlog_url_key(urls), press_backlog_url_key(rows$url))
  first <- suppressWarnings(as.numeric(as.Date(rows$first_seen_date[matched], format = "%Y-%m-%d")))
  # A legacy ledger row ("" first_seen_date) is the oldest sighting; only a URL with no
  # ledger row at all is new and counts as first seen today.
  first[is.na(first) & !is.na(matched)] <- -Inf
  first[is.na(matched)] <- as.numeric(press_backlog_run_date())
  # FIFO fairness: a URL with three failed attempts goes behind everything else; the rest are
  # served oldest-first-sighting, so a member that lists new URLs every run cannot keep pushing
  # the URLs it listed earlier past the per-member request budget. A legacy ledger row with no
  # recorded first sighting ("") is the oldest and goes first; a brand-new URL not yet in the
  # ledger counts as first seen today and goes after every ledger row. Within one sighting
  # day, never-attempted URLs precede repeat failures; newer publication dates and
  # the URL key then make the finite backlog advance without relying on worker completion order.
  urls[order(counts >= 3L, first, counts, -dates, press_backlog_url_key(urls))]
}

press_backlog_coverage_status <- function(index) {
  if ("coverage_status" %in% names(index)) return(as.character(index$coverage_status))
  if (exists("press_official_coverage_classify", inherits = TRUE))
    return(press_official_coverage_classify(
      index, DATE_FLOOR, environment(press_backlog_coverage_status)))
  known <- !is.na(index$date)
  status <- rep("blocking_current_uncertainty", nrow(index))
  status[known & as.Date(index$date) < DATE_FLOOR] <- "historical_pre_floor"
  status[known & as.Date(index$date) >= DATE_FLOOR] <- "dated_current_uncovered"
  status
}

press_backlog_is_blocking <- function(status) {
  status %in% c("official_current_inventory", "blocking_current_uncertainty",
                "dated_current_uncovered")
}

press_backlog_historical_snapshot <- function() {
  if (!exists("PRESS_BACKLOG_CONTEXT", inherits = TRUE)) stop("press backlog coverage context missing")
  index <- PRESS_BACKLOG_CONTEXT$index
  status <- press_backlog_coverage_status(index)
  historical <- index[!press_backlog_is_blocking(status), , drop = FALSE]
  historical$official_coverage_status <- status[!press_backlog_is_blocking(status)]
  historical$collection_status <- rep("retained_nonblocking_maintenance", nrow(historical))
  historical
}

press_backlog_snapshot <- function() {
  if (!exists("PRESS_BACKLOG_CONTEXT", inherits = TRUE)) stop("press backlog coverage context missing")
  index <- PRESS_BACKLOG_CONTEXT$index
  pending <- list()
  for (bg in unique(index$bioguide)) {
    rows <- press_backlog_read(bg)
    settled <- rows$url[press_backlog_current(rows, bg) & rows$outcome == "exhausted"]
    member <- index[index$bioguide == bg, , drop = FALSE]
    status <- press_backlog_coverage_status(member)
    member <- member[press_backlog_is_blocking(status), , drop = FALSE]
    member <- member[!press_backlog_url_key(member$url) %in%
                       press_backlog_url_key(c(press_backlog_cached(bg), settled)), , drop = FALSE]
    if (nrow(member)) {
      member$collection_status <- "body_pending_network_attempt"
      pending[[length(pending) + 1L]] <- member
    }
  }
  if (length(pending)) do.call(rbind, pending) else {
    result <- index[FALSE, , drop = FALSE]
    result$collection_status <- character(0)
    result
  }
}

press_backlog_ledger_snapshot <- function() {
  members <- sort(unique(PRESS_BACKLOG_CONTEXT$index$bioguide))
  list(schema = "press-body-attempts-snapshot-v1", source_key = PRESS_BACKLOG_CONTEXT$source_key,
       run_key = PRESS_BACKLOG_CONTEXT$run_key,
       members = setNames(lapply(members, press_backlog_read), members))
}

press_backlog_revisit_cap <- function() {
  value <- Sys.getenv("PRESS_REVISIT_URL_CAP", "100")
  if (!grepl("^[0-9]+$", value)) stop("PRESS_REVISIT_URL_CAP must be a nonnegative integer")
  cap <- suppressWarnings(as.integer(value))
  if (is.na(cap) || cap > 10000L) stop("PRESS_REVISIT_URL_CAP exceeds the bounded maintenance limit")
  cap
}

press_backlog_due_revisits <- function() {
  due <- list()
  for (bg in sort(unique(PRESS_BACKLOG_CONTEXT$index$bioguide))) {
    rows <- press_backlog_read(bg)
    last <- suppressWarnings(as.Date(rows$last_attempt_date, format = "%Y-%m-%d"))
    take <- press_backlog_current(rows, bg) & rows$outcome == "exhausted" &
      !is.na(last) & last + 7L <= press_backlog_attempt_date() &
      !press_backlog_url_key(rows$url) %in% press_backlog_url_key(press_backlog_cached(bg))
    rows <- rows[take, , drop = FALSE]
    if (nrow(rows)) due[[length(due) + 1L]] <- data.frame(
      bioguide = bg, url = rows$url, due_date = as.Date(rows$last_attempt_date) + 7L,
      attempts = rows$attempts, stringsAsFactors = FALSE)
  }
  if (!length(due)) return(data.frame(bioguide = character(0), url = character(0),
    due_date = as.Date(character(0)), attempts = integer(0)))
  frame <- do.call(rbind, due)
  frame[order(frame$due_date, frame$attempts, frame$bioguide, press_backlog_url_key(frame$url)), , drop = FALSE]
}

press_backlog_due_historical <- function() {
  historical <- press_backlog_historical_snapshot()
  if (!nrow(historical)) return(data.frame(
    bioguide = character(0), url = character(0), due_date = as.Date(character(0)),
    attempts = integer(0), maintenance_kind = character(0), stringsAsFactors = FALSE))
  due <- list()
  for (bg in sort(unique(historical$bioguide))) {
    member <- historical[historical$bioguide == bg, , drop = FALSE]
    cached <- press_backlog_url_key(press_backlog_cached(bg))
    member <- member[!press_backlog_url_key(member$url) %in% cached, , drop = FALSE]
    if (!nrow(member)) next
    ledger <- press_backlog_read(bg)
    matched <- match(press_backlog_url_key(member$url), press_backlog_url_key(ledger$url))
    attempts <- ledger$attempts[matched]
    attempts[is.na(attempts)] <- 0L
    last <- suppressWarnings(as.Date(ledger$last_attempt_date[matched], format = "%Y-%m-%d"))
    eligible <- is.na(last) | last + 7L <= press_backlog_attempt_date()
    member <- member[eligible, , drop = FALSE]
    if (!nrow(member)) next
    last <- last[eligible]
    attempts <- attempts[eligible]
    due_date <- last + 7L
    due_date[is.na(due_date)] <- as.Date("1900-01-01")
    due[[length(due) + 1L]] <- data.frame(
      bioguide = bg, url = member$url, due_date = due_date,
      attempts = as.integer(attempts), maintenance_kind = "historical_inventory",
      stringsAsFactors = FALSE)
  }
  if (!length(due)) return(data.frame(
    bioguide = character(0), url = character(0), due_date = as.Date(character(0)),
    attempts = integer(0), maintenance_kind = character(0), stringsAsFactors = FALSE))
  frame <- do.call(rbind, due)
  frame[order(frame$due_date, frame$attempts, frame$bioguide,
              press_backlog_url_key(frame$url)), , drop = FALSE]
}

press_backlog_due_maintenance <- function() {
  revisits <- press_backlog_due_revisits()
  revisits$maintenance_kind <- rep("settled_revisit", nrow(revisits))
  historical <- press_backlog_due_historical()
  combined <- dplyr::bind_rows(revisits, historical)
  if (!nrow(combined)) return(combined)
  keys <- paste(combined$bioguide, press_backlog_url_key(combined$url), sep = "#")
  # Historical classification wins a duplicate because it may be cached for
  # research but cannot re-enter the current publication through maintenance.
  combined <- combined[!duplicated(keys, fromLast = TRUE), , drop = FALSE]
  combined[order(combined$due_date, combined$attempts, combined$bioguide,
                 press_backlog_url_key(combined$url)), , drop = FALSE]
}

press_backlog_maintain <- function(js_bgs = character(0), envir = .GlobalEnv) {
  due <- press_backlog_due_maintenance()
  cap <- press_backlog_revisit_cap()
  # A member that still has pending bodies spends its budget on them; every other member's
  # due revisits proceed, so one stuck member cannot switch maintenance off for everybody.
  busy <- unique(press_backlog_snapshot()$bioguide)
  selected <- head(due[!due$bioguide %in% busy, , drop = FALSE], cap)
  metrics <- list(minimum_age_days = 7L, url_cap = cap, due = nrow(due),
                  selected = nrow(selected), attempted = 0L, recovered = 0L,
                  historical_retained = nrow(press_backlog_historical_snapshot()),
                  historical_selected = sum(selected$maintenance_kind == "historical_inventory"))
  context <- get("PRESS_BACKLOG_CONTEXT", envir = envir)
  context$maintenance <- metrics
  context$maintenance_active <- TRUE
  assign("PRESS_BACKLOG_CONTEXT", context, envir)
  on.exit({
    context <- get("PRESS_BACKLOG_CONTEXT", envir = envir)
    context$maintenance_active <- FALSE
    assign("PRESS_BACKLOG_CONTEXT", context, envir)
  }, add = TRUE)
  for (index in seq_len(nrow(selected))) {
    bg <- selected$bioguide[index]
    url <- selected$url[index]
    before <- press_backlog_read(bg)
    key <- press_backlog_url_key(url)
    previous <- before$attempts[match(key, press_backlog_url_key(before$url))]
    if (!length(previous) || is.na(previous)) previous <- 0L
    # The original member flag retains its configured network/fallback chain.
    # The deadline adapter shares any remaining JS budget with earlier phases.
    tryCatch(get("fetch_member_fulltext", envir = envir)(bg, url, force = FALSE, js = bg %in% js_bgs),
      error = function(error) message("Non-blocking press revisit failed for ", bg, ": ", conditionMessage(error)))
    after <- press_backlog_read(bg)
    current <- after$attempts[match(key, press_backlog_url_key(after$url))]
    attempted <- length(current) == 1L && !is.na(current) && current > previous
    metrics$attempted <- metrics$attempted + as.integer(attempted)
    metrics$recovered <- metrics$recovered + as.integer(attempted &&
      key %in% press_backlog_url_key(press_backlog_cached(bg)))
  }
  context <- get("PRESS_BACKLOG_CONTEXT", envir = envir)
  context$maintenance <- metrics
  assign("PRESS_BACKLOG_CONTEXT", context, envir)
  message("Press non-blocking revisits: due=", metrics$due, " selected=", metrics$selected,
          " attempted=", metrics$attempted, " recovered=", metrics$recovered,
          " historical_retained=", metrics$historical_retained,
          " (minimum 7 days; cap=", cap, ")")
  selected
}

install_press_backlog <- function(root, envir = .GlobalEnv) {
  assign("PRESS_BACKLOG_DIRECTORY", file.path(root, "data", "engine", "press_body_attempts"), envir)
  original_member <- get("fetch_member_fulltext", envir = envir, inherits = FALSE)
  original_stage <- get("stage_fulltext", envir = envir, inherits = FALSE)
  if (isTRUE(attr(original_member, "melange_press_backlog"))) return(invisible(NULL))
  member <- function(bg, urls, force = FALSE, js = FALSE) {
    # The upstream committee and news lanes share this low-level fetcher but
    # are not entries in the congressional member-index coverage context.
    if (length(bg) == 1L && grepl("^(CMTE|NEWS)_[A-Za-z0-9_-]+$", bg))
      return(original_member(bg, urls, force = force, js = js))
    ordered <- press_backlog_order(bg, urls, force)
    cached <- if (force) character(0) else press_backlog_cached(bg)
    out <- original_member(bg, ordered, force = force, js = js)
    # The deadline adapter reports actual GETs and interrupted fallback chains
    # for JS/CDX members. A normal non-JS return means its unbounded loop visited
    # every uncached URL; exceptions never reach this completion hook.
    if (!isTRUE(js)) {
      offline <- nzchar(Sys.getenv("CU_PR_OFFLINE")) || nzchar(Sys.getenv("CU_PR_SKIP_FETCH"))
      completed <- if (is.null(out) || !"url" %in% names(out))
        character(0) else out$url[press_cached_body_complete(out)]
      press_member_network_result(list(bioguide = bg, requested_urls = ordered,
        attempted_urls = if (offline) character(0) else setdiff(ordered, cached),
        completed_urls = completed, budget_deferred_urls = if (offline) setdiff(ordered, cached) else character(0)))
    }
    out
  }
  attr(member, "melange_press_backlog") <- TRUE
  stage <- function(idx, force = FALSE, js_bgs = character(0)) {
    if (!all(c("bioguide", "url", "date") %in% names(idx))) stop("press index lacks backlog identity/date columns")
    # Listing identity comes from the raw index. Filling a missing date from
    # that URL's own cached body must not invent new listing evidence.
    evidence <- press_backlog_listing_evidence(idx)
    restored <- press_backlog_restore_dates(idx)
    idx <- restored$index
    idx$evidence_key <- evidence
    coverage_status <- press_backlog_coverage_status(idx)
    assign("PRESS_BACKLOG_CONTEXT", list(index = transform(
      idx[, c("bioguide", "url", "date", "evidence_key"), drop = FALSE],
      coverage_status = coverage_status), cached_dates = restored$evidence,
      run_key = normalizePath(run_root, winslash = "/", mustWork = FALSE),
      source_key = paste(unlist(collector_sources_sha256[sort(names(collector_sources_sha256))]), collapse = ":"),
      maintenance_active = FALSE), envir)
    # All known-current releases, including parallel members, run before the
    # undated legacy sweep. Unknown dates remain pending, never assumed old.
    known <- !is.na(idx$date)
    floor <- get("DATE_FLOOR", envir = envir, inherits = TRUE)
    blocking <- press_backlog_is_blocking(coverage_status)
    current <- known & as.Date(idx$date) >= floor & blocking
    unknown <- !known & blocking
    message("Press body coverage: ", sum(current), " current-dated, ", sum(!known),
            " unknown-date; ", sum(!blocking), " retained historical-maintenance row(s); ",
            sum(known & as.Date(idx$date) < floor), " affirmatively pre-floor cached/index rows.")
    outputs <- list()
    for (phase in list(which(current), which(unknown))) {
      if (!length(phase)) next
      outputs[[length(outputs) + 1L]] <- original_stage(idx[phase, , drop = FALSE], force, js_bgs)
    }
    revisited <- press_backlog_maintain(js_bgs, envir)
    publishable_revisits <- revisited[
      revisited$maintenance_kind != "historical_inventory", , drop = FALSE]
    if (nrow(publishable_revisits)) {
      keys <- paste(publishable_revisits$bioguide,
                    press_backlog_url_key(publishable_revisits$url), sep = "#")
      rows <- idx[paste(idx$bioguide, press_backlog_url_key(idx$url), sep = "#") %in% keys, , drop = FALSE]
      outputs <- lapply(outputs, function(frame) {
        if (!nrow(frame)) return(frame)
        frame[!paste(frame$bioguide, press_backlog_url_key(frame$url), sep = "#") %in% keys, , drop = FALSE]
      })
      # A successful maintenance recovery becomes part of this publication.
      # Replace the prior rows even when a recovered date proves a URL belongs
      # before the window and the upstream join correctly returns no row.
      # Failed revisits remain settled, so this cache join cannot repeat them.
      outputs[[length(outputs) + 1L]] <- original_stage(rows, FALSE, js_bgs)
    }
    output <- dplyr::bind_rows(outputs)
    if (nrow(output) && all(c("url", "bioguide") %in% names(output))) {
      keys <- paste(output$bioguide, press_backlog_url_key(output$url), sep = "#")
      output <- output[!duplicated(keys, fromLast = TRUE), , drop = FALSE]
    }
    output
  }
  assign("press_member_network_result", press_member_network_result, envir)
  assign("fetch_member_fulltext", member, envir)
  assign("stage_fulltext", stage, envir)
  invisible(NULL)
}

#!/usr/bin/env Rscript

suppressPackageStartupMessages({
  library(chromote)
  library(digest)
  library(future)
  library(future.apply)
  library(httr)
  library(jsonlite)
  library(rvest)
})

parse_args <- function(x) {
  out <- list(
    records = "audits/legfix5/congress_advanced_pages_20260725/congress_advanced_records.ndjson",
    out = "audits/legfix5/congress_advanced_pages_20260725/text_relevance_httr_shadow",
    limit = 0L, workers = 6L, host = "127.0.0.1", port = 9222L,
    resume = FALSE, delay = 0
  )
  i <- 1L
  while (i <= length(x)) {
    if (identical(x[[i]], "--resume")) {
      out$resume <- TRUE
      i <- i + 1L
      next
    }
    if (!startsWith(x[[i]], "--") || i == length(x)) stop("Invalid argument: ", x[[i]])
    key <- sub("^--", "", x[[i]])
    if (!key %in% names(out)) stop("Unknown argument: ", x[[i]])
    out[[key]] <- x[[i + 1L]]
    i <- i + 2L
  }
  out$limit <- as.integer(out$limit)
  out$workers <- as.integer(out$workers)
  out$port <- as.integer(out$port)
  out$delay <- as.numeric(out$delay)
  if (is.na(out$limit) || out$limit < 0L) stop("--limit must be non-negative")
  if (is.na(out$workers) || out$workers < 1L) stop("--workers must be positive")
  # Inter-batch pacing only.  It never retries and never re-requests a URL; it
  # only spaces successive batches so the lane does not reproduce the six 429
  # outcomes that fail-closed the 2026-07-26 run.  The default of 0 preserves
  # the original timing exactly.
  if (is.na(out$delay) || out$delay < 0 || out$delay > 60) {
    stop("--delay must be between 0 and 60 seconds")
  }
  out
}

args <- parse_args(commandArgs(trailingOnly = TRUE))
records_file <- normalizePath(args$records, winslash = "/", mustWork = TRUE)
out_dir <- normalizePath(args$out, winslash = "/", mustWork = FALSE)
events_dir <- file.path(out_dir, "events")
accepted_dir <- file.path(out_dir, "accepted_html")
receipts_dir <- file.path(out_dir, "receipts")
quarantine_dir <- file.path(out_dir, "quarantine")
failure_attempts_dir <- file.path(out_dir, "failure_attempts")
batches_dir <- file.path(out_dir, "batches")
for (directory in c(out_dir, events_dir, accepted_dir, receipts_dir, quarantine_dir, failure_attempts_dir, batches_dir)) {
  dir.create(directory, recursive = TRUE, showWarnings = FALSE)
}
invocation_started <- Sys.time()
command_line <- paste(commandArgs(), collapse = " ")
invocation_id <- digest(
  charToRaw(paste(
    format(invocation_started, "%Y-%m-%dT%H:%M:%OS6Z", tz = "UTC"),
    Sys.getpid(),
    command_line,
    sep = "\n"
  )),
  algo = "sha256",
  serialize = FALSE
)

json_text <- function(value) {
  paste0(toJSON(value, auto_unbox = TRUE, null = "null", pretty = TRUE, digits = NA), "\n")
}

write_checked_json <- function(file, value) {
  text <- json_text(value)
  dir.create(dirname(file), recursive = TRUE, showWarnings = FALSE)
  for (attempt in seq_len(3L)) {
    temporary <- paste0(file, ".", Sys.getpid(), ".", attempt, ".tmp")
    writeChar(text, temporary, eos = NULL, useBytes = TRUE)
    ok <- file.copy(temporary, file, overwrite = TRUE)
    unlink(temporary)
    if (!ok) next
    observed <- paste0(paste(readLines(file, warn = FALSE, encoding = "UTF-8"), collapse = "\n"), "\n")
    if (identical(observed, text)) return(invisible(TRUE))
  }
  stop("Checked JSON write failed: ", file)
}

write_immutable_json <- function(file, value) {
  text <- json_text(value)
  dir.create(dirname(file), recursive = TRUE, showWarnings = FALSE)
  if (file.exists(file)) {
    observed <- paste0(paste(readLines(file, warn = FALSE, encoding = "UTF-8"), collapse = "\n"), "\n")
    if (!identical(observed, text)) stop("Immutable receipt collision: ", file)
    return(invisible(TRUE))
  }
  temporary <- paste0(file, ".", Sys.getpid(), ".tmp")
  writeChar(text, temporary, eos = NULL, useBytes = TRUE)
  if (!file.rename(temporary, file)) {
    unlink(temporary)
    stop("Immutable receipt publish failed: ", file)
  }
  invisible(TRUE)
}

write_immutable_gzip <- function(file, body_raw) {
  expected <- digest(body_raw, algo = "sha256", serialize = FALSE)
  dir.create(dirname(file), recursive = TRUE, showWarnings = FALSE)
  if (file.exists(file)) {
    verify_existing <- gzfile(file, "rb")
    existing <- readBin(verify_existing, what = "raw", n = 1024L * 1024L * 256L)
    close(verify_existing)
    observed <- digest(existing, algo = "sha256", serialize = FALSE)
    if (!identical(observed, expected)) stop("Immutable gzip collision: ", file)
    return(expected)
  }
  temporary <- paste0(file, ".", Sys.getpid(), ".tmp")
  connection <- gzfile(temporary, "wb")
  writeBin(body_raw, connection)
  close(connection)
  verify <- gzfile(temporary, "rb")
  observed <- readBin(verify, what = "raw", n = length(body_raw) + 1L)
  close(verify)
  if (!identical(digest(observed, algo = "sha256", serialize = FALSE), expected)) {
    unlink(temporary)
    stop("Gzip readback mismatch: ", file)
  }
  if (!file.rename(temporary, file)) {
    unlink(temporary)
    stop("Immutable gzip publish failed: ", file)
  }
  expected
}

debugger_up <- function(host, port) {
  tryCatch({
    connection <- url(sprintf("http://%s:%d/json/version", host, port))
    on.exit(close(connection), add = TRUE)
    length(suppressWarnings(readLines(connection, n = 1L, warn = FALSE))) > 0L
  }, error = function(error) FALSE)
}

lock_dir <- file.path(out_dir, ".collector-lock")

# A lock left by a process that is no longer alive is stale, not contention.
# Previously the lock was never released at all (see the reg.finalizer note
# below), so every subsequent run died claiming another invocation held it.
# Reclaim it only when the recorded PID is provably gone.
if (dir.exists(lock_dir)) {
  owner_path <- file.path(lock_dir, "owner.json")
  owner_pid <- NA_integer_
  if (file.exists(owner_path)) {
    owner <- tryCatch(fromJSON(owner_path, simplifyVector = FALSE), error = function(e) NULL)
    if (!is.null(owner) && !is.null(owner$pid)) owner_pid <- suppressWarnings(as.integer(owner$pid))
  }
  holder_alive <- FALSE
  if (!is.na(owner_pid)) {
    probe <- suppressWarnings(tryCatch(
      system2("tasklist", c("/FI", shQuote(paste0("PID eq ", owner_pid)), "/NH"),
              stdout = TRUE, stderr = NULL),
      error = function(e) character()))
    holder_alive <- any(grepl(as.character(owner_pid), probe, fixed = TRUE))
  }
  if (holder_alive) {
    stop("Collector output is locked by a LIVE invocation (pid ", owner_pid, "): ", lock_dir)
  }
  unlink(lock_dir, recursive = TRUE, force = TRUE)
}

if (!dir.create(lock_dir, recursive = FALSE, showWarnings = FALSE)) {
  stop("Collector output is locked and the lock could not be reclaimed: ", lock_dir)
}
write_immutable_json(file.path(lock_dir, "owner.json"), list(
  schema = "congress-httr-collector-lock-v1",
  invocation_id = invocation_id,
  pid = Sys.getpid(),
  created_at = format(invocation_started, tz = "UTC", usetz = TRUE),
  command = command_line
))

# on.exit() only fires when the FUNCTION it was registered in returns. Registered
# at top level, as it was here, it is a silent no-op and the lock survived every
# exit -- including clean successful runs. reg.finalizer(onexit = TRUE) is the
# top-level equivalent and does run at interpreter shutdown.
.lock_release_token <- new.env(parent = emptyenv())
reg.finalizer(.lock_release_token, function(e) {
  unlink(lock_dir, recursive = TRUE, force = TRUE)
}, onexit = TRUE)

if (!debugger_up(args$host, args$port)) stop("Headful Chrome DevTools endpoint is unavailable")
chrome <- Chromote$new(browser = ChromeRemote$new(host = args$host, port = args$port))
set_default_chromote_object(chrome)
session <- ChromoteSession$new()
session$default_timeout <- 120
ua <- session$Runtime$evaluate("navigator.userAgent")$result$value
all_cookies <- session$Network$getAllCookies()$cookies
try(session$close(), silent = TRUE)
congress_cookies <- Filter(function(cookie) grepl("congress\\.gov", cookie$domain), all_cookies)
cookie_header <- paste(
  vapply(congress_cookies, function(cookie) paste0(cookie$name, "=", cookie$value), ""),
  collapse = "; "
)
has_clearance <- any(vapply(congress_cookies, function(cookie) identical(cookie$name, "cf_clearance"), TRUE))
if (!nzchar(cookie_header) || !has_clearance) stop("Chrome cookie harvest lacks cf_clearance")
rm(all_cookies, congress_cookies)

refresh_cf_session <- function(probe_url, timeout_seconds = 40) {
  targets <- tryCatch(
    chrome$Target$getTargets()$targetInfos,
    error = function(error) list()
  )
  congress_targets <- Filter(
    function(target) {
      identical(target$type, "page") &&
        grepl("https://www.congress.gov/", target$url, fixed = TRUE)
    },
    targets
  )
  if (!length(congress_targets)) return(NULL)
  preferred <- Filter(
    function(target) grepl("/text", target$url, fixed = TRUE),
    congress_targets
  )
  target <- if (length(preferred)) preferred[[1L]] else congress_targets[[1L]]
  refresh <- tryCatch(
    chrome$new_session(targetId = target$targetId),
    error = function(error) NULL
  )
  if (is.null(refresh)) return(NULL)
  refresh$default_timeout <- 120
  invisible(try(refresh$Page$bringToFront(), silent = TRUE))
  navigated <- tryCatch({
    refresh$Page$navigate(probe_url, wait_ = FALSE)
    TRUE
  }, error = function(error) FALSE)
  if (!navigated) return(NULL)
  deadline <- Sys.time() + timeout_seconds
  cleared <- FALSE
  while (Sys.time() < deadline) {
    state <- tryCatch({
      encoded <- refresh$Runtime$evaluate(
        "JSON.stringify({title:document.title,url:location.href,body:(document.querySelector('#main, main')?.innerText||'').slice(0,2400)})"
      )$result$value
      fromJSON(encoded, simplifyVector = TRUE)
    }, error = function(error) NULL)
    if (!is.null(state)) {
      challenge <- grepl(
        "just a moment|checking your browser|attention required|verifying you are human|cf-browser-verification",
        paste(state$title, state$body), ignore.case = TRUE
      )
      official_surface <- grepl("Shown Here:|Amendment Text:", state$body, fixed = FALSE)
      if (!challenge && identical(state$url, probe_url) && official_surface) {
        cleared <- TRUE
        break
      }
    }
    Sys.sleep(0.5)
  }
  if (!cleared) return(NULL)
  refreshed_ua <- tryCatch(
    refresh$Runtime$evaluate("navigator.userAgent")$result$value,
    error = function(error) NULL
  )
  refreshed_cookies <- tryCatch(
    refresh$Network$getAllCookies()$cookies,
    error = function(error) list()
  )
  kept <- Filter(function(cookie) grepl("congress\\.gov", cookie$domain), refreshed_cookies)
  refreshed_header <- paste(
    vapply(kept, function(cookie) paste0(cookie$name, "=", cookie$value), ""),
    collapse = "; "
  )
  refreshed_clearance <- any(vapply(kept, function(cookie) identical(cookie$name, "cf_clearance"), TRUE))
  if (!nzchar(refreshed_header) || !refreshed_clearance || is.null(refreshed_ua)) return(NULL)
  list(cookie = refreshed_header, ua = refreshed_ua)
}

lines <- readLines(records_file, warn = FALSE, encoding = "UTF-8")
if (args$limit > 0L) lines <- head(lines, args$limit)
records <- lapply(lines, fromJSON, simplifyVector = FALSE)
rm(lines)
if (!length(records)) stop("No discovery records selected")

text_url <- function(record) paste0(sub("/+$", "", record$canonical_url), "/text")
parts <- function(record) c(as.character(record$congress), record$type, as.character(record$number))
artifact_file <- function(record) do.call(file.path, c(list(accepted_dir), as.list(parts(record)), list("page.html.gz")))
receipt_file <- function(record) do.call(file.path, c(list(receipts_dir), as.list(parts(record)), list("receipt.json")))

# Fast surface check. The typed marker sits behind a stable class anchor and a
# Cloudflare interstitial announces itself in the first 4 KB. Verified against
# 497 stored bodies (449 accepted + all 48 distinct quarantined): 497/497
# agreement with the full parse, zero false accepts. This can only SHORT-CIRCUIT
# AN ACCEPT -- anything it cannot positively confirm falls through to the full
# read_html path, so no page can be wrongly rejected by it.
CHALLENGE_PATTERN <- paste0(
  "just a moment|checking your browser|attention required|",
  "verifying you are human|cf-browser-verification")

cheap_surface_checks <- function(record_type, body) {
  head_chunk <- substr(body, 1L, 4096L)
  challenge <- grepl(CHALLENGE_PATTERN, head_chunk, ignore.case = TRUE, perl = TRUE)
  pattern <- if (record_type %in% c("hamdt", "samdt")) {
    "<h2[^>]*class=\"primary\"[^>]*>\\s*<span>\\s*Amendment Text:\\s*</span>\\s*\\S"
  } else {
    "<h3[^>]*class=\"currentVersion\"[^>]*>\\s*Shown Here:\\s*<br\\s*/?>\\s*<span>\\s*\\S"
  }
  list(not_challenge = !challenge,
       expected_main_marker = grepl(pattern, body, perl = TRUE))
}

validate_body <- function(record, response, body_raw) {
  body <- tryCatch(rawToChar(body_raw), error = function(error) "")
  content_type_early <- as.character(httr::headers(response)[["content-type"]])
  marker_early <- if (record$type %in% c("hamdt", "samdt")) "Amendment Text:" else "Shown Here:"
  transport_checks <- list(
    status_200 = as.integer(status_code(response)) == 200L,
    exact_url = identical(response$url, text_url(record)),
    html_content_type = grepl("text/html|application/xhtml\\+xml", content_type_early,
                              ignore.case = TRUE))
  # html_text2 over #main is ~79% of all parse time on these 350KB-6MB pages;
  # skip it entirely whenever the cheap check already proves the surface.
  fast <- cheap_surface_checks(record$type, body)
  if (isTRUE(fast$not_challenge) && isTRUE(fast$expected_main_marker)) {
    checks <- c(transport_checks,
                list(not_challenge = TRUE, expected_main_marker = TRUE))
    return(list(accepted = all(unlist(checks, use.names = FALSE)), checks = checks,
                marker = marker_early, content_type = content_type_early,
                validation_path = "fast"))
  }
  document <- tryCatch(read_html(body), error = function(error) NULL)
  title <- if (is.null(document)) "" else html_text2(html_element(document, "title"))
  surfaces <- if (is.null(document)) character() else html_text2(html_elements(document, "#main, main"))
  prefix <- paste0(title, " ", paste(substr(surfaces, 1L, 1200L), collapse = " "))
  challenge <- grepl(
    "just a moment|checking your browser|attention required|verifying you are human|cf-browser-verification",
    prefix, ignore.case = TRUE
  )
  marker <- if (record$type %in% c("hamdt", "samdt")) "Amendment Text:" else "Shown Here:"
  marker_pattern <- paste0("(?m)(^|\\n)\\s*", marker, "(?=\\s|$)")
  matching <- surfaces[vapply(surfaces, function(value) grepl(marker_pattern, value, perl = TRUE), TRUE)]
  nonempty <- length(matching) > 0L && any(vapply(matching, function(value) {
    found <- regexpr(marker_pattern, value, perl = TRUE)
    found[[1]] >= 0L && nzchar(trimws(substr(value, found[[1]] + attr(found, "match.length"), nchar(value))))
  }, TRUE))
  checks <- c(transport_checks,
              list(not_challenge = !challenge, expected_main_marker = nonempty))
  list(accepted = all(unlist(checks, use.names = FALSE)), checks = checks,
       marker = marker, content_type = content_type_early,
       validation_path = "full")
}

fetch_once <- function(record, cf, out_dir, accepted_dir, receipts_dir, quarantine_dir, request_context) {
  expected_url <- paste0(sub("/+$", "", record$canonical_url), "/text")
  attempt_ordinal <- as.integer(request_context$attempt_ordinal)
  request_id <- digest(
    charToRaw(paste(
      request_context$invocation_id,
      request_context$batch_id,
      record$canonical_key,
      expected_url,
      attempt_ordinal,
      sep = "\n"
    )),
    algo = "sha256",
    serialize = FALSE
  )
  local_parts <- c(as.character(record$congress), record$type, as.character(record$number))
  html_file <- do.call(file.path, c(list(accepted_dir), as.list(local_parts), list("page.html.gz")))
  receipt_path <- do.call(file.path, c(list(receipts_dir), as.list(local_parts), list("receipt.json")))

  public_headers <- c(
    "User-Agent" = cf$ua,
    "Cookie" = cf$cookie,
    "Accept" = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language" = "en-US,en;q=0.9",
    "Referer" = "https://www.congress.gov/"
  )
  response <- tryCatch(
    GET(expected_url, add_headers(.headers = public_headers), timeout(25)),
    error = function(error) error
  )
  if (inherits(response, "error")) {
    failure <- list(
      schema = "congress-httr-failure-attempt-v2",
      created_at = format(Sys.time(), tz = "UTC", usetz = TRUE),
      invocation_id = request_context$invocation_id,
      batch_id = request_context$batch_id,
      attempt_ordinal = attempt_ordinal,
      request_id = request_id,
      canonical_key = record$canonical_key,
      expected_url = expected_url,
      failure_class = "transport_error",
      response_body_available = FALSE,
      transport = "native_chrome_cookie_replay_httr",
      retry_waits = 0L,
      cookie_values_persisted = FALSE
    )
    safe_key <- gsub("[^A-Za-z0-9._-]+", "-", record$canonical_key)
    attempt_sha256 <- digest(charToRaw(json_text(failure)), algo = "sha256", serialize = FALSE)
    failure_file <- file.path(
      out_dir, "failure_attempts", "transport", safe_key,
      paste0(attempt_sha256, ".json")
    )
    write_immutable_json(failure_file, failure)
    return(list(
      status = "failed", canonical_key = record$canonical_key,
      expected_url = expected_url, reason = "transport_error",
      artifact = failure_file, terminal_receipt = failure_file,
      attempt_receipts = list(failure_file), request_id = request_id,
      attempt_ordinal = attempt_ordinal
    ))
  }
  body_raw <- content(response, as = "raw")
  checked <- validate_body(record, response, body_raw)
  body_sha256 <- digest(body_raw, algo = "sha256", serialize = FALSE)
  if (!checked$accepted) {
    quarantine_file <- file.path(quarantine_dir, body_sha256, "page.html.gz")
    write_immutable_gzip(quarantine_file, body_raw)
    failure <- list(
      schema = "congress-httr-failure-v2",
      created_at = format(Sys.time(), tz = "UTC", usetz = TRUE),
      invocation_id = request_context$invocation_id,
      batch_id = request_context$batch_id,
      attempt_ordinal = attempt_ordinal,
      request_id = request_id,
      canonical_key = record$canonical_key,
      expected_url = expected_url,
      final_url = response$url,
      http_status = status_code(response),
      body_sha256 = body_sha256,
      byte_length = length(body_raw),
      validation = checked$checks,
      quarantine_file = quarantine_file,
      transport = "native_chrome_cookie_replay_httr",
      cookie_values_persisted = FALSE
    )
    safe_key <- gsub("[^A-Za-z0-9._-]+", "-", record$canonical_key)
    failure$failure_class <- "surface_rejected"
    failure$retry_waits <- 0L
    attempt_sha256 <- digest(charToRaw(json_text(failure)), algo = "sha256", serialize = FALSE)
    failure_file <- file.path(
      out_dir, "failure_attempts", "surface", safe_key,
      paste0(attempt_sha256, ".json")
    )
    write_immutable_json(failure_file, failure)
    return(list(
      status = "failed",
      canonical_key = record$canonical_key,
      expected_url = expected_url,
      reason = "surface_rejected",
      artifact = failure_file,
      terminal_receipt = failure_file,
      attempt_receipts = list(failure_file),
      request_id = request_id,
      attempt_ordinal = attempt_ordinal,
      checks = checked$checks,
      challenge = !isTRUE(checked$checks$not_challenge) || status_code(response) %in% c(403L, 429L)
    ))
  }
  write_immutable_gzip(html_file, body_raw)
  receipt <- list(
    schema = "congress-httr-cookie-replay-receipt-v2",
    created_at = format(Sys.time(), tz = "UTC", usetz = TRUE),
    invocation_id = request_context$invocation_id,
    batch_id = request_context$batch_id,
    attempt_ordinal = attempt_ordinal,
    request_id = request_id,
    status = "accepted",
    canonical_key = record$canonical_key,
    expected_url = expected_url,
    final_url = response$url,
    http_status = status_code(response),
    content_type = checked$content_type,
    expected_marker = checked$marker,
    body_sha256 = body_sha256,
    byte_length = length(body_raw),
    validation = checked$checks,
    html_file = html_file,
    transport = "native_chrome_cookie_replay_httr",
    http_attempts = 1L,
    retry_waits = 0L,
    cf_clearance_present = TRUE,
    cookie_values_persisted = FALSE
  )
  write_immutable_json(receipt_path, receipt)
  list(
    status = "accepted", canonical_key = record$canonical_key,
    expected_url = expected_url, artifact = html_file,
    terminal_receipt = receipt_path, body_sha256 = body_sha256,
    byte_length = length(body_raw), request_id = request_id,
    attempt_ordinal = attempt_ordinal
  )
}

resumable_record <- function(record) {
  expected_url <- text_url(record)
  receipt_path <- receipt_file(record)
  html_file <- artifact_file(record)
  if (!file.exists(receipt_path) || !file.exists(html_file)) return(FALSE)
  prior <- tryCatch(
    fromJSON(receipt_path, simplifyVector = FALSE),
    error = function(error) NULL
  )
  if (is.null(prior) || !identical(prior$status, "accepted") ||
      !identical(prior$expected_url, expected_url) ||
      !identical(prior$html_file, html_file)) return(FALSE)
  archived_connection <- gzfile(html_file, "rb")
  archived <- tryCatch(
    readBin(archived_connection, what = "raw", n = 1024L * 1024L * 256L),
    error = function(error) raw()
  )
  close(archived_connection)
  identical(digest(archived, algo = "sha256", serialize = FALSE), prior$body_sha256) &&
    identical(as.numeric(length(archived)), as.numeric(prior$byte_length))
}

cf <- list(cookie = cookie_header, ua = ua)
preflight_dir <- file.path(out_dir, "preflight", invocation_id)
preflight_accepted_dir <- file.path(preflight_dir, "accepted_html")
preflight_receipts_dir <- file.path(preflight_dir, "receipts")
preflight_quarantine_dir <- file.path(preflight_dir, "quarantine")
for (directory in c(preflight_dir, preflight_accepted_dir, preflight_receipts_dir, preflight_quarantine_dir)) {
  dir.create(directory, recursive = TRUE, showWarnings = FALSE)
}
canaries <- list(
  list(congress = 117L, type = "hr", number = 3664L, canonical_key = "117:hr:3664", canonical_url = "https://www.congress.gov/bill/117th-congress/house-bill/3664"),
  list(congress = 116L, type = "hamdt", number = 3L, canonical_key = "116:hamdt:3", canonical_url = "https://www.congress.gov/amendment/116th-congress/house-amendment/3")
)
run_preflight <- function(attempt_ordinal) {
  lapply(
    canaries, fetch_once,
    cf = cf, out_dir = preflight_dir,
    accepted_dir = preflight_accepted_dir,
    receipts_dir = preflight_receipts_dir,
    quarantine_dir = preflight_quarantine_dir,
    request_context = list(
      invocation_id = invocation_id,
      batch_id = "preflight-canary",
      attempt_ordinal = attempt_ordinal
    )
  )
}
canary_results <- run_preflight(1L)
if (!all(vapply(canary_results, function(item) identical(item$status, "accepted"), TRUE))) {
  failed_canary <- canary_results[[which(!vapply(
    canary_results,
    function(item) identical(item$status, "accepted"),
    TRUE
  ))[[1L]]]]
  refreshed <- refresh_cf_session(failed_canary$expected_url)
  if (!is.null(refreshed)) {
    cf <- refreshed
    canary_results <- run_preflight(2L)
  }
}
write_checked_json(file.path(preflight_dir, "manifest.json"), list(
  schema = "congress-httr-preflight-manifest-v1",
  invocation_id = invocation_id,
  completed_at = format(Sys.time(), tz = "UTC", usetz = TRUE),
  statuses = vapply(canary_results, function(item) item$status, ""),
  request_ids = vapply(canary_results, function(item) item$request_id, ""),
  passed = all(vapply(canary_results, function(item) identical(item$status, "accepted"), TRUE)),
  retry_waits = 0L,
  cookie_values_persisted = FALSE
))
if (!all(vapply(canary_results, function(item) identical(item$status, "accepted"), TRUE))) {
  stop("Native Chrome cookie-replay httr bill+amendment canary failed after one headful clearance refresh")
}

total_records <- length(records)
resume_mask <- if (isTRUE(args$resume)) {
  vapply(records, resumable_record, TRUE)
} else {
  rep(FALSE, total_records)
}
preserved_resumed <- sum(resume_mask)
if (preserved_resumed > 0L) records <- records[!resume_mask]
rm(resume_mask)

plan(multisession, workers = args$workers)
on.exit(plan(sequential), add = TRUE)
started <- invocation_started
completed <- preserved_resumed
processed_this_run <- 0L
accepted <- 0L
resumed <- preserved_resumed
failed <- 0L
attempt_failures_observed <- 0L
recovered_failure_count <- 0L
event_files <- list.files(events_dir, pattern = "^[0-9]{8}-.*[.]json$", full.names = FALSE)
event_prefixes <- suppressWarnings(as.integer(sub("^([0-9]{8}).*$", "\\1", event_files)))
event_sequence <- if (length(event_prefixes) && any(!is.na(event_prefixes))) max(event_prefixes, na.rm = TRUE) else 0L
rm(event_files, event_prefixes)

# This collector deliberately does NOT publish to the shared Glass Box stream.
# When it is launched on a filtered remaining-work queue its `total` is the
# outstanding count rather than the 18,557-record corpus, so publishing made the
# dashboard alternate between true coverage and a much smaller percentage for
# what looks like one job. The supervisor
# (fec\supervise_fulltext_clearance_glassbox.ps1) is the single owner of
# shared-stream progress and always reports collected/18557. It also asserts
# that invariant every 30s and raises a denominator-conflict alarm if any other
# emitter reintroduces a competing total.
#
# progress.json below remains the per-run record and is what the supervisor
# reads; it is intentionally scoped to this invocation's queue.

write_progress <- function(status, detail) {
  elapsed <- max(0.001, as.numeric(difftime(Sys.time(), started, units = "secs")))
  accepted_evidence <- accepted + resumed
  rate <- if (accepted > 0L) accepted / (elapsed / 60) else 0
  eta <- if (rate > 0) max(0, (total_records - accepted_evidence) / rate * 60) else NULL
  progress_payload <- list(
    schema_version = 1L,
    updated_at = format(Sys.time(), tz = "UTC", usetz = TRUE),
    lane = "codex",
    source = "congress-full-text-httr",
    run_id = "legislation-full-text-r-20260726",
    kind = "collector.progress",
    status = status,
    stage = "native-chrome-cookie-replay-httr",
    current = accepted_evidence,
    total = total_records,
    summary = paste0(accepted_evidence, "/", total_records, " accepted official pages | ", failed, " unresolved"),
    detail = detail,
    workers = args$workers,
    active_workers = if (identical(status, "running")) min(args$workers, total_records - accepted_evidence) else 0L,
    collected_records = accepted,
    resumed_records = resumed,
    failure_count = failed,
    attempt_failure_count = attempt_failures_observed,
    recovered_failure_count = recovered_failure_count,
    records_per_minute = round(rate, 3),
    rate = round(rate, 3),
    eta_seconds = if (is.null(eta)) NULL else round(eta, 1),
    command = command_line,
    output_directory = out_dir,
    transport = "native_chrome_cookie_replay_httr",
    retry_waits = 0L,
    candidate_deletion_allowed = FALSE,
    failure_catchnet_directory = failure_attempts_dir,
    batch_accounting_directory = batches_dir,
    invocation_id = invocation_id,
    failures_silently_skipped = FALSE,
    cookie_values_persisted = FALSE
  )
  write_checked_json(file.path(out_dir, "progress.json"), progress_payload)
  # Deliberately NOT mirrored to the shared Glass Box stream. When the collector
  # is launched on a filtered remaining-work queue its total is the outstanding
  # count, not the 18,557-record corpus, so publishing it made the dashboard
  # flip between true coverage and a much smaller filtered percentage. The
  # supervisor owns the shared-stream progress and reports banked/18557.
}

value_or_null <- function(value) if (is.null(value) || !length(value)) NULL else value

failure_artifacts <- function(item) {
  unique(as.character(unlist(c(
    item$recovered_failure_artifacts,
    item$attempt_receipts
  ), use.names = FALSE)))
}

attach_recovery <- function(prior, replayed) {
  replayed$recovered_failure_artifacts <- unique(c(
    failure_artifacts(prior),
    failure_artifacts(replayed)
  ))
  replayed
}

write_event <- function(item) {
  event_sequence <<- event_sequence + 1L
  safe_key <- gsub("[^A-Za-z0-9._-]+", "-", item$canonical_key)
  recovered_artifacts <- failure_artifacts(item)
  recovered_count <- length(recovered_artifacts)
  recovered <- !identical(item$status, "failed") && recovered_count > 0L
  event <- list(
    schema_version = 1L,
    timestamp_utc = format(Sys.time(), tz = "UTC", usetz = TRUE),
    lane = "codex",
    source = "congress-full-text-httr",
    run_id = "legislation-full-text-r-20260726",
    kind = if (identical(item$status, "failed")) "collector.record.failed" else if (recovered) "collector.record.recovered" else "collector.record.completed",
    status = if (identical(item$status, "failed")) "failed" else "completed",
    stage = "native-chrome-cookie-replay-httr",
    summary = paste0(completed, "/", total_records, " ", item$canonical_key, " | ", item$status),
    detail = if (identical(item$status, "failed")) item$reason else if (recovered) paste0("Recovered after ", recovered_count, " captured failure attempt(s); final surface passed every acceptance guard.") else "Exact URL, HTML, challenge, main-surface, and typed marker guards passed.",
    command = paste0("GET ", item$expected_url),
    input = list(
      invocation_id = invocation_id,
      canonical_key = item$canonical_key,
      request_id = value_or_null(item$request_id),
      attempt_ordinal = value_or_null(item$attempt_ordinal)
    ),
    output = list(body_sha256 = value_or_null(item$body_sha256), byte_length = value_or_null(item$byte_length), transport = "native_chrome_cookie_replay_httr", immediate_replays = recovered_count, retry_waits = 0L, recovered_failure_artifacts = recovered_artifacts),
    artifact = value_or_null(item$artifact),
    current = completed,
    total = total_records
  )
  write_immutable_json(file.path(events_dir, sprintf("%08d-%s-%s.json", event_sequence, substr(invocation_id, 1L, 12L), safe_key)), event)
}

write_batch_event <- function(kind, batch_id, batch_keys, status, detail, outcomes = NULL) {
  event_sequence <<- event_sequence + 1L
  event <- list(
    schema_version = 1L,
    timestamp_utc = format(Sys.time(), tz = "UTC", usetz = TRUE),
    lane = "codex",
    source = "congress-full-text-httr",
    run_id = "legislation-full-text-r-20260726",
    kind = kind,
    status = status,
    stage = "native-chrome-cookie-replay-httr",
    summary = paste0("batch ", substr(batch_id, 1L, 12L), " | ", length(batch_keys), " dispatched keys | ", status),
    detail = detail,
    command = "six-worker native Chrome cookie-replay httr batch",
    input = list(
      invocation_id = invocation_id,
      batch_id = batch_id,
      canonical_keys = batch_keys,
      retry_waits = 0L
    ),
    output = outcomes,
    current = completed,
    total = total_records
  )
  write_immutable_json(
    file.path(events_dir, sprintf("%08d-%s-batch-%s.json", event_sequence, substr(invocation_id, 1L, 12L), substr(batch_id, 1L, 12L))),
    event
  )
}

write_progress("running", "Native Chrome clearance verified; six one-shot httr workers are collecting official HTML.")
indexes <- seq_along(records)
blocked <- FALSE
# A block is a run of FULLY failed batches, not one bad record.
consecutive_dead_batches <- 0L
dead_batch_limit <- 3L
if (length(indexes) > 0L) for (start in seq(1L, length(indexes), by = args$workers)) {
  if (start > 1L && args$delay > 0) Sys.sleep(args$delay)
  selected <- indexes[start:min(length(indexes), start + args$workers - 1L)]
  batch_records <- records[selected]
  batch_keys <- vapply(batch_records, function(record) record$canonical_key, "")
  batch_urls <- vapply(batch_records, text_url, "")
  batch_id <- digest(
    charToRaw(paste(c(invocation_id, batch_keys), collapse = "\n")),
    algo = "sha256",
    serialize = FALSE
  )
  batch_dir <- file.path(batches_dir, batch_id)
  dispatch_path <- file.path(batch_dir, "dispatch.json")
  completion_path <- file.path(batch_dir, "completion.json")
  write_immutable_json(dispatch_path, list(
    schema = "congress-httr-batch-dispatch-v2",
    dispatched_at = format(Sys.time(), tz = "UTC", usetz = TRUE),
    invocation_id = invocation_id,
    batch_id = batch_id,
    canonical_keys = as.list(batch_keys),
    expected_urls = as.list(batch_urls),
    worker_count = length(batch_keys),
    retry_waits = 0L,
    candidate_deletion_allowed = FALSE
  ))
  dispatch_sha256 <- digest(file = dispatch_path, algo = "sha256", serialize = FALSE)
  write_batch_event(
    "collector.batch.dispatched",
    batch_id,
    batch_keys,
    "running",
    "Immutable dispatch receipt published before any worker request."
  )
  results <- future_lapply(
    batch_records, fetch_once,
    cf = cf, out_dir = out_dir, accepted_dir = accepted_dir,
    receipts_dir = receipts_dir, quarantine_dir = quarantine_dir,
    request_context = list(
      invocation_id = invocation_id,
      batch_id = batch_id,
      attempt_ordinal = 1L
    ),
    future.packages = c("digest", "httr", "jsonlite", "rvest"),
    future.seed = TRUE
  )
  transport_positions <- which(vapply(
    results,
    function(item) identical(item$status, "failed") && identical(item$reason, "transport_error"),
    TRUE
  ))
  if (length(transport_positions) > 0L) {
    replayed <- future_lapply(
      records[selected[transport_positions]], fetch_once,
      cf = cf, out_dir = out_dir, accepted_dir = accepted_dir,
      receipts_dir = receipts_dir, quarantine_dir = quarantine_dir,
      request_context = list(
        invocation_id = invocation_id,
        batch_id = batch_id,
        attempt_ordinal = 2L
      ),
      future.packages = c("digest", "httr", "jsonlite", "rvest"),
      future.seed = TRUE
    )
    for (position_index in seq_along(transport_positions)) {
      position <- transport_positions[[position_index]]
      results[[position]] <- attach_recovery(
        results[[position]], replayed[[position_index]]
      )
    }
  }
  replay_positions <- which(vapply(
    results,
    function(item) identical(item$status, "failed") && isTRUE(item$challenge) && item$attempt_ordinal < 2L,
    TRUE
  ))
  if (length(replay_positions) > 0L) {
    refreshed <- refresh_cf_session(results[[replay_positions[[1L]]]]$expected_url)
    if (!is.null(refreshed)) {
      cf <- refreshed
      replayed <- future_lapply(
        records[selected[replay_positions]], fetch_once,
        cf = cf, out_dir = out_dir, accepted_dir = accepted_dir,
        receipts_dir = receipts_dir, quarantine_dir = quarantine_dir,
        request_context = list(
          invocation_id = invocation_id,
          batch_id = batch_id,
          attempt_ordinal = 2L
        ),
        future.packages = c("digest", "httr", "jsonlite", "rvest"),
        future.seed = TRUE
      )
      for (position_index in seq_along(replay_positions)) {
        position <- replay_positions[[position_index]]
        results[[position]] <- attach_recovery(
          results[[position]], replayed[[position_index]]
        )
      }
    }
  }
  for (item in results) {
    completed <- completed + 1L
    processed_this_run <- processed_this_run + 1L
    item_failure_artifacts <- failure_artifacts(item)
    attempt_failures_observed <- attempt_failures_observed + length(item_failure_artifacts)
    if (!identical(item$status, "failed") && length(item_failure_artifacts) > 0L) {
      recovered_failure_count <- recovered_failure_count + 1L
    }
    if (identical(item$status, "accepted")) accepted <- accepted + 1L
    if (identical(item$status, "resumed")) resumed <- resumed + 1L
    if (identical(item$status, "failed")) failed <- failed + 1L
    write_event(item)
  }
  batch_outcomes <- lapply(results, function(item) list(
    canonical_key = item$canonical_key,
    expected_url = item$expected_url,
    status = item$status,
    request_id = value_or_null(item$request_id),
    attempt_ordinal = value_or_null(item$attempt_ordinal),
    terminal_receipt = value_or_null(item$terminal_receipt),
    terminal_artifact = value_or_null(item$artifact),
    body_sha256 = value_or_null(item$body_sha256),
    failure_attempt_artifacts = failure_artifacts(item),
    retry_waits = 0L
  ))
  write_immutable_json(completion_path, list(
    schema = "congress-httr-batch-completion-v2",
    completed_at = format(Sys.time(), tz = "UTC", usetz = TRUE),
    invocation_id = invocation_id,
    batch_id = batch_id,
    dispatch_receipt_sha256 = dispatch_sha256,
    dispatched_canonical_keys = as.list(batch_keys),
    dispatched_expected_urls = as.list(batch_urls),
    outcomes = batch_outcomes,
    outcome_count = length(batch_outcomes),
    every_dispatched_key_has_terminal_outcome = identical(
      sort(batch_keys),
      sort(vapply(batch_outcomes, function(outcome) outcome$canonical_key, ""))
    ),
    retry_waits = 0L,
    candidate_deletion_allowed = FALSE
  ))
  write_batch_event(
    "collector.batch.completed",
    batch_id,
    batch_keys,
    if (any(vapply(results, function(item) identical(item$status, "failed"), TRUE))) "failed" else "completed",
    "Every dispatched key returned an accepted/resumed artifact or an explicit failure artifact.",
    outcomes = batch_outcomes
  )
  unresolved_batch <- sum(vapply(results, function(item) identical(item$status, "failed"), TRUE))
  # A single unresolved record is NOT a reason to end the harvest. It already has
  # an explicit failure artifact and a quarantined body, so nothing is skipped
  # silently and the catchnet will replay it -- halting adds no safety, it just
  # prevents the run from ever finishing. Measured behaviour: Cloudflare blocks
  # are all-or-nothing per batch (144 clean / 8 fully-failed / 0 partial across
  # 152 batches), so a FULLY failed batch is the real block signal. Halting on
  # any single failure meant each relaunch collected ~4 records and stopped.
  if (unresolved_batch > 0L && unresolved_batch == length(results)) {
    consecutive_dead_batches <- consecutive_dead_batches + 1L
  } else {
    consecutive_dead_batches <- 0L
  }
  if (consecutive_dead_batches >= dead_batch_limit) {
    blocked <- TRUE
    write_progress(
      "blocked",
      paste0(consecutive_dead_batches, " consecutive fully-failed batch(es) after clearance refresh; ",
             "stopping without skipping any record. Every dispatched key retains an explicit failure artifact.")
    )
    break
  }
  if (unresolved_batch > 0L) {
    write_progress(
      "running",
      paste0(unresolved_batch, " record(s) unresolved this batch and quarantined for replay; continuing.")
    )
  } else {
    write_progress("running", "Last batch passed; any challenged response was replayed once after headful clearance refresh.")
  }
}

terminal_status <- if (blocked) "blocked" else if (failed > 0L) "needs-review" else "complete"
write_progress(
  terminal_status,
  if (blocked) "Harvest stopped after one permitted immediate replay/clearance refresh failed; no candidate was deleted or silently skipped." else if (failed > 0L) "Harvest ended with quarantined failures; no candidate was deleted or silently skipped." else "All selected official pages were accepted; no culling or production write was performed."
)
manifest <- list(
  schema = "congress-full-text-httr-harvest-manifest-v1",
  status = terminal_status,
  completed_at = format(Sys.time(), tz = "UTC", usetz = TRUE),
  records_file = records_file,
  selected_record_count = total_records,
  accepted_record_count = accepted,
  resumed_record_count = resumed,
  failed_record_count = failed,
  attempt_failure_count = attempt_failures_observed,
  recovered_failure_count = recovered_failure_count,
  completed_record_count = accepted + resumed,
  terminal_outcome_record_count = completed,
  unresolved_record_count = failed,
  remaining_record_count = total_records - accepted - resumed,
  workers = args$workers,
  transport = "native_chrome_cookie_replay_httr",
  browser_mode = "remote_headful_chrome",
  chrome_remote_host = args$host,
  chrome_remote_port = args$port,
  initial_http_attempts_per_url = 1L,
  maximum_http_attempts_per_url = 2L,
  failed_attempt_immediate_replay_limit = 1L,
  retry_waits = 0L,
  failure_catchnet_directory = failure_attempts_dir,
  batch_accounting_directory = batches_dir,
  invocation_id = invocation_id,
  failure_catchnet_receipt_count = length(list.files(
    failure_attempts_dir, pattern = "[.]json$", recursive = TRUE,
    full.names = FALSE
  )),
  historical_quarantine_failure_receipt_count = length(list.files(
    quarantine_dir, pattern = "^failure-.*[.]json$", recursive = TRUE,
    full.names = FALSE
  )),
  failures_silently_skipped = FALSE,
  cf_clearance_present = TRUE,
  cookie_values_persisted = FALSE,
  candidate_deletion_allowed = FALSE,
  destructive_filtering = FALSE
)
write_checked_json(file.path(out_dir, "manifest.json"), manifest)
cat(toJSON(manifest, auto_unbox = TRUE, pretty = TRUE), "\n")

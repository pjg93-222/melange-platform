# Private fixtures only. Parse upstream function definitions without sourcing its
# driver, and replace every HTTP primitive and clock before invoking a function.
args <- commandArgs(trailingOnly = TRUE)
stopifnot(length(args) == 2L)
source(args[[1]], local = globalenv())
.libPaths(c(file.path(dirname(args[[1]]), "..", "..", "outputs", "r-library"), .libPaths()))
definitions <- as.list(parse(file = args[[2]]))
functions <- c("fetch_html", "cdx_get", "wayback_snapshot_url", "fetch_member_fulltext",
               "fetch_html_js", "js_session")
definition <- function(name) {
  result <- Filter(function(expr) is.call(expr) && identical(expr[[1]], as.name("<-")) &&
                     identical(expr[[2]], as.name(name)), definitions)
  stopifnot(length(result) == 1L)
  result[[1]]
}

fixture <- function(budget, response) {
  response_environment <- new.env(parent = environment(response))
  response_environment$reply <- reply
  environment(response) <- response_environment
  env <- new.env(parent = globalenv())
  for (name in functions) eval(definition(name), envir = env)
  env$HAVE_CHROMOTE <- FALSE
  env$.js_session <- NULL
  env$HTTP_TIMEOUT <- 30
  env$FULLTEXT_DELAY <- 0.15
  env$POLITE_DELAY <- 0.5
  env$JS_MEMBER_BUDGET <- budget
  env$JS_SETTLE_SECS <- 2
  env$CKPT_EVERY <- 25L
  env$CHECKPOINT_DIR <- tempfile("press-deadline-fixture-")
  dir.create(file.path(env$CHECKPOINT_DIR, "fulltext"), recursive = TRUE)
  env$CHROME_UA <- "fixture agent"
  env$bind_rows <- dplyr::bind_rows
  env$user_agent <- identity
  env$add_headers <- function(...) list(...)
  env$timeout <- function(seconds) list(timeout = seconds)
  env$status_code <- function(result) result$status
  env$content <- function(result, ...) result$text
  env$read_html <- identity
  env$extract_release <- function(html, url) data.frame(
    url = url, title_detail = html, date_detail = as.Date("2026-09-12"),
    body = html, stringsAsFactors = FALSE)
  env$stage_fulltext <- function(...) {
    message("Stage 4: 1 JS member(s) via chromote (sequential, main process; 600s/member cap)...")
    "stage-result"
  }
  state <- new.env(parent = emptyenv())
  state$now <- 0
  state$calls <- list()
  state$reports <- list()
  env$press_member_network_result <- function(result) {
    state$reports[[length(state$reports) + 1L]] <- result
  }
  env$GET <- function(url, ...) {
    options <- list(...)
    timeouts <- unlist(lapply(options, function(option) if (is.list(option)) option$timeout else NULL))
    stopifnot(length(timeouts) == 1L, is.finite(timeouts), timeouts > 0)
    state$calls[[length(state$calls) + 1L]] <- list(url = url, start = state$now, timeout = timeouts)
    result <- response(url, length(state$calls))
    state$now <- state$now + min(result$duration, timeouts)
    if (result$duration > timeouts) stop("fixture HTTP timeout")
    list(status = result$status, text = result$text)
  }
  install_press_request_deadlines(env, clock = function() state$now,
                                  sleep = function(seconds) state$now <- state$now + seconds)
  list(env = env, state = state)
}
reply <- function(duration = 1, status = 200L, text = paste(rep(
  "Complete official fixture article body evidence for collection.", 12L), collapse = " "))
  list(duration = duration, status = status, text = text)
urls <- c("https://member.house.gov/one", "https://member.house.gov/two")

# Static retries use 6.8 seconds; the first CDX timeout gets only the remaining
# 2.7 seconds after its polite delay. Neither another retry nor URL may start.
one <- fixture(10, function(url, index) {
  if (grepl("/cdx/", url, fixed = TRUE)) reply(60, 500L) else reply(1, 404L)
})
result <- one$env$fetch_member_fulltext("X000001", urls, js = TRUE)
stopifnot(abs(one$state$now - 10) < 0.002, length(one$state$calls) == 3L,
          grepl("/cdx/", one$state$calls[[3]]$url, fixed = TRUE),
          one$state$calls[[3]]$timeout <= 2.701,
          identical(one$state$reports[[1]]$attempted_urls, urls[[1]]),
          setequal(one$state$reports[[1]]$budget_deferred_urls, urls))
for (call in one$state$calls) stopifnot(call$start + call$timeout <= 10.001)

# A successful CDX lookup does not reset the budget for the snapshot body.
two <- fixture(15, function(url, index) {
  if (grepl("/cdx/", url, fixed = TRUE)) return(reply(2, 200L, "20260912000000"))
  if (grepl("/web/", url, fixed = TRUE)) return(reply(30, 200L))
  reply(1, 404L)
})
two$env$fetch_member_fulltext("X000002", urls[[1]], js = TRUE)
stopifnot(abs(two$state$now - 15) < 0.002, length(two$state$calls) == 4L,
          grepl("/web/", two$state$calls[[4]]$url, fixed = TRUE),
          two$state$calls[[4]]$timeout <= 5.201,
          identical(two$state$reports[[1]]$budget_deferred_urls, urls[[1]]))

# Retain cached and newly completed bodies, then resume only the unfinished URL
# on the next invocation with a fresh deadline. No URL becomes terminal merely
# because time ran out; a successful response at the deadline remains usable.
pending <- "https://member.house.gov/pending"
three <- fixture(2, function(url, index) {
  if (url == pending && index < 3) reply(30) else reply(0.25)
})
cached <- data.frame(url = urls[[1]], title_detail = "cached title",
                     date_detail = as.Date("2026-09-01"), body = "immutable cached body")
checkpoint <- file.path(three$env$CHECKPOINT_DIR, "fulltext", "X000003.rds")
saveRDS(cached, checkpoint)
first <- three$env$fetch_member_fulltext("X000003", c(urls, pending), js = TRUE)
stopifnot(identical(first[first$url == urls[[1]], ], cached), urls[[2]] %in% first$url,
          identical(three$state$reports[[1]]$budget_deferred_urls, pending))
three$env$JS_MEMBER_BUDGET <- 10
second <- three$env$fetch_member_fulltext("X000003", c(urls, pending), js = TRUE)
stopifnot(setequal(second$url, c(urls, pending)),
          identical(second[second$url == urls[[1]], ], cached),
          identical(three$state$reports[[2]]$attempted_urls, pending),
          !three$state$reports[[2]]$budget_exhausted,
          length(three$state$reports[[2]]$budget_deferred_urls) == 0L,
          identical(readRDS(checkpoint), second))

# The configured member cap does not change the parallel/non-JS HTTP contract.
four <- fixture(0.1, function(url, index) reply(2))
four$env$fetch_member_fulltext("X000004", urls, js = FALSE)
stopifnot(length(four$state$calls) == 2L,
          all(vapply(four$state$calls, function(call) call$timeout == 30, logical(1))),
          four$state$now > 4, length(four$state$reports) == 0L)

# A sub-millisecond remainder must never turn curl's timeout into zero/unlimited.
five <- fixture(0.0005, function(url, index) stop("HTTP must not start"))
five$env$fetch_member_fulltext("X000005", urls, js = TRUE)
stopifnot(length(five$state$calls) == 0L,
          setequal(five$state$reports[[1]]$budget_deferred_urls, urls))

# An unrelated checkpoint error cannot leave a deadline active for later work.
broken <- fixture(1, function(url, index) reply(0.1))
broken$env$CHECKPOINT_DIR <- file.path(broken$env$CHECKPOINT_DIR, "missing-directory")
stopifnot(inherits(suppressWarnings(try(
  broken$env$fetch_member_fulltext("X000006", urls[[1]], js = TRUE), silent = TRUE)), "try-error"))
broken$env$fetch_html(urls[[2]])
stopifnot(broken$state$calls[[length(broken$state$calls)]]$timeout == 30)

# Known-date and unknown-date phases share the same member's run allowance.
# A second member still has its own full allowance.
phases <- fixture(5, function(url, index) reply(3))
phases$env$fetch_member_fulltext("X000010", urls[[1]], js = TRUE)
phases$env$fetch_member_fulltext("X000010", urls[[2]], js = TRUE)
stopifnot(abs(phases$state$now - 5) < 0.002, length(phases$state$calls) == 2L,
          phases$state$calls[[2]]$timeout <= 1.701,
          identical(phases$state$reports[[2]]$budget_deferred_urls, urls[[2]]))
phases$env$fetch_member_fulltext("X000011", urls[[2]], js = TRUE)
stopifnot(identical(phases$state$reports[[3]]$completed_urls, urls[[2]]),
          !phases$state$reports[[3]]$budget_exhausted,
          phases$state$calls[[3]]$timeout > 4)

# A third maintenance call cannot renew the allowance after both foreground
# phases succeed. A subsequent no-budget call must report no actual attempt.
maintenance <- fixture(3, function(url, index) reply(if (index <= 2L) 1 else 30))
maintenance$env$fetch_member_fulltext("X000014", urls[[1]], js = TRUE)
maintenance$env$fetch_member_fulltext("X000014", urls[[2]], js = TRUE)
maintenance_url <- "https://member.house.gov/revisit"
maintenance$env$fetch_member_fulltext("X000014", maintenance_url, js = TRUE)
stopifnot(abs(maintenance$state$now - 3) < 0.002,
          length(maintenance$state$calls) == 3L,
          maintenance$state$calls[[3]]$timeout <= 0.551,
          identical(maintenance$state$reports[[3]]$budget_deferred_urls, maintenance_url))
maintenance$env$fetch_member_fulltext("X000014", maintenance_url, js = TRUE)
stopifnot(length(maintenance$state$calls) == 3L,
          length(maintenance$state$reports[[4]]$attempted_urls) == 0L,
          identical(maintenance$state$reports[[4]]$budget_deferred_urls, maintenance_url))

# The optional strict predicate changes cache reuse and parsed JS acceptance,
# without deleting the prior evidence when its date needs a fresh fetch.
dated <- fixture(10, function(url, index) reply(0.1))
dated$env$press_cached_body_complete <- function(frame) {
  !is.na(frame$body) & nzchar(frame$body) & !is.na(frame$date_detail) &
    frame$date_detail >= as.Date("1900-01-01") & frame$date_detail <= as.Date("2026-09-13")
}
future_cache <- cached
future_cache$date_detail <- as.Date("2026-10-02")
date_checkpoint <- file.path(dated$env$CHECKPOINT_DIR, "fulltext", "X000012.rds")
saveRDS(future_cache, date_checkpoint)
refetched <- dated$env$fetch_member_fulltext("X000012", urls[[1]], js = TRUE)
stopifnot(length(dated$state$calls) == 1L, refetched$date_detail == as.Date("2026-09-12"),
          identical(dated$state$reports[[1]]$completed_urls, urls[[1]]))
parsed_date <- fixture(10, function(url, index) {
  if (grepl("/cdx/", url, fixed = TRUE)) return(reply(0.1, 200L, "20260912000000"))
  if (grepl("/web/", url, fixed = TRUE)) return(reply(0.1, 200L, "valid archived body"))
  reply(0.1, 200L, "future-dated shell")
})
parsed_date$env$press_cached_body_complete <- dated$env$press_cached_body_complete
parsed_date$env$extract_release <- function(html, url) data.frame(
  url = url, title_detail = html, body = html,
  date_detail = as.Date(if (html == "future-dated shell") "2026-10-02" else "2026-09-12"))
validated <- parsed_date$env$fetch_member_fulltext("X000013", urls[[1]], js = TRUE)
stopifnot(length(parsed_date$state$calls) == 3L, validated$body == "valid archived body",
          validated$date_detail == as.Date("2026-09-12"))

# Migrate pre-existing dated shells without deleting either those raw bytes or
# a longer good body. A failed real request retains both; a later successful
# request replaces only the deficient shell and can complete it.
short_mode <- "fail"
short_cache <- fixture(1000, function(url, index)
  if (short_mode == "fail") reply(0.1, 404L) else reply(0.1))
short_reporter <- short_cache$env$press_member_network_result
source(file.path(dirname(args[[1]]), "press_backlog.R"), local = short_cache$env)
short_cache$env$press_member_network_result <- short_reporter
short_cache$env$press_backlog_run_date <- function() as.Date("2026-09-13")
short_path <- file.path(short_cache$env$CHECKPOINT_DIR, "fulltext", "X000014.rds")
prior <- data.frame(url = urls, title_detail = c("retained good title", "short cached shell"),
  body = c(reply()$text, "Read more."), date_detail = as.Date("2026-09-12"))
saveRDS(prior, short_path)
invisible(short_cache$env$fetch_member_fulltext("X000014", urls, js = TRUE))
stopifnot(length(short_cache$state$calls) > 0L,
  !any(vapply(short_cache$state$calls, function(call) identical(call$url, urls[[1L]]), logical(1L))),
  identical(readRDS(short_path), prior),
  identical(short_cache$env$press_cached_body_complete(prior), c(TRUE, FALSE)))
short_mode <- "success"
before_gets <- length(short_cache$state$calls)
migrated <- short_cache$env$fetch_member_fulltext("X000014", urls, js = TRUE)
stopifnot(length(short_cache$state$calls) == before_gets + 1L,
  identical(migrated[migrated$url == urls[[1L]], names(prior), drop = FALSE], prior[1L, , drop = FALSE]),
  all(short_cache$env$press_cached_body_complete(migrated)))

# Keep installation idempotent and ensure the disabled browser label is honest.
wrapped <- four$env$fetch_member_fulltext
install_press_request_deadlines(four$env)
stopifnot(identical(wrapped, four$env$fetch_member_fulltext))
banner <- capture.output(value <- four$env$stage_fulltext(), type = "message")
stopifnot(identical(value, "stage-result"),
          any(grepl("HTTP/Wayback (browser disabled)", banner, fixed = TRUE)),
          !any(grepl("via chromote", banner, fixed = TRUE)))
four$env$HAVE_CHROMOTE <- TRUE
stopifnot(inherits(try(install_press_request_deadlines(four$env), silent = TRUE), "try-error"))
four$env$HAVE_CHROMOTE <- FALSE

# Do not silently install an ineffective adapter against changed upstream calls.
six <- new.env(parent = four$env)
for (name in c("fetch_member_fulltext", "fetch_html", "cdx_get", "wayback_snapshot_url", "stage_fulltext"))
  assign(name, get(name, envir = four$env), envir = six)
eval(definition("fetch_member_fulltext"), envir = six)
eval(definition("fetch_html"), envir = six)
six$cdx_get <- function(url, ...) NULL
before <- six$fetch_member_fulltext
stopifnot(inherits(try(install_press_request_deadlines(six), silent = TRUE), "try-error"),
          identical(before, six$fetch_member_fulltext))

# Both adapters must still work in the production parallel-worker mechanism.
# All paths, HTTP replies and clocks remain private fixtures in these children.
parallel_env <- four$env
source(file.path(dirname(args[[1]]), "press_backlog.R"), local = parallel_env)
parallel_env$write_rds_atomic <- function(value, path) saveRDS(value, path)
parallel_env$install_press_backlog(root = parallel_env$CHECKPOINT_DIR, envir = parallel_env)
parallel_env$PRESS_BACKLOG_CONTEXT <- list(
  index = data.frame(bioguide = c("X000007", "X000008"), url = urls,
                     date = as.Date(c("2026-09-12", "2026-09-12"))),
  run_key = "fixture-run", source_key = "fixture-source")
future::plan(future::multisession, workers = 2L)
parallel_result <- tryCatch(future.apply::future_lapply(seq_along(urls), function(index) {
  frame <- parallel_env$fetch_member_fulltext(c("X000007", "X000008")[[index]], urls[[index]], js = FALSE)
  if (!nrow(frame)) {
    tryCatch(parallel_env$GET(urls[[index]], parallel_env$timeout(30)),
             error = function(error) stop("fixture transport failed: ", conditionMessage(error)))
    stop("empty worker result despite available fixture transport")
  }
  frame
}, future.seed = NULL, future.packages = "dplyr"),
finally = future::plan(future::sequential))
stopifnot(length(parallel_result) == 2L,
          identical(vapply(parallel_result, function(frame) frame$url[[1]], character(1)), urls))
for (bg in c("X000007", "X000008")) {
  rows <- parallel_env$press_backlog_read(bg)
  stopifnot(nrow(rows) == 1L, rows$outcome == "completed", rows$attempts == 1L)
}
cat("PASS: shared monotonic deadline, static/CDX/snapshot chain, checkpoint resume, non-JS preservation, labels and compatibility guard\n")

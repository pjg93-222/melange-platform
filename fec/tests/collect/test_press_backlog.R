# Isolated attempt-ledger and stage-order fixtures. No network or live paths.
args <- commandArgs(trailingOnly = TRUE)
stopifnot(length(args) == 1L)
.libPaths(c(file.path(dirname(args[[1]]), "..", "..", "outputs", "r-library"), .libPaths()))
env <- new.env(parent = globalenv())
source(args[[1]], local = env)
root <- tempfile("press-backlog-fixture-")
dir.create(root)
env$run_root <- file.path(root, "day-one")
env$CHECKPOINT_DIR <- file.path(env$run_root, "pr_checkpoints")
dir.create(file.path(env$CHECKPOINT_DIR, "fulltext"), recursive = TRUE)
env$collector_sources_sha256 <- list(source = "fixture-v1")
env$DATE_FLOOR <- as.Date("2026-06-25")
env$write_rds_atomic <- function(value, path) saveRDS(value, path)
env$calls <- list()
env$fixture_body <- paste(rep("Retained complete official article body evidence.", 12L), collapse = " ")
env$limit <- 1L
env$fetch_member_fulltext <- eval(quote(function(bg, urls, force = FALSE, js = FALSE) {
  calls[[length(calls) + 1L]] <<- list(bg = bg, urls = urls)
  path <- file.path(CHECKPOINT_DIR, "fulltext", paste0(bg, ".rds"))
  prev <- if (file.exists(path)) readRDS(path) else data.frame(
    url = character(0), body = character(0), date_detail = as.Date(character(0)))
  pending <- setdiff(urls, prev$url)
  attempted <- head(pending, limit)
  completed <- data.frame(url = attempted, body = rep(fixture_body, length(attempted)),
    date_detail = rep(as.Date("2026-09-12"), length(attempted)))
  result <- dplyr::bind_rows(prev, completed)
  saveRDS(result, path)
  if (js) press_member_network_result(list(bioguide = bg, requested_urls = urls,
    attempted_urls = attempted, completed_urls = result$url,
    budget_deferred_urls = setdiff(pending, attempted), budget_exhausted = length(pending) > length(attempted)))
  result
}), env)
env$stage_fulltext <- eval(quote(function(idx, force = FALSE, js_bgs = character(0)) {
  outputs <- lapply(sort(unique(idx$bioguide)), function(bg)
    fetch_member_fulltext(bg, idx$url[idx$bioguide == bg], force, bg %in% js_bgs))
  dplyr::bind_rows(outputs)
}), env)
env$install_press_backlog(root, env)
wrapped <- env$fetch_member_fulltext
env$install_press_backlog(root, env)
stopifnot(identical(wrapped, env$fetch_member_fulltext))

# A later alphabetic non-JS member's current release must run before an earlier
# JS member's large undated backlog. A partial pass preserves both body caches.
idx <- data.frame(bioguide = c("X000001", "X000001", "X000002"),
  url = c("https://example.gov/undated-b", "https://example.gov/undated-a", "https://example.gov/current"),
  date = as.Date(c(NA, NA, "2026-09-12")))
env$stage_fulltext(idx, js_bgs = "X000001")
stopifnot(identical(vapply(env$calls, function(call) call$bg, character(1)), c("X000002", "X000001")),
          identical(env$calls[[2]]$urls, sort(idx$url[1:2])),
          identical(env$press_backlog_snapshot()$url, "https://example.gov/undated-b"))
retained <- readRDS(file.path(env$CHECKPOINT_DIR, "fulltext", "X000001.rds"))
env$calls <- list()
env$stage_fulltext(idx, js_bgs = "X000001")
stopifnot(nrow(env$press_backlog_snapshot()) == 0L,
          identical(tail(env$calls, 1)[[1]]$urls[[1]], "https://example.gov/undated-b"))
after <- readRDS(file.path(env$CHECKPOINT_DIR, "fulltext", "X000001.rds"))
stopifnot(identical(after[match(retained$url, after$url), ], retained), nrow(after) == 2L)

# A real failed attempt can be exhausted. A deadline-cut attempt and a URL
# never requested remain pending, with the untouched one first on the retry.
urls <- paste0("https://example.gov/", c("success", "failed", "interrupted", "untouched"))
env$PRESS_BACKLOG_CONTEXT$index <- data.frame(bioguide = "X000003", url = urls,
                                            date = as.Date(NA))
saveRDS(data.frame(url = urls[1], body = env$fixture_body, date_detail = as.Date("2026-09-12")),
        file.path(env$CHECKPOINT_DIR, "fulltext", "X000003.rds"))
env$press_member_network_result(list(bioguide = "X000003", requested_urls = urls,
  attempted_urls = urls[1:3], completed_urls = urls[1], budget_deferred_urls = urls[3:4]))
rows <- env$press_backlog_read("X000003")
stopifnot(identical(rows$outcome, c("completed", "exhausted", "pending", "pending")),
          identical(rows$attempts, c(1L, 1L, 1L, 0L)),
          setequal(env$press_backlog_snapshot()$url, urls[3:4]),
          identical(env$press_backlog_order("X000003", urls)[1], urls[4]),
          !urls[2] %in% env$press_backlog_order("X000003", urls),
          urls[2] %in% env$press_backlog_order("X000003", urls, force = TRUE))
env$PRESS_BACKLOG_CONTEXT$run_key <- "next-day"
stopifnot(!urls[2] %in% env$press_backlog_order("X000003", urls),
          setequal(env$press_backlog_snapshot()$url, urls[3:4]))
alias <- sub("https://", "https://www.", urls[2])
stopifnot(!alias %in% env$press_backlog_order("X000003", alias))
env$PRESS_BACKLOG_CONTEXT$index$title <- c("", "Changed official headline", "", "")
stopifnot(urls[2] %in% env$press_backlog_order("X000003", urls))
env$PRESS_BACKLOG_CONTEXT$index$title <- NULL
env$PRESS_BACKLOG_CONTEXT$index$date[2] <- as.Date("2026-09-12")
stopifnot(urls[2] %in% env$press_backlog_order("X000003", urls))
env$PRESS_BACKLOG_CONTEXT$index$date[2] <- as.Date(NA)
env$PRESS_BACKLOG_CONTEXT$run_key <- rows$run_key[1]
env$PRESS_BACKLOG_CONTEXT$source_key <- "repaired-source"
stopifnot(urls[2] %in% env$press_backlog_order("X000003", urls))

# Empty targeted resumes have an explicit zero-pending context and no calls.
env$calls <- list()
env$stage_fulltext(idx[FALSE, ], js_bgs = "X000001")
stopifnot(length(env$calls) == 0L, nrow(env$press_backlog_snapshot()) == 0L)

# Committee/news fetches also use the upstream member function. Preserve these
# supported pseudo-member callers rather than swallowing them as ledger errors.
for (bg in c("CMTE_hfsc_dem", "NEWS_rollcall")) {
  output <- env$fetch_member_fulltext(bg, paste0("https://example.gov/", bg))
  stopifnot(nrow(output) == 1L, output$url == paste0("https://example.gov/", bg),
            !file.exists(file.path(env$PRESS_BACKLOG_DIRECTORY, paste0(bg, ".rds"))))
}

# Cached body dates may settle pre-floor work, but only the same member and
# canonical URL supply evidence. Unknown date URLs stay unknown and retryable.
date_urls <- paste0("https://example.gov/", c("old", "recent", "unknown", "alias"))
dated_idx <- data.frame(bioguide = "X000004", url = date_urls, date = as.Date(NA))
cached <- data.frame(url = c(date_urls[1:2], paste0(date_urls[4], "/")),
  body = env$fixture_body, date_detail = as.Date(c("2026-06-01", "2026-09-12", "2026-06-01")))
cache_path <- file.path(env$CHECKPOINT_DIR, "fulltext", "X000004.rds")
saveRDS(cached, cache_path)
restored <- env$press_backlog_restore_dates(dated_idx)
stopifnot(identical(restored$index$date[1:2], cached$date_detail[1:2]),
          is.na(restored$index$date[3]), restored$index$date[4] == cached$date_detail[3],
          identical(restored$index$url[4], cached$url[3]), nrow(restored$evidence) == 3L)
env$calls <- list()
env$stage_fulltext(dated_idx, js_bgs = "X000004")
stopifnot(!date_urls[1] %in% unlist(lapply(env$calls, function(call) call$urls)),
          date_urls[2] %in% env$calls[[1]]$urls,
          !date_urls[4] %in% unlist(lapply(env$calls, function(call) call$urls)))
for (kind in c("identity", "conflict")) {
  invalid <- cached
  if (kind == "identity") invalid$bioguide <- "X000005"
  if (kind == "conflict") invalid <- rbind(invalid, data.frame(url = paste0(date_urls[1], "/"),
    body = env$fixture_body, date_detail = as.Date("2026-09-12")))
  saveRDS(invalid, cache_path)
  stopifnot(inherits(try(env$press_backlog_restore_dates(dated_idx), silent = TRUE), "try-error"))
}
invalid <- cached
invalid$date_detail[1] <- as.Date("2999-01-01")
saveRDS(invalid, cache_path)
stopifnot(is.na(env$press_backlog_restore_dates(dated_idx)$index$date[1]),
          !date_urls[1] %in% env$press_backlog_cached("X000004"))
stopifnot(identical(env$press_cached_body_complete(data.frame(body = env$fixture_body,
  date_detail = c("1899-12-31", "2999-01-01", "2026-02-30", "2026-09-12"))),
  c(FALSE, FALSE, FALSE, TRUE)))
stopifnot(identical(env$press_cached_body_complete(data.frame(
  body = c("", "short dated shell", paste(rep("a", 25L), collapse = " "),
           paste(rep(strrep("x", 30L), 10L), collapse = " "), env$fixture_body),
  date_detail = as.Date("2026-09-12"))), c(FALSE, FALSE, FALSE, FALSE, TRUE)))

# FIFO fairness across runs: a URL first seen on an earlier run is served before one first seen
# later, even when the later one has a newer publication date; a new row is stamped with its
# run's date.
day_one <- env$run_root
fifo_urls <- paste0("https://example.gov/fifo-", c("old-a", "old-b", "new"))
env$run_root <- file.path(root, "press_refresh_20260901")
env$PRESS_BACKLOG_CONTEXT$index <- data.frame(bioguide = "X000006", url = fifo_urls[1:2],
                                            date = as.Date(NA))
env$press_member_network_result(list(bioguide = "X000006", requested_urls = fifo_urls[1:2],
  attempted_urls = character(0), completed_urls = character(0), budget_deferred_urls = fifo_urls[1:2]))
env$run_root <- file.path(root, "press_refresh_20260908")
env$PRESS_BACKLOG_CONTEXT$index <- data.frame(bioguide = "X000006", url = fifo_urls,
                                            date = as.Date(c(NA, NA, "2026-09-07")))
env$press_member_network_result(list(bioguide = "X000006", requested_urls = fifo_urls[3],
  attempted_urls = character(0), completed_urls = character(0), budget_deferred_urls = fifo_urls[3]))
fifo <- env$press_backlog_read("X000006")
stopifnot(identical(fifo$first_seen_date[match(fifo_urls, fifo$url)],
                    c("2026-09-01", "2026-09-01", "2026-09-08")),
          identical(env$press_backlog_order("X000006", rev(fifo_urls)), fifo_urls))
# Three failed attempts send a URL behind every URL still owed its turn, however old.
fifo$attempts[match(fifo_urls[1], fifo$url)] <- 3L
saveRDS(list(schema = "press-body-attempts-v1", rows = fifo), env$press_backlog_ledger_path("X000006"))
stopifnot(identical(env$press_backlog_order("X000006", fifo_urls), fifo_urls[c(2, 3, 1)]))
env$run_root <- day_one

# A legacy ledger row has no recorded first sighting: it reads back as "" (kept as-is, never
# stamped or inferred), and a malformed first_seen_date is refused like any attempt date.
legacy_path <- env$press_backlog_ledger_path("X000007")
legacy_rows <- data.frame(url = paste0("https://example.gov/legacy-", c("a", "b")),
  attempts = c(1L, 2L), outcome = "pending", run_key = "old", source_key = "old",
  stringsAsFactors = FALSE)
saveRDS(list(schema = "press-body-attempts-v1", rows = legacy_rows), legacy_path)
legacy <- env$press_backlog_read("X000007")
stopifnot(identical(legacy$first_seen_date, c("", "")), identical(legacy$attempts, c(1L, 2L)))
legacy_rows$first_seen_date <- c("2026-09-01", "not-a-date")
saveRDS(list(schema = "press-body-attempts-v1", rows = legacy_rows), legacy_path)
stopifnot(inherits(try(env$press_backlog_read("X000007"), silent = TRUE), "try-error"))

# Legacy "" is the OLDEST sighting, not "today": a legacy row is attempted before a row first seen
# yesterday, a brand-new URL (no ledger row yet) sorts after both, and a row with three attempts
# stays last. First sighting outranks the newer publication date the brand-new URL carries, and
# recording a later attempt never stamps the legacy "".
env$run_root <- file.path(root, "press_refresh_20260910")
legacy_order_urls <- paste0("https://example.gov/legacy-order-", c("legacy", "yesterday", "exhausted", "new"))
saveRDS(list(schema = "press-body-attempts-v1", rows = data.frame(url = legacy_order_urls[1:3],
  attempts = c(1L, 0L, 3L), outcome = "pending", run_key = "old", source_key = "old",
  first_seen_date = c("", "2026-09-09", ""), stringsAsFactors = FALSE)),
  env$press_backlog_ledger_path("X000010"))
env$PRESS_BACKLOG_CONTEXT$index <- data.frame(bioguide = "X000010", url = legacy_order_urls,
  date = as.Date(c(NA, NA, NA, "2026-09-09")))
stopifnot(identical(env$press_backlog_order("X000010", rev(legacy_order_urls)),
                    legacy_order_urls[c(1, 2, 4, 3)]))
env$press_member_network_result(list(bioguide = "X000010", requested_urls = legacy_order_urls[1:2],
  attempted_urls = character(0), completed_urls = character(0), budget_deferred_urls = legacy_order_urls[1:2]))
stopifnot(identical(env$press_backlog_read("X000010")$first_seen_date[1:2], c("", "2026-09-09")),
          identical(env$press_backlog_order("X000010", rev(legacy_order_urls)),
                    legacy_order_urls[c(1, 2, 4, 3)]))
env$run_root <- day_one

# Aliases of one canonical URL merge into a single row that keeps the EARLIEST recorded first
# sighting (an unrecorded "" never beats a real date) and the sum of the actual attempts.
alias_urls <- paste0(c("https://example.gov/", "https://www.example.gov/"), rep(c("alias-a", "alias-b"), each = 2L))
alias_urls[c(2, 4)] <- paste0(alias_urls[c(2, 4)], "/")
saveRDS(list(schema = "press-body-attempts-v1", rows = data.frame(url = alias_urls,
  attempts = c(1L, 2L, 1L, 1L), outcome = "pending", run_key = "old", source_key = "old",
  first_seen_date = c("2026-09-05", "2026-09-01", "", "2026-09-03"), stringsAsFactors = FALSE)),
  env$press_backlog_ledger_path("X000009"))
merged <- env$press_backlog_read("X000009")
stopifnot(nrow(merged) == 2L, identical(merged$attempts, c(3L, 2L)),
          identical(merged$first_seen_date, c("2026-09-01", "2026-09-03")))

# Three attempts exhaust a URL even when the run that made the third one was budget-deferred or
# never reached it: a URL the budget keeps cutting short must not hold its member pending forever.
three <- "https://example.gov/three-attempts"
env$PRESS_BACKLOG_CONTEXT$index <- data.frame(bioguide = "X000008", url = three, date = as.Date(NA))
deferred <- list(bioguide = "X000008", requested_urls = three, attempted_urls = three,
                 completed_urls = character(0), budget_deferred_urls = three)
for (attempt in 1:2) env$press_member_network_result(deferred)
two <- env$press_backlog_read("X000008")
stopifnot(identical(two$attempts, 2L), identical(two$outcome, "pending"),
          identical(env$press_backlog_snapshot()$url, three))
env$press_member_network_result(deferred)
env$press_member_network_result(modifyList(deferred, list(attempted_urls = character(0))))
three_rows <- env$press_backlog_read("X000008")
stopifnot(identical(three_rows$attempts, 3L), identical(three_rows$outcome, "exhausted"),
          nrow(env$press_backlog_snapshot()) == 0L, !three %in% env$press_backlog_order("X000008", three))

# Corrupt ledgers cannot silently suppress an unattempted URL.
path <- env$press_backlog_ledger_path("X000003")
corrupt <- readRDS(path)
corrupt$rows$outcome[1] <- "invented-terminal-state"
saveRDS(corrupt, path)
stopifnot(inherits(try(env$press_backlog_read("X000003"), silent = TRUE), "try-error"))
cat("PASS: pending/exhausted distinction, fair persistent resume, current-date priority, retained caches, empty scope and corrupt-ledger refusal\n")

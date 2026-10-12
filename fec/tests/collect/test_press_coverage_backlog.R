# Private end-to-end fixture for official inventory versus historical CDX work.
args <- commandArgs(trailingOnly = TRUE)
stopifnot(length(args) == 2L)
.libPaths(c(file.path(dirname(args[[1]]), "..", "..", "outputs", "r-library"), .libPaths()))
env <- new.env(parent = globalenv())
source(args[[1]], local = env)
source(args[[2]], local = env)
root <- tempfile("press-coverage-backlog-")
dir.create(root)
env$run_root <- file.path(root, "press_refresh_20260913")
env$CHECKPOINT_DIR <- file.path(env$run_root, "pr_checkpoints")
dir.create(file.path(env$CHECKPOINT_DIR, "fulltext"), recursive = TRUE)
env$DATE_FLOOR <- as.Date("2026-06-25")
env$collector_sources_sha256 <- list(source = "coverage-fixture-v1")
env$write_rds_atomic <- function(value, path) saveRDS(value, path)
env$press_backlog_attempt_date <- function() as.Date("2026-09-13")
env$press_backlog_run_date <- function() as.Date("2026-09-13")
env$calls <- character(0)
env$fetch_member_fulltext <- eval(quote(function(bg, urls, force = FALSE, js = FALSE) {
  calls <<- c(calls, urls)
  path <- file.path(CHECKPOINT_DIR, "fulltext", paste0(bg, ".rds"))
  prior <- if (file.exists(path)) readRDS(path) else data.frame(
    url = character(0), body = character(0), date_detail = as.Date(character(0)))
  fresh <- data.frame(url = urls, body = paste(rep(
    "Official fixture body with enough words for retention.", 12L), collapse = " "),
    date_detail = ifelse(grepl("current", urls), as.Date("2026-09-10"), as.Date("2020-01-01")))
  fresh$date_detail <- as.Date(fresh$date_detail, origin = "1970-01-01")
  out <- dplyr::bind_rows(prior, fresh)
  out <- out[!duplicated(press_backlog_url_key(out$url), fromLast = TRUE), , drop = FALSE]
  saveRDS(out, path)
  if (js) press_member_network_result(list(bioguide = bg, requested_urls = urls,
    attempted_urls = urls, completed_urls = out$url[press_cached_body_complete(out)],
    budget_deferred_urls = character(0)))
  out
}), env)
env$stage_fulltext <- eval(quote(function(idx, force = FALSE, js_bgs = character(0)) {
  parts <- lapply(sort(unique(idx$bioguide)), function(bg) {
    rows <- idx[idx$bioguide == bg, , drop = FALSE]
    bodies <- fetch_member_fulltext(bg, rows$url, force, bg %in% js_bgs)
    joined <- merge(rows, bodies, by = "url", all.x = TRUE, sort = FALSE)
    joined$date_final <- as.Date(ifelse(is.na(joined$date_detail), joined$date, joined$date_detail),
                                 origin = "1970-01-01")
    joined[is.na(joined$date_final) | joined$date_final >= DATE_FLOOR, , drop = FALSE]
  })
  dplyr::bind_rows(parts)
}), env)

current <- "https://example.gov/press/current"
proof <- list(
  schema = "official-current-inventory-proof-v1", bioguide = "X000001",
  source_kind = "fixture", coverage_mode = "fully_paginated_after_floor",
  floor = "2026-06-25", current_count = 1L, current_urls = current,
  current_url_set_sha256 = env$press_coverage_json_hash(env$press_coverage_url_key(current)),
  requests = list(list(request_index = 1L, role = "inventory_page",
    endpoint = "https://example.gov/api?page=1", final_url = "https://example.gov/api?page=1",
    raw_response_sha256 = strrep("a", 64L), item_count = 1L, page = 1L)),
  completeness = list(pagination_complete = TRUE, date_order_verified = TRUE,
    identity_complete = TRUE, after_floor_query_verified = TRUE))
env$press_official_coverage_register(proof, env)
env$install_press_backlog(root, env)
Sys.setenv(PRESS_REVISIT_URL_CAP = "1")
on.exit(Sys.unsetenv("PRESS_REVISIT_URL_CAP"), add = TRUE)

historical <- paste0("https://example.gov/archive/", c("unknown-a", "unknown-b", "unknown-c"))
index <- data.frame(bioguide = "X000001", url = c(current, historical),
  date = as.Date(c("2026-09-10", NA, NA, NA)), title = "official fixture",
  stringsAsFactors = FALSE)
output <- env$stage_fulltext(index, js_bgs = "X000001")
stopifnot(identical(output$url, current), nrow(env$press_backlog_snapshot()) == 0L,
          nrow(env$press_backlog_historical_snapshot()) == 3L,
          env$PRESS_BACKLOG_CONTEXT$maintenance$historical_retained == 3L,
          env$PRESS_BACKLOG_CONTEXT$maintenance$historical_selected == 1L,
          length(env$calls) == 2L, env$calls[[1]] == current,
          env$calls[[2]] %in% historical)

# A mutated response hash invalidates coverage before a second body call.
before <- length(env$calls)
env$PRESS_OFFICIAL_COVERAGE$proofs[["X000001"]]$requests[[1]]$raw_response_sha256 <- "bad"
stopifnot(inherits(try(env$stage_fulltext(index, js_bgs = "X000001"), silent = TRUE), "try-error"),
          length(env$calls) == before)
cat("PASS: complete official inventory blocks only current work while CDX history is retained and bounded\n")

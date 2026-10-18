# All roots, bodies, clocks, and fetch results are private fixtures.
args <- commandArgs(trailingOnly = TRUE)
stopifnot(length(args) == 1L)
.libPaths(c(file.path(dirname(args[[1]]), "..", "..", "outputs", "r-library"), .libPaths()))
env <- new.env(parent = globalenv())
source(args[[1]], local = env)
root <- tempfile("press-revisit-fixture-")
dir.create(root)
env$run_root <- file.path(root, "press_refresh_20260913")
env$CHECKPOINT_DIR <- file.path(env$run_root, "pr_checkpoints")
dir.create(file.path(env$CHECKPOINT_DIR, "fulltext"), recursive = TRUE)
env$today <- as.Date("2026-09-13")
env$press_backlog_run_date <- eval(quote(function() today), env)
env$press_backlog_attempt_date <- eval(quote(function() today), env)
env$DATE_FLOOR <- as.Date("2026-06-25")
env$collector_sources_sha256 <- list(source = "fixture-v1")
env$write_rds_atomic <- function(value, path) saveRDS(value, path)
env$actual <- character(0)
env$fixture_body <- paste(rep("Recovered complete official article body evidence.", 12L), collapse = " ")
env$modes <- character(0)
env$fetch_member_fulltext <- eval(quote(function(bg, urls, force = FALSE, js = FALSE) {
  path <- file.path(CHECKPOINT_DIR, "fulltext", paste0(bg, ".rds"))
  prev <- if (file.exists(path)) readRDS(path) else data.frame(
    url = character(0), body = character(0), date_detail = as.Date(character(0)))
  todo <- setdiff(urls, prev$url[press_cached_body_complete(prev)])
  states <- modes[todo]
  states[is.na(states)] <- "fail"
  attempted <- todo[states != "unattempted"]
  actual <<- c(actual, attempted)
  recovered <- todo[states %in% c("success", "historical_success")]
  new <- data.frame(url = recovered, body = rep(fixture_body, length(recovered)),
                    date_detail = rep(today, length(recovered)))
  new$date_detail[new$url %in% todo[states == "historical_success"]] <- as.Date("2026-06-01")
  out <- dplyr::bind_rows(prev, new)
  if (nrow(out)) out <- out[!duplicated(out$url, fromLast = TRUE), , drop = FALSE]
  saveRDS(out, path)
  stopifnot(js) # this fixture reports actual network events like the adapter
  press_member_network_result(list(bioguide = bg, requested_urls = urls,
    attempted_urls = attempted, completed_urls = out$url[press_cached_body_complete(out)],
    budget_deferred_urls = todo[states %in% c("unattempted", "cut")]))
  out
}), env)
env$stage_fulltext <- eval(quote(function(idx, force = FALSE, js_bgs = character(0)) {
  parts <- lapply(sort(unique(idx$bioguide)), function(bg) {
    rows <- idx[idx$bioguide == bg, , drop = FALSE]
    body <- fetch_member_fulltext(bg, rows$url, force, bg %in% js_bgs)
    joined <- merge(rows, body, by = "url", all.x = TRUE, sort = FALSE)
    joined$date_final <- as.Date(ifelse(is.na(joined$date_detail), joined$date, joined$date_detail), origin = "1970-01-01")
    joined[is.na(joined$date_final) | joined$date_final >= DATE_FLOOR, , drop = FALSE]
  })
  dplyr::bind_rows(parts)
}), env)
env$install_press_backlog(root, env)
urls <- sprintf("https://example.gov/release-%03d", seq_len(105))
idx <- data.frame(bioguide = c(rep("X000001", 100), rep("X000002", 5)),
                  url = urls, title = "Stable raw official listing", date = as.Date(NA))
js_bgs <- unique(idx$bioguide)
invisible(env$stage_fulltext(idx, js_bgs = js_bgs))
stopifnot(length(env$actual) == 105L, nrow(env$press_backlog_snapshot()) == 0L)

# Tomorrow's same source/listing preserves exhaustion; there is no new GET or
# blocking pending set simply because the transaction date changed.
env$today <- env$today + 1L
env$run_root <- file.path(root, "press_refresh_20260914")
env$actual <- character(0)
invisible(env$stage_fulltext(idx, js_bgs = js_bgs))
stopifnot(length(env$actual) == 0L, nrow(env$press_backlog_snapshot()) == 0L,
          env$PRESS_BACKLOG_CONTEXT$maintenance$due == 0L)
env$today <- as.Date("2026-09-19")
stopifnot(nrow(env$press_backlog_due_revisits()) == 0L)
env$today <- as.Date("2026-09-20")
stopifnot(nrow(env$press_backlog_due_revisits()) == 105L)

# Deterministic oldest-due, then least-attempted selection across all members.
path <- env$press_backlog_ledger_path("X000001")
saved <- readRDS(path)
saved$rows$last_attempt_date[1:3] <- c("2026-09-10", "2026-09-09", "2026-09-09")
saved$rows$attempts[2:3] <- c(3L, 2L)
saveRDS(saved, path)
stopifnot(identical(env$press_backlog_due_revisits()$url[1:3], urls[c(3, 2, 1)]))
Sys.setenv(PRESS_REVISIT_URL_CAP = "3")
env$modes <- setNames(c("fail", "cut", "unattempted"), urls[c(3, 2, 1)])
env$actual <- character(0)
invisible(env$stage_fulltext(idx, js_bgs = js_bgs))
metrics <- env$PRESS_BACKLOG_CONTEXT$maintenance
stopifnot(metrics$due == 105L, metrics$selected == 3L, metrics$attempted == 2L, metrics$recovered == 0L,
          identical(env$actual, urls[c(3, 2)]), nrow(env$press_backlog_snapshot()) == 0L)
rows <- env$press_backlog_read("X000001")
# urls[2] already had 3 attempts, so a revisit the budget cut short is still exhausted by that
# attempt (it is stamped on the attempt day, not left at its older exhaustion date).
stopifnot(all(rows$outcome == "exhausted"), rows$last_attempt_date[1] == "2026-09-10",
          rows$last_attempt_date[2] == "2026-09-20", rows$last_exhausted_date[2] == "2026-09-20",
          rows$last_exhausted_date[3] == "2026-09-20")

# A successful due revisit updates the body cache and this run's output; failed
# or cut revisits never turn the old completed chain into a blocking backlog.
Sys.setenv(PRESS_REVISIT_URL_CAP = "1")
env$modes <- setNames("success", urls[1])
env$actual <- character(0)
output <- env$stage_fulltext(idx, js_bgs = js_bgs)
stopifnot(identical(env$actual, urls[1]), env$PRESS_BACKLOG_CONTEXT$maintenance$recovered == 1L,
          output$body[match(urls[1], output$url)] == env$fixture_body,
          nrow(env$press_backlog_snapshot()) == 0L)
key_before <- env$PRESS_BACKLOG_CONTEXT$index$evidence_key[1]
invisible(env$stage_fulltext(idx, js_bgs = js_bgs))
stopifnot(identical(key_before, env$PRESS_BACKLOG_CONTEXT$index$evidence_key[1]))

# A maintenance recovery proving an old publication date removes the stale
# failed placeholder from this run as well as retaining the historical body.
historical_url <- env$press_backlog_due_revisits()$url[1]
env$modes <- setNames("historical_success", historical_url)
output <- env$stage_fulltext(idx, js_bgs = js_bgs)
stopifnot(!historical_url %in% output$url, nrow(env$press_backlog_snapshot()) == 0L,
          env$PRESS_BACKLOG_CONTEXT$maintenance$recovered == 1L)

# Default cap is global, not per member. Due items can remain after a PASS.
Sys.unsetenv("PRESS_REVISIT_URL_CAP")
env$today <- as.Date("2026-10-01")
env$modes <- character(0)
env$actual <- character(0)
invisible(env$stage_fulltext(idx, js_bgs = js_bgs))
stopifnot(env$PRESS_BACKLOG_CONTEXT$maintenance$selected == 100L,
          env$PRESS_BACKLOG_CONTEXT$maintenance$attempted == 100L,
          length(env$actual) == 100L, nrow(env$press_backlog_snapshot()) == 0L,
          nrow(env$press_backlog_due_revisits()) > 0L)

# Listing changes reopen only affected failures, and source changes reopen all
# affected failures. Neither maintenance nor date enrichment hides new work.
Sys.setenv(PRESS_REVISIT_URL_CAP = "0")
changed <- idx
changed$title[2] <- "New substantive official listing evidence"
env$actual <- character(0)
invisible(env$stage_fulltext(changed, js_bgs = js_bgs))
stopifnot(identical(env$actual, urls[2]))
env$collector_sources_sha256 <- list(source = "fixture-v2")
env$actual <- character(0)
invisible(env$stage_fulltext(changed, js_bgs = js_bgs))
stopifnot(setequal(env$actual, setdiff(urls, c(urls[1], historical_url))), nrow(env$press_backlog_snapshot()) == 0L)

# Unattempted new URLs still block, and consume priority before maintenance -- but only for
# the member that owns them. X000001 has a pending new URL, so none of its due revisits run;
# X000002 has nothing pending, so its due revisits still do.
Sys.setenv(PRESS_REVISIT_URL_CAP = "100")
env$today <- env$today + 8L
new_url <- "https://example.gov/new-current-work"
pending_idx <- rbind(changed, data.frame(bioguide = "X000001", url = new_url,
                                       title = "New listing", date = as.Date(NA)))
env$modes <- setNames("unattempted", new_url)
env$actual <- character(0)
invisible(env$stage_fulltext(pending_idx, js_bgs = js_bgs))
stopifnot(identical(env$press_backlog_snapshot()$url, new_url),
          env$PRESS_BACKLOG_CONTEXT$maintenance$due > 0L,
          env$PRESS_BACKLOG_CONTEXT$maintenance$selected == 5L,
          setequal(env$actual, urls[101:105]), !any(env$actual %in% urls[1:100]),
          nrow(env$press_backlog_snapshot()) == 1L)
Sys.unsetenv("PRESS_REVISIT_URL_CAP")
cat("PASS: cross-day convergence, source/listing invalidation, seven-day age, global cap/order, non-blocking revisit failures, same-run recovery and current-work priority\n")

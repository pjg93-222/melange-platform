# Pure fixture tests: no network, producer invocation, or live checkpoints.
args <- commandArgs(trailingOnly = TRUE)
stopifnot(length(args) == 3L)
source(args[[1]], local = globalenv())

members <- data.frame(
  bioguide = c("B001328", "G000607", "W000832", "H001085", "X000001"),
  member = c("", NA, "  ", "Chrissy Houlahan", "Unrelated Member"),
  url = c("", NA, "  ", "https://houlahan.house.gov", "https://other.house.gov"),
  evidence = seq_len(5), stringsAsFactors = FALSE
)
fixed <- apply_press_member_overrides(members)
stopifnot(identical(nrow(fixed), nrow(members)), identical(fixed$bioguide, members$bioguide),
          identical(fixed$evidence, members$evidence), identical(fixed[5, ], members[5, ]),
          identical(fixed$member[1:4], PRESS_MEMBER_OVERRIDES$member),
          identical(fixed$url[1:4], PRESS_MEMBER_OVERRIDES$url),
          identical(apply_press_member_overrides(fixed), fixed))
provided <- members
provided$member[1] <- "Existing verified name"
provided$url[1] <- "https://updated.house.gov"
preserved <- apply_press_member_overrides(provided)
stopifnot(identical(preserved[1, ], provided[1, ]))
stopifnot(nrow(apply_press_member_overrides(members[5, , drop = FALSE])) == 1L)
stopifnot(inherits(try(apply_press_member_overrides(members["bioguide"]), silent = TRUE), "try-error"))

fixture <- new.env(parent = globalenv())
fixture$MANUAL_PRESS_URLS <- c(X000001 = "https://other.house.gov/press",
                             H001085 = "https://houlahan.house.gov/news")
forces <- logical(0)
fixture$stage_roster <- function(force = FALSE) {
  forces <<- c(forces, force)
  members
}
install_press_member_overrides(fixture)
stopifnot(identical(fixture$stage_roster(TRUE), fixed), identical(forces, TRUE),
          fixture$MANUAL_PRESS_URLS[["X000001"]] == "https://other.house.gov/press",
          identical(unname(fixture$MANUAL_PRESS_URLS[PRESS_MEMBER_OVERRIDES$bioguide]),
                    PRESS_MEMBER_OVERRIDES$press_url))
wrapped <- fixture$stage_roster
install_press_member_overrides(fixture)
stopifnot(identical(wrapped, fixture$stage_roster))

# Evaluate only the upstream retry FUNCTION definition, never its source driver.
# Prove the normal next invocation drops failed discovery/empty index while
# preserving successful discovery/index and every cached article body.
expressions <- parse(file = args[[2]])
definition <- Filter(function(expr) is.call(expr) && identical(expr[[1]], as.name("<-")) &&
                       identical(expr[[2]], as.name("retry_failed_checkpoints")), as.list(expressions))
stopifnot(length(definition) == 1L)
eval(definition[[1]], envir = globalenv())
CHECKPOINT_DIR <- tempfile("press-overrides-test-")
for (kind in c("discovery", "index", "fulltext"))
  dir.create(file.path(CHECKPOINT_DIR, kind), recursive = TRUE)
MANUAL_PRESS_URLS <- fixture$MANUAL_PRESS_URLS
for (bg in members$bioguide) {
  success <- bg == "X000001"
  saveRDS(list(index_url = if (success) "https://other.house.gov/press" else NA_character_,
               scheme = "none", n_items_p1 = 0L),
          file.path(CHECKPOINT_DIR, "discovery", paste0(bg, ".rds")))
  if (success) {
    d <- readRDS(file.path(CHECKPOINT_DIR, "discovery", paste0(bg, ".rds")))
    d$scheme <- "wpjson"
    saveRDS(d, file.path(CHECKPOINT_DIR, "discovery", paste0(bg, ".rds")))
  }
  saveRDS(data.frame(url = if (success) "https://other.house.gov/article" else character(0)),
          file.path(CHECKPOINT_DIR, "index", paste0(bg, ".rds")))
  saveRDS(paste("retained body", bg), file.path(CHECKPOINT_DIR, "fulltext", paste0(bg, ".rds")))
}
body_paths <- list.files(file.path(CHECKPOINT_DIR, "fulltext"), full.names = TRUE)
before <- tools::md5sum(body_paths)
retry_failed_checkpoints(fixed)
stopifnot(identical(before, tools::md5sum(body_paths)),
          identical(list.files(file.path(CHECKPOINT_DIR, "discovery")), "X000001.rds"),
          identical(list.files(file.path(CHECKPOINT_DIR, "index")), "X000001.rds"))

# Exercise the real receipt writer in a private environment, not the R driver.
.libPaths(c(file.path(dirname(args[[1]]), "..", "..", "outputs", "r-library"), .libPaths()))
producer <- new.env(parent = globalenv())
producer$run_root <- tempfile("press-receipt-test-")
dir.create(producer$run_root)
wrapper_expressions <- parse(file = args[[3]])
for (name in c("sha256_file", "write_retry_receipt", "materialize_press_outputs",
               "bind_historical_retry_source")) {
  definition <- Filter(function(expr) is.call(expr) && identical(expr[[1]], as.name("<-")) &&
                         identical(expr[[2]], as.name(name)), as.list(wrapper_expressions))
  stopifnot(length(definition) == 1L)
  eval(definition[[1]], envir = producer)
}
source_names <- c("refresh_press_delta.R", "press_member_overrides.R", "press_official_coverage.R",
                  "press_release_date_precedence.R", "press_request_deadlines.R",
                  "press_wordpress_custom_posts.R", "press_official_dated_listings.R",
                  "press_backlog.R", "cu_press_release_scraper.R")
producer$collector_source_paths <- as.list(setNames(file.path(producer$run_root, source_names), source_names))
for (path in producer$collector_source_paths) writeLines("original source", path)
producer$collector_sources_sha256 <- lapply(producer$collector_source_paths, producer$sha256_file)
for (name in c("press_delta_fulltext.rds", "press_delta_fulltext.csv"))
  writeLines("fixture body bytes", file.path(producer$run_root, name))
producer$retry_receipt_path <- file.path(producer$run_root, "press_network_retry_receipt.json")
producer$official_coverage_artifact_path <- file.path(producer$run_root, "press_official_coverage.json")
producer$historical_maintenance_artifact_path <- file.path(producer$run_root, "press_historical_maintenance.csv")
producer$live_retry_started_at <- "2026-09-13T08:00:00+00:00"
producer$iso8601_utc <- function() "2026-09-13T08:01:00+00:00"
producer$canonical_url <- identity
producer$press_backlog_snapshot <- function() data.frame(
  bioguide = character(0), url = character(0), date = character(0),
  collection_status = character(0))
producer$press_backlog_ledger_snapshot <- function() list(schema = "press-body-attempts-snapshot-v1", members = list())
producer$press_backlog_historical_snapshot <- function() data.frame(
  bioguide = character(0), url = character(0), official_coverage_status = character(0),
  collection_status = character(0))
producer$write_csv_atomic <- function(frame, path) write.csv(frame, path, row.names = FALSE)
producer$write_rds_atomic <- function(frame, path) saveRDS(frame, path)
producer$DATE_FLOOR <- as.Date("2026-06-25")
producer$run_receipt_metrics <- list(retry_source_run = NULL, terminal_nonarticle_urls = 0L,
                                    retryable_body_candidates = 0L, recovered_bodies = 0L)
producer$promote_with_backup <- function(from, to) stopifnot(file.rename(from, to))
producer$write_official_coverage_artifact <- function() {
  value <- list(schema = "official-current-inventory-snapshot-v1", floor = "2026-06-25",
                member_count = 0L, current_url_count = 0L, members = list())
  jsonlite::write_json(value, producer$official_coverage_artifact_path,
                       auto_unbox = TRUE, pretty = TRUE)
  value
}
materialized <- list(failed_bodies = data.frame(collection_status = character(0)),
                     fulltext = data.frame(), articles = data.frame(),
                     host_articles = data.frame(url = character(0)), article_mentions = data.frame())
producer$write_retry_receipt(materialized)
receipt <- jsonlite::fromJSON(producer$retry_receipt_path, simplifyVector = FALSE)
stopifnot(identical(receipt$collector_sources_sha256, producer$collector_sources_sha256),
          identical(receipt$status, "PASS"), identical(receipt$pending_body_urls, 0L),
          identical(receipt$body_attempt_ledger_sha256,
                    producer$sha256_file(file.path(producer$run_root, "press_body_attempt_ledger.rds"))),
          identical(receipt$revisit_due_urls, 0L), identical(receipt$revisit_min_days, 7L),
          identical(receipt$pending_body_urls_sha256,
                    producer$sha256_file(file.path(producer$run_root, "press_delta_pending.csv"))))
producer$press_backlog_snapshot <- function() data.frame(bioguide = "X000001", url = "https://example.gov/pending",
  date = NA_character_, collection_status = "body_pending_network_attempt")
completion <- producer$write_retry_receipt(materialized)
stopifnot(identical(completion$status, "PARTIAL"), identical(completion$pending_body_urls, 1L))
pending_snapshot <- producer$press_backlog_snapshot
producer$press_backlog_snapshot <- function() pending_snapshot()[FALSE, ]
producer$PRESS_BACKLOG_CONTEXT <- list(maintenance = list(minimum_age_days = 7L, url_cap = 100L,
  due = 105L, selected = 100L, attempted = 2L, recovered = 1L,
  historical_retained = 0L, historical_selected = 0L))
completion <- producer$write_retry_receipt(materialized)
stopifnot(completion$status == "PASS", completion$pending_body_urls == 0L,
          completion$revisit_due_urls == 105L, completion$revisit_attempted_urls == 2L,
          completion$revisit_recovered_urls == 1L)

# A targeted body recovery binds the old inventory generation and cannot
# silently recertify it as a fresh current-source collection.
historical_root <- tempfile("historical-press-source-")
dir.create(historical_root)
writeLines("original source fulltext", file.path(historical_root, "press_delta_fulltext.rds"))
historical_receipt <- list(mode = "live_network_retry",
  fulltext_rds_sha256 = producer$sha256_file(file.path(historical_root, "press_delta_fulltext.rds")),
  collector_sources_sha256 = list(source = "older-inventory-generation"))
jsonlite::write_json(historical_receipt, file.path(historical_root, "press_network_retry_receipt.json"),
                     auto_unbox = TRUE)
producer$run_receipt_metrics$retry_source_run <- basename(historical_root)
producer$run_receipt_metrics$historical_inventory_source <-
  producer$bind_historical_retry_source(historical_root)
historical <- producer$write_retry_receipt(materialized)
stopifnot(historical$status == "PASS", historical$mode == "historical_body_recovery",
  historical$inventory_freshness == "historical",
  historical$historical_inventory_source$collector_sources_sha256$source == "older-inventory-generation")
historical_before <- tools::md5sum(producer$retry_receipt_path)
writeLines("changed historical fulltext", file.path(historical_root, "press_delta_fulltext.rds"))
stopifnot(inherits(try(producer$bind_historical_retry_source(historical_root), silent = TRUE), "try-error"),
  inherits(try(producer$write_retry_receipt(materialized), silent = TRUE), "try-error"),
  identical(tools::md5sum(producer$retry_receipt_path), historical_before))
producer$run_receipt_metrics$retry_source_run <- NULL
producer$run_receipt_metrics$historical_inventory_source <- NULL
producer$press_backlog_snapshot <- pending_snapshot
receipt_before <- tools::md5sum(producer$retry_receipt_path)
writeLines("changed source", producer$collector_source_paths[[1]])
stopifnot(inherits(try(producer$write_retry_receipt(materialized), silent = TRUE), "try-error"),
          identical(tools::md5sum(producer$retry_receipt_path), receipt_before))
empty <- producer$materialize_press_outputs(data.frame(), "after_network_retry")
stopifnot(nrow(empty$fulltext) == 0L, nrow(empty$articles) == 0L)
producer$PRESS_BACKLOG_CONTEXT <- list()
pending_full <- data.frame(bioguide = "X000001", url = "https://example.gov/pending", body = NA_character_,
                          source = NA_character_, date_final = as.Date(NA))
missing <- producer$materialize_press_outputs(pending_full, "after_network_retry")
stopifnot(nrow(missing$articles) == 0L, nrow(missing$failed_bodies) == 1L,
          missing$failed_bodies$collection_status == "body_pending_network_attempt")
cat("PASS: missing identities/sites repaired; existing data, roster membership, and manual routes preserved; installation idempotent\n")

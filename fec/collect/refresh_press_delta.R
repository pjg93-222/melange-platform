#!/usr/bin/env Rscript

# Refresh congressional press-release metadata in an isolated checkpoint tree.
#
# The production scraper resumes successful per-member index checkpoints, so a
# normal rerun does not discover newer releases.  This wrapper deliberately uses
# a fresh checkpoint root, anti-joins the refreshed index against the production
# URL universe, and fetches bodies only for unseen URLs.  It never invokes the
# production scraper's autorun driver or stage_outputs().

args <- commandArgs(trailingOnly = TRUE)
discover_only <- "--discover-only" %in% args
cache_only <- "--cache-only" %in% args
score_only <- "--score-only" %in% args
retry_from_at <- match("--retry-from", args)
retry_from <- NA_character_
if (!is.na(retry_from_at)) {
  if (retry_from_at == length(args) || startsWith(args[[retry_from_at + 1L]], "--"))
    stop("--retry-from requires a prior press_refresh directory")
  retry_from <- args[[retry_from_at + 1L]]
  args <- args[-c(retry_from_at, retry_from_at + 1L)]
}
# Receipt timestamps are a cross-language contract.  `usetz = TRUE` appends the
# timezone NAME ("UTC"), which Python's ISO parser does not accept.  Emit a
# numeric offset through one helper for both fields.
iso8601_utc <- function(when = Sys.time()) {
  format(when, "%Y-%m-%dT%H:%M:%S+00:00", tz = "UTC")
}
live_retry_started_at <- iso8601_utc()
args <- args[!(args %in% c("--discover-only", "--cache-only", "--score-only"))]

run_root <- if (length(args) >= 1L) args[[1L]] else
  "C:/Users/pgoff/OneDrive/Desktop/Melange Systems/outputs/press_refresh_20260828"
date_floor_text <- if (length(args) >= 2L) args[[2L]] else "2026-06-25"
workers_text <- if (length(args) >= 3L) args[[3L]] else "4"
max_delta_text <- if (length(args) >= 4L) args[[4L]] else "5000"

date_floor <- as.Date(date_floor_text)
workers <- suppressWarnings(as.integer(workers_text))
max_delta <- suppressWarnings(as.integer(max_delta_text))
if (is.na(date_floor)) stop("invalid date floor: ", date_floor_text)
if (is.na(workers) || workers < 1L) stop("workers must be a positive integer")
if (is.na(max_delta) || max_delta < 1L) stop("max delta must be a positive integer")

# Windows defaults unchanged; the compute container points these at its mounts (MONEYBALL_DRAFT is the same variable fec/paths.py reads).
env_or <- function(name, default) { value <- Sys.getenv(name); if (nzchar(value)) value else default }
draft_root <- env_or("MONEYBALL_DRAFT", "C:/Users/pgoff/OneDrive/Desktop/Draft Systems")
scraper_path <- file.path(draft_root, "cu_press_release_scraper.R")
old_index_path <- file.path(draft_root, "all_press_release_index.csv")
if (!file.exists(scraper_path)) stop("scraper not found: ", scraper_path)
if (!file.exists(old_index_path)) stop("production index not found: ", old_index_path)

Sys.setenv(
  CU_PR_NO_AUTORUN = "1",
  CU_PR_NO_CHROMOTE = "1",
  CU_PR_WORKERS = as.character(workers),
  CU_PR_OFFLINE = "",
  CU_PR_SKIP_FETCH = if (cache_only) "1" else "",
  CU_PR_COMMITTEES = "TRUE",
  CU_PR_NEWS = "TRUE",
  CU_PR_COSPONSORS = "FALSE"
)

r_library <- env_or("MELANGE_R_LIBRARY", "C:/Users/pgoff/OneDrive/Desktop/Melange Systems/outputs/r-library")
dir.create(r_library, recursive = TRUE, showWarnings = FALSE)
.libPaths(c(r_library, .libPaths()))

sha256_file <- function(path) {
  if (!requireNamespace("digest", quietly = TRUE))
    stop("digest package is required for retry-receipt hashing")
  unname(digest::digest(file = path, algo = "sha256", serialize = FALSE))
}
wrapper_file <- sub("^--file=", "", grep("^--file=", commandArgs(), value = TRUE)[1])
overrides_path <- file.path(dirname(normalizePath(wrapper_file, mustWork = TRUE)),
                            "press_member_overrides.R")
coverage_path <- file.path(dirname(normalizePath(wrapper_file, mustWork = TRUE)),
                           "press_official_coverage.R")
date_precedence_path <- file.path(dirname(normalizePath(wrapper_file, mustWork = TRUE)),
                                  "press_release_date_precedence.R")
wordpress_path <- file.path(dirname(normalizePath(wrapper_file, mustWork = TRUE)),
                            "press_wordpress_custom_posts.R")
dated_listings_path <- file.path(dirname(normalizePath(wrapper_file, mustWork = TRUE)),
                                 "press_official_dated_listings.R")
deadlines_path <- file.path(dirname(normalizePath(wrapper_file, mustWork = TRUE)),
                           "press_request_deadlines.R")
backlog_path <- file.path(dirname(normalizePath(wrapper_file, mustWork = TRUE)),
                         "press_backlog.R")
collector_source_paths <- list(refresh_press_delta.R = wrapper_file,
                               press_member_overrides.R = overrides_path,
                               press_official_coverage.R = coverage_path,
                               press_release_date_precedence.R = date_precedence_path,
                               press_wordpress_custom_posts.R = wordpress_path,
                               press_official_dated_listings.R = dated_listings_path,
                               press_request_deadlines.R = deadlines_path,
                               press_backlog.R = backlog_path,
                               cu_press_release_scraper.R = scraper_path)
collector_sources_sha256 <- lapply(collector_source_paths, sha256_file)
source(scraper_path, local = globalenv(), chdir = FALSE)
source(coverage_path, local = globalenv(), chdir = FALSE)
press_official_coverage_reset()
source(date_precedence_path, local = globalenv(), chdir = FALSE)
install_press_release_date_precedence()

# Keep current member routes in this repository while retaining the upstream
# scraper. The existing retry_failed_checkpoints() applies repaired routes on
# the next live invocation; loading these definitions never changes checkpoints.
source(overrides_path, local = globalenv(), chdir = FALSE)
install_press_member_overrides()
source(wordpress_path, local = globalenv(), chdir = FALSE)
install_press_wordpress_custom_posts()
source(dated_listings_path, local = globalenv(), chdir = FALSE)
install_press_official_dated_listings()
source(deadlines_path, local = globalenv(), chdir = FALSE)
install_press_request_deadlines()
source(backlog_path, local = globalenv(), chdir = FALSE)
install_press_backlog(dirname(dirname(dirname(normalizePath(wrapper_file, mustWork = TRUE)))))
if (!identical(collector_sources_sha256, lapply(collector_source_paths, sha256_file)))
  stop("press collector source changed during initialization")

# The upstream tokenizers contain a locale-sensitive curly-apostrophe regex.
# Replace curly apostrophes with ASCII before using an ASCII-safe character
# class; this preserves the same attribution rules on Windows C locales.
normalize_press_apostrophes <- function(value) {
  gsub(intToUtf8(0x2019), "'", value, fixed = TRUE)
}

# The same literal occurs in the sentiment and bill-number regular expressions.
# Keep the semantics while ensuring that worker processes never have to compile
# a locale-dependent source-code literal.
CU_TERMS_RX <- paste0(
  "credit[- ]union|\\bNCUA\\b|National Credit Union|Share Insurance Fund|",
  "field of membership|America'?s Credit Unions|\\bCUNA\\b|\\bNAFCU\\b|",
  "Defense Credit Union"
)
bill_num_rx <- function(nums) {
  one <- function(value) {
    value <- toupper(gsub("[^A-Z0-9]", "", toupper(value)))
    digits <- sub("^[A-Z]+", "", value)
    prefix <- sub("[0-9]+$", "", value)
    pre <- switch(prefix,
      S = "S\\.?",
      HR = "H\\.?\\s*R\\.?",
      SJRES = "S\\.?\\s*J\\.?\\s*RES\\.?",
      HJRES = "H\\.?\\s*J\\.?\\s*RES\\.?",
      prefix
    )
    paste0("(?<!')\\b", pre, "\\s*", digits, "\\b")
  }
  paste(vapply(strsplit(nums, ";", fixed = TRUE)[[1]], one, character(1)),
        collapse = "|")
}
if (exists("BILLS") && "nums" %in% names(BILLS)) {
  BILLS$num_rx <- vapply(BILLS$nums, bill_num_rx, character(1))
  ALL_BILLS_NUM_RX <- paste(BILLS$num_rx, collapse = "|")
}
score_release_upstream <- score_release
score_release <- function(title, body, date_final) {
  score_release_upstream(
    normalize_press_apostrophes(title),
    normalize_press_apostrophes(body),
    date_final
  )
}

press_token_words <- function(value, upper = FALSE) {
  value <- normalize_press_apostrophes(value)
  pattern <- if (upper) "[^A-Za-z'-]+" else "[^a-z'-]+"
  words <- tolower(unlist(strsplit(value, pattern, perl = TRUE)))
  words[nzchar(words)]
}

attribute_statement_to_member <- function(title, roster) {
  if (length(title) != 1L || is.na(title) || !nzchar(title)) return(NA_character_)
  words <- press_token_words(title, upper = TRUE)
  if (!length(words)) return(NA_character_)
  nm <- roster$member
  bg <- roster$bioguide
  first <- tolower(sub("\\s.*", "", nm))
  surn <- tolower(sub(".*\\s", "", nm))
  full_hit <- which(first %in% words & surn %in% words)
  if (length(unique(bg[full_hit])) == 1L) return(bg[full_hit][1])
  tl <- tolower(title)
  role <- if (grepl("senator|\\bsen\\.|ranking member|\\bchair", tl)) "Senate"
          else if (grepl("\\brep\\.|representative|congress", tl)) "House"
          else NA_character_
  hit <- which(surn %in% words)
  if (!is.na(role) && "chamber" %in% names(roster)) {
    role_hit <- hit[roster$chamber[hit] == role]
    if (length(role_hit)) hit <- role_hit
  }
  if (length(unique(bg[hit])) == 1L) return(bg[hit][1])
  NA_character_
}

members_in_text <- function(text, idx) {
  if (is.null(text) || is.na(text) || !nzchar(text)) return(integer(0))
  tl <- tolower(normalize_press_apostrophes(text))
  words <- unique(press_token_words(tl))
  which(vapply(seq_along(idx$bg), function(i) {
    if (!(idx$surn[i] %in% words)) return(FALSE)
    if (grepl(paste0("\\b", idx$first[i], "\\s+", idx$surn[i], "\\b"), tl, perl = TRUE))
      return(TRUE)
    idx$uniq[i] &&
      grepl(paste0("\\b", HONORIFIC_RX, "\\s+", idx$surn[i], "\\b"),
            tl, perl = TRUE)
  }, logical(1)))
}

match_one_member <- function(name, roster) {
  if (is.null(name) || is.na(name) || !nzchar(name)) return(NA_character_)
  words <- press_token_words(tolower(name))
  if (!length(words)) return(NA_character_)
  first <- tolower(sub("\\s.*", "", roster$member))
  surn <- tolower(sub(".*\\s", "", roster$member))
  full <- which(first %in% words & surn %in% words)
  if (length(unique(roster$bioguide[full])) == 1L) return(roster$bioguide[full][1])
  surname_hit <- which(surn %in% words)
  if (length(unique(roster$bioguide[surname_hit])) == 1L)
    return(roster$bioguide[surname_hit][1])
  NA_character_
}

expand_official_member_turns <- function(frame, roster) {
  if (!nrow(frame)) return(frame)
  idx <- news_name_index(roster)
  additions <- vector("list", nrow(frame))
  n_added <- 0L
  for (i in seq_len(nrow(frame))) {
    title <- ifelse(is.na(frame$title[i]), "", frame$title[i])
    body <- ifelse(is.na(frame$body[i]), "", frame$body[i])
    text <- squish(paste(title, body))
    if (!nzchar(text)) next
    hits <- members_in_text(text, idx)
    hits <- hits[idx$bg[hits] != frame$bioguide[i]]
    if (!length(hits)) next
    sents <- split_sentences(text)
    rows <- lapply(hits, function(hit) {
      roster_row <- roster[match(idx$bg[hit], roster$bioguide), , drop = FALSE]
      row <- frame[i, , drop = FALSE]
      for (field in intersect(c("member", "chamber", "state", "party", "bioguide"), names(row))) {
        row[[field]] <- roster_row[[field]][1]
      }
      row$url <- paste0(sub("#.*$", "", row$url), "#", idx$bg[hit])
      row$body <- member_slice(title, sents, idx$surn[hit])
      row$source <- "OFFICIAL_MENTION"
      row
    })
    additions[[i]] <- dplyr::bind_rows(rows)
    n_added <- n_added + length(rows)
  }
  message("Official press coauthor/mention expansion added ", n_added,
          " article-member turn(s).")
  dplyr::bind_rows(c(list(frame), additions))
}

run_root <- normalizePath(run_root, winslash = "/", mustWork = FALSE)
CHECKPOINT_DIR <- file.path(run_root, "pr_checkpoints")
DATE_FLOOR <- date_floor
FULLTEXT_MODE <- "all"
CHROME_UA <- "MelangeSystemsResearchCollector/1.0 (public-records research)"

OUT_XLSX <- file.path(run_root, "press_delta_scored.xlsx")
OUT_SCORED_CSV <- file.path(run_root, "press_delta_scored.csv")
OUT_INDEX_CSV <- file.path(run_root, "press_refreshed_index.csv")
OUT_CAT_CSV <- file.path(run_root, "press_delta_category_scores.csv")
OUT_NET_XLSX <- file.path(run_root, "press_delta_net_scores.xlsx")
MANUAL_NEWS_CSV <- file.path(run_root, "manual_cu_news.csv")
X_HANDLES_CSV <- file.path(run_root, "member_x_handles.csv")
COSPONSOR_CSV <- file.path(run_root, "bill_sponsorship_coverage.csv")

for (path in c(
  run_root,
  CHECKPOINT_DIR,
  file.path(CHECKPOINT_DIR, "discovery"),
  file.path(CHECKPOINT_DIR, "index"),
  file.path(CHECKPOINT_DIR, "fulltext")
)) {
  dir.create(path, recursive = TRUE, showWarnings = FALSE)
}

copy_if_absent <- function(source_path, target_path) {
  if (!file.exists(target_path) && file.exists(source_path)) {
    ok <- file.copy(source_path, target_path, overwrite = FALSE)
    if (!ok) stop("failed to copy seed file: ", source_path)
  }
}

copy_if_absent(file.path(draft_root, "manual_cu_news.csv"), MANUAL_NEWS_CSV)
copy_if_absent(file.path(draft_root, "member_x_handles.csv"), X_HANDLES_CSV)

# Committee/news body checkpoints are small and source-wide rather than tied to
# a member index.  Seed only those caches; member discovery/index/fulltext starts
# empty so the metadata refresh is genuine.
production_fulltext <- file.path(draft_root, "pr_checkpoints", "fulltext")
if (dir.exists(production_fulltext)) {
  seed <- list.files(
    production_fulltext,
    pattern = "^(CMTE_|NEWS_).*\\.rds$",
    full.names = TRUE
  )
  for (source_path in seed) {
    copy_if_absent(
      source_path,
      file.path(CHECKPOINT_DIR, "fulltext", basename(source_path))
    )
  }
}

# CROSS-DAY BODY RESUME (2026-08-30).  The body of a published release is
# immutable, so the PREVIOUS run's member full-text checkpoints are sound to
# reuse and a run killed at 98% costs 2% instead of six hours.
#
# Discovery and index deliberately stay COLD.  Pre-seeding those is exactly what
# caused the 2026-08-28 incident: a hand-copied June discovery tree hid ~4,269
# releases across ten members, which the run then reported as a clean delta.
# Bodies may be inherited; the question "what exists?" must always be re-asked.
previous_run_fulltext <- function(current_root) {
  parent <- dirname(current_root)
  siblings <- list.dirs(parent, full.names = TRUE, recursive = FALSE)
  siblings <- siblings[grepl("press_refresh_[0-9]{8}$", siblings)]
  current <- normalizePath(current_root, winslash = "/", mustWork = FALSE)
  siblings <- siblings[
    normalizePath(siblings, winslash = "/", mustWork = FALSE) != current
  ]
  for (candidate in sort(siblings, decreasing = TRUE)) {
    fulltext_dir <- file.path(candidate, "pr_checkpoints", "fulltext")
    if (dir.exists(fulltext_dir)) return(fulltext_dir)
  }
  character(0)
}

prior_fulltext <- previous_run_fulltext(run_root)
if (length(prior_fulltext)) {
  seeded <- 0L
  for (source_path in list.files(prior_fulltext, pattern = "[.]rds$",
                                 full.names = TRUE)) {
    target <- file.path(CHECKPOINT_DIR, "fulltext", basename(source_path))
    if (!file.exists(target)) {
      copy_if_absent(source_path, target)
      seeded <- seeded + 1L
    }
  }
  message("Cross-day resume: seeded ", seeded, " member body checkpoint(s) from ",
          prior_fulltext, " (bodies are immutable; discovery/index stay cold).")
} else {
  message("Cross-day resume: no prior run checkpoints to inherit.")
}

# A checkpoint truncated by a kill mid-saveRDS poisons its member FOREVER:
# readRDS() throws, stage_fulltext's tryCatch swallows the error, and that
# member yields nothing on every later run.  Quarantine unreadable checkpoints
# at startup so the member simply re-fetches -- no human in the loop.
quarantine_unreadable_checkpoints <- function(dir_path) {
  if (!dir.exists(dir_path)) return(invisible(0L))
  moved <- 0L
  for (path in list.files(dir_path, pattern = "[.]rds$", full.names = TRUE)) {
    frame <- tryCatch(readRDS(path), error = function(error) NULL)
    if (!is.null(frame)) next
    quarantined <- paste0(path, ".corrupt.", format(Sys.time(), "%Y%m%dT%H%M%S"))
    if (file.rename(path, quarantined)) {
      moved <- moved + 1L
      message("SELF-HEAL: quarantined unreadable checkpoint ", basename(path),
              " -> ", basename(quarantined), "; that member will re-fetch.")
    } else {
      warning("unreadable checkpoint could not be quarantined: ", path)
    }
  }
  invisible(moved)
}
quarantine_unreadable_checkpoints(file.path(CHECKPOINT_DIR, "fulltext"))

canonical_url <- function(value) {
  value <- trimws(as.character(value))
  value <- sub("#.*$", "", value)
  value <- sub("/$", "", value)
  tolower(sub("^https?://(www\\.)?", "", value, ignore.case = TRUE))
}

# Discovery can surface images/documents nested below an article URL (notably
# Senate Drupal media aliases) and generic constituent-service pages.  They are
# still accounted for, but are terminal non-articles rather than failed article
# bodies.  Classification is URL-structural and deterministic; ambiguous slugs
# stay retryable.
terminal_nonarticle_reason <- function(value) {
  value <- tolower(trimws(as.character(value %||% "")))
  path <- sub("^https?://[^/]+", "", sub("[?#].*$", "", value))
  host <- sub("^https?://([^/]+).*$", "\\1", value)
  if (grepl("[.](jpe?g|png|gif|webp|svg|ico|pdf|docx?|xlsx?|pptx?|zip)$", path,
            perl = TRUE)) return("document_or_media_asset")
  if (host %in% c("www.bluntrochester.senate.gov", "bluntrochester.senate.gov") &&
      grepl("/(news/press-releases|newsroom/newsletters|news/in-the-news)/[^/]+/[^/]+/?$",
            path, perl = TRUE))
    return("nested_press_release_media_asset")
  if (grepl("/(services?|constituent-services?|contact|about)/", path, perl = TRUE))
    return("nonarticle_service_page")
  if (grepl("/(feed|tag|category|author|page)/?[^/]*$", path, perl = TRUE))
    return("listing_or_feed_page")
  ""
}

split_terminal_nonarticles <- function(frame) {
  if (!nrow(frame)) return(list(articles = frame, nonarticles = frame))
  reason <- vapply(frame$url, terminal_nonarticle_reason, character(1))
  terminal <- frame[nzchar(reason), , drop = FALSE]
  if (nrow(terminal)) terminal$terminal_reason <- reason[nzchar(reason)]
  list(articles = frame[!nzchar(reason), , drop = FALSE], nonarticles = terminal)
}

# A kill between the two renames in promote_with_backup() used to wedge EVERY
# future run: the leftover .previous made the next promote stop() forever, and
# clearing it needed a human.  The 2026-08-30 ruling ("It should be
# self-healing", "a process that doesn't require AI") makes that unacceptable.
#
# The recovery is unambiguous because of where the only window is.  Between
# file.rename(path, backup) and file.rename(temp, path) the primary is ABSENT --
# rename is atomic, so the primary can never be observed truncated.  Hence:
#   .previous present + primary intact  -> the promote SUCCEEDED and only the
#                                          backup removal failed: drop the backup
#   .previous present + primary missing -> we died inside the window: restore it
# Both paths are loud; neither is fatal.  Only a failed RESTORE still stops,
# because that genuinely leaves no good copy.
heal_stale_backup <- function(path) {
  backup <- paste0(path, ".previous")
  if (!file.exists(backup)) return(invisible(FALSE))
  size <- if (file.exists(path)) file.size(path) else NA_real_
  if (!is.na(size) && size > 0) {
    message("SELF-HEAL: discarding stale backup (primary intact, ", size,
            " bytes): ", backup)
    if (!file.remove(backup))
      warning("could not remove stale backup: ", backup)
  } else {
    message("SELF-HEAL: primary missing or empty; restoring it from ", backup)
    if (!file.rename(backup, path))
      stop("cannot restore primary from stale backup: ", backup)
  }
  invisible(TRUE)
}

promote_with_backup <- function(temp, path) {
  heal_stale_backup(path)
  backup <- paste0(path, ".previous")
  had_previous <- file.exists(path)
  if (had_previous && !file.rename(path, backup)) stop("cannot back up: ", path)
  if (!file.rename(temp, path)) {
    if (had_previous) file.rename(backup, path)
    stop("cannot promote temporary output: ", path)
  }
  if (had_previous && !file.remove(backup))
    warning("promoted output but could not remove backup: ", backup)
}

write_csv_atomic <- function(frame, path) {
  temp <- paste0(path, ".part")
  utils::write.csv(frame, temp, row.names = FALSE, na = "")
  promote_with_backup(temp, path)
}

write_rds_atomic <- function(object, path) {
  temp <- paste0(path, ".part")
  saveRDS(object, temp)
  promote_with_backup(temp, path)
}

retry_receipt_path <- file.path(run_root, "press_network_retry_receipt.json")
official_coverage_artifact_path <- file.path(run_root, "press_official_coverage.json")
historical_maintenance_artifact_path <- file.path(run_root, "press_historical_maintenance.csv")
run_receipt_metrics <- list(
  retry_source_run = NULL,
  historical_inventory_source = NULL,
  terminal_nonarticle_urls = 0L,
  retryable_body_candidates = 0L,
  recovered_bodies = 0L
)

write_official_coverage_artifact <- function() {
  snapshot <- press_official_coverage_snapshot()
  temp <- paste0(official_coverage_artifact_path, ".part")
  jsonlite::write_json(snapshot, temp, auto_unbox = TRUE, pretty = TRUE, na = "null")
  promote_with_backup(temp, official_coverage_artifact_path)
  snapshot
}

validate_official_coverage_artifacts <- function(receipt) {
  if (is.null(receipt$schema_version) || receipt$schema_version < 2L) return(invisible(TRUE))
  if (!file.exists(official_coverage_artifact_path) ||
      !identical(receipt$official_coverage_sha256,
                 sha256_file(official_coverage_artifact_path)))
    stop("official press coverage proof does not match its receipt")
  snapshot <- jsonlite::fromJSON(official_coverage_artifact_path, simplifyVector = FALSE)
  press_official_coverage_import(snapshot)
  rebuilt <- press_official_coverage_snapshot()
  if (!identical(as.integer(receipt$official_coverage_members),
                 as.integer(rebuilt$member_count)) ||
      !identical(as.integer(receipt$official_current_urls),
                 as.integer(rebuilt$current_url_count)))
    stop("official press coverage counts do not match their proof")
  if (!file.exists(historical_maintenance_artifact_path) ||
      !identical(receipt$historical_maintenance_sha256,
                 sha256_file(historical_maintenance_artifact_path)))
    stop("historical press maintenance artifact does not match its receipt")
  historical <- utils::read.csv(historical_maintenance_artifact_path,
                                stringsAsFactors = FALSE, check.names = FALSE)
  if (!all(c("bioguide", "url", "official_coverage_status", "collection_status") %in%
           names(historical)) ||
      !identical(as.integer(receipt$historical_maintenance_urls), nrow(historical)))
    stop("historical press maintenance counts do not match their artifact")
  invisible(TRUE)
}

bind_historical_retry_source <- function(source_root) {
  receipt_path <- file.path(source_root, "press_network_retry_receipt.json")
  fulltext_path <- file.path(source_root, "press_delta_fulltext.rds")
  if (!file.exists(receipt_path) || !file.exists(fulltext_path))
    stop("historical body recovery requires a source receipt and fulltext artifact")
  receipt <- jsonlite::fromJSON(receipt_path, simplifyVector = FALSE)
  if (!identical(receipt$fulltext_rds_sha256, sha256_file(fulltext_path)))
    stop("historical body recovery source fulltext is not hash-bound")
  list(run_root = normalizePath(source_root, winslash = "/", mustWork = TRUE),
       receipt_sha256 = sha256_file(receipt_path),
       fulltext_rds_sha256 = sha256_file(fulltext_path),
       collector_sources_sha256 = receipt$collector_sources_sha256,
       official_coverage_sha256 = receipt$official_coverage_sha256)
}

import_official_coverage_from_run <- function(source_root) {
  path <- file.path(source_root, "press_official_coverage.json")
  receipt_path <- file.path(source_root, "press_network_retry_receipt.json")
  if (!file.exists(path)) return(invisible(FALSE))
  if (!file.exists(receipt_path)) stop("coverage retry source lacks a receipt")
  receipt <- jsonlite::fromJSON(receipt_path, simplifyVector = TRUE)
  if (is.null(receipt$official_coverage_sha256) ||
      !identical(receipt$official_coverage_sha256, sha256_file(path)))
    stop("coverage retry source proof is not hash-bound to its receipt")
  snapshot <- jsonlite::fromJSON(path, simplifyVector = FALSE)
  press_official_coverage_import(snapshot)
  invisible(TRUE)
}

write_retry_receipt <- function(materialized) {
  if (!identical(collector_sources_sha256, lapply(collector_source_paths, sha256_file)))
    stop("press collector source changed during collection; clean retry required")
  if (!is.null(run_receipt_metrics$retry_source_run)) {
    source <- run_receipt_metrics$historical_inventory_source
    if (is.null(source) ||
        !identical(source$receipt_sha256, sha256_file(file.path(
          source$run_root, "press_network_retry_receipt.json"))) ||
        !identical(source$fulltext_rds_sha256, sha256_file(file.path(
          source$run_root, "press_delta_fulltext.rds"))))
      stop("historical inventory source changed during body recovery")
  }
  full_rds <- file.path(run_root, "press_delta_fulltext.rds")
  full_csv <- file.path(run_root, "press_delta_fulltext.csv")
  failures <- materialized$failed_bodies
  pending <- press_backlog_snapshot()
  pending_path <- file.path(run_root, "press_delta_pending.csv")
  write_csv_atomic(pending, pending_path)
  historical <- if (exists("PRESS_BACKLOG_CONTEXT", inherits = TRUE))
    press_backlog_historical_snapshot() else data.frame(
      bioguide = character(0), url = character(0), date = character(0),
      evidence_key = character(0), coverage_status = character(0),
      official_coverage_status = character(0), collection_status = character(0))
  write_csv_atomic(historical, historical_maintenance_artifact_path)
  coverage <- write_official_coverage_artifact()
  cached_dates <- if (exists("PRESS_BACKLOG_CONTEXT", inherits = TRUE)) PRESS_BACKLOG_CONTEXT$cached_dates else NULL
  if (is.null(cached_dates)) cached_dates <- data.frame(
    bioguide = character(0), url = character(0), cached_url = character(0), date_detail = as.Date(character(0)), evidence = character(0))
  cached_dates_path <- file.path(run_root, "press_delta_cached_dates.csv")
  write_csv_atomic(cached_dates, cached_dates_path)
  ledger_path <- file.path(run_root, "press_body_attempt_ledger.rds")
  write_rds_atomic(press_backlog_ledger_snapshot(), ledger_path)
  revisits <- if (exists("PRESS_BACKLOG_CONTEXT", inherits = TRUE)) PRESS_BACKLOG_CONTEXT$maintenance else NULL
  if (is.null(revisits)) revisits <- list(minimum_age_days = 7L, url_cap = 100L,
    due = 0L, selected = 0L, attempted = 0L, recovered = 0L,
    historical_retained = nrow(historical), historical_selected = 0L)
  status <- if (nrow(failures)) failures$collection_status else character(0)
  payload <- list(
    schema_version = 2L,
    mode = if (is.null(run_receipt_metrics$retry_source_run))
      "live_network_retry" else "historical_body_recovery",
    inventory_freshness = if (is.null(run_receipt_metrics$retry_source_run))
      "fresh" else "historical",
    historical_inventory_source = run_receipt_metrics$historical_inventory_source,
    status = if (nrow(pending)) "PARTIAL" else "PASS",
    pending_body_urls = nrow(pending),
    pending_body_urls_sha256 = sha256_file(pending_path),
    cached_date_rejoins = nrow(cached_dates),
    cached_date_rejoins_sha256 = sha256_file(cached_dates_path),
    official_coverage_members = coverage$member_count,
    official_current_urls = coverage$current_url_count,
    official_coverage_sha256 = sha256_file(official_coverage_artifact_path),
    historical_maintenance_urls = nrow(historical),
    historical_maintenance_sha256 = sha256_file(historical_maintenance_artifact_path),
    body_attempt_ledger_sha256 = sha256_file(ledger_path),
    revisit_min_days = revisits$minimum_age_days,
    revisit_url_cap = revisits$url_cap,
    revisit_due_urls = revisits$due,
    revisit_selected_urls = revisits$selected,
    revisit_attempted_urls = revisits$attempted,
    revisit_recovered_urls = revisits$recovered,
    historical_maintenance_selected = revisits$historical_selected,
    collector_sources_sha256 = collector_sources_sha256,
    started_at = live_retry_started_at,
    completed_at = iso8601_utc(),
    fulltext_rds_sha256 = sha256_file(full_rds),
    fulltext_csv_sha256 = sha256_file(full_csv),
    fulltext_receipts = nrow(materialized$fulltext),
    usable_article_member_rows = nrow(materialized$articles),
    unique_host_articles = length(unique(canonical_url(materialized$host_articles$url))),
    mention_relationships = nrow(materialized$article_mentions),
    failed_bodies = nrow(failures),
    exhausted_network_body_failures = sum(grepl("after_network_retry$", status)),
    rejected_short_mention_slices = sum(status == "mention_slice_under_20_words"),
    retry_source_run = run_receipt_metrics$retry_source_run,
    terminal_nonarticle_urls = run_receipt_metrics$terminal_nonarticle_urls,
    retryable_body_candidates = run_receipt_metrics$retryable_body_candidates,
    recovered_bodies = run_receipt_metrics$recovered_bodies
  )
  temp <- paste0(retry_receipt_path, ".part")
  jsonlite::write_json(payload, temp, auto_unbox = TRUE, pretty = TRUE, na = "null")
  promote_with_backup(temp, retry_receipt_path)
  invisible(payload)
}

load_valid_retry_receipt <- function() {
  if (!file.exists(retry_receipt_path))
    stop("score-only requires a completed live-network retry receipt")
  receipt <- jsonlite::fromJSON(retry_receipt_path, simplifyVector = TRUE)
  full_rds <- file.path(run_root, "press_delta_fulltext.rds")
  full_csv <- file.path(run_root, "press_delta_fulltext.csv")
  if (!identical(receipt$mode, "live_network_retry") ||
      identical(receipt$status, "PARTIAL") || isTRUE(receipt$pending_body_urls > 0) ||
      !identical(receipt$fulltext_rds_sha256, sha256_file(full_rds)) ||
      !identical(receipt$fulltext_csv_sha256, sha256_file(full_csv))) {
    stop("live-network retry receipt does not match the fulltext artifacts")
  }
  validate_official_coverage_artifacts(receipt)
  receipt
}

materialize_press_outputs <- function(full, failure_status, rewrite_fulltext = TRUE) {
  if (!nrow(full)) {
    for (name in c("url", "bioguide", "body", "source", "url_key"))
      if (!name %in% names(full)) full[[name]] <- character(0)
    if (!"date_final" %in% names(full)) full$date_final <- as.Date(character(0))
  }
  if (nrow(full)) {
    full$date_final <- as.Date(full$date_final, origin = "1970-01-01")
    full$url_key <- canonical_url(full$url)
    member_key <- toupper(trimws(ifelse(is.na(full$bioguide), "", full$bioguide)))
    full$turn_key <- paste(full$url_key, member_key, sep = "#")
    # One document can legitimately yield several member-attributed turns.
    # Collapse only duplicate document/member pairs, never distinct members.
    full <- full[nzchar(full$url_key) & !duplicated(full$turn_key), , drop = FALSE]
  }
  body <- ifelse(is.na(full$body), "", trimws(full$body))
  word_count <- ifelse(
    nzchar(body),
    lengths(gregexpr("\\S+", body)),
    0L
  )
  full$body_word_count <- word_count
  # A short nonempty string can still be a consent wall or navigation shell.
  # Match the production collector's minimum usable-article threshold.
  articles <- full[word_count >= 20L, , drop = FALSE]
  failures <- full[word_count < 20L, , drop = FALSE]
  articles$collection_status <- rep("usable_article", nrow(articles))
  mention_failure <- !is.na(failures$source) & failures$source == "OFFICIAL_MENTION"
  failures$collection_status <- ifelse(
    mention_failure,
    "mention_slice_under_20_words",
    ifelse(
      word_count[word_count < 20L] == 0L,
      paste0("body_unavailable_", failure_status),
      paste0("body_shell_under_20_words_", failure_status)
    )
  )
  # Budget-deferred URLs were not exhaustively tried. Keep that distinction in
  # the per-row artifact as well as the completion receipt.
  pending_keys <- if (exists("PRESS_BACKLOG_CONTEXT", inherits = TRUE))
    canonical_url(press_backlog_snapshot()$url) else character(0)
  failures$collection_status[!mention_failure & failures$url_key %in% pending_keys] <-
    "body_pending_network_attempt"
  is_mention <- if ("source" %in% names(articles))
    !is.na(articles$source) & articles$source == "OFFICIAL_MENTION"
  else rep(FALSE, nrow(articles))
  article_mentions <- articles[is_mention, , drop = FALSE]
  host_articles <- articles[!is_mention, , drop = FALSE]
  upper_date <- Sys.Date()
  dated <- articles[
    !is.na(articles$date_final) &
      articles$date_final >= DATE_FLOOR &
      articles$date_final <= upper_date,
    ,
    drop = FALSE
  ]
  unresolved <- articles[is.na(articles$date_final), , drop = FALSE]
  out_of_range <- articles[
    !is.na(articles$date_final) &
      (articles$date_final < DATE_FLOOR | articles$date_final > upper_date),
    ,
    drop = FALSE
  ]

  outputs <- list(
    fulltext = full,
    articles = articles,
    host_articles = host_articles,
    article_mentions = article_mentions,
    failed_bodies = failures,
    dated = dated,
    undated = unresolved,
    out_of_range = out_of_range
  )
  for (name in names(outputs)) {
    if (!rewrite_fulltext && name == "fulltext") next
    write_rds_atomic(outputs[[name]], file.path(run_root, paste0("press_delta_", name, ".rds")))
    write_csv_atomic(outputs[[name]], file.path(run_root, paste0("press_delta_", name, ".csv")))
  }
  outputs
}

future::plan(future::multisession, workers = FUTURE_WORKERS)
progressr::handlers(global = TRUE)

if (!is.na(retry_from)) {
  source_root <- normalizePath(retry_from, winslash = "/", mustWork = TRUE)
  if (identical(normalizePath(run_root, winslash = "/", mustWork = TRUE), source_root))
    stop("historical body recovery requires a separate target run")
  if (file.exists(retry_receipt_path)) {
    previous_receipt <- jsonlite::fromJSON(retry_receipt_path, simplifyVector = FALSE)
    if (identical(previous_receipt$mode, "live_network_retry"))
      stop("historical body recovery cannot replace a fresh-inventory run")
  }
  run_receipt_metrics$historical_inventory_source <- bind_historical_retry_source(source_root)
  import_official_coverage_from_run(source_root)
  source_full_path <- file.path(source_root, "press_delta_fulltext.rds")
  if (!file.exists(source_full_path)) stop("retry source lacks press_delta_fulltext.rds: ", source_root)
  source_full <- readRDS(source_full_path)
  source_words <- ifelse(
    is.na(source_full$body) | !nzchar(trimws(source_full$body)), 0L,
    lengths(gregexpr("\\S+", source_full$body))
  )
  host_failure <- source_words < 20L &
    (is.na(source_full$source) | source_full$source != "OFFICIAL_MENTION")
  candidates <- source_full[host_failure, , drop = FALSE]
  split <- split_terminal_nonarticles(candidates)
  terminal <- split$nonarticles
  retryable <- split$articles
  retry_key <- function(frame) paste(
    canonical_url(frame$url),
    toupper(ifelse(is.na(frame$bioguide), "", frame$bioguide)),
    sep = "#"
  )
  # A same-day retry may be resumed after some URLs succeeded.  Preserve those
  # usable bodies and retry only the still-deficient keys; otherwise a later
  # transient failure could overwrite an earlier success and make the sealed
  # run regress.  The source run remains the authoritative candidate universe.
  prior_recovered <- data.frame()
  target_full_path <- file.path(run_root, "press_delta_fulltext.rds")
  if (file.exists(target_full_path) &&
      normalizePath(run_root, winslash = "/", mustWork = TRUE) != source_root) {
    target_full <- readRDS(target_full_path)
    target_words <- ifelse(
      is.na(target_full$body) | !nzchar(trimws(target_full$body)), 0L,
      lengths(gregexpr("\\S+", target_full$body))
    )
    candidate_keys <- retry_key(retryable)
    prior_recovered <- target_full[
      target_words >= 20L & retry_key(target_full) %in% candidate_keys,
      , drop = FALSE
    ]
    if (nrow(prior_recovered)) {
      prior_recovered <- prior_recovered[!duplicated(retry_key(prior_recovered)), , drop = FALSE]
      retryable <- retryable[
        !(retry_key(retryable) %in% retry_key(prior_recovered)),
        , drop = FALSE
      ]
      message("Targeted retry resume: preserved ", nrow(prior_recovered),
              " previously recovered body/bodies; ", nrow(retryable),
              " deficient key(s) remain.")
    }
  }
  write_rds_atomic(terminal, file.path(run_root, "press_delta_terminal_nonarticles.rds"))
  write_csv_atomic(terminal, file.path(run_root, "press_delta_terminal_nonarticles.csv"))

  run_receipt_metrics$retry_source_run <- basename(source_root)
  run_receipt_metrics$terminal_nonarticle_urls <- nrow(terminal)
  run_receipt_metrics$retryable_body_candidates <- nrow(retryable)
  message("Targeted retry from ", basename(source_root), ": ", nrow(candidates),
          " deficient host row(s); ", nrow(terminal), " terminal non-article; ",
          nrow(retryable), " content-addressable article URL(s) retried.")

  recovered <- data.frame()
  if (!nrow(retryable)) {
    # Establish an explicit empty attempt scope when a targeted resume already
    # recovered every candidate. This does not fetch or remove any cached body.
    invisible(stage_fulltext(retryable, force = TRUE, js_bgs = character(0)))
  }
  if (nrow(retryable)) {
    retryable <- retryable[!duplicated(paste(canonical_url(retryable$url),
                                             toupper(retryable$bioguide), sep = "#")), , drop = FALSE]
    # stage_fulltext() accepts index rows and left-joins freshly fetched detail
    # fields. Feeding its own prior fulltext output back in would create .x/.y
    # columns and hide title_detail/date_detail/body from the materializer.
    derived <- c("url_key", "title_detail", "date_detail", "body", "date_final",
                 "turn_key", "body_word_count", "collection_status")
    retry_index <- retryable[, setdiff(names(retryable), derived), drop = FALSE]
    recovered <- stage_fulltext(retry_index, force = TRUE, js_bgs = character(0))
    roster_path <- file.path(source_root, "press_roster.rds")
    members <- if (file.exists(roster_path)) readRDS(roster_path) else
      apply_member_filter(stage_roster(FALSE), filter = character(0))
    if (nrow(recovered)) recovered <- expand_official_member_turns(recovered, members)
  }
  recovered <- dplyr::bind_rows(prior_recovered, recovered)
  recovered_words <- if (nrow(recovered)) ifelse(
    is.na(recovered$body) | !nzchar(trimws(recovered$body)), 0L,
    lengths(gregexpr("\\S+", recovered$body))
  ) else integer(0)
  recovered_good <- if (nrow(recovered)) recovered[recovered_words >= 20L, , drop = FALSE] else recovered
  run_receipt_metrics$recovered_bodies <- nrow(recovered_good)

  drop_keys <- c(if (nrow(terminal)) retry_key(terminal) else character(0),
                 if (nrow(recovered)) retry_key(recovered) else character(0))
  retained <- source_full[!(retry_key(source_full) %in% drop_keys), , drop = FALSE]
  full <- dplyr::bind_rows(retained, recovered)
  # The audit contract treats every still-short host body after this forced
  # retry as an exhausted network failure.  ``retry_source_run`` in the receipt
  # already records that this was the targeted path; keep the shared terminal
  # status suffix so the intake gate can prove that no failure was silently
  # skipped.
  materialized <- materialize_press_outputs(full, "after_network_retry")
  scoring_dated <- materialized$dated[
    is.na(materialized$dated$source) | materialized$dated$source != "OFFICIAL_MENTION",
    , drop = FALSE
  ]
  scored <- if (nrow(scoring_dated)) stage_score(scoring_dated) else data.frame()
  write_rds_atomic(scored, file.path(run_root, "press_delta_scored.rds"))
  write_csv_atomic(scored, file.path(run_root, "press_delta_scored.csv"))
  completion <- write_retry_receipt(materialized)
  message("Targeted retry complete: recovered=", nrow(recovered_good),
          " failed_bodies=", nrow(materialized$failed_bodies),
          " terminal_nonarticles=", nrow(terminal),
          " status=", completion$status, " pending_body_urls=", completion$pending_body_urls)
  future::plan(future::sequential)
  quit(save = "no", status = if (completion$pending_body_urls) 3L else 0L)
}

if (score_only) {
  full_path <- file.path(run_root, "press_delta_fulltext.rds")
  if (!file.exists(full_path)) stop("press receipt artifact not found: ", full_path)
  retry_receipt <- load_valid_retry_receipt()
  materialized <- materialize_press_outputs(
    readRDS(full_path),
    "after_network_retry",
    rewrite_fulltext = FALSE
  )
  dated <- materialized$dated
  scoring_dated <- dated[
    is.na(dated$source) | dated$source != "OFFICIAL_MENTION", , drop = FALSE
  ]
  scored <- stage_score(scoring_dated)
  write_rds_atomic(scored, file.path(run_root, "press_delta_scored.rds"))
  write_csv_atomic(scored, file.path(run_root, "press_delta_scored.csv"))
  message(
    "Scratch score-only complete: receipts=", nrow(materialized$fulltext),
    " articles=", nrow(materialized$articles),
    " failed_bodies=", nrow(materialized$failed_bodies),
    " dated=", nrow(dated),
    " undated=", nrow(materialized$undated),
    " out_of_range=", nrow(materialized$out_of_range),
    " cu_relevant=", nrow(scored)
  )
  future::plan(future::sequential)
  quit(save = "no", status = 0L)
}

exit_code <- tryCatch({
  members <- apply_member_filter(stage_roster(FALSE), filter = character(0))
  if (!cache_only) retry_failed_checkpoints(members)
  discovery <- stage_discovery(members, FALSE)
  refreshed_index <- stage_index(members, discovery, FALSE)

  old_index <- utils::read.csv(
    old_index_path,
    stringsAsFactors = FALSE,
    colClasses = "character",
    check.names = FALSE
  )
  if (!"url" %in% names(old_index)) stop("production index has no url column")
  if (!"url" %in% names(refreshed_index)) stop("refreshed index has no url column")

  old_keys <- unique(canonical_url(old_index$url))
  refreshed_index$url_key <- canonical_url(refreshed_index$url)
  delta <- refreshed_index[
    nzchar(refreshed_index$url_key) & !(refreshed_index$url_key %in% old_keys),
    ,
    drop = FALSE
  ]
  delta <- delta[!duplicated(delta$url_key), , drop = FALSE]
  split <- split_terminal_nonarticles(delta)
  delta <- split$articles
  terminal_nonarticles <- split$nonarticles
  run_receipt_metrics$terminal_nonarticle_urls <- nrow(terminal_nonarticles)

  write_rds_atomic(members, file.path(run_root, "press_roster.rds"))
  write_rds_atomic(discovery, file.path(run_root, "press_discovery.rds"))
  write_rds_atomic(refreshed_index, file.path(run_root, "press_refreshed_index.rds"))
  write_csv_atomic(refreshed_index, file.path(run_root, "press_refreshed_index.csv"))
  write_rds_atomic(delta, file.path(run_root, "press_url_delta.rds"))
  write_csv_atomic(delta, file.path(run_root, "press_url_delta.csv"))
  write_rds_atomic(terminal_nonarticles,
                   file.path(run_root, "press_delta_terminal_nonarticles.rds"))
  write_csv_atomic(terminal_nonarticles,
                   file.path(run_root, "press_delta_terminal_nonarticles.csv"))

  dated_delta <- sum(!is.na(delta$date) & as.Date(delta$date) >= DATE_FLOOR)
  undated_delta <- sum(is.na(delta$date))
  message(
    "Scratch discovery complete: roster=", nrow(members),
    " discovery=", nrow(discovery),
    " refreshed_index=", nrow(refreshed_index),
    " unseen_urls=", nrow(delta),
    " terminal_nonarticles=", nrow(terminal_nonarticles),
    " dated_since_floor=", dated_delta,
    " undated=", undated_delta
  )

  if (discover_only) {
    if (discover_only) message("Discovery-only mode: no bodies fetched.")
    0L
  } else {
    if (nrow(delta) > max_delta) {
      stop(
        "delta has ", nrow(delta), " rows, above safety cap ", max_delta,
        "; inspect press_url_delta.csv and explicitly raise PRESS_REFRESH_MAX_DELTA"
      )
    }

    js_bgs <- unique(c(
      discovery$bioguide[discovery$js_render %in% TRUE],
      discovery$bioguide[discovery$scheme %in% "cdx"]
    ))
    member_full <- stage_fulltext(delta, FALSE, js_bgs)
    member_full <- expand_official_member_turns(member_full, members)
    committee_full <- tryCatch(
      stage_committees(members, FALSE),
      error = function(error) {
        message("Committee source lane failed safely: ", conditionMessage(error))
        NULL
      }
    )
    news_full <- tryCatch(
      stage_news(members, FALSE),
      error = function(error) {
        message("News source lane failed safely: ", conditionMessage(error))
        NULL
      }
    )
    full <- dplyr::bind_rows(member_full, committee_full, news_full)

    # Even an empty candidate set or an all-pre-floor cache result needs a
    # complete, hash-bound receipt. An empty result with pending attempts is
    # PARTIAL and must remain retryable.
    {
      materialized <- materialize_press_outputs(
        full,
        if (cache_only) "from_existing_receipt" else "after_network_retry"
      )
      dated <- materialized$dated

      if (nrow(dated) > 0L) {
        scoring_dated <- dated[
          is.na(dated$source) | dated$source != "OFFICIAL_MENTION", , drop = FALSE
        ]
        scored <- stage_score(scoring_dated)
      } else {
        scored <- data.frame()
      }
      write_rds_atomic(scored, file.path(run_root, "press_delta_scored.rds"))
      write_csv_atomic(scored, file.path(run_root, "press_delta_scored.csv"))
      completion <- write_retry_receipt(materialized)
      message(
        "Scratch full-text complete: receipts=", nrow(materialized$fulltext),
        " articles=", nrow(materialized$articles),
        " failed_bodies=", nrow(materialized$failed_bodies),
        " dated_since_floor=", nrow(dated),
        " undated=", nrow(materialized$undated),
        " out_of_range=", nrow(materialized$out_of_range),
        " cu_relevant=", nrow(scored),
        " status=", completion$status, " pending_body_urls=", completion$pending_body_urls
      )
      if (completion$pending_body_urls) 3L else 0L
    }
  }
}, error = function(error) {
  message("PRESS REFRESH FAILED: ", conditionMessage(error))
  2L
}, finally = {
  future::plan(future::sequential)
})

quit(save = "no", status = exit_code)

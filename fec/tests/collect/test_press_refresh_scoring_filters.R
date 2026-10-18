# Owner ruling D-219 (2026-10-15): undated releases are kept and scored, an exhausted short/empty body is
# scored on its title (body_fallback = "title"), and a pending URL is never scored from a title.  Runs the
# real materialize_press_outputs() / score_scorable_press_rows() out of refresh_press_delta.R in a private
# environment, with stage_score() stubbed.  No network, no live paths.
args <- commandArgs(trailingOnly = TRUE)
stopifnot(length(args) == 1L)
.libPaths(c(file.path(dirname(args[[1]]), "..", "..", "outputs", "r-library"), .libPaths()))
suppressMessages(library(dplyr))
producer <- new.env(parent = globalenv())
expressions <- as.list(parse(file = args[[1]]))
for (name in c("materialize_press_outputs", "score_scorable_press_rows")) {
  definition <- Filter(function(expr) is.call(expr) && identical(expr[[1]], as.name("<-")) &&
                         identical(expr[[2]], as.name(name)), expressions)
  stopifnot(length(definition) == 1L)
  eval(definition[[1]], envir = producer)
}
producer$run_root <- tempfile("scoring-filters-")
dir.create(producer$run_root)
producer$DATE_FLOOR <- as.Date("2026-06-25")
producer$canonical_url <- function(value) tolower(sub("[#?].*$", "", value))
producer$write_csv_atomic <- function(frame, path) utils::write.csv(frame, path, row.names = FALSE)
producer$write_rds_atomic <- function(frame, path) saveRDS(frame, path)
producer$PRESS_BACKLOG_CONTEXT <- list()
producer$press_backlog_snapshot <- function() data.frame(url = "https://example.gov/pending")
producer$staged <- NULL
producer$stage_score <- function(full) {
  assign("staged", full, envir = producer)
  data.frame(url = full$url, title = full$title, stringsAsFactors = FALSE)
}

shell <- "too short to be a body"
body <- paste(rep("Complete official article body evidence.", 5L), collapse = " ")
full <- data.frame(
  bioguide = sprintf("B%02d", 1:10),
  url = c("https://example.gov/dated", "https://example.gov/undated", "https://example.gov/prefloor",
          "https://example.gov/shell", "https://example.gov/empty-undated", "https://example.gov/shell-prefloor",
          "https://example.gov/pending", "https://example.gov/mention-short", "https://example.gov/no-title",
          "https://example.gov/mention-usable"),
  title = c("Dated", "Undated", "Pre-floor", "Shell title", "Empty undated title", "Shell pre-floor", "Pending title",
            "Mention title", "", "Mention usable"),
  body = c(body, body, body, shell, NA, shell, NA, shell, shell, body),
  source = c(rep(NA_character_, 7L), "OFFICIAL_MENTION", NA_character_, "OFFICIAL_MENTION"),
  date_final = as.Date(c("2026-07-01", NA, "2026-01-01", "2026-07-02", NA, "2026-01-02", NA, "2026-07-03",
                         "2026-07-04", "2026-07-05")),
  stringsAsFactors = FALSE
)
materialized <- producer$materialize_press_outputs(full, "after_network_retry")

# The audited partitions are exactly what they were: undated stays in its artifact, failures stay failures.
stopifnot(setequal(materialized$undated$url, "https://example.gov/undated"),
          setequal(materialized$dated$url, c("https://example.gov/dated", "https://example.gov/mention-usable")),
          setequal(materialized$out_of_range$url, "https://example.gov/prefloor"),
          setequal(materialized$failed_bodies$url, full$url[c(4:9)]),
          !"body_fallback" %in% names(utils::read.csv(file.path(producer$run_root, "press_delta_dated.csv"))),
          !file.exists(file.path(producer$run_root, "press_delta_scorable.csv")))
status <- setNames(materialized$failed_bodies$collection_status, materialized$failed_bodies$url)
stopifnot(identical(unname(status[["https://example.gov/pending"]]), "body_pending_network_attempt"))

# What reaches stage_score: dated + UNDATED articles, plus exhausted failures with a title (date floor on dated rows only).
scorable <- setNames(materialized$scorable$body_fallback, materialized$scorable$url)
stopifnot(setequal(names(scorable),
                   c("https://example.gov/dated", "https://example.gov/undated", "https://example.gov/mention-usable",
                     "https://example.gov/shell", "https://example.gov/empty-undated")),
          identical(unname(scorable[c("https://example.gov/dated", "https://example.gov/undated")]), c("", "")),
          identical(unname(scorable[c("https://example.gov/shell", "https://example.gov/empty-undated")]),
                    c("title", "title")))
fallback_rows <- materialized$scorable[materialized$scorable$body_fallback == "title", ]
stopifnot(all(fallback_rows$body == ""))

scored <- producer$score_scorable_press_rows(materialized$scorable)
# A mention relationship never reaches the scorer; a pending, mention-slice, pre-floor or title-less row never does either.
stopifnot(!("https://example.gov/mention-usable" %in% producer$staged$url),
          setequal(scored$url, c("https://example.gov/dated", "https://example.gov/undated",
                                 "https://example.gov/shell", "https://example.gov/empty-undated")),
          identical(setNames(scored$body_fallback, scored$url)[["https://example.gov/shell"]], "title"),
          identical(setNames(scored$body_fallback, scored$url)[["https://example.gov/undated"]], ""))

# Nothing to score is an empty frame, not an error.
empty <- producer$materialize_press_outputs(data.frame(), "after_network_retry")
stopifnot(nrow(empty$scorable) == 0L, nrow(producer$score_scorable_press_rows(empty$scorable)) == 0L)
cat("PASS: undated kept, exhausted short bodies scored on the title, pending/mention/pre-floor rows never scored\n")

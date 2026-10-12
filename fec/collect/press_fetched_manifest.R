#!/usr/bin/env Rscript

# Emit fetched-body evidence from a press-refresh run's per-member full-text
# checkpoints.
#
# A run that is killed before materialize_press_outputs() has no
# press_delta_fulltext.csv, so press_baseline_fold.py has nothing to prove that
# a URL's body was durably stored -- even though the bodies are sitting right
# there in pr_checkpoints/fulltext/<BIOGUIDE>.rds.  This reads those
# checkpoints and writes the minimal evidence CSV the fold consumes:
# url, body_word_count, source.
#
# Bodies only.  This never emits an index row, so it can never be mistaken for
# a licence to fold something that was not fetched.

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 2L) {
  stop("usage: press_fetched_manifest.R RUN_ROOT OUT_CSV [MIN_WORDS]")
}

run_root <- normalizePath(args[[1]], winslash = "/", mustWork = TRUE)
out_csv <- args[[2]]
min_words <- if (length(args) >= 3L) suppressWarnings(as.integer(args[[3]])) else 20L
if (is.na(min_words) || min_words < 1L) stop("MIN_WORDS must be a positive integer")

fulltext_dir <- file.path(run_root, "pr_checkpoints", "fulltext")
if (!dir.exists(fulltext_dir)) stop("no full-text checkpoint directory: ", fulltext_dir)

files <- list.files(fulltext_dir, pattern = "[.]rds$", full.names = TRUE)
if (!length(files)) stop("no full-text checkpoints under: ", fulltext_dir)

word_count <- function(value) {
  value <- trimws(as.character(value))
  if (!nzchar(value)) return(0L)
  length(gregexpr("\\S+", value)[[1]])
}

urls <- character(0)
counts <- integer(0)
sources <- character(0)
n_files <- 0L
n_corrupt <- 0L

for (path in files) {
  frame <- tryCatch(readRDS(path), error = function(e) NULL)
  if (is.null(frame) || !is.data.frame(frame) ||
      !all(c("url", "body") %in% names(frame))) {
    # A checkpoint truncated by a kill mid-write is skipped, not fatal: its
    # URLs simply stay unproven and get re-fetched.  Fail-safe by omission.
    if (is.null(frame)) n_corrupt <- n_corrupt + 1L
    next
  }
  n_files <- n_files + 1L
  label <- sub("[.]rds$", "", basename(path))
  for (i in seq_len(nrow(frame))) {
    url <- trimws(as.character(frame$url[[i]]))
    if (!nzchar(url) || is.na(url)) next
    raw_body <- frame$body[[i]]
    if (length(raw_body) != 1L || is.na(raw_body)) next
    n <- word_count(raw_body)
    if (n < min_words) next
    urls <- c(urls, url)
    counts <- c(counts, n)
    sources <- c(sources, paste0("MEMBER_FULLTEXT_CHECKPOINT:", label))
  }
}

manifest <- data.frame(url = urls, body_word_count = counts, source = sources,
                       stringsAsFactors = FALSE)
manifest <- manifest[!duplicated(manifest$url), , drop = FALSE]

temp <- paste0(out_csv, ".part")
utils::write.csv(manifest, temp, row.names = FALSE, na = "", fileEncoding = "UTF-8")
if (file.exists(out_csv) && !file.remove(out_csv))
  stop("cannot replace existing manifest: ", out_csv)
if (!file.rename(temp, out_csv)) stop("cannot promote manifest: ", out_csv)

cat(sprintf("checkpoints=%d corrupt_skipped=%d stored_bodies=%d min_words=%d out=%s\n",
            n_files, n_corrupt, nrow(manifest), min_words, out_csv))

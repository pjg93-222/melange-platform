#!/usr/bin/env Rscript

# Recover unusable (<20-word) rows in press_text_cache.jsonl from the full-text checkpoints produced by
# cu_press_release_scraper.R.  This is deliberately additive: the JSONL cache keeps its audit
# history, and consumers already use the latest non-empty body for a URL.

suppressPackageStartupMessages(library(jsonlite))

args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 2L) {
  stop("usage: backfill_press_text_from_rds.R PRESS_CACHE_JSONL FULLTEXT_RDS_DIR")
}

cache_path <- normalizePath(args[[1]], mustWork = TRUE)
rds_dir <- normalizePath(args[[2]], mustWork = TRUE)
canon <- function(x) sub("/+$", "", trimws(as.character(x)))

latest <- new.env(hash = TRUE, parent = emptyenv())
for (line in readLines(cache_path, warn = FALSE, encoding = "UTF-8")) {
  row <- tryCatch(fromJSON(line, simplifyVector = TRUE), error = function(e) NULL)
  if (is.null(row) || is.null(row$url) || !nzchar(canon(row$url))) next
  assign(canon(row$url), row, envir = latest)
}

needed <- new.env(hash = TRUE, parent = emptyenv())
for (url in ls(latest, all.names = TRUE)) {
  row <- get(url, envir = latest, inherits = FALSE)
  body <- if (is.null(row$text)) "" else trimws(as.character(row$text))
  if (length(strsplit(body, "\\s+")[[1]]) < 20L) assign(url, TRUE, envir = needed)
}

found <- new.env(hash = TRUE, parent = emptyenv())
files <- list.files(rds_dir, pattern = "[.]rds$", full.names = TRUE)
for (path in files) {
  x <- tryCatch(readRDS(path), error = function(e) NULL)
  if (is.null(x) || !is.data.frame(x) || !all(c("url", "body") %in% names(x))) next
  for (i in seq_len(nrow(x))) {
    url <- canon(x$url[[i]])
    raw_body <- x$body[[i]]
    if (length(raw_body) != 1L || is.na(raw_body)) next
    body <- trimws(enc2utf8(as.character(raw_body)))
    if (!nzchar(url) || !exists(url, envir = needed, inherits = FALSE) ||
        length(strsplit(body, "\\s+")[[1]]) < 20L) next
    old <- if (exists(url, envir = found, inherits = FALSE))
      get(url, envir = found, inherits = FALSE) else ""
    if (nchar(body, type = "chars") > nchar(old, type = "chars"))
      assign(url, body, envir = found)
  }
}

out <- file(cache_path, open = "ab")
on.exit(if (isOpen(out)) close(out), add = TRUE)
written <- 0L
for (url in sort(ls(found, all.names = TRUE))) {
  body <- get(url, envir = found, inherits = FALSE)
  # Preserve jsonlite's marked UTF-8 result. Calling enc2utf8() on the json class under the
  # Windows C locale truncates otherwise valid records before their closing quote/brace.
  encoded <- as.character(toJSON(
    list(url = url, text = body, n = nchar(body, type = "chars"),
         recovered_from = "cu_press_fulltext_rds"),
    auto_unbox = TRUE, null = "null", na = "null"))
  bytes <- charToRaw(encoded)
  # jsonlite can preserve malformed control bytes from old RDS strings. JSON forbids every raw
  # byte below 0x20 inside a record, so replace them before validating and appending.
  bad <- as.integer(bytes) < 32L
  if (any(bad)) bytes[bad] <- as.raw(32L)
  cleaned <- rawToChar(bytes)
  Encoding(cleaned) <- "UTF-8"
  valid <- tryCatch({ fromJSON(cleaned, simplifyVector = TRUE); TRUE },
                    error = function(e) FALSE)
  if (!valid) next
  writeBin(bytes, out); writeBin(charToRaw("\n"), out)
  written <- written + 1L
}
close(out)

unrecovered <- setdiff(ls(needed, all.names = TRUE), ls(found, all.names = TRUE))
cat(sprintf("needed=%d recovered=%d appended_valid=%d unrecovered=%d\n",
            length(ls(needed, all.names = TRUE)), length(ls(found, all.names = TRUE)), written,
            length(unrecovered)))
if (length(unrecovered)) cat(paste(sort(unrecovered), collapse = "\n"), "\n")
if (written != length(ls(found, all.names = TRUE)) || length(unrecovered)) quit(status = 2L)

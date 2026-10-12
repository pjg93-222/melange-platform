# Pure DOM fixture; no network or live checkpoints.
args <- commandArgs(trailingOnly = TRUE)
stopifnot(length(args) == 1L)
.libPaths(c(file.path(dirname(args[[1]]), "..", "..", "outputs", "r-library"), .libPaths()))
env <- new.env(parent = globalenv())
source(args[[1]], local = env)
env$attr_date <- function(value) suppressWarnings(as.Date(substr(value, 1L, 10L)))
env$extract_release <- function(pg, u) data.frame(
  url = u, title_detail = "Fixture release", date_detail = as.Date("2017-11-13"),
  body = "fixture body", stringsAsFactors = FALSE)
env$install_press_release_date_precedence(env)

page <- xml2::read_html(paste0(
  "<html><head><meta property='article:published_time' content='2026-09-10T09:00:00-04:00'></head>",
  "<body><time datetime='2017-11-13'>navigation timestamp</time></body></html>"))
result <- env$extract_release(page, "https://larsen.house.gov/news/documentsingle.aspx?DocumentID=1")
stopifnot(result$date_detail == as.Date("2026-09-10"))

without_meta <- xml2::read_html("<html><body><time datetime='2017-11-13'></time></body></html>")
stopifnot(env$extract_release(without_meta, "https://example.gov/release")$date_detail == as.Date("2017-11-13"))

conflicting <- xml2::read_html(paste0(
  "<html><head>",
  "<meta property='article:published_time' content='2026-09-10T09:00:00-04:00'>",
  "<meta property='article:published_time' content='2026-09-11T09:00:00-04:00'>",
  "</head><body></body></html>"))
stopifnot(inherits(try(env$extract_release(conflicting, "https://example.gov/conflict"), silent = TRUE),
                     "try-error"))
wrapped <- env$extract_release
env$install_press_release_date_precedence(env)
stopifnot(identical(wrapped, env$extract_release))
cat("PASS: article publication metadata precedes unrelated time elements and conflicts fail closed\n")

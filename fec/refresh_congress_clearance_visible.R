#!/usr/bin/env Rscript

suppressPackageStartupMessages({
  library(chromote)
  library(jsonlite)
})

args <- commandArgs(trailingOnly = TRUE)
probe_url <- if (length(args)) args[[1L]] else {
  "https://www.congress.gov/bill/116th-congress/house-bill/6364/text"
}

chrome <- Chromote$new(
  browser = ChromeRemote$new(host = "127.0.0.1", port = 9222)
)
targets <- chrome$Target$getTargets()$targetInfos
congress_targets <- Filter(
  function(target) {
    identical(target$type, "page") &&
      grepl("https://www.congress.gov/", target$url, fixed = TRUE)
  },
  targets
)
if (!length(congress_targets)) stop("No visible Congress.gov Chrome tab is available")

preferred <- Filter(
  function(target) grepl("/text", target$url, fixed = TRUE),
  congress_targets
)
target <- if (length(preferred)) preferred[[1L]] else congress_targets[[1L]]
session <- chrome$new_session(targetId = target$targetId)
session$default_timeout <- 120
invisible(try(session$Page$bringToFront(), silent = TRUE))
session$Page$navigate(probe_url, wait_ = FALSE)

deadline <- Sys.time() + 75
reloaded <- FALSE
cleared <- FALSE
last_state <- list(title = "", url = "", marker = FALSE, challenge = FALSE)
while (Sys.time() < deadline) {
  state <- tryCatch({
    encoded <- session$Runtime$evaluate(
      "JSON.stringify({title:document.title,url:location.href,body:(document.querySelector('#main, main')?.innerText||'').slice(0,3000)})"
    )$result$value
    fromJSON(encoded, simplifyVector = TRUE)
  }, error = function(error) NULL)
  if (!is.null(state)) {
    challenge <- grepl(
      "just a moment|checking your browser|attention required|verifying you are human|cf-browser-verification|too many requests",
      paste(state$title, state$body),
      ignore.case = TRUE
    )
    marker <- grepl("Shown Here:|Amendment Text:", state$body)
    last_state <- list(
      title = state$title,
      url = state$url,
      marker = marker,
      challenge = challenge
    )
    if (!challenge && identical(state$url, probe_url) && marker) {
      cleared <- TRUE
      break
    }
  }
  if (!reloaded && Sys.time() > deadline - 55) {
    invisible(try(session$Page$reload(ignoreCache = TRUE, wait_ = FALSE), silent = TRUE))
    reloaded <- TRUE
  }
  Sys.sleep(0.5)
}

cookies <- tryCatch(session$Network$getAllCookies()$cookies, error = function(error) list())
has_clearance <- any(vapply(
  Filter(function(cookie) grepl("congress\\.gov", cookie$domain), cookies),
  function(cookie) identical(cookie$name, "cf_clearance"),
  TRUE
))
rm(cookies)

result <- list(
  schema = "congress-visible-clearance-refresh-v1",
  status = if (cleared && has_clearance) "PASS" else "BLOCKED",
  probe_url = probe_url,
  reused_visible_tab = TRUE,
  target_id = target$targetId,
  exact_url = identical(last_state$url, probe_url),
  official_marker_present = isTRUE(last_state$marker),
  challenge_present = isTRUE(last_state$challenge),
  cf_clearance_present = has_clearance,
  cookie_values_persisted = FALSE
)
cat(toJSON(result, auto_unbox = TRUE, pretty = TRUE), "\n")
quit(status = if (identical(result$status, "PASS")) 0L else 2L)

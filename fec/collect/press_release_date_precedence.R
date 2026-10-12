# Workspace-owned correction for upstream detail-page date precedence. Some
# member templates place a site-navigation <time> before the article while
# exposing the authoritative publication date in article:published_time.

press_article_published_date <- function(page) {
  nodes <- rvest::html_elements(page, "meta[property='article:published_time']")
  if (!length(nodes)) return(as.Date(NA))
  values <- rvest::html_attr(nodes, "content")
  dates <- vapply(values, function(value) {
    parsed <- tryCatch(attr_date(value), error = function(error) as.Date(NA))
    as.character(parsed)
  }, character(1))
  dates <- unique(dates[!is.na(dates) & nzchar(dates)])
  if (length(dates) > 1L)
    stop("conflicting article:published_time metadata on official press page")
  if (!length(dates)) as.Date(NA) else as.Date(dates)
}

install_press_release_date_precedence <- function(envir = .GlobalEnv) {
  original <- get("extract_release", envir = envir, inherits = FALSE)
  if (isTRUE(attr(original, "melange_press_date_precedence"))) return(invisible(NULL))
  corrected <- function(pg, u) {
    published <- press_article_published_date(pg)
    result <- original(pg, u)
    if (!is.data.frame(result) || nrow(result) != 1L || !"date_detail" %in% names(result))
      stop("upstream press detail extractor returned an invalid row")
    if (!is.na(published)) result$date_detail <- published
    result
  }
  attr(corrected, "melange_press_date_precedence") <- TRUE
  assign("extract_release", corrected, envir = envir)
  invisible(NULL)
}

# Official dated-listing adapter for congressional sites whose generic
# discovery path falls back to large, undated Wayback inventories.  The live
# listing is the coverage authority for the active date window; archived URLs
# remain a separate historical-maintenance concern and cannot block a daily
# current-period publication.

PRESS_OFFICIAL_DATED_MEMBERS <- data.frame(
  bioguide = c("D000563", "L000560", "H001085"),
  source_kind = c("durbin_official_pagenum", "larsen_official_documentquery",
                  "houlahan_official_documentquery"),
  origin = c("https://www.durbin.senate.gov", "https://larsen.house.gov",
             "https://houlahan.house.gov"),
  max_pages = c(550L, 260L, 260L),
  stringsAsFactors = FALSE
)

PRESS_OFFICIAL_LARSEN_RSS <- "https://larsen.house.gov/news/rss.aspx"

press_official_empty <- function() data.frame(
  bioguide = character(0), title = character(0), url = character(0),
  date = as.Date(character(0)), title_src = character(0),
  press_official_listing = logical(0), press_official_source_kind = character(0),
  press_official_listing_endpoint = character(0),
  press_official_listing_page = integer(0),
  press_official_listing_row_sha256 = character(0),
  press_official_listing_coverage_sha256 = character(0),
  press_official_pages_fetched = integer(0),
  press_official_rows_examined = integer(0),
  press_official_current_rows = integer(0),
  press_official_boundary_page = integer(0),
  press_official_boundary_max_date = character(0),
  press_official_boundary_min_date = character(0),
  press_official_rss_endpoint = character(0),
  press_official_rss_response_sha256 = character(0),
  press_official_rss_items = integer(0),
  press_official_rss_intersections = integer(0),
  press_official_body_source = character(0),
  press_official_body_sha256 = character(0),
  press_official_body = character(0), stringsAsFactors = FALSE
)

press_official_squish <- function(value) {
  value <- as.character(value)
  value[is.na(value)] <- ""
  # Windows' C locale does not classify a no-break space as [[:space:]].
  # Congressional CMS feeds use both forms as layout whitespace in titles.
  value <- gsub(intToUtf8(0x00A0), " ", value, fixed = TRUE)
  value <- gsub(intToUtf8(0x202F), " ", value, fixed = TRUE)
  gsub("[[:space:]]+", " ", trimws(value))
}

press_official_sha256 <- function(value) {
  if (!requireNamespace("digest", quietly = TRUE))
    stop("digest package is required for official press evidence")
  digest::digest(paste(as.character(value), collapse = "\n"),
                 algo = "sha256", serialize = FALSE)
}

press_official_url_key <- function(value) {
  value <- trimws(as.character(value))
  if (anyNA(value) || any(!grepl("^https?://[^/[:space:]]+", value,
                                ignore.case = TRUE)))
    stop("invalid official press URL identity")
  tolower(sub("/$", "", sub("^https?://(www\\.)?", "",
    sub("#.*$", "", value), ignore.case = TRUE)))
}

press_official_host <- function(value) {
  tolower(sub("^www\\.", "", sub("^https?://([^/]+).*$", "\\1", value)))
}

press_official_same_host <- function(left, right) {
  length(left) == 1L && length(right) == 1L &&
    identical(press_official_host(left), press_official_host(right))
}

press_official_strict_iso_date <- function(value, label = "publication date") {
  value <- as.character(value)
  if (length(value) != 1L || is.na(value) ||
      !grepl("^[0-9]{4}-[0-9]{2}-[0-9]{2}$", value))
    stop("invalid official press ", label, ": ", paste(value, collapse = ","))
  parsed <- suppressWarnings(as.Date(value, format = "%Y-%m-%d"))
  if (is.na(parsed) || as.character(parsed) != value)
    stop("invalid official press ", label, ": ", value)
  parsed
}

press_official_english_date <- function(value) {
  value <- press_official_squish(value)
  match <- regexec(
    "^(January|February|March|April|May|June|July|August|September|October|November|December) ([0-9]{1,2}), ([0-9]{4})$",
    value, perl = TRUE
  )
  parts <- regmatches(value, match)[[1L]]
  if (length(parts) != 4L) stop("invalid Durbin listing date: ", value)
  month <- match(parts[[2L]], month.name)
  press_official_strict_iso_date(sprintf("%04d-%02d-%02d",
    as.integer(parts[[4L]]), month, as.integer(parts[[3L]])), "Durbin listing date")
}

press_official_rfc_date <- function(value) {
  value <- press_official_squish(value)
  match <- regexec(
    "^(Mon|Tue|Wed|Thu|Fri|Sat|Sun), ([0-9]{2}) (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) ([0-9]{4}) [0-9]{2}:[0-9]{2}:[0-9]{2} GMT$",
    value, perl = TRUE
  )
  parts <- regmatches(value, match)[[1L]]
  if (length(parts) != 5L) stop("invalid Larsen RSS publication date: ", value)
  month <- match(parts[[4L]], month.abb)
  press_official_strict_iso_date(sprintf("%04d-%02d-%02d",
    as.integer(parts[[5L]]), month, as.integer(parts[[3L]])), "Larsen RSS date")
}

press_official_default_request <- function(url) {
  response <- NULL
  for (attempt in seq_len(3L)) {
    response <- tryCatch(
      httr::GET(url, httr::user_agent(CHROME_UA), httr::timeout(HTTP_TIMEOUT),
                httr::add_headers(Accept = "text/html,application/xhtml+xml,application/xml,text/xml")),
      error = function(error) NULL
    )
    if (!is.null(response) && httr::status_code(response) == 200L) {
      return(list(
        status = 200L, headers = httr::headers(response),
        text = httr::content(response, as = "text", encoding = "UTF-8"),
        url = response$url
      ))
    }
    if (attempt < 3L) Sys.sleep(1.5 * attempt)
  }
  status <- if (is.null(response)) "network error" else httr::status_code(response)
  stop("official dated press endpoint unavailable (", status, "): ", url)
}

press_official_response_text <- function(response, endpoint, origin) {
  if (!is.list(response) || length(response$status) != 1L ||
      !identical(as.integer(response$status), 200L) ||
      length(response$text) != 1L || is.na(response$text) || !nzchar(response$text))
    stop("invalid official dated press response: ", endpoint)
  final_url <- if (is.null(response$url)) endpoint else as.character(response$url)
  if (length(final_url) != 1L || !press_official_same_host(final_url, origin))
    stop("official dated press response crossed host boundary: ", endpoint)
  as.character(response$text)
}

press_official_response_url <- function(response, endpoint) {
  if (is.null(response$url)) endpoint else as.character(response$url)
}

press_official_listing_endpoint <- function(source_kind, page) {
  if (length(page) != 1L || is.na(page) || page < 1L || page != floor(page))
    stop("invalid official press page number")
  switch(source_kind,
    durbin_official_pagenum = paste0(
      "https://www.durbin.senate.gov/newsroom/press-releases?PageNum_rs=", page),
    larsen_official_documentquery = paste0(
      "https://larsen.house.gov/news/documentquery.aspx?DocumentTypeID=27&Page=", page),
    houlahan_official_documentquery = paste0(
      "https://houlahan.house.gov/press/documentquery.aspx?DocumentTypeID=27&Page=", page),
    stop("unknown official dated press source: ", source_kind)
  )
}

press_official_validate_article_url <- function(url, source_kind, origin) {
  if (!press_official_same_host(url, origin))
    stop("official press listing article crosses host boundary: ", url)
  valid <- switch(source_kind,
    durbin_official_pagenum = grepl(
      "^https?://(www\\.)?durbin\\.senate\\.gov/newsroom/press-releases/.+",
      url, ignore.case = TRUE, perl = TRUE),
    larsen_official_documentquery = grepl(
      "^https?://(www\\.)?larsen\\.house\\.gov/news/documentsingle\\.aspx\\?DocumentID=[0-9]+$",
      url, ignore.case = TRUE, perl = TRUE),
    houlahan_official_documentquery = grepl(
      "^https?://(www\\.)?houlahan\\.house\\.gov/news/documentsingle\\.aspx\\?DocumentID=[1-9][0-9]*$",
      url, ignore.case = TRUE, perl = TRUE),
    FALSE
  )
  if (!isTRUE(valid)) stop("invalid official press listing article identity: ", url)
  invisible(TRUE)
}

press_official_parse_durbin_page <- function(text, endpoint, origin) {
  page <- tryCatch(xml2::read_html(text), error = function(error) NULL)
  if (is.null(page)) stop("unparseable Durbin official listing: ", endpoint)
  dates <- rvest::html_elements(page, "#press > span.date.black")
  links <- rvest::html_elements(page, "#press > h2.title a[href]")
  if (!length(dates) || length(dates) != length(links))
    stop("Durbin official listing date/link cardinality changed: ", endpoint)
  urls <- xml2::url_absolute(rvest::html_attr(links, "href"), endpoint)
  invisible(lapply(urls, press_official_validate_article_url,
                   source_kind = "durbin_official_pagenum", origin = origin))
  titles <- press_official_squish(rvest::html_text2(links))
  if (any(!nzchar(titles))) stop("Durbin official listing has an empty title: ", endpoint)
  rows <- data.frame(
    title = titles, url = urls,
    date = as.Date(vapply(rvest::html_text2(dates), function(value)
      as.character(press_official_english_date(value)), character(1L))),
    stringsAsFactors = FALSE
  )
  next_node <- rvest::html_element(page, "li.next a[href]")
  next_url <- if (inherits(next_node, "xml_missing")) NA_character_
              else xml2::url_absolute(rvest::html_attr(next_node, "href"), endpoint)
  list(rows = rows, next_url = next_url)
}

press_official_parse_larsen_page <- function(text, endpoint, origin) {
  page <- tryCatch(xml2::read_html(text), error = function(error) NULL)
  if (is.null(page)) stop("unparseable Larsen official listing: ", endpoint)
  articles <- rvest::html_elements(page, "article")
  rows <- list()
  for (article in articles) {
    link <- rvest::html_elements(article, "h2.newsie-titler a[href]")
    date <- rvest::html_elements(article, "time[datetime]")
    if (!length(link) && !length(date)) next
    if (length(link) != 1L || length(date) != 1L)
      stop("Larsen official listing article identity/date changed: ", endpoint)
    url <- xml2::url_absolute(rvest::html_attr(link, "href"), endpoint)
    press_official_validate_article_url(url, "larsen_official_documentquery", origin)
    title <- press_official_squish(rvest::html_text2(link))
    if (!nzchar(title)) stop("Larsen official listing has an empty title: ", endpoint)
    date_text <- substr(rvest::html_attr(date, "datetime"), 1L, 10L)
    rows[[length(rows) + 1L]] <- data.frame(
      title = title, url = url,
      date = press_official_strict_iso_date(date_text, "Larsen listing date"),
      stringsAsFactors = FALSE
    )
  }
  if (!length(rows)) stop("Larsen official listing returned no articles: ", endpoint)
  next_node <- rvest::html_element(page, "#ctl00_ctl19_LinkNext2[href]")
  next_url <- if (inherits(next_node, "xml_missing")) NA_character_
              else xml2::url_absolute(rvest::html_attr(next_node, "href"), endpoint)
  list(rows = dplyr::bind_rows(rows), next_url = next_url)
}

press_official_parse_houlahan_page <- function(text, endpoint, origin, request, today) {
  # The heading hrefs contain leading spaces. Generic url_absolute() resolves
  # those as a different host/path, then retains only the Newsletter Archive.
  # The visible listing dates omit years, so exact dates come from each
  # identity-checked official detail's article:published_time metadata.
  page <- tryCatch(xml2::read_html(text), error = function(error) NULL)
  if (is.null(page)) stop("unparseable Houlahan official listing: ", endpoint)
  kind <- "houlahan_official_documentquery"
  number <- suppressWarnings(as.integer(sub("^.*[?&]Page=", "", endpoint)))
  if (is.na(number) || !identical(endpoint, press_official_listing_endpoint(kind, number)))
    stop("Houlahan listing endpoint has the wrong document filter or page")
  heading <- rvest::html_elements(page, "#ctl00_PageLink")
  filter <- rvest::html_elements(page, "input#DocumentTypeID")
  container <- rvest::html_elements(page, "#newsdoclist")
  if (length(heading) != 1L ||
      !identical(press_official_squish(rvest::html_text2(heading)), "Press Releases") ||
      length(filter) != 1L || !identical(rvest::html_attr(filter, "value"), "27") ||
      length(container) != 1L)
    stop("Houlahan listing identity/structure changed: ", endpoint)
  next_nodes <- rvest::html_elements(page, "#ctl00_ctl19_LinkNext2[href]")
  previous_nodes <- rvest::html_elements(page, "#ctl00_ctl19_LinkPrev2[href]")
  if (length(next_nodes) > 1L || length(previous_nodes) > 1L)
    stop("Houlahan listing has ambiguous pagination")
  next_url <- if (!length(next_nodes)) NA_character_ else xml2::url_absolute(
    trimws(rvest::html_attr(next_nodes, "href")), endpoint)
  if (number > 1L) {
    previous <- if (!length(previous_nodes)) "" else xml2::url_absolute(
      trimws(rvest::html_attr(previous_nodes, "href")), endpoint)
    if (!identical(tolower(previous), tolower(press_official_listing_endpoint(kind, number - 1L))))
      stop("Houlahan previous-page identity is not contiguous")
  } else if (length(previous_nodes)) stop("Houlahan first listing page has a previous page")
  cards <- rvest::html_elements(container, ".p-body.newsbody")
  hrefs <- trimws(rvest::html_attr(rvest::html_elements(container, "a[href]"), "href"))
  document_urls <- unique(xml2::url_absolute(hrefs[grepl("documentsingle", hrefs,
                                                          ignore.case = TRUE)], endpoint))
  if (!length(cards)) {
    marker <- rvest::html_elements(container, "span.errormsg")
    # An empty result must be the intact CMS listing, not missing markup or an
    # error response. The caller reached this page through a validated Next.
    if (number <= 1L || length(marker) != 1L || length(next_nodes) || length(document_urls) ||
        length(xml2::xml_children(container)) != 1L ||
        length(xml2::xml_children(marker)) != 0L ||
        nzchar(press_official_squish(rvest::html_text2(container))))
      stop("Houlahan empty listing lacks a terminal CMS result proof")
    return(list(rows = data.frame(), next_url = NA_character_, empty_terminal = TRUE,
                detail_evidence = list()))
  }
  if (length(cards) != 10L)
    stop("Houlahan official listing must contain ten distinct document cards")
  invisible(lapply(document_urls, press_official_validate_article_url,
                   source_kind = kind, origin = origin))
  if (length(document_urls) != 10L)
    stop("Houlahan listing has unaccounted or duplicate document links")
  rows <- list(); details <- list(); seen <- character(0)
  for (card in cards) {
    link <- rvest::html_elements(card, ".press-heading strong.title a[href]")
    label <- rvest::html_elements(card, ".press-heading .date")
    if (length(link) != 1L || length(label) != 1L)
      stop("Houlahan listing card lacks a unique title/date/link")
    url <- xml2::url_absolute(trimws(rvest::html_attr(link, "href")), endpoint)
    press_official_validate_article_url(url, kind, origin)
    key <- press_official_url_key(url)
    if (key %in% seen) stop("Houlahan listing repeats a DocumentID")
    seen <- c(seen, key)
    title <- press_official_squish(rvest::html_text2(link))
    if (!nzchar(title)) stop("Houlahan listing has an empty title")
    response <- request(url)
    detail_text <- press_official_response_text(response, url, origin)
    if (!identical(press_official_url_key(press_official_response_url(response, url)), key))
      stop("Houlahan detail response changed the requested DocumentID")
    detail <- tryCatch(xml2::read_html(detail_text), error = function(error) NULL)
    if (is.null(detail)) stop("unparseable Houlahan official detail")
    detail_titles <- unique(press_official_squish(rvest::html_attr(
      rvest::html_elements(detail, "meta[property='og:title']"), "content")))
    if (length(detail_titles) != 1L || !identical(detail_titles, title))
      stop("Houlahan official detail title disagrees with listing")
    day <- press_official_meta_date(detail, today)
    if (is.na(day)) stop("Houlahan official detail lacks an exact publication date")
    listing_day <- press_official_squish(rvest::html_text2(label))
    parts <- regmatches(listing_day, regexec("^([A-Za-z]+) ([0-9]{1,2})$", listing_day))[[1L]]
    if (length(parts) != 3L || !parts[[2L]] %in% month.name ||
        as.integer(format(day, "%m")) != match(parts[[2L]], month.name) ||
        as.integer(format(day, "%d")) != as.integer(parts[[3L]]))
      stop("Houlahan listing date disagrees with official detail")
    bodies <- rvest::html_elements(detail, ".newsbody .bodycopy")
    body <- if (length(bodies) == 1L) press_official_plain_body(as.character(bodies)) else ""
    usable <- nchar(body) >= 200L && length(strsplit(body, "[[:space:]]+")[[1L]]) >= 20L
    if (!usable) body <- ""
    response_sha <- press_official_sha256(detail_text)
    rows[[length(rows) + 1L]] <- data.frame(
      title = title, url = url, date = day,
      press_official_detail_response_sha256 = response_sha,
      press_official_body = body,
      press_official_body_source = if (nzchar(body)) "official_documentquery_detail" else "",
      press_official_body_sha256 = if (nzchar(body)) press_official_sha256(body) else "",
      stringsAsFactors = FALSE)
    details[[length(details) + 1L]] <- list(endpoint = url,
      final_url = press_official_response_url(response, url), raw_response_sha256 = response_sha,
      item_count = 1L, publication_date = as.character(day),
      body_sha256 = if (nzchar(body)) press_official_sha256(body) else "")
  }
  if (!setequal(press_official_url_key(document_urls), seen))
    stop("Houlahan listing document links do not match its parsed cards")
  list(rows = dplyr::bind_rows(rows), next_url = next_url,
       empty_terminal = FALSE, detail_evidence = details)
}

press_official_validate_next <- function(next_url, source_kind, page, origin) {
  if (length(next_url) != 1L || is.na(next_url) || !nzchar(next_url) ||
      !press_official_same_host(next_url, origin))
    stop("official press pagination ended before the pre-floor boundary: page ", page)
  expected <- press_official_listing_endpoint(source_kind, page + 1L)
  # Durbin's live template appends an empty `&`; permit only that harmless
  # delimiter. Any changed filter, duplicate page key, skip, or extra query
  # value breaks the exact continuity proof.
  observed <- sub("[?&]+$", "", sub("#.*$", "", next_url))
  valid <- identical(tolower(observed), tolower(expected))
  if (!isTRUE(valid)) stop("official press pagination is not contiguous after page ", page)
  invisible(TRUE)
}

press_official_collect_member <- function(config, floor, request, today = Sys.Date()) {
  floor <- press_official_strict_iso_date(as.character(floor), "collection floor")
  today <- press_official_strict_iso_date(as.character(today), "collection ceiling")
  pages <- list(); request_evidence <- list(); detail_evidence <- list(); previous_oldest <- NULL
  boundary <- NULL
  for (number in seq_len(as.integer(config$max_pages[[1L]]))) {
    endpoint <- press_official_listing_endpoint(config$source_kind[[1L]], number)
    response <- request(endpoint)
    text <- press_official_response_text(response, endpoint, config$origin[[1L]])
    if (identical(config$source_kind[[1L]], "houlahan_official_documentquery") &&
        !identical(press_official_url_key(press_official_response_url(response, endpoint)),
                   press_official_url_key(endpoint)))
      stop("Houlahan listing response changed the requested filter or page")
    parsed <- switch(config$source_kind[[1L]],
      durbin_official_pagenum = press_official_parse_durbin_page(
        text, endpoint, config$origin[[1L]]),
      larsen_official_documentquery = press_official_parse_larsen_page(
        text, endpoint, config$origin[[1L]]),
      houlahan_official_documentquery = press_official_parse_houlahan_page(
        text, endpoint, config$origin[[1L]], request, today),
      stop("unknown official dated press source: ", config$source_kind[[1L]])
    )
    rows <- parsed$rows
    if (isTRUE(parsed$empty_terminal)) {
      if (!length(pages)) stop("official press empty boundary has no preceding inventory")
      request_evidence[[number]] <- list(page = number, endpoint = endpoint,
        final_url = press_official_response_url(response, endpoint),
        raw_response_sha256 = press_official_sha256(text), item_count = 0L,
        newest_date = "", oldest_date = "", next_endpoint = "")
      boundary <- list(page = number, max_date = "", min_date = "", empty = TRUE)
      break
    }
    if (length(parsed$detail_evidence))
      detail_evidence <- c(detail_evidence, parsed$detail_evidence)
    if (!nrow(rows) || anyNA(rows$date) || any(rows$date < as.Date("1900-01-01")) ||
        any(rows$date > today))
      stop("official press listing has an invalid publication-date range: ", endpoint)
    if (any(diff(as.numeric(rows$date)) > 0))
      stop("official press listing is not descending within page ", number)
    if (!is.null(previous_oldest) && max(rows$date) > previous_oldest)
      stop("official press listing is not globally descending at page ", number)
    keys <- press_official_url_key(rows$url)
    if (anyDuplicated(keys)) stop("official press listing repeats a URL on page ", number)
    if (length(pages)) {
      prior_keys <- press_official_url_key(dplyr::bind_rows(pages)$url)
      if (any(keys %in% prior_keys))
        stop("official press listing repeats a URL across pages at page ", number)
    }
    rows$bioguide <- config$bioguide[[1L]]
    rows$title_src <- "official_dated_listing"
    rows$press_official_listing_endpoint <- endpoint
    rows$press_official_listing_page <- number
    rows$press_official_listing_row_sha256 <- vapply(seq_len(nrow(rows)), function(row)
      press_official_sha256(c(config$bioguide[[1L]], config$source_kind[[1L]],
        endpoint, number, as.character(rows$date[[row]]),
        press_official_url_key(rows$url[[row]]), rows$title[[row]],
        if ("press_official_detail_response_sha256" %in% names(rows))
          rows$press_official_detail_response_sha256[[row]] else "")), character(1L))
    pages[[number]] <- rows
    request_evidence[[number]] <- list(
      page = number, endpoint = endpoint,
      final_url = press_official_response_url(response, endpoint),
      raw_response_sha256 = press_official_sha256(text),
      item_count = nrow(rows), newest_date = as.character(max(rows$date)),
      oldest_date = as.character(min(rows$date)),
      next_endpoint = if (is.na(parsed$next_url)) "" else parsed$next_url
    )
    previous_oldest <- min(rows$date)
    if (max(rows$date) < floor) {
      boundary <- list(page = number, max_date = as.character(max(rows$date)),
                       min_date = as.character(min(rows$date)))
      break
    }
    press_official_validate_next(parsed$next_url, config$source_kind[[1L]],
                                 number, config$origin[[1L]])
  }
  if (is.null(boundary))
    stop("official press listing never reached a wholly pre-floor page: ",
         config$bioguide[[1L]])
  examined <- dplyr::bind_rows(pages)
  if (any(diff(as.numeric(examined$date)) > 0))
    stop("official press listing failed global date-order proof: ", config$bioguide[[1L]])
  current <- examined[examined$date >= floor, , drop = FALSE]
  if (nrow(current) && anyDuplicated(press_official_url_key(current$url)))
    stop("official current press listing contains duplicate URL identities")
  coverage_sha256 <- press_official_sha256(c(
    config$bioguide[[1L]], config$source_kind[[1L]], as.character(floor),
    vapply(request_evidence, function(item) paste(
      item$page, item$endpoint, item$raw_response_sha256, item$item_count,
      item$newest_date, item$oldest_date, sep = "|"), character(1L)),
    vapply(detail_evidence, function(item) paste(item$endpoint, item$raw_response_sha256,
      item$publication_date, item$body_sha256, sep = "|"), character(1L)),
    paste("boundary", boundary$page, boundary$max_date, boundary$min_date, sep = "|"),
    current$press_official_listing_row_sha256
  ))
  fields <- list(press_official_listing = TRUE,
    press_official_source_kind = config$source_kind[[1L]],
    press_official_listing_coverage_sha256 = coverage_sha256,
    press_official_pages_fetched = length(request_evidence),
    press_official_rows_examined = nrow(examined), press_official_current_rows = nrow(current),
    press_official_boundary_page = boundary$page,
    press_official_boundary_max_date = boundary$max_date,
    press_official_boundary_min_date = boundary$min_date,
    press_official_rss_endpoint = "", press_official_rss_response_sha256 = "",
    press_official_rss_items = 0L, press_official_rss_intersections = 0L)
  for (field in names(fields)) current[[field]] <- rep(fields[[field]], nrow(current))
  for (field in c("press_official_body_source", "press_official_body_sha256", "press_official_body"))
    if (!field %in% names(current)) current[[field]] <- rep("", nrow(current))
  proof <- list(
    bioguide = config$bioguide[[1L]], source_kind = config$source_kind[[1L]],
    floor = as.character(floor), current_urls = current$url,
    request_evidence = request_evidence, detail_evidence = detail_evidence,
    pages_fetched = length(request_evidence),
    rows_examined = nrow(examined), current_rows = nrow(current),
    boundary = boundary, coverage_sha256 = coverage_sha256,
    body_evidence = list()
  )
  list(records = current, proof = proof)
}

press_official_plain_body <- function(value) {
  if (length(value) != 1L || is.na(value) || !nzchar(value)) return("")
  page <- tryCatch(xml2::read_html(paste0("<article>", value, "</article>")),
                   error = function(error) NULL)
  if (is.null(page)) stop("official RSS body is not parseable HTML")
  xml2::xml_remove(rvest::html_elements(page, "script, style, noscript"))
  press_official_squish(rvest::html_text2(rvest::html_element(page, "article")))
}

press_official_add_larsen_rss <- function(result, request) {
  records <- result$records
  response <- tryCatch(request(PRESS_OFFICIAL_LARSEN_RSS), error = function(error) NULL)
  if (is.null(response)) {
    message("Official Larsen RSS unavailable; current detail URLs remain retryable.")
    return(result)
  }
  text <- press_official_response_text(response, PRESS_OFFICIAL_LARSEN_RSS,
                                       "https://larsen.house.gov")
  feed <- tryCatch(xml2::read_xml(charToRaw(enc2utf8(text))), error = function(error) NULL)
  if (is.null(feed)) stop("unparseable official Larsen RSS")
  items <- xml2::xml_find_all(feed, "//item")
  if (!length(items)) stop("official Larsen RSS has no items")
  listing_keys <- press_official_url_key(records$url)
  candidates <- list()
  for (item in items) {
    link_node <- xml2::xml_find_first(item, "link")
    title_node <- xml2::xml_find_first(item, "title")
    date_node <- xml2::xml_find_first(item, "pubDate")
    body_node <- xml2::xml_find_first(item, "description")
    if (any(vapply(list(link_node, title_node, date_node, body_node),
                   inherits, logical(1L), what = "xml_missing")))
      stop("official Larsen RSS item lacks required identity/date/body fields")
    url <- sub("^http://", "https://", press_official_squish(xml2::xml_text(link_node)),
               ignore.case = TRUE)
    if (!grepl("/news/documentsingle\\.aspx\\?DocumentID=[0-9]+$", url,
               ignore.case = TRUE, perl = TRUE)) next
    press_official_validate_article_url(url, "larsen_official_documentquery",
                                        "https://larsen.house.gov")
    candidates[[length(candidates) + 1L]] <- list(
      url = url, key = press_official_url_key(url),
      title = press_official_squish(xml2::xml_text(title_node)),
      date = press_official_rfc_date(xml2::xml_text(date_node)),
      body = press_official_plain_body(xml2::xml_text(body_node))
    )
  }
  if (length(candidates)) {
    keys <- vapply(candidates, `[[`, character(1L), "key")
    if (anyDuplicated(keys)) stop("official Larsen RSS repeats a press URL identity")
  }
  matched <- Filter(function(item) item$key %in% listing_keys, candidates)
  for (item in matched) {
    row <- match(item$key, listing_keys)
    if (!identical(item$title, records$title[[row]]))
      stop("official Larsen RSS title disagrees with listing: ", item$url)
    if (item$date != records$date[[row]])
      stop("official Larsen RSS date disagrees with listing: ", item$url)
    if (nchar(item$body) < 200L)
      stop("official Larsen RSS body is unexpectedly short: ", item$url)
    records$press_official_body[[row]] <- item$body
    records$press_official_body_source[[row]] <- "official_rss_listing_intersection"
    records$press_official_body_sha256[[row]] <- press_official_sha256(item$body)
  }
  response_sha256 <- press_official_sha256(text)
  records$press_official_rss_endpoint <- PRESS_OFFICIAL_LARSEN_RSS
  records$press_official_rss_response_sha256 <- response_sha256
  records$press_official_rss_items <- length(items)
  records$press_official_rss_intersections <- length(matched)
  result$records <- records
  result$proof$rss_evidence <- list(
    endpoint = PRESS_OFFICIAL_LARSEN_RSS,
    final_url = press_official_response_url(response, PRESS_OFFICIAL_LARSEN_RSS),
    raw_response_sha256 = response_sha256, item_count = length(items),
    press_candidate_count = length(candidates), intersection_count = length(matched),
    intersected_urls = vapply(matched, `[[`, character(1L), "url"),
    body_sha256 = setNames(vapply(matched, function(item)
      press_official_sha256(item$body), character(1L)),
      vapply(matched, `[[`, character(1L), "url"))
  )
  result
}

press_official_shared_coverage_proof <- function(result) {
  proof <- result$proof
  requests <- lapply(seq_along(proof$request_evidence), function(number) {
    item <- proof$request_evidence[[number]]
    list(
      request_index = as.integer(number), role = "inventory_page",
      endpoint = item$endpoint, final_url = item$final_url,
      raw_response_sha256 = item$raw_response_sha256,
      item_count = as.integer(item$item_count), page = as.integer(item$page),
      min_date = item$oldest_date, max_date = item$newest_date,
      next_endpoint = item$next_endpoint
    )
  })
  for (item in proof$detail_evidence) {
    requests[[length(requests) + 1L]] <- list(
      request_index = as.integer(length(requests) + 1L), role = "publication_date_detail",
      endpoint = item$endpoint, final_url = item$final_url,
      raw_response_sha256 = item$raw_response_sha256, item_count = 1L,
      publication_date = item$publication_date, body_sha256 = item$body_sha256
    )
  }
  if (!is.null(proof$rss_evidence)) {
    item <- proof$rss_evidence
    requests[[length(requests) + 1L]] <- list(
      request_index = as.integer(length(requests) + 1L),
      role = "supplemental_body_feed", endpoint = item$endpoint,
      final_url = item$final_url, raw_response_sha256 = item$raw_response_sha256,
      item_count = as.integer(item$item_count), page = 0L,
      press_candidate_count = as.integer(item$press_candidate_count),
      intersection_count = as.integer(item$intersection_count)
    )
  }
  shared <- list(
    schema = "official-current-inventory-proof-v1",
    bioguide = proof$bioguide,
    source_kind = proof$source_kind,
    coverage_mode = if (isTRUE(proof$boundary$empty))
      "sequential_dated_listing_terminal_empty" else "sequential_dated_listing_terminal_prefloor",
    floor = proof$floor,
    current_count = length(proof$current_urls),
    current_urls = sort(as.character(proof$current_urls)),
    current_url_set_sha256 = press_coverage_json_hash(sort(
      press_official_url_key(proof$current_urls))),
    requests = requests,
    completeness = list(
      pagination_complete = TRUE, date_order_verified = TRUE,
      identity_complete = TRUE, terminal_page_wholly_prefloor = !isTRUE(proof$boundary$empty),
      terminal_empty_page_verified = isTRUE(proof$boundary$empty),
      pages_fetched = as.integer(proof$pages_fetched),
      rows_examined = as.integer(proof$rows_examined),
      boundary_page = as.integer(proof$boundary$page),
      boundary_max_date = proof$boundary$max_date,
      boundary_min_date = proof$boundary$min_date
    )
  )
  press_coverage_validate_proof(shared, proof$floor)
  shared
}

press_official_collect <- function(members, floor, request = press_official_default_request,
                                   today = Sys.Date(), envir = NULL) {
  if (!is.data.frame(members) || !all(c("bioguide", "url") %in% names(members)))
    stop("press roster lacks official listing identity/origin columns")
  configured <- PRESS_OFFICIAL_DATED_MEMBERS[
    PRESS_OFFICIAL_DATED_MEMBERS$bioguide %in% members$bioguide, , drop = FALSE]
  records <- list(); proofs <- list()
  for (number in seq_len(nrow(configured))) {
    config <- configured[number, , drop = FALSE]
    member <- members[members$bioguide == config$bioguide[[1L]], , drop = FALSE]
    if (nrow(member) != 1L)
      stop("official dated press member identity is ambiguous: ", config$bioguide[[1L]])
    if (!press_official_same_host(member$url[[1L]], config$origin[[1L]]))
      stop("official dated press origin no longer matches roster: ", config$bioguide[[1L]])
    result <- press_official_collect_member(config, floor, request, today)
    if (config$bioguide[[1L]] == "L000560")
      result <- press_official_add_larsen_rss(result, request)
    if (!is.null(envir))
      press_official_coverage_register(press_official_shared_coverage_proof(result), envir)
    for (name in intersect(c("member", "chamber", "state", "party"), names(member)))
      result$records[[name]] <- rep(member[[name]][[1L]], nrow(result$records))
    records[[length(records) + 1L]] <- result$records
    proofs[[config$bioguide[[1L]]]] <- result$proof
  }
  list(records = if (length(records)) dplyr::bind_rows(records) else press_official_empty(),
       proofs = proofs)
}

press_official_seed_checkpoints <- function(index, records, envir) {
  if (!nrow(index) || !nrow(records) || !"press_official_listing" %in% names(index))
    return(invisible(NULL))
  selected <- index$press_official_listing %in% TRUE
  selected[is.na(selected)] <- FALSE
  if (!any(selected)) return(invisible(NULL))
  index <- index[selected, , drop = FALSE]
  record_keys <- paste(records$bioguide, press_official_url_key(records$url), sep = "#")
  index_keys <- paste(index$bioguide, press_official_url_key(index$url), sep = "#")
  matched <- match(index_keys, record_keys)
  if (anyNA(matched)) stop("official dated press index is not bound to collected evidence")
  records <- records[matched, , drop = FALSE]
  checkpoint_root <- get("CHECKPOINT_DIR", envir = envir, inherits = TRUE)
  writer <- get("write_rds_atomic", envir = envir, inherits = TRUE)
  for (bg in unique(records$bioguide)) {
    member <- records[records$bioguide == bg, , drop = FALSE]
    path <- file.path(checkpoint_root, "fulltext", paste0(bg, ".rds"))
    previous <- if (file.exists(path)) readRDS(path) else data.frame()
    if (!is.data.frame(previous) || (nrow(previous) &&
        !all(c("url", "title_detail", "date_detail", "body") %in% names(previous))))
      stop("invalid official press fulltext checkpoint: ", bg)
    if (nrow(previous) && "bioguide" %in% names(previous) &&
        any(is.na(previous$bioguide) | previous$bioguide != bg))
      stop("official press fulltext checkpoint member mismatch: ", bg)
    if (nrow(previous) && anyDuplicated(press_official_url_key(previous$url)))
      stop("official press fulltext checkpoint has duplicate URL identities: ", bg)
    for (name in c("press_official_date_source", "press_official_body_source",
                   "press_official_body_sha256",
                   "press_official_listing_row_sha256"))
      if (!name %in% names(previous)) previous[[name]] <- rep("", nrow(previous))
    changed <- FALSE
    for (row in seq_len(nrow(member))) {
      key <- press_official_url_key(member$url[[row]])
      at <- if (nrow(previous)) match(key, press_official_url_key(previous$url)) else NA_integer_
      rss_body <- member$press_official_body[[row]]
      if (nzchar(rss_body)) {
        expected <- press_official_sha256(rss_body)
        if (!identical(expected, member$press_official_body_sha256[[row]]))
          stop("official RSS body hash changed before checkpoint seed: ", member$url[[row]])
        seed <- data.frame(
          url = member$url[[row]], title_detail = member$title[[row]],
          date_detail = member$date[[row]], body = rss_body,
          press_official_date_source = "official_listing",
          press_official_body_source = member$press_official_body_source[[row]],
          press_official_body_sha256 = expected,
          press_official_listing_row_sha256 = member$press_official_listing_row_sha256[[row]],
          stringsAsFactors = FALSE
        )
        if (is.na(at)) previous <- dplyr::bind_rows(previous, seed)
        else previous <- dplyr::bind_rows(previous[-at, , drop = FALSE], seed)
        changed <- TRUE
      } else if (!is.na(at) && !is.na(previous$body[[at]]) &&
                 nzchar(trimws(previous$body[[at]]))) {
        previous$url[[at]] <- member$url[[row]]
        if (is.na(previous$title_detail[[at]]) || !nzchar(previous$title_detail[[at]]))
          previous$title_detail[[at]] <- member$title[[row]]
        previous$date_detail[[at]] <- member$date[[row]]
        previous$press_official_date_source[[at]] <- "official_listing"
        previous$press_official_body_source[[at]] <- "existing_member_checkpoint"
        previous$press_official_body_sha256[[at]] <- press_official_sha256(previous$body[[at]])
        previous$press_official_listing_row_sha256[[at]] <-
          member$press_official_listing_row_sha256[[row]]
        changed <- TRUE
      }
    }
    if (changed) {
      dir.create(dirname(path), recursive = TRUE, showWarnings = FALSE)
      writer(previous, path)
    }
  }
  invisible(NULL)
}

press_official_checkpoint_body_evidence <- function(records, envir) {
  root <- get("CHECKPOINT_DIR", envir = envir, inherits = TRUE)
  evidence <- list()
  for (bg in unique(records$bioguide)) {
    path <- file.path(root, "fulltext", paste0(bg, ".rds"))
    if (!file.exists(path)) next
    frame <- readRDS(path)
    if (!is.data.frame(frame) || !all(c("url", "date_detail", "body") %in% names(frame)))
      stop("invalid official press fulltext evidence checkpoint: ", bg)
    if (nrow(frame) && anyDuplicated(press_official_url_key(frame$url)))
      stop("duplicate official press body evidence identity: ", bg)
    member <- records[records$bioguide == bg, , drop = FALSE]
    matched <- match(press_official_url_key(member$url), press_official_url_key(frame$url))
    for (row in which(!is.na(matched))) {
      at <- matched[[row]]
      if (is.na(frame$body[[at]]) || !nzchar(trimws(frame$body[[at]]))) next
      date <- press_official_strict_iso_date(as.character(frame$date_detail[[at]]),
                                             "body evidence date")
      if (date != member$date[[row]])
        stop("official press body date disagrees with listing: ", member$url[[row]])
      evidence[[length(evidence) + 1L]] <- list(
        url = member$url[[row]], date = as.character(date),
        body_sha256 = press_official_sha256(frame$body[[at]]),
        source = if ("press_official_body_source" %in% names(frame) &&
                       !is.na(frame$press_official_body_source[[at]]) &&
                       nzchar(frame$press_official_body_source[[at]]))
          frame$press_official_body_source[[at]] else "official_detail_or_member_checkpoint"
      )
    }
  }
  evidence
}

press_official_meta_date <- function(page, today = Sys.Date()) {
  nodes <- rvest::html_elements(page, "meta[property='article:published_time']")
  if (!length(nodes)) return(as.Date(NA))
  values <- unique(substr(press_official_squish(rvest::html_attr(nodes, "content")), 1L, 10L))
  values <- values[nzchar(values)]
  if (length(values) != 1L)
    stop("official article has ambiguous article:published_time metadata")
  date <- press_official_strict_iso_date(values[[1L]], "article:published_time")
  if (date < as.Date("1900-01-01") || date > today)
    stop("official article:published_time is outside the valid collection range")
  date
}

install_press_official_dated_listings <- function(
    envir = .GlobalEnv, request = press_official_default_request,
    today = function() Sys.Date()) {
  original_index <- get("stage_index", envir = envir, inherits = FALSE)
  if (isTRUE(attr(original_index, "melange_press_official_dated_listings")))
    return(invisible(NULL))
  original_fulltext <- get("stage_fulltext", envir = envir, inherits = FALSE)
  original_extract <- get("extract_release", envir = envir, inherits = FALSE)
  state <- new.env(parent = emptyenv())
  state$records <- press_official_empty()
  state$proofs <- list()

  index_stage <- function(members, disc, force = FALSE) {
    configured <- intersect(members$bioguide, PRESS_OFFICIAL_DATED_MEMBERS$bioguide)
    base <- original_index(members, disc, force = force)
    floor <- get("DATE_FLOOR", envir = envir, inherits = TRUE)
    collected <- press_official_collect(members[members$bioguide %in% configured, , drop = FALSE],
                                        floor, request, today(), envir)
    state$records <- collected$records
    state$proofs <- collected$proofs
    if (!nrow(collected$records)) return(base)
    metadata <- collected$records[, setdiff(names(collected$records),
                                             "press_official_body"), drop = FALSE]
    combined <- dplyr::bind_rows(base, metadata)
    keys <- paste(combined$bioguide, press_official_url_key(combined$url), sep = "#")
    # The official dated row replaces an exact CDX collision. Distinct archive
    # URLs remain in the index for bounded historical maintenance.
    combined <- combined[!duplicated(keys, fromLast = TRUE), , drop = FALSE]
    attr(combined, "press_official_dated_listing_proofs") <- state$proofs
    message("Official dated press listings: ", nrow(collected$records),
            " current release(s) across ", length(collected$proofs),
            " member(s), each with a verified dated inventory boundary proof.")
    combined
  }
  attr(index_stage, "melange_press_official_dated_listings") <- TRUE

  fulltext_stage <- function(idx, force = FALSE, js_bgs = character(0)) {
    press_official_seed_checkpoints(idx, state$records, envir)
    official <- unique(state$records$bioguide)
    output <- original_fulltext(idx, force = force,
                                js_bgs = setdiff(js_bgs, official))
    bodies <- press_official_checkpoint_body_evidence(state$records, envir)
    if (length(bodies)) {
      by_member <- split(bodies, vapply(bodies, function(item) {
        key <- press_official_url_key(item$url)
        state$records$bioguide[match(key, press_official_url_key(state$records$url))]
      }, character(1L)))
      for (bg in names(by_member)) state$proofs[[bg]]$body_evidence <- by_member[[bg]]
    }
    attr(output, "press_official_dated_listing_proofs") <- state$proofs
    output
  }
  attr(fulltext_stage, "melange_press_official_dated_listings") <- TRUE

  extract_release <- function(pg, u) {
    meta_date <- press_official_meta_date(pg, today())
    output <- original_extract(pg, u)
    if (!is.na(meta_date)) {
      if (!is.data.frame(output) || nrow(output) != 1L ||
          !"date_detail" %in% names(output))
        stop("upstream press detail result changed; review official date adapter")
      output$date_detail <- meta_date
      output$press_official_date_source <- "article:published_time"
      output$press_official_detail_body_sha256 <- if (
        "body" %in% names(output) && !is.na(output$body[[1L]]) && nzchar(output$body[[1L]]))
        press_official_sha256(output$body[[1L]]) else ""
    }
    output
  }
  attr(extract_release, "melange_press_official_meta_date") <- TRUE

  proof_getter <- function() state$proofs
  assign("stage_index", index_stage, envir = envir)
  assign("stage_fulltext", fulltext_stage, envir = envir)
  assign("extract_release", extract_release, envir = envir)
  assign("press_official_dated_listing_proofs", proof_getter, envir = envir)
  invisible(NULL)
}

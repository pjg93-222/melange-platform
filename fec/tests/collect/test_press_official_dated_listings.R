# Private fixtures for the bounded Durbin and Larsen official-listing adapter.
# No live endpoint, production checkpoint, or active output is read or written.
args <- commandArgs(trailingOnly = TRUE)
stopifnot(length(args) == 2L)
.libPaths(c(file.path(dirname(args[[1L]]), "..", "..", "outputs", "r-library"),
            .libPaths()))
source(args[[1L]], local = globalenv())
source(args[[2L]], local = globalenv())

TODAY <- as.Date("2026-09-13")
FLOOR <- as.Date("2026-06-25")
D1 <- "https://www.durbin.senate.gov/newsroom/press-releases/durbin-current-one"
D2 <- "https://www.durbin.senate.gov/newsroom/press-releases/durbin-current-two"
D_OLD <- "https://www.durbin.senate.gov/newsroom/press-releases/durbin-old"
D_OLDER <- "https://www.durbin.senate.gov/newsroom/press-releases/durbin-older"
L1 <- "https://larsen.house.gov/news/documentsingle.aspx?DocumentID=3001"
L2 <- "https://larsen.house.gov/news/documentsingle.aspx?DocumentID=3002"
L_OLD <- "https://larsen.house.gov/news/documentsingle.aspx?DocumentID=2999"

durbin_page <- function(rows, next_page = NULL) {
  entries <- vapply(rows, function(row) paste0(
    "<span class='date black'>", row$date, "</span>",
    "<h2 class='title'><a href='", row$url, "'>", row$title, "</a></h2>"),
    character(1L))
  next_link <- if (is.null(next_page)) "" else paste0(
    "<ul><li class='next'><a href='/newsroom/press-releases?PageNum_rs=",
    next_page, "'>Next</a></li></ul>")
  paste0("<html><body><div id='press'>", paste(entries, collapse = ""),
         "</div>", next_link, "</body></html>")
}

larsen_page <- function(rows, next_page = NULL) {
  entries <- vapply(rows, function(row) paste0(
    "<article><h2 class='newsie-titler'><a href='", row$url, "'>",
    row$title, "</a></h2><time datetime='", row$date,
    "T12:00:00-04:00'>", row$date, "</time></article>"), character(1L))
  next_link <- if (is.null(next_page)) "" else paste0(
    "<a id='ctl00_ctl19_LinkNext2' href='/news/documentquery.aspx?DocumentTypeID=27&amp;Page=",
    next_page, "'>Next</a>")
  paste0("<html><body>", paste(entries, collapse = ""), next_link,
         "</body></html>")
}

rss_item <- function(title, url, pub_date, body) paste0(
  "<item><title>", title, "</title><link>", gsub("&", "&amp;", url, fixed = TRUE),
  "</link><pubDate>", pub_date, "</pubDate><description><![CDATA[",
  body, "]]></description></item>")

body_one <- paste0("<p>", paste(rep(
  "Larsen announced a detailed official policy update for constituents and local partners.",
  8L), collapse = " "), "</p>")
body_two <- paste0("<p>", paste(rep(
  "The official release explains the program, its public purpose, and the next implementation steps.",
  8L), collapse = " "), "</p>")
event_body <- paste0("<p>", paste(rep("Calendar event information for a district meeting.", 8L),
                                   collapse = " "), "</p>")

D_PAGE_1 <- press_official_listing_endpoint("durbin_official_pagenum", 1L)
D_PAGE_2 <- press_official_listing_endpoint("durbin_official_pagenum", 2L)
L_PAGE_1 <- press_official_listing_endpoint("larsen_official_documentquery", 1L)
L_PAGE_2 <- press_official_listing_endpoint("larsen_official_documentquery", 2L)

standard_responses <- function() {
  pages <- list(
    durbin_page(list(
      list(date = "September 11, 2026", url = D1, title = "Durbin current one"),
      list(date = "June 25, 2026", url = D2, title = "Durbin current two")), 2L),
    durbin_page(list(
      list(date = "June 24, 2026", url = D_OLD, title = "Durbin old"),
      list(date = "June 18, 2026", url = D_OLDER, title = "Durbin older"))),
    larsen_page(list(
      list(date = "2026-09-09", url = L1, title = "Larsen current one"),
      list(date = "2026-06-30", url = L2, title = "Larsen current two")), 2L),
    larsen_page(list(
      list(date = "2026-06-01", url = L_OLD, title = "Larsen old"))),
    paste0("<?xml version='1.0'?><rss><channel>",
      rss_item("Larsen current one&#160;", L1,
               "Wed, 09 Sep 2026 12:00:00 GMT", body_one),
      rss_item("Larsen current two", L2, "Tue, 30 Jun 2026 12:00:00 GMT", body_two),
      rss_item("District calendar", "https://larsen.house.gov/calendar/eventsingle.aspx?EventID=8",
               "Mon, 01 Jun 2026 12:00:00 GMT", event_body),
      "</channel></rss>")
  )
  stats::setNames(pages, c(D_PAGE_1, D_PAGE_2, L_PAGE_1, L_PAGE_2,
                           PRESS_OFFICIAL_LARSEN_RSS))
}

request_from <- function(responses, calls = NULL) {
  if (is.null(calls)) calls <- new.env(parent = emptyenv())
  calls$urls <- character(0)
  function(url) {
    calls$urls <- c(calls$urls, url)
    text <- responses[[url]]
    if (is.null(text)) stop("fixture has no response for ", url)
    list(status = 200L, headers = list(), text = text, url = url)
  }
}

write_atomic <- function(value, path) {
  dir.create(dirname(path), recursive = TRUE, showWarnings = FALSE)
  temporary <- paste0(path, ".part")
  saveRDS(value, temporary)
  if (file.exists(path)) unlink(path)
  stopifnot(file.rename(temporary, path))
}

make_installed_fixture <- function() {
  env <- new.env(parent = globalenv())
  source(args[[1L]], local = env)
  source(args[[2L]], local = env)
  env$press_official_coverage_reset(env)
  env$DATE_FLOOR <- FLOOR
  env$CHECKPOINT_DIR <- tempfile("press-official-dated-")
  dir.create(file.path(env$CHECKPOINT_DIR, "fulltext"), recursive = TRUE)
  env$write_rds_atomic <- write_atomic
  env$index_members_seen <- character(0)
  env$fulltext_js_seen <- character(0)
  env$stage_index <- function(members, disc, force = FALSE) {
    env$index_members_seen <- members$bioguide
    stopifnot(identical(disc$bioguide, members$bioguide))
    data.frame(
      bioguide = "X000001", title = "Other current release",
      url = "https://other.house.gov/news/other-current", date = TODAY,
      member = "Other Member", chamber = "House", state = "XX", party = "Other",
      stringsAsFactors = FALSE)
  }
  env$stage_fulltext <- function(idx, force = FALSE, js_bgs = character(0)) {
    env$fulltext_js_seen <- js_bgs
    official <- idx[idx$bioguide == "D000563" & idx$press_official_listing %in% TRUE,
                    , drop = FALSE]
    path <- file.path(env$CHECKPOINT_DIR, "fulltext", "D000563.rds")
    cached <- if (file.exists(path)) readRDS(path) else data.frame()
    known <- if (nrow(cached)) press_official_url_key(cached$url) else character(0)
    for (row in seq_len(nrow(official))) {
      if (press_official_url_key(official$url[[row]]) %in% known) next
      detail <- data.frame(
        url = official$url[[row]], title_detail = official$title[[row]],
        date_detail = official$date[[row]],
        body = paste(rep(paste("Official Durbin detail body", row), 20L), collapse = " "),
        press_official_date_source = "article:published_time",
        press_official_body_source = "official_detail",
        press_official_body_sha256 = press_official_sha256(
          paste(rep(paste("Official Durbin detail body", row), 20L), collapse = " ")),
        press_official_listing_row_sha256 = official$press_official_listing_row_sha256[[row]],
        stringsAsFactors = FALSE)
      cached <- dplyr::bind_rows(cached, detail)
    }
    if (nrow(cached)) write_atomic(cached, path)
    idx
  }
  env$extract_release <- function(pg, u) data.frame(
    url = u, title_detail = "fixture title", date_detail = as.Date("2017-11-13"),
    body = paste(rep("fixture detail body", 20L), collapse = " "),
    stringsAsFactors = FALSE)
  calls <- new.env(parent = emptyenv())
  request <- request_from(standard_responses(), calls)
  env$install_press_official_dated_listings(env, request, function() TODAY)
  list(env = env, calls = calls)
}

members <- data.frame(
  bioguide = c("D000563", "L000560", "X000001"),
  member = c("Richard Durbin", "Rick Larsen", "Other Member"),
  chamber = c("Senate", "House", "House"), state = c("IL", "WA", "XX"),
  party = c("Democrat", "Democrat", "Other"),
  url = c("https://www.durbin.senate.gov", "https://larsen.house.gov",
          "https://other.house.gov"), stringsAsFactors = FALSE)
discovery <- data.frame(bioguide = members$bioguide, method = "fixture",
                        stringsAsFactors = FALSE)

fixture <- make_installed_fixture()
d_checkpoint <- file.path(fixture$env$CHECKPOINT_DIR, "fulltext", "D000563.rds")
saveRDS(data.frame(
  url = D1, title_detail = "cached Durbin title",
  date_detail = as.Date("2017-11-13"),
  body = paste(rep("retained official Durbin body", 20L), collapse = " "),
  stringsAsFactors = FALSE), d_checkpoint)

index <- fixture$env$stage_index(members, discovery)
official <- index[index$press_official_listing %in% TRUE, , drop = FALSE]
stopifnot(
  identical(fixture$env$index_members_seen, members$bioguide),
  nrow(index) == 5L, nrow(official) == 4L,
  setequal(official$url, c(D1, D2, L1, L2)),
  all(!is.na(official$date)), all(official$date >= FLOOR),
  identical(fixture$calls$urls,
            c(D_PAGE_1, D_PAGE_2, L_PAGE_1, L_PAGE_2, PRESS_OFFICIAL_LARSEN_RSS)))
shared <- fixture$env$press_official_coverage_snapshot(fixture$env)
stopifnot(shared$member_count == 2L, shared$current_url_count == 4L,
          all(vapply(shared$members, function(proof)
            proof$coverage_mode == "sequential_dated_listing_terminal_prefloor" &&
              isTRUE(proof$completeness$terminal_page_wholly_prefloor), logical(1L))))

proofs <- fixture$env$press_official_dated_listing_proofs()
required <- c("bioguide", "source_kind", "floor", "current_urls",
              "request_evidence", "boundary", "coverage_sha256", "body_evidence")
stopifnot(setequal(names(proofs), c("D000563", "L000560")))
for (bg in names(proofs)) {
  proof <- proofs[[bg]]
  stopifnot(all(required %in% names(proof)), identical(proof$bioguide, bg),
            identical(proof$floor, as.character(FLOOR)), length(proof$request_evidence) == 2L,
            proof$boundary$page == 2L, grepl("^[0-9a-f]{64}$", proof$coverage_sha256),
            all(vapply(proof$request_evidence, function(page)
              all(c("endpoint", "raw_response_sha256", "item_count") %in% names(page)) &&
                grepl("^[0-9a-f]{64}$", page$raw_response_sha256), logical(1L))))
}
stopifnot(
  setequal(proofs$D000563$current_urls, c(D1, D2)),
  identical(vapply(proofs$D000563$request_evidence, `[[`, integer(1L), "item_count"),
            c(2L, 2L)),
  setequal(proofs$L000560$current_urls, c(L1, L2)),
  identical(vapply(proofs$L000560$request_evidence, `[[`, integer(1L), "item_count"),
            c(2L, 1L)),
  proofs$L000560$rss_evidence$item_count == 3L,
  proofs$L000560$rss_evidence$press_candidate_count == 2L,
  proofs$L000560$rss_evidence$intersection_count == 2L,
  setequal(proofs$L000560$rss_evidence$intersected_urls, c(L1, L2)),
  all(grepl("^[0-9a-f]{64}$", proofs$L000560$rss_evidence$body_sha256)))

fulltext <- fixture$env$stage_fulltext(index, js_bgs = c("D000563", "L000560", "X000001"))
stopifnot(nrow(fulltext) == nrow(index), identical(fixture$env$fulltext_js_seen, "X000001"))
d_cached <- readRDS(d_checkpoint)
l_checkpoint <- file.path(fixture$env$CHECKPOINT_DIR, "fulltext", "L000560.rds")
l_cached <- readRDS(l_checkpoint)
stopifnot(
  nrow(d_cached) == 2L, setequal(d_cached$url, c(D1, D2)),
  d_cached$date_detail[match(D1, d_cached$url)] == as.Date("2026-09-11"),
  d_cached$press_official_body_source[match(D1, d_cached$url)] == "existing_member_checkpoint",
  nrow(l_cached) == 2L, setequal(l_cached$url, c(L1, L2)),
  l_cached$date_detail[match(L1, l_cached$url)] == as.Date("2026-09-09"),
  l_cached$date_detail[match(L2, l_cached$url)] == as.Date("2026-06-30"),
  all(l_cached$press_official_body_source == "official_rss_listing_intersection"),
  all(grepl("^[0-9a-f]{64}$", l_cached$press_official_body_sha256)))
proofs <- fixture$env$press_official_dated_listing_proofs()
stopifnot(length(proofs$D000563$body_evidence) == 2L,
          length(proofs$L000560$body_evidence) == 2L,
          all(vapply(c(proofs$D000563$body_evidence, proofs$L000560$body_evidence),
                     function(item) grepl("^[0-9a-f]{64}$", item$body_sha256), logical(1L))))

# article:published_time takes precedence over an unrelated time element.
page <- xml2::read_html(paste0(
  "<html><head><meta property='article:published_time' content='2026-09-09T12:00:00-04:00'>",
  "</head><body><time datetime='2017-11-13'>Related release</time></body></html>"))
extracted <- fixture$env$extract_release(page, L1)
stopifnot(extracted$date_detail == as.Date("2026-09-09"),
          extracted$press_official_date_source == "article:published_time",
          grepl("^[0-9a-f]{64}$", extracted$press_official_detail_body_sha256))
fallback <- fixture$env$extract_release(
  xml2::read_html("<html><body><time datetime='2017-11-13'>Related</time></body></html>"), L1)
stopifnot(fallback$date_detail == as.Date("2017-11-13"))
ambiguous <- xml2::read_html(paste0(
  "<meta property='article:published_time' content='2026-09-09'>",
  "<meta property='article:published_time' content='2026-09-10'>"))
stopifnot(inherits(try(fixture$env$extract_release(ambiguous, L1), silent = TRUE), "try-error"))
wrapped <- fixture$env$stage_index
fixture$env$install_press_official_dated_listings(
  fixture$env, function(url) stop("idempotent install must not request"), function() TODAY)
stopifnot(identical(wrapped, fixture$env$stage_index))

expect_failure <- function(expression) {
  stopifnot(inherits(try(force(expression), silent = TRUE), "try-error"))
}
d_config <- PRESS_OFFICIAL_DATED_MEMBERS[
  PRESS_OFFICIAL_DATED_MEMBERS$bioguide == "D000563", , drop = FALSE]
l_config <- PRESS_OFFICIAL_DATED_MEMBERS[
  PRESS_OFFICIAL_DATED_MEMBERS$bioguide == "L000560", , drop = FALSE]

# Dates must be descending within and across pages.
bad_order <- standard_responses()
bad_order[[D_PAGE_1]] <- durbin_page(list(
  list(date = "June 25, 2026", url = D2, title = "Durbin current two"),
  list(date = "September 11, 2026", url = D1, title = "Durbin current one")), 2L)
expect_failure(press_official_collect_member(
  d_config, FLOOR, request_from(bad_order), TODAY))
cross_page_order <- standard_responses()
cross_page_order[[D_PAGE_2]] <- durbin_page(list(
  list(date = "June 26, 2026",
       url = "https://www.durbin.senate.gov/newsroom/press-releases/cross-page-newer",
       title = "Cross-page newer"),
  list(date = "June 24, 2026", url = D_OLD, title = "Durbin old")), 3L)
expect_failure(press_official_collect_member(
  d_config, FLOOR, request_from(cross_page_order), TODAY))

# Independent selectors must still yield a one-to-one date/link pairing.
bad_pairing <- standard_responses()
bad_pairing[[D_PAGE_1]] <- paste0(
  "<div id='press'><span class='date black'>September 11, 2026</span>",
  "<span class='date black'>June 25, 2026</span>",
  "<h2 class='title'><a href='", D1, "'>Only one link</a></h2></div>",
  "<ul><li class='next'><a href='/newsroom/press-releases?PageNum_rs=2'>Next</a></li></ul>")
expect_failure(press_official_collect_member(
  d_config, FLOOR, request_from(bad_pairing), TODAY))

# A current page must point to the exact next page; ending or skipping is fatal.
missing_next <- standard_responses()
missing_next[[D_PAGE_1]] <- durbin_page(list(
  list(date = "September 11, 2026", url = D1, title = "Durbin current one")))
expect_failure(press_official_collect_member(
  d_config, FLOOR, request_from(missing_next), TODAY))
skipped_next <- standard_responses()
skipped_next[[D_PAGE_1]] <- durbin_page(list(
  list(date = "September 11, 2026", url = D1, title = "Durbin current one")), 3L)
expect_failure(press_official_collect_member(
  d_config, FLOOR, request_from(skipped_next), TODAY))

# URL identity is unique across pages and remains on the member's official host.
duplicate <- standard_responses()
duplicate[[D_PAGE_2]] <- durbin_page(list(
  list(date = "June 24, 2026", url = D1, title = "Duplicate current identity")))
expect_failure(press_official_collect_member(
  d_config, FLOOR, request_from(duplicate), TODAY))
foreign <- standard_responses()
foreign[[D_PAGE_1]] <- durbin_page(list(
  list(date = "September 11, 2026",
       url = "https://example.com/newsroom/press-releases/foreign", title = "Foreign")), 2L)
expect_failure(press_official_collect_member(
  d_config, FLOOR, request_from(foreign), TODAY))

# Exhausting the bound without one wholly pre-floor page cannot prove coverage.
no_boundary <- standard_responses()
no_boundary[[D_PAGE_2]] <- durbin_page(list(
  list(date = "June 25, 2026",
       url = "https://www.durbin.senate.gov/newsroom/press-releases/still-current",
       title = "Still current")), 3L)
short_config <- d_config
short_config$max_pages <- 2L
expect_failure(press_official_collect_member(
  short_config, FLOOR, request_from(no_boundary), TODAY))

# RSS may enrich only listing-intersected identities, and any title/date conflict fails.
l_result <- press_official_collect_member(
  l_config, FLOOR, request_from(standard_responses()), TODAY)
bad_rss <- standard_responses()
bad_rss[[PRESS_OFFICIAL_LARSEN_RSS]] <- sub(
  "Larsen current one", "Mismatched RSS title",
  bad_rss[[PRESS_OFFICIAL_LARSEN_RSS]], fixed = TRUE)
expect_failure(press_official_add_larsen_rss(
  l_result, request_from(bad_rss)))

cat(paste0(
  "PASS: bounded official listings, exact date/link pairing, terminal cutoff proof, ",
  "RSS intersection, body hashes, checkpoint provenance, meta-date priority, and fail-closed identity/order/pagination\n"))

# Houlahan's redesigned DocumentQuery: heading hrefs have leading whitespace,
# visible dates omit the year, and each page contains exactly ten documents.
h_kind <- "houlahan_official_documentquery"
h_origin <- "https://houlahan.house.gov"
h_config <- PRESS_OFFICIAL_DATED_MEMBERS[
  PRESS_OFFICIAL_DATED_MEMBERS$bioguide == "H001085", , drop = FALSE]
h_url <- function(id) paste0(h_origin, "/news/documentsingle.aspx?DocumentID=", id)
h_title <- function(id) paste("Houlahan official release", id)
h_cards <- function(ids, dates, page, next_page = NULL) {
  cards <- vapply(seq_along(ids), function(i) paste0(
    "<div class='p-body newsbody'><div class='press-heading'><span class='date'>",
    month.name[as.integer(format(dates[i], "%m"))], " <span>", format(dates[i], "%d"),
    "</span></span><strong class='title'><a href=' ", h_url(ids[i]), "'>",
    h_title(ids[i]), "</a></strong></div><a href='", h_url(ids[i]),
    "'>Read More</a></div>"), character(1L))
  previous <- if (page == 1L) "" else paste0(
    "<a id='ctl00_ctl19_LinkPrev2' href='", press_official_listing_endpoint(h_kind, page - 1L), "'>Previous</a>")
  next_link <- if (is.null(next_page)) "" else paste0(
    "<a id='ctl00_ctl19_LinkNext2' href='", press_official_listing_endpoint(h_kind, next_page), "'>Next</a>")
  paste0("<html><span id='ctl00_PageLink'>Press Releases</span>",
    "<input id='DocumentTypeID' value='27'><div id='newsdoclist'>",
    paste(cards, collapse = ""), if (!length(ids)) "<span class='errormsg'></span>" else "",
    "</div>", previous, next_link, "</html>")
}
h_dates <- c(TODAY - seq_len(10L), FLOOR - seq_len(10L))
h_ids <- 4001:4020
h_responses <- list()
h_responses[[press_official_listing_endpoint(h_kind, 1L)]] <- h_cards(h_ids[1:10], h_dates[1:10], 1L, 2L)
h_responses[[press_official_listing_endpoint(h_kind, 2L)]] <- h_cards(h_ids[11:20], h_dates[11:20], 2L)
for (i in seq_along(h_ids)) h_responses[[h_url(h_ids[i])]] <- paste0(
  "<meta property='og:title' content='", h_title(h_ids[i]), "'>",
  "<meta property='article:published_time' content='", h_dates[i], "'>",
  "<div class='newsbody'><div class='bodycopy'>", body_one, "</div></div>")
h_calls <- new.env(parent = emptyenv())
h_result <- press_official_collect_member(h_config, FLOOR, request_from(h_responses, h_calls), TODAY)
stopifnot(nrow(h_result$records) == 10L, h_result$proof$pages_fetched == 2L,
  h_result$proof$rows_examined == 20L, h_result$proof$boundary$page == 2L,
  length(h_result$proof$detail_evidence) == 20L, length(h_calls$urls) == 22L,
  identical(h_result$records$date, h_dates[1:10]),
  all(nchar(h_result$records$press_official_body) >= 200L))
h_proof <- press_official_shared_coverage_proof(h_result)
stopifnot(length(h_proof$requests) == 22L,
  sum(vapply(h_proof$requests, function(x) x$role == "publication_date_detail", logical(1L))) == 20L)
press_coverage_validate_proof(h_proof, FLOOR)

# A real empty terminal CMS page is distinct from missing or error markup.
h_empty <- h_responses
h_empty[[press_official_listing_endpoint(h_kind, 2L)]] <- h_cards(integer(0), as.Date(character(0)), 2L)
empty_result <- press_official_collect_member(h_config, FLOOR, request_from(h_empty), TODAY)
empty_proof <- press_official_shared_coverage_proof(empty_result)
stopifnot(nrow(empty_result$records) == 10L,
  empty_proof$coverage_mode == "sequential_dated_listing_terminal_empty",
  empty_proof$completeness$terminal_empty_page_verified,
  !empty_proof$completeness$terminal_page_wholly_prefloor)
press_coverage_validate_proof(empty_proof, FLOOR)
invalid_empty <- empty_proof
invalid_empty$completeness$terminal_empty_page_verified <- FALSE
expect_failure(press_coverage_validate_proof(invalid_empty, FLOOR))
invalid_empty <- empty_proof
invalid_empty$source_kind <- "another_source"
expect_failure(press_coverage_validate_proof(invalid_empty, FLOOR))

# No inferred year, cross-host/changed-ID detail, duplicate IDs, thin page,
# missing pager, changed filter, or generic empty HTML can prove coverage.
for (replacement in c("<html>temporary service failure</html>",
  sub("<span class='errormsg'></span>",
      "<span class='errormsg'></span><a href='/news/documentsingle.aspx?DocumentID=999'><img src='x'></a>",
      h_empty[[press_official_listing_endpoint(h_kind, 2L)]], fixed = TRUE),
  sub("value='27'", "value='1951'", h_empty[[press_official_listing_endpoint(h_kind, 2L)]], fixed = TRUE),
  sub("class='errormsg'", "class='unexpected'", h_empty[[press_official_listing_endpoint(h_kind, 2L)]], fixed = TRUE))) {
  broken <- h_empty; broken[[press_official_listing_endpoint(h_kind, 2L)]] <- replacement
  expect_failure(press_official_collect_member(h_config, FLOOR, request_from(broken), TODAY))
}
for (ids in list(h_ids[1:9], rep(h_ids[1L], 10L))) {
  broken <- h_responses
  broken[[press_official_listing_endpoint(h_kind, 1L)]] <- h_cards(ids, h_dates[seq_along(ids)], 1L, 2L)
  expect_failure(press_official_collect_member(h_config, FLOOR, request_from(broken), TODAY))
}
extra_link <- h_responses
extra_link[[press_official_listing_endpoint(h_kind, 1L)]] <- sub(
  "<div id='newsdoclist'>", paste0("<div id='newsdoclist'><a href='", h_url(4999),
    "'>Unparsed extra release</a>"), extra_link[[press_official_listing_endpoint(h_kind, 1L)]], fixed = TRUE)
expect_failure(press_official_collect_member(h_config, FLOOR, request_from(extra_link), TODAY))
for (detail in c("<meta property='og:title' content='Houlahan official release 4001'>",
  sub("2026-09-12", "2026-09-11", h_responses[[h_url(4001)]], fixed = TRUE),
  sub("Houlahan official release 4001", "Wrong identity", h_responses[[h_url(4001)]], fixed = TRUE))) {
  broken <- h_responses; broken[[h_url(4001)]] <- detail
  expect_failure(press_official_collect_member(h_config, FLOOR, request_from(broken), TODAY))
}
base_request <- request_from(h_responses)
expect_failure(press_official_collect_member(h_config, FLOOR, function(url) {
  response <- base_request(url)
  if (url == h_url(4001)) response$url <- h_url(9999)
  response
}, TODAY))
expect_failure(press_official_collect_member(h_config, FLOOR, function(url) {
  response <- base_request(url); response$url <- sub("houlahan.house.gov", "example.com", url, fixed = TRUE)
  response
}, TODAY))
thin_body <- h_responses
thin_body[[h_url(4001)]] <- sub(body_one, "<p>Short shell</p>", thin_body[[h_url(4001)]], fixed = TRUE)
thin <- press_official_collect_member(h_config, FLOOR, request_from(thin_body), TODAY)
stopifnot(thin$records$press_official_body[1L] == "", thin$records$press_official_body_sha256[1L] == "")
h_seed <- new.env(parent = globalenv())
h_seed$CHECKPOINT_DIR <- tempfile("press-houlahan-seed-")
h_seed$write_rds_atomic <- write_atomic
press_official_seed_checkpoints(h_result$records, h_result$records, h_seed)
h_cache_path <- file.path(h_seed$CHECKPOINT_DIR, "fulltext", "H001085.rds")
h_cached <- readRDS(h_cache_path)
stopifnot(nrow(h_cached) == 10L, identical(h_cached$date_detail, h_dates[1:10]),
  all(h_cached$press_official_body_source == "official_documentquery_detail"),
  length(press_official_checkpoint_body_evidence(h_result$records, h_seed)) == 10L)
prior_body <- h_cached$body[1L]
press_official_seed_checkpoints(thin$records, thin$records, h_seed)
stopifnot(identical(readRDS(h_cache_path)$body[1L], prior_body))

# Filter-preserving redirects and exact contiguous Next links are required.
expect_failure(press_official_collect_member(h_config, FLOOR, function(url) {
  response <- base_request(url)
  if (grepl("documentquery", url)) response$url <- sub("=27", "=1951", url, fixed = TRUE)
  response
}, TODAY))
for (next_page in list(NULL, 3L)) {
  broken <- h_responses
  broken[[press_official_listing_endpoint(h_kind, 1L)]] <- h_cards(h_ids[1:10], h_dates[1:10], 1L, next_page)
  expect_failure(press_official_collect_member(h_config, FLOOR, request_from(broken), TODAY))
}

# An entirely pre-floor inventory remains a valid zero-current result.
none_current <- press_official_collect_member(h_config, TODAY, request_from(h_responses), TODAY)
stopifnot(nrow(none_current$records) == 0L, none_current$proof$pages_fetched == 1L)
press_coverage_validate_proof(press_official_shared_coverage_proof(none_current), TODAY)
cat("PASS: Houlahan whitespace hrefs, exact detail dates, ten-ID pages, terminal proof, body preservation and fail-closed identity\n")

# Official WordPress custom-post intake for Senate sites whose press releases
# are not stored in the generic /wp/v2/posts collection.  The upstream scraper
# probes only that generic collection, so these sites otherwise fall back to a
# stale and noisy Wayback URL inventory with no publication dates.

PRESS_WORDPRESS_CUSTOM_MEMBERS <- data.frame(
  bioguide = c("O000174", "M001198", "S001184", "Y000064", "C001113"),
  origin = c(
    "https://www.ossoff.senate.gov",
    "https://www.marshall.senate.gov",
    "https://www.scott.senate.gov",
    "https://www.young.senate.gov",
    "https://www.cortezmasto.senate.gov"
  ),
  rest_base = rep("press_releases", 5L),
  stringsAsFactors = FALSE
)

press_wp_empty <- function() data.frame(
  bioguide = character(0), title = character(0), url = character(0),
  date = as.Date(character(0)), modified = character(0),
  title_src = character(0), press_wp_api = logical(0),
  press_wp_api_id = character(0), press_wp_endpoint = character(0),
  press_wp_content_sha256 = character(0), press_wp_body_usable = logical(0),
  press_wp_body = character(0),
  stringsAsFactors = FALSE
)

press_wp_url_key <- function(value) {
  value <- trimws(as.character(value))
  if (anyNA(value) || any(!grepl("^https?://[^/[:space:]]+", value, ignore.case = TRUE)))
    stop("invalid official WordPress press URL")
  tolower(sub("/$", "", sub("^https?://(www\\.)?", "", sub("#.*$", "", value),
                              ignore.case = TRUE)))
}

press_wp_same_host <- function(left, right) {
  host <- function(value) tolower(sub("^www\\.", "", sub("^https?://([^/]+).*$", "\\1", value)))
  identical(host(left), host(right))
}

press_wp_default_request <- function(url) {
  attempts <- 3L
  for (number in seq_len(attempts)) {
    response <- tryCatch(
      httr::GET(url, httr::user_agent(CHROME_UA), httr::timeout(HTTP_TIMEOUT),
                httr::add_headers(Accept = "application/json")),
      error = function(error) NULL
    )
    if (!is.null(response) && httr::status_code(response) == 200L) {
      type <- as.character(httr::headers(response)[["content-type"]])
      if (!length(type) || !grepl("application/json", type, ignore.case = TRUE))
        stop("official WordPress endpoint returned non-JSON content: ", url)
      return(list(
        status = 200L,
        headers = httr::headers(response),
        text = httr::content(response, as = "text", encoding = "UTF-8"),
        url = response$url
      ))
    }
    if (number < attempts) Sys.sleep(1.5 * number)
  }
  status <- if (is.null(response)) "network error" else as.character(httr::status_code(response))
  stop("official WordPress endpoint unavailable (", status, "): ", url)
}

press_wp_header_integer <- function(response, name) {
  headers <- response$headers
  candidates <- c(name, tolower(name), toupper(name))
  value <- NULL
  for (candidate in candidates) {
    if (!is.null(headers[[candidate]])) {
      value <- unlist(headers[[candidate]], use.names = FALSE)
      break
    }
  }
  if (is.null(value) || length(value) != 1L || !grepl("^[0-9]+$", as.character(value)))
    stop("official WordPress response lacks valid ", name)
  parsed <- suppressWarnings(as.integer(value))
  if (is.na(parsed) || parsed < 0L) stop("invalid official WordPress ", name)
  parsed
}

press_wp_json <- function(response, endpoint) {
  if (is.null(response$status) || response$status != 200L ||
      length(response$text) != 1L || is.na(response$text) || !nzchar(response$text))
    stop("invalid official WordPress response: ", endpoint)
  tryCatch(
    jsonlite::fromJSON(response$text, simplifyVector = FALSE),
    error = function(error) stop("invalid official WordPress JSON: ", endpoint)
  )
}

press_wp_request_evidence <- function(response, endpoint, request_index, role,
                                      item_count, page = 0L,
                                      declared_total = NULL,
                                      declared_pages = NULL,
                                      min_date = NULL, max_date = NULL) {
  final_url <- if (!is.null(response$url) && length(response$url) == 1L)
    as.character(response$url) else endpoint
  if (!press_wp_same_host(final_url, endpoint))
    stop("official WordPress request crossed host boundary: ", endpoint)
  evidence <- list(
    request_index = as.integer(request_index), role = role,
    endpoint = endpoint, final_url = final_url,
    raw_response_sha256 = press_coverage_sha256_text(response$text),
    item_count = as.integer(item_count), page = as.integer(page)
  )
  if (!is.null(declared_total)) evidence$declared_total <- as.integer(declared_total)
  if (!is.null(declared_pages)) evidence$declared_pages <- as.integer(declared_pages)
  if (!is.null(min_date)) evidence$min_date <- as.character(min_date)
  if (!is.null(max_date)) evidence$max_date <- as.character(max_date)
  evidence
}

press_wp_plain_text <- function(value) {
  if (length(value) != 1L || is.na(value) || !nzchar(value)) return("")
  page <- tryCatch(xml2::read_html(paste0("<article>", value, "</article>")),
                   error = function(error) NULL)
  if (is.null(page)) stop("official WordPress content is not parseable HTML")
  xml2::xml_remove(rvest::html_elements(page, "script, style, noscript"))
  text <- rvest::html_text2(rvest::html_element(page, "article"))
  if (length(text) != 1L || is.na(text)) "" else squish(text)
}

press_wp_usable_body <- function(value) {
  # Match the publication's 20-word floor and the official RSS adapter's
  # 200-character guard. Empty/rendered teaser content proves an inventory
  # entry, not a fetched article; the normal detail collector must handle it.
  value <- trimws(as.character(value))
  usable <- !is.na(value) & nchar(value) >= 200L
  usable[is.na(usable)] <- FALSE
  for (number in which(usable))
    usable[[number]] <- length(strsplit(value[[number]], "[[:space:]]+")[[1L]]) >= 20L
  usable
}

press_wp_verify_type <- function(origin, rest_base, request) {
  endpoint <- paste0(origin, "/wp-json/wp/v2/types")
  response <- request(endpoint)
  payload <- press_wp_json(response, endpoint)
  matches <- Filter(function(value) {
    is.list(value) && length(value$rest_base) == 1L &&
      identical(as.character(value$rest_base), rest_base)
  }, payload)
  if (length(matches) != 1L)
    stop("official WordPress custom post type is missing or ambiguous: ", origin,
         " rest_base=", rest_base)
  press_wp_request_evidence(response, endpoint, 1L, "type_registry",
                            length(payload), page = 0L)
}

press_wp_page_endpoint <- function(origin, rest_base, floor, page) {
  # WordPress `after` is exclusive. One second before local midnight includes
  # every release on the floor date without admitting an earlier calendar day.
  after <- paste0(as.character(floor - 1L), "T23:59:59")
  paste0(
    origin, "/wp-json/wp/v2/", rest_base,
    "?after=", utils::URLencode(after, reserved = TRUE),
    "&per_page=100&orderby=date&order=desc&page=", page,
    "&_fields=id,date,date_gmt,modified,link,slug,title,content"
  )
}

press_wp_parse_records <- function(payload, bioguide, origin, endpoint, floor) {
  if (!is.list(payload)) stop("official WordPress collection is not a JSON array: ", endpoint)
  if (!length(payload)) return(press_wp_empty())
  rows <- lapply(payload, function(record) {
    if (!is.list(record) || length(record$id) != 1L || length(record$date) != 1L ||
        length(record$link) != 1L || !is.list(record$title) ||
        length(record$title$rendered) != 1L || !is.list(record$content) ||
        length(record$content$rendered) != 1L || length(record$modified) != 1L)
      stop("official WordPress press record lacks required fields: ", endpoint)
    link <- as.character(record$link)
    if (!press_wp_same_host(link, origin))
      stop("official WordPress press record crosses host boundary: ", link)
    date_text <- substr(as.character(record$date), 1L, 10L)
    date <- suppressWarnings(as.Date(date_text, format = "%Y-%m-%d"))
    if (is.na(date) || !grepl("^[0-9]{4}-[0-9]{2}-[0-9]{2}$", date_text) ||
        as.character(date) != date_text || date < floor || date > Sys.Date())
      stop("official WordPress press record has an invalid publication date: ", link)
    title <- press_wp_plain_text(as.character(record$title$rendered))
    if (!nzchar(title)) stop("official WordPress press record has no title: ", link)
    body <- press_wp_plain_text(as.character(record$content$rendered))
    data.frame(
      bioguide = bioguide, title = title, url = link, date = date,
      modified = as.character(record$modified), title_src = "official_wp_api",
      press_wp_api = TRUE, press_wp_api_id = as.character(record$id),
      press_wp_endpoint = endpoint,
      press_wp_content_sha256 = digest::digest(body, algo = "sha256", serialize = FALSE),
      press_wp_body_usable = press_wp_usable_body(body),
      press_wp_body = body, stringsAsFactors = FALSE
    )
  })
  dplyr::bind_rows(rows)
}

press_wp_fetch_member <- function(bioguide, origin, rest_base, floor, request) {
  type_evidence <- press_wp_verify_type(origin, rest_base, request)
  first_endpoint <- press_wp_page_endpoint(origin, rest_base, floor, 1L)
  first_response <- request(first_endpoint)
  pages <- press_wp_header_integer(first_response, "X-WP-TotalPages")
  total <- press_wp_header_integer(first_response, "X-WP-Total")
  expected_pages <- if (total) as.integer(ceiling(total / 100)) else pages
  if (pages != expected_pages || (total > 0L && pages < 1L) ||
      (total == 0L && !pages %in% c(0L, 1L)))
    stop("official WordPress pagination headers are inconsistent: ", origin)
  responses <- list(first_response)
  endpoints <- list(first_endpoint)
  payloads <- list(press_wp_json(first_response, first_endpoint))
  if (pages > 1L) {
    for (page in 2L:pages) {
      endpoint <- press_wp_page_endpoint(origin, rest_base, floor, page)
      response <- request(endpoint)
      if (press_wp_header_integer(response, "X-WP-TotalPages") != pages ||
          press_wp_header_integer(response, "X-WP-Total") != total)
        stop("official WordPress pagination changed during collection: ", origin)
      responses[[page]] <- response
      endpoints[[page]] <- endpoint
      payloads[[page]] <- press_wp_json(response, endpoint)
    }
  }
  for (page in seq_along(payloads)) {
    expected <- if (total) min(100L, total - (page - 1L) * 100L) else 0L
    if (length(payloads[[page]]) != expected)
      stop("official WordPress page length is inconsistent: ", origin,
           " page=", page, " expected=", expected,
           " received=", length(payloads[[page]]))
  }
  records <- dplyr::bind_rows(lapply(seq_along(payloads), function(page)
    press_wp_parse_records(payloads[[page]], bioguide, origin,
                           press_wp_page_endpoint(origin, rest_base, floor, page), floor)))
  if (nrow(records) != total)
    stop("official WordPress collection was partial: ", origin, " expected=", total,
         " received=", nrow(records))
  if (nrow(records) && (anyDuplicated(records$press_wp_api_id) ||
                       anyDuplicated(press_wp_url_key(records$url))))
    stop("official WordPress collection contains duplicate identities: ", origin)
  if (nrow(records) > 1L && any(diff(as.numeric(records$date)) > 0))
    stop("official WordPress collection is not in descending publication order: ", origin)
  page_evidence <- lapply(seq_along(payloads), function(page) {
    member <- records[records$press_wp_endpoint == endpoints[[page]], , drop = FALSE]
    press_wp_request_evidence(
      responses[[page]], endpoints[[page]], page + 1L, "inventory_page",
      length(payloads[[page]]), page = page, declared_total = total,
      declared_pages = pages,
      min_date = if (nrow(member)) min(member$date) else NULL,
      max_date = if (nrow(member)) max(member$date) else NULL
    )
  })
  proof <- list(
    schema = "official-current-inventory-proof-v1",
    bioguide = bioguide,
    source_kind = "official_wordpress_custom_post",
    coverage_mode = "fully_paginated_after_floor",
    floor = as.character(as.Date(floor)),
    current_count = nrow(records),
    current_urls = sort(as.character(records$url)),
    current_url_set_sha256 = press_coverage_json_hash(sort(press_wp_url_key(records$url))),
    requests = c(list(type_evidence), page_evidence),
    completeness = list(
      pagination_complete = TRUE,
      date_order_verified = TRUE,
      identity_complete = TRUE,
      after_floor_query_verified = TRUE,
      declared_total = total,
      declared_pages = pages,
      received_total = nrow(records)
    )
  )
  press_coverage_validate_proof(proof, floor)
  attr(records, "official_coverage_proof") <- proof
  records
}

press_wp_collect <- function(members, floor, request, envir = .GlobalEnv) {
  if (!"bioguide" %in% names(members)) stop("press roster lacks bioguide identity")
  outputs <- list()
  configured <- PRESS_WORDPRESS_CUSTOM_MEMBERS[
    PRESS_WORDPRESS_CUSTOM_MEMBERS$bioguide %in% members$bioguide, , drop = FALSE]
  for (number in seq_len(nrow(configured))) {
    config <- configured[number, ]
    member <- members[members$bioguide == config$bioguide, , drop = FALSE]
    if (nrow(member) != 1L) stop("official WordPress member identity is ambiguous: ", config$bioguide)
    if (!"url" %in% names(member) || !press_wp_same_host(member$url[[1]], config$origin))
      stop("official WordPress origin no longer matches the verified roster: ", config$bioguide)
    rows <- press_wp_fetch_member(config$bioguide, config$origin, config$rest_base, floor, request)
    press_official_coverage_register(attr(rows, "official_coverage_proof"), envir)
    for (name in intersect(c("member", "chamber", "state", "party"), names(member)))
      rows[[name]] <- rep(member[[name]][[1]], nrow(rows))
    outputs[[length(outputs) + 1L]] <- rows
  }
  if (length(outputs)) dplyr::bind_rows(outputs) else press_wp_empty()
}

press_wp_seed_checkpoints <- function(index, records, envir) {
  if (!nrow(index) || !nrow(records) || !"press_wp_api" %in% names(index)) return(invisible(NULL))
  selected <- index$press_wp_api %in% TRUE
  selected[is.na(selected)] <- FALSE
  if (!any(selected)) return(invisible(NULL))
  index <- index[selected, , drop = FALSE]
  record_key <- paste(records$bioguide, press_wp_url_key(records$url), sep = "#")
  index_key <- paste(index$bioguide, press_wp_url_key(index$url), sep = "#")
  matched <- match(index_key, record_key)
  if (anyNA(matched)) stop("official WordPress body evidence is missing from the bound response")
  records <- records[matched, , drop = FALSE]
  if (!identical(as.character(index$press_wp_content_sha256),
                 as.character(records$press_wp_content_sha256)))
    stop("official WordPress body hash no longer matches index evidence")
  actual_hashes <- vapply(records$press_wp_body, function(body)
    digest::digest(body, algo = "sha256", serialize = FALSE), character(1L))
  if (!identical(unname(actual_hashes), as.character(records$press_wp_content_sha256)))
    stop("official WordPress body content changed before checkpoint seed")
  usable <- press_wp_usable_body(records$press_wp_body)
  if (!identical(usable, as.logical(records$press_wp_body_usable)))
    stop("official WordPress body usability no longer matches collected evidence")
  # Never replace a retained good checkpoint with missing/short API content.
  # Keep every URL in the index; an unseeded URL without a completed checkpoint
  # remains eligible for the normal detail GET and its configured fallbacks.
  records <- records[usable, , drop = FALSE]
  if (!nrow(records)) return(invisible(NULL))
  writer <- get("write_rds_atomic", envir = envir, inherits = TRUE)
  checkpoint_root <- get("CHECKPOINT_DIR", envir = envir, inherits = TRUE)
  dir.create(file.path(checkpoint_root, "fulltext"), recursive = TRUE, showWarnings = FALSE)
  for (bg in unique(records$bioguide)) {
    path <- file.path(checkpoint_root, "fulltext", paste0(bg, ".rds"))
    previous <- if (file.exists(path)) readRDS(path) else data.frame()
    if (!is.data.frame(previous) || (nrow(previous) && !"url" %in% names(previous)))
      stop("invalid press fulltext checkpoint: ", bg)
    member <- records[records$bioguide == bg, , drop = FALSE]
    seeded <- data.frame(
      url = member$url, title_detail = member$title, date_detail = member$date,
      body = member$press_wp_body, stringsAsFactors = FALSE
    )
    merged <- dplyr::bind_rows(previous, seeded)
    keys <- press_wp_url_key(merged$url)
    merged <- merged[!duplicated(keys, fromLast = TRUE), , drop = FALSE]
    writer(merged, path)
  }
  invisible(NULL)
}

install_press_wordpress_custom_posts <- function(envir = .GlobalEnv,
                                                  request = press_wp_default_request) {
  original_index <- get("stage_index", envir = envir, inherits = FALSE)
  if (isTRUE(attr(original_index, "melange_press_wordpress_custom_posts")))
    return(invisible(NULL))
  original_fulltext <- get("stage_fulltext", envir = envir, inherits = FALSE)
  state <- new.env(parent = emptyenv())
  state$records <- press_wp_empty()

  index_stage <- function(members, disc, force = FALSE) {
    base <- original_index(members, disc, force = force)
    floor <- get("DATE_FLOOR", envir = envir, inherits = TRUE)
    records <- press_wp_collect(members, floor, request, envir)
    state$records <- records
    if (!nrow(records)) return(base)
    metadata <- records[, setdiff(names(records), "press_wp_body"), drop = FALSE]
    combined <- dplyr::bind_rows(base, metadata)
    keys <- paste(combined$bioguide, press_wp_url_key(combined$url), sep = "#")
    combined <- combined[!duplicated(keys, fromLast = TRUE), , drop = FALSE]
    message("Official WordPress press API: ", nrow(records),
            " current dated release(s) with body evidence across ",
            length(unique(records$bioguide)), " member(s).")
    combined
  }
  attr(index_stage, "melange_press_wordpress_custom_posts") <- TRUE

  fulltext_stage <- function(idx, force = FALSE, js_bgs = character(0)) {
    press_wp_seed_checkpoints(idx, state$records, envir)
    original_fulltext(idx, force = force, js_bgs = js_bgs)
  }
  attr(fulltext_stage, "melange_press_wordpress_custom_posts") <- TRUE
  assign("stage_index", index_stage, envir = envir)
  assign("stage_fulltext", fulltext_stage, envir = envir)
  invisible(NULL)
}

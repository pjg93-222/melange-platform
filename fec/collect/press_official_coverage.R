# Shared proof contract for authoritative official press-release inventories.
#
# An adapter may establish only that its fully enumerated official source is the
# complete current inventory at DATE_FLOOR.  URLs outside that inventory keep
# their original metadata and remain available to bounded historical
# maintenance; absence never becomes an invented publication date.

press_coverage_url_key <- function(value) {
  value <- trimws(as.character(value))
  if (anyNA(value) || any(!grepl("^https?://[^/[:space:]]+", value, ignore.case = TRUE)))
    stop("invalid official coverage URL identity")
  tolower(sub("^https?://(www\\.)?", "", sub("/$", "", sub("#.*$", "", value)),
               ignore.case = TRUE))
}

press_coverage_sha256_text <- function(value) {
  digest::digest(enc2utf8(as.character(value)), algo = "sha256", serialize = FALSE)
}

press_coverage_json_hash <- function(value) {
  press_coverage_sha256_text(jsonlite::toJSON(
    value, auto_unbox = TRUE, null = "null", na = "null", digits = NA
  ))
}

press_coverage_valid_sha256 <- function(value) {
  length(value) == 1L && !is.na(value) && grepl("^[a-f0-9]{64}$", value)
}

press_coverage_validate_request <- function(request, index) {
  required <- c("request_index", "role", "endpoint", "final_url",
                "raw_response_sha256", "item_count")
  if (!is.list(request) || !all(required %in% names(request)))
    stop("official coverage request evidence lacks required fields")
  if (length(request$request_index) != 1L || is.na(request$request_index) ||
      as.integer(request$request_index) != index)
    stop("official coverage request evidence is not contiguous")
  if (length(request$role) != 1L || is.na(request$role) || !nzchar(request$role))
    stop("official coverage request role is invalid")
  for (name in c("endpoint", "final_url")) {
    value <- as.character(request[[name]])
    if (length(value) != 1L || is.na(value) ||
        !grepl("^https?://[^/[:space:]]+", value, ignore.case = TRUE))
      stop("official coverage request URL is invalid")
  }
  if (!press_coverage_valid_sha256(as.character(request$raw_response_sha256)))
    stop("official coverage response hash is invalid")
  count <- suppressWarnings(as.integer(request$item_count))
  if (length(count) != 1L || is.na(count) || count < 0L ||
      !identical(as.numeric(count), as.numeric(request$item_count)))
    stop("official coverage request item count is invalid")
  invisible(TRUE)
}

press_coverage_validate_proof <- function(proof, floor = NULL) {
  required <- c("schema", "bioguide", "source_kind", "coverage_mode", "floor",
                "current_count", "current_urls", "current_url_set_sha256",
                "requests", "completeness")
  if (!is.list(proof) || !all(required %in% names(proof)) ||
      !identical(proof$schema, "official-current-inventory-proof-v1"))
    stop("invalid official current-inventory proof")
  bg <- as.character(proof$bioguide)
  if (length(bg) != 1L || is.na(bg) || !grepl("^[A-Z][0-9]{6}$", bg))
    stop("invalid official coverage member identity")
  if (length(proof$source_kind) != 1L || is.na(proof$source_kind) ||
      !nzchar(proof$source_kind) ||
      !proof$coverage_mode %in% c("fully_paginated_after_floor",
                                  "sequential_dated_listing_terminal_prefloor",
                                  "sequential_dated_listing_terminal_empty"))
    stop("invalid official coverage source contract")
  proof_floor <- suppressWarnings(as.Date(as.character(proof$floor), format = "%Y-%m-%d"))
  if (length(proof_floor) != 1L || is.na(proof_floor) ||
      as.character(proof_floor) != as.character(proof$floor) ||
      (!is.null(floor) && proof_floor != as.Date(floor)))
    stop("official coverage floor mismatch")
  urls <- as.character(unlist(proof$current_urls, use.names = FALSE))
  if (anyNA(urls) || (length(urls) && anyDuplicated(press_coverage_url_key(urls))))
    stop("official coverage current inventory has duplicate or invalid URLs")
  count <- suppressWarnings(as.integer(proof$current_count))
  if (length(count) != 1L || is.na(count) || count != length(urls))
    stop("official coverage current inventory count mismatch")
  expected_set_hash <- press_coverage_json_hash(sort(press_coverage_url_key(urls)))
  if (!identical(as.character(proof$current_url_set_sha256), expected_set_hash))
    stop("official coverage current URL-set hash mismatch")
  requests <- proof$requests
  if (!is.list(requests) || !length(requests))
    stop("official coverage proof has no request evidence")
  for (number in seq_along(requests)) press_coverage_validate_request(requests[[number]], number)
  if (!is.list(proof$completeness) ||
      !isTRUE(proof$completeness$pagination_complete) ||
      !isTRUE(proof$completeness$date_order_verified) ||
      !isTRUE(proof$completeness$identity_complete))
    stop("official coverage completeness checks did not pass")
  if (identical(proof$coverage_mode, "fully_paginated_after_floor") &&
      !isTRUE(proof$completeness$after_floor_query_verified))
    stop("official after-floor coverage proof is incomplete")
  if (identical(proof$coverage_mode, "sequential_dated_listing_terminal_prefloor") &&
      !isTRUE(proof$completeness$terminal_page_wholly_prefloor))
    stop("official dated-listing proof lacks a wholly pre-floor terminal page")
  if (identical(proof$coverage_mode, "sequential_dated_listing_terminal_empty")) {
    inventory <- Filter(function(item) identical(item$role, "inventory_page"), requests)
    boundary <- proof$completeness$boundary_page
    if (!identical(proof$source_kind, "houlahan_official_documentquery") ||
        !identical(bg, "H001085") ||
        !isTRUE(proof$completeness$terminal_empty_page_verified) ||
        !isTRUE(boundary >= 2L) || length(inventory) != boundary)
      stop("official dated-listing proof lacks a verified empty terminal page")
    for (number in seq_along(inventory)) {
      item <- inventory[[number]]
      endpoint <- paste0("https://houlahan.house.gov/press/documentquery.aspx?DocumentTypeID=27&Page=", number)
      if (!isTRUE(item$page == number) ||
          !isTRUE(item$item_count == if (number == boundary) 0L else 10L) ||
          !identical(tolower(item$endpoint), tolower(endpoint)) ||
          !identical(tolower(item$final_url), tolower(endpoint)))
        stop("official empty terminal inventory is not a contiguous Houlahan archive")
    }
  }
  invisible(TRUE)
}

press_official_coverage_reset <- function(envir = .GlobalEnv) {
  assign("PRESS_OFFICIAL_COVERAGE", list(
    schema = "official-current-inventory-registry-v1", proofs = list()
  ), envir = envir)
  invisible(NULL)
}

press_official_coverage_registry <- function(envir = .GlobalEnv) {
  if (!exists("PRESS_OFFICIAL_COVERAGE", envir = envir, inherits = FALSE))
    press_official_coverage_reset(envir)
  registry <- get("PRESS_OFFICIAL_COVERAGE", envir = envir, inherits = FALSE)
  if (!is.list(registry) ||
      !identical(registry$schema, "official-current-inventory-registry-v1") ||
      !is.list(registry$proofs))
    stop("invalid official coverage registry")
  registry
}

press_official_coverage_register <- function(proof, envir = .GlobalEnv) {
  floor <- if (exists("DATE_FLOOR", envir = envir, inherits = TRUE))
    get("DATE_FLOOR", envir = envir, inherits = TRUE) else NULL
  press_coverage_validate_proof(proof, floor)
  registry <- press_official_coverage_registry(envir)
  bg <- as.character(proof$bioguide)
  prior <- registry$proofs[[bg]]
  if (!is.null(prior) && !identical(press_coverage_json_hash(prior),
                                     press_coverage_json_hash(proof)))
    stop("conflicting official coverage proofs for member: ", bg)
  registry$proofs[[bg]] <- proof
  registry$proofs <- registry$proofs[sort(names(registry$proofs))]
  assign("PRESS_OFFICIAL_COVERAGE", registry, envir = envir)
  invisible(proof)
}

press_official_coverage_snapshot <- function(envir = .GlobalEnv) {
  registry <- press_official_coverage_registry(envir)
  floor <- if (exists("DATE_FLOOR", envir = envir, inherits = TRUE))
    get("DATE_FLOOR", envir = envir, inherits = TRUE) else NULL
  for (proof in registry$proofs) press_coverage_validate_proof(proof, floor)
  proofs <- lapply(unname(registry$proofs), function(proof) {
    # Preserve these fields as JSON arrays even when a future source has only
    # one current URL or one request.
    proof$current_urls <- unname(as.list(as.character(unlist(
      proof$current_urls, use.names = FALSE))))
    proof$requests <- unname(as.list(proof$requests))
    proof
  })
  list(
    schema = "official-current-inventory-snapshot-v1",
    floor = if (is.null(floor)) NULL else as.character(as.Date(floor)),
    member_count = length(registry$proofs),
    current_url_count = sum(vapply(registry$proofs, function(proof)
      as.integer(proof$current_count), integer(1))),
    members = proofs
  )
}

press_official_coverage_import <- function(snapshot, envir = .GlobalEnv) {
  if (!is.list(snapshot) ||
      !identical(snapshot$schema, "official-current-inventory-snapshot-v1") ||
      !is.list(snapshot$members) ||
      as.integer(snapshot$member_count) != length(snapshot$members))
    stop("invalid official coverage snapshot")
  press_official_coverage_reset(envir)
  for (proof in snapshot$members) press_official_coverage_register(proof, envir)
  rebuilt <- press_official_coverage_snapshot(envir)
  if (as.integer(snapshot$current_url_count) != rebuilt$current_url_count ||
      !identical(as.character(snapshot$floor), as.character(rebuilt$floor)))
    stop("official coverage snapshot count or floor mismatch")
  invisible(rebuilt)
}

press_official_coverage_classify <- function(index, floor, envir = .GlobalEnv) {
  if (!all(c("bioguide", "url", "date") %in% names(index)))
    stop("press index lacks official coverage identity/date columns")
  registry <- press_official_coverage_registry(envir)
  status <- rep("blocking_current_uncertainty", nrow(index))
  known <- !is.na(index$date)
  status[known & as.Date(index$date) < as.Date(floor)] <- "historical_pre_floor"
  status[known & as.Date(index$date) >= as.Date(floor)] <- "dated_current_uncovered"
  for (bg in names(registry$proofs)) {
    proof <- registry$proofs[[bg]]
    press_coverage_validate_proof(proof, floor)
    member <- which(index$bioguide == bg)
    if (!length(member)) next
    current <- press_coverage_url_key(index$url[member]) %in%
      press_coverage_url_key(unlist(proof$current_urls, use.names = FALSE))
    status[member] <- "historical_outside_current_inventory"
    status[member[current]] <- "official_current_inventory"
  }
  status
}

press_official_coverage_alias_map <- function(candidate_urls, official_urls) {
  # Slugs are a diagnostic aid only. A slug maps when both sides are unique;
  # ambiguous aliases never acquire an official-current identity or date.
  slug <- function(value) {
    path <- sub("[?#].*$", "", as.character(value))
    path <- sub("/$", "", path)
    tolower(utils::URLdecode(sub("^.*/", "", path)))
  }
  official_slug <- slug(official_urls)
  candidate_slug <- slug(candidate_urls)
  official_counts <- table(official_slug)
  candidate_counts <- table(candidate_slug)
  mapped <- rep(NA_integer_, length(candidate_urls))
  for (number in seq_along(candidate_urls)) {
    value <- candidate_slug[number]
    if (nzchar(value) && identical(unname(official_counts[value]), 1L) &&
        identical(unname(candidate_counts[value]), 1L))
      mapped[number] <- match(value, official_slug)
  }
  mapped
}

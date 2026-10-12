# Pure coverage-contract fixture. No network or workspace output paths.
args <- commandArgs(trailingOnly = TRUE)
stopifnot(length(args) == 1L)
.libPaths(c(file.path(dirname(args[[1]]), "..", "..", "outputs", "r-library"), .libPaths()))
env <- new.env(parent = globalenv())
source(args[[1]], local = env)
env$DATE_FLOOR <- as.Date("2026-06-25")

make_proof <- function(bg = "X000001", urls = c(
    "https://example.gov/news/current-a/", "https://example.gov/news/current-b/")) {
  list(
    schema = "official-current-inventory-proof-v1", bioguide = bg,
    source_kind = "fixture_official_api", coverage_mode = "fully_paginated_after_floor",
    floor = "2026-06-25", current_count = length(urls), current_urls = sort(urls),
    current_url_set_sha256 = env$press_coverage_json_hash(sort(env$press_coverage_url_key(urls))),
    requests = list(list(
      request_index = 1L, role = "inventory_page", endpoint = "https://example.gov/api?page=1",
      final_url = "https://example.gov/api?page=1", raw_response_sha256 = strrep("a", 64L),
      item_count = length(urls), page = 1L, declared_total = length(urls), declared_pages = 1L
    )),
    completeness = list(pagination_complete = TRUE, date_order_verified = TRUE,
      identity_complete = TRUE, after_floor_query_verified = TRUE)
  )
}

proof <- make_proof()
env$press_official_coverage_register(proof, env)
snapshot <- env$press_official_coverage_snapshot(env)
stopifnot(snapshot$member_count == 1L, snapshot$current_url_count == 2L,
          snapshot$members[[1]]$bioguide == "X000001")

index <- data.frame(
  bioguide = c(rep("X000001", 3L), rep("X000002", 3L)),
  url = c(proof$current_urls[[1]], "https://example.gov/archive/unknown",
          "https://example.gov/archive/known-old", "https://other.gov/unknown",
          "https://other.gov/old", "https://other.gov/current"),
  date = as.Date(c("2026-09-01", NA, "2026-01-01", NA, "2026-01-01", "2026-09-01")),
  stringsAsFactors = FALSE)
status <- env$press_official_coverage_classify(index, env$DATE_FLOOR, env)
stopifnot(identical(status, c(
  "official_current_inventory", "historical_outside_current_inventory",
  "historical_outside_current_inventory", "blocking_current_uncertainty",
  "historical_pre_floor", "dated_current_uncovered")))

# A unique slug can be inspected as an alias, while duplicate slugs never map.
official <- c("https://example.gov/news/a", "https://example.gov/news/b")
stopifnot(identical(env$press_official_coverage_alias_map(
  c("https://example.gov/es/news/a", "https://example.gov/es/news/c"), official), c(1L, NA_integer_)))
stopifnot(all(is.na(env$press_official_coverage_alias_map(
  c("https://example.gov/es/news/a", "https://example.gov/fr/news/a"), official))))
stopifnot(all(is.na(env$press_official_coverage_alias_map(
  "https://example.gov/es/news/a",
  c("https://example.gov/news/a", "https://example.gov/releases/a")))))

# Any mutation of the floor, response hash, current set, or completeness state
# invalidates the proof rather than reopening an absence-based inference.
for (kind in c("floor", "response_hash", "set_hash", "pagination")) {
  invalid <- proof
  if (kind == "floor") invalid$floor <- "2026-06-26"
  if (kind == "response_hash") invalid$requests[[1]]$raw_response_sha256 <- "bad"
  if (kind == "set_hash") invalid$current_urls[[1]] <- "https://example.gov/news/mutated"
  if (kind == "pagination") invalid$completeness$pagination_complete <- FALSE
  stopifnot(inherits(try(env$press_coverage_validate_proof(invalid, env$DATE_FLOOR), silent = TRUE),
                     "try-error"))
}

imported <- new.env(parent = env)
imported$DATE_FLOOR <- env$DATE_FLOOR
env$press_official_coverage_import(snapshot, imported)
stopifnot(env$press_coverage_json_hash(env$press_official_coverage_snapshot(imported)) ==
          env$press_coverage_json_hash(snapshot))
cat("PASS: official current inventory proof, retained historical classification, alias ambiguity, and invalidation\n")

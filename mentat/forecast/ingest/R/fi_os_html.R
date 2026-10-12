# OpenSecrets page HTML -> capture envelope (FEEDS_SPEC 2.2, 2.3, 3.3). Used by the manual-save route and by the fetcher; it only
# reads text (no network). The pages are server-rendered: every table row is in the HTTP response and the client-side DataTables
# script pages it down to about 25 rows, so the parser reads the source HTML and never needs JavaScript.
#
# The envelope is the JSON shape the Codex captures use, so the refit's rf_os_totals / rf_os_candidates / rf_os_presidential read
# it unchanged: {url, final_url, title, retrieved_utc, http, route, source_file_sha256, source_tables, tables, fec_release_note,
# parser, plus the page identity (os_code, cycle, kind, page)}.

FI_OS_BASE <- "https://www.opensecrets.org"
FI_OS_KINDS <- c("totals", "candidates_H", "candidates_S", "presidential")

# All <table> elements of an HTML document: list(rows = list of cell-text vectors, row_links = list of href vectors). This is the
# Codex TableParser (collect.py:7-23): one entry per <tr>, cells are td / th text with whitespace collapsed, links are the hrefs of
# <a> elements inside the row.
fi_os_parse_tables <- function(html) {
  doc <- xml2::read_html(html, encoding = "UTF-8")
  lapply(xml2::xml_find_all(doc, "//table"), function(tb) {
    trs <- xml2::xml_find_all(tb, ".//tr")
    list(rows = lapply(trs, function(tr) fi_clean_cell(xml2::xml_text(xml2::xml_find_all(tr, "./td|./th")))),
         row_links = lapply(trs, function(tr) { h <- xml2::xml_attr(xml2::xml_find_all(tr, ".//a[@href]"), "href"); h[!is.na(h) & nzchar(h)] }))
  })
}

# Page identity and notes: title, the hero heading, canonical URL, og:url, breadcrumb trail, the FEC release sentence, and the
# "Showing 1 to N of M entries" marker of a rendered (saved) DataTable.
fi_os_page_meta <- function(html) {
  doc <- xml2::read_html(html, encoding = "UTF-8")
  first_text <- function(xp) { n <- xml2::xml_find_first(doc, xp); if (inherits(n, "xml_missing")) NA_character_ else fi_clean_cell(xml2::xml_text(n)) }
  first_attr <- function(xp, a) { n <- xml2::xml_find_first(doc, xp); if (inherits(n, "xml_missing")) NA_character_ else xml2::xml_attr(n, a) }
  crumbs <- xml2::xml_find_all(doc, "//*[contains(concat(' ', normalize-space(@class), ' '), ' Breadcrumbs-link ')]")
  body <- fi_clean_cell(xml2::xml_text(xml2::xml_find_first(doc, "//body")))
  rel <- regmatches(body, regexpr("Data for the current election cycle were released by the Federal Election Commission on [A-Za-z]+ [0-9]{1,2}, [0-9]{4}", body))
  mk <- regmatches(body, regexec("Showing [0-9,]+ to [0-9,]+ of ([0-9,]+) entries", body))[[1]]
  list(title = first_text("//title"), h1 = first_text("//h1[contains(@class,'Hero-title')]"), canonical = first_attr("//link[@rel='canonical']", "href"),
       og_url = first_attr("//meta[@property='og:url']", "content"),
       breadcrumbs = lapply(seq_along(crumbs), function(i) list(text = fi_clean_cell(xml2::xml_text(crumbs[[i]])), href = xml2::xml_attr(crumbs[[i]], "href"))),
       fec_release_note = if (length(rel)) rel else NA_character_,
       showing_of = if (length(mk) == 2L) as.integer(gsub(",", "", mk[2])) else NA_integer_)
}

# Query parameters of a URL as a named character vector.
fi_os_url_params <- function(url) {
  q <- sub("^[^?]*\\?", "", url)
  if (identical(q, url)) return(character())
  parts <- strsplit(strsplit(sub("#.*$", "", q), "&", fixed = TRUE)[[1]], "=", fixed = TRUE)
  stats::setNames(vapply(parts, function(p) if (length(p) > 1L) utils::URLdecode(p[2]) else "", ""), vapply(parts, `[`, "", 1L))
}

# Target of a page URL: code (ind= or code=), cycle, kind. NA when the URL is not one of the four page types.
fi_os_target_from_url <- function(url) {
  p <- fi_os_url_params(url)
  path <- sub("^https?://[^/]+", "", sub("\\?.*$", "", url))
  code <- if ("ind" %in% names(p)) p[["ind"]] else if ("code" %in% names(p)) p[["code"]] else NA_character_
  cycle <- if ("cycle" %in% names(p)) suppressWarnings(as.integer(p[["cycle"]])) else NA_integer_
  rd <- if ("recipdetail" %in% names(p)) p[["recipdetail"]] else NA_character_
  kind <- NA_character_
  if (grepl("^/industries/totals$", path)) kind <- "totals"
  else if (grepl("^/industries/summary$", path) && rd %in% c("H", "S")) kind <- paste0("candidates_", rd)
  else if (grepl("^/industries/recips$", path) && identical(rd, "P")) kind <- "presidential"
  list(code = code, cycle = cycle, kind = kind)
}

# The planned URL of a page (FEEDS_SPEC 2.2).
fi_os_url <- function(code, cycle, kind) {
  switch(kind,
         totals = sprintf("%s/industries/totals?cycle=%d&ind=%s", FI_OS_BASE, as.integer(cycle), code),
         candidates_H = sprintf("%s/industries/summary?code=%s&cycle=%d&ind=%s&mem=N&recipdetail=H", FI_OS_BASE, code, as.integer(cycle), code),
         candidates_S = sprintf("%s/industries/summary?code=%s&cycle=%d&ind=%s&mem=N&recipdetail=S", FI_OS_BASE, code, as.integer(cycle), code),
         presidential = sprintf("%s/industries/recips?code=%s&cycle=%d&ind=%s&recipdetail=P", FI_OS_BASE, code, as.integer(cycle), code),
         stop("fi_os_url: unknown page kind ", kind, call. = FALSE))
}

# A target like "F3400_2012_candidates_H.html" (the manual-save file-name convention) -> list(code, cycle, kind) or NULL.
fi_os_target_from_filename <- function(name) {
  m <- regmatches(basename(name), regexec("^([A-Z][0-9]{2,4})_([0-9]{4})_(totals|candidates_H|candidates_S|presidential)(?:[^/]*)\\.(?:html?|json)$", basename(name), perl = TRUE))[[1]]
  if (length(m) == 4L) list(code = m[2], cycle = as.integer(m[3]), kind = m[4]) else NULL
}

.fi_os_table <- function(tables, test) { hit <- Filter(test, tables); if (length(hit)) hit[[1]] else NULL }
.fi_os_header_is <- function(t, h) length(t$rows) > 0L && identical(unlist(t$rows[[1]]), h)

# Validate a parsed page against the checks of FEEDS_SPEC 3.3. -> list(issues, checks, table) where table is the data table.
fi_os_validate <- function(kind, tables, meta) {
  issues <- character(); checks <- list()
  add <- function(name, examined, failures, detail = "") checks[[length(checks) + 1L]] <<- rf_check(name, examined, failures, detail)
  tb <- NULL
  if (kind == "totals") {
    tb <- .fi_os_table(tables, function(t) length(t$rows) > 0L && identical(unlist(t$rows[[1]])[1], "Election Cycle"))
    if (is.null(tb)) return(list(issues = "totals_table_not_found", checks = list(), table = NULL))
    ok_hdr <- .fi_os_header_is(tb, RF_TOTALS_HEADER)
    add("totals header", 1L, as.integer(!ok_hdr))
    if (!ok_hdr) issues <- c(issues, "unexpected_totals_header")
    rows <- tb$rows[-1]
    if (ok_hdr && length(rows)) {
      m <- do.call(rbind, lapply(rows, function(r) { r <- unlist(r); if (length(r) < 7L) rep(NA_character_, 7L) else r[1:7] }))
      a <- suppressWarnings(apply(m[, 2:7, drop = FALSE], 2, rf_money_int))
      if (!is.matrix(a)) a <- matrix(a, nrow = 1L)
      bad_money <- sum(is.na(a))
      add("totals money parses", length(a), bad_money)
      if (bad_money) issues <- c(issues, "unparseable_money")
      ident <- a[, 1] == a[, 2] + a[, 3] + a[, 4]
      n_bad <- sum(!ident | is.na(ident))
      add("total == individuals + PACs + outside", nrow(a), n_bad)
      if (n_bad) issues <- c(issues, "totals_identity_fails")
      cyc <- suppressWarnings(as.integer(m[, 1]))
      dup <- anyDuplicated(cyc) > 0L || any(is.na(cyc))
      add("cycles unique", length(cyc), as.integer(dup))
      if (dup) issues <- c(issues, "duplicate_or_missing_cycles")
    }
  } else if (kind %in% c("candidates_H", "candidates_S")) {
    tb <- .fi_os_table(tables, function(t) length(t$rows) > 0L && identical(tail(unlist(t$rows[[1]]), 2), c("State", "Amount")))
    if (is.null(tb)) return(list(issues = "recipient_table_not_found", checks = list(), table = NULL))
    hdr_expected <- if (kind == "candidates_H") "Representative" else "Senator"
    if (!identical(unlist(tb$rows[[1]])[1], hdr_expected)) issues <- c(issues, paste0("unexpected_recipient_header_for_", kind))
    rows <- tb$rows[-1]
    n3 <- sum(lengths(rows) != 3L)
    add("recipient rows have 3 cells", length(rows), n3)
    if (n3) issues <- c(issues, "recipient_row_not_3_cells")
    good <- rows[lengths(rows) == 3L]
    if (length(good)) {
      m <- do.call(rbind, lapply(good, unlist))
      bad_money <- sum(is.na(suppressWarnings(rf_money_int(m[, 3]))))
      add("recipient amounts parse", nrow(m), bad_money)
      if (bad_money) issues <- c(issues, "unparseable_money")
      no_party <- sum(!grepl("\\(([A-Z0-9]+)(?:-[A-Z]{2})?\\)$", m[, 1], perl = TRUE))
      add("recipient has a party code", nrow(m), no_party)
      if (no_party) issues <- c(issues, "recipient_without_party_code")
    }
    n_rows <- length(rows)
    truncated <- !is.na(meta$showing_of) && meta$showing_of > n_rows
    add("page holds every entry (truncated-save guard)", 1L, as.integer(truncated),
        if (truncated) sprintf("%d rows of %d entries", n_rows, meta$showing_of) else sprintf("%d rows", n_rows))
    if (truncated) issues <- c(issues, "truncated_save")
  } else if (kind == "presidential") {
    tb <- .fi_os_table(tables, function(t) .fi_os_header_is(t, c("Rank", "Recipient", "Chamber", "Total")))
    if (is.null(tb)) return(list(issues = "presidential_table_not_found", checks = list(), table = NULL))
    rows <- tb$rows[-1]
    if (length(rows)) {
      m <- do.call(rbind, lapply(rows, function(r) { r <- unlist(r); if (length(r) != 4L) rep(NA_character_, 4L) else r }))
      bad <- sum(is.na(m[, 1]) | m[, 3] != "President")
      add("presidential rows are President rows", nrow(m), bad)
      if (bad) issues <- c(issues, "non_presidential_row")
      bad_money <- sum(is.na(suppressWarnings(rf_money_int(m[, 4]))))
      add("presidential amounts parse", nrow(m), bad_money)
      if (bad_money) issues <- c(issues, "unparseable_money")
    }
    truncated <- !is.na(meta$showing_of) && meta$showing_of > length(rows)
    add("page holds every entry (truncated-save guard)", 1L, as.integer(truncated))
    if (truncated) issues <- c(issues, "truncated_save")
  }
  list(issues = issues, checks = if (length(checks)) do.call(rbind, checks) else rf_check("none", 0L, 0L)[0, ], table = tb)
}

.fi_asis_rows <- function(rows) lapply(rows, function(r) I(as.character(unlist(r))))

fi_os_parser_info <- function(home = fi_home()) {
  list(name = "fi_os_html", version = FI$VERSION, code_sha256 = rf_sha256(file.path(home, "R", "fi_os_html.R")))
}

# Parse one page into an envelope.
#   html         the page source (character scalar, UTF-8) - what the server sent, or a "Webpage, HTML only" save
#   expect       list(code, cycle, kind): the claimed target (from the planned URL or the file name)
#   source_sha   sha256 of the bytes that `html` was decoded from
# -> list(envelope, issues, checks, kind, code, cycle, ok) ; ok is FALSE when any issue was found.
fi_os_envelope <- function(html, url, final_url = url, retrieved_utc = rf_now_utc(), http = 200L, route = "fetch", source_sha = NA_character_,
                           expect = NULL) {
  tables <- fi_os_parse_tables(html)
  meta <- fi_os_page_meta(html)
  issues <- character()
  planned <- fi_os_target_from_url(url)
  tgt <- expect %||% planned
  kind <- tgt$kind %||% NA_character_
  if (is.na(kind)) return(list(envelope = NULL, issues = "page_kind_not_recognised", checks = rf_check("none", 0L, 0L)[0, ], kind = NA_character_, code = tgt$code, cycle = tgt$cycle, ok = FALSE))
  if (is.na(meta$title) || !grepl("OpenSecrets", meta$title, fixed = TRUE)) issues <- c(issues, "title_lacks_OpenSecrets")
  # the page's own identity (canonical URL) must agree with the claimed target
  ident <- c(meta$canonical, meta$og_url, if (!identical(final_url, url)) final_url)
  ident <- ident[!is.na(ident)]
  ident_checks <- list()
  if (length(ident)) {
    p <- fi_os_target_from_url(gsub("&amp;", "&", ident[1], fixed = TRUE))
    if (!is.na(p$code) && !is.na(tgt$code) && !identical(p$code, tgt$code)) issues <- c(issues, sprintf("page_code_%s_differs_from_claimed_%s", p$code, tgt$code))
    if (kind != "totals" && !is.na(p$cycle) && !is.na(tgt$cycle) && !identical(p$cycle, tgt$cycle)) issues <- c(issues, sprintf("page_cycle_%d_differs_from_claimed_%d", p$cycle, tgt$cycle))
  } else {
    issues <- c(issues, "page_identity_not_stated")
  }
  v <- fi_os_validate(kind, tables, meta)
  issues <- c(issues, v$issues)
  keep <- Filter(function(t) length(t$rows) > 0L, tables)
  as_json_tables <- lapply(keep, function(t) list(rows = .fi_asis_rows(t$rows), row_links = lapply(t$row_links, function(l) I(as.character(l)))))
  env <- list(url = url, final_url = final_url, title = meta$title, retrieved_utc = retrieved_utc, http = as.integer(http), route = route,
              source_file_sha256 = source_sha, os_code = tgt$code, cycle = tgt$cycle, kind = kind,
              page = list(heading = meta$h1, canonical = meta$canonical, og_url = meta$og_url,
                          breadcrumbs = lapply(meta$breadcrumbs, function(b) list(text = b$text, href = b$href))),
              source_tables = as_json_tables, tables = as_json_tables, fec_release_note = meta$fec_release_note, parser = fi_os_parser_info())
  list(envelope = env, issues = unique(issues), checks = v$checks, kind = kind, code = tgt$code, cycle = tgt$cycle, ok = !length(issues), meta = meta)
}

# The envelope path name of a target: outputs/os/{code}_{cycle}_{kind}.json
fi_os_envelope_name <- function(code, cycle, kind) sprintf("%s_%d_%s.json", code, as.integer(cycle), kind)

fi_os_write_envelope <- function(env, path) rf_write_json_atomic(env, path)

# Tidy rows of an envelope (FEEDS_SPEC 7.2). Each returns a data.frame (possibly zero rows).
fi_os_tidy_totals <- function(env, industry_id) {
  tb <- Filter(function(t) length(t$rows) > 0L && identical(as.character(unlist(t$rows[[1]]))[1], "Election Cycle"), env$tables)
  if (!length(tb)) return(NULL)
  m <- do.call(rbind, lapply(tb[[1]]$rows[-1], function(r) as.character(unlist(r))[1:9]))
  a <- apply(m[, 2:7, drop = FALSE], 2, rf_money_int)
  data.frame(industry_id = industry_id, os_code = env$os_code, cycle = as.integer(m[, 1]), total = a[, 1], individuals = a[, 2], pacs = a[, 3],
             outside = a[, 4], democrats = a[, 5], republicans = a[, 6], pct_dem = fi_num(sub("%", "", m[, 8], fixed = TRUE)),
             pct_rep = fi_num(sub("%", "", m[, 9], fixed = TRUE)), route = env$route, recipe_version = env$recipe_version %||% NA_character_,
             source_sha256 = env$source_file_sha256, fec_release_note = env$fec_release_note %||% NA_character_, retrieved_utc = env$retrieved_utc,
             status = "observed", stringsAsFactors = FALSE)
}

fi_os_tidy_candidates <- function(env, industry_id) {
  chamber <- switch(env$kind, candidates_H = "H", candidates_S = "S", presidential = "P")
  rows <- env$source_tables[[1]]$rows[-1]
  if (!length(rows)) return(NULL)
  m <- do.call(rbind, lapply(rows, function(r) as.character(unlist(r))))
  if (chamber == "P") {
    label <- m[, 2]; state <- NA_character_; amt <- rf_money_int(m[, 4])
  } else {
    label <- m[, 1]; state <- m[, 2]; amt <- rf_money_int(m[, 3])
  }
  pm <- regmatches(label, regexec("\\(([A-Z0-9]+)(?:-([A-Z]{2}))?\\)$", label, perl = TRUE))
  party <- vapply(pm, function(x) if (length(x) >= 2L) x[2] else NA_character_, "")
  data.frame(industry_id = industry_id, os_code = env$os_code, cycle = env$cycle, chamber = chamber, recipient_label = label, name_key = rf_name_key(label),
             party = party, state = state, amount = amt, cid = NA_character_, fec_cand_id = NA_character_, dist_id_run_for = NA_character_,
             route = env$route, source_sha256 = env$source_file_sha256, stringsAsFactors = FALSE)
}

# The category verification of FEEDS_SPEC 2.1 and the owner's ruling: confirm a code and its name from the site's own page before
# any data for it is used. Checks the page's canonical code, its heading (the category name), and the breadcrumb parent industry.
# -> list(verified, code, heading, parent, issues)
fi_os_verify_category_page <- function(meta, code, name_regex, parent_code = NULL, parent_name = NULL) {
  issues <- character(); notes <- character()
  canon <- gsub("&amp;", "&", meta$canonical %||% "", fixed = TRUE)
  pcode <- fi_os_target_from_url(canon)$code
  if (is.na(pcode) || !identical(pcode, code)) issues <- c(issues, sprintf("canonical_code_is_%s_not_%s", if (is.na(pcode)) "missing" else pcode, code))
  heading <- sub("\\s+(Summary|Recipients)$", "", meta$h1 %||% "")      # totals pages say "Summary", recipient pages "Recipients"
  heading <- gsub("&amp;", "&", heading, fixed = TRUE)
  if (!nzchar(heading) || !grepl(name_regex, heading, ignore.case = TRUE, perl = TRUE)) issues <- c(issues, sprintf("heading_'%s'_does_not_match_expected_name", heading))
  parent <- NA_character_
  if (length(meta$breadcrumbs)) {
    hrefs <- vapply(meta$breadcrumbs, function(b) b$href %||% "", "")
    texts <- vapply(meta$breadcrumbs, function(b) b$text %||% "", "")
    codes <- vapply(hrefs, function(h) { v <- fi_os_url_params(gsub("&amp;", "&", h, fixed = TRUE)); if ("ind" %in% names(v)) v[["ind"]] else NA_character_ }, "")
    i <- which(codes == code)
    if (!length(i)) issues <- c(issues, "breadcrumb_does_not_end_at_the_code")
    if (!is.null(parent_code)) {
      j <- which(codes == parent_code)
      other <- which(!is.na(codes) & grepl("^[A-Z][0-9]{2}$", codes) & codes != parent_code)
      if (length(j)) {
        parent <- texts[j[1]]
        if (!is.null(parent_name) && !grepl(parent_name, texts[j[1]], ignore.case = TRUE)) issues <- c(issues, sprintf("parent_%s_is_named_'%s'", parent_code, texts[j[1]]))
      } else if (length(other)) {
        issues <- c(issues, sprintf("page_places_the_code_under_%s_not_%s", codes[other[1]], parent_code))   # a different parent industry contradicts the expectation
      } else {
        notes <- c(notes, sprintf("parent_industry_%s_not_shown_in_the_breadcrumb", parent_code))
      }
    }
  } else {
    notes <- c(notes, "no_breadcrumb_on_page")
  }
  list(verified = !length(issues), code = code, heading = heading, parent = parent, issues = issues, notes = notes)
}

# IBISWorld report text -> edition record (FEEDS_SPEC 5.2-5.4 and 6 steps 2-4). Pure text in, records out: no network, no
# browser, no login. The text engine is R pdftools (poppler), proven on all 18 property & casualty editions; the xpdf-style
# `pdftotext -layout` shifts year labels one row and is never used. The numeric logic is an R port of the Codex
# extract_archived_forecasts.py (Key Statistics / Outlook tables, mode state machine, first number after the year), with the
# per-year Annual Change alignment check added: the Codex parser checked only the target year.
#
# Layouts: L1 legacy "Outlook" table (Revenue $ Million | Growth %), L2 mid-period wide "Key Statistics / Industry Data"
# table (Revenue ($m) first), L3 modern "Key Statistics / Industry Data / Values" table (Revenue ($ Million) after Year).

.fi_ctrl_re <- "[\\x01-\\x08\\x0b\\x0c\\x0e-\\x1f]"

# Page texts as parsed: U+2212 minus -> "-", control characters -> space.
fi_ibis_clean <- function(pages) {
  x <- gsub("−", "-", pages, fixed = TRUE)
  gsub(.fi_ctrl_re, " ", x, perl = TRUE)
}

fi_compact <- function(s) tolower(gsub("[\\s ]+", "", s, perl = TRUE))

# Page texts of a PDF file, through pdftools only.
fi_ibis_pdf_pages <- function(path) {
  fi_assert_pdf_engine()
  pdftools::pdf_text(path)
}

.fi_month_alt <- paste0("(", paste(c(FI$MONTHS, "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sept", "sep", "oct", "nov", "dec"), collapse = "|"), ")")

# Every "Month D, YYYY", "Month YYYY" and "D Month YYYY" date of a text, merged by (year, month): the day is kept when any
# form carries it. -> data.frame(year, month, day) with one row per distinct month (day NA when none is printed).
fi_find_dates <- function(text) {
  t <- paste(text, collapse = "\n")
  add <- function(rows, y, m, d) { rows[[length(rows) + 1L]] <- data.frame(year = y, month = m, day = d); rows }
  rows <- list()
  re1 <- paste0(.fi_month_alt, "\\.?[ \\t]*(?:([0-9]{1,2})[ \\t]*,?[ \\t]+)?((?:19|20)[0-9]{2})(?![0-9])")
  m1 <- regmatches(t, gregexec(re1, t, perl = TRUE, ignore.case = TRUE))[[1]]
  if (length(m1)) for (j in seq_len(ncol(m1))) {
    rows <- add(rows, as.integer(m1[4, j]), match(substr(tolower(m1[2, j]), 1, 3), substr(FI$MONTHS, 1, 3)),
                if (nzchar(m1[3, j])) as.integer(m1[3, j]) else NA_integer_)
  }
  re2 <- paste0("(?<![0-9])([0-9]{1,2})[ \\t]+(", paste(FI$MONTHS, collapse = "|"), ")[ \\t]*,?[ \\t]*((?:19|20)[0-9]{2})(?![0-9])")
  m2 <- regmatches(t, gregexec(re2, t, perl = TRUE, ignore.case = TRUE))[[1]]
  if (length(m2)) for (j in seq_len(ncol(m2))) rows <- add(rows, as.integer(m2[4, j]), match(tolower(m2[3, j]), FI$MONTHS), as.integer(m2[2, j]))
  if (!length(rows)) return(data.frame(year = integer(), month = integer(), day = integer()))
  d <- do.call(rbind, rows)
  d <- d[order(d$year, d$month, is.na(d$day)), ]
  d[!duplicated(d[c("year", "month")]), ]
}

# Publication date of an edition: the distinct months printed on the cover (page 1); when the cover has none, the header of
# page 2. More than one distinct month on the cover is ambiguous (the Codex rule) and leaves the date missing.
# -> list(year, month, day, date, precision, issue)
fi_ibis_pub_date <- function(pages) {
  none <- list(year = NA_integer_, month = NA_integer_, day = NA_integer_, date = NA_character_, precision = NA_character_)
  d <- fi_find_dates(pages[1])
  if (!nrow(d) && length(pages) > 1L) d <- fi_find_dates(substr(pages[2], 1, 600))
  if (nrow(d) != 1L) return(c(none, list(issue = "publication_date_missing_or_ambiguous_on_cover")))
  has_day <- !is.na(d$day)
  list(year = d$year, month = d$month, day = if (has_day) d$day else NA_integer_,
       date = if (has_day) sprintf("%04d-%02d-%02d", d$year, d$month, d$day) else sprintf("%04d-%02d", d$year, d$month),
       precision = if (has_day) "day" else "month", issue = NA_character_)
}

# The Codex file-name pattern (a hint only: the content decides) and the canonical name used for the P&C set.
FI_FILE_RE <- "^WebsiteFiles_Downloads_us_archive_([0-9]{4})_([0-9]{1,2})_([A-Za-z0-9]+)(?: \\([0-9]+\\))?\\.pdf$"
FI_FILE_RE2 <- "^([0-9]{4})_([0-9]{2})_([0-9]{5}[a-z]?)\\.pdf$"
fi_ibis_filename_hint <- function(name) {
  b <- basename(name)
  for (re in c(FI_FILE_RE, FI_FILE_RE2)) {
    m <- regmatches(b, regexec(re, b, perl = TRUE))[[1]]
    if (length(m) == 4L) return(list(date = sprintf("%s-%02d", m[2], as.integer(m[3])), code = tolower(m[4])))
  }
  list(date = NA_character_, code = NA_character_)
}

.fi_norm_title <- function(s) gsub("[^a-z0-9]+", "", tolower(s))

# Report code and title. Sources, in order: the Key Statistics URL text (`industry/<code>/`), the legacy cover (`US 52412`,
# `US: 52412`), the registry's title list. `industries` is the feeds registry's industries (or NULL: no registry check).
fi_ibis_identify <- function(pages, industries = NULL) {
  all_text <- paste(pages, collapse = "\n")
  url_re <- "https?://[^\\s]*ibisworld\\.com/[^\\s]*?industry/([0-9]{5}[a-z]?)/[^\\s]*"
  um <- regmatches(all_text, gregexec(url_re, all_text, perl = TRUE, ignore.case = TRUE))[[1]]
  url_code <- if (length(um)) tolower(names(sort(table(um[2, ]), decreasing = TRUE))[1]) else NA_character_
  key_url <- NA_character_
  if (length(um)) {
    mine <- which(tolower(um[2, ]) == url_code)
    ks <- mine[grepl("key-statistics", um[1, mine], fixed = TRUE)]
    key_url <- um[1, (if (length(ks)) ks else mine)[1]]
    key_url <- sub("(?<!:)/{2,}", "/", key_url, perl = TRUE)   # the printed URL may carry a doubled slash
  }
  cm <- regmatches(pages[1], regexec("(?i)\\bUS[:\\s]+([0-9]{5}[a-z]?)\\b", pages[1], perl = TRUE))[[1]]
  cover_code <- if (length(cm) == 2L) tolower(cm[2]) else NA_character_
  title <- NA_character_
  title_code <- NA_character_
  if (length(industries)) {
    cover_norm <- .fi_norm_title(paste(pages[seq_len(min(2L, length(pages)))], collapse = " "))
    for (ind in industries) for (tt in unlist(ind$ibisworld$titles)) {
      if (grepl(.fi_norm_title(tt), cover_norm, fixed = TRUE)) { title <- tt; title_code <- tolower(ind$ibisworld$code); break }
    }
  }
  code <- c(url_code, cover_code, title_code)
  code <- code[!is.na(code)]
  source <- c("url", "cover", "title")[!is.na(c(url_code, cover_code, title_code))]
  issues <- character()
  if (length(unique(code)) > 1L) issues <- c(issues, "code_conflict_between_sources")
  list(code = if (length(code)) code[1] else NA_character_, source = if (length(code)) source[1] else NA_character_, title = title,
       key_stats_url = key_url, issues = issues)
}

# A table row: a year, then the first number after it. A first "number" that is itself a bare year ("2002 2003 2004 ...", the
# header row of a legacy Key Statistics table) is not a value; revenue with a comma or a decimal point always is.
.fi_num_re <- "[-+]?[0-9][0-9,]*(?:\\.[0-9]+)?"
.fi_row_re <- paste0("^\\s*((?:19|20)[0-9]{2})\\s+(?!(?:19|20)[0-9]{2}(?![0-9,.]))(", .fi_num_re, ")")
# An Outlook-table row: year, revenue, growth %; the growth is N/A on the first row of a series.
.fi_outlook_row_re <- paste0("^\\s*((?:19|20)[0-9]{2})\\s+(", .fi_num_re, ")\\s+(N/A|[-+]?[0-9]+(?:\\.[0-9]+)?)\\s*$")

# Parse the page texts of one report into an edition record:
#   code, title, key_stats_url, pub_* (year, month, day, date, precision), layout, pages (candidate pdf pages), revenue_unit,
#   price_base_year, price_basis, price_basis_evidence, values (year, revenue_million, pdf_page, source_row),
#   changes (year, printed_change_pct, pdf_page, source_row), issues
# Checks that need the file (first-seen time, filename) are done by fi_ibis_check_edition.
fi_ibis_parse_pages <- function(pages, industries = NULL) {
  tx <- fi_ibis_clean(pages)
  cp <- fi_compact(tx)
  n <- length(tx)
  issues <- character()
  id <- fi_ibis_identify(tx, industries)
  issues <- c(issues, id$issues)
  pd <- fi_ibis_pub_date(tx)
  if (!is.na(pd$issue)) issues <- c(issues, pd$issue)

  hit <- which((grepl("keystatistics", cp, fixed = TRUE) & !grepl("tableofcontents", cp, fixed = TRUE) & seq_len(n) > 3L) |
                 grepl("revenue$milliongrowth%", cp, fixed = TRUE))
  cand <- sort(unique(unlist(lapply(hit, function(i) i:min(i + 2L, n)))))

  values <- list(); changes <- list()
  unit <- NA_character_
  mode <- NA_character_
  saw_outlook <- FALSE
  saw_values_header <- FALSE
  bases <- integer(); base_ev <- character(); basis_kind <- NA_character_
  glossary <- FALSE; constant_dollars <- integer(); constant_ev <- character(); real_outlook <- FALSE; real_ev <- character()
  conflicts <- character()
  for (p in cand) {
    txt <- tx[p]
    for (yr in regmatches(txt, gregexpr("(?i)inflation[\\s-]*adjusted\\s*(?:to\\s*)?((?:19|20)[0-9]{2})", txt, perl = TRUE))[[1]]) {
      y <- as.integer(sub(".*((?:19|20)[0-9]{2})$", "\\1", yr, perl = TRUE))
      bases <- c(bases, y); base_ev <- c(base_ev, sprintf("PDF page %d: inflation adjusted %d", p, y))
    }
    cpp <- cp[p]
    if (grepl("includingforecasts,areadjustedforinflation", cpp, fixed = TRUE) && grepl("yearpublished)asthebaseyear", cpp, fixed = TRUE)) {
      glossary <- TRUE
    }
    for (cd in regmatches(txt, gregexpr("(?i)((?:19|20)[0-9]{2})\\s+constant\\s+dollars|constant\\s+((?:19|20)[0-9]{2})\\s+dollars", txt, perl = TRUE))[[1]]) {
      y <- as.integer(regmatches(cd, regexpr("(?:19|20)[0-9]{2}", cd, perl = TRUE)))
      constant_dollars <- c(constant_dollars, y); constant_ev <- c(constant_ev, sprintf("PDF page %d: %d constant dollars", p, y))
    }
    if (grepl("revenue$milliongrowth%", cpp, fixed = TRUE)) {
      if (is.na(unit)) unit <- "USD million"
      if (grepl("realrate", cpp, fixed = TRUE) || grepl("constantprices", cpp, fixed = TRUE)) {
        real_outlook <- TRUE; real_ev <- c(real_ev, sprintf("PDF page %d: revenue table or outlook explicitly states constant prices or real rate", p))
      }
    }
    lines <- strsplit(txt, "\n", fixed = TRUE)[[1]]
    first_row <- which(grepl(.fi_row_re, lines, perl = TRUE))[1]
    if (is.na(unit) && !is.na(first_row)) {
      header <- paste(lines[seq_len(first_row - 1L)], collapse = "\n")
      if (grepl("revenue", header, ignore.case = TRUE)) {
        um <- regmatches(header, regexec("(?i)\\(\\s*\\$\\s*(million|billion|m|b)\\s*\\)", header, perl = TRUE))[[1]]
        if (length(um) == 2L) unit <- if (tolower(um[2]) %in% c("b", "billion")) "USD billion" else "USD million"
      }
    }
    put <- function(bucket, y, val, src, label) {
      old <- bucket[[y]]
      if (is.null(old)) bucket[[y]] <- list(v = val, page = p, row = src)
      else if (abs(old$v - val) > 1e-9) conflicts <<- c(conflicts, sprintf("conflicting_%s_row_%s", label, y))
      bucket
    }
    if (grepl("revenue$milliongrowth%", cpp, fixed = TRUE)) {
      # Legacy Outlook page (the Codex parser's dedicated branch): only the Revenue table above the first Gross Product line.
      saw_outlook <- TRUE
      for (ln in lines) {
        if (grepl("grossproduct", fi_compact(ln), fixed = TRUE)) break
        om <- regmatches(ln, regexec(.fi_outlook_row_re, ln, perl = TRUE))[[1]]
        if (length(om) != 4L) next
        values <- put(values, om[2], as.numeric(gsub(",", "", om[3])), trimws(ln), "outlook_values")
        if (om[4] != "N/A") changes <- put(changes, om[2], as.numeric(om[4]), trimws(ln), "outlook_changes")
      }
      mode <- NA_character_      # a table continuing on the next page (Gross Product) must not be read as revenue
      next
    }
    # Key Statistics pages: the mode state machine (Codex parse_file). The Outlook headers are not triggers here, because the
    # Outlook pages are read by the branch above and a mode left switched on would pick up the Gross Product tables below.
    for (ln in lines) {
      c1 <- fi_compact(ln)
      if (grepl("^([0-9]+\\.)?keystatistics$|^industrydata$|^values$", c1)) {
        mode <- "values"
        if (c1 == "values") saw_values_header <- TRUE
      }
      if (grepl("annualchange", c1, fixed = TRUE)) mode <- "changes"
      if (grepl("keyratios|industryfinancial|grossproduct|sectorrank", c1)) mode <- NA_character_
      if (is.na(mode)) next
      r <- regmatches(ln, regexec(.fi_row_re, ln, perl = TRUE))[[1]]
      if (length(r) != 3L) next
      v <- as.numeric(gsub(",", "", r[3]))
      if (mode == "values") values <- put(values, r[2], v, trimws(ln), "values") else changes <- put(changes, r[2], v, trimws(ln), "changes")
    }
  }
  issues <- c(issues, unique(conflicts))
  # price basis, in the order of FEEDS_SPEC 6 step 3
  price_year <- NA_integer_; price_basis <- NA_character_; price_ev <- NA_character_
  if (length(unique(bases)) == 1L) {
    price_year <- unique(bases); price_ev <- paste(unique(base_ev), collapse = "; ")
  } else if (length(unique(bases)) > 1L) {
    issues <- c(issues, "constant_price_base_ambiguous")
  } else if (glossary && !is.na(pd$year)) {
    price_year <- pd$year
    price_ev <- "CONSTANT PRICES glossary defines Key Statistics including forecasts as inflation adjusted to publication year"
  } else if (length(unique(constant_dollars)) == 1L) {
    price_year <- unique(constant_dollars); price_ev <- paste(unique(constant_ev), collapse = "; ")
  }
  if (!is.na(price_year)) {
    price_basis <- sprintf("constant %d USD", price_year)
  } else if (real_outlook && !"constant_price_base_ambiguous" %in% issues) {
    price_basis <- "real growth stated in outlook; price-base year not stated in forecast table"
    price_ev <- paste(unique(real_ev), collapse = "; ")
  } else if (!"constant_price_base_ambiguous" %in% issues) {
    issues <- c(issues, "constant_price_base_missing")
  }
  if (is.na(unit)) issues <- c(issues, "revenue_unit_not_verified")

  to_df <- function(b, nm) {
    if (!length(b)) return(data.frame(year = integer(), v = numeric(), pdf_page = integer(), source_row = character(), stringsAsFactors = FALSE))
    d <- data.frame(year = as.integer(names(b)), v = vapply(b, function(z) z$v, 0), pdf_page = vapply(b, function(z) as.integer(z$page), 0L),
                    source_row = vapply(b, function(z) z$row, ""), stringsAsFactors = FALSE, row.names = NULL)
    names(d)[2] <- nm
    d
  }
  vals <- to_df(values, "revenue_million")
  if (!is.na(unit) && identical(unit, "USD billion")) vals$revenue_million <- vals$revenue_million * 1000
  layout <- if (length(values) && saw_outlook) "L1" else if (saw_values_header) "L3" else "L2"
  if (!length(values)) { layout <- NA_character_; issues <- c(issues, "no_revenue_table_found") }
  list(code = id$code, code_source = id$source, title = id$title, key_stats_url = id$key_stats_url,
       pub_year = pd$year, pub_month = pd$month, pub_day = pd$day, pub_date = pd$date, date_precision = pd$precision,
       edition_month = if (is.na(pd$year)) NA_character_ else sprintf("%04d-%02d", pd$year, pd$month),
       layout = layout, pages = cand, n_pages = n, revenue_unit = unit, price_base_year = price_year, price_basis = price_basis,
       price_basis_evidence = price_ev, values = vals, changes = to_df(changes, "printed_change_pct"), issues = unique(issues))
}

fi_ibis_parse_pdf <- function(path, industries = NULL) {
  ed <- fi_ibis_parse_pages(fi_ibis_pdf_pages(path), industries)
  ed$pdf_info <- tryCatch({ i <- pdftools::pdf_info(path); list(pages = i$pages) }, error = function(e) list(pages = NA_integer_))
  ed
}

# The checks of FEEDS_SPEC 6 step 4 on one edition record. `first_seen_utc` (ISO text) enables the publication-date guard,
# `filename` the filename-versus-content comparison, `registry_codes` the unregistered-code test.
# -> list(issues, numerics_ok, status, checks) where numerics_ok means every check except the price basis passed.
fi_ibis_check_edition <- function(ed, first_seen_utc = NULL, filename = NULL, registry_codes = NULL) {
  issues <- ed$issues
  v <- ed$values
  ch <- ed$changes
  checks <- list()
  add <- function(name, examined, failures, detail = "") checks[[length(checks) + 1L]] <<- rf_check(name, examined, failures, detail)
  if (is.na(ed$code)) issues <- c(issues, "report_code_not_found")
  if (!is.null(registry_codes) && !is.na(ed$code) && !(ed$code %in% registry_codes)) issues <- c(issues, "unregistered_code")
  if (nrow(v)) {
    bad_order <- is.unsorted(v$year, strictly = TRUE)
    add("years strictly increasing", nrow(v), as.integer(bad_order))
    if (bad_order) issues <- c(issues, "years_not_strictly_increasing")
    bad_rev <- sum(!(v$revenue_million > 0))
    add("revenue > 0", nrow(v), bad_rev)
    if (bad_rev) issues <- c(issues, "revenue_not_positive")
  }
  # row alignment: for every year with both a revenue level for the year before and a printed change
  if (nrow(v) && nrow(ch)) {
    j <- match(ch$year, v$year); jprev <- match(ch$year - 1L, v$year)
    ok <- !is.na(j) & !is.na(jprev)
    recomputed <- 100 * (v$revenue_million[j[ok]] / v$revenue_million[jprev[ok]] - 1)
    diff <- abs(recomputed - ch$printed_change_pct[ok])
    n_bad <- sum(diff > FI$ALIGN_TOL_PP)
    add("printed change aligns with revenue (per year)", sum(ok), n_bad,
        if (n_bad) paste(ch$year[ok][diff > FI$ALIGN_TOL_PP], collapse = ",") else sprintf("max diff %.3f pp", if (length(diff)) max(diff) else 0))
    if (n_bad) issues <- c(issues, "printed_change_misaligned_with_revenue")
    if (!any(ok)) issues <- c(issues, "no_year_to_cross_check")
  } else if (nrow(v)) {
    issues <- c(issues, "reported_annual_growth_not_found")
  }
  if (!is.null(first_seen_utc) && !is.na(ed$pub_year)) {
    pub_end <- if (!is.na(ed$pub_day)) as.Date(sprintf("%04d-%02d-%02d", ed$pub_year, ed$pub_month, ed$pub_day)) else
      seq(as.Date(sprintf("%04d-%02d-01", ed$pub_year, ed$pub_month)), by = "month", length.out = 2L)[2] - 1
    seen <- as.Date(substr(first_seen_utc, 1, 10))
    late <- pub_end > seen
    add("publication date not after first seen", 1L, as.integer(late))
    if (late) issues <- c(issues, "publication_date_after_first_seen")
  }
  hint <- if (!is.null(filename)) fi_ibis_filename_hint(filename) else list(date = NA_character_, code = NA_character_)
  if (!is.na(hint$date) && !is.na(ed$edition_month) && !identical(hint$date, ed$edition_month)) issues <- c(issues, "filename_date_differs_from_cover")
  if (!is.na(hint$code) && !is.na(ed$code) && !identical(hint$code, ed$code)) issues <- c(issues, "filename_code_differs_from_content")
  issues <- unique(issues)
  basis_issue <- c("constant_price_base_missing", "constant_price_base_ambiguous")
  numerics_ok <- !length(setdiff(issues, basis_issue))
  list(issues = issues, numerics_ok = numerics_ok, status = if (length(issues)) "review_required" else "checked",
       checks = if (length(checks)) do.call(rbind, checks) else rf_check("none", 0L, 0L)[0, ])
}

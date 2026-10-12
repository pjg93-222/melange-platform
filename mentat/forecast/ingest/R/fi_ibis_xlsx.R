# IBISWorld Key Statistics export (xlsx) reader, review mode (FEEDS_SPEC 6 step 5). No sample of a real export exists on this
# machine, so the layout is read by convention (a sheet with a header row holding "Year" and "Revenue") and every edition read
# this way carries the issue `xlsx_layout_not_fixture_verified`: it never feeds a derived series until Paul confirms the first
# real export and a fixture is recorded. When a PDF of the same edition exists the PDF is authoritative and the workbook is only
# a cross-check (fi_ibis_xlsx_crosscheck).

.fi_xlsx_sheet_text <- function(path, sheet) {
  x <- suppressMessages(readxl::read_excel(path, sheet = sheet, col_names = FALSE, col_types = "text", .name_repair = "minimal"))
  as.data.frame(x, stringsAsFactors = FALSE)
}

# -> edition record shaped like fi_ibis_parse_pages (layout "xlsx"), or NULL when no sheet has a Year / Revenue header row.
fi_ibis_read_xlsx <- function(path, industries = NULL) {
  if (!requireNamespace("readxl", quietly = TRUE)) stop("fi_ibis_read_xlsx: package readxl is unavailable", call. = FALSE)
  for (sh in readxl::excel_sheets(path)) {
    d <- .fi_xlsx_sheet_text(path, sh)
    if (!nrow(d) || ncol(d) < 2L) next
    m <- as.matrix(d)
    mm <- m
    mm[is.na(mm)] <- ""
    cell <- function(i, j) { v <- m[i, j]; if (is.na(v)) "" else trimws(v) }
    hdr <- NA_integer_; ycol <- NA_integer_; rcol <- NA_integer_
    for (i in seq_len(min(nrow(m), 40L))) {
      yj <- which(grepl("^year$", tolower(vapply(seq_len(ncol(m)), function(j) cell(i, j), ""))))
      rj <- which(grepl("^revenue", tolower(vapply(seq_len(ncol(m)), function(j) cell(i, j), ""))))
      if (length(yj) && length(rj)) { hdr <- i; ycol <- yj[1]; rcol <- rj[1]; break }
    }
    if (is.na(hdr)) next
    top <- paste(mm[seq_len(hdr), ], collapse = "\n")
    hdr_text <- tolower(paste(vapply(seq_len(ncol(m)), function(j) cell(hdr, j), ""), collapse = " "))
    unit_text <- paste(top, hdr_text, cell(min(hdr + 1L, nrow(m)), rcol))
    unit <- if (grepl("\\(\\s*\\$\\s*(billion|b)\\s*\\)", unit_text, ignore.case = TRUE)) "USD billion" else if (grepl("\\(\\s*\\$\\s*(million|m)\\s*\\)", unit_text, ignore.case = TRUE)) "USD million" else NA_character_
    body <- (hdr + 1L):nrow(m)
    yrs <- suppressWarnings(as.integer(gsub("[^0-9]", "", vapply(body, function(i) cell(i, ycol), ""))))
    rev <- suppressWarnings(as.numeric(gsub(",", "", vapply(body, function(i) cell(i, rcol), ""))))
    keep <- !is.na(yrs) & yrs >= 1900L & yrs <= 2100L & !is.na(rev)
    if (!any(keep)) next
    vals <- data.frame(year = yrs[keep], revenue_million = rev[keep] * if (identical(unit, "USD billion")) 1000 else 1,
                       pdf_page = NA_integer_, source_row = paste0(sh, "!row ", body[keep]), stringsAsFactors = FALSE)
    all_text <- paste(mm, collapse = "\n")
    dates <- fi_find_dates(top)
    id <- fi_ibis_identify(c(all_text), industries)
    bm <- regmatches(all_text, gregexpr("(?i)inflation[\\s-]*adjusted\\s*(?:to\\s*)?((?:19|20)[0-9]{2})", all_text, perl = TRUE))[[1]]
    by <- unique(as.integer(sub(".*((?:19|20)[0-9]{2})$", "\\1", bm, perl = TRUE)))
    issues <- c("xlsx_layout_not_fixture_verified", id$issues)
    if (nrow(dates) != 1L) issues <- c(issues, "publication_date_missing_or_ambiguous_on_cover")
    if (is.na(unit)) issues <- c(issues, "revenue_unit_not_verified")
    if (length(by) != 1L) issues <- c(issues, "constant_price_base_missing")
    return(list(code = id$code, code_source = id$source, title = id$title, key_stats_url = id$key_stats_url,
                pub_year = if (nrow(dates) == 1L) dates$year else NA_integer_, pub_month = if (nrow(dates) == 1L) dates$month else NA_integer_,
                pub_day = if (nrow(dates) == 1L) dates$day else NA_integer_,
                pub_date = if (nrow(dates) == 1L) (if (!is.na(dates$day)) sprintf("%04d-%02d-%02d", dates$year, dates$month, dates$day) else sprintf("%04d-%02d", dates$year, dates$month)) else NA_character_,
                date_precision = if (nrow(dates) == 1L) (if (!is.na(dates$day)) "day" else "month") else NA_character_,
                edition_month = if (nrow(dates) == 1L) sprintf("%04d-%02d", dates$year, dates$month) else NA_character_,
                layout = "xlsx", pages = integer(), n_pages = NA_integer_, revenue_unit = unit,
                price_base_year = if (length(by) == 1L) by else NA_integer_,
                price_basis = if (length(by) == 1L) sprintf("constant %d USD", by) else NA_character_,
                price_basis_evidence = if (length(by) == 1L) paste0("workbook text: inflation adjusted ", by) else NA_character_,
                values = vals[order(vals$year), ],
                changes = data.frame(year = integer(), printed_change_pct = numeric(), pdf_page = integer(), source_row = character(), stringsAsFactors = FALSE),
                issues = unique(issues)))
  }
  NULL
}

# Cross-check an xlsx edition against the PDF edition of the same code and month: every shared year must agree to 0.05 (the
# printed precision). -> character vector of issues (empty when they agree or there is nothing to compare).
fi_ibis_xlsx_crosscheck <- function(xlsx_ed, pdf_ed) {
  j <- match(xlsx_ed$values$year, pdf_ed$values$year)
  ok <- !is.na(j)
  if (!any(ok)) return("xlsx_shares_no_year_with_pdf")
  if (any(abs(xlsx_ed$values$revenue_million[ok] - pdf_ed$values$revenue_million[j[ok]]) > 0.05)) return("xlsx_differs_from_pdf")
  character()
}

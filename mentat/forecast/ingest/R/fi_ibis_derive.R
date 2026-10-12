# IBISWorld editions -> the 06 (predicted growth) and 07 (actual growth) shaped feeds (FEEDS_SPEC 6 steps 6-7 and 7.3).
#
# fi_ibis_collect   inbox scan -> parsed and checked edition records, the editions table, the long Key Statistics table
# fi_ibis_derive_predicted   one row per target year: the latest edition published in the year before it (look-ahead guard)
# fi_ibis_derive_actual      the latest checked edition's historical years as one vintage (never spliced), or a pinned workbook
#                            passed through byte-identical
# The column sets equal the 06 and 07 `data` sheets of the Sector Growth Study workbooks.

FI_COLS_06 <- c("sector", "year", "predicted_growth_pct", "collected_forecast_growth_pct", "forecast_status", "forecast_publication_month",
                "forecast_known_available_by", "forecast_baseline_year", "forecast_target_year", "forecast_baseline_revenue_million",
                "forecast_target_revenue_million", "forecast_price_basis", "forecast_method", "forecast_component_months",
                "forecast_source_urls", "forecast_source_pdfs", "forecast_source_pages", "forecast_note", "scope_qualification",
                "forecast_horizon_years", "study_sector", "forecast_age_months", "status", "source_id")
FI_COLS_07 <- c("sector", "year", "revenue_million_constant_2026_usd", "growth_same_year_pct", "growth_previous_year_pct", "ibisworld_code",
                "report_vintage", "source_url", "study_sector", "revenue_million_report_price_basis", "revenue_price_basis",
                "printed_revenue_change_pct", "value_type", "source_pdf", "source_pdf_sha256", "source_page", "build_note", "status", "source_id")
FI_COLS_EDITIONS <- c("code", "title", "edition_month", "publication_date", "date_precision", "layout", "pages", "revenue_unit", "price_base_year",
                      "price_basis", "price_basis_evidence", "source_sha256", "vault_path", "inbox_name", "filename_date", "status", "issues",
                      "numerics_ok", "key_stats_url")
FI_COLS_LONG <- c("code", "edition_month", "year", "revenue_million", "printed_change_pct", "value_kind", "pdf_page", "source_row")

.fi_inferred_basis <- "constant prices inferred from publisher methodology; exact base year unverified"

# The registry codes (lower case) of every industry that has an IBISWorld code.
fi_ibis_registry_codes <- function(feeds) {
  out <- vapply(feeds$industries, function(i) tolower(i$ibisworld$code %||% NA_character_), "")
  out[!is.na(out)]
}

.fi_editions_row <- function(ed, sha, vault_path, inbox_name, filename, status, issues, numerics_ok) {
  hint <- fi_ibis_filename_hint(filename)
  data.frame(code = ed$code %||% NA_character_, title = ed$title %||% NA_character_, edition_month = ed$edition_month %||% NA_character_,
             publication_date = ed$pub_date %||% NA_character_, date_precision = ed$date_precision %||% NA_character_,
             layout = ed$layout %||% NA_character_, pages = paste(ed$pages, collapse = " "), revenue_unit = ed$revenue_unit %||% NA_character_,
             price_base_year = ed$price_base_year %||% NA_integer_, price_basis = ed$price_basis %||% NA_character_,
             price_basis_evidence = ed$price_basis_evidence %||% NA_character_, source_sha256 = sha, vault_path = vault_path,
             inbox_name = inbox_name, filename_date = hint$date, status = status, issues = paste(issues, collapse = "; "),
             numerics_ok = numerics_ok, key_stats_url = ed$key_stats_url %||% NA_character_, stringsAsFactors = FALSE)
}

.fi_long_rows <- function(ed) {
  v <- ed$values
  if (!nrow(v)) return(NULL)
  ch <- ed$changes
  data.frame(code = ed$code, edition_month = ed$edition_month, year = v$year, revenue_million = v$revenue_million,
             printed_change_pct = ch$printed_change_pct[match(v$year, ch$year)],
             value_kind = ifelse(v$year < ed$pub_year, "historical", ifelse(v$year == ed$pub_year, "current", "forecast")),
             pdf_page = v$pdf_page, source_row = v$source_row, stringsAsFactors = FALSE)
}

# Parse and check every readable file of an inbox scan.
# -> list(editions = data.frame(FI_COLS_EDITIONS), long = data.frame(FI_COLS_LONG), records = list by sha256, checks = data.frame)
fi_ibis_collect <- function(scan, feeds, emit = NULL) {
  say <- function(kind, summary, metadata) if (is.function(emit)) emit(kind, summary, metadata)
  inds <- feeds$industries
  reg_codes <- fi_ibis_registry_codes(feeds)
  rows <- list(); longs <- list(); records <- list(); checks <- list()
  for (i in seq_len(nrow(scan))) {
    r <- scan[i, ]
    if (r$status == "duplicate") { say("feeds.file", paste("duplicate of", r$duplicate_of), list(sha256 = r$sha256, kind = r$kind, code = NA, edition = NA, status = "duplicate")); next }
    if (r$status == "unsupported" || !(r$kind %in% c("pdf", "xlsx"))) {
      rows[[length(rows) + 1L]] <- .fi_editions_row(list(), r$sha256, r$vault_path, r$inbox_name, r$inbox_name, "unsupported", "unsupported_file_type", FALSE)
      say("feeds.file", paste("unsupported:", r$inbox_name), list(sha256 = r$sha256, kind = r$kind, code = NA, edition = NA, status = "unsupported"))
      next
    }
    ed <- tryCatch({
      if (r$kind == "pdf") fi_ibis_parse_pdf(if (!is.na(r$vault_path)) r$vault_path else r$path, inds) else fi_ibis_read_xlsx(if (!is.na(r$vault_path)) r$vault_path else r$path, inds)
    }, error = function(e) structure(list(error = conditionMessage(e)), class = "fi_parse_error"))
    if (inherits(ed, "fi_parse_error") || is.null(ed)) {
      why <- if (inherits(ed, "fi_parse_error")) paste0("unreadable: ", ed$error) else "no Year/Revenue table found"
      rows[[length(rows) + 1L]] <- .fi_editions_row(list(), r$sha256, r$vault_path, r$inbox_name, r$inbox_name, "review_required", why, FALSE)
      say("feeds.file", paste("not parsed:", r$inbox_name), list(sha256 = r$sha256, kind = r$kind, code = NA, edition = NA, status = "review_required"))
      next
    }
    ck <- fi_ibis_check_edition(ed, first_seen_utc = r$first_seen_utc, filename = r$inbox_name, registry_codes = reg_codes)
    status <- if (!is.na(ed$code) && !(ed$code %in% reg_codes)) "unregistered_code" else ck$status
    ed$issues <- ck$issues
    records[[r$sha256]] <- c(ed, list(status = status, numerics_ok = ck$numerics_ok, sha256 = r$sha256, vault_path = r$vault_path, inbox_name = r$inbox_name))
    cdf <- ck$checks
    if (nrow(cdf)) { cdf$name <- paste0(cdf$name, " [", r$inbox_name, "]"); checks[[length(checks) + 1L]] <- cdf }
    rows[[length(rows) + 1L]] <- .fi_editions_row(ed, r$sha256, r$vault_path, r$inbox_name, r$inbox_name, status, ck$issues, ck$numerics_ok)
    lg <- .fi_long_rows(ed)
    if (!is.null(lg) && status != "unregistered_code") longs[[length(longs) + 1L]] <- lg
    say("feeds.file", paste(r$inbox_name, "->", ed$code %||% "?", ed$edition_month %||% "?"), list(sha256 = r$sha256, kind = r$kind, code = ed$code, edition = ed$edition_month, status = status))
    say("feeds.parse", paste("parsed", r$inbox_name), list(edition = ed$edition_month, layout = ed$layout, pages = length(ed$pages), issues = paste(ck$issues, collapse = "; ")))
  }
  editions <- if (length(rows)) do.call(rbind, rows) else as.data.frame(stats::setNames(rep(list(character()), length(FI_COLS_EDITIONS)), FI_COLS_EDITIONS), stringsAsFactors = FALSE)
  # two different PDFs of one code and month: neither is used (the vintage would be ambiguous)
  pdf_rows <- which(!is.na(editions$code) & !is.na(editions$edition_month) & editions$layout %in% c("L1", "L2", "L3"))
  key <- paste(editions$code[pdf_rows], editions$edition_month[pdf_rows])
  dup_keys <- unique(key[duplicated(key)])
  for (k in dup_keys) {
    ix <- pdf_rows[key == k]
    editions$status[ix] <- "review_required"
    editions$issues[ix] <- ifelse(nzchar(editions$issues[ix]), paste(editions$issues[ix], "duplicate_edition", sep = "; "), "duplicate_edition")
    editions$numerics_ok[ix] <- FALSE
  }
  long <- if (length(longs)) do.call(rbind, longs) else as.data.frame(stats::setNames(rep(list(character()), length(FI_COLS_LONG)), FI_COLS_LONG), stringsAsFactors = FALSE)
  # the long table keeps only editions that are still usable after the duplicate test
  if (nrow(long) && length(dup_keys)) long <- long[!(paste(long$code, long$edition_month) %in% dup_keys), ]
  list(editions = editions[FI_COLS_EDITIONS], long = long[FI_COLS_LONG], records = records,
       checks = if (length(checks)) do.call(rbind, checks) else rf_check("none", 0L, 0L)[0, ])
}

.fi_ed_order <- function(ed) order(as.integer(substr(ed$edition_month, 1, 4)), as.integer(substr(ed$edition_month, 6, 7)),
                                   ifelse(is.na(ed$publication_date) | nchar(ed$publication_date) < 10, 0L, as.integer(substr(ed$publication_date, 9, 10))))

# 06-shaped rows for one industry. `cfg` is the industry's registry entry; its ibisworld block supplies the code, study sector,
# the target range (predicted_targets = [first, last]) and the scope text.
fi_ibis_derive_predicted <- function(editions, long, industry_id, cfg) {
  code <- tolower(cfg$ibisworld$code)
  study <- cfg$ibisworld$study_sector %||% industry_id
  tg <- unlist(cfg$ibisworld$predicted_targets %||% c(2008L, 2026L))
  scope <- cfg$ibisworld$scope_qualification %||% ""
  usable <- editions[!is.na(editions$code) & editions$code == code & editions$numerics_ok %in% TRUE & !is.na(editions$edition_month) &
                       editions$layout %in% c("L1", "L2", "L3"), , drop = FALSE]
  usable$pub_year <- as.integer(substr(usable$edition_month, 1, 4))
  out <- list()
  for (t in seq(tg[1], tg[2])) {
    h1 <- usable[usable$pub_year == t - 1L, , drop = FALSE]
    cand <- if (nrow(h1)) h1 else usable[usable$pub_year < t - 1L, , drop = FALSE]   # look-ahead guard: pub_year >= t never qualifies
    row <- NULL
    if (nrow(cand)) {
      cand <- cand[.fi_ed_order(cand), , drop = FALSE]
      # the latest edition that carries both revenue levels
      for (k in rev(seq_len(nrow(cand)))) {
        e <- cand[k, ]
        lv <- long[long$code == code & long$edition_month == e$edition_month, , drop = FALSE]
        b0 <- lv$revenue_million[match(t - 1L, lv$year)]; b1 <- lv$revenue_million[match(t, lv$year)]
        if (is.na(b0) || is.na(b1) || b0 == 0) next
        pg <- lv$pdf_page[match(t, lv$year)]
        h <- t - e$pub_year
        g <- round(100 * (b1 / b0 - 1), 8)
        basis_ok <- !is.na(e$price_basis)
        status <- if (h == 1L && basis_ok) "observed" else "partial"
        fstat <- if (h == 1L && basis_ok) "ready" else if (h == 1L) "numeric_verified_price_basis_inferred" else "verified_older_vintage_sensitivity"
        note <- if (h > 1L) (if (h == 2L) "publication_two_calendar_years_before_target" else sprintf("publication_%d_calendar_years_before_target", h))
                else if (!basis_ok) "historical_report_price_basis_not_explicit" else NA_character_
        row <- data.frame(
          sector = industry_id, year = t, predicted_growth_pct = if (status == "observed") g else NA_real_, collected_forecast_growth_pct = g,
          forecast_status = fstat, forecast_publication_month = e$edition_month, forecast_known_available_by = e$publication_date,
          forecast_baseline_year = t - 1L, forecast_target_year = t, forecast_baseline_revenue_million = b0, forecast_target_revenue_million = b1,
          forecast_price_basis = if (basis_ok) e$price_basis else .fi_inferred_basis, forecast_method = "same_report_revenue_ratio",
          forecast_component_months = paste0(code, ":", e$edition_month), forecast_source_urls = e$key_stats_url, forecast_source_pdfs = e$vault_path,
          forecast_source_pages = pg, forecast_note = note, scope_qualification = scope, forecast_horizon_years = h, study_sector = study,
          forecast_age_months = (t * 12L + 1L) - (e$pub_year * 12L + as.integer(substr(e$edition_month, 6, 7))), status = status,
          source_id = paste0("ibis_feed_", code), stringsAsFactors = FALSE)
        break
      }
    }
    if (is.null(row)) {
      row <- data.frame(sector = industry_id, year = t, predicted_growth_pct = NA_real_, collected_forecast_growth_pct = NA_real_, forecast_status = "missing",
                        forecast_publication_month = NA_character_, forecast_known_available_by = NA_character_, forecast_baseline_year = t - 1L,
                        forecast_target_year = t, forecast_baseline_revenue_million = NA_real_, forecast_target_revenue_million = NA_real_,
                        forecast_price_basis = NA_character_, forecast_method = NA_character_, forecast_component_months = NA_character_,
                        forecast_source_urls = NA_character_, forecast_source_pdfs = NA_character_, forecast_source_pages = NA_integer_,
                        forecast_note = sprintf("No %d edition available; waiting for owner file.", t - 1L), scope_qualification = scope,
                        forecast_horizon_years = NA_integer_, study_sector = study, forecast_age_months = NA_integer_, status = "missing",
                        source_id = paste0("ibis_feed_", code), stringsAsFactors = FALSE)
    }
    out[[length(out) + 1L]] <- row
  }
  do.call(rbind, out)[FI_COLS_06]
}

# 07-shaped rows for one industry.
#   single_latest_vintage: the latest fully checked edition (price basis found). Its historical years (year < publication year,
#     year >= actual_first_year) become the series; growth is computed inside that one vintage only. The whole series is
#     replaced whenever a later edition arrives: vintages are never spliced.
#   pinned_workbook: `workbook_rows` (the existing 07 data rows) pass through unchanged.
# attr "vintage" holds the chosen edition month (NA when none); attr "waiting" says the newest edition is older than
# actual_min_edition (or there is none), i.e. the history does not yet reach the newest cycle.
fi_ibis_derive_actual <- function(editions, long, industry_id, cfg, workbook_rows = NULL) {
  policy <- cfg$ibisworld$actual_vintage_policy %||% "single_latest_vintage"
  if (identical(policy, "pinned_workbook")) {
    if (is.null(workbook_rows)) stop("fi_ibis_derive_actual: pinned_workbook industry ", industry_id, " needs its workbook rows", call. = FALSE)
    out <- workbook_rows[workbook_rows$sector == industry_id, , drop = FALSE]
    attr(out, "vintage") <- NA_character_
    attr(out, "waiting") <- FALSE
    return(out)
  }
  code <- tolower(cfg$ibisworld$code)
  study <- cfg$ibisworld$study_sector %||% industry_id
  first_year <- as.integer(cfg$ibisworld$actual_first_year %||% 2006L)
  min_ed <- cfg$ibisworld$actual_min_edition %||% NA_character_
  ok <- editions[!is.na(editions$code) & editions$code == code & editions$status %in% "checked" & editions$layout %in% c("L1", "L2", "L3"), , drop = FALSE]
  empty <- as.data.frame(stats::setNames(rep(list(logical()), length(FI_COLS_07)), FI_COLS_07), stringsAsFactors = FALSE)
  if (!nrow(ok)) {
    attr(empty, "vintage") <- NA_character_; attr(empty, "waiting") <- TRUE
    return(empty)
  }
  e <- ok[.fi_ed_order(ok), , drop = FALSE]
  e <- e[nrow(e), ]
  pub_year <- as.integer(substr(e$edition_month, 1, 4))
  lv <- long[long$code == code & long$edition_month == e$edition_month, , drop = FALSE]
  rev_of <- function(y) lv$revenue_million[match(y, lv$year)]
  years <- sort(lv$year[lv$year < pub_year & lv$year >= first_year])
  rows <- lapply(years, function(y) {
    g <- function(yy) { a <- rev_of(yy); b <- rev_of(yy - 1L); if (is.na(a) || is.na(b) || b == 0) NA_real_ else 100 * (a / b - 1) }
    base_txt <- if (!is.na(e$price_base_year)) sprintf(" ('Figures are inflation adjusted to %d')", e$price_base_year) else ""
    data.frame(sector = industry_id, year = y, revenue_million_constant_2026_usd = NA_real_, growth_same_year_pct = g(y), growth_previous_year_pct = g(y - 1L),
               ibisworld_code = code, report_vintage = e$edition_month, source_url = e$key_stats_url,
               study_sector = study, revenue_million_report_price_basis = rev_of(y), revenue_price_basis = e$price_basis,
               printed_revenue_change_pct = lv$printed_change_pct[match(y, lv$year)],
               value_type = sprintf("historical estimate in the %s vintage (year before publication year)", e$edition_month),
               source_pdf = e$vault_path, source_pdf_sha256 = e$source_sha256, source_page = lv$pdf_page[match(y, lv$year)],
               build_note = sprintf("Single vintage: IBISWorld %s %s Key Statistics, %s%s; growth computed within this vintage only.", code, e$edition_month, e$price_basis %||% "price basis not stated", base_txt),
               status = "observed", source_id = sprintf("ibis_%s_%s_key_statistics", code, gsub("-", "_", e$edition_month)), stringsAsFactors = FALSE)
  })
  out <- if (length(rows)) do.call(rbind, rows)[FI_COLS_07] else empty
  attr(out, "vintage") <- e$edition_month
  attr(out, "waiting") <- !is.na(min_ed) && e$edition_month < min_ed
  out
}

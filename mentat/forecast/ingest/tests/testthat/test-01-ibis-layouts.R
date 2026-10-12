# IBISWorld report layouts L1 / L2 / L3 on SYNTHETIC text (invented numbers), the row-alignment negative control, the price-basis
# rules and the publication-date rules. FEEDS_SPEC 8 test 1.

inds <- function() synth_feeds()$industries

test_that("L3 modern layout: values, unit, price basis, date, code from the Key Statistics URL, layout", {
  ed <- fi_ibis_parse_pages(synth_l3_pages(), inds())
  expect_equal(ed$code, "99912")
  expect_equal(ed$code_source, "url")
  expect_match(ed$key_stats_url, "industry/99912/key-statistics", fixed = TRUE)
  expect_equal(ed$edition_month, "2021-03")
  expect_equal(ed$date_precision, "month")
  expect_equal(ed$layout, "L3")
  expect_equal(ed$revenue_unit, "USD million")
  expect_equal(ed$price_base_year, 2021L)
  expect_equal(ed$price_basis, "constant 2021 USD")
  expect_equal(ed$values$year, 2008:2026)
  expect_equal(ed$values$revenue_million, syn_revenue(2008:2026))
  expect_equal(ed$changes$year, 2009:2026)
  expect_equal(ed$changes$printed_change_pct, syn_change(syn_revenue(2008:2026))[-1])
  expect_length(ed$issues, 0L)
  ck <- fi_ibis_check_edition(ed, first_seen_utc = "2026-09-30T00:00:00Z", filename = "2021_03_99912.pdf", registry_codes = "99912")
  expect_equal(ck$status, "checked")
  expect_true(ck$numerics_ok)
  expect_gt(nrow(ck$checks), 0L)
})

test_that("L2 mid layout: Revenue ($m) first column, title lookup, explicit inflation-adjusted year", {
  ed <- fi_ibis_parse_pages(synth_l2_pages(), inds())
  expect_equal(ed$code, "99912")
  expect_equal(ed$code_source, "title")
  expect_equal(ed$edition_month, "2014-12")
  expect_equal(ed$layout, "L2")
  expect_equal(ed$revenue_unit, "USD million")
  expect_equal(ed$price_basis, "constant 2014 USD")
  expect_equal(ed$values$revenue_million, syn_revenue(2008:2022, 500000))
  expect_equal(fi_ibis_check_edition(ed, "2026-09-30T00:00:00Z", registry_codes = "99912")$status, "checked")
})

test_that("L2 glossary rule sets the price base to the publication year", {
  ed <- fi_ibis_parse_pages(synth_l2_pages(glossary = TRUE, pub = list(2016, "June")), inds())
  expect_equal(ed$price_base_year, 2016L)
  expect_match(ed$price_basis_evidence, "glossary")
})

test_that("L1 legacy Outlook table: day precision from 'Month D YYYY', 'In YYYY constant dollars' price base, growth column as printed change", {
  ed <- fi_ibis_parse_pages(synth_l1_pages(), inds())
  expect_equal(ed$code, "99912")
  expect_equal(ed$code_source, "cover")
  expect_equal(ed$layout, "L1")
  expect_equal(ed$pub_date, "2008-01-10")
  expect_equal(ed$date_precision, "day")
  expect_equal(ed$price_basis, "constant 2006 USD")
  expect_equal(ed$values$year, 2007:2013)
  expect_equal(ed$values$revenue_million, syn_revenue(2007:2013, 500000))
  expect_equal(ed$changes$year, 2008:2013)       # 2007 growth is N/A; the Gross Product table below is not read as revenue
  expect_length(ed$issues, 0L)
  expect_equal(fi_ibis_check_edition(ed, "2026-09-30T00:00:00Z", registry_codes = "99912")$status, "checked")
})

test_that("L1 with only real-rate prose states a real-growth basis without a base year; '28 December 2006' form gives day precision", {
  ed <- fi_ibis_parse_pages(synth_l1_pages(constant = NA, real_rate = TRUE, pub = list(2006, "December", 28), day_first = TRUE, years = 2005:2010), inds())
  expect_equal(ed$pub_date, "2006-12-28")
  expect_equal(ed$date_precision, "day")
  expect_true(is.na(ed$price_base_year))
  expect_match(ed$price_basis, "real growth stated in outlook")
  expect_length(ed$issues, 0L)
})

test_that("a legacy header row of bare years is not read as a value row, and Gross Product rows are not read as revenue", {
  pages <- synth_l1_pages()
  pages[[3]] <- "KEY STATISTICS\nKey Statistics\nCONSTANT PRICES\n                       2002         2003         2004\n2002 2003 2004 2005 2006\nIndustry Revenue   443,840.3   485,022.0   499,459.7\n"
  ed <- fi_ibis_parse_pages(pages, inds())
  expect_equal(ed$values$year, 2007:2013)
  expect_false(any(ed$values$revenue_million < 10000))
})

test_that("a PDF written from synthetic text round-trips through pdftools into the same record", {
  skip_if_not_installed("pdftools")
  d <- local_ingest_env()
  p <- write_synth_pdf(d$root, "syn.pdf", "l3")
  ed <- fi_ibis_parse_pdf(p, inds())
  expect_equal(ed$code, "99912")
  expect_equal(ed$edition_month, "2021-03")
  expect_equal(ed$values$revenue_million, syn_revenue(2008:2026))
  expect_equal(ed$price_basis, "constant 2021 USD")
  expect_equal(fi_ibis_check_edition(ed, "2026-09-30T00:00:00Z", registry_codes = "99912")$status, "checked")
})

test_that("NEGATIVE CONTROL: a one-row year shift (the xpdf failure) is caught by the per-year Annual Change check", {
  ed <- fi_ibis_parse_pages(synth_l3_pages(shift = TRUE), inds())
  ck <- fi_ibis_check_edition(ed, "2026-09-30T00:00:00Z", registry_codes = "99912")
  expect_equal(ck$status, "review_required")
  expect_true("printed_change_misaligned_with_revenue" %in% ck$issues)
  expect_false(ck$numerics_ok)
  row <- ck$checks[grepl("aligns", ck$checks$name), ]
  expect_gt(row$failures, 0L)
  # and the unshifted table passes the same check
  ok <- fi_ibis_check_edition(fi_ibis_parse_pages(synth_l3_pages(), inds()), "2026-09-30T00:00:00Z", registry_codes = "99912")
  expect_equal(ok$checks$failures[grepl("aligns", ok$checks$name)], 0L)
})

test_that("a missing price basis gives review_required with the basis issue only (numerics stay usable)", {
  ed <- fi_ibis_parse_pages(synth_l3_pages(footnote = FALSE), inds())
  expect_true(is.na(ed$price_basis))
  ck <- fi_ibis_check_edition(ed, "2026-09-30T00:00:00Z", registry_codes = "99912")
  expect_equal(ck$status, "review_required")
  expect_equal(ck$issues, "constant_price_base_missing")
  expect_true(ck$numerics_ok)
})

test_that("two different inflation-adjusted years on the report are ambiguous, not silently resolved", {
  pages <- synth_l3_pages()
  pages[[5]] <- paste0(pages[[5]], "\n*Figures are inflation adjusted to 2019")
  ed <- fi_ibis_parse_pages(pages, inds())
  expect_true("constant_price_base_ambiguous" %in% ed$issues)
  expect_true(is.na(ed$price_basis))
})

test_that("publication date rules: missing or ambiguous cover date is flagged; a date after first-seen is flagged", {
  pages <- synth_l3_pages()
  pages[[1]] <- "IBISWorld | Synthetic Widget Carriers in the US\nINDUSTRY REPORT\nNo date here"
  pages[[2]] <- "Table of Contents\nKey Statistics"
  ed <- fi_ibis_parse_pages(pages, inds())
  expect_true("publication_date_missing_or_ambiguous_on_cover" %in% ed$issues)
  pages[[1]] <- "IBISWorld | Synthetic Widget Carriers in the US  Mar 2021\nupdated Apr 2021"
  expect_true("publication_date_missing_or_ambiguous_on_cover" %in% fi_ibis_parse_pages(pages, inds())$issues)
  future <- fi_ibis_parse_pages(synth_l3_pages(pub = list(2031, "Mar")), inds())
  ck <- fi_ibis_check_edition(future, first_seen_utc = "2026-09-30T00:00:00Z", registry_codes = "99912")
  expect_true("publication_date_after_first_seen" %in% ck$issues)
})

test_that("date finder: month-year, month-day-year, day-month-year and abbreviations, merged by month", {
  d <- fi_find_dates("Nov 2024 and November 2 2024")
  expect_equal(nrow(d), 1L); expect_equal(d$day, 2L)
  d2 <- fi_find_dates("28 December 2006 ... December 2006")
  expect_equal(nrow(d2), 1L); expect_equal(d2$day, 28L)
  d3 <- fi_find_dates("USDecember 2016 1 Saving up")
  expect_equal(c(d3$year, d3$month), c(2016L, 12L))
  expect_equal(nrow(fi_find_dates("no dates here 12345")), 0L)
})

test_that("file-name hints: the Codex archive name and the canonical name, both only hints", {
  expect_equal(fi_ibis_filename_hint("WebsiteFiles_Downloads_us_archive_2024_11_52412 (2).pdf")$code, "52412")
  expect_equal(fi_ibis_filename_hint("WebsiteFiles_Downloads_us_archive_2024_11_52412 (2).pdf")$date, "2024-11")
  expect_equal(fi_ibis_filename_hint("2006_12_52412.pdf")$date, "2006-12")
  expect_true(is.na(fi_ibis_filename_hint("report.pdf")$code))
})

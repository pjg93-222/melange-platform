# IBISWorld Key Statistics export (xlsx), review mode: no real sample exists, so the reader works by convention and every read edition
# stays review_required until the owner confirms the first real export. FEEDS_SPEC 6 step 5. Synthetic workbooks only.

make_xlsx <- function(path, years = 2019:2024, rev = c(100, 101.5, 103.2, 104, 106.5, 108.1), with_date = TRUE, with_basis = TRUE, unit = "($ Million)") {
  skip_if_not_installed("writexl")
  top <- list(c("Synthetic Widget Carriers in the US 99912", "", ""), c(if (with_date) "Nov 2024" else "", "", ""), c(if (with_basis) "*Figures are inflation adjusted to 2024" else "", "", ""),
              c("Year", paste("Revenue", unit), "IVA"))
  body <- lapply(seq_along(years), function(i) c(as.character(years[i]), format(rev[i], nsmall = 1), "20.0"))
  m <- do.call(rbind, c(top, body))
  writexl::write_xlsx(as.data.frame(m, stringsAsFactors = FALSE), path, col_names = FALSE)
  path
}

test_that("an xlsx export is read by its Year / Revenue header, unit, date and price base, and is marked review_required", {
  d <- local_ingest_env()
  p <- make_xlsx(file.path(d$root, "e.xlsx"))
  ed <- fi_ibis_read_xlsx(p, synth_feeds()$industries)
  expect_equal(ed$layout, "xlsx")
  expect_equal(ed$code, "99912")
  expect_equal(ed$edition_month, "2024-11")
  expect_equal(ed$revenue_unit, "USD million")
  expect_equal(ed$values$year, 2019:2024)
  expect_equal(ed$values$revenue_million, c(100, 101.5, 103.2, 104, 106.5, 108.1))
  expect_equal(ed$price_base_year, 2024L)
  expect_true("xlsx_layout_not_fixture_verified" %in% ed$issues)
  ck <- fi_ibis_check_edition(ed, "2026-09-30T00:00:00Z", registry_codes = "99912")
  expect_equal(ck$status, "review_required")
  expect_false(ck$numerics_ok)                       # so it never reaches a derived series
})

test_that("billions are converted to millions; a missing date, unit or basis is flagged", {
  d <- local_ingest_env()
  ed <- fi_ibis_read_xlsx(make_xlsx(file.path(d$root, "b.xlsx"), rev = c(1, 1.1, 1.2, 1.3, 1.4, 1.5), unit = "($ Billion)"), synth_feeds()$industries)
  expect_equal(ed$revenue_unit, "USD billion")
  expect_equal(ed$values$revenue_million[1], 1000)
  nd <- fi_ibis_read_xlsx(make_xlsx(file.path(d$root, "n.xlsx"), with_date = FALSE, with_basis = FALSE, unit = ""), synth_feeds()$industries)
  expect_true(all(c("publication_date_missing_or_ambiguous_on_cover", "constant_price_base_missing", "revenue_unit_not_verified") %in% nd$issues))
})

test_that("a workbook with no Year / Revenue table reads as NULL and is recorded for review by the collector", {
  skip_if_not_installed("writexl")
  d <- local_ingest_env()
  ib <- file.path(d$inbox, "ibisworld"); dir.create(ib, recursive = TRUE)
  writexl::write_xlsx(data.frame(a = 1:3, b = c("x", "y", "z")), file.path(ib, "other.xlsx"))
  expect_null(fi_ibis_read_xlsx(file.path(ib, "other.xlsx")))
  scan <- fi_inbox_scan(ib, ledger_path = file.path(d$frt, "s.json"))
  col <- fi_ibis_collect(scan, synth_feeds())
  expect_equal(col$editions$status, "review_required")
  expect_match(col$editions$issues, "no Year/Revenue table found", fixed = TRUE)
})

test_that("when a PDF of the same edition exists the xlsx is only a cross-check: agreement passes, disagreement is flagged", {
  skip_if_not_installed("pdftools")
  d <- local_ingest_env()
  pdf_ed <- fi_ibis_parse_pages(synth_l3_pages(pub = list(2024, "Nov"), years = 2019:2024, base = 2024, start = 100), synth_feeds()$industries)
  x_ok <- list(values = data.frame(year = 2019:2024, revenue_million = pdf_ed$values$revenue_million + 0.02))
  x_bad <- list(values = data.frame(year = 2019:2024, revenue_million = pdf_ed$values$revenue_million + 5))
  expect_length(fi_ibis_xlsx_crosscheck(x_ok, pdf_ed), 0L)
  expect_equal(fi_ibis_xlsx_crosscheck(x_bad, pdf_ed), "xlsx_differs_from_pdf")
  expect_equal(fi_ibis_xlsx_crosscheck(list(values = data.frame(year = 1990:1991, revenue_million = c(1, 2))), pdf_ed), "xlsx_shares_no_year_with_pdf")
})

# Derived 06 (predicted) and 07 (actual) shaped feeds: edition selection with the look-ahead guard, single-vintage actuals with no
# splicing, pinned-workbook pass-through, exact column sets. FEEDS_SPEC 8 test 4. Synthetic editions only.

build_editions <- function(d, specs) {
  ib <- file.path(d$inbox, "ibisworld"); dir.create(ib, recursive = TRUE, showWarnings = FALSE)
  for (nm in names(specs)) do.call(write_synth_pdf, c(list(ib, paste0(nm, ".pdf"), "l3"), specs[[nm]]))
  scan <- fi_inbox_scan(ib, ledger_path = file.path(d$frt, "inbox_seen.json"), now = "2026-09-30T00:00:00Z")
  fi_ibis_collect(scan, synth_feeds())
}

SPECS <- list(
  e2019 = list(pub = list(2019, "Mar"), years = 2008:2024, base = 2019, start = 90000),
  e2020a = list(pub = list(2020, "Feb"), years = 2008:2025, base = 2020, start = 95000),
  e2020b = list(pub = list(2020, "Oct"), years = 2008:2025, base = 2020, start = 97000),
  e2022 = list(pub = list(2022, "May"), years = 2008:2027, base = 2022, start = 101000))

cfg <- function() synth_feeds()$industries$widgets

test_that("predicted rows: the latest edition published in the year BEFORE the target is used; one row per target year", {
  d <- local_ingest_env()
  col <- build_editions(d, SPECS)
  expect_equal(sort(col$editions$status), rep("checked", 4L))
  p <- fi_ibis_derive_predicted(col$editions, col$long, "widgets", cfg())
  expect_identical(names(p), FI_COLS_06)
  expect_equal(p$year, 2009:2024)
  r21 <- p[p$year == 2021, ]
  expect_equal(r21$forecast_publication_month, "2020-10")                     # latest of the three 2020 editions
  expect_equal(r21$forecast_status, "ready"); expect_equal(r21$status, "observed")
  v <- syn_revenue(2008:2025, 97000)
  expect_equal(r21$forecast_baseline_revenue_million, v[2020 - 2007]); expect_equal(r21$forecast_target_revenue_million, v[2021 - 2007])
  expect_equal(r21$collected_forecast_growth_pct, round(100 * (v[2021 - 2007] / v[2020 - 2007] - 1), 8))
  expect_equal(r21$predicted_growth_pct, r21$collected_forecast_growth_pct)
  expect_equal(r21$forecast_horizon_years, 1L)
  expect_equal(r21$forecast_age_months, 3L)                                    # 2020-10 to 2021-01
  expect_equal(r21$forecast_method, "same_report_revenue_ratio")
  expect_equal(r21$forecast_component_months, "99912:2020-10")
  expect_true(grepl("99912/key-statistics", r21$forecast_source_urls, fixed = TRUE))
  expect_true(file.exists(r21$forecast_source_pdfs))
  r20 <- p[p$year == 2020, ]
  expect_equal(r20$forecast_publication_month, "2019-03")
})

test_that("look-ahead guard: an edition published in the target year or later never serves as that target's forecast", {
  d <- local_ingest_env()
  col <- build_editions(d, SPECS)
  p <- fi_ibis_derive_predicted(col$editions, col$long, "widgets", cfg())
  # e2019 (published 2019) may not forecast 2019; nothing older exists, so 2019 and earlier are missing
  expect_equal(p$status[p$year == 2019], "missing")
  expect_true(all(p$status[p$year <= 2019] == "missing"))
  expect_true(all(is.na(p$collected_forecast_growth_pct[p$year <= 2019])))
  # for every used row the edition was published strictly before the target year
  used <- p[p$status != "missing", ]
  expect_true(all(as.integer(substr(used$forecast_publication_month, 1, 4)) < used$year))
})

test_that("no edition in the year before the target: the latest older edition is used as a two-year-ahead sensitivity row, never as 'ready'", {
  d <- local_ingest_env()
  col <- build_editions(d, SPECS)
  p <- fi_ibis_derive_predicted(col$editions, col$long, "widgets", cfg())
  r22 <- p[p$year == 2022, ]                                                   # nothing published in 2021 -> latest older is 2020-10
  expect_equal(r22$forecast_publication_month, "2020-10")
  expect_equal(r22$forecast_status, "verified_older_vintage_sensitivity")
  expect_equal(r22$status, "partial")
  expect_true(is.na(r22$predicted_growth_pct)); expect_false(is.na(r22$collected_forecast_growth_pct))
  expect_equal(r22$forecast_horizon_years, 2L)
  expect_equal(r22$forecast_note, "publication_two_calendar_years_before_target")
  expect_equal(p$forecast_status[p$year == 2023], "ready")                     # e2022 forecasts 2023 one year ahead
})

test_that("an edition with numerics verified but no price basis gives a partial, inferred row with the predicted growth withheld", {
  d <- local_ingest_env()
  specs <- c(SPECS, list(e2023 = list(pub = list(2023, "Jun"), years = 2008:2027, footnote = FALSE, start = 103000)))
  col <- build_editions(d, specs)
  e <- col$editions[col$editions$edition_month == "2023-06", ]
  expect_equal(e$status, "review_required"); expect_true(e$numerics_ok)
  p <- fi_ibis_derive_predicted(col$editions, col$long, "widgets", cfg())
  r <- p[p$year == 2024, ]
  expect_equal(r$forecast_status, "numeric_verified_price_basis_inferred"); expect_equal(r$status, "partial")
  expect_true(is.na(r$predicted_growth_pct)); expect_false(is.na(r$collected_forecast_growth_pct))
  expect_match(r$forecast_price_basis, "inferred from publisher methodology")
  expect_equal(r$forecast_note, "historical_report_price_basis_not_explicit")
  # the actual series ignores an edition that is not fully checked: the vintage stays 2022-05
  a <- fi_ibis_derive_actual(col$editions, col$long, "widgets", cfg())
  expect_equal(attr(a, "vintage"), "2022-05")
})

test_that("actual rows: one vintage only (the latest checked edition), years before its publication year, growth computed inside it", {
  d <- local_ingest_env()
  col <- build_editions(d, SPECS)
  a <- fi_ibis_derive_actual(col$editions, col$long, "widgets", cfg())
  expect_identical(names(a), FI_COLS_07)
  expect_equal(a$year, 2010:2021)                                              # actual_first_year 2010, publication year 2022
  expect_equal(unique(a$report_vintage), "2022-05")
  expect_equal(unique(a$ibisworld_code), "99912")
  v <- syn_revenue(2008:2027, 101000)
  expect_equal(a$revenue_million_report_price_basis, v[a$year - 2007])
  expect_equal(a$growth_same_year_pct, 100 * (v[a$year - 2007] / v[a$year - 2008] - 1))
  expect_equal(a$growth_previous_year_pct, 100 * (v[a$year - 2008] / v[a$year - 2009] - 1))
  expect_equal(unique(a$revenue_price_basis), "constant 2022 USD")
  expect_true(all(a$status == "observed"))
  expect_true(all(is.na(a$revenue_million_constant_2026_usd)))
  expect_equal(unique(a$source_pdf_sha256), col$editions$source_sha256[col$editions$edition_month == "2022-05"])
  expect_true(all(grepl("2022-05 vintage", a$value_type, fixed = TRUE)))
})

test_that("a new vintage replaces the whole history: no year of the older vintage survives (no splicing)", {
  d <- local_ingest_env()
  old_only <- build_editions(d, SPECS["e2020b"])
  a_old <- fi_ibis_derive_actual(old_only$editions, old_only$long, "widgets", cfg())
  expect_equal(attr(a_old, "vintage"), "2020-10")
  expect_equal(max(a_old$year), 2019L)
  d2 <- local_ingest_env()
  both <- build_editions(d2, SPECS[c("e2020b", "e2022")])
  a_new <- fi_ibis_derive_actual(both$editions, both$long, "widgets", cfg())
  expect_equal(unique(a_new$report_vintage), "2022-05")
  expect_equal(max(a_new$year), 2021L)
  # the 2020-10 vintage's values do not appear anywhere in the new series
  expect_false(any(a_new$revenue_million_report_price_basis %in% a_old$revenue_million_report_price_basis))
})

test_that("waiting flag: history older than actual_min_edition, or no edition at all, is reported as waiting for the owner's file", {
  d <- local_ingest_env()
  col <- build_editions(d, SPECS["e2020b"])
  c2 <- cfg(); c2$ibisworld$actual_min_edition <- "2025-01"
  expect_true(isTRUE(attr(fi_ibis_derive_actual(col$editions, col$long, "widgets", c2), "waiting")))
  c3 <- cfg(); c3$ibisworld$actual_min_edition <- "2019-01"
  expect_false(isTRUE(attr(fi_ibis_derive_actual(col$editions, col$long, "widgets", c3), "waiting")))
  none <- fi_ibis_derive_actual(col$editions[0, ], col$long[0, ], "widgets", c2)
  expect_equal(nrow(none), 0L)
  expect_true(isTRUE(attr(none, "waiting")))
  expect_identical(names(none), FI_COLS_07)
})

test_that("pinned_workbook industries pass through byte-identical", {
  wb <- data.frame(sector = rep(c("gadgets", "other"), each = 3), year = rep(2019:2021, 2), revenue_million_constant_2026_usd = c(1.5, 2.5, 3.5, 4, 5, 6),
                   growth_same_year_pct = c(NA, 2.123456789012345, 3), stringsAsFactors = FALSE)
  pin <- synth_feeds()$industries$gadgets
  out <- fi_ibis_derive_actual(NULL, NULL, "gadgets", pin, workbook_rows = wb)
  expect_identical({ x <- out; attr(x, "vintage") <- NULL; attr(x, "waiting") <- NULL; x }, wb[wb$sector == "gadgets", , drop = FALSE])
  expect_error(fi_ibis_derive_actual(NULL, NULL, "gadgets", pin), "needs its workbook rows")
})

test_that("column sets equal the 06 and 07 data sheets exactly, in order", {
  expect_identical(FI_COLS_06, c("sector", "year", "predicted_growth_pct", "collected_forecast_growth_pct", "forecast_status", "forecast_publication_month",
                                 "forecast_known_available_by", "forecast_baseline_year", "forecast_target_year", "forecast_baseline_revenue_million",
                                 "forecast_target_revenue_million", "forecast_price_basis", "forecast_method", "forecast_component_months", "forecast_source_urls",
                                 "forecast_source_pdfs", "forecast_source_pages", "forecast_note", "scope_qualification", "forecast_horizon_years", "study_sector",
                                 "forecast_age_months", "status", "source_id"))
  expect_identical(FI_COLS_07, c("sector", "year", "revenue_million_constant_2026_usd", "growth_same_year_pct", "growth_previous_year_pct", "ibisworld_code",
                                 "report_vintage", "source_url", "study_sector", "revenue_million_report_price_basis", "revenue_price_basis", "printed_revenue_change_pct",
                                 "value_type", "source_pdf", "source_pdf_sha256", "source_page", "build_note", "status", "source_id"))
  d <- local_ingest_env()
  col <- build_editions(d, SPECS)
  expect_identical(names(fi_ibis_derive_predicted(col$editions, col$long, "widgets", cfg())), FI_COLS_06)
  expect_identical(names(col$editions), FI_COLS_EDITIONS)
  expect_identical(names(col$long), FI_COLS_LONG)
  # the long table types every value as historical / current / forecast relative to the publication year
  e22 <- col$long[col$long$edition_month == "2022-05", ]
  expect_equal(unique(e22$value_kind[e22$year < 2022]), "historical"); expect_equal(e22$value_kind[e22$year == 2022], "current"); expect_equal(unique(e22$value_kind[e22$year > 2022]), "forecast")
})

test_that("CSV writer: UTF-8 without BOM, LF, NA as empty, quotes doubled; read back unchanged", {
  d <- local_ingest_env()
  df <- data.frame(a = c("x,y", "say \"hi\"", NA), b = c(1.5, NA, 3), stringsAsFactors = FALSE)
  p <- file.path(d$root, "t.csv")
  sha <- fi_write_csv_atomic(df, p)
  expect_equal(sha, unname(tools::sha256sum(p)))
  raw <- readBin(p, "raw", file.size(p))
  expect_false(identical(raw[1:3], as.raw(c(0xef, 0xbb, 0xbf))))
  expect_false(as.raw(13L) %in% raw)
  back <- fi_read_csv(p)
  expect_equal(back$a, c("x,y", "say \"hi\"", NA)); expect_equal(as.numeric(back$b), c(1.5, NA, 3))
})

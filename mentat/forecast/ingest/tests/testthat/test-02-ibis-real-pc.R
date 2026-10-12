# LOCAL-ONLY: the 18 real property & casualty editions (IBISWorld 52412) reproduce the Sector Growth Study workbooks 06 and 07.
# The PDFs are licensed and are never in git; the test looks each file up by its pinned sha256 (the pipeline vault first, then the
# Downloads copy) and SKIPS with a reason when they are absent. FEEDS_SPEC 8 test 2; reproduces work/ibis_r_parity_probe_console.txt.

PC_DIR <- "C:/Users/pgoff/Downloads/Sector_Research_Source_Check_2026-09-25/expanded_sectors/property_casualty/source_pdfs"
PC_PINS <- c("2024_11_52412.pdf" = "f484c65f2afbbdc8514eca2bac4f9d6d49f6bb689547d60e9519d0121f3b3b21",
             "2016_12_52412.pdf" = "76e370d7678b6bc225ff20ab81b12bdcd7d77e4680a7d1930b93cb0ca4f900fb",
             "2008_01_52412.pdf" = "b3d7d054c7d4dfbb480c5def9e8e9a63e502da03250453f46b3bf175151d0704",
             "2006_12_52412.pdf" = "0636eb8f26fc1b53553248f5c5018a1e44a2892efe5e62c626eb2cdf5243449e")

pc_files <- function() {
  f <- sort(list.files(PC_DIR, pattern = "^[0-9]{4}_[0-9]{2}_52412\\.pdf$", full.names = TRUE))
  if (length(f) != 18L) return(NULL)
  for (nm in names(PC_PINS)) {
    p <- find_pinned(PC_PINS[[nm]], nm, file.path(PC_DIR, nm))
    if (is.null(p)) return(NULL)
  }
  f
}

wb_data <- function(name) {
  p <- file.path(rf_roots()$SGSD, name)
  if (!file.exists(p)) return(NULL)
  rf_read_xlsx(p, "data")
}

read_header06 <- function() { wb <- wb_data("06_predicted_industry_growth.xlsx"); wb[0, ] }

skip_unless_pc <- function() {
  skip_if(is.null(pc_files()), "the 18 P&C IBISWorld editions (pinned sha256) are not on this machine")
  skip_if(is.null(wb_data("06_predicted_industry_growth.xlsx")) || is.null(wb_data("07_actual_industry_growth.xlsx")), "workbooks 06/07 are not on this machine")
}

real_pc <- function() memo_pc(function() {
  root <- tempfile("fi_pc_")
  ib <- file.path(root, "inbox", "ibisworld"); dir.create(ib, recursive = TRUE)
  file.copy(pc_files(), ib)
  feeds <- fi_read_feeds()
  scan <- fi_inbox_scan(ib, ledger_path = file.path(root, "inbox_seen.json"))
  col <- fi_ibis_collect(scan, feeds)
  cfg <- feeds$industries$property_casualty
  list(col = col, pred = fi_ibis_derive_predicted(col$editions, col$long, "property_casualty", cfg), act = fi_ibis_derive_actual(col$editions, col$long, "property_casualty", cfg), cfg = cfg)
})
.pc_cache <- new.env(parent = emptyenv())
memo_pc <- function(f) { if (is.null(.pc_cache$x)) .pc_cache$x <- f(); .pc_cache$x }

test_that("the 18 real editions parse: 17 fully checked, the 2023-11 edition flagged for its missing price basis only", {
  skip_unless_pc()
  r <- real_pc()
  e <- r$col$editions
  expect_equal(nrow(e), 18L)
  expect_equal(sum(e$status == "checked"), 17L)
  bad <- e[e$status != "checked", ]
  expect_equal(bad$edition_month, "2023-11")
  expect_equal(bad$issues, "constant_price_base_missing")
  expect_true(bad$numerics_ok)
  expect_true(all(e$code == "52412"))
  expect_equal(e$edition_month, c("2006-12", "2008-01", "2009-11", "2010-11", "2011-12", "2012-12", "2013-12", "2014-10", "2015-12", "2016-12", "2017-06",
                                  "2018-12", "2019-05", "2020-12", "2021-08", "2022-06", "2023-11", "2024-11"))
  expect_equal(e$layout[e$edition_month %in% c("2006-12", "2008-01", "2009-11")], rep("L1", 3L))
  expect_equal(e$layout[e$edition_month == "2024-11"], "L3")
})

test_that("06: predicted rows reproduce the workbook - revenues exactly, growth within 1e-7, status, dates, price basis, horizon, age, pages", {
  skip_unless_pc()
  r <- real_pc()
  wb <- wb_data("06_predicted_industry_growth.xlsx")
  wb <- wb[wb$sector == "property_casualty", ]
  p <- r$pred[r$pred$year %in% wb$year, ]
  p <- p[order(p$year), ]; wb <- wb[order(wb$year), ]
  expect_equal(nrow(p), 18L)
  expect_identical(names(r$pred), names(read_header06()))
  expect_equal(p$forecast_baseline_revenue_million, wb$forecast_baseline_revenue_million)
  expect_equal(p$forecast_target_revenue_million, wb$forecast_target_revenue_million)
  expect_lt(max(abs(p$collected_forecast_growth_pct - wb$collected_forecast_growth_pct)), 1e-7)
  expect_identical(is.na(p$predicted_growth_pct), is.na(wb$predicted_growth_pct))
  expect_lt(max(abs(p$predicted_growth_pct - wb$predicted_growth_pct), na.rm = TRUE), 1e-7)
  expect_identical(p$forecast_status, wb$forecast_status)
  expect_identical(p$status, wb$status)
  expect_identical(p$forecast_publication_month, wb$forecast_publication_month)
  expect_identical(p$forecast_known_available_by, wb$forecast_known_available_by)
  expect_identical(p$forecast_price_basis, wb$forecast_price_basis)
  expect_equal(p$forecast_horizon_years, wb$forecast_horizon_years)
  expect_equal(p$forecast_age_months, wb$forecast_age_months)
  expect_equal(p$forecast_source_pages, as.integer(wb$forecast_source_pages))
  expect_identical(p$forecast_note, ifelse(is.na(wb$forecast_note), NA_character_, wb$forecast_note))
  expect_identical(p$forecast_component_months, wb$forecast_component_months)
  # the 2026 target has no edition published in 2025 yet: the 2024-11 edition serves as a two-year-ahead sensitivity row, never as "ready"
  r26 <- r$pred[r$pred$year == 2026, ]
  expect_equal(r26$forecast_status, "verified_older_vintage_sensitivity")
  expect_equal(r26$forecast_publication_month, "2024-11")
  expect_equal(r26$forecast_horizon_years, 2L)
  expect_true(is.na(r26$predicted_growth_pct))
})

test_that("07: the 2024-11 single vintage reproduces the workbook 2006-2023 exactly (revenue, printed change) and growth to 1e-9", {
  skip_unless_pc()
  r <- real_pc()
  wb <- wb_data("07_actual_industry_growth.xlsx")
  wb <- wb[wb$sector == "property_casualty", ]
  wb <- wb[order(wb$year), ]
  a <- r$act[order(r$act$year), ]
  expect_identical(names(a), names(wb))
  expect_equal(a$year, 2006:2023)
  expect_equal(a$revenue_million_report_price_basis, wb$revenue_million_report_price_basis)
  expect_equal(a$printed_revenue_change_pct, wb$printed_revenue_change_pct)
  expect_lt(max(abs(a$growth_same_year_pct - wb$growth_same_year_pct)), 1e-9)
  expect_lt(max(abs(a$growth_previous_year_pct - wb$growth_previous_year_pct)), 1e-9)
  expect_equal(unique(a$report_vintage), "2024-11")
  expect_equal(unique(a$revenue_price_basis), "constant 2024 USD")
  expect_equal(unique(a$source_pdf_sha256), PC_PINS[["2024_11_52412.pdf"]])
  expect_equal(a$source_page, as.integer(wb$source_page))
  expect_true(isTRUE(attr(r$act, "waiting")))          # the newest edition (2024-11) predates actual_min_edition 2025-01: the history stops at 2023
})

test_that("the pinned sha256 of four editions match the file bytes", {
  skip_unless_pc()
  for (nm in names(PC_PINS)) expect_equal(unname(tools::sha256sum(file.path(PC_DIR, nm))), PC_PINS[[nm]])
})

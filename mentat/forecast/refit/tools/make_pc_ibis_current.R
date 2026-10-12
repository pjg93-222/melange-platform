# One-time derivation of the property and casualty current-year row from the IBISWorld 52412 edition of 2024-11 (owner ruling 2026-09-30).
#   & "C:\Program Files\R\R-4.6.0\bin\Rscript.exe" mentat\forecast\refit\tools\make_pc_ibis_current.R
#
# Workbook 07 holds P&C 2006-2023 from the 2024-11 edition only ("07 = 2024-11 vintage only; ends 2023"). That edition's 2024 row is IBISWorld's
# current-year ESTIMATE (value_kind "current"), not an actual. The owner ruled that the 2024-11 edition is P&C's ONE vintage for the whole actual
# series, 2024 included, because no 2025+ edition is available: the series is never spliced across editions. This tool parses the licensed PDF with the
# feeds ingester's own parser (read-only use of mentat/forecast/ingest/R), checks the pinned sha256 of the edition, and writes one 07-shaped row to
# {RFDATA}/ibis_pc_2024_11_current_year.csv. The refit pins that CSV like any raw input (registry/inputs.json, role ibis_actual_current:property_casualty)
# and uses the row only for a (sector, year) that workbook 07 does not hold: the day a 2025+ edition puts 2024 in the main series, the estimate drops out.
# The PDF and the CSV are licensed content and stay out of git.

fa <- grep("^--file=", commandArgs(trailingOnly = FALSE), value = TRUE)
here <- if (length(fa)) dirname(normalizePath(sub("^--file=", "", fa[1]), winslash = "/")) else getwd()
.RF_HOME <- normalizePath(file.path(here, ".."), winslash = "/")
.libPaths(c("C:/Users/pgoff/AppData/Local/R/win-library/4.6", .libPaths()))
source(file.path(.RF_HOME, "R", "rf_config.R"))
source(file.path(.RF_HOME, "R", "rf_io.R"))
PDF <- "C:/Users/pgoff/Downloads/Sector_Research_Source_Check_2026-09-25/expanded_sectors/property_casualty/source_pdfs/2024_11_52412.pdf"
PDF_SHA <- "f484c65f2afbbdc8514eca2bac4f9d6d49f6bb689547d60e9519d0121f3b3b21"
stopifnot(file.exists(PDF), identical(rf_sha256(PDF), PDF_SHA))

ing <- normalizePath(file.path(.RF_HOME, "..", "ingest"), winslash = "/")
assign(".FI_HOME", ing, envir = globalenv())
source(file.path(ing, "R", "fi_config.R"), encoding = "UTF-8")
fi_source_all(ing)
root <- tempfile("pc_current_"); ib <- file.path(root, "inbox", "ibisworld"); dir.create(ib, recursive = TRUE)
file.copy(PDF, ib)
feeds <- fi_read_feeds()
col <- fi_ibis_collect(fi_inbox_scan(ib, ledger_path = file.path(root, "inbox_seen.json")), feeds)
ed <- col$editions
stopifnot(nrow(ed) == 1L, ed$status == "checked", ed$code == "52412", ed$edition_month == "2024-11")
lv <- col$long[col$long$code == "52412" & col$long$edition_month == "2024-11", ]
cur <- lv[lv$value_kind == "current", ]
stopifnot(nrow(cur) == 1L, cur$year == 2024L)
rev <- function(y) lv$revenue_million[match(y, lv$year)]
g <- function(y) 100 * (rev(y) / rev(y - 1L) - 1)
ok_hist <- lv[lv$year >= 2006 & lv$year <= 2023, ]
# the same single edition reproduces workbook 07 for 2006-2023 (revenue and printed change), so the added row belongs to that series
wb <- rf_read_xlsx(file.path(rf_roots()$SGSD, "07_actual_industry_growth.xlsx"), "data")
wb <- wb[wb$sector == "property_casualty", ]
wb <- wb[order(wb$year), ]
stopifnot(identical(as.integer(wb$year), 2006:2023), all.equal(wb$revenue_million_report_price_basis, ok_hist$revenue_million[order(ok_hist$year)]),
          all(wb$report_vintage == "2024-11"))
out <- data.frame(sector = "property_casualty", year = 2024L, revenue_million_constant_2026_usd = NA_real_, growth_same_year_pct = g(2024L), growth_previous_year_pct = g(2023L),
                  ibisworld_code = "52412", report_vintage = "2024-11", source_url = ed$key_stats_url, study_sector = "property_casualty",
                  revenue_million_report_price_basis = cur$revenue_million, revenue_price_basis = ed$price_basis, printed_revenue_change_pct = cur$printed_change_pct,
                  value_type = "current-year ESTIMATE in the 2024-11 edition (value_kind current); not an actual", source_pdf = basename(PDF), source_pdf_sha256 = PDF_SHA,
                  source_page = cur$pdf_page,
                  build_note = "Single vintage (owner ruling 2026-09-30): P&C 2024 revenue is IBISWorld's current-year estimate from the 2024-11 edition; replaced automatically when a 2025+ edition is ingested.",
                  status = "observed", source_id = "ibis_52412_2024_11_key_statistics_current_year", stringsAsFactors = FALSE)
dest <- file.path(rf_expand("{RFDATA}"), "ibis_pc_2024_11_current_year.csv")
dir.create(dirname(dest), recursive = TRUE, showWarnings = FALSE)
utils::write.csv(out, dest, row.names = FALSE, na = "", fileEncoding = "UTF-8")
cat(sprintf("wrote %s\n  sha256 %s\n  2024 revenue %.1f (printed change %.1f%%), growth within the vintage %.4f%%\n", dest, rf_sha256(dest), out$revenue_million_report_price_basis,
            out$printed_revenue_change_pct, out$growth_same_year_pct))

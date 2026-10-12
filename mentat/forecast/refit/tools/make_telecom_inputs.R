# One-time derivation of the Telecom inputs (owner ruling 2026-10-08: "use B09 and wireless+cable for Telecom giving").
#   & "C:\Program Files\R\R-4.6.0\bin\Rscript.exe" mentat\forecast\refit\tools\make_telecom_inputs.R
#
# Three files, each pinned by sha256 in registry/inputs.json:
#   {RFDATA}/B09_totals.json                      the OpenSecrets "Totals by Election Cycle" table of category B09 (Telecom Services & Equipment; B08 Telephone Utilities is NOT used),
#                                                 read from the page the 2026-10-01 collector saved (outputs/opensecrets-2008-2026-20261001/raw/<sha>.html) and written in the capture envelope
#                                                 the refit already reads for F03 / F05 / F09 (rf_os_totals).
#   {RFDATA}/ibis_telecom_2024_current_year.csv   the 07-shaped 2024 rows of the two IBISWorld components (wireless 51721 edition 2024-10, cable 51711a edition 2024-12): in both editions 2024 is the
#                                                 publication year, i.e. a current-year ESTIMATE, from the SAME editions as workbook 07's series (editions are never spliced).
#   {CYCLE}/ibis_telecom_2024_p87_p89_p45_p46.csv the two components' revenue levels and reported annual change 2022-2027 (the pages the evidence files hold), the IBISWorld growth path of the 50/50 budget rule.
# The IBISWorld pages are read from the extracted-evidence files of the telecom source check (sha256 pinned below); nothing is fetched. The licensed PDFs and the OpenSecrets page stay out of git.

fa <- grep("^--file=", commandArgs(trailingOnly = FALSE), value = TRUE)
here <- if (length(fa)) dirname(normalizePath(sub("^--file=", "", fa[1]), winslash = "/")) else getwd()
.RF_HOME <- normalizePath(file.path(here, ".."), winslash = "/")
.libPaths(c("C:/Users/pgoff/AppData/Local/R/win-library/4.6", .libPaths()))
source(file.path(.RF_HOME, "R", "rf_config.R"))
source(file.path(.RF_HOME, "R", "rf_io.R"))
source(file.path(.RF_HOME, "R", "rf_opensecrets.R"))

OS_HTML <- "C:/Users/pgoff/Mentat/outputs/opensecrets-2008-2026-20261001/raw/c3fbb06a677823c89c6cd2d6f7008fa6d0de92b178e55eae9daa04180c63a500.html"
OS_HTML_SHA <- "567cdb6e65313402dd356a2940a5c9f72637d15ce268d0b405f187c38655e7e2"
OS_URL <- "https://www.opensecrets.org/industries/totals?ind=B09&cycle=2026"
OS_RETRIEVED <- "2026-10-01T20:50:57+00:00"
EVID <- "C:/Users/pgoff/Downloads/Sector_Research_Source_Check_2026-09-25/expanded_sectors/telecom_ibis/extracted_evidence"
COMP <- list(
  list(sector = "telecom_wireless", code = "51721", edition = "2024-10", file = "2024_10_51721.json", pdf_sha = "62c696e04ca8ea793c28fa2014a277a4fbe36a81260f039c09c8f5a908c1725f", rev_page = "87", chg_page = "89"),
  list(sector = "telecom_cable", code = "51711a", edition = "2024-12", file = "2024_12_51711a.json", pdf_sha = "76022804644ee64074204c45aaab4f9b37b21dd9a2a8afb4d1a5273947354f3f", rev_page = "45", chg_page = "46"))

# ---- B09 totals envelope ------------------------------------------------------------------------------------------------------------------
stopifnot(file.exists(OS_HTML), identical(rf_sha256(OS_HTML), OS_HTML_SHA))
page <- rawToChar(readBin(OS_HTML, "raw", file.size(OS_HTML)))
Encoding(page) <- "UTF-8"
m <- regmatches(page, regexec('<table class="DataTable-Partial[^>]*data-collection="([^"]*)"', page))[[1]]
stopifnot(length(m) == 2L)
unesc <- function(s) { s <- gsub("&quot;", "\"", s, fixed = TRUE); s <- gsub("&#39;", "'", s, fixed = TRUE); gsub("&amp;", "&", s, fixed = TRUE) }
recs <- jsonlite::fromJSON(unesc(m[2]), simplifyVector = FALSE)
rows <- c(list(as.list(RF_TOTALS_HEADER)), lapply(recs, function(r) { stopifnot(identical(names(r), RF_TOTALS_HEADER)); unname(as.list(unlist(r))) }))
env <- list(url = OS_URL, final_url = OS_URL, title = "Telecom Services & Equipment Summary \u2022 OpenSecrets", initial_http_status = 200L, retrieved_utc = OS_RETRIEVED,
            tables = list(list(caption = "", rows = rows)), links = list(), html_sha256 = OS_HTML_SHA,
            provenance = "Table 'Totals by Election Cycle' of OpenSecrets industry B09 (Telecom Services & Equipment), parsed from the page saved by the 2026-10-01 collector (raw html sha256 above). B08 (Telephone Utilities) is not part of this scope (owner ruling 2026-10-08).")
dest <- file.path(rf_expand("{RFDATA}"), "B09_totals.json")
rf_write_json_atomic(env, dest)
tot <- rf_os_totals(dest)                                            # the identity total = individuals + PACs + outside money is asserted by the reader
stopifnot(identical(range(tot$cycle_end), c(1990L, 2024L)), nrow(tot) == 18L)
cat(sprintf("wrote %s\n  sha256 %s\n  %d cycles %d-%d; 2024 total %s, individuals %s, PACs %s\n", dest, rf_sha256(dest), nrow(tot), min(tot$cycle_end), max(tot$cycle_end),
            format(tot$total_nominal[tot$cycle_end == 2024], big.mark = ","), format(tot$individual_nominal[tot$cycle_end == 2024], big.mark = ","), format(tot$pac_nominal[tot$cycle_end == 2024], big.mark = ",")))

# ---- IBISWorld pages ------------------------------------------------------------------------------------------------------------------------
read_table <- function(text, ncol_min) {
  lines <- trimws(strsplit(text, "\n", fixed = TRUE)[[1]])
  hit <- regmatches(lines, regexec("^(\\d{4}) (N/A|-?[0-9][0-9,]*(?:\\.[0-9]+)?)(?: |$)", lines, perl = TRUE))
  hit <- hit[lengths(hit) == 3L]
  setNames(vapply(hit, function(h) if (h[3] == "N/A") NA_real_ else as.numeric(gsub(",", "", h[3])), 0), vapply(hit, `[`, "", 2L))
}
wb7 <- rf_read_xlsx(file.path(rf_roots()$SGSD, "07_actual_industry_growth.xlsx"), "data")
cyc <- list(); cur <- list()
for (cp in COMP) {
  ev_path <- file.path(EVID, cp$file)
  ev <- rf_read_json(ev_path)
  stopifnot(identical(ev$sha256, cp$pdf_sha))
  lvl <- read_table(ev$pages[[cp$rev_page]]); chg <- read_table(ev$pages[[cp$chg_page]])
  stopifnot(all(as.character(2005:2030) %in% names(lvl)), all(as.character(2006:2030) %in% names(chg)))
  # the same single edition reproduces workbook 07 for 2006-2023 (revenue and printed change), so the added 2024 row belongs to that series
  w <- wb7[wb7$sector == cp$sector, ]; w <- w[order(w$year), ]
  stopifnot(identical(as.integer(w$year), 2006:2023), all.equal(w$revenue_million_report_price_basis, unname(lvl[as.character(2006:2023)])), all(w$report_vintage == cp$edition))
  growth <- 100 * (lvl[as.character(2006:2030)] / lvl[as.character(2005:2029)] - 1)
  stopifnot(max(abs(growth - chg[as.character(2006:2030)])) < 0.0501)       # the printed change is the rounded growth of the printed levels, every year
  yrs <- as.character(2022:2027)
  cyc[[cp$code]] <- data.frame(code = cp$code, year = as.integer(yrs), revenue_million = unname(lvl[yrs]), reported_change_pct = unname(chg[yrs]), revenue_page = as.integer(cp$rev_page),
                              change_page = as.integer(cp$chg_page), edition = cp$edition, evidence_sha256 = cp$pdf_sha, stringsAsFactors = FALSE)
  src <- w$source_url[1]
  cur[[cp$code]] <- data.frame(sector = cp$sector, year = 2024L, revenue_million_constant_2026_usd = NA_real_, growth_same_year_pct = unname(growth["2024"]), growth_previous_year_pct = unname(growth["2023"]),
                              ibisworld_code = cp$code, report_vintage = cp$edition, source_url = src, study_sector = "telecom", revenue_million_report_price_basis = unname(lvl["2024"]),
                              revenue_price_basis = w$revenue_price_basis[1], printed_revenue_change_pct = unname(chg["2024"]),
                              value_type = sprintf("current-year ESTIMATE in the %s edition (the publication year); not an actual", cp$edition), source_pdf = sprintf("%s_%s.pdf", sub("-", "_", cp$edition), cp$code),
                              source_pdf_sha256 = cp$pdf_sha, source_page = as.integer(cp$rev_page),
                              build_note = sprintf("Single vintage: %s 2024 revenue is IBISWorld's current-year estimate from the %s edition (the same edition as workbook 07's series); replaced automatically when a 2025+ edition is ingested.", cp$code, cp$edition),
                              status = "observed", source_id = sprintf("ibis_%s_%s_key_statistics_current_year", cp$code, sub("-", "_", cp$edition)), stringsAsFactors = FALSE)
}
cur <- do.call(rbind, cur); cyc <- do.call(rbind, cyc)
d1 <- file.path(rf_expand("{RFDATA}"), "ibis_telecom_2024_current_year.csv")
utils::write.csv(cur, d1, row.names = FALSE, na = "", fileEncoding = "UTF-8")
d2 <- file.path(.RF_HOME, "inputs", "cycle_model", "ibis_telecom_2024_p87_p89_p45_p46.csv")
utils::write.csv(cyc, d2, row.names = FALSE, quote = FALSE, fileEncoding = "UTF-8", eol = "\n")
cat(sprintf("wrote %s\n  sha256 %s\n  bytes %d\nwrote %s\n  sha256 %s\n  bytes %d\n", d1, rf_sha256(d1), file.size(d1), d2, rf_sha256(d2), file.size(d2)))
print(cur[, c("sector", "year", "revenue_million_report_price_basis", "printed_revenue_change_pct", "report_vintage")], row.names = FALSE)
print(cyc[, c("code", "year", "revenue_million", "reported_change_pct")], row.names = FALSE)

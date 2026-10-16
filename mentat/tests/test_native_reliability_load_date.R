script_argument <- grep("^--file=", commandArgs(trailingOnly = FALSE), value = TRUE)
script_path <- normalizePath(sub("^--file=", "", script_argument[[1L]]), winslash = "/", mustWork = TRUE)
project_root <- normalizePath(file.path(dirname(script_path), ".."), winslash = "/", mustWork = TRUE)
run_fixture <- function() {
  previous_directory <- getwd()
  previous_reports <- Sys.getenv("MENTAT_REPORT_DIRECTORY", unset = NA_character_)
  previous_inputs <- Sys.getenv("MENTAT_INPUT_DIR", unset = NA_character_)
  temporary <- tempfile("mentat-native-reliability-")
  dir.create(temporary)
  reports_directory <- file.path(temporary, "reports")
  dir.create(reports_directory)
  on.exit({
    setwd(previous_directory)
    if (is.na(previous_reports)) Sys.unsetenv("MENTAT_REPORT_DIRECTORY") else Sys.setenv(MENTAT_REPORT_DIRECTORY = previous_reports)
    if (is.na(previous_inputs)) Sys.unsetenv("MENTAT_INPUT_DIR") else Sys.setenv(MENTAT_INPUT_DIR = previous_inputs)
    unlink(temporary, recursive = TRUE, force = TRUE)
  }, add = TRUE)
  setwd(project_root)
  Sys.setenv(MENTAT_REPORT_DIRECTORY = reports_directory, MENTAT_INPUT_DIR = file.path(project_root, "inputs"))
  for (module in c("paths.R", "schema.R", "ingest.R", "collect.R", "reconcile.R", "report.R")) {
    source(file.path(project_root, "R", module), local = .GlobalEnv)
  }
  connection <- DBI::dbConnect(RSQLite::SQLite(), ":memory:")
  on.exit(DBI::dbDisconnect(connection), add = TRUE)
  init_schema(connection)
  DBI::dbAppendTable(connection, "companies", data.frame(industry = "Crypto", company = "Audit Synthetic",
    parent = "Audit Synthetic", variant = "AUDIT SYNTHETIC", variant_order = 1L, confidence = "normal",
    exclusions = NA_character_, metadata_json = "{}", source_sheet = "Audit", source_row = 1L))
  DBI::dbAppendTable(connection, "contributions", data.frame(
    sub_id = c("audit-one", "audit-two", "audit-zero"), industry = "Crypto", company = "Audit Synthetic",
    matched_variant = "AUDIT SYNTHETIC", phase = "P1_employer", scraped_at = "2026-10-14T00:00:00Z",
    contributor_name = c("SYNTHETIC ONE", "SYNTHETIC TWO", "SYNTHETIC THREE"),
    contributor_employer = "AUDIT SYNTHETIC", contribution_receipt_amount = c(12.50, -2.25, 0),
    contribution_receipt_date = c("2026-01-01", "2026-01-02", "2026-01-03"),
    two_year_transaction_period = 2026L, entity_type = "IND", committee_id = "C00000001",
    committee_name = "SYNTHETIC COMMITTEE", filing_form = "F3X", line_number = "11AI",
    load_date = c("2026-01-02T00:00:00Z", "2026-03-04T12:34:56Z", "")))
  before <- DBI::dbGetQuery(connection, "SELECT * FROM contributions ORDER BY sub_id")
  path <- write_reliability_report(connection, temporary, amendment_summary = data.frame())
  body <- readLines(path, warn = FALSE)
  unresolved_count <- function(lines) {
    section <- match("## Contribution rows and leg flags", lines)
    fields <- function(line) trimws(strsplit(line, "|", fixed = TRUE)[[1L]])
    headers <- fields(lines[[section + 2L]])
    values <- fields(lines[[section + 4L]])
    as.integer(values[[match("unresolved_receipt_rows", headers)]])
  }
  stopifnot(identical(normalizePath(dirname(path)), normalizePath(reports_directory)),
    any(body == "- Latest observed FEC row load_date: 2026-03-04T12:34:56Z"),
    any(body == "- Collection completeness requires coverage evidence."),
    !any(grepl("FEC data-through", body, fixed = TRUE)),
    identical(before, DBI::dbGetQuery(connection, "SELECT * FROM contributions ORDER BY sub_id")),
    DBI::dbGetQuery(connection, "SELECT COUNT(*) AS n FROM scrape_progress")$n == 0L,
    DBI::dbGetQuery(connection, "SELECT SUM(contribution_receipt_amount) AS n FROM contributions")$n == 10.25,
    unresolved_count(body) == 0L)
  cat("Native reliability actual body: observed maximum unchanged; signed amounts and source rows preserved: PASS\n")
  original_raw_rows <- spending_raw_report_rows
  on.exit(assign("spending_raw_report_rows", original_raw_rows, envir = .GlobalEnv), add = TRUE)
  assign("spending_raw_report_rows", function(...) {
    rows <- original_raw_rows(...)
    rows[, receipt_role := c("contribution", NA_character_, "unverified_receipt_role")]
    rows
  }, envir = .GlobalEnv)
  flagged_body <- reliability_report_lines(connection, amendment_summary = data.frame())
  stopifnot(unresolved_count(flagged_body) == 2L,
    identical(before, DBI::dbGetQuery(connection, "SELECT * FROM contributions ORDER BY sub_id")))
  assign("spending_raw_report_rows", function(...) {
    rows <- original_raw_rows(...)
    rows[, receipt_role := NULL]
    rows
  }, envir = .GlobalEnv)
  malformed <- tryCatch(reliability_report_lines(connection, amendment_summary = data.frame()), error = identity)
  stopifnot(inherits(malformed, "error"), identical(body, readLines(path, warn = FALSE)))
  assign("spending_raw_report_rows", original_raw_rows, envir = .GlobalEnv)
  cat("Native reliability valid/NA/unverified roles retain counts; nonempty missing role column refuses: PASS\n")
  DBI::dbExecute(connection, "UPDATE contributions SET load_date=NULL")
  missing_path <- write_reliability_report(connection, temporary, amendment_summary = data.frame())
  missing_body <- readLines(missing_path, warn = FALSE)
  stopifnot(any(missing_body == "- Latest observed FEC row load_date: not available"),
    any(missing_body == "- Collection completeness requires coverage evidence."))
  cat("Native reliability missing row load dates remain not available, not a coverage date: PASS\n")
  DBI::dbExecute(connection, "DELETE FROM contributions")
  empty_path <- write_reliability_report(connection, temporary, amendment_summary = data.frame())
  empty_body <- readLines(empty_path, warn = FALSE)
  stopifnot(any(empty_body == "- Latest observed FEC row load_date: not available"))
  cat("Native reliability empty source retains unknown observation and no completion claim: PASS\n")
}
run_fixture()

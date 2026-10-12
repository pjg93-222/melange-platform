# Apply amendment/duplicate versioning to an existing database.
#
# apply_amendment_versioning() normally runs as part of a collection pass. A
# database assembled some other way - the telecom merge, for instance, which
# unioned four interrupted shard walks - never had it applied, so every
# superseded_by_amendment, duplicate_filing_version and duplicate_memo flag is 0
# and the reports built from it can carry amendment and memo double counts that
# every other book has already had removed.
#
# Usage:
#   Rscript tools/apply_versioning.R <database> [industry]
#
# Writes to the database given. Point it at a COPY, never at a source snapshot.

args <- commandArgs(trailingOnly = TRUE)
if (!length(args)) {
  stop("Usage: Rscript tools/apply_versioning.R <database> [industry]", call. = FALSE)
}
database_path <- args[[1L]]
industry <- if (length(args) >= 2L) args[[2L]] else "Telecom"

script_dir <- dirname(
  sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[[1L]])
)
project_root <- dirname(script_dir)
for (module in c(
  "paths.R", "schema.R", "ingest.R", "identity_components.R",
  "fec_page.R", "fec_api.R", "collect.R", "reconcile.R", "report.R"
)) {
  source(file.path(project_root, "R", module))
}
assign("MENTAT_INDUSTRY", industry, envir = .GlobalEnv)

if (!file.exists(database_path)) {
  stop("Database not found: ", database_path, call. = FALSE)
}
require_package("DBI")
require_package("RSQLite")
connection <- DBI::dbConnect(RSQLite::SQLite(), dbname = database_path)
on.exit(DBI::dbDisconnect(connection), add = TRUE)
DBI::dbExecute(connection, "PRAGMA busy_timeout = 120000")
DBI::dbExecute(connection, "PRAGMA journal_mode = WAL")
init_schema(connection)

flag_totals <- function(label) {
  tables <- c("contributions", "corporate_giving")
  rows <- lapply(tables, function(table) {
    totals <- DBI::dbGetQuery(
      connection,
      paste(
        "SELECT SUM(superseded_by_amendment) AS superseded,",
        "SUM(duplicate_filing_version) AS duplicate_filing,",
        "SUM(duplicate_memo) AS duplicate_memo, COUNT(*) AS rows",
        "FROM", table
      )
    )
    totals$table <- table
    totals
  })
  by_table <- data.table::rbindlist(rows, fill = TRUE)
  for (index in seq_len(nrow(by_table))) {
    cat(sprintf(
      "%-8s table=%-16s rows=%s superseded=%s duplicate_filing=%s duplicate_memo=%s\n",
      label,
      by_table$table[[index]],
      format(by_table$rows[[index]], big.mark = ","),
      format(by_table$superseded[[index]] %||% 0, big.mark = ","),
      format(by_table$duplicate_filing[[index]] %||% 0, big.mark = ","),
      format(by_table$duplicate_memo[[index]] %||% 0, big.mark = ",")
    ))
  }
  list(
    by_table = by_table,
    rows = sum(by_table$rows %||% 0),
    superseded = sum(by_table$superseded %||% 0, na.rm = TRUE),
    duplicate_filing = sum(by_table$duplicate_filing %||% 0, na.rm = TRUE),
    duplicate_memo = sum(by_table$duplicate_memo %||% 0, na.rm = TRUE)
  )
}

before <- flag_totals("before")
apply_amendment_versioning(connection, print_summary = TRUE)
after <- flag_totals("after")

check <- DBI::dbGetQuery(connection, "PRAGMA quick_check(1)")[[1L]]
cat("quick_check:", check, "\n")
if (!identical(tolower(as.character(check)), "ok")) {
  stop("Integrity check failed after versioning: ", check, call. = FALSE)
}
# A pass that moves nothing on a database known never to have been versioned is
# itself the finding - report it rather than letting the build proceed silently.
moved <- (after$superseded %||% 0) + (after$duplicate_filing %||% 0) +
  (after$duplicate_memo %||% 0)
if (moved == 0) {
  cat("NOTE: versioning flagged nothing. Verify this is expected for this data.\n")
}

# Collect a SINGLE company for an industry, into an existing database.
#
# mentat.R only exposes --industry, which re-walks all ten companies. When one
# company is missing - Farmers Insurance published $0.00 in every Property &
# Casualty book because an FEC 429 was recorded as a genuine zero - re-collecting
# the whole industry costs hours of network for nine companies that are already
# complete. collect_crypto() has always taken a company_filter; this exposes it.
#
# Usage:
#   Rscript tools/collect_company.R <database> <industry> <company> [cycles]
# Example:
#   Rscript tools/collect_company.R C:/tmp/pc.db "Property & Casualty Insurance" "Farmers Insurance"
#
# Writes to the database given. Point it at a COPY, never at a source snapshot.

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 3L) {
  stop(
    "Usage: Rscript tools/collect_company.R <database> <industry> <company> [cycles]",
    call. = FALSE
  )
}
database_path <- args[[1L]]
industry <- args[[2L]]
company <- args[[3L]]
cycles <- if (length(args) >= 4L) {
  as.integer(strsplit(args[[4L]], ",", fixed = TRUE)[[1L]])
} else {
  c(2022L, 2024L, 2026L)
}

script_dir <- dirname(
  sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[[1L]])
)
project_root <- dirname(script_dir)
for (module in c(
  "paths.R", "schema.R", "ingest.R", "identity_components.R",
  "fec_page.R", "fec_api.R", "collect.R"
)) {
  source(file.path(project_root, "R", module))
}

assign("MENTAT_INDUSTRY", industry, envir = .GlobalEnv)

if (!file.exists(database_path)) {
  stop("Database not found: ", database_path, call. = FALSE)
}
# Connect to the file directly rather than through db_connect(), which takes a
# DATA ROOT and would try to create a directory at this path. Same PRAGMAs.
require_package("DBI")
require_package("RSQLite")
connection <- DBI::dbConnect(RSQLite::SQLite(), dbname = database_path)
on.exit(DBI::dbDisconnect(connection), add = TRUE)
DBI::dbExecute(connection, "PRAGMA busy_timeout = 60000")
journal_mode <- DBI::dbGetQuery(connection, "PRAGMA journal_mode = WAL")[[1L]]
if (!identical(tolower(as.character(journal_mode)), "wal")) {
  stop("SQLite refused WAL mode for: ", database_path, call. = FALSE)
}
DBI::dbExecute(connection, "PRAGMA synchronous = NORMAL")
DBI::dbExecute(connection, "PRAGMA foreign_keys = ON")
# Brings an older database up to the current schema, which is what adds
# zero_verified so this run's checkpoints can record that their zeros were
# actually confirmed rather than inherited from a rate-limited walk.
init_schema(connection)

before <- collection_company_store_counts(connection, company)
cat(sprintf(
  paste0(
    "collect_company: %s / %s / cycles %s | rows before = %d ",
    "(contributions=%d corporate=%d)\n"
  ),
  industry, company, paste(cycles, collapse = ","), before[["total"]],
  before[["contributions"]], before[["corporate_giving"]]
))

collect_crypto(
  connection,
  cycles = cycles,
  company_filter = company
)

after <- collection_company_store_counts(connection, company)
gained <- after - before
progress <- DBI::dbGetQuery(
  connection,
  paste(
    "SELECT cycle, COUNT(*) AS variant_sets, SUM(records_total) AS records,",
    "SUM(zero_verified) AS verified",
    "FROM scrape_progress WHERE company = ? GROUP BY cycle ORDER BY cycle"
  ),
  params = list(company)
)
cat(sprintf(
  paste0(
    "collect_company: rows after = %d (added %d; ",
    "contributions=%+d corporate=%+d)\n"
  ),
  after[["total"]], gained[["total"]], gained[["contributions"]],
  gained[["corporate_giving"]]
))
print(progress)

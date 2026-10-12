script <- sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[[1L]])
root <- normalizePath(file.path(dirname(script), ".."), winslash = "/")
for (file in c("paths.R", "schema.R", "ingest.R", "collect.R", "fec_api.R")) source(file.path(root, "R", file))
suppressPackageStartupMessages(library(data.table))
connection <- DBI::dbConnect(RSQLite::SQLite(), ":memory:")
init_schema(connection)
count <- function(table) DBI::dbGetQuery(connection, paste("SELECT COUNT(*) AS n FROM", table))$n[[1L]]

unit <- list(id = "employer:Acme:2024", industry = "Crypto", company = "Acme", cycle = 2024L,
  kind = "employer", variants = "Acme", company_variants = "Acme", exclusions = character(),
  query_field = "contributor_employer", query_filters = list())
spec <- fec_api_request_spec(unit)
raw_row <- list(sub_id = "400000000000000001", contributor_id = "C00000123", contributor_name = "ACME",
  contributor_employer = "ACME", committee_id = "C00000001", entity_type = "ORG",
  contribution_receipt_amount = 123.45, contribution_receipt_date = "2024-01-02",
  filing_form = "F3X", line_number = "11C", two_year_transaction_period = 2024L,
  transaction_id = "ORIGINAL1", file_number = 12345L, arbitrary_future_field = "keep this evidence")
raw_body <- mentat_evidence_json(list(results = list(raw_row), pagination = list(count = 1L,
  is_count_exact = TRUE, last_indexes = NULL), original_envelope_field = "also preserve"))
response <- list(status = 200L, body = raw_body, headers = list(`X-Api-Key` = "do-not-archive"))
transport <- fec_api_archiving_transport(connection, function(specs, api_key, concurrency) list(response))
invisible(transport(list(spec), "do-not-archive", 1L))
invisible(transport(list(spec), "do-not-archive", 1L))
stopifnot(count("fec_response_archive") == 1L, count("fec_response_observations") == 1L)
archive <- DBI::dbReadTable(connection, "fec_response_archive")
stopifnot(identical(archive$payload_json[[1]], raw_body),
  archive$archive_sha256[[1]] == digest::digest(raw_body, algo = "sha256", serialize = FALSE))
page <- fec_api_parse_page(response); page$page_number <- 0L; page$complete <- TRUE; page$source_request <- spec
state <- list(unit = unit, last_indexes = list(), rows_seen = 1L, expected_count = 1L,
  seen_keysets = character(), complete = TRUE)
fec_api_checkpoint_page(connection, state, page)
fec_api_mark_work_unit_stored(connection, unit$id)
stopifnot(count("api_page_checkpoints") == 0L, count("fec_response_archive") == 1L)
observations <- DBI::dbReadTable(connection, "fec_response_observations")
stopifnot(!any(grepl("do-not-archive|X-Api-Key", observations$query_json)),
  any(grepl("Acme", observations$query_json)))

# Malformed success bodies are archived before parsing fails.
bad_transport <- fec_api_archiving_transport(connection, function(...) list(list(status = 200L, body = "not json")))
bad <- bad_transport(list(spec), "secret", 1L)
stopifnot(inherits(try(fec_api_parse_page(bad[[1]]), silent = TRUE), "try-error"),
  any(DBI::dbReadTable(connection, "fec_response_archive")$payload_json == "not json"))

records <- flatten_fec_rows(list(raw_row), "Crypto", "Acme", "Acme", "P1_employer", character(),
  match_overrides = data.frame(), home_states = data.frame())
stopifnot(records$contributor_id[[1]] == "C00000123")
corporate <- fec_api_corporate_records(connection, unit, list(raw_row),
  match_overrides = data.frame(), home_states = data.frame())
for (table in c("contributions", "corporate_giving")) {
  rows <- if (table == "contributions") copy(records) else copy(corporate)
  insert_rows_or_ignore(connection, table, rows, names(rows))
  insert_rows_or_ignore(connection, table, rows, names(rows))
  stopifnot(count(table) == 1L)
  # Same ID, a competing company and different amount: preserve both claims
  # and the conflict; the accepted financial row and attribution do not change.
  disputed <- copy(rows)
  disputed[, `:=`(company = "Acme Parent", contribution_receipt_amount = 999.99)]
  insert_rows_or_ignore(connection, table, disputed, names(disputed))
  insert_rows_or_ignore(connection, table, disputed, names(disputed))
  stored <- DBI::dbReadTable(connection, table)
  stopifnot(stored$company[[1]] == "Acme", stored$contribution_receipt_amount[[1]] == 123.45,
    stored$contributor_id[[1]] == "C00000123")
  claims <- DBI::dbGetQuery(connection, "SELECT * FROM attribution_claims WHERE source_table=?", params = list(table))
  conflicts <- DBI::dbGetQuery(connection, "SELECT * FROM source_record_conflicts WHERE source_table=?", params = list(table))
  stopifnot(nrow(claims) == 2L, setequal(claims$company, c("Acme", "Acme Parent")),
    nrow(conflicts) == 1L, conflicts$field_name[[1]] == "contribution_receipt_amount",
    as.numeric(conflicts$before_json[[1]]) == 123.45, as.numeric(conflicts$after_json[[1]]) == 999.99,
    conflicts$resolution_status[[1]] == "unresolved")
}
stopifnot(count("attribution_claims") == 4L, count("source_record_conflicts") == 2L)

# Conflicting repeated IDs within one input batch retain both observations.
batch <- rbind(copy(records), copy(records))
batch[, sub_id := "batch-conflict"]
batch[2L, contribution_receipt_amount := 678.90]
insert_rows_or_ignore(connection, "contributions", batch, names(batch))
stopifnot(DBI::dbGetQuery(connection,
  "SELECT COUNT(*) AS n FROM source_record_conflicts WHERE sub_id='batch-conflict'")$n[[1L]] == 1L)

# A populated legacy schema gains the optional identifier without changing
# existing money or rebuilding the receipt tables.
before <- DBI::dbGetQuery(connection, "SELECT sub_id,company,contribution_receipt_amount FROM contributions")
DBI::dbExecute(connection, "ALTER TABLE contributions DROP COLUMN contributor_id")
DBI::dbExecute(connection, "ALTER TABLE corporate_giving DROP COLUMN contributor_id")
init_schema(connection)
after <- DBI::dbGetQuery(connection, "SELECT sub_id,company,contribution_receipt_amount FROM contributions")
stopifnot(identical(before, after), "contributor_id" %in% DBI::dbListFields(connection, "contributions"))
DBI::dbDisconnect(connection)
cat("test_evidence_archive: PASS\n")

# Inert HTTP-shaped bodies only. No network, browser or production DB is used.
args <- commandArgs(trailingOnly=TRUE)
project <- args[[1L]]; directory <- args[[2L]]
assign("MENTAT_PROJECT_ROOT",project,envir=.GlobalEnv)
for (module in c("paths.R","schema.R","ingest.R","collect.R","fec_api.R")) source(file.path(project,"R",module))
Sys.unsetenv(c("FEC_API_KEY","MENTAT_API_BUDGET_ID","MENTAT_REQUEST_POOL_URL","MENTAT_API_BUDGET_IMPORTS"))
connection <- DBI::dbConnect(RSQLite::SQLite(),file.path(directory,"shard.sqlite"))
init_schema(connection)
target <- DBI::dbConnect(RSQLite::SQLite(),file.path(directory,"target.sqlite"))
init_schema(target); DBI::dbDisconnect(target)
budget <- mentat_api_budget_connect(file.path(directory,"budget.sqlite"))
fec_api_budget_schema(budget)
key <- "fixture-producer-only"
DBI::dbExecute(budget,"INSERT INTO api_request_budget VALUES (?,?,?)",
  params=list(fec_api_legacy_budget_id(key),90,1L))
first <- paste0('{"results":[{"sub_id":400000000000000001,"contributor_name":"SYNTHETIC FIXTURE",',
  '"contributor_employer":"Fixture","committee_id":"C00000001","contribution_receipt_amount":123.45,',
  '"contribution_receipt_date":"2026-01-01","entity_type":"IND","two_year_transaction_period":2026,',
  '"filing_form":"F3X","line_number":"11AI","arbitrary_source_field":"preserved"}],',
  '"pagination":{"count":2,"is_count_exact":true,"last_indexes":{"last_index":400000000000000001,',
  '"last_contribution_receipt_date":"2026-01-01"}}}')
second <- paste0('{"results":[{"sub_id":400000000000000003,"contributor_name":"SYNTHETIC FIXTURE",',
  '"contributor_employer":"Fixture","committee_id":"C00000001","contribution_receipt_amount":-20.02,',
  '"contribution_receipt_date":"2026-01-01","entity_type":"IND","two_year_transaction_period":2026,',
  '"filing_form":"F3X","line_number":"11AI"}],',
  '"pagination":{"count":2,"is_count_exact":true,"last_indexes":null}}')
bodies <- c(first,second); calls <- 0L
transport <- function(specs,api_key,concurrency) {
  calls <<- calls+1L
  if (calls==2L) stopifnot(identical(specs[[1L]]$params$last_index,"400000000000000001"))
  list(list(status=200L,body=bodies[[calls]],headers=list(`X-Api-Key`="never-archive-this-fixture-token")))
}
unit <- list(id="fixture-public-producer",kind="employer",industry="Crypto",company="Fixture",
  cycle=2026L,variants="Fixture",company_variants="Fixture",exclusions=character(),
  phase="P1_employer",query_field="contributor_employer",query_filters=list())
result <- fec_api_collect_checkpointed(connection,list(unit),key,1L,transport,function(...)NULL,
  function(...)0,function(count)fec_api_budget_reserve(budget,count,cap=3L,now=100+calls,
    budget_id=fec_api_budget_id(key),legacy_ids=fec_api_budget_aliases(key)),
  lookup_cache=list(match_overrides=data.frame(),home_states=data.frame(),trusted_by_company=list(Fixture=character())))
rows <- DBI::dbGetQuery(connection,"SELECT sub_id,typeof(sub_id) AS id_type,contribution_receipt_amount FROM contributions ORDER BY sub_id")
archive <- DBI::dbReadTable(connection,"fec_response_archive")
queries <- DBI::dbGetQuery(connection,"SELECT query_json FROM fec_response_observations")
stopifnot(calls==2L,identical(rows$sub_id,c("400000000000000001","400000000000000003")),
  all(rows$id_type=="text"),identical(rows$contribution_receipt_amount,c(123.45,-20.02)),
  nrow(archive)==2L,setequal(archive$payload_json,bodies),
  all(archive$archive_sha256==vapply(archive$payload_json,mentat_evidence_sha256,character(1L))),
  !any(grepl("never-archive-this-fixture-token|X-Api-Key",queries$query_json)),
  DBI::dbGetQuery(connection,"SELECT COUNT(*) AS n FROM api_page_checkpoints")$n[[1L]]==0L,
  DBI::dbGetQuery(connection,"SELECT stored_complete FROM api_work_progress")$stored_complete[[1L]]==1L,
  fec_api_budget_status(budget,cap=3L,now=103,budget_id=fec_api_budget_id(key))$requests_used==3L)
DBI::dbDisconnect(budget);DBI::dbDisconnect(connection)
cat("test_api_budget_producer: PASS (synthetic HTTP body -> quota -> archive -> stored rows)\n")

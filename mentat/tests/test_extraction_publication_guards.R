args <- commandArgs(trailingOnly=TRUE)
project <- if (length(args)) args[[1L]] else {
  script <- sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value=TRUE)[[1L]])
  normalizePath(file.path(dirname(script), ".."), winslash="/", mustWork=TRUE)
}
candidate <- if (length(args)>=2L) args[[2L]] else file.path(project,"R")
assign("MENTAT_PROJECT_ROOT",project,envir=.GlobalEnv)
for (module in c("paths.R","schema.R","ingest.R","fec_page.R","collect.R","fec_api.R")) {
  source(file.path(if (module %in% c("collect.R","fec_api.R")) candidate else file.path(project,"R"), module))
}
Sys.unsetenv(c("MENTAT_API_BUDGET_ID","MENTAT_REQUEST_POOL_URL"))
connection <- DBI::dbConnect(RSQLite::SQLite(),":memory:")
init_schema(connection)
count <- function(table) DBI::dbGetQuery(connection,paste("SELECT COUNT(*) AS n FROM",table))$n[[1L]]
DBI::dbExecute(connection,paste(
  "INSERT INTO scrape_progress VALUES",
  "('Fixture',2026,'unverified-zero',0,0,'2026-10-14T01:00:00+0000',0),",
  "('Fixture',2026,'verified-zero',0,0,'2026-10-14T01:00:00+0000',1),",
  "('Fixture',2026,'legacy-positive',0,1,'2026-10-14T01:00:00+0000',0)"))
resume <- function(key) completed_scrape_checkpoint(connection,"Fixture",2026L,key,
  "2026-10-14T00:00:00+0000")
stopifnot(is.null(resume("unverified-zero")),isTRUE(resume("verified-zero")$skipped),
  isTRUE(resume("legacy-positive")$skipped))
cat("D060 zero-resume: PASS\n")

# Direct legacy helper callers can predate the verification-column migration.
legacy_connection <- DBI::dbConnect(RSQLite::SQLite(), ":memory:")
DBI::dbExecute(legacy_connection, paste(
  "CREATE TABLE scrape_progress (company TEXT, cycle INTEGER, variant_set TEXT,",
  "last_page INTEGER, records_total INTEGER, updated_at TEXT)"))
DBI::dbExecute(legacy_connection, paste(
  "INSERT INTO scrape_progress VALUES",
  "('Fixture',2026,'unstamped-zero',0,0,'2026-10-14T01:00:00+0000'),",
  "('Fixture',2026,'unstamped-positive',0,1,'2026-10-14T01:00:00+0000')"))
legacy_resume <- function(key) completed_scrape_checkpoint(legacy_connection,
  "Fixture",2026L,key,"2026-10-14T00:00:00+0000")
stopifnot(is.null(legacy_resume("unstamped-zero")),
  isTRUE(legacy_resume("unstamped-positive")$skipped),
  !"zero_verified" %in% DBI::dbListFields(legacy_connection,"scrape_progress"))
DBI::dbDisconnect(legacy_connection)
cat("D060 absent-column legacy compatibility: PASS\n")

fetches <- 0L
transport <- function(...) {
  fetches <<- fetches+1L
  list(list(status=200L,body='{"results":[],"pagination":{"count":0,"is_count_exact":true}}'))
}
DBI::dbExecute(connection,"DROP TABLE fec_response_observations")
spec <- list(id="fixture",url=FEC_API_ENDPOINT,params=list())
failure <- tryCatch(fec_api_fetch_with_retry(list(spec),api_key="synthetic",concurrency=1L,
  transport=fec_api_archiving_transport(connection,transport),max_attempts=2L,
  sleep=function(...)NULL,jitter=function(...)0),error=function(condition)condition)
stopifnot(inherits(failure,"mentat_evidence_archive_failed"),fetches==1L,
  !grepl("connection_error|no such table|synthetic",conditionMessage(failure)),
  count("contributions")==0L,count("fec_response_archive")==0L)
cat("D060 archive-storage refusal: PASS\n")
DBI::dbDisconnect(connection)
connection <- DBI::dbConnect(RSQLite::SQLite(),":memory:")
init_schema(connection)

unit <- list(id="fixture-narrow",kind="employer",industry="Crypto",company="Fixture",
  cycle=2026L,variants="Fixture",company_variants=c("Fixture","Fixture Alt"),
  decomposition_variants="Fixture Alt",decomposition_source="observed_employers",
  phase="P1_employer",query_field="contributor_employer",query_filters=list(contributor_city="City"))
narrow <- function(specs,...) lapply(specs,function(spec) {
  if (identical(spec$params$contributor_employer,"Fixture")) return(list(status=504L,body="",elapsed_seconds=0.1))
  list(status=200L,json=list(results=list(list(sub_id="synthetic-row",contributor_name="SYNTHETIC",
    contributor_employer="Fixture Alt",committee_id="C00000001",contribution_receipt_amount=1,
    contribution_receipt_date="2026-01-01",entity_type="IND",two_year_transaction_period=2026L)),
    pagination=list(count=1L,is_count_exact=TRUE,last_indexes=NULL)))
})
collected <- fec_api_collect_work_units(list(unit),api_key="synthetic",concurrency=1L,
  transport=fec_api_archiving_transport(connection,narrow),sleep=function(...)NULL,jitter=function(...)0,
  page_callback=function(state,page)fec_api_checkpoint_page(connection,state,page))
cache <- list(match_overrides=data.frame(),home_states=data.frame(),
  trusted_by_company=list(Fixture=character()))
archive_count <- count("fec_response_archive")
checkpoint_count <- count("api_page_checkpoints")
stopifnot(archive_count>0L,checkpoint_count>0L,identical(collected$units[[unit$id]]$coverage_verified,FALSE))
for (stored in c(FALSE,TRUE)) {
  collected$units[[unit$id]]$stored_complete <- stored
  failure <- tryCatch(fec_api_store_results(connection,collected,lookup_cache=cache),error=conditionMessage)
  stopifnot(is.character(failure),grepl("Cannot certify an unverified",failure,fixed=TRUE),
    count("contributions")==0L,count("fec_response_archive")==archive_count,
    count("api_page_checkpoints")==checkpoint_count,
    DBI::dbGetQuery(connection,"SELECT stored_complete FROM api_work_progress WHERE unit_id='fixture-narrow'")$stored_complete[[1L]]==0L)
}
cat("D060 unverified-coverage refusal and evidence preservation: PASS\n")

# Missing flag remains a supported legacy state; explicit TRUE works too.
collected$units[[unit$id]]$stored_complete <- FALSE
collected$units[[unit$id]]$coverage_verified <- NULL
fec_api_store_results(connection,collected,lookup_cache=cache)
stopifnot(count("contributions")==1L)
collected$units[[unit$id]]$coverage_verified <- TRUE
fec_api_store_results(connection,collected,lookup_cache=cache)
stopifnot(count("contributions")==1L)
DBI::dbDisconnect(connection)
cat("D060 legacy-missing and verified-true compatibility: PASS\n")
cat("test_D060_extraction: PASS\n")

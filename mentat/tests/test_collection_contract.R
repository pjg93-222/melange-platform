script <- sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value=TRUE)[[1]])
root <- normalizePath(file.path(dirname(script), ".."), winslash="/")
for (f in c("paths.R","schema.R","ingest.R","collect.R","fec_api.R")) source(file.path(root,"R",f))
stopifnot(identical(company_contributor_queries("Acme.Pay",c("ACME.PAY","Acme Pay")), c("Acme.Pay","Acme Pay")))
parts <- partition_company_variants(c("Acme.Pay","Acme Pay","ACME.PAY","Acme"))
stopifnot(setequal(c(parts$phase1,parts$phase2),c("Acme","Acme.Pay","Acme Pay")))
unit <- list(id="test",company="Acme",cycle=2024L,kind="employer",variants="Acme.Pay",
             company_variants=c("Acme.Pay","Acme Pay"),query_field="contributor_employer",query_filters=list())
stopifnot(identical(fec_api_request_spec(unit)$params$contributor_employer,"Acme.Pay"))
unsupported <- unit;unsupported$query_filters <- list(file_number=123)
stopifnot(inherits(try(fec_api_request_spec(unsupported),silent=TRUE),"try-error"))
for (exact in list(FALSE,NULL)) {
  saved <- list()
  failure <- tryCatch(fec_api_collect_work_units(list(unit),api_key="fixture",concurrency=1L,
    transport=function(...) list(list(status=200L,json=list(results=list(list(sub_id="1")),pagination=list(count=1L,is_count_exact=exact)))),
    sleep=function(...) NULL,jitter=function(...) 0,
    page_callback=function(state,page) {saved[[length(saved)+1L]] <<- page}),error=conditionMessage)
  stopifnot(grepl("exact nonnegative",failure),length(saved)==1L,!isTRUE(saved[[1]]$complete))
}
c <- DBI::dbConnect(RSQLite::SQLite(),":memory:");init_schema(c)
state <- list(unit=unit,last_indexes=list(),rows_seen=1L,expected_count=1L,seen_keysets=character(),complete=TRUE)
page <- list(page_number=0L,rows=list(list(sub_id="1")),count=1L,is_count_exact=TRUE,complete=TRUE)
fec_api_checkpoint_page(c,state,page);fec_api_mark_work_unit_stored(c,unit$id)
stopifnot(isTRUE(fec_api_load_progress(c,list(unit))[[unit$id]]$stored_complete))
# Paul's ruling: unresumable saved work is a per-unit fact, not a lane-fatal
# one. The progress row and its page checkpoints are dropped so the unit
# re-collects; the evidence archive keeps the original responses.
saved_units <- function() DBI::dbGetQuery(c,"SELECT COUNT(*) AS n FROM api_work_progress")$n[[1]]
changed <- unit; changed$variants <- "Acme Pay"
stopifnot(length(fec_api_load_progress(c,list(changed)))==0L, saved_units()==0L)
fec_api_checkpoint_page(c,state,page);fec_api_mark_work_unit_stored(c,unit$id)
DBI::dbExecute(c,"UPDATE api_work_progress SET query_contract=NULL")
stopifnot(length(fec_api_load_progress(c,list(unit)))==0L, saved_units()==0L)
stopifnot(identical(fec_funding_source(c("IND","ORG","PAC","PTY","",NA)),
 c("Individual","Organization (treasury attribution unverified)","PAC / committee","PAC / committee","Unverified funding source","Unverified funding source")))
DBI::dbDisconnect(c)
window_calls <- character()
window_result <- fec_api_collect_work_units(list(unit),api_key="fixture",concurrency=4L,
 transport=function(specs,...) lapply(specs,function(spec) {
  stopifnot(identical(spec$params$contributor_employer,"Acme.Pay"))
  window_calls <<- c(window_calls,spec$id)
  if (!grepl("::window::",spec$id,fixed=TRUE)) return(list(status=504L,elapsed_seconds=55,headers=list(),body=""))
  list(status=200L,json=list(results=list(list(sub_id=spec$id)),pagination=list(count=1L,is_count_exact=TRUE,last_indexes=NULL)))
 }),sleep=function(...) NULL,jitter=function(...) 0)
stopifnot(isTRUE(window_result$units[[unit$id]]$coverage_verified),
          window_result$units[[unit$id]]$rows_seen==26L,
          length(window_calls)==30L,
          identical(window_result$units[[unit$id]]$pages[[1]]$coverage_method,"exhaustive_date_windows"))
cat("test_collection_contract: PASS\n")

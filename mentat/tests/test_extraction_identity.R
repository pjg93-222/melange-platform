# Offline raw-JSON extraction replay: IDs, pagination, checkpoint, storage and staging.
script <- sub('^--file=', '', grep('^--file=', commandArgs(FALSE), value=TRUE)[[1L]])
root <- normalizePath(file.path(dirname(script), '..'), winslash='/', mustWork=TRUE)
for (name in c('paths','schema','ingest','collect','fec_api','fec_page','report','fec_beneficiary','tidy_stage'))
  source(file.path(root,'R',paste0(name,'.R')))
data.table::setDTthreads(2L)
ids <- c('4020420242001634771','4020420242001634772')
unit <- list(id='precision-fixture',kind='employer',industry='Fixture',company='Acme',
  cycle=2024L,variants='Acme',company_variants='Acme',phase='P1_employer',
  query_field='contributor_employer',query_filters=list(),exclusions=character())
body <- function(i) paste0('{"results":[{"sub_id":',ids[[i]],
  ',"original_sub_id":4020420242001634769,"image_number":202402019999999991,',
  '"memo_text":"Reference 4020420242001634771, quoted \\"1234567890123456789\\"",',
  '"transaction_id":"TX-',i,'","contributor_name":"FIXTURE, PERSON ',i,
  '","contributor_employer":"ACME","contributor_occupation":"ENGINEER",',
  '"entity_type":"IND","committee_id":"C00835959","committee_name":"FAIRSHAKE",',
  '"filing_form":"F3X","line_number":"11AI","contribution_receipt_date":"2024-01-01",',
  '"contribution_receipt_amount":',c('10.25','20.75')[[i]],',"two_year_transaction_period":2024}],',
  '"pagination":{"count":2,"is_count_exact":true,"last_indexes":',
  if(i==1L) paste0('{"last_index":',ids[[i]],',"last_contribution_receipt_date":"2024-01-01"}') else 'null', '}}')

parsed <- fec_api_parse_page(list(status=200L,body=body(1L)))
stopifnot(identical(parsed$rows[[1L]]$sub_id,ids[[1L]]),
  identical(parsed$rows[[1L]]$original_sub_id,'4020420242001634769'),
  identical(parsed$rows[[1L]]$image_number,'202402019999999991'),
  identical(parsed$last_indexes$last_index,ids[[1L]]),
  identical(parsed$rows[[1L]]$contribution_receipt_amount,10.25))
# Execute the browser fallback's generated JavaScript with a fake fetch. This
# tests its JSON boundary and cursor handoff without opening a browser/network.
original_runtime <- fec_runtime_value
fec_runtime_value <- function(session,expression,...) {
  program <- tempfile(fileext='.cjs')
  on.exit(unlink(program))
  writeLines(c('const assert=require("node:assert/strict");',
    paste0('const bodies=',jsonlite::toJSON(list(body(1L),body(2L)),auto_unbox=TRUE),'; let calls=0;'),
    paste0('global.fetch=async url=>{if(calls===1)assert.equal(new URL(url).searchParams.get("last_index"),"',ids[[1L]],'");return {ok:true,status:200,text:async()=>bodies[calls++]};};'),
    paste0('Promise.resolve(eval(',jsonlite::toJSON(expression,auto_unbox=TRUE),
      ')).then(x=>{assert.equal(calls,2);console.log(x)}).catch(e=>{console.error(e);process.exit(1)});')),
    program,useBytes=TRUE)
  output <- system2('node',shQuote(program),stdout=TRUE,stderr=TRUE)
  if(!is.null(attr(output,'status'))) stop(paste(output,collapse='\n'))
  paste(output,collapse='\n')
}
browser_pages <- fec_browser_api_batch(NULL,'https://api.open.fec.gov/v1/schedules/schedule_a/',1:2)
fec_runtime_value <- original_runtime
stopifnot(identical(vapply(browser_pages$responses,function(p) p$body$results[[1L]]$sub_id,character(1L)),ids),
  identical(browser_pages$responses[[1L]]$body$results[[1L]]$memo_text,
    'Reference 4020420242001634771, quoted "1234567890123456789"'),
  browser_pages$responses[[1L]]$body$results[[1L]]$contribution_receipt_amount==10.25)
args <- commandArgs(TRUE)
directory <- if(length(args)) args[[1L]] else tempfile('extraction-identity-')
dir.create(directory,recursive=TRUE,showWarnings=FALSE)
database <- file.path(directory,'extraction.sqlite')
connection <- DBI::dbConnect(RSQLite::SQLite(),database)
init_schema(connection)
DBI::dbExecute(connection,paste(
  "INSERT INTO companies (industry,company,parent,variant,variant_order,confidence,source_sheet,source_row)",
  "VALUES ('Fixture','Acme','Acme','Acme',1,'high','Fixture',1)"))
lookups <- list(match_overrides=company_match_overrides_data(connection),
  home_states=company_home_states_data(connection),trusted_by_company=list(Acme=character()))
requests <- 0L
transport <- function(specs,...) lapply(specs,function(spec) {
  requests <<- requests+1L
  if(requests==2L) stopifnot(identical(spec$params$last_index,ids[[1L]]))
  stopifnot(requests<=2L)
  list(status=200L,body=body(requests),headers=list())
})
paused <- tryCatch(fec_api_collect_checkpointed(connection,list(unit),'fixture',1L,transport,
  sleep=function(...) stop('Unexpected retry'),jitter=function(...) 0,
  before_request=function(...) {if(requests==1L) stop('Fixture interruption')},
  lookup_cache=lookups),error=conditionMessage)
stopifnot(identical(paused,'Fixture interruption'),requests==1L,
  DBI::dbGetQuery(connection,'SELECT COUNT(*) AS n FROM contributions')$n==0L)
result <- fec_api_collect_checkpointed(connection,list(unit),'fixture',1L,transport,
  sleep=function(...) stop('Unexpected retry'),jitter=function(...) 0,before_request=function(...) NULL,
  lookup_cache=lookups)
stored <- DBI::dbGetQuery(connection,'SELECT * FROM contributions ORDER BY sub_id')
stopifnot(requests==2L,nrow(stored)==2L,identical(stored$sub_id,ids),
  sum(stored$contribution_receipt_amount)==31)
resumed <- fec_api_collect_checkpointed(connection,list(unit),'fixture',1L,
  transport=function(...) stop('Completed replay requested another page'),
  sleep=function(...) NULL,jitter=function(...) 0,before_request=function(...) NULL,lookup_cache=lookups)
stopifnot(DBI::dbGetQuery(connection,'SELECT COUNT(*) AS n FROM contributions')$n==2L)
archive <- DBI::dbGetQuery(connection,'SELECT archive_sha256,payload_json FROM fec_response_archive')
stopifnot(all(vapply(seq_len(nrow(archive)),function(i)
  identical(mentat_evidence_sha256(archive$payload_json[[i]]),archive$archive_sha256[[i]]),logical(1L))))
raw <- data.table::as.data.table(stored)[,.(source_id=sub_id,company,cycle=2024L,
  cycle_label='2023-2024',amount=contribution_receipt_amount,include_in_analysis='Yes',
  committee_id,transaction_id,contributor=contributor_name,date=contribution_receipt_date)]
tidy <- mentat_tidy_contributions(raw)
stopifnot(identical(tidy$source_id,ids),sum(tidy$analysis_amount)==31)
empty <- data.table::data.table()
frames <- do.call(mentat_build_tidy_frames,c(list(raw),rep(list(empty),9L)))
for(name in c('giving_facts','recipient_facts','location_facts'))
  stopifnot(sum(frames[[name]]$giving)==31)
mentat_write_tidy_stage(file.path(directory,'staging.sqlite'),frames,
  list(industry='Fixture',cycles=2024L,expected_cycle_totals=list('2023-2024'=31)))
jsonlite::write_json(list(source_ids=ids,source_sha256=archive$archive_sha256,
  requests=requests,stored_rows=nrow(stored),raw_amount=sum(raw$amount),
  analysis_amount=sum(tidy$analysis_amount),staged_rows=tidy),
  file.path(directory,'replay.json'),auto_unbox=TRUE,pretty=TRUE,digits=NA,na='null')
DBI::dbDisconnect(connection)
if(!length(args)) unlink(directory,recursive=TRUE)
cat('Extraction identity: exact IDs, cursor, source hashes, resume, dollars and tidy projection PASS\n')

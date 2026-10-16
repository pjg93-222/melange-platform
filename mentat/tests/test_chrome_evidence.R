# Offline evidence retention replay. Every database/download/browser response is synthetic.
args <- commandArgs(TRUE)
root <- normalizePath(args[[1L]], winslash="/", mustWork=TRUE)
for (module in c("paths", "schema", "ingest", "fec_page", "collect", "fec_api"))
  source(file.path(root,"R",paste0(module,".R")))
data.table::setDTthreads(2L)
connection <- DBI::dbConnect(RSQLite::SQLite(), ":memory:")
init_schema(connection)
session <- new.env(parent=emptyenv())
callback <- fec_chrome_archive_callback(connection,"Fixture",2026L,"fixture-unit","Fixture",
  "contributor_employer",list(contributor_city="CITY",api_key="DO_NOT_ARCHIVE",signature="DO_NOT_ARCHIVE"))
attr(session,"mentat_evidence_callback") <- callback
archives <- function() DBI::dbGetQuery(connection,"SELECT * FROM fec_response_archive ORDER BY rowid")
observations <- function() DBI::dbGetQuery(connection,"SELECT * FROM fec_response_observations ORDER BY rowid")
decode <- function(index) jsonlite::fromJSON(archives()$payload_json[[index]],simplifyVector=FALSE)
bytes <- c(as.raw(c(0xef,0xbb,0xbf)),charToRaw("sub_id,contribution_receipt_amount\r\n4020420242001634771,-12.34\r\n4020420242001634772,0\r\n"))
csv <- tempfile(fileext=".csv")
writeBin(bytes,csv)
rows <- fec_parse_export_file(session,csv,2L)
envelope <- decode(1L)
stopifnot(identical(jsonlite::base64_dec(envelope$bytes_base64),bytes),
  identical(envelope$bytes_sha256,digest::digest(bytes,algo="sha256",serialize=FALSE)),
  identical(envelope$source_kind,"fec_download_csv_bytes"),isTRUE(envelope$original_response_bytes),
  identical(rows$sub_id,c("4020420242001634771","4020420242001634772")),
  identical(rows$contribution_receipt_amount,c("-12.34","0")),
  !grepl("DO_NOT_ARCHIVE|api_key|signature",paste(observations()$query_json,collapse="")))
cat("Chrome CSV lossless bytes/SHA, text IDs, signed and zero: PASS\n")
malformed <- charToRaw("sub_id,contribution_receipt_amount\r\ntext-id,0\r\n")
writeBin(malformed,csv)
failure <- tryCatch(fec_parse_export_file(session,csv,2L),error=function(e)e)
stopifnot(inherits(failure,"error"),identical(jsonlite::base64_dec(decode(2L)$bytes_base64),malformed))
cat("Chrome invalid CSV original bytes survive validation refusal: PASS\n")

runtime <- fec_runtime_value
body <- charToRaw('{"results":[{"sub_id":4020420242001634771,"amount":-12.34},{"sub_id":"4020420242001634772","amount":0}],"pagination":{"last_indexes":{}}}')
calls <- 0L
fec_runtime_value <- function(...) {
  calls <<- calls+1L
  mentat_evidence_json(list(status=200L,bytes_base64=jsonlite::base64_enc(body)))
}
response <- fec_browser_api_batch(session,"https://api.open.fec.gov/v1/schedules/schedule_a/?api_key=DO_NOT_ARCHIVE",1L)
stopifnot(calls==1L,identical(response$responses[[1L]]$body$results[[1L]]$sub_id,"4020420242001634771"),
  identical(response$responses[[1L]]$body$results[[1L]]$amount,-12.34),
  identical(response$responses[[1L]]$body$results[[2L]]$amount,0L),
  identical(jsonlite::base64_dec(decode(3L)$bytes_base64),body),
  identical(decode(3L)$source_kind,"fec_browser_http_body_bytes"))
cat("Chrome HTTP bytes archived before bigint JSON parse, signs/zero: PASS\n")
body <- charToRaw("{malformed-json")
failure <- tryCatch(fec_browser_api_batch(session,"https://api.open.fec.gov/v1/schedules/schedule_a/",1L),error=function(e)e)
stopifnot(inherits(failure,"error"),identical(jsonlite::base64_dec(decode(4L)$bytes_base64),body))
cat("Chrome malformed HTTP bytes retained before parse refusal: PASS\n")

# Execute the actual small-table JS expression with fake DataTables, no browser/network.
browser_rows <- '[{"sub_id":"4020420242001634771","amount":-12.34},{"sub_id":"text-0","amount":0}]'
fec_runtime_value <- function(session,expression,...) {
  script <- tempfile(fileext=".cjs"); on.exit(unlink(script))
  writeLines(c(paste0("const rows=",browser_rows,";"),
    "global.window={jQuery:()=>({DataTable:()=>({page:{info:()=>({page:0,pages:1,start:0,end:2,length:100,recordsTotal:2,recordsDisplay:2,serverSide:true})},rows:()=>({data:()=>({toArray:()=>rows})})})})};",
    paste0("console.log(eval(",jsonlite::toJSON(expression,auto_unbox=TRUE),"));")),script,useBytes=TRUE)
  output <- system2("node",shQuote(script),stdout=TRUE,stderr=TRUE)
  if (!is.null(attr(output,"status"))) stop(paste(output,collapse="\n"))
  paste(output,collapse="\n")
}
table <- fec_read_page(session)
stopifnot(identical(table$rows[[1L]]$sub_id,"4020420242001634771"),
  identical(table$rows[[1L]]$amount,-12.34),table$rows[[2L]]$amount==0,
  identical(decode(5L)$source_kind,"parsed_browser_snapshot"),identical(decode(5L)$original_response_bytes,FALSE))
browser_rows <- '[{"sub_id":4020420242001634771,"amount":0}]'
failure <- tryCatch(fec_read_page(session),error=function(e)e)
stopifnot(inherits(failure,"error"),grepl("unsafe numeric sub_id",conditionMessage(failure),fixed=TRUE),
  identical(jsonlite::base64_dec(decode(6L)$bytes_base64),charToRaw('{"unsafe_numeric_sub_id":true}')),
  !fec_is_transient_chromote_error(failure))
cat("Chrome parsed snapshot labeled honestly; unsafe numeric ID rejected before stringify: PASS\n")
fec_runtime_value <- runtime

# Fuzz exact text IDs, escaped Unicode/memos, and both signed/zero amounts.
for (i in seq_len(16L)) {
  id <- paste0("4020420242001634",sprintf("%03d",i))
  amount <- if (i %% 4L == 0L) 0 else (-1)^i*i/100
  payload <- mentat_evidence_json(list(results=list(list(sub_id=id,amount=amount,
    memo=paste0("quoted \\\"",i,"\\\" and unicode ",intToUtf8(0x00e9)))),pagination=list(last_indexes=list())))
  raw_payload <- charToRaw(enc2utf8(payload))
  fec_runtime_value <- function(...) mentat_evidence_json(list(status=200L,bytes_base64=jsonlite::base64_enc(raw_payload)))
  answer <- fec_browser_api_batch(session,"https://api.open.fec.gov/v1/schedules/schedule_a/",1L)
  stopifnot(identical(answer$responses[[1L]]$body$results[[1L]]$sub_id,id),
    answer$responses[[1L]]$body$results[[1L]]$amount==amount)
  envelope <- decode(nrow(archives()))
  stopifnot(identical(jsonlite::base64_dec(envelope$bytes_base64),raw_payload),
    identical(envelope$bytes_sha256,digest::digest(raw_payload,algo="sha256",serialize=FALSE)))
}
cat("Chrome 16-case text-ID/Unicode/signed/zero lossless replay: PASS\n")
calls <- 0L
retry_bodies <- list(charToRaw('{"error":"rate limit"}'),charToRaw('{"results":[],"pagination":{"last_indexes":{}}}'))
before <- nrow(archives())
fec_runtime_value <- function(...) {
  calls <<- calls+1L
  mentat_evidence_json(list(status=c(429L,200L)[[calls]],bytes_base64=jsonlite::base64_enc(retry_bodies[[calls]])))
}
Sys.sleep <- function(...) NULL
answer <- fec_browser_api_batch(session,"https://api.open.fec.gov/v1/schedules/schedule_a/",1L,attempts=2L)
rm(Sys.sleep)
stopifnot(calls==2L,answer$responses[[1L]]$status==200L,
  all(c(429L,200L) %in% observations()$http_status),nrow(archives())==before+2L,
  identical(jsonlite::base64_dec(decode(before+1L)$bytes_base64),retry_bodies[[1L]]))
cat("Chrome failed HTTP attempt retained before bounded retry and success: PASS\n")
fec_runtime_value <- runtime

# Actual collector wiring reaches the mandatory callback. A failed archive cannot
# enter a page callback, recover a browser, retry a request, or certify completion.
DBI::dbExecute(connection,"DROP TABLE fec_response_observations")
calls <- 0L
fec_runtime_value <- function(...) {
  calls <<- calls+1L
  mentat_evidence_json(list(status=429L,bytes_base64=jsonlite::base64_enc(charToRaw("fixture-error-body"))))
}
failure <- tryCatch(fec_browser_api_batch(session,"https://api.open.fec.gov/v1/schedules/schedule_a/",1L,attempts=4L),error=function(e)e)
stopifnot(inherits(failure,"mentat_evidence_archive_failed"),calls==1L)
failure <- tryCatch(fec_parse_export_file(session,csv,1L),error=function(e)e)
stopifnot(inherits(failure,"mentat_evidence_archive_failed"))
fec_runtime_value <- runtime
cat("Chrome failed HTTP/CSV archive stops before source parse or transport retry: PASS\n")
original_once <- fec_walk_variant_set_once
original_recover <- fec_recover_session
once_calls <- 0L; page_calls <- 0L; recover_calls <- 0L
fec_walk_variant_set_once <- function(session,page_callback,...) {
  once_calls <<- once_calls+1L
  fec_retain_browser_bytes(session,charToRaw("fixture-body"),"fec_browser_http_body_bytes",0L,200L)
  page_calls <<- page_calls+1L
  page_callback(list(),0L,0L)
  list(records_total=0L,pages=0L,retries=0L)
}
fec_recover_session <- function(...) {recover_calls <<- recover_calls+1L;stop("must not recover")}
attr(session,"mentat_evidence_callback") <- NULL
failure <- tryCatch(collect_employer_set(connection,session,"Fixture","Fixture",2026L,"Fixture","P1_employer",
  trusted_name_keys=character(),match_overrides=data.frame()),error=function(e)e)
stopifnot(inherits(failure,"mentat_evidence_archive_failed"),once_calls==1L,page_calls==0L,recover_calls==0L,
  is.null(fec_browser_evidence_callback(session)),
  DBI::dbGetQuery(connection,"SELECT COUNT(*) n FROM contributions")$n==0L,
  DBI::dbGetQuery(connection,"SELECT COUNT(*) n FROM scrape_progress")$n==0L,
  !grepl("no such table|DO_NOT_ARCHIVE",conditionMessage(failure)))
fec_walk_variant_set_once <- original_once; fec_recover_session <- original_recover
cat("Chrome archive refusal stops actual collector before rows/checkpoint/retry and restores hook: PASS\n")
DBI::dbDisconnect(connection); unlink(csv)
cat("Chrome evidence fixture: PASS\n")

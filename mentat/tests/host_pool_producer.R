# Inert container R producer against Windows-owned fake transport, never provider.
args <- commandArgs(trailingOnly=TRUE)
project <- args[[1L]];directory <- args[[2L]]
assign("MENTAT_PROJECT_ROOT",project,envir=.GlobalEnv)
for (module in c("paths.R","schema.R","ingest.R","collect.R","fec_api.R")) source(file.path(project,"R",module))
stopifnot(fec_api_remote_pool())
connection <- DBI::dbConnect(RSQLite::SQLite(),file.path(directory,"shard.sqlite"))
init_schema(connection)
key <- "fixture-host-pool-a"
context <- fec_api_run_budget(connection,key)
stopifnot(is.na(context$status()$requests_used),is.na(context$status()$requests_remaining))
stopifnot(inherits(tryCatch({context$reserve(-1L);NULL},error=identity),"error"))
# A syntactically valid but wrong token must stop in this remote transport.
original_token <- Sys.getenv("MENTAT_REQUEST_POOL_TOKEN")
Sys.setenv(MENTAT_REQUEST_POOL_TOKEN=paste(rep("invalid",8L),collapse=""))
auth_failure <- tryCatch({fec_api_default_transport(list(list(id="auth-negative",url=FEC_API_ENDPOINT,
 params=list(per_page="1"))),key,1L);NULL},error=identity)
Sys.setenv(MENTAT_REQUEST_POOL_TOKEN=original_token)
stopifnot(inherits(auth_failure,"error"),grepl("request pool unavailable",conditionMessage(auth_failure)))
logical_requests <- 0L
unit <- list(id="inert-host-bridge",kind="employer",industry="Crypto",company="Fixture",cycle=2026L,
 variants="Fixture",company_variants="Fixture",exclusions=character(),phase="P1_employer",
 query_field="contributor_employer",query_filters=list())
fec_api_collect_checkpointed(connection,list(unit),key,1L,fec_api_default_transport,function(...)NULL,
 function(...)0,function(count) {context$reserve(count);logical_requests <<- logical_requests+count},
 lookup_cache=list(match_overrides=data.frame(),home_states=data.frame(),trusted_by_company=list(Fixture=character())))
rows <- DBI::dbGetQuery(connection,"SELECT sub_id,typeof(sub_id) AS kind,contribution_receipt_amount FROM contributions")
archive <- DBI::dbReadTable(connection,"fec_response_archive")
stopifnot(logical_requests==1L,nrow(rows)==1L,rows$sub_id[[1L]]=="400000000000000001",rows$kind[[1L]]=="text",
 rows$contribution_receipt_amount[[1L]]== -1.25,nrow(archive)==1L,
 archive$archive_sha256[[1L]]==mentat_evidence_sha256(archive$payload_json[[1L]]),
 DBI::dbGetQuery(connection,"SELECT COUNT(*) AS n FROM api_request_budget")$n[[1L]]==0L)
context$close();DBI::dbDisconnect(connection)
cat("host_pool_producer: PASS (inert remote body -> archive -> signed text-ID storage; no local quota writes)\n")

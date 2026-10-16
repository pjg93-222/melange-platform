# Actual recovery-tool AST calls, inert browser values and in-memory SQLite only.
args <- commandArgs(TRUE);project <- normalizePath(args[[1L]],winslash="/",mustWork=TRUE)
assign("MENTAT_PROJECT_ROOT",project,envir=.GlobalEnv)
for (module in c("paths","schema","ingest","fec_page","collect","fec_api")) source(file.path(project,"R",paste0(module,".R")))
script <- parse(file.path(project,"tools","collect_employer_date_partition.R"))
helper <- Filter(function(value) is.call(value) && identical(value[[1L]],as.name("<-")) &&
  identical(value[[2L]],as.name("read_planning_total")),as.list(script))
stopifnot(length(helper)==1L)
walker <- list()
visit <- function(value) {
  if (missing(value)) return(invisible(NULL))
  if (!is.call(value) && !is.expression(value) && !is.pairlist(value)) return(invisible(NULL))
  if (is.call(value) && identical(value[[1L]],as.name("fec_walk_variant_set"))) walker[[length(walker)+1L]] <<- value
  for (child in as.list(value)) visit(child)
}
visit(script);stopifnot(length(walker)==1L,identical(walker[[1L]]$evidence_connection,as.name("connection")))
connection <- DBI::dbConnect(RSQLite::SQLite(),":memory:");init_schema(connection)
session <- new.env(parent=emptyenv());company <- "Fixture";cycle <- 2026L;variants <- "Fixture"
broad_variant_set <- "P1_employer:employer:Fixture";broad_url <- fec_build_url(cycle,variants)
sentinel <- function(...) stop("previous callback must only be restored")
attr(session,"mentat_evidence_callback") <- sentinel
fec_navigate <- function(...) invisible(NULL)
fec_runtime_value <- function(...) '{"info":{"page":0,"pages":0,"length":100,"recordsTotal":0,"recordsDisplay":0,"start":0,"end":0,"serverSide":true},"rows":[]}'
eval(helper[[1L]],envir=.GlobalEnv)
stopifnot(read_planning_total()==0L,identical(fec_browser_evidence_callback(session),sentinel))
observation <- DBI::dbReadTable(connection,"fec_response_observations")
archive <- DBI::dbReadTable(connection,"fec_response_archive")
payload <- jsonlite::fromJSON(archive$payload_json[[1L]],simplifyVector=FALSE)
stopifnot(grepl(":planning_count$",observation$unit_id[[1L]]),identical(payload$source_kind,"parsed_browser_snapshot"),
 identical(payload$original_response_bytes,FALSE),DBI::dbGetQuery(connection,"SELECT COUNT(*) n FROM scrape_progress")$n[[1L]]==0L)
cat("Recovery planning-count snapshot has distinct context, parsed label and hook restoration: PASS\n")
partition <- list(min_date="01/01/2026",max_date="12/31/2026");partition_variant_set <- paste0(broad_variant_set,":date:cycle_year_2")
read_calls <- 0L;past_archive <- 0L
fec_walk_variant_set_once <- function(session,...) {
 read_calls <<- read_calls+1L
 fec_retain_browser_bytes(session,charToRaw('{"results":[]}'),"fec_browser_http_body_bytes",0L,200L)
 past_archive <<- past_archive+1L
 list(records_total=0L,pages=0L,retries=0L)
}
eval(walker[[1L]],envir=.GlobalEnv)
stopifnot(read_calls==1L,past_archive==1L,identical(fec_browser_evidence_callback(session),sentinel),
 nrow(DBI::dbReadTable(connection,"fec_response_archive"))==2L,
 any(DBI::dbReadTable(connection,"fec_response_observations")$unit_id==paste(company,cycle,partition_variant_set,sep="::")))
cat("Actual recovery partition call supplies mandatory archive connection/context: PASS\n")
DBI::dbExecute(connection,"DROP TABLE fec_response_observations")
planning_failure <- tryCatch(read_planning_total(),error=identity)
stopifnot(inherits(planning_failure,"mentat_evidence_archive_failed"),identical(fec_browser_evidence_callback(session),sentinel))
read_calls <- 0L;past_archive <- 0L
partition_failure <- tryCatch(eval(walker[[1L]],envir=.GlobalEnv),error=identity)
stopifnot(inherits(partition_failure,"mentat_evidence_archive_failed"),read_calls==1L,past_archive==0L,
 identical(fec_browser_evidence_callback(session),sentinel),
 DBI::dbGetQuery(connection,"SELECT COUNT(*) n FROM contributions")$n[[1L]]==0L,
 DBI::dbGetQuery(connection,"SELECT COUNT(*) n FROM scrape_progress")$n[[1L]]==0L)
DBI::dbDisconnect(connection)
cat("Recovery archive refusal prevents broad zero certification/page callback and restores hook: PASS\n")

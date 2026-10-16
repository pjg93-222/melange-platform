args <- commandArgs(trailingOnly = TRUE)
project <- args[[1L]]
assign("MENTAT_PROJECT_ROOT", project, envir = .GlobalEnv)
for (module in c("paths.R", "schema.R", "fec_api.R")) source(file.path(project,"R",module))
Sys.unsetenv(c("FEC_API_KEY","MENTAT_API_BUDGET_ID","MENTAT_REQUEST_POOL_URL"))
mode <- args[[2L]]
key <- "fixture-budget-a"
if (mode == "identity") {
  cat(jsonlite::toJSON(list(id=fec_api_budget_id(key),aliases=fec_api_budget_aliases(key)),auto_unbox=TRUE))
} else if (mode == "path") {
  Sys.setenv(LOCALAPPDATA=args[[3L]],MENTAT_DATA_ROOT=args[[4L]])
  if (nzchar(args[[5L]])) Sys.setenv(MENTAT_API_BUDGET_DB=args[[5L]]) else Sys.unsetenv("MENTAT_API_BUDGET_DB")
  cat(jsonlite::toJSON(mentat_api_budget_path(project),auto_unbox=TRUE))
} else if (mode == "validate") {
  values <- jsonlite::fromJSON(args[[3L]])
  connection <- DBI::dbConnect(RSQLite::SQLite(), ":memory:")
  result <- tryCatch(fec_api_budget_reserve(connection, values$requests, cap=values$cap,
    now=100, budget_id=fec_api_budget_id(key)),
    error=function(condition) list(error=class(condition)[[1L]],message=conditionMessage(condition)))
  DBI::dbDisconnect(connection)
  cat(jsonlite::toJSON(result,auto_unbox=TRUE,digits=NA))
} else {
  connection <- mentat_api_budget_connect(args[[3L]])
  on.exit(DBI::dbDisconnect(connection))
  result <- tryCatch(fec_api_budget_reserve(connection, as.integer(args[[4L]]),cap=as.integer(args[[5L]]),
    now=as.numeric(args[[6L]]),budget_id=fec_api_budget_id(key),legacy_ids=fec_api_budget_aliases(key),
    legacy_sources=if (length(args)>=7L && nzchar(args[[7L]])) list(args[[7L]]) else list()),
    error=function(condition) list(error=class(condition)[[1L]],message=conditionMessage(condition)))
  cat(jsonlite::toJSON(result,auto_unbox=TRUE,digits=NA))
}

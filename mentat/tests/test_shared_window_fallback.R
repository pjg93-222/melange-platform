script <- sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value=TRUE)[[1]])
root <- normalizePath(file.path(dirname(script), ".."), winslash="/")
for (f in c("paths.R","schema.R","ingest.R","collect.R","fec_api.R")) source(file.path(root,"R",f))
# Fix: the shared-worker date split is optional, but when the FEC API timed out
# (504) on the date-filtered window queries, each window fell back to narrower
# spellings that can never prove coverage, and every reconcile pass repeated
# that outcome until the task failed (Cash App, SECU 2020). The parent query had
# served its first page, so the collector now walks the parent cursor instead.
Sys.setenv(MENTAT_REQUEST_POOL_WIDTH="6", MENTAT_RECONCILE_PASSES="1")
unit <- list(id="P2_variant:Acme:2020:ACME PAY", company="Acme", cycle=2020L, kind="employer",
  phase="P2_variant", variants="ACME PAY", company_variants=c("Acme","ACME PAY"),
  decomposition_variants=c("Acme","ACME PAY"), query_field="contributor_employer", query_filters=list())
n <- 2500L; parent_requests <- 0L
transport <- function(specs, api_key, concurrency) lapply(specs, function(spec) {
  p <- spec$params
  dated <- !is.null(p$min_date) || !is.null(p$max_date)
  if (dated && identical(p$contributor_employer, "ACME PAY"))
    return(list(status=504L, headers=list(), body="", elapsed_seconds=5))
  if (dated) return(list(status=200L, body=list(results=list(),
    pagination=list(is_count_exact=TRUE, count=0L, last_indexes=list()))))
  parent_requests <<- parent_requests + 1L
  start <- if (is.null(p$last_index)) 0L else as.integer(p$last_index)
  ids <- seq.int(start + 1L, min(start + 100L, n))
  list(status=200L, body=list(results=lapply(ids, function(id) list(sub_id=as.character(id))),
    pagination=list(is_count_exact=TRUE, count=n,
      last_indexes=list(last_index=as.character(max(ids)), last_contribution_receipt_date="2020-06-30"))))
})
checkpointed <- character(); discarded <- character()
result <- fec_api_collect_work_units(list(unit), api_key="fake", concurrency=6L, transport=transport,
  sleep=function(...) NULL, jitter=function(...) 0,
  page_callback=function(state, page) checkpointed <<- union(checkpointed, state$unit$id),
  discard_callback=function(unit_ids) discarded <<- c(discarded, unit_ids))
state <- result$units[[1]]
ids <- unlist(lapply(state$pages, function(page) vapply(page$rows, function(r) r$sub_id, "")))
stopifnot(state$complete, state$rows_seen == n, setequal(ids, as.character(seq_len(n))),
          !anyDuplicated(ids), parent_requests == 25L)   # split once, then one serial walk
# Fintech 2020: the abandoned windows stayed checkpointed (fetched, never stored)
# and the coverage gate counted all 26 as incomplete Cash App units.
windows <- grep("::window::", checkpointed, value=TRUE, fixed=TRUE)
stopifnot(length(windows) == 26L, setequal(discarded, windows), !anyDuplicated(discarded),
          !unit$id %in% discarded)
Sys.unsetenv(c("MENTAT_REQUEST_POOL_WIDTH", "MENTAT_RECONCILE_PASSES"))
cat("test_shared_window_fallback: PASS\n")

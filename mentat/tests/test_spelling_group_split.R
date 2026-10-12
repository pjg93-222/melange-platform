script <- sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value=TRUE)[[1]])
root <- normalizePath(file.path(dirname(script), ".."), winslash="/")
for (f in c("paths.R","schema.R","ingest.R","collect.R","fec_api.R")) source(file.path(root,"R",f))
# Berkshire Hathaway 2020: the 10-spelling employer query timed out (504) even
# inside one-month date windows, fell back to unproven narrower spellings, and
# never verified. A multi-spelling query is now split into spelling groups, each
# verified against its own exact count; the union is taken by sub_id.
Sys.unsetenv("MENTAT_REQUEST_POOL_WIDTH"); Sys.setenv(MENTAT_RECONCILE_PASSES="1")
spellings <- c("ALPHA RE","BETA RE","GAMMA RE","DELTA RE")
unit <- list(id="P2_variant:Acme:2020:multi", company="Acme", cycle=2020L, kind="employer",
  phase="P2_variant", variants=spellings, company_variants=c("Acme", spellings),
  decomposition_variants=character(), query_field="contributor_employer", query_filters=list())
# Row 1 matches two spellings (full-text employer search overlaps); it must appear once.
population <- list(
  list(sub_id="1", date=as.Date("2019-03-05"), hits=c("ALPHA RE","BETA RE")),
  list(sub_id="2", date=as.Date("2019-03-20"), hits="GAMMA RE"),
  list(sub_id="3", date=as.Date("2020-07-01"), hits="DELTA RE"),
  list(sub_id="4", date=as.Date("2020-07-02"), hits="ALPHA RE"),
  list(sub_id="5", date=as.Date("2020-11-15"), hits="ZETA RE"))   # no roster spelling
make_transport <- function(stuck = character()) function(specs, api_key, concurrency) lapply(specs, function(spec) {
  p <- spec$params; values <- unlist(p$contributor_employer)
  if (length(values) > 1L || any(values %in% stuck))
    return(list(status=504L, headers=list(), body="", elapsed_seconds=5))
  lo <- if (is.null(p$min_date)) as.Date("1900-01-01") else as.Date(p$min_date, "%m/%d/%Y")
  hi <- if (is.null(p$max_date)) as.Date("2999-12-31") else as.Date(p$max_date, "%m/%d/%Y")
  rows <- Filter(function(r) r$date >= lo && r$date <= hi && any(r$hits %in% values), population)
  list(status=200L, body=list(results=lapply(rows, function(r) list(sub_id=r$sub_id)),
    pagination=list(is_count_exact=TRUE, count=length(rows), last_indexes=list())))
})
run <- function(transport) fec_api_collect_work_units(list(unit), api_key="fake", concurrency=4L,
  transport=transport, sleep=function(...) NULL, jitter=function(...) 0)

state <- run(make_transport())$units[[1]]
ids <- unlist(lapply(state$pages, function(page) vapply(page$rows, function(r) r$sub_id, "")))
stopifnot(state$complete, isTRUE(state$coverage_verified),
          identical(state$pages[[1]]$coverage_method, "exhaustive_date_windows"),
          setequal(ids, c("1","2","3","4")), !anyDuplicated(ids), state$rows_seen == 4L)

# A spelling that stays unservable on its own can never be proven; the rest of
# the groups must not stand in for it.
failed <- tryCatch({ run(make_transport(stuck="DELTA RE")); FALSE }, error=function(e) TRUE)
stopifnot(failed)
Sys.unsetenv("MENTAT_RECONCILE_PASSES")
cat("test_spelling_group_split: PASS\n")

args <- commandArgs(trailingOnly = FALSE)
root <- normalizePath(file.path(dirname(sub("^--file=", "", grep("^--file=",args,value=TRUE)[[1]])),".."),winslash="/")
for (f in c("paths.R","schema.R","ingest.R","collect.R","fec_api.R")) source(file.path(root,"R",f))
Sys.setenv(MENTAT_REQUEST_POOL_WIDTH="6")
unit <- list(id="sample",company="Sample",cycle=2026L,kind="employer",phase="P1_employer",variants="SAMPLE",query_field="contributor_employer",query_filters=list())
windows <- fec_api_date_windows(unit)
stopifnot(length(windows)==26L, is.null(windows[[1]]$query_filters$min_date),is.null(windows[[26]]$query_filters$max_date))
for(i in 1:25)stopifnot(as.Date(windows[[i]]$query_filters$max_date,"%m/%d/%Y")+1==as.Date(windows[[i+1]]$query_filters$min_date,"%m/%d/%Y"))
mode <- "complete"; widths <- integer(); checkpoints <- list()
transport <- function(specs,api_key,concurrency){
  widths <<- c(widths,length(specs))
  lapply(specs,function(spec){
    index <- if(grepl("::window::",spec$id,fixed=TRUE))as.integer(sub(".*::window::","",spec$id)) else 0L
    ids <- if(index)seq((index-1L)*100L+1L,index*100L) else 1:100
    if(mode=="overlap" && index==2L)ids<-1:100
    if(mode=="short" && index==26L)ids<-ids[-100]
    list(status=200L,body=list(results=lapply(ids,function(id)list(sub_id=as.character(id))),pagination=list(is_count_exact=TRUE,count=if(index)length(ids) else 2600L,last_indexes=if(index)list() else list(last_index="100",last_contribution_receipt_date="2026-12-31"))))
  })
}
result <- fec_api_collect_work_units(list(unit),api_key="fake",concurrency=6L,transport=transport,page_callback=function(state,page)checkpoints[[state$unit$id]] <<- state)
stopifnot(result$rows==2600L,max(widths)==6L,result$units$sample$complete,length(checkpoints)==27L,
          result$reconcile_count==0L,identical(widths,c(1L,6L,6L,6L,6L,2L,1L)))
for(bad in c("overlap","short")){
  mode<-bad
  error<-tryCatch({fec_api_collect_work_units(list(unit),api_key="fake",concurrency=6L,transport=transport);""},error=conditionMessage)
  # Overlap is a bug and stops at once; a short union never verifies against
  # the fresh parent count and stops loudly once the passes run out.
  stopifnot(grepl(if(bad=="overlap") "Date-window coverage" else "did not verify after 3 passes",
                  error,fixed=TRUE))
}
mode<-"complete"
# A restart reuses complete child checkpoints, preserving parent publication gating.
restored<-checkpoints[startsWith(names(checkpoints),"sample::window::")]
widths<-integer()
again<-fec_api_collect_work_units(list(unit),api_key="fake",concurrency=6L,transport=transport,initial_states=restored)
# One request splits the parent and one more proves the restored union's count.
stopifnot(again$rows==2600L,identical(widths,c(1L,1L)))
Sys.unsetenv("MENTAT_REQUEST_POOL_WIDTH")
# Timeout fallback must retain an exact count already observed before failure.
for (recovered_count in c(2L,3L)) {
  fallback_transport <- function(specs,api_key,concurrency) lapply(specs,function(spec) {
    if (!grepl("::window::",spec$id,fixed=TRUE)) {
      if (!is.null(spec$params$last_index)) return(list(status=504L,body="",elapsed_seconds=0.01))
      return(list(status=200L,json=list(results=list(list(sub_id="1")),pagination=list(
        is_count_exact=TRUE,count=3L,last_indexes=list(last_index="next",last_contribution_receipt_date="2026-01-01")))))
    }
    rows <- if (endsWith(spec$id,"::01")) lapply(seq_len(recovered_count),function(i)list(sub_id=as.character(i))) else list()
    list(status=200L,json=list(results=rows,pagination=list(is_count_exact=TRUE,count=length(rows),last_indexes=list())))
  })
  outcome <- tryCatch(fec_api_collect_work_units(list(unit),api_key="fixture",concurrency=4L,
    transport=fallback_transport,sleep=function(...)NULL,jitter=function(...)0),error=conditionMessage)
  if (recovered_count==2L) stopifnot(is.character(outcome),grepl("did not verify",outcome))
  else stopifnot(outcome$units$sample$complete,outcome$units$sample$expected_count==3L)
}
# Population drift is now settled against a FRESH parent count before the union
# is combined; the combine step still refuses a union short of the count it is
# handed, which is what an overlapping or missing window looks like.
drifting <- list(unit=unit,expected_count=3L,pages=list(list(count=4L),list(count=3L)))
error <- tryCatch({fec_api_combine_date_windows(drifting,outcome,4L);""},error=conditionMessage)
stopifnot(grepl("partial or overlapping",error,fixed=TRUE))
cat("Shared request date windows, complete union, overlap/missing rejection, checkpoint resume, retained counts: PASS\n")

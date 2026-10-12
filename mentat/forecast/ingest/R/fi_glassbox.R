# Glass Box emission of the feeds (FEEDS_SPEC 8, "Glass Box emission"). The event writer is the refit's rf_gb_emit (itself a
# port of the SGS emitter), sourced from REFIT\R\rf_glassbox.R. The refit hard-wires its own source name, so the ingest binds a
# private copy of that function to an environment where RF$GLASSBOX_SOURCE is "giving-forecast-ingest" (lane "claude");
# the refit's own emitter and its global RF are left as they are.
#
# Every run writes feeds.run.start and exactly one of feeds.run.done / feeds.run.fail. A failed event write only warns.

.fi_gb_emitter <- function() {
  e <- new.env(parent = globalenv())
  e$RF <- utils::modifyList(RF, list(GLASSBOX_LANE = FI$GLASSBOX_LANE, GLASSBOX_SOURCE = FI$SOURCE))
  f <- rf_gb_emit
  environment(f) <- e
  f
}

# A run-scoped emitter. The first event is the correlation root; later events name it as parent.
fi_gb_run <- function(run_id, events_dir = rf_glassbox_dir()) {
  emit_one <- .fi_gb_emitter()
  root <- new.env(parent = emptyenv())
  root$id <- ""
  root$ids <- character()
  root$finished <- FALSE
  stamp <- new.env(parent = emptyenv())
  emit <- function(kind, status, stage, summary, detail = "", metadata = NULL, duration_ms = -1) {
    id <- emit_one(kind, status, stage, summary, run_id, correlation_id = root$id, parent_id = root$id, detail = detail,
                   duration_ms = duration_ms, events_dir = events_dir, metadata = metadata)
    if (!nzchar(root$id) && !is.null(id)) root$id <- id
    if (!is.null(id)) root$ids <- c(root$ids, id)
    id
  }
  elapsed_ms <- function() if (!is.null(stamp$run)) as.integer(round(as.numeric(difftime(Sys.time(), stamp$run, units = "secs")) * 1000)) else -1L
  list(
    run_id = run_id,
    events_dir = events_dir,
    ids = function() root$ids,
    event = emit,
    start = function(feed, industry = "", inbox = "", mode = "run") {
      stamp$run <- Sys.time()
      emit("feeds.run.start", "running", "start", sprintf("feeds run %s (%s)", feed, mode), "",
           list(feed = feed, industry = industry, run_id = run_id, inbox = inbox, mode = mode))
    },
    done = function(summary, receipt = "", outputs = 0L, detail = "") {
      if (root$finished) return(NULL)
      root$finished <- TRUE
      emit("feeds.run.done", "succeeded", "finish", summary, detail, list(receipt = receipt, outputs = outputs), duration_ms = elapsed_ms())
    },
    fail = function(stage, error, detail = "") {
      if (root$finished) return(NULL)
      root$finished <- TRUE
      emit("feeds.run.fail", "failed", stage, paste0("feeds run failed at ", stage, ": ", error), detail,
           list(stage = stage, error = error), duration_ms = elapsed_ms())
    },
    finished = function() root$finished
  )
}

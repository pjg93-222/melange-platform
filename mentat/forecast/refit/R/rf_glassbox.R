# Glass Box event emission for the refit pipeline (R port of sector_growth_study/code/R/sgs_glassbox.R; shape of the
# sector-growth events of 2026-09-25, schema_version 1). File name evt-<UTC yyyyMMddTHHmmss + 7-digit fraction>Z-<10-digit
# pid>.json, written atomically (temporary file + rename), UTF-8 without BOM, compact JSON.
#
# Every pipeline process emits: run start, each stage start / finish, each gate result, publish or hold, failure.
# The folder is RF_GLASSBOX_DIR (default C:\Users\pgoff\.claude\glassbox\events); tests point it at a temp folder.

.rf_gb_stamp <- function(t) {
  secs <- as.numeric(t)
  whole <- floor(secs)
  micro <- min(999999, max(0, round((secs - whole) * 1e6)))
  base <- as.POSIXct(whole, origin = "1970-01-01", tz = "UTC")
  frac7 <- sprintf("%07d", as.integer(micro * 10))
  list(compact = paste0(format(base, "%Y%m%dT%H%M%S", tz = "UTC"), frac7, "Z"),
       iso = paste0(format(base, "%Y-%m-%dT%H:%M:%S", tz = "UTC"), ".", frac7, "Z"), secs = whole + micro / 1e6)
}

# Write one event; returns its id, or NULL (with a warning on stderr) if the event could not be written.
rf_gb_emit <- function(kind, status, stage, summary, run_id, correlation_id = "", parent_id = "", detail = "",
                       duration_ms = -1, cwd = "", events_dir = rf_glassbox_dir(), metadata = NULL) {
  tryCatch({
    dir.create(events_dir, recursive = TRUE, showWarnings = FALSE)
    t <- Sys.time()
    repeat {
      st <- .rf_gb_stamp(t)
      eid <- paste0("evt-", st$compact, "-", sprintf("%010d", Sys.getpid()))
      final <- file.path(events_dir, paste0(eid, ".json"))
      if (!file.exists(final)) break
      t <- st$secs + 1e-6
    }
    event <- list(schema_version = 1L, id = eid, timestamp_utc = st$iso, run_id = run_id,
                  correlation_id = if (nzchar(correlation_id)) correlation_id else eid, parent_id = parent_id,
                  lane = RF$GLASSBOX_LANE, source = RF$GLASSBOX_SOURCE, kind = kind, status = status, stage = stage,
                  summary = substr(summary, 1, 320), detail = substr(detail, 1, 2400),
                  cwd = if (nzchar(cwd)) cwd else normalizePath(getwd(), winslash = "\\"),
                  duration_ms = as.integer(duration_ms), bytes = -1L, metadata = metadata)
    txt <- as.character(jsonlite::toJSON(event, auto_unbox = TRUE, null = "null", digits = NA))
    tmp <- file.path(events_dir, paste0(eid, ".json.", paste(sample(c(0:9, letters[1:6]), 32, TRUE), collapse = ""), ".tmp"))
    con <- file(tmp, open = "wb")
    writeBin(charToRaw(enc2utf8(txt)), con)
    close(con)
    if (!file.rename(tmp, final)) stop("rename failed")
    eid
  }, error = function(e) {
    message("WARNING: Glass Box event not written (", conditionMessage(e), ")")
    NULL
  })
}

# A run-scoped emitter: e <- rf_gb_run(run_id); e$start(...); e$stage_start("panels"); e$stage_finish("panels", "..."); e$finish(...)
# The first event of the run is the correlation root; later events name it as parent, as the SGS runs do.
rf_gb_run <- function(run_id, events_dir = rf_glassbox_dir()) {
  root <- new.env(parent = emptyenv())
  root$id <- ""
  root$ids <- character()
  stamp <- new.env(parent = emptyenv())
  emit <- function(kind, status, stage, summary, detail = "", duration_ms = -1, metadata = NULL) {
    id <- rf_gb_emit(kind, status, stage, summary, run_id, correlation_id = root$id, parent_id = root$id, detail = detail,
                     duration_ms = duration_ms, events_dir = events_dir, metadata = metadata)
    if (!nzchar(root$id) && !is.null(id)) root$id <- id
    if (!is.null(id)) root$ids <- c(root$ids, id)
    id
  }
  list(
    run_id = run_id,
    events_dir = events_dir,
    ids = function() root$ids,
    start = function(summary, detail = "") { stamp$run <- Sys.time(); emit("run.start", "running", "start", summary, detail) },
    stage_start = function(stage, summary = stage) { stamp[[stage]] <- Sys.time(); emit("stage.start", "running", stage, summary) },
    stage_finish = function(stage, summary = stage, detail = "", status = "succeeded") {
      dur <- if (!is.null(stamp[[stage]])) as.integer(round(as.numeric(difftime(Sys.time(), stamp[[stage]], units = "secs")) * 1000)) else -1
      emit("stage.finish", status, stage, summary, detail, duration_ms = dur)
    },
    gate = function(gate, status, detail = "") emit("gate.result", if (status %in% c("PASS", "WARN")) "succeeded" else "failed", "gate", paste0(gate, ": ", status), detail),
    finish = function(summary, detail = "", status = "succeeded") {
      dur <- if (!is.null(stamp$run)) as.integer(round(as.numeric(difftime(Sys.time(), stamp$run, units = "secs")) * 1000)) else -1
      emit("run.finish", status, "finish", summary, detail, duration_ms = dur)
    },
    fail = function(summary, detail = "") emit("run.finish", "failed", "failure", summary, detail),
    emit_event = function(kind, status, stage, summary, detail = "") emit(kind, status, stage, summary, detail)
  )
}

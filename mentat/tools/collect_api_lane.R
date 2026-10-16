# Run one private FEC API key lane.
#
# Each lane owns one SQLite snapshot and one API key. Company-cycle tasks run
# sequentially inside that database so the per-key request budget remains
# authoritative, while other key lanes run concurrently in separate processes.
#
# Usage:
#   Rscript collect_api_lane.R DATABASE INDUSTRY ASSIGNMENTS_JSON RECEIPT_JSON
#     KEY_ID CONCURRENCY
#
# FEC_API_KEY is read only from the process environment and is never printed.

options(stringsAsFactors = FALSE)

arguments <- commandArgs(trailingOnly = TRUE)
if (length(arguments) != 6L) {
  stop(
    paste(
      "usage: collect_api_lane.R DATABASE INDUSTRY ASSIGNMENTS_JSON",
      "RECEIPT_JSON KEY_ID CONCURRENCY"
    ),
    call. = FALSE
  )
}

database_path <- normalizePath(arguments[[1L]], winslash = "/", mustWork = TRUE)
industry <- trimws(arguments[[2L]])
assignments_path <- normalizePath(
  arguments[[3L]],
  winslash = "/",
  mustWork = TRUE
)
receipt_path <- normalizePath(
  arguments[[4L]],
  winslash = "/",
  mustWork = FALSE
)
key_id <- trimws(arguments[[5L]])
concurrency <- suppressWarnings(as.integer(arguments[[6L]]))
pool_width <- suppressWarnings(as.integer(Sys.getenv("MENTAT_REQUEST_POOL_WIDTH", "0")))
if (!is.na(pool_width) && pool_width > 0L) concurrency <- max(concurrency, pool_width)
if (!nzchar(industry) || !nzchar(key_id)) {
  stop("Industry and redacted key id are required.", call. = FALSE)
}
if (is.na(concurrency) || concurrency < 1L) {
  stop("Lane concurrency must be a positive integer.", call. = FALSE)
}

script_path <- normalizePath(
  sub("^--file=", "", grep(
    "^--file=",
    commandArgs(trailingOnly = FALSE),
    value = TRUE
  )[[1L]]),
  winslash = "/",
  mustWork = TRUE
)
project_root <- dirname(dirname(script_path))
assign("MENTAT_PROJECT_ROOT", project_root, envir = .GlobalEnv)
assign("MENTAT_INDUSTRY", industry, envir = .GlobalEnv)

for (module in c(
  "paths.R", "schema.R", "ingest.R", "fec_page.R", "collect.R",
  "fec_api.R", "reconcile.R", "report.R", "lane_owner.R"
)) {
  source(file.path(project_root, "R", module), local = .GlobalEnv)
}

require_package("DBI")
require_package("RSQLite")
require_package("jsonlite")

# A managed upgrade can attach an existing checkpointed fanout to a shared
# request pool without changing its company/database ownership manifest.
pool_config_path <- file.path(dirname(dirname(dirname(assignments_path))), "request-pool.json")
if (!nzchar(Sys.getenv("MENTAT_REQUEST_POOL_URL", "")) && file.exists(pool_config_path)) {
  pool_config <- jsonlite::read_json(pool_config_path)
  if (!identical(pool_config$schema, "mentat-request-pool-v1") ||
      !grepl("^http://127[.]0[.]0[.]1:[0-9]+/batch$", pool_config$url)) stop("Invalid request pool configuration.")
  Sys.setenv(MENTAT_REQUEST_POOL_URL = pool_config$url,
    MENTAT_REQUEST_POOL_TOKEN = pool_config$token,
    MENTAT_REQUEST_POOL_WIDTH = as.character(pool_config$width),
    MENTAT_REQUEST_POOL_LANE = basename(dirname(assignments_path)))
  concurrency <- max(concurrency, as.integer(pool_config$width))
}

atomic_write_json <- function(value, target) {
  directory <- dirname(target)
  if (!dir.exists(directory) &&
      !dir.create(directory, recursive = TRUE, showWarnings = FALSE)) {
    stop("Could not create receipt directory: ", directory, call. = FALSE)
  }
  temporary <- tempfile(
    pattern = paste0(".", basename(target), "."),
    tmpdir = directory
  )
  on.exit(if (file.exists(temporary)) unlink(temporary, force = TRUE), add = TRUE)
  jsonlite::write_json(
    value,
    temporary,
    auto_unbox = TRUE,
    pretty = TRUE,
    null = "null",
    na = "null",
    digits = NA
  )
  atomic_replace(temporary, target)
}

parse_assignments <- function(path) {
  payload <- jsonlite::read_json(path, simplifyVector = FALSE)
  tasks <- payload$tasks %||% payload
  if (!is.list(tasks) || !length(tasks)) {
    stop("Lane assignments must contain a non-empty tasks list.", call. = FALSE)
  }
  normalized <- lapply(seq_along(tasks), function(index) {
    task <- tasks[[index]]
    company <- trimws(as.character(task$company %||% ""))
    cycle <- suppressWarnings(as.integer(task$cycle %||% NA_integer_))
    task_id <- trimws(as.character(task$task_id %||% ""))
    if (!nzchar(company) || is.na(cycle) ||
        !cycle %in% c(2022L, 2024L, 2026L)) {
      stop("Invalid company-cycle assignment at index ", index, ".", call. = FALSE)
    }
    if (!nzchar(task_id)) {
      task_id <- paste0(
        gsub("[^a-z0-9]+", "-", tolower(company), perl = TRUE),
        "-", cycle
      )
    }
    list(task_id = task_id, company = company, cycle = cycle,
         initialization_marker = payload$initialization_markers[[task_id]] %||% "")
  })
  ids <- vapply(normalized, `[[`, character(1L), "task_id")
  pairs <- vapply(normalized, function(task) {
    paste(task$company, task$cycle, sep = "\034")
  }, character(1L))
  if (anyDuplicated(ids) || anyDuplicated(pairs)) {
    stop("Lane assignments contain duplicate task ids or company-cycle pairs.", call. = FALSE)
  }
  list(
    lane = suppressWarnings(as.integer(payload$lane %||% 0L)),
    resume = isTRUE(payload$resume),
    supervisor_owner_path = as.character(payload$supervisor_owner_path %||% ""),
    supervisor_token = as.character(payload$supervisor_token %||% ""),
    tasks = normalized[order(ids, method = "radix")]
  )
}

sqlite_cache_mb <- function(
  value = Sys.getenv("MENTAT_SQLITE_CACHE_MB", unset = "256")
) {
  result <- suppressWarnings(as.integer(trimws(value)))
  if (is.na(result) || result < 32L || result > 2048L) {
    stop("MENTAT_SQLITE_CACHE_MB must be between 32 and 2048.", call. = FALSE)
  }
  result
}

lane_startup_delay <- function(
  lane_number,
  request_pool_url = Sys.getenv("MENTAT_REQUEST_POOL_URL", unset = "")
) {
  # A managed request pool initializes the shared ledger before dispatching R
  # lanes and reserves requests itself. Repeating the ordinal stagger for every
  # company invocation adds no budget protection in this mode. SQLite still
  # keeps its busy timeout, WAL checks, durable writes and per-key accounting.
  if (grepl("^http://127[.]0[.]0[.]1:[0-9]+/batch$", request_pool_url)) return(0)
  lane_index <- if (is.na(lane_number) || lane_number < 1L) 1L else lane_number
  (lane_index - 1L) * 0.25
}

write_run_status <- function(
  connection,
  run_id,
  cycle,
  status,
  started_at,
  ended_at = NA_character_,
  retry_count = 0L,
  determinism = NA_character_,
  message = ""
) {
  DBI::dbExecute(
    connection,
    paste(
      "INSERT INTO run_log",
      "(run_id, cycle, status, started_at, ended_at, retry_count,",
      "determinism_result, message) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
      "ON CONFLICT(run_id, cycle) DO UPDATE SET",
      "status=excluded.status, started_at=excluded.started_at,",
      "ended_at=excluded.ended_at, retry_count=excluded.retry_count,",
      "determinism_result=excluded.determinism_result, message=excluded.message"
    ),
    params = list(
      run_id,
      as.integer(cycle),
      status,
      started_at,
      ended_at,
      as.integer(retry_count),
      determinism,
      message
    )
  )
}

initialize_lane_tasks <- function(connection, tasks, resume) {
  # A durable dispatch can precede R startup. Missing markers still need a
  # fresh reset on recovery. Mark after commit, before making API requests.
  fresh_tasks <- Filter(function(task) {
    marker <- task$initialization_marker %||% ""
    !isTRUE(resume) || (nzchar(marker) && !file.exists(marker))
  }, tasks)
  reset_count <- if (length(fresh_tasks)) reset_api_progress(connection, fresh_tasks) else 0L
  for (task in fresh_tasks) {
    marker <- task$initialization_marker %||% ""
    if (nzchar(marker)) atomic_write_json(list(task_id = task$task_id, initialized = TRUE), marker)
  }
  reset_count
}

run_lane <- function() {
  started_wall <- Sys.time()
  started_elapsed <- proc.time()[["elapsed"]]
  assignment <- parse_assignments(assignments_path)
  tasks <- assignment$tasks
  lane_number <- assignment$lane
  api_key <- fec_api_key()
  lane_status_path <- trimws(Sys.getenv(
    "MENTAT_LANE_STATUS_PATH",
    unset = paste0(receipt_path, ".status.json")
  ))

  writer_owner <- acquire_lane_writer(database_path)
  on.exit(release_lane_writer(writer_owner), add = TRUE)
  check_lane_supervisor(assignment$supervisor_owner_path,
                        assignment$supervisor_token)

  connection <- DBI::dbConnect(RSQLite::SQLite(), database_path)
  DBI::dbExecute(connection, "PRAGMA busy_timeout=60000")
  # Close the data connection before releasing the separate writer lease.
  on.exit(DBI::dbDisconnect(connection), add = TRUE, after = FALSE)
  journal_mode <- DBI::dbGetQuery(
    connection,
    "PRAGMA journal_mode=WAL"
  )[[1L]][[1L]]
  if (!identical(tolower(as.character(journal_mode)), "wal")) {
    stop("SQLite refused WAL mode for lane database.", call. = FALSE)
  }
  cache_mb <- sqlite_cache_mb()
  DBI::dbExecute(connection, "PRAGMA synchronous=NORMAL")
  DBI::dbExecute(connection, "PRAGMA foreign_keys=ON")
  DBI::dbExecute(connection, "PRAGMA temp_store=MEMORY")
  DBI::dbExecute(
    connection,
    paste0("PRAGMA cache_size=-", as.integer(cache_mb * 1024L))
  )
  DBI::dbExecute(connection, "PRAGMA mmap_size=1073741824")
  init_schema(connection)
  reset_unit_count <- initialize_lane_tasks(connection, tasks, assignment$resume)
  refresh_mode <- if (isTRUE(assignment$resume)) {
    "resume"
  } else {
    "fresh"
  }
  budget_database_path <- if (fec_api_remote_pool()) "" else mentat_api_budget_path(project_root)
  budget_connection <- connection
  startup_jitter_seconds <- 0
  if (nzchar(budget_database_path)) {
    budget_database_path <- normalizePath(
      budget_database_path,
      winslash = "/",
      mustWork = FALSE
    )
    if (!dir.exists(dirname(budget_database_path)) &&
        !dir.create(
          dirname(budget_database_path),
          recursive = TRUE,
          showWarnings = FALSE
        )) {
      stop(
        "Could not create persistent API-budget directory: ",
        dirname(budget_database_path),
        call. = FALSE
      )
    }
    startup_jitter_seconds <- lane_startup_delay(lane_number)
    if (startup_jitter_seconds > 0) {
      Sys.sleep(startup_jitter_seconds)
    }
    budget_connection <- DBI::dbConnect(
      RSQLite::SQLite(),
      budget_database_path
    )
    DBI::dbExecute(budget_connection, "PRAGMA busy_timeout=60000")
    on.exit(DBI::dbDisconnect(budget_connection), add = TRUE, after = FALSE)
    budget_journal_mode <- DBI::dbGetQuery(
      budget_connection,
      "PRAGMA journal_mode=WAL"
    )[[1L]][[1L]]
    if (!identical(tolower(as.character(budget_journal_mode)), "wal")) {
      stop("SQLite refused WAL mode for persistent API-budget database.", call. = FALSE)
    }
    DBI::dbExecute(budget_connection, "PRAGMA synchronous=FULL")
    DBI::dbExecute(
      budget_connection,
      paste(
        "CREATE TABLE IF NOT EXISTS api_request_budget (",
        "budget_id TEXT PRIMARY KEY, window_started_epoch REAL NOT NULL,",
        "requests_used INTEGER NOT NULL DEFAULT 0)"
      )
    )
  }
  preflight <- DBI::dbGetQuery(
    connection,
    "PRAGMA quick_check(1)"
  )[[1L]][[1L]]
  if (!identical(preflight, "ok")) {
    stop("Lane database preflight quick_check failed: ", preflight, call. = FALSE)
  }

  cat(
    "FEC API lane:", key_id,
    "| industry=", industry,
    "| tasks=", length(tasks),
    "| refresh_mode=", refresh_mode,
    "| reset_units=", reset_unit_count,
    "| concurrency=", concurrency,
    "| startup_jitter_seconds=", startup_jitter_seconds,
    "| sqlite_cache_mb=", cache_mb,
    "| preflight_quick_check=", preflight,
    "\n",
    sep = ""
  )

  task_receipts <- vector("list", length(tasks))
  write_lane_status <- function(
    status,
    completed,
    failed,
    current = NULL
  ) {
    finished_tasks <- if (completed) {
      task_receipts[seq_len(completed)]
    } else {
      list()
    }
    durations <- vapply(
      finished_tasks,
      function(task) as.numeric(task$wall_clock_seconds %||% 0),
      numeric(1L)
    )
    remaining <- length(tasks) - completed
    estimate <- if (length(durations) && remaining > 0L) {
      mean(durations) * remaining
    } else {
      NA_real_
    }
    atomic_write_json(
      list(
        status = status,
        lane = lane_number,
        industry = industry,
        completed = as.integer(completed),
        total = length(tasks),
        failed = as.integer(failed),
        current_company = current$company %||% NULL,
        current_cycle = current$cycle %||% NULL,
        estimated_remaining_seconds = if (is.na(estimate)) NULL else estimate,
        updated_at = format(Sys.time(), "%Y-%m-%dT%H:%M:%OS3%z"),
        tasks = finished_tasks
      ),
      lane_status_path
    )
  }
  write_lane_status("running", 0L, 0L, tasks[[1L]])
  for (index in seq_along(tasks)) {
    task <- tasks[[index]]
    task_started_wall <- Sys.time()
    task_started_elapsed <- proc.time()[["elapsed"]]
    run_id <- paste("api-fanout", key_id, task$task_id, sep = ":")
    write_run_status(
      connection,
      run_id,
      task$cycle,
      "started",
      format(task_started_wall, "%Y-%m-%dT%H:%M:%OS3%z")
    )
    cat(
      "TASK START ", index, "/", length(tasks),
      ": ", task$company, " ", task$cycle, "\n",
      sep = ""
    )

    collected <- tryCatch(
      collect_crypto_api(
        connection,
        cycles = task$cycle,
        phase2_names = FALSE,
        api_key = api_key,
        concurrency = concurrency,
        run_id = run_id,
        data_root = dirname(database_path),
        company_filter = task$company,
        budget_connection = budget_connection
      ),
      error = function(condition) condition
    )
    task_ended_wall <- Sys.time()
    task_seconds <- unname(
      proc.time()[["elapsed"]] - task_started_elapsed
    )
    if (inherits(collected, "condition")) {
      status <- "failed"
      error_message <- conditionMessage(collected)
      retries <- 0L
      requests <- 0L
      rows <- 0L
      determinism <- NA
    } else if (isTRUE(collected$paused)) {
      status <- "paused"
      error_message <- collected$pause_reason %||% "API lane paused."
      retries <- collected$retries %||% 0L
      requests <- collected$request_count %||% 0L
      rows <- collected$rows %||% 0L
      determinism <- collected$determinism %||% NA
    } else {
      status <- "succeeded"
      error_message <- ""
      retries <- collected$retries %||% 0L
      requests <- collected$request_count %||% 0L
      rows <- collected$rows %||% 0L
      determinism <- collected$determinism %||% NA
    }
    write_run_status(
      connection,
      run_id,
      task$cycle,
      status,
      format(task_started_wall, "%Y-%m-%dT%H:%M:%OS3%z"),
      format(task_ended_wall, "%Y-%m-%dT%H:%M:%OS3%z"),
      retries,
      if (is.na(determinism)) NA_character_ else as.character(determinism),
      error_message
    )
    task_receipts[[index]] <- list(
      task_id = task$task_id,
      company = task$company,
      cycle = as.integer(task$cycle),
      status = status,
      started_at = format(
        task_started_wall,
        "%Y-%m-%dT%H:%M:%OS3%z"
      ),
      ended_at = format(task_ended_wall, "%Y-%m-%dT%H:%M:%OS3%z"),
      wall_clock_seconds = task_seconds,
      rows = as.integer(rows),
      requests = as.integer(requests),
      retries = as.integer(retries),
      determinism = determinism,
      error = error_message
    )
    cat(
      "TASK ", toupper(status), ": ", task$company, " ", task$cycle,
      " | rows=", rows,
      " requests=", requests,
      " retries=", retries,
      " wall=", sprintf("%.3f", task_seconds), "s",
      if (nzchar(error_message)) paste0(" | ", error_message) else "",
      "\n",
      sep = ""
    )
    next_task <- if (index < length(tasks)) tasks[[index + 1L]] else NULL
    write_lane_status(
      if (index < length(tasks)) "running" else "finishing",
      index,
      sum(vapply(
        task_receipts[seq_len(index)],
        function(value) !identical(value$status, "succeeded"),
        logical(1L)
      )),
      next_task
    )
  }

  postflight <- DBI::dbGetQuery(
    connection,
    "PRAGMA quick_check(1)"
  )[[1L]][[1L]]
  failed <- sum(vapply(
    task_receipts,
    function(task) !identical(task$status, "succeeded"),
    logical(1L)
  ))
  ended_wall <- Sys.time()
  receipt <- list(
    status = if (failed) "failed" else "succeeded",
    industry = industry,
    key_id = key_id,
    database = database_path,
    assignments = assignments_path,
    task_count = length(task_receipts),
    succeeded = length(task_receipts) - failed,
    failed = failed,
    refresh_mode = refresh_mode,
    reset_unit_count = reset_unit_count,
    concurrency = as.integer(concurrency),
    startup_jitter_seconds = startup_jitter_seconds,
    sqlite_cache_mb = cache_mb,
    quick_check = postflight,
    started_at = format(started_wall, "%Y-%m-%dT%H:%M:%OS3%z"),
    ended_at = format(ended_wall, "%Y-%m-%dT%H:%M:%OS3%z"),
    wall_clock_seconds = unname(
      proc.time()[["elapsed"]] - started_elapsed
    ),
    tasks = task_receipts
  )
  atomic_write_json(receipt, receipt_path)
  write_lane_status(
    receipt$status,
    length(task_receipts),
    failed,
    NULL
  )
  cat(
    "FEC API lane complete:", key_id,
    "| status=", receipt$status,
    "| succeeded=", receipt$succeeded, "/", receipt$task_count,
    "| quick_check=", postflight,
    "| receipt=", receipt_path,
    "\n",
    sep = ""
  )
  if (!identical(postflight, "ok")) return(3L)
  if (failed) 2L else 0L
}

exit_status <- tryCatch(
  run_lane(),
  error = function(condition) {
    if (inherits(condition, "mentat_lane_owned")) {
      message("FEC API lane writer refused: ", conditionMessage(condition))
      return(4L)
    }
    receipt <- list(
      status = "failed",
      industry = industry,
      key_id = key_id,
      database = database_path,
      assignments = assignments_path,
      task_count = 0L,
      succeeded = 0L,
      failed = 1L,
      error = conditionMessage(condition)
    )
    try(atomic_write_json(receipt, receipt_path), silent = TRUE)
    message("FEC API lane FAILED: ", conditionMessage(condition))
    1L
  }
)
quit(save = "no", status = as.integer(exit_status), runLast = FALSE)

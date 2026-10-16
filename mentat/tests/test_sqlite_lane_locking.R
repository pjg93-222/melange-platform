options(stringsAsFactors = FALSE)

script_argument <- grep(
  "^--file=",
  commandArgs(trailingOnly = FALSE),
  value = TRUE
)
if (!length(script_argument)) {
  stop("Could not locate this test script.", call. = FALSE)
}
test_path <- normalizePath(
  sub("^--file=", "", script_argument[[1L]]),
  winslash = "/",
  mustWork = TRUE
)
project_root <- dirname(dirname(test_path))
source(file.path(project_root, "R", "fec_api.R"), local = .GlobalEnv)

assert_dbconnect_configuration <- function(path, expected_count) {
  lines <- readLines(path, warn = FALSE)
  starts <- grep("DBI::dbConnect", lines, fixed = TRUE)
  stopifnot(length(starts) == expected_count)
  for (start in starts) {
    end <- start
    balance <- 0L
    repeat {
      balance <- balance +
        lengths(regmatches(lines[[end]], gregexpr("(", lines[[end]], fixed = TRUE))) -
        lengths(regmatches(lines[[end]], gregexpr(")", lines[[end]], fixed = TRUE)))
      if (balance <= 0L) break
      end <- end + 1L
      if (end > length(lines)) {
        stop("Unclosed dbConnect expression in ", path, call. = FALSE)
      }
    }
    next_line <- end + 1L
    while (next_line <= length(lines) &&
           (!nzchar(trimws(lines[[next_line]])) ||
            startsWith(trimws(lines[[next_line]]), "#"))) {
      next_line <- next_line + 1L
    }
    if (next_line > length(lines) ||
        !grepl("DBI::dbExecute", lines[[next_line]], fixed = TRUE) ||
        !grepl("PRAGMA busy_timeout", lines[[next_line]], fixed = TRUE) ||
        !grepl("60000", lines[[next_line]], fixed = TRUE)) {
      stop(
        "dbConnect is not immediately followed by busy_timeout=60000 in ",
        path,
        " at line ",
        start,
        call. = FALSE
      )
    }
  }
  invisible(length(starts))
}

lane_path <- file.path(project_root, "tools", "collect_api_lane.R")
paths_path <- file.path(project_root, "R", "paths.R")
fec_api_path <- file.path(project_root, "R", "fec_api.R")
collect_path <- file.path(project_root, "R", "collect.R")
fec_page_path <- file.path(project_root, "R", "fec_page.R")

assert_dbconnect_configuration(lane_path, 2L)
assert_dbconnect_configuration(paths_path, 2L)
assert_dbconnect_configuration(fec_api_path, 0L)
assert_dbconnect_configuration(collect_path, 0L)
assert_dbconnect_configuration(fec_page_path, 0L)

lane_text <- paste(readLines(lane_path, warn = FALSE), collapse = "\n")
fec_api_text <- paste(readLines(fec_api_path, warn = FALSE), collapse = "\n")
reserve_start <- regexpr(
  "fec_api_budget_reserve <- function",
  fec_api_text,
  fixed = TRUE
)[[1L]]
reserve_end <- regexpr(
  "fec_api_budget_status <- function",
  fec_api_text,
  fixed = TRUE
)[[1L]]
reserve_text <- substr(fec_api_text, reserve_start, reserve_end - 1L)
stopifnot(
  grepl("PRAGMA journal_mode=WAL", lane_text, fixed = TRUE),
  grepl(
    "startup_jitter_seconds <- lane_startup_delay(lane_number)",
    lane_text,
    fixed = TRUE
  ),
  grepl('DBI::dbExecute(connection, "BEGIN IMMEDIATE")', reserve_text, fixed = TRUE),
  grepl("DBI::dbCommit(connection)", reserve_text, fixed = TRUE),
  !grepl("transport", reserve_text, fixed = TRUE),
  !grepl("Sys.sleep", reserve_text, fixed = TRUE)
)

if (identical(Sys.getenv("MENTAT_STRUCTURAL_ONLY", unset = ""), "1")) {
  cat("test_sqlite_lane_locking: STRUCTURAL PASS\n")
  quit(save = "no", status = 0L, runLast = FALSE)
}
if (!requireNamespace("DBI", quietly = TRUE) ||
    !requireNamespace("RSQLite", quietly = TRUE)) {
  stop("test_sqlite_lane_locking requires DBI and RSQLite.", call. = FALSE)
}

test_root <- tempfile("mentat-sqlite-lock-")
dir.create(test_root, recursive = TRUE)
database_path <- file.path(test_root, "budget.sqlite")
ready_path <- file.path(test_root, "writer.ready")
done_path <- file.path(test_root, "writer.done")
helper_path <- file.path(test_root, "hold-write-lock.R")
helper_stdout <- file.path(test_root, "helper.stdout.log")
helper_stderr <- file.path(test_root, "helper.stderr.log")
on.exit(unlink(test_root, recursive = TRUE, force = TRUE), add = TRUE)

connection <- DBI::dbConnect(RSQLite::SQLite(), database_path)
DBI::dbExecute(connection, "PRAGMA busy_timeout=60000")
on.exit(
  if (DBI::dbIsValid(connection)) DBI::dbDisconnect(connection),
  add = TRUE
)
journal_mode <- DBI::dbGetQuery(
  connection,
  "PRAGMA journal_mode=WAL"
)[[1L]][[1L]]
stopifnot(identical(tolower(as.character(journal_mode)), "wal"))
DBI::dbExecute(
  connection,
  paste(
    "CREATE TABLE api_request_budget (",
    "budget_id TEXT PRIMARY KEY, window_started_epoch REAL NOT NULL,",
    "requests_used INTEGER NOT NULL DEFAULT 0)"
  )
)

writeLines(
  c(
    "args <- commandArgs(trailingOnly = TRUE)",
    "connection <- DBI::dbConnect(RSQLite::SQLite(), args[[1L]])",
    "DBI::dbExecute(connection, 'PRAGMA busy_timeout=60000')",
    paste0(
      "on.exit(if (DBI::dbIsValid(connection)) ",
      "DBI::dbDisconnect(connection), add = TRUE)"
    ),
    "DBI::dbGetQuery(connection, 'PRAGMA journal_mode=WAL')",
    "DBI::dbExecute(connection, 'BEGIN IMMEDIATE')",
    paste0(
      "DBI::dbExecute(connection, \"INSERT INTO api_request_budget ",
      "(budget_id, window_started_epoch, requests_used) ",
      "VALUES ('helper', 100, 1)\")"
    ),
    "writeLines('ready', args[[2L]])",
    "Sys.sleep(1.25)",
    "DBI::dbCommit(connection)",
    "DBI::dbDisconnect(connection)",
    "writeLines('done', args[[3L]])"
  ),
  helper_path,
  useBytes = TRUE
)

rscript_path <- file.path(R.home("bin"), "Rscript.exe")
if (!file.exists(rscript_path)) {
  rscript_path <- file.path(R.home("bin"), "Rscript")
}
system2(
  rscript_path,
  c(
    shQuote(helper_path),
    shQuote(database_path),
    shQuote(ready_path),
    shQuote(done_path)
  ),
  stdout = helper_stdout,
  stderr = helper_stderr,
  wait = FALSE
)

deadline <- Sys.time() + 10
while (!file.exists(ready_path) && Sys.time() < deadline) {
  Sys.sleep(0.05)
}
if (!file.exists(ready_path)) {
  helper_error <- if (file.exists(helper_stderr)) {
    paste(readLines(helper_stderr, warn = FALSE), collapse = "\n")
  } else {
    "no helper stderr"
  }
  stop("Write-lock helper did not start: ", helper_error, call. = FALSE)
}

started <- proc.time()[["elapsed"]]
reservation <- fec_api_budget_reserve(
  connection,
  requests = 2L,
  cap = 10L,
  now = 101,
  budget_id = "openfec:sha256:cccccccccccc"
)
waited_seconds <- unname(proc.time()[["elapsed"]] - started)
rows <- DBI::dbGetQuery(
  connection,
  "SELECT budget_id, requests_used FROM api_request_budget ORDER BY budget_id"
)
configured_timeout <- DBI::dbGetQuery(
  connection,
  "PRAGMA busy_timeout"
)[[1L]][[1L]]

stopifnot(
  waited_seconds >= 0.5,
  waited_seconds < 10,
  reservation$requests_used == 2L,
  as.integer(configured_timeout) == 60000L,
  nrow(rows) == 2L,
  identical(rows$budget_id, c("helper", "openfec:sha256:cccccccccccc"))
)

deadline <- Sys.time() + 5
while (!file.exists(done_path) && Sys.time() < deadline) {
  Sys.sleep(0.05)
}
stopifnot(file.exists(done_path))
DBI::dbDisconnect(connection)
cat(
  sprintf(
    "test_sqlite_lane_locking: PASS (waited %.3fs for write lock)\n",
    waited_seconds
  )
)

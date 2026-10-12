script_argument <- grep("^--file=", commandArgs(trailingOnly = FALSE), value = TRUE)
stopifnot(length(script_argument) == 1L)
script_path <- normalizePath(
  sub("^--file=", "", script_argument[[1L]]),
  winslash = "/",
  mustWork = TRUE
)
project_root <- normalizePath(
  file.path(dirname(script_path), ".."),
  winslash = "/",
  mustWork = TRUE
)
for (file in c("paths.R", "schema.R", "ingest.R", "collect.R", "fec_api.R")) {
  source(file.path(project_root, "R", file), local = .GlobalEnv)
}

filter_fixture <- list(
  rosters = list(
    Alpha = data.frame(variant = "ALPHA", stringsAsFactors = FALSE),
    Beta = data.frame(variant = "BETA", stringsAsFactors = FALSE)
  ),
  match_overrides = data.frame(),
  home_states = data.frame(),
  trusted_by_company = list(Alpha = "A PERSON", Beta = "B PERSON")
)
stopifnot(
  identical(fec_api_filter_lookups(filter_fixture, NULL), filter_fixture),
  identical(
    names(fec_api_filter_lookups(filter_fixture, "Beta")$rosters),
    "Beta"
  ),
  identical(
    names(fec_api_filter_lookups(filter_fixture, "Beta")$trusted_by_company),
    "Beta"
  )
)
unknown_company <- tryCatch(
  {
    fec_api_filter_lookups(filter_fixture, "Gamma")
    ""
  },
  error = conditionMessage
)
stopifnot(grepl("unknown company names: Gamma", unknown_company, fixed = TRUE))

# The persistent redacted budget ledger is global to a key, not a run/shard.
# Reopening the database simulates a same-key rerun; a second key must retain an
# independent count in the same hourly window.
budget_database <- tempfile(fileext = ".sqlite")
budget_connection <- DBI::dbConnect(RSQLite::SQLite(), budget_database)
init_schema(budget_connection)
first_key_id <- "openfec:sha256:aaaaaaaaaaaa"
second_key_id <- "openfec:sha256:bbbbbbbbbbbb"
first_reservation <- fec_api_budget_reserve(
  budget_connection,
  requests = 3L,
  cap = 10L,
  now = 100,
  budget_id = first_key_id
)
DBI::dbDisconnect(budget_connection)
budget_connection <- DBI::dbConnect(RSQLite::SQLite(), budget_database)
same_key_rerun <- fec_api_budget_reserve(
  budget_connection,
  requests = 2L,
  cap = 10L,
  now = 101,
  budget_id = first_key_id
)
other_key <- fec_api_budget_reserve(
  budget_connection,
  requests = 4L,
  cap = 10L,
  now = 101,
  budget_id = second_key_id
)
stopifnot(
  first_reservation$requests_used == 3L,
  same_key_rerun$requests_used == 5L,
  other_key$requests_used == 4L,
  fec_api_budget_status(
    budget_connection,
    cap = 10L,
    now = 102,
    budget_id = first_key_id
  )$requests_remaining == 5L,
  fec_api_budget_status(
    budget_connection,
    cap = 10L,
    now = 102,
    budget_id = second_key_id
  )$requests_remaining == 6L,
  identical(
    fec_api_budget_id("never-stored", first_key_id),
    first_key_id
  )
)
DBI::dbDisconnect(budget_connection)
unlink(budget_database, force = TRUE)

# Load only helper definitions, never the collector's command-line entry point.
for (expression in parse(file.path(project_root, "tools", "collect_api_lane.R"))) {
  if (is.call(expression) && identical(expression[[1L]], as.name("<-")) &&
      as.character(expression[[2L]]) %in% c("atomic_write_json", "parse_assignments", "initialize_lane_tasks", "lane_startup_delay")) {
    eval(expression, envir = .GlobalEnv)
  }
}
# Managed pools already created the persistent budget before dispatching R.
# Direct lanes retain the existing stagger and malformed pool URLs do not
# disable it. Repeated company invocations must incur no pool-mode sleep.
stopifnot(
  lane_startup_delay(32L, "http://127.0.0.1:12345/batch") == 0,
  lane_startup_delay(32L, "http://127.0.0.1:12345/batch") == 0,
  lane_startup_delay(32L, "") == 7.75,
  lane_startup_delay(1L, "") == 0,
  lane_startup_delay(NA_integer_, "") == 0,
  lane_startup_delay(0L, "") == 0,
  lane_startup_delay(32L, "https://example.invalid/batch") == 7.75,
  lane_startup_delay(32L, "http://127.0.0.1:12345/other") == 7.75
)
marker_root <- tempfile("mentat-initialization-")
dir.create(marker_root)
marker <- file.path(marker_root, "a.json")
tasks <- list(list(task_id = "a-2026", company = "Alpha", cycle = 2026L, initialization_marker = marker))
original_reset <- reset_api_progress
reset_calls <- 0L
reset_api_progress <- function(connection, tasks) { reset_calls <<- reset_calls + 1L; 2L }
# Crash after dispatch but before R startup: no marker, so even resume resets.
stopifnot(initialize_lane_tasks(NULL, tasks, TRUE) == 2L, file.exists(marker))
# Crash after collection begins: preserve all existing page checkpoints.
stopifnot(initialize_lane_tasks(NULL, tasks, TRUE) == 0L, reset_calls == 1L)
stopifnot(initialize_lane_tasks(NULL, tasks, FALSE) == 2L, reset_calls == 2L)
unlink(marker)
reset_api_progress <- function(connection, tasks) stop("injected reset failure")
failure <- tryCatch(initialize_lane_tasks(NULL, tasks, TRUE), error = conditionMessage)
stopifnot(identical(failure, "injected reset failure"), !file.exists(marker))
reset_api_progress <- original_reset
unlink(marker_root, recursive = TRUE)
cat("test_fec_api_fanout: PASS\n")

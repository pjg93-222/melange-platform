# Reconciliation contract for a moving FEC pagination.count.
# Paul's ruling: "API count can change, it should always be verified" and "It
# should always be complete, no misrepresentation". A count that moves mid-walk
# must not abort the lane and must not be stored unverified; the collector
# re-walks until one whole pass sees a constant count equal to the rows it
# collected, or fails loudly with nothing stored.
# Fake transports only - this test never touches the network.
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
suppressPackageStartupMessages(library(data.table))

results <- list()
run_test <- function(name, body) {
  outcome <- tryCatch({
    body()
    "PASS"
  }, error = function(condition) paste0("FAIL: ", conditionMessage(condition)))
  results[[name]] <<- outcome
  cat(sprintf("%-3s %s\n", name, outcome))
  invisible(NULL)
}

employer_unit <- function(id, variants = "ACME") {
  list(
    id = id, kind = "employer", industry = MENTAT_INDUSTRY, company = "Acme",
    cycle = 2024L, variants = variants, company_variants = variants,
    phase = "P1_employer", query_field = "contributor_employer",
    query_filters = list(), exclusions = character(), per_page = 100L
  )
}

fixture_row <- function(id) {
  list(
    sub_id = id, contributor_name = "DOE, JANE", contributor_employer = "ACME",
    contributor_city = "SEATTLE", contributor_state = "WA",
    contributor_zip = "98101", entity_type = "IND",
    committee_id = "C00000001", committee_name = "FUND",
    contribution_receipt_date = "2024-03-01",
    contribution_receipt_amount = 100, two_year_transaction_period = 2024L
  )
}

ok_page <- function(rows, count, cursor = NULL) {
  list(status = 200L, headers = list(), json = list(
    results = rows,
    pagination = list(
      is_count_exact = TRUE, count = as.integer(count),
      last_indexes = if (is.null(cursor)) NULL else list(
        last_index = as.character(cursor),
        last_contribution_receipt_date = "2024-01-01"
      )
    )
  ))
}

fresh_database <- function() {
  connection <- DBI::dbConnect(RSQLite::SQLite(), ":memory:")
  init_schema(connection)
  connection
}

store_cache <- function(connection) {
  list(
    rosters = list(Acme = data.frame(variant = "ACME", stringsAsFactors = FALSE)),
    match_overrides = company_match_overrides_data(connection),
    home_states = company_home_states_data(connection),
    trusted_by_company = list(Acme = character())
  )
}

no_sleep <- function(seconds) invisible(NULL)
no_jitter <- function(...) 0

count_rows <- function(connection, prefix) {
  DBI::dbGetQuery(
    connection,
    "SELECT COUNT(*) AS n FROM contributions WHERE sub_id LIKE ?",
    params = list(paste0(prefix, "%"))
  )$n[[1L]]
}

# ---------------------------------------------------------------------------
# A single cursor walk whose count moves. Pass 1 reports 250 on page 1 and 251
# on the terminal page; pass 2 is stable at 251 and collects 251 rows.
# Pass-2 rows carry a different id prefix, so a stored pass-1 row is visible.
# ---------------------------------------------------------------------------
moving_pass <- 0L
moving_checkpoints_at_pass_2 <- NA_integer_
moving_connection <- NULL
moving_unit <- employer_unit("P1_employer:Acme:2024:MOVING")
moving_transport <- function(specs, api_key, concurrency) {
  lapply(specs, function(spec) {
    cursor <- spec$params$last_index
    if (is.null(cursor)) {
      moving_pass <<- moving_pass + 1L
      page <- 1L
      if (moving_pass == 2L && !is.null(moving_connection)) {
        moving_checkpoints_at_pass_2 <<- DBI::dbGetQuery(
          moving_connection,
          "SELECT COUNT(*) AS n FROM api_page_checkpoints WHERE unit_id=?",
          params = list(moving_unit$id)
        )$n[[1L]]
      }
    } else {
      page <- as.integer(cursor) + 1L
    }
    available <- if (moving_pass == 1L) 250L else 251L
    reported <- if (moving_pass == 1L && page >= 3L) 251L else available
    prefix <- if (moving_pass == 1L) "T1A-" else "T1B-"
    first <- (page - 1L) * 100L + 1L
    last <- min(page * 100L, available)
    rows <- if (first > last) list() else lapply(
      seq.int(first, last), function(index) fixture_row(sprintf("%s%04d", prefix, index))
    )
    ok_page(rows, reported, if (last < available) page else NULL)
  })
}

run_test("t1", function() {
  moving_pass <<- 0L
  moving_checkpoints_at_pass_2 <<- NA_integer_
  connection <- fresh_database()
  moving_connection <<- connection
  on.exit({
    moving_connection <<- NULL
    DBI::dbDisconnect(connection)
  }, add = TRUE)
  result <- fec_api_collect_checkpointed(
    connection, list(moving_unit), api_key = "fixture-key", concurrency = 1L,
    transport = moving_transport, sleep = no_sleep, jitter = no_jitter,
    before_request = function(count) invisible(NULL),
    run_id = NULL, lookup_cache = store_cache(connection)
  )
  state <- result$units[[moving_unit$id]]
  stopifnot(
    "unit did not complete" = isTRUE(state$complete),
    "exactly one reconciliation pass" = identical(result$reconcile_count, 1L),
    "unit records its pass" = identical(state$reconcile_pass, 1L),
    "pass reason recorded" = length(state$reconcile_log) == 1L,
    "reason names the moved count" =
      grepl("pass 1: count moved 250->251 after 250 rows", state$reconcile_log[[1L]], fixed = TRUE),
    "verified population is 251" = identical(as.integer(state$expected_count), 251L),
    "collected 251 rows" = identical(as.integer(state$rows_seen), 251L),
    "pass-1 checkpoints were removed before pass 2" =
      identical(moving_checkpoints_at_pass_2, 0L),
    "stored the verified pass only" = count_rows(connection, "T1B-") == 251L,
    "stored no unverified row" = count_rows(connection, "T1A-") == 0L
  )
})

# ---------------------------------------------------------------------------
# A stable count whose walk runs out of cursor 10 rows short. Pass 2 completes.
# ---------------------------------------------------------------------------
short_pass <- 0L
short_unit <- employer_unit("P1_employer:Acme:2024:SHORT")
short_transport <- function(specs, api_key, concurrency) {
  lapply(specs, function(spec) {
    cursor <- spec$params$last_index
    if (is.null(cursor)) {
      short_pass <<- short_pass + 1L
      page <- 1L
    } else {
      page <- as.integer(cursor) + 1L
    }
    available <- if (short_pass == 1L) 240L else 250L
    prefix <- if (short_pass == 1L) "T2A-" else "T2B-"
    first <- (page - 1L) * 100L + 1L
    last <- min(page * 100L, available)
    rows <- if (first > last) list() else lapply(
      seq.int(first, last), function(index) fixture_row(sprintf("%s%04d", prefix, index))
    )
    ok_page(rows, 250L, if (last < available) page else NULL)
  })
}

run_test("t2", function() {
  short_pass <<- 0L
  connection <- fresh_database()
  on.exit(DBI::dbDisconnect(connection), add = TRUE)
  result <- fec_api_collect_checkpointed(
    connection, list(short_unit), api_key = "fixture-key", concurrency = 1L,
    transport = short_transport, sleep = no_sleep, jitter = no_jitter,
    before_request = function(count) invisible(NULL),
    run_id = NULL, lookup_cache = store_cache(connection)
  )
  state <- result$units[[short_unit$id]]
  stopifnot(
    "unit did not complete" = isTRUE(state$complete),
    "exactly one reconciliation pass" = identical(result$reconcile_count, 1L),
    "reason names the shortfall" =
      grepl("pass 1: shortfall 240 of 250", state$reconcile_log[[1L]], fixed = TRUE),
    "collected 250 rows" = identical(as.integer(state$rows_seen), 250L),
    "stored the verified pass only" = count_rows(connection, "T2B-") == 250L,
    "stored no unverified row" = count_rows(connection, "T2A-") == 0L
  )
})

# ---------------------------------------------------------------------------
# A population that moves on every pass fails loudly and stores nothing.
# ---------------------------------------------------------------------------
endless_pass <- 0L
endless_unit <- employer_unit("P1_employer:Acme:2024:ENDLESS")
endless_transport <- function(specs, api_key, concurrency) {
  lapply(specs, function(spec) {
    cursor <- spec$params$last_index
    if (is.null(cursor)) {
      endless_pass <<- endless_pass + 1L
      page <- 1L
    } else {
      page <- as.integer(cursor) + 1L
    }
    available <- 150L
    reported <- if (page >= 2L) 150L + endless_pass else 150L
    first <- (page - 1L) * 100L + 1L
    last <- min(page * 100L, available)
    rows <- if (first > last) list() else lapply(
      seq.int(first, last), function(index) fixture_row(sprintf("T3-%04d", index))
    )
    ok_page(rows, reported, if (last < available) page else NULL)
  })
}

run_test("t3", function() {
  endless_pass <<- 0L
  connection <- fresh_database()
  on.exit(DBI::dbDisconnect(connection), add = TRUE)
  failure <- tryCatch({
    fec_api_collect_checkpointed(
      connection, list(endless_unit), api_key = "fixture-key", concurrency = 1L,
      transport = endless_transport, sleep = no_sleep, jitter = no_jitter,
      before_request = function(count) invisible(NULL),
      run_id = NULL, lookup_cache = store_cache(connection)
    )
    ""
  }, error = conditionMessage)
  stopifnot(
    "failure names the pass budget" =
      grepl("did not verify after 3 passes", failure, fixed = TRUE),
    "failure names the unit" = grepl(endless_unit$id, failure, fixed = TRUE),
    "failure states nothing was stored" =
      grepl("Nothing unverified was stored", failure, fixed = TRUE),
    "every reason is logged" = grepl("pass 4: ", failure, fixed = TRUE),
    "four walks were attempted" = identical(endless_pass, 4L),
    "nothing was stored" = count_rows(connection, "T3-") == 0L
  )
})

# ---------------------------------------------------------------------------
# Date windows. The split-time parent count is a hint; the fresh parent count
# observed after the windows is what publishes the union.
# ---------------------------------------------------------------------------
window_index <- function(spec_id) {
  if (!grepl("::window::", spec_id, fixed = TRUE)) return(0L)
  as.integer(sub(".*::window::", "", spec_id))
}

window_unit <- employer_unit("P1_employer:Acme:2024:WINDOWS")

# Windows 1..21 hold 100 rows each, window 22 holds the remainder, 23..26 are
# empty. `late` is the size window 22 reports once it has been collected.
window_transport_factory <- function(split_count, early, late, calls) {
  function(specs, api_key, concurrency) {
    lapply(specs, function(spec) {
      index <- window_index(spec$id)
      seen <- (calls[[spec$id]] %||% 0L) + 1L
      calls[[spec$id]] <- seen
      if (index == 0L) {
        count <- if (seen == 1L) split_count else 2100L + late
        rows <- lapply(1:100, function(row) fixture_row(sprintf("W01-%04d", row)))
        return(ok_page(rows, count, 1L))
      }
      size <- if (index <= 21L) 100L else if (index == 22L) {
        if (seen == 1L) early else late
      } else 0L
      rows <- if (!size) list() else lapply(
        seq_len(size), function(row) fixture_row(sprintf("W%02d-%04d", index, row))
      )
      ok_page(rows, size, NULL)
    })
  }
}

run_test("t4", function() {
  Sys.setenv(MENTAT_REQUEST_POOL_WIDTH = "6")
  on.exit(Sys.unsetenv("MENTAT_REQUEST_POOL_WIDTH"), add = TRUE)
  calls <- new.env(parent = emptyenv())
  result <- fec_api_collect_work_units(
    list(window_unit), api_key = "fixture-key", concurrency = 6L,
    transport = window_transport_factory(2174L, 80L, 80L, calls),
    sleep = no_sleep, jitter = no_jitter
  )
  state <- result$units[[window_unit$id]]
  per_window <- vapply(sprintf("%s::window::%02d", window_unit$id, 1:26),
    function(id) calls[[id]] %||% 0L, integer(1L))
  stopifnot(
    "union did not publish" = isTRUE(state$complete),
    "coverage is verified" = isTRUE(state$coverage_verified),
    "population is the fresh parent count" =
      identical(as.integer(state$expected_count), 2180L),
    "no reconciliation pass was needed" = identical(result$reconcile_count, 0L),
    "parent was fetched exactly twice" =
      identical(calls[[window_unit$id]], 2L),
    "every window was walked exactly once" = all(per_window == 1L),
    "one parent split, one parent proof, 26 windows" =
      identical(result$request_count, 28L)
  )
})

run_test("t5", function() {
  Sys.setenv(MENTAT_REQUEST_POOL_WIDTH = "6")
  on.exit(Sys.unsetenv("MENTAT_REQUEST_POOL_WIDTH"), add = TRUE)
  calls <- new.env(parent = emptyenv())
  result <- fec_api_collect_work_units(
    list(window_unit), api_key = "fixture-key", concurrency = 6L,
    transport = window_transport_factory(2174L, 74L, 80L, calls),
    sleep = no_sleep, jitter = no_jitter
  )
  state <- result$units[[window_unit$id]]
  per_window <- vapply(sprintf("%s::window::%02d", window_unit$id, 1:26),
    function(id) calls[[id]] %||% 0L, integer(1L))
  stopifnot(
    "union did not publish" = isTRUE(state$complete),
    "coverage is verified" = isTRUE(state$coverage_verified),
    "population is the fresh parent count" =
      identical(as.integer(state$expected_count), 2180L),
    "exactly one reconciliation pass" = identical(result$reconcile_count, 1L),
    "pass reason names the mismatch" = any(grepl(
      "windows hold 2174 rows against a fresh parent count of 2180",
      state$reconcile_log, fixed = TRUE
    )),
    "only the drifted window was re-walked" =
      identical(per_window[[22L]], 3L) && all(per_window[-22L] == 2L),
    "parent split plus two proofs" = identical(calls[[window_unit$id]], 3L),
    "1 split + 26 walks + 1 proof + 26 rechecks + 1 re-walk + 1 proof" =
      identical(result$request_count, 56L)
  )
})

# ---------------------------------------------------------------------------
# A saved row whose query contract no longer matches is dropped for that unit
# alone; a sibling with a matching contract still resumes from its cursor.
# ---------------------------------------------------------------------------
run_test("t6", function() {
  connection <- fresh_database()
  on.exit(DBI::dbDisconnect(connection), add = TRUE)
  stale_unit <- employer_unit("P1_employer:Acme:2024:STALE")
  resume_unit <- employer_unit("P1_employer:Acme:2024:RESUME")
  cursor <- list(last_index = "1", last_contribution_receipt_date = "2024-01-01")
  for (unit in list(stale_unit, resume_unit)) {
    prefix <- if (identical(unit$id, stale_unit$id)) "T6S-" else "T6R-"
    rows <- lapply(1:100, function(index) fixture_row(sprintf("%s%04d", prefix, index)))
    state <- list(
      unit = unit, last_indexes = cursor, seen_keysets = "seen-1",
      expected_count = 150L, rows_seen = 100L, complete = FALSE,
      reconcile_pass = 0L
    )
    page <- list(
      page_number = 0L, rows = rows, count = 150L, is_count_exact = TRUE,
      rows_collected = 100L, last_indexes = cursor, complete = FALSE
    )
    fec_api_checkpoint_page(connection, state, page)
  }
  DBI::dbExecute(
    connection,
    "UPDATE api_work_progress SET query_contract='stale-contract' WHERE unit_id=?",
    params = list(stale_unit$id)
  )
  restored <- fec_api_load_progress(connection, list(stale_unit, resume_unit))
  remaining <- DBI::dbGetQuery(
    connection,
    "SELECT unit_id FROM api_work_progress ORDER BY unit_id"
  )$unit_id
  stale_pages <- DBI::dbGetQuery(
    connection,
    "SELECT COUNT(*) AS n FROM api_page_checkpoints WHERE unit_id=?",
    params = list(stale_unit$id)
  )$n[[1L]]
  first_specs <- list()
  transport <- function(specs, api_key, concurrency) {
    lapply(specs, function(spec) {
      if (is.null(first_specs[[spec$id]])) first_specs[[spec$id]] <<- spec
      prefix <- if (identical(spec$id, stale_unit$id)) "T6S-" else "T6R-"
      cursor_value <- spec$params$last_index
      page <- if (is.null(cursor_value)) 1L else as.integer(cursor_value) + 1L
      first <- (page - 1L) * 100L + 1L
      last <- min(page * 100L, 150L)
      rows <- lapply(seq.int(first, last), function(index) {
        fixture_row(sprintf("%s%04d", prefix, index))
      })
      ok_page(rows, 150L, if (last < 150L) page else NULL)
    })
  }
  result <- fec_api_collect_work_units(
    list(stale_unit, resume_unit), api_key = "fixture-key", concurrency = 2L,
    transport = transport, sleep = no_sleep, jitter = no_jitter,
    initial_states = restored
  )
  stopifnot(
    "load_progress did not stop" = is.list(restored),
    "only the matching contract resumed" = identical(names(restored), resume_unit$id),
    "stale progress row was removed" = identical(remaining, resume_unit$id),
    "stale page checkpoints were removed" = identical(stale_pages, 0L),
    "stale unit restarts without a cursor" =
      is.null(first_specs[[stale_unit$id]]$params$last_index),
    "resumed unit continues from its cursor" =
      identical(first_specs[[resume_unit$id]]$params$last_index, "1"),
    "both units completed" = all(vapply(result$units, function(state) {
      isTRUE(state$complete)
    }, logical(1L))),
    "no reconciliation was needed" = identical(result$reconcile_count, 0L),
    "the stale unit was collected whole" =
      identical(as.integer(result$units[[stale_unit$id]]$rows_seen), 150L)
  )
})

# ---------------------------------------------------------------------------
# Negative control: with the pass budget at zero, t1's fixture must fail. This
# proves the reconciliation path, not the fixture, is what makes t1 pass.
# ---------------------------------------------------------------------------
run_test("t7", function() {
  Sys.setenv(MENTAT_RECONCILE_PASSES = "0")
  on.exit(Sys.unsetenv("MENTAT_RECONCILE_PASSES"), add = TRUE)
  moving_pass <<- 0L
  moving_checkpoints_at_pass_2 <<- NA_integer_
  connection <- fresh_database()
  on.exit(DBI::dbDisconnect(connection), add = TRUE)
  failure <- tryCatch({
    fec_api_collect_checkpointed(
      connection, list(moving_unit), api_key = "fixture-key", concurrency = 1L,
      transport = moving_transport, sleep = no_sleep, jitter = no_jitter,
      before_request = function(count) invisible(NULL),
      run_id = NULL, lookup_cache = store_cache(connection)
    )
    ""
  }, error = conditionMessage)
  stopifnot(
    "the same fixture now fails" =
      grepl("did not verify after 0 passes", failure, fixed = TRUE),
    "failure names the moved count" =
      grepl("count moved 250->251 after 250 rows", failure, fixed = TRUE),
    "no second walk was attempted" = identical(moving_pass, 1L),
    "nothing was stored" = count_rows(connection, "T1A-") == 0L
  )
})

# ---------------------------------------------------------------------------
# Determinism spot-check against a date-window union. The union is one synthetic
# page 0 holding the whole verified population, so the old exact page-1 set
# comparison was a structural false positive (t11 shows it failing on a correct
# snapshot). The check is now containment plus an unmoved exact count, and a
# FALSE is recorded, not fatal.
# ---------------------------------------------------------------------------
union_unit <- employer_unit("P1_employer:Acme:2024:UNION")
union_page_ids <- sprintf("W01-%04d", 1:100)

# Windows 1..22 hold 100 rows each and window 23 holds 7: 2,207 verified ids.
# The parent's own page 1 is window 1's first 100 rows, as the live split was.
union_transport_factory <- function(parent_count, first_page_ids, calls) {
  function(specs, api_key, concurrency) {
    lapply(specs, function(spec) {
      index <- window_index(spec$id)
      seen <- (calls[[spec$id]] %||% 0L) + 1L
      calls[[spec$id]] <- seen
      if (index == 0L) {
        return(ok_page(lapply(first_page_ids, fixture_row), parent_count, 1L))
      }
      size <- if (index <= 22L) 100L else if (index == 23L) 7L else 0L
      rows <- if (!size) list() else lapply(
        seq_len(size), function(row) fixture_row(sprintf("W%02d-%04d", index, row))
      )
      ok_page(rows, size, NULL)
    })
  }
}

# One page-1 response for the spot-check re-fetch.
spot_check_transport <- function(ids, count) {
  function(specs, api_key, concurrency) {
    lapply(specs, function(spec) ok_page(lapply(ids, fixture_row), count, NULL))
  }
}

union_result_cache <- NULL
union_result <- function() {
  if (is.null(union_result_cache)) {
    Sys.setenv(MENTAT_REQUEST_POOL_WIDTH = "6")
    on.exit(Sys.unsetenv("MENTAT_REQUEST_POOL_WIDTH"), add = TRUE)
    calls <- new.env(parent = emptyenv())
    union_result_cache <<- fec_api_collect_work_units(
      list(union_unit), api_key = "fixture-key", concurrency = 6L,
      transport = union_transport_factory(2207L, union_page_ids, calls),
      sleep = no_sleep, jitter = no_jitter
    )
  }
  union_result_cache
}

run_test("t8", function() {
  result <- union_result()
  state <- result$units[[union_unit$id]]
  check <- fec_api_determinism_spot_check(
    result, api_key = "fixture-key", concurrency = 1L,
    transport = spot_check_transport(union_page_ids, 2207L),
    sleep = no_sleep, jitter = no_jitter
  )
  stopifnot(
    "the split unit published a union" = isTRUE(state$complete),
    "the union is the whole verified population" =
      identical(as.integer(state$expected_count), 2207L),
    "the union is one synthetic page" = identical(length(state$pages), 1L),
    "the union page is flagged decomposed" = isTRUE(state$pages[[1L]]$decomposed),
    "the union page names its coverage method" =
      identical(state$pages[[1L]]$coverage_method, "exhaustive_date_windows"),
    "a date-window split records no decomposition event" =
      identical(length(result$decompositions %||% list()), 0L),
    "the split unit is still spot-checked" = is.list(check),
    "the union branch was taken" = identical(check$mode, "union_subset"),
    "a contained page 1 is not drift" = isTRUE(check$identical),
    "the detail is reportable" = identical(
      check$detail,
      "fresh page 1: 100 ids, 100 in union; count 2207 vs verified 2207"
    ),
    "the unit is named for the receipt" = identical(check$unit_id, union_unit$id)
  )
})

run_test("t9", function() {
  result <- union_result()
  drifted <- c(union_page_ids[1:99], "DRIFT-0001")
  check <- fec_api_determinism_spot_check(
    result, api_key = "fixture-key", concurrency = 1L,
    transport = spot_check_transport(drifted, 2207L),
    sleep = no_sleep, jitter = no_jitter
  )
  # The caller's own FALSE branch, lifted from the parsed function, must record
  # the drift instead of stopping the task.
  statements <- as.list(body(collect_crypto_api))
  branch <- Filter(function(statement) {
    grepl(
      "identical(determinism_value, FALSE)",
      paste(deparse(statement), collapse = " "),
      fixed = TRUE
    )
  }, statements)
  stopifnot("the caller still branches on a FALSE" = identical(length(branch), 1L))
  environment <- new.env(parent = globalenv())
  assign("determinism_value", check$identical, envir = environment)
  assign("determinism_unit", check$unit_id, envir = environment)
  assign("determinism_detail", check$detail, envir = environment)
  printed <- paste(
    capture.output(eval(branch[[1L]], envir = environment)),
    collapse = " "
  )
  source_text <- readLines(
    file.path(project_root, "R", "fec_api.R"), warn = FALSE
  )
  caller_text <- paste(deparse(body(collect_crypto_api)), collapse = " ")
  stopifnot(
    "a stray id is drift" = identical(check$identical, FALSE),
    "the union branch was taken" = identical(check$mode, "union_subset"),
    "the detail counts the stray id" = identical(
      check$detail,
      "fresh page 1: 100 ids, 99 in union; count 2207 vs verified 2207"
    ),
    "the fatal stop is gone from the source" = !any(grepl(
      "determinism spot-check returned a different sub_id set",
      source_text, fixed = TRUE
    )),
    "the caller cannot stop on a FALSE" = !grepl(
      "returned a different sub_id set", caller_text, fixed = TRUE
    ),
    "the caller records the drift" = grepl(
      "population changed after a verified walk", caller_text, fixed = TRUE
    ),
    "the receipt carries the detail" = grepl(
      "determinism_detail", caller_text, fixed = TRUE
    ),
    "the recorded line names the unit" = grepl(union_unit$id, printed, fixed = TRUE),
    "the recorded line carries the detail" = grepl(check$detail, printed, fixed = TRUE),
    "the recorded line keeps the snapshot verified" = grepl(
      "snapshot stands as verified at walk time", printed, fixed = TRUE
    )
  )
})

run_test("t10", function() {
  normal_unit <- employer_unit("P1_employer:Acme:2024:NORMAL")
  normal_ids <- sprintf("N1-%04d", 1:100)
  transport <- function(specs, api_key, concurrency) {
    lapply(specs, function(spec) {
      ok_page(lapply(normal_ids, fixture_row), 100L, NULL)
    })
  }
  result <- fec_api_collect_work_units(
    list(normal_unit), api_key = "fixture-key", concurrency = 1L,
    transport = transport, sleep = no_sleep, jitter = no_jitter
  )
  state <- result$units[[normal_unit$id]]
  check <- fec_api_determinism_spot_check(
    result, api_key = "fixture-key", concurrency = 1L,
    transport = transport, sleep = no_sleep, jitter = no_jitter
  )
  stopifnot(
    "the unit walked normally" = isTRUE(state$complete),
    "no union page was written" = !isTRUE(state$pages[[1L]]$decomposed),
    # An ordinary page 1 is also numbered 0, so page_number cannot identify a
    # union page; only the decomposed/coverage_method flags can.
    "an ordinary page 1 is numbered 0" =
      identical(as.integer(state$pages[[1L]]$page_number), 0L),
    "the exact page-1 comparison is kept" = identical(check$mode, "page_one"),
    "an unchanged page 1 passes" = isTRUE(check$identical),
    "the detail is reportable" = identical(
      check$detail,
      "fresh page 1: 100 ids, 100 matching the stored page 1 of 100; count 100 vs verified 100"
    )
  )
})

# ---------------------------------------------------------------------------
# Negative control: the pre-change comparison, run inline on t8's fixture, must
# fail on a snapshot the collector verified. That is why this change exists.
# ---------------------------------------------------------------------------
run_test("t11", function() {
  result <- union_result()
  state <- result$units[[union_unit$id]]
  expected <- vapply(state$pages[[1L]]$rows, function(row) {
    row_scalar(row, "sub_id", "")
  }, character(1L))
  actual <- union_page_ids
  pre_change <- identical(sort(expected), sort(actual))
  check <- fec_api_determinism_spot_check(
    result, api_key = "fixture-key", concurrency = 1L,
    transport = spot_check_transport(union_page_ids, 2207L),
    sleep = no_sleep, jitter = no_jitter
  )
  stopifnot(
    "the union page holds the whole population" = identical(length(expected), 2207L),
    "the fresh page 1 holds 100 ids" = identical(length(actual), 100L),
    "every fresh id is inside the union" = all(actual %in% expected),
    "the pre-change comparison fails on a correct snapshot" =
      identical(pre_change, FALSE),
    "the new check passes on the same fixture" = isTRUE(check$identical)
  )
})

# ---------------------------------------------------------------------------
# openFEC answers a large result set with pagination.is_count_exact FALSE and an
# ESTIMATED count. Paul's rulings: "It should always be complete, no
# misrepresentation" and "API count can change, it should always be verified".
# An estimated count is a refusal to count, not a verdict on the data, so the
# collector verifies the population another way - exhaustive date windows, and,
# when the parent will not count either, the windows' own exact counts - instead
# of failing the task. Nothing unverified is stored either way.
# ---------------------------------------------------------------------------
estimated_unit <- employer_unit("P1_employer:Acme:2024:ESTIMATED")

estimated_page <- function(rows, count, exact, cursor = NULL) {
  page <- ok_page(rows, count, cursor)
  page$json$pagination$is_count_exact <- exact
  page
}

# Windows 1..21 hold 100 rows each and window 22 holds 80: 2,180 verified ids.
# Windows 23..26 are empty.
estimated_windows <- function(index, seen) {
  size <- if (index <= 21L) 100L else if (index == 22L) 80L else 0L
  list(size = size, count = size, exact = TRUE)
}

# parent_count(seen) and window_count(index, seen) report what the API says on
# the seen-th request for that query, so a probe can differ from the walk. The
# parent's own page 1 is window 1's rows, as a live split's page 1 would be, so
# no row observed before the split can go missing from the union.
estimated_transport_factory <- function(prefix, parent_count, window_count, calls, cursors) {
  function(specs, api_key, concurrency) {
    lapply(specs, function(spec) {
      index <- window_index(spec$id)
      seen <- (calls[[spec$id]] %||% 0L) + 1L
      calls[[spec$id]] <- seen
      if (!is.null(spec$params$last_index)) {
        cursors[[spec$id]] <- (cursors[[spec$id]] %||% 0L) + 1L
      }
      if (index == 0L) {
        reported <- parent_count(seen)
        rows <- lapply(1:100, function(row) fixture_row(sprintf("%s01-%04d", prefix, row)))
        return(estimated_page(rows, reported$count, reported$exact, 1L))
      }
      reported <- window_count(index, seen)
      rows <- if (!reported$size) list() else lapply(
        seq_len(reported$size),
        function(row) fixture_row(sprintf("%s%02d-%04d", prefix, index, row))
      )
      estimated_page(rows, reported$count, reported$exact, NULL)
    })
  }
}

estimated_per_window <- function(calls) {
  vapply(sprintf("%s::window::%02d", estimated_unit$id, 1:26),
    function(id) calls[[id]] %||% 0L, integer(1L))
}

run_test("t12", function() {
  calls <- new.env(parent = emptyenv())
  cursors <- new.env(parent = emptyenv())
  result <- fec_api_collect_work_units(
    list(estimated_unit), api_key = "fixture-key", concurrency = 6L,
    transport = estimated_transport_factory(
      "Q",
      function(seen) {
        if (seen == 1L) list(count = 2200L, exact = FALSE) else list(count = 2180L, exact = TRUE)
      },
      estimated_windows, calls, cursors
    ),
    sleep = no_sleep, jitter = no_jitter
  )
  state <- result$units[[estimated_unit$id]]
  per_window <- estimated_per_window(calls)
  stopifnot(
    "an estimated count no longer fails the unit" = isTRUE(state$complete),
    "coverage is verified" = isTRUE(state$coverage_verified),
    "the union names its coverage method" =
      identical(state$pages[[1L]]$coverage_method, "exhaustive_date_windows"),
    "the union is the whole population" =
      identical(as.integer(state$expected_count), 2180L),
    "rows are the whole union" = identical(as.integer(state$rows_seen), 2180L),
    "the union page holds every row" = identical(length(state$pages[[1L]]$rows), 2180L),
    "no reconciliation pass was needed" = identical(result$reconcile_count, 0L),
    "the parent was asked twice: the estimate and one fresh proof" =
      identical(calls[[estimated_unit$id]], 2L),
    "the parent was never re-walked as a cursor chain" =
      identical(cursors[[estimated_unit$id]] %||% 0L, 0L),
    "every window was walked exactly once" = all(per_window == 1L),
    "1 estimate + 26 windows + 1 parent proof" = identical(result$request_count, 28L)
  )
})

run_test("t13", function() {
  calls <- new.env(parent = emptyenv())
  cursors <- new.env(parent = emptyenv())
  result <- fec_api_collect_work_units(
    list(estimated_unit), api_key = "fixture-key", concurrency = 6L,
    transport = estimated_transport_factory(
      "R",
      function(seen) list(count = 2190L + seen, exact = FALSE),
      estimated_windows, calls, cursors
    ),
    sleep = no_sleep, jitter = no_jitter
  )
  state <- result$units[[estimated_unit$id]]
  per_window <- estimated_per_window(calls)
  stopifnot(
    "a parent that will not count no longer fails the unit" = isTRUE(state$complete),
    "coverage is verified" = isTRUE(state$coverage_verified),
    "the population is the sum of the window counts" =
      identical(as.integer(state$expected_count), 2180L),
    "no reconciliation pass was needed" = identical(result$reconcile_count, 0L),
    "the log names the window-sum mode" = any(grepl(
      "verified against the sum of 26 exact window counts (parent count estimated)",
      state$reconcile_log, fixed = TRUE
    )),
    "the estimated parent count was never treated as the population" =
      !any(grepl("fresh parent count", state$reconcile_log, fixed = TRUE)),
    "the parent was asked twice and never re-walked" =
      identical(calls[[estimated_unit$id]], 2L) &&
      identical(cursors[[estimated_unit$id]] %||% 0L, 0L),
    "every window was walked once and proved once" = all(per_window == 2L),
    "1 estimate + 26 windows + 1 parent probe + 26 window proofs" =
      identical(result$request_count, 54L)
  )
})

run_test("t14", function() {
  calls <- new.env(parent = emptyenv())
  cursors <- new.env(parent = emptyenv())
  failure <- tryCatch({
    fec_api_collect_work_units(
      list(estimated_unit), api_key = "fixture-key", concurrency = 6L,
      transport = estimated_transport_factory(
        "S",
        function(seen) list(count = 2190L + seen, exact = FALSE),
        function(index, seen) {
          reported <- estimated_windows(index, seen)
          # Window 22 walked cleanly but will not count itself afterwards.
          if (index == 22L && seen >= 2L) reported$exact <- FALSE
          reported
        },
        calls, cursors
      ),
      sleep = no_sleep, jitter = no_jitter
    )
    ""
  }, error = conditionMessage)
  stopifnot(
    "the window that would not count is named" =
      grepl(paste0(estimated_unit$id, "::window::22"), failure, fixed = TRUE),
    "the failure says that window's count was estimated" =
      grepl("reported an estimated pagination.count", failure, fixed = TRUE),
    "the failure names the population that cannot be verified" =
      grepl(paste0("population of ", estimated_unit$id), failure, fixed = TRUE),
    "the failure states nothing was stored" =
      grepl("Nothing unverified was stored", failure, fixed = TRUE)
  )
})

run_test("t15", function() {
  # Negative control for t12's guard: a unit that already carries query filters
  # has no date windows to split into, so the exact-count contract still fires
  # and the response is still checkpointed for diagnosis.
  filtered_unit <- employer_unit("P1_employer:Acme:2024:FILTERED")
  filtered_unit$query_filters <- list(min_date = "01/01/2024", max_date = "12/31/2024")
  saved <- list()
  failure <- tryCatch({
    fec_api_collect_work_units(
      list(filtered_unit), api_key = "fixture-key", concurrency = 1L,
      transport = function(specs, api_key, concurrency) {
        lapply(specs, function(spec) {
          estimated_page(
            lapply(1:10, function(row) fixture_row(sprintf("U1-%04d", row))),
            500L, FALSE, NULL
          )
        })
      },
      sleep = no_sleep, jitter = no_jitter,
      page_callback = function(state, page) saved[[length(saved) + 1L]] <<- page
    )
    ""
  }, error = conditionMessage)
  stopifnot(
    "a filtered unit has no date windows to split into" =
      identical(length(fec_api_date_windows(filtered_unit)), 0L),
    "the exact-count contract still fires" =
      grepl("requires an exact nonnegative pagination.count", failure, fixed = TRUE),
    "the failure names the unit" = grepl(filtered_unit$id, failure, fixed = TRUE),
    "exactly one response was checkpointed for diagnosis" = identical(length(saved), 1L),
    "the checkpointed page is not complete" = !isTRUE(saved[[1L]]$complete),
    "the checkpoint names the validation failure" =
      identical(saved[[1L]]$validation_failure, "exact_count_missing")
  )
})

run_test("t16", function() {
  calls <- new.env(parent = emptyenv())
  cursors <- new.env(parent = emptyenv())
  recollected <- character()
  result <- fec_api_collect_work_units(
    list(estimated_unit), api_key = "fixture-key", concurrency = 6L,
    transport = estimated_transport_factory(
      "V",
      function(seen) list(count = 2190L + seen, exact = FALSE),
      function(index, seen) {
        # Window 22 was walked while it held 74 rows and grew to 80 afterwards.
        if (index == 22L && seen == 1L) {
          return(list(size = 74L, count = 74L, exact = TRUE))
        }
        estimated_windows(index, seen)
      },
      calls, cursors
    ),
    sleep = no_sleep, jitter = no_jitter,
    reset_callback = function(state) recollected <<- c(recollected, state$unit$id)
  )
  state <- result$units[[estimated_unit$id]]
  per_window <- estimated_per_window(calls)
  stopifnot(
    "the union published after the second pass" = isTRUE(state$complete),
    "the population is the sum of the window counts" =
      identical(as.integer(state$expected_count), 2180L),
    "exactly one reconciliation pass" = identical(result$reconcile_count, 1L),
    "the pass reason names the window-count sum" = any(grepl(
      "windows hold 2174 rows against a window-count sum of 2180",
      state$reconcile_log, fixed = TRUE
    )),
    "the second pass verified against the window counts" = any(grepl(
      "verified against the sum of 26 exact window counts (parent count estimated)",
      state$reconcile_log, fixed = TRUE
    )),
    "only the stale window was re-collected" =
      identical(recollected, paste0(estimated_unit$id, "::window::22")),
    "the stale window was asked twice more than the others" =
      identical(per_window[[22L]], 4L) && all(per_window[-22L] == 3L),
    "1 estimate + 26 windows + 2 x (1 parent probe + 26 proofs) + 1 re-walk" =
      identical(result$request_count, 82L)
  )
})

passed <- sum(vapply(results, function(value) identical(value, "PASS"), logical(1L)))
cat(sprintf(
  "test_fec_api_reconcile: %s (%d/%d)\n",
  if (passed == length(results)) "PASS" else "FAIL", passed, length(results)
))
if (passed != length(results)) quit(status = 1L)

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

missing_key <- tryCatch(
  {
    fec_api_key("")
    ""
  },
  error = conditionMessage
)
stopifnot(
  grepl("FEC_API_KEY is unset", missing_key, fixed = TRUE),
  grepl("https://api.data.gov/signup", missing_key, fixed = TRUE)
)

reset_database <- tempfile(fileext = ".sqlite")
reset_connection <- DBI::dbConnect(RSQLite::SQLite(), reset_database)
init_schema(reset_connection)
DBI::dbExecute(
  reset_connection,
  paste(
    "INSERT INTO scrape_progress",
    "(company, cycle, variant_set, last_page, records_total, updated_at)",
    "VALUES ('Alpha', 2026, 'legacy-alpha', 1, 10, 'old'),",
    "('Beta', 2026, 'legacy-beta', 1, 20, 'old')"
  )
)
DBI::dbExecute(
  reset_connection,
  paste(
    "INSERT INTO api_work_progress",
    "(unit_id, company, cycle, kind, last_page, rows_seen, records_total,",
    "fetched_complete, stored_complete, updated_at)",
    "VALUES ('api-alpha', 'Alpha', 2026, 'employer', 1, 10, 10, 1, 1, 'old'),",
    "('api-beta', 'Beta', 2026, 'employer', 1, 20, 20, 1, 1, 'old')"
  )
)
DBI::dbExecute(
  reset_connection,
  paste(
    "INSERT INTO api_page_checkpoints (unit_id, page, payload_json)",
    "VALUES ('api-alpha', 1, '{}'), ('api-beta', 1, '{}')"
  )
)
reset_count <- reset_api_progress(
  reset_connection,
  list(list(company = "Alpha", cycle = 2026L))
)
stopifnot(
  reset_count == 1L,
  DBI::dbGetQuery(
    reset_connection,
    "SELECT COUNT(*) AS n FROM scrape_progress WHERE company='Alpha'"
  )$n[[1L]] == 0L,
  DBI::dbGetQuery(
    reset_connection,
    "SELECT COUNT(*) AS n FROM scrape_progress WHERE company='Beta'"
  )$n[[1L]] == 1L,
  DBI::dbGetQuery(
    reset_connection,
    "SELECT COUNT(*) AS n FROM api_work_progress WHERE unit_id='api-alpha'"
  )$n[[1L]] == 0L,
  DBI::dbGetQuery(
    reset_connection,
    "SELECT COUNT(*) AS n FROM api_work_progress WHERE unit_id='api-beta'"
  )$n[[1L]] == 1L,
  DBI::dbGetQuery(
    reset_connection,
    "SELECT COUNT(*) AS n FROM api_page_checkpoints WHERE unit_id='api-alpha'"
  )$n[[1L]] == 0L,
  DBI::dbGetQuery(
    reset_connection,
    "SELECT COUNT(*) AS n FROM api_page_checkpoints WHERE unit_id='api-beta'"
  )$n[[1L]] == 1L
)
DBI::dbDisconnect(reset_connection)
unlink(reset_database)

corporate_database <- tempfile(fileext = ".sqlite")
corporate_connection <- DBI::dbConnect(RSQLite::SQLite(), corporate_database)
init_schema(corporate_connection)
corporate_unit <- list(
  id = "P2_company_name:Coinbase:2026:Coinbase",
  kind = "corporate",
  industry = "Crypto",
  company = "Coinbase",
  cycle = 2026L,
  variants = "Coinbase",
  company_variants = "Coinbase",
  phase = "P2_company_name",
  query_field = "contributor_name",
  query_filters = list(),
  exclusions = character()
)
corporate_api_fixture <- list(list(
  sub_id = "corporate-api-amendment",
  transaction_id = "TX-CORPORATE-API",
  original_sub_id = "corporate-api-original",
  amendment_indicator = "A",
  report_year = 2026L,
  report_type = "Q1",
  file_number = 4242L,
  pdf_url = "https://docquery.fec.gov/cgi-bin/fecimg/?424200000001",
  load_date = "2026-07-19T12:34:56",
  contributor_name = "COINBASE, INC.",
  contributor_employer = "COINBASE",
  contributor_occupation = "CORPORATION",
  contributor_city = "SAN FRANCISCO",
  contributor_state = "CA",
  contributor_zip = "94104",
  entity_type = "ORG",
  committee_id = "C00835959",
  committee_name = "FAIRSHAKE",
  recipient_committee_type = "O",
  recipient_committee_designation = "U",
  contribution_receipt_date = "2025-06-01",
  contribution_receipt_amount = 25000000,
  two_year_transaction_period = 2026L
))
corporate_fixture_without_version <- corporate_api_fixture
for (field in c(
  "transaction_id", "original_sub_id", "amendment_indicator", "report_year",
  "report_type", "file_number", "pdf_url", "load_date"
)) {
  corporate_fixture_without_version[[1L]][[field]] <- NULL
}
corporate_records_without_version <- fec_api_corporate_records(
  corporate_connection,
  corporate_unit,
  corporate_fixture_without_version
)
checkpoint_corporate_page(
  corporate_connection,
  corporate_records_without_version,
  company = "Coinbase",
  cycle = 2026L,
  variant_set = corporate_unit$id,
  page = 0L,
  records_total = 1L
)
corporate_records <- fec_api_corporate_records(
  corporate_connection,
  corporate_unit,
  corporate_api_fixture
)
checkpoint_corporate_page(
  corporate_connection,
  corporate_records,
  company = "Coinbase",
  cycle = 2026L,
  variant_set = corporate_unit$id,
  page = 0L,
  records_total = 1L
)
stored_corporate <- DBI::dbGetQuery(
  corporate_connection,
  paste(
    "SELECT transaction_id, original_sub_id, amendment_indicator, report_year,",
    "report_type, file_num, pdf_url, load_date FROM corporate_giving",
    "WHERE sub_id='corporate-api-amendment'"
  )
)
stopifnot(
  nrow(stored_corporate) == 1L,
  stored_corporate$transaction_id[[1L]] == "TX-CORPORATE-API",
  stored_corporate$original_sub_id[[1L]] == "corporate-api-original",
  stored_corporate$amendment_indicator[[1L]] == "A",
  stored_corporate$report_year[[1L]] == 2026L,
  stored_corporate$report_type[[1L]] == "Q1",
  stored_corporate$file_num[[1L]] == 4242L,
  stored_corporate$pdf_url[[1L]] ==
    "https://docquery.fec.gov/cgi-bin/fecimg/?424200000001",
  stored_corporate$load_date[[1L]] == "2026-07-19T12:34:56"
)
DBI::dbDisconnect(corporate_connection)
unlink(corporate_database)

unit <- list(
  id = "P1_employer:Binance:2024:BINANCE",
  kind = "employer",
  industry = "Crypto",
  company = "BINANCE US",
  cycle = 2024L,
  variants = "Binance",
  company_variants = c("BINANCE US", "Binance"),
  phase = "P1_employer",
  query_field = "contributor_employer",
  query_filters = list(),
  exclusions = character()
)
stopifnot(identical(eval(formals(collect_crypto_api)$phase2_names), FALSE))

zero_database <- tempfile(fileext = ".sqlite")
zero_connection <- DBI::dbConnect(RSQLite::SQLite(), zero_database)
init_schema(zero_connection)
zero_page <- list(
  page_number = 0L,
  count = 0L,
  rows = list(),
  rows_collected = 0L,
  last_indexes = NULL,
  complete = TRUE
)
for (zero_unit in list(unit, corporate_unit)) {
  fec_api_store_page(
    zero_connection,
    state = list(unit = zero_unit, rows_seen = 0L),
    page = zero_page,
    match_overrides = company_match_overrides_data(zero_connection),
    trusted_name_keys = character(),
    home_states = company_home_states_data(zero_connection)
  )
}
zero_checkpoints <- DBI::dbGetQuery(
  zero_connection,
  paste(
    "SELECT company, cycle, variant_set, records_total, zero_verified",
    "FROM scrape_progress ORDER BY company, cycle, variant_set"
  )
)
stopifnot(
  nrow(zero_checkpoints) == 2L,
  all(zero_checkpoints$records_total == 0L),
  all(zero_checkpoints$zero_verified == 1L)
)
DBI::dbDisconnect(zero_connection)
unlink(zero_database)

pages <- list(
  list(
    results = list(
      list(sub_id = "B", contribution_receipt_date = "2024-04-02"),
      list(sub_id = "A", contribution_receipt_date = "2024-04-03")
    ),
    pagination = list(is_count_exact = TRUE,
      count = 3L,
      last_indexes = list(
        last_index = "cursor-1",
        last_contribution_receipt_date = "2024-04-02"
      )
    )
  ),
  list(
    results = list(
      list(sub_id = "C", contribution_receipt_date = "2024-04-01")
    ),
    pagination = list(is_count_exact = TRUE,
      count = 3L,
      last_indexes = list(
        last_index = "cursor-2",
        last_contribution_receipt_date = "2024-04-01"
      )
    )
  )
)
request_specs <- list()
fixture_transport <- function(specs, api_key, concurrency) {
  stopifnot(identical(api_key, "fixture-key"), concurrency == 2L)
  output <- vector("list", length(specs))
  for (index in seq_along(specs)) {
    request_specs[[length(request_specs) + 1L]] <<- specs[[index]]
    page_index <- length(request_specs)
    output[[index]] <- list(status = 200L, headers = list(), json = pages[[page_index]])
  }
  output
}
result <- fec_api_collect_work_units(
  list(unit),
  api_key = "fixture-key",
  concurrency = 2L,
  transport = fixture_transport,
  sleep = function(seconds) stop("unexpected sleep"),
  jitter = function(...) 0
)
state <- result$units[[unit$id]]
stopifnot(
  result$request_count == 2L,
  result$rows == 3L,
  isTRUE(state$complete),
  length(state$pages) == 2L,
  identical(
    vapply(state$pages[[1L]]$rows, function(row) row$sub_id, character(1L)),
    c("A", "B")
  ),
  identical(request_specs[[2L]]$params$last_index, "cursor-1"),
  identical(
    request_specs[[2L]]$params$last_contribution_receipt_date,
    "2024-04-02"
  ),
  all(!vapply(
    request_specs,
    function(spec) "page" %in% names(spec$params),
    logical(1L)
  ))
)

# These fixtures each serve one fixed page list, so reconciliation is switched
# off for them on purpose: they assert the terminal refusal - a population that
# never verifies is never certified and nothing unverified is stored. The
# re-walk that resolves a genuinely moving count lives in
# test_fec_api_reconcile.R.
Sys.setenv(MENTAT_RECONCILE_PASSES = "0")
null_cursor_calls <- 0L
null_cursor_transport <- function(specs, api_key, concurrency) {
  null_cursor_calls <<- null_cursor_calls + length(specs)
  lapply(specs, function(spec) list(
    status = 200L,
    headers = list(),
    json = list(
      results = list(list(sub_id = "ONLY")),
      pagination = list(is_count_exact = TRUE, count = 999L, last_indexes = NULL)
    )
  ))
}
null_cursor_failure <- tryCatch(
  {
    fec_api_collect_work_units(
      list(unit),
      api_key = "fixture-key",
      concurrency = 1L,
      transport = null_cursor_transport,
      sleep = function(seconds) stop("unexpected sleep"),
      jitter = function(...) 0
    )
    ""
  },
  error = conditionMessage
)
stopifnot(
  null_cursor_calls == 1L,
  grepl("did not verify after 0 passes", null_cursor_failure, fixed = TRUE),
  grepl("shortfall 1 of 999", null_cursor_failure, fixed = TRUE),
  grepl("Nothing unverified was stored", null_cursor_failure, fixed = TRUE)
)

moving_count_pages <- list(
  list(
    results = list(list(sub_id = "MOVING-1"), list(sub_id = "MOVING-2")),
    pagination = list(is_count_exact = TRUE,
      count = 3L,
      last_indexes = list(
        last_index = "moving-cursor",
        last_contribution_receipt_date = "2024-04-02"
      )
    )
  ),
  list(
    results = list(list(sub_id = "MOVING-3")),
    pagination = list(is_count_exact = TRUE, count = 4L, last_indexes = NULL)
  )
)
moving_count_calls <- 0L
moving_count_transport <- function(specs, api_key, concurrency) {
  moving_count_calls <<- moving_count_calls + length(specs)
  list(list(
    status = 200L,
    headers = list(),
    json = moving_count_pages[[moving_count_calls]]
  ))
}
moving_count_saved <- list()
moving_count_failure <- tryCatch(suppressWarnings(fec_api_collect_work_units(
  list(unit), api_key = "fixture-key", concurrency = 1L,
  transport = moving_count_transport, sleep = function(seconds) stop("unexpected sleep"),
  jitter = function(...) 0,
  page_callback = function(state, page) { moving_count_saved[[length(moving_count_saved)+1L]] <<- page }
)), error = conditionMessage)
stopifnot(grepl("did not verify after 0 passes", moving_count_failure, fixed = TRUE),
          grepl("count moved ", moving_count_failure, fixed = TRUE),
          !isTRUE(tail(moving_count_saved, 1)[[1]]$complete),
          identical(tail(moving_count_saved, 1)[[1]]$validation_failure, "population_changed_during_collection"))

moving_count_cursor_pages <- list(
  list(
    results = list(
      list(sub_id = "MOVING-CURSOR-1"),
      list(sub_id = "MOVING-CURSOR-2")
    ),
    pagination = list(is_count_exact = TRUE,
      count = 3L,
      last_indexes = list(
        last_index = "moving-cursor-1",
        last_contribution_receipt_date = "2024-04-03"
      )
    )
  ),
  list(
    results = list(list(sub_id = "MOVING-CURSOR-3")),
    pagination = list(is_count_exact = TRUE,
      count = 4L,
      last_indexes = list(
        last_index = "moving-cursor-2",
        last_contribution_receipt_date = "2024-04-02"
      )
    )
  ),
  list(
    results = list(list(sub_id = "MOVING-CURSOR-4")),
    pagination = list(is_count_exact = TRUE, count = 4L, last_indexes = NULL)
  )
)
moving_count_cursor_calls <- 0L
moving_count_cursor_transport <- function(specs, api_key, concurrency) {
  moving_count_cursor_calls <<- moving_count_cursor_calls + length(specs)
  list(list(
    status = 200L,
    headers = list(),
    json = moving_count_cursor_pages[[moving_count_cursor_calls]]
  ))
}
moving_count_cursor_saved <- list()
moving_count_cursor_failure <- tryCatch(suppressWarnings(fec_api_collect_work_units(
  list(unit), api_key = "fixture-key", concurrency = 1L,
  transport = moving_count_cursor_transport, sleep = function(seconds) stop("unexpected sleep"),
  jitter = function(...) 0,
  page_callback = function(state, page) { moving_count_cursor_saved[[length(moving_count_cursor_saved)+1L]] <<- page }
)), error = conditionMessage)
stopifnot(grepl("did not verify after 0 passes", moving_count_cursor_failure, fixed = TRUE),
          grepl("count moved ", moving_count_cursor_failure, fixed = TRUE),
          !isTRUE(tail(moving_count_cursor_saved, 1)[[1]]$complete),
          identical(tail(moving_count_cursor_saved, 1)[[1]]$validation_failure, "population_changed_during_collection"))

downward_count_pages <- list(
  list(
    results = list(list(sub_id = "DOWN-1"), list(sub_id = "DOWN-2")),
    pagination = list(is_count_exact = TRUE,
      count = 5L,
      last_indexes = list(
        last_index = "down-cursor-1",
        last_contribution_receipt_date = "2024-04-03"
      )
    )
  ),
  list(
    results = list(list(sub_id = "DOWN-3")),
    pagination = list(is_count_exact = TRUE,
      count = 3L,
      last_indexes = list(
        last_index = "down-cursor-2",
        last_contribution_receipt_date = "2024-04-02"
      )
    )
  ),
  list(
    results = list(list(sub_id = "DOWN-4"), list(sub_id = "DOWN-5")),
    pagination = list(is_count_exact = TRUE, count = 3L, last_indexes = NULL)
  )
)
downward_count_calls <- 0L
downward_count_transport <- function(specs, api_key, concurrency) {
  downward_count_calls <<- downward_count_calls + length(specs)
  list(list(
    status = 200L,
    headers = list(),
    json = downward_count_pages[[downward_count_calls]]
  ))
}
downward_count_saved <- list()
downward_count_failure <- tryCatch(suppressWarnings(fec_api_collect_work_units(
  list(unit), api_key = "fixture-key", concurrency = 1L,
  transport = downward_count_transport, sleep = function(seconds) stop("unexpected sleep"),
  jitter = function(...) 0,
  page_callback = function(state, page) { downward_count_saved[[length(downward_count_saved)+1L]] <<- page }
)), error = conditionMessage)
stopifnot(grepl("did not verify after 0 passes", downward_count_failure, fixed = TRUE),
          grepl("count moved ", downward_count_failure, fixed = TRUE),
          !isTRUE(tail(downward_count_saved, 1)[[1]]$complete),
          identical(tail(downward_count_saved, 1)[[1]]$validation_failure, "population_changed_during_collection"))

terminal_downward_pages <- list(
  list(
    results = list(list(sub_id = "TERM-DOWN-1"), list(sub_id = "TERM-DOWN-2")),
    pagination = list(is_count_exact = TRUE,
      count = 3L,
      last_indexes = list(
        last_index = "terminal-down-cursor",
        last_contribution_receipt_date = "2024-04-02"
      )
    )
  ),
  list(
    results = list(list(sub_id = "TERM-DOWN-3")),
    pagination = list(is_count_exact = TRUE, count = 2L, last_indexes = NULL)
  )
)
terminal_downward_calls <- 0L
terminal_downward_transport <- function(specs, api_key, concurrency) {
  terminal_downward_calls <<- terminal_downward_calls + length(specs)
  list(list(
    status = 200L,
    headers = list(),
    json = terminal_downward_pages[[terminal_downward_calls]]
  ))
}
terminal_downward_saved <- list()
terminal_downward_failure <- tryCatch(suppressWarnings(fec_api_collect_work_units(
  list(unit), api_key = "fixture-key", concurrency = 1L,
  transport = terminal_downward_transport, sleep = function(seconds) stop("unexpected sleep"),
  jitter = function(...) 0,
  page_callback = function(state, page) { terminal_downward_saved[[length(terminal_downward_saved)+1L]] <<- page }
)), error = conditionMessage)
stopifnot(grepl("did not verify after 0 passes", terminal_downward_failure, fixed = TRUE),
          grepl("count moved ", terminal_downward_failure, fixed = TRUE),
          !isTRUE(tail(terminal_downward_saved, 1)[[1]]$complete),
          identical(tail(terminal_downward_saved, 1)[[1]]$validation_failure, "population_changed_during_collection"))

terminal_current_count_pages <- list(
  list(
    results = list(
      list(sub_id = "CURRENT-DOWN-1"),
      list(sub_id = "CURRENT-DOWN-2")
    ),
    pagination = list(is_count_exact = TRUE,
      count = 5L,
      last_indexes = list(
        last_index = "current-down-cursor",
        last_contribution_receipt_date = "2024-04-02"
      )
    )
  ),
  list(
    results = list(list(sub_id = "CURRENT-DOWN-3")),
    pagination = list(is_count_exact = TRUE, count = 3L, last_indexes = NULL)
  )
)
terminal_current_count_calls <- 0L
terminal_current_count_transport <- function(specs, api_key, concurrency) {
  terminal_current_count_calls <<- terminal_current_count_calls + length(specs)
  list(list(
    status = 200L,
    headers = list(),
    json = terminal_current_count_pages[[terminal_current_count_calls]]
  ))
}
terminal_current_count_saved <- list()
terminal_current_count_failure <- tryCatch(suppressWarnings(fec_api_collect_work_units(
  list(unit), api_key = "fixture-key", concurrency = 1L,
  transport = terminal_current_count_transport, sleep = function(seconds) stop("unexpected sleep"),
  jitter = function(...) 0,
  page_callback = function(state, page) { terminal_current_count_saved[[length(terminal_current_count_saved)+1L]] <<- page }
)), error = conditionMessage)
stopifnot(grepl("did not verify after 0 passes", terminal_current_count_failure, fixed = TRUE),
          grepl("count moved ", terminal_current_count_failure, fixed = TRUE),
          !isTRUE(tail(terminal_current_count_saved, 1)[[1]]$complete),
          identical(tail(terminal_current_count_saved, 1)[[1]]$validation_failure, "population_changed_during_collection"))
Sys.unsetenv("MENTAT_RECONCILE_PASSES")

partial_cursor_failure <- tryCatch(
  {
    fec_api_request_spec(unit, list(
      last_index = "cursor-null-date",
      last_contribution_receipt_date = NULL
    ))
    ""
  },
  error = conditionMessage
)
stopifnot(
  grepl("cursor is partial", partial_cursor_failure, fixed = TRUE),
  grepl("last_contribution_receipt_date", partial_cursor_failure, fixed = TRUE)
)

cycle_calls <- 0L
cycle_transport <- function(specs, api_key, concurrency) {
  signatures <- c("A", "B", "A")
  dates <- c("2024-04-03", "2024-04-02", "2024-04-03")
  cycle_calls <<- cycle_calls + 1L
  list(list(
    status = 200L,
    headers = list(),
    json = list(
      results = list(list(sub_id = paste0("CYCLE-", cycle_calls))),
      pagination = list(is_count_exact = TRUE,
        count = 10L,
        last_indexes = list(
          last_index = signatures[[cycle_calls]],
          last_contribution_receipt_date = dates[[cycle_calls]]
        )
      )
    )
  ))
}
cycle_failure <- tryCatch(
  {
    fec_api_collect_work_units(
      list(unit),
      api_key = "fixture-key",
      concurrency = 1L,
      transport = cycle_transport,
      sleep = function(seconds) stop("unexpected sleep"),
      jitter = function(...) 0
    )
    ""
  },
  error = conditionMessage
)
stopifnot(
  cycle_calls == 3L,
  grepl("cycled to a seen keyset", cycle_failure, fixed = TRUE)
)

page_ceiling_calls <- 0L
page_ceiling_failure <- local({
  previous_ceiling <- FEC_API_MAX_PAGES_PER_UNIT
  on.exit(FEC_API_MAX_PAGES_PER_UNIT <<- previous_ceiling, add = TRUE)
  FEC_API_MAX_PAGES_PER_UNIT <<- 2L
  transport <- function(specs, api_key, concurrency) {
    page_ceiling_calls <<- page_ceiling_calls + 1L
    list(list(
      status = 200L,
      headers = list(),
      json = list(
        results = list(list(sub_id = paste0("CEILING-", page_ceiling_calls))),
        pagination = list(is_count_exact = TRUE,
          count = 10L,
          last_indexes = list(
            last_index = paste0("ceiling-", page_ceiling_calls),
            last_contribution_receipt_date = "2024-04-01"
          )
        )
      )
    ))
  }
  tryCatch(
    {
      fec_api_collect_work_units(
        list(unit),
        api_key = "fixture-key",
        concurrency = 1L,
        transport = transport,
        sleep = function(seconds) stop("unexpected sleep"),
        jitter = function(...) 0
      )
      ""
    },
    error = conditionMessage
  )
})
stopifnot(
  page_ceiling_calls == 3L,
  grepl("page ceiling exceeded", page_ceiling_failure, fixed = TRUE)
)

retry_after_calls <- 0L
retry_after_sleeps <- numeric()
retry_after_503 <- function(specs, api_key, concurrency) {
  retry_after_calls <<- retry_after_calls + 1L
  if (retry_after_calls == 1L) {
    return(list(list(
      status = 503L,
      headers = list(`Retry-After` = "999"),
      body = ""
    )))
  }
  list(list(status = 200L, headers = list(), body = "{}"))
}
capped_retry <- fec_api_fetch_with_retry(
  list(fec_api_request_spec(unit)),
  api_key = "fixture-key",
  concurrency = 1L,
  transport = retry_after_503,
  max_attempts = 2L,
  sleep = function(seconds) retry_after_sleeps <<- c(retry_after_sleeps, seconds),
  jitter = function(...) 0
)
stopifnot(
  retry_after_calls == 2L,
  identical(retry_after_sleeps, 120),
  capped_retry$request_count == 2L,
  length(capped_retry$unservable_queries) == 0L
)

unservable_unit <- unit
unservable_unit$is_window <- TRUE  # Exercise unresolved narrow-spelling rescue inside a date window.
unservable_unit$decomposition_variants <- c(
  "BINANCE", "BINANCE.US", "BINANCEUS"
)
unservable_unit$decomposition_source <- "observed_employers"
unservable_calls <- character()
unservable_transport <- function(specs, api_key, concurrency) {
  lapply(specs, function(spec) {
    employer <- toupper(spec$params$contributor_employer[[1L]])
    unservable_calls <<- c(unservable_calls, employer)
    if (identical(employer, "BINANCE")) {
      elapsed <- if (sum(unservable_calls == "BINANCE") == 1L) 55 else 0.4
      return(list(status = 504L, headers = list(), body = "", elapsed_seconds = elapsed))
    }
    ids <- if (identical(employer, "BINANCE.US")) c("A", "DUP") else c("DUP", "Z")
    list(
      status = 200L,
      headers = list(),
      json = list(
        results = lapply(ids, function(id) list(sub_id = id)),
        pagination = list(is_count_exact = TRUE, count = length(ids), last_indexes = NULL)
      )
    )
  })
}
unservable_result <- fec_api_collect_work_units(
  list(unservable_unit),
  api_key = "fixture-key",
  concurrency = 1L,
  transport = unservable_transport,
  sleep = function(seconds) NULL,
  jitter = function(...) 0
)
unservable_rows <- fec_api_state_rows(
  unservable_result$units[[unservable_unit$id]]
)
stopifnot(
  identical(unservable_calls, c("BINANCE", "BINANCE", "BINANCE.US", "BINANCEUS")),
  unservable_result$request_count == 4L,
  unservable_result$retry_count == 1L,
  identical(
    unservable_result$decompositions[[1L]]$term,
    "Binance"
  ),
  length(unservable_result$decompositions) == 1L,
  unservable_result$decompositions[[1L]]$query_count == 2L,
  unservable_result$decompositions[[1L]]$row_count == 3L,
  identical(
    vapply(unservable_rows, function(row) row$sub_id, character(1L)),
    c("A", "DUP", "Z")
  )
)

no_decomposition_unit <- unit
no_decomposition_unit$is_window <- TRUE
no_decomposition_unit$company_variants <- no_decomposition_unit$variants
no_decomposition_unit$decomposition_variants <- character()
no_decomposition_failure <- tryCatch(
  {
    fec_api_collect_work_units(
      list(no_decomposition_unit),
      api_key = "fixture-key",
      concurrency = 1L,
      transport = function(specs, api_key, concurrency) {
        lapply(specs, function(spec) list(
          status = 504L,
          headers = list(),
          body = "",
          elapsed_seconds = 55
        ))
      },
      sleep = function(seconds) NULL,
      jitter = function(...) 0
    )
    ""
  },
  error = conditionMessage
)
stopifnot(
  grepl(
    "Unservable FEC API query has no decomposition spellings",
    no_decomposition_failure,
    fixed = TRUE
  ),
  grepl("refusing an unverified zero", no_decomposition_failure, fixed = TRUE)
)

adaptive_page_calls <- list()
adaptive_page_transport <- function(specs, api_key, concurrency) {
  lapply(specs, function(spec) {
    employer <- toupper(spec$params$contributor_employer[[1L]])
    per_page <- as.integer(spec$params$per_page[[1L]])
    adaptive_page_calls[[length(adaptive_page_calls) + 1L]] <<- list(
      employer = employer,
      per_page = per_page
    )
    if (identical(employer, "BINANCE") ||
        (identical(employer, "BINANCE.US") && per_page > 50L)) {
      return(list(
        status = 504L,
        headers = list(),
        body = "",
        elapsed_seconds = 0.4
      ))
    }
    id <- if (identical(employer, "BINANCE.US")) "SMALL-PAGE" else "OTHER"
    list(
      status = 200L,
      headers = list(),
      json = list(
        results = list(list(sub_id = id)),
        pagination = list(is_count_exact = TRUE, count = 1L, last_indexes = NULL)
      )
    )
  })
}
adaptive_page_result <- fec_api_collect_work_units(
  list(unservable_unit),
  api_key = "fixture-key",
  concurrency = 1L,
  transport = adaptive_page_transport,
  sleep = function(seconds) NULL,
  jitter = function(...) 0
)
adaptive_page_rows <- fec_api_state_rows(
  adaptive_page_result$units[[unservable_unit$id]]
)
binance_us_page_sizes <- vapply(
  adaptive_page_calls[
    vapply(
      adaptive_page_calls,
      function(call) identical(call$employer, "BINANCE.US"),
      logical(1L)
    )
  ],
  function(call) call$per_page,
  integer(1L)
)
stopifnot(
  identical(binance_us_page_sizes, c(100L, 100L, 50L)),
  identical(
    vapply(adaptive_page_rows, function(row) row$sub_id, character(1L)),
    c("OTHER", "SMALL-PAGE")
  )
)

partial_decomposition_calls <- new.env(parent = emptyenv())
partial_decomposition_transport <- function(specs, api_key, concurrency) {
  lapply(specs, function(spec) {
    employer <- toupper(spec$params$contributor_employer[[1L]])
    count <- partial_decomposition_calls[[employer]] %||% 0L
    partial_decomposition_calls[[employer]] <- count + 1L
    if (employer %in% c("BINANCE", "BINANCEUS")) {
      return(list(
        status = 504L,
        headers = list(),
        body = "",
        elapsed_seconds = if (count == 0L) 55 else 0.4
      ))
    }
    list(
      status = 200L,
      headers = list(),
      json = list(
        results = list(list(sub_id = "ONLY-SUCCESSFUL-CHILD")),
        pagination = list(is_count_exact = TRUE, count = 1L, last_indexes = NULL)
      )
    )
  })
}
partial_decomposition_failure <- tryCatch(
  {
    fec_api_collect_work_units(
      list(unservable_unit),
      api_key = "fixture-key",
      concurrency = 1L,
      transport = partial_decomposition_transport,
      sleep = function(seconds) NULL,
      jitter = function(...) 0
    )
    ""
  },
  error = conditionMessage
)
stopifnot(grepl(
  "refusing a partial parent result",
  partial_decomposition_failure,
  fixed = TRUE
))

duplicate_page_calls <- 0L
duplicate_page_transport <- function(specs, api_key, concurrency) {
  duplicate_page_calls <<- duplicate_page_calls + 1L
  id <- if (duplicate_page_calls == 1L) "DUPLICATE" else "DUPLICATE"
  list(list(
    status = 200L,
    headers = list(),
    json = list(
      results = list(list(sub_id = id)),
      pagination = list(is_count_exact = TRUE,
        count = 2L,
        last_indexes = if (duplicate_page_calls == 1L) {
          list(
            last_index = "next",
            last_contribution_receipt_date = "2024-01-01"
          )
        } else {
          NULL
        }
      )
    )
  ))
}
duplicate_page_failure <- tryCatch(
  {
    fec_api_collect_work_units(
      list(unit),
      api_key = "fixture-key",
      concurrency = 1L,
      transport = duplicate_page_transport,
      sleep = function(seconds) NULL,
      jitter = function(...) 0
    )
    ""
  },
  error = conditionMessage
)
stopifnot(grepl(
  "repeated sub_id",
  duplicate_page_failure,
  fixed = TRUE
))

partial_batch_sizes <- integer()
partial_batch_reservations <- integer()
partial_batch_attempts <- new.env(parent = emptyenv())
partial_batch_transport <- function(specs, api_key, concurrency) {
  partial_batch_sizes <<- c(partial_batch_sizes, length(specs))
  lapply(specs, function(spec) {
    id <- spec$id
    attempt <- partial_batch_attempts[[id]] %||% 0L
    partial_batch_attempts[[id]] <- attempt + 1L
    if (identical(id, "transport-failure") && attempt == 0L) {
      list(status = 0L, headers = list(), body = "")
    } else {
      list(status = 200L, headers = list(), body = "{}")
    }
  })
}
partial_batch <- fec_api_fetch_with_retry(
  list(
    list(id = "success", url = FEC_API_ENDPOINT, params = list()),
    list(id = "transport-failure", url = FEC_API_ENDPOINT, params = list())
  ),
  api_key = "fixture-key",
  concurrency = 2L,
  transport = partial_batch_transport,
  max_attempts = 2L,
  sleep = function(seconds) NULL,
  jitter = function(...) 0,
  before_request = function(count) {
    partial_batch_reservations <<- c(partial_batch_reservations, count)
  }
)
stopifnot(
  identical(partial_batch_sizes, c(2L, 1L)),
  identical(partial_batch_reservations, c(2L, 1L)),
  partial_batch$request_count == 3L,
  partial_batch$retry_count == 1L
)

adaptive_units <- lapply(seq_len(8L), function(index) {
  value <- unit
  value$id <- paste0("adaptive-", index)
  value
})
adaptive_calls <- 0L
adaptive_concurrency <- integer()
adaptive_transport <- function(specs, api_key, concurrency) {
  adaptive_calls <<- adaptive_calls + 1L
  adaptive_concurrency <<- c(adaptive_concurrency, concurrency)
  lapply(seq_along(specs), function(index) {
    if (adaptive_calls == 1L && index == 1L) {
      return(list(status = 429L, headers = list(`Retry-After` = "0"), body = ""))
    }
    list(
      status = 200L,
      headers = list(),
      json = list(
        results = list(list(sub_id = specs[[index]]$id)),
        pagination = list(is_count_exact = TRUE, count = 1L, last_indexes = NULL)
      )
    )
  })
}
adaptive <- fec_api_collect_work_units(
  adaptive_units,
  api_key = "fixture-key",
  concurrency = 4L,
  transport = adaptive_transport,
  sleep = function(seconds) NULL,
  jitter = function(...) 0
)
stopifnot(
  identical(adaptive_concurrency, c(4L, 4L, 2L, 3L)),
  adaptive$rate_limit_count == 1L,
  adaptive$final_concurrency == 4L,
  adaptive$rows == 8L
)

secret_key <- "DO-NOT-PRINT-THIS-KEY"
retry_calls <- 0L
always_500 <- function(specs, api_key, concurrency) {
  retry_calls <<- retry_calls + length(specs)
  lapply(specs, function(spec) list(status = 500L, headers = list(), body = ""))
}
failure <- tryCatch(
  {
    fec_api_fetch_with_retry(
      list(fec_api_request_spec(unit)),
      api_key = secret_key,
      concurrency = 1L,
      transport = always_500,
      max_attempts = 3L,
      sleep = function(seconds) NULL,
      jitter = function(...) 0
    )
    ""
  },
  error = conditionMessage
)
stopifnot(
  retry_calls == 3L,
  grepl("HTTP_500", failure, fixed = TRUE),
  grepl("contributor_employer=Binance", failure, fixed = TRUE),
  grepl("API key redacted", failure, fixed = TRUE),
  !grepl(secret_key, failure, fixed = TRUE)
)

rescue_rows <- list(
  list(sub_id = "near-miss", contributor_name = "MOLLOY, SALLY"),
  list(sub_id = "target", contributor_name = "MOLLOY, SALLY")
)
stopifnot(
  length(fec_api_bulk_rescue_match(rescue_rows, "target")) == 1L,
  identical(
    fec_api_bulk_rescue_match(rescue_rows, "target")[[1L]]$sub_id,
    "target"
  ),
  length(fec_api_bulk_rescue_match(rescue_rows, "TARGET")) == 0L
)

rescue_page_rows <- character()
rescue_exact_rows <- character()
rescue_no_cull <- fec_api_execute_rescue_plan(
  data.frame(
    company = "BINANCE US", cycle = 2024L, sub_id = "target",
    contributor_name = "MOLLOY, SALLY", contributor_zip = "98101",
    stringsAsFactors = FALSE
  ),
  cap = 1L,
  api_key = "fixture-key",
  transport = function(specs, api_key, concurrency) {
    list(list(
      status = 200L,
      headers = list(),
      json = list(
        results = rescue_rows,
        pagination = list(is_count_exact = TRUE, count = 2L, last_indexes = NULL)
      )
    ))
  },
  sleep = function(seconds) stop("unexpected rescue sleep"),
  jitter = function(...) 0,
  on_complete = function(candidate, pages, exact_rows) {
    rescue_page_rows <<- vapply(pages[[1L]]$rows, function(row) row$sub_id, character(1L))
    rescue_exact_rows <<- vapply(exact_rows, function(row) row$sub_id, character(1L))
  }
)
stopifnot(
  rescue_no_cull$rows == 1L,
  rescue_no_cull$raw_rows == 2L,
  identical(rescue_page_rows, c("near-miss", "target")),
  identical(rescue_exact_rows, "target")
)

rescue_candidates <- data.frame(
  company = rep("BINANCE US", 51L),
  cycle = rep(2024L, 51L),
  sub_id = sprintf("rescue-%02d", seq_len(51L)),
  contributor_name = sprintf("PERSON, %02d", seq_len(51L)),
  contributor_zip = rep("98101-1234", 51L),
  stringsAsFactors = FALSE
)
rescue_network_ids <- character()
rescue_accepted <- character()
rescue_transport <- function(specs, api_key, concurrency) {
  stopifnot(length(specs) == 1L, concurrency == 1L)
  id <- sub("^P3_bulk_rescue:[^:]+:[^:]+:", "", specs[[1L]]$id)
  rescue_network_ids <<- c(rescue_network_ids, id)
  stopifnot(identical(specs[[1L]]$params$contributor_zip, "98101"))
  list(list(
    status = 200L,
    headers = list(),
    json = list(
      results = list(list(sub_id = id)),
      pagination = list(is_count_exact = TRUE, count = 1L, last_indexes = NULL)
    )
  ))
}
rescue_cap_result <- fec_api_execute_rescue_plan(
  rescue_candidates,
  cap = 50L,
  api_key = "fixture-key",
  concurrency = 4L,
  transport = rescue_transport,
  sleep = function(seconds) stop("unexpected rescue sleep"),
  jitter = function(...) 0,
  on_exact = function(candidate, rows, page) {
    rescue_accepted <<- c(rescue_accepted, candidate$sub_id)
  }
)
stopifnot(
  rescue_cap_result$request_count == 50L,
  rescue_cap_result$rows == 50L,
  length(rescue_network_ids) == 50L,
  length(rescue_accepted) == 50L,
  !"rescue-51" %in% rescue_network_ids,
  nrow(rescue_cap_result$overflow) == 1L,
  identical(rescue_cap_result$overflow$sub_id, "rescue-51"),
  identical(rescue_cap_result$overflow$overflow_reason, "rescue_request_cap")
)

cat("test_fec_api: PASS\n")

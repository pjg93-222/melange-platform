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
for (file in c("paths.R", "ingest.R", "fec_page.R")) {
  source(file.path(project_root, "R", file), local = .GlobalEnv)
}

stopifnot(
  fec_is_transient_chromote_error(simpleError(
    "Chromote: timed out waiting for response to command Runtime.evaluate"
  )),
  fec_is_transient_chromote_error(simpleError(
    "FEC canary failure for Coinbase cycle 2024 page 0"
  ))
)

scoped_url <- fec_build_url(
  2024L,
  "BROWN, MICHAEL",
  query_field = "contributor_name",
  query_filters = list(
    contributor_zip = "20190",
    contributor_city = "RESTON"
  )
)
stopifnot(
  grepl("contributor_name=BROWN%2C%20MICHAEL", scoped_url, fixed = TRUE),
  grepl("contributor_zip=20190", scoped_url, fixed = TRUE),
  grepl("contributor_city=RESTON", scoped_url, fixed = TRUE)
)
unsupported_filter_error <- tryCatch(
  {
    fec_build_url(
      2024L,
      "BROWN, MICHAEL",
      query_field = "contributor_name",
      query_filters = list(committee_id = "C00000000")
    )
    ""
  },
  error = conditionMessage
)
stopifnot(grepl("Unsupported FEC query filter", unsupported_filter_error))

# Employer variants are queried exactly as the roster spells them.
# normalize_register_key() would strip the punctuation AND fold two spellings
# into one parameter, which silently changes the population FEC returns.
literal_employer_url <- fec_build_url(
  2024L,
  c("Ripple Labs, Inc.", "Ripple Labs Inc"),
  query_field = "contributor_employer"
)
employer_parameters <- gregexpr(
  "contributor_employer=", literal_employer_url, fixed = TRUE
)[[1L]]
stopifnot(
  grepl(
    "contributor_employer=Ripple%20Labs%2C%20Inc.",
    literal_employer_url,
    fixed = TRUE
  ),
  grepl(
    "contributor_employer=Ripple%20Labs%20Inc",
    literal_employer_url,
    fixed = TRUE
  ),
  !grepl(
    utils::URLencode(
      normalize_register_key("Ripple Labs, Inc."),
      reserved = TRUE,
      repeated = TRUE
    ),
    literal_employer_url,
    fixed = TRUE
  ),
  identical(length(employer_parameters), 2L)
)
# An ampersand is a query separator, so the literal spelling must survive as a
# percent-encoded value rather than being normalized away or split in two.
ampersand_employer_url <- fec_build_url(
  2024L,
  "Bakkt Holdings & Co.",
  query_field = "contributor_employer"
)
stopifnot(
  grepl(
    "contributor_employer=Bakkt%20Holdings%20%26%20Co.",
    ampersand_employer_url,
    fixed = TRUE
  ),
  !grepl(
    utils::URLencode(
      normalize_register_key("Bakkt Holdings & Co."),
      reserved = TRUE,
      repeated = TRUE
    ),
    ampersand_employer_url,
    fixed = TRUE
  ),
  identical(
    length(gregexpr(
      "contributor_employer=", ampersand_employer_url, fixed = TRUE
    )[[1L]]),
    1L
  )
)

local({
  original_runtime <- fec_runtime_value
  original_ready <- fec_results_ready
  original_read <- fec_read_page
  original_wait <- fec_wait_until
  on.exit({
    assign("fec_runtime_value", original_runtime, envir = .GlobalEnv)
    assign("fec_results_ready", original_ready, envir = .GlobalEnv)
    assign("fec_read_page", original_read, envir = .GlobalEnv)
    assign("fec_wait_until", original_wait, envir = .GlobalEnv)
  })

  redraws <- 0L
  reads <- 0L
  waits <- 0L
  assign(
    "fec_runtime_value",
    function(session, expression) {
      redraws <<- redraws + 1L
      FALSE
    },
    envir = .GlobalEnv
  )
  assign(
    "fec_read_page",
    function(session) {
      reads <<- reads + 1L
      list(info = list(length = 100L, recordsDisplay = 4L))
    },
    envir = .GlobalEnv
  )
  assign(
    "fec_wait_until",
    function(...) {
      waits <<- waits + 1L
      invisible(TRUE)
    },
    envir = .GlobalEnv
  )
  unchanged <- fec_set_page_length(list(), 100L)
  stopifnot(
    identical(redraws, 1L),
    identical(reads, 1L),
    identical(waits, 0L),
    identical(unchanged$info$length, 100L)
  )

  runtime_values <- list(TRUE, TRUE)
  redraws <- 0L
  reads <- 0L
  waits <- 0L
  assign(
    "fec_runtime_value",
    function(session, expression) {
      redraws <<- redraws + 1L
      value <- runtime_values[[1L]]
      runtime_values <<- runtime_values[-1L]
      value
    },
    envir = .GlobalEnv
  )
  assign(
    "fec_results_ready",
    function(session) TRUE,
    envir = .GlobalEnv
  )
  assign(
    "fec_wait_until",
    function(predicate, ...) {
      waits <<- waits + 1L
      stopifnot(isTRUE(predicate()))
      invisible(TRUE)
    },
    envir = .GlobalEnv
  )
  changed <- fec_set_page_length(list(), 100L)
  stopifnot(
    identical(redraws, 2L),
    identical(reads, 1L),
    identical(waits, 1L),
    identical(changed$info$length, 100L)
  )
})

local({
  original_runtime <- fec_runtime_value
  original_ready <- fec_results_ready
  original_read <- fec_read_page
  original_wait <- fec_wait_until
  on.exit({
    assign("fec_runtime_value", original_runtime, envir = .GlobalEnv)
    assign("fec_results_ready", original_ready, envir = .GlobalEnv)
    assign("fec_read_page", original_read, envir = .GlobalEnv)
    assign("fec_wait_until", original_wait, envir = .GlobalEnv)
  })

  runtime_values <- list(TRUE, TRUE)
  reads <- 0L
  assign(
    "fec_runtime_value",
    function(session, expression) {
      value <- runtime_values[[1L]]
      runtime_values <<- runtime_values[-1L]
      value
    },
    envir = .GlobalEnv
  )
  assign("fec_results_ready", function(session) TRUE, envir = .GlobalEnv)
  assign(
    "fec_wait_until",
    function(predicate, ...) {
      stopifnot(isTRUE(predicate()))
      invisible(TRUE)
    },
    envir = .GlobalEnv
  )
  assign(
    "fec_read_page",
    function(session) {
      reads <<- reads + 1L
      list(info = list(page = 1L))
    },
    envir = .GlobalEnv
  )
  payload <- fec_click_next(list(), 0L)
  stopifnot(
    identical(reads, 1L),
    identical(payload$info$page, 1L)
  )
})

local({
  attempts <- 0L
  session <- list(
    Page = list(
      navigate = function(url) {
        attempts <<- attempts + 1L
        stop(
          "Chromote: timed out waiting for response to command Runtime.evaluate",
          call. = FALSE
        )
      }
    )
  )
  error_message <- tryCatch(
    {
      fec_navigate(
        session,
        "https://www.fec.gov/",
        timeout_seconds = 1,
        retries = 3L
      )
      ""
    },
    error = conditionMessage
  )
  stopifnot(
    identical(attempts, 1L),
    grepl("timed out waiting for response", error_message, fixed = TRUE)
  )
})

local({
  original_once <- fec_walk_variant_set_once
  original_sleep <- fec_query_retry_sleep
  original_recover <- fec_recover_session
  on.exit({
    assign("fec_walk_variant_set_once", original_once, envir = .GlobalEnv)
    assign("fec_query_retry_sleep", original_sleep, envir = .GlobalEnv)
    assign("fec_recover_session", original_recover, envir = .GlobalEnv)
  })

  attempts <- 0L
  observed_sessions <- character()
  assign(
    "fec_walk_variant_set_once",
    function(session, ...) {
      attempts <<- attempts + 1L
      observed_sessions <<- c(observed_sessions, session$id)
      if (attempts < 3L) {
        stop(
          "Chromote: timed out waiting for response to command Runtime.evaluate",
          call. = FALSE
        )
      }
      list(records_total = 17L, pages = 1L, retries = 0L)
    },
    envir = .GlobalEnv
  )
  recoveries <- 0L
  assign(
    "fec_recover_session",
    function(session) {
      recoveries <<- recoveries + 1L
      list(id = paste0("recovered-", recoveries))
    },
    envir = .GlobalEnv
  )
  assign(
    "fec_query_retry_sleep",
    function(attempt) invisible(attempt),
    envir = .GlobalEnv
  )

  state <- new_fec_session_state(list(id = "initial"))
  result <- fec_walk_variant_set(
    session = state,
    company = "Coinbase",
    cycle = 2024L,
    variants = "Coinbase",
    variant_set = "P1_employer",
    page_callback = function(...) invisible(NULL),
    canary_callback = function(...) invisible(NULL),
    sleep_range = c(0, 0),
    query_retries = 2L
  )
  stopifnot(
    identical(attempts, 3L),
    identical(recoveries, 2L),
    identical(
      observed_sessions,
      c("initial", "recovered-1", "recovered-2")
    ),
    identical(state$session$id, "recovered-2"),
    identical(result$records_total, 17L),
    identical(result$retries, 2L)
  )

  attempts <- 0L
  assign(
    "fec_walk_variant_set_once",
    function(session, ...) {
      attempts <<- attempts + 1L
      stop("FEC canary failure for Coinbase cycle 2024 page 0", call. = FALSE)
    },
    envir = .GlobalEnv
  )
  error_message <- tryCatch(
    {
      fec_walk_variant_set(
        session = list(),
        company = "Coinbase",
        cycle = 2024L,
        variants = "Coinbase",
        variant_set = "P1_employer",
        page_callback = function(...) invisible(NULL),
        canary_callback = function(...) invisible(NULL),
        query_retries = 3L
      )
      ""
    },
    error = conditionMessage
  )
  stopifnot(
    identical(attempts, 4L),
    grepl("FEC canary failure", error_message, fixed = TRUE)
  )
})

local({
  original_api_url <- fec_browser_api_url
  original_api_batch <- fec_browser_api_batch
  on.exit({
    assign("fec_browser_api_url", original_api_url, envir = .GlobalEnv)
    assign("fec_browser_api_batch", original_api_batch, envir = .GlobalEnv)
  })

  assign(
    "fec_browser_api_url",
    function(session) "https://api.open.fec.gov/v1/schedules/schedule_a/",
    envir = .GlobalEnv
  )
  calls <- 0L
  make_response <- function(page, sub_id) {
    list(
      page = page,
      status = 200L,
      body = list(
        pagination = list(
          count = 2L,
          last_indexes = list(
            last_contribution_receipt_date = paste0("2026-01-0", page),
            last_index = as.character(page)
          )
        ),
        results = list(list(sub_id = sub_id))
      )
    )
  }
  assign(
    "fec_browser_api_batch",
    function(session, api_url, pages, cursor, per_page) {
      calls <<- calls + 1L
      if (calls == 1L) {
        return(list(
          responses = list(make_response(1L, "retry-1")),
          cursor = list()
        ))
      }
      list(
        responses = list(
          make_response(1L, "complete-1"),
          make_response(2L, "complete-2")
        ),
        cursor = list(
          last_contribution_receipt_date = "2026-01-02",
          last_index = "2"
        )
      )
    },
    envir = .GlobalEnv
  )
  callback_rows <- 0L
  canary_pages <- 0L
  result <- fec_walk_variant_set_api(
    session = list(),
    records_total = 2L,
    page_callback = function(rows, ...) {
      callback_rows <<- callback_rows + length(rows)
    },
    canary_callback = function(...) {
      canary_pages <<- canary_pages + 1L
    },
    company = "Coinbase",
    cycle = 2026L,
    variant_set = "P1_employer",
    per_page = 1L,
    batch_pages = 2L,
    batch_attempts = 2L
  )
  stopifnot(
    identical(calls, 2L),
    identical(callback_rows, 2L),
    identical(canary_pages, 2L),
    identical(result$records_total, 2L),
    isTRUE(result$api_fallback)
  )
})

local({
  # Regression for the employer date-partition recovery path: session state is
  # intentionally passed to fec_walk_variant_set(), which knows how to recover
  # and replace it, but direct browser calls require the active Chromote
  # session. Passing the state wrapper to either direct call fails at runtime
  # because it has no Page/Runtime domains.
  partition_script <- file.path(
    project_root,
    "tools",
    "collect_employer_date_partition.R"
  )
  parsed <- parse(file = partition_script, keep.source = FALSE)
  direct_calls <- list()
  collect_direct_calls <- function(node) {
    if (is.call(node)) {
      call_name <- as.character(node[[1L]])[[1L]]
      if (call_name %in% c("fec_navigate", "fec_read_page")) {
        direct_calls[[length(direct_calls) + 1L]] <<- node
      }
      children <- as.list(node)[-1L]
      for (child_index in seq_along(children)) {
        if (!nzchar(paste(deparse(children[[child_index]]), collapse = ""))) {
          next
        }
        child <- children[[child_index]]
        collect_direct_calls(child)
      }
    } else if (is.expression(node) || is.pairlist(node) || is.list(node)) {
      children <- as.list(node)
      for (child_index in seq_along(children)) {
        if (!nzchar(paste(deparse(children[[child_index]]), collapse = ""))) {
          next
        }
        child <- children[[child_index]]
        collect_direct_calls(child)
      }
    }
    invisible(NULL)
  }
  collect_direct_calls(parsed)

  call_names <- vapply(
    direct_calls,
    function(node) as.character(node[[1L]])[[1L]],
    character(1L)
  )
  active_session_argument <- vapply(
    direct_calls,
    function(node) {
      argument <- node[[2L]]
      is.call(argument) &&
        identical(as.character(argument[[1L]])[[1L]], "fec_active_session") &&
        identical(argument[[2L]], as.name("session"))
    },
    logical(1L)
  )
  stopifnot(
    identical(sum(call_names == "fec_navigate"), 1L),
    identical(sum(call_names == "fec_read_page"), 1L),
    all(active_session_argument)
  )
})

# The export URL comes from page storage: only https URLs may reach
# download.file(). The host is not pinned (openFEC returns presigned S3 URLs).
# Nothing here touches the network.
refuses <- function(url) {
  inherits(tryCatch(fec_require_https_url(url), error = identity), "error")
}
presigned <- "https://cg-abc.s3-us-gov-west-1.amazonaws.com/x.zip?X-Amz-Foo=1"
stopifnot(
  !refuses("https://www.fec.gov/files/x.csv"),
  !refuses(presigned),
  !refuses("HTTPS://Example.org:8443/x.csv"),
  identical(fec_require_https_url(presigned), presigned),
  refuses("javascript:alert(1)"),
  refuses("file:///C:/x"),
  refuses("http://www.fec.gov/x.csv"),
  refuses("https://user@host/x"),
  refuses("ftp://www.fec.gov/x.csv"),
  refuses("//www.fec.gov/x.csv"),
  refuses("https://www.fec.gov@evil.com/x.csv"),
  refuses("https://evil.com\\@www.fec.gov/x.csv"),
  refuses("https://example.org/a\\b"),
  refuses("https://example.org/a b"),
  refuses("https://example.org/a\nfile:///x"),
  refuses(" https://example.org/x"),
  refuses("https://"),
  refuses("https:///x.csv"),
  refuses("https://:443/x.csv"),
  refuses(""),
  refuses(NA_character_),
  refuses(NULL),
  refuses(c("https://www.fec.gov/a", "https://www.fec.gov/b"))
)
# Non-ASCII lookalikes (NBSP, fullwidth quote/backslash/@, zero-width space, RTL
# override, Cyrillic homoglyph host) are refused whatever the string's encoding
# mark. Built from \u escapes so this file stays ASCII. A percent-encoded URL is
# still accepted.
lookalikes <- c(
  "https://x.org/a\u00a0b",
  "https://x.org/a\uff02b",
  "https://x.org/a\uff3cb",
  "https://www.fec.gov\uff20evil.example/x",
  "https://x.org/a\u200bb",
  "https://x.org/a\u202eb",
  "https://www.f\u0435c.gov/x.csv"
)
latin1_nbsp <- iconv("https://x.org/a\u00a0b", "UTF-8", "latin1")
bytes_nbsp <- "https://x.org/a\u00a0b"
Encoding(bytes_nbsp) <- "bytes"
invalid_utf8 <- paste0("https://x.org/a", rawToChar(as.raw(0xc3)), "b")
stopifnot(
  identical(Encoding(latin1_nbsp), "latin1"),
  identical(Encoding(bytes_nbsp), "bytes"),
  all(vapply(
    c(lookalikes, latin1_nbsp, bytes_nbsp, invalid_utf8),
    refuses,
    logical(1L),
    USE.NAMES = FALSE
  )),
  !refuses("https://x.org/a%20b?X-Amz-Signature=abc%2Fdef")
)
refusal_message <- tryCatch(
  fec_require_https_url("http://www.fec.gov/x.csv"),
  error = conditionMessage
)
stopifnot(grepl("must be an https URL", refusal_message, fixed = TRUE))
export_body <- paste(deparse(body(fec_export_current_query)), collapse = "\n")
stopifnot(
  regexpr("fec_require_https_url(download_url)", export_body, fixed = TRUE) <
    regexpr("download.file", export_body, fixed = TRUE)
)

cat("test_fec_page: transient whole-query retry verified\n")
cat("test_fec_page: incomplete browser API batches retried atomically\n")
cat("test_fec_page: scoped ZIP/city URL filters verified\n")
cat("test_fec_page: employer variants keep their literal roster spelling\n")
cat("test_fec_page: redundant redraw and page reads avoided\n")
cat("test_fec_page: dead sessions bypass inner retries\n")
cat("test_fec_page: canary failures remain bounded and fail closed\n")
cat("test_fec_page: employer partition direct calls use active session\n")
cat("test_fec_page: export download URL limited to https\n")
cat("test_fec_page: PASS\n")

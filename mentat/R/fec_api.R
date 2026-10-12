if (!exists("MENTAT_INDUSTRY")) MENTAT_INDUSTRY <- "Crypto"
FEC_API_ENDPOINT <- "https://api.open.fec.gov/v1/schedules/schedule_a/"
FEC_API_PER_PAGE <- 100L
FEC_API_MAX_ATTEMPTS <- 5L
FEC_API_MAX_PAGES_PER_UNIT <- 10000L
FEC_API_MAX_QUERY_VALUES <- 10L
FEC_API_RETRY_AFTER_CAP_SECONDS <- 120
FEC_API_HOURLY_REQUEST_CAP <- 1000L
FEC_API_BUDGET_WINDOW_SECONDS <- 3600

fec_api_key <- function(value = Sys.getenv("FEC_API_KEY", unset = "")) {
  key <- trimws(value)
  if (!nzchar(key)) {
    stop(
      paste(
        "FEC_API_KEY is unset. Register for a key at",
        "https://api.data.gov/signup and set the FEC_API_KEY environment variable."
      ),
      call. = FALSE
    )
  }
  key
}

# The persistent request budget is governed PER API KEY, not per database.
# api.data.gov enforces its hourly cap per registered key, so workers holding
# distinct keys are independent even when they share one database and industry.
# Keying the budget row by a derived id is what makes that parallelism real: a
# single hardcoded id collapses every worker onto one shared 1,000/hr bucket,
# which silently caps an N-key fan-out at 1x. The key is never stored - only a
# non-reversible polynomial digest of it.
fec_api_budget_id <- function(
  api_key = Sys.getenv("FEC_API_KEY", unset = ""),
  configured = Sys.getenv("MENTAT_API_BUDGET_ID", unset = "")
) {
  configured <- trimws(configured)
  if (nzchar(configured)) {
    if (!grepl("^openfec:sha256:[0-9a-f]{12,64}$", configured, perl = TRUE)) {
      stop(
        "MENTAT_API_BUDGET_ID must be an openfec:sha256:<hex> identifier.",
        call. = FALSE
      )
    }
    return(configured)
  }
  key <- trimws(api_key)
  if (!nzchar(key)) return("openfec")
  codes <- utf8ToInt(key)
  digest <- 0
  for (code in codes) {
    digest <- (digest * 31 + code) %% 1000003
  }
  paste0("openfec:", format(digest, scientific = FALSE))
}

fec_api_concurrency <- function(
  value = Sys.getenv("MENTAT_CONCURRENCY", unset = "8")
) {
  concurrency <- suppressWarnings(as.integer(trimws(value)))
  if (length(concurrency) != 1L || is.na(concurrency) || concurrency < 1L) {
    stop("MENTAT_CONCURRENCY must be a positive integer.", call. = FALSE)
  }
  concurrency
}

# Paul's ruling: an FEC pagination.count that moves during collection must not
# abort the lane and must never be stored unverified. The collector re-walks the
# cursor until one whole pass observes a constant count equal to the rows it
# collected, and fails loudly only after this many reconciliation passes.
fec_api_reconcile_passes <- function(
  value = Sys.getenv("MENTAT_RECONCILE_PASSES", unset = "3")
) {
  passes <- suppressWarnings(as.integer(trimws(value)))
  if (length(passes) != 1L || is.na(passes) || passes < 0L) {
    stop("MENTAT_RECONCILE_PASSES must be a nonnegative integer.", call. = FALSE)
  }
  passes
}

fec_api_budget_exhausted <- function(used, requested, cap) {
  structure(
    list(
      message = paste0(
        "FEC API persistent request budget is exhausted: used=", used,
        ", requested=", requested, ", cap=", cap,
        ". Checkpoints are saved; resume after the budget window resets."
      ),
      call = NULL,
      used = as.integer(used),
      requested = as.integer(requested),
      cap = as.integer(cap)
    ),
    class = c("mentat_api_budget_exhausted", "error", "condition")
  )
}

fec_api_budget_reserve <- function(
  connection,
  requests,
  cap = FEC_API_HOURLY_REQUEST_CAP,
  now = as.numeric(Sys.time()),
  budget_id = fec_api_budget_id()
) {
  requests <- as.integer(requests)
  if (length(requests) != 1L || is.na(requests) || requests < 0L) {
    stop("FEC API budget reservation must be a nonnegative integer.", call. = FALSE)
  }
  # A deferred transaction lets concurrent readers both reach the later write;
  # SQLite cannot safely wait while upgrading both read locks, so one lane gets
  # SQLITE_BUSY immediately despite busy_timeout. BEGIN IMMEDIATE serializes
  # that upgrade before the read. This transaction contains SQLite work only
  # and is committed before the caller performs any API request.
  DBI::dbExecute(connection, "BEGIN IMMEDIATE")
  transaction_open <- TRUE
  on.exit(
    if (transaction_open) try(DBI::dbRollback(connection), silent = TRUE),
    add = TRUE
  )
  row <- DBI::dbGetQuery(
    connection,
    paste(
      "SELECT window_started_epoch, requests_used FROM api_request_budget",
      "WHERE budget_id=?"
    ),
    params = list(budget_id)
  )
  if (!nrow(row) || now - row$window_started_epoch[[1L]] >=
      FEC_API_BUDGET_WINDOW_SECONDS) {
    started <- now
    used <- 0L
  } else {
    started <- as.numeric(row$window_started_epoch[[1L]])
    used <- as.integer(row$requests_used[[1L]])
  }
  if (used + requests > as.integer(cap)) {
    stop(fec_api_budget_exhausted(used, requests, cap))
  }
  used <- used + requests
  DBI::dbExecute(
    connection,
    paste(
      "INSERT INTO api_request_budget",
      "(budget_id, window_started_epoch, requests_used) VALUES (?, ?, ?)",
      "ON CONFLICT(budget_id) DO UPDATE SET",
      "window_started_epoch=excluded.window_started_epoch,",
      "requests_used=excluded.requests_used"
    ),
    params = list(budget_id, started, used)
  )
  status <- list(
    requests_used = used,
    requests_remaining = as.integer(cap) - used,
    window_started_epoch = started
  )
  DBI::dbCommit(connection)
  transaction_open <- FALSE
  status
}

fec_api_budget_status <- function(
  connection,
  cap = FEC_API_HOURLY_REQUEST_CAP,
  now = as.numeric(Sys.time()),
  budget_id = fec_api_budget_id()
) {
  row <- DBI::dbGetQuery(
    connection,
    paste(
      "SELECT window_started_epoch, requests_used FROM api_request_budget",
      "WHERE budget_id=?"
    ),
    params = list(budget_id)
  )
  active <- nrow(row) &&
    now - row$window_started_epoch[[1L]] < FEC_API_BUDGET_WINDOW_SECONDS
  used <- if (active) as.integer(row$requests_used[[1L]]) else 0L
  list(
    requests_used = used,
    requests_remaining = as.integer(cap) - used
  )
}

fec_api_query_pairs <- function(params) {
  pairs <- list()
  for (name in names(params)) {
    values <- params[[name]]
    if (is.null(values) || !length(values)) next
    values <- as.character(values)
    values <- values[!is.na(values) & nzchar(values)]
    for (value in values) {
      pairs[[length(pairs) + 1L]] <- c(name = name, value = value)
    }
  }
  pairs
}

fec_api_encode_query <- function(params) {
  pairs <- fec_api_query_pairs(params)
  if (!length(pairs)) return("")
  paste(vapply(pairs, function(pair) {
    paste0(
      utils::URLencode(pair[["name"]], reserved = TRUE, repeated = TRUE),
      "=",
      utils::URLencode(pair[["value"]], reserved = TRUE, repeated = TRUE)
    )
  }, character(1L)), collapse = "&")
}

fec_api_spec_url <- function(spec) {
  query <- fec_api_encode_query(spec$params)
  if (nzchar(query)) paste0(spec$url, "?", query) else spec$url
}

fec_api_validate_cursor <- function(last_indexes, unit_id = "") {
  cursor_names <- c("last_index", "last_contribution_receipt_date")
  present <- names(last_indexes) %||% character()
  missing <- setdiff(cursor_names, present)
  supported <- intersect(cursor_names, present)
  invalid <- supported[vapply(
    supported,
    function(name) {
      value <- last_indexes[[name]]
      is.null(value) || length(value) != 1L || is.na(value) ||
        !nzchar(trimws(as.character(value)))
    },
    logical(1L)
  )]
  if (length(missing) || length(invalid)) {
    detail <- c(
      if (length(missing)) paste0("missing=", paste(missing, collapse = ",")),
      if (length(invalid)) paste0("null_or_blank=", paste(invalid, collapse = ","))
    )
    stop(
      paste0(
        "FEC API keyset cursor is partial for ", unit_id,
        ": ", paste(detail, collapse = "; ")
      ),
      call. = FALSE
    )
  }
  last_indexes[cursor_names]
}

fec_api_request_spec <- function(unit, last_indexes = NULL) {
  supported_filters <- c("min_date","max_date","contributor_zip","contributor_city","contributor_state")
  unknown <- setdiff(names(unit$query_filters %||% list()), supported_filters)
  if (length(unknown)) stop("Unsupported FEC query filter(s): ",paste(unknown,collapse=", "),
    ". An ignored filter would silently broaden collection.",call.=FALSE)
  if (!unit$query_field %in% c("contributor_employer","contributor_name","contributor_id")) {
    stop("Unsupported FEC contributor query field: ",unit$query_field,call.=FALSE)
  }
  query_values <- trim_text(unit$variants)
  query_values <- query_values[nzchar(query_values) & !duplicated(toupper(query_values))]
  if (!length(query_values)) {
    stop("FEC API work unit has no normalized query value: ", unit$id, call. = FALSE)
  }
  per_page <- suppressWarnings(as.integer(
    unit$per_page %||% FEC_API_PER_PAGE
  ))
  if (length(per_page) != 1L || is.na(per_page) ||
      per_page < 1L || per_page > FEC_API_PER_PAGE) {
    stop(
      "FEC API work unit has an invalid per_page value: ",
      unit$id,
      call. = FALSE
    )
  }
  params <- c(
    list(
      per_page = as.character(per_page),
      two_year_transaction_period = as.character(as.integer(unit$cycle)),
      sort = "-contribution_receipt_date"
    ),
    stats::setNames(list(query_values), unit$query_field),
    unit$query_filters %||% list()
  )
  if (!is.null(last_indexes) && length(last_indexes)) {
    cursor <- fec_api_validate_cursor(last_indexes, unit$id)
    params <- c(params, cursor)
  }
  list(id = unit$id, url = FEC_API_ENDPOINT, params = params)
}

fec_api_smaller_page_size <- function(unit) {
  current <- suppressWarnings(as.integer(
    unit$per_page %||% FEC_API_PER_PAGE
  ))
  candidates <- c(50L, 20L, 10L, 5L, 1L)
  smaller <- candidates[candidates < current]
  if (length(smaller)) smaller[[1L]] else NA_integer_
}

fec_api_response_header <- function(response, name) {
  headers <- response$headers %||% list()
  if (!length(headers)) return(NA_character_)
  header_names <- tolower(names(headers) %||% rep("", length(headers)))
  index <- which(header_names == tolower(name))
  if (!length(index)) NA_character_ else as.character(headers[[index[[1L]]]])
}

fec_api_retry_after_seconds <- function(
  response,
  now = Sys.time(),
  cap = FEC_API_RETRY_AFTER_CAP_SECONDS
) {
  value <- trimws(fec_api_response_header(response, "Retry-After"))
  if (is.na(value) || !nzchar(value)) return(NA_real_)
  numeric_value <- suppressWarnings(as.numeric(value))
  if (!is.na(numeric_value)) return(min(as.numeric(cap), max(0, numeric_value)))
  parsed <- as.POSIXct(
    value,
    format = "%a, %d %b %Y %H:%M:%S GMT",
    tz = "GMT"
  )
  if (is.na(parsed)) {
    NA_real_
  } else {
    min(as.numeric(cap), max(0, as.numeric(parsed - now, units = "secs")))
  }
}

fec_api_parse_raw_headers <- function(headers) {
  if (is.null(headers) || !length(headers)) return(list())
  text <- if (is.raw(headers)) rawToChar(headers) else paste(headers, collapse = "
")
  blocks <- strsplit(text, "\r?\n\r?\n", perl = TRUE)[[1L]]
  block <- tail(blocks[nzchar(trimws(blocks))], 1L)
  lines <- strsplit(block, "\r?\n", perl = TRUE)[[1L]]
  lines <- lines[grepl(":", lines, fixed = TRUE)]
  result <- list()
  for (line in lines) {
    split_at <- regexpr(":", line, fixed = TRUE)[[1L]]
    name <- trimws(substr(line, 1L, split_at - 1L))
    value <- trimws(substr(line, split_at + 1L, nchar(line)))
    if (nzchar(name)) result[[name]] <- value
  }
  result
}

fec_api_httr2_transport <- function(specs, api_key, concurrency) {
  requests <- lapply(specs, function(spec) {
    request <- httr2::request(fec_api_spec_url(spec))
    request <- do.call(
      httr2::req_headers,
      c(list(request), stats::setNames(list(api_key), "X-Api-Key"))
    )
    httr2::req_error(request, is_error = function(response) FALSE)
  })
  responses <- httr2::req_perform_parallel(
    requests,
    max_active = min(as.integer(concurrency), length(requests)),
    on_error = "continue"
  )
  lapply(responses, function(response) {
    if (is.null(response) || inherits(response, "condition")) {
      return(list(
        status = 0L,
        headers = list(),
        body = "",
        error = if (inherits(response, "condition")) {
          conditionMessage(response)
        } else {
          "parallel transport failure"
        }
      ))
    }
    list(
      status = httr2::resp_status(response),
      headers = as.list(httr2::resp_headers(response)),
      body = httr2::resp_body_string(response)
    )
  })
}

fec_api_curl_transport <- function(specs, api_key, concurrency) {
  require_package("curl")
  if (!length(specs)) return(list())
  maximum <- min(as.integer(concurrency), length(specs))
  pool <- curl::new_pool(total_con = maximum, host_con = maximum)
  results <- vector("list", length(specs))
  for (index in seq_along(specs)) {
    handle <- curl::new_handle()
    do.call(
      curl::handle_setheaders,
      c(list(handle = handle), stats::setNames(list(api_key), "X-Api-Key"))
    )
    local({
      result_index <- index
      curl::curl_fetch_multi(
        fec_api_spec_url(specs[[result_index]]),
        done = function(response) {
          results[[result_index]] <<- list(
            status = as.integer(response$status_code),
            headers = fec_api_parse_raw_headers(response$headers),
            body = rawToChar(response$content)
          )
        },
        fail = function(message) {
          results[[result_index]] <<- list(
            status = 0L,
            headers = list(),
            body = "",
            error = enc2utf8(as.character(message))
          )
        },
        handle = handle,
        pool = pool
      )
    })
  }
  curl::multi_run(pool = pool)
  if (any(vapply(results, is.null, logical(1L)))) {
    stop("curl completed without a response for one or more FEC API requests.", call. = FALSE)
  }
  results
}

fec_api_default_transport <- function(specs, api_key, concurrency) {
  if (!length(specs)) return(list())
  pool_url <- Sys.getenv("MENTAT_REQUEST_POOL_URL", unset = "")
  if (nzchar(pool_url)) {
    require_package("curl")
    require_package("jsonlite")
    if (!grepl("^http://127[.]0[.]0[.]1:[0-9]+/batch$", pool_url)) stop("Invalid local request pool address.")
    handle <- curl::new_handle()
    curl::handle_setheaders(handle, "Content-Type" = "application/json",
      "Authorization" = paste("Bearer", Sys.getenv("MENTAT_REQUEST_POOL_TOKEN")))
    curl::handle_setopt(handle, postfields = jsonlite::toJSON(list(
      specs = specs, lane = Sys.getenv("MENTAT_REQUEST_POOL_LANE")), auto_unbox = TRUE, digits = NA), timeout = 0)
    response <- curl::curl_fetch_memory(pool_url, handle)
    if (response$status_code != 200L) stop("Local request pool unavailable; checkpoints retained.")
    result <- jsonlite::fromJSON(rawToChar(response$content), simplifyVector = FALSE,
                                 bigint_as_char = TRUE)$responses
    return(result)
  }
  if (requireNamespace("httr2", quietly = TRUE)) {
    return(fec_api_httr2_transport(specs, api_key, concurrency))
  }
  fec_api_curl_transport(specs, api_key, concurrency)
}

fec_api_query_label <- function(spec) {
  fec_api_spec_url(spec)
}

fec_api_fetch_with_retry <- function(
  specs,
  api_key,
  concurrency = fec_api_concurrency(),
  transport = fec_api_default_transport,
  max_attempts = FEC_API_MAX_ATTEMPTS,
  sleep = Sys.sleep,
  jitter = stats::runif,
  before_request = function(count) invisible(NULL)
) {
  if (!length(specs)) {
    return(list(
      responses = list(), request_count = 0L, retry_count = 0L,
      rate_limit_count = 0L, unservable_queries = integer(),
      instant_504_queries = integer()
    ))
  }
  pending <- seq_along(specs)
  attempts <- integer(length(specs))
  results <- vector("list", length(specs))
  request_count <- 0L
  retry_count <- 0L
  rate_limit_count <- 0L
  unservable <- logical(length(specs))
  saw_504 <- logical(length(specs))
  instant_504 <- logical(length(specs))
  waiting <- integer()
  wait_remaining <- numeric(length(specs))
  while (length(pending) || length(waiting)) {
    if (!length(pending)) {
      delay <- min(wait_remaining[waiting])
      if (delay > 0) sleep(delay)
      wait_remaining[waiting] <- pmax(0, wait_remaining[waiting] - delay)
      pending <- waiting[wait_remaining[waiting] <= 0]
      waiting <- setdiff(waiting, pending)
    }
    before_request(length(pending))
    attempts[pending] <- attempts[pending] + 1L
    batch_started <- proc.time()[["elapsed"]]
    batch <- tryCatch(
      transport(specs[pending], api_key, concurrency),
      error = function(condition) {
        replicate(
          length(pending),
          list(status = 0L, headers = list(), body = ""),
          simplify = FALSE
        )
      }
    )
    batch_elapsed <- proc.time()[["elapsed"]] - batch_started
    if (length(batch) != length(pending)) {
      stop("FEC API transport returned the wrong response count.", call. = FALSE)
    }
    request_count <- request_count + length(batch)
    retry <- integer()
    retry_delays <- numeric()
    for (offset in seq_along(pending)) {
      index <- pending[[offset]]
      response <- batch[[offset]]
      status <- suppressWarnings(as.integer(response$status %||% 0L))
      if (is.null(response$elapsed_seconds)) {
        response$elapsed_seconds <- unname(batch_elapsed)
      }
      is_504 <- identical(status, 504L)
      prior_504 <- saw_504[[index]]
      elapsed_seconds <- suppressWarnings(as.numeric(response$elapsed_seconds))
      if (is_504 && prior_504 && length(elapsed_seconds) == 1L &&
          is.finite(elapsed_seconds) && elapsed_seconds < 2) {
        instant_504[[index]] <- TRUE
      }
      if (is_504) saw_504[[index]] <- TRUE
      retryable <- status == 0L || status == 429L ||
        (status >= 500L && status <= 599L)
      if (status == 429L) rate_limit_count <- rate_limit_count + 1L
      if (identical(status, 200L)) {
        results[[index]] <- response
      } else if (retryable && saw_504[[index]] &&
                 (attempts[[index]] >= min(2L, max_attempts) ||
                  instant_504[[index]])) {
        response$classification <- "unservable_query"
        results[[index]] <- response
        unservable[[index]] <- TRUE
      } else if (retryable && attempts[[index]] < max_attempts) {
        retry_after <- fec_api_retry_after_seconds(response)
        if (is.na(retry_after)) {
          retry_after <- 2^(attempts[[index]] - 1L) + jitter(1L, 0, 1)
        }
        retry <- c(retry, index)
        retry_delays <- c(retry_delays, retry_after)
        retry_count <- retry_count + 1L
      } else {
        status_label <- if (status == 0L) "connection_error" else paste0("HTTP_", status)
        if (status == 429L) {
          stop(structure(
            list(
              message = paste0(
                "FEC API quota remained rate-limited after ", attempts[[index]],
                " attempt(s). Checkpoints are saved; resume after reset."
              ),
              call = NULL
            ),
            class = c("mentat_api_quota_exhausted", "error", "condition")
          ))
        }
        stop(
          paste0(
            "FEC API request failed after ", attempts[[index]],
            " attempt(s): status=", status_label,
            "; query=", fec_api_query_label(specs[[index]]),
            "; API key redacted."
          ),
          call. = FALSE
        )
      }
    }
    if (length(retry)) {
      waiting <- unique(c(waiting, retry))
      wait_remaining[retry] <- retry_delays
    }
    pending <- integer()
  }
  list(
    responses = results,
    request_count = request_count,
    retry_count = retry_count,
    rate_limit_count = rate_limit_count,
    unservable_queries = which(unservable),
    instant_504_queries = which(instant_504 & unservable)
  )
}

fec_api_json <- function(response) {
  if (!is.null(response$json)) return(response$json)
  body <- response$body %||% ""
  if (is.list(body)) return(body)
  if (!nzchar(trimws(body))) {
    stop("FEC API returned an empty success response.", call. = FALSE)
  }
  # FEC identifiers and keyset cursors can exceed double precision. Preserve
  # integer tokens as text before flattening, deduplication or checkpointing.
  jsonlite::fromJSON(body, simplifyVector = FALSE, bigint_as_char = TRUE)
}

fec_api_rows_as_list <- function(rows) {
  if (is.null(rows)) return(list())
  if (is.data.frame(rows)) {
    return(lapply(seq_len(nrow(rows)), function(index) as.list(rows[index, , drop = FALSE])))
  }
  if (!is.list(rows)) {
    stop("FEC API results must be a list of records.", call. = FALSE)
  }
  rows
}

fec_api_parse_page <- function(response) {
  payload <- fec_api_json(response)
  rows <- fec_api_rows_as_list(payload$results %||% list())
  pagination <- payload$pagination %||% list()
  last_indexes <- pagination$last_indexes %||% list()
  if (!is.list(last_indexes)) {
    stop("FEC API pagination.last_indexes must be an object.", call. = FALSE)
  }
  if (length(last_indexes)) {
    last_indexes <- fec_api_validate_cursor(last_indexes, "API response")
  }
  sub_ids <- vapply(rows, function(row) {
    trim_text(row_scalar(row, "sub_id", ""))
  }, character(1L))
  if (length(sub_ids) && any(!nzchar(sub_ids))) {
    stop("FEC API returned a schedule_a row without sub_id.", call. = FALSE)
  }
  if (length(rows)) rows <- rows[order(sub_ids, method = "radix")]
  numeric_count <- suppressWarnings(as.numeric(pagination$count %||% NA_real_))
  count <- if (length(numeric_count)==1L && is.finite(numeric_count) && numeric_count >= 0 &&
               numeric_count == floor(numeric_count) && numeric_count <= .Machine$integer.max) as.integer(numeric_count) else NA_integer_
  list(rows = rows, last_indexes = last_indexes, count = count,
       is_count_exact = isTRUE(pagination$is_count_exact),
       source_response = list(status = as.integer(response$status %||% 200L),
         body = if (is.character(response$body) && length(response$body) == 1L && nzchar(response$body)) {
           response$body
         } else mentat_evidence_json(payload)))
}

# Archive only public response bodies and the allow-listed request specification.
# Never persist transport headers, API keys, local pool tokens or transport objects.
fec_api_archive_response <- function(connection, spec, response, page = -1L,
                                     payload_kind = "fec_api_response_body") {
  body <- response$body
  if (!is.character(body) || length(body) != 1L) {
    body <- mentat_evidence_json(response$json %||% body %||% list())
  }
  allowed_params <- c("per_page", "two_year_transaction_period", "sort", "contributor_employer",
    "contributor_name", "min_date", "max_date", "contributor_zip", "contributor_city",
    "contributor_state", "last_index", "last_contribution_receipt_date")
  query <- mentat_evidence_json(list(schema_version = 1L,
    request = list(id = spec$id, url = FEC_API_ENDPOINT,
      params = spec$params[intersect(names(spec$params), allowed_params)])))
  archive_hash <- mentat_evidence_sha256(body)
  query_hash <- mentat_evidence_sha256(query)
  status <- as.integer(response$status %||% NA_integer_)
  unit_id <- as.character(spec$id %||% "")
  observation_id <- mentat_evidence_sha256(mentat_evidence_json(list(
    archive_sha256 = archive_hash, query_sha256 = query_hash,
    unit_id = unit_id, page = as.integer(page), http_status = status, payload_kind = payload_kind)))
  observed_at <- format(Sys.time(), "%Y-%m-%dT%H:%M:%OS3%z")
  DBI::dbExecute(connection, paste(
    "INSERT OR IGNORE INTO fec_response_archive",
    "(archive_sha256,schema_version,payload_kind,payload_json,created_at) VALUES (?,1,?,?,?)"),
    params = list(archive_hash, payload_kind, body, observed_at))
  DBI::dbExecute(connection, paste(
    "INSERT INTO fec_response_observations",
    "(observation_id,archive_sha256,unit_id,query_sha256,query_json,page,http_status,observed_at,last_observed_at)",
    "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(observation_id) DO UPDATE SET last_observed_at=excluded.last_observed_at"),
    params = list(observation_id, archive_hash, unit_id, query_hash, query,
                  as.integer(page), status, observed_at, observed_at))
  invisible(archive_hash)
}

fec_api_archiving_transport <- function(connection, transport) {
  force(connection); force(transport)
  function(specs, api_key, concurrency) {
    responses <- transport(specs, api_key, concurrency)
    # Preserve responses before parsing, count validation or retries can fail.
    DBI::dbWithTransaction(connection, {
      for (index in seq_along(responses)) {
        fec_api_archive_response(connection, specs[[index]], responses[[index]])
      }
    })
    responses
  }
}

fec_api_work_contract <- function(unit) {
  # Never resume an old query under changed spelling, aliases, or validation.
  as.character(jsonlite::toJSON(list(version = 2L,
    request = fec_api_request_spec(unit), company = unit$company,
    company_variants = as.list(unit$company_variants %||% unit$variants),
    exclusions = as.list(unit$exclusions %||% character())),
    auto_unbox = TRUE, null = "null", digits = NA))
}

fec_api_checkpoint_page <- function(connection, state, page) {
  contract <- fec_api_work_contract(state$unit)
  if (isTRUE(page$decomposed) && !isTRUE(page$coverage_method %in%
      c("exhaustive_date_windows", "exhaustive_spelling_groups"))) {
    value <- jsonlite::fromJSON(contract, simplifyVector = FALSE)
    value$coverage_verified <- FALSE
    value$coverage_reason <- "Observed spellings do not prove the failed broad query was covered"
    contract <- as.character(jsonlite::toJSON(value,auto_unbox=TRUE,null="null",digits=NA))
  }
  # A resumed unit must know which reconciliation pass produced these pages.
  page$reconcile_pass <- as.integer(state$reconcile_pass %||% 0L)
  payload <- jsonlite::toJSON(
    page,
    auto_unbox = TRUE,
    null = "null",
    na = "null",
    digits = NA
  )
  cursor <- state$last_indexes %||% list()
  DBI::dbWithTransaction(connection, {
    response <- page$source_response
    if (is.null(response)) {
      # Legacy checkpoints and a synthetic date-window union are preserved as
      # parsed evidence; they must not be relabelled original HTTP responses.
      response <- list(status = NA_integer_, body = payload)
    }
    fec_api_archive_response(connection,
      page$source_request %||% fec_api_request_spec(state$unit), response, page = page$page_number,
      payload_kind = if (is.null(page$source_response)) "parsed_checkpoint" else "fec_api_response_body")
    if (isTRUE(page$decomposed)) {
      DBI::dbExecute(
        connection,
        "DELETE FROM api_page_checkpoints WHERE unit_id=?",
        params = list(state$unit$id)
      )
    }
    DBI::dbExecute(
      connection,
      paste(
        "INSERT INTO api_page_checkpoints (unit_id, page, payload_json)",
        "VALUES (?, ?, ?) ON CONFLICT(unit_id, page) DO UPDATE SET",
        "payload_json=excluded.payload_json"
      ),
      params = list(state$unit$id, as.integer(page$page_number), payload)
    )
    DBI::dbExecute(
      connection,
      paste(
        "INSERT INTO api_work_progress",
        "(unit_id, company, cycle, kind, last_page, rows_seen, records_total,",
        "last_index, last_contribution_receipt_date, seen_keysets,",
        "fetched_complete, stored_complete, updated_at, query_contract)",
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)",
        "ON CONFLICT(unit_id) DO UPDATE SET",
        "last_page=excluded.last_page, rows_seen=excluded.rows_seen,",
        "records_total=excluded.records_total, last_index=excluded.last_index,",
        "last_contribution_receipt_date=excluded.last_contribution_receipt_date,",
        "seen_keysets=excluded.seen_keysets,",
        "fetched_complete=excluded.fetched_complete, updated_at=excluded.updated_at,",
        "query_contract=excluded.query_contract, stored_complete=0"
      ),
      params = list(
        state$unit$id,
        state$unit$company,
        as.integer(state$unit$cycle),
        state$unit$kind,
        as.integer(page$page_number),
        as.integer(state$rows_seen),
        if (is.na(state$expected_count)) NA_integer_ else as.integer(state$expected_count),
        trim_text(cursor$last_index %||% ""),
        trim_text(cursor$last_contribution_receipt_date %||% ""),
        paste(state$seen_keysets, collapse = "\n"),
        as.integer(state$complete),
        format(Sys.time(), "%Y-%m-%dT%H:%M:%OS3%z"),
        contract
      )
    )
  })
  invisible(TRUE)
}

fec_api_load_progress <- function(connection, units) {
  if (!length(units)) return(list())
  progress <- DBI::dbGetQuery(
    connection,
    "SELECT * FROM api_work_progress ORDER BY unit_id"
  )
  if (!nrow(progress)) return(list())
  unit_ids <- vapply(units, function(unit) unit$id, character(1L))
  expected_units <- c(units, unlist(lapply(units, fec_api_date_windows), recursive = FALSE))
  expected_ids <- vapply(expected_units, function(unit) unit$id, character(1L))
  relevant <- progress$unit_id %in% unit_ids
  for (id in unit_ids) relevant <- relevant | startsWith(progress$unit_id, paste0(id, "::window::"))
  progress <- progress[relevant, , drop = FALSE]
  # Paul's ruling: unresumable saved work is a per-unit fact, never lane-fatal.
  # Drop that unit's checkpoints and progress row, say so, and collect it fresh.
  # The evidence archive tables are never touched by this.
  discard <- function(unit_id, reason) {
    DBI::dbWithTransaction(connection, {
      DBI::dbExecute(
        connection,
        "DELETE FROM api_page_checkpoints WHERE unit_id=?",
        params = list(unit_id)
      )
      DBI::dbExecute(
        connection,
        "DELETE FROM api_work_progress WHERE unit_id=?",
        params = list(unit_id)
      )
    })
    cat(sprintf(
      "Reconcile: saved work for %s is not resumable (%s); re-collecting.\n",
      unit_id, reason
    ))
    invisible(NULL)
  }
  restored <- list()
  for (index in seq_len(nrow(progress))) {
    row <- progress[index, , drop = FALSE]
    unit_id <- as.character(row$unit_id[[1L]])
    expected_index <- match(unit_id, expected_ids)
    if (is.na(expected_index)) {
      discard(unit_id, "unrecognized saved FEC query window")
      next
    }
    unit <- expected_units[[expected_index]]
    if (!"query_contract" %in% names(row) ||
        !identical(as.character(row$query_contract[[1L]]), fec_api_work_contract(unit))) {
      discard(unit_id, "saved work differs from the current query contract")
      next
    }
    stored <- isTRUE(as.integer(row$stored_complete[[1L]]) == 1L)
    pages <- list()
    if (!stored) {
      payloads <- DBI::dbGetQuery(
        connection,
        paste(
          "SELECT page, payload_json FROM api_page_checkpoints",
          "WHERE unit_id=? ORDER BY page"
        ),
        params = list(unit_id)
      )
      if (nrow(payloads)) {
        pages <- lapply(payloads$payload_json, function(payload) {
          jsonlite::fromJSON(payload, simplifyVector = FALSE, bigint_as_char = TRUE)
        })
      }
    }
    if (length(pages) && any(vapply(pages, function(page) {
      nzchar(page$validation_failure %||% "")
    }, logical(1L)))) {
      discard(unit_id, "saved pages failed snapshot validation")
      next
    }
    cursor <- list()
    if (nzchar(trim_text(row$last_index[[1L]])) ||
        nzchar(trim_text(row$last_contribution_receipt_date[[1L]]))) {
      cursor <- fec_api_validate_cursor(
        list(
          last_index = row$last_index[[1L]],
          last_contribution_receipt_date = row$last_contribution_receipt_date[[1L]]
        ),
        unit_id
      )
    }
    seen <- trim_text(row$seen_keysets[[1L]])
    seen <- if (nzchar(seen)) strsplit(seen, "\n", fixed = TRUE)[[1L]] else character()
    saved_passes <- vapply(pages, function(page) {
      suppressWarnings(as.integer(page$reconcile_pass %||% 0L))
    }, integer(1L))
    saved_passes <- saved_passes[!is.na(saved_passes)]
    restored[[unit_id]] <- list(
      last_indexes = cursor,
      seen_keysets = seen,
      expected_count = suppressWarnings(as.integer(row$records_total[[1L]])),
      page = as.integer(row$last_page[[1L]]) + 1L,
      rows_seen = as.integer(row$rows_seen[[1L]]),
      pages = pages,
      reconcile_pass = if (length(saved_passes)) max(saved_passes) else 0L,
      complete = stored || isTRUE(as.integer(row$fetched_complete[[1L]]) == 1L),
      stored_complete = stored
    )
  }
  restored
}

fec_api_mark_work_unit_stored <- function(connection, unit_id) {
  DBI::dbExecute(
    connection,
    paste(
      "UPDATE api_work_progress SET stored_complete=1, updated_at=?",
      "WHERE unit_id=?"
    ),
    params = list(format(Sys.time(), "%Y-%m-%dT%H:%M:%OS3%z"), unit_id)
  )
  DBI::dbExecute(
    connection,
    "DELETE FROM api_page_checkpoints WHERE unit_id=?",
    params = list(unit_id)
  )
  invisible(TRUE)
}

reset_api_progress <- function(connection, tasks) {
  reset <- 0L
  DBI::dbWithTransaction(connection, {
    for (task in tasks) {
      # A fresh API task covers the entire company-cycle. Remove every older
      # browser/API progress row for that cell before repopulating it; otherwise
      # export guards sum historical and current FEC counts and falsely report
      # a complete refresh as short.
      DBI::dbExecute(
        connection,
        "DELETE FROM scrape_progress WHERE company=? AND cycle=?",
        params = list(task$company, as.integer(task$cycle))
      )
      unit_ids <- DBI::dbGetQuery(
        connection,
        paste(
          "SELECT unit_id FROM api_work_progress",
          "WHERE company=? AND cycle=?"
        ),
        params = list(task$company, as.integer(task$cycle))
      )$unit_id
      if (!length(unit_ids)) next
      placeholders <- paste(rep("?", length(unit_ids)), collapse = ",")
      DBI::dbExecute(
        connection,
        paste0(
          "DELETE FROM api_page_checkpoints WHERE unit_id IN (",
          placeholders,
          ")"
        ),
        params = as.list(unit_ids)
      )
      DBI::dbExecute(
        connection,
        paste0(
          "DELETE FROM api_work_progress WHERE unit_id IN (",
          placeholders,
          ")"
        ),
        params = as.list(unit_ids)
      )
      reset <- reset + length(unit_ids)
    }
  })
  as.integer(reset)
}

fec_api_keyset_signature <- function(indexes) {
  if (!length(indexes)) return("")
  names_sorted <- sort(names(indexes), method = "radix")
  paste(vapply(names_sorted, function(name) {
    paste0(name, "=", as.character(indexes[[name]]))
  }, character(1L)), collapse = "&")
}

fec_api_decomposition_queries <- function(unit) {
  values <- unit$decomposition_variants %||% unit$company_variants %||% character()
  values <- trim_text(values)
  values <- values[nzchar(values)]
  if (!length(values)) return(character())
  failed_keys <- unique(normalize_variant_key(unit$variants))
  values <- values[!normalize_variant_key(values) %in% failed_keys]
  if (!length(values)) return(character())
  values <- values[order(toupper(values), values, method = "radix")]
  values[!duplicated(toupper(values))]
}

fec_api_decomposition_units <- function(unit, queries = fec_api_decomposition_queries(unit)) {
  if (!length(queries)) return(list())
  lapply(seq_along(queries), function(index) {
    child <- unit
    child$id <- paste0(unit$id, "::decomposed::", sprintf("%04d", index))
    child$variants <- queries[[index]]
    child$decomposition_variants <- character()
    child$is_decomposition <- TRUE
    child$preserve_query_spelling <- identical(
      unit$decomposition_source %||% "roster",
      "observed_employers"
    )
    child
  })
}

# A multi-value query is an OR over its spellings, so its population is the
# union of its spelling groups' populations. Halving (down to one spelling)
# keeps every group a complete query of its own, provable by its own count.
fec_api_spelling_groups <- function(unit) {
  values <- trim_text(unit$variants)
  values <- values[nzchar(values) & !duplicated(toupper(values))]
  if (length(values) < 2L) return(list())
  half <- ceiling(length(values) / 2)
  lapply(list(values[seq_len(half)], values[-seq_len(half)]), function(group) {
    child <- unit
    child$id <- paste0(unit$id, "::spelling::", paste(group, collapse = "|"))
    child$variants <- group
    child
  })
}

fec_api_state_rows <- function(state) {
  unlist(lapply(state$pages %||% list(), function(page) page$rows), recursive = FALSE)
}

fec_api_union_rows <- function(rows) {
  if (!length(rows)) return(list())
  sub_ids <- vapply(rows, function(row) {
    trim_text(row_scalar(row, "sub_id", ""))
  }, character(1L))
  if (any(!nzchar(sub_ids))) {
    stop("A decomposed FEC API result contained a blank sub_id.", call. = FALSE)
  }
  order_index <- order(sub_ids, method = "radix")
  rows <- rows[order_index]
  sub_ids <- sub_ids[order_index]
  rows[!duplicated(sub_ids)]
}

fec_api_date_windows <- function(unit) {
  # Unbounded outer windows preserve dates outside the nominal election cycle.
  # The parent count and row-ID checks below gate publication of the union.
  if (length(unit$query_filters) || isTRUE(unit$is_decomposition) || isTRUE(unit$is_window)) return(list())
  cuts <- seq(as.Date(sprintf("%d-01-01", unit$cycle - 1L)), as.Date(sprintf("%d-01-01", unit$cycle + 1L)), by = "month")
  lapply(seq_len(length(cuts) + 1L), function(index) {
    child <- unit
    child$id <- paste0(unit$id, "::window::", sprintf("%02d", index))
    child$is_window <- TRUE
    child$query_filters <- list()
    if (index > 1L) child$query_filters$min_date <- format(cuts[[index - 1L]], "%m/%d/%Y")
    if (index <= length(cuts)) child$query_filters$max_date <- format(cuts[[index]] - 1, "%m/%d/%Y")
    child
  })
}

fec_api_combine_date_windows <- function(state, child_result, expected_count = NULL) {
  # Pure combine. Population verification against a FRESH parent count lives in
  # fec_api_collect_windows_verified; a split-time count is only a hint now.
  # Blank or duplicate ids still mean overlapping windows: a bug, not API drift.
  child_rows <- unlist(lapply(child_result$units, fec_api_state_rows), recursive = FALSE)
  ids <- vapply(child_rows, function(row) trim_text(row_scalar(row,"sub_id","")), character(1L))
  if (any(!nzchar(ids)) || anyDuplicated(ids) ||
      (!is.null(expected_count) && length(ids) != as.integer(expected_count))) {
    stop("Date-window coverage is partial or overlapping: ", state$unit$id)
  }
  count <- length(ids)
  state$pages <- list(list(rows=child_rows,last_indexes=list(),count=count,
    is_count_exact=TRUE,rows_collected=count,page_number=0L,complete=TRUE,
    decomposed=TRUE,coverage_method="exhaustive_date_windows"))
  state$last_indexes <- list(); state$seen_keysets <- character(); state$expected_count <- count
  state$page <- 1L; state$rows_seen <- count; state$rows_collected_run <- count
  state$seen_sub_ids <- ids; state$complete <- TRUE; state$stored_complete <- FALSE
  state$coverage_verified <- TRUE
  state
}

# Paul's ruling: a date-window union is published only against a count observed
# in the SAME pass that produced the rows. Collect the windows, re-fetch the
# parent page, and re-walk only the windows whose own count moved. Nothing
# unverified is returned; the split-time parent count is a hint, not proof.
fec_api_collect_windows_verified <- function(
  state, windows, api_key, concurrency, transport, sleep, jitter,
  initial_states = list(), page_callback = NULL,
  before_request = function(count) invisible(NULL), reset_callback = NULL,
  limit = fec_api_reconcile_passes(), probe_rows = list(),
  abandon_unproven = FALSE
) {
  requests <- 0L; retries <- 0L; rate_limits <- 0L; child_reconciles <- 0L
  absorb <- function(outcome) {
    requests <<- requests + as.integer(outcome$request_count)
    retries <<- retries + as.integer(outcome$retry_count)
    rate_limits <<- rate_limits + as.integer(outcome$rate_limit_count)
    child_reconciles <<- child_reconciles + as.integer(outcome$reconcile_count %||% 0L)
    invisible(NULL)
  }
  collect <- function(targets, states) {
    outcome <- fec_api_collect_work_units(targets, api_key = api_key,
      concurrency = concurrency, transport = transport, sleep = sleep, jitter = jitter,
      initial_states = states, page_callback = page_callback,
      before_request = before_request, reset_callback = reset_callback)
    absorb(outcome)
    outcome
  }
  # One page-1 request. NULL means that query itself is unservable, which is not
  # a moving population: the caller then verifies window by window instead. An
  # estimated count is likewise no verdict on the data but a refusal to count;
  # only a caller that can verify another way passes allow_estimate, and every
  # page carries count_exact so that caller can tell the two apart.
  probe <- function(unit, allow_estimate = FALSE) {
    fetched <- fec_api_fetch_with_retry(list(fec_api_request_spec(unit)), api_key = api_key,
      concurrency = concurrency, transport = transport, sleep = sleep, jitter = jitter,
      before_request = before_request)
    absorb(fetched)
    if (length(fetched$unservable_queries)) return(NULL)
    page <- fec_api_parse_page(fetched$responses[[1L]])
    page$count_exact <- isTRUE(page$is_count_exact) && !is.na(page$count) && page$count >= 0L
    if (!page$count_exact && !isTRUE(allow_estimate)) {
      stop("FEC API requires an exact nonnegative pagination.count: ", unit$id,
           ". Response preserved; split or rerun the query.", call. = FALSE)
    }
    page
  }
  child_states <- collect(windows, initial_states)$units
  # A window that was itself unservable comes back as narrower spellings, which
  # can never prove the window's population; re-walking it repeats the same
  # outcome every pass. When the split was optional (the parent query served
  # its first page), hand back state = NULL so the caller walks the parent
  # cursor instead of failing a query that was never unservable. The abandoned
  # windows' ids go back too, so the caller can drop their checkpoints.
  if (isTRUE(abandon_unproven) && any(vapply(child_states,
      function(child) identical(child$coverage_verified, FALSE), logical(1L)))) {
    return(list(state = NULL, request_count = requests, retry_count = retries,
      rate_limit_count = rate_limits, reconcile_count = child_reconciles,
      abandoned_unit_ids = unname(vapply(windows, function(window) window$id, character(1L)))))
  }
  # One page-1 request per window, shared by the window-count sum and the
  # stale-window search so that one pass never asks the same window twice.
  probe_windows <- function(allow_estimate) {
    pages <- list()
    for (name in names(child_states)) {
      if (identical(child_states[[name]]$coverage_verified, FALSE)) {
        pages[name] <- list(NULL)
        next
      }
      pages[name] <- list(probe(child_states[[name]]$unit, allow_estimate = allow_estimate))
    }
    pages
  }
  # The windows partition the parent population, so their own exact counts sum
  # to it. A window that will not report one cannot stand in for the parent's
  # refusal to count: nothing unverified is stored for it.
  window_count_total <- function(pages) {
    total <- 0L
    for (name in names(child_states)) {
      page <- pages[[name]]
      if (is.null(page)) {
        stop("FEC API date window ", name, " remained unservable, so the population of ",
          state$unit$id, " cannot be verified against the window counts. ",
          "Nothing unverified was stored.", call. = FALSE)
      }
      if (!isTRUE(page$count_exact)) {
        stop("FEC API date window ", name, " reported an estimated pagination.count, so the ",
          "population of ", state$unit$id, " cannot be verified against the window counts. ",
          "Nothing unverified was stored.", call. = FALSE)
      }
      total <- total + as.integer(page$count)
    }
    total
  }
  observed <- c(fec_api_state_rows(state), probe_rows)
  reconcile_log <- character()
  ids <- character()
  pass <- 0L
  repeat {
    parent <- probe(state$unit, allow_estimate = TRUE)
    if (!is.null(parent)) observed <- c(observed, parent$rows)
    union_rows <- unlist(lapply(child_states, fec_api_state_rows), recursive = FALSE)
    ids <- vapply(union_rows, function(row) trim_text(row_scalar(row,"sub_id","")), character(1L))
    if (any(!nzchar(ids)) || anyDuplicated(ids)) {
      stop("Date-window coverage is partial or overlapping: ", state$unit$id)
    }
    unproven <- names(child_states)[vapply(child_states,
      function(child) identical(child$coverage_verified, FALSE), logical(1L))]
    # An estimated parent count is a refusal to count, not a verdict on the
    # data. The union is then verified against the windows' own exact counts,
    # which partition the same population.
    window_sum <- !length(unproven) && !is.null(parent) && !isTRUE(parent$count_exact)
    window_pages <- if (window_sum) probe_windows(TRUE) else NULL
    window_total <- if (window_sum) window_count_total(window_pages) else NA_integer_
    reason <- if (length(unproven)) {
      paste0(length(unproven),
        " date window(s) were replaced by unproven narrower spellings")
    } else if (is.null(parent)) {
      NA_character_
    } else if (window_sum) {
      if (length(ids) != window_total) {
        paste0("windows hold ", length(ids),
          " rows against a window-count sum of ", window_total)
      } else NA_character_
    } else if (length(ids) != parent$count) {
      paste0("windows hold ", length(ids),
        " rows against a fresh parent count of ", parent$count)
    } else NA_character_
    if (is.na(reason)) {
      if (is.null(parent)) {
        reconcile_log <- c(reconcile_log,
          "parent count unavailable (query remained unservable); verified per window")
      } else if (window_sum) {
        reconcile_log <- c(reconcile_log,
          paste0("verified against the sum of ", length(window_pages),
            " exact window counts (parent count estimated)"))
      }
      break
    }
    if (pass >= limit) {
      reconcile_log <- c(reconcile_log, paste0("pass ", pass + 1L, ": ", reason))
      stop("FEC API date-window population for ", state$unit$id,
        " did not verify after ", limit, " passes: ",
        paste(reconcile_log, collapse = "; "),
        ". Nothing unverified was stored.", call. = FALSE)
    }
    pass <- pass + 1L
    reconcile_log <- c(reconcile_log, paste0("pass ", pass, ": ", reason))
    cat(sprintf("Reconcile pass %d for %s: %s\n", pass, state$unit$id, reason))
    if (is.null(window_pages)) window_pages <- probe_windows(FALSE)
    stale <- character()
    for (name in names(child_states)) {
      child <- child_states[[name]]
      if (identical(child$coverage_verified, FALSE)) {
        stale <- c(stale, name)
        next
      }
      window_page <- window_pages[[name]]
      if (is.null(window_page)) next
      if (!identical(as.integer(window_page$count), length(fec_api_state_rows(child)))) {
        stale <- c(stale, name)
      }
    }
    if (!length(stale)) next
    for (name in stale) if (is.function(reset_callback)) reset_callback(child_states[[name]])
    refreshed <- collect(lapply(stale, function(name) child_states[[name]]$unit), list())
    for (name in names(refreshed$units)) child_states[[name]] <- refreshed$units[[name]]
  }
  prior <- unique(vapply(observed,
    function(row) trim_text(row_scalar(row,"sub_id","")), character(1L)))
  absent <- sum(!prior %in% ids)
  if (absent > 0L) {
    reconcile_log <- c(reconcile_log,
      paste0(absent, " previously observed rows absent from the verified population"))
    cat(sprintf("Reconcile: %s -> %d previously observed row(s) absent from the verified population\n",
      state$unit$id, absent))
  }
  combined <- fec_api_combine_date_windows(state, list(units = child_states), length(ids))
  combined$reconcile_pass <- as.integer(state$reconcile_pass %||% 0L) + pass
  combined$reconcile_log <- c(state$reconcile_log %||% character(), reconcile_log)
  list(state = combined, request_count = requests, retry_count = retries,
    rate_limit_count = rate_limits, reconcile_count = pass + child_reconciles)
}

fec_api_collect_work_units <- function(
  units,
  api_key = fec_api_key(),
  concurrency = fec_api_concurrency(),
  transport = fec_api_default_transport,
  sleep = Sys.sleep,
  jitter = stats::runif,
  initial_states = list(),
  page_callback = NULL,
  before_request = function(count) invisible(NULL),
  reset_callback = NULL,
  discard_callback = NULL
) {
  if (!length(units)) {
    return(list(
      units = list(), request_count = 0L, retry_count = 0L,
      rate_limit_count = 0L, rows = 0L, rows_total = 0L,
      final_concurrency = as.integer(concurrency), wall_clock_seconds = 0,
      decompositions = list(), reconcile_count = 0L
    ))
  }
  unit_ids <- vapply(units, function(unit) unit$id, character(1L))
  if (any(!nzchar(unit_ids)) || anyDuplicated(unit_ids)) {
    stop("FEC API work-unit IDs must be non-empty and unique.", call. = FALSE)
  }
  units <- units[order(unit_ids, method = "radix")]
  reconcile_limit <- fec_api_reconcile_passes()
  states <- lapply(units, function(unit) {
    restored <- initial_states[[unit$id]] %||% list()
    restored_pages <- restored$pages %||% list()
    restored_sub_ids <- vapply(
      fec_api_state_rows(list(pages = restored_pages)),
      function(row) trim_text(row_scalar(row, "sub_id", "")),
      character(1L)
    )
    if (
      any(!nzchar(restored_sub_ids)) ||
      anyDuplicated(restored_sub_ids)
    ) {
      stop(
        "Saved FEC API checkpoints contain blank or repeated sub_id(s) for ",
        "work unit: ",
        unit$id,
        call. = FALSE
      )
    }
    list(
      unit = unit,
      last_indexes = restored$last_indexes %||% list(),
      seen_keysets = restored$seen_keysets %||% character(),
      expected_count = restored$expected_count %||% NA_integer_,
      page = restored$page %||% 0L,
      rows_seen = restored$rows_seen %||% 0L,
      rows_collected_run = 0L,
      pages = restored_pages,
      seen_sub_ids = restored_sub_ids,
      complete = isTRUE(restored$complete),
      stored_complete = isTRUE(restored$stored_complete),
      reconcile_pass = as.integer(restored$reconcile_pass %||% 0L),
      reconcile_log = as.character(restored$reconcile_log %||% character()),
      coverage_verified = TRUE
    )
  })
  queue <- which(!vapply(states, function(state) state$complete, logical(1L)))
  request_count <- 0L
  retry_count <- 0L
  rate_limit_count <- 0L
  decompositions <- list()
  reconcile_count <- 0L
  current_concurrency <- as.integer(concurrency)
  started <- proc.time()[["elapsed"]]
  while (length(queue)) {
    selected <- head(queue, current_concurrency)
    rest <- setdiff(queue, selected)
    specs <- lapply(selected, function(index) {
      fec_api_request_spec(states[[index]]$unit, states[[index]]$last_indexes)
    })
    fetched <- fec_api_fetch_with_retry(
      specs,
      api_key = api_key,
      concurrency = current_concurrency,
      transport = transport,
      sleep = sleep,
      jitter = jitter,
      before_request = before_request
    )
    request_count <- request_count + fetched$request_count
    retry_count <- retry_count + fetched$retry_count
    rate_limit_count <- rate_limit_count + fetched$rate_limit_count
    if (fetched$rate_limit_count > 0L) {
      current_concurrency <- max(1L, current_concurrency %/% 2L)
    } else if (current_concurrency < as.integer(concurrency)) {
      current_concurrency <- current_concurrency + 1L
    }
    unfinished <- integer()
    for (offset in seq_along(selected)) {
      index <- selected[[offset]]
      state <- states[[index]]
      if (offset %in% fetched$unservable_queries) {
        windows <- fec_api_date_windows(state$unit)
        if (length(windows)) {
          cat(sprintf("Unservable query: %s -> %d exhaustive date windows\n",state$unit$id,length(windows)))
          verified <- fec_api_collect_windows_verified(state,windows,api_key,concurrency,
            transport,sleep,jitter,initial_states=initial_states,
            page_callback=page_callback,before_request=before_request,
            reset_callback=reset_callback,limit=reconcile_limit)
          state <- verified$state
          if (is.function(page_callback)) page_callback(state,state$pages[[1L]])
          states[[index]] <- state
          request_count <- request_count + verified$request_count
          retry_count <- retry_count + verified$retry_count
          rate_limit_count <- rate_limit_count + verified$rate_limit_count
          reconcile_count <- reconcile_count + verified$reconcile_count
          next
        }
        # Berkshire Hathaway 2020: the 10-spelling employer query timed out even
        # inside one-month windows. Split the spellings; each group must verify
        # against its own exact count (or split again). No group stands in for
        # the whole: the union is accepted only when every group verified.
        groups <- fec_api_spelling_groups(state$unit)
        if (length(groups)) {
          cat(sprintf("Unservable query: %s -> %d spelling groups\n", state$unit$id, length(groups)))
          split_result <- fec_api_collect_work_units(groups, api_key = api_key,
            concurrency = current_concurrency, transport = transport, sleep = sleep,
            jitter = jitter, before_request = before_request)
          request_count <- request_count + split_result$request_count
          retry_count <- retry_count + split_result$retry_count
          rate_limit_count <- rate_limit_count + split_result$rate_limit_count
          reconcile_count <- reconcile_count + (split_result$reconcile_count %||% 0L)
          proven <- vapply(split_result$units, function(child) {
            isTRUE(child$complete) && !identical(child$coverage_verified, FALSE) &&
              identical(as.integer(child$expected_count), length(fec_api_state_rows(child)))
          }, logical(1L))
          if (!all(proven)) {
            stop("FEC API spelling group(s) did not verify against their own exact count for ",
              state$unit$id, ": ", paste(names(split_result$units)[!proven], collapse = ", "),
              ". Nothing unverified was stored.", call. = FALSE)
          }
          union_rows <- fec_api_union_rows(unlist(lapply(split_result$units,
            fec_api_state_rows), recursive = FALSE))
          count <- length(union_rows)
          state$pages <- list(list(rows = union_rows, last_indexes = list(), count = count,
            is_count_exact = TRUE, rows_collected = count, page_number = 0L, complete = TRUE,
            decomposed = TRUE, coverage_method = "exhaustive_spelling_groups"))
          state$last_indexes <- list(); state$seen_keysets <- character()
          state$expected_count <- count; state$page <- 1L; state$rows_seen <- count
          state$rows_collected_run <- count
          state$seen_sub_ids <- vapply(union_rows,
            function(row) trim_text(row_scalar(row, "sub_id", "")), character(1L))
          state$complete <- TRUE; state$stored_complete <- FALSE
          state$coverage_verified <- TRUE
          if (is.function(page_callback)) page_callback(state, state$pages[[1L]])
          states[[index]] <- state
          next
        }
        if (isTRUE(state$unit$is_decomposition)) {
          current_page_size <- suppressWarnings(as.integer(
            state$unit$per_page %||% FEC_API_PER_PAGE
          ))
          smaller_page_size <- fec_api_smaller_page_size(state$unit)
          if (!is.na(smaller_page_size)) {
            cat(sprintf(
              paste0(
                "unservable_decomposed_query: %s cycle %d per_page %d",
                " -> retrying from page 1 at per_page %d\n"
              ),
              paste(state$unit$variants, collapse = "|"),
              as.integer(state$unit$cycle),
              current_page_size,
              smaller_page_size
            ))
            state$unit$per_page <- smaller_page_size
            state$last_indexes <- list()
            state$seen_keysets <- character()
            state$expected_count <- NA_integer_
            state$page <- 0L
            state$rows_seen <- 0L
            state$rows_collected_run <- 0L
            state$pages <- list()
            state$seen_sub_ids <- character()
            state$complete <- FALSE
            state$stored_complete <- FALSE
            states[[index]] <- state
            unfinished <- c(unfinished, index)
            next
          }
          stop(
            "Decomposed FEC API query remained unservable; refusing a partial ",
            "parent result: ",
            paste(state$unit$variants, collapse = "|"),
            " cycle ", state$unit$cycle,
            call. = FALSE
          )
        }
        queries <- fec_api_decomposition_queries(state$unit)
        children <- fec_api_decomposition_units(state$unit, queries)
        if (!length(children)) {
          stop(
            "Unservable FEC API query has no decomposition spellings; ",
            "refusing an unverified zero: ",
            paste(state$unit$variants, collapse = "|"),
            " cycle ", state$unit$cycle,
            call. = FALSE
          )
        }
        child_result <- if (length(children)) {
          fec_api_collect_work_units(
            children,
            api_key = api_key,
            concurrency = current_concurrency,
            transport = transport,
            sleep = sleep,
            jitter = jitter,
            initial_states = list(),
            page_callback = NULL,
            before_request = before_request,
            reset_callback = NULL
          )
        } else {
          stop("unreachable: decomposition children were checked above")
        }
        request_count <- request_count + child_result$request_count
        retry_count <- retry_count + child_result$retry_count
        rate_limit_count <- rate_limit_count + child_result$rate_limit_count
        reconcile_count <- reconcile_count + (child_result$reconcile_count %||% 0L)
        child_names <- sort(names(child_result$units), method = "radix")
        child_rows <- unlist(lapply(child_names, function(name) {
          fec_api_state_rows(child_result$units[[name]])
        }), recursive = FALSE)
        union_rows <- fec_api_union_rows(c(fec_api_state_rows(state), child_rows))
        union_count <- length(union_rows)
        state$last_indexes <- list()
        state$seen_keysets <- character()
        state$expected_count <- as.integer(union_count)
        state$page <- 1L
        state$rows_seen <- as.integer(union_count)
        state$rows_collected_run <- as.integer(union_count)
        state$complete <- TRUE
        state$stored_complete <- FALSE
        state$coverage_verified <- FALSE
        state$pages <- list(list(
          rows = union_rows,
          last_indexes = list(),
          count = as.integer(union_count),
          rows_collected = as.integer(union_count),
          page_number = 0L,
          complete = TRUE,
          decomposed = TRUE
        ))
        event <- list(
          unit_id = state$unit$id,
          company = state$unit$company,
          cycle = as.integer(state$unit$cycle),
          term = paste(state$unit$variants, collapse = " | "),
          queries = as.list(queries),
          query_count = length(queries),
          row_count = union_count
        )
        decompositions[[length(decompositions) + 1L]] <- event
        cat(sprintf(
          "unservable_query: %s cycle %d -> decomposed into %d queries, %d rows\n",
          event$term,
          event$cycle,
          event$query_count,
          event$row_count
        ))
        if (is.function(page_callback)) {
          page_callback(state, state$pages[[1L]])
        }
        states[[index]] <- state
        next
      }
      page <- fec_api_parse_page(fetched$responses[[offset]])
      page$source_request <- specs[[offset]]
      if (!isTRUE(page$is_count_exact) || is.na(page$count) || page$count < 0L) {
        # An estimated count is a refusal to count, not a verdict on the data.
        # Verify the population by exhaustive date windows rather than fail.
        estimate_windows <- fec_api_date_windows(state$unit)
        if (length(estimate_windows)) {
          cat(sprintf("Reconcile: %s reported an estimated count; verifying by %d exhaustive date windows\n",
            state$unit$id, length(estimate_windows)))
          verified <- fec_api_collect_windows_verified(state, estimate_windows, api_key, concurrency,
            transport, sleep, jitter, initial_states = initial_states,
            page_callback = page_callback, before_request = before_request,
            reset_callback = reset_callback, limit = reconcile_limit, probe_rows = page$rows)
          state <- verified$state
          if (is.function(page_callback)) page_callback(state,state$pages[[1L]])
          states[[index]] <- state
          request_count <- request_count + verified$request_count
          retry_count <- retry_count + verified$retry_count
          rate_limit_count <- rate_limit_count + verified$rate_limit_count
          reconcile_count <- reconcile_count + verified$reconcile_count
          next
        }
        # No window to split into: save the response for diagnosis, but never
        # certify an estimated count.
        page$page_number <- state$page
        page$complete <- FALSE
        page$validation_failure <- "exact_count_missing"
        if (is.function(page_callback)) page_callback(state, page)
        stop("FEC API requires an exact nonnegative pagination.count: ", state$unit$id,
             ". Response preserved; split or rerun the query.", call. = FALSE)
      }
      # A large cursor chain cannot occupy idle workers. Split it into disjoint
      # date windows, let the shared pool fetch those windows, and retain one
      # coordinator for all checkpoint/database writes.
      pool_width <- suppressWarnings(as.integer(Sys.getenv("MENTAT_REQUEST_POOL_WIDTH", "0")))
      children <- if (!is.na(pool_width) && pool_width > 1L && !is.na(page$count) && page$count >= 2000L && !isTRUE(state$windows_abandoned)) fec_api_date_windows(state$unit) else list()
      if (length(children)) {
        cat(sprintf("Shared workers: %s -> %d date windows; expected %d rows\n", state$unit$id, length(children), page$count))
        verified <- fec_api_collect_windows_verified(state, children, api_key, concurrency,
          transport, sleep, jitter, initial_states = initial_states,
          page_callback = page_callback, before_request = before_request,
          reset_callback = reset_callback, limit = reconcile_limit, probe_rows = page$rows,
          abandon_unproven = TRUE)
        request_count <- request_count + verified$request_count
        retry_count <- retry_count + verified$retry_count
        rate_limit_count <- rate_limit_count + verified$rate_limit_count
        reconcile_count <- reconcile_count + verified$reconcile_count
        if (!is.null(verified$state)) {
          state <- verified$state
          if (is.function(page_callback)) page_callback(state,state$pages[[1L]])
          states[[index]] <- state
          next
        }
        cat(sprintf("Shared workers: %s has unservable date windows; walking the parent cursor serially\n",
          state$unit$id))
        # The abandoned windows were never stored; left checkpointed they read
        # as incomplete units to the coverage gate (Fintech 2020, Cash App).
        if (is.function(discard_callback)) discard_callback(verified$abandoned_unit_ids)
        # Later pages report the same count; never re-split this walk.
        state$windows_abandoned <- TRUE
      }
      page_sub_ids <- vapply(
        page$rows,
        function(row) trim_text(row_scalar(row, "sub_id", "")),
        character(1L)
      )
      if (any(!nzchar(page_sub_ids))) {
        stop(
          "FEC API page contained a blank sub_id for work unit: ",
          state$unit$id,
          call. = FALSE
        )
      }
      duplicate_sub_ids <- unique(c(
        page_sub_ids[duplicated(page_sub_ids)],
        intersect(page_sub_ids, state$seen_sub_ids)
      ))
      if (length(duplicate_sub_ids)) {
        stop(
          "FEC API pagination repeated sub_id(s) for work unit ",
          state$unit$id, ": ",
          paste(head(duplicate_sub_ids, 5L), collapse = ", "),
          call. = FALSE
        )
      }
      state$seen_sub_ids <- c(state$seen_sub_ids, page_sub_ids)
      state$page <- state$page + 1L
      if (state$page > FEC_API_MAX_PAGES_PER_UNIT) {
        stop(
          "FEC API page ceiling exceeded for work unit: ",
          state$unit$id,
          call. = FALSE
        )
      }
      if (!is.na(page$count)) {
        if (page$count < 0L) {
          stop("FEC API pagination.count cannot be negative.", call. = FALSE)
        }
        if (is.na(state$expected_count)) {
          state$expected_count <- page$count
        } else if (state$expected_count != page$count) {
          # Continue saving evidence, but a moving population is not a verified
          # snapshot, even if its final size equals one previously seen count.
          warning(
            "FEC API pagination.count moved during work unit (continuing): ",
            state$unit$id, " ", state$expected_count, " -> ", page$count,
            call. = FALSE
          )
          state$expected_count <- page$count
        }
      }
      state$rows_seen <- state$rows_seen + length(page$rows)
      state$rows_collected_run <- state$rows_collected_run + length(page$rows)
      page$rows_collected <- state$rows_seen
      page$page_number <- state$page - 1L
      page_position <- length(state$pages) + 1L
      state$pages[[page_position]] <- page
      observed_counts <- vapply(
        state$pages,
        function(saved_page) suppressWarnings(as.integer(
          saved_page$count %||% NA_integer_
        )),
        integer(1L)
      )
      observed_counts <- observed_counts[
        !is.na(observed_counts) & observed_counts >= 0L
      ]
      counts_moved <- length(unique(observed_counts)) > 1L
      terminal_page <- !length(page$rows) || !length(page$last_indexes)
      count_complete <- !counts_moved && !is.na(state$expected_count) &&
        state$rows_seen == state$expected_count
      # Paul's ruling: a moving count is reconciled, not aborted. Re-walk the
      # cursor from scratch until one pass observes a constant count equal to the
      # rows it collected; fail loudly, having stored nothing, once passes run out.
      drift <- if (counts_moved && terminal_page) {
        paste0("count moved ", paste(unique(observed_counts), collapse = "->"),
          " after ", state$rows_seen, " rows")
      } else if (!count_complete && terminal_page) {
        paste0("shortfall ", state$rows_seen, " of ",
          if (length(observed_counts)) paste(unique(observed_counts), collapse = ",") else "unknown")
      } else if (!counts_moved && !is.na(state$expected_count) &&
                 state$rows_seen > state$expected_count) {
        paste0("overrun ", state$rows_seen, " > ", state$expected_count)
      } else {
        NA_character_
      }
      if (!is.na(drift)) {
        attempt <- as.integer(state$reconcile_pass) + 1L
        state$reconcile_log <- c(state$reconcile_log, paste0("pass ", attempt, ": ", drift))
        page$complete <- FALSE
        page$validation_failure <- "population_changed_during_collection"
        if (is.function(page_callback)) page_callback(state, page)
        if (attempt > reconcile_limit) {
          stop("FEC API population for ", state$unit$id, " did not verify after ",
            reconcile_limit, " passes: ", paste(state$reconcile_log, collapse = "; "),
            ". Nothing unverified was stored.", call. = FALSE)
        }
        state$reconcile_pass <- attempt
        state$last_indexes <- list()
        state$seen_keysets <- character()
        state$page <- 0L
        state$rows_seen <- 0L
        state$expected_count <- NA_integer_
        state$pages <- list()
        state$seen_sub_ids <- character()
        state$rows_collected_run <- 0L
        state$complete <- FALSE
        if (is.function(reset_callback)) reset_callback(state)
        cat(sprintf("Reconcile pass %d for %s: %s\n", attempt, state$unit$id, drift))
        reconcile_count <- reconcile_count + 1L
        states[[index]] <- state
        unfinished <- c(unfinished, index)
        next
      }
      has_more <- if (!is.na(state$expected_count)) {
        !count_complete
      } else {
        length(page$rows) > 0L && length(page$last_indexes) > 0L
      }
      if (has_more) {
        signature <- fec_api_keyset_signature(page$last_indexes)
        if (!nzchar(signature)) {
          stop(
            "FEC API omitted pagination.last_indexes before the query was complete.",
            call. = FALSE
          )
        }
        if (signature %in% state$seen_keysets) {
          stop("FEC API cycled to a seen keyset cursor; refusing an infinite loop.", call. = FALSE)
        }
        state$seen_keysets <- c(state$seen_keysets, signature)
        state$last_indexes <- page$last_indexes
        unfinished <- c(unfinished, index)
      } else {
        state$complete <- TRUE
      }
      state$pages[[page_position]]$complete <- state$complete
      if (is.function(page_callback)) {
        page_callback(state, state$pages[[page_position]])
      }
      states[[index]] <- state
    }
    queue <- c(rest, unfinished)
  }
  elapsed <- proc.time()[["elapsed"]] - started
  names(states) <- vapply(states, function(state) state$unit$id, character(1L))
  list(
    units = states,
    request_count = request_count,
    retry_count = retry_count,
    rate_limit_count = rate_limit_count,
    rows = sum(vapply(states, function(state) state$rows_collected_run, integer(1L))),
    rows_total = sum(vapply(states, function(state) state$rows_seen, integer(1L))),
    final_concurrency = current_concurrency,
    wall_clock_seconds = unname(elapsed),
    decompositions = decompositions,
    reconcile_count = reconcile_count
  )
}

fec_api_company_rosters <- function(connection) {
  companies <- data.table::as.data.table(DBI::dbGetQuery(
    connection,
    paste(
      "SELECT company, variant, variant_order, exclusions",
      paste0("FROM companies WHERE industry = '", MENTAT_INDUSTRY, "'"),
      "ORDER BY company, variant_order"
    )
  ))
  if (!nrow(companies)) {
    stop("Crypto company register is empty; run Phase 0 ingest first.", call. = FALSE)
  }
  split(companies, companies$company)
}

fec_api_run_lookups <- function(connection) {
  rosters <- fec_api_company_rosters(connection)
  list(
    rosters = rosters,
    match_overrides = company_match_overrides_data(connection),
    home_states = company_home_states_data(connection),
    trusted_by_company = setNames(lapply(names(rosters), function(company) {
      company_cross_reference_people(connection, company)
    }), names(rosters))
  )
}

fec_api_filter_lookups <- function(lookup_cache, company_filter = NULL) {
  if (is.null(company_filter)) return(lookup_cache)
  requested <- unique(trim_text(company_filter))
  requested <- requested[nzchar(requested)]
  if (!length(requested)) {
    stop("FEC API company_filter must contain at least one company.", call. = FALSE)
  }
  available <- names(lookup_cache$rosters)
  unknown <- setdiff(requested, available)
  if (length(unknown)) {
    stop(
      "FEC API company_filter contains unknown company names: ",
      paste(sort(unknown, method = "radix"), collapse = ", "),
      call. = FALSE
    )
  }
  selected <- available[available %in% requested]
  lookup_cache$rosters <- lookup_cache$rosters[selected]
  lookup_cache$trusted_by_company <-
    lookup_cache$trusted_by_company[selected]
  lookup_cache
}

fec_api_roster_exclusions <- function(roster) {
  values <- unique(unlist(strsplit(
    paste(na.omit(roster$exclusions), collapse = "|"),
    "|",
    fixed = TRUE
  )))
  values[nzchar(values)]
}

fec_api_decomposition_spellings <- function(
  connection,
  company,
  cycle,
  roster,
  match_overrides = NULL,
  home_states = NULL
) {
  fallback <- function() {
    list(values = unique(trim_text(roster$variant)), source = "roster")
  }
  if (!DBI::dbExistsTable(connection, "observed_employers")) return(fallback())
  observed <- DBI::dbGetQuery(
    connection,
    paste(
      "SELECT employer_string FROM observed_employers",
      "WHERE cycle=? ORDER BY employer_string"
    ),
    params = list(as.integer(cycle))
  )$employer_string
  observed <- unique(trim_text(observed))
  observed <- observed[nzchar(observed)]
  if (!length(observed)) return(fallback())
  overrides <- match_overrides %||% company_match_overrides_data(connection)
  accepted <- vapply(observed, function(employer) {
    match <- company_employer_match(
      company = company,
      employer = employer,
      contributor_name = "",
      variants = roster$variant,
      trusted_name_keys = character(),
      overrides = overrides,
      excluded_reason = NA_character_,
      home_states = home_states
    )
    identical(match$status, "accepted")
  }, logical(1L))
  values <- observed[accepted]
  if (!length(values)) return(fallback())
  list(values = values, source = "observed_employers")
}

fec_api_employer_units <- function(
  connection,
  cycles,
  rosters = NULL,
  match_overrides = NULL,
  home_states = NULL
) {
  rosters <- rosters %||% fec_api_company_rosters(connection)
  units <- list()
  for (company in sort(names(rosters), method = "radix")) {
    roster <- rosters[[company]]
    partition <- partition_company_variants(roster$variant)
    phases <- list(P1_employer = partition$phase1, P2_variant = partition$phase2)
    for (cycle in sort(as.integer(cycles))) {
      decomposition <- fec_api_decomposition_spellings(
        connection,
        company,
        cycle,
        roster,
        match_overrides = match_overrides,
        home_states = home_states
      )
      for (phase in names(phases)) {
        # The FEC API answers HTTP 422 to more than 10 contributor_employer
        # values, so a longer phase list is queried as disjoint <=10-value
        # units. Their rows union by sub_id; lists of 10 or fewer are unchanged.
        phase_variants <- phases[[phase]]
        for (variants in split(phase_variants,
            ceiling(seq_along(phase_variants) / FEC_API_MAX_QUERY_VALUES))) {
        id <- paste(phase, company, cycle, paste(variants, collapse = "||"), sep = ":")
        units[[length(units) + 1L]] <- list(
          id = id,
          kind = "employer",
          industry = MENTAT_INDUSTRY,
          company = company,
          cycle = as.integer(cycle),
          variants = variants,
          company_variants = roster$variant,
          phase = phase,
          query_field = "contributor_employer",
          query_filters = list(),
          exclusions = fec_api_roster_exclusions(roster),
          decomposition_variants = decomposition$values,
          decomposition_source = decomposition$source
        )
        }
      }
    }
  }
  units
}

fec_api_identity_units <- function(connection, cycles, rosters = NULL) {
  rosters <- rosters %||% fec_api_company_rosters(connection)
  seeds <- data.table::as.data.table(DBI::dbGetQuery(
    connection,
    paste(
      "SELECT sub_id, contributor_name, contributor_city, contributor_state,",
      "contributor_zip, contributor_employer, contributor_occupation, company,",
      "two_year_transaction_period AS cycle FROM contributions",
      paste0("WHERE industry='", MENTAT_INDUSTRY, "' AND phase IN ('P1_employer','P2_variant')"),
      "AND company_match_status='accepted'",
      "AND (excluded_reason IS NULL OR excluded_reason='')"
    )
  ))
  seeds <- seeds[cycle %in% as.integer(cycles)]
  seeds <- seeds[company %in% names(rosters)]
  seeds <- seeds[vapply(contributor_name, plausible_person_query, logical(1L))]
  data.table::setorder(seeds, company, cycle, contributor_name, sub_id)
  seeds <- unique(
    seeds,
    by = c("company", "cycle", "contributor_name", "contributor_zip", "contributor_city")
  )
  units <- list()
  for (index in seq_len(nrow(seeds))) {
    seed <- as.list(seeds[index])
    filters <- list()
    zip <- normalize_zip5(seed$contributor_zip)
    city <- toupper(trim_text(seed$contributor_city))
    if (nzchar(zip)) {
      filters$contributor_zip <- zip
    } else if (nzchar(city)) {
      filters$contributor_city <- city
    }
    company <- seed$company
    roster <- rosters[[company]]
    id <- paste("P2_name", company, seed$cycle, seed$sub_id, sep = ":")
    units[[length(units) + 1L]] <- list(
      id = id,
      kind = "identity",
      industry = MENTAT_INDUSTRY,
      company = company,
      cycle = as.integer(seed$cycle),
      variants = seed$contributor_name,
      company_variants = roster$variant,
      phase = "P2_name",
      query_field = "contributor_name",
      query_filters = filters,
      exclusions = fec_api_roster_exclusions(roster),
      seed = seed
    )
  }
  units
}

fec_api_corporate_units <- function(connection, cycles, rosters = NULL) {
  rosters <- rosters %||% fec_api_company_rosters(connection)
  units <- list()
  for (company in sort(names(rosters), method = "radix")) {
    roster <- rosters[[company]]
    queries <- company_contributor_queries(company, roster$variant)
    for (cycle in sort(as.integer(cycles))) {
      for (query in queries) {
        id <- paste("P2_company_name", company, cycle, query, sep = ":")
        units[[length(units) + 1L]] <- list(
          id = id,
          kind = "corporate",
          industry = MENTAT_INDUSTRY,
          company = company,
          cycle = as.integer(cycle),
          variants = query,
          company_variants = roster$variant,
          phase = "P2_company_name",
          query_field = "contributor_name",
          query_filters = list(),
          exclusions = fec_api_roster_exclusions(roster)
        )
      }
    }
  }
  units
}

# FEC's contributor_name search is token-based, so a recipient that spells the
# company PAC "SCHOOLS FIRST FCU EMPLOYEES FEDERAL PAC" matches neither
# "SchoolsFirst" nor "Schools First Federal Credit Union", although its filing
# carries the PAC's committee id. Re-query Schedule A by that id. The ids come
# only from stored committee rows whose own contributor name already names the
# company, so a misattributed committee never seeds a query; the rows found are
# attributed by the same name rules as every other corporate row.
fec_api_company_committee_ids <- function(connection, rosters) {
  rows <- data.table::as.data.table(DBI::dbGetQuery(connection, paste(
    "SELECT DISTINCT company, UPPER(TRIM(contributor_id)) AS contributor_id,",
    "contributor_name, entity_type, company_match_status, company_match_reason,",
    "excluded_reason FROM corporate_giving WHERE contributor_id IS NOT NULL"
  )))
  rows <- rows[grepl("^C[0-9]{8}$", contributor_id) &
    toupper(trim_text(entity_type)) != "IND" & company %in% names(rosters)]
  if (!nrow(rows)) return(list())
  status <- fec_attribution_status(rows, lapply(rosters, function(roster) roster$variant))
  rows <- rows[status %in% c("accepted", "company_named_unverified")]
  lapply(split(rows$contributor_id, rows$company), function(ids) sort(unique(ids), method = "radix"))
}

fec_api_corporate_id_units <- function(connection, cycles, rosters = NULL) {
  rosters <- rosters %||% fec_api_company_rosters(connection)
  ids <- fec_api_company_committee_ids(connection, rosters)
  units <- list()
  for (company in sort(as.character(names(ids)), method = "radix")) {
    roster <- rosters[[company]]
    for (cycle in sort(as.integer(cycles))) {
      for (committee_id in ids[[company]]) {
        units[[length(units) + 1L]] <- list(
          # The P2_company_name prefix keeps export guards routing it to corporate.
          id = paste("P2_company_name", company, cycle, paste0("contributor_id=", committee_id), sep = ":"),
          kind = "corporate",
          industry = MENTAT_INDUSTRY,
          company = company,
          cycle = as.integer(cycle),
          variants = committee_id,
          company_variants = roster$variant,
          phase = "P2_company_name",
          query_field = "contributor_id",
          query_filters = list(),
          exclusions = fec_api_roster_exclusions(roster)
        )
      }
    }
  }
  units
}

fec_api_rescue_cap <- function(
  value = Sys.getenv("MENTAT_RESCUE_CAP", unset = "50")
) {
  cap <- suppressWarnings(as.integer(trimws(value)))
  if (length(cap) != 1L || is.na(cap) || cap < 0L) {
    stop("MENTAT_RESCUE_CAP must be a nonnegative integer.", call. = FALSE)
  }
  cap
}

fec_api_rescue_cap_reached <- function(cap) {
  structure(
    list(
      message = paste0("Bulk name-rescue request cap reached: ", cap),
      call = NULL,
      cap = as.integer(cap)
    ),
    class = c("mentat_rescue_cap_reached", "error", "condition")
  )
}

fec_api_bulk_rescue_match <- function(rows, target_sub_id) {
  rows <- fec_api_rows_as_list(rows)
  target <- trim_text(target_sub_id)
  if (!length(rows) || !nzchar(target)) return(list())
  exact <- rows[vapply(rows, function(row) {
    identical(trim_text(row_scalar(row, "sub_id", "")), target)
  }, logical(1L))]
  fec_api_union_rows(exact)
}

fec_api_execute_rescue_plan <- function(
  candidates,
  cap = fec_api_rescue_cap(),
  api_key,
  concurrency = 1L,
  transport = fec_api_default_transport,
  sleep = Sys.sleep,
  jitter = stats::runif,
  before_request = function(count) invisible(NULL),
  on_exact = function(candidate, rows, page) invisible(TRUE),
  on_complete = function(candidate, pages, exact_rows) invisible(TRUE)
) {
  candidates <- as.data.frame(candidates, stringsAsFactors = FALSE)
  required <- c("company", "cycle", "sub_id", "contributor_name", "contributor_zip")
  missing <- setdiff(required, names(candidates))
  if (length(missing)) {
    stop(
      "Bulk rescue candidates are missing columns: ", paste(missing, collapse = ", "),
      call. = FALSE
    )
  }
  started <- proc.time()[["elapsed"]]
  requests <- 0L
  retries <- 0L
  rate_limits <- 0L
  raw_rows <- 0L
  rescued_ids <- character()
  overflow_index <- integer()
  if (!nrow(candidates)) {
    return(list(
      rows = 0L, request_count = 0L, retry_count = 0L,
      rate_limit_count = 0L, rescued_sub_ids = character(),
      overflow = candidates, wall_clock_seconds = 0, queries_walked = 0L,
      raw_rows = 0L
    ))
  }
  reserve <- function(count) {
    count <- as.integer(count)
    if (requests + count > as.integer(cap)) {
      stop(fec_api_rescue_cap_reached(cap))
    }
    before_request(count)
    requests <<- requests + count
    invisible(NULL)
  }
  queries_walked <- 0L
  for (index in seq_len(nrow(candidates))) {
    if (requests >= as.integer(cap)) {
      overflow_index <- seq.int(index, nrow(candidates))
      break
    }
    candidate <- as.list(candidates[index, , drop = FALSE])
    name <- trim_text(candidate$contributor_name)
    if (!nzchar(name)) next
    zip <- normalize_zip5(candidate$contributor_zip)
    filters <- if (nzchar(zip)) list(contributor_zip = zip) else list()
    unit <- list(
      id = paste(
        "P3_bulk_rescue", candidate$company, candidate$cycle,
        candidate$sub_id, sep = ":"
      ),
      company = trim_text(candidate$company),
      cycle = as.integer(candidate$cycle),
      variants = name,
      query_field = "contributor_name",
      query_filters = filters
    )
    queries_walked <- queries_walked + 1L
    cursor <- list()
    rows_seen <- 0L
    expected_count <- NA_integer_
    seen_keysets <- character()
    cap_hit <- FALSE
    candidate_pages <- list()
    exact_rows <- list()
    repeat {
      fetched <- tryCatch(
        fec_api_fetch_with_retry(
          list(fec_api_request_spec(unit, cursor)),
          api_key = api_key,
          concurrency = min(1L, as.integer(concurrency)),
          transport = transport,
          sleep = sleep,
          jitter = jitter,
          before_request = reserve
        ),
        mentat_rescue_cap_reached = function(condition) condition
      )
      if (inherits(fetched, "mentat_rescue_cap_reached")) {
        overflow_index <- seq.int(index, nrow(candidates))
        cap_hit <- TRUE
        break
      }
      retries <- retries + fetched$retry_count
      rate_limits <- rate_limits + fetched$rate_limit_count
      if (length(fetched$unservable_queries)) break
      page <- fec_api_parse_page(fetched$responses[[1L]])
      if (!is.na(page$count)) {
        if (is.na(expected_count)) {
          expected_count <- page$count
        } else if (!identical(expected_count, page$count)) {
          warning(
            "FEC API pagination.count moved during bulk rescue (continuing) for ",
            candidate$sub_id,
            call. = FALSE
          )
          expected_count <- page$count
        }
      }
      rows_seen <- rows_seen + length(page$rows)
      raw_rows <- raw_rows + length(page$rows)
      candidate_pages[[length(candidate_pages) + 1L]] <- page
      exact <- fec_api_bulk_rescue_match(page$rows, candidate$sub_id)
      if (length(exact)) {
        on_exact(candidate, exact, page)
        exact_rows <- exact
        rescued_ids <- c(rescued_ids, trim_text(candidate$sub_id))
        break
      }
      count_complete <- !is.na(expected_count) && rows_seen >= expected_count
      if (!is.na(expected_count) && rows_seen < expected_count &&
          (!length(page$rows) || !length(page$last_indexes))) {
        stop(
          "FEC API pagination shortfall during bulk rescue for ",
          candidate$sub_id,
          call. = FALSE
        )
      }
      if (count_complete || !length(page$rows) || !length(page$last_indexes)) break
      signature <- fec_api_keyset_signature(page$last_indexes)
      if (!nzchar(signature) || signature %in% seen_keysets) {
        stop(
          "FEC API bulk-rescue pagination returned an invalid or repeated cursor.",
          call. = FALSE
        )
      }
      seen_keysets <- c(seen_keysets, signature)
      cursor <- page$last_indexes
    }
    if (length(candidate_pages)) {
      on_complete(candidate, candidate_pages, exact_rows)
    }
    if (cap_hit) break
  }
  overflow <- if (length(overflow_index)) {
    candidates[overflow_index, , drop = FALSE]
  } else {
    candidates[FALSE, , drop = FALSE]
  }
  if (nrow(overflow)) overflow$overflow_reason <- "rescue_request_cap"
  list(
    rows = length(unique(rescued_ids)),
    request_count = as.integer(requests),
    retry_count = as.integer(retries),
    rate_limit_count = as.integer(rate_limits),
    rescued_sub_ids = sort(unique(rescued_ids), method = "radix"),
    overflow = overflow,
    wall_clock_seconds = unname(proc.time()[["elapsed"]] - started),
    queries_walked = as.integer(queries_walked),
    raw_rows = as.integer(raw_rows)
  )
}

fec_api_bulk_rescue <- function(
  connection,
  cycles,
  api_key,
  concurrency,
  transport,
  sleep,
  jitter,
  before_request,
  data_root = mentat_default_data_root(),
  cap = fec_api_rescue_cap(),
  lookup_cache = NULL,
  company_filter = NULL
) {
  if (!DBI::dbExistsTable(connection, "bulk_candidates")) {
    candidates <- data.frame(
      company = character(), cycle = integer(), sub_id = character(),
      contributor_name = character(), contributor_zip = character(),
      stringsAsFactors = FALSE
    )
  } else {
    placeholders <- paste(rep("?", length(cycles)), collapse = ", ")
    candidates <- DBI::dbGetQuery(
      connection,
      paste0(
        "SELECT b.* FROM bulk_candidates b ",
        "LEFT JOIN contributions c ON c.sub_id=b.sub_id ",
        "WHERE c.sub_id IS NULL AND b.cycle IN (", placeholders, ") ",
        "ORDER BY b.company, b.cycle, b.sub_id"
      ),
      params = as.list(as.integer(cycles))
    )
  }
  if (!is.null(company_filter) && nrow(candidates)) {
    candidates <- candidates[
      candidates$company %in% trim_text(company_filter),
      ,
      drop = FALSE
    ]
  }
  if (nrow(candidates)) {
    candidates <- candidates[!duplicated(candidates$sub_id), , drop = FALSE]
  }
  lookup_cache <- lookup_cache %||% fec_api_run_lookups(connection)
  rosters <- lookup_cache$rosters
  match_overrides <- lookup_cache$match_overrides
  home_states <- lookup_cache$home_states
  trusted_by_company <- lookup_cache$trusted_by_company
  result <- fec_api_execute_rescue_plan(
    candidates = candidates,
    cap = cap,
    api_key = api_key,
    concurrency = concurrency,
    transport = fec_api_archiving_transport(connection, transport),
    sleep = sleep,
    jitter = jitter,
    before_request = before_request,
    on_exact = function(candidate, rows, page) invisible(TRUE),
    on_complete = function(candidate, pages, exact_rows) {
      company <- trim_text(candidate$company)
      roster <- rosters[[company]]
      variant_set <- paste(
        "P3_bulk_rescue", company, candidate$cycle, candidate$sub_id,
        sep = ":"
      )
      DBI::dbWithTransaction(connection, {
        for (page_index in seq_along(pages)) {
          page <- pages[[page_index]]
          records <- flatten_fec_rows(
            page$rows,
            industry = MENTAT_INDUSTRY,
            company = company,
            variants = roster$variant,
            phase = "P3_bulk_rescue",
            exclusions = fec_api_roster_exclusions(roster),
            trusted_name_keys = trusted_by_company[[company]],
            match_overrides = match_overrides,
            home_states = home_states,
            scraped_at_value = fec_api_deterministic_timestamp(page$rows)
          )
          if (nrow(records)) data.table::setorder(records, sub_id)
          checkpoint_contribution_page(
            connection,
            records,
            company,
            as.integer(candidate$cycle),
             variant_set,
             page = page_index - 1L,
             records_total = page$count %||% length(page$rows),
             zero_verified = 1L,
             use_transaction = FALSE
           )
        }
      })
      invisible(TRUE)
    }
  )
  overflow <- result$overflow
  if (!"overflow_reason" %in% names(overflow)) {
    overflow$overflow_reason <- character(nrow(overflow))
  }
  overflow_target <- file.path(
    mentat_bootstrap_dirs(data_root)$reports,
    "bulk_rescue_overflow.csv"
  )
  atomic_write_csv(overflow, overflow_target)
  result$overflow_path <- overflow_target
  result
}

fec_api_deterministic_timestamp <- function(rows) {
  load_dates <- vapply(rows, function(row) {
    trim_text(row_scalar(row, "load_date", ""))
  }, character(1L))
  load_dates <- load_dates[nzchar(load_dates)]
  if (length(load_dates)) max(load_dates) else "1970-01-01T00:00:00+0000"
}

fec_api_log_page_canaries <- function(
  connection,
  unit,
  page_index,
  page,
  use_transaction = TRUE
) {
  sub_ids <- vapply(page$rows, function(row) {
    trim_text(row_scalar(row, "sub_id", ""))
  }, character(1L))
  checks <- data.table::data.table(
    check_name = c("api_http_success", "api_sub_id", "api_keyset_pagination"),
    passed = c(
      TRUE,
      !length(sub_ids) || all(nzchar(sub_ids)),
      isTRUE(page$complete) || length(page$last_indexes) > 0L
    ),
    detail = c(
      paste0("rows=", length(page$rows)),
      paste0("rows=", length(sub_ids)),
      paste0(
        "rows_collected=", page$rows_collected %||% length(page$rows),
        "; count=", page$count,
        "; last_indexes=", fec_api_keyset_signature(page$last_indexes)
      )
    )
  )
  log_page_canaries(
    connection,
    unit$company,
    unit$cycle,
    unit$id,
    page_index,
    checks,
    use_transaction = use_transaction
  )
  if (any(!checks$passed)) {
    stop("FEC API page canary failed for work unit: ", unit$id, call. = FALSE)
  }
}

fec_api_corporate_records <- function(
  connection,
  unit,
  rows,
  match_overrides = NULL,
  trusted_name_keys = character(),
  home_states = NULL
) {
  records <- flatten_fec_rows(
    rows,
    industry = unit$industry,
    company = unit$company,
    variants = unit$company_variants,
    phase = "P2_company_name",
    exclusions = unit$exclusions,
    trusted_name_keys = trusted_name_keys,
    match_overrides = match_overrides %||% company_match_overrides_data(connection),
    home_states = home_states %||% company_home_states_data(connection),
    scraped_at_value = fec_api_deterministic_timestamp(rows)
  )
  if (nrow(records)) {
    individual <- which(toupper(trim_text(records$entity_type)) == "IND")
    if (length(individual)) {
      records$company_match_status[individual] <- "rejected"
      records$company_match_reason[individual] <- "corporate_query_individual_entity"
    }
    missing_entity <- which(!nzchar(trim_text(records$entity_type)))
    if (length(missing_entity)) {
      records$company_match_status[missing_entity] <- "review"
      records$company_match_reason[missing_entity] <- "corporate_query_missing_entity_type"
    }
    junk <- vapply(records$contributor_employer, junk_employer_reason, character(1L))
    junk_index <- which(!is.na(junk))
    if (length(junk_index)) {
      records$company_match_status[junk_index] <- "rejected"
      records$company_match_reason[junk_index] <- junk[junk_index]
    }
  }
  records[, .(
    sub_id, contributor_id, contributor_name, contributor_employer,
    contributor_occupation, contributor_street_1, contributor_street_2,
    contributor_city, contributor_state, contributor_zip,
    entity_type, is_entity_contributor, committee_id, committee_name,
    recipient_committee_type, recipient_committee_designation,
    contribution_receipt_date,
    contribution_receipt_amount, industry, company, matched_variant,
    cycle = as.integer(unit$cycle), scraped_at, transaction_id, original_sub_id,
    amendment_indicator, report_year, report_type, file_num, pdf_url, load_date,
    filing_form, receipt_type, receipt_type_full, line_number, line_number_label,
    memo_code, memoed_subtotal, memo_text, back_reference_transaction_id, back_reference_schedule_name,
    is_conduit_leg, is_memo_leg, is_passthrough_leg,
    superseded_by_amendment, duplicate_filing_version, duplicate_memo,
    company_match_status, company_match_reason,
    excluded_reason
  )]
}

fec_api_store_page <- function(
  connection,
  state,
  page,
  match_overrides,
  trusted_name_keys,
  home_states,
  use_transaction = TRUE
) {
  action <- function() {
    unit <- state$unit
    page_index <- as.integer(page$page_number)
    fec_api_log_page_canaries(
      connection,
      unit,
      page_index,
      page,
      use_transaction = FALSE
    )
    records_total <- if (!is.na(page$count)) page$count else state$rows_seen
    if (identical(unit$kind, "employer")) {
      records <- flatten_fec_rows(
        page$rows,
        industry = unit$industry,
        company = unit$company,
        variants = unit$company_variants,
        phase = unit$phase,
        exclusions = unit$exclusions,
        trusted_name_keys = trusted_name_keys,
        match_overrides = match_overrides,
        home_states = home_states,
        scraped_at_value = fec_api_deterministic_timestamp(page$rows)
      )
      if (nrow(records)) data.table::setorder(records, sub_id)
      checkpoint_contribution_page(
        connection,
        records,
        unit$company,
        unit$cycle,
        unit$id,
        page_index,
        records_total,
        zero_verified = 1L,
        use_transaction = FALSE
      )
    } else if (identical(unit$kind, "identity")) {
      checkpoint_identity_query_page(
        connection = connection,
        rows = page$rows,
        page = page_index,
        records_total = records_total,
        variant_set = unit$id,
        industry = unit$industry,
        company = unit$company,
        cycle = unit$cycle,
        seed = unit$seed,
        company_variants = unit$company_variants,
        exclusions = unit$exclusions,
        scraped_at_value = fec_api_deterministic_timestamp(page$rows),
        match_overrides = match_overrides,
        use_transaction = FALSE
      )
    } else if (identical(unit$kind, "corporate")) {
      corporate <- fec_api_corporate_records(
        connection,
        unit,
        page$rows,
        match_overrides = match_overrides,
        trusted_name_keys = trusted_name_keys,
        home_states = home_states
      )
      if (nrow(corporate)) data.table::setorder(corporate, sub_id)
      checkpoint_corporate_page(
        connection,
        corporate,
        unit$company,
        unit$cycle,
        unit$id,
        page_index,
        records_total,
        zero_verified = 1L,
        use_transaction = FALSE
      )
    } else {
      stop("Unknown FEC API work-unit kind: ", unit$kind, call. = FALSE)
    }
  }
  if (isTRUE(use_transaction)) {
    DBI::dbWithTransaction(connection, action())
  } else {
    action()
  }
  invisible(TRUE)
}

fec_api_store_results <- function(connection, result, lookup_cache = NULL) {
  state_names <- sort(names(result$units), method = "radix")
  lookup_cache <- lookup_cache %||% fec_api_run_lookups(connection)
  match_overrides <- lookup_cache$match_overrides
  home_states <- lookup_cache$home_states
  trusted_by_company <- lookup_cache$trusted_by_company
  for (state_name in state_names) {
    state <- result$units[[state_name]]
    if (isTRUE(state$stored_complete)) next
    pages <- state$pages
    if (!isTRUE(state$complete)) {
      stop("Cannot store an incomplete FEC API work unit: ", state_name, call. = FALSE)
    }
    DBI::dbWithTransaction(connection, {
      for (page in pages) {
        fec_api_store_page(
          connection,
          state,
          page,
          match_overrides,
          trusted_by_company[[state$unit$company]],
          home_states,
          use_transaction = FALSE
        )
      }
      fec_api_mark_work_unit_stored(connection, state_name)
    })
  }
  invisible(result$rows)
}

fec_api_record_decompositions <- function(connection, run_id, decompositions) {
  if (is.null(run_id) || !nzchar(trim_text(run_id)) || !length(decompositions)) {
    return(invisible(NULL))
  }
  cycles <- sort(unique(vapply(
    decompositions,
    function(event) as.integer(event$cycle),
    integer(1L)
  )))
  DBI::dbWithTransaction(connection, {
    for (cycle in cycles) {
      events <- decompositions[vapply(
        decompositions,
        function(event) identical(as.integer(event$cycle), as.integer(cycle)),
        logical(1L)
      )]
      current <- DBI::dbGetQuery(
        connection,
        paste(
          "SELECT decomposition_log FROM run_log",
          "WHERE run_id=? AND cycle=?"
        ),
        params = list(run_id, as.integer(cycle))
      )
      existing <- list()
      if (nrow(current)) {
        text <- trim_text(current$decomposition_log[[1L]])
        if (nzchar(text)) {
          existing <- jsonlite::fromJSON(text, simplifyVector = FALSE)
        }
      }
      payload <- jsonlite::toJSON(
        c(existing, events),
        auto_unbox = TRUE,
        null = "null",
        na = "null",
        digits = NA
      )
      DBI::dbExecute(
        connection,
        paste(
          "UPDATE run_log SET decomposition_log=?",
          "WHERE run_id=? AND cycle=?"
        ),
        params = list(payload, run_id, as.integer(cycle))
      )
    }
  })
  invisible(NULL)
}

fec_api_collect_checkpointed <- function(
  connection,
  units,
  api_key,
  concurrency,
  transport,
  sleep,
  jitter,
  before_request,
  run_id = NULL,
  lookup_cache = NULL
) {
  initial_states <- fec_api_load_progress(connection, units)

  # A budget/quota pause is raised INSIDE fec_api_collect_work_units and unwinds
  # past the fec_api_store_results call below. Any unit fetched during that
  # window stayed checkpointed but was never stored, so a phase that always
  # exhausts its budget before finishing (e.g. thousands of P2_name units) could
  # fetch forever and store nothing. Flush the fetched-but-unstored units first,
  # then re-signal the pause so the caller still stops.
  flush_fetched <- function() {
    states <- fec_api_load_progress(connection, units)
    pending <- list()
    for (unit in units) {
      state <- states[[unit$id]]
      if (is.null(state)) next
      if (!isTRUE(state$complete) || isTRUE(state$stored_complete)) next
      state$unit <- unit
      pending[[unit$id]] <- state
    }
    if (!length(pending)) {
      return(invisible(0L))
    }
    fec_api_store_results(
      connection,
      list(units = pending, rows = 0L),
      lookup_cache = lookup_cache
    )
    cat(sprintf(
      "FEC API pause flush: stored %d fetched work unit(s) before pausing.
",
      length(pending)
    ))
    invisible(length(pending))
  }
  # Catch EVERY abort path, not just budget/quota. A hard error (e.g. an FEC
  # connection_error after retries) unwinds past the store call exactly the same
  # way and would discard the whole window's fetched work. Re-signalling the
  # original condition preserves the caller's pause-vs-fail semantics.
  on_abort <- function(cond) {
    # The flush must never mask the original abort, but it must not vanish either:
    # a silent try() here hid a store failure behind a clean-looking fetch loop.
    flushed <- try(flush_fetched(), silent = TRUE)
    if (inherits(flushed, "try-error")) {
      cat(sprintf(
        "FEC API pause flush FAILED (fetched work stays checkpointed): %s\n",
        conditionMessage(attr(flushed, "condition"))
      ))
    }
    stop(cond)
  }

  result <- tryCatch(
    fec_api_collect_work_units(
      units,
      api_key = api_key,
      concurrency = concurrency,
      transport = fec_api_archiving_transport(connection, transport),
      sleep = sleep,
      jitter = jitter,
      initial_states = initial_states,
      page_callback = function(state, page) {
        fec_api_checkpoint_page(connection, state, page)
      },
      before_request = before_request,
      # A reconciliation pass restarts one unit's cursor walk. Its earlier
      # checkpoints must never survive into the verified population.
      reset_callback = function(state) {
        DBI::dbWithTransaction(connection, {
          DBI::dbExecute(
            connection,
            "DELETE FROM api_page_checkpoints WHERE unit_id=?",
            params = list(state$unit$id)
          )
          DBI::dbExecute(
            connection,
            paste(
              "UPDATE api_work_progress SET last_page=-1, rows_seen=0,",
              "records_total=NULL, last_index='', last_contribution_receipt_date='',",
              "seen_keysets='', fetched_complete=0, stored_complete=0, updated_at=?",
              "WHERE unit_id=?"
            ),
            params = list(
              format(Sys.time(), "%Y-%m-%dT%H:%M:%OS3%z"),
              state$unit$id
            )
          )
        })
      },
      # Date windows abandoned for a serial parent walk were never stored.
      discard_callback = function(unit_ids) {
        if (!length(unit_ids)) return(invisible(NULL))
        marks <- paste(rep("?", length(unit_ids)), collapse = ",")
        DBI::dbWithTransaction(connection, {
          DBI::dbExecute(connection, paste0("DELETE FROM api_page_checkpoints WHERE unit_id IN (", marks, ")"),
            params = as.list(unit_ids))
          DBI::dbExecute(connection, paste0("DELETE FROM api_work_progress WHERE unit_id IN (", marks, ")"),
            params = as.list(unit_ids))
        })
      }
    ),
    error = on_abort
  )
  fec_api_record_decompositions(connection, run_id, result$decompositions)
  fec_api_store_results(connection, result, lookup_cache = lookup_cache)
  result
}

fec_api_determinism_spot_check <- function(
  result,
  api_key,
  concurrency,
  transport,
  sleep,
  jitter,
  before_request = function(count) invisible(NULL)
) {
  if (!length(result$units)) return(NA)
  decomposed_ids <- vapply(
    result$decompositions %||% list(),
    function(event) trim_text(event$unit_id),
    character(1L)
  )
  eligible <- setdiff(sort(names(result$units), method = "radix"), decomposed_ids)
  if (!length(eligible)) return(TRUE)
  first_name <- eligible[[1L]]
  first <- result$units[[first_name]]
  if (!length(first$pages)) return(TRUE)
  spec <- fec_api_request_spec(first$unit, list())
  repeated <- fec_api_fetch_with_retry(
    list(spec),
    api_key,
    concurrency,
    transport,
    sleep = sleep,
    jitter = jitter,
    before_request = before_request
  )
  page <- fec_api_parse_page(repeated$responses[[1L]])
  first_page <- first$pages[[1L]]
  expected <- vapply(first_page$rows, function(row) {
    row_scalar(row, "sub_id", "")
  }, character(1L))
  actual <- vapply(page$rows, function(row) {
    row_scalar(row, "sub_id", "")
  }, character(1L))
  # A date-window union is one synthetic page 0 carrying the WHOLE verified
  # population, so an exact comparison against a 100-row page 1 can never
  # match: a structural false positive. For a union the drift question is
  # containment of the fresh page plus an unmoved exact count. page_number is
  # 0 for an ordinary page 1 as well (see page_number <- state$page - 1L), so
  # only the decomposed/coverage_method flags identify a union page.
  union_page <- isTRUE(first_page$decomposed) ||
    identical(first_page$coverage_method, "exhaustive_date_windows")
  verified_count <- suppressWarnings(as.integer(
    first$expected_count %||% first$rows_seen %||% NA_integer_
  ))
  fresh_count <- if (isTRUE(page$is_count_exact)) {
    suppressWarnings(as.integer(page$count))
  } else NA_integer_
  count_text <- if (is.na(fresh_count)) "not exact" else format(fresh_count)
  verified_text <- if (is.na(verified_count)) "unknown" else format(verified_count)
  counts_agree <- is.na(fresh_count) || is.na(verified_count) ||
    identical(fresh_count, verified_count)
  contained <- sum(actual %in% expected)
  if (union_page) {
    return(list(
      identical = identical(contained, length(actual)) && counts_agree,
      mode = "union_subset",
      unit_id = first_name,
      request_count = repeated$request_count,
      retry_count = repeated$retry_count,
      detail = sprintf(
        "fresh page 1: %d ids, %d in union; count %s vs verified %s",
        length(actual), contained, count_text, verified_text
      )
    ))
  }
  list(
    identical = identical(sort(expected), sort(actual)),
    mode = "page_one",
    unit_id = first_name,
    request_count = repeated$request_count,
    retry_count = repeated$retry_count,
    detail = sprintf(
      paste0(
        "fresh page 1: %d ids, %d matching the stored page 1 of %d;",
        " count %s vs verified %s"
      ),
      length(actual), contained, length(expected), count_text, verified_text
    )
  )
}

fec_api_record_metric <- function(connection, run_id, phase, result) {
  if (is.null(run_id) || !nzchar(trim_text(run_id))) return(invisible(NULL))
  rows_per_second <- if (result$wall_clock_seconds > 0) {
    result$rows / result$wall_clock_seconds
  } else 0
  ended <- format(Sys.time(), "%Y-%m-%dT%H:%M:%OS3%z")
  started <- format(
    Sys.time() - result$wall_clock_seconds,
    "%Y-%m-%dT%H:%M:%OS3%z"
  )
  DBI::dbExecute(
    connection,
    paste(
      "INSERT OR REPLACE INTO run_metrics",
      "(run_id, phase, started_at, ended_at, wall_clock_seconds,",
      "rows_processed, request_count, rows_per_second, detail)",
      "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
    ),
    params = list(
      run_id,
      phase,
      started,
      ended,
      result$wall_clock_seconds,
      as.integer(result$rows),
      as.integer(result$request_count),
      as.numeric(rows_per_second),
      "FEC schedule_a JSON API"
    )
  )
  invisible(NULL)
}

fec_api_log_phase <- function(phase, result) {
  rate <- if (result$wall_clock_seconds > 0) {
    result$rows / result$wall_clock_seconds
  } else 0
  cat(sprintf(
    "FEC API phase %s: rows=%d requests=%d retries=%d wall=%.3fs rows/sec=%.2f\n",
    phase,
    result$rows,
    result$request_count,
    result$retry_count,
    result$wall_clock_seconds,
    rate
  ))
}

collect_crypto_api <- function(
  connection,
  cycles = c(2022L, 2024L, 2026L),
  phase2_names = FALSE,
  api_key = fec_api_key(),
  concurrency = fec_api_concurrency(),
  transport = fec_api_default_transport,
  sleep = Sys.sleep,
  jitter = stats::runif,
  run_id = NULL,
  data_root = mentat_default_data_root(),
  company_filter = NULL,
  budget_connection = NULL
) {
  require_package("data.table")
  require_package("jsonlite")
  run_started <- proc.time()[["elapsed"]]
  cycles <- sort(unique(as.integer(cycles)))
  if (!length(cycles) || anyNA(cycles) ||
      any(!cycles %in% c(2022L, 2024L, 2026L))) {
    stop("Cycles must be a subset of 2022, 2024, and 2026.", call. = FALSE)
  }
  lookup_cache <- fec_api_filter_lookups(
    fec_api_run_lookups(connection),
    company_filter
  )
  budget_connection <- budget_connection %||% connection
  budget_id <- fec_api_budget_id(api_key)
  requests_reserved_run <- 0L
  reserve_request <- function(count) {
    if (!nzchar(Sys.getenv("MENTAT_REQUEST_POOL_URL", ""))) fec_api_budget_reserve(
      budget_connection,
      count,
      budget_id = budget_id
    )
    requests_reserved_run <<- requests_reserved_run + as.integer(count)
    invisible(NULL)
  }
  is_pause_condition <- function(value) {
    inherits(value, c(
      "mentat_api_budget_exhausted",
      "mentat_api_quota_exhausted"
    ))
  }
  collect_phase <- function(units) {
    tryCatch(
      fec_api_collect_checkpointed(
        connection,
        units,
        api_key,
        concurrency,
        transport,
        sleep,
        jitter,
        reserve_request,
        run_id = run_id,
        lookup_cache = lookup_cache
      ),
      mentat_api_budget_exhausted = function(condition) condition,
      mentat_api_quota_exhausted = function(condition) condition
    )
  }
  pause_result <- function(condition, rows = 0L, retries = 0L) {
    budget <- fec_api_budget_status(
      budget_connection,
      budget_id = budget_id
    )
    requests_run <- requests_reserved_run
    cat(sprintf(
      paste0(
        "FEC API run paused: requests-used-run=%d requests-used-window=%d ",
        "requests-remaining=%d; checkpoints saved.\n"
      ),
      requests_run,
      budget$requests_used,
      budget$requests_remaining
    ))
    list(
      paused = TRUE,
      pause_reason = conditionMessage(condition),
      retries = as.integer(retries),
      request_count = as.integer(requests_run),
      requests_used = budget$requests_used,
      requests_remaining = budget$requests_remaining,
      rows = as.integer(rows),
      determinism = NA,
      variant_sets_walked = 0L
    )
  }
  total_requests <- 0L
  total_retries <- 0L
  employer <- collect_phase(fec_api_employer_units(
    connection,
    cycles,
    rosters = lookup_cache$rosters,
    match_overrides = lookup_cache$match_overrides,
    home_states = lookup_cache$home_states
  ))
  if (is_pause_condition(employer)) {
    return(pause_result(employer))
  }
  fec_api_log_phase("employer", employer)
  fec_api_record_metric(connection, run_id, "collect_employer", employer)
  total_requests <- total_requests + employer$request_count
  total_retries <- total_retries + employer$retry_count
  employer_companies <- unique(vapply(
    employer$units,
    function(state) state$unit$company,
    character(1L)
  ))
  for (company in sort(employer_companies, method = "radix")) {
    for (cycle in cycles) {
      retag_employer_company_matches(connection, company, cycle)
    }
  }
  rescue <- tryCatch(
    fec_api_bulk_rescue(
      connection = connection,
      cycles = cycles,
      api_key = api_key,
      concurrency = concurrency,
      transport = transport,
      sleep = sleep,
      jitter = jitter,
      before_request = reserve_request,
      data_root = data_root,
      lookup_cache = lookup_cache,
      company_filter = names(lookup_cache$rosters)
    ),
    mentat_api_budget_exhausted = function(condition) condition,
    mentat_api_quota_exhausted = function(condition) condition
  )
  if (is_pause_condition(rescue)) {
    return(pause_result(rescue, employer$rows, total_retries))
  }
  fec_api_log_phase("bulk_rescue", rescue)
  fec_api_record_metric(connection, run_id, "collect_bulk_rescue", rescue)
  total_requests <- total_requests + rescue$request_count
  total_retries <- total_retries + rescue$retry_count
  determinism <- tryCatch(
    fec_api_determinism_spot_check(
      employer,
      api_key,
      concurrency,
      transport,
      sleep,
      jitter,
      before_request = reserve_request
    ),
    mentat_api_budget_exhausted = function(condition) condition,
    mentat_api_quota_exhausted = function(condition) condition
  )
  if (is_pause_condition(determinism)) {
    return(pause_result(determinism, employer$rows, total_retries))
  }
  if (is.list(determinism)) {
    total_requests <- total_requests + determinism$request_count
    total_retries <- total_retries + determinism$retry_count
    determinism_value <- determinism$identical
  } else {
    determinism_value <- determinism
  }
  determinism_detail <- ""
  determinism_unit <- ""
  if (is.list(determinism)) {
    determinism_detail <- trim_text(determinism$detail %||% "")
    determinism_unit <- trim_text(determinism$unit_id %||% "")
  }
  # Advisory, not fatal: a date-window union is a synthetic page 0 over the
  # whole population, which made the old exact page-1 comparison a structural
  # false positive, and a live cycle's page 1 legitimately changes as filings
  # post. The walk-time verification (a constant count equal to the rows
  # collected, after reconciliation) remains the guarantee; the spot check only
  # records drift observed after that snapshot was verified.
  if (identical(determinism_value, FALSE)) {
    cat(sprintf(
      paste0(
        "Determinism spot-check: population changed after a verified walk",
        " for %s (%s); snapshot stands as verified at walk time.\n"
      ),
      determinism_unit,
      determinism_detail
    ))
  }
  identity <- list(
    units = list(), request_count = 0L, retry_count = 0L,
    rate_limit_count = 0L, rows = 0L, rows_total = 0L,
    final_concurrency = as.integer(concurrency), wall_clock_seconds = 0
  )
  if (isTRUE(phase2_names)) {
    identity <- collect_phase(fec_api_identity_units(
      connection,
      cycles,
      rosters = lookup_cache$rosters
    ))
    if (is_pause_condition(identity)) {
      return(pause_result(identity, employer$rows + rescue$rows, total_retries))
    }
    fec_api_log_phase("identity", identity)
    fec_api_record_metric(connection, run_id, "collect_identity", identity)
    total_requests <- total_requests + identity$request_count
    total_retries <- total_retries + identity$retry_count
  }
  corporate <- collect_phase(fec_api_corporate_units(
    connection,
    cycles,
    rosters = lookup_cache$rosters
  ))
  if (is_pause_condition(corporate)) {
    return(pause_result(
      corporate,
      employer$rows + rescue$rows + identity$rows,
      total_retries
    ))
  }
  fec_api_log_phase("corporate", corporate)
  fec_api_record_metric(connection, run_id, "collect_corporate", corporate)
  total_requests <- total_requests + corporate$request_count
  total_retries <- total_retries + corporate$retry_count
  # Runs after the name pass has stored its rows, so this run's own committee
  # ids are known (fec_api_company_committee_ids).
  corporate_id_units <- fec_api_corporate_id_units(
    connection,
    cycles,
    rosters = lookup_cache$rosters
  )
  corporate_id <- list(rows = 0L, request_count = 0L, retry_count = 0L)
  if (length(corporate_id_units)) {
    corporate_id <- collect_phase(corporate_id_units)
    if (is_pause_condition(corporate_id)) {
      return(pause_result(
        corporate_id,
        employer$rows + rescue$rows + identity$rows + corporate$rows,
        total_retries
      ))
    }
    fec_api_log_phase("corporate_id", corporate_id)
    fec_api_record_metric(connection, run_id, "collect_corporate_id", corporate_id)
  }
  total_requests <- total_requests + corporate_id$request_count
  total_retries <- total_retries + corporate_id$retry_count
  resolve_recipient_register_committee_ids(connection, log = TRUE)
  total_rows <- employer$rows + rescue$rows + identity$rows + corporate$rows +
    corporate_id$rows
  wall_clock_seconds <- proc.time()[["elapsed"]] - run_started
  rows_per_second <- if (wall_clock_seconds > 0) {
    total_rows / wall_clock_seconds
  } else {
    0
  }
  total_metric <- list(
    rows = total_rows,
    request_count = total_requests,
    wall_clock_seconds = unname(wall_clock_seconds)
  )
  fec_api_record_metric(connection, run_id, "collect_total", total_metric)
  budget <- fec_api_budget_status(
    budget_connection,
    budget_id = budget_id
  )
  requests_used_run <- requests_reserved_run
  cat(sprintf(
    paste0(
      "FEC API run total: rows=%d requests=%d retries=%d ",
      "wall=%.3fs rows/sec=%.2f rate-cap-usage=%.1f%% ",
      "requests-used-run=%d requests-used-window=%d requests-remaining=%d\n"
    ),
    total_rows,
    total_requests,
    total_retries,
    wall_clock_seconds,
    rows_per_second,
    100 * total_requests / FEC_API_HOURLY_REQUEST_CAP,
    requests_used_run,
    budget$requests_used,
    budget$requests_remaining
  ))
  if (total_requests >= 900L) {
    warning(
      "FEC API request count is near or above the registered-key hourly cap of 1,000.",
      call. = FALSE
    )
  }
  list(
    paused = FALSE,
    retries = total_retries,
    determinism = determinism_value,
    determinism_detail = determinism_detail,
    request_count = total_requests,
    requests_used = budget$requests_used,
    requests_remaining = budget$requests_remaining,
    rows = total_rows,
    wall_clock_seconds = unname(wall_clock_seconds),
    rows_per_second = rows_per_second,
    rescued = rescue$rows,
    rescue_overflow = nrow(rescue$overflow),
    rescue_overflow_path = rescue$overflow_path,
    variant_sets_walked = length(employer$units) +
      length(identity$units) + length(corporate$units) + rescue$queries_walked
  )
}

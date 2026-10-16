fec_runtime_value <- function(session, expression, timeout_seconds = 10) {
  response <- session$Runtime$evaluate(
    expression = expression,
    returnByValue = TRUE,
    awaitPromise = TRUE,
    timeout_ = timeout_seconds
  )
  value <- response$result$value
  if (is.null(value)) {
    stop("Chromote evaluation returned no value.", call. = FALSE)
  }
  value
}

fec_build_url <- function(
  cycle,
  variants,
  query_field = "contributor_employer",
  query_filters = list()
) {
  cycle <- as.integer(cycle)
  if (!cycle %in% c(2022L, 2024L, 2026L)) {
    stop("Unsupported two-year transaction period: ", cycle, call. = FALSE)
  }
  allowed_fields <- c("contributor_employer", "contributor_name")
  if (!query_field %in% allowed_fields) {
    stop("Unsupported FEC query field: ", query_field, call. = FALSE)
  }
  variants <- unique(trim_text(variants))
  variants <- variants[!is.na(variants) & nzchar(variants)]
  # Query literal roster spellings. Attribution normalization cannot establish
  # that FEC searches with altered punctuation return the same population.
  if (!length(variants)) {
    stop("At least one non-empty FEC query value is required.", call. = FALSE)
  }
  parameters <- c(
    paste0("two_year_transaction_period=", cycle),
    paste0(
      query_field,
      "=",
      vapply(
        variants,
        utils::URLencode,
        character(1L),
        reserved = TRUE,
        repeated = TRUE,
        USE.NAMES = FALSE
      )
    )
  )
  allowed_filters <- c(
    "contributor_zip",
    "contributor_city",
    "contributor_state",
    "min_date",
    "max_date"
  )
  if (length(query_filters)) {
    if (is.null(names(query_filters)) ||
        any(!names(query_filters) %in% allowed_filters)) {
      stop("Unsupported FEC query filter.", call. = FALSE)
    }
    for (filter_name in names(query_filters)) {
      filter_values <- unique(trim_text(query_filters[[filter_name]]))
      filter_values <- filter_values[
        !is.na(filter_values) & nzchar(filter_values)
      ]
      if (!length(filter_values)) {
        next
      }
      parameters <- c(
        parameters,
        paste0(
          filter_name,
          "=",
          vapply(
            filter_values,
            utils::URLencode,
            character(1L),
            reserved = TRUE,
            repeated = TRUE,
            USE.NAMES = FALSE
          )
        )
      )
    }
  }
  paste0(
    "https://www.fec.gov/data/receipts/individual-contributions/?",
    paste(parameters, collapse = "&")
  )
}

new_fec_session <- function() {
  require_package("chromote")
  chromote::ChromoteSession$new()
}

new_fec_session_state <- function(session) {
  state <- new.env(parent = emptyenv())
  state$session <- session
  class(state) <- "mentat_fec_session_state"
  state
}

fec_active_session <- function(session) {
  if (inherits(session, "mentat_fec_session_state")) {
    return(session$session)
  }
  session
}

fec_replace_session <- function(session, replacement) {
  if (inherits(session, "mentat_fec_session_state")) {
    session$session <- replacement
  }
  invisible(replacement)
}

fec_recover_session <- function(session) {
  replacement <- tryCatch(
    session$respawn(),
    error = function(error) NULL
  )
  if (is.null(replacement)) {
    replacement <- new_fec_session()
  }
  replacement
}

fec_results_ready <- function(session) {
  # recordsDisplay==0 is ALSO the table's state before the API response lands,
  # so it cannot mean "ready" by itself (verified live 2026-07-18: reading at
  # that moment yields recordsTotal=0 for employers that have rows). Ready =
  # the fetch overlay is hidden AND either data rows exist or the table shows
  # its confirmed-empty message row. A completed schedule_a resource entry is
  # NOT sufficient on its own - see the note on the zero branch below, which is
  # where that shortcut published a rate-limited query as a genuine $0.
  isTRUE(fec_runtime_value(
    session,
    paste0(
      "(function(){try{",
      "if(!window.jQuery){return false;}",
      "var dt=window.jQuery('#results').DataTable();",
      "var info=dt.page.info();",
      "if(!info||typeof info.recordsTotal!=='number'){return false;}",
      "var ov=document.querySelector('.overlay');",
      "if(ov && getComputedStyle(ov).display!=='none'){return false;}",
      "if(dt.rows().data().toArray().length>0){return true;}",
      # A zero result is only believable if the request behind it actually
      # SUCCEEDED. Any schedule_a resource entry used to satisfy this, but a
      # 429/500/504 creates one exactly like a 200 does - so a rate-limited
      # query read as a genuine "0 records". That published Farmers Insurance at
      # $0.00 for all three cycles: 24 variant sets "completed" in 16 seconds
      # (versus 8 minutes for a comparable company) minutes after FEC returned
      # "you have exceeded your maximum downloads for the hour", and every
      # canary passed because expected_total came from the same empty payload.
      # The trustworthy signal is DataTables' own "No data available in table"
      # row, which it renders only after a successful empty response. We require
      # that, and additionally refuse if any schedule_a entry reports an explicit
      # non-2xx. responseStatus is 0 for cross-origin entries without a
      # Timing-Allow-Origin header, so it cannot be relied on alone - it is a
      # disqualifier when present, never the sole proof of success. Failing to
      # become ready now times out loudly instead of recording a false zero.
      "var r=document.querySelector('#results tbody tr');",
      "var empty=!!r && /No data available in table/.test(r.textContent);",
      "if(info.recordsTotal===0 && info.recordsDisplay===0){",
      "var eps=performance.getEntriesByType('resource').filter(function(e){",
      "return e && typeof e.name==='string' && ",
      "e.name.indexOf('api.open.fec.gov/v1/schedules/schedule_a/')>=0;",
      "});",
      "if(!eps.length){return false;}",
      "var last=eps[eps.length-1];",
      "var lastFailed=typeof last.responseStatus==='number' && ",
      "last.responseStatus>0 && ",
      "(last.responseStatus<200||last.responseStatus>=300);",
      "return !lastFailed && empty;",
      "}",
      "return empty;",
      "}catch(e){return false;}})()"
    )
  ))
}

fec_wait_until <- function(predicate, timeout_seconds = 60, interval_seconds = 0.5) {
  deadline <- Sys.time() + timeout_seconds
  repeat {
    if (isTRUE(predicate())) {
      return(invisible(TRUE))
    }
    if (Sys.time() >= deadline) {
      stop("Timed out waiting for the FEC results table.", call. = FALSE)
    }
    Sys.sleep(interval_seconds)
  }
}

fec_navigate <- function(
  session,
  url,
  timeout_seconds = 60,
  retries = 3L
) {
  retries <- as.integer(retries)
  last_error <- NULL
  for (attempt in seq_len(retries + 1L)) {
    result <- tryCatch({
      navigate_url <- url
      if (attempt > 1L) {
        separator <- if (grepl("?", navigate_url, fixed = TRUE)) "&" else "?"
        navigate_url <- paste0(
          navigate_url,
          separator,
          "_mentat_retry=",
          as.integer(Sys.time()),
          "-",
          attempt
        )
      }
      session$Page$navigate(url = navigate_url)
      fec_wait_until(
        function() fec_results_ready(session),
        timeout_seconds = timeout_seconds
      )
      TRUE
    }, error = function(error) {
      last_error <<- error
      if (fec_is_transient_chromote_error(error)) {
        stop(error)
      }
      FALSE
    })
    if (isTRUE(result)) {
      return(attempt - 1L)
    }
    if (attempt <= retries) {
      Sys.sleep(min(30, 2 ^ attempt))
    }
  }
  stop(
    "FEC navigation failed after ", retries, " retries: ",
    conditionMessage(last_error),
    call. = FALSE
  )
}

fec_set_page_length <- function(session, page_length = 100L) {
  page_length <- as.integer(page_length)
  changed <- fec_runtime_value(
    session,
    sprintf(
      paste0(
        "(function(){var dt=window.jQuery('#results').DataTable();",
        "if(dt.page.len()===%d){return false;}",
        "var select=document.querySelector('#results_length select, ",
        ".dataTables_length select');",
        "if(select){select.value='%d';",
        "window.jQuery(select).trigger('change');}",
        "else{dt.page.len(%d).draw();}",
        "return true;})()"
      ),
      page_length,
      page_length,
      page_length
    )
  )
  if (!is.logical(changed) || length(changed) != 1L || is.na(changed)) {
    stop("Could not set the FEC results page length.", call. = FALSE)
  }
  if (!isTRUE(changed)) {
    payload <- fec_read_page(session)
    if (
      payload$info$length != page_length &&
        payload$info$recordsDisplay != 0L
    ) {
      stop("Could not verify the FEC results page length.", call. = FALSE)
    }
    return(payload)
  }
  fec_wait_until(function() {
    if (!fec_results_ready(session)) {
      return(FALSE)
    }
    isTRUE(fec_runtime_value(
      session,
      sprintf(
        paste0(
          "(function(){",
          "var dt=window.jQuery('#results').DataTable();",
          "var info=dt.page.info();",
          "var rows=dt.rows().data().toArray().length;",
          "var expected=(info.page+1<info.pages)",
          "?info.length:(info.recordsDisplay-info.start);",
          "return (info.length===%d || info.recordsDisplay===0)",
          " && rows===expected;",
          "})()"
        ),
        page_length
      )
    ))
  })
  payload <- fec_read_page(session)
  if (payload$info$length != page_length &&
      payload$info$recordsDisplay != 0L) {
    stop("Could not set the FEC results page length.", call. = FALSE)
  }
  payload
}

# Session-scoped optional reader hook; actual collectors always supply their SQLite connection.
fec_browser_evidence_callback <- function(session) attr(session, "mentat_evidence_callback", exact = TRUE)

fec_retain_browser_bytes <- function(session, bytes, source_kind, page = -1L, status = NA_integer_) {
  callback <- fec_browser_evidence_callback(session)
  if (is.function(callback)) callback(bytes, source_kind, page, status)
  invisible(NULL)
}

fec_chrome_archive_callback <- function(connection, company, cycle, variant_set, variants,
                                       query_field, query_filters = list()) {
  force(connection)
  params <- c(list(two_year_transaction_period = as.integer(cycle)),
    stats::setNames(list(as.list(variants)), query_field), query_filters)
  spec <- list(id = paste(company, cycle, variant_set, sep = "::"), params = params)
  function(bytes, source_kind, page, status) {
    tryCatch(DBI::dbWithTransaction(connection, {
      if (!is.raw(bytes)) stop("Browser evidence must be raw bytes.")
      if (!source_kind %in% c("fec_browser_http_body_bytes", "fec_download_csv_bytes", "parsed_browser_snapshot"))
        stop("Unsupported browser evidence kind.")
      envelope <- mentat_evidence_json(list(schema_version = 1L, source_kind = source_kind,
        company = company, cycle = as.integer(cycle),
        original_response_bytes = source_kind != "parsed_browser_snapshot",
        bytes_sha256 = digest::digest(bytes, algo = "sha256", serialize = FALSE),
        byte_length = length(bytes), bytes_base64 = jsonlite::base64_enc(bytes)))
      fec_api_archive_response(connection, spec, list(status = status, body = envelope),
        page = page, payload_kind = source_kind)
    }), error = function(condition) {
      stop(structure(list(message = "FEC Chrome evidence could not be saved; collection stopped before parsing or certification.", call = NULL),
        class = c("mentat_evidence_archive_failed", "error", "condition")))
    })
  }
}

fec_parse_export_file <- function(session, csv_path, expected_rows) {
  stream <- file(csv_path, "rb")
  bytes <- tryCatch(readBin(stream, "raw", n = file.info(csv_path)$size), finally = close(stream))
  fec_retain_browser_bytes(session, bytes, "fec_download_csv_bytes", status = 200L)
  rows <- data.table::fread(csv_path, colClasses = "character", na.strings = "NA",
    showProgress = FALSE, encoding = "UTF-8")
  if (nrow(rows) != expected_rows) stop("FEC export row-count mismatch: expected=", expected_rows,
    " downloaded=", nrow(rows), call. = FALSE)
  if (!"sub_id" %in% names(rows)) stop("FEC export is missing the required sub_id column.", call. = FALSE)
  export_sub_ids <- trim_text(rows$sub_id)
  if (any(!nzchar(export_sub_ids)) || anyDuplicated(export_sub_ids))
    stop("FEC export contains blank or repeated sub_id values; refusing row-count-only completion.", call. = FALSE)
  rows[]
}

fec_read_page <- function(session) {
  require_package("jsonlite")
  expression <- paste0(
    "JSON.stringify((function(){",
    "var dt=window.jQuery('#results').DataTable();",
    "var info=dt.page.info();",
    "var rows=dt.rows().data().toArray();",
    "if(rows.some(function(row){return typeof row.sub_id==='number' && !Number.isSafeInteger(row.sub_id);})){return {unsafe_numeric_sub_id:true};}",
    "return {info:info,rows:rows};",
    "})())"
  )
  json <- fec_runtime_value(session, expression)
  fec_retain_browser_bytes(session, charToRaw(enc2utf8(json)), "parsed_browser_snapshot")
  payload <- jsonlite::fromJSON(json, simplifyVector = FALSE, bigint_as_char = TRUE)
  if (isTRUE(payload$unsafe_numeric_sub_id)) stop("FEC browser table has an unsafe numeric sub_id; collect exact CSV or HTTP bytes instead.", call. = FALSE)
  if (is.null(payload$info) || is.null(payload$rows)) {
    stop("FEC DataTable payload is missing info or rows.", call. = FALSE)
  }
  payload$info <- lapply(payload$info, function(value) {
    if (length(value) == 1L) value else value
  })
  numeric_fields <- c(
    "page", "pages", "start", "end", "length", "recordsTotal", "recordsDisplay"
  )
  for (field in numeric_fields) {
    payload$info[[field]] <- as.integer(payload$info[[field]] %||% NA_integer_)
  }
  payload$info$serverSide <- isTRUE(payload$info$serverSide)
  payload
}

fec_canaries <- function(payload, expected_total = NULL) {
  info <- payload$info
  rows <- payload$rows
  row_ids <- vapply(
    rows,
    function(row) trim_text(row$sub_id %||% ""),
    character(1L)
  )
  checks <- list(
    server_side = list(
      passed = isTRUE(info$serverSide),
      detail = paste0("serverSide=", as.character(info$serverSide))
    ),
    records_total_stable = list(
      passed = is.null(expected_total) ||
        identical(as.integer(info$recordsTotal), as.integer(expected_total)),
      detail = paste0(
        "recordsTotal=", info$recordsTotal,
        if (!is.null(expected_total)) paste0("; expected=", expected_total) else ""
      )
    ),
    nonempty_rows_while_pages_remain = list(
      passed = !(info$page + 1L < info$pages && length(rows) == 0L),
      detail = paste0("page=", info$page, "; pages=", info$pages, "; rows=", length(rows))
    ),
    nonempty_sub_id = list(
      passed = !length(rows) || all(nzchar(row_ids)),
      detail = paste0("rows=", length(rows), "; nonempty_sub_id=", sum(nzchar(row_ids)))
    ),
    unique_sub_id = list(
      passed = !length(row_ids) || !anyDuplicated(row_ids),
      detail = paste0(
        "rows=", length(row_ids),
        "; unique_sub_id=", data.table::uniqueN(row_ids)
      )
    ),
    page_row_count = list(
      passed = length(rows) == if (info$page + 1L < info$pages) {
        info$length
      } else {
        info$recordsDisplay - info$start
      },
      detail = paste0(
        "page=", info$page, "; pages=", info$pages, "; rows=", length(rows),
        "; expected=", if (info$page + 1L < info$pages) {
          info$length
        } else {
          info$recordsDisplay - info$start
        }
      )
    )
  )
  data.table::rbindlist(lapply(names(checks), function(name) {
    data.table::data.table(
      check_name = name,
      passed = as.integer(isTRUE(checks[[name]]$passed)),
      detail = checks[[name]]$detail
    )
  }))
}

fec_click_next <- function(session, current_page) {
  clicked <- fec_runtime_value(
    session,
    paste0(
      "(function(){",
      "var next=document.querySelector('.next:not(.disabled)');",
      "if(!next){return false;} next.click(); return true;",
      "})()"
    )
  )
  if (!isTRUE(clicked)) {
    stop("FEC pagination expected another page but .next was unavailable.", call. = FALSE)
  }
  fec_wait_until(function() {
    if (!fec_results_ready(session)) {
      return(FALSE)
    }
    page_is_complete <- fec_runtime_value(
      session,
      sprintf(
        paste0(
          "(function(){",
          "var dt=window.jQuery('#results').DataTable();",
          "var info=dt.page.info();",
          "var rows=dt.rows().data().toArray().length;",
          "var expected=(info.page+1<info.pages)",
          "?info.length:(info.recordsDisplay-info.start);",
          "return info.page>%d && rows===expected;",
          "})()"
        ),
        as.integer(current_page)
      )
    )
    isTRUE(page_is_complete)
  })
  fec_read_page(session)
}

fec_is_transient_chromote_error <- function(error) {
  if (inherits(error, "mentat_evidence_archive_failed")) return(FALSE)
  message <- tolower(conditionMessage(error))
  signatures <- c(
    "chromote: timed out waiting for response",
    "timed out waiting for response to command",
    "timed out waiting for the fec results table",
    # A canary mismatch means the DataTable DOM was read between browser-side
    # updates. Never accept that page, but replay the complete query through
    # the existing bounded retry path instead of aborting the industry run.
    "fec canary failure",
    "fec export",
    "websocket",
    "connection closed",
    "target closed",
    "session closed",
    "session has been closed"
  )
  any(vapply(
    signatures,
    function(signature) grepl(signature, message, fixed = TRUE),
    logical(1L)
  ))
}

fec_query_retry_sleep <- function(attempt) {
  Sys.sleep(min(15, 2 ^ as.integer(attempt)))
}

fec_url_with_date_range <- function(url, min_date, max_date) {
  separator <- if (grepl("?", url, fixed = TRUE)) "&" else "?"
  paste0(
    url,
    separator,
    "min_date=",
    utils::URLencode(format(as.Date(min_date), "%m/%d/%Y"), reserved = TRUE),
    "&max_date=",
    utils::URLencode(format(as.Date(max_date), "%m/%d/%Y"), reserved = TRUE)
  )
}

# The export URL is read out of page storage, so it is untrusted: download.file()
# would fetch whatever scheme it names. Allow https only. The host is NOT pinned:
# openFEC returns presigned S3 URLs and the real export host is unverified, so a
# fec.gov allowlist could break live exports.
fec_require_https_url <- function(url) {
  if (!(is.character(url) && length(url) == 1L && !is.na(url))) {
    stop("FEC export URL refused: not a single string.", call. = FALSE)
  }
  # A real export URL is ASCII (percent-encoded where needed). Refuse any other
  # byte first, so lookalikes (NBSP, fullwidth quote/backslash/@, zero-width,
  # bidi override, homoglyph hosts) fail whatever the string's encoding mark.
  if (grepl("[^\\x01-\\x7e]", url, perl = TRUE, useBytes = TRUE)) {
    stop(
      "FEC export URL refused: it must be ASCII (percent-encode any other character).",
      call. = FALSE
    )
  }
  authority <- sub("^https://([^/?#]*).*$", "\\1", url, ignore.case = TRUE)
  if (
    !grepl("^https://", url, ignore.case = TRUE) ||
      grepl("[[:space:][:cntrl:]\\\\]", url) ||
      !grepl("^[^@:]+(:[0-9]+)?$", authority)
  ) {
    stop(
      "FEC export URL refused: it must be an https URL with a plain host ",
      "(no userinfo, backslash, whitespace or control characters).",
      call. = FALSE
    )
  }
  invisible(url)
}

fec_export_current_query <- function(
  session,
  expected_rows,
  timeout_seconds = 300
) {
  expected_rows <- as.integer(expected_rows)
  if (is.na(expected_rows) || expected_rows < 0L) {
    stop("FEC export row count must be a non-negative integer.", call. = FALSE)
  }
  if (!expected_rows) return(data.table::data.table())
  clicked <- fec_runtime_value(
    session,
    paste0(
      "(function(){",
      "document.querySelectorAll('.download .js-close').forEach(",
      "function(button){button.click();});",
      "Object.keys(localStorage).filter(function(key){",
      "return key.indexOf('download-')===0;",
      "}).forEach(function(key){localStorage.removeItem(key);});",
      "var button=document.querySelector('.js-export');",
      "if(!button){return false;} button.click(); return true;",
      "})()"
    )
  )
  if (!isTRUE(clicked)) {
    stop("FEC export button was unavailable.", call. = FALSE)
  }
  download_url <- ""
  export_error <- ""
  deadline <- Sys.time() + timeout_seconds
  message("FEC export requested for ", expected_rows, " rows.")
  repeat {
    status <- fec_runtime_value(
      session,
      paste0(
        "(function(){",
        "var keys=Object.keys(localStorage).filter(function(key){",
        "return key.indexOf('download-')===0;",
        "});",
        "for(var i=0;i<keys.length;i++){",
        "try{var value=JSON.parse(localStorage.getItem(keys[i]));",
        "if(value.downloadUrl){return {url:value.downloadUrl,error:''};}",
        "}catch(e){}",
        "}",
        "var alert=document.querySelector('.message--alert');",
        "return {url:'',error:alert?(alert.textContent||'').trim():''};",
        "})()"
      )
    )
    if (is.list(status)) {
      download_url <- trim_text(status$url %||% "")
      export_error <- trim_text(status$error %||% "")
    }
    if (nzchar(download_url)) break
    if (nzchar(export_error)) {
      stop("FEC export server error: ", export_error, call. = FALSE)
    }
    if (Sys.time() >= deadline) {
      stop("Timed out waiting for the FEC export.", call. = FALSE)
    }
    Sys.sleep(2)
  }
  fec_require_https_url(download_url)
  require_package("data.table")
  csv_path <- tempfile("mentat-fec-export-", fileext = ".csv")
  on.exit(unlink(csv_path, force = TRUE), add = TRUE)
  download_result <- tryCatch(
    utils::download.file(
      download_url,
      csv_path,
      mode = "wb",
      quiet = TRUE
    ),
    error = function(error) {
      stop(
        "FEC export download failed: ",
        conditionMessage(error),
        call. = FALSE
      )
    }
  )
  if (!identical(as.integer(download_result), 0L)) {
    stop(
      "FEC export download failed with status ",
      download_result,
      ".",
      call. = FALSE
    )
  }
  rows <- fec_parse_export_file(session, csv_path, expected_rows)
  message("FEC export downloaded and verified: ", nrow(rows), " rows.")
  rows[]
}

fec_export_partitions <- function(
  session,
  url,
  expected_total,
  min_date = as.Date("1900-01-01"),
  max_date = as.Date("2100-12-31"),
  max_rows = 100000L
) {
  max_rows <- as.integer(max_rows)
  if (is.na(max_rows) || max_rows < 1L) {
    stop("FEC export partition size must be positive.", call. = FALSE)
  }
  leaves <- list()
  visit <- function(left, right) {
    partition_url <- fec_url_with_date_range(url, left, right)
    fec_navigate(session, partition_url)
    payload <- fec_set_page_length(session, 30L)
    count <- as.integer(payload$info$recordsTotal)
    if (!count) return(invisible(NULL))
    if (count <= max_rows || left >= right) {
      leaves[[length(leaves) + 1L]] <<- list(
        url = partition_url,
        count = count,
        min_date = left,
        max_date = right
      )
      return(invisible(NULL))
    }
    midpoint <- left + floor(as.numeric(right - left) / 2)
    visit(left, midpoint)
    visit(midpoint + 1, right)
    invisible(NULL)
  }
  visit(as.Date(min_date), as.Date(max_date))
  partition_total <- sum(vapply(
    leaves,
    function(leaf) as.integer(leaf$count),
    integer(1L)
  ))
  if (partition_total != as.integer(expected_total)) {
    stop(
      "FEC export date partitions do not cover the query exactly: expected=",
      expected_total,
      " partition_total=",
      partition_total,
      call. = FALSE
    )
  }
  message(
    "FEC export partition plan verified: ",
    length(leaves),
    " file(s), ",
    partition_total,
    " rows."
  )
  leaves
}

fec_browser_api_url <- function(session) {
  url <- trim_text(fec_runtime_value(
    session,
    paste0(
      "(function(){",
      "var urls=performance.getEntriesByType('resource')",
      ".map(function(entry){return entry.name;})",
      ".filter(function(value){return value.indexOf(",
      "'api.open.fec.gov/v1/schedules/schedule_a/')>=0;});",
      "return urls.length?urls[urls.length-1]:'';",
      "})()"
    )
  ))
  if (!nzchar(url)) {
    stop(
      "FEC browser API fallback could not locate the loaded query.",
      call. = FALSE
    )
  }
  url
}

fec_browser_api_batch <- function(
  session,
  api_url,
  pages,
  cursor = list(),
  per_page = 100L,
  attempts = 4L
) {
  require_package("jsonlite")
  if (is.null(fec_browser_evidence_callback(session))) return(fec_browser_api_batch_legacy(
    session, api_url, pages, cursor, per_page, attempts))
  pages <- as.integer(pages)
  responses <- list()
  for (page in pages) {
    response <- list(page = page, status = 0L, error = "request did not start")
    for (attempt in seq_len(as.integer(attempts))) {
      expression <- sprintf(paste0(
        "(async function(){try{var url=new URL(%s);var cursor=%s;",
        "url.searchParams.delete('page');url.searchParams.delete('last_contribution_receipt_date');",
        "url.searchParams.delete('last_index');url.searchParams.set('per_page',String(%d));",
        "if(cursor.last_contribution_receipt_date){url.searchParams.set('last_contribution_receipt_date',String(cursor.last_contribution_receipt_date));}",
        "if(cursor.last_index){url.searchParams.set('last_index',String(cursor.last_index));}",
        "var response=await fetch(url.href);var bytes=new Uint8Array(await response.arrayBuffer());",
        "var binary='';for(var i=0;i<bytes.length;i++){binary+=String.fromCharCode(bytes[i]);}",
        "return JSON.stringify({status:response.status,bytes_base64:btoa(binary),retry_after:Number(response.headers.get('Retry-After')||'0')});",
        "}catch(error){return JSON.stringify({status:0,error:'browser transport failed'});}})()"),
        jsonlite::toJSON(api_url, auto_unbox = TRUE),
        jsonlite::toJSON(cursor, auto_unbox = TRUE, null = "null"), as.integer(per_page))
      # Parse only the transport envelope first; archive original bytes before parsing the source JSON.
      wire <- jsonlite::fromJSON(fec_runtime_value(session, expression, timeout_seconds = 60), simplifyVector = FALSE)
      status <- as.integer(wire$status %||% 0L)
      response <- list(page = page, status = status, error = wire$error %||% "")
      if (!is.null(wire$bytes_base64)) {
        bytes <- jsonlite::base64_dec(wire$bytes_base64)
        fec_retain_browser_bytes(session, bytes, "fec_browser_http_body_bytes", page - 1L, status)
        if (status == 200L) {
          response$body <- jsonlite::fromJSON(rawToChar(bytes), simplifyVector = FALSE, bigint_as_char = TRUE)
          break
        }
        response$error <- "FEC HTTP response refused"
      }
      if (status != 0L && status != 429L && status < 500L) break
      if (attempt < attempts) {
        retry_after <- suppressWarnings(as.numeric(wire$retry_after %||% 0))
        Sys.sleep(if (is.finite(retry_after) && retry_after > 0) retry_after else min(8, 2^(attempt - 1L)))
      }
    }
    responses[[length(responses) + 1L]] <- response
    if (response$status != 200L || is.null(response$body)) break
    cursor <- response$body$pagination$last_indexes %||% list()
  }
  list(responses = responses, cursor = cursor)
}

fec_browser_api_batch_legacy <- function(
  session,
  api_url,
  pages,
  cursor = list(),
  per_page = 100L,
  attempts = 4L
) {
  require_package("jsonlite")
  pages <- as.integer(pages)
  per_page <- as.integer(per_page)
  attempts <- as.integer(attempts)
  expression <- sprintf(
    paste0(
      "(async function(){",
      "var base=new URL(%s);",
      "var pages=%s;",
      "var cursor=%s;",
      "var perPage=%d;",
      "var attempts=%d;",
      # Match complete JSON strings first, so digits inside names/memos are
      # untouched. Quote long integer tokens before JS can round IDs/cursors.
      "function parseExact(text){return JSON.parse(text.replace(",
      r"(/"(?:[^"\\]|\\.)*"|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/g,)",
      r"(function(token){return /^-?\d{16,}$/.test(token)?'"'+token+'"':token;}));})",
      "async function fetchPage(page,cursor){",
      "var last={status:0,error:'request did not start'};",
      "for(var attempt=1;attempt<=attempts;attempt++){",
      "try{",
      "var url=new URL(base.href);",
      "url.searchParams.delete('page');",
      "url.searchParams.delete('last_contribution_receipt_date');",
      "url.searchParams.delete('last_index');",
      "url.searchParams.set('per_page',String(perPage));",
      "if(cursor.last_contribution_receipt_date){",
      "url.searchParams.set(",
      "'last_contribution_receipt_date',",
      "String(cursor.last_contribution_receipt_date));",
      "}",
      "if(cursor.last_index){",
      "url.searchParams.set('last_index',String(cursor.last_index));",
      "}",
      "var response=await fetch(url.href);",
      "if(response.ok){",
      "return {page:page,status:response.status,body:parseExact(await response.text())};",
      "}",
      "last={page:page,status:response.status,error:await response.text()};",
      "if(response.status!==429 && response.status<500){return last;}",
      "var retryAfter=Number(response.headers.get('Retry-After')||'0');",
      "var delay=retryAfter>0?retryAfter*1000:",
      "Math.min(8000,Math.pow(2,attempt-1)*1000);",
      "await new Promise(function(resolve){setTimeout(resolve,delay);});",
      "}catch(error){",
      "last={page:page,status:0,error:String(error)};",
      "if(attempt<attempts){",
      "await new Promise(function(resolve){",
      "setTimeout(resolve,Math.min(8000,Math.pow(2,attempt-1)*1000));",
      "});",
      "}",
      "}",
      "}",
      "return last;",
      "}",
      "var responses=[];",
      "for(var index=0;index<pages.length;index++){",
      "var result=await fetchPage(pages[index],cursor);",
      "responses.push(result);",
      "if(result.status!==200 || !result.body){break;}",
      "cursor=(result.body.pagination&&result.body.pagination.last_indexes)||{};",
      "}",
      "return JSON.stringify({responses:responses,cursor:cursor});",
      "})()"
    ),
    jsonlite::toJSON(api_url, auto_unbox = TRUE),
    jsonlite::toJSON(as.list(pages), auto_unbox = TRUE),
    jsonlite::toJSON(cursor, auto_unbox = TRUE, null = "null"),
    per_page,
    attempts
  )
  payload <- fec_runtime_value(session, expression, timeout_seconds = 60)
  jsonlite::fromJSON(payload, simplifyVector = FALSE, bigint_as_char = TRUE)
}

fec_walk_variant_set_api <- function(
  session,
  records_total,
  page_callback,
  canary_callback,
  company,
  cycle,
  variant_set,
  per_page = 100L,
  batch_pages = 10L,
  batch_attempts = 4L
) {
  require_package("data.table")
  records_total <- as.integer(records_total)
  per_page <- as.integer(per_page)
  batch_pages <- as.integer(batch_pages)
  batch_attempts <- as.integer(batch_attempts)
  if (
    records_total < 0L ||
      per_page < 1L ||
      batch_pages < 1L ||
      batch_attempts < 1L
  ) {
    stop("Invalid FEC browser API fallback bounds.", call. = FALSE)
  }
  api_url <- fec_browser_api_url(session)
  pages <- if (records_total) {
    seq_len(as.integer(ceiling(records_total / per_page)))
  } else {
    integer()
  }
  processed <- 0L
  batches <- if (length(pages)) {
    split(pages, ceiling(seq_along(pages) / batch_pages))
  } else {
    list()
  }
  cursor <- list(
    last_contribution_receipt_date = "",
    last_index = ""
  )
  seen_sub_ids <- new.env(hash = TRUE, parent = emptyenv())
  for (batch_index in seq_along(batches)) {
    batch <- batches[[batch_index]]
    message(
      "FEC browser API fallback batch ",
      batch_index,
      "/",
      length(batches),
      ": pages ",
      min(batch),
      "-",
      max(batch),
      "."
    )
    batch_result <- NULL
    responses <- list()
    batch_error <- ""
    for (batch_attempt in seq_len(batch_attempts)) {
      batch_result <- tryCatch(
        fec_browser_api_batch(
          session,
          api_url,
          batch,
          cursor = cursor,
          per_page = per_page
        ),
        error = function(error) {
          if (inherits(error, "mentat_evidence_archive_failed")) stop(error)
          batch_error <<- conditionMessage(error)
          NULL
        }
      )
      responses <- batch_result$responses %||% list()
      response_ready <- length(responses) == length(batch) && all(vapply(
        responses,
        function(response) {
          identical(as.integer(response$status %||% 0L), 200L) &&
            !is.null(response$body)
        },
        logical(1L)
      ))
      if (response_ready) break
      if (length(responses)) {
        last_response <- responses[[length(responses)]]
        batch_error <- paste0(
          "responses=", length(responses), "/", length(batch),
          "; last_page=", last_response$page %||% "?",
          "; last_status=", last_response$status %||% 0L,
          "; error=", substr(
            trim_text(last_response$error %||% batch_error),
            1L,
            240L
          )
        )
      } else if (!nzchar(batch_error)) {
        batch_error <- "no responses returned"
      }
      if (batch_attempt < batch_attempts) {
        message(
          "FEC browser API fallback retry ",
          batch_attempt,
          "/",
          batch_attempts - 1L,
          " for pages ",
          min(batch),
          "-",
          max(batch),
          ": ",
          batch_error
        )
        Sys.sleep(min(8, 2^(batch_attempt - 1L)))
      }
    }
    next_cursor <- batch_result$cursor %||% list()
    if (length(responses) != length(batch)) {
      stop(
        "FEC browser API fallback returned the wrong response count after ",
        batch_attempts,
        " attempts: ",
        batch_error,
        call. = FALSE
      )
    }
    batch_rows <- list()
    batch_last_page <- NA_integer_
    for (response in responses) {
      page <- as.integer(response$page %||% NA_integer_)
      status <- as.integer(response$status %||% 0L)
      if (is.na(page) || status != 200L || is.null(response$body)) {
        stop(
          "FEC browser API fallback failed on page ",
          if (is.na(page)) "?" else page,
          " with status ",
          status,
          ".",
          call. = FALSE
        )
      }
      body <- response$body
      pagination <- body$pagination %||% list()
      observed_total <- as.integer(pagination$count %||% NA_integer_)
      if (!identical(observed_total, records_total)) {
        stop(
          "FEC browser API fallback total changed: expected=",
          records_total,
          " observed=",
          observed_total,
          call. = FALSE
        )
      }
      rows <- body$results %||% list()
      row_ids <- vapply(
        rows,
        function(row) trim_text(row$sub_id %||% ""),
        character(1L)
      )
      duplicate_ids <- duplicated(row_ids) | vapply(
        row_ids,
        exists,
        logical(1L),
        envir = seen_sub_ids,
        inherits = FALSE
      )
      page_zero <- page - 1L
      expected_rows <- if (page < length(pages)) {
        per_page
      } else {
        records_total - (page_zero * per_page)
      }
      payload <- list(
        info = list(
          page = page_zero,
          pages = length(pages),
          start = page_zero * per_page,
          end = page_zero * per_page + length(rows),
          length = per_page,
          recordsTotal = records_total,
          recordsDisplay = records_total,
          serverSide = TRUE
        ),
        rows = rows
      )
      checks <- fec_canaries(payload, records_total)
      checks <- data.table::rbindlist(list(
        checks,
        data.table::data.table(
          check_name = "api_page_row_count",
          passed = as.integer(length(rows) == expected_rows),
          detail = paste0(
            "page=", page,
            "; rows=", length(rows),
            "; expected=", expected_rows
          )
        ),
        data.table::data.table(
          check_name = "api_unique_page_ids",
          passed = as.integer(!any(duplicate_ids)),
          detail = paste0(
            "page=", page,
            "; duplicate_sub_ids=", sum(duplicate_ids)
          )
        )
      ))
      canary_callback(
        company = company,
        cycle = cycle,
        variant_set = variant_set,
        page = page_zero,
        checks = checks
      )
      if (any(checks$passed == 0L)) {
        stop(
          "FEC browser API fallback canary failure on page ",
          page,
          ".",
          call. = FALSE
        )
      }
      for (sub_id in row_ids) {
        assign(sub_id, TRUE, envir = seen_sub_ids)
      }
      batch_rows <- c(batch_rows, rows)
      batch_last_page <- page_zero
    }
    if (
      max(batch) < length(pages) &&
      (
        !nzchar(trim_text(next_cursor$last_contribution_receipt_date %||% "")) ||
          !nzchar(trim_text(next_cursor$last_index %||% ""))
      )
    ) {
      stop(
        "FEC browser API fallback did not return a continuation cursor.",
        call. = FALSE
      )
    }
    cursor <- next_cursor
    if (length(batch_rows)) {
      page_callback(
        rows = batch_rows,
        page = batch_last_page,
        records_total = records_total
      )
      processed <- processed + length(batch_rows)
    }
  }
  if (processed != records_total) {
    stop(
      "FEC browser API fallback processing mismatch: expected=",
      records_total,
      " processed=",
      processed,
      call. = FALSE
    )
  }
  list(
    records_total = records_total,
    pages = length(pages),
    retries = 0L,
    truncated = FALSE,
    export = FALSE,
    api_fallback = TRUE
  )
}

fec_walk_variant_set_export <- function(
  session,
  url,
  records_total,
  page_callback,
  canary_callback,
  company,
  cycle,
  variant_set,
  chunk_size = 20000L
) {
  require_package("data.table")
  # The live FEC exporter was verified on 2026-07-23 to produce a download
  # for a 151,184-row query. Exporting the authoritative query directly also
  # retains undated records that disappear when min_date/max_date filters are
  # applied, so do not impose the obsolete 100,000-row client-side split.
  processed <- 0L
  consume_export <- function(exported) {
    if (!nrow(exported)) return(invisible(NULL))
    starts <- seq.int(1L, nrow(exported), by = as.integer(chunk_size))
    for (start in starts) {
      end <- min(start + as.integer(chunk_size) - 1L, nrow(exported))
      chunk <- exported[start:end]
      processed <<- processed + nrow(chunk)
      page_callback(
        rows = chunk,
        page = floor((processed - 1L) / 100L),
        records_total = records_total
      )
      # Bound the overlap between the export table, flattened rows, and the
      # SQLite staging frame. Long-lived Windows sessions have exited natively
      # at this handoff without raising an R condition.
      rm(chunk)
      gc(verbose = FALSE)
    }
    invisible(NULL)
  }
  direct_error <- NULL
  direct <- NULL
  export_attempts <- 4L
  for (export_attempt in seq_len(export_attempts)) {
    message(
      "FEC export file attempt ",
      export_attempt,
      "/",
      export_attempts,
      ": ",
      records_total,
      " rows."
    )
    direct <- tryCatch(
      fec_export_current_query(session, records_total),
      error = function(error) {
        direct_error <<- error
        NULL
      }
    )
    if (!is.null(direct)) break
    if (
      !grepl(
        "fec export server error",
        tolower(conditionMessage(direct_error)),
        fixed = TRUE
      ) ||
      export_attempt >= export_attempts
    ) {
      break
    }
    message(
      "FEC export server retry ",
      export_attempt,
      "/",
      export_attempts - 1L,
      ": ",
      conditionMessage(direct_error)
    )
    Sys.sleep(min(12, export_attempt * 3))
  }
  if (!is.null(direct)) {
    consume_export(direct)
    direct <- NULL
    gc(verbose = FALSE)
  } else {
    if (!grepl(
      "fec export server error",
      tolower(conditionMessage(direct_error)),
      fixed = TRUE
    )) {
      stop(direct_error)
    }
    message(
      "FEC full export failed server-side; using verified browser API pages."
    )
    return(fec_walk_variant_set_api(
      session = session,
      records_total = records_total,
      page_callback = page_callback,
      canary_callback = canary_callback,
      company = company,
      cycle = cycle,
      variant_set = variant_set
    ))
  }
  checks <- data.table::data.table(
    check_name = "export_row_count",
    passed = as.integer(processed == as.integer(records_total)),
    detail = paste0("rows=", processed, "; expected=", records_total)
  )
  canary_callback(
    company = company,
    cycle = cycle,
    variant_set = variant_set,
    page = max(0L, ceiling(records_total / 100L) - 1L),
    checks = checks
  )
  if (processed != as.integer(records_total)) {
    stop(
      "FEC export processing mismatch: expected=", records_total,
      " processed=", processed,
      call. = FALSE
    )
  }
  list(
    records_total = as.integer(records_total),
    pages = as.integer(ceiling(records_total / 100L)),
    retries = 0L,
    truncated = FALSE,
    export = TRUE
  )
}

fec_walk_variant_set_once <- function(
  session,
  company,
  cycle,
  variants,
  variant_set,
  page_callback,
  canary_callback,
  query_field = "contributor_employer",
  query_filters = list(),
  timeout_seconds = 60,
  retries = 3L,
  sleep_range = c(1.5, 3.5),
  max_pages = Inf
) {
  max_pages <- as.numeric(max_pages)
  if (length(max_pages) != 1L || is.na(max_pages) || max_pages <= 0) {
    stop("max_pages must be a positive number.", call. = FALSE)
  }
  url <- fec_build_url(
    cycle,
    variants,
    query_field = query_field,
    query_filters = query_filters
  )
  retry_count <- fec_navigate(
    session,
    url,
    timeout_seconds = timeout_seconds,
    retries = retries
  )
  # The durable checkpoint contract is 100 rows/page. Enforce it before the
  # first callback so resume never has to infer completion from an arbitrary UI
  # preference left in local storage.
  initial <- fec_set_page_length(session, 100L)
  records_total <- initial$info$recordsTotal
  if (records_total >= 500L && is.infinite(max_pages)) {
    exported <- fec_walk_variant_set_export(
      session = session,
      url = url,
      records_total = records_total,
      page_callback = page_callback,
      canary_callback = canary_callback,
      company = company,
      cycle = cycle,
      variant_set = variant_set
    )
    exported$retries <- as.integer(exported$retries) + as.integer(retry_count)
    return(exported)
  }
  expected_total <- records_total
  first_payload <- initial
  processed_pages <- 0L
  seen_sub_ids <- character()
  repeat {
    payload <- first_payload
    first_payload <- NULL
    if (is.null(payload)) {
      payload <- fec_read_page(session)
    }
    page <- payload$info$page
    checks <- fec_canaries(payload, expected_total)
    canary_callback(
      company = company,
      cycle = cycle,
      variant_set = variant_set,
      page = page,
      checks = checks
    )
    if (any(checks$passed == 0L)) {
      failed <- checks[passed == 0L, check_name]
      stop(
        "FEC canary failure for ", company, " cycle ", cycle,
        " page ", page, ": ", paste(failed, collapse = ", "),
        call. = FALSE
      )
    }
    page_sub_ids <- vapply(
      payload$rows,
      function(row) trim_text(row$sub_id %||% ""),
      character(1L)
    )
    repeated_sub_ids <- intersect(page_sub_ids, seen_sub_ids)
    if (length(repeated_sub_ids)) {
      stop(
        "FEC pagination repeated sub_id(s) across pages: ",
        paste(head(unique(repeated_sub_ids), 5L), collapse = ", "),
        call. = FALSE
      )
    }
    seen_sub_ids <- c(seen_sub_ids, page_sub_ids)
    page_callback(
      rows = payload$rows,
      page = page,
      records_total = records_total
    )
    processed_pages <- processed_pages + 1L
    if (processed_pages >= max_pages ||
        page + 1L >= payload$info$pages) {
      break
    }
    Sys.sleep(stats::runif(1L, min(sleep_range), max(sleep_range)))
    first_payload <- fec_click_next(session, page)
  }
  list(
    records_total = records_total,
    pages = initial$info$pages,
    retries = retry_count,
    truncated = processed_pages < initial$info$pages
  )
}

fec_walk_variant_set <- function(
  session,
  company,
  cycle,
  variants,
  variant_set,
  page_callback,
  canary_callback,
  query_field = "contributor_employer",
  query_filters = list(),
  timeout_seconds = 60,
  retries = 3L,
  sleep_range = c(1.5, 3.5),
  query_retries = 3L,
  max_pages = Inf,
  evidence_connection = NULL
) {
  query_retries <- as.integer(query_retries)
  if (is.na(query_retries) || query_retries < 0L) {
    stop("query_retries must be a non-negative integer.", call. = FALSE)
  }
  last_error <- NULL
  for (attempt in seq_len(query_retries + 1L)) {
    active_session <- fec_active_session(session)
    previous_evidence <- fec_browser_evidence_callback(active_session)
    attr(active_session, "mentat_evidence_callback") <- if (is.null(evidence_connection)) previous_evidence else fec_chrome_archive_callback(
      evidence_connection, company, cycle, variant_set, variants, query_field, query_filters)
    result <- tryCatch(
      fec_walk_variant_set_once(
        session = active_session,
        company = company,
        cycle = cycle,
        variants = variants,
        variant_set = variant_set,
        page_callback = page_callback,
        canary_callback = canary_callback,
        query_field = query_field,
        query_filters = query_filters,
        timeout_seconds = timeout_seconds,
        retries = retries,
        sleep_range = sleep_range,
        max_pages = max_pages
      ),
      error = function(error) {
        last_error <<- error
        NULL
      },
      finally = { attr(active_session, "mentat_evidence_callback") <- previous_evidence }
    )
    if (!is.null(result)) {
      result$retries <- as.integer(result$retries) + attempt - 1L
      return(result)
    }
    transient <- fec_is_transient_chromote_error(last_error)
    if (!transient || attempt > query_retries) {
      stop(last_error)
    }
    message(
      "Transient FEC browser failure for ", company, " cycle ", cycle,
      " (", variant_set, "); replaying the query, attempt ",
      attempt + 1L, " of ", query_retries + 1L, ": ",
      conditionMessage(last_error)
    )
    replacement <- fec_recover_session(active_session)
    fec_replace_session(session, replacement)
    fec_query_retry_sleep(attempt)
  }
  stop(conditionMessage(last_error), call. = FALSE)
}

fec_determinism_spot_check <- function(session) {
  session <- fec_active_session(session)
  first <- fec_read_page(session)
  second <- fec_read_page(session)
  ids <- function(payload) {
    sort(vapply(payload$rows, function(row) trim_text(row$sub_id %||% ""), character(1L)))
  }
  identical(ids(first), ids(second))
}

# Mentat single-pipeline entry point
#
# RStudio: open this file and click Source. Command line: Rscript mentat.R [flags]
#
# Flags:
#   --industry=NAME          Select one configured industry (default: Crypto).
#   --cycles=2022,2024,2026   Comma-separated subset of supported FEC cycles.
#   --data-root=PATH          Override MENTAT_DATA_ROOT for this run.
#   --engine=api|chrome       Override MENTAT_ENGINE (default: api).
#   --concurrency=N           Override MENTAT_CONCURRENCY (API default: 8).
#   --skip-scrape             Run ingest, reconciliation, and reports without network.
#   --phase2-names            Opt in to contributor-name identity collection.
#   --skip-phase2-names       Disable contributor-name identity collection (default).
#   --resume-after=TIMESTAMP  Chrome-only checkpoint floor, YYYY-MM-DDTHH:MM:SS...
#   --defer-report-files      Prepare final database flags but let a downstream
#                             report generator write the user-facing files.
#   --retag-only              Recompute stored leg/company tags and rewrite reports.
#   --help                    Print this flag list without running the pipeline.
#
# Environment variables:
#   FEC_API_KEY               Required for the default API engine. Obtain a free key
#                             at https://api.data.gov/signup. The key is never logged.
#   MENTAT_DATA_ROOT          SQLite/checkpoint/report root outside synchronized storage.
#   MENTAT_INDUSTRY           Industry roster to collect (default: Crypto).
#   MENTAT_ENGINE             api (default) or chrome.
#   MENTAT_CONCURRENCY        Concurrent API work units (default: 8).
#   MENTAT_BULK_DIR           Optional FEC bulk-superset directory (default: DATA_ROOT/bulk).
#   MENTAT_RESCUE_CAP         Maximum bulk name-rescue requests per run (default: 50).
#   MENTAT_SPENDING_TEMPLATE  Optional Spending Report template workbook override.
#   R_LIBS_USER               R package library; on the target host use
#                             C:/Users/pgoff/AppData/Local/R/win-library/4.6.
#   MENTAT_R_LIBRARY          Library R/paths.R adds when data.table is not found (default: that Windows
#                             library; a container sets its own).

mentat_entry_path <- function() {
  file_argument <- grep("^--file=", commandArgs(trailingOnly = FALSE), value = TRUE)
  if (length(file_argument)) {
    candidate <- sub("^--file=", "", file_argument[[1L]])
    if (file.exists(candidate) && identical(
      tolower(basename(candidate)),
      "mentat.r"
    )) {
      return(normalizePath(candidate, winslash = "/", mustWork = TRUE))
    }
  }
  frames <- rev(sys.frames())
  for (frame in frames) {
    candidate <- get0("ofile", envir = frame, inherits = FALSE, ifnotfound = "")
    if (length(candidate) == 1L && nzchar(candidate) && file.exists(candidate)) {
      candidate <- normalizePath(candidate, winslash = "/", mustWork = TRUE)
      if (identical(tolower(basename(candidate)), "mentat.r")) return(candidate)
    }
  }
  current <- normalizePath(getwd(), winslash = "/", mustWork = TRUE)
  repeat {
    candidate <- file.path(current, "mentat.R")
    if (file.exists(candidate)) {
      return(normalizePath(candidate, winslash = "/", mustWork = TRUE))
    }
    parent <- dirname(current)
    if (identical(parent, current)) break
    current <- parent
  }
  stop("Could not locate mentat.R; open the workspace copy and Source it again.", call. = FALSE)
}

MENTAT_ENTRY_PATH <- mentat_entry_path()
MENTAT_PROJECT_ROOT <- dirname(MENTAT_ENTRY_PATH)

for (module in c(
  "paths.R", "schema.R", "ingest.R", "fec_page.R", "collect.R",
  "fec_api.R", "reconcile.R", "report.R"
)) {
  source(file.path(MENTAT_PROJECT_ROOT, "R", module), local = .GlobalEnv)
}

mentat_usage <- function() {
  lines <- readLines(MENTAT_ENTRY_PATH, warn = FALSE, encoding = "UTF-8")
  comment_lines <- lines[seq_len(min(length(lines), 30L))]
  comment_lines <- comment_lines[grepl("^#", comment_lines)]
  cat(sub("^# ?", "", comment_lines), sep = "\n")
  invisible(NULL)
}

mentat_parse_arguments <- function(arguments) {
  result <- list(
    industry = trimws(Sys.getenv("MENTAT_INDUSTRY", unset = "Crypto")),
    cycles = c(2022L, 2024L, 2026L),
    data_root = NULL,
    engine = trimws(Sys.getenv("MENTAT_ENGINE", unset = "api")),
    concurrency = NULL,
    skip_scrape = FALSE,
    phase2_names = FALSE,
    resume_after = NULL,
    defer_report_files = FALSE,
    retag_only = FALSE,
    help = FALSE
  )
  if (!nzchar(result$engine)) result$engine <- "api"
  for (argument in arguments) {
    if (grepl("^--industry=", argument)) {
      value <- trimws(sub("^--industry=", "", argument))
      if (!nzchar(value)) stop("--industry cannot be empty.", call. = FALSE)
      result$industry <- value
    } else if (grepl("^--cycles=", argument)) {
      values <- strsplit(sub("^--cycles=", "", argument), ",", fixed = TRUE)[[1L]]
      cycles <- suppressWarnings(as.integer(trimws(values)))
      if (!length(cycles) || anyNA(cycles) ||
          any(!cycles %in% c(2022L, 2024L, 2026L))) {
        stop("--cycles must be a comma-separated subset of 2022,2024,2026.", call. = FALSE)
      }
      result$cycles <- sort(unique(cycles))
    } else if (grepl("^--data-root=", argument)) {
      value <- trimws(sub("^--data-root=", "", argument))
      if (!nzchar(value)) stop("--data-root cannot be empty.", call. = FALSE)
      result$data_root <- value
    } else if (grepl("^--engine=", argument)) {
      result$engine <- tolower(trimws(sub("^--engine=", "", argument)))
    } else if (grepl("^--concurrency=", argument)) {
      value <- trimws(sub("^--concurrency=", "", argument))
      result$concurrency <- fec_api_concurrency(value)
    } else if (identical(argument, "--skip-scrape")) {
      result$skip_scrape <- TRUE
    } else if (identical(argument, "--phase2-names")) {
      result$phase2_names <- TRUE
    } else if (identical(argument, "--skip-phase2-names")) {
      result$phase2_names <- FALSE
    } else if (grepl("^--resume-after=", argument)) {
      value <- trimws(sub("^--resume-after=", "", argument))
      if (!grepl("^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}", value, perl = TRUE)) {
        stop("--resume-after must begin with YYYY-MM-DDTHH:MM:SS.", call. = FALSE)
      }
      result$resume_after <- value
    } else if (identical(argument, "--defer-report-files")) {
      result$defer_report_files <- TRUE
    } else if (identical(argument, "--retag-only")) {
      result$retag_only <- TRUE
    } else if (identical(argument, "--help")) {
      result$help <- TRUE
    } else {
      stop("Unknown argument: ", argument, call. = FALSE)
    }
  }
  result$engine <- tolower(trimws(result$engine))
  if (!result$engine %in% c("api", "chrome")) {
    stop("MENTAT_ENGINE/--engine must be api or chrome.", call. = FALSE)
  }
  if (!is.null(result$resume_after) && result$engine != "chrome") {
    stop("--resume-after is supported only with --engine=chrome.", call. = FALSE)
  }
  if (!nzchar(result$industry)) {
    stop("MENTAT_INDUSTRY/--industry cannot be empty.", call. = FALSE)
  }
  result
}

mentat_leg_counts <- function(connection) {
  DBI::dbGetQuery(
    connection,
    paste(
      "SELECT COUNT(*) AS total_rows,",
      "COALESCE(SUM(is_conduit_leg), 0) AS conduit_legs,",
      "COALESCE(SUM(is_memo_leg), 0) AS memo_legs,",
      "COALESCE(SUM(is_passthrough_leg), 0) AS passthrough_legs,",
      "COALESCE(SUM(memo_review), 0) AS memo_review_rows",
      "FROM contributions"
    )
  )
}

mentat_retag_legs <- function(connection) {
  before <- mentat_leg_counts(connection)
  stored <- data.table::as.data.table(DBI::dbGetQuery(
    connection,
    paste(
      "SELECT sub_id, memo_text, memo_code, memoed_subtotal, committee_id",
      "FROM contributions ORDER BY sub_id"
    )
  ))
  if (nrow(stored)) {
    retagged <- stored[, .(
      sub_id,
      is_conduit_leg = as.integer(
        trim_text(committee_id) %in% c("C00401224", "C00694323")
      ),
      is_memo_leg = as.integer(
        toupper(trim_text(memo_code)) == "X" |
          as_db_bool(memoed_subtotal) == 1L
      ),
      is_passthrough_leg = as.integer(is_passthrough_memo(memo_text)),
      memo_review = as.integer(is_memo_review(memo_text))
    )]
    DBI::dbWithTransaction(connection, {
      DBI::dbExecute(connection, "DROP TABLE IF EXISTS temp._mentat_retag_values")
      DBI::dbExecute(
        connection,
        paste(
          "CREATE TEMP TABLE _mentat_retag_values (",
          "sub_id TEXT PRIMARY KEY, is_conduit_leg INTEGER NOT NULL,",
          "is_memo_leg INTEGER NOT NULL, is_passthrough_leg INTEGER NOT NULL,",
          "memo_review INTEGER NOT NULL)"
        )
      )
      DBI::dbAppendTable(connection, "_mentat_retag_values", as.data.frame(retagged))
      DBI::dbExecute(
        connection,
        paste(
          "UPDATE contributions SET",
          "is_conduit_leg=(SELECT is_conduit_leg FROM _mentat_retag_values r",
          "WHERE r.sub_id=contributions.sub_id),",
          "is_memo_leg=(SELECT is_memo_leg FROM _mentat_retag_values r",
          "WHERE r.sub_id=contributions.sub_id),",
          "is_passthrough_leg=(SELECT is_passthrough_leg FROM _mentat_retag_values r",
          "WHERE r.sub_id=contributions.sub_id),",
          "memo_review=(SELECT memo_review FROM _mentat_retag_values r",
          "WHERE r.sub_id=contributions.sub_id)",
          "WHERE sub_id IN (SELECT sub_id FROM _mentat_retag_values)"
        )
      )
      DBI::dbExecute(connection, "DROP TABLE _mentat_retag_values")
    })
  }
  after <- mentat_leg_counts(connection)
  cat("Before retag:\n")
  print(before, row.names = FALSE)
  cat("After retag:\n")
  print(after, row.names = FALSE)
  invisible(after)
}

mentat_main <- function(arguments = character()) {
  options <- mentat_parse_arguments(arguments)
  if (isTRUE(options$help)) return(mentat_usage())
  assign("MENTAT_INDUSTRY", options$industry, envir = .GlobalEnv)
  if (!is.null(options$concurrency)) {
    Sys.setenv(MENTAT_CONCURRENCY = as.character(options$concurrency))
  }
  if (!options$skip_scrape && !options$retag_only && options$engine == "api") {
    fec_api_key()
  }
  data_root <- options$data_root %||% mentat_default_data_root()
  data_root <- normalizePath(data_root, winslash = "/", mustWork = FALSE)
  paths <- mentat_bootstrap_dirs(data_root)
  connection <- db_connect(data_root)
  on.exit(DBI::dbDisconnect(connection), add = TRUE)
  init_schema(connection)
  cat("Mentat project root:", MENTAT_PROJECT_ROOT, "\n")
  cat("Mentat data root:", paths$root, "\n")

  if (isTRUE(options$retag_only)) {
    # Retagging is defined by the current file-backed registers as well as the
    # current R rules. Reload them first so a newly approved override, home
    # state, diminutive, or roster correction is not silently ignored merely
    # because collection is being skipped.
    ingest_all(
      connection,
      input_dir = mentat_input_dir(MENTAT_PROJECT_ROOT),
      data_root = data_root,
      print_summary = FALSE
    )
    mentat_retag_legs(connection)
    retag_employer_company_matches(connection)
    retag_identity_company_matches(connection)
    retag_corporate_company_matches(connection)
    reports <- if (isTRUE(options$defer_report_files)) {
      apply_amendment_versioning(connection, print_summary = TRUE)
      resolve_recipient_register_committee_ids(connection, log = TRUE)
      cat(
        "Native report files deferred; database is prepared for the ",
        "downstream workbook generator.\n",
        sep = ""
      )
      list()
    } else {
      write_reports(connection, data_root, retag = FALSE)
    }
    cat("Mentat retag-only run: PASS\n")
    return(invisible(list(reports = reports)))
  }

  run_id <- paste0(format(Sys.time(), "%Y%m%dT%H%M%S"), "-", Sys.getpid())
  started_at <- format(Sys.time(), "%Y-%m-%dT%H:%M:%OS%z")
  DBI::dbWithTransaction(connection, {
    for (cycle in options$cycles) {
      DBI::dbExecute(
        connection,
        paste(
          "INSERT INTO run_log",
          "(run_id, cycle, status, started_at, retry_count, message)",
          "VALUES (?, ?, ?, ?, 0, ?)"
        ),
        params = list(
          run_id,
          as.integer(cycle),
          if (options$skip_scrape) "offline_started" else "started",
          started_at,
          if (options$skip_scrape) "Collection intentionally skipped." else ""
        )
      )
    }
  })
  finish_run <- function(status, retry_count = 0L, determinism = NULL, message = "") {
    ended_at <- format(Sys.time(), "%Y-%m-%dT%H:%M:%OS%z")
    DBI::dbWithTransaction(connection, {
      for (index in seq_along(options$cycles)) {
        DBI::dbExecute(
          connection,
          paste(
            "UPDATE run_log SET status=?, ended_at=?, retry_count=?,",
            "determinism_result=?, message=? WHERE run_id=? AND cycle=?"
          ),
          params = list(
            status,
            ended_at,
            if (index == 1L) as.integer(retry_count) else 0L,
            if (is.null(determinism)) NA_character_ else as.character(determinism),
            message,
            run_id,
            as.integer(options$cycles[[index]])
          )
        )
      }
    })
  }

  tryCatch({
    ingest <- ingest_all(
      connection,
      input_dir = mentat_input_dir(MENTAT_PROJECT_ROOT),
      data_root = data_root,
      print_summary = TRUE
    )
    collection <- list(retries = 0L, determinism = NULL, request_count = 0L)
    if (!options$skip_scrape) {
      collection <- if (options$engine == "api") {
        collect_crypto_api(
          connection,
          cycles = options$cycles,
          phase2_names = options$phase2_names,
          concurrency = fec_api_concurrency(),
          run_id = run_id,
          data_root = data_root
        )
      } else {
        collect_crypto(
          connection,
          cycles = options$cycles,
          phase2_names = options$phase2_names,
          resume_after = options$resume_after
        )
      }
    } else {
      cat("Collection skipped: no network or browser session was opened.\n")
    }
    if (isTRUE(collection$paused)) {
      pause_reason <- collection$pause_reason %||% "API collection paused."
      finish_run(
        "paused",
        retry_count = collection$retries %||% 0L,
        determinism = collection$determinism,
        message = pause_reason
      )
      cat("Mentat pipeline paused:", pause_reason, "\n")
      return(invisible(list(
        ingest = ingest,
        collection = collection,
        paths = paths
      )))
    }
    company_matches <- retag_employer_company_matches(connection)
    identity_matches <- retag_identity_company_matches(connection)
    corporate_matches <- retag_corporate_company_matches(connection)
    if (nrow(company_matches)) print(company_matches)
    if (nrow(identity_matches)) print(identity_matches)
    if (nrow(corporate_matches)) print(corporate_matches)
    reconciliation <- if (identical(MENTAT_INDUSTRY, "Crypto")) {
      reconcile_fixture(
        connection,
        data_root = data_root,
        write_outputs = TRUE
      )
    } else {
      cat("Crypto fixture reconciliation: not applicable to ", MENTAT_INDUSTRY, ".\n", sep = "")
      list(
        total_matches = 0L,
        total_fixture = 0L,
        match_percent = NA_real_,
        covered_fixture = 0L,
        coverage_percent = NA_real_
      )
    }
    contribution_count <- DBI::dbGetQuery(
      connection,
      "SELECT COUNT(*) AS rows FROM contributions"
    )$rows[[1L]]
    if (identical(MENTAT_INDUSTRY, "Crypto") &&
        contribution_count > 0L && reconciliation$coverage_percent < 99.33) {
      stop(
        sprintf(
          "Fixture reconciliation coverage regressed: %.2f%% is below the 99.33%% gate.",
          reconciliation$coverage_percent
        ),
        call. = FALSE
      )
    }
    reports <- if (isTRUE(options$defer_report_files)) {
      apply_amendment_versioning(connection, print_summary = TRUE)
      resolve_recipient_register_committee_ids(connection, log = TRUE)
      cat(
        "Native report files deferred; database is prepared for the ",
        "downstream workbook generator.\n",
        sep = ""
      )
      list()
    } else {
      write_reports(connection, data_root, retag = FALSE)
    }
    collection_detail <- collection_completion_detail(
      engine = options$engine,
      collection = collection,
      skipped = options$skip_scrape
    )
    finish_run(
      if (options$skip_scrape) "offline_completed" else "completed",
      retry_count = collection$retries %||% 0L,
      determinism = collection$determinism,
      message = paste0(
        "Matched ", reconciliation$total_matches, " of ",
        reconciliation$total_fixture, " fixture rows; database coverage ",
        reconciliation$covered_fixture, " of ", reconciliation$total_fixture,
        "; ", collection_detail, "."
      )
    )
    if (identical(MENTAT_INDUSTRY, "Crypto")) {
      cat(sprintf(
        paste0(
          "Reconciliation: %d of %d strict matches (%.2f%%); ",
          "database coverage %d of %d (%.2f%%)\n"
        ),
        reconciliation$total_matches,
        reconciliation$total_fixture,
        reconciliation$match_percent,
        reconciliation$covered_fixture,
        reconciliation$total_fixture,
        reconciliation$coverage_percent
      ))
    }
    if (isTRUE(options$defer_report_files)) {
      cat("Report files deferred to the downstream workbook generator.\n")
    } else {
      cat("Reports written to:", paths$reports, "\n")
    }
    cat("Mentat pipeline: PASS\n")
    invisible(list(
      ingest = ingest,
      collection = collection,
      reconciliation = reconciliation,
      reports = reports
    ))
  }, error = function(error) {
    try(finish_run("failed", message = conditionMessage(error)), silent = TRUE)
    stop(error)
  })
}

mentat_rscript_entry <- any(vapply(
  grep("^--file=", commandArgs(trailingOnly = FALSE), value = TRUE),
  function(argument) {
    path <- sub("^--file=", "", argument)
    file.exists(path) && identical(
      normalizePath(path, winslash = "/", mustWork = TRUE),
      MENTAT_ENTRY_PATH
    )
  },
  logical(1L)
))
mentat_arguments <- if (mentat_rscript_entry) {
  commandArgs(trailingOnly = TRUE)
} else {
  character()
}
mentat_result <- mentat_main(mentat_arguments)

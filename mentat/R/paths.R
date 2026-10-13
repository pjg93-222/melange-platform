# R_LIBS_USER is the supported configuration; this fallback covers stripped environments.
if (!requireNamespace("data.table", quietly = TRUE)) {
  .libPaths(c(
    local({
      v <- trimws(Sys.getenv("MENTAT_R_LIBRARY", unset = ""))   # container: the image's library
      if (nzchar(v)) v else "C:/Users/pgoff/AppData/Local/R/win-library/4.6"
    }),
    .libPaths()
  ))
}

`%||%` <- function(x, y) {
  if (is.null(x) || length(x) == 0L || (length(x) == 1L && is.na(x))) y else x
}

require_package <- function(package) {
  if (!requireNamespace(package, quietly = TRUE)) {
    stop("Required installed package is unavailable: ", package, call. = FALSE)
  }
  invisible(TRUE)
}

mentat_default_data_root <- function() {
  configured <- trimws(Sys.getenv("MENTAT_DATA_ROOT", unset = ""))
  if (nzchar(configured)) {
    return(normalizePath(configured, winslash = "/", mustWork = FALSE))
  }
  local_app_data <- trimws(Sys.getenv("LOCALAPPDATA", unset = ""))
  if (!nzchar(local_app_data)) {
    stop(
      "LOCALAPPDATA is empty; set MENTAT_DATA_ROOT to a writable local directory.",
      call. = FALSE
    )
  }
  normalizePath(
    file.path(local_app_data, "Mentat"),
    winslash = "/",
    mustWork = FALSE
  )
}

mentat_paths <- function(data_root = mentat_default_data_root()) {
  root <- normalizePath(data_root, winslash = "/", mustWork = FALSE)
  list(
    root = root,
    database = file.path(root, "mentat.db"),
    reports = mentat_report_directory(),
    rosters_summary = file.path(root, "rosters_summary.txt")
  )
}

mentat_report_directory <- function(project_root = NULL) {
  configured <- trimws(Sys.getenv("MENTAT_REPORT_DIRECTORY", unset = ""))
  if (!nzchar(configured)) {
    if (is.null(project_root)) {
      project_root <- get0("MENTAT_PROJECT_ROOT", inherits = TRUE, ifnotfound = NULL) %||% mentat_project_root()
    }
    config_path <- file.path(project_root, "config", "report-directory.json")
    if (!file.exists(config_path)) stop("The authoritative report directory is not configured.", call. = FALSE)
    require_package("jsonlite")
    config <- jsonlite::fromJSON(config_path)
    if (!identical(as.integer(config$schema_version), 1L)) stop("Unsupported report-directory configuration.", call. = FALSE)
    configured <- config$directory
  }
  if (!is.character(configured) || length(configured) != 1L || is.na(configured) ||
      !grepl("^(?:[A-Za-z]:[/\\\\]|/|\\\\\\\\)", configured, perl = TRUE)) {
    stop("The report directory must be an explicit absolute path.", call. = FALSE)
  }
  if (!dir.exists(configured)) stop("The configured report directory is unavailable; no alternate directory will be used.", call. = FALSE)
  normalizePath(configured, winslash = "/", mustWork = TRUE)
}

mentat_bulk_dir <- function(data_root = mentat_default_data_root()) {
  configured <- trimws(Sys.getenv("MENTAT_BULK_DIR", unset = ""))
  root <- if (nzchar(configured)) configured else file.path(data_root, "bulk")
  normalizePath(root, winslash = "/", mustWork = FALSE)
}

mentat_bootstrap_dirs <- function(data_root = mentat_default_data_root()) {
  paths <- mentat_paths(data_root)
  for (directory in c(paths$root, paths$reports)) {
    if (!dir.exists(directory) &&
        !dir.create(directory, recursive = TRUE, showWarnings = FALSE)) {
      stop("Could not create directory: ", directory, call. = FALSE)
    }
  }
  paths
}

mentat_project_root <- function(start = getwd()) {
  current <- normalizePath(start, winslash = "/", mustWork = TRUE)
  repeat {
    if (file.exists(file.path(current, "mentat.R")) &&
        dir.exists(file.path(current, "R"))) {
      return(current)
    }
    nested <- file.path(current, "mentat")
    if (file.exists(file.path(nested, "mentat.R")) &&
        dir.exists(file.path(nested, "R"))) {
      return(normalizePath(nested, winslash = "/", mustWork = TRUE))
    }
    parent <- dirname(current)
    if (identical(parent, current)) {
      stop("Could not locate the mentat project directory.", call. = FALSE)
    }
    current <- parent
  }
}

# Resolution order, most explicit first:
#   1. an explicit project_root argument - the caller knows best
#   2. MENTAT_INPUT_DIR - set by the orchestrator from its own script path
#   3. walking up from getwd()
#
# Step 2 exists because step 3 is fragile: it aborts whenever the process starts
# outside the project (a git worktree, a scheduled task, another drive), and this
# is reached from default arguments in several modules, so the abort surfaces far
# from its cause. Honouring the variable HERE fixes every call site at once -
# patching them individually missed `mentat_name_diminutive_register()` and killed
# six exports at once.
mentat_input_dir <- function(project_root = NULL) {
  if (!is.null(project_root) && nzchar(project_root)) {
    return(normalizePath(
      file.path(project_root, "inputs"),
      winslash = "/",
      mustWork = TRUE
    ))
  }
  configured <- trimws(Sys.getenv("MENTAT_INPUT_DIR", unset = ""))
  if (nzchar(configured) && dir.exists(configured)) {
    return(normalizePath(configured, winslash = "/", mustWork = TRUE))
  }
  normalizePath(
    file.path(mentat_project_root(), "inputs"),
    winslash = "/",
    mustWork = TRUE
  )
}

# Non-throwing variant. mentat_input_dir() walks up from getwd() with
# mustWork = TRUE, so it aborts whenever the process starts outside the project
# - a git worktree, a scheduled task, another drive. That is correct for ingest,
# which cannot proceed without inputs, but fatal on the REPORT path, which needs
# inputs only for the optional trade-association overrides. Callers that can
# degrade should use this and handle NA rather than crash the whole export.
# MENTAT_INPUT_DIR wins so an orchestrator can pin the directory explicitly.
mentat_input_dir_or_na <- function() {
  configured <- trimws(Sys.getenv("MENTAT_INPUT_DIR", unset = ""))
  if (nzchar(configured) && dir.exists(configured)) {
    return(normalizePath(configured, winslash = "/", mustWork = FALSE))
  }
  tryCatch(mentat_input_dir(), error = function(e) NA_character_)
}

db_connect <- function(data_root = mentat_default_data_root()) {
  require_package("DBI")
  require_package("RSQLite")
  paths <- mentat_bootstrap_dirs(data_root)
  connection <- DBI::dbConnect(RSQLite::SQLite(), dbname = paths$database)
  DBI::dbExecute(connection, "PRAGMA busy_timeout = 60000")
  ok <- FALSE
  on.exit(if (!ok) DBI::dbDisconnect(connection), add = TRUE)
  journal_mode <- DBI::dbGetQuery(connection, "PRAGMA journal_mode = WAL")[[1L]]
  if (!identical(tolower(as.character(journal_mode)), "wal")) {
    stop("SQLite refused WAL mode for: ", paths$database, call. = FALSE)
  }
  DBI::dbExecute(connection, "PRAGMA synchronous = NORMAL")
  DBI::dbExecute(connection, "PRAGMA foreign_keys = ON")
  ok <- TRUE
  connection
}

atomic_replace <- function(temporary, target) {
  if (!file.exists(temporary)) {
    stop("Atomic-write temporary file is missing: ", temporary, call. = FALSE)
  }
  target_dir <- dirname(target)
  if (!dir.exists(target_dir) &&
      !dir.create(target_dir, recursive = TRUE, showWarnings = FALSE)) {
    stop("Could not create output directory: ", target_dir, call. = FALSE)
  }
  backup <- NULL
  if (file.exists(target)) {
    backup <- tempfile(
      pattern = paste0(".", basename(target), ".backup-"),
      tmpdir = target_dir
    )
    if (!file.rename(target, backup)) {
      stop("Could not stage existing output: ", target, call. = FALSE)
    }
  }
  installed <- file.rename(temporary, target)
  if (!installed) {
    if (!is.null(backup) && file.exists(backup)) {
      file.rename(backup, target)
    }
    stop("Could not atomically install output: ", target, call. = FALSE)
  }
  if (!is.null(backup) && file.exists(backup)) {
    unlink(backup, force = TRUE)
  }
  invisible(target)
}

atomic_write_lines <- function(lines, target) {
  directory <- dirname(target)
  if (!dir.exists(directory) &&
      !dir.create(directory, recursive = TRUE, showWarnings = FALSE)) {
    stop("Could not create output directory: ", directory, call. = FALSE)
  }
  temporary <- tempfile(
    pattern = paste0(".", basename(target), "."),
    tmpdir = directory
  )
  on.exit(if (file.exists(temporary)) unlink(temporary, force = TRUE), add = TRUE)
  writeLines(enc2utf8(lines), temporary, useBytes = TRUE)
  atomic_replace(temporary, target)
}

atomic_write_csv <- function(data, target) {
  require_package("data.table")
  data <- as.data.frame(data, stringsAsFactors = FALSE)
  for (column in names(data)) {
    if (is.character(data[[column]])) {
      data[[column]] <- enc2utf8(data[[column]])
    }
  }
  directory <- dirname(target)
  if (!dir.exists(directory) &&
      !dir.create(directory, recursive = TRUE, showWarnings = FALSE)) {
    stop("Could not create output directory: ", directory, call. = FALSE)
  }
  temporary <- tempfile(
    pattern = paste0(".", basename(target), "."),
    tmpdir = directory
  )
  on.exit(if (file.exists(temporary)) unlink(temporary, force = TRUE), add = TRUE)
  data.table::fwrite(
    data,
    file = temporary,
    quote = TRUE,
    na = "",
    bom = TRUE
  )
  atomic_replace(temporary, target)
}

atomic_write_xlsx <- function(sheets, target, col_names = TRUE) {
  require_package("writexl")
  directory <- dirname(target)
  if (!dir.exists(directory) &&
      !dir.create(directory, recursive = TRUE, showWarnings = FALSE)) {
    stop("Could not create output directory: ", directory, call. = FALSE)
  }
  temporary <- tempfile(
    pattern = paste0(".", basename(target), "."),
    tmpdir = directory,
    fileext = ".xlsx"
  )
  on.exit(if (file.exists(temporary)) unlink(temporary, force = TRUE), add = TRUE)
  arguments <- list(x = sheets, path = temporary, col_names = col_names)
  # Header restoration expects inline strings. writexl 2.x now defaults small
  # workbooks to shared strings; explicitly retain the established XML format.
  if ("constant_memory" %in% names(formals(writexl::write_xlsx))) {
    arguments$constant_memory <- TRUE
  }
  do.call(writexl::write_xlsx, arguments)
  atomic_replace(temporary, target)
}

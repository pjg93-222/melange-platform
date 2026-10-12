# Fixed configuration of the Giving Forecast feeds (OpenSecrets and IBISWorld ingest; FEEDS_SPEC 7).
#
# Paths are resolved at call time so a test can redirect them with environment variables:
#   MENTAT_FEEDS_INBOX  Paul's drop folders (default <forecast root>\inbox)
#   FI_FRT              ingest runs, receipts, OpenSecrets cache (default <forecast root>\ingest)
#   RF_RUNTIME          the refit runtime folder; its raw\<sha256>\<name> vault is shared with the refit
#   RF_GLASSBOX_DIR     Glass Box event folder
#   MENTAT_DATA_ROOT    data root honoured like the refit (<root>\forecast\...)
#   MENTAT_OS_CONTACT   optional contact string appended to the OpenSecrets User-Agent
# <forecast root> is %LOCALAPPDATA%\Mentat\forecast. Everything the ingest writes is under FRT, the vault or the Glass
# Box folder, never inside the git tree, and inbox files are never modified.

FI <- list(
  VERSION = "ingest-0.1.0",
  SOURCE = "giving-forecast-ingest",
  GLASSBOX_LANE = "claude",
  PDF_ENGINE_PIN = "3.9.0",                 # pdftools; the xpdf-style text engine is forbidden (FEEDS_SPEC 5.4)
  PACKAGES = c("pdftools", "xml2", "data.table", "jsonlite", "readxl"),
  ALIGN_TOL_PP = 0.11,                      # printed Annual Change versus the recomputed growth (extract_archived_forecasts.py)
  MONTHS = c("january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
             "november", "december"),
  MODULES = c("fi_config.R", "fi_io.R", "fi_glassbox.R", "fi_inbox.R", "fi_ibis_pdf.R", "fi_ibis_xlsx.R", "fi_ibis_derive.R",
              "fi_os_html.R", "fi_os_bulk.R", "fi_os_calibrate.R", "fi_os_fetch.R", "fi_manifest.R", "fi_receipt.R", "fi_run.R")
)

fi_env_path <- function(name, default) {
  v <- trimws(Sys.getenv(name, unset = ""))
  if (nzchar(v)) v else default
}

# The ingest folder (the one holding R/, registry/, tests/); set once by run_ingest.R or the test helper.
fi_home <- function() {
  h <- get0(".FI_HOME", envir = globalenv(), inherits = FALSE)
  if (is.null(h)) stop("fi_home(): set .FI_HOME (the ingest folder) before use", call. = FALSE)
  h
}

# The refit folder next to it (the ingest reuses its io, vault and Glass Box emitter; it never edits it).
fi_refit_home <- function() normalizePath(fi_env_path("FI_REFIT_HOME", file.path(dirname(fi_home()), "refit")), winslash = "/", mustWork = FALSE)

fi_forecast_root <- function() {
  data_root <- trimws(Sys.getenv("MENTAT_DATA_ROOT", unset = ""))
  if (nzchar(data_root)) return(file.path(data_root, "forecast"))
  lad <- trimws(Sys.getenv("LOCALAPPDATA", unset = ""))
  if (nzchar(lad)) return(file.path(lad, "Mentat", "forecast"))
  home <- trimws(Sys.getenv("HOME", unset = ""))
  if (nzchar(home)) return(file.path(home, ".local", "share", "Mentat", "forecast"))
  stop("LOCALAPPDATA and HOME are empty; set FI_FRT and MENTAT_FEEDS_INBOX to writable local directories.", call. = FALSE)
}

fi_inbox_dir <- function() normalizePath(fi_env_path("MENTAT_FEEDS_INBOX", file.path(fi_forecast_root(), "inbox")), winslash = "/", mustWork = FALSE)
fi_frt_dir <- function() normalizePath(fi_env_path("FI_FRT", file.path(fi_forecast_root(), "ingest")), winslash = "/", mustWork = FALSE)
fi_registry_path <- function() file.path(fi_home(), "registry", "feeds.json")

# Load the refit modules the ingest shares (config, io, vault, Glass Box emitter), then the ingest modules.
# .RF_HOME is pointed at the refit folder because rf_config.R resolves its registry paths through it.
fi_source_all <- function(home = fi_home()) {
  assign(".FI_HOME", home, envir = globalenv())
  rh <- fi_refit_home()
  if (!dir.exists(file.path(rh, "R"))) stop("fi_source_all: refit folder not found at ", rh, call. = FALSE)
  assign(".RF_HOME", rh, envir = globalenv())
  for (m in c("rf_config.R", "rf_io.R", "rf_inputs.R", "rf_glassbox.R", "rf_opensecrets.R")) source(file.path(rh, "R", m), encoding = "UTF-8", local = FALSE)
  for (m in FI$MODULES) {
    p <- file.path(home, "R", m)
    if (file.exists(p)) source(p, encoding = "UTF-8", local = FALSE)
  }
  invisible(TRUE)
}

fi_read_feeds <- function(path = fi_registry_path()) {
  f <- jsonlite::fromJSON(path, simplifyVector = FALSE)
  f$industries <- f$industries %||% list()
  f
}

# Names of the OpenSecrets codes of every registered industry -> industry id.
fi_code_map <- function(feeds) {
  ids <- names(feeds$industries)
  codes <- lapply(ids, function(id) unlist(feeds$industries[[id]]$opensecrets$codes))
  stats::setNames(rep(ids, lengths(codes)), unlist(codes))
}

# Package versions for the receipt (the DESCRIPTION text, as the refit records them) plus the poppler build.
fi_pkg_versions <- function(pkgs = FI$PACKAGES) {
  out <- lapply(stats::setNames(pkgs, pkgs), function(p) {
    if (requireNamespace(p, quietly = TRUE)) utils::packageDescription(p)$Version else NA_character_
  })
  if (requireNamespace("pdftools", quietly = TRUE)) out$poppler <- tryCatch(pdftools::poppler_config()$version, error = function(e) NA_character_)
  out
}

# The text engine must be the pinned pdftools; any other version fails fast (FEEDS_SPEC 5.4: row alignment depends on it).
fi_assert_pdf_engine <- function() {
  if (!requireNamespace("pdftools", quietly = TRUE)) stop("fi_assert_pdf_engine: package pdftools is unavailable", call. = FALSE)
  v <- utils::packageDescription("pdftools")$Version
  if (!identical(v, FI$PDF_ENGINE_PIN)) stop("fi_assert_pdf_engine: pdftools is ", v, " but ", FI$PDF_ENGINE_PIN, " is pinned", call. = FALSE)
  invisible(v)
}

# A run id: UTC timestamp plus a short random suffix (sortable and collision-free across parallel runs).
fi_new_run_id <- function(feed) {
  paste0(format(Sys.time(), "%Y%m%dT%H%M%SZ", tz = "UTC"), "-", gsub("[^a-z0-9]+", "-", tolower(feed)), "-",
         paste(sample(c(0:9, letters[1:6]), 6, TRUE), collapse = ""))
}

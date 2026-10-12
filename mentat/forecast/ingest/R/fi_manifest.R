# Proposed manifest and registry changes (FEEDS_SPEC 7.5-5). The ingest NEVER edits registry\*.json or inputs.json (of the refit
# or of itself): it writes proposed_inputs.json and proposed_registry_patch.json next to its receipt and the owner applies them
# (APPLY = Paul; the refit's gate G1 keeps pinned hashes).

# One proposed inputs.json entry. `pinned` is TRUE for raw files (their sha256 must not change) and FALSE for derived feeds.
.fi_manifest_entry <- function(role, path, purpose, pinned, vintage = "", inputs_sha256 = NULL, code_sha256 = NULL) {
  e <- list(role = role, path = path, sha256 = rf_sha256(path), bytes = as.integer(file.size(path)), pinned = pinned, purpose = purpose,
            vintage = vintage, supplied_by = "giving-forecast-ingest")
  if (!is.null(inputs_sha256)) e$inputs_sha256 <- as.list(inputs_sha256)
  if (!is.null(code_sha256)) e$code_sha256 <- code_sha256
  e
}

# Entries for the IBISWorld feed: each vaulted PDF / xlsx that fed an edition, and the four derived CSVs as feed:<name>.
fi_manifest_ibis <- function(editions, outputs, code_sha) {
  used <- editions[editions$status %in% c("checked", "review_required") & !is.na(editions$vault_path) & !is.na(editions$code), , drop = FALSE]
  raw <- lapply(seq_len(nrow(used)), function(i) {
    ext <- if (identical(used$layout[i], "xlsx")) "ibis_xlsx" else "ibis_pdf"
    .fi_manifest_entry(sprintf("%s:%s:%s", ext, used$code[i], used$edition_month[i]), used$vault_path[i], "raw", TRUE, used$edition_month[i])
  })
  in_sha <- used$source_sha256
  feeds <- lapply(names(outputs), function(nm) .fi_manifest_entry(paste0("feed:", nm), outputs[[nm]], "derived", FALSE, inputs_sha256 = in_sha, code_sha256 = code_sha))
  c(raw, feeds)
}

# Entries for OpenSecrets envelopes written under outputs/os (roles of SPEC 3.5).
#   run_dir: when given, each entry also carries path_run_relative (the path inside the run folder), because a run folder can be moved
#   (for example pulled back from a compute pod) and the absolute path then names the machine that ran.
fi_manifest_os <- function(envelope_files, industry_id = NULL, code_sha = NULL, run_dir = NULL) {
  lapply(names(envelope_files), function(role) {
    # the vintage of a fetched or saved page is its retrieval time, read from the envelope
    vint <- tryCatch(jsonlite::fromJSON(envelope_files[[role]], simplifyVector = FALSE)$retrieved_utc %||% "", error = function(e) "")
    e <- .fi_manifest_entry(role, envelope_files[[role]], "raw", TRUE, vintage = if (is.character(vint) && length(vint) == 1L) vint else "", code_sha256 = NULL)
    if (!is.null(run_dir)) {
      rd <- normalizePath(run_dir, winslash = "/", mustWork = FALSE); p <- normalizePath(envelope_files[[role]], winslash = "/", mustWork = FALSE)
      if (startsWith(p, paste0(rd, "/"))) e$path_run_relative <- substring(p, nchar(rd) + 2L)
    }
    e
  })
}

fi_write_proposals <- function(run_dir, entries, registry_patch, note = "") {
  ip <- file.path(run_dir, "proposed_inputs.json")
  rp <- file.path(run_dir, "proposed_registry_patch.json")
  rf_write_json_atomic(list(schema_version = 1L, note = paste("PROPOSED entries for the refit's registry/inputs.json; the ingest never applies them.", note), inputs = entries), ip)
  rf_write_json_atomic(list(schema_version = 1L, note = paste("PROPOSED patch for the refit's registry/industries.json and the ingest's registry/feeds.json; never applied automatically.", note),
                            patch = registry_patch), rp)
  list(inputs = ip, registry_patch = rp)
}

# ---- reading a feed back (FEEDS_SPEC 7.5-4) ------------------------------------------------------------------------------------------------

# Columns that are numbers in the derived CSVs (fi_read_csv returns everything as text).
FI_NUM_COLS <- c("year", "predicted_growth_pct", "collected_forecast_growth_pct", "forecast_baseline_year", "forecast_target_year", "forecast_baseline_revenue_million",
                 "forecast_target_revenue_million", "forecast_source_pages", "forecast_horizon_years", "forecast_age_months", "revenue_million_constant_2026_usd",
                 "growth_same_year_pct", "growth_previous_year_pct", "revenue_million_report_price_basis", "printed_revenue_change_pct", "source_page",
                 "revenue_million", "printed_change_pct", "pdf_page", "price_base_year")

# The refit's only way to read a derived feed: fi_load_feed("ibis_predicted_growth"). The accepted output of that role (current.json) is
# checked against the receipt of the run that wrote it:
#   1. the output file's sha256 equals the recorded one;
#   2. every raw input the run used is still in the vault with its recorded sha256;
#   3. the ingest code hash of the run equals the current code.
# -> list(ok, action, data, path, sha256, run_id, checks, reason). action is "use", "rederive" (an IBISWorld feed whose only failure is the
#    code hash: re-deriving is cheap) or "rerun_ingest" (anything else, including every bulk feed).
fi_load_feed <- function(name, frt = fi_frt_dir(), code_sha = fi_code_sha256()) {
  name <- sub("^feed:", "", name)
  fail <- function(reason, action = "rerun_ingest", checks = NULL) list(ok = FALSE, action = action, data = NULL, path = NA_character_, sha256 = NA_character_, run_id = NA_character_, checks = checks, reason = reason)
  cp <- file.path(frt, "current.json")
  if (!file.exists(cp)) return(fail("no current.json: nothing has been accepted yet"))
  e <- jsonlite::fromJSON(cp, simplifyVector = FALSE)[[name]]
  if (is.null(e)) return(fail(paste0("no accepted output for role ", name)))
  rcp <- file.path(frt, "runs", e$run_id, "receipt.json")
  if (!file.exists(rcp)) return(fail(paste0("receipt of run ", e$run_id, " is missing")))
  rc <- jsonlite::fromJSON(rcp, simplifyVector = FALSE)
  out_ok <- file.exists(e$path) && identical(rf_sha256(e$path), e$sha256)
  ins <- Filter(function(z) !is.null(z$vault_path) && z$status %in% c("new", "seen_before", "fetched", "cache_hit"), rc$inputs)
  bad_in <- vapply(ins, function(z) !(file.exists(z$vault_path) && identical(rf_sha256(z$vault_path), z$sha256)), NA)
  rec_code <- fi_sha256_raw(paste(vapply(rc$code, function(z) paste(z$file, z$sha256), ""), collapse = "\n"))
  code_ok <- identical(rec_code, code_sha)
  checks <- rbind(rf_check("output sha256 equals the receipt", 1L, as.integer(!out_ok)),
                  rf_check("raw inputs present in the vault with their sha256", length(ins), sum(bad_in)),
                  rf_check("ingest code hash equals the current code", 1L, as.integer(!code_ok)))
  ok <- out_ok && !any(bad_in) && code_ok
  if (!ok) {
    action <- if (out_ok && !any(bad_in) && grepl("^ibis_", name)) "rederive" else "rerun_ingest"
    why <- c(if (!out_ok) "output changed or missing", if (any(bad_in)) "a raw input is missing from the vault or changed", if (!code_ok) "ingest code changed since the run")
    return(list(ok = FALSE, action = action, data = NULL, path = e$path, sha256 = e$sha256, run_id = e$run_id, checks = checks, reason = paste(why, collapse = "; ")))
  }
  d <- fi_read_csv(e$path)
  for (cn in intersect(names(d), FI_NUM_COLS)) d[[cn]] <- fi_num(d[[cn]])
  list(ok = TRUE, action = "use", data = d, path = e$path, sha256 = e$sha256, run_id = e$run_id, checks = checks, reason = "")
}

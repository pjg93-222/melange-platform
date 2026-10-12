# Run receipts (FEEDS_SPEC 7.6). FRT\runs\<run_id>\receipt.json is written LAST and atomically; outputs are hashed from the bytes
# on disk at the moment they are added, so a receipt can never describe a file other than the one it names.

FI_RECEIPT_VERSION <- 1L

# Files whose hashes identify the ingest code of a run: every R module, the CLI and the feeds registry.
fi_code_files <- function(home = fi_home()) {
  f <- c(list.files(file.path(home, "R"), pattern = "\\.R$", full.names = TRUE), file.path(home, "run_ingest.R"), file.path(home, "registry", "feeds.json"))
  f[file.exists(f)]
}

fi_code_hashes <- function(home = fi_home()) {
  f <- fi_code_files(home)
  lapply(f, function(p) list(file = substring(p, nchar(home) + 2L), sha256 = rf_sha256(p)))
}

# The combined code hash used by derived feeds (7.5-4): sha256 of the per-file hashes in path order.
fi_code_sha256 <- function(home = fi_home()) {
  h <- fi_code_hashes(home)
  fi_sha256_raw(paste(vapply(h, function(z) paste(z$file, z$sha256), ""), collapse = "\n"))
}

# A receipt under construction (an environment so helpers can append).
fi_receipt_new <- function(run_id, feed, mode = "run", industry = "", inbox = "") {
  r <- new.env(parent = emptyenv())
  r$run_id <- run_id; r$feed <- feed; r$mode <- mode; r$industry <- industry; r$inbox <- inbox
  r$started_utc <- rf_now_utc()
  r$inputs <- list(); r$outputs <- list(); r$checks <- list(); r$decisions <- character(); r$issues <- character()
  r$network <- list(); r$extra <- list(); r$status <- "running"; r$feed_state <- NA_character_
  r
}

fi_receipt_input <- function(r, row, identified = NULL) {
  r$inputs[[length(r$inputs) + 1L]] <- list(inbox_name = row$inbox_name, sha256 = row$sha256, bytes = row$bytes, first_seen_utc = row$first_seen_utc,
                                            vault_path = if (is.na(row$vault_path)) NULL else row$vault_path, kind = row$kind, status = row$status,
                                            identified = identified)
  invisible(r)
}

fi_receipt_output <- function(r, path, rows = NA_integer_, role = NULL) {
  r$outputs[[length(r$outputs) + 1L]] <- list(path = path, sha256 = rf_sha256(path), rows = if (is.na(rows)) NULL else as.integer(rows), role = role)
  invisible(r)
}

fi_receipt_check <- function(r, name, examined, failures, detail = "") {
  r$checks[[length(r$checks) + 1L]] <- list(name = name, examined = as.integer(examined), failures = as.integer(failures), detail = detail)
  if (failures > 0L) r$issues <- c(r$issues, sprintf("check failed: %s (%d of %d)%s", name, failures, examined, if (nzchar(detail)) paste0(" - ", detail) else ""))
  invisible(r)
}

fi_receipt_checks_df <- function(r, df) {
  if (is.data.frame(df) && nrow(df)) for (i in seq_len(nrow(df))) fi_receipt_check(r, df$name[i], df$rows_examined[i], df$failures[i], df$detail[i])
  invisible(r)
}

fi_receipt_decision <- function(r, text) { r$decisions <- c(r$decisions, text); invisible(r) }
fi_receipt_issue <- function(r, text) { r$issues <- c(r$issues, text); invisible(r) }
fi_receipt_network <- function(r, entries) { r$network <- c(r$network, entries); invisible(r) }

fi_receipt_list <- function(r, glassbox_ids = character()) {
  list(receipt_version = FI_RECEIPT_VERSION, ingest_version = FI$VERSION, run_id = r$run_id, feed = r$feed, mode = r$mode, industry = r$industry,
       status = r$status, feed_state = r$feed_state, started_utc = r$started_utc, finished_utc = rf_now_utc(), inbox = r$inbox,
       code = fi_code_hashes(), packages = c(fi_pkg_versions(), fi_fetch_pkg_versions()), inputs = r$inputs, outputs = r$outputs, checks = r$checks,
       decisions = as.list(r$decisions), issues = as.list(unique(r$issues)), network = r$network, extra = r$extra,
       glassbox_event_ids = as.list(glassbox_ids))
}

# Write receipt.json into the run folder (last write of a run). Returns list(path, sha256).
fi_receipt_write <- function(r, run_dir, glassbox_ids = character()) {
  dir.create(run_dir, recursive = TRUE, showWarnings = FALSE)
  path <- file.path(run_dir, "receipt.json")
  sha <- rf_write_json_atomic(fi_receipt_list(r, glassbox_ids), path)
  list(path = path, sha256 = sha)
}

# current.json: role -> latest accepted output {path, sha256, run_id}.
fi_current_update <- function(role, path, run_id, frt = fi_frt_dir()) {
  cp <- file.path(frt, "current.json")
  cur <- if (file.exists(cp)) jsonlite::fromJSON(cp, simplifyVector = FALSE) else list()
  cur[[role]] <- list(path = path, sha256 = rf_sha256(path), run_id = run_id)
  rf_write_json_atomic(cur, cp)
}

# The v1-shaped bundle projection and the run receipt. Phase 1 writes ONLY the projection of the frozen v1 configuration, for
# parity with mentat/app/data/giving-models-v1.json (never published; no v2 schema, no DL blend, no anchor change yet).

# One data-frame row as a bundle record: NA / NaN become null, integers stay integers.
rf_record <- function(row, ints = character()) {
  out <- list()
  for (k in names(row)) {
    v <- row[[k]]
    if (length(v) != 1L || is.na(v)) { out[k] <- list(NULL); next }
    if (is.factor(v)) v <- as.character(v)
    if (k %in% ints) v <- as.integer(v)
    out[[k]] <- v
  }
  out
}

rf_records <- function(df, ints = character()) lapply(seq_len(nrow(df)), function(i) rf_record(df[i, , drop = FALSE], ints))

# The 12 validation_metrics rows of the bundle: pooled RShare cases, comparisons in the order of the Codex CSV.
rf_v1_validation_rows <- function(ctx) {
  v <- ctx$stepwise$validation
  v <- v[grepl("__pooled__RShare", v$case), ]
  v <- v[order(v$case, match(v$comparison, c("forced_only", "previous_cycle", "original_full"))), ]
  rf_records(v, ints = c("rows", "election_targets"))
}

# saved_outlook of the v1 bundle: workbook 02 sheet current_2026 (Kalshi / Polymarket snapshot of Sept. 24/25 and the Sept.
# Crystal Ball seat snapshots), both chambers.
rf_v1_saved_outlook <- function(ctx) {
  o <- rf_read_xlsx(rf_input_path(ctx$inputs, "election_workbook"), "current_2026")
  out <- list(cycle = 2026L, available_on = "2026-09-25",
              control = list(description = "Saved Sept. 24/25, 2026 Kalshi/Polymarket normalized average; cross-provider sensitivity, not IEM or a live feed"),
              seats = list(description = "Saved Sept. 18 House / Sept. 22 Senate, 2026 ratings-based expected R seat changes; not a live feed"))
  for (ch in c("House", "Senate")) {
    for (sg in c("control", "seats")) {
      tbl <- if (sg == "control") "current_2026_control_probabilities.csv" else "current_2026_seat_predictions.csv"
      fld <- if (sg == "control") "p_rep_control" else "pred_rep_net_change"
      row <- o[o$table == tbl & o$chamber == ch, ][1, ]
      out[[sg]][[ch]] <- list(value = as.numeric(row[[fld]]) * (if (sg == "control") 100 else 1), current_control = 1L, as_of = row$as_of,
                              source = if (sg == "control") row$sources else row$source, notes = row$notes)
    }
  }
  out
}

# The v1 `industries` block from the rebuilt PAC panel.
rf_v1_industries <- function(ctx) {
  p <- ctx$pac_panel
  out <- list()
  for (sec in names(RF$SECTOR_LABELS)) {
    g <- p[p$sector == sec, ]
    g <- g[order(g$cycle_end), ]
    code <- g$opensecrets_code[1]
    out[[sec]] <- list(id = sec, label = unname(RF$SECTOR_LABELS[sec]), code = code,
      growth_mean = mean(g$cycle_revenue_growth_pct), growth_sd = stats::sd(g$cycle_revenue_growth_pct),
      growth_min = min(g$cycle_revenue_growth_pct), growth_max = max(g$cycle_revenue_growth_pct),
      history = lapply(seq_len(nrow(g)), function(i) list(cycle = as.integer(g$cycle_end[i]), growth_pct = g$cycle_revenue_growth_pct[i],
        prior_growth_pct = g$prior_cycle_revenue_growth_pct[i], pac_real_2025 = g$pac_real_2025[i], individual_real_2025 = g$individual_real_2025[i],
        total_real_2025 = g$total_real_2025[i], source_url = g$source_url[i])),
      scope = sprintf("OpenSecrets %s whole-industry accounting; separate from company/subindustry report scopes", code))
  }
  out
}

# The v1 `sources` array: the nine files the v1 builder hashed (Codex design files pinned as parity references, plus workbook 02).
rf_v1_sources <- function(ctx) {
  role_of <- function(f) if (f == "02_election_forecasting.xlsx") "election_workbook" else paste0("parity_ref:", f)
  lapply(RF$V1_SOURCE_FILES, function(f) list(file = f, sha256 = rf_sha256(rf_input_path(ctx$inputs, role_of(f)))))
}

# giving-models-v1.json rebuilt from raw inputs. Compared leaf by leaf with the committed file by test-10-v1-projection.R.
rf_bundle_v1_projection <- function(ctx) {
  m <- rf_v1_models(ctx)
  slopes <- ctx$stepwise$industry_slopes
  list(schema_version = 1L, model_version = RF$V1_MODEL_VERSION, built_on = RF$V1_TEXT$built_on, currency = RF$V1_TEXT$currency,
       inference = RF$V1_TEXT$inference, validation = RF$V1_TEXT$validation,
       industries = rf_v1_industries(ctx), share_models = m$share_models,
       evidence = rf_records(ctx$stepwise$effects),
       pac_model = m$pac_model, lobby_model = m$lobby_model,
       industry_slopes = rf_records(slopes), validation_metrics = rf_v1_validation_rows(ctx),
       saved_outlook = rf_v1_saved_outlook(ctx), sources = rf_v1_sources(ctx))
}

# Run receipt: what was read (every input record), which code produced the result, the package versions, the parity outcome and
# the bundle hash. Written as JSON beside the run's outputs.
rf_receipt <- function(run, bundle_path = NULL) {
  code_files <- list.files(file.path(rf_home(), "R"), pattern = "\\.R$", full.names = TRUE)
  code <- lapply(code_files, function(f) list(file = basename(f), sha256 = rf_sha256(f)))
  inp <- run$inputs
  list(run_id = run$run_id, mode = run$mode, pipeline = RF$PIPELINE, version = RF$VERSION,
       started_utc = run$started_utc, finished_utc = rf_now_utc(),
       inputs = lapply(seq_len(nrow(inp)), function(i) list(role = inp$role[i], path = inp$path[i], sha256 = inp$actual[i], status = inp$status[i], purpose = inp$purpose[i])),
       code = code, code_sha256 = rf_sha256_of_strings(vapply(code, function(x) x$sha256, "")),
       R_version = R.version.string, platform = rf_platform_info(), packages = lapply(seq_len(nrow(run$packages)), function(i) list(package = run$packages$package[i], version = run$packages$version[i], lib = run$packages$lib[i])),
       gates = lapply(seq_len(nrow(run$gates)), function(i) list(gate = run$gates$gate[i], status = run$gates$status[i], detail = run$gates$detail[i])),
       parity = run$parity, bundle_path = bundle_path, bundle_sha256 = if (!is.null(bundle_path) && file.exists(bundle_path)) rf_sha256(bundle_path) else NULL,
       decision = run$decision, published_to = list())
}

# sha256 of a character vector (hash of the sorted hashes): tools::sha256sum needs a file, so write a temp file.
rf_sha256_of_strings <- function(x) {
  f <- tempfile(fileext = ".txt")
  on.exit(unlink(f))
  writeBin(charToRaw(paste(sort(x), collapse = "\n")), f)
  rf_sha256(f)
}

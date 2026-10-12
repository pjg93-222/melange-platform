# The refit agent (Phase 5): a read-only watcher of Mentat's export receipts, a queue for industries the bundle does not know, the refit
# run (gates G0-G9, review packet, receipt, publish or hold), and a clean "waiting for data" outcome for an industry whose inputs have
# not been supplied yet (for example property and casualty insurance, OpenSecrets F3400 pending). Every step emits a Glass Box event.
# The agent never commits, never edits Mentat, and reads the report directory and the audit manifests read-only.
#
#   Rscript run_refit.R --mode detect                     scan the report directory, queue new industries, write DATA_REQUEST.md
#   Rscript run_refit.R --mode refit [--industry <id>] [--publish auto|never] [--run-id <id>]
#   Rscript run_refit.R --rollback <model_version>

# ---- the watcher ---------------------------------------------------------------------------------------------------------------------

# The report directory named by mentat/config/report-directory.json (override: RF_REPORT_DIR).
rf_report_dir <- function() {
  v <- trimws(Sys.getenv("RF_REPORT_DIR", unset = ""))
  if (nzchar(v)) return(v)
  cfg <- file.path(dirname(dirname(rf_home())), "config", "report-directory.json")
  if (!file.exists(cfg)) return(NA_character_)
  jsonlite::fromJSON(cfg, simplifyVector = FALSE)$directory %||% NA_character_
}

rf_agent_state_dir <- function() file.path(rf_runtime(), "agent")
rf_queue_dir <- function() file.path(rf_agent_state_dir(), "queue")

rf_slug <- function(x) gsub("^_|_$", "", gsub("[^a-z0-9]+", "_", tolower(x)))

# Workbooks "<Industry> Industry Spending Overview.xlsx" of a report directory (lock files and the Rust FEC snapshot are not industries).
rf_list_workbooks <- function(report_dir) {
  if (is.na(report_dir) || !dir.exists(report_dir)) return(character())
  f <- list.files(report_dir, pattern = " Industry Spending Overview\\.xlsx$", full.names = FALSE)
  f[!startsWith(f, "~$") & !grepl("Rust FEC Verified", f, fixed = TRUE)]
}

# Export receipt of a workbook: <name>.audit/audit-manifest.json (workbook_sha256, payload_sha256), or NULL.
rf_export_receipt <- function(report_dir, workbook) {
  p <- file.path(report_dir, sub("\\.xlsx$", ".audit", workbook), "audit-manifest.json")
  if (!file.exists(p)) return(NULL)
  m <- tryCatch(jsonlite::fromJSON(p, simplifyVector = FALSE), error = function(e) NULL)
  if (is.null(m)) return(NULL)
  list(path = p, workbook_sha256 = m$workbook_sha256, payload_sha256 = m$payload_sha256)
}

# Scan the report directory (read-only) against the registry and the last state seen.
# -> data.frame(workbook, industry, slug, status, registry_id, sha256, receipt, changed): status in known | declined | new
rf_agent_detect <- function(reg, report_dir = rf_report_dir(), state_file = file.path(rf_agent_state_dir(), "seen.json")) {
  wbs <- rf_list_workbooks(report_dir)
  by_wb <- stats::setNames(names(reg$industries), vapply(reg$industries, function(i) i$mentat_workbook %||% NA_character_, ""))
  seen <- if (file.exists(state_file)) jsonlite::fromJSON(state_file, simplifyVector = FALSE) else list()
  rows <- lapply(wbs, function(w) {
    rc <- rf_export_receipt(report_dir, w)
    sha <- rc$workbook_sha256 %||% rf_sha256(file.path(report_dir, w))             # read-only hash of the published workbook
    id <- if (w %in% names(by_wb)) by_wb[[w]] else NA_character_
    st <- if (is.na(id)) "new" else if (identical(reg$industries[[id]]$status, "declined")) "declined" else "known"
    industry <- sub(" Industry Spending Overview\\.xlsx$", "", w)
    data.frame(workbook = w, industry = industry, slug = rf_slug(industry), status = st, registry_id = id, sha256 = sha, receipt = !is.null(rc),
               changed = !identical(seen[[w]], sha), stringsAsFactors = FALSE)
  })
  if (!length(rows)) return(data.frame(workbook = character(), industry = character(), slug = character(), status = character(), registry_id = character(), sha256 = character(),
                                       receipt = logical(), changed = logical(), stringsAsFactors = FALSE))
  do.call(rbind, rows)
}

# Remember what was seen (the watcher's only write besides the queue).
rf_agent_remember <- function(det, state_file = file.path(rf_agent_state_dir(), "seen.json")) {
  seen <- stats::setNames(as.list(det$sha256), det$workbook)
  rf_write_json_atomic(seen, state_file)
}

# Queue one refit request per new industry (idempotent: an existing request is left as it is). -> data.frame(slug, file, created)
rf_agent_queue <- function(det, dir = rf_queue_dir()) {
  new <- det[det$status == "new", ]
  out <- list()
  for (i in seq_len(nrow(new))) {
    f <- file.path(dir, paste0(new$slug[i], ".json"))
    created <- !file.exists(f)
    if (created) rf_write_json_atomic(list(id = new$slug[i], industry = new$industry[i], workbook = new$workbook[i], workbook_sha256 = new$sha256[i], detected_utc = rf_now_utc(),
                                           status = "queued", note = "registry mapping (OpenSecrets code, IBISWorld code, LobbyView NAICS) must be supplied by Paul"), f)
    out[[length(out) + 1L]] <- data.frame(slug = new$slug[i], file = f, created = created, stringsAsFactors = FALSE)
  }
  if (length(out)) do.call(rbind, out) else data.frame(slug = character(), file = character(), created = logical())
}

# DATA_REQUEST.md text for an industry: the files the refit needs (SPEC section 2, adapted).
rf_data_request <- function(industry, status = NULL) {
  c(sprintf("# Data request: %s", industry), "",
    "The refit agent found this industry in Mentat's report directory but it is not (fully) in the Giving Forecast registry.", "",
    "The mapping fields are Paul's to supply; they cannot be inferred safely:", "",
    "1. OpenSecrets category code and its totals page (nine-column Totals table, 1990-2024 ideally; 2008-2024 at minimum), captured with the existing collector pattern.",
    "2. OpenSecrets candidate recipients by chamber for cycles 2012-2024 (House and Senate) and the presidential recipient list per cycle (21 captures).",
    "3. IBISWorld Key Statistics (PDF) for the industry's code, a vintage published in 2025 or later covering revenue 2006-2024 in one vintage, so workbook 07 extends without splicing vintages.",
    "4. Optional (extends lobbying): IBISWorld outlook pages with the 2024 and 2026 targets and their price basis; a LobbyView export newer than 2024-10-25.", "",
    if (!is.null(status) && length(status$missing)) c("Missing today:", "", sprintf("- %s: %s", vapply(status$missing, function(m) m$item, ""), vapply(status$missing, function(m) m$detail, "")), ""),
    "A registry stub with every mapping field null is written beside this file; the refit runs once the stub is completed and its inputs appear in the manifest.")
}

# ---- data readiness ----------------------------------------------------------------------------------------------------------------------

# What an industry still needs before its PAC / control / lobbying models can be fitted. The agent reports this cleanly (WAITING_FOR_DATA), it never fails on it.
# -> list(id, ready, missing = list(list(item, detail)), models = list(pac, control, lobbying))
rf_agent_data_status <- function(reg, id, inputs = NULL) {
  i <- reg$industries[[id]]
  miss <- list()
  add <- function(item, detail) miss[[length(miss) + 1L]] <<- list(item = item, detail = detail)
  os <- i$opensecrets
  if (is.null(os$totals)) add("OpenSecrets totals", os$reason %||% sprintf("OpenSecrets %s capture not supplied", paste(unlist(os$codes), collapse = "/")))
  else if (!file.exists(rf_expand(os$totals))) add("OpenSecrets totals", sprintf("capture file missing: %s", os$totals))
  if (isTRUE(i$models$control) && (is.null(os$recipients) || is.null(os$presidential))) add("OpenSecrets recipients and presidential lists", "21 captures (cycles 2012-2024 x House, Senate, presidential) not supplied")     # only the control (share) model reads them
  ib <- i$ibisworld
  if (is.null(ib$actual_sector)) add("IBISWorld actual revenue", "no workbook-07 sector mapped")
  else if (!is.null(inputs) && "ibis_actual" %in% inputs$role) {
    g <- tryCatch(rf_cycle_growth(rf_input_path(inputs, "ibis_actual"), sectors = ib$actual_sector, supplement = rf_growth_supplement(inputs, id), components = rf_actual_components(reg, id)), error = function(e) NULL)
    ok <- if (is.null(g)) NULL else g[!is.na(g$cycle_revenue_growth_pct) & !is.na(g$prior_cycle_revenue_growth_pct), ]
    if (is.null(ok) || !nrow(ok)) add("IBISWorld actual revenue", sprintf("no cycle growth for sector '%s' in workbook 07", ib$actual_sector))
    else if (!(2024 %in% ok$cycle_end)) add("IBISWorld actual revenue", sprintf("revenue ends before the 2024 cycle (latest cycle with growth: %d); a 2025+ vintage covering 2006-2024 is needed (%s)", max(ok$cycle_end), ib$vintage_note %||% ""))
  }
  list(id = id, ready = length(miss) == 0L, missing = miss, models = list(pac = is.na(rf_withheld_reason(i, "pac")), control = is.na(rf_withheld_reason(i, "control")), lobbying = is.na(rf_withheld_reason(i, "lobbying"))))
}

# ---- the review packet -----------------------------------------------------------------------------------------------------------------

.rf_md_table <- function(df, digits = 4) {
  if (!nrow(df)) return("(none)")
  fmt <- function(x) if (is.numeric(x)) ifelse(is.na(x), "", formatC(x, digits = digits, format = "g")) else ifelse(is.na(x), "", as.character(x))
  c(paste0("| ", paste(names(df), collapse = " | "), " |"), paste0("|", paste(rep("---", ncol(df)), collapse = "|"), "|"),
    vapply(seq_len(nrow(df)), function(i) paste0("| ", paste(vapply(df[i, ], fmt, ""), collapse = " | "), " |"), ""))
}

# Write the review packet into <dir>/review/. `res` = the refit result (v, bundle, gates, published, status...). Returns the written file names.
rf_review_packet <- function(res, dir, b_random = RF$B_RANDOM) {
  rd <- file.path(dir, "review"); dir.create(rd, recursive = TRUE, showWarnings = FALSE)
  v <- res$v; b <- res$bundle; gates <- res$gates$gates
  files <- character()
  put <- function(name, lines) { rf_write_lines(lines, file.path(rd, name)); files <<- c(files, name) }
  csv <- function(name, d) { rf_write_csv(d, file.path(rd, name)); files <<- c(files, name) }
  # regressions: real summary(lm()) blocks and the labelled robustness blocks, including the lobbying log refit
  txt <- rf_regressions_report(v$ctx, b_random = b_random, v1 = res$published)
  lg <- c("", strrep("=", 110), "MODEL lobby_log  (lobby_model.log_refit: dly ~ dg + factor(year); the relative lobbying model the calculator applies)", strrep("=", 110),
          rf_summary_text(v$lobby_log_fit), "", rf_robustness_block("lobby_log", v$lobby_log_fit, rf_lobby_log_panel(v$ctx$lobby$diffs), "dg", c("study_sector", "year"), NULL, b_random = b_random)$text)
  put("regressions.txt", c(txt, lg))
  csv("backtests.csv", v$backtests)
  csv("rule_selection.csv", rf_selection_table(v$selection))
  csv("anchor_selection.csv", v$anchors$table)
  dl <- do.call(rbind, lapply(names(b$pac_model$slopes), function(id) { s <- b$pac_model$slopes[[id]]
    data.frame(term = "pac", industry = id, own_beta = s$own_beta, own_se = s$own_se, own_p = s$own_p, dl_weight = s$dl_weight, dl_beta = s$dl_beta, applied_rule = s$applied$rule,
               applied_beta = s$applied$beta, applied_se = s$applied$se, df = s$applied$df, stringsAsFactors = FALSE) }))
  csv("dl_slopes.csv", dl)
  rb <- do.call(rbind, lapply(names(v$evidence), function(k) v$evidence[[k]]))
  csv("robustness_pvalues.csv", rb)
  csv("g4_adequacy.csv", res$gates$g4$table); csv("g5_stability.csv", res$gates$g5$table); csv("g6_nondegradation.csv", res$gates$g6$table)
  sbt <- rf_scored_backtest(v); if (!is.null(sbt)) csv("scored_only.csv", sbt)
  csv("inputs.csv", v$ctx$inputs)
  rf_write_json_atomic(list(decision = res$gates$decision, gates = lapply(seq_len(nrow(gates)), function(i) as.list(gates[i, ]))), file.path(rd, "gates.json")); files <- c(files, "gates.json")
  diff_rows <- if (!is.null(res$published)) rf_bundle_diff(rf_canonical_json(b), res$published) else data.frame(path = character(), a = character(), b = character(), dev = numeric())
  csv("bundle_diff.csv", diff_rows)
  ind <- b$industries
  gaps <- do.call(rbind, lapply(names(ind), function(id) data.frame(industry = id, offered = ind[[id]]$calculator, pac = ind[[id]]$capabilities$pac, control = ind[[id]]$capabilities$control,
    lobbying = ind[[id]]$capabilities$lobbying, withheld = paste(sprintf("%s: %s", names(ind[[id]]$withheld), unlist(ind[[id]]$withheld)), collapse = "; "), stringsAsFactors = FALSE)))
  pi <- b$political_inputs$anchor_cycles$`2024`; so <- b$saved_outlook$control
  g6tab <- res$gates$g6$table; g6tab$status <- ifelse(g6tab$ok, "ok", ifelse(g6tab$waived, "WAIVED", "HOLD")); g6tab <- g6tab[, c("term", "industry", "new_MAE", "published_MAE", "status")]
  w6 <- res$gates$g6$waivers; shown <- w6[w6$applied & w6$show_warning, , drop = FALSE]
  md <- c(
    sprintf("# Giving Forecast refit: review packet (%s)", b$model_version), "",
    sprintf("Run `%s`, built %s, previous model `%s`.", b$producer$run_id, b$built_at_utc, b$previous_model_version %||% "none"), "",
    sprintf("## Decision: %s", res$gates$decision), "", .rf_md_table(gates[, c("gate", "status", "detail")]), "",
    "## Rule selection (auto-pick: a rule must beat the pooled slope by 5% in every design)", "", .rf_md_table(rf_selection_table(v$selection)[, c("selection_term", "design", "rule", "n", "folds", "MAE", "RMSE", "units", "choice", "reported_rule")]), "",
    "## Anchor selection (share models: previous-cycle default; same-phase only if it beats it by 5% in every design)", "", .rf_md_table(v$anchors$table), "",
    "## Slopes by industry (PAC)", "", .rf_md_table(dl), "",
    "## Backtests and the published calculator (G6)", "", .rf_md_table(g6tab), "",
    sprintf("G6 baseline: %s.", res$gates$g6$baseline), "",
    if (nrow(shown)) c("## Owner waivers shown as warnings", "", .rf_md_table(shown[, c("cell", "reason", "owner", "date", "new_MAE", "published_MAE")]), "") else NULL,
    "## Calculator paths against last-period naive", "", .rf_md_table(v$backtests[v$backtests$rule %in% c("R0", "R1", "R2") & v$backtests$design %in% c("loeo", "rolling") & v$backtests$path %in% c("prevchg", "calc"),
                                                                                  c("term", "base", "design", "path", "rule", "n", "MAE", "units", "beats_naive", "coverage_95")]), "",
    if (!is.null(b$cycle_model)) rf_cycle_model_review(b$cycle_model) else NULL,
    rf_scored_review(v),
    "## Lobbying log refit", "",
    sprintf("Relative change per +1 pp of predicted growth: %.3f%% (95%% coefficient interval %.3f%% to %.3f%%), p = %.4f, n = %d; step residual sigma2 = %.6f over %d pairs.",
            v$lobby_log$relative_change_pct_per_point, v$lobby_log$coefficient_interval_pct$low, v$lobby_log$coefficient_interval_pct$high, v$lobby_log$ordinary_p, v$lobby_log$n,
            v$lobby_log$prediction$sigma2, v$lobby_log$prediction$pairs), "",
    "## Data gaps and withheld capabilities", "", .rf_md_table(gaps), "",
    if (length(res$waiting)) c("Waiting for data:", "", unlist(lapply(res$waiting, function(w) c(sprintf("- %s", w$id), sprintf("  - %s: %s", vapply(w$missing, function(m) m$item, ""), vapply(w$missing, function(m) m$detail, ""))))), "") else NULL,
    "## Expected-control inputs", "",
    sprintf("2024 anchor cycle (Polymarket, Jan-Jun): House %.5f over %d of %d days from %s (partial window), Senate %.6f over %d days.", pi$House$p_rep, pi$House$days, pi$House$expected_days, pi$House$first_day,
            pi$Senate$p_rep, pi$Senate$days),
    sprintf("2026 (Polymarket, Jan-Jun): House score %.3f, Senate score %.3f; Kalshi + Polymarket equal weight (sensitivity): House %.3f, Senate %.3f.", so$House$value, so$Senate$value,
            so$House$sensitivity$equal_weight_score, so$Senate$sensitivity$equal_weight_score), "",
    "## Platform", "", sprintf("%s; BLAS %s; LAPACK %s; %d worker(s). Bundles built on different platforms agree to ~1e-13; diffs use tolerances (bundle_diff.csv).",
                              R.version.string, rf_platform_info()$blas, rf_platform_info()$lapack, rf_cores()), "",
    "Files: regressions.txt, backtests.csv, rule_selection.csv, dl_slopes.csv, robustness_pvalues.csv, bundle_diff.csv, inputs.csv, gates.json, g4/g5/g6 csv.",
    "Publishing writes files only; committing stays a human step.")
  put("REVIEW.md", md)
  files
}

# ---- the refit run --------------------------------------------------------------------------------------------------------------------

# Refit: parity context -> v2 context -> bundle -> gates -> packet -> receipt -> publish or hold. `industry` (optional) names an industry
# the request is about: when its data is not all there the run ends as WAITING_FOR_DATA (a clean outcome, exit 0) and nothing is fitted or published.
# publish = "auto" publishes when every gate allows it (decision PUBLISH); "never" stops at the packet.
rf_run_refit <- function(industry = NULL, publish = c("auto", "never"), run_id = NULL, runs_dir = rf_runs_dir(), run_tests = TRUE, b_random = RF$B_RANDOM,
                         reg = rf_read_registry(), models = rf_read_models(), manifest = rf_read_manifest(), published_path = NULL, publish_dir = rf_publish_dir(),
                         consumer_cmd = rf_g8_default_cmd()) {
  publish <- match.arg(publish)
  started <- rf_now_utc()
  run_id <- run_id %||% paste0("refit-", format(Sys.time(), "%Y%m%dT%H%M%SZ", tz = "UTC"))
  dir <- file.path(runs_dir, run_id); dir.create(dir, recursive = TRUE, showWarnings = FALSE)
  gb <- rf_gb_run(run_id)
  log <- character()
  say <- function(...) { line <- paste0(format(Sys.time(), "%H:%M:%S"), "  ", paste0(...)); log <<- c(log, line); message(line) }
  gb$start(sprintf("giving forecast refit: refit mode (%s)%s", RF$VERSION, if (!is.null(industry)) paste(" for", industry) else ""), detail = paste("publish", publish, "cores", rf_cores()))
  tryCatch({
    gb$stage_start("environment")
    pkgs <- rf_load_packages()
    g0 <- rf_gate_env(pkgs)
    gb$stage_finish("environment", g0$detail, status = if (g0$status == "PASS") "succeeded" else "failed"); gb$gate("G0 environment", g0$status, g0$detail); say("G0 ", g0$status)
    gb$stage_start("inputs")
    inputs <- rf_resolve_inputs(manifest)
    g1 <- rf_gate_inputs(inputs)
    gb$stage_finish("inputs", g1$detail, status = if (g1$status == "PASS") "succeeded" else "failed"); gb$gate("G1 inputs", g1$status, g1$detail); say("G1 ", g1$status, ": ", g1$detail)
    if (g1$status != "PASS") stop("G1 failed: ", g1$detail)
    # data readiness of the requested industry (and of every pilot industry, for the packet): waiting is an outcome, not an error
    waiting <- list()
    ids <- unique(c(industry, names(reg$industries)[vapply(reg$industries, function(i) identical(i$status, "pilot"), logical(1))]))
    for (id in ids[!is.na(ids)]) { st <- rf_agent_data_status(reg, id, inputs); if (!st$ready) waiting[[id]] <- st }
    if (!is.null(industry) && industry %in% names(waiting)) {
      st <- waiting[[industry]]
      msg <- sprintf("waiting for data: %s: %s", industry, paste(vapply(st$missing, function(m) paste0(m$item, " (", m$detail, ")"), ""), collapse = "; "))
      rf_write_lines(rf_data_request(industry, st), file.path(dir, "DATA_REQUEST.md"))
      rf_write_json_atomic(list(decision = "WAITING_FOR_DATA", industry = industry, missing = st$missing), file.path(dir, "waiting.json"))
      rf_write_lines(c(log, msg), file.path(dir, "log.txt"))
      gb$finish(msg, detail = dir, status = "succeeded"); say(msg)
      return(list(run_id = run_id, dir = dir, decision = "WAITING_FOR_DATA", waiting = waiting, gates = NULL))
    }
    gb$stage_start("panels")
    ctx0 <- rf_parity_context(inputs, rf_frozen_registry(reg), models)        # the frozen v1 sample: parity gates and the G6 baseline
    ctx0$manifest <- manifest
    g3 <- rf_gate_panels(ctx0)
    pilots <- rf_ready_pilots(reg, inputs)
    reg2 <- rf_v2_registry(reg, inputs)
    ctx <- if (length(pilots) || length(reg2$scored)) { x <- rf_parity_context(inputs, reg2, models); x$manifest <- manifest; x } else ctx0   # + ready pilots (property and casualty) and the scored-only panel (Telecom)
    gb$stage_finish("panels", g3$detail); gb$gate("G3 panel validity", g3$status, g3$detail)
    say("panels, fits and stepwise grid built; G3 ", g3$status, if (length(pilots)) paste0("; pilot industries in the v2 sample: ", paste(pilots, collapse = ", ")) else "",
        if (length(reg2$scored)) paste0("; scored-only industries (outside the pooled fit): ", paste(reg2$scored, collapse = ", ")) else "")
    gb$stage_start("models")
    v <- rf_v2_context(ctx, run_id = run_id, packages = pkgs, b_random = b_random, frozen_ctx = if (length(pilots)) ctx0 else NULL)
    v$adequacy <- rf_gate_adequacy(ctx)
    gb$stage_finish("models", sprintf("rule selection: %s", paste(sprintf("%s=%s", names(v$selection), vapply(v$selection, function(s) s$reported_rule, "")), collapse = ", "))); say("v2 models built")
    published <- if (!is.null(published_path)) jsonlite::fromJSON(published_path, simplifyVector = FALSE) else {
      ptr <- rf_read_pointer(publish_dir); if (!is.null(ptr)) jsonlite::fromJSON(file.path(publish_dir, ptr$active), simplifyVector = FALSE) else rf_read_json(rf_input_path(inputs, "v1_bundle")) }
    gb$stage_start("parity")
    parity <- list(suite = "not run")
    if (run_tests) { parity$suite <- rf_parity_suite(); ok <- parity$suite$failed == 0 && parity$suite$errors == 0
      g2 <- data.frame(gate = "G2 parity", status = if (ok) "PASS" else "FAIL", detail = sprintf("%d expectations, %d failed, %d errors", parity$suite$expectations, parity$suite$failed, parity$suite$errors), stringsAsFactors = FALSE)
    } else g2 <- data.frame(gate = "G2 parity", status = "PASS", detail = "parity suite not run in this call", stringsAsFactors = FALSE)
    gb$stage_finish("parity", g2$detail, status = if (g2$status == "PASS") "succeeded" else "failed"); gb$gate("G2 parity", g2$status, g2$detail)
    base <- rbind(g0, g1, g2, g3)
    gb$stage_start("gates")
    b1 <- rf_bundle_v2(v, previous_model_version = published$model_version)
    if (rf_cycle_model_on() && is.null(b1$cycle_model))                   # the owner's cycle-aware model was asked for: a bundle without it is not what was ordered
      stop("RF_CYCLE_MODEL is on but the bundle has no cycle_model: ", paste(rf_cycle_model_gap(v), collapse = "; "), call. = FALSE)
    staged <- file.path(dir, "bundle", "giving-models-v2.staged.json")
    rf_write_json_atomic(b1, staged)
    res_g <- rf_gates(v, b1, published = published, base = base, bundle_path = staged, consumer_cmd = consumer_cmd)
    for (k in seq_len(nrow(res_g$gates))) if (!(substr(res_g$gates$gate[k], 1, 2) %in% c("G0", "G1", "G2", "G3"))) gb$gate(res_g$gates$gate[k], res_g$gates$status[k], res_g$gates$detail[k])
    bundle <- rf_bundle_v2(v, res_g$gates, previous_model_version = published$model_version)
    g7 <- rf_gate_bundle(bundle, reg)                                     # the finalized bundle (with its gate summary) must validate too
    if (g7$status != "PASS") { res_g$gates$status[substr(res_g$gates$gate, 1, 2) == "G7"] <- "FAIL"; res_g$gates$detail[substr(res_g$gates$gate, 1, 2) == "G7"] <- g7$detail; res_g$decision <- rf_decision(res_g$gates); bundle <- rf_bundle_v2(v, res_g$gates, previous_model_version = published$model_version) }
    unlink(staged)
    bundle_path <- file.path(dir, "bundle", "giving-models-v2.json")
    rf_write_json_atomic(bundle, bundle_path)
    gb$stage_finish("gates", sprintf("decision %s", res_g$decision), status = if (res_g$decision == "FAIL") "failed" else "succeeded"); say("gates: ", res_g$decision)
    res <- list(v = v, bundle = bundle, gates = res_g, published = published, waiting = waiting)
    gb$stage_start("packet")
    files <- rf_review_packet(res, dir, b_random = b_random)
    if (length(waiting)) for (id in names(waiting)) rf_write_lines(rf_data_request(id, waiting[[id]]), file.path(dir, "review", paste0("DATA_REQUEST_", id, ".md")))
    gb$stage_finish("packet", sprintf("%d review files", length(files))); say("review packet written")
    run <- list(run_id = run_id, mode = "refit", started_utc = started, inputs = inputs, packages = pkgs, gates = res_g$gates, parity = parity, decision = res_g$decision)
    receipt <- rf_receipt(run, bundle_path)
    receipt$rule_selection <- lapply(v$selection, function(s) list(reported_rule = s$reported_rule, reason = s$reason))
    if (!is.null(bundle$cycle_model)) receipt$cycle_model <- list(version = bundle$cycle_model$version, pac_to_candidates = bundle$cycle_model$measures$pac_to_candidates$candidate,
                                                                  general_giving = bundle$cycle_model$measures$general_giving$candidate, panel_sha256 = bundle$cycle_model$source$panel_sha256)
    receipt$anchor_selection <- lapply(v$anchors$entries, function(ch) lapply(ch, function(e) list(anchor = e$anchor, slope_rule = e$rule, reason = e$reason)))
    w6 <- res_g$g6$waivers                                                   # every owner waiver on the list, applied or not, whether or not it is shown as a warning
    receipt$waivers <- lapply(seq_len(nrow(w6)), function(i) as.list(w6[i, ]))
    receipt$waiting_for_data <- lapply(waiting, function(w) w$missing)
    pub <- list(status = "not_requested")
    if (publish == "auto") {
      gb$stage_start("publish")
      pub <- rf_publish(bundle, res_g$gates, receipt, dir = publish_dir, gb = gb)
      gb$stage_finish("publish", pub$status, status = if (pub$status == "published") "succeeded" else "failed"); say("publish: ", pub$status)
      if (pub$status == "published") receipt$published_to <- list(list(dir = publish_dir, file = pub$pointer$active, sha256 = pub$sha256))
    }
    rf_write_json_atomic(receipt, file.path(dir, "receipt.json"))
    rf_write_lines(log, file.path(dir, "log.txt"))
    gb$finish(sprintf("refit run %s: %s%s", run_id, res_g$decision, if (pub$status == "published") " (published)" else ""), detail = dir, status = if (res_g$decision == "FAIL") "failed" else "succeeded")
    list(run_id = run_id, dir = dir, decision = res_g$decision, gates = res_g, bundle = bundle, published = pub, waiting = waiting, res = res, receipt = receipt)
  }, error = function(e) {
    gb$fail(paste("refit run failed:", conditionMessage(e)), detail = dir)
    rf_write_lines(c(log, paste("FAILED:", conditionMessage(e))), file.path(dir, "log.txt"))
    stop(e)
  })
}

# Detect mode: scan, queue new industries, write one DATA_REQUEST.md per queued industry, emit events. -> list(det, queued)
rf_run_detect <- function(run_id = NULL, runs_dir = rf_runs_dir(), reg = rf_read_registry(), report_dir = rf_report_dir(), remember = TRUE) {
  run_id <- run_id %||% paste0("detect-", format(Sys.time(), "%Y%m%dT%H%M%SZ", tz = "UTC"))
  dir <- file.path(runs_dir, run_id); dir.create(dir, recursive = TRUE, showWarnings = FALSE)
  gb <- rf_gb_run(run_id)
  gb$start(sprintf("giving forecast refit: detect mode over %s", if (is.na(report_dir)) "(no report directory)" else report_dir))
  gb$stage_start("scan")
  if (is.na(report_dir) || !dir.exists(report_dir)) {
    msg <- "report directory not reachable: nothing to scan"
    gb$stage_finish("scan", msg); gb$finish(msg, status = "succeeded"); rf_write_lines(msg, file.path(dir, "log.txt"))
    return(list(run_id = run_id, dir = dir, det = NULL, queued = NULL, status = "no_report_directory"))
  }
  det <- rf_agent_detect(reg, report_dir)
  gb$stage_finish("scan", sprintf("%d workbooks: %d known, %d declined, %d new; %d changed since last seen", nrow(det), sum(det$status == "known"), sum(det$status == "declined"), sum(det$status == "new"), sum(det$changed)))
  gb$stage_start("queue")
  q <- rf_agent_queue(det)
  for (i in seq_len(nrow(q))) if (q$created[i]) {
    row <- det[det$slug == q$slug[i], ]
    rf_write_lines(rf_data_request(row$industry[1]), file.path(dir, paste0("DATA_REQUEST_", q$slug[i], ".md")))
    rf_write_json_atomic(list(id = q$slug[i], label = row$industry[1], mentat_key = NULL, mentat_workbook = row$workbook[1], calculator = FALSE,
                              opensecrets = list(codes = list(), totals = NULL, recipients = NULL, presidential = NULL, status = "missing"),
                              ibisworld = list(code = NULL, actual_sector = NULL, predicted_study_sector = NULL, predicted_rule = NULL),
                              lobbyview = list(study_sector = NULL, confidence = list(), naics = list()), models = list(pac = FALSE, control = FALSE, lobbying = FALSE), status = "stub"),
                         file.path(dir, paste0("registry_stub_", q$slug[i], ".json"))) }
  gb$stage_finish("queue", sprintf("%d new industries queued (%d already queued)", sum(q$created), sum(!q$created)))
  if (remember) rf_agent_remember(det)
  rf_write_csv(det, file.path(dir, "detect.csv"))
  gb$finish(sprintf("detect run %s: %d new industries", run_id, sum(det$status == "new")), status = "succeeded")
  list(run_id = run_id, dir = dir, det = det, queued = q, status = "ok")
}

# The parity run: raw inputs -> panels -> frozen fits -> stepwise grid -> v1 bundle projection -> review files -> receipt.
# Phase 1 runs the v1 configuration only (no methodology change); it never publishes. Every stage emits Glass Box events.

# ---- gates of Phase 1 ------------------------------------------------------------------------------------------------------

# G0: R version, package pins (vendored library), rf_rng.R hash.
rf_gate_env <- function(pkgs) {
  ok_r <- getRversion() >= "4.6.0"
  ok_rng <- identical(rf_sha256(file.path(rf_home(), "R", "rf_rng.R")), RF$RNG_SHA256)
  ok <- ok_r && ok_rng && all(pkgs$ok)
  data.frame(gate = "G0 environment", status = if (ok) "PASS" else "FAIL",
             detail = sprintf("%s; %s; rf_rng.R %s", R.version.string, paste(sprintf("%s %s", pkgs$package, pkgs$version), collapse = ", "),
                              if (ok_rng) "hash pinned" else "HASH CHANGED"), stringsAsFactors = FALSE)
}

# G3: every panel check has zero failures.
rf_gate_panels <- function(ctx) {
  ck <- rbind(cbind(panel = "pac", rf_panel_checks(ctx$pac_panel, "pac")), cbind(panel = "money", rf_panel_checks(ctx$money, "money")),
              do.call(rbind, lapply(names(ctx$share), function(nm) cbind(panel = nm, rf_panel_checks(ctx$share[[nm]], "share")))),
              cbind(panel = "lobby", rf_panel_checks(ctx$lobby$diffs, "lobby")))
  bad <- ck[ck$failures > 0, ]
  data.frame(gate = "G3 panel validity", status = if (nrow(bad)) "FAIL" else "PASS",
             detail = if (nrow(bad)) paste(sprintf("%s: %s (%d)", bad$panel, bad$name, bad$failures), collapse = "; ") else sprintf("%d checks, 0 failures", nrow(ck)),
             stringsAsFactors = FALSE)
}

# G2: run the parity test files in a testthat run and count expectations. `filter` is a regex over the test file names.
rf_parity_suite <- function(filter = "^(01|02|03|04|05|06|07|08|08b|10)-") {
  # The test helper redirects RF_RUNTIME, RF_GLASSBOX_DIR and other settings into
  # its sandbox. Restore the complete caller environment even if a helper errors:
  # a real refit's subsequent publication mutex and archive must remain persistent.
  caller_env <- Sys.getenv()
  on.exit({
    added <- setdiff(names(Sys.getenv()), names(caller_env))
    if (length(added)) Sys.unsetenv(added)
    do.call(Sys.setenv, as.list(caller_env))
  }, add = TRUE)
  res <- testthat::test_dir(file.path(rf_home(), "tests", "testthat"), filter = filter, stop_on_failure = FALSE, reporter = "silent")
  df <- as.data.frame(res)
  list(files = length(unique(df$file)), tests = nrow(df), expectations = sum(df$nb), failed = sum(df$failed), errors = sum(df$error),
       skipped = sum(df$skipped), warnings = sum(df$warning))
}

# Leaf-by-leaf comparison of two parsed JSON trees for the run report: number of mismatching leaves and the largest deviation.
rf_compare_bundles <- function(a, b, rel_tol = 1e-9, abs_tol = 1e-12) {
  bad <- 0L; maxdev <- 0
  walk <- function(x, y) {
    if (is.list(x) && is.list(y)) {
      nx <- names(x); ny <- names(y)
      if (!is.null(nx) || !is.null(ny)) {
        if (!setequal(nx, ny)) { bad <<- bad + 1L; return(invisible()) }
        for (k in nx) walk(x[[k]], y[[k]])
      } else if (length(x) != length(y)) {
        bad <<- bad + 1L
      } else {
        for (i in seq_along(x)) walk(x[[i]], y[[i]])
      }
    } else if (is.null(x) && is.null(y)) {
      invisible()
    } else if (is.numeric(x) && is.numeric(y) && length(x) == 1L && length(y) == 1L) {
      d <- abs(x - y); s <- max(abs(x), abs(y)); dev <- if (s < 1e-6) d else d / s
      if ((s < 1e-6 && d > abs_tol) || (s >= 1e-6 && dev > rel_tol)) bad <<- bad + 1L
      maxdev <<- max(maxdev, dev)
    } else if (!identical(x, y)) {
      bad <<- bad + 1L
    }
    invisible()
  }
  walk(a, b)
  list(n_mismatch = bad, max_dev = maxdev)
}

# CRLF line endings written explicitly (the bytes R's text mode gives on Windows), so the file is byte-identical on every platform (compute parity, D-177).
rf_write_csv <- function(d, path) {
  dir.create(dirname(path), recursive = TRUE, showWarnings = FALSE)
  rc <- rawConnection(raw(0), "w"); utils::write.csv(d, rc, row.names = FALSE); b <- rawConnectionValue(rc); close(rc)
  writeBin(charToRaw(gsub("\n", "\r\n", rawToChar(b), fixed = TRUE, useBytes = TRUE)), path)
  invisible(path)
}

# Run the v1 configuration end to end and write the run folder <runs_dir>/<run_id>/:
#   panels/*.csv, bundle/giving-models-v1.projection.json, review/{regressions.txt, backtests.csv, rule_selection.csv,
#   selected_models.csv, nested_validation_metrics.csv, inputs.csv, gates.json}, receipt.json, log.txt
# The decision is PARITY_GREEN or FAIL; nothing is published.
rf_run_parity <- function(run_id = NULL, runs_dir = rf_runs_dir(), run_tests = TRUE, b_random = RF$B_RANDOM, reg = rf_read_registry(),
                          models = rf_read_models(), manifest = rf_read_manifest()) {
  started <- rf_now_utc()
  run_id <- run_id %||% paste0("parity-", format(Sys.time(), "%Y%m%dT%H%M%SZ", tz = "UTC"))
  dir <- file.path(runs_dir, run_id)
  dir.create(dir, recursive = TRUE, showWarnings = FALSE)
  gb <- rf_gb_run(run_id)
  log <- character()
  say <- function(...) { line <- paste0(format(Sys.time(), "%H:%M:%S"), "  ", paste0(...)); log <<- c(log, line); message(line) }
  gb$start(sprintf("giving forecast refit: parity mode (%s, v1 configuration)", RF$VERSION), detail = paste("cores", rf_cores(), "run folder", dir))
  tryCatch({
    gb$stage_start("environment")
    pkgs <- rf_load_packages()
    g0 <- rf_gate_env(pkgs)
    gb$stage_finish("environment", g0$detail, status = if (g0$status == "PASS") "succeeded" else "failed")
    gb$gate("G0 environment", g0$status, g0$detail)
    say("G0 ", g0$status)

    gb$stage_start("inputs")
    inputs <- rf_resolve_inputs(manifest)
    g1 <- rf_gate_inputs(inputs)
    gb$stage_finish("inputs", g1$detail, status = if (g1$status == "PASS") "succeeded" else "failed")
    gb$gate("G1 inputs", g1$status, g1$detail)
    say("G1 ", g1$status, ": ", g1$detail)
    if (g1$status != "PASS") stop("G1 failed: ", g1$detail)

    gb$stage_start("panels")
    reg <- rf_frozen_registry(reg)                       # parity is judged on the frozen v1 sample (pilot industries are not in it)
    ctx <- rf_build_panels(inputs, reg)
    ctx$inputs <- inputs; ctx$reg <- reg; ctx$models <- models
    g3 <- rf_gate_panels(ctx)
    gb$stage_finish("panels", g3$detail)
    gb$gate("G3 panel validity", g3$status, g3$detail)
    say("panels built; G3 ", g3$status)
    rf_write_csv(ctx$pac_panel, file.path(dir, "panels", "pac_panel.csv"))
    rf_write_csv(ctx$money, file.path(dir, "panels", "money_panel_congressional.csv"))
    for (nm in names(ctx$share)) rf_write_csv(ctx$share[[nm]], file.path(dir, "panels", paste0("share_", nm, ".csv")))
    rf_write_csv(ctx$lobby$levels, file.path(dir, "panels", "lobby_levels.csv"))
    rf_write_csv(ctx$lobby$diffs, file.path(dir, "panels", "lobby_diffs.csv"))

    gb$stage_start("fits")
    fp <- rf_fit_panels(ctx, models)
    ctx$fits <- fp$fits; ctx$fits_data <- fp$data
    ctx$pac_selection <- rf_selection_repeated_pac(ctx$pac_panel)
    m <- ctx$fits$lobby
    X <- stats::model.matrix(m); y <- stats::model.response(stats::model.frame(m)); k <- match("dg", colnames(X))
    ctx$lobby_wild <- rf_wcr(X, y, ctx$lobby$diffs$study_sector, k, "webb",
                             entropy = c(RF$SEED, sgs_crc32("lobby"), sgs_crc32("dg"), sgs_crc32("study_sector")))
    ctx$lobby_cr2 <- rf_cr2(m, "dg", ctx$lobby$diffs$study_sector)
    gb$stage_finish("fits", sprintf("%d lm() fits; PAC selection-repeated p %.6f (%d of %d); lobbying wild p %.6f", length(ctx$fits),
                                    ctx$pac_selection$p, ctx$pac_selection$hits, ctx$pac_selection$draws, ctx$lobby_wild$p))
    say("fits done")

    gb$stage_start("stepwise")
    ctx$stepwise <- rf_stepwise_grid(ctx$cases)
    rf_write_csv(ctx$stepwise$selected, file.path(dir, "review", "selected_models.csv"))
    rf_write_csv(ctx$stepwise$validation, file.path(dir, "review", "nested_validation_metrics.csv"))
    gb$stage_finish("stepwise", sprintf("%d cases; selection-repeated p computed for all", nrow(ctx$stepwise$selected)))
    say("stepwise grid done")

    gb$stage_start("bundle")
    proj <- rf_bundle_v1_projection(ctx)
    v1 <- rf_read_json(rf_input_path(inputs, "v1_bundle"))
    cmp <- rf_compare_bundles(jsonlite::fromJSON(jsonlite::toJSON(proj, auto_unbox = TRUE, digits = NA, null = "null", na = "null"), simplifyVector = FALSE), v1)
    bundle_path <- file.path(dir, "bundle", "giving-models-v1.projection.json")
    rf_write_json_atomic(proj, bundle_path)
    gb$stage_finish("bundle", sprintf("v1 projection written; %d leaf mismatches; max deviation %.2e", cmp$n_mismatch, cmp$max_dev))
    say("v1 projection: ", cmp$n_mismatch, " mismatching leaves, max deviation ", format(cmp$max_dev, digits = 3))

    gb$stage_start("report")
    txt <- rf_regressions_report(ctx, b_random = b_random, v1 = v1)
    rf_write_lines(txt, file.path(dir, "review", "regressions.txt"))
    specs <- rf_backtest_specs(ctx)
    sel <- rf_select_rules(specs)
    rf_write_csv(rf_selection_table(sel), file.path(dir, "review", "rule_selection.csv"))
    cells <- unlist(lapply(names(specs), function(key) lapply(if (specs[[key]]$kind == "lobby") "rolling" else c("loeo", "chrono"),
                     function(ds) rf_backtest_metrics(rf_backtest(specs[[key]], specs[[key]]$d, ds)))), recursive = FALSE)
    bt <- rf_rbind_fill(cells)
    rf_write_csv(bt, file.path(dir, "review", "backtests.csv"))
    rf_write_csv(inputs, file.path(dir, "review", "inputs.csv"))
    gb$stage_finish("report", sprintf("regressions.txt (%d lines), backtests.csv (%d rows), rule_selection.csv", length(txt), nrow(bt)))
    say("report written")

    parity <- list(v1_projection = list(leaf_mismatches = cmp$n_mismatch, max_deviation = cmp$max_dev), suite = "not run")
    gb$stage_start("parity")
    if (run_tests) {
      parity$suite <- rf_parity_suite()
      ok <- parity$suite$failed == 0 && parity$suite$errors == 0 && cmp$n_mismatch == 0
      g2 <- data.frame(gate = "G2 parity", status = if (ok) "PASS" else "FAIL",
                       detail = sprintf("%d expectations, %d failed, %d errors; %d leaf mismatches", parity$suite$expectations, parity$suite$failed,
                                        parity$suite$errors, cmp$n_mismatch), stringsAsFactors = FALSE)
    } else {
      g2 <- data.frame(gate = "G2 parity", status = if (cmp$n_mismatch == 0) "PASS" else "FAIL",
                       detail = sprintf("test suite not run in this call; v1 projection has %d leaf mismatches", cmp$n_mismatch), stringsAsFactors = FALSE)
    }
    gb$stage_finish("parity", g2$detail, status = if (g2$status == "PASS") "succeeded" else "failed")
    gb$gate("G2 parity", g2$status, g2$detail)
    say("G2 ", g2$status, ": ", g2$detail)

    gates <- rbind(g0, g1, g2, g3)
    decision <- if (any(gates$status == "FAIL")) "FAIL" else "PARITY_GREEN"
    rf_write_json_atomic(list(decision = decision, gates = lapply(seq_len(nrow(gates)), function(i) as.list(gates[i, ]))), file.path(dir, "review", "gates.json"))
    run <- list(run_id = run_id, mode = "parity", started_utc = started, inputs = inputs, packages = pkgs, gates = gates, parity = parity, decision = decision)
    rf_write_json_atomic(rf_receipt(run, bundle_path), file.path(dir, "receipt.json"))
    rf_write_lines(log, file.path(dir, "log.txt"))
    gb$finish(sprintf("parity run %s: %s", run_id, decision), detail = dir, status = if (decision == "FAIL") "failed" else "succeeded")
    list(run_id = run_id, dir = dir, decision = decision, gates = gates, parity = parity, ctx = ctx, projection = proj, events = gb$ids())
  }, error = function(e) {
    gb$fail(paste("parity run failed:", conditionMessage(e)), detail = dir)
    rf_write_lines(c(log, paste("FAILED:", conditionMessage(e))), file.path(dir, "log.txt"))
    stop(e)
  })
}

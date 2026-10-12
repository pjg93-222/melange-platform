# Command line entry of the Giving Forecast refit pipeline.
#   Rscript run_refit.R --mode parity [--run-id <id>] [--no-tests]                        rebuild the v1 bundle from raw inputs, compare, write a run folder
#   Rscript run_refit.R --mode detect [--run-id <id>]                                      scan Mentat's report directory (read-only), queue new industries
#   Rscript run_refit.R --mode refit [--industry <id>] [--publish auto|never] [--run-id <id>] [--no-tests]
#                                                                                          v2 refit: gates G0-G9, review packet, receipt; publishes only when every gate allows
#   Rscript run_refit.R --rollback <model_version>                                         make an earlier published (or the kept v1) bundle active again
# Exit status: 0 = parity green / PUBLISH / HOLD / WAITING_FOR_DATA (clean outcomes), 1 = FAIL or an error, 2 = unknown mode.
# Environment: RF_RUNTIME, RF_RLIB, RF_INPUT_ROOT / RF_ROOT_<ALIAS>, RF_GLASSBOX_DIR, REFIT_OUT, REFIT_CORES, RF_PUBLISH_DIR, RF_REPORT_DIR,
# MENTAT_G8_CMD, RF_CYCLE_MODEL (1 = cycle-aware 50/50 model, default; 0 = the bundle as published before it) (see R/rf_config.R, R/rf_publish.R, R/rf_agent.R). Nothing here commits to git.
fa <- grep("^--file=", commandArgs(trailingOnly = FALSE), value = TRUE)
here <- if (length(fa)) dirname(normalizePath(sub("^--file=", "", fa[1]), winslash = "/")) else getwd()
assign(".RF_HOME", here, envir = globalenv())
source(file.path(here, "R", "rf_config.R"), encoding = "UTF-8")
rf_source_all(here)

args <- commandArgs(trailingOnly = TRUE)
opt <- function(name, default = NULL) { i <- match(name, args); if (is.na(i) || i == length(args)) default else args[i + 1L] }
mode <- opt("--mode", "parity")
if ("--rollback" %in% args) {
  suppressWarnings(rf_load_packages())
  r <- rf_rollback(opt("--rollback"))
  cat(sprintf("rolled back to %s (%s)\n", r$model_version, r$file))
  quit(status = 0L)
}
if (mode == "parity") {
  res <- rf_run_parity(run_id = opt("--run-id"), run_tests = !("--no-tests" %in% args))
  cat(sprintf("\nparity run %s -> %s\n", res$run_id, res$decision))
  print(res$gates, row.names = FALSE)
  cat("run folder:", res$dir, "\n")
  quit(status = if (res$decision == "FAIL") 1L else 0L)
} else if (mode == "detect") {
  suppressWarnings(rf_load_packages())
  res <- rf_run_detect(run_id = opt("--run-id"))
  cat(sprintf("\ndetect run %s -> %s\n", res$run_id, res$status))
  if (!is.null(res$det)) print(res$det[, c("workbook", "status", "registry_id", "changed")], row.names = FALSE)
  cat("run folder:", res$dir, "\n")
  quit(status = 0L)
} else if (mode == "refit") {
  res <- rf_run_refit(industry = opt("--industry"), publish = opt("--publish", "auto"), run_id = opt("--run-id"), run_tests = !("--no-tests" %in% args))
  cat(sprintf("\nrefit run %s -> %s\n", res$run_id, res$decision))
  if (!is.null(res$gates)) print(res$gates$gates, row.names = FALSE)
  if (identical(res$decision, "WAITING_FOR_DATA")) for (w in res$waiting) cat(sprintf("waiting: %s\n", paste(vapply(w$missing, function(m) m$detail, ""), collapse = " | ")))
  if (!is.null(res$published)) cat("publish:", res$published$status, "\n")
  cat("run folder:", res$dir, "\n")
  quit(status = if (identical(res$decision, "FAIL")) 1L else 0L)
} else {
  message("run_refit.R: unknown mode '", mode, "' (parity, detect, refit, or --rollback <model_version>)")
  quit(status = 2L)
}

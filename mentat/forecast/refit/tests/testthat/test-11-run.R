# The parity run end to end (spec 3.14, 4.9): read-only on every input, writes only under its run folder, emits Glass Box events for
# every stage and gate, writes a receipt, and fails loudly (with a failure event) when a pinned input changes.

tree_hashes <- function(dir) {
  f <- list.files(dir, recursive = TRUE, full.names = TRUE, all.files = TRUE)
  f <- f[!grepl("/(_snaps|\\.Rhistory)", f)]
  stats::setNames(unname(tools::sha256sum(f)), sub(paste0("^", dir, "/?"), "", f))
}

test_that("a parity run is read-only on inputs and code, writes its run folder, and ends PARITY_GREEN", {
  inp <- test_inputs()
  before_inputs <- stats::setNames(vapply(inp$path, function(p) rf_sha256(p), ""), inp$role)
  before_code <- tree_hashes(.RF_HOME)
  runs <- file.path(tempdir(), "runs_test"); unlink(runs, recursive = TRUE)
  gb_dir <- file.path(tempdir(), "gb_run_test"); unlink(gb_dir, recursive = TRUE)
  old <- Sys.getenv("RF_GLASSBOX_DIR"); on.exit(Sys.setenv(RF_GLASSBOX_DIR = old), add = TRUE)
  Sys.setenv(RF_GLASSBOX_DIR = gb_dir)
  res <- rf_run_parity(run_id = "unit-parity", runs_dir = runs, run_tests = FALSE, b_random = 999L)
  expect_identical(res$decision, "PARITY_GREEN")
  expect_true(all(res$gates$status == "PASS"))
  expect_setequal(res$gates$gate, c("G0 environment", "G1 inputs", "G2 parity", "G3 panel validity"))
  # inputs and code are byte-identical after the run
  after_inputs <- stats::setNames(vapply(inp$path, function(p) rf_sha256(p), ""), inp$role)
  expect_identical(after_inputs, before_inputs)
  expect_identical(tree_hashes(.RF_HOME), before_code)
  # everything the run wrote is under its run folder
  d <- file.path(runs, "unit-parity")
  files <- list.files(d, recursive = TRUE)
  expect_true(all(c("receipt.json", "log.txt", "bundle/giving-models-v1.projection.json", "review/regressions.txt", "review/backtests.csv",
                    "review/rule_selection.csv", "review/selected_models.csv", "review/gates.json", "review/inputs.csv",
                    "panels/pac_panel.csv", "panels/money_panel_congressional.csv") %in% files))
  expect_identical(list.files(runs), "unit-parity")
  # the receipt names every input, every code file, the packages, the gates and the bundle hash
  rc <- jsonlite::fromJSON(file.path(d, "receipt.json"), simplifyVector = FALSE)
  expect_identical(rc$decision, "PARITY_GREEN")
  expect_equal(length(rc$inputs), nrow(inp))
  expect_identical(vapply(rc$inputs, function(i) i$sha256, ""), unname(inp$actual))
  code <- list.files(file.path(.RF_HOME, "R"), pattern = "\\.R$")
  expect_setequal(vapply(rc$code, function(x) x$file, ""), code)
  expect_true(all(c("jsonlite", "readxl", "sandwich", "clubSandwich") %in% vapply(rc$packages, function(p) p$package, "")))
  expect_identical(rc$bundle_sha256, rf_sha256(file.path(d, "bundle", "giving-models-v1.projection.json")))
  expect_identical(rc$parity$v1_projection$leaf_mismatches, 0L)
  # the regressions report is real summary(lm()) output with robustness blocks and coefficient-match lines
  txt <- readLines(file.path(d, "review", "regressions.txt"), warn = FALSE)
  expect_gt(sum(grepl("^Coefficients:", txt)), 9L)
  expect_gt(sum(grepl("ROBUSTNESS / BOOTSTRAP BLOCK", txt)), 8L)
  expect_true(any(grepl("COEFFICIENT MATCH vs v1 pac_model", txt)))
  # Glass Box: a start, each stage, each gate, a finish - all with this run id
  ev <- lapply(list.files(gb_dir, pattern = "^evt-.*\\.json$", full.names = TRUE), jsonlite::fromJSON, simplifyVector = FALSE)
  expect_true(all(vapply(ev, function(e) e$run_id, "") == "unit-parity"))
  kinds <- vapply(ev, function(e) e$kind, "")
  expect_identical(sum(kinds == "run.start"), 1L)
  expect_identical(sum(kinds == "run.finish"), 1L)
  expect_identical(sum(kinds == "gate.result"), 4L)
  stages <- vapply(ev[kinds == "stage.start"], function(e) e$stage, "")
  expect_setequal(stages, c("environment", "inputs", "panels", "fits", "stepwise", "bundle", "report", "parity"))
  expect_identical(sum(kinds == "stage.finish"), length(stages))
  expect_false(any(vapply(ev, function(e) e$status, "") == "failed"))
})

test_that("a changed pinned input stops the run at G1 and emits a failure event (negative control)", {
  man <- RF_TEST_MANIFEST
  man$sha256[match("cpi", man$role)] <- paste(rep("0", 64), collapse = "")
  runs <- file.path(tempdir(), "runs_test_fail"); unlink(runs, recursive = TRUE)
  gb_dir <- file.path(tempdir(), "gb_run_fail"); unlink(gb_dir, recursive = TRUE)
  old <- Sys.getenv("RF_GLASSBOX_DIR"); on.exit(Sys.setenv(RF_GLASSBOX_DIR = old), add = TRUE)
  Sys.setenv(RF_GLASSBOX_DIR = gb_dir)
  expect_error(rf_run_parity(run_id = "unit-fail", runs_dir = runs, run_tests = FALSE, manifest = man), "G1 failed")
  ev <- lapply(list.files(gb_dir, pattern = "^evt-.*\\.json$", full.names = TRUE), jsonlite::fromJSON, simplifyVector = FALSE)
  status <- vapply(ev, function(e) paste(e$kind, e$status), "")
  expect_true("run.finish failed" %in% status)
  expect_true("gate.result failed" %in% status)
  expect_false(file.exists(file.path(runs, "unit-fail", "receipt.json")))
  expect_true(any(grepl("FAILED", readLines(file.path(runs, "unit-fail", "log.txt"), warn = FALSE))))
})

test_that("run_refit.R offers parity, detect, refit and rollback and refuses an unknown mode", {
  src <- readLines(file.path(.RF_HOME, "run_refit.R"), warn = FALSE)
  for (pat in c("--mode parity", "--mode detect", "--mode refit", "--rollback", "unknown mode")) expect_true(any(grepl(pat, src, fixed = TRUE)), info = pat)
})

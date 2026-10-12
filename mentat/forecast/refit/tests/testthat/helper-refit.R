# Shared helpers of the testthat suite: load the modules, isolate the runtime and Glass Box folders in a temp
# directory, and memoize the expensive parity context (raw inputs -> panels -> fits) once per test process.
# The vendored package library (clubSandwich) is the pipeline-owned one outside git, located BEFORE the runtime
# folder is redirected.

if (!requireNamespace("readxl", quietly = TRUE)) .libPaths(c("C:/Users/pgoff/AppData/Local/R/win-library/4.6", .libPaths()))
assign(".RF_HOME", normalizePath(file.path(testthat::test_path(), "..", ".."), winslash = "/", mustWork = TRUE), envir = globalenv())
.RF_HOME <- get(".RF_HOME", envir = globalenv())
source(file.path(.RF_HOME, "R", "rf_config.R"), encoding = "UTF-8")

# Default (persistent) vendored library, then redirect everything else that writes into a per-process temp folder.
RF_TEST_RLIB <- rf_rlib_dir()
# RF_TEST_RUNTIME (optional) keeps the content-addressed raw vault between test runs (hash-verified on every use), which saves
# the ~1 minute it takes to copy ~100 input files into a fresh temp folder.
RF_TEST_TMP <- rf_env_path("RF_TEST_RUNTIME", file.path(tempdir(), "rf_test_runtime"))
dir.create(RF_TEST_TMP, recursive = TRUE, showWarnings = FALSE)
# Serial unless the caller sets REFIT_CORES (the pod runs set it; a parallel run is bit-identical to a serial one, see test-13).
if (!nzchar(Sys.getenv("REFIT_CORES", unset = ""))) Sys.setenv(REFIT_CORES = "1")
Sys.setenv(RF_RLIB = RF_TEST_RLIB, RF_RUNTIME = RF_TEST_TMP, RF_GLASSBOX_DIR = file.path(tempdir(), "rf_test_glassbox"))
rf_source_all(.RF_HOME)

# The pipeline-owned Rlib is created once (copy from the Codex Rlib + manifest); later runs only verify it.
suppressWarnings(rf_setup_rlib())
RF_TEST_PKGS <- suppressWarnings(rf_load_packages())

RF_TEST_REG <- rf_read_registry()
RF_TEST_MODELS <- rf_read_models()
RF_TEST_MANIFEST <- rf_read_manifest()

.rf_cache <- new.env(parent = emptyenv())
memo <- function(key, expr) {
  if (is.null(.rf_cache[[key]])) .rf_cache[[key]] <- force(expr)
  .rf_cache[[key]]
}

# Inputs resolved (hash-checked, vaulted) once.
test_inputs <- function() memo("inputs", rf_resolve_inputs(RF_TEST_MANIFEST))
# The whole parity build (panels, fits, stepwise grid, evidence...) once.
test_ctx <- function() memo("ctx", rf_parity_context(test_inputs(), rf_frozen_registry(RF_TEST_REG), RF_TEST_MODELS))   # the frozen v1 sample (parity)

# Backtest specs (tailoring.R's eight term/base cells) once.
test_specs <- function() memo("specs", rf_backtest_specs(test_ctx()))

# The v2 context (auto-pick, own-slope PAC, previous-cycle shares, log lobbying, evidence) once; 999 draws for the random bootstrap blocks.
test_v2 <- function() memo("v2", { v <- rf_v2_context(test_ctx(), run_id = "unit", packages = RF_TEST_PKGS, built_at_utc = "2026-09-30T12:00:00Z", b_random = 999L)
                                   v$adequacy <- rf_gate_adequacy(test_ctx()); v })
test_bundle2 <- function() memo("bundle2", rf_bundle_v2(test_v2()))

# The v2 ESTIMATION sample of a production refit: the frozen sample plus the ready property and casualty pilot. The cycle model (phase fit, steps, IBISWorld paths) needs every industry the
# console offers, so test-23 and the G6 cycle tests run on it; the frozen-sample context above keeps every earlier assertion of the suite on its own numbers.
test_ctx_pilot <- function() memo("ctx_pilot", rf_parity_context(test_inputs(), rf_v2_registry(RF_TEST_REG, test_inputs()), RF_TEST_MODELS))
test_v2_pilot <- function() memo("v2_pilot", { v <- rf_v2_context(test_ctx_pilot(), run_id = "unit", packages = RF_TEST_PKGS, built_at_utc = "2026-10-07T12:00:00Z", b_random = 999L, frozen_ctx = test_ctx())
                                              v$adequacy <- rf_gate_adequacy(test_ctx_pilot()); v })
test_bundle2_pilot <- function() memo("bundle2_pilot", rf_bundle_v2(test_v2_pilot()))

# The bundle published before the cycle model (model_version refit-20260930-377f7324-c3f221343515), read from the immutable versions folder of the publish directory; NULL when this tree has none
# (a mimic tree without mentat/app/data), so the tests that need it skip with a reason.
RF_PRE_CYCLE_SHA <- "7bc7bcf175f7a3c291cc67300f981d610ef7b52059eefb7cb21e2dc4e020e0bb"
published_before_cycle <- function() memo("pre_cycle", {
  f <- file.path(rf_publish_dir(), "versions", RF_PRE_CYCLE_SHA, "giving-models-v2.json")
  if (file.exists(f) && identical(rf_sha256(f), RF_PRE_CYCLE_SHA)) jsonlite::fromJSON(f, simplifyVector = FALSE) else NA
})

# A Codex design file (parity reference) by name, read from the vault.
ref_path <- function(name) rf_input_path(test_inputs(), paste0("parity_ref:", name))
ref_csv <- function(name) utils::read.csv(ref_path(name), stringsAsFactors = FALSE, check.names = FALSE)
v1_bundle <- function() memo("v1", jsonlite::fromJSON(rf_input_path(test_inputs(), "v1_bundle"), simplifyVector = FALSE))

# max |a - b| over paired numeric vectors (NA-aware: NA must match NA).
max_abs <- function(a, b) {
  a <- unname(a); b <- unname(b)
  stopifnot(length(a) == length(b))
  if (!identical(is.na(a), is.na(b))) return(Inf)
  ok <- !is.na(a)
  if (!any(ok)) return(0)
  d <- abs(as.numeric(a[ok]) - as.numeric(b[ok]))
  d[as.numeric(a[ok]) == as.numeric(b[ok])] <- 0    # equal infinities (AICc of an ineligible model) are a zero difference
  max(d)
}

# Leaf-by-leaf comparison of two parsed JSON trees; returns a data.frame of mismatches (path, kind, a, b) and the
# largest numeric deviation (relative, or absolute near zero).
compare_trees <- function(a, b, path = "$", rel_tol = 1e-9, abs_tol = 1e-12) {
  bad <- list()
  maxdev <- 0
  walk <- function(x, y, p) {
    if (is.list(x) && is.list(y)) {
      nx <- names(x); ny <- names(y)
      if (!is.null(nx) || !is.null(ny)) {
        if (!setequal(nx, ny)) { bad[[length(bad) + 1L]] <<- list(path = p, kind = "keys", a = paste(sort(nx), collapse = ","), b = paste(sort(ny), collapse = ",")); return(invisible()) }
        for (k in nx) walk(x[[k]], y[[k]], paste0(p, ".", k))
      } else {
        if (length(x) != length(y)) { bad[[length(bad) + 1L]] <<- list(path = p, kind = "length", a = length(x), b = length(y)); return(invisible()) }
        for (i in seq_along(x)) walk(x[[i]], y[[i]], paste0(p, "[", i - 1L, "]"))
      }
    } else if (is.null(x) && is.null(y)) {
      invisible()
    } else if (is.numeric(x) && is.numeric(y) && length(x) == 1L && length(y) == 1L) {
      d <- abs(x - y); s <- max(abs(x), abs(y))
      dev <- if (s < 1e-6) d else d / s
      if (s < 1e-6 && d > abs_tol || s >= 1e-6 && dev > rel_tol) bad[[length(bad) + 1L]] <<- list(path = p, kind = "number", a = x, b = y)
      maxdev <<- max(maxdev, if (s < 1e-6) d else dev)
    } else if (!identical(x, y)) {
      bad[[length(bad) + 1L]] <<- list(path = p, kind = "value", a = format(x)[1], b = format(y)[1])
    }
    invisible()
  }
  walk(a, b, path)
  list(mismatches = if (length(bad)) do.call(rbind, lapply(bad, as.data.frame, stringsAsFactors = FALSE)) else data.frame(),
       max_dev = maxdev)
}

# Fixed configuration of the Giving Forecast refit pipeline (Phase 1: parity with giving-models-v1.json).
#
# Every number that changes a result lives here or in registry/*.json, so a run is reproducible from this file plus
# the pinned raw inputs. Paths are aliases resolved against RF$ROOTS; each root can be redirected with an environment
# variable so tests and other machines can point elsewhere:
#   RF_RUNTIME       runtime folder (raw vault, Rlib, runs); default %LOCALAPPDATA%\Mentat\forecast\refit, or
#                    <MENTAT_DATA_ROOT>\forecast\refit when MENTAT_DATA_ROOT is set (as mentat/R/paths.R does)
#   RF_RLIB          vendored package library (default <RUNTIME>\Rlib)
#   REFIT_OUT        folder the run outputs go to (default <RUNTIME>; the pod sets it so outputs are pulled back)
#   REFIT_CORES      worker count of the parallel batteries (see rf_parallel.R; 1 = serial)
#   RF_GLASSBOX_DIR  Glass Box event folder (default C:\Users\pgoff\.claude\glassbox\events)
#   RF_INPUT_ROOT    one folder that mirrors every pinned raw-input root (for another machine, e.g. a compute pod):
#                    <root>/LOO, <root>/CHE, <root>/SGSD, <root>/SGSA, <root>/RLIBX and
#                    <root>/House_Senate_Control_Forecasts_CSV.zip. The manifest hashes still pin every file.
#   RF_ROOT_<ALIAS>  one of LOO, CHE, SGSD, SGSA, ZIPC, RLIBX, INGEST, RFDATA, BUNDLE_V1, CYCLE; overrides RF_INPUT_ROOT and the local defaults
#   RF_CYCLE_MODEL   1 (default) = cycle-aware 50/50 model (phase, trajectory, inflation, BLEND, Senate Pres term); 0 = the bundle as published before it
#
# The pipeline reads inputs read-only, writes only under the runtime folder, and never touches the network.

`%||%` <- function(x, y) if (is.null(x) || length(x) == 0L) y else x

rf_env_path <- function(name, default) {
  v <- trimws(Sys.getenv(name, unset = ""))
  if (nzchar(v)) v else default
}

# The module load order (rf_config.R and rf_rng.R first; the rest only call each other at run time).
RF_MODULES <- c("rf_config.R", "rf_rng.R", "rf_io.R", "rf_parallel.R", "rf_glassbox.R", "rf_packages.R", "rf_registry.R", "rf_inputs.R",
                "rf_growth.R", "rf_opensecrets.R", "rf_political.R", "rf_lobbying.R", "rf_panels.R", "rf_fit.R",
                "rf_stepwise.R", "rf_robust.R", "rf_pool.R", "rf_intervals.R", "rf_backtest.R", "rf_select.R", "rf_phase.R", "rf_consistency.R", "rf_blend.R", "rf_v2model.R",
                "rf_report.R", "rf_bundle.R", "rf_bundle2.R", "rf_schema.R", "rf_gates.R", "rf_publish.R", "rf_agent.R", "rf_run.R", "rf_parity.R")

rf_source_all <- function(home = rf_home()) {
  for (m in RF_MODULES) {
    p <- file.path(home, "R", m)
    if (file.exists(p)) source(p, encoding = "UTF-8", local = FALSE)
  }
  invisible(TRUE)
}

# The refit folder (the one holding R/, registry/, tests/); set once by run_refit.R or the test helper.
rf_home <- function() {
  h <- get0(".RF_HOME", envir = globalenv(), inherits = FALSE)
  if (is.null(h)) stop("rf_home(): set .RF_HOME (the refit folder) before use", call. = FALSE)
  h
}

rf_runtime_default <- function() {
  data_root <- trimws(Sys.getenv("MENTAT_DATA_ROOT", unset = ""))
  if (nzchar(data_root)) return(file.path(data_root, "forecast", "refit"))
  lad <- trimws(Sys.getenv("LOCALAPPDATA", unset = ""))
  if (nzchar(lad)) return(file.path(lad, "Mentat", "forecast", "refit"))
  home <- trimws(Sys.getenv("HOME", unset = ""))          # non-Windows (compute pod): ~/.local/share
  if (nzchar(home)) return(file.path(home, ".local", "share", "Mentat", "forecast", "refit"))
  stop("LOCALAPPDATA and HOME are empty; set RF_RUNTIME to a writable local directory.", call. = FALSE)
}

# Resolved at call time (not at load time) so a test can change the environment variables between runs.
rf_runtime <- function() normalizePath(rf_env_path("RF_RUNTIME", rf_runtime_default()), winslash = "/", mustWork = FALSE)
rf_rlib_dir <- function() normalizePath(rf_env_path("RF_RLIB", file.path(rf_runtime(), "Rlib")), winslash = "/", mustWork = FALSE)
# Where run folders (panels, fits, review packet, receipt) are written: REFIT_OUT when set (the pod pulls that folder back), else the runtime.
rf_runs_dir <- function() file.path(normalizePath(rf_env_path("REFIT_OUT", rf_runtime()), winslash = "/", mustWork = FALSE), "runs")

rf_glassbox_dir <- function() {
  default <- if (nzchar(trimws(Sys.getenv("LOCALAPPDATA", unset = "")))) "C:/Users/pgoff/.claude/glassbox/events"
             else file.path(Sys.getenv("HOME"), ".claude", "glassbox", "events")
  rf_env_path("RF_GLASSBOX_DIR", default)
}

rf_roots <- function() {
  loo <- "C:/Users/pgoff/Documents/Codex/2026-09-26/loo"
  d <- list(
    LOO = loo,
    CHE = "C:/Users/pgoff/Documents/Codex/2026-09-27/che",
    SGSD = "C:/Users/pgoff/Mentat/sector_growth_study/data",
    SGSA = "C:/Users/pgoff/Mentat/sector_growth_study/archive/session_d613b40c_scratchpad",
    ZIPC = "C:/Users/pgoff/Downloads/House_Senate_Control_Forecasts_CSV.zip",
    RLIBX = file.path(loo, "work/lobbying_followup/Rlib"),
    INGEST = "C:/Users/pgoff/AppData/Local/Mentat/forecast/ingest/runs",        # OpenSecrets page captures of the feeds ingest (F09 for property and casualty)
    RFDATA = "C:/Users/pgoff/AppData/Local/Mentat/forecast/refit/data"          # derived raw inputs of this pipeline (tools/make_pc_ibis_current.R)
  )
  umbrella <- trimws(Sys.getenv("RF_INPUT_ROOT", unset = ""))
  if (nzchar(umbrella)) {
    d <- list(LOO = file.path(umbrella, "LOO"), CHE = file.path(umbrella, "CHE"), SGSD = file.path(umbrella, "SGSD"),
              SGSA = file.path(umbrella, "SGSA"), ZIPC = file.path(umbrella, "House_Senate_Control_Forecasts_CSV.zip"),
              RLIBX = file.path(umbrella, "RLIBX"), INGEST = file.path(umbrella, "INGEST"), RFDATA = file.path(umbrella, "RFDATA"))
  }
  d$BUNDLE_V1 <- file.path(dirname(dirname(rf_home())), "app", "data", "giving-models-v1.json")
  d$CYCLE <- file.path(rf_home(), "inputs", "cycle_model")          # the pinned IBISWorld paths and roster pace series of the cycle model travel with the code (also under RF_INPUT_ROOT)
  for (k in names(d)) d[[k]] <- rf_env_path(paste0("RF_ROOT_", k), d[[k]])
  d
}

# The cycle-aware 50/50 model (owner rulings 2026-10-07): RF_CYCLE_MODEL=1 (default) builds the phase / trajectory / inflation model, writes the bundle's `cycle_model`
# and gives the Senate share model the presidential-cycle term; RF_CYCLE_MODEL=0 reproduces the bundle published before it (parity switch).
rf_cycle_model_on <- function() !identical(trimws(Sys.getenv("RF_CYCLE_MODEL", unset = "1")), "0")

# "{LOO}/outputs/x.csv" -> the real path; a path with no alias is returned unchanged.
rf_expand <- function(path, roots = rf_roots()) {
  for (k in names(roots)) path <- gsub(paste0("{", k, "}"), roots[[k]], path, fixed = TRUE)
  if (grepl("{", path, fixed = TRUE) && grepl("^\\{[A-Z0-9_]+\\}", path)) stop("Unknown path alias in: ", path, call. = FALSE)
  path
}

RF <- list(
  VERSION = "refit-0.1.0-phase1",
  PIPELINE = "giving-forecast-refit",
  GLASSBOX_LANE = "claude",
  GLASSBOX_SOURCE = "giving-forecast-refit",

  # Frozen v1 model bundle this phase must reproduce (its sha256 is the parity target).
  V1_MODEL_VERSION = "2026-09-27-association-v1",
  V1_TEXT = list(built_on = "2026-09-27", currency = "2025 USD", inference = "Ordinary OLS; conditional on model selection",
                 validation = "Historical associations. Scenario intervals cover coefficients, not future outcomes."),
  V1_SOURCE_FILES = c("selected_models.csv", "analysis_case_panels.csv", "regression_panel.csv", "pac_selected_design.csv",
                      "year_effects__all71.csv", "money_panel.csv", "industry_forecast_slopes.csv", "nested_validation_metrics.csv",
                      "02_election_forecasting.xlsx"),
  V1_BUNDLE_SHA256 = "75c382af",   # prefix only; the full value is compared in test-02-inputs.R from the file itself

  # The byte copy of the SGS numpy-stream port (see rf_rng.R); a test pins its hash.
  RNG_SHA256 = "c9bcf7312016ec6e09319a647a9dbbc2624f39cb039a4b22a806c815dfef727e",

  # Statistics.
  BASE_YEAR = 2025L,
  SEED = 20260929,                 # production entropy root of the robustness draws (Codex stepwise used 20260927)
  STEPWISE_SEED = 20260927,        # analyze.py:  np.random.default_rng([20260927, 8061, counter])
  STEPWISE_TAG = 8061,
  MAX_ENUM = 300000,               # enumerate all Webb / Rademacher vectors when 6^G (2^G) is at most this
  B_RANDOM = 99999,                # random wild-bootstrap draws otherwise
  B_SELECT = 9999,                 # draws of the selection-repeated bootstrap (analyze.py DRAWS)
  CHUNK = 50000,                   # bootstrap matrices are built in blocks of this many draws
  TOL_AICC = 1e-9,                 # a model is accepted only if its AICc falls by more than this
  TOL_T = 1e-9,                    # relative slack of the "|t*| >= |t|" comparison (floating point)
  MIN_DF_OWN = 2L,                 # interaction fit needs this many residual df for its slopes, SEs and tau2
  LOBBY_TARGETS = 2014:2023,       # rolling-origin targets of the lobbying backtest (train on years < T)
  RULES = c("R0", "R1", "R2"),     # slope rules: pooled, own, DerSimonian-Laird blend toward the pooled slope
  RULE_MIN_GAIN = 0.05,            # a non-pooled rule must beat the pooled slope by this relative margin in EVERY design (owner ruling 2026-09-30)
  RULE_TIE_TOL = 1e-9,             # MAEs within this are a tie

  OPTIONAL_TERMS = c("Forecast", "G_prior", "G_current", "Pres"),

  # Sector ids of the three v1 industries with OpenSecrets history, in sorted (reference-first) order.
  V1_SECTORS = c("auto_light_duty_manufacturing", "commercial_banks", "credit_unions"),
  SECTOR_LABELS = c(commercial_banks = "Commercial banks", credit_unions = "Credit unions",
                    auto_light_duty_manufacturing = "Auto manufacturers")
)

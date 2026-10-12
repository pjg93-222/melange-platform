# Deterministic parallel map for the heavy batteries (robustness blocks, wild-bootstrap enumerations, folds, the stepwise grid).
#
# HARD RULE: a parallel run is BIT-IDENTICAL to the serial run. That holds because
#   * no unit of work draws from R's global random number generator; every random stream is built from explicit entropy
#     (rf_rng.R: numpy SeedSequence of c(seed, crc32(model), crc32(term), crc32(cluster)), or the case counter), so a unit's draws
#     do not depend on which worker runs it or when;
#   * results are returned in input order (mclapply / parLapply preserve it) and combined serially;
#   * a bootstrap is split into fixed-size blocks of draws whose hit counts are integers, summed (order-free).
# The worker count is REFIT_CORES (default detectCores() - 2 on Linux; 1 on Windows, where workers are slow to start, unless
# REFIT_CORES is set). REFIT_CORES=1 runs everything serially. Inside a worker REFIT_CORES is forced to 1 so maps never nest.

rf_cores <- function() {
  v <- trimws(Sys.getenv("REFIT_CORES", unset = ""))
  if (nzchar(v)) return(max(1L, suppressWarnings(as.integer(v)) %||% 1L, na.rm = TRUE))
  if (.Platform$OS.type == "windows") return(1L)
  max(1L, parallel::detectCores() - 2L, na.rm = TRUE)
}

# Start-up of a PSOCK worker (Windows / any platform without fork): same modules, same packages, same runtime folders.
.rf_worker_init <- function(home, libpaths) {
  .libPaths(libpaths)
  assign(".RF_HOME", home, envir = globalenv())
  source(file.path(home, "R", "rf_config.R"), encoding = "UTF-8")
  rf_source_all(home)
  suppressWarnings(rf_load_packages())
  Sys.setenv(REFIT_CORES = "1")
  invisible(TRUE)
}

# lapply() over X on REFIT_CORES workers; results in input order; an error in any unit stops the map with its message.
rf_pmap <- function(X, FUN, cores = rf_cores()) {
  n <- length(X)
  wrapped <- function(x) { Sys.setenv(REFIT_CORES = "1"); FUN(x) }
  if (cores <= 1L || n <= 1L) return(lapply(X, FUN))
  k <- min(cores, n)
  if (.Platform$OS.type == "unix") {
    # mclapply warns when a unit errors; the error itself is detected below and stops the map with its message
    res <- suppressWarnings(parallel::mclapply(X, wrapped, mc.cores = k, mc.preschedule = FALSE, mc.set.seed = FALSE))
  } else {
    cl <- parallel::makeCluster(k)
    on.exit(parallel::stopCluster(cl), add = TRUE)
    parallel::clusterCall(cl, .rf_worker_init, rf_home(), .libPaths())
    res <- parallel::parLapply(cl, X, wrapped)
  }
  bad <- which(vapply(res, function(r) inherits(r, "try-error") || inherits(r, "error"), logical(1)))
  if (length(bad)) stop("rf_pmap: unit ", bad[1], " failed: ", as.character(res[[bad[1]]]), call. = FALSE)
  res
}

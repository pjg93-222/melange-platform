# Publishing: write giving-models-vN.json (+ its receipt) and switch an atomic pointer, under a lock, with rollback. Never commits, never
# touches git, never overwrites v1 (the v1 file stays where it is and stays the rollback target).
#
#   <publish dir>/giving-models-v1.json               the frozen v1 bundle (kept)
#   <publish dir>/giving-models-v2.json               compatibility copy of the published v2 bundle
#   <publish dir>/versions/<sha256>/                 immutable bundle and receipt selected by the pointer
#   <publish dir>/giving-models-v2.receipt.json       its receipt
#   <publish dir>/giving-models-current.json          the pointer the console follows: {active, sha256, model_version, previous: {...}}
#   <runtime>/published/<model_version>/              archive of every publish: bundle, receipt and the pointer as it was before the switch
#   <runtime>/refit.lock/                             mutex directory (dir.create is atomic); stale after 6 h with a dead owner pid
#
# The publish dir is RF_PUBLISH_DIR, default mentat/app/data of the checkout holding this code. A publish happens only when the decision
# is PUBLISH. All comparisons of a bundle with another (another platform's BLAS differs at ~1e-14) are tolerance-based.

rf_publish_dir <- function() rf_env_path("RF_PUBLISH_DIR", file.path(dirname(dirname(rf_home())), "app", "data"))
rf_pointer_path <- function(dir = rf_publish_dir()) file.path(dir, "giving-models-current.json")
rf_archive_dir <- function(model_version) {
  if (!is.character(model_version) || length(model_version) != 1L || is.na(model_version) || !grepl("^[A-Za-z0-9][A-Za-z0-9._-]*$", model_version))
    stop("invalid model_version for publication archive", call. = FALSE)
  file.path(rf_runtime(), "published", model_version)
}
rf_lock_path <- function() file.path(rf_runtime(), "refit.lock")

rf_pid_alive <- function(pid) {
  pid <- suppressWarnings(as.integer(pid))
  if (is.na(pid) || pid <= 0L) return(FALSE)
  if (.Platform$OS.type == "windows") {
    out <- suppressWarnings(system2("tasklist", c("/FI", shQuote(paste0("PID eq ", pid)), "/NH"), stdout = TRUE, stderr = TRUE))
    return(any(grepl(paste0("\\b", pid, "\\b"), out)))
  }
  file.exists(file.path("/proc", pid))
}

# Acquire the refit lock (a directory). A lock older than `stale_hours` whose owner process is gone is broken (with a Glass Box event by the caller
# via the returned flag); any other existing lock stops the caller. -> list(path, broke_stale)
rf_lock_acquire <- function(stale_hours = 6, path = rf_lock_path()) {
  dir.create(dirname(path), recursive = TRUE, showWarnings = FALSE)
  broke <- FALSE
  if (dir.exists(path)) {
    owner <- tryCatch(jsonlite::fromJSON(file.path(path, "owner.json"), simplifyVector = FALSE), error = function(e) list())
    age_h <- as.numeric(difftime(Sys.time(), file.mtime(path), units = "hours"))
    if (age_h > stale_hours && !rf_pid_alive(owner$pid)) { unlink(path, recursive = TRUE); broke <- TRUE }
    else stop(sprintf("rf_lock_acquire: another refit holds the lock (pid %s, since %s)", owner$pid %||% "?", owner$acquired_utc %||% "?"), call. = FALSE)
  }
  if (!dir.create(path, showWarnings = FALSE)) stop("rf_lock_acquire: could not create the lock (another refit won the race)", call. = FALSE)
  writeLines(as.character(jsonlite::toJSON(list(pid = Sys.getpid(), acquired_utc = rf_now_utc(), host = Sys.info()[["nodename"]]), auto_unbox = TRUE)), file.path(path, "owner.json"))
  list(path = path, broke_stale = broke)
}

rf_lock_release <- function(path = rf_lock_path()) { unlink(path, recursive = TRUE); invisible(!dir.exists(path)) }

# Pointer readers and writers use the same selected bytes. Legacy installs without a pointer
# prefer v2 when present, matching the Python consumer. A malformed pointer never falls back.
rf_read_pointer <- function(dir = rf_publish_dir()) {
  p <- rf_pointer_path(dir)
  if (file.exists(p)) {
    ptr <- jsonlite::fromJSON(p, simplifyVector = FALSE)
    .rf_validate_pointer(ptr, dir)
    return(ptr)
  }
  for (name in c("giving-models-v2.json", "giving-models-v1.json")) {
    path <- file.path(dir, name)
    if (!file.exists(path)) next
    bundle <- jsonlite::fromJSON(path, simplifyVector = FALSE)
    ptr <- list(schema_version = 1L, active = name, sha256 = rf_sha256(path), model_version = bundle$model_version,
                model_schema_version = bundle$schema_version, switched_at_utc = NA_character_, previous = NULL)
    .rf_validate_pointer(ptr, dir)
    return(ptr)
  }
  NULL
}

.rf_validate_pointer <- function(ptr, dir) {
  scalar <- function(x) is.character(x) && length(x) == 1L && !is.na(x) && nzchar(x)
  if (!is.list(ptr) || !identical(ptr$schema_version, 1L)) stop("invalid publication pointer schema", call. = FALSE)
  if (!scalar(ptr$active) || !grepl("^(versions/[0-9a-f]{64}/)?giving-models-v[12]\\.json$", ptr$active)) stop("invalid publication pointer active path", call. = FALSE)
  if (!scalar(ptr$sha256) || !grepl("^[0-9a-f]{64}$", ptr$sha256) || !scalar(ptr$model_version)) stop("invalid publication pointer identity", call. = FALSE)
  target <- file.path(dir, ptr$active)
  if (!file.exists(target)) stop("publication pointer target is missing", call. = FALSE)
  root <- paste0(normalizePath(dir, winslash = "/", mustWork = TRUE), "/")
  if (!startsWith(normalizePath(target, winslash = "/", mustWork = TRUE), root)) stop("publication pointer target escapes model directory", call. = FALSE)
  if (!identical(rf_sha256(target), ptr$sha256)) stop("publication pointer SHA-256 mismatch", call. = FALSE)
  bundle <- jsonlite::fromJSON(target, simplifyVector = FALSE)
  expected <- as.integer(sub(".*-v([12])\\.json$", "\\1", ptr$active))
  if (!identical(bundle$model_version, ptr$model_version) || !identical(bundle$schema_version, expected) ||
      (!is.null(ptr$model_schema_version) && !identical(ptr$model_schema_version, expected))) stop("publication pointer model identity mismatch", call. = FALSE)
  if (expected == 2L && length(rf_validate_parsed(bundle))) stop("publication pointer bundle fails contract", call. = FALSE)
  invisible(bundle)
}

.rf_write_pointer <- function(ptr, dir) {
  ptr <- ptr[!vapply(ptr, is.null, logical(1))]
  .rf_validate_pointer(ptr, dir)
  rf_write_json_atomic(ptr, rf_pointer_path(dir))
}

# Never replace published bytes. Copy into a sibling temporary file, verify, then rename.
.rf_copy_immutable <- function(source, target) {
  sha <- rf_sha256(source)
  if (file.exists(target)) {
    if (!identical(rf_sha256(target), sha)) stop("immutable publication file already has different bytes: ", target, call. = FALSE)
    return(invisible(sha))
  }
  dir.create(dirname(target), recursive = TRUE, showWarnings = FALSE)
  tmp <- tempfile(pattern = ".publish-", tmpdir = dirname(target))
  on.exit(unlink(tmp), add = TRUE)
  if (!file.copy(source, tmp) || !identical(rf_sha256(tmp), sha) || !file.rename(tmp, target))
    stop("could not install immutable publication file: ", target, call. = FALSE)
  invisible(sha)
}

# Fixed-name files are compatibility copies only; pointer-aware readers never select them
# during a v2 publication. Update them only after the atomic commit, and report copy errors
# without falsely claiming that a successful pointer switch failed.
.rf_compatibility_copy <- function(source, target) {
  tryCatch({
    tmp <- tempfile(pattern = ".compat-", tmpdir = dirname(target))
    on.exit(unlink(tmp), add = TRUE)
    if (!file.copy(source, tmp) || !identical(rf_sha256(tmp), rf_sha256(source)) || !file.rename(tmp, target)) stop("copy failed")
    NULL
  }, error = function(e) paste("compatibility copy unchanged:", basename(target), conditionMessage(e)))
}

rf_publish <- function(bundle, gates, receipt = list(), dir = rf_publish_dir(), gb = NULL) {
  decision <- rf_decision(gates)
  mv <- bundle$model_version
  note <- function(kind, status, summary, detail = "") if (!is.null(gb)) gb$emit_event(kind, status, "publish", summary, detail)
  if (decision != "PUBLISH") {
    note("publish.hold", "failed", sprintf("not published: decision %s", decision), paste(gates$gate[gates$status %in% c("HOLD", "FAIL")], collapse = ", "))
    return(list(status = "not_published", decision = decision, reason = paste(gates$gate[gates$status %in% c("HOLD", "FAIL")], gates$detail[gates$status %in% c("HOLD", "FAIL")], sep = ": ", collapse = " | ")))
  }
  problems <- rf_validate_bundle(bundle)
  if (length(problems)) stop("rf_publish: bundle fails the contract schema: ", paste(utils::head(problems, 3), collapse = "; "), call. = FALSE)
  arch <- rf_archive_dir(mv)
  lock <- rf_lock_acquire()
  on.exit(rf_lock_release(lock$path), add = TRUE)
  note("publish.start", "running", sprintf("publishing %s", mv), if (lock$broke_stale) "a stale lock was broken" else "")
  dir.create(dir, recursive = TRUE, showWarnings = FALSE)
  before <- rf_read_pointer(dir)
  name <- sprintf("giving-models-v%d.json", bundle$schema_version)
  receipt_name <- sub("\\.json$", ".receipt.json", name)
  tmp <- tempfile(pattern = ".publish-", tmpdir = dir)
  on.exit(unlink(tmp), add = TRUE)
  sha <- rf_write_json_atomic(bundle, tmp)
  if (length(rf_validate_parsed(jsonlite::fromJSON(tmp, simplifyVector = FALSE))) || !identical(rf_sha256(tmp), sha)) stop("rf_publish: staged bundle did not verify", call. = FALSE)
  # A model version is an identity, not a mutable archive directory.
  archive_bundle <- file.path(arch, name)
  if (file.exists(archive_bundle) && !identical(rf_sha256(archive_bundle), sha)) stop("rf_publish: model_version already identifies different bytes", call. = FALSE)
  active <- paste("versions", sha, name, sep = "/")
  target <- file.path(dir, active)
  .rf_copy_immutable(tmp, target)
  .rf_copy_immutable(tmp, archive_bundle)
  receipt$published_to <- list(dir = dir, file = active, sha256 = sha, model_version = mv)
  receipt$decision <- decision
  # The archived first receipt remains paired with this exact bundle on repeated publishes.
  archive_receipt <- file.path(arch, receipt_name)
  if (!file.exists(archive_receipt)) rf_write_json_atomic(receipt, archive_receipt)
  if (!identical(jsonlite::fromJSON(archive_receipt, simplifyVector = FALSE)$published_to$sha256, sha)) stop("rf_publish: archive receipt SHA-256 mismatch", call. = FALSE)
  version_receipt <- file.path(dirname(target), receipt_name)
  .rf_copy_immutable(archive_receipt, version_receipt)
  rsha <- rf_sha256(version_receipt)
  if (!is.null(before) && !file.exists(file.path(arch, "pointer-before.json"))) rf_write_json_atomic(before, file.path(arch, "pointer-before.json"))
  ptr <- list(schema_version = 1L, active = active, sha256 = sha, model_version = mv, model_schema_version = bundle$schema_version, switched_at_utc = rf_now_utc(),
              previous = if (!is.null(before)) list(active = before$active, sha256 = before$sha256, model_version = before$model_version) else NULL)
  .rf_write_pointer(ptr, dir)  # the only operation that changes the active model
  warnings <- Filter(Negate(is.null), list(.rf_compatibility_copy(target, file.path(dir, name)), .rf_compatibility_copy(version_receipt, file.path(dir, receipt_name))))
  note("publish.done", "succeeded", sprintf("published %s -> %s", mv, active), paste(c(sha, unlist(warnings)), collapse = "; "))
  list(status = "published", decision = decision, model_version = mv, file = target, sha256 = sha, receipt_sha256 = rsha, pointer = ptr, archive = arch,
       broke_stale = lock$broke_stale, warnings = warnings)
}

# Restore archived, validated immutable bytes, or the retained v1. The previous active target
# is never overwritten; only the pointer changes, so in-flight readers can finish safely.
rf_rollback <- function(model_version, dir = rf_publish_dir(), gb = NULL) {
  note <- function(kind, status, summary, detail = "") if (!is.null(gb)) gb$emit_event(kind, status, "rollback", summary, detail)
  arch <- rf_archive_dir(model_version)
  lock <- rf_lock_acquire()
  on.exit(rf_lock_release(lock$path), add = TRUE)
  before <- rf_read_pointer(dir)
  v1 <- file.path(dir, "giving-models-v1.json")
  copies <- list()
  if (dir.exists(arch) && length(list.files(arch, pattern = "^giving-models-v2\\.json$"))) {
    name <- "giving-models-v2.json"
    archived <- file.path(arch, name)
    bundle <- jsonlite::fromJSON(archived, simplifyVector = FALSE)
    if (!identical(bundle$model_version, model_version) || length(rf_validate_parsed(bundle))) stop("rf_rollback: archived bundle fails identity or contract", call. = FALSE)
    sha <- rf_sha256(archived)
    receipt_name <- sub("\\.json$", ".receipt.json", name)
    archive_receipt <- file.path(arch, receipt_name)
    if (!file.exists(archive_receipt) || !identical(jsonlite::fromJSON(archive_receipt, simplifyVector = FALSE)$published_to$sha256, sha))
      stop("rf_rollback: archive receipt SHA-256 mismatch", call. = FALSE)
    active <- paste("versions", sha, name, sep = "/")
    target <- file.path(dir, active)
    .rf_copy_immutable(archived, target)
    version_receipt <- file.path(dirname(target), receipt_name)
    .rf_copy_immutable(archive_receipt, version_receipt)
    copies <- list(c(target, file.path(dir, name)), c(version_receipt, file.path(dir, receipt_name)))
    schema <- 2L
  } else if (file.exists(v1) && identical(jsonlite::fromJSON(v1, simplifyVector = FALSE)$model_version, model_version)) {
    active <- "giving-models-v1.json"; sha <- rf_sha256(v1); schema <- 1L
  } else stop("rf_rollback: no archived publish and no kept bundle for model_version ", model_version, call. = FALSE)
  ptr <- list(schema_version = 1L, active = active, sha256 = sha, model_version = model_version, model_schema_version = schema, switched_at_utc = rf_now_utc(),
              previous = if (!is.null(before)) list(active = before$active, sha256 = before$sha256, model_version = before$model_version) else NULL, rolled_back = TRUE)
  .rf_write_pointer(ptr, dir)
  warnings <- Filter(Negate(is.null), lapply(copies, function(pair) .rf_compatibility_copy(pair[1], pair[2])))
  note("rollback.done", "succeeded", sprintf("rolled back to %s (%s)", model_version, active), sha)
  list(status = "rolled_back", model_version = model_version, file = active, sha256 = sha, pointer = ptr, warnings = warnings)
}

# Tolerance-based comparison of two parsed bundles (e.g. a pod-built and a Windows-built one): every numeric leaf within rel_tol (abs_tol near zero),
# strings and integers exact, keys equal. -> data.frame(path, a, b, dev) of the leaves beyond tolerance (empty = equivalent); attr "max_dev".
rf_bundle_diff <- function(a, b, rel_tol = 1e-9, abs_tol = 1e-12, ignore = c("built_at_utc", "model_version", "run_id", "code_sha256", "R_version", "packages", "note", "as_of")) {
  rows <- list(); maxdev <- 0
  walk <- function(x, y, p, key) {
    if (key %in% ignore) return(invisible())
    if (is.list(x) && is.list(y)) {
      nx <- names(x); ny <- names(y)
      if (!is.null(nx) || !is.null(ny)) {
        for (k in setdiff(union(nx, ny), intersect(nx, ny))) rows[[length(rows) + 1L]] <<- data.frame(path = paste0(p, ".", k), a = NA_character_, b = "missing on one side", dev = Inf, stringsAsFactors = FALSE)
        for (k in intersect(nx, ny)) walk(x[[k]], y[[k]], paste0(p, ".", k), k)
      } else if (length(x) != length(y)) rows[[length(rows) + 1L]] <<- data.frame(path = p, a = as.character(length(x)), b = as.character(length(y)), dev = Inf, stringsAsFactors = FALSE)
      else for (i in seq_along(x)) walk(x[[i]], y[[i]], paste0(p, "[", i - 1L, "]"), "")
    } else if (is.null(x) && is.null(y)) {
      invisible()
    } else if (is.numeric(x) && is.numeric(y) && length(x) == 1L && length(y) == 1L) {
      d <- abs(x - y); s <- max(abs(x), abs(y)); dev <- if (s < 1e-6) d else d / s
      maxdev <<- max(maxdev, dev)
      if ((s < 1e-6 && d > abs_tol) || (s >= 1e-6 && dev > rel_tol)) rows[[length(rows) + 1L]] <<- data.frame(path = p, a = format(x, digits = 15), b = format(y, digits = 15), dev = dev, stringsAsFactors = FALSE)
    } else if (!identical(x, y)) {
      rows[[length(rows) + 1L]] <<- data.frame(path = p, a = format(x)[1], b = format(y)[1], dev = Inf, stringsAsFactors = FALSE)
    }
    invisible()
  }
  walk(a, b, "$", "")
  out <- if (length(rows)) do.call(rbind, rows) else data.frame(path = character(), a = character(), b = character(), dev = numeric())
  attr(out, "max_dev") <- maxdev
  out
}

# R, BLAS and LAPACK of this session: stated in every receipt (a pod's ATLAS BLAS and a laptop's reference BLAS differ at ~1e-14).
rf_platform_info <- function() {
  si <- utils::sessionInfo()
  list(R_version = R.version.string, platform = R.version$platform, os = si$running %||% R.version$os, blas = si$BLAS %||% NA_character_, lapack = si$LAPACK %||% NA_character_,
       cores = rf_cores(), node = Sys.info()[["nodename"]], note = "numerical results agree across platforms to ~1e-13; compare bundles with rf_bundle_diff(), never byte equality")
}

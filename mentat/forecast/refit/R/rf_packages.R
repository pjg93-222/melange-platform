# Package loading without network installs. clubSandwich exists on this machine only inside a dated Codex work folder
# (RLIBX); rf_setup_rlib() copies it (and sandwich, for a version lock) once into the pipeline-owned library
# <RUNTIME>\Rlib, outside git, and writes registry/rlib_manifest.json with the sha256 of each package's identifying
# files. rf_load_packages() puts that library first on .libPaths(), loads every required package, checks its version
# against the pins of registry/models.json, and refuses to fall back silently when clubSandwich is missing.

RF_VENDORED <- c("clubSandwich", "sandwich")

rf_rlib_manifest_path <- function() file.path(rf_home(), "registry", "rlib_manifest.json")

# sha256 of the files that identify an installed package build (relative paths as names).
rf_rlib_hash_tree <- function(pkg_dir) {
  rdb <- list.files(file.path(pkg_dir, "R"), pattern = "\\.(rdb|rdx)$")
  rel <- c("DESCRIPTION", "NAMESPACE", "Meta/package.rds", if (length(rdb)) file.path("R", rdb))
  paths <- file.path(pkg_dir, rel)
  ok <- file.exists(paths)
  out <- vapply(paths[ok], rf_sha256, "")
  stats::setNames(unname(out), rel[ok])
}

RF_PLATFORM <- function() R.version$platform

# The OS token of a DESCRIPTION Built field ("R 4.6.1; ; 2026-09-16 00:22:32 UTC; windows" -> "windows"; a Linux build says "unix").
.rf_built_os <- function(built) trimws(utils::tail(strsplit(built, ";", fixed = TRUE)[[1]], 1L))

# The manifest entry of one package for the library at hand: the platform the library was BUILT for (`built` = its DESCRIPTION Built field), which is not always the
# platform R runs on (the container loads the Windows-built vendored library). Several entries of one OS: the one named like R's platform, else the first. NULL when not
# recorded; without a Built field, R's own platform decides.
.rf_man_entry <- function(man, p, built = NA_character_) {
  plats <- man[[p]]$platforms
  if (is.na(built)) return(plats[[RF_PLATFORM()]])
  os <- .rf_built_os(built)
  hit <- Filter(function(e) identical(.rf_built_os(e$built), os), plats)
  if (!length(hit)) return(NULL)
  hit[[if (RF_PLATFORM() %in% names(hit)) RF_PLATFORM() else 1L]]
}

.rf_rlib_record <- function(target, packages, manifest_path, merge_into = NULL) {
  man <- merge_into %||% list(schema_version = 2L, note = "sha256 of the vendored package files per platform (the key is R.version$platform of the machine that recorded it; the entry used for a library is the one whose Built OS, windows or unix, is the library's own, so the Windows-built vendored library verifies in the Linux container too); RUNTIME/Rlib is outside git. A library built for an OS without an entry is reported as unrecorded (version still checked); record it with tools/record_rlib_manifest.R.", packages = list())
  for (p in packages) {
    d <- read.dcf(file.path(target, p, "DESCRIPTION"), c("Version", "Built"))
    man$packages[[p]]$version <- unname(d[1, "Version"])
    man$packages[[p]]$platforms[[RF_PLATFORM()]] <- list(built = unname(d[1, "Built"]), files = as.list(rf_rlib_hash_tree(file.path(target, p))))
  }
  rf_write_json_atomic(man, manifest_path)
  invisible(man)
}

# Copy the vendored packages from `source` into `target` once (when `target` lacks them). An existing copy is never overwritten.
# A missing manifest is created for the current platform. Returns the verification table.
rf_setup_rlib <- function(source = rf_roots()$RLIBX, target = rf_rlib_dir(), packages = RF_VENDORED,
                          manifest_path = rf_rlib_manifest_path()) {
  dir.create(target, recursive = TRUE, showWarnings = FALSE)
  for (p in packages) {
    src <- file.path(source, p)
    dst <- file.path(target, p)
    if (!dir.exists(src) && !dir.exists(dst)) stop("rf_setup_rlib: ", p, " not found in ", source, call. = FALSE)
    if (!dir.exists(dst)) {
      ok <- file.copy(src, target, recursive = TRUE, copy.mode = FALSE)
      if (!all(ok)) stop("rf_setup_rlib: copying ", p, " failed", call. = FALSE)
    }
  }
  if (!file.exists(manifest_path)) .rf_rlib_record(target, packages, manifest_path)
  rf_verify_rlib(target, manifest_path)
}

# Record the current platform's package hashes (used once per platform; the result is merged into registry/rlib_manifest.json).
rf_record_rlib_platform <- function(target = rf_rlib_dir(), packages = RF_VENDORED, manifest_path = rf_rlib_manifest_path(), out_path = manifest_path) {
  man <- if (file.exists(manifest_path)) jsonlite::fromJSON(manifest_path, simplifyVector = FALSE) else NULL
  .rf_rlib_record(target, packages, out_path, merge_into = man)
}

# Verification table per recorded file: package, file, expected, actual, status in ok / mismatch / missing / unrecorded_platform,
# and ok (TRUE when the file matches, or when the platform has no recorded hashes and only the version can be checked).
rf_verify_rlib <- function(target = rf_rlib_dir(), manifest_path = rf_rlib_manifest_path()) {
  man <- jsonlite::fromJSON(manifest_path, simplifyVector = FALSE)$packages
  rows <- list()
  for (p in names(man)) {
    here <- dir.exists(file.path(target, p))
    dcf <- if (here) read.dcf(file.path(target, p, "DESCRIPTION"), c("Version", "Built")) else NULL
    ver <- if (here) unname(dcf[1, "Version"]) else NA_character_
    ent <- .rf_man_entry(man, p, if (here) unname(dcf[1, "Built"]) else NA_character_)
    have <- if (here) rf_rlib_hash_tree(file.path(target, p)) else character()
    if (is.null(ent)) {
      rows[[length(rows) + 1L]] <- data.frame(package = p, file = "DESCRIPTION (Version)", expected = man[[p]]$version, actual = ver,
                                              status = "unrecorded_platform", ok = identical(ver, man[[p]]$version), stringsAsFactors = FALSE)
      next
    }
    for (f in names(ent$files)) {
      exp <- ent$files[[f]]
      act <- if (f %in% names(have)) unname(have[[f]]) else NA_character_
      st <- if (is.na(act)) "missing" else if (identical(act, exp)) "ok" else "mismatch"
      rows[[length(rows) + 1L]] <- data.frame(package = p, file = f, expected = exp, actual = act, status = st, ok = st == "ok", stringsAsFactors = FALSE)
    }
  }
  do.call(rbind, rows)
}

# Prepend the vendored library, load each package, compare versions with the pins. Returns
# data.frame(package, version, pinned, ok, lib). Fails (stop) when a vendored package is missing or a pin differs.
rf_load_packages <- function(required = c("jsonlite", "readxl", "sandwich", "clubSandwich"), pins = NULL) {
  rlib <- rf_rlib_dir()
  if (dir.exists(rlib)) .libPaths(unique(c(rlib, .libPaths())))
  if (getRversion() < "4.6.0") stop("rf_load_packages: R >= 4.6.0 is required (found ", as.character(getRversion()), ")", call. = FALSE)
  pins <- pins %||% rf_read_json(file.path(rf_home(), "registry", "models.json"))$packages
  rows <- list()
  for (p in required) {
    loaded <- suppressWarnings(requireNamespace(p, quietly = TRUE))
    if (!loaded) stop("rf_load_packages: required package ", p, " is unavailable", call. = FALSE)
    v <- utils::packageDescription(p)$Version   # the DESCRIPTION text ("3.1-3"), not the normalised "3.1.3"
    pin <- pins[[p]] %||% NA_character_
    ok <- is.na(pin) || identical(v, pin)
    rows[[length(rows) + 1L]] <- data.frame(package = p, version = v, pinned = pin, ok = ok,
                                            lib = dirname(find.package(p)), stringsAsFactors = FALSE)
    if (!ok) stop("rf_load_packages: ", p, " is ", v, " but the registry pins ", pin, call. = FALSE)
    if (p %in% RF_VENDORED && dir.exists(rlib) && !identical(normalizePath(dirname(find.package(p)), winslash = "/"), rlib)) {
      stop("rf_load_packages: ", p, " loaded from ", dirname(find.package(p)), " not from the vendored library ", rlib, call. = FALSE)
    }
  }
  do.call(rbind, rows)
}

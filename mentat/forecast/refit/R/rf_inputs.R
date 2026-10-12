# Raw-input manifest and the content-addressed vault (registry/inputs.json).
# Each entry pins one raw input by sha256. On first sight the pipeline copies a file into <RUNTIME>\raw\<sha256>\<name>
# and thereafter reads the vault copy, so a clean-up of Downloads / Temp / a Codex work folder cannot break a refit.
# A pinned input whose sha256 changes FAILS gate G1 until Paul regenerates the manifest for a new vintage.

rf_read_manifest <- function(path = file.path(rf_home(), "registry", "inputs.json")) {
  m <- rf_read_json(path)
  df <- do.call(rbind, lapply(m$inputs, function(e) data.frame(
    role = e$role, path = e$path, member = e$member %||% NA_character_, sheet = e$sheet %||% NA_character_,
    sha256 = e$sha256, bytes = e$bytes %||% NA_integer_, pinned = isTRUE(e$pinned), purpose = e$purpose %||% "raw",
    vintage = e$vintage %||% "", supplied_by = e$supplied_by %||% "", stringsAsFactors = FALSE)))
  df
}

# Record of one file as read: role, path, member, sheet, sha256, bytes, mtime (UTC).
rf_input_record <- function(role, path, member = NA_character_, sheet = NA_character_) {
  list(role = role, path = path, member = member, sheet = sheet, sha256 = rf_sha256(path), bytes = as.integer(file.size(path)),
       mtime_utc = format(file.mtime(path), "%Y-%m-%dT%H:%M:%SZ", tz = "UTC"))
}

rf_vault_dir <- function() file.path(rf_runtime(), "raw")

rf_vault_path <- function(sha256, name) file.path(rf_vault_dir(), sha256, name)

# Copy `path` into the vault (idempotent); returns the vault path.
rf_vault_put <- function(path, sha256 = rf_sha256(path)) {
  dst <- rf_vault_path(sha256, basename(path))
  if (file.exists(dst) && identical(rf_sha256(dst), sha256)) return(dst)
  dir.create(dirname(dst), recursive = TRUE, showWarnings = FALSE)
  if (!file.copy(path, dst, overwrite = TRUE, copy.mode = FALSE)) stop("rf_vault_put: cannot copy ", path, call. = FALSE)
  if (!identical(rf_sha256(dst), sha256)) stop("rf_vault_put: vault copy of ", path, " is not byte-equal", call. = FALSE)
  dst
}

# Resolve every manifest entry to a hash-checked vault copy.
# -> data.frame(role, path, vault_path, expected, actual, status, purpose); status in ok, ok_from_vault, missing, hash_mismatch
rf_resolve_inputs <- function(manifest = rf_read_manifest(), roots = rf_roots(), vault = TRUE) {
  rows <- lapply(seq_len(nrow(manifest)), function(i) {
    e <- manifest[i, ]
    src <- rf_expand(e$path, roots)
    name <- basename(src)
    vp <- rf_vault_path(e$sha256, name)
    if (file.exists(src)) {
      act <- rf_sha256(src)
      if (!identical(act, e$sha256)) {
        return(data.frame(role = e$role, path = src, vault_path = NA_character_, expected = e$sha256, actual = act,
                          status = "hash_mismatch", purpose = e$purpose, stringsAsFactors = FALSE))
      }
      vpath <- if (vault) rf_vault_put(src, act) else src
      return(data.frame(role = e$role, path = src, vault_path = vpath, expected = e$sha256, actual = act, status = "ok",
                        purpose = e$purpose, stringsAsFactors = FALSE))
    }
    if (file.exists(vp) && identical(rf_sha256(vp), e$sha256)) {
      return(data.frame(role = e$role, path = src, vault_path = vp, expected = e$sha256, actual = e$sha256,
                        status = "ok_from_vault", purpose = e$purpose, stringsAsFactors = FALSE))
    }
    data.frame(role = e$role, path = src, vault_path = NA_character_, expected = e$sha256, actual = NA_character_,
               status = "missing", purpose = e$purpose, stringsAsFactors = FALSE)
  })
  do.call(rbind, rows)
}

# Gate G1: every manifest input present, pinned hashes equal, vault copy byte-equal.
rf_gate_inputs <- function(inputs) {
  bad <- inputs[!inputs$status %in% c("ok", "ok_from_vault"), ]
  vault_ok <- vapply(seq_len(nrow(inputs)), function(i) {
    vp <- inputs$vault_path[i]
    !is.na(vp) && file.exists(vp) && identical(rf_sha256(vp), inputs$expected[i])
  }, logical(1))
  data.frame(gate = "G1 inputs", status = if (nrow(bad) == 0L && all(vault_ok)) "PASS" else "FAIL",
             detail = if (nrow(bad)) paste(sprintf("%s: %s", bad$role, bad$status), collapse = "; ") else
               sprintf("%d inputs present, hashes pinned, vault copies byte-equal", nrow(inputs)), stringsAsFactors = FALSE)
}

# The vault path of one role (stops when the role is unknown or unresolved).
rf_input_path <- function(inputs, role) {
  i <- match(role, inputs$role)
  if (is.na(i)) stop("rf_input_path: role not in the manifest: ", role, call. = FALSE)
  if (is.na(inputs$vault_path[i])) stop("rf_input_path: input ", role, " is ", inputs$status[i], call. = FALSE)
  inputs$vault_path[i]
}

# The input records (role, path, sha256, ...) behind a set of roles, for attr(x, "inputs") and the receipt.
rf_input_records <- function(inputs, roles) {
  lapply(roles, function(r) {
    i <- match(r, inputs$role)
    list(role = r, path = inputs$path[i], vault_path = inputs$vault_path[i], sha256 = inputs$actual[i])
  })
}

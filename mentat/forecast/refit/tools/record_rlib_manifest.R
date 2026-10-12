# Record the sha256 of the vendored packages (clubSandwich, sandwich) for THIS platform into a manifest file; used once per
# platform (for example on the compute pod, whose Linux builds differ from the Windows ones). Merge the platform entry into
# registry/rlib_manifest.json by hand or with the same function; nothing in the repo is written unless --out points there.
#   Rscript tools/record_rlib_manifest.R [--out <path>]      default out: <REFIT_OUT or cwd>/rlib_manifest_<platform>.json
fa <- grep("^--file=", commandArgs(trailingOnly = FALSE), value = TRUE)
here <- if (length(fa)) dirname(normalizePath(sub("^--file=", "", fa[1]), winslash = "/")) else getwd()
assign(".RF_HOME", normalizePath(file.path(here, ".."), winslash = "/"), envir = globalenv())
source(file.path(.RF_HOME, "R", "rf_config.R"))
rf_source_all()
args <- commandArgs(trailingOnly = TRUE)
out <- if ("--out" %in% args) args[match("--out", args) + 1L] else
  file.path(rf_env_path("REFIT_OUT", getwd()), paste0("rlib_manifest_", R.version$platform, ".json"))
rf_record_rlib_platform(out_path = out)
cat("recorded", R.version$platform, "->", out, "\n")
print(rf_verify_rlib(manifest_path = out)[, c("package", "file", "status")])

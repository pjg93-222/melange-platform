# Giving Forecast feeds: OpenSecrets and IBISWorld ingest (FEEDS_SPEC 7.1).
#
#   Rscript run_ingest.R --feed ibisworld                        parse the IBISWorld reports the owner dropped in the inbox
#   Rscript run_ingest.R --feed opensecrets-fetch --industry property_casualty   the permission-gated page fetcher
#   Rscript run_ingest.R --feed opensecrets-pages                parse OpenSecrets pages saved by hand
#   Rscript run_ingest.R --feed opensecrets-bulk                 bulk files (verified and calibrated before any use)
#   Rscript run_ingest.R --feed calibrate                        only the calibration gate
#   options: [--industry <id>] [--dry-run] [--run-id <id>] [--refresh]
#
# See README.md for what to download and where to drop it. Exit codes: 0 ok / waiting for an owner file, 2 files need review,
# 3 stopped (blocked, gate closed, verification or calibration failed), 1 error.

fa <- grep("^--file=", commandArgs(trailingOnly = FALSE), value = TRUE)
here <- if (length(fa)) dirname(normalizePath(sub("^--file=", "", fa[1]), winslash = "/")) else normalizePath(getwd(), winslash = "/")
if (!requireNamespace("xml2", quietly = TRUE)) .libPaths(c("C:/Users/pgoff/AppData/Local/R/win-library/4.6", .libPaths()))
assign(".FI_HOME", here, envir = globalenv())
source(file.path(here, "R", "fi_config.R"), encoding = "UTF-8")
fi_source_all(here)
quit(status = fi_main(), save = "no")

# Regenerate registry/frozen_os_pages.json: the inventory of every page in the 2026-09-27 OpenSecrets captures (code, cycle, kind, file,
# sha256, url, retrieval time). Holds no page content. Run it only when the capture folders change; the fetcher reads the committed file.
#   & "C:\Program Files\R\R-4.6.0\bin\Rscript.exe" tools\make_frozen_inventory.R
if (!requireNamespace("xml2", quietly = TRUE)) .libPaths(c("C:/Users/pgoff/AppData/Local/R/win-library/4.6", .libPaths()))
fa <- grep("^--file=", commandArgs(trailingOnly = FALSE), value = TRUE)
here <- dirname(dirname(normalizePath(sub("^--file=", "", fa[1]), winslash = "/")))
assign(".FI_HOME", here, envir = globalenv())
source(file.path(here, "R", "fi_config.R"), encoding = "UTF-8")
fi_source_all(here)
root <- rf_roots()$LOO
inv <- fi_os_scan_captures(root)
if (is.null(inv) || !nrow(inv)) stop("no captures found under ", root)
p <- file.path(here, "registry", "frozen_os_pages.json")
sha <- fi_os_write_inventory(inv, p, generated_from = "LOO outputs/opensecrets_baseline and outputs/opensecrets_partisanship (captured 2026-09-27)")
cat(sprintf("%d pages -> %s (sha256 %s)\n", nrow(inv), p, sha))
print(table(inv$code, inv$kind))

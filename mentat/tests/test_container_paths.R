script <- sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[[1L]])
root <- normalizePath(file.path(dirname(script), ".."), winslash = "/")
# R/paths.R adds one library to .libPaths() when data.table cannot be found: MENTAT_R_LIBRARY, else the Windows library.
# requireNamespace is masked so the fallback always runs; .libPaths() keeps only directories that exist.
requireNamespace <- function(...) FALSE
prior <- Sys.getenv("MENTAT_R_LIBRARY", unset = NA_character_)
restore <- function() if (is.na(prior)) Sys.unsetenv("MENTAT_R_LIBRARY") else Sys.setenv(MENTAT_R_LIBRARY = prior)
first_library <- function() { source(file.path(root, "R", "paths.R")); normalizePath(.libPaths()[[1L]], winslash = "/") }

container <- file.path(tempdir(), "container-r-library"); dir.create(container)
Sys.setenv(MENTAT_R_LIBRARY = container)
stopifnot(identical(first_library(), normalizePath(container, winslash = "/")))

windows <- "C:/Users/pgoff/AppData/Local/R/win-library/4.6"
if (.Platform$OS.type == "windows" && dir.exists(windows)) {
  Sys.unsetenv("MENTAT_R_LIBRARY")
  stopifnot(identical(first_library(), windows))
  Sys.setenv(MENTAT_R_LIBRARY = "  ")      # an empty value is not a location
  stopifnot(identical(first_library(), windows))
}
restore()
cat("PASS: MENTAT_R_LIBRARY moves the R/paths.R fallback library; the Windows library is the default\n")

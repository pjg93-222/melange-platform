# Hygiene: the pipeline code never reaches for the network or installs anything (spec 3.1, 4.9).

test_that("no pipeline source uses install.packages, download.file, url(), curl, httr, or a shell call with a URL", {
  files <- c(list.files(file.path(.RF_HOME, "R"), pattern = "\\.R$", full.names = TRUE), file.path(.RF_HOME, "run_refit.R"))
  files <- files[file.exists(files)]
  expect_gt(length(files), 5L)
  banned <- c("install\\.packages", "download\\.file", "download\\.packages", "(^|[^A-Za-z0-9_.])url\\(", "curl::", "httr", "socketConnection",
              "RCurl", "utils::available\\.packages", "system2?\\([^)]*https?://", "Sys\\.setenv\\([^)]*proxy",
              "readLines\\(\\s*[\"']https?://", "read\\.csv\\(\\s*[\"']https?://")
  for (f in files) {
    if (basename(f) == "rf_rng.R") next   # byte copy of the SGS port; it holds no I/O
    txt <- readLines(f, warn = FALSE, encoding = "UTF-8")
    txt <- txt[!grepl("^\\s*#", txt)]
    for (pat in banned) {
      hit <- grep(pat, txt, perl = TRUE)
      expect_length(hit, 0L)
      if (length(hit)) message(basename(f), ": ", pat, " -> ", txt[hit[1]])
    }
  }
})

test_that("the scanner itself flags each banned construct (negative control)", {
  expect_length(grep("install\\.packages", "install.packages('a')"), 1L)
  expect_length(grep("(^|[^A-Za-z0-9_.])url\\(", "u <- url('http://x')", perl = TRUE), 1L)
  expect_length(grep("(^|[^A-Za-z0-9_.])url\\(", "source_url(x)", perl = TRUE), 0L)
  expect_length(grep("system2?\\([^)]*https?://", "system('curl https://x')", perl = TRUE), 1L)
})

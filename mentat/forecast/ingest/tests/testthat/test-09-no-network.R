# Hygiene (FEEDS_SPEC 8 test 9): only fi_os_fetch.R may use the network, nothing may reach the publisher of the IBISWorld reports,
# and no file may use browser automation. The scan reads the source of every R file of the ingest and the CLI, comments removed.

code_lines <- function(f) {
  x <- readLines(f, warn = FALSE, encoding = "UTF-8")
  x <- x[!grepl("^\\s*#", x)]
  sub("\\s+#[^\"']*$", "", x)
}
src_files <- function() c(list.files(file.path(.FI_HOME, "R"), pattern = "\\.R$", full.names = TRUE), file.path(.FI_HOME, "run_ingest.R"))

NET <- c("\\bcurl\\b", "\\bhttr\\b", "(^|[^A-Za-z0-9_.])url\\(", "download\\.file", "browseURL", "(^|[^A-Za-z0-9_.])system2?\\(", "socketConnection")

test_that("network, download and shell constructs appear only in fi_os_fetch.R", {
  files <- src_files()
  expect_gt(length(files), 10L)
  expect_true(any(basename(files) == "fi_os_fetch.R"))
  for (f in files) {
    if (basename(f) == "fi_os_fetch.R") next
    txt <- code_lines(f)
    for (pat in NET) {
      hit <- grep(pat, txt, perl = TRUE)
      expect_length(hit, 0L)
      if (length(hit)) message(basename(f), ": ", pat, " -> ", txt[hit[1]])
    }
  }
  # and the fetch file really is the one that uses it (the scanner sees it)
  expect_gt(length(grep("\\bcurl\\b", code_lines(file.path(.FI_HOME, "R", "fi_os_fetch.R")), perl = TRUE)), 0L)
})

test_that("the publisher's host is never spelled in code: no R file contains it, and the PDF parser holds it only as an escaped regex", {
  host_txt <- paste0("ibis", "world", ".com")
  for (f in src_files()) {
    txt <- code_lines(f)
    expect_length(grep(host_txt, txt, fixed = TRUE), 0L)
  }
  pdf_txt <- code_lines(file.path(.FI_HOME, "R", "fi_ibis_pdf.R"))
  hits <- grep("ibisworld", pdf_txt, fixed = TRUE, value = TRUE)
  hits <- hits[grepl("\\.com", hits)]
  expect_gt(length(hits), 0L)                                          # the Key Statistics URL text regex is there
  expect_true(all(grepl("ibisworld\\\\.com", hits, fixed = TRUE)))     # ... as a regex (escaped dot), over PDF text
  # no IBISWorld code path imports or calls anything from the fetcher
  for (f in src_files()) if (grepl("fi_ibis", basename(f))) expect_length(grep("fi_os_fetch|fi_curl_transport|fi_robots", code_lines(f)), 0L)
})

test_that("no file references browser automation", {
  pats <- "playwright|chromote|selenium|rselenium|headless|webdriver|puppeteer"
  for (f in src_files()) expect_length(grep(pats, code_lines(f), ignore.case = TRUE), 0L)
})

test_that("the fetcher carries no circumvention constructs: no proxy, no cookie jar, no header spoofing, no script execution", {
  txt <- code_lines(file.path(.FI_HOME, "R", "fi_os_fetch.R"))
  for (pat in c("proxy", "cookiefile", "cookiejar", "cookielist", "followlocation\\s*=\\s*TRUE", "chromeversion", "impersonate", "user-agent.*(Mozilla|Chrome|Safari)", "Mozilla/"))
    expect_length(grep(pat, txt, ignore.case = TRUE, perl = TRUE), 0L)
  expect_gt(length(grep("followlocation = FALSE", txt, fixed = TRUE)), 0L)
})

test_that("the scanner itself flags each banned construct (negative control)", {
  expect_length(grep("\\bcurl\\b", "x <- curl::new_handle()", perl = TRUE), 1L)
  expect_length(grep("(^|[^A-Za-z0-9_.])url\\(", "con <- url('http://x')", perl = TRUE), 1L)
  expect_length(grep("(^|[^A-Za-z0-9_.])url\\(", "fi_os_url(code, cycle)", perl = TRUE), 0L)
  expect_length(grep("(^|[^A-Za-z0-9_.])system2?\\(", "system2('curl')", perl = TRUE), 1L)
  expect_length(grep("playwright|selenium", "library(RSelenium)", ignore.case = TRUE), 1L)
})

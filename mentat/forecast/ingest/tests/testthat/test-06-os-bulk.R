# OpenSecrets bulk reader and reconstruction recipe (FEEDS_SPEC 8 test 6) on SYNTHETIC pipe-quoted lines: Latin-1 bytes, refunds,
# PRES / TXS1 / TX07 districts, chunk-boundary independence, the line-count assertion, zip members, the category file.

recipe <- function() synth_feeds()$bulk_recipes[[1]]

strip_attr <- function(x) { attr(x, "lines") <- NULL; x }

test_that("chamber mapping: PRES -> P, third character S -> Senate, anything else -> House", {
  expect_equal(fi_os_chamber(c("PRES", "TXS1", "TX07", "CAS2", "AK00", "DCAL")), c("P", "S", "H", "S", "H", "H"))
})

test_that("labels follow the page shape 'Last, First (P-ST)'; suffixes stay with the first name; presidents carry no state", {
  expect_equal(fi_os_label("Alex Smith (R)", "R", "TX", "H"), "Smith, Alex (R-TX)")
  expect_equal(fi_os_label("Bea Jones (D)", "D", "TX", "S"), "Jones, Bea (D-TX)")
  expect_equal(fi_os_label("Cal Déja Jr (R)", "R", "PR", "P"), "Déja, Cal Jr (R)")
  expect_equal(fi_os_label("Cher (I)", "I", "CA", "H"), "Cher (I-CA)")
})

test_that("the reader keeps only the industry's lines, decodes Latin-1, and matches the code exactly on RealCode", {
  d <- local_ingest_env(); write_synth_bulk(d$root)
  pacs <- fi_os_bulk_read(fi_os_bulk_locate(d$root, "pacs", 2024), "pacs", "Z9900")
  expect_equal(nrow(pacs), 4L)
  expect_false("r4" %in% pacs$FECRecNo)                              # another industry's code on the same file
  expect_equal(pacs$Amount[pacs$FECRecNo == "r3"], "-100")           # refunds keep their sign
  ind <- fi_os_bulk_read(fi_os_bulk_locate(d$root, "indivs", 2024), "indivs", "Z9900")
  expect_equal(nrow(ind), 4L)
  expect_true(all(ind$Contrib == "Doe, José"))                   # the comma inside a pipe-quoted field stays in the field; \xe9 -> U+00E9
  expect_true(all(ind$Employer[1] == "Café Co"))
  expect_identical(attr(ind, "lines")$read, attr(ind, "lines")$counted)
  cands <- fi_os_bulk_read(fi_os_bulk_locate(d$root, "cands", 2024), "cands")
  expect_equal(nrow(cands), 3L); expect_equal(cands$DistIDRunFor, c("TX07", "TXS1", "PRES"))
})

test_that("chunk-boundary independence: chunk sizes 3, 7 and 1e6 give identical output", {
  d <- local_ingest_env(); write_synth_bulk(d$root)
  for (tb in c("pacs", "indivs", "cands")) {
    loc <- fi_os_bulk_locate(d$root, tb, 2024)
    codes <- if (tb == "cands") NULL else "Z9900"
    a <- strip_attr(fi_os_bulk_read(loc, tb, codes, chunk_lines = 3)); b <- strip_attr(fi_os_bulk_read(loc, tb, codes, chunk_lines = 7)); c <- strip_attr(fi_os_bulk_read(loc, tb, codes, chunk_lines = 1e6))
    expect_identical(a, c, info = tb); expect_identical(b, c, info = tb)
  }
  loc <- fi_os_bulk_locate(d$root, "indivs", 2024)
  kept <- attr(fi_os_bulk_read(loc, "indivs", "Z9900", chunk_lines = 3), "lines")$kept_per_chunk
  expect_equal(length(kept), 2L); expect_equal(sum(kept), 4L)        # rows kept per chunk are recorded
})

test_that("NEGATIVE CONTROL: the line-count assertion fires when the reader and the file disagree (a file with CR-only line ends)", {
  d <- local_ingest_env()
  p <- file.path(d$root, "pacs24.txt")
  con <- file(p, "wb"); writeBin(charToRaw("|2024|,|r1|,|C001|,|N00000001|,500,|d|,|Z9900|,|24K|,|D|,|x|\r|2024|,|r2|,|C001|,|N00000002|,300,|d|,|Z9900|,|24K|,|D|,|y|\r"), con); close(con)
  expect_error(fi_os_bulk_read(list(path = p, member = NULL), "pacs", "Z9900"), "read 2 lines of pacs24.txt but the file has 1")
})

test_that("a layout with the wrong number of fields per line stops the read", {
  d <- local_ingest_env()
  p <- file.path(d$root, "pacs24.txt")
  write_latin1(p, "|2024|,|r1|,|C001|,|N00000001|,500,|Z9900|")
  expect_error(fi_os_bulk_read(list(path = p, member = NULL), "pacs", "Z9900"), "fields per line")
  expect_error(fi_os_bulk_read(list(path = p, member = NULL), "nope"), "unknown table")
})

test_that("tables inside a zip are located and read like plain files", {
  skip_if_not_installed("zip")
  d <- local_ingest_env(); src <- file.path(d$root, "src"); write_synth_bulk(src)
  zdir <- file.path(d$root, "zipped"); dir.create(zdir)
  old <- setwd(src); on.exit(setwd(old), add = TRUE)
  zip::zip(file.path(zdir, "CampaignFin24.zip"), files = c("pacs24.txt", "indivs24.txt", "cands24.txt"))
  setwd(old)
  loc <- fi_os_bulk_locate(zdir, "pacs", 2024)
  expect_false(is.null(loc$member)); expect_equal(basename(loc$path), "CampaignFin24.zip")
  z <- fi_os_bulk_read(loc, "pacs", "Z9900"); p <- fi_os_bulk_read(fi_os_bulk_locate(src, "pacs", 2024), "pacs", "Z9900")
  expect_identical(strip_attr(z), strip_attr(p))
  expect_null(fi_os_bulk_locate(zdir, "pacs", 2022))
})

test_that("recipe v1: candidate money = individual gifts to N-prefixed recipients + direct PAC gifts (DI = D), refunds kept; chambers and labels from cands", {
  d <- local_ingest_env(); write_synth_bulk(d$root)
  b <- fi_os_bulk_build(d$root, "Z9900", recipe(), 2024, "widgets")
  r <- b$recipients
  expect_equal(nrow(r), 3L)
  h <- r[r$chamber == "H", ]; s <- r[r$chamber == "S", ]; p <- r[r$chamber == "P", ]
  expect_equal(h$recipient_label, "Smith, Alex (R-TX)"); expect_equal(h$amount, 250 + 500 - 100); expect_equal(h$state, "Texas"); expect_equal(h$cid, "N00000001")
  expect_equal(s$recipient_label, "Jones, Bea (D-TX)"); expect_equal(s$amount, 100 + 300)
  expect_equal(p$recipient_label, "Déja, Cal Jr (R)"); expect_equal(p$amount, -40); expect_true(is.na(p$state))
  expect_equal(h$dist_id_run_for, "TX07"); expect_equal(p$dist_id_run_for, "PRES")
  expect_true(all(r$route == "bulk"))
  # a non-candidate recipient (C-prefixed committee), another industry's money and the DI = I PAC row never count
  expect_false(any(r$amount == 60)); expect_equal(sum(r$amount), 650 + 400 - 40)
  # totals (PAC model): direct PAC gifts 500 + 300 - 100; individuals to candidates 250 + 100 - 40; party split by recipient party
  tt <- b$totals
  expect_equal(tt$pacs, 700); expect_equal(tt$individuals, 310); expect_equal(tt$democrats, 300 + 100); expect_equal(tt$republicans, 400 + 210)
  expect_true(all(b$checks$failures == 0L))
  expect_equal(b$lines[["2024"]]$indivs$read, b$lines[["2024"]]$indivs$counted)
})

test_that("a recipient CID that is not a candidate of the cycle is counted as a failed check, not silently dropped", {
  d <- local_ingest_env(); write_synth_bulk(d$root)
  pac <- file.path(d$root, "pacs24.txt")
  cat("|2024|,|r9|,|C009|,|N09999999|,42,|2024-03-09|,|Z9900|,|24K|,|D|,|zz|\n", file = pac, append = TRUE)
  b <- fi_os_bulk_build(d$root, "Z9900", recipe(), 2024, "widgets")
  ck <- b$checks[grepl("candidate of cycle", b$checks$name), ]
  expect_equal(ck$failures, 1L); expect_match(ck$detail, "N09999999")
  expect_equal(nrow(b$recipients), 3L)
})

test_that("a missing table for a cycle stops the build and names the table", {
  d <- local_ingest_env(); write_synth_bulk(d$root); file.remove(file.path(d$root, "indivs24.txt"))
  expect_error(fi_os_bulk_build(d$root, "Z9900", recipe(), 2024, "widgets"), "missing bulk table\\(s\\): indivs")
})

test_that("bulk envelopes: page-shaped candidate tables readable by the refit loaders; totals carry null for every uncalibrated column and no table", {
  d <- local_ingest_env(); write_synth_bulk(d$root)
  b <- fi_os_bulk_build(d$root, "Z9900", recipe(), 2024, "widgets")
  env <- fi_os_bulk_envelopes(b, "Z9900", "v1", calibration_sha256 = "deadbeef")
  expect_true(all(c("Z9900_2024_candidates_H.json", "Z9900_2024_candidates_S.json", "Z9900_2024_presidential.json", "Z9900_totals.json") %in% names(env)))
  for (nm in names(env)) { expect_equal(env[[nm]]$route, "bulk"); expect_equal(env[[nm]]$recipe_version, "v1"); expect_equal(env[[nm]]$calibration_receipt_sha256, "deadbeef") }
  p <- file.path(d$root, "h.json"); rf_write_json_atomic(env[["Z9900_2024_candidates_H.json"]], p)
  c2 <- rf_os_candidates(p, "H"); expect_equal(c2$amount, 650); expect_equal(c2$party, "R")
  pp <- file.path(d$root, "p.json"); rf_write_json_atomic(env[["Z9900_2024_presidential.json"]], pp)
  expect_equal(rf_os_presidential(pp)$amount, -40)
  tp <- file.path(d$root, "t.json"); rf_write_json_atomic(env[["Z9900_totals.json"]], tp)
  j <- jsonlite::fromJSON(tp, simplifyVector = FALSE)
  expect_length(j$tables, 0L)
  expect_true(is.null(j$bulk_totals[[1]]$total)); expect_true(is.null(j$bulk_totals[[1]]$outside)); expect_equal(j$bulk_totals[[1]]$pacs, 700)
  expect_error(rf_os_totals(tp), "expected one totals table")        # the page-route loader fails loudly instead of reading an uncalibrated number
})

test_that("CRP_Categories.txt: read after its preamble; F3400-style verification of code, name and parent order code", {
  d <- local_ingest_env()
  p <- file.path(d$root, "CRP_Categories.txt")
  lines <- c("CRP Industry Codes (synthetic preamble)", "Updated for testing", "",
             "Catcode\tCatname\tCatorder\tIndustry\tSector\tSector Long",
             "Z9900\tWidget & Gadget Makers\tZ99\tSynthetic Parent\tSynth\tSynthetic Sector",
             "Z9800\tOther Makers\tZ98\tSynthetic Parent\tSynth\tSynthetic Sector")
  write_latin1(p, lines)
  cats <- fi_os_read_categories(p)
  expect_equal(nrow(cats), 2L); expect_equal(cats$Catname[1], "Widget & Gadget Makers")
  exp <- synth_feeds()$industries$widgets$opensecrets$expect
  v <- fi_os_verify_categories(cats, "Z9900", exp)
  expect_true(v$verified)
  expect_false(fi_os_verify_categories(cats, "Z9800", exp)$verified)
  e2 <- exp; e2$catorder <- "Q00"
  v2 <- fi_os_verify_categories(cats, "Z9900", e2); expect_false(v2$verified); expect_match(v2$issues, "catorder_is_Z99_not_Q00")
  expect_false(fi_os_verify_categories(cats, "Z0000", exp)$verified)
  writeLines("no header here", file.path(d$root, "x.txt"))
  expect_error(fi_os_read_categories(file.path(d$root, "x.txt")), "no header row")
})

# Inbox scan: hashing, classification by bytes, dedupe, vault, first-seen ledger, filename hints, unregistered codes, and the
# guarantee that inbox files are never modified, moved or deleted. FEEDS_SPEC 8 test 3. Synthetic files only.

setup_inbox <- function(d) {
  ib <- file.path(d$inbox, "ibisworld")
  dir.create(ib, recursive = TRUE, showWarnings = FALSE)
  write_synth_pdf(ib, "WebsiteFiles_Downloads_us_archive_2021_3_99912.pdf", "l3")
  file.copy(file.path(ib, "WebsiteFiles_Downloads_us_archive_2021_3_99912.pdf"), file.path(ib, "WebsiteFiles_Downloads_us_archive_2021_3_99912 (1).pdf"))
  writeLines("not a report, just a note", file.path(ib, "notes.txt"))
  writeBin(as.raw(c(0x50, 0x00, 0x01, 0xff, 0x02)), file.path(ib, "blob.bin"))
  ib
}

test_that("files are hashed, classified by magic bytes, deduplicated by hash and vaulted; unsupported files are recorded and left in place", {
  d <- local_ingest_env()
  ib <- setup_inbox(d)
  before <- fi_inbox_state(ib)
  scan <- fi_inbox_scan(ib, allowed = c("pdf", "xlsx"), ledger_path = file.path(d$frt, "inbox_seen.json"))
  s <- attr(scan, "summary")
  expect_equal(nrow(scan), 4L)
  expect_equal(s$files, 4L); expect_equal(s$duplicate, 1L); expect_equal(s$unsupported, 2L); expect_equal(s$new, 1L)
  expect_equal(sort(scan$kind), sort(c("pdf", "pdf", "text", "unsupported")))
  dup <- scan[scan$status == "duplicate", ]
  expect_equal(nrow(dup), 1L)
  expect_false(is.na(dup$duplicate_of))
  expect_equal(scan$sha256[scan$inbox_name == dup$duplicate_of], dup$sha256)
  fresh <- scan[scan$status == "new", ]
  expect_true(file.exists(fresh$vault_path))
  expect_equal(unname(tools::sha256sum(fresh$vault_path)), fresh$sha256)
  expect_true(grepl(fresh$sha256, fresh$vault_path, fixed = TRUE))
  expect_true(all(is.na(scan$vault_path[scan$status %in% c("duplicate", "unsupported")])))
  # nothing in the inbox changed, moved or disappeared
  expect_identical(fi_inbox_state(ib), before)
})

test_that("a second scan reports the file as seen before and keeps its first-seen time", {
  d <- local_ingest_env()
  ib <- setup_inbox(d)
  lp <- file.path(d$frt, "inbox_seen.json")
  a <- fi_inbox_scan(ib, ledger_path = lp, now = "2026-09-01T00:00:00Z")
  b <- fi_inbox_scan(ib, ledger_path = lp, now = "2026-09-30T00:00:00Z")
  pdf_a <- a[a$status == "new", ]; pdf_b <- b[b$status == "seen_before", ]
  expect_equal(nrow(pdf_b), 1L)
  expect_equal(pdf_b$first_seen_utc, "2026-09-01T00:00:00Z")
  expect_equal(pdf_a$sha256, pdf_b$sha256)
})

test_that("a dry scan writes neither the vault nor the ledger", {
  d <- local_ingest_env()
  ib <- setup_inbox(d)
  lp <- file.path(d$frt, "inbox_seen.json")
  s <- fi_inbox_scan(ib, ledger_path = lp, write = FALSE)
  expect_false(file.exists(lp))
  expect_true(all(is.na(s$vault_path)))
  expect_false(dir.exists(rf_vault_dir()) && length(list.files(rf_vault_dir())) > 0L)
})

test_that("kinds are decided by the bytes: a PDF renamed .txt is a PDF, an xlsx is an xlsx, an empty file is unsupported", {
  d <- local_ingest_env()
  ib <- file.path(d$inbox, "ibisworld"); dir.create(ib, recursive = TRUE)
  write_synth_pdf(ib, "looks_like_text.txt", "l3")
  skip_if_not_installed("writexl")
  writexl::write_xlsx(data.frame(Year = 2020:2021, Revenue = c(1, 2)), file.path(ib, "export.dat"))
  file.create(file.path(ib, "empty.pdf"))
  kinds <- vapply(list.files(ib, full.names = TRUE), fi_classify_file, "")
  expect_equal(unname(kinds[basename(names(kinds)) == "looks_like_text.txt"]), "pdf")
  expect_equal(unname(kinds[basename(names(kinds)) == "export.dat"]), "xlsx")
  expect_equal(unname(kinds[basename(names(kinds)) == "empty.pdf"]), "unsupported")
})

test_that("a file name that disagrees with the cover becomes an issue; an unknown code is recorded as unregistered_code and not used", {
  d <- local_ingest_env()
  ib <- file.path(d$inbox, "ibisworld"); dir.create(ib, recursive = TRUE)
  write_synth_pdf(ib, "WebsiteFiles_Downloads_us_archive_2019_5_99912.pdf", "l3")                    # cover says 2021-03
  pages <- synth_l3_pages(code = "88877", title = "Unknown Gizmo Makers in the US")
  make_text_pdf(file.path(ib, "gizmo.pdf"), pages)
  scan <- fi_inbox_scan(ib, ledger_path = file.path(d$frt, "inbox_seen.json"), now = "2026-09-30T00:00:00Z")
  col <- fi_ibis_collect(scan, synth_feeds())
  e <- col$editions
  a <- e[e$inbox_name == "WebsiteFiles_Downloads_us_archive_2019_5_99912.pdf", ]
  expect_equal(a$status, "review_required")
  expect_match(a$issues, "filename_date_differs_from_cover", fixed = TRUE)
  expect_equal(a$filename_date, "2019-05")
  g <- e[e$inbox_name == "gizmo.pdf", ]
  expect_equal(g$status, "unregistered_code")
  expect_equal(g$code, "88877")
  expect_false(g$numerics_ok)
  expect_false("88877" %in% col$long$code)              # unregistered content never reaches the long table
})

test_that("two different reports of the same code and month are both set aside (the vintage would be ambiguous)", {
  d <- local_ingest_env()
  ib <- file.path(d$inbox, "ibisworld"); dir.create(ib, recursive = TRUE)
  write_synth_pdf(ib, "a.pdf", "l3")
  write_synth_pdf(ib, "b.pdf", "l3", start = 123456)
  scan <- fi_inbox_scan(ib, ledger_path = file.path(d$frt, "inbox_seen.json"), now = "2026-09-30T00:00:00Z")
  col <- fi_ibis_collect(scan, synth_feeds())
  expect_equal(sort(col$editions$status), c("review_required", "review_required"))
  expect_true(all(grepl("duplicate_edition", col$editions$issues)))
  expect_false(any(col$editions$numerics_ok))
  expect_equal(nrow(col$long), 0L)
})

test_that("a full ibisworld run leaves every inbox file byte-identical and in place", {
  d <- local_ingest_env()
  ib <- setup_inbox(d)
  before <- fi_inbox_state(ib)
  r <- fi_run("ibisworld", run_id = "t-inbox-unchanged", feeds = synth_feeds(), events_dir = d$glassbox, verbose = FALSE)
  expect_identical(fi_inbox_state(ib), before)
  expect_true(r$status %in% c("ok", "waiting_for_owner_file", "review_required"))
  rc <- jsonlite::fromJSON(r$receipt$path, simplifyVector = FALSE)
  chk <- Filter(function(x) grepl("inbox files unchanged", x$name), rc$checks)
  expect_length(chk, 1L)
  expect_equal(chk[[1]]$failures, 0L)
})

test_that("Latin-1 text (the bulk-data encoding) classifies as text, and saved HTML as html", {
  d <- local_ingest_env()
  p <- file.path(d$root, "pacs24.txt"); write_latin1(p, c("|2024|,|Caf\u00e9 Co|,500"))
  expect_equal(fi_classify_file(p), "text")
  h <- file.path(d$root, "page.dat"); writeLines("<!DOCTYPE html><html><body><table></table></body></html>", h)
  expect_equal(fi_classify_file(h), "html")
  expect_equal(fi_classify_file(file.path(d$root, "missing.bin")), "unsupported")
})

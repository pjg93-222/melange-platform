# Receipts and proposals (FEEDS_SPEC 8 test 11): required fields present; every output sha256 equals the bytes on disk; proposed_*.json
# written; registry\*.json (the refit's and the ingest's) and inputs.json untouched.

reg_hashes <- function() {
  f <- c(list.files(file.path(fi_refit_home(), "registry"), full.names = TRUE), file.path(.FI_HOME, "registry", "feeds.json"))
  stats::setNames(vapply(f, rf_sha256, ""), basename(f))
}

run_ok <- function(d, id = "rc-ok") {
  ib <- file.path(d$inbox, "ibisworld"); dir.create(ib, recursive = TRUE, showWarnings = FALSE)
  write_synth_pdf(ib, "good.pdf", "l3")
  write_synth_pdf(ib, "older.pdf", "l3", pub = list(2020, "Feb"), years = 2008:2025, base = 2020, start = 95000)
  fi_run("ibisworld", run_id = id, feeds = synth_feeds(), events_dir = d$glassbox, verbose = FALSE)
}

test_that("the receipt carries every field of FEEDS_SPEC 7.6", {
  d <- local_ingest_env()
  r <- run_ok(d)
  rc <- jsonlite::fromJSON(r$receipt$path, simplifyVector = FALSE)
  for (k in c("run_id", "feed", "mode", "started_utc", "finished_utc", "code", "packages", "inputs", "outputs", "checks", "decisions", "issues", "network", "glassbox_event_ids"))
    expect_true(k %in% names(rc), info = k)
  expect_equal(rc$run_id, "rc-ok"); expect_equal(rc$feed, "ibisworld"); expect_equal(rc$mode, "run")
  expect_match(rc$started_utc, "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:]{8}Z$")
  expect_gte(length(rc$code), 12L)
  expect_true(all(vapply(rc$code, function(z) all(c("file", "sha256") %in% names(z)), NA)))
  expect_true("fi_ibis_pdf.R" %in% vapply(rc$code, function(z) basename(z$file), ""))
  expect_true(all(c("pdftools", "poppler", "xml2", "data.table", "curl", "jsonlite", "readxl") %in% names(rc$packages)))
  inp <- Filter(function(z) z$kind == "pdf", rc$inputs)
  expect_length(inp, 2L)
  for (z in inp) { expect_true(all(c("inbox_name", "sha256", "bytes", "first_seen_utc", "vault_path", "kind") %in% names(z))); expect_true(file.exists(z$vault_path)) }
  expect_equal(length(rc$network), 0L)
  expect_gt(length(rc$glassbox_event_ids), 3L)
  expect_true(length(rc$checks) > 0L)
  expect_true(all(vapply(rc$checks, function(z) all(c("name", "examined", "failures", "detail") %in% names(z)), NA)))
})

test_that("every output's recorded sha256 and row count equal the file on disk", {
  d <- local_ingest_env()
  r <- run_ok(d)
  rc <- jsonlite::fromJSON(r$receipt$path, simplifyVector = FALSE)
  expect_gte(length(rc$outputs), 6L)
  for (o in rc$outputs) {
    expect_true(file.exists(o$path), info = o$path)
    expect_equal(o$sha256, unname(tools::sha256sum(o$path)), info = o$path)
    if (!is.null(o$rows) && grepl("\\.csv$", o$path)) expect_equal(o$rows, nrow(utils::read.csv(o$path)), info = o$path)
  }
  roles <- vapply(rc$outputs, function(o) o$role %||% "", "")
  expect_true(all(c("ibis_editions", "ibis_key_statistics_long", "ibis_predicted_growth", "ibis_actual_growth", "proposed_inputs", "proposed_registry_patch") %in% roles))
  # the receipt is the last file written of the run
  mt <- file.mtime(c(vapply(rc$outputs, function(o) o$path, ""), r$receipt$path))
  expect_true(mt[length(mt)] >= max(mt))
  expect_equal(r$receipt$sha256, unname(tools::sha256sum(r$receipt$path)))
})

test_that("proposals are written, name the vault copies and derived feeds, and no registry or manifest file changes", {
  d <- local_ingest_env()
  before <- reg_hashes()
  r <- run_ok(d)
  expect_identical(reg_hashes(), before)
  pi <- jsonlite::fromJSON(file.path(r$run_dir, "proposed_inputs.json"), simplifyVector = FALSE)
  pr <- jsonlite::fromJSON(file.path(r$run_dir, "proposed_registry_patch.json"), simplifyVector = FALSE)
  expect_equal(pi$schema_version, 1L)
  roles <- vapply(pi$inputs, function(e) e$role, "")
  expect_true(all(c("ibis_pdf:99912:2021-03", "ibis_pdf:99912:2020-02") %in% roles))
  expect_true(all(c("feed:ibis_predicted_growth", "feed:ibis_actual_growth", "feed:ibis_editions", "feed:ibis_key_statistics_long") %in% roles))
  raw <- Filter(function(e) grepl("^ibis_pdf:", e$role), pi$inputs)
  for (e in raw) { expect_true(e$pinned); expect_equal(e$sha256, unname(tools::sha256sum(e$path))) }
  feed <- Filter(function(e) e$role == "feed:ibis_predicted_growth", pi$inputs)[[1]]
  expect_false(feed$pinned)
  expect_equal(length(feed$inputs_sha256), 2L)
  expect_equal(feed$code_sha256, fi_code_sha256())
  expect_true(!is.null(pr$patch$industries$widgets$ibisworld$predicted_source))
  expect_match(pi$note, "never applies")
})

test_that("current.json maps each role to the latest accepted output; a run with no checked edition leaves it alone", {
  d <- local_ingest_env()
  r <- run_ok(d, "rc-cur")
  cur <- jsonlite::fromJSON(file.path(d$frt, "current.json"), simplifyVector = FALSE)
  expect_true(all(c("ibis_predicted_growth", "ibis_actual_growth", "ibis_editions") %in% names(cur)))
  expect_equal(cur$ibis_actual_growth$run_id, "rc-cur")
  expect_equal(cur$ibis_actual_growth$sha256, unname(tools::sha256sum(cur$ibis_actual_growth$path)))
  # an empty inbox: a second run must not replace the accepted outputs
  unlink(file.path(d$inbox, "ibisworld", "*.pdf"))
  r2 <- fi_run("ibisworld", run_id = "rc-empty", feeds = synth_feeds(), events_dir = d$glassbox, verbose = FALSE)
  expect_equal(r2$status, "waiting_for_owner_file")
  cur2 <- jsonlite::fromJSON(file.path(d$frt, "current.json"), simplifyVector = FALSE)
  expect_equal(cur2$ibis_actual_growth$run_id, "rc-cur")
})

test_that("an empty inbox reports 'waiting for owner file' with exit code 0, and the receipt says so", {
  d <- local_ingest_env()
  out <- capture.output(r <- fi_run("ibisworld", run_id = "rc-wait", feeds = synth_feeds(), events_dir = d$glassbox, verbose = TRUE))
  expect_equal(r$status, "waiting_for_owner_file"); expect_equal(r$exit_code, 0L)
  expect_true(any(grepl("waiting for owner file", out, fixed = TRUE)))
  rc <- jsonlite::fromJSON(r$receipt$path, simplifyVector = FALSE)
  expect_equal(rc$feed_state, "waiting_for_owner_file")
  expect_true(any(grepl("waiting for owner file", unlist(rc$decisions), fixed = TRUE)))
})

test_that("the replaced vintage is recorded when a newer edition arrives", {
  d <- local_ingest_env()
  ib <- file.path(d$inbox, "ibisworld"); dir.create(ib, recursive = TRUE)
  write_synth_pdf(ib, "a.pdf", "l3", pub = list(2020, "Feb"), years = 2008:2025, base = 2020, start = 95000)
  fi_run("ibisworld", run_id = "rc-v1", feeds = synth_feeds(), events_dir = d$glassbox, verbose = FALSE)
  write_synth_pdf(ib, "b.pdf", "l3")
  r2 <- fi_run("ibisworld", run_id = "rc-v2", feeds = synth_feeds(), events_dir = d$glassbox, verbose = FALSE)
  rc <- jsonlite::fromJSON(r2$receipt$path, simplifyVector = FALSE)
  expect_equal(rc$extra$replaced_vintage, "2020-02")
  expect_true(any(grepl("replaces the whole 2020-02 history", unlist(rc$decisions), fixed = TRUE)))
})

test_that("fi_load_feed: an accepted feed reads back typed; a changed output or a missing vault input fails with rerun_ingest; a changed code hash means re-derive for IBISWorld feeds", {
  d <- local_ingest_env()
  r <- run_ok(d, "rc-load")
  f <- fi_load_feed("feed:ibis_predicted_growth")
  expect_true(f$ok); expect_equal(f$action, "use"); expect_equal(f$run_id, "rc-load")
  expect_true(is.numeric(f$data$collected_forecast_growth_pct)); expect_true(is.numeric(f$data$year)); expect_equal(nrow(f$data), 16L)
  expect_true(all(f$checks$failures == 0L))
  a <- fi_load_feed("ibis_actual_growth"); expect_true(a$ok); expect_true(is.numeric(a$data$revenue_million_report_price_basis))
  expect_false(fi_load_feed("nope")$ok)
  # a different code hash (another version of the ingest) on an IBISWorld feed: cheap re-derive
  c2 <- fi_load_feed("ibis_predicted_growth", code_sha = "0000")
  expect_false(c2$ok); expect_equal(c2$action, "rederive"); expect_match(c2$reason, "code changed")
  # a tampered output: rerun
  p <- f$path; txt <- readLines(p); writeLines(c(txt, ""), p, useBytes = TRUE)
  t <- fi_load_feed("ibis_predicted_growth"); expect_false(t$ok); expect_equal(t$action, "rerun_ingest"); expect_match(t$reason, "output changed")
  # a missing raw input: rerun
  run_ok2 <- fi_run("ibisworld", run_id = "rc-load2", feeds = synth_feeds(), events_dir = d$glassbox, verbose = FALSE)
  unlink(rf_vault_dir(), recursive = TRUE)
  m <- fi_load_feed("ibis_predicted_growth"); expect_false(m$ok); expect_equal(m$action, "rerun_ingest"); expect_match(m$reason, "raw input")
})

test_that("fi_load_feed with nothing accepted yet says so", {
  d <- local_ingest_env()
  x <- fi_load_feed("ibis_predicted_growth"); expect_false(x$ok); expect_match(x$reason, "no current.json")
})

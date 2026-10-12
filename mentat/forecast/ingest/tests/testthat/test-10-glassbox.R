# Glass Box emission (FEEDS_SPEC 8 test 10): every CLI path writes feeds.run.start and exactly one of feeds.run.done / feeds.run.fail to
# RF_GLASSBOX_DIR, with the stage events of the path. Schema of the SGS emitter: evt-<UTC>-<pid>.json, schema_version 1.

kinds_of <- function(ev) vapply(ev, function(e) e$kind, "")

put_inbox <- function(d, good = TRUE, bad = FALSE) {
  ib <- file.path(d$inbox, "ibisworld"); dir.create(ib, recursive = TRUE, showWarnings = FALSE)
  if (good) write_synth_pdf(ib, "good.pdf", "l3")
  if (bad) writeLines("not a report", file.path(ib, "bad.txt"))
  ib
}

expect_one_finish <- function(ev) {
  k <- kinds_of(ev)
  expect_equal(sum(k == "feeds.run.start"), 1L)
  expect_equal(sum(k %in% c("feeds.run.done", "feeds.run.fail")), 1L)
  expect_equal(k[1], "feeds.run.start")
  expect_true(k[length(k)] %in% c("feeds.run.done", "feeds.run.fail"))
}

test_that("success path: start, inbox scan, file and parse events, derive, manifest proposal, done", {
  d <- local_ingest_env(); put_inbox(d)
  r <- fi_run("ibisworld", run_id = "gb-success", feeds = synth_feeds(), events_dir = d$glassbox, verbose = FALSE)
  ev <- read_events(d$glassbox)
  expect_one_finish(ev)
  k <- kinds_of(ev)
  expect_equal(k[length(k)], "feeds.run.done")
  expect_true(all(c("feeds.inbox.scan", "feeds.file", "feeds.parse", "feeds.derive", "feeds.manifest.proposed") %in% k))
  # shape of every event
  for (e in ev) {
    expect_equal(e$schema_version, 1L); expect_equal(e$lane, "claude"); expect_equal(e$source, "giving-forecast-ingest"); expect_equal(e$run_id, "gb-success")
    expect_match(e$id, "^evt-[0-9]{8}T[0-9]{6}[0-9]{7}Z-[0-9]{10}$")
  }
  expect_equal(ev[[1]]$correlation_id, ev[[1]]$id)                    # first event is the correlation root
  expect_true(all(vapply(ev[-1], function(e) identical(e$parent_id, ev[[1]]$id), NA)))
  expect_equal(ev[[1]]$metadata$feed, "ibisworld")
  expect_equal(ev[[length(ev)]]$metadata$outputs > 0L, TRUE)
  # the receipt lists the same events
  rc <- jsonlite::fromJSON(r$receipt$path, simplifyVector = FALSE)
  expect_equal(sort(unlist(rc$glassbox_event_ids)), sort(vapply(ev, function(e) e$id, "")))
})

test_that("review-required path: a file that needs review still ends with exactly one feeds.run.done, and the exit code says so", {
  d <- local_ingest_env(); put_inbox(d, good = TRUE, bad = TRUE)
  r <- fi_run("ibisworld", run_id = "gb-review", feeds = synth_feeds(), events_dir = d$glassbox, verbose = FALSE)
  expect_equal(r$status, "review_required"); expect_equal(r$exit_code, 2L)
  ev <- read_events(d$glassbox)
  expect_one_finish(ev)
  expect_equal(tail(kinds_of(ev), 1), "feeds.run.done")
})

test_that("stop path: a closed fetch gate makes no request, emits feeds.fetch.gate and ends with exactly one feeds.run.fail", {
  d <- local_ingest_env()
  calls <- 0L
  feeds <- synth_feeds(); feeds$opensecrets_fetch$permission <- NULL
  r <- fi_run("opensecrets-fetch", industry = "widgets", run_id = "gb-stop", feeds = feeds, events_dir = d$glassbox, verbose = FALSE,
              transport = function(...) { calls <<- calls + 1L; stop("must not be called") })
  expect_equal(calls, 0L)
  expect_equal(r$status, "stopped"); expect_equal(r$exit_code, 3L)
  ev <- read_events(d$glassbox)
  expect_one_finish(ev)
  k <- kinds_of(ev)
  expect_equal(tail(k, 1), "feeds.run.fail")
  expect_true("feeds.fetch.gate" %in% k)
  gate <- ev[[which(k == "feeds.fetch.gate")[1]]]
  expect_equal(gate$metadata$decision, "stop")
})

test_that("error path: an exception inside the run ends with exactly one feeds.run.fail and a failed receipt", {
  d <- local_ingest_env(); put_inbox(d)
  feeds <- synth_feeds(); feeds$industries$widgets$ibisworld$predicted_targets <- list("x", "y")
  r <- suppressWarnings(fi_run("ibisworld", run_id = "gb-error", feeds = feeds, events_dir = d$glassbox, verbose = FALSE))
  expect_equal(r$status, "failed"); expect_equal(r$exit_code, 1L)
  ev <- read_events(d$glassbox)
  expect_one_finish(ev)
  expect_equal(tail(kinds_of(ev), 1), "feeds.run.fail")
  rc <- jsonlite::fromJSON(r$receipt$path, simplifyVector = FALSE)
  expect_equal(rc$status, "failed")
})

test_that("dry run: start and done events, no receipt, no outputs, no vault copy", {
  d <- local_ingest_env(); put_inbox(d)
  r <- fi_run("ibisworld", dry_run = TRUE, run_id = "gb-dry", feeds = synth_feeds(), events_dir = d$glassbox, verbose = FALSE)
  ev <- read_events(d$glassbox)
  expect_one_finish(ev)
  expect_equal(ev[[1]]$metadata$mode, "dry-run")
  expect_false(dir.exists(file.path(d$frt, "runs", "gb-dry")))
  expect_false(dir.exists(rf_vault_dir()) && length(list.files(rf_vault_dir())) > 0L)
})

test_that("an unwritable event folder only warns; the run still completes and writes its receipt", {
  d <- local_ingest_env(); put_inbox(d)
  blocked <- file.path(d$root, "not_a_folder"); writeLines("x", blocked)
  expect_message(suppressWarnings(r <- fi_run("ibisworld", run_id = "gb-blocked", feeds = synth_feeds(), events_dir = blocked, verbose = FALSE)), "Glass Box event not written")
  expect_true(file.exists(r$receipt$path))
})

test_that("argument parsing: known feeds and flags; unknown feed or flag is an error", {
  o <- fi_parse_args(c("--feed", "ibisworld", "--industry", "property_casualty", "--dry-run", "--run-id", "r1"))
  expect_equal(o$feed, "ibisworld"); expect_equal(o$industry, "property_casualty"); expect_true(o$dry_run); expect_equal(o$run_id, "r1")
  expect_error(fi_parse_args(c("--feed", "nope")), "must be one of")
  expect_error(fi_parse_args(c("--feed", "ibisworld", "--bogus")), "unknown argument")
  expect_error(fi_parse_args(character()), "must be one of")
})

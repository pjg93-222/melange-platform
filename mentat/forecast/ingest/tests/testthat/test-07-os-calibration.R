# Calibration gate of the bulk route (FEEDS_SPEC 8 test 7): tolerances, recipe order, the stop-with-residuals outcome, on synthetic page
# captures written in the Codex capture format. The real-bulk test is local-only and skips without the bulk files.

mk_cap_tree <- function(root, cycles = c(2012L, 2014L)) {
  bd <- file.path(root, "outputs/opensecrets_baseline/direct_sources"); pd <- file.path(root, "outputs/opensecrets_partisanship/direct_sources")
  dir.create(bd, recursive = TRUE); dir.create(pd, recursive = TRUE)
  rows <- synth_totals_rows(cycles = c(2014, 2012, 1990), pac = c(1000000, 800000, 5), indiv = c(2000000, 1500000, 6), outside = c(300000, 100000, 0))
  cap <- list(url = "https://www.opensecrets.org/industries/totals?cycle=2024&ind=F03", retrieved_utc = "2026-09-27T18:45:51+00:00", tables = list(list(rows = lapply(rows, as.list))))
  jsonlite::write_json(cap, file.path(bd, "F03_2024_totals.json"), auto_unbox = TRUE)
  truth <- list()
  for (cy in cycles) for (ch in c("H", "S")) {
    r <- synth_candidate_rows(40, ch)
    cap <- list(url = fi_os_url("F03", cy, paste0("candidates_", ch)), retrieved_utc = "2026-09-27T20:58:08+00:00", source_tables = list(list(rows = lapply(r, as.list))))
    jsonlite::write_json(cap, file.path(pd, sprintf("F03_%d_candidates_%s.json", cy, ch)), auto_unbox = TRUE)
    x <- rf_os_candidates(file.path(pd, sprintf("F03_%d_candidates_%s.json", cy, ch)), ch)
    truth[[length(truth) + 1L]] <- data.frame(cycle = cy, chamber = ch, name_key = x$name_key, party = x$party, amount = x$amount, stringsAsFactors = FALSE)
  }
  list(roots = list(LOO = root), truth = do.call(rbind, truth), pac = c("2012" = 800000, "2014" = 1000000))
}

# A build stub that reproduces the page truth, optionally distorted.
stub_build <- function(tree, pac_scale = 1, drop_frac = 0, amount_off = 0, off_frac = 0) {
  function(dir, code, recipe, cycles, industry_id = NA) {
    rec <- tree$truth
    if (drop_frac > 0) rec <- rec[-seq_len(ceiling(nrow(rec) * drop_frac)), ]
    if (off_frac > 0) { k <- seq_len(ceiling(nrow(rec) * off_frac)); rec$amount[k] <- rec$amount[k] + amount_off }
    rec$industry_id <- industry_id
    tot <- data.frame(industry_id = industry_id, os_code = code, cycle = as.integer(names(tree$pac)), pacs = unname(tree$pac) * pac_scale, stringsAsFactors = FALSE)
    list(recipients = rec[rec$cycle %in% cycles, ], totals = tot[tot$cycle %in% cycles, ], checks = rf_check("none", 0L, 0L)[0, ], lines = list())
  }
}

feeds_cal <- function(n_recipes = 1L) { f <- synth_feeds(); f$bulk_recipes <- lapply(seq_len(n_recipes), function(i) list(version = paste0("v", i))); f$industries$widgets$opensecrets$calibration_codes <- list("F03"); f }

test_that("compare: tolerances at their boundaries (PAC 0.5%, party sums 1%, 98% name matches within $1)", {
  tree <- mk_cap_tree(local_ingest_env()$root)
  pt <- data.frame(cycle = c(2012L, 2014L), pac = c(800000, 1000000))
  page <- tree$truth
  run <- function(bulk_rec = page, bulk_pac = pt$pac) fi_os_calibrate_compare(bulk_rec, data.frame(cycle = pt$cycle, pac = bulk_pac), page, pt)
  expect_true(all(run()$pass))
  expect_true(all(run(bulk_pac = pt$pac * 1.004)$pass))                   # 0.4% off: inside
  expect_false(any(run(bulk_pac = pt$pac * 1.006)$pass))                  # 0.6% off: outside
  d <- page; d$amount[d$party == "D" & d$chamber == "H"] <- d$amount[d$party == "D" & d$chamber == "H"] * 1.02
  expect_false(all(run(d)$pass))                                          # Democratic House sum 2% off
  n <- nrow(page[page$cycle == 2012L & page$chamber == "H", ])
  one_off <- page; one_off$amount[which(one_off$cycle == 2012L & one_off$chamber == "H")[1]] <- one_off$amount[which(one_off$cycle == 2012L & one_off$chamber == "H")[1]] + 0.5
  expect_true(all(run(one_off)$pass))                                     # within $1
  two <- page; two$amount[which(two$cycle == 2012L & two$chamber == "H")[1:2]] <- two$amount[which(two$cycle == 2012L & two$chamber == "H")[1:2]] + 3
  res <- run(two); expect_false(res$pass[res$cycle == 2012L]); expect_lt(res$match_H[res$cycle == 2012L], 0.98)   # 2 of 40 off by $3 -> 95% matched
})

test_that("a recipe that reproduces the captures is accepted", {
  tree <- mk_cap_tree(local_ingest_env()$root)
  r <- fi_os_calibrate("unused", feeds_cal(), cycles = c(2012L, 2014L), roots = tree$roots, build = stub_build(tree))
  expect_true(r$passed); expect_equal(r$accepted, "v1")
  expect_true(all(r$table$pass)); expect_equal(unique(r$table$code), "F03")
  expect_equal(sort(r$table$cycle), c(2012L, 2014L))
  expect_lt(r$max_delta, 1e-9)
})

test_that("NEGATIVE CONTROLS: a PAC total off by 2%, 10% missing recipients, or a third of amounts off by $5 each fail the gate with a residual table", {
  tree <- mk_cap_tree(local_ingest_env()$root)
  for (b in list(stub_build(tree, pac_scale = 1.02), stub_build(tree, drop_frac = 0.10), stub_build(tree, amount_off = 5, off_frac = 0.33))) {
    r <- fi_os_calibrate("unused", feeds_cal(), cycles = c(2012L, 2014L), roots = tree$roots, build = b)
    expect_false(r$passed); expect_null(r$accepted)
    expect_true(is.data.frame(r$table)); expect_false(all(r$table$pass))
  }
  r <- fi_os_calibrate("unused", feeds_cal(), cycles = c(2012L, 2014L), roots = tree$roots, build = stub_build(tree, pac_scale = 1.02))
  expect_equal(r$max_delta, 0.02, tolerance = 1e-9)
})

test_that("the first passing recipe in registry order is accepted", {
  tree <- mk_cap_tree(local_ingest_env()$root)
  n <- 0L
  b <- function(dir, code, recipe, cycles, industry_id = NA) { n <<- n + 1L; stub_build(tree, pac_scale = if (recipe$version == "v1") 1.05 else 1)(dir, code, recipe, cycles, industry_id) }
  r <- fi_os_calibrate("unused", feeds_cal(3L), cycles = c(2012L, 2014L), roots = tree$roots, build = b)
  expect_equal(r$accepted, "v2")
  expect_equal(sort(unique(r$table$recipe)), c("v1", "v2"))               # v3 was never tried
})

test_that("no calibration codes in the registry is an error, and a missing frozen capture is an error - never a silent pass", {
  tree <- mk_cap_tree(local_ingest_env()$root)
  f <- feeds_cal(); f$industries$widgets$opensecrets$calibration_codes <- list()
  expect_error(fi_os_calibrate("unused", f, roots = tree$roots), "no calibration_codes")
  empty_roots <- list(LOO = file.path(tempdir(), "no_such_loo"))
  expect_error(fi_os_calibrate("unused", feeds_cal(), cycles = c(2012L, 2014L), roots = empty_roots, build = stub_build(tree)), "frozen totals capture of calibration code F03 is missing")
})

test_that("the bulk CLI path stops with the residual table and a failed-calibration reason when no recipe passes", {
  d <- local_ingest_env()
  bulk <- file.path(d$inbox, "opensecrets", "bulk"); dir.create(bulk, recursive = TRUE)
  write_synth_bulk(bulk, 2012); write_synth_bulk(bulk, 2014)
  cats <- c("Catcode\tCatname\tCatorder\tIndustry\tSector\tSector Long", "Z9900\tWidget & Gadget Makers\tZ99\tSynthetic Parent\tSynth\tSynthetic Sector")
  write_latin1(file.path(bulk, "CRP_Categories.txt"), cats)
  tree <- mk_cap_tree(file.path(d$root, "loo"))
  feeds <- feeds_cal()
  withr::local_envvar(RF_ROOT_LOO = tree$roots$LOO)
  r <- fi_run("opensecrets-bulk", industry = "widgets", run_id = "os-bulk", feeds = feeds, events_dir = d$glassbox, verbose = FALSE)
  expect_equal(r$status, "stopped", info = r$message); expect_equal(r$stop_reason, "calibration_failed"); expect_equal(r$exit_code, 3L)
  ev <- read_events(d$glassbox)
  cal <- Filter(function(e) e$kind == "feeds.calibrate", ev)
  expect_length(cal, 1L); expect_false(cal[[1]]$metadata$pass)
  expect_equal(tail(vapply(ev, function(e) e$kind, ""), 1), "feeds.run.fail")
  expect_true(file.exists(file.path(r$run_dir, "outputs", "calibration.csv")))
  expect_false(dir.exists(file.path(r$run_dir, "outputs", "os")))            # no bulk number was written
  rc <- jsonlite::fromJSON(r$receipt$path, simplifyVector = FALSE)
  expect_true(any(grepl("calibration", vapply(rc$checks, function(z) z$name, ""))))
})

test_that("the bulk path without the categories file stops before any number is read; an empty bulk folder waits for the owner", {
  d <- local_ingest_env()
  r0 <- fi_run("opensecrets-bulk", industry = "widgets", run_id = "os-bulk-empty", feeds = synth_feeds(), events_dir = d$glassbox, verbose = FALSE)
  expect_equal(r0$status, "waiting_for_owner_file"); expect_equal(r0$exit_code, 0L)
  bulk <- file.path(d$inbox, "opensecrets", "bulk"); write_synth_bulk(bulk)
  r1 <- fi_run("opensecrets-bulk", industry = "widgets", run_id = "os-bulk-nocats", feeds = synth_feeds(), events_dir = d$glassbox, verbose = FALSE)
  expect_equal(r1$status, "stopped", info = r1$message); expect_equal(r1$stop_reason, "categories_file_missing")
})

test_that("LOCAL-ONLY: with the real bulk files in the owner's inbox, the recipe passes the gate on F03, F05, T2100 and F09", {
  bulk <- if (nzchar(REAL_LOCALAPPDATA)) file.path(REAL_LOCALAPPDATA, "Mentat", "forecast", "inbox", "opensecrets", "bulk") else ""
  skip_if(!dir.exists(bulk) || !length(list.files(bulk, pattern = "^(cands|pacs|indivs)[0-9]{2}|CampaignFin", ignore.case = TRUE, recursive = TRUE)), "no OpenSecrets bulk files in the owner's inbox")
  feeds <- fi_read_feeds()
  r <- fi_os_calibrate(bulk, feeds, roots = rf_roots())
  expect_true(r$passed, info = paste(utils::capture.output(print(r$table)), collapse = "\n"))
})

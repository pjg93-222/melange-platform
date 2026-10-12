# The frozen page inventory and the F09 baseline ruling (owner, 2026-09-30): the existing captures are never re-fetched or overwritten, but
# pages that no capture holds may be fetched; only the missing pages are planned and requested; the scope caveat is in every receipt.
# Synthetic inventories; one local-only test checks the committed inventory against the real capture folders.

write_inv <- function(path, rows) {
  inv <- do.call(rbind, lapply(rows, function(r) data.frame(code = r[[1]], cycle = r[[2]], kind = r[[3]], group = "synthetic", file = sprintf("outputs/x/%s_%d_%s.json", r[[1]], r[[2]], r[[3]]),
                                                            sha256 = paste(rep("ab", 32), collapse = ""), url = fi_os_url(r[[1]], r[[2]], r[[3]]), retrieved_utc = "2026-09-27T00:00:00+00:00", stringsAsFactors = FALSE)))
  fi_os_write_inventory(inv, path, "synthetic")
  path
}

feeds_inv <- function(path) { f <- synth_feeds(); f$opensecrets_frozen$codes <- list("F03"); f$opensecrets_frozen$inventory <- path; f }

test_that("the committed inventory lists the 2026-09-27 captures page by page: 63 recipient captures and, for F09, only the totals and summary pages", {
  inv <- fi_os_frozen_inventory(fi_read_feeds())
  expect_equal(nrow(inv), 75L)
  rec <- inv[inv$kind %in% c("candidates_H", "candidates_S", "presidential"), ]
  expect_equal(nrow(rec), 63L)
  expect_equal(sort(unique(rec$code)), c("F03", "F05", "T2100"))
  f09 <- inv[inv$code == "F09", ]
  expect_equal(sort(paste(f09$cycle, f09$kind)), c("2024 summary", "2024 totals"))
  expect_true(all(grepl("^[0-9a-f]{64}$", inv$sha256)))
  expect_false(anyDuplicated(inv[c("code", "cycle", "kind", "group", "file")]) > 0L)
  expect_true(all(is.na(inv$url) | grepl("^https://www\\.opensecrets\\.org/", inv$url)))
})

test_that("LOCAL-ONLY: the committed inventory equals a fresh scan of the capture folders (nothing captured was added, changed or lost)", {
  root <- rf_roots()$LOO
  skip_if_not(dir.exists(file.path(root, FI_CAPTURE_DIRS[["baseline"]])), "the 2026-09-27 capture folders are not on this machine")
  fresh <- fi_os_scan_captures(root)
  inv <- fi_os_frozen_inventory(fi_read_feeds())
  skip_if(is.null(fresh) || nrow(fresh) < nrow(inv), "only part of the 2026-09-27 capture folders is on this machine (for example the pinned subset on a compute pod)")
  cols <- c("code", "cycle", "kind", "group", "file", "sha256", "url")
  expect_equal(nrow(fresh), nrow(inv))
  expect_identical(as.data.frame(fresh[cols]), as.data.frame(inv[cols]))
})

test_that("the real P&C plan is the 21 F09 pages no capture holds: House, Senate and presidential recipients for 2012-2024, no totals page", {
  f <- fi_read_feeds()
  inv <- fi_os_frozen_inventory(f)
  p <- fi_os_fetch_plan(f, "property_casualty", inventory = inv)
  expect_equal(nrow(p), 21L)
  expect_equal(sum(p$kind == "candidates_H"), 7L); expect_equal(sum(p$kind == "candidates_S"), 7L); expect_equal(sum(p$kind == "presidential"), 7L)
  expect_false("totals" %in% p$kind)
  expect_true(all(p$code == "F09"))
  expect_equal(p$url[1], "https://www.opensecrets.org/industries/summary?code=F09&cycle=2012&ind=F09&mem=N&recipdetail=H")
  pres <- attr(p, "present")
  expect_equal(nrow(pres), 1L); expect_equal(paste(pres$code, pres$cycle, pres$kind), "F09 2024 totals")
  expect_equal(nrow(attr(p, "expected")), 22L)
  g <- fi_os_fetch_gate(f$opensecrets_fetch, p, unlist(f$opensecrets_frozen$codes), frt = tempfile(), inventory = inv)
  expect_true(g$ok)
  # the captured F09 totals page and any page of a fully frozen code are refused by the gate
  tot <- data.frame(url = fi_os_url("F09", 2024L, "totals"), code = "F09", cycle = 2024L, kind = "totals", role = "x", stringsAsFactors = FALSE)
  g2 <- fi_os_fetch_gate(f$opensecrets_fetch, rbind(p, tot), unlist(f$opensecrets_frozen$codes), frt = tempfile(), inventory = inv)
  expect_false(g2$ok); expect_match(g2$reason, "frozen capture inventory")
  g3 <- fi_os_fetch_gate(f$opensecrets_fetch, data.frame(url = fi_os_url("F03", 2026L, "candidates_H"), code = "F03", cycle = 2026L, kind = "candidates_H", role = "x"),
                         unlist(f$opensecrets_frozen$codes), frt = tempfile(), inventory = inv)
  expect_false(g3$ok); expect_match(g3$reason, "frozen baseline")
})

test_that("the P&C registry entry records the F09 ruling and the scope caveat, and no longer expects F3400", {
  f <- fi_read_feeds()
  os <- f$industries$property_casualty$opensecrets
  expect_equal(unlist(os$codes), "F09")
  expect_equal(os$code_status, "owner_ruling_f09_all_insurance")
  expect_equal(os$baseline_scope_caveat, "OpenSecrets baseline F09 = all insurance; Mentat P&C roster and IBISWorld 52412 are P&C only.")
  expect_equal(os$superseded_candidate$code, "F3400")
  expect_false("bulk" %in% unlist(os$routes_allowed))
  expect_equal(unlist(f$opensecrets_frozen$codes), c("F03", "F05", "T2100"))        # F09 is governed page by page, not closed as a code
})

test_that("plan and gate are page-level: pages in the inventory are left out, the rest of the same code is planned, a captured page is refused", {
  d <- local_ingest_env()
  ip <- write_inv(file.path(d$root, "inv.json"), list(list("Z9900", 2014L, "totals"), list("Z9900", 2012L, "presidential")))
  f <- feeds_inv(ip); inv <- fi_os_frozen_inventory(f)
  p <- fi_os_fetch_plan(f, "widgets", inventory = inv)
  expect_equal(nrow(p), 5L)                                                          # 7 expected, totals 2014 and presidential 2012 already captured
  expect_false(any(p$kind == "totals")); expect_false(any(p$kind == "presidential" & p$cycle == 2012L))
  expect_equal(nrow(attr(p, "present")), 2L)
  w <- make_world()
  bad <- rbind(p, attr(p, "present")[1, ])
  r <- fi_os_fetch_run(bad, f$opensecrets_fetch, unlist(f$opensecrets_frozen$codes), d$frt, w$transport, w$clock, w$sleep, inventory = inv)
  expect_length(w$calls, 0L); expect_equal(r$stopped$kind, "gate"); expect_match(r$stopped$reason, "frozen capture inventory")
})

test_that("a registry that names an inventory that is missing or unreadable stops (fail closed); none named means no inventory", {
  f <- synth_feeds()
  expect_null(fi_os_frozen_inventory(f))
  f$opensecrets_frozen$inventory <- file.path(tempdir(), "no_such_inventory.json")
  expect_error(fi_os_frozen_inventory(f), "it is missing")
  d <- local_ingest_env(); w <- make_world()
  out <- capture.output(r <- fi_run("opensecrets-fetch", industry = "widgets", run_id = "inv-missing", feeds = f, events_dir = d$glassbox, verbose = FALSE, transport = w$transport, clock = w$clock, sleep = w$sleep))
  expect_equal(r$status, "failed"); expect_match(r$message, "it is missing"); expect_length(w$calls, 0L)
})

test_that("END TO END: only the missing pages are requested; the receipt tabulates present / fetched; proposals reference the frozen totals and the run-relative outputs; the scope caveat is in the receipt", {
  d <- local_ingest_env()
  ip <- write_inv(file.path(d$root, "inv.json"), list(list("Z9900", 2014L, "totals"), list("Z9900", 2012L, "presidential")))
  f <- feeds_inv(ip)
  w <- make_world()
  r <- fi_run("opensecrets-fetch", industry = "widgets", run_id = "inv-ok", feeds = f, events_dir = d$glassbox, verbose = FALSE, transport = w$transport, clock = w$clock, sleep = w$sleep)
  expect_equal(r$status, "ok")
  expect_equal(calls_to(w, "industries"), 5L)
  expect_equal(calls_to(w, "totals"), 0L); expect_equal(calls_to(w, "recipdetail=P&|recipdetail=P$") + 0L, 1L)    # only the 2014 presidential page is requested
  expect_match(w$calls[[2]]$url, "recipdetail=H")                                                     # verification comes from the first candidate page
  rc <- jsonlite::fromJSON(r$receipt$path, simplifyVector = FALSE)
  ver <- Filter(function(z) grepl("verified from the site", z$name), rc$checks)
  expect_equal(ver[[1]]$failures, 0L)
  tb <- do.call(rbind, lapply(rc$extra$inventory_table, as.data.frame, stringsAsFactors = FALSE))
  expect_equal(nrow(tb), 7L)
  expect_equal(as.integer(table(tb$status)[["fetched"]]), 5L); expect_equal(as.integer(table(tb$status)[["present_in_frozen_captures"]]), 2L)
  expect_true(any(grepl("inventory: 7 expected page", unlist(rc$decisions))))
  expect_equal(rc$extra$scope_caveats$widgets, "synthetic caveat")
  expect_true(any(grepl("scope (widgets): synthetic caveat", unlist(rc$decisions), fixed = TRUE)))
  pi <- jsonlite::fromJSON(file.path(r$run_dir, "proposed_inputs.json"), simplifyVector = FALSE)
  roles <- vapply(pi$inputs, function(e) e$role, "")
  expect_true("os_totals:widgets" %in% roles)
  tot <- pi$inputs[[which(roles == "os_totals:widgets")]]
  expect_match(tot$path, "^\\{LOO\\}/outputs/x/Z9900_2014_totals.json$"); expect_match(tot$supplied_by, "not re-fetched")
  rec <- pi$inputs[[which(roles == "os_recipients:widgets:2012:H")]]
  expect_equal(rec$path_run_relative, "outputs/os/Z9900_2012_candidates_H.json")
  pr <- jsonlite::fromJSON(file.path(r$run_dir, "proposed_registry_patch.json"), simplifyVector = FALSE)$patch$industries$widgets$opensecrets
  expect_equal(pr$status, "supplied"); expect_match(pr$totals, "\\{LOO\\}")
  expect_equal(pr$recipients_run_relative, "outputs/os/Z9900_{cycle}_candidates_{chamber}.json")
  expect_equal(pr$baseline_scope_caveat, "synthetic caveat")
})

test_that("every receipt states the baseline scope: an IBISWorld run with the real registry carries the F09 caveat verbatim", {
  d <- local_ingest_env()
  r <- fi_run("ibisworld", run_id = "scope-ibis", feeds = fi_read_feeds(), events_dir = d$glassbox, verbose = FALSE)
  rc <- jsonlite::fromJSON(r$receipt$path, simplifyVector = FALSE)
  cav <- "OpenSecrets baseline F09 = all insurance; Mentat P&C roster and IBISWorld 52412 are P&C only."
  expect_equal(rc$extra$scope_caveats$property_casualty, cav)
  expect_true(any(grepl(paste0("scope (property_casualty): ", cav), unlist(rc$decisions), fixed = TRUE)))
  r2 <- fi_run("opensecrets-fetch", industry = "property_casualty", dry_run = TRUE, run_id = "scope-dry", feeds = fi_read_feeds(), events_dir = d$glassbox, verbose = FALSE)
  expect_equal(r2$status, "ok")
})

test_that("a recipient page of F09 verifies: the heading 'Insurance Recipients' is the industry name and the canonical code is F09", {
  crumbs <- list(c("Home", "/"), c("Interest Groups", "/industries"), c("Finance/Insur/RealEst", "/industries/indus?ind=F"), c("Insurance", "/industries/indus?ind=F09"))
  html <- synth_os_html(synth_candidate_rows(5, "H"), heading = "Insurance Recipients", canonical = fi_os_url("F09", 2012L, "candidates_H"), crumbs = crumbs)
  v <- fi_os_verify_category_page(fi_os_page_meta(html), "F09", "^insurance$")
  expect_true(v$verified); expect_equal(v$heading, "Insurance")
  wrong <- synth_os_html(synth_candidate_rows(5, "H"), heading = "Commercial Banks Recipients", canonical = fi_os_url("F09", 2012L, "candidates_H"), crumbs = crumbs)
  expect_false(fi_os_verify_category_page(fi_os_page_meta(wrong), "F09", "^insurance$")$verified)
  # the real registry's expectation is this regular expression
  f <- fi_read_feeds()
  expect_true(grepl(f$industries$property_casualty$opensecrets$expect$catname_regex, "Insurance", ignore.case = TRUE, perl = TRUE))
  expect_false(grepl(f$industries$property_casualty$opensecrets$expect$catname_regex, "Life Insurance", ignore.case = TRUE, perl = TRUE))
})

test_that("a challenged run still proposes the baseline: frozen totals by reference, recipient pages missing, the attempt with its Cloudflare evidence; a later gate-closed run repeats it from fetch_state.json without a request", {
  d <- local_ingest_env()
  ip <- write_inv(file.path(d$root, "inv.json"), list(list("Z9900", 2014L, "totals")))
  f <- feeds_inv(ip); f$industries$widgets$opensecrets$baseline_scope_caveat <- "synthetic baseline caveat"; f$industries$widgets$opensecrets$code_status <- "owner_ruling_synthetic"
  w <- make_world(function(url) if (grepl("robots", url)) resp(200, ROBOTS_2026, list(), "text/plain") else
    resp(403, "<html><title>Just a moment...</title></html>", list(`cf-mitigated` = "challenge", `cf-ray` = "abc-IAD", `set-cookie` = "x=y")))
  r <- fi_run("opensecrets-fetch", industry = "widgets", run_id = "blk-1", feeds = f, events_dir = d$glassbox, verbose = FALSE, transport = w$transport, clock = w$clock, sleep = w$sleep)
  expect_equal(r$status, "stopped"); expect_equal(r$stop_reason, "cf_mitigated_header")
  pi <- jsonlite::fromJSON(file.path(r$run_dir, "proposed_inputs.json"), simplifyVector = FALSE)
  expect_equal(vapply(pi$inputs, function(e) e$role, ""), "os_totals:widgets")
  expect_match(pi$inputs[[1]]$path, "^\\{LOO\\}/outputs/x/Z9900_2014_totals.json$")
  pr <- jsonlite::fromJSON(file.path(r$run_dir, "proposed_registry_patch.json"), simplifyVector = FALSE)$patch$industries$widgets$opensecrets
  expect_equal(unlist(pr$codes), "Z9900"); expect_equal(pr$code_status, "owner_ruling_synthetic"); expect_equal(pr$baseline_scope_caveat, "synthetic baseline caveat")
  expect_match(pr$totals, "\\{LOO\\}"); expect_null(pr$recipients); expect_equal(pr$status, "missing"); expect_match(pr$reason, "0 of 6 planned pages accepted; stopped: cf_mitigated_header")
  expect_equal(pr$verification_attempt$http, 403L); expect_equal(pr$verification_attempt$cf_mitigated, "challenge")
  expect_false(grepl("x=y", paste(readLines(file.path(r$run_dir, "proposed_registry_patch.json")), collapse = ""), fixed = TRUE))
  # the next run: the gate is closed (sticky flag), zero requests, and the proposals still carry the recorded attempt
  n <- length(w$calls)
  r2 <- fi_run("opensecrets-fetch", industry = "widgets", run_id = "blk-2", feeds = f, events_dir = d$glassbox, verbose = FALSE, transport = w$transport, clock = w$clock, sleep = w$sleep)
  expect_equal(length(w$calls), n); expect_equal(r2$status, "stopped")
  pr2 <- jsonlite::fromJSON(file.path(r2$run_dir, "proposed_registry_patch.json"), simplifyVector = FALSE)$patch$industries$widgets$opensecrets
  expect_equal(pr2$verification_attempt$http, 403L); expect_equal(pr2$verification_attempt$cf_mitigated, "challenge"); expect_false(is.null(pr2$verification_attempt$stop_recorded_utc))
  expect_equal(pr2$status, "missing")
})

test_that("the receipt records the parsed row count of every page (data rows, header excluded)", {
  d <- local_ingest_env(); w <- make_world()
  r <- fi_run("opensecrets-fetch", industry = "widgets", run_id = "rows-ok", feeds = synth_feeds(), events_dir = d$glassbox, verbose = FALSE, transport = w$transport, clock = w$clock, sleep = w$sleep)
  rc <- jsonlite::fromJSON(r$receipt$path, simplifyVector = FALSE)
  rows <- stats::setNames(vapply(rc$outputs, function(o) o$rows %||% NA_integer_, 0L), vapply(rc$outputs, function(o) basename(o$path), ""))
  expect_equal(rows[["Z9900_2014_candidates_H.json"]], 6L); expect_equal(rows[["Z9900_2012_candidates_S.json"]], 6L)
  expect_equal(rows[["Z9900_2012_presidential.json"]], 3L); expect_equal(rows[["Z9900_2014_totals.json"]], 2L)
})

test_that("proposed inputs of fetched pages carry the page's retrieval time as their vintage", {
  d <- local_ingest_env(); w <- make_world()
  r <- fi_run("opensecrets-fetch", industry = "widgets", run_id = "vint-ok", feeds = synth_feeds(), events_dir = d$glassbox, verbose = FALSE, transport = w$transport, clock = w$clock, sleep = w$sleep)
  pi <- jsonlite::fromJSON(file.path(r$run_dir, "proposed_inputs.json"), simplifyVector = FALSE)
  v <- vapply(pi$inputs, function(e) e$vintage, "")
  expect_true(all(grepl("^[0-9]{4}-[0-9]{2}-[0-9]{2}T", v)))
})

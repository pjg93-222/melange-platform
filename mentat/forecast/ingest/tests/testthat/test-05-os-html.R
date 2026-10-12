# OpenSecrets page HTML -> capture envelope (FEEDS_SPEC 8 test 5) on SYNTHETIC DataTable-Partial pages: full rows, money edge cases, the
# truncated-save guard, the totals identity, page identity checks, category verification, and the round trip through the refit's
# own loaders (rf_os_totals / rf_os_candidates / rf_os_presidential). A local-only test checks the parser against real captures.

env_of <- function(html, kind, code = "Z9900", cycle = 2014L, route = "manual_save", url = NULL) {
  fi_os_envelope(html, url %||% fi_os_url(code, cycle, kind), route = route, source_sha = "abc123", expect = list(code = code, cycle = cycle, kind = kind))
}

canon <- function(kind, code = "Z9900", cycle = 2014L) fi_os_url(code, cycle, kind)

test_that("money text: dollars, commas, negatives, and the U+2212 minus sign", {
  expect_equal(rf_money_int("$1,234"), 1234)
  expect_equal(rf_money_int("-$6,792"), -6792)
  expect_equal(rf_money_int("−$6,792"), -6792)
  expect_equal(rf_money_int(" $0 "), 0)
  expect_true(is.na(suppressWarnings(rf_money_int("n/a"))))
})

test_that("totals page: header, money, identity, cycles; the envelope carries the release note and the page identity", {
  html <- synth_os_html(synth_totals_rows(), heading = "Widget Makers Summary", canonical = canon("totals"),
                        extra = "Data for the current election cycle were released by the Federal Election Commission on February 06, 2025")
  r <- env_of(html, "totals")
  expect_true(r$ok); expect_length(r$issues, 0L)
  expect_equal(r$envelope$fec_release_note, "Data for the current election cycle were released by the Federal Election Commission on February 06, 2025")
  expect_equal(r$envelope$page$heading, "Widget Makers Summary")
  expect_equal(r$envelope$route, "manual_save"); expect_equal(r$envelope$source_file_sha256, "abc123")
  expect_equal(length(r$envelope$tables[[1]]$rows), 3L)                 # header + two cycles
  expect_equal(r$envelope$parser$name, "fi_os_html")
  expect_match(r$envelope$parser$code_sha256, "^[0-9a-f]{64}$")
  expect_true(all(r$checks$failures == 0L))
})

test_that("totals identity: total == individuals + PACs + outside is enforced", {
  rows <- synth_totals_rows()
  rows[[2]][2] <- money_txt(1)                                          # break the total
  r <- env_of(synth_os_html(rows, canonical = canon("totals")), "totals")
  expect_false(r$ok)
  expect_true("totals_identity_fails" %in% r$issues)
  rows2 <- synth_totals_rows(); rows2[[1]][4] <- "Contributions from Committees"
  expect_true("unexpected_totals_header" %in% env_of(synth_os_html(rows2, canonical = canon("totals")), "totals")$issues)
  rows3 <- synth_totals_rows(cycles = c(2014, 2014))
  expect_true("duplicate_or_missing_cycles" %in% env_of(synth_os_html(rows3, canonical = canon("totals")), "totals")$issues)
})

test_that("candidate page: every row is parsed (a long table), refunds are kept, row links are recorded, whitespace and nbsp are collapsed", {
  rows <- synth_candidate_rows(120, "H")
  rows[[2]][1] <- "Smith,   Person1 (R-TX)"
  html <- synth_os_html(rows, heading = "Widget Makers Summary", canonical = canon("candidates_H", cycle = 2014L))
  r <- env_of(html, "candidates_H")
  expect_true(r$ok)
  tb <- r$envelope$source_tables[[1]]$rows
  expect_equal(length(tb), 121L)
  expect_equal(as.character(tb[[2]])[1], "Smith, Person1 (R-TX)")
  expect_true(any(vapply(tb, function(z) grepl("^-\\$", z[3]), NA)))     # a refund row
  expect_equal(length(r$envelope$source_tables[[1]]$row_links), 121L)
})

test_that("NEGATIVE CONTROL: a saved page that holds only the rendered 25 rows of 864 entries is rejected as truncated", {
  rows <- synth_candidate_rows(25, "H")
  html <- synth_os_html(rows, canonical = canon("candidates_H", cycle = 2014L), extra = "Showing 1 to 25 of 864 entries")
  r <- env_of(html, "candidates_H")
  expect_false(r$ok)
  expect_true("truncated_save" %in% r$issues)
  expect_equal(r$checks$failures[grepl("truncated", r$checks$name)], 1L)
  # the same 25 rows with no marker (a complete small table), and all 864 rows with the marker, are accepted
  expect_true(env_of(synth_os_html(rows, canonical = canon("candidates_H", cycle = 2014L)), "candidates_H")$ok)
  expect_true(env_of(synth_os_html(synth_candidate_rows(864, "H"), canonical = canon("candidates_H", cycle = 2014L), extra = "Showing 1 to 25 of 864 entries"), "candidates_H")$ok)
})

test_that("senate and presidential pages: header per chamber; a non-President row or a missing party code is rejected", {
  s <- env_of(synth_os_html(synth_candidate_rows(6, "S"), canonical = canon("candidates_S", cycle = 2014L)), "candidates_S")
  expect_true(s$ok)
  # a House table presented as the Senate page
  expect_true("unexpected_recipient_header_for_candidates_S" %in% env_of(synth_os_html(synth_candidate_rows(6, "H"), canonical = canon("candidates_S", cycle = 2014L)), "candidates_S")$issues)
  p <- env_of(synth_os_html(synth_presidential_rows(), canonical = canon("presidential", cycle = 2014L)), "presidential")
  expect_true(p$ok)
  bad <- synth_presidential_rows(); bad[[3]][3] <- "Senate"
  expect_true("non_presidential_row" %in% env_of(synth_os_html(bad, canonical = canon("presidential", cycle = 2014L)), "presidential")$issues)
  nop <- synth_candidate_rows(4, "H"); nop[[2]][1] <- "Nameless Person"
  expect_true("recipient_without_party_code" %in% env_of(synth_os_html(nop, canonical = canon("candidates_H", cycle = 2014L)), "candidates_H")$issues)
})

test_that("page identity: a canonical URL for another code or cycle, a title without OpenSecrets, or no canonical at all is an issue", {
  ok_rows <- synth_candidate_rows(4, "H")
  wrong_code <- synth_os_html(ok_rows, canonical = canon("candidates_H", code = "Z1111", cycle = 2014L))
  expect_true(any(grepl("page_code_Z1111_differs", env_of(wrong_code, "candidates_H")$issues)))
  wrong_cycle <- synth_os_html(ok_rows, canonical = canon("candidates_H", cycle = 2012L))
  expect_true(any(grepl("page_cycle_2012_differs", env_of(wrong_cycle, "candidates_H")$issues)))
  no_title <- synth_os_html(ok_rows, canonical = canon("candidates_H", cycle = 2014L), title = "Some Other Site")
  expect_true("title_lacks_OpenSecrets" %in% env_of(no_title, "candidates_H")$issues)
  no_canon <- sub("<link rel=\"canonical\"[^>]*>", "", synth_os_html(ok_rows, canonical = "x"))
  expect_true("page_identity_not_stated" %in% env_of(no_canon, "candidates_H")$issues)
})

test_that("URL helpers: planned URLs round-trip to (code, cycle, kind); manual-save file names are understood", {
  for (k in FI_OS_KINDS) {
    u <- fi_os_url("F3400", 2016L, k)
    t <- fi_os_target_from_url(u)
    expect_equal(t$code, "F3400"); expect_equal(t$cycle, 2016L); expect_equal(t$kind, k)
  }
  expect_equal(fi_os_url("F3400", 2012L, "candidates_H"), "https://www.opensecrets.org/industries/summary?code=F3400&cycle=2012&ind=F3400&mem=N&recipdetail=H")
  expect_equal(fi_os_url("F3400", 2012L, "presidential"), "https://www.opensecrets.org/industries/recips?code=F3400&cycle=2012&ind=F3400&recipdetail=P")
  expect_equal(fi_os_url("F3400", 2024L, "totals"), "https://www.opensecrets.org/industries/totals?cycle=2024&ind=F3400")
  expect_true(is.na(fi_os_target_from_url("https://www.opensecrets.org/industries/methodology")$kind))
  f <- fi_os_target_from_filename("F3400_2012_candidates_S (1).html")
  expect_equal(f$code, "F3400"); expect_equal(f$cycle, 2012L); expect_equal(f$kind, "candidates_S")
  expect_null(fi_os_target_from_filename("page.html"))
})

test_that("tidy rows: totals and recipients have the FEEDS_SPEC 7.2 columns, party and state from the label, name key from rf_name_key", {
  tot <- env_of(synth_os_html(synth_totals_rows(), canonical = canon("totals")), "totals")$envelope
  tt <- fi_os_tidy_totals(tot, "widgets")
  expect_identical(names(tt), c("industry_id", "os_code", "cycle", "total", "individuals", "pacs", "outside", "democrats", "republicans", "pct_dem", "pct_rep", "route",
                                "recipe_version", "source_sha256", "fec_release_note", "retrieved_utc", "status"))
  expect_equal(tt$cycle, c(2014L, 2012L)); expect_equal(tt$pacs, c(1000000, 800000)); expect_equal(tt$pct_dem, c(40, 40))
  cand <- env_of(synth_os_html(synth_candidate_rows(6, "H"), canonical = canon("candidates_H", cycle = 2014L)), "candidates_H")$envelope
  tc <- fi_os_tidy_candidates(cand, "widgets")
  expect_identical(names(tc), c("industry_id", "os_code", "cycle", "chamber", "recipient_label", "name_key", "party", "state", "amount", "cid", "fec_cand_id", "dist_id_run_for", "route", "source_sha256"))
  expect_equal(tc$chamber, rep("H", 6L)); expect_equal(tc$party[1:2], c("R", "D"))
  expect_equal(tc$name_key[1], rf_name_key(tc$recipient_label[1]))
  expect_equal(tc$amount[3], -6792)
  pres <- env_of(synth_os_html(synth_presidential_rows(), canonical = canon("presidential", cycle = 2014L)), "presidential")$envelope
  tp <- fi_os_tidy_candidates(pres, "widgets")
  expect_equal(tp$chamber, rep("P", 3L)); expect_true(all(is.na(tp$state))); expect_equal(tp$amount[1], 5790673)
})

test_that("envelopes written to disk are read unchanged by the refit loaders (rf_os_totals, rf_os_candidates, rf_os_presidential)", {
  d <- local_ingest_env()
  p1 <- file.path(d$root, "Z9900_2014_totals.json"); fi_os_write_envelope(env_of(synth_os_html(synth_totals_rows(), canonical = canon("totals")), "totals")$envelope, p1)
  t <- rf_os_totals(p1)
  expect_equal(t$cycle_end, c(2012L, 2014L)); expect_equal(t$pac_nominal, c(800000, 1000000))
  expect_equal(t$total_nominal, t$individual_nominal + t$pac_nominal + t$outside_nominal)
  rows <- synth_candidate_rows(7, "S")
  p2 <- file.path(d$root, "Z9900_2014_candidates_S.json")
  fi_os_write_envelope(env_of(synth_os_html(rows, canonical = canon("candidates_S", cycle = 2014L)), "candidates_S")$envelope, p2)
  c2 <- rf_os_candidates(p2, "S")
  expect_equal(nrow(c2), 7L); expect_equal(c2$amount[3], -6792); expect_equal(c2$party[1], "R")
  p3 <- file.path(d$root, "Z9900_2014_presidential.json")
  fi_os_write_envelope(env_of(synth_os_html(synth_presidential_rows(), canonical = canon("presidential", cycle = 2014L)), "presidential")$envelope, p3)
  pr <- rf_os_presidential(p3)
  expect_equal(pr$amount, c(5790673, 2100000, 250)); expect_equal(pr$name_key[1], "nominee, alpha")
  # a one-cell row must survive JSON as an array, not a scalar
  j <- jsonlite::fromJSON(p3, simplifyVector = FALSE)
  expect_true(is.list(j$source_tables[[1]]$rows[[1]]))
})

test_that("category verification from the site's own page: code, name and parent industry", {
  crumbs_ok <- list(c("Home", "/"), c("Interest Groups", "/industries"), c("Synthetic Sector", "/industries/indus?ind=Z"), c("Synthetic Parent", "/industries/indus?ind=Z99"),
                    c("Widget Makers", "/industries/indus?ind=Z9900"))
  page <- function(heading = "Widget &amp; Gadget Makers Summary", canonical = canon("totals"), crumbs = crumbs_ok) synth_os_html(synth_totals_rows(), heading = heading, canonical = canonical, crumbs = crumbs)
  ver <- function(html) fi_os_verify_category_page(fi_os_page_meta(html), "Z9900", "widget\\s*(&|and)\\s*gadget", "Z99", "Synthetic Parent")
  v <- ver(page())
  expect_true(v$verified); expect_equal(v$heading, "Widget & Gadget Makers"); expect_equal(v$parent, "Synthetic Parent"); expect_length(v$issues, 0L)
  expect_false(ver(page(heading = "Gizmo Makers Summary"))$verified)                                               # wrong name
  expect_false(ver(page(canonical = canon("totals", code = "Z1111")))$verified)                                    # page is about another code
  other_parent <- crumbs_ok; other_parent[[4]] <- c("Other Industry", "/industries/indus?ind=Q55")
  v2 <- ver(page(crumbs = other_parent)); expect_false(v2$verified); expect_true(any(grepl("under_Q55_not_Z99", v2$issues)))   # a different parent contradicts
  not_shown <- crumbs_ok[-4]
  v3 <- ver(page(crumbs = not_shown)); expect_true(v3$verified); expect_true(is.na(v3$parent)); expect_true(any(grepl("not_shown", v3$notes)))   # parent absent: noted, not a contradiction
  v4 <- ver(page(crumbs = crumbs_ok[1:3])); expect_false(v4$verified)                                               # the code is not at the end of the trail
})

test_that("the saved-pages CLI path: accepts good pages, skips the frozen baseline codes, rejects unregistered codes and bad pages, leaves the inbox alone", {
  d <- local_ingest_env()
  pg <- file.path(d$inbox, "opensecrets", "pages"); dir.create(pg, recursive = TRUE)
  put <- function(name, html) writeLines(html, file.path(pg, name), useBytes = TRUE)
  put("Z9900_2014_totals.html", synth_os_html(synth_totals_rows(), canonical = canon("totals")))
  put("Z9900_2014_candidates_H.html", synth_os_html(synth_candidate_rows(6, "H"), canonical = canon("candidates_H", cycle = 2014L)))
  put("F03_2014_candidates_H.html", synth_os_html(synth_candidate_rows(6, "H"), canonical = fi_os_url("F03", 2014L, "candidates_H")))          # frozen baseline code
  put("Q5500_2014_candidates_H.html", synth_os_html(synth_candidate_rows(6, "H"), canonical = fi_os_url("Q5500", 2014L, "candidates_H")))        # not in the registry
  put("Z9900_2012_candidates_S.html", synth_os_html(synth_candidate_rows(25, "S"), canonical = canon("candidates_S", cycle = 2012L), extra = "Showing 1 to 25 of 181 entries"))   # truncated
  before <- fi_inbox_state(pg)
  r <- fi_run("opensecrets-pages", run_id = "os-pages", feeds = synth_feeds(), events_dir = d$glassbox, verbose = FALSE)
  expect_identical(fi_inbox_state(pg), before)
  expect_equal(r$status, "review_required")
  rc <- jsonlite::fromJSON(r$receipt$path, simplifyVector = FALSE)
  roles <- vapply(rc$outputs, function(o) o$role %||% "", "")
  expect_true(all(c("os_totals:widgets", "os_recipients:widgets:2014:H", "os_industry_totals", "os_candidate_recipients") %in% roles))
  expect_false(any(grepl("F03|Q5500|2012:S", roles)))
  issues <- unlist(rc$issues)
  expect_true(any(grepl("Q5500.*unregistered_code", issues))); expect_true(any(grepl("truncated_save", issues)))
  expect_true(any(grepl("frozen baseline", unlist(rc$decisions))))
  expect_true(file.exists(file.path(r$run_dir, "outputs", "os", "Z9900_2014_candidates_H.json")))
})

test_that("LOCAL-ONLY: the parser reproduces the Codex capture of real pages (rows and row links), and their totals through rf_os_totals", {
  loo <- rf_roots()$LOO
  dd <- file.path(loo, "outputs/opensecrets_partisanship/direct_sources"); bd <- file.path(loo, "outputs/opensecrets_baseline/direct_sources")
  skip_if_not(file.exists(file.path(dd, "F03_2012_candidates_H.source.html")) && file.exists(file.path(bd, "F03_2024_totals.html")), "real OpenSecrets captures are not on this machine")
  as_html <- function(p) { h <- rawToChar(readBin(p, "raw", file.size(p))); Encoding(h) <- "UTF-8"; h }
  for (k in c("F03_2012_candidates_H", "F03_2012_candidates_S", "F03_2012_presidential")) {
    cap <- jsonlite::fromJSON(file.path(dd, paste0(k, ".json")), simplifyVector = FALSE)
    tabs <- Filter(function(t) length(t$rows) > 0L, fi_os_parse_tables(as_html(file.path(dd, paste0(k, ".source.html")))))
    expect_identical(lapply(tabs, function(t) lapply(t$rows, as.character)), lapply(cap$source_tables, function(t) lapply(t$rows, function(r) as.character(unlist(r)))), info = k)
    expect_identical(lapply(tabs, function(t) lapply(t$row_links, as.character)), lapply(cap$source_tables, function(t) lapply(t$row_links, function(r) as.character(unlist(r)))), info = k)
  }
  d <- local_ingest_env()
  for (code in c("F03", "F09")) {
    cap_path <- file.path(bd, sprintf("%s_2024_totals.json", code))
    r <- fi_os_envelope(as_html(file.path(bd, sprintf("%s_2024_totals.html", code))), fi_os_url(code, 2024L, "totals"), route = "manual_save",
                        expect = list(code = code, cycle = 2024L, kind = "totals"))
    expect_true(r$ok, info = paste(r$issues, collapse = ";"))
    mine <- file.path(d$root, sprintf("%s.json", code)); fi_os_write_envelope(r$envelope, mine)
    a <- rf_os_totals(mine); b <- rf_os_totals(cap_path)
    expect_equal(a[, 1:7], b[, 1:7])
    expect_match(r$envelope$fec_release_note, "February 06, 2025")
  }
})

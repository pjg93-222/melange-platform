# The permission-gated polite fetcher (FEEDS_SPEC 8 test 8). No test touches the network: an injected fake transport records every call,
# and a test fails if it is called when it must not be. A fake clock drives the rate limiter. The 2026-09-30 robots.txt is used as a
# fixture (plain robots rules, not page content).

test_that("GATE: no permission attestation -> STOP with zero transport calls", {
  d <- local_ingest_env(); w <- make_world()
  f <- synth_feeds(); f$opensecrets_fetch$permission <- NULL
  r <- run_fetch(f, w, d$frt)
  expect_length(w$calls, 0L)
  expect_equal(r$stopped$kind, "gate"); expect_match(r$stopped$reason, "no permission attestation")
  expect_true(all(r$pages$status == "not_attempted"))
  f2 <- synth_feeds(); f2$opensecrets_fetch$permission$basis <- ""
  r2 <- run_fetch(f2, w, d$frt); expect_length(w$calls, 0L); expect_match(r2$stopped$reason, "no permission attestation")
})

test_that("GATE: no contact for the User-Agent -> STOP with zero calls; MENTAT_OS_CONTACT satisfies it", {
  d <- local_ingest_env(); w <- make_world()
  f <- synth_feeds(); f$opensecrets_fetch$user_agent_contact <- NULL
  r <- run_fetch(f, w, d$frt)
  expect_length(w$calls, 0L); expect_match(r$stopped$reason, "no contact for the User-Agent")
  withr::local_envvar(MENTAT_OS_CONTACT = "owner@example.test")
  r2 <- run_fetch(f, w, d$frt)
  expect_null(r2$stopped); expect_gt(length(w$calls), 0L)
  expect_match(w$calls[[1]]$ua, "owner@example.test", fixed = TRUE)
})

test_that("GATE: a stopped fetch_state.json keeps every later run closed until the owner clears it", {
  d <- local_ingest_env(); w <- make_world()
  fi_fetch_state_stop("http_403", "https://www.opensecrets.org/x", NULL, d$frt)
  r <- run_fetch(synth_feeds(), w, d$frt)
  expect_length(w$calls, 0L); expect_match(r$stopped$reason, "says stopped")
  rf_write_json_atomic(list(stopped = FALSE), fi_fetch_state_path(d$frt))                              # the owner clears it
  expect_null(run_fetch(synth_feeds(), w, d$frt)$stopped)
})

test_that("GATE: a URL outside the allowed patterns or host, or of a frozen baseline code, stops the run before any request", {
  d <- local_ingest_env()
  f <- synth_feeds()
  base <- fi_os_fetch_plan(f, "widgets")
  for (bad in c("https://www.opensecrets.org/about/terms-and-conditions", "https://evil.example.test/industries/totals?cycle=2014&ind=Z9900",
                fi_os_url("F03", 2014L, "totals"), fi_os_url("T2100", 2012L, "candidates_H"), fi_os_url("F09", 2024L, "totals"))) {
    w <- make_world()
    plan <- rbind(base, data.frame(url = bad, code = "x", cycle = 2014L, kind = "totals", role = "x", stringsAsFactors = FALSE))
    r <- fi_os_fetch_run(plan, f$opensecrets_fetch, unlist(f$opensecrets_frozen$codes), d$frt, w$transport, w$clock, w$sleep)
    expect_length(w$calls, 0L)
    expect_equal(r$stopped$kind, "gate")
  }
  w <- make_world()
  plan <- rbind(base, data.frame(url = fi_os_url("F05", 2012L, "totals"), code = "F05", cycle = 2012L, kind = "totals", role = "x", stringsAsFactors = FALSE))
  r <- fi_os_fetch_run(plan, f$opensecrets_fetch, unlist(f$opensecrets_frozen$codes), d$frt, w$transport, w$clock, w$sleep)
  expect_match(r$stopped$reason, "frozen baseline"); expect_length(w$calls, 0L)
})

test_that("STOP: HTTP 403 with the Cloudflare 'Just a moment' page halts at once, writes fetch_state.json with the evidence, and the next run stays closed", {
  d <- local_ingest_env()
  body <- "<html><head><title>Just a moment...</title></head><body>Performing security verification <script src=\"/cdn-cgi/challenge-platform/h/b/orchestrate/chl_page/v1\"></script></body></html>"
  w <- make_world(function(url) if (grepl("robots", url)) resp(200, ROBOTS_2026, list(), "text/plain") else resp(403, body, list(`cf-mitigated` = "challenge", server = "cloudflare")))
  events <- list(); emit <- function(kind, status, stage, summary, detail = "", metadata = NULL) events[[length(events) + 1L]] <<- list(kind = kind, status = status, metadata = metadata)
  r <- run_fetch(synth_feeds(), w, d$frt, emit = emit)
  expect_equal(r$stopped$kind, "block"); expect_equal(r$stopped$reason, "cf_mitigated_header")
  expect_equal(calls_to(w, "opensecrets.org/industries"), 1L)                         # nothing after the first blocked page: no retry, no second URL
  expect_equal(r$requests, 2L)                                                        # robots.txt + the one page
  st <- jsonlite::fromJSON(fi_fetch_state_path(d$frt), simplifyVector = FALSE)
  expect_true(st$stopped); expect_equal(st$reason, "cf_mitigated_header"); expect_match(st$url, "industries/totals")
  expect_equal(st$evidence$http, 403L); expect_match(st$evidence$body_excerpt, "Just a moment", fixed = TRUE); expect_equal(st$evidence$headers$`cf-mitigated`, "challenge")
  expect_true(any(vapply(events, function(e) e$kind == "feeds.fetch.stop" && e$metadata$reason == "cf_mitigated_header", NA)))
  expect_true(tail(r$network, 1)[[1]]$stop_reason == "cf_mitigated_header")
  w2 <- make_world()
  r2 <- run_fetch(synth_feeds(), w2, d$frt)
  expect_length(w2$calls, 0L); expect_equal(r2$stopped$kind, "gate")                  # the next run makes no request at all
})

test_that("STOP: each challenge signal on its own - 403 body, Cloudflare header on a 200, captcha text, 401/407/429/503 - halts with no retry", {
  cases <- list(
    list(resp(403, "<html><title>Just a moment...</title></html>"), "http_403"),
    list(resp(200, "<html><title>OpenSecrets</title></html>", list(`cf-mitigated` = "challenge")), "cf_mitigated_header"),
    list(resp(200, "<html><title>Checking</title><body>Please solve the CAPTCHA</body></html>"), "challenge_page_body"),
    list(resp(200, "<html><title>OpenSecrets</title><body>Performing security verification</body></html>"), "challenge_page_body"),
    list(resp(200, "<html><title>OpenSecrets</title><script src='/cdn-cgi/challenge-platform/x'></script></html>"), "challenge_page_body"),
    list(resp(401, "<html><title>OpenSecrets</title></html>"), "http_401"), list(resp(407, ""), "http_407"),
    list(resp(429, "<html><title>OpenSecrets</title></html>", list(`retry-after` = "120")), "http_429"), list(resp(503, "<html><title>OpenSecrets</title></html>"), "http_503"),
    list(resp(200, "%PDF-1.4", list(), "application/pdf"), "non_html_content_type:application/pdf"),
    list(resp(200, "<html><title>Some Other Site</title></html>"), "title_without_OpenSecrets"))
  for (cs in cases) {
    d <- local_ingest_env()
    w <- make_world(function(url) if (grepl("robots", url)) resp(200, ROBOTS_2026, list(), "text/plain") else cs[[1]])
    r <- run_fetch(synth_feeds(), w, d$frt)
    expect_equal(r$stopped$kind, "block", info = cs[[2]]); expect_equal(r$stopped$reason, cs[[2]])
    expect_equal(calls_to(w, "industries"), 1L, info = cs[[2]])
    expect_length(w$sleeps[w$sleeps >= 30], 0L)                                       # no waiting-and-retrying on a block
    expect_true(jsonlite::fromJSON(fi_fetch_state_path(d$frt))$stopped)
  }
})

test_that("a missing page (404) halts the run but is a data fact, not a block: no sticky stop flag", {
  d <- local_ingest_env()
  w <- make_world(function(url) if (grepl("robots", url)) resp(200, ROBOTS_2026, list(), "text/plain") else resp(404, "<html><title>Page not found - OpenSecrets</title></html>"))
  r <- run_fetch(synth_feeds(), w, d$frt)
  expect_equal(r$stopped$kind, "error"); expect_match(r$stopped$reason, "http_404")
  expect_false(isTRUE(fi_fetch_state_read(d$frt)$stopped))
})

test_that("a 5xx other than 503: exactly one retry after Retry-After (or 60 s); a second failure stops", {
  d <- local_ingest_env(); n <- 0L
  w <- make_world(function(url) if (grepl("robots", url)) resp(200, ROBOTS_2026, list(), "text/plain") else { n <<- n + 1L; if (n == 1L) resp(502, "<html></html>", list(`retry-after` = "7")) else serve_page(url) })
  r <- run_fetch(synth_feeds(), w, d$frt)
  expect_null(r$stopped)
  expect_true(7 %in% w$sleeps)
  d2 <- local_ingest_env()
  w2 <- make_world(function(url) if (grepl("robots", url)) resp(200, ROBOTS_2026, list(), "text/plain") else resp(500, "<html></html>"))
  r2 <- run_fetch(synth_feeds(), w2, d2$frt)
  expect_equal(r2$stopped$reason, "http_500_after_retry"); expect_true(60 %in% w2$sleeps)
  expect_equal(calls_to(w2, "industries"), 2L)
})

test_that("redirects: same-host followed (and counted); off-site or to a login page STOPS", {
  d <- local_ingest_env(); hops <- 0L
  w <- make_world(function(url) if (grepl("robots", url)) resp(200, ROBOTS_2026, list(), "text/plain") else if (grepl("totals\\?cycle=2014&ind=Z9900$", url) && hops == 0L) { hops <<- 1L; resp(301, "", list(location = "/industries/totals?ind=Z9900&cycle=2014")) } else serve_page(url))
  r <- run_fetch(synth_feeds(), w, d$frt)
  expect_null(r$stopped)
  expect_true(calls_to(w, "totals\\?ind=Z9900&cycle=2014") == 1L)
  for (loc in c("https://evil.example.test/industries/totals", "https://www.opensecrets.org/users/sign_in", "//evil.example.test/x")) {
    d2 <- local_ingest_env()
    w2 <- make_world(function(url) if (grepl("robots", url)) resp(200, ROBOTS_2026, list(), "text/plain") else resp(302, "", list(location = loc)))
    r2 <- run_fetch(synth_feeds(), w2, d2$frt)
    expect_equal(r2$stopped$kind, "block", info = loc); expect_true(r2$stopped$reason %in% c("redirect_off_site", "redirect_to_login"), info = loc)
  }
})

test_that("robots.txt: the 2026-09-30 rules parse; the listed paths are disallowed, the industry pages allowed; PiplBot / GPTBot groups do not bind us", {
  g <- fi_robots_parse(ROBOTS_2026)
  expect_length(g, 3L)
  for (p in c("/widgets/x", "/export_data", "/export_data?x=1", "/admin", "/api/v1", "/donate", "/donate/thank-you", "/404", "/500"))
    expect_false(fi_robots_allowed(g, p), info = p)
  for (p in c("/industries/totals?cycle=2024&ind=F3400", "/industries/summary?code=F3400&cycle=2012&ind=F3400&mem=N&recipdetail=H", "/industries/recips?code=F3400&cycle=2012&ind=F3400&recipdetail=P", "/about/terms-and-conditions"))
    expect_true(fi_robots_allowed(g, p), info = p)
  expect_false(fi_robots_allowed(g, "/industries/totals", agent = "gptbot"))                   # the group that names GPTBot
  expect_true(fi_robots_allowed(g, "/industries/totals", agent = "mentat-giving-forecast"))
  g2 <- fi_robots_parse("User-agent: *\nDisallow: /industries/\nAllow: /industries/totals\nDisallow: /*.pdf$\nDisallow:")
  expect_true(fi_robots_allowed(g2, "/industries/totals?x=1")); expect_false(fi_robots_allowed(g2, "/industries/recips")); expect_false(fi_robots_allowed(g2, "/files/a.pdf")); expect_true(fi_robots_allowed(g2, "/files/a.pdf.html"))
  expect_true(fi_robots_allowed(fi_robots_parse(""), "/anything"))
})

test_that("robots.txt Disallow skips the URL without a request; a non-200 robots response stops the run before any page", {
  d <- local_ingest_env()
  rb <- sub("Disallow: /widgets/", "Disallow: /industries/recips\nDisallow: /widgets/", ROBOTS_2026, fixed = TRUE)   # the rule joins the "*" group
  w <- make_world(function(url) if (grepl("robots", url)) resp(200, rb, list(), "text/plain") else serve_page(url))
  r <- run_fetch(synth_feeds(), w, d$frt)
  expect_equal(sum(r$pages$status == "skipped_robots"), 2L)                                  # the two presidential pages (recips)
  expect_equal(calls_to(w, "industries/recips"), 0L)
  expect_equal(sum(r$pages$status == "fetched"), 5L)
  d2 <- local_ingest_env()
  w2 <- make_world(function(url) resp(503, "x", list(), "text/plain"))
  r2 <- run_fetch(synth_feeds(), w2, d2$frt)
  expect_equal(r2$stopped$kind, "block"); expect_length(w2$calls, 1L)
  d3 <- local_ingest_env()
  w3 <- make_world(function(url) resp(404, "no robots", list(), "text/plain"))
  r3 <- run_fetch(synth_feeds(), w3, d3$frt)
  expect_equal(r3$stopped$kind, "error"); expect_length(w3$calls, 1L)
})

test_that("rate limit with a fake clock: at least 4 s between requests, never below the 3 s floor", {
  d <- local_ingest_env(); w <- make_world()
  r <- run_fetch(synth_feeds(), w, d$frt)
  t <- vapply(w$calls, function(z) z$t, 0)
  expect_equal(length(t), 8L)                                                                # robots + totals + 6 pages
  expect_gte(min(diff(t)), 4 + 0.3 - 1e-9)                                                   # 4 s after the previous response completed (latency 0.3)
  expect_true(all(w$sleeps >= 0) && length(w$sleeps) >= 7L)
  d2 <- local_ingest_env(); w2 <- make_world()
  f <- synth_feeds(); f$opensecrets_fetch$min_interval_s <- 1                                 # asks for faster than the floor: the floor wins
  run_fetch(f, w2, d2$frt)
  expect_gte(min(diff(vapply(w2$calls, function(z) z$t, 0))), 3 + 0.3 - 1e-9)
})

test_that("cache: each URL is fetched once; a second run is all cache hits with zero calls; --refresh fetches again", {
  d <- local_ingest_env(); w <- make_world()
  r1 <- run_fetch(synth_feeds(), w, d$frt)
  expect_equal(sum(r1$pages$status == "fetched"), 7L)
  n1 <- length(w$calls)
  r2 <- run_fetch(synth_feeds(), w, d$frt)
  expect_equal(length(w$calls), n1)                                                          # zero new calls (robots.txt cached too)
  expect_true(all(r2$pages$status == "cache_hit"))
  expect_true(all(vapply(r2$network, function(z) isTRUE(z$cache_hit), NA)))
  expect_equal(r2$pages$sha256, r1$pages$sha256)
  for (u in r1$pages$url) { cd <- fi_os_cache_dir(u, d$frt); expect_true(all(file.exists(file.path(cd, c("body.html", "headers.txt", "meta.json"))))) }
  r3 <- run_fetch(synth_feeds(), w, d$frt, refresh = TRUE)
  expect_equal(length(w$calls), n1 + 7L)
  expect_true(all(r3$pages$status == "fetched"))
})

test_that("terms check (optional): a changed terms hash STOPS; a matching hash passes", {
  body <- "<html><title>Terms - OpenSecrets</title><body>terms v1</body></html>"
  serve <- function(b) function(url) if (grepl("robots", url)) resp(200, ROBOTS_2026, list(), "text/plain") else if (grepl("terms-and-conditions", url)) resp(200, b) else serve_page(url)
  f <- synth_feeds(); f$opensecrets_fetch$terms_sha256_reviewed <- fi_sha256_raw(charToRaw(body))
  d <- local_ingest_env(); w <- make_world(serve(body))
  expect_null(run_fetch(f, w, d$frt)$stopped)
  expect_equal(calls_to(w, "terms-and-conditions"), 1L)
  d2 <- local_ingest_env(); w2 <- make_world(serve(sub("v1", "v2", body)))
  r2 <- run_fetch(f, w2, d2$frt)
  expect_equal(r2$stopped$reason, "terms_changed_since_review"); expect_equal(calls_to(w2, "industries"), 0L)
})

test_that("max_requests_per_run caps the run; the rest is not attempted", {
  d <- local_ingest_env(); w <- make_world()
  f <- synth_feeds(); f$opensecrets_fetch$max_requests_per_run <- 4L
  r <- run_fetch(f, w, d$frt)
  expect_equal(r$stopped$reason, "max_requests_per_run_reached")
  expect_length(w$calls, 4L)
  expect_equal(sum(r$pages$status == "not_attempted"), 4L)
  expect_false(isTRUE(fi_fetch_state_read(d$frt)$stopped))
})

test_that("the User-Agent is descriptive, names the tool and its operator, is identical on every request, and never imitates a browser", {
  d <- local_ingest_env(); w <- make_world()
  run_fetch(synth_feeds(), w, d$frt)
  uas <- unique(vapply(w$calls, function(z) z$ua, ""))
  expect_length(uas, 1L)
  expect_match(uas, "^Mentat-Giving-Forecast/[0-9.]+ \\(research data fetcher; operator Test Owner, Test Org\\)$")
  expect_false(grepl("Mozilla|Chrome|Safari|AppleWebKit|Firefox|curl/", uas))
  expect_true(all(vapply(w$calls, function(z) z$timeout == 60, NA)))
})

test_that("the real transport is the only thing that opens a connection; its handle follows no redirects and keeps no cookies", {
  body <- deparse(fi_curl_transport)
  expect_true(any(grepl("followlocation = FALSE", body, fixed = TRUE)))
  expect_false(any(grepl("cookie|proxy", body, ignore.case = TRUE)))
})

test_that("END TO END (fake transport): verification of the code comes first, then the planned pages; envelopes, receipt network log, proposals and Glass Box events", {
  d <- local_ingest_env(); w <- make_world()
  r <- fi_run("opensecrets-fetch", industry = "widgets", run_id = "fx-ok", feeds = synth_feeds(), events_dir = d$glassbox, verbose = FALSE,
              transport = w$transport, clock = w$clock, sleep = w$sleep)
  expect_equal(r$status, "ok"); expect_equal(r$exit_code, 0L)
  expect_match(w$calls[[1]]$url, "robots.txt$"); expect_match(w$calls[[2]]$url, "industries/totals\\?cycle=2014&ind=Z9900")          # robots first, then the totals page that verifies the code
  rc <- jsonlite::fromJSON(r$receipt$path, simplifyVector = FALSE)
  expect_length(rc$network, 8L)
  expect_true(all(vapply(rc$network, function(z) all(c("url", "utc", "http", "bytes", "cache_hit", "stop_reason") %in% names(z)), NA)))
  ver <- Filter(function(z) grepl("verified from the site", z$name), rc$checks)
  expect_length(ver, 1L); expect_equal(ver[[1]]$failures, 0L)
  expect_equal(rc$extra$code_verification$heading, "Widget & Gadget Makers"); expect_equal(rc$extra$code_verification$parent, "Synthetic Parent")
  outs <- vapply(rc$outputs, function(o) basename(o$path), "")
  expect_true(all(c("Z9900_2014_totals.json", "Z9900_2012_candidates_H.json", "Z9900_2014_candidates_S.json", "Z9900_2012_presidential.json", "os_industry_totals.csv", "os_candidate_recipients.csv", "proposed_inputs.json") %in% outs))
  # envelopes are readable by the refit loaders
  od <- file.path(r$run_dir, "outputs", "os")
  expect_equal(nrow(rf_os_totals(file.path(od, "Z9900_2014_totals.json"))), 2L)
  expect_equal(nrow(rf_os_candidates(file.path(od, "Z9900_2012_candidates_H.json"), "H")), 6L)
  # every fetched page is in the vault; inputs carry their sha256
  inp <- Filter(function(z) z$kind == "html", rc$inputs); expect_length(inp, 7L)
  for (z in inp) { expect_true(file.exists(z$vault_path)); expect_equal(unname(tools::sha256sum(z$vault_path)), z$sha256) }
  pr <- jsonlite::fromJSON(file.path(r$run_dir, "proposed_registry_patch.json"), simplifyVector = FALSE)$patch$industries$widgets$opensecrets
  expect_equal(pr$page_verification$heading, "Widget & Gadget Makers"); expect_equal(pr$status, "supplied")
  expect_match(pr$recipients, "Z9900_{cycle}_candidates_{chamber}.json", fixed = TRUE)
  k <- vapply(read_events(d$glassbox), function(e) e$kind, "")
  expect_true(all(c("feeds.fetch.gate", "feeds.fetch.request", "feeds.manifest.proposed", "feeds.run.done") %in% k)); expect_false("feeds.fetch.stop" %in% k)
  expect_equal(sum(k == "feeds.fetch.request"), 8L)
})

test_that("END TO END: a code whose page does not verify stops the run after the first page - no further request, no data written", {
  for (bad in list(list(heading = "Gizmo Makers Summary", crumbs = CRUMBS_OK),
                   list(heading = "Widget &amp; Gadget Makers Summary", crumbs = list(c("Home", "/"), c("Other Parent", "/industries/indus?ind=Q55"), c("Widget Makers", "/industries/indus?ind=Z9900"))))) {
    d <- local_ingest_env()
    w <- make_world(function(url) if (grepl("robots", url)) resp(200, ROBOTS_2026, list(), "text/plain") else serve_page(url, bad$heading, bad$crumbs))
    r <- fi_run("opensecrets-fetch", industry = "widgets", run_id = "fx-bad", feeds = synth_feeds(), events_dir = d$glassbox, verbose = FALSE,
                transport = w$transport, clock = w$clock, sleep = w$sleep)
    expect_equal(r$status, "stopped"); expect_equal(r$exit_code, 3L); expect_match(r$stop_reason, "category_verification_failed")
    expect_length(w$calls, 2L)                                                                # robots.txt and the one verifying page
    expect_false(dir.exists(file.path(r$run_dir, "outputs", "os")))
    rc <- jsonlite::fromJSON(r$receipt$path, simplifyVector = FALSE)
    expect_equal(Filter(function(z) grepl("verified from the site", z$name), rc$checks)[[1]]$failures, 1L)
    expect_false(isTRUE(fi_fetch_state_read(d$frt)$stopped))                                   # a failed verification is not a block
  }
})

test_that("END TO END: a block mid-run keeps the pages already accepted, marks the run stopped, and tells the owner what to do", {
  d <- local_ingest_env(); n <- 0L
  w <- make_world(function(url) if (grepl("robots", url)) resp(200, ROBOTS_2026, list(), "text/plain") else { n <<- n + 1L; if (n >= 4L) resp(403, "<html><title>Just a moment...</title></html>", list(`cf-mitigated` = "challenge")) else serve_page(url) })
  out <- capture.output(r <- fi_run("opensecrets-fetch", industry = "widgets", run_id = "fx-block", feeds = synth_feeds(), events_dir = d$glassbox, verbose = TRUE,
                                    transport = w$transport, clock = w$clock, sleep = w$sleep))
  expect_equal(r$status, "stopped"); expect_equal(r$stop_reason, "cf_mitigated_header")
  expect_true(any(grepl("blocked or challenged automated access", out, fixed = TRUE)))
  expect_true(any(grepl("allowlisted access method", out, fixed = TRUE)) && any(grepl("bulk-data files in the inbox", out, fixed = TRUE)))
  expect_true(isTRUE(fi_fetch_state_read(d$frt)$stopped))
  rc <- jsonlite::fromJSON(r$receipt$path, simplifyVector = FALSE)
  expect_equal(rc$status, "stopped")
  expect_equal(tail(rc$network, 1)[[1]]$stop_reason, "cf_mitigated_header"); expect_equal(tail(rc$network, 1)[[1]]$http, 403L)
  expect_equal(rc$extra$fetch$stopped$evidence$http, 403L)
  expect_true(file.exists(file.path(r$run_dir, "outputs", "os", "Z9900_2014_totals.json")))         # the three accepted pages were kept
  ev <- vapply(read_events(d$glassbox), function(e) e$kind, "")
  expect_true("feeds.fetch.stop" %in% ev); expect_equal(tail(ev, 1), "feeds.run.fail")
  # no partial registry patch: the capture paths are proposed only when every planned page was accepted
  pr <- jsonlite::fromJSON(file.path(r$run_dir, "proposed_registry_patch.json"), simplifyVector = FALSE)$patch$industries$widgets$opensecrets
  expect_null(pr$totals); expect_equal(pr$status, "missing"); expect_match(pr$reason, "not all captured")
})

test_that("a dry run plans the pages and makes no request", {
  d <- local_ingest_env(); w <- make_world()
  out <- capture.output(r <- fi_run("opensecrets-fetch", industry = "widgets", dry_run = TRUE, run_id = "fx-dry", feeds = synth_feeds(), events_dir = d$glassbox, verbose = TRUE,
                                    transport = w$transport, clock = w$clock, sleep = w$sleep))
  expect_length(w$calls, 0L); expect_equal(r$status, "ok")
  expect_true(any(grepl("would fetch 7 page", out)))
})

test_that("the plan for the real P&C entry with no inventory is 22 pages: one totals page then H, S and presidential for each cycle 2012-2024", {
  f <- fi_read_feeds()
  p <- fi_os_fetch_plan(f, "property_casualty")
  expect_equal(nrow(p), 22L)
  expect_equal(p$url[1], "https://www.opensecrets.org/industries/totals?cycle=2024&ind=F09")
  expect_equal(sum(p$kind == "candidates_H"), 7L); expect_equal(sum(p$kind == "candidates_S"), 7L); expect_equal(sum(p$kind == "presidential"), 7L)
  expect_equal(sort(unique(p$cycle[p$kind != "totals"])), seq(2012L, 2024L, 2L))
  g <- fi_os_fetch_gate(f$opensecrets_fetch, p[p$kind != "totals", ], unlist(f$opensecrets_frozen$codes), frt = tempfile(), inventory = fi_os_frozen_inventory(f))
  expect_true(g$ok)
  expect_match(g$user_agent, "^Mentat-Giving-Forecast/.* \\(research data fetcher; operator Paul Goff, Melange Systems\\)$")
  expect_false(grepl("@", g$user_agent))                                                   # no e-mail address goes into a request header
  expect_equal(f$opensecrets_fetch$permission$who, "Paul Goff"); expect_equal(f$opensecrets_fetch$permission$date, "2026-09-30")
  expect_equal(f$opensecrets_fetch$permission$basis, "owner states explicit permission from OpenSecrets")
  expect_equal(f$opensecrets_fetch$min_interval_s, 4)
})

test_that("a redirect of the code's page to the organisation's apex host (the site's answer for a page that does not exist) stops the run, leaves the code unverified, and is not recorded as a block", {
  d <- local_ingest_env()
  w <- make_world(function(url) if (grepl("robots", url)) resp(200, ROBOTS_2026, list(), "text/plain") else resp(302, "<html><body>You are being redirected.</body></html>", list(location = "http://opensecrets.org/industries", `set-cookie` = "s=1")))
  out <- capture.output(r <- fi_run("opensecrets-fetch", industry = "widgets", run_id = "fx-apex", feeds = synth_feeds(), events_dir = d$glassbox, verbose = TRUE,
                                    transport = w$transport, clock = w$clock, sleep = w$sleep))
  expect_equal(r$status, "stopped"); expect_equal(r$exit_code, 3L)
  expect_match(r$stop_reason, "^redirect_to_other_opensecrets_host:http://opensecrets.org/industries")
  expect_length(w$calls, 2L)                                                                 # robots.txt and the one page; no second try, no follow
  expect_false(isTRUE(fi_fetch_state_read(d$frt)$stopped))                                   # not a bot block: no sticky flag
  expect_false(any(grepl("blocked or challenged", out, fixed = TRUE)))
  rc <- jsonlite::fromJSON(r$receipt$path, simplifyVector = FALSE)
  ver <- Filter(function(z) grepl("verified from the site", z$name), rc$checks)
  expect_length(ver, 1L); expect_equal(ver[[1]]$failures, 1L); expect_match(ver[[1]]$detail, "not verified")
  expect_false(rc$extra$code_verification$verified)
  expect_equal(rc$extra$fetch$stopped$evidence$http, 302L)
  pr <- jsonlite::fromJSON(file.path(r$run_dir, "proposed_registry_patch.json"), simplifyVector = FALSE)$patch$industries$widgets$opensecrets
  expect_equal(pr$code_status, "unverified_recall"); expect_equal(pr$verification_attempt$http, 302L); expect_equal(pr$verification_attempt$location, "http://opensecrets.org/industries")
  expect_false(dir.exists(file.path(r$run_dir, "outputs", "os")))
})

test_that("block evidence keeps the status and headers but drops the client's IP address and session cookie", {
  d <- local_ingest_env()
  w <- make_world(function(url) if (grepl("robots", url)) resp(200, ROBOTS_2026, list(), "text/plain") else
    resp(403, "<html><title>Just a moment...</title></html>", list(`set-cookie` = "_s=secret", `cf-connecting-ip` = "203.0.113.9", `x-forwarded-for` = "203.0.113.9", `cf-ray` = "abc-IAD", server = "cloudflare")))
  r <- run_fetch(synth_feeds(), w, d$frt)
  h <- r$stopped$evidence$headers
  expect_true(all(c("cf-ray", "server", "content-type") %in% names(h)))
  expect_false(any(c("set-cookie", "cf-connecting-ip", "x-forwarded-for") %in% names(h)))
  txt <- paste(readLines(fi_fetch_state_path(d$frt), warn = FALSE), collapse = "\n")
  expect_false(grepl("203.0.113.9", txt, fixed = TRUE)); expect_false(grepl("_s=secret", txt, fixed = TRUE))
  expect_equal(names(fi_redact_headers(NULL)), NULL)
})

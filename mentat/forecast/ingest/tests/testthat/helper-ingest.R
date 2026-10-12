# Shared helpers of the ingest test suite: load the modules, isolate every folder the ingest writes (inbox, runtime, vault, Glass Box)
# in a temp directory, and build SYNTHETIC fixtures (invented names and numbers; no IBISWorld or OpenSecrets content). No helper
# or test touches the network.

if (!requireNamespace("readxl", quietly = TRUE) || !requireNamespace("xml2", quietly = TRUE)) .libPaths(c("C:/Users/pgoff/AppData/Local/R/win-library/4.6", .libPaths()))
assign(".FI_HOME", normalizePath(file.path(testthat::test_path(), "..", ".."), winslash = "/", mustWork = TRUE), envir = globalenv())
.FI_HOME <- get(".FI_HOME", envir = globalenv())
source(file.path(.FI_HOME, "R", "fi_config.R"), encoding = "UTF-8")
REAL_LOCALAPPDATA <- Sys.getenv("LOCALAPPDATA", unset = "")
.fi_test_tmp <- file.path(tempdir(), "fi_test_runtime")
dir.create(.fi_test_tmp, recursive = TRUE, showWarnings = FALSE)
Sys.setenv(MENTAT_FEEDS_INBOX = file.path(.fi_test_tmp, "inbox"), FI_FRT = file.path(.fi_test_tmp, "frt"), RF_RUNTIME = file.path(.fi_test_tmp, "runtime"),
           RF_GLASSBOX_DIR = file.path(.fi_test_tmp, "glassbox"))
fi_source_all(.FI_HOME)

# A fresh isolated set of folders for one test; environment variables are restored when the test ends.
local_ingest_env <- function(env = parent.frame()) {
  root <- tempfile("fi_case_")
  d <- list(root = root, inbox = file.path(root, "inbox"), frt = file.path(root, "frt"), runtime = file.path(root, "runtime"), glassbox = file.path(root, "glassbox"))
  for (p in d) dir.create(p, recursive = TRUE, showWarnings = FALSE)
  withr::local_envvar(MENTAT_FEEDS_INBOX = d$inbox, FI_FRT = d$frt, RF_RUNTIME = d$runtime, RF_GLASSBOX_DIR = d$glassbox, MENTAT_OS_CONTACT = "", .local_envir = env)
  withr::defer(unlink(root, recursive = TRUE), envir = env)
  d$inbox <- normalizePath(d$inbox, winslash = "/"); d$frt <- normalizePath(d$frt, winslash = "/")
  d
}

# Glass Box events written in a folder, as parsed lists sorted by file name (= time).
read_events <- function(dir) {
  f <- sort(list.files(dir, pattern = "^evt-.*\\.json$", full.names = TRUE))
  lapply(f, function(p) jsonlite::fromJSON(p, simplifyVector = FALSE))
}

# ---- synthetic IBIS report text and PDFs ----------------------------------------------------------------------------------------------

IBIS_DOM <- paste0("my.ibis", "world.com")                     # built piecewise: no source file spells the publisher's host
SYN_TITLE <- "Synthetic Widget Carriers in the US"

# Invented revenue series: deterministic, non-constant growth so that a one-row shift changes the recomputed growth.
syn_revenue <- function(years, start = 100000) {
  g <- 0.02 + 0.03 * sin(seq_along(years) * 1.3)
  round(start * cumprod(1 + c(0, g[-1])), 1)
}
syn_change <- function(v) c(NA_real_, round(100 * (v[-1] / v[-length(v)] - 1), 1))
fmt_num <- function(x, d = 1) formatC(x, format = "f", digits = d, big.mark = ",")

# L3 (modern): Key Statistics > Industry Data > Values (Year | Revenue ($ Million) | IVA ...), running onto a second page, then Annual Change.
#   pub: c(year, month_abbr); years: table years; shift: paste each year label with the NEXT year's revenue (the xpdf failure)
synth_l3_pages <- function(code = "99912", title = SYN_TITLE, pub = list(2021, "Mar"), years = 2008:2026, base = 2021, shift = FALSE, footnote = TRUE,
                           start = 100000, url = TRUE) {
  v <- syn_revenue(years, start); ch <- syn_change(v)
  lab <- years
  vv <- v
  if (shift) vv <- c(v[-1], NA)
  keep <- !is.na(vv)
  rows <- sprintf("%d            %s        %s              %s          %s", lab[keep], fmt_num(vv[keep]), fmt_num(vv[keep] * 0.2), "1,200", "3,400")
  half <- ceiling(length(rows) / 2)
  hdr <- sprintf("IBISWorld | %s                                    %s %d", title, pub[[2]], pub[[1]])
  keyurl <- if (url) sprintf("https://%s/us/en/industry/%s/key-statistics", IBIS_DOM, code) else ""
  list(
    sprintf("%s\nINDUSTRY REPORT\n%s %s %d", hdr, title, pub[[2]], pub[[1]]),
    "Table of Contents\n1. About this industry ........ 3\n10. Key Statistics ........ 6\n",
    "About this industry\nSynthetic filler text about widgets.\n",
    paste(c(hdr, "", "10. Key Statistics", keyurl, "", "Industry Data", "Values", " Year               Revenue              IVA       Establishments   Enterprises",
            "                ($ Million)        ($ Million)         (Units)        (Units)", "", rows[seq_len(half)]), collapse = "\n"),
    paste(c(hdr, "", rows[(half + 1):length(rows)], "", if (footnote) sprintf("*Figures are inflation adjusted to %d", base) else "", "", "  82"), collapse = "\n"),
    paste(c(hdr, "", "Annual Change", " Year          Revenue (%)      IVA (%)", "", sprintf("%d         %s         %s", years[-1], fmt_num(ch[-1]), fmt_num(ch[-1] / 2)),
            "", "Key Ratios", "2021     9.9    8.8"), collapse = "\n")
  )
}

# L2 (mid period): one wide table, Revenue ($m) first, then Annual Change. glossary = TRUE uses the glossary rule for the price base.
synth_l2_pages <- function(code = "99912", title = SYN_TITLE, pub = list(2014, "December"), years = 2008:2022, base = pub[[1]], glossary = FALSE, start = 500000) {
  v <- syn_revenue(years, start); ch <- syn_change(v)
  rows <- sprintf("%d                       %s   %s      21,880          4,668     637,115        --        --", years, fmt_num(v), fmt_num(v * 0.12))
  hdr <- sprintf("WWW.SYNTHETIC-REPORTS.EXAMPLE      %s %s %d   35", title, pub[[2]], pub[[1]])
  list(
    sprintf("%s\nCover page\n%s%s %d 1 Saving up: synthetic teaser", "WWW.SYNTHETIC-REPORTS.EXAMPLE", title, pub[[2]], pub[[1]]),
    "Table of Contents\nKey Statistics ........ 35\n",
    "Filler\n",
    paste(c(hdr, "", "Key Statistics", "Industry Data                        Industry", "                           Revenue     Value Added   Establish-     Enterprises  Employment",
            "                             ($m)        ($m)        ments", rows, "Sector Rank                  4/31     3/31    15/31    18/31", "",
            "Annual Change                        Industry  Establish-", "                        Revenue     Value Added   ments",
            sprintf("%d                      %s      %s      %s", years[-1], fmt_num(ch[-1]), fmt_num(ch[-1] * 1.1), fmt_num(ch[-1] / 3)), "Key Ratios", "2008   1.5   2.5"), collapse = "\n"),
    if (glossary) paste("CONSTANT PRICES. Key Statistics including forecasts, are adjusted for inflation using the", "(year published) as the base year.") else
      sprintf("Note: revenue is inflation adjusted to %d.", base)
  )
}

# L1 (legacy): Outlook table (Revenue $ Million | Growth %); the price base comes from "In YYYY constant dollars" on the next page.
#   day_first = TRUE prints the cover date as "28 December 2006"
synth_l1_pages <- function(code = "99912", title = "Synthetic Widget Carriers in the US", pub = list(2008, "January", 10), years = 2007:2013, constant = 2006, day_first = FALSE,
                           real_rate = FALSE, start = 500000) {
  v <- syn_revenue(years, start); ch <- syn_change(v)
  date_txt <- if (day_first) sprintf("%d %s %d", pub[[3]], pub[[2]], pub[[1]]) else sprintf("%s %d %d", pub[[2]], pub[[3]], pub[[1]])
  rows <- sprintf("%d                                      %s                 %s", years, fmt_num(v), ifelse(is.na(ch), "N/A", fmt_num(ch)))
  list(
    sprintf("IBISWorld Industry Report %s %s: %s DISCLAIMER", date_txt, title, code),
    "Contents\nKey Statistics ........ 5\n",
    "Filler\n",
    paste(c("OUTLOOK", paste0("          ", title), paste0("          ", date_txt), "", "Outlook", "Revenue", "Revenue $ Million         Growth %", rows,
            "", if (real_rate) "Revenue grew at an average real rate of 2.1% per annum." else "", "Gross Product $ Million     Growth %", "2007   119,001.1   -5.7"), collapse = "\n"),
    if (!is.na(constant)) sprintf("Industry Value Added   1.2\nNote: In %d constant dollars.\n", constant) else "Industry Value Added   1.2\n"
  )
}

# A valid minimal multi-page PDF (Courier text, one line per row) written to `path`. pdftools reads it back page by page.
make_text_pdf <- function(path, pages) {
  esc <- function(s) gsub("([()\\\\])", "\\\\\\1", s)
  objs <- list("<< /Type /Catalog /Pages 2 0 R >>", NULL, "<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>")
  kids <- character()
  for (i in seq_along(pages)) {
    lines <- strsplit(pages[[i]], "\n", fixed = TRUE)[[1]]
    ys <- 760 - 11 * (seq_along(lines) - 1L)
    stream <- paste(sprintf("BT /F1 8 Tf 30 %d Td (%s) Tj ET", ys, vapply(lines, esc, "")), collapse = "\n")
    po <- 4L + 2L * (i - 1L)
    objs[[po]] <- sprintf("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents %d 0 R /Resources << /Font << /F1 3 0 R >> >> >>", po + 1L)
    objs[[po + 1L]] <- sprintf("<< /Length %d >>\nstream\n%s\nendstream", nchar(stream, "bytes"), stream)
    kids <- c(kids, sprintf("%d 0 R", po))
  }
  objs[[2]] <- sprintf("<< /Type /Pages /Kids [%s] /Count %d >>", paste(kids, collapse = " "), length(pages))
  out <- "%PDF-1.4\n"; offs <- integer()
  for (i in seq_along(objs)) { offs <- c(offs, nchar(out, "bytes")); out <- paste0(out, i, " 0 obj\n", objs[[i]], "\nendobj\n") }
  xr <- nchar(out, "bytes")
  out <- paste0(out, "xref\n0 ", length(objs) + 1L, "\n0000000000 65535 f \n", paste(sprintf("%010d 00000 n \n", offs), collapse = ""),
                "trailer\n<< /Size ", length(objs) + 1L, " /Root 1 0 R >>\nstartxref\n", xr, "\n%%EOF\n")
  dir.create(dirname(path), recursive = TRUE, showWarnings = FALSE)
  writeBin(charToRaw(out), path)
  invisible(path)
}

# A synthetic feeds registry (R list, same shape as registry/feeds.json) with two IBISWorld industries: one fed from the inbox
# (`widgets`, code 99912) and one pinned to its workbook (`gadgets`, code 99913).
synth_feeds <- function() {
  list(schema_version = 1L,
       opensecrets_frozen = list(codes = list("F03", "F05", "T2100", "F09")),
       opensecrets_fetch = list(permission = list(kind = "owner_attestation", who = "Test Owner", date = "2026-09-30", basis = "synthetic test attestation", evidence_file = NULL),
                                user_agent_contact = "operator Test Owner, Test Org", allowed_hosts = list("www.opensecrets.org"),
                                url_patterns = list("^https://www\\.opensecrets\\.org/industries/(totals|summary|recips|indus)\\?"),
                                min_interval_s = 4, min_interval_floor_s = 3, max_requests_per_run = 25, request_timeout_s = 60, terms_sha256_reviewed = NULL),
       industries = list(
         widgets = list(opensecrets = list(codes = list("Z9900"), route = "fetch", routes_allowed = list("fetch", "bulk", "manual_save"),
                                           expect = list(catname_regex = "widget\\s*(&|and)\\s*gadget", catorder = "Z99", parent_industry_name = "Synthetic Parent"),
                                           cycles_required = list(2012, 2014), totals_cycle_anchor = 2014, calibration_codes = list("F03", "F05"), scope_caveat = "synthetic caveat"),
                        ibisworld = list(code = "99912", titles = list("Synthetic Widget Carriers in the US"), study_sector = "widgets", actual_vintage_policy = "single_latest_vintage",
                                         actual_min_edition = "2022-01", actual_first_year = 2010, predicted_rule = "direct", predicted_targets = list(2009, 2024),
                                         scope_qualification = "synthetic scope"), status = "pilot"),
         gadgets = list(ibisworld = list(code = "99913", titles = list("Synthetic Gadget Makers in the US"), study_sector = "gadgets", actual_vintage_policy = "pinned_workbook",
                                         predicted_rule = "direct", predicted_targets = list(2009, 2024)), status = "fitted")),
       bulk_recipes = list(list(version = "v1", individuals = list(recipient_prefix = "N", exclude_types = list()), pacs = list(di = list("D")),
                                totals = list(pacs_di = list("D"), individuals_recipient_prefix = "N"))))
}

# Write a synthetic report PDF into `dir` and return its path. kind: "l3", "l2" or "l1"; extra arguments go to the page builder.
write_synth_pdf <- function(dir, name, kind = "l3", ...) {
  pages <- switch(kind, l3 = synth_l3_pages(...), l2 = synth_l2_pages(...), l1 = synth_l1_pages(...))
  make_text_pdf(file.path(dir, name), pages)
}

# ---- synthetic OpenSecrets HTML -----------------------------------------------------------------------------------------------------------

money_txt <- function(a) paste0(ifelse(a < 0, "-", ""), "$", formatC(abs(a), format = "f", digits = 0, big.mark = ","))

synth_table_html <- function(rows, class = "DataTable-Partial") {
  th <- function(cells) paste0("<tr>", paste0("<th>", cells, "</th>", collapse = ""), "</tr>")
  td <- function(cells) paste0("<tr>", paste0("<td>", cells, "</td>", collapse = ""), "</tr>")
  body <- vapply(rows[-1], td, "")
  sprintf("<table class=\"%s\"><thead>%s</thead><tbody>%s</tbody></table>", class, th(rows[[1]]), paste(body, collapse = ""))
}

# A server-rendered OpenSecrets-like page: title, canonical link, breadcrumbs, hero heading, one data table.
synth_os_html <- function(rows, heading = "Widget Makers Summary", canonical = "https://www.opensecrets.org/industries/totals?cycle=2014&ind=Z9900",
                          crumbs = list(c("Home", "/"), c("Interest Groups", "/industries"), c("Synthetic Sector", "/industries/indus?ind=Z"), c("Widget Makers", "/industries/indus?ind=Z9900")),
                          title = NULL, extra = "", table_class = "DataTable-Partial") {
  title <- title %||% paste(heading, "\u2014 OpenSecrets")
  bc <- paste0("<li class=\"Breadcrumbs-item\"><a class=\"Breadcrumbs-link\" href=\"", vapply(crumbs, `[`, "", 2L), "\">", vapply(crumbs, `[`, "", 1L), "</a></li>", collapse = "")
  sprintf("<!DOCTYPE html><html><head><meta charset=\"utf-8\"><title>%s</title><link rel=\"canonical\" href=\"%s\"></head><body><div class=\"Breadcrumbs\"><ol class=\"Breadcrumbs-items\">%s</ol></div><h1 class=\"Hero-title\">%s</h1>%s<p>%s</p></body></html>",
          title, gsub("&", "&amp;", canonical, fixed = TRUE), bc, heading, synth_table_html(rows, table_class), extra)
}

# Totals rows (header + one row per cycle) with the identity total = individuals + PACs + outside.
synth_totals_rows <- function(cycles = c(2014, 2012), pac = c(1000000, 800000), indiv = c(2000000, 1500000), outside = c(300000, 100000)) {
  c(list(RF_TOTALS_HEADER), lapply(seq_along(cycles), function(i) {
    tot <- pac[i] + indiv[i] + outside[i]
    c(as.character(cycles[i]), money_txt(tot), money_txt(indiv[i]), money_txt(pac[i]), money_txt(outside[i]), money_txt(tot * 0.4), money_txt(tot * 0.55), "40.00%", "55.00%")
  }))
}

synth_candidate_rows <- function(n = 5, chamber = "H", seed = 1) {
  surnames <- c("Smith", "Jones", "Brown", "Miller", "Davis", "Wilson", "Moore", "Taylor", "Clark", "Lewis", "Young", "Hall", "Allen", "King", "Wright")
  states <- c("TX", "OH", "CA", "NY", "FL")
  st_name <- FI_OS_STATE_NAMES[states]
  party <- rep(c("R", "D"), length.out = n)
  amt <- as.numeric(rev(seq_len(n)) * 10000 + 500)
  if (n >= 3) amt[3] <- -6792
  hdr <- list(c(if (chamber == "H") "Representative" else "Senator", "State", "Amount"))
  c(hdr, lapply(seq_len(n), function(i) c(sprintf("%s, Person%d (%s-%s)", surnames[(i - 1L) %% length(surnames) + 1L], i, party[i], states[(i - 1L) %% 5L + 1L]),
                                          unname(st_name[(i - 1L) %% 5L + 1L]), money_txt(amt[i]))))
}

synth_presidential_rows <- function() {
  list(c("Rank", "Recipient", "Chamber", "Total"), c("1", "Nominee, Alpha (R)", "President", "$5,790,673"), c("2", "Nominee, Beta (D)", "President", "$2,100,000"), c("3", "Minor, Gamma (U)", "President", "$250"))
}

# ---- synthetic bulk files -------------------------------------------------------------------------------------------------------------------

write_latin1 <- function(path, lines) {
  dir.create(dirname(path), recursive = TRUE, showWarnings = FALSE)
  con <- file(path, open = "wb")
  for (ln in lines) writeBin(c(iconv(ln, "UTF-8", "latin1", toRaw = TRUE)[[1]], as.raw(10L)), con)
  close(con)
  invisible(path)
}

# Bulk tables of one cycle with invented rows for category Z9900 (and noise codes): three candidates (House TX07, Senate TXS1,
# President PRES), one refund, Latin-1 accented text, a line for another industry that must be ignored.
write_synth_bulk <- function(dir, cycle = 2024) {
  yy <- sprintf("%02d", cycle %% 100L)
  q <- function(...) paste0("|", c(...), "|", collapse = ",")
  cands <- c(paste0(q(cycle, "H0TX07001", "N00000001", "Alex Smith (R)", "R", "TX07", "TX07", "Y", "Y", "C", "RC", "N")),
             paste0(q(cycle, "S0TX00002", "N00000002", "Bea Jones (D)", "D", "TXS1", "TXS1", "Y", "Y", "C", "DC", "N")),
             paste0(q(cycle, "P00000003", "N00000003", "Cal D\u00e9ja Jr (R)", "R", "PRES", "PRES", "N", "Y", "C", "RC", "N")))
  pacs <- c(paste0("|", cycle, "|,|r1|,|C001|,|N00000001|,500,|", cycle, "-03-01|,|Z9900|,|24K|,|D|,|H0TX07001|"),
            paste0("|", cycle, "|,|r2|,|C001|,|N00000002|,300,|", cycle, "-03-02|,|Z9900|,|24K|,|D|,|S0TX00002|"),
            paste0("|", cycle, "|,|r3|,|C002|,|N00000001|,-100,|", cycle, "-03-03|,|Z9900|,|24K|,|D|,|H0TX07001|"),
            paste0("|", cycle, "|,|r4|,|C003|,|N00000001|,999,|", cycle, "-03-04|,|Y1100|,|24K|,|D|,|H0TX07001|"),
            paste0("|", cycle, "|,|r5|,|C004|,|N00000001|,77,|", cycle, "-03-05|,|Z9900|,|24K|,|I|,|H0TX07001|"))
  ind_line <- function(id, recip, amt, code, recipcode) paste0("|", cycle, "|,|", id, "|,|c", id, "|,|Doe, Jos\u00e9|,|", recip, "|,|Acme|,|Acme|,|", code, "|,|", cycle, "-02-01|,", amt,
                                                              ",|1 Main St|,|Austin|,|TX|,|78701|,|", recipcode, "|,|15|,|C001|,||,|F|,|m", id, "|,|Adjuster|,|Caf\u00e9 Co|,|x|")
  indivs <- c(ind_line("i1", "N00000001", 250, "Z9900", "RC"), ind_line("i2", "N00000002", 100, "Z9900", "DC"), ind_line("i3", "N00000003", -40, "Z9900", "RC"),
              ind_line("i4", "N00000001", 5000, "Y1100", "RC"), ind_line("i5", "C00000009", 60, "Z9900", "RC"))
  write_latin1(file.path(dir, paste0("cands", yy, ".txt")), cands)
  write_latin1(file.path(dir, paste0("pacs", yy, ".txt")), pacs)
  write_latin1(file.path(dir, paste0("indivs", yy, ".txt")), indivs)
  invisible(dir)
}

# ---- locating licensed files for the local-only tests ---------------------------------------------------------------------------------------------

# Path of a file with a pinned sha256: the real (non-test) vault first, then the given candidate paths. NULL when absent.
find_pinned <- function(sha256, name, candidates = character()) {
  vault <- if (nzchar(REAL_LOCALAPPDATA)) file.path(REAL_LOCALAPPDATA, "Mentat", "forecast", "refit", "raw", sha256, name) else character()
  for (p in c(vault, candidates)) if (length(p) && file.exists(p) && identical(unname(tools::sha256sum(p)), sha256)) return(p)
  NULL
}

# ---- fake OpenSecrets world for the fetcher tests (shared by test-08 and test-12) ------------------------------------------------------------------

ROBOTS_2026 <- paste("User-Agent: *", "Disallow: /widgets/", "Disallow: /export_data", "Disallow: /admin", "Disallow: /api", "Disallow: /400", "Disallow: /404",
                     "Disallow: /406", "Disallow: /422", "Disallow: /500", "Disallow: /503", "Disallow: /504", "Disallow: /donate", "Disallow: /donate/thank-you", "",
                     "User-agent: PiplBot", "Disallow: /", "", "User-agent: GPTBot", "Disallow: /", sep = "\n")

resp <- function(status = 200L, body = "", headers = list(), ctype = "text/html; charset=utf-8") {
  h <- c(list(`content-type` = ctype), headers)
  list(status = as.integer(status), headers = h, body = if (is.raw(body)) body else charToRaw(enc2utf8(body)), url = "")
}

CRUMBS_OK <- list(c("Home", "/"), c("Interest Groups", "/industries"), c("Synthetic Sector", "/industries/indus?ind=Z"), c("Synthetic Parent", "/industries/indus?ind=Z99"),
                  c("Widget Makers", "/industries/indus?ind=Z9900"))

serve_page <- function(url, heading = "Widget &amp; Gadget Makers Summary", crumbs = CRUMBS_OK) {
  t <- fi_os_target_from_url(url)
  rows <- switch(t$kind, totals = synth_totals_rows(), candidates_H = synth_candidate_rows(6, "H"), candidates_S = synth_candidate_rows(6, "S"), presidential = synth_presidential_rows())
  resp(200, synth_os_html(rows, heading = heading, canonical = url, crumbs = crumbs))
}

# A fake world: clock, transport (records calls with the time of each), sleep.
make_world <- function(handler = NULL, latency = 0.3) {
  w <- new.env(parent = emptyenv())
  w$t <- 1000; w$calls <- list(); w$sleeps <- numeric()
  w$handler <- handler %||% function(url) if (grepl("/robots.txt$", url)) resp(200, ROBOTS_2026, list(`cache-control` = "public, max-age=86400"), "text/plain") else serve_page(url)
  w$transport <- function(url, user_agent, timeout_s) {
    w$calls[[length(w$calls) + 1L]] <- list(url = url, ua = user_agent, t = w$t, timeout = timeout_s)
    w$t <- w$t + latency
    w$handler(url)
  }
  w$clock <- function() w$t
  w$sleep <- function(s) { w$sleeps <- c(w$sleeps, s); w$t <- w$t + s }
  w
}

fetch_args <- function(feeds, w, frt, ...) {
  list(plan = fi_os_fetch_plan(feeds, "widgets"), cfg = feeds$opensecrets_fetch, frozen_codes = unlist(feeds$opensecrets_frozen$codes), frt = frt,
       transport = w$transport, clock = w$clock, sleep = w$sleep, ...)
}
run_fetch <- function(feeds, w, frt, ...) do.call(fi_os_fetch_run, fetch_args(feeds, w, frt, ...))
calls_to <- function(w, pattern) sum(vapply(w$calls, function(z) grepl(pattern, z$url), NA))


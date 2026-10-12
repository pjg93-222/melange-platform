# F13, the memo re-disclosure rule (Paul approved 2026-09-26, "Approve F13").
# Every case in tests/fixtures/memo_redisclosure_cases.json, which
# tests/test_memo_redisclosure.py runs against the Python twin as well.
# Run from inside mentat/tests: Rscript test_memo_redisclosure.R
script <- sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[[1L]])
project_root <- normalizePath(file.path(dirname(script), ".."), winslash = "/")
for (file in c("paths.R", "schema.R", "ingest.R", "collect.R", "fec_api.R", "report.R")) {
  source(file.path(project_root, "R", file), local = .GlobalEnv)
}
suppressPackageStartupMessages(library(data.table))
fixture <- jsonlite::fromJSON(file.path(project_root, "tests", "fixtures", "memo_redisclosure_cases.json"),
                              simplifyVector = FALSE)
stopifnot(identical(fixture$name, "memo_redisclosure_cases"), length(fixture$cases) >= 20L)

rows_of <- function(case) {
  rows <- lapply(case$rows, function(row) {
    row <- utils::modifyList(fixture$defaults, row)
    lapply(row, function(value) if (is.null(value)) NA else value)
  })
  data.table::rbindlist(rows, use.names = TRUE, fill = TRUE)
}

# One verdict per row: the memo plan's duplicate flag and parent, what stays in
# the total (fec_receipt_countable, the export's predicate) and the
# disclosure_memo_unmatched flag a counted re-disclosure memo carries.
verdicts <- function(rows) {
  plan <- mentat_memo_duplicate_plan(rows)
  flagged_rows <- data.table::copy(rows)
  flagged_rows[, duplicate_memo := plan$duplicate_memo]
  data.table::data.table(
    sub_id = as.character(rows$sub_id),
    counted = fec_receipt_countable(flagged_rows),
    duplicate_of = plan$duplicate_of_sub_id,
    reason = plan$duplicate_reason,
    flagged = fec_receipt_role_reason(fec_receipt_role(rows)) == "disclosure_memo_unmatched" &
      plan$duplicate_memo == 0L
  )
}

check_case <- function(case, expectations = case$expect) {
  found <- verdicts(rows_of(case))
  problems <- character()
  for (sid in names(expectations)) {
    e <- expectations[[sid]]
    v <- found[sub_id == sid]
    if (nrow(v) != 1L) {
      problems <- c(problems, sprintf("%s/%s: row missing", case$name, sid))
      next
    }
    wanted_reason <- if (nzchar(e$duplicate_of)) "memo_redisclosure_of_counted_parent" else ""
    if (!identical(v$counted, isTRUE(e$counted))) problems <- c(problems, sprintf("%s/%s counted %s", case$name, sid, v$counted))
    if (!identical(v$duplicate_of, e$duplicate_of)) problems <- c(problems, sprintf("%s/%s duplicate_of '%s'", case$name, sid, v$duplicate_of))
    if (!identical(v$reason, wanted_reason)) problems <- c(problems, sprintf("%s/%s reason '%s'", case$name, sid, v$reason))
    if (!identical(v$flagged, isTRUE(e$flagged))) problems <- c(problems, sprintf("%s/%s flagged %s", case$name, sid, v$flagged))
  }
  if (!setequal(names(expectations), found$sub_id)) problems <- c(problems, sprintf("%s: every row needs an expectation", case$name))
  problems
}

problems <- unlist(lapply(fixture$cases, check_case))
if (length(problems)) stop(paste(c("F13 fixture cases failed:", problems), collapse = "\n  "), call. = FALSE)
cat(sprintf("fixture cases: %d passed\n", length(fixture$cases)))

# Negative control on the harness itself: a wrong expectation must be caught.
first <- fixture$cases[[1L]]
wrong <- first$expect
wrong[["4021420241824800143"]]$counted <- TRUE
wrong[["4021420241824800143"]]$duplicate_of <- ""
stopifnot(length(check_case(first, wrong)) >= 2L)

# The shared statements: disclosure_pattern and the unitemized guard, on the
# text normalised exactly as fec_receipt_role() normalises it.
rules <- fec_receipt_rules()
stopifnot(rules$version == 6)
words <- function(x) trim_text(gsub("[^A-Z0-9]+", " ", toupper(trim_text(x))))
statement_problems <- character()
for (s in fixture$statements) {
  receipt <- if (is.null(s$receipt_type_full)) NA_character_ else s$receipt_type_full
  memo_text <- if (is.null(s$memo_text)) NA_character_ else s$memo_text
  text <- trim_text(paste(words(receipt), words(memo_text)))
  states <- grepl(rules$disclosure_pattern, text, perl = TRUE)
  unitemized <- grepl(rules$redisclosure_unitemized_pattern, text, perl = TRUE)
  # And through the classifier, as a memo on an F3X other-receipts line.
  role <- fec_receipt_role(data.table(filing_form = "F3X", line_number = "17", memo_code = "X",
                                      receipt_type_full = receipt, memo_text = memo_text))
  if (!identical(states, isTRUE(s$states))) statement_problems <- c(statement_problems, sprintf("states %s: %s", states, text))
  if (!identical(unitemized, isTRUE(s$unitemized))) statement_problems <- c(statement_problems, sprintf("unitemized %s: %s", unitemized, text))
  if (!identical(role == "unverified_disclosure_memo", isTRUE(s$states))) statement_problems <- c(statement_problems, sprintf("role %s: %s", role, text))
}
if (length(statement_problems)) stop(paste(c("F13 statements failed:", statement_problems), collapse = "\n  "), call. = FALSE)
cat(sprintf("statements: %d passed\n", length(fixture$statements)))

# An explicit FEC back reference keeps precedence over the text rule.
precedence <- data.table(
  sub_id = c("parent", "earlier", "memo"), company = "Acme", committee_id = "C1",
  contributor_name = "PERSON, ONE", contribution_receipt_date = c("2024-01-01", "2023-12-01", "2024-01-01"),
  contribution_receipt_amount = 100, is_memo_leg = c(0L, 0L, 1L), memo_code = c("", "", "X"),
  transaction_id = c("SA11AI.42", "SA11AI.7", "MEMO1"),
  receipt_type_full = c("", "", "CONTRIBUTION PREVIOUSLY DISCLOSED [SA11AI:42]"),
  file_num = 2, report_year = 2024, report_type = "Q1", company_match_status = "accepted",
  filing_form = "F3X", line_number = c("11AI", "11AI", "17"))
plan <- mentat_memo_duplicate_plan(precedence)
stopifnot(plan$duplicate_of_sub_id[[3L]] == "parent", plan$duplicate_reason[[3L]] == "fec_text_back_reference")
# Without the back reference the text rule finds the latest counted parent.
precedence$receipt_type_full[[3L]] <- "CONTRIBUTION PREVIOUSLY DISCLOSED"
plan <- mentat_memo_duplicate_plan(precedence)
stopifnot(plan$duplicate_of_sub_id[[3L]] == "parent",
          plan$duplicate_reason[[3L]] == "memo_redisclosure_of_counted_parent")
# Negative control: the same rows without the statement stay untouched.
precedence$receipt_type_full[[3L]] <- ""
stopifnot(!any(mentat_memo_duplicate_plan(precedence)$duplicate_memo == 1L))

# The database path: apply_amendment_versioning() persists duplicate_memo from
# the global plan across both tables (the Coinbase -> FAIRSHAKE rows).
connection <- DBI::dbConnect(RSQLite::SQLite(), ":memory:")
init_schema(connection)
stored <- rbindlist(lapply(fixture$cases[vapply(fixture$cases, function(case) case$name %in% c(
  "fairshake_15_5m_disclosedon_typo", "fairshake_four_bitcoin_memos", "fairshake_usdc_previously_disclosed",
  "unitemized_memo_without_parent", "d040c_blank_rows_untouched"), logical(1L))], rows_of), fill = TRUE)
stored <- unique(stored, by = "sub_id")
stored[, `:=`(industry = "Crypto", matched_variant = "COINBASE", cycle = 2024L, scraped_at = "2026-09-26",
              contribution_receipt_amount = as.numeric(contribution_receipt_amount))]
invisible(DBI::dbAppendTable(connection, "corporate_giving", as.data.frame(stored[, intersect(names(stored),
  DBI::dbListFields(connection, "corporate_giving")), with = FALSE])))
invisible(apply_amendment_versioning(connection))
flags <- DBI::dbGetQuery(connection, "SELECT sub_id, duplicate_memo FROM corporate_giving ORDER BY sub_id")
DBI::dbDisconnect(connection)
flagged_ids <- sort(flags$sub_id[flags$duplicate_memo == 1L])
stopifnot(identical(flagged_ids, sort(c("4021420241824800143", "4030420261386079659", "4030420261386079661",
                                        "4081520251218411849", "4081520251218411851", "4081220242011729506"))))
cat("database path: 6 FAIRSHAKE memos flagged; the $5 unitemized memo and the D-040(c) rows are not\n")
cat("test_memo_redisclosure: PASS\n")

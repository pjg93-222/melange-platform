script_argument <- grep("^--file=", commandArgs(trailingOnly = FALSE), value = TRUE)
stopifnot(length(script_argument) == 1L)
script_path <- normalizePath(
  sub("^--file=", "", script_argument[[1L]]),
  winslash = "/",
  mustWork = TRUE
)
project_root <- normalizePath(
  file.path(dirname(script_path), ".."),
  winslash = "/",
  mustWork = TRUE
)
for (file in c(
  "paths.R", "schema.R", "ingest.R", "collect.R", "reconcile.R", "report.R"
)) {
  source(file.path(project_root, "R", file), local = .GlobalEnv)
}

data_root <- tempfile("mentat-test-report-")
dir.create(data_root, recursive = TRUE)
isolated_reports <- file.path(data_root, "reports")
dir.create(isolated_reports)
Sys.setenv(MENTAT_REPORT_DIRECTORY = isolated_reports)
connection <- NULL
on.exit({
  if (!is.null(connection) && DBI::dbIsValid(connection)) {
    DBI::dbDisconnect(connection)
  }
  if (dir.exists(data_root)) {
    unlink(data_root, recursive = TRUE, force = TRUE)
  }
}, add = TRUE)

connection <- db_connect(data_root)
init_schema(connection)
DBI::dbAppendTable(
  connection,
  "companies",
  data.frame(
    industry = "Crypto",
    company = c("Coinbase", "Gemini"),
    parent = c("Coinbase", "Gemini"),
    variant = c("Coinbase", "Gemini"),
    variant_order = 1L,
    confidence = "normal",
    exclusions = NA_character_,
    metadata_json = "{}",
    source_sheet = "Crypto",
    source_row = c(1L, 2L)
  )
)

insert_contribution <- function(
  sub_id,
  name,
  employer,
  occupation,
  company,
  recipient,
  committee_id,
  committee_type,
  committee_designation,
  date,
  amount,
  entity_type = "IND",
  memo_code = "",
  receipt_type_full = "",
  conduit_committee_id = "",
  transaction_id = "",
  original_sub_id = "",
  amendment_indicator = "",
  report_type = "Q1",
  file_num = NA_integer_
) {
  DBI::dbExecute(
    connection,
    paste(
      "INSERT INTO contributions (",
      "sub_id, contributor_name, contributor_city, contributor_state,",
      "contributor_zip, contributor_employer, contributor_occupation,",
      "contribution_receipt_amount, contribution_receipt_date,",
      "committee_name, committee_id, recipient_committee_type,",
      "recipient_committee_designation, two_year_transaction_period,",
      "entity_type, memo_code, receipt_type_full, conduit_committee_id,",
      "transaction_id, original_sub_id, amendment_indicator, report_type, file_num,",
      "is_conduit_leg, is_memo_leg,",
      "is_entity_contributor, industry, company, matched_variant, phase, scraped_at",
      ") VALUES (?, ?, 'BERNARDSVILLE', 'NJ', '079241106', ?, ?, ?, ?,",
      "?, ?, ?, ?, 2026, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Crypto', ?, ?,",
      "'P1_employer', '2026-07-18T00:00:00Z')"
    ),
    params = list(
      sub_id, name, employer, occupation, as.numeric(amount), date,
      recipient, committee_id, committee_type, committee_designation,
      entity_type, memo_code, receipt_type_full, conduit_committee_id,
      transaction_id, original_sub_id, amendment_indicator, report_type,
      as.integer(file_num),
      as.integer(committee_id %in% c("C00401224", "C00694323")),
      as.integer(toupper(memo_code) == "X"),
      as.integer(toupper(entity_type) != "IND"),
      company, company
    )
  )
}

insert_contribution(
  "coinbase-1", "TUSAR, GREG", "COINBASE, INC.", "VP, PRODUCT",
  "Coinbase", "COINBASE, INC. INNOVATION PAC", "CTEST001", "N", "B",
  "2026-05-01", 1000
)
insert_contribution(
  "gemini-trust", "EMPLOYEE, ERIN", "GEMINI TRUST COMPANY, LLC",
  "SOFTWARE ENGINEER", "Gemini", "OFF THE SIDELINES PAC", "CTEST002",
  "O", "U", "2026-04-01", 500
)
insert_contribution(
  "gemini-exact", "EMPLOYEE, ERIN", "GEMINI", "SOFTWARE ENGINEER",
  "Gemini", "TEST CANDIDATE FOR CONGRESS", "CTEST003", "H", "P",
  "2026-03-01", 250
)
insert_contribution(
  "gemini-transport", "DRIVER, DONNA", "GEMINI TRANSPORTATION",
  "TRUCK DRIVER", "Gemini", "TEST RECIPIENT", "CTEST004", "N", "U",
  "2026-02-01", 100
)
insert_contribution(
  "actblue-primary", "PERSON, BLUE", "COINBASE", "ANALYST",
  "Coinbase", "ACTBLUE", "C00401224", "N", "U",
  "2026-01-15", 35
)
insert_contribution(
  "actblue-memo", "PERSON, BLUE", "COINBASE", "ANALYST",
  "Coinbase", "TEST CANDIDATE", "CMEMO001", "H", "A",
  "2026-01-15", 35, memo_code = "X"
)
insert_contribution(
  "winred-primary", "PERSON, RED", "COINBASE", "ANALYST",
  "Coinbase", "WINRED", "C00694323", "N", "U",
  "2026-01-10", 45
)

# Confirmed live anatomy: a WinRed receipt, a downstream EARMARKED THROUGH row,
# and a memo-X row. All three count to their displayed recipient.
insert_contribution(
  "silverberg-conduit", "SILVERBERG, MARCIA", "COINBASE", "EMPLOYEE",
  "Coinbase", "WINRED", "C00694323", "N", "U",
  "2024-10-26", 10.41
)
insert_contribution(
  "silverberg-earmark", "SILVERBERG, MARCIA", "COINBASE", "EMPLOYEE",
  "Coinbase", "TRUMP NATIONAL COMMITTEE JFC", "C00873893", "N", "J",
  "2024-10-26", 10.41, receipt_type_full = "EARMARKED THROUGH WINRED",
  conduit_committee_id = "C00694323"
)
insert_contribution(
  "silverberg-memo", "SILVERBERG, MARCIA", "COINBASE", "EMPLOYEE",
  "Coinbase", "NEVER SURRENDER", "C00828541", "O", "U",
  "2024-10-26", 10.41, memo_code = "X"
)
silverberg <- spending_report_rows(connection)[
  contributor_name == "SILVERBERG, MARCIA"
]
silverberg_categories <- spending_recipient_classification(connection, silverberg)
silverberg[, recipient_category := silverberg_categories$recipient_category]
stopifnot(
  nrow(silverberg) == 3L,
  setequal(
    silverberg$sub_id,
    c("silverberg-conduit", "silverberg-earmark", "silverberg-memo")
  ),
  silverberg[sub_id == "silverberg-conduit", recipient_category] == "Conduit",
  silverberg_categories$category_source[
    silverberg$sub_id == "silverberg-conduit"
  ] == "fec:registered_conduit:earmark_platform",
  silverberg[sub_id == "silverberg-earmark", recipient_category] == "JFC",
  silverberg[sub_id == "silverberg-memo", recipient_category] == "Super PAC",
  all(silverberg$contribution_receipt_amount == 10.41)
)

# Paul's ruling (2026-10-09): the registered earmark conduits are their own
# Conduit category, matched by committee ID or by exact name and tried before
# every other rule (even a Super PAC type). An ordinary hybrid (type V) PAC is
# still a Carey.
insert_contribution(
  "conduit-by-name", "PERSON, NAMED", "COINBASE", "ANALYST",
  "Coinbase", "WINRED", "C99999991", "N", "U", "2026-01-12", 20
)
insert_contribution(
  "conduit-actblue-super", "PERSON, BLUEISH", "COINBASE", "ANALYST",
  "Coinbase", "ACTBLUE", "C00401224", "O", "U", "2026-01-13", 15
)
insert_contribution(
  "ordinary-carey", "PERSON, HYBRID", "COINBASE", "ANALYST",
  "Coinbase", "ORDINARY HYBRID PAC", "C99999992", "V", "", "2026-01-14", 10
)
conduit_rows <- spending_report_rows(connection)[
  sub_id %in% c("conduit-by-name", "conduit-actblue-super", "ordinary-carey")
]
conduit_categories <- spending_recipient_classification(connection, conduit_rows)
conduit_rows[, `:=`(
  recipient_category = conduit_categories$recipient_category,
  category_source = conduit_categories$category_source
)]
stopifnot(
  nrow(conduit_rows) == 3L,
  conduit_rows[sub_id == "conduit-by-name", recipient_category] == "Conduit",
  conduit_rows[sub_id == "conduit-actblue-super", recipient_category] == "Conduit",
  conduit_rows[sub_id == "ordinary-carey", recipient_category] == "Carey",
  conduit_rows[sub_id == "conduit-by-name", category_source] ==
    "fec:registered_conduit:earmark_platform",
  conduit_rows[sub_id == "ordinary-carey", category_source] ==
    "fec:hybrid_committee_type",
  !any(conduit_rows$recipient_category == "Super PAC")
)
DBI::dbExecute(
  connection,
  paste(
    "DELETE FROM contributions WHERE sub_id IN",
    "('conduit-by-name', 'conduit-actblue-super', 'ordinary-carey')"
  )
)
legitimate_split <- data.table::data.table(
  sub_id = paste0("balachander-", seq_len(10L)),
  contributor_name = "BALACHANDER, SUPRIYA",
  contributor_zip = "98004",
  contribution_receipt_date = "2022-10-09",
  contribution_receipt_amount = 1.67,
  committee_id = "C00401224",
  company = "Coinbase",
  transaction_id = "",
  original_sub_id = "",
  receipt_type_full = "",
  conduit_committee_id = "",
  is_conduit_leg = 1L
)
legitimate_split_plan <- mentat_filing_version_plan(
  legitimate_split,
  "contributions"
)
different_people <- data.frame(
  sub_id = c("different-person-a", "different-person-b"),
  contributor_name = c("PERSON, ALPHA", "PERSON, BETA"),
  contributor_zip = "98004",
  contribution_receipt_date = "2024-01-02",
  contribution_receipt_amount = 100,
  committee_id = "C00401224",
  company = "Coinbase",
  transaction_id = "SHARED-UPSTREAM-VALUE",
  original_sub_id = "",
  stringsAsFactors = FALSE
)
different_people_plan <- mentat_filing_version_plan(
  different_people,
  "contributions"
)
linked_contribution_versions <- legitimate_split[seq_len(5L)]
linked_contribution_versions[, `:=`(
  sub_id = paste0("linked-contribution-", seq_len(.N)),
  transaction_id = "TX-CROSS-CYCLE",
  file_num = c(100L, 101L, 102L, 103L, 104L)
)]
linked_contribution_plan <- mentat_filing_version_plan(
  linked_contribution_versions,
  "contributions"
)
stopifnot(
  isTRUE(spending_assert_no_duplicate_kept_tuples(legitimate_split)),
  sum(legitimate_split_plan$duplicate_filing_version) == 0L,
  sum(different_people_plan$duplicate_filing_version) == 0L,
  sum(linked_contribution_plan$duplicate_filing_version) == 4L,
  linked_contribution_plan$sub_id[
    linked_contribution_plan$duplicate_filing_version == 0L
  ] == "linked-contribution-5"
)
for (index in seq_len(10L)) {
  insert_contribution(
    paste0("balachander-", index),
    "BALACHANDER, SUPRIYA", "COINBASE", "ENGINEER", "Coinbase",
    "ACTBLUE", "C00401224", "N", "U", "2022-10-09", 1.67
  )
}
insert_contribution(
  "memo-pair-primary", "MEMO, MATCH", "COINBASE", "ENGINEER",
  "Coinbase", "MATCHED RECIPIENT", "CMEMOPAIR", "H", "P",
  "2026-01-08", 75, transaction_id="ORIGINAL1", file_num=1234L
)
insert_contribution(
  "memo-pair-copy", "MEMO, MATCH", "COINBASE", "ENGINEER",
  "Coinbase", "MATCHED RECIPIENT", "CMEMOPAIR", "H", "P",
  "2026-01-08", 75, memo_code = "X"
)
DBI::dbExecute(connection,"UPDATE contributions SET file_num=1234,back_reference_transaction_id='ORIGINAL1' WHERE sub_id='memo-pair-copy'")
DBI::dbExecute(connection,"UPDATE contributions SET filing_form='F3',line_number='11AI' WHERE sub_id IN ('memo-pair-primary','memo-pair-copy')")
memo_pair_analysis <- spending_report_rows(connection)[
  sub_id %in% c("memo-pair-primary", "memo-pair-copy")
]
memo_pair_raw <- spending_raw_report_rows(connection)[
  sub_id %in% c("memo-pair-primary", "memo-pair-copy")
]
balachander_analysis <- spending_report_rows(connection)[
  contributor_name == "BALACHANDER, SUPRIYA"
]
balachander_raw <- spending_raw_report_rows(connection)[
  contributor_name == "BALACHANDER, SUPRIYA"
]
stopifnot(
  nrow(memo_pair_raw) == 2L,
  memo_pair_raw[sub_id == "memo-pair-copy", duplicate_memo] == 1L,
  memo_pair_raw[sub_id == "memo-pair-primary", duplicate_memo] == 0L,
  identical(memo_pair_analysis$sub_id, "memo-pair-primary"),
  nrow(balachander_raw) == 10L,
  nrow(balachander_analysis) == 10L,
  sum(balachander_analysis$contribution_receipt_amount) == 16.7
)
DBI::dbExecute(
  connection,
  "DELETE FROM contributions WHERE sub_id LIKE 'silverberg-%'"
)

insert_contribution(
  "amend-original", "VERSIONED, VERA", "COINBASE", "ENGINEER",
  "Coinbase", "TEST CANDIDATE", "CAMEND001", "H", "P",
  "2026-01-04", 100, transaction_id = "TX-AMEND-1",
  amendment_indicator = "N", file_num = 100L
)
insert_contribution(
  "amend-current", "VERSIONED, VERA", "COINBASE", "ENGINEER",
  "Coinbase", "TEST CANDIDATE", "CAMEND001", "H", "P",
  "2026-01-04", 125, transaction_id = "TX-AMEND-1",
  original_sub_id = "amend-original", amendment_indicator = "A", file_num = 101L
)
amendment_rows <- spending_report_rows(connection)[
  contributor_name == "VERSIONED, VERA"
]
amendment_raw <- spending_raw_report_rows(connection)[
  contributor_name == "VERSIONED, VERA"
]
amendment_flags <- DBI::dbGetQuery(
  connection,
  paste(
    "SELECT sub_id, superseded_by_amendment FROM contributions",
    "WHERE sub_id LIKE 'amend-%' ORDER BY sub_id"
  )
)
stopifnot(
  nrow(amendment_rows) == 2L,
  nrow(amendment_raw) == 2L,
  setequal(amendment_rows$sub_id, c("amend-original", "amend-current")),
  sum(amendment_rows$contribution_receipt_amount) == 225,
  amendment_flags$superseded_by_amendment[amendment_flags$sub_id == "amend-original"] == 1L,
  amendment_flags$superseded_by_amendment[amendment_flags$sub_id == "amend-current"] == 0L
)
DBI::dbExecute(connection, "DELETE FROM contributions WHERE sub_id LIKE 'amend-%'")

insert_contribution(
  "redesignation-positive", "REDESIGNATION, RITA", "COINBASE", "ENGINEER",
  "Coinbase", "TEST CANDIDATE", "CREDES001", "H", "P",
  "2026-01-03", 3500, transaction_id = "TX-REDES-POS"
)
insert_contribution(
  "redesignation-negative", "REDESIGNATION, RITA", "COINBASE", "ENGINEER",
  "Coinbase", "TEST CANDIDATE", "CREDES001", "H", "P",
  "2026-01-03", -3500, transaction_id = "TX-REDES-NEG"
)
insert_contribution(
  "blank-entity-person", "PERSON, BLANK", "COINBASE", "ENGINEER",
  "Coinbase", "TEST CANDIDATE", "CBLANK001", "H", "P",
  "2026-01-06", 12, entity_type = ""
)
blank_entity_person <- spending_report_rows(connection)[
  sub_id == "blank-entity-person"
]
stopifnot(
  nrow(blank_entity_person) == 1L,
  blank_entity_person$is_entity_contributor[[1L]] == 0L,
  blank_entity_person$record_type[[1L]] == "CONTRIBUTION"
)
DBI::dbExecute(
  connection,
  "DELETE FROM contributions WHERE sub_id = 'blank-entity-person'"
)
insert_contribution(
  "excluded-527", "PERSON, OTHER", "COINBASE", "ANALYST",
  "Coinbase", "TEST 527 GROUP", "C5270001", "N", "U",
  "2026-01-05", 55
)
DBI::dbExecute(
  connection,
  paste(
    "INSERT INTO corporate_giving (",
    "sub_id, contributor_name, contributor_city, contributor_state,",
    "contributor_zip, contributor_employer, contributor_occupation, entity_type,",
    "is_entity_contributor, committee_id, committee_name, recipient_committee_type,",
    "recipient_committee_designation, contribution_receipt_date,",
    "contribution_receipt_amount, industry, company, matched_variant, cycle, scraped_at,",
    "company_match_status, company_match_reason",
    ") VALUES ('corporate-1', 'COINBASE, INC.', 'SAN FRANCISCO', 'CA', '94104',",
    "'COINBASE', 'CORPORATION', 'ORG', 1, 'CCORP001', 'TEST CANDIDATE', 'H',",
    "'A', '2026-01-01', 5000, 'Crypto', 'Coinbase', 'Coinbase', 2026,",
    "'2026-07-18T00:00:00Z', 'review', 'migration_requires_revalidation')"
  )
)
DBI::dbExecute(
  connection,
  paste(
    "INSERT INTO corporate_giving",
    "(sub_id, contributor_name, entity_type, committee_id, committee_name,",
    "contribution_receipt_date, contribution_receipt_amount, industry, company,",
    "matched_variant, cycle, scraped_at, company_match_status, company_match_reason)",
    "VALUES ('corporate-qualified-report', 'COINBASE COMMERCE (EXCHANGE)', 'ORG',",
    "'CCORP003', 'TEST CANDIDATE', '2026-01-03', 7000, 'Crypto', 'Coinbase',",
    "'Coinbase', 2026, '2026-07-18T00:00:00Z', 'review',",
    "'migration_requires_revalidation')"
  )
)
DBI::dbExecute(
  connection,
  paste(
    "INSERT INTO company_match_overrides",
    "(employer_key, company, state, occupation_pattern, decision)",
    "VALUES ('COINBASE COMMERCE EXCHANGE', 'Coinbase', '', '', 'accept')"
  )
)
DBI::dbExecute(connection, paste(
  "UPDATE corporate_giving SET filing_form='F3X', line_number='11AI'",
  "WHERE sub_id='corporate-qualified-report'"
))
corporate_versions <- data.frame(
  sub_id = c("corporate-amend-original", "corporate-amend-current"),
  contributor_name = "COINBASE, INC.",
  contributor_employer = "COINBASE",
  contributor_occupation = "CORPORATION",
  entity_type = "ORG",
  is_entity_contributor = 1L,
  committee_id = "CCORP002",
  committee_name = "TEST CANDIDATE",
  recipient_committee_type = "H",
  recipient_committee_designation = "A",
  contribution_receipt_date = "2026-01-02",
  contribution_receipt_amount = c(10000, 12000),
  industry = "Crypto",
  company = "Coinbase",
  matched_variant = "Coinbase",
  cycle = 2026L,
  scraped_at = "2026-07-18T00:00:00Z",
  transaction_id = "TX-CORP-AMEND-1",
  original_sub_id = c("", "corporate-amend-original"),
  amendment_indicator = c("N", "A"),
  report_year = 2026L,
  report_type = "Q1",
  file_num = c(200L, 201L),
  company_match_status = "accepted",
  company_match_reason = "verified_roster_alias",
  stringsAsFactors = FALSE
)
DBI::dbAppendTable(connection, "corporate_giving", corporate_versions)
corporate_amendment_rows <- spending_report_rows(connection)[
  sub_id %in% corporate_versions$sub_id
]
corporate_amendment_raw <- spending_raw_report_rows(connection)[
  sub_id %in% corporate_versions$sub_id
]
corporate_amendment_flags <- DBI::dbGetQuery(
  connection,
  paste(
    "SELECT sub_id, superseded_by_amendment FROM corporate_giving",
    "WHERE sub_id LIKE 'corporate-amend-%' ORDER BY sub_id"
  )
)
stopifnot(
  nrow(corporate_amendment_rows) == 2L,
  nrow(corporate_amendment_raw) == 2L,
  setequal(
    corporate_amendment_rows$sub_id,
    c("corporate-amend-original", "corporate-amend-current")
  ),
  sum(corporate_amendment_rows$contribution_receipt_amount) == 22000,
  corporate_amendment_flags$superseded_by_amendment[
    corporate_amendment_flags$sub_id == "corporate-amend-original"
  ] == 1L
)
DBI::dbExecute(
  connection,
  "DELETE FROM corporate_giving WHERE sub_id LIKE 'corporate-amend-%'"
)
corporate_duplicate_versions <- data.frame(
  sub_id = c(
    "4020420251141540904", "4030420261386079659",
    "4030420261386079661", "4081520251218411849",
    "4081520251218411851"
  ),
  contributor_name = "COINBASE, INC.",
  contributor_employer = "COINBASE",
  contributor_occupation = "CORPORATION",
  entity_type = "ORG",
  is_entity_contributor = 1L,
  committee_id = "C00835959",
  committee_name = "FAIRSHAKE",
  recipient_committee_type = "O",
  recipient_committee_designation = "U",
  contribution_receipt_date = "2024-12-20",
  contribution_receipt_amount = 23000000,
  industry = "Crypto",
  company = "Coinbase",
  matched_variant = "Coinbase",
  cycle = c(2024L, 2026L, 2026L, 2026L, 2026L),
  scraped_at = "2026-07-18T00:00:00Z",
  transaction_id = "",
  original_sub_id = "",
  amendment_indicator = "",
  report_year = NA_integer_,
  report_type = "",
  file_num = NA_integer_,
  company_match_status = "accepted",
  company_match_reason = "verified_roster_alias",
  stringsAsFactors = FALSE
)
DBI::dbAppendTable(connection, "corporate_giving", corporate_duplicate_versions)
corporate_duplicate_summary <- apply_amendment_versioning(
  connection,
  print_summary = FALSE
)
corporate_duplicate_flags <- DBI::dbGetQuery(
  connection,
  paste(
    "SELECT sub_id, excluded_reason, duplicate_filing_version FROM corporate_giving",
    "WHERE committee_id='C00835959' ORDER BY sub_id"
  )
)
corporate_duplicate_rows <- spending_report_rows(connection)[
  committee_id == "C00835959"
]
coinbase_duplicate_summary <- corporate_duplicate_summary[
  corporate_duplicate_summary$table == "corporate_giving" &
    corporate_duplicate_summary$company == "Coinbase",
  , drop = FALSE
]
stopifnot(
  nrow(corporate_duplicate_rows) == 5L,
  sum(corporate_duplicate_flags$duplicate_filing_version) == 4L,
  all(is.na(corporate_duplicate_flags$excluded_reason)),
  coinbase_duplicate_summary$rows_duplicate_filing_versions[[1L]] == 4L,
  coinbase_duplicate_summary$filing_version_dollar_delta[[1L]] == 0,
  coinbase_duplicate_summary$current_total[[1L]] ==
    coinbase_duplicate_summary$before_total[[1L]]
)
DBI::dbExecute(
  connection,
  "DELETE FROM corporate_giving WHERE committee_id='C00835959'"
)
DBI::dbAppendTable(
  connection,
  "categories",
  data.frame(
    cycle = 2026L,
    group_name = "OFF THE SIDELINES PAC",
    category = "Super PAC",
    lean = "D",
    committee_id = "CTEST002",
    source_sheet = "2025-2026",
    source_marks = "Super PAC",
    resolution_status = "resolved"
  )
)

DBI::dbAppendTable(
  connection,
  "bulk_candidates",
  data.frame(
    cycle = c(2026L, 2026L, 2026L),
    company = c("Coinbase", "Gemini", "Gemini"),
    sub_id = c("coinbase-1", "gemini-trust", "bulk-unreachable"),
    contributor_name = c("TUSAR, GREG", "EMPLOYEE, ERIN", "MISSING, PERSON"),
    contributor_city = c("BERNARDSVILLE", "BERNARDSVILLE", "SEATTLE"),
    contributor_state = c("NJ", "NJ", "WA"),
    contributor_zip = c("07924", "07924", "98101"),
    employer_string = c("COINBASE", "GEMINI TRUST COMPANY", "GEMINI"),
    contributor_occupation = c("VP, PRODUCT", "SOFTWARE ENGINEER", "ENGINEER"),
    committee_id = c("CTEST001", "CTEST002", "CMISSING"),
    amount = c(1000, 500, 100),
    stringsAsFactors = FALSE
  )
)
DBI::dbAppendTable(
  connection,
  "categories",
  data.frame(
    cycle = c(2026L, 2026L),
    group_name = c("TEST 527 GROUP", "PROVISIONAL SUPER PAC"),
    category = c("527", "Super PAC"),
    lean = c(NA_character_, "R"),
    committee_id = c("C5270001", "CPROV001"),
    source_sheet = c("2025-2026", "2023-2024 (carried forward)"),
    source_marks = c("527s", "Super PAC"),
    provisional = c(0L, 1L),
    resolution_status = "resolved"
  )
)

original_apply_amendment_versioning <- apply_amendment_versioning
versioning_call_count <- 0L
apply_amendment_versioning <- function(...) {
  versioning_call_count <<- versioning_call_count + 1L
  original_apply_amendment_versioning(...)
}
DBI::dbExecute(
  connection,
  paste(
    "INSERT INTO run_log",
    "(run_id, cycle, status, started_at, ended_at, retry_count)",
    "VALUES ('test-run', 2024, 'complete',",
    "'2026-07-18T12:00:00+0000', '2026-07-18T12:01:00+0000', 2),",
    "('test-run', 2026, 'complete',",
    "'2026-07-18T12:00:00+0000', '2026-07-18T12:01:00+0000', 0)"
  )
)
# These synthetic receipts are contribution fixtures, not legacy unknowns.
# Supply their filing evidence before exercising the publication boundary.
for (table in c("contributions", "corporate_giving")) {
  DBI::dbExecute(connection, paste0("UPDATE ", table,
    " SET filing_form='F3X', line_number='11AI' WHERE COALESCE(filing_form,'')=''"))
}
reports <- write_reports(connection, data_root)
apply_amendment_versioning <- original_apply_amendment_versioning
cat("test_report: report versioning passes=", versioning_call_count, "\n", sep = "")
stopifnot(versioning_call_count == 1L)
workbook <- reports$spending_report
reliability_lines <- readLines(reports$reliability_report, warn = FALSE)
DBI::dbExecute(
  connection,
  paste(
    "UPDATE contributions SET phase='P3_bulk_rescue',",
    "company_match_status='accepted',",
    "company_match_reason='verified_employer_alias'",
    "WHERE sub_id='gemini-trust'"
  )
)
write_bulk_audit_reports(connection, data_root, print_summary = FALSE)
bulk_audit <- data.table::fread(reports$bulk_audit)
bulk_unmatched <- data.table::fread(reports$bulk_audit_unmatched)
company_review_queue <- data.table::fread(reports$company_match_review_queue)
corporate_status <- DBI::dbGetQuery(
  connection,
  paste(
    "SELECT company_match_status, company_match_reason, is_entity_contributor",
    "FROM corporate_giving WHERE sub_id='corporate-1'"
  )
)
qualified_report_status <- DBI::dbGetQuery(
  connection,
  paste(
    "SELECT company_match_status, company_match_reason, is_entity_contributor",
    "FROM corporate_giving WHERE sub_id='corporate-qualified-report'"
  )
)
coinbase_audit <- bulk_audit[company == "Coinbase" & cycle == 2026L]
gemini_audit <- bulk_audit[company == "Gemini" & cycle == 2026L]
stopifnot(
  file.exists(workbook),
  file.exists(reports$bulk_audit),
  file.exists(reports$bulk_audit_unmatched),
  coinbase_audit$bulk_candidate_rows == 1L,
  coinbase_audit$api_captured == 1L,
  coinbase_audit$rescued == 0L,
  coinbase_audit$unreachable == 0L,
  gemini_audit$bulk_candidate_rows == 2L,
  gemini_audit$api_captured == 0L,
  gemini_audit$rescued == 1L,
  gemini_audit$unreachable == 1L,
  identical(bulk_unmatched$sub_id, "bulk-unreachable"),
  all(c("source_table", "match_value", "company") %in%
    names(company_review_queue)),
  corporate_status$company_match_status[[1L]] == "accepted",
  corporate_status$company_match_reason[[1L]] ==
    "verified_company_name_entity",
  corporate_status$is_entity_contributor[[1L]] == 1L,
  qualified_report_status$company_match_status[[1L]] == "accepted",
  qualified_report_status$company_match_reason[[1L]] ==
    "verified_company_name:persistent_override:accept",
  qualified_report_status$is_entity_contributor[[1L]] == 1L,
  "## Run wall-clock" %in% reliability_lines,
  sum(grepl(
    "| test-run | 2024,2026 | 60 |",
    reliability_lines,
    fixed = TRUE
  )) == 1L,
  "## Corporate rows and match coverage" %in% reliability_lines,
  any(grepl(
    "| total_rows | accepted_rows | review_rows | rejected_rows |",
    reliability_lines,
    fixed = TRUE
  )),
  # 85 metrics before Conduit got its own column group (3 each in the company
  # block, the three role blocks and the Company PAC allocation).
  length(spending_stats_headers(2026L)) == 101L,
  spending_stats_metric_count() == 100L,
  identical(dim(spending_stats_sheet()), c(84L, 102L)),
  length(spending_stats_company_formulas(2026L, "$B3", 4L)) == 100L,
  spending_report_company_labels("Foris Dax Inc") == "Crypto.com"
)

# Conduit layout: one group directly after each Carey group, four in all, plus
# its own Company PAC allocation group, in both the headers and the formulas.
conduit_headers <- spending_stats_headers(2026L)
conduit_positions <- which(conduit_headers == "Conduit")
stopifnot(
  length(conduit_positions) == 4L,
  all(conduit_headers[conduit_positions - 3L] == "Carey"),
  all(conduit_headers[conduit_positions + 3L] == "Company PAC"),
  identical(
    conduit_headers[conduit_positions + 1:2][1:2],
    c("% of Total Giving", "Partisan Split")
  ),
  identical(
    tail(conduit_headers, 6L),
    c(
      "Company PAC: Carey", "% to Carey", "Partisan Split",
      "Company PAC: Conduit", "% to Conduit", "Partisan Split"
    )
  ),
  identical(names(spending_stats_category_groups())[2:3], c("Carey", "Conduit")),
  !anyDuplicated(unlist(spending_stats_category_groups())),
  identical(
    spending_stats_allocation_categories(),
    c("Super PAC", "Candidate", "Trade Association", "Carey", "Conduit")
  )
)
conduit_formulas <- spending_stats_company_formulas(2026L, "$B3", 4L)
stopifnot(
  # Company block, three role blocks and the allocation: five Conduit totals.
  sum(grepl('"Conduit"', conduit_formulas, fixed = TRUE)) == 4L * 3L + 3L,
  grepl("PAC_DISBURSEMENT", conduit_formulas[[98L]], fixed = TRUE),
  grepl('"Conduit"', conduit_formulas[[98L]], fixed = TRUE),
  !grepl('"Carey"', conduit_formulas[[98L]], fixed = TRUE)
)

# Company PAC allocation: every disbursement category, Conduit included, has its
# own share column, so shares over a fully classified disbursement set still sum
# to 100%, and a Conduit disbursement can no longer fall out of the allocation.
allocation_fixture <- data.table::data.table(
  record_type = "PAC_DISBURSEMENT", year = 2026L, company = "Coinbase",
  employee_tier = "",
  recipient_category = c(
    "Super PAC", "Candidate", "Trade Association", "Carey", "Conduit"
  ),
  party = c("R", "D", "", "D", "R"),
  amount = c(400, 300, 100, 50, 150), records = 1L
)
allocation_values <- spending_stats_company_cached_values(
  allocation_fixture, 2026L, "Coinbase"
)
allocation_base <- spending_stats_metric_count() - 14L
allocation_shares <- allocation_values[allocation_base + c(2L, 5L, 8L, 10L, 13L)]
allocation_totals <- allocation_values[allocation_base + c(1L, 4L, 7L, 9L, 12L)]
no_conduit_values <- spending_stats_company_cached_values(
  allocation_fixture[recipient_category != "Conduit"], 2026L, "Coinbase"
)
stopifnot(
  length(allocation_values) == 100L,
  isTRUE(all.equal(allocation_totals, c(400, 300, 100, 50, 150))),
  isTRUE(all.equal(sum(allocation_shares), 1)),
  isTRUE(all.equal(allocation_shares[[5L]], 0.15)),
  # Conduit money is R only in this fixture: its own Partisan Split is 100% R.
  isTRUE(all.equal(allocation_values[[allocation_base + 14L]], 1)),
  # Carey keeps only its own 50, not the conduit 150.
  isTRUE(all.equal(allocation_values[[allocation_base + 9L]], 50)),
  # With no conduit disbursement the other four shares still make up 100%.
  isTRUE(all.equal(
    sum(no_conduit_values[allocation_base + c(2L, 5L, 8L, 10L)]), 1
  )),
  no_conduit_values[[allocation_base + 12L]] == 0
)

# Category reconciliation: Conduit is a destination of its own, every dollar
# lands in exactly one category group, and an unknown label is still refused.
reconciliation_categories <- c(
  "Super PAC", "Carey", "Conduit", "Company PAC", "Leadership PAC",
  "Trade Association", "Candidate", "Party Committee", "JFC", "Unclassified"
)
reconciliation_facts <- data.table::data.table(
  record_type = c(rep("CONTRIBUTION", 10L), "EXCLUDED_527_501C"),
  year = 2026L, company = "Coinbase",
  recipient_category = c(reconciliation_categories, "527"),
  amount = c(seq_len(10L) * 10, 99)
)
stopifnot(
  isTRUE(spending_assert_category_reconciliation(reconciliation_facts)),
  inherits(try(
    spending_assert_category_reconciliation(data.table::copy(
      reconciliation_facts
    )[, recipient_category := sub("^Conduit$", "Conduits", recipient_category)]),
    silent = TRUE
  ), "try-error")
)
original_industry <- MENTAT_INDUSTRY
MENTAT_INDUSTRY <- "Coverage Test"
company_overflow_error <- tryCatch(
  {
    spending_stats_companies(paste0("Company ", seq_len(11L)))
    ""
  },
  error = function(condition) conditionMessage(condition)
)
MENTAT_INDUSTRY <- original_industry
stopifnot(grepl("at most ten companies", company_overflow_error, fixed = TRUE))

statuses <- DBI::dbGetQuery(
  connection,
  paste(
    "SELECT sub_id, company_match_status, company_match_reason",
    "FROM contributions ORDER BY sub_id"
  )
)
stopifnot(
  statuses$company_match_status[statuses$sub_id == "gemini-transport"] == "review",
  statuses$company_match_status[statuses$sub_id == "gemini-trust"] == "accepted",
  statuses$company_match_status[statuses$sub_id == "gemini-exact"] == "accepted",
  statuses$company_match_reason[statuses$sub_id == "gemini-exact"] ==
    "verified_roster_alias"
)

sheets <- readxl::excel_sheets(workbook)
stopifnot(identical(
  sheets,
  c(
    "Stats (XXX Company)",
    "Employees (XXX Company)",
    "Coinbase 2025-2026",
    "Gemini 2025-2026",
    "Crypto Industry 2025-2026",
    "Sheet4"
  )
))

stats <- readxl::read_excel(
  workbook,
  sheet = "Stats (XXX Company)",
  range = "A1:CX84",
  col_names = FALSE,
  .name_repair = "minimal"
)
stopifnot(
  nrow(stats) == 84L,
  ncol(stats) == 102L,
  grepl("flagged 1 row", stats[[2L]][[1L]], fixed = TRUE),
  stats[[2L]][[2L]] == "Company 2026",
  stats[[3L]][[2L]] == "Total Giving",
  stats[[6L]][[2L]] == "Corporate Giving",
  stats[[2L]][[84L]] == "Change from last year"
)

xml_dir <- tempfile("mentat-report-xml-")
dir.create(xml_dir)
on.exit(if (dir.exists(xml_dir)) {
  unlink(xml_dir, recursive = TRUE, force = TRUE)
}, add = TRUE)
utils::unzip(
  workbook,
  files = c(
    paste0("xl/worksheets/sheet", seq_along(sheets), ".xml"),
    "xl/styles.xml",
    "xl/workbook.xml"
  ),
  exdir = xml_dir
)
stats_xml <- spending_read_xml(
  file.path(xml_dir, "xl", "worksheets", "sheet1.xml")
)
raw_xml <- spending_read_xml(
  file.path(xml_dir, "xl", "worksheets", "sheet3.xml")
)
styles_xml <- spending_read_xml(file.path(xml_dir, "xl", "styles.xml"))
workbook_xml <- spending_read_xml(file.path(xml_dir, "xl", "workbook.xml"))
template_xml_dir <- tempfile("mentat-template-xml-")
dir.create(template_xml_dir)
on.exit(if (dir.exists(template_xml_dir)) {
  unlink(template_xml_dir, recursive = TRUE, force = TRUE)
}, add = TRUE)
template <- spending_report_template_path()
utils::unzip(
  template,
  files = paste0("xl/worksheets/sheet", 1:4, ".xml"),
  exdir = template_xml_dir
)
output_cols <- vapply(seq_along(sheets), function(sheet) {
  spending_xml_cols_block(spending_read_xml(file.path(
    xml_dir,
    "xl",
    "worksheets",
    paste0("sheet", sheet, ".xml")
  )))
}, character(1L))
template_cols <- vapply(1:4, function(sheet) {
  spending_xml_cols_block(spending_read_xml(file.path(
    template_xml_dir,
    "xl",
    "worksheets",
    paste0("sheet", sheet, ".xml")
  )))
}, character(1L))
expected_stats_cols <- spending_set_column_width(
  spending_stats_expand_template_cols(template_cols[[1L]]),
  9L,
  14
)
formula_matches <- gregexpr("<f>", stats_xml, fixed = TRUE)[[1L]]
formula_count <- if (formula_matches[[1L]] == -1L) 0L else length(formula_matches)
formula_blocks <- regmatches(
  stats_xml,
  gregexpr(
    '<c[^>]*><f>[\\s\\S]*?</f><v>[^<]*</v></c>',
    stats_xml,
    perl = TRUE
  )
)[[1L]]
formula_cache_text <- sub(
  '^[\\s\\S]*<v>([^<]*)</v></c>$',
  "\\1",
  formula_blocks,
  perl = TRUE
)
formula_cache_numeric <- suppressWarnings(as.numeric(formula_cache_text))
formula_text <- sub(
  '^[\\s\\S]*<f>([\\s\\S]*?)</f>[\\s\\S]*$',
  "\\1",
  formula_blocks,
  perl = TRUE
)
formula_cache_value <- function(cell) {
  block <- formula_blocks[grepl(
    paste0('r="', cell, '"'),
    formula_blocks,
    fixed = TRUE
  )]
  if (length(block) != 1L) stop("Formula cache cell not unique: ", cell)
  as.numeric(sub(
    '^[\\s\\S]*<v>([^<]*)</v></c>$',
    "\\1",
    block,
    perl = TRUE
  ))
}
fact_last_row <- max(2L, nrow(spending_report_rows(connection)) + 1L)
person_formula <- paste0(
  "SUMIFS('Sheet4'!$G$2:$G$", fact_last_row,
  ",'Sheet4'!$A$2:$A$", fact_last_row,
  ",\"CONTRIBUTION\",'Sheet4'!$B$2:$B$", fact_last_row,
  ",2026,'Sheet4'!$C$2:$C$", fact_last_row, ",$B3)"
)
corporate_formula <- paste0(
  "SUMIFS('Sheet4'!$G$2:$G$", fact_last_row,
  ",'Sheet4'!$A$2:$A$", fact_last_row,
  ",\"CORPORATE\",'Sheet4'!$B$2:$B$", fact_last_row,
  ",2026,'Sheet4'!$C$2:$C$", fact_last_row, ",$B3)"
)
stopifnot(
  identical(output_cols[[1L]], expected_stats_cols),
  identical(output_cols[[2L]], template_cols[[2L]]),
  all(!nzchar(output_cols[3:6])),
  identical(output_cols[[6L]], template_cols[[4L]]),
  formula_count == 7100L,
  length(formula_blocks) == 7100L,
  max(nchar(formula_text), na.rm = TRUE) <= 8192L,
  sum(!is.na(formula_cache_numeric) & formula_cache_numeric != 0) > 0L,
  grepl(person_formula, stats_xml, fixed = TRUE),
  grepl(corporate_formula, stats_xml, fixed = TRUE),
  grepl("AVERAGE(C3:C12)", stats_xml, fixed = TRUE),
  grepl("C13-C27", stats_xml, fixed = TRUE),
  !grepl("#REF!", stats_xml, fixed = TRUE),
  # writexl emits <c r="C1" t="inlineStr" s="2">, so a fixed-substring match on
  # attribute order is brittle. Assert the cell carries the style, order-agnostic.
  grepl('<c r="C1"[^>]*\\bs="2"', raw_xml),
  grepl('<c r="D1"[^>]*\\bs="3"', raw_xml),
  grepl('numFmtId="14"', styles_xml, fixed = TRUE),
  grepl('calcMode="auto"', workbook_xml, fixed = TRUE),
  grepl('calcId="191029"', workbook_xml, fixed = TRUE),
  grepl('fullCalcOnLoad="0"', workbook_xml, fixed = TRUE),
  grepl('forceFullCalc="0"', workbook_xml, fixed = TRUE),
  length(gregexpr('shrinkToFit="1"', styles_xml, fixed = TRUE)[[1L]]) == 2L,
  grepl(
    'formatCode="&quot;$&quot;#,##0.00_);[Red]\\(&quot;$&quot;#,##0.00\\)"',
    styles_xml,
    fixed = TRUE
  )
)

employees <- readxl::read_excel(
  workbook,
  sheet = "Employees (XXX Company)",
  col_names = FALSE,
  .name_repair = "minimal"
)
stopifnot(
  identical(
    as.character(unlist(employees[1L, ], use.names = FALSE)),
    c("Employee Name", "Employee Title", "Company", "Zipcode", "State")
  ),
  "TUSAR, GREG" %in% employees[[1L]],
  "DRIVER, DONNA" %in% employees[[1L]]
)

raw <- readxl::read_excel(
  workbook,
  sheet = "Coinbase 2025-2026",
  col_names = FALSE,
  .name_repair = "minimal"
)
# The per-company raw sheets carry a header row, so reading the full sheet types the
# Date column as character. Assert the date TYPE on the data range only.
raw_data <- readxl::read_excel(
  workbook,
  sheet = "Coinbase 2025-2026",
  range = paste0("A2:G", nrow(raw)),
  col_names = FALSE,
  .name_repair = "minimal"
)
expected_coinbase_amounts <- c(
  1000, 35, 35, 45, 55, 3500, -3500, 5000, 7000,
  75, 75, rep(1.67, 10L)
)
stopifnot(
  ncol(raw) == 15L,
  nrow(raw) == length(expected_coinbase_amounts) + 1L,
  inherits(raw_data[[3L]], "POSIXct"),
  isTRUE(all.equal(
    sort(as.numeric(raw[[4L]][-1L])),
    sort(expected_coinbase_amounts),
    check.attributes = FALSE
  )),
  raw[[1L]][[1L]] == "Name",
  raw[[2L]][[1L]] == "Recipient",
  raw[[5L]][[1L]] == "CSZ",
  raw[[6L]][[1L]] == "Employer",
  raw[[7L]][[1L]] == "Occupation",
  raw[[8L]][[1L]] == "Excluded Reason",
  raw[[9L]][[1L]] == "Is Memo Leg",
  raw[[10L]][[1L]] == "Is Conduit Leg",
  raw[[11L]][[1L]] == "Superseded By Amendment",
  raw[[12L]][[1L]] == "Duplicate Filing Version",
  raw[[13L]][[1L]] == "Duplicate Memo",
  raw[[14L]][[1L]] == "Company Match Status",
  raw[[15L]][[1L]] == "Company Match Reason",
  "TUSAR, GREG" %in% raw[[1L]],
  "COINBASE, INC." %in% raw[[1L]],
  sum(raw[[1L]] == "PERSON, BLUE", na.rm = TRUE) == 2L,
  sum(raw[[1L]] == "REDESIGNATION, RITA", na.rm = TRUE) == 2L,
  sum(raw[[1L]] == "BALACHANDER, SUPRIYA", na.rm = TRUE) == 10L,
  as.integer(raw[[13L]][raw[[1L]] == "MEMO, MATCH" & raw[[9L]] == "1"][[1L]]) == 1L,
  !"DRIVER, DONNA" %in% raw[[1L]]
)
gemini_raw <- readxl::read_excel(
  workbook,
  sheet = "Gemini 2025-2026",
  col_names = FALSE,
  .name_repair = "minimal"
)
stopifnot(
  ncol(gemini_raw) == 15L,
  "DRIVER, DONNA" %in% gemini_raw[[1L]],
  gemini_raw[[14L]][gemini_raw[[1L]] == "DRIVER, DONNA"][[1L]] == "review"
)

support <- readxl::read_excel(
  workbook,
  sheet = "Sheet4",
  col_names = FALSE,
  .name_repair = "minimal"
)
redesignation_fact <- support[[12L]] %in% c(
  "redesignation-positive",
  "redesignation-negative"
)
memo_pair_fact <- support[[12L]] %in% c("memo-pair-primary", "memo-pair-copy")
expected_c3 <- sum(as.numeric(support[[7L]][
  support[[1L]] %in% c("CONTRIBUTION", "CORPORATE") &
    support[[2L]] == 2026L &
    support[[3L]] == "Coinbase"
]), na.rm = TRUE)
stopifnot(
  support[[1L]][[1L]] == "Record Type",
  support[[2L]][[1L]] == "Year",
  support[[3L]][[1L]] == "Company",
  support[[7L]][[1L]] == "Amount",
  support[[8L]][[1L]] == "Recipient / Formula Definition",
  support[[10L]][[1L]] == "Raw Input Row",
  support[[11L]][[1L]] == "Employee Row",
  support[[1L]][[2L]] == "CONTRIBUTION",
  support[[2L]][[2L]] == 2026,
  # Display-name rule: the template's "Coinbase Exchange" normalizes to the
  # canonical name Paul specified. Fact-table company labels use it too.
  support[[3L]][[2L]] == "Coinbase",
  support[[4L]][[2L]] == "Middle Management",
  support[[5L]][[2L]] == "Company PAC",
  support[[7L]][[2L]] == 1000,
  raw[[1L]][as.integer(support[[10L]][[2L]])] == support[[9L]][[2L]],
  employees[[1L]][as.integer(support[[11L]][[2L]])] == support[[9L]][[2L]],
  employees[[3L]][as.integer(support[[11L]][[2L]])] == support[[3L]][[2L]],
  any(support[[5L]] == "Super PAC", na.rm = TRUE),
  any(support[[5L]] == "Candidate", na.rm = TRUE),
  any(support[[6L]] == "D", na.rm = TRUE),
  sum(redesignation_fact) == 2L,
  sum(as.numeric(support[[7L]][redesignation_fact])) == 0,
  sum(memo_pair_fact) == 1L,
  support[[12L]][memo_pair_fact][[1L]] == "memo-pair-primary",
  any(
    support[[1L]] == "CORPORATE" &
      support[[9L]] == "COINBASE COMMERCE (EXCHANGE)",
    na.rm = TRUE
  ),
  isTRUE(all.equal(
    formula_cache_value("C3"),
    expected_c3,
    tolerance = 1e-12
  )),
  any(grepl("1 row(s) were flagged", support[[8L]], fixed = TRUE), na.rm = TRUE)
)

# Conduit (ActBlue C00401224, WinRed C00694323) is its own category in Sheet4 and
# its own Stats column group; Carey no longer holds them, and every dollar is
# still in exactly one category group.
support_fact <- support[[1L]] %in% c("CONTRIBUTION", "CORPORATE")
conduit_fact <- support_fact & support[[5L]] %in% "Conduit"
conduit_2026_coinbase <- sum(as.numeric(support[[7L]][
  conduit_fact & support[[2L]] %in% "2026" & support[[3L]] %in% "Coinbase"
]))
group_cells <- paste0(c("I", "L", "O", "R", "U", "X"), "3")
stopifnot(
  setequal(
    support[[12L]][conduit_fact],
    c("actblue-primary", "winred-primary", paste0("balachander-", 1:10))
  ),
  all(support[[13L]][conduit_fact] == "fec:registered_conduit:earmark_platform"),
  isTRUE(all.equal(sum(as.numeric(support[[7L]][conduit_fact])), 35 + 45 + 16.7)),
  !any(support[[5L]][support_fact] %in% "Carey"),
  conduit_2026_coinbase == 80,
  identical(
    as.character(unlist(stats[2L, 3:102], use.names = FALSE)),
    spending_stats_headers(2026L)[-1L]
  ),
  stats[[12L]][[2L]] == "Carey",
  stats[[15L]][[2L]] == "Conduit",
  stats[[18L]][[2L]] == "Company PAC",
  stats[[102L]][[2L]] == "Partisan Split",
  stats[[100L]][[2L]] == "Company PAC: Conduit",
  grepl('"Conduit"', stats_xml, fixed = TRUE),
  isTRUE(all.equal(formula_cache_value("O3"), conduit_2026_coinbase)),
  formula_cache_value("L3") == 0,
  isTRUE(all.equal(
    sum(vapply(group_cells, formula_cache_value, numeric(1L))),
    formula_cache_value("C3"),
    tolerance = 1e-12
  ))
)

DBI::dbExecute(
  connection,
  paste(
    "INSERT INTO corporate_giving",
    "(sub_id, contributor_name, entity_type, industry, company, matched_variant,",
    "cycle, scraped_at, company_match_status, company_match_reason)",
    "VALUES ('corporate-ambiguous', 'COINBASE TRANSPORT LLC', 'ORG',",
    "'Crypto', 'Coinbase', 'Coinbase', 2026, '2026-07-18T00:00:00Z',",
    "'review', 'migration_requires_revalidation')"
  )
)
retag_corporate_company_matches(connection)
ambiguous_corporate <- DBI::dbGetQuery(
  connection,
  paste(
    "SELECT company_match_status, company_match_reason FROM corporate_giving",
    "WHERE sub_id='corporate-ambiguous'"
  )
)
stopifnot(
  ambiguous_corporate$company_match_status[[1L]] == "review",
  ambiguous_corporate$company_match_reason[[1L]] == "corporate_name_not_verified"
)
ambiguous_queue_path <- write_company_match_review_queue(connection, data_root)
ambiguous_queue <- data.table::fread(ambiguous_queue_path)
stopifnot(
  nrow(ambiguous_queue[
    source_table == "corporate_giving" &
      match_value == "COINBASE TRANSPORT LLC" &
      company == "Coinbase"
  ]) == 1L
)
DBI::dbExecute(
  connection,
  paste(
    "DELETE FROM corporate_giving WHERE sub_id IN",
    "('corporate-ambiguous')"
  )
)

store_counts <- collection_company_store_counts(connection, "Coinbase")
expected_store_counts <- DBI::dbGetQuery(
  connection,
  paste(
    "SELECT",
    "(SELECT COUNT(*) FROM contributions WHERE company='Coinbase')",
    "AS contributions,",
    "(SELECT COUNT(*) FROM corporate_giving WHERE company='Coinbase')",
    "AS corporate_giving"
  )
)
stopifnot(
  store_counts[["contributions"]] ==
    expected_store_counts$contributions[[1L]],
  store_counts[["corporate_giving"]] ==
    expected_store_counts$corporate_giving[[1L]],
  store_counts[["total"]] ==
    expected_store_counts$contributions[[1L]] +
      expected_store_counts$corporate_giving[[1L]],
  store_counts[["corporate_giving"]] > 0L,
  collection_receipt_status(
    "ok",
    c(contributions = 0L, corporate_giving = 1L, total = 1L)
  ) == "GAINED",
  collection_receipt_status(
    "ok",
    c(contributions = 0L, corporate_giving = 0L, total = 0L)
  ) == "unchanged",
  collection_receipt_status(
    "error",
    c(contributions = 1L, corporate_giving = 1L, total = 2L)
  ) == "ERROR"
)

# The template's Stats widths predate the Conduit groups. Encode each template
# column's index as its width, expand, and check every new column carries the
# width of the template column that header was sized for: a Conduit column the
# width of the Carey column it follows, every later column shifted right by the
# groups inserted before it.
width_fixture <- paste0(
  "<cols>",
  paste0(
    '<col min="', 2:87, '" max="', 2:87, '" width="', 2:87,
    '" bestFit="1" customWidth="1"/>',
    collapse = ""
  ),
  "</cols>"
)
expanded_cols <- spending_stats_expand_template_cols(width_fixture)
expanded_blocks <- regmatches(
  expanded_cols,
  gregexpr("<col\\s[^>]*/>", expanded_cols, perl = TRUE)
)[[1L]]
expanded_attribute <- function(name) {
  as.numeric(sub(
    paste0('^.*\\b', name, '="([0-9.]+)".*$'),
    "\\1",
    expanded_blocks,
    perl = TRUE
  ))
}
expanded_min <- expanded_attribute("min")
expanded_max <- expanded_attribute("max")
expanded_width <- expanded_attribute("width")
new_headers <- spending_stats_headers(2026L)[-1L]
conduit_starts <- which(new_headers %in% c("Conduit", "Company PAC: Conduit"))
is_conduit_column <- rep(FALSE, length(new_headers))
for (start in conduit_starts) is_conduit_column[start + 0:2] <- TRUE
legacy_column <- integer(length(new_headers))
legacy_column[!is_conduit_column] <- seq_len(sum(!is_conduit_column)) + 2L
for (start in conduit_starts) legacy_column[start + 0:2] <- legacy_column[start - 3:1]
stopifnot(
  length(conduit_starts) == 5L,
  identical(expanded_min, expanded_max),
  !anyDuplicated(expanded_min),
  expanded_width[match(2L, expanded_min)] == 2,
  identical(
    expanded_width[match(seq_along(new_headers) + 2L, expanded_min)],
    as.numeric(legacy_column)
  ),
  # A template that was never sized is left alone.
  identical(spending_stats_expand_template_cols(""), "")
)

template_cols_fixture <- '<cols><col min="1" max="1" width="18"/></cols>'
large_sheet_data <- paste0(
  "<sheetData>",
  strrep('<row r="1"><c r="A1"><v>1</v></c></row>', 100000L),
  "</sheetData>"
)
self_closing_format <- paste0(
  '<worksheet><sheetFormatPr defaultRowHeight="15"/>',
  large_sheet_data,
  "</worksheet>"
)
paired_format <- paste0(
  '<worksheet><sheetFormatPr defaultRowHeight="15"></sheetFormatPr>',
  large_sheet_data,
  "</worksheet>"
)
self_closing_restored <- spending_set_xml_cols_block(
  self_closing_format,
  template_cols_fixture
)
paired_restored <- spending_set_xml_cols_block(
  paired_format,
  template_cols_fixture
)
stopifnot(
  grepl(
    paste0('<sheetFormatPr defaultRowHeight="15"/>', template_cols_fixture),
    self_closing_restored,
    fixed = TRUE
  ),
  grepl(
    paste0(
      '<sheetFormatPr defaultRowHeight="15"></sheetFormatPr>',
      template_cols_fixture
    ),
    paired_restored,
    fixed = TRUE
  )
)

cat("test_report: strict company retagging verified\n")
cat("test_report: legacy corporate exact-match revalidation verified\n")
cat("test_report: six-sheet Spending Report contract verified\n")
cat("test_report: exact template column layouts verified\n")
cat("test_report: large worksheet column restoration verified\n")
cat("test_report: contribution and corporate receipt counts verified\n")
cat("test_report: amendment versions and signed redesignation legs verified\n")
cat("test_report: 7,100 uniform Stats formulas and cross-sheet references verified\n")
cat("test_report: Stats caches and fast automatic calculation mode verified\n")
cat("test_report: PASS\n")

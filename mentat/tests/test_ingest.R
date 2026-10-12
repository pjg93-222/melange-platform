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
for (file in c("paths.R", "schema.R", "ingest.R", "collect.R")) {
  source(file.path(project_root, "R", file), local = .GlobalEnv)
}

stopifnot(
  identical(trim_text(factor(c(" alpha ", NA))), c("alpha", "")),
  identical(trim_text(c(123L, NA_integer_)), c("123", "")),
  identical(
    normalize_register_key(factor(c("Acme, Inc.", NA))),
    c("ACME INC", "")
  ),
  identical(normalize_register_key(c(123L, NA_integer_)), c("123", ""))
)

bulk_fixture_dir <- tempfile("mentat-bulk-fixture-")
dir.create(bulk_fixture_dir)
on.exit(unlink(bulk_fixture_dir, recursive = TRUE, force = TRUE), add = TRUE)
bulk_line <- function(sub_id, employer) {
  fields <- rep("", 21L)
  fields[[1L]] <- "C00744946"
  fields[[8L]] <- "PLAYER, TEST"
  fields[[11L]] <- "98101"
  fields[[12L]] <- employer
  fields[[13L]] <- "ENGINEER"
  fields[[15L]] <- "1000"
  fields[[21L]] <- sub_id
  paste(fields, collapse = "|")
}
writeLines(
  c(
    bulk_line("kraken-good", "KRAKEN DIGITAL"),
    bulk_line("kraken-hockey", "SEATTLE KRAKEN")
  ),
  file.path(bulk_fixture_dir, "indiv24_crypto_superset.psv"),
  useBytes = TRUE
)
bulk_fixture <- read_bulk_supersets(bulk_fixture_dir, cycles = 2024L)
bulk_register <- build_observed_employer_register(
  bulk_fixture,
  data.table::data.table(
    industry = "Crypto",
    company = "Kraken",
    variant = "Kraken",
    exclusions = "SEATTLE KRAKEN"
  ),
  data.frame(
    employer_key = character(), company = character(), decision = character(),
    stringsAsFactors = FALSE
  )
)
stopifnot(
  identical(bulk_register$observed_employers$employer_string, "KRAKEN DIGITAL"),
  identical(bulk_register$bulk_candidates$sub_id, "kraken-good"),
  !"SEATTLE KRAKEN" %in% bulk_register$observed_employers$employer_string
)

header_only_overrides <- tempfile(fileext = ".csv")
writeLines("employer_key,company,decision", header_only_overrides, useBytes = TRUE)
empty_overrides <- read_company_match_overrides(header_only_overrides)
unlink(header_only_overrides, force = TRUE)
stopifnot(
  nrow(empty_overrides) == 0L,
  all(vapply(empty_overrides, is.character, logical(1L))),
  identical(names(empty_overrides), c(
    "employer_key", "company", "decision", "state", "occupation_pattern"
  ))
)
legacy_overrides <- tempfile(fileext = ".csv")
writeLines(
  c("employer_key,company,decision", "TEST EMPLOYER,Gemini,reject"),
  legacy_overrides,
  useBytes = TRUE
)
legacy_override_rows <- read_company_match_overrides(legacy_overrides)
unlink(legacy_overrides, force = TRUE)
stopifnot(
  nrow(legacy_override_rows) == 1L,
  legacy_override_rows$state[[1L]] == "",
  legacy_override_rows$occupation_pattern[[1L]] == ""
)

data_root <- tempfile("mentat-test-ingest-")
dir.create(data_root, recursive = TRUE)
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
result <- ingest_all(
  connection,
  input_dir = file.path(project_root, "inputs"),
  data_root = data_root,
  print_summary = FALSE
)

company_roster_path <- file.path(project_root, "inputs", "Company Name.xlsx")
roster_sheet_names <- readxl::excel_sheets(company_roster_path)
workbook_company_counts <- data.table::rbindlist(lapply(
  roster_sheet_names,
  function(sheet) {
    sheet_data <- read_sheet_matrix(company_roster_path, sheet)
    data.table::data.table(
      industry = sheet,
      workbook_blocks = length(roster_blocks(
        sheet_data,
        crypto = identical(sheet, "Crypto")
      ))
    )
  }
))
company_counts <- result$companies[, .(
  companies = data.table::uniqueN(company),
  variants = .N
), by = industry]
roster_count_check <- merge(
  workbook_company_counts,
  company_counts,
  by = "industry",
  all = TRUE
)

bom_csv <- file.path(data_root, "bom-check.csv")
atomic_write_csv(data.frame(name = "José", stringsAsFactors = FALSE), bom_csv)
bom <- readBin(bom_csv, what = "raw", n = 3L)

stopifnot(
  tolower(DBI::dbGetQuery(connection, "PRAGMA journal_mode")[[1L]]) == "wal",
  "memo_review" %in% DBI::dbListFields(connection, "contributions"),
  all(c(
    "company_match_status", "company_match_reason", "file_num",
    "superseded_by_amendment", "duplicate_filing_version", "duplicate_memo"
  ) %in%
        DBI::dbListFields(connection, "contributions")),
  all(c(
    "transaction_id", "original_sub_id", "amendment_indicator", "report_type",
    "file_num", "superseded_by_amendment", "duplicate_filing_version",
    "duplicate_memo"
  ) %in% DBI::dbListFields(connection, "corporate_giving")),
  DBI::dbExistsTable(connection, "attribution_conflicts"),
  DBI::dbExistsTable(connection, "api_work_progress"),
  DBI::dbExistsTable(connection, "api_request_budget"),
  DBI::dbExistsTable(connection, "observed_employers"),
  DBI::dbExistsTable(connection, "bulk_candidates"),
  DBI::dbExistsTable(connection, "company_home_states"),
  "decomposition_log" %in% DBI::dbListFields(connection, "run_log"),
  # 0xEF/0xBB/0xBF parse as doubles in R, so identical() against as.integer()
  # would fail on type even with a correct BOM. Compare integer-to-integer.
  identical(as.integer(bom), c(239L, 187L, 191L)),
  nrow(result$name_diminutives) == 168L,
  data.table::uniqueN(result$name_diminutives[variant == "HARRY", canonical]) == 2L,
  nrow(roster_count_check) == length(roster_sheet_names),
  !anyNA(roster_count_check),
  all(roster_count_check$workbook_blocks > 0L),
  all(roster_count_check$companies == roster_count_check$workbook_blocks),
  all(roster_count_check$variants >= roster_count_check$companies),
  setequal(result$companies$industry, roster_sheet_names),
  all(nzchar(trimws(result$companies$company))),
  all(nzchar(trimws(result$companies$variant))),
  !anyDuplicated(result$companies[, .(industry, company, variant)]),
  result$company_home_states[company == "Gemini", state][[1L]] == "NY",
  nrow(result$company_match_overrides) >= 31L,
  nrow(result$company_match_overrides) == DBI::dbGetQuery(
    connection,
    "SELECT COUNT(*) AS n FROM company_match_overrides"
  )$n[[1L]]
)

crypto <- result$companies[industry == "Crypto"]
crypto_exclusions <- unique(unlist(strsplit(
  paste(na.omit(crypto$exclusions), collapse = "|"),
  "|",
  fixed = TRUE
)))
crypto_exclusions <- crypto_exclusions[nzchar(crypto_exclusions)]
stopifnot(
  nrow(crypto) == 16L,
  data.table::uniqueN(crypto$company) == 10L,
  setequal(crypto_exclusions, c("SEATTLE KRAKEN", "TZERO TECHNOLOGIES")),
  !any(grepl("^(NOT|CIK|JUST\\b)", toupper(crypto$variant), perl = TRUE)),
  grepl("GEMINI", crypto[company == "Gemini", metadata_json][[1L]], fixed = TRUE),
  grepl("2055592", crypto[company == "Gemini", metadata_json][[1L]], fixed = TRUE)
)

stopifnot(
  # 425 not 410: 527/501c register rows are no longer silently deleted at
  # ingest (they were dropped by the old fcase -> NA path). 2022 gains 7 501c
  # rows (168 -> 175); 2024 gains 3 501c + 1 527 (121 -> 125); 2026 is the
  # 2024 register carried forward, so it matches 2024 exactly.
  nrow(result$categories) == 425L,
  result$categories[cycle == 2022L, .N] == 175L,
  result$categories[cycle == 2024L, .N] == 125L,
  result$categories[cycle == 2026L, .N] == 125L,
  result$categories[cycle == 2022L & category %in% c("527", "501c"), .N] == 7L,
  result$categories[cycle == 2024L & category %in% c("527", "501c"), .N] == 4L,
  result$categories[
    cycle == 2022L &
      category %in% c("Candidate", "Party Committee", "JFC", "Unclassified"),
    .N
  ] == 19L,
  result$categories[
    cycle == 2024L &
      category %in% c("Candidate", "Party Committee", "JFC", "Unclassified"),
    .N
  ] == 10L,
  # Party Committee / JFC / Candidate rows carry no lean. Unclassified rows DO
  # keep the View-column lean (C/L) the workbook author marked by hand - that is
  # user-authored data feeding Partisan Split, and must not be discarded merely
  # because the row has no category x-mark.
  all(is.na(result$categories[
    category %in% c("Candidate", "Party Committee", "JFC"),
    lean
  ])),
  result$categories[category == "Unclassified" & !is.na(lean), .N] > 0L,
  all(result$categories[category == "Unclassified" & !is.na(lean), lean]
      %in% c("D", "R")),
  result$categories[cycle == 2026L, all(provisional == 1L)]
)

stopifnot(
  nrow(result$leadership) == 2322L,
  result$leadership[cycle == 2022L, .N] == 746L,
  result$leadership[cycle == 2024L, .N] == 788L,
  result$leadership[cycle == 2026L, .N] == 788L,
  result$leadership[cycle == 2022L & lean == "REVIEW", .N] == 10L,
  result$leadership[cycle == 2024L & lean == "REVIEW", .N] == 12L,
  result$leadership[cycle == 2026L, all(provisional == 1L)]
)

stopifnot(
  nrow(result$trade) == 342L,
  data.table::uniqueN(result$trade$association_name) == 342L,
  !any(grepl("\\]\\]\\[1\\]", result$trade$association_name, perl = TRUE)),
  "National Automatic Merchandising Association (NAMA)" %in%
    result$trade$association_name
)

stopifnot(
  nrow(result$executives) == 50L,
  result$executives[!is.na(executive_name), .N] == 43L,
  result$executives[source_company == "Krakken", company][[1L]] == "Kraken",
  result$executives[source_executive == "Tyler Winkelvoss", executive_name][[1L]] ==
    "Tyler Winklevoss",
  nrow(result$industry_assets) == 5L,
  !"ABS issuers" %in% result$industry_assets$industry
)

stopifnot(
  nrow(result$fixture) == 1189L,
  max(result$fixture$receipt_date) == as.Date("2026-05-31"),
  DBI::dbGetQuery(connection, "SELECT COUNT(*) AS n FROM fixture_crypto")$n == 1189L,
  file.exists(result$summary_path),
  grepl(
    "MENTAT ROSTER: Crypto",
    paste(readLines(result$summary_path, encoding = "UTF-8"), collapse = "\n"),
    fixed = TRUE
  )
)

cat(
  sprintf(
    "test_ingest: industries=%d companies=%d variants=%d crypto_variants=%d\n",
    length(roster_sheet_names),
    sum(company_counts$companies),
    sum(company_counts$variants),
    nrow(crypto)
  ),
  sprintf(
    paste0(
      "test_ingest: categories=%d leadership=%d trade=%d ",
      "executives=%d fixture=%d\n"
    ),
    nrow(result$categories),
    nrow(result$leadership),
    nrow(result$trade),
    nrow(result$executives),
    nrow(result$fixture)
  ),
  "test_ingest: PASS\n",
  sep = ""
)

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
for (file in c("paths.R", "schema.R", "ingest.R", "fec_page.R", "collect.R", "report.R")) {
  source(file.path(project_root, "R", file), local = .GlobalEnv)
}

match_status <- function(company, employer, variants, overrides = NULL) {
  company_employer_match(
    company = company,
    employer = employer,
    contributor_name = "DOE, JANE",
    variants = variants,
    overrides = overrides
  )$status
}

coinbase_corporate_override <- data.frame(
  employer_key = "COINBASE COMMERCE EXCHANGE",
  company = "Coinbase",
  decision = "accept",
  state = "",
  occupation_pattern = "",
  stringsAsFactors = FALSE
)
stopifnot(
  identical(
    collection_completion_detail(
      "api", list(request_count = 7L), skipped = FALSE
    ),
    "engine=api; network_requests=7"
  ),
  identical(
    collection_completion_detail(
      "chrome",
      list(rows = 42L, wall_clock_seconds = 1.25),
      skipped = FALSE
    ),
    paste0(
      "engine=chrome; network_requests=n/a; rows=42; ",
      "collection_wall_seconds=1.250"
    )
  ),
  identical(
    collection_completion_detail(
      "api", list(request_count = 99L), skipped = TRUE
    ),
    "engine=offline; network_requests=0"
  ),
  match_status("Bakkt", "BAKKT HOLDINGS", "Bakkt") == "accepted",
  match_status("Kraken", "KRAKEN TECH", "Kraken") == "accepted",
  match_status(
    "Coinbase",
    "COINBASE COMMERCE (EXCHANGE)",
    "Coinbase",
    overrides = coinbase_corporate_override
  ) ==
    "accepted",
  match_status("Kraken", "KRAKEN COMMERCE", "Kraken") == "review",
  match_status("BINANCE US", "BINANCEUS", c("BINANCE US", "Binance")) == "accepted",
  match_status(
    "Foris Dax Inc",
    "CRYPTO.COM (FORIS DAX INC.)",
    c("Foris Dax Inc", "Crypto.com")
  ) == "accepted",
  match_status("Kraken", "SEATTLE KRAKEN", "Kraken") == "rejected",
  match_status("Kraken", "SNAPPY KRAKEN", "Kraken") == "rejected",
  match_status("Gemini", "GEMINI TRANSPORTATION", "Gemini") == "review",
  match_status("Gemini", "G.E. MINISTRIES INC.", "Gemini") == "review",
  match_status("Foris Dax Inc", "CRYPTO COMPANY", "Crypto.com") == "review",
  match_status(
    "Foris Dax Inc",
    "CRYPTO.COM ARENA",
    c("Foris Dax Inc", "Crypto.com")
  ) == "review",
  match_status(
    "BINANCE US",
    "TOP CRYPTO COMPANY - BINANCE",
    c("BINANCE US", "Binance")
  ) == "rejected"
)

stopifnot(
  is.na(exclusion_reason(
    "COINBASE",
    "ENGINEER",
    "TZERO TECHNOLOGIES",
    "DOE, JANE"
  )),
  exclusion_reason(
    "COINBASE",
    "ENGINEER",
    "TZERO TECHNOLOGIES",
    "TZERO TECHNOLOGIES LLC"
  ) == "exclusion_pattern:TZERO TECHNOLOGIES",
  unmarked_recipient_category("Democratic Congressional Campaign Cmte") ==
    "Party Committee",
  unmarked_recipient_category("Democratic Senatorial Campaign Cmte.") ==
    "Party Committee",
  unmarked_recipient_category("National Republican Congressional Cmte") ==
    "Party Committee",
  unmarked_recipient_category("National Republican Senatorial Comm") ==
    "Party Committee",
  unmarked_recipient_category("Some Unmarked Organization") == "Unclassified"
)

stopifnot(
  nickname_canonical("Greg") == "GREGORY",
  nickname_canonical("Gregory") == "GREGORY",
  person_identity_key("TUSAR, GREG") == person_identity_key("TUSAR, GREGORY"),
  nickname_canonical("Harry") == "HARRY",
  nickname_canonical("Rick") == "RICK",
  person_identity_key("PERSON, HARRY") != person_identity_key("PERSON, HAROLD"),
  person_identity_key("PERSON, RICK") != person_identity_key("PERSON, RICHARD")
)

home_states <- data.frame(
  company = "Gemini", state = "NY", source = "test_register",
  stringsAsFactors = FALSE
)
gemini_ny_product <- company_employer_match(
  "Gemini", "Gemini", "EMPLOYEE, ERIN", "Gemini",
  contributor_state = "NY", contributor_occupation = "PRODUCT MANAGER",
  home_states = home_states
)
gemini_nh_cnc <- company_employer_match(
  "Gemini", "Gemini", "WORKER, CASEY", "Gemini",
  contributor_state = "NH", contributor_occupation = "CNC PROGRAMMER",
  home_states = home_states
)
gemini_ny_truck <- company_employer_match(
  "Gemini", "Gemini", "DRIVER, DANA", "Gemini",
  contributor_state = "NY", contributor_occupation = "TRUCK DRIVER",
  home_states = home_states
)
stopifnot(
  gemini_ny_product$status == "accepted",
  gemini_ny_product$reason == "ambiguous_brand_home_state",
  gemini_nh_cnc$status == "rejected",
  gemini_ny_truck$status == "review"
)

stopifnot(
  !is.na(junk_employer_reason("NOT EMPLOYED")),
  !is.na(junk_employer_reason("SELF-EMPLOYED")),
  is.na(junk_employer_reason("SELF STORAGE COMPANY")),
  identity_candidate_initial_match(
    "Kraken",
    "NOT EMPLOYED",
    NA_character_,
    data.frame(
      employer_key = character(),
      company = character(),
      decision = character(),
      stringsAsFactors = FALSE
    )
  )$status == "rejected"
)

scoped_override <- data.frame(
  employer_key = "GEMINI",
  company = "Gemini",
  decision = "accept",
  state = "NH",
  occupation_pattern = "CNC PROGRAMMER",
  stringsAsFactors = FALSE
)
stopifnot(company_employer_match(
  "Gemini", "Gemini", "WORKER, CASEY", "Gemini",
  overrides = scoped_override,
  excluded_reason = "exclusion_pattern:GEMINI",
  contributor_state = "NH",
  contributor_occupation = "CNC PROGRAMMER",
  home_states = home_states
)$status == "accepted")

accept_override <- data.frame(
  employer_key = "SEATTLE KRAKEN",
  company = "Kraken",
  decision = "accept",
  stringsAsFactors = FALSE
)
reject_override <- data.frame(
  employer_key = "BAKKT HOLDINGS",
  company = "Bakkt",
  decision = "reject",
  stringsAsFactors = FALSE
)
stopifnot(
  match_status("Kraken", "SEATTLE KRAKEN", "Kraken", accept_override) == "accepted",
  match_status("Bakkt", "BAKKT HOLDINGS", "Bakkt", reject_override) == "rejected",
  company_employer_match(
    company = "Kraken",
    employer = "SEATTLE KRAKEN",
    contributor_name = "SEATTLE KRAKEN LLC",
    variants = "Kraken",
    overrides = accept_override,
    excluded_reason = "exclusion_pattern:SEATTLE KRAKEN"
  )$status == "accepted"
)

partition <- partition_company_variants(c("BINANCE US", "Binance"))
stopifnot(
  identical(partition$phase1, "Binance"),
  identical(partition$phase2, "BINANCE US"),
  length(partition$covered) == 0L
)
employer_url <- fec_build_url(2024L, "Crypto.com")
stopifnot(
  grepl("contributor_employer=Crypto.com", employer_url, fixed = TRUE),
  !grepl("CRYPTO%20COM", employer_url, fixed = TRUE)
)

stopifnot(
  is_entity_contributor_value("ORG", "DOE, JANE", "Kraken", "Kraken") == 1L,
  is_entity_contributor_value(
    "IND",
    "BINANCE US",
    "BINANCE US",
    c("BINANCE US", "Binance")
  ) == 1L,
  is_entity_contributor_value("IND", "DOE, JANE", "Kraken", "Kraken") == 0L,
  is_entity_contributor_value("", "DOE, JANE", "Kraken", "Kraken") == 0L,
  is_entity_contributor_value(NA_character_, "DOE, JANE", "Kraken", "Kraken") == 0L,
  spending_contributor_tier("CEO", TRUE, TRUE) == "Corporate",
  spending_contributor_tier("CEO", TRUE, FALSE) == "Chief"
)

unsafe_names <- c(
  "This [illegal]: sheet/name* is much longer than thirty-one characters",
  "Collision/Name",
  "Collision:Name",
  "collision name"
)
safe_names <- spending_unique_sheet_names(unsafe_names)
stopifnot(
  all(nchar(safe_names) <= 31L),
  all(!grepl("[", safe_names, fixed = TRUE)),
  all(!grepl("]", safe_names, fixed = TRUE)),
  all(!grepl(":", safe_names, fixed = TRUE)),
  all(!grepl("/", safe_names, fixed = TRUE)),
  !anyDuplicated(toupper(safe_names)),
  identical(safe_names[[2L]], "Collision Name"),
  identical(safe_names[[3L]], "Collision Name~2"),
  identical(safe_names[[4L]], "collision name~3")
)

headers <- spending_stats_headers(2024L)
formulas <- spending_stats_company_formulas(2024L, "$B3", 10L)
category_groups <- spending_stats_category_groups()
category_lookup <- setNames(
  rep(names(category_groups), lengths(category_groups)),
  unlist(category_groups, use.names = FALSE)
)
stopifnot(
  length(headers) == 101L,
  length(formulas) == 100L,
  sum(headers == "Corporate Giving") == 1L,
  identical(names(spending_stats_category_groups()), c(
    "Super PAC", "Carey", "Conduit", "Company PAC", "Trade Association",
    "Candidate"
  )),
  # One Super PAC and one Carey column in the company block and in each of
  # the three role blocks. The second Super PAC header in a role block used
  # to be the Carey column's, so Carey read as a super PAC.
  sum(headers == "Super PAC") == 4L,
  sum(headers == "Carey") == 4L,
  identical(headers[which(headers == "Super PAC") + 3L], rep("Carey", 4L)),
  # ActBlue and WinRed have their own Conduit column group straight after each
  # Carey group, not a share of the Carey column.
  sum(headers == "Conduit") == 4L,
  identical(headers[which(headers == "Carey") + 3L], rep("Conduit", 4L)),
  any(grepl("Leadership PAC", formulas, fixed = TRUE)),
  !any(grepl("EXCLUDED_527_501C", formulas, fixed = TRUE))
)
stopifnot(identical(
  unname(category_lookup[c(
    "Super PAC", "Carey", "Conduit", "Company PAC", "Leadership PAC",
    "Trade Association", "Candidate", "Party Committee", "JFC", "Unclassified"
  )]),
  c(
    "Super PAC", "Carey", "Conduit", "Company PAC", "Company PAC",
    "Trade Association", "Candidate", "Candidate", "Candidate", "Candidate"
  )
))

stopifnot(
  identical(spending_report_company_labels("BINANCE US"), "Binance"),
  identical(spending_report_company_labels("Foris Dax Inc"), "Crypto.com"),
  identical(spending_report_company_labels("Coinlist"), "CoinList"),
  identical(spending_report_company_labels("Tzero"), "tZero")
)

# Explicit FEC back-references can establish a memo parent. Identical amount,
# date and donor alone cannot. The linked memo may describe part of a parent.
# Cross-committee earmark legs must stay countable (R8 recipient-as-shown).
memo_rows <- data.frame(
  sub_id = c("PARENT", "MEMO_DRIFT", "MEMO_EXACT", "EARMARK_OTHER_CMTE"),
  company = "Gemini",
  committee_id = c("C00825851", "C00825851", "C00825851", "C00401224"),
  contribution_receipt_date = "2024-07-19",
  contribution_receipt_amount = c(176952.05, 176942.05, 176952.05, 176952.05),
  contributor_name = "WINKLEVOSS, TYLER",
  is_memo_leg = c(0L, 1L, 1L, 1L),
  transaction_id = c("SA11AI.7772", "SA17.7929.0", "SA17.7930.0", "SA17.7931.0"),
  file_num = 1234L, report_year = 2024L, report_type = "M8",
  filing_form = "F3X", line_number = c("11AI", "17", "17", "17"),
  receipt_type_full = c(
    "2.692 BITCOINS LIQUIDATED",
    "2.692 BITCOINS LIQUIDATED [SA11AI:7772]",
    "2.692 BITCOINS LIQUIDATED",
    "EARMARKED FOR SOMEONE ELSE [SA11AI:7772]"
  ),
  memo_text = "",
  stringsAsFactors = FALSE
)
memo_plan <- mentat_memo_duplicate_plan(memo_rows)
memo_flag <- setNames(memo_plan$duplicate_memo, memo_plan$sub_id)
stopifnot(
  memo_flag[["PARENT"]] == 0L,
  # caught by the back-reference despite the amount drift
  memo_flag[["MEMO_DRIFT"]] == 1L,
  # Matching all five financial fields is a review clue, not a relationship.
  memo_flag[["MEMO_EXACT"]] == 0L,
  # different committee: a real earmark leg, counts (R8)
  memo_flag[["EARMARK_OTHER_CMTE"]] == 0L
)

cat("test_rules: PASS\n")

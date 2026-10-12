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
  "paths.R", "schema.R", "ingest.R", "fec_page.R", "collect.R"
)) {
  source(file.path(project_root, "R", file), local = .GlobalEnv)
}

inputs <- c(
  "SMITH", "SMITH,", "", "ACTBLUE", "SMITH JR",
  "SMITH, JOHN A", "O'BRIEN, TIM"
)
expected <- list(
  list(base = "SMITH", first = "", last = "SMITH", suffix = ""),
  list(base = "SMITH", first = "", last = "SMITH", suffix = ""),
  list(base = "", first = "", last = "", suffix = ""),
  list(base = "ACTBLUE", first = "", last = "ACTBLUE", suffix = ""),
  list(base = "SMITH", first = "", last = "SMITH", suffix = "JR"),
  list(
    base = "SMITH, JOHN A", first = "JOHN", last = "SMITH", suffix = ""
  ),
  list(
    base = "O'BRIEN, TIM", first = "TIM", last = "O'BRIEN", suffix = ""
  )
)

for (index in seq_along(inputs)) {
  stopifnot(identical(person_name_parts(inputs[[index]]), expected[[index]]))
}

stopifnot(
  plausible_person_query("TUSAR, GREGORY"),
  plausible_person_query("O'BRIEN, TIM"),
  !plausible_person_query("C, DAVID"),
  !plausible_person_query("SMITH, J"),
  !plausible_person_query("COINBASE")
)

brown_seed <- list(
  contributor_name = "BROWN, MICHAEL",
  contributor_zip = "201904032",
  contributor_city = "Reston",
  contributor_state = "va"
)
brown_plans <- identity_query_plans(brown_seed, 15842L)
stopifnot(
  identical(
    vapply(brown_plans, function(plan) plan$leg, character(1L)),
    c("zip", "city")
  ),
  identical(brown_plans[[1L]]$value, "20190"),
  identical(
    brown_plans[[1L]]$query_filters,
    list(contributor_zip = "20190")
  ),
  identical(
    identity_query_plans(brown_seed, 100L)[[1L]]$leg,
    "broad"
  )
)

require_package("DBI")
require_package("RSQLite")
checkpoint_connection <- DBI::dbConnect(RSQLite::SQLite(), ":memory:")
DBI::dbExecute(
  checkpoint_connection,
  paste(
    "CREATE TABLE scrape_progress (company TEXT, cycle INTEGER,",
    "variant_set TEXT, last_page INTEGER, records_total INTEGER,",
    "updated_at TEXT)"
  )
)
DBI::dbExecute(
  checkpoint_connection,
  "INSERT INTO scrape_progress VALUES (?, ?, ?, ?, ?, ?)",
  params = list(
    "Coinbase", 2024L, "P2_name:complete",
    2L, 232L, "2026-07-18T23:00:00.000-0500"
  )
)
DBI::dbExecute(
  checkpoint_connection,
  "INSERT INTO scrape_progress VALUES (?, ?, ?, ?, ?, ?)",
  params = list(
    "Coinbase", 2024L, "P2_name:partial",
    1L, 1049000L, "2026-07-19T00:15:24.804-0500"
  )
)
stopifnot(
  !is.null(completed_scrape_checkpoint(
    checkpoint_connection,
    "Coinbase",
    2024L,
    "P2_name:complete",
    "2026-07-18T22:36:51"
  )),
  is.null(completed_scrape_checkpoint(
    checkpoint_connection,
    "Coinbase",
    2024L,
    "P2_name:complete",
    "2026-07-19T00:00:00"
  )),
  is.null(completed_scrape_checkpoint(
    checkpoint_connection,
    "Coinbase",
    2024L,
    "P2_name:partial",
    "2026-07-18T22:36:51"
  ))
)
DBI::dbDisconnect(checkpoint_connection)

scoped_resume_connection <- DBI::dbConnect(RSQLite::SQLite(), ":memory:")
init_schema(scoped_resume_connection)
DBI::dbExecute(
  scoped_resume_connection,
  paste(
    "INSERT INTO contributions (",
    "sub_id, contributor_name, contributor_city, contributor_state,",
    "contributor_zip, contributor_employer, contributor_occupation,",
    "two_year_transaction_period, industry, company, matched_variant,",
    "phase, scraped_at, company_match_status",
    ") VALUES ('resume-seed', 'BROWN, MICHAEL', 'RESTON', 'VA',",
    "'201904032', 'COINBASE', 'EXECUTIVE', 2024, 'Crypto',",
    "'Coinbase', 'Coinbase', 'P1_employer',",
    "'2026-07-19T00:00:00Z', 'accepted')"
  )
)
DBI::dbExecute(
  scoped_resume_connection,
  paste(
    "INSERT INTO scrape_progress",
    "(company, cycle, variant_set, last_page, records_total, updated_at)",
    "VALUES ('Coinbase', 2024,",
    "'P2_name_zip:resume-seed:20190', 0, 17,",
    "'2026-07-19T00:10:00.000-0500')"
  )
)
scoped_resume_result <- local({
  original_walk <- fec_walk_variant_set
  walked_sets <- character()
  on.exit(assign(
    "fec_walk_variant_set",
    original_walk,
    envir = .GlobalEnv
  ))
  assign(
    "fec_walk_variant_set",
    function(
      session,
      company,
      cycle,
      variants,
      variant_set,
      ...
    ) {
      walked_sets <<- c(walked_sets, variant_set)
      list(records_total = 9L, pages = 1L, retries = 0L)
    },
    envir = .GlobalEnv
  )
  result <- collect_contributor_name_pass(
    scoped_resume_connection,
    session = list(),
    industry = "Crypto",
    company = "Coinbase",
    cycle = 2024L,
    company_variants = "Coinbase",
    resume_after = "2026-07-19T00:00:00"
  )
  list(result = result, walked_sets = walked_sets)
})
stopifnot(
  identical(
    scoped_resume_result$walked_sets,
    "P2_name_city:resume-seed:RESTON"
  ),
  !any(grepl(
    "^P2_name:resume-seed$",
    scoped_resume_result$walked_sets
  )),
  identical(scoped_resume_result$result$executed, 1L)
)
DBI::dbDisconnect(scoped_resume_connection)

retag_connection <- DBI::dbConnect(RSQLite::SQLite(), ":memory:")
init_schema(retag_connection)
DBI::dbAppendTable(
  retag_connection,
  "companies",
  data.frame(
    industry = "Crypto",
    company = c("Gemini", "Coinbase"),
    parent = c("Gemini", "Coinbase"),
    variant = c("Gemini", "Coinbase"),
    variant_order = 1L,
    confidence = "normal",
    exclusions = NA_character_,
    metadata_json = "{}",
    source_sheet = "Crypto",
    source_row = 1:2
  )
)
DBI::dbExecute(
  retag_connection,
  paste(
    "INSERT INTO company_home_states (company, state, source)",
    "VALUES ('Gemini', 'NY', 'test_register')"
  )
)
DBI::dbExecute(
  retag_connection,
  paste(
    "INSERT INTO company_match_overrides",
    "(employer_key, company, state, occupation_pattern, decision) VALUES",
    "('GEMINI ENERGY SERVICES', 'Gemini', '', '', 'reject'),",
    "('GEMINI TRUST COMPANY LLC', 'Gemini', '', '', 'accept')"
  )
)
insert_retag_fixture <- function(
  sub_id,
  company,
  cycle,
  contributor_name,
  contributor_employer
) {
  DBI::dbExecute(
    retag_connection,
    paste(
      "INSERT INTO contributions (",
      "sub_id, contributor_name, contributor_employer,",
      "two_year_transaction_period, industry, company, matched_variant,",
      "phase, scraped_at, company_match_status, company_match_reason",
      ") VALUES (?, ?, ?, ?, 'Crypto', ?, ?, 'P1_employer',",
      "'2026-07-19T00:00:00Z', 'accepted', 'stale_legacy_accept')"
    ),
    params = list(
      sub_id,
      contributor_name,
      contributor_employer,
      as.integer(cycle),
      company,
      company
    )
  )
}
insert_retag_fixture(
  "gemini-energy-2022",
  "Gemini",
  2022L,
  "WORKER, WRONG",
  "GEMINI ENERGY SERVICES"
)
insert_retag_fixture(
  "gemini-trust-2022",
  "Gemini",
  2022L,
  "EMPLOYEE, ERIN",
  "GEMINI TRUST COMPANY, LLC"
)
insert_retag_fixture(
  "gemini-trust-duplicate-2022",
  "Gemini",
  2022L,
  "EMPLOYEE, ERIN",
  "GEMINI TRUST COMPANY, LLC"
)
insert_retag_fixture(
  "gemini-trust-unrelated-2022",
  "Gemini",
  2022L,
  "OTHER, OLIVIA",
  "GEMINI TRUST COMPANY, LLC"
)
insert_retag_fixture(
  "gemini-energy-2024",
  "Gemini",
  2024L,
  "WORKER, OTHER",
  "GEMINI ENERGY SERVICES"
)
insert_retag_fixture(
  "coinbase-unrelated-2022",
  "Coinbase",
  2022L,
  "WORKER, CONTROL",
  "UNRELATED EMPLOYER"
)
DBI::dbExecute(
  retag_connection,
  paste(
    "UPDATE contributions SET contributor_city = 'BERNARDSVILLE',",
    "contributor_state = 'NJ', contributor_zip = '079241106'",
    "WHERE sub_id IN ('gemini-trust-2022',",
    "'gemini-trust-duplicate-2022')"
  )
)
insert_identity_fixture <- function(
  sub_id,
  contributor_name,
  contributor_employer,
  status
) {
  DBI::dbExecute(
    retag_connection,
    paste(
      "INSERT INTO contributions (",
      "sub_id, contributor_name, contributor_city, contributor_state,",
      "contributor_zip, contributor_employer, contributor_occupation,",
      "two_year_transaction_period, industry, company, matched_variant,",
      "phase, scraped_at, company_match_status, company_match_reason",
      ") VALUES (?, ?, 'BERNARDSVILLE', 'NJ', '079241106', ?,",
      "'EXECUTIVE', 2022, 'Crypto', 'Gemini', ?, 'P2_name',",
      "'2026-07-19T00:00:00Z', ?, 'stale_identity_status')"
    ),
    params = list(
      sub_id,
      contributor_name,
      contributor_employer,
      contributor_name,
      status
    )
  )
}
insert_identity_fixture(
  "gemini-identity-restored",
  "EMPLOYEE, ERIN",
  "GREYWOLF ANALYTICS",
  "rejected"
)
insert_identity_fixture(
  "gemini-identity-rejected",
  "WORKER, WRONG",
  "GEMINI ENERGY SERVICES",
  "accepted"
)
retag_summary <- retag_employer_company_matches(
  retag_connection,
  company = "Gemini",
  cycle = 2022L
)
identity_retag_result <- local({
  original_ladder <- identity_match_ladder
  comparisons <- data.table::data.table(
    seed_name = character(),
    candidate_name = character()
  )
  on.exit(assign(
    "identity_match_ladder",
    original_ladder,
    envir = .GlobalEnv
  ))
  assign(
    "identity_match_ladder",
    function(seed, candidate, company_variants) {
      comparisons <<- data.table::rbindlist(list(
        comparisons,
        data.table::data.table(
          seed_name = seed$contributor_name,
          candidate_name = candidate$contributor_name
        )
      ))
      original_ladder(seed, candidate, company_variants)
    },
    envir = .GlobalEnv
  )
  list(
    summary = retag_identity_company_matches(
      retag_connection,
      company = "Gemini",
      cycle = 2022L
    ),
    comparisons = comparisons
  )
})
identity_retag_summary <- identity_retag_result$summary
retag_statuses <- DBI::dbGetQuery(
  retag_connection,
  paste(
    "SELECT sub_id, company_match_status, company_match_reason",
    "FROM contributions",
    "ORDER BY sub_id"
  )
)
accepted_gemini_2022 <- DBI::dbGetQuery(
  retag_connection,
  paste(
    "SELECT sub_id FROM contributions",
    "WHERE company = 'Gemini' AND two_year_transaction_period = 2022",
    "AND phase IN ('P1_employer','P2_variant')",
    "AND company_match_status = 'accepted'"
  )
)
stopifnot(
  sum(retag_summary$rows) == 4L,
  sum(identity_retag_summary$rows) == 2L,
  retag_statuses$company_match_status[
    retag_statuses$sub_id == "gemini-energy-2022"
  ] == "rejected",
  retag_statuses$company_match_status[
    retag_statuses$sub_id == "gemini-trust-2022"
  ] == "accepted",
  retag_statuses$company_match_status[
    retag_statuses$sub_id == "gemini-trust-duplicate-2022"
  ] == "accepted",
  retag_statuses$company_match_status[
    retag_statuses$sub_id == "gemini-trust-unrelated-2022"
  ] == "accepted",
  retag_statuses$company_match_status[
    retag_statuses$sub_id == "gemini-energy-2024"
  ] == "accepted",
  retag_statuses$company_match_status[
    retag_statuses$sub_id == "coinbase-unrelated-2022"
  ] == "accepted",
  retag_statuses$company_match_status[
    retag_statuses$sub_id == "gemini-identity-restored"
  ] == "accepted",
  retag_statuses$company_match_reason[
    retag_statuses$sub_id == "gemini-identity-restored"
  ] == "identity_cross_reference:exact_name_same_zip_cross_employer",
  retag_statuses$company_match_status[
    retag_statuses$sub_id == "gemini-identity-rejected"
  ] == "rejected",
  # This fixture's employer (GEMINI ENERGY SERVICES) is also covered by the
  # explicit override inserted above, and an explicit override outranks every
  # inferred path by design - so the reason is the override, not the identity
  # revocation. The rejection itself is what this row asserts.
  retag_statuses$company_match_reason[
    retag_statuses$sub_id == "gemini-identity-rejected"
  ] %in% c("persistent_override:reject", "identity_seed_no_longer_verified"),
  setequal(
    accepted_gemini_2022$sub_id,
    c(
      "gemini-trust-2022",
      "gemini-trust-duplicate-2022",
      "gemini-trust-unrelated-2022"
    )
  ),
  nrow(identity_retag_result$comparisons) == 1L,
  identity_retag_result$comparisons$seed_name[[1L]] == "EMPLOYEE, ERIN",
  identity_retag_result$comparisons$candidate_name[[1L]] == "EMPLOYEE, ERIN",
  !"OTHER, OLIVIA" %in% identity_retag_result$comparisons$seed_name,
  !"WORKER, WRONG" %in%
    identity_retag_result$comparisons$candidate_name
)
DBI::dbDisconnect(retag_connection)

gemini_variants <- "Gemini"
gemini_home_states <- data.frame(
  company = "Gemini", state = "NY", source = "test_register",
  stringsAsFactors = FALSE
)
transport <- company_employer_match(
  "Gemini",
  "Gemini Transportation",
  "DRIVER, DONNA",
  gemini_variants,
  home_states = gemini_home_states
)
official <- company_employer_match(
  "Gemini",
  "Gemini Trust Company, LLC",
  "EMPLOYEE, ERIN",
  gemini_variants
)
ambiguous <- company_employer_match(
  "Gemini",
  "Gemini, Inc.",
  "EMPLOYEE, ERIN",
  gemini_variants,
  contributor_state = "MN",
  contributor_occupation = "GRAPHIC DESIGN",
  home_states = gemini_home_states
)
trusted <- company_employer_match(
  "Gemini",
  "Gemini",
  "EMPLOYEE, ERIN",
  gemini_variants,
  trusted_name_keys = person_identity_key("EMPLOYEE, ERIN"),
  contributor_state = "NY",
  contributor_occupation = "PRODUCT MANAGER",
  home_states = gemini_home_states
)
stopifnot(
  identical(transport$status, "review"),
  identical(official$status, "accepted"),
  identical(official$reason, "verified_employer_alias"),
  identical(ambiguous$status, "rejected"),
  identical(trusted$status, "accepted"),
  identical(trusted$reason, "ambiguous_brand_home_state")
)

tusar_seed <- list(
  contributor_name = "TUSAR, GREGORY",
  contributor_zip = "079241106",
  contributor_city = "BERNARDSVILLE",
  contributor_state = "NJ",
  contributor_employer = "COINBASE, INC.",
  contributor_occupation = "VP, PRODUCT"
)
tusar_greywolf <- list(
  contributor_name = "TUSAR, GREG",
  contributor_zip = "079241106",
  contributor_city = "BERNARDSVILLE",
  contributor_state = "NJ",
  contributor_employer = "GREYWOLF ANALYTICS",
  contributor_occupation = "EXECUTIVE"
)
tusar_match <- identity_match_ladder(
  tusar_seed,
  tusar_greywolf,
  "Coinbase"
)
stopifnot(
  identical(tusar_match$level, "HIGH"),
  identical(tusar_match$reason, "nickname_same_zip_cross_employer")
)

harry_seed <- tusar_seed
harry_seed$contributor_name <- "PERSON, HAROLD"
harry_candidate <- tusar_greywolf
harry_candidate$contributor_name <- "PERSON, HARRY"
harry_match <- identity_match_ladder(harry_seed, harry_candidate, "Coinbase")
stopifnot(
  nickname_canonical("Harry") == "HARRY",
  identical(harry_match$level, "DISCARD"),
  identical(harry_match$reason, "below_identity_threshold")
)

corporate_queries <- company_contributor_queries(
  "Coinbase",
  c("COINBASE GLOBAL", "Coinbase")
)
stopifnot(
  identical(corporate_queries[[1L]], "Coinbase"),
  "Coinbase" %in% corporate_queries
)

cat("test_names: single-token, blank, suffix, and LAST, FIRST names verified\n")
cat("test_names: structurally invalid broad person queries rejected\n")
cat("test_names: broad identities split into ZIP5/city query legs\n")
cat("test_names: campaign-scoped completed checkpoints resume safely\n")
cat("test_names: scoped resume skips broad probe and runs missing leg\n")
cat("test_names: scoped resume retag rejects stale employer and identity seeds\n")
cat("test_names: strict employer and cross-employer identity checks verified\n")
cat("test_names: canonical company contributor query verified\n")
cat("test_names: PASS\n")

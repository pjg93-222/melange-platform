suppressPackageStartupMessages(library(data.table))
script_argument <- grep("^--file=", commandArgs(trailingOnly = FALSE), value = TRUE)
stopifnot(length(script_argument) == 1L)
project_root <- normalizePath(
  file.path(dirname(sub("^--file=", "", script_argument[[1L]])), ".."),
  winslash = "/",
  mustWork = TRUE
)
# Same read-only source list tests/test_spending_detail_frames.R uses, plus the
# module under test. ingest.R supplies trim_text, report.R the FEC code books
# and mentat_fec_code_label, fec_beneficiary.R the modal/min/max text helpers,
# export_guards.R the guard result shape. None of them touches the database or
# the network at load time.
for (file in c(
  "paths.R", "ingest.R", "report.R", "fec_beneficiary.R", "export_guards.R",
  "tidy_stage.R"
)) {
  source(file.path(project_root, "R", file), local = .GlobalEnv)
}
require_package("DBI")
require_package("RSQLite")

# The staging store is the ONLY path by which receipt-level FEC fields reach the
# companion Data workbook: it bypasses the JSON payload and therefore
# tools/prepare_workbook_payload.py's 18-field RAW_FIELDS projection. A column
# silently dropped or retyped here is not a degraded chart, it is a chart of a
# different number, so this file asserts the contract column by column rather
# than sampling.

# ---------------------------------------------------------------------------
# Fixture. Modelled on S\STAGING_CONTRACT.md section 4, extended so every rule
# with a branch has at least one row on each side of it:
#   * one excluded row carrying the LARGEST amount in the fixture, so anything
#     that leaks it into a total is unmissable;
#   * one entity (corporate) row with no person_key, which must stay in
#     giving_facts and must NOT appear in contributor_facts;
#   * three ZIP shapes: ZIP+4, plain 5-digit, and alphabetic (must be NULL);
#   * two spellings of one person in one party group, to pin the modal
#     tie-break;
#   * a committee with two sponsors (no single sponsor on the row) and one with
#     exactly one (sponsor columns populated).
# ---------------------------------------------------------------------------

raw_export <- data.table(
  source_id = c("1", "2", "3", "4", "5", "6", "7", "8"),
  transaction_id = c("SA11AI.1", "SA11AI.2", "SA11AI.3", "SB23.4", "SA11AI.5",
                     "SA11AI.6", "SA11AI.7", "SA11AI.8"),
  back_reference_transaction_id = rep("", 8L),
  cycle = c(2022L, 2022L, 2022L, 2022L, 2022L, 2024L, 2024L, 2024L),
  cycle_label = c(rep("2021-2022", 5L), rep("2023-2024", 3L)),
  source_cycle = c(2022L, 2022L, 2022L, 2022L, 2022L, 2024L, 2024L, 2024L),
  cycle_alignment = rep("OK", 8L),
  company = c(rep("Example Credit Union", 5L), rep("Other Credit Union", 3L)),
  contributor = c("DOE, ZARA", "DOE, ZARA", "DOE, ANNA", "EXAMPLE CU PAC",
                  "ROE, RITA", "FOE, FRED", "FOE, FRED", "GOE, GAIL"),
  person_key = c("P1", "P1", "P1", "", "P2", "P3", "P3", "P4"),
  identity_basis = c(rep("Exact name + employer", 3L), "", rep("Exact name + employer", 4L)),
  employee_tier = c("Employee", "Employee", "Employee", "Corporate", "Employee",
                    "Executive", "Executive", "Employee"),
  employer = c(rep("EXAMPLE CREDIT UNION", 5L), rep("OTHER CREDIT UNION", 3L)),
  occupation = c(rep("MEMBER SERVICE", 5L), "DIRECTOR", "DIRECTOR", "CLERK"),
  city = c("VIENNA", "VIENNA", "VIENNA", "VIENNA", "VIENNA", "BOSTON",
           "CAMBRIDGE", "BOSTON"),
  state = c(rep("VA", 5L), rep("MA", 3L)),
  zipcode = c("221804907", "22180", "22180", "22180", "22180", "02134", "02139",
              "ABCDE"),
  date = c("2021-06-28", "2021-07-01", "2022-01-15", "2022-03-01", "2022-09-09",
           "2023-05-05", "2024-02-02", "2024-06-06"),
  amount = c(25, 75, 10, 5000, 9999, 200, 50, 15),
  include_in_analysis = c("Yes", "Yes", "Yes", "Yes", "No", "Yes", "Yes", "Yes"),
  inclusion_reason = c(rep("Included", 4L), "Memo duplicate: earmarked leg",
                       rep("Included", 3L)),
  company_match_status = rep("accepted", 8L),
  company_match_reason = rep("verified_roster_alias", 8L),
  matched_alias = rep("EXAMPLE CREDIT UNION", 8L),
  recipient = c("CONDUIT ONE", "LEADERSHIP TWO", "CONDUIT ONE", "COMPANY PAC",
                "CONDUIT ONE", "LEADERSHIP TWO", "LEADERSHIP TWO", "OPEN FOUR"),
  committee_id = c("C00000001", "C00000002", "C00000001", "C00000003",
                   "C00000001", "C00000002", "C00000002", "C00000004"),
  recipient_key = paste0("FEC:", c("C00000001", "C00000002", "C00000001",
                                   "C00000003", "C00000001", "C00000002",
                                   "C00000002", "C00000004")),
  recipient_category = c("Candidate", "Leadership PAC", "Candidate",
                         "Company PAC", "Candidate", "Leadership PAC",
                         "Leadership PAC", "Candidate"),
  category_source = rep("master", 8L),
  party = c("Democratic", "Republican", "Democratic", "Other / Unclassified",
            "Democratic", "Republican", "Republican", "Democratic"),
  party_source = rep("fec_committee_master:cm22", 8L),
  committee_type = c("O", "N", "O", "Q", "O", "N", "N", "ZZ"),
  committee_designation = c("U", "D", "U", "B", "U", "D", "D", ""),
  record_type = rep("contribution", 8L),
  source_table = rep("contributions", 8L),
  funding_source_type = rep("individual", 8L),
  funding_direction = c(rep("To named recipient", 3L), "Into company PAC",
                        rep("To named recipient", 4L)),
  receipt_role = rep("original", 8L),
  receipt_type_full = rep("Earmarked contribution", 8L),
  filing_form = rep("F3X", 8L),
  line_number = rep("11AI", 8L),
  memo_text = rep("", 8L),
  is_memo_leg = c(0L, 0L, 0L, 0L, 1L, 0L, 0L, 0L),
  is_conduit_leg = rep(0L, 8L),
  superseded_by_amendment = rep(0L, 8L),
  duplicate_filing_version = rep(0L, 8L),
  duplicate_memo = c(0L, 0L, 0L, 0L, 1L, 0L, 0L, 0L),
  duplicate_of_sub_id = c(rep("", 4L), "1", rep("", 3L)),
  duplicate_reason = c(rep("", 4L), "memo_leg", rep("", 3L)),
  recipient_state = c(rep("MA", 5L), rep("NY", 3L)),
  represented_state = c(rep("XX", 5L), rep("Unknown", 3L)),
  source_url = rep("https://docquery.fec.gov/cgi-bin/fecimg/?0", 8L)
)

committee_map <- data.table(
  cycle = c(2022L, 2022L, 2022L, 2024L, 2024L),
  committee_id = c("C00000001", "C00000002", "C00000003", "C00000002",
                   "C00000004"),
  master_committee_name = c("CONDUIT ONE PAC", "LEADERSHIP TWO PAC",
                            "EXAMPLE CU FEDERAL PAC", "LEADERSHIP TWO PAC",
                            "OPEN FOUR COMMITTEE"),
  connected_organization = c("", "", "EXAMPLE CREDIT UNION", "", ""),
  ownership_status = c("Organization/committee; no individual ownership link in FEC files",
                       "Candidate/officeholder-sponsored PAC",
                       "Organization/committee; no individual ownership link in FEC files",
                       "Candidate/officeholder-sponsored PAC",
                       "Candidate-linked committee"),
  relationship = c("No direct candidate/officeholder link",
                   "Leadership PAC sponsor; Principal campaign committee",
                   "No direct candidate/officeholder link",
                   "Leadership PAC sponsor",
                   "Principal campaign committee"),
  mapping_source = c("", "https://a.example/ccl22.zip; https://a.example/leadership2022.csv",
                     "", "https://a.example/leadership2024.csv",
                     "https://a.example/ccl24.zip"),
  top_downstream_candidate_id = c("", "", "P0ZZ00004", "", ""),
  top_downstream_person = c("", "", "VOE, VERA", "", ""),
  preliminary_top_downstream_amount = c(0, 0, 15800, 0, 0)
)

fec_committee_master <- data.table(
  cycle = c(2022L, 2022L, 2024L),
  committee_id = c("C00000001", "C00000002", "C00000002"),
  state = c("MA", "NY", "NY"),
  party_affiliation = c("DEM", "REP", "REP")
)

candidate_links <- data.table(
  cycle = c(2022L, 2024L, 2022L),
  candidate_id = c("H0XX00001", "H0XX00003", "H0XX00009"),
  committee_id = c("C00000002", "C00000004", "C00009999"),
  committee_type = c("N", "H", "H"),
  designation = c("P", "P", "P"),
  relationship = rep("Principal campaign committee", 3L),
  source_url = c("https://a.example/ccl22.zip", "https://a.example/ccl24.zip",
                 "https://a.example/ccl22.zip")
)

# The leadership-PAC sponsor rows only exist in fec_build_committee_map's
# internal `mappings` frame; P3 returns it so they can be carried here.
mappings <- data.table(
  cycle = c(2022L, 2022L),
  committee_id = c("C00000002", "C00000002"),
  candidate_id = c("H0XX00001", "S0YY00002"),
  candidate_name = c("ROE, RICHARD", "POE, PAULA"),
  relationship = c("Principal campaign committee", "Leadership PAC sponsor"),
  mapping_source = c("https://a.example/ccl22.zip",
                     "https://a.example/leadership2022.csv")
)

candidate_master <- data.table(
  cycle = c(2022L, 2022L, 2024L, 2022L, 2022L),
  candidate_id = c("H0XX00001", "S0YY00002", "H0XX00003", "P0ZZ00004",
                   "H0XX00009"),
  candidate_name = c("ROE, RICHARD", "POE, PAULA", "NOE, NINA", "VOE, VERA",
                     "UNREFERENCED, ULA"),
  candidate_party = c("Democratic", "Republican", "Democratic", "Republican",
                      "Democratic"),
  party_affiliation = c("DEM", "REP", "DEM", "REP", "DEM"),
  office = c("H", "S", "H", "P", "H"),
  office_label = c("House", "Senate", "House", "President", "House"),
  office_state = c("XX", "YY", "ZZ", "US", "XX"),
  district = c("01", "", "07", "", "02"),
  incumbent_challenger = c("I", "C", "O", "X", "I"),
  principal_committee_id = c("C00000002", "C00000005", "C00000004",
                             "C00000006", "C00009999"),
  source_url = rep("https://a.example/cn.zip", 5L)
)

# Two rows for ONE candidate_id under two filed name spellings. This is the
# shape that collided in 92 groups of the real Credit Union export:
# fec_aggregate_downstream_activity groups by candidate_name as well as
# candidate_id, so these are two legitimate rows and the key must include
# `candidate` (contract amendment D2, 2026-09-15) or the guard refuses a
# correct export.
downstream_activity_export <- data.table(
  cycle = c(2022L, 2022L),
  source_committee = rep("EXAMPLE CU FEDERAL PAC", 2L),
  source_committee_id = rep("C00000003", 2L),
  source_recipient_category = rep("Company PAC", 2L),
  candidate_name = c("VOE, VERA", "VOE, VERA M"),
  candidate_id = rep("P0ZZ00004", 2L),
  candidate_party = rep("Republican", 2L),
  office = rep("P", 2L),
  office_label = rep("President", 2L),
  office_state = rep("US", 2L),
  district = rep("", 2L),
  activity_type = rep("Independent expenditure", 2L),
  support_oppose = rep("Oppose", 2L),
  transaction_type = rep("24E", 2L),
  first_activity_date = c("2022-04-01", "2022-05-01"),
  last_activity_date = c("2022-10-01", "2022-10-15"),
  source_rows = c(4L, 2L),
  included_rows = c(3L, 2L),
  excluded_rows = c(1L, 0L),
  superseded_rows = c(0L, 0L),
  memo_rows = c(0L, 0L),
  exact_duplicate_rows = c(1L, 0L),
  unresolved_version_rows = c(0L, 0L),
  unverified_version_rows = c(1L, 0L),
  reported_amount = c(20000, 3000),
  excluded_amount = c(4200, 0),
  preliminary_included_amount = c(15800, 3000),
  transfer_records = rep("[]", 2L),
  filing_records = rep('[{"date":"2022-04-01","amount":100,"source_url":"https://docquery.fec.gov/cgi-bin/fecimg/?1"}]', 2L),
  source_url = rep("https://a.example/pas222.zip", 2L),
  candidate_source_url = rep("https://a.example/cn.zip", 2L)
)

bridge <- data.table(
  cycle = c(2022L, 2022L),
  view = c("Company", "Industry"),
  company = c("Example Credit Union", "Industry Total"),
  source_committee = c("EXAMPLE CU FEDERAL PAC", "EXAMPLE CU FEDERAL PAC"),
  source_committee_id = c("C00000003", "C00000003"),
  source_recipient_category = c("Company PAC", "Company PAC"),
  candidate_name = c("VOE, VERA", "VOE, VERA"),
  candidate_id = c("P0ZZ00004", "P0ZZ00004"),
  candidate_party = c("Republican", "Republican"),
  office = c("P", "P"),
  office_label = c("President", "President"),
  office_state = c("US", "US"),
  district = c("", ""),
  transaction_count = c(3L, 3L),
  first_activity_date = c("2022-04-01", "2022-04-01"),
  last_activity_date = c("2022-10-01", "2022-10-01"),
  preliminary_downstream_amount = c(15800, 15800),
  preliminary_crypto_funding = c(5000, 5000),
  activity_relationships = c("Independent expenditure; Transfer",
                             "Independent expenditure; Transfer"),
  directions = c("Oppose; Support", "Oppose; Support"),
  bridge_key = c("2022|Company|Example Credit Union|C00000003|CAND:P0ZZ00004",
                 "2022|Industry|Industry Total|C00000003|CAND:P0ZZ00004")
)

state_destinations <- data.table(
  cycle = c(2022L, 2024L),
  cycle_label = c("2021-2022", "2023-2024"),
  company = c("Example Credit Union", "Other Credit Union"),
  party = c("Democratic", "Republican"),
  destination = c("MA", "NY"),
  location_view = c("recipient_state", "recipient_state"),
  accounting_basis = c("Included receipts", "Included receipts"),
  net_amount = c(5110, 265),
  receipt_count = c(4L, 3L)
)

recipient_locations <- data.table(
  cycle = c(2022L, 2024L),
  committee_id = c("C00000001", "C00000002"),
  recipient = c("CONDUIT ONE", "LEADERSHIP TWO"),
  recipient_state = c("MA", "NY"),
  represented_state = c("XX", "Unknown"),
  recipient_location_status = c("known", "known"),
  represented_location_status = c("known", "unknown"),
  recipient_location_evidence = c("https://a.example/cm22.zip; https://a.example/cm24.zip", "x"),
  represented_location_evidence = c("https://a.example/cn.zip", "y")
)

guard_results <- data.frame(
  id = c("roster_coverage", "category_agreement"),
  severity = c("warn", "fail"),
  passed = c(TRUE, TRUE),
  detail = c("roster is fully covered", "category views agree in 2 cycle(s)"),
  metric = c(NA_real_, 2),
  stringsAsFactors = FALSE
)

expected_cycle_totals <- list("2021-2022" = 5110, "2023-2024" = 265)

frames <- mentat_build_tidy_frames(
  raw_export = raw_export,
  committee_map = committee_map,
  committee_master = fec_committee_master,
  candidate_links = candidate_links,
  candidate_master = candidate_master,
  downstream_activity_export = downstream_activity_export,
  bridge = bridge,
  state_destinations = state_destinations,
  recipient_locations = recipient_locations,
  guard_results = guard_results,
  mappings = mappings
)

failures <- character()
check <- function(label, condition) {
  ok <- isTRUE(condition)
  if (!ok) failures <<- c(failures, label)
  cat(sprintf("  [%s] %s\n", if (ok) "PASS" else "FAIL", label))
  invisible(ok)
}

cat("\nTIDY STAGE CONTRACT\n")

# --- contributions ---------------------------------------------------------
contributions <- frames$contributions
check("contributions carries every receipt, included and excluded",
      nrow(contributions) == 8L)
check("contributions columns match the contract, in order",
      identical(names(contributions), names(MENTAT_TIDY_TABLE_SPECS$contributions)))
check("ZIP+4 truncates to five digits",
      identical(contributions[source_id == "1", zip5], "22180"))
check("an alphabetic ZIP is NULL, never a truncated guess",
      is.na(contributions[source_id == "8", zip5]))
check("blank text becomes NA, never an empty-string category",
      is.na(contributions[source_id == "1", memo_text]) &&
        is.na(contributions[source_id == "4", person_key]))
check("flags are integer 0/1, never Yes/No",
      is.integer(contributions$include_in_analysis) &&
        identical(sort(unique(contributions$include_in_analysis)), c(0L, 1L)))
check("analysis_amount is the amount when included and 0 when not",
      contributions[source_id == "2", analysis_amount] == 75 &&
        contributions[source_id == "5", analysis_amount] == 0 &&
        contributions[source_id == "5", amount] == 9999)
check("committee type and designation carry BOTH the code and its label",
      contributions[source_id == "1", committee_type_code] == "O" &&
        contributions[source_id == "1", committee_type] ==
          "Independent expenditure-only (Super PAC)" &&
        contributions[source_id == "2", committee_designation] == "Leadership PAC")
check("an unknown committee-type code resolves to NULL, never a neighbouring label",
      is.na(contributions[source_id == "8", committee_type]))
check("dates stay ISO text",
      is.character(contributions$date) &&
        all(grepl("^[0-9]{4}-[0-9]{2}-[0-9]{2}$", contributions$date)))

# --- fact tables -----------------------------------------------------------
giving <- frames$giving_facts
check("giving_facts reconciles to the cycle totals",
      isTRUE(all.equal(
        giving[, sum(giving), by = cycle_label][order(cycle_label)]$V1,
        c(5110, 265)
      )))
check("giving_facts keeps the corporate row under its tier",
      giving[employee_tier == "Corporate", sum(giving)] == 5000)
check("distinct_contributors ignores entity rows with no person",
      giving[employee_tier == "Corporate", distinct_contributors] == 0L)

contributor <- frames$contributor_facts
check("contributor_facts excludes entity rows",
      nrow(contributor[is.na(person_key)]) == 0L &&
        isTRUE(all.equal(
          contributor[, sum(giving), by = cycle_label][order(cycle_label)]$V1,
          c(110, 265)
        )))
check("modal contributor spelling breaks ties alphabetically",
      contributor[person_key == "P1" & party == "Democratic", contributor] ==
        "DOE, ANNA")
check("distinct_addresses counts the person's cycle, across parties",
      contributor[person_key == "P1" & party == "Democratic", distinct_addresses] == 1L &&
        contributor[person_key == "P3", distinct_addresses][[1L]] == 2L)

recipient <- frames$recipient_facts
check("recipient_facts reconciles to the cycle totals",
      isTRUE(all.equal(
        recipient[, sum(giving), by = cycle_label][order(cycle_label)]$V1,
        c(5110, 265)
      )))
check("recipient_facts carries first and last gift dates",
      recipient[committee_id == "C00000001" & cycle_label == "2021-2022",
                first_gift_date] == "2021-06-28" &&
        recipient[committee_id == "C00000001" & cycle_label == "2021-2022",
                  last_gift_date] == "2022-01-15")
check("a committee with two sponsors carries no single sponsor on the row",
      recipient[committee_id == "C00000002" & cycle_label == "2021-2022",
                sponsor_count] == 2L &&
        is.na(recipient[committee_id == "C00000002" & cycle_label == "2021-2022",
                        sponsor_candidate_id]))
check("a committee with exactly one sponsor carries the sponsor columns",
      recipient[committee_id == "C00000004", sponsor_candidate_id] == "H0XX00003" &&
        recipient[committee_id == "C00000004", sponsor_candidate] == "NOE, NINA" &&
        recipient[committee_id == "C00000004", sponsor_district] == "07")

location <- frames$location_facts
check("location_facts reconciles to the cycle totals",
      isTRUE(all.equal(
        location[, sum(giving), by = cycle_label][order(cycle_label)]$V1,
        c(5110, 265)
      )))

# --- Conduit ---------------------------------------------------------------
# ActBlue and WinRed receipts carry the category Conduit (Paul, 2026-10-09). The
# store treats a category as free text, so it must carry the label through every
# fact table, give it its own rows, and leave each dollar in exactly one
# category: the cycle totals do not move when a category is relabelled.
conduit_export <- data.table::copy(raw_export)
conduit_export[source_id %in% c("1", "3", "5"), `:=`(
  recipient_category = "Conduit",
  category_source = "fec:registered_conduit:earmark_platform"
)]
conduit_frames <- mentat_build_tidy_frames(
  raw_export = conduit_export,
  committee_map = committee_map,
  committee_master = fec_committee_master,
  candidate_links = candidate_links,
  candidate_master = candidate_master,
  downstream_activity_export = downstream_activity_export,
  bridge = bridge,
  state_destinations = state_destinations,
  recipient_locations = recipient_locations,
  guard_results = guard_results,
  mappings = mappings
)
conduit_giving <- conduit_frames$giving_facts
check("a Conduit receipt keeps its category and source in contributions",
      conduit_frames$contributions[source_id == "1", recipient_category] ==
        "Conduit" &&
        conduit_frames$contributions[source_id == "1", category_source] ==
          "fec:registered_conduit:earmark_platform")
check("giving_facts gives Conduit its own rows, counting included receipts only",
      conduit_giving[recipient_category == "Conduit" &
                       cycle_label == "2021-2022", sum(giving)] == 35)
check("relabelling a category moves no dollars between cycles",
      isTRUE(all.equal(
        conduit_giving[, sum(giving), by = cycle_label][order(cycle_label)]$V1,
        c(5110, 265)
      )))
check("every dollar stays in exactly one category",
      isTRUE(all.equal(
        sum(conduit_giving[recipient_category == "Conduit", giving]) +
          sum(conduit_giving[recipient_category != "Conduit", giving]),
        sum(giving$giving)
      )) &&
        !anyDuplicated(conduit_giving[, .(
          cycle_label, company, employee_tier, recipient_category, party
        )]))
check("recipient_facts and committees carry Conduit for the committee",
      conduit_frames$recipient_facts[committee_id == "C00000001",
                                     unique(recipient_category)] == "Conduit" &&
        conduit_frames$committees[committee_id == "C00000001",
                                  recipient_category] == "Conduit")

# --- dimensions ------------------------------------------------------------
committees <- frames$committees
check("committees covers every committee that appears in contributions",
      identical(sort(unique(committees$committee_id)),
                c("C00000001", "C00000002", "C00000003", "C00000004")))
check("a `;`-joined relationship becomes Multiple, never the joined text",
      committees[committee_id == "C00000002" & cycle_label == "2021-2022",
                 committee_relationship] == "Multiple" &&
        committees[committee_id == "C00000002" & cycle_label == "2021-2022",
                   mapping_source] == "Multiple")
check("a single-valued relationship is kept verbatim",
      committees[committee_id == "C00000004", committee_relationship] ==
        "Principal campaign committee")
check("committees take state and party affiliation from the FEC master",
      committees[committee_id == "C00000001", committee_state] == "MA" &&
        committees[committee_id == "C00000001", party_affiliation] == "DEM")
check("sponsor_count is zero, not NULL, for an unsponsored committee",
      committees[committee_id == "C00000001", sponsor_count] == 0L)

sponsors <- frames$committee_sponsors
check("committee_sponsors is filtered to the recipient universe",
      !"C00009999" %in% sponsors$committee_id)
check("both link sources are represented with their own row",
      identical(sort(unique(sponsors$link_source)),
                c("FEC ccl", "Leadership PAC roster")) &&
        nrow(sponsors[committee_id == "C00000002" & cycle_label == "2021-2022"]) == 2L)
check("sponsor rows carry the candidate name",
      sponsors[candidate_id == "S0YY00002", candidate] == "POE, PAULA")

candidates <- frames$candidates
check("candidates is filtered to referenced ids only",
      !"H0XX00009" %in% candidates$candidate_id &&
        all(c("H0XX00001", "S0YY00002", "H0XX00003", "P0ZZ00004") %in%
              candidates$candidate_id))
check("incumbent codes decode to words",
      candidates[candidate_id == "H0XX00001", incumbent_status] == "Incumbent" &&
        candidates[candidate_id == "S0YY00002", incumbent_status] == "Challenger" &&
        candidates[candidate_id == "H0XX00003", incumbent_status] == "Open seat")
check("an unknown incumbent code stays NULL",
      is.na(candidates[candidate_id == "P0ZZ00004", incumbent_status]))

bridge_rows <- frames$beneficiary_bridge
check("beneficiary_bridge contains company associations; canonical industry facts are separate",
      nrow(bridge_rows) == 1L && bridge_rows$company == "Example Credit Union")
check("set-valued activity columns become 0/1 flags, not `;`-joined text",
      all(c("has_independent_expenditure", "has_transfer", "has_oppose",
            "has_support") %in% names(bridge_rows)) &&
        bridge_rows$has_oppose == 1L && bridge_rows$has_support == 1L &&
        !any(grepl(";", unlist(lapply(bridge_rows, as.character)), fixed = TRUE)))

downstream <- frames$downstream_activity
check("a row counted on the filing-number convention says so in its own column",
      downstream[candidate == "VOE, VERA", unverified_version_rows] == 1L &&
        downstream[candidate == "VOE, VERA M", unverified_version_rows] == 0L)
check("downstream activity keeps its JSON evidence as one text cell",
      all(downstream$transfer_records == "[]") &&
        all(grepl("docquery.fec.gov", downstream$filing_records, fixed = TRUE)) &&
        downstream[candidate == "VOE, VERA", net_included_amount] == 15800 &&
        downstream[candidate == "VOE, VERA", duplicate_rows] == 1L)
check("one candidate_id under two filed name spellings stays TWO rows",
      nrow(downstream) == 2L &&
        data.table::uniqueN(downstream$candidate_id) == 1L &&
        identical(sort(downstream$candidate), c("VOE, VERA", "VOE, VERA M")))
check("the downstream key carries `candidate` (amendment D2)",
      identical(MENTAT_TIDY_TABLE_KEYS$downstream_activity,
                c("cycle_label", "source_committee_id", "candidate_id",
                  "candidate", "activity_type", "support_oppose",
                  "transaction_type")))

locations <- frames$recipient_locations
check("recipient_locations drops the `;`-joined evidence columns",
      !any(grepl("evidence", names(locations))))

quality <- frames$data_quality
check("data_quality records the producer of every check",
      all(quality$source == "r_guard") && nrow(quality) == 2L)

# --- guard -----------------------------------------------------------------
passing <- mentat_guard_tidy_reconciliation(frames, expected_cycle_totals)
check("the reconciliation guard passes on a consistent store",
      isTRUE(passing$passed) && passing$id == "tidy_reconciliation" &&
        passing$severity == "fail")

corrupted <- frames
corrupted$giving_facts <- data.table::copy(frames$giving_facts)
corrupted$giving_facts[1L, giving := giving + 0.5]
broken <- mentat_guard_tidy_reconciliation(corrupted, expected_cycle_totals)
check("a corrupted fact table FAILS the guard",
      !isTRUE(broken$passed) && grepl("giving_facts", broken$detail))

duplicated_keys <- frames
duplicated_keys$contributions <- rbind(frames$contributions,
                                       frames$contributions[1L])
duplicated_guard <- mentat_guard_tidy_reconciliation(duplicated_keys,
                                                     expected_cycle_totals)
check("a repeated row key FAILS the guard",
      !isTRUE(duplicated_guard$passed) &&
        grepl("duplicate key", duplicated_guard$detail))

# Amendment D2, both directions. The guard must PASS on the two name-variant
# rows above (it does, in the check further up), and the pre-amendment key must
# be shown to have failed them - otherwise this fixture proves nothing about
# why the key changed.
pre_amendment <- nrow(frames$downstream_activity) - nrow(unique(
  frames$downstream_activity,
  by = c("cycle_label", "source_committee_id", "candidate_id", "activity_type",
         "support_oppose", "transaction_type")
))
check("the pre-amendment six-column key WOULD have reported a false duplicate",
      pre_amendment == 1L)

# A key column the frame does not carry must be reported, not quietly skipped:
# checking a shorter key is a weaker guard that still says PASS.
truncated <- frames
truncated$downstream_activity <- data.table::copy(frames$downstream_activity)
truncated$downstream_activity[, candidate := NULL]
truncated_guard <- mentat_guard_tidy_reconciliation(truncated, expected_cycle_totals)
check("a missing key column FAILS the guard rather than weakening it",
      !isTRUE(truncated_guard$passed) &&
        grepl("missing key column", truncated_guard$detail))

# --- store -----------------------------------------------------------------
metadata <- list(
  schema_version = mentat_tidy_schema_version,
  industry = "Example Credit Union Industry",
  industry_slug = "example",
  cycle_label = NA_character_,
  supported_cycles = c(2022L, 2024L),
  cycle_labels = c("2021-2022", "2023-2024"),
  database_path = "C:/example/mentat.db",
  database_sha256 = NA_character_,
  generated_at = "2026-09-14T00:00:00Z",
  exporter_version = "test",
  expected_cycle_totals = expected_cycle_totals,
  accounting_note = "Analysis Amount counts included itemized receipts only."
)

store <- file.path(tempdir(), "tidy-test.sqlite")
if (file.exists(store)) unlink(store, force = TRUE)
mentat_write_tidy_stage(store, frames, metadata)
check("the store is installed under its final name, with no pending leftovers",
      file.exists(store) &&
        !length(list.files(dirname(store), pattern = "tidy-test\\.sqlite\\.pending")))

connection <- DBI::dbConnect(RSQLite::SQLite(), store)
on.exit(DBI::dbDisconnect(connection), add = TRUE)
tables <- sort(DBI::dbListTables(connection))
check("every contract table is present",
      identical(tables, sort(c(
        "metadata", "contributions", "giving_facts", "recipient_facts",
        "contributor_facts", "location_facts", "committees",
        "committee_sponsors", "candidates", "downstream_activity",
        "beneficiary_bridge", "beneficiary_activity", "state_destinations", "recipient_locations",
        "data_quality"
      ))))

declared <- DBI::dbGetQuery(connection, "PRAGMA table_info(contributions)")
wanted <- MENTAT_TIDY_TABLE_SPECS$contributions
check("stored column names and order match the contract",
      identical(declared$name, unname(names(wanted))))
check("stored column affinities match the contract",
      identical(declared$type, unname(wanted)))

stored <- DBI::dbGetQuery(
  connection,
  "SELECT source_id, date, amount, analysis_amount, include_in_analysis, zip5, memo_text
     FROM contributions ORDER BY source_id"
)
check("dates survive the round trip as ISO TEXT, never as an R Date's day number",
      is.character(stored$date) && stored$date[[1L]] == "2021-06-28")
check("money survives as REAL dollars",
      is.numeric(stored$amount) && stored$amount[[5L]] == 9999)
check("blanks are SQL NULL in the store",
      is.na(stored$memo_text[[1L]]) && is.na(stored$zip5[[8L]]))
check("the metadata table renders lists as JSON",
      DBI::dbGetQuery(connection,
                      "SELECT value FROM metadata WHERE key='expected_cycle_totals'"
      )$value[[1L]] == "{\"2021-2022\":5110,\"2023-2024\":265}")
check("a missing metadata value is NULL, not the string NA",
      is.na(DBI::dbGetQuery(connection,
                            "SELECT value FROM metadata WHERE key='database_sha256'"
      )$value[[1L]]))

flag_columns <- DBI::dbGetQuery(connection, "PRAGMA table_info(beneficiary_bridge)")
check("the derived flag columns are stored as INTEGER",
      all(flag_columns$type[grepl("^has_", flag_columns$name)] == "INTEGER"))

empty_check <- DBI::dbGetQuery(connection, "PRAGMA table_info(data_quality)")
check("data_quality metric is TEXT, so three producers' metrics are never summed",
      empty_check$type[empty_check$name == "metric"] == "TEXT")

DBI::dbDisconnect(connection)

# Determinism: the same frames must produce byte-identical stores. A staging
# store that changes on every run would invalidate the orchestrator's
# stage_sha256 and force a rebuild of an unchanged workbook on every build.
second <- file.path(tempdir(), "tidy-test-2.sqlite")
if (file.exists(second)) unlink(second, force = TRUE)
mentat_write_tidy_stage(second, frames, metadata)
check("two writes of the same frames produce identical stores",
      identical(tools::md5sum(store)[[1L]], tools::md5sum(second)[[1L]]))
unlink(c(store, second), force = TRUE)

if (length(failures)) {
  stop(sprintf("test_tidy_stage: %d check(s) failed:\n  - %s",
               length(failures), paste(failures, collapse = "\n  - ")),
       call. = FALSE)
}
cat("test_tidy_stage: PASS\n")

suppressPackageStartupMessages(library(data.table))
script_argument <- grep("^--file=", commandArgs(trailingOnly = FALSE), value = TRUE)
stopifnot(length(script_argument) == 1L)
project_root <- normalizePath(
  file.path(dirname(sub("^--file=", "", script_argument[[1L]])), ".."),
  winslash = "/",
  mustWork = TRUE
)
for (file in c(
  "paths.R", "ingest.R", "report.R", "fec_beneficiary.R", "export_guards.R"
)) {
  source(file.path(project_root, "R", file), local = .GlobalEnv)
}

# FEC code books. An unknown or blank code must resolve to "" and never to a
# neighbouring label: a mislabelled committee reads as classified.
stopifnot(
  mentat_fec_code_label(
    c("O", "o", "Q", "ZZ", "", NA), MENTAT_FEC_COMMITTEE_TYPE_LABELS
  ) == c(
    "Independent expenditure-only (Super PAC)",
    "Independent expenditure-only (Super PAC)",
    "PAC - qualified", "", "", ""
  ),
  mentat_fec_code_label(
    c("A", "D", "X", ""), MENTAT_FEC_COMMITTEE_DESIGNATION_LABELS
  ) == c("Authorized by a candidate", "Leadership PAC", "", ""),
  length(MENTAT_FEC_COMMITTEE_TYPE_LABELS) == 16L,
  length(MENTAT_FEC_COMMITTEE_DESIGNATION_LABELS) == 6L
)

# Three (cycle, company) groups, ten recipient committees, nine contributor
# cells (S4: street_1/street_2 joined the contributor grain, so the Boston
# pair below - same city/zip/employer/occupation but different street_1 -
# now lands in two cells instead of one; the Austin pair keeps sharing a
# street, including a blank street_2, so it still folds into one). The last
# row is excluded from analysis and carries the largest amount in the
# fixture, so anything that leaks it into a total is unmissable.
fixture <- data.table(
  cycle = c(rep(2024L, 7L), rep(2022L, 3L), 2024L),
  cycle_label = c(rep("2023-2024", 7L), rep("2021-2022", 3L), "2023-2024"),
  company = c("Alpha", "Alpha", "Alpha", "Alpha", "Alpha", "Beta", "Beta",
              "Alpha", "Alpha", "Alpha", "Alpha"),
  committee_id = c("C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8", "C9", "C10",
                   "C1"),
  recipient = c("SUPER PAC ONE", "PAC TWO", "PAC THREE", "PAC FOUR",
                "PAC FIVE", "PAC SIX", "PAC SEVEN", "PAC EIGHT", "PAC NINE",
                "PAC TEN", "SUPER PAC ONE"),
  committee_type = c("O", "N", "N", "N", "N", "Q", "N", "ZZ", "N", "N", "O"),
  committee_designation = c("U", "U", "U", "U", "U", "B", "U", "", "U", "U",
                            "U"),
  recipient_category = c("Super PAC", rep("Other PAC", 6L), "Unclassified",
                         "Other PAC", "Other PAC", "Super PAC"),
  party = c(rep("D", 5L), "R", "R", "", "D", "D", "D"),
  state = c("CA", "CA", "NY", "TX", "TX", "WA", "WA", "CA", "MA", "MA", "CA"),
  city = c("San Francisco", "Oakland", "New York", "Austin", "Austin",
           "Seattle", "Seattle", "San Francisco", "Boston", "Boston",
           "San Francisco"),
  zipcode = c("94105", "94607-1234", "10001", "", "ABCDE", "98101", "2000",
              "94105", "02134", "02134", "94105"),
  employer = c(rep("Alpha", 5L), "Beta", "Beta", rep("Alpha", 4L)),
  occupation = c("Engineer", "Analyst", "Analyst", "Manager", "Manager",
                 "Director", "Director", "Engineer", "Clerk", "Clerk",
                 "Engineer"),
  street_1 = c("1 Market St", "200 Broadway", "350 5th Ave",
               "100 Congress Ave", "100 Congress Ave", "400 Pine St",
               "400 Pine St", "1 Market St", "10 Beacon St", "20 Beacon St",
               "1 Market St"),
  street_2 = c("Suite 100", "", "Fl 20", "", "", "", "Apt 2", "Suite 100",
               "", "", "Suite 100"),
  amount = c(1000, 50, 40, 30, 20, 800, 10, 900, 5, 5, 9999),
  person_key = c("p1", "p2", "p3", "shared", "shared", "p4", "p5", "p6", "p7",
                 "p7", "pX"),
  include_in_analysis = c(rep("Yes", 10L), "No"),
  date = c("2024-03-01", "2024-04-01", "2024-05-01", "2024-06-01",
           "2024-07-01", "2024-02-01", "2024-08-01", "2022-03-01",
           "2022-04-01", "2022-05-01", "2024-09-01")
)
included_by_cycle <- fixture[include_in_analysis == "Yes",
  .(total = sum(amount)), by = cycle_label]
stopifnot(sort(included_by_cycle$total) == c(910, 1950))

# Uncapped: every included row is regrouped, nothing is folded, and the excluded
# row is absent from both frames.
uncapped <- spending_detail_frames(fixture)
recipients <- uncapped$recipient_detail
contributors <- uncapped$contributor_detail
stopifnot(
  nrow(recipients) == 10L,
  nrow(contributors) == 9L,
  all(recipients$folded_rows == 0L),
  all(contributors$folded_rows == 0L),
  uncapped$stats$recipient_folded_rows == 0L,
  uncapped$stats$contributor_folded_rows == 0L,
  uncapped$stats$row_cap == 45000L,
  is.integer(recipients$cycle),
  is.integer(contributors$cycle),
  # The excluded 9,999 never reaches a sheet.
  recipients[committee_id == "C1" & cycle == 2024L, total_giving] == 1000,
  contributors[city == "San Francisco" & cycle == 2024L, total_giving] == 1000
)
stopifnot(
  recipients[committee_id == "C1" & cycle == 2024L, committee_type_label] ==
    "Independent expenditure-only (Super PAC)",
  recipients[committee_id == "C6", designation_label] == "Lobbyist/Registrant PAC",
  # An unrecognised type code and a blank designation label as "", not as a guess.
  recipients[committee_id == "C8", committee_type] == "ZZ",
  recipients[committee_id == "C8", committee_type_label] == "",
  recipients[committee_id == "C8", designation_label] == "",
  recipients[committee_id == "C9", gift_count] == 1L,
  recipients[committee_id == "C9", contributors] == 1L,
  recipients[committee_id == "C1" & cycle == 2024L, first_date] == "2024-03-01",
  recipients[committee_id == "C1" & cycle == 2024L, last_date] == "2024-03-01"
)
# ZIP: five digits kept, ZIP+4 truncated to five, and blank/short/alphabetic
# codes counted under "" rather than dropped - the two Austin rows share one
# cell precisely because both ZIPs are unusable.
stopifnot(
  sort(unique(contributors$zip5)) ==
    c("", "02134", "10001", "94105", "94607", "98101"),
  contributors[city == "Oakland", zip5] == "94607",
  contributors[city == "Austin", zip5] == "",
  contributors[city == "Austin", total_giving] == 50,
  contributors[city == "Austin", gift_count] == 2L,
  contributors[city == "Austin", contributors] == 1L,
  contributors[city == "Seattle" & zip5 == "", total_giving] == 10
)

# S4: street_1/street_2 joined the contributor grain directly after
# occupation, both in the group-by key and in the output frame's column
# order.
occupation_position <- match("occupation", names(contributors))
stopifnot(
  !is.na(occupation_position),
  identical(
    names(contributors)[occupation_position + c(1L, 2L)],
    c("street_1", "street_2")
  )
)

# Capped at 6 with three (cycle, company) groups: three rows are reserved for the
# Other rows, so only the three largest survive and seven recipient groups fold.
# The contributor grain now has nine cells (not eight - see the street_1 split
# above), so its fold count moves too: reserve 3, keep 3, fold 9 - 3 = 6.
capped <- spending_detail_frames(fixture, 6L)
capped_recipients <- capped$recipient_detail
capped_contributors <- capped$contributor_detail
stopifnot(
  nrow(capped_recipients) <= 6L,
  nrow(capped_contributors) <= 6L,
  capped$stats$recipient_rows == nrow(capped_recipients),
  capped$stats$contributor_rows == nrow(capped_contributors),
  capped$stats$recipient_folded_rows == 7L,
  capped$stats$contributor_folded_rows == 6L,
  sum(capped_recipients$folded_rows) == 7L,
  sum(capped_contributors$folded_rows) == 6L,
  sort(capped_recipients[folded_rows == 0L, total_giving], decreasing = TRUE) ==
    c(1000, 900, 800)
)
other_alpha <- capped_recipients[
  cycle == 2024L & company == "Alpha" & folded_rows > 0L
]
stopifnot(
  nrow(other_alpha) == 1L,
  other_alpha$recipient == "Other (4 rows folded)",
  # Only the first text column the sheet shows carries the marker; the join key
  # stays blank so nothing can mistake it for a committee.
  other_alpha$committee_id == "",
  other_alpha$committee_type == "",
  other_alpha$committee_type_label == "",
  other_alpha$total_giving == 140,
  other_alpha$gift_count == 4L,
  # The shared donor gave to two folded committees. Summing per-group counts
  # would report 4 contributors; the source rows report 3.
  other_alpha$contributors == 3L,
  other_alpha$first_date == "2024-04-01",
  other_alpha$last_date == "2024-07-01",
  capped_recipients[cycle == 2022L & folded_rows > 0L, contributors] == 1L,
  capped_contributors[cycle == 2024L & company == "Alpha" & folded_rows > 0L,
    state] == "Other (3 rows folded)",
  capped_contributors[cycle == 2024L & company == "Alpha" & folded_rows > 0L,
    city] == "",
  capped_contributors[cycle == 2024L & company == "Alpha" & folded_rows > 0L,
    contributors] == 3L
)

# THE invariant: folding moves rows, never dollars. Both frames, every cycle,
# capped and uncapped.
for (frames in list(uncapped, capped)) {
  for (name in c("recipient_detail", "contributor_detail")) {
    totals <- frames[[name]][, .(total = sum(total_giving)), by = cycle_label]
    check <- merge(totals, included_by_cycle, by = "cycle_label",
      suffixes = c("_detail", "_source"))
    stopifnot(
      nrow(check) == nrow(included_by_cycle),
      abs(check$total_detail - check$total_source) < 1e-6
    )
  }
}

# A cap the fold cannot work under, and a frame missing a source column, are
# refused rather than silently worked around.
stopifnot(
  inherits(try(spending_detail_frames(fixture, 0L), silent = TRUE), "try-error"),
  inherits(
    try(spending_detail_frames(fixture[, !"zipcode"]), silent = TRUE),
    "try-error"
  )
)

# No included rows at all: typed empty frames, not a crash and not a NULL.
empty <- spending_detail_frames(fixture[include_in_analysis == "No"])
stopifnot(
  nrow(empty$recipient_detail) == 0L,
  nrow(empty$contributor_detail) == 0L,
  "committee_type_label" %in% names(empty$recipient_detail),
  "zip5" %in% names(empty$contributor_detail),
  empty$stats$recipient_rows == 0L
)

# G8. The guard has to fail on a shortfall, a surplus, and a cycle the detail
# frames never reached at all.
expected <- list(`2023-2024` = 1950, `2021-2022` = 910)
stopifnot(
  mentat_guard_detail_reconciliation(uncapped, expected)$passed,
  mentat_guard_detail_reconciliation(capped, expected)$passed,
  mentat_guard_detail_reconciliation(uncapped, expected)$severity == "fail",
  mentat_guard_detail_reconciliation(uncapped, c(expected, `2025-2026` = 0))$passed,
  !mentat_guard_detail_reconciliation(
    uncapped, list(`2023-2024` = 1949.99, `2021-2022` = 910)
  )$passed,
  !mentat_guard_detail_reconciliation(
    uncapped, list(`2023-2024` = 1950, `2021-2022` = 910, `2025-2026` = 25)
  )$passed,
  !mentat_guard_detail_reconciliation(empty, expected)$passed,
  # Half a pass is a fail: one good sheet must not mask the other.
  !mentat_guard_detail_reconciliation(
    list(recipient_detail = uncapped$recipient_detail,
         contributor_detail = empty$contributor_detail),
    expected
  )$passed,
  # Within tolerance is still a pass; float summation order must not fail a build.
  mentat_guard_detail_reconciliation(
    uncapped, list(`2023-2024` = 1950.004, `2021-2022` = 910)
  )$passed
)
detail_failure <- mentat_guard_detail_reconciliation(empty, expected)
stopifnot(
  detail_failure$metric == 4,
  grepl("2023-2024/recipient_detail", detail_failure$detail, fixed = TRUE)
)

# Conduit (ActBlue, WinRed) is a category like any other to the detail sheets:
# its committees keep their own rows labelled Conduit, an ordinary Carey stays
# Carey, and the extra dollars reconcile to the cycle totals through the guard.
conduit_fixture <- rbind(
  fixture,
  fixture[1L][, `:=`(
    committee_id = "C00401224", recipient = "ACTBLUE", committee_type = "N",
    committee_designation = "U", recipient_category = "Conduit", amount = 35,
    person_key = "pC1", date = "2024-03-05"
  )],
  fixture[1L][, `:=`(
    committee_id = "C00694323", recipient = "WINRED", committee_type = "N",
    committee_designation = "U", recipient_category = "Conduit", amount = 45,
    person_key = "pC2", date = "2024-03-06"
  )],
  fixture[1L][, `:=`(
    committee_id = "C00000005", recipient = "ORDINARY HYBRID PAC",
    committee_type = "V", committee_designation = "", recipient_category = "Carey",
    amount = 10, person_key = "pC3", date = "2024-03-07"
  )]
)
conduit_frames <- spending_detail_frames(conduit_fixture)
conduit_recipients <- conduit_frames$recipient_detail
stopifnot(
  nrow(conduit_recipients) == nrow(recipients) + 3L,
  conduit_recipients[committee_id == "C00401224", recipient_category] == "Conduit",
  conduit_recipients[committee_id == "C00694323", recipient_category] == "Conduit",
  conduit_recipients[committee_id == "C00000005", recipient_category] == "Carey",
  conduit_recipients[recipient_category == "Conduit", sum(total_giving)] == 80,
  conduit_recipients[recipient_category == "Carey", sum(total_giving)] == 10,
  mentat_guard_detail_reconciliation(
    conduit_frames, list(`2023-2024` = 1950 + 90, `2021-2022` = 910)
  )$passed
)

cat("test_spending_detail_frames: PASS\n")

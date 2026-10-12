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
source(file.path(project_root, "R", "paths.R"), local = .GlobalEnv)
source(file.path(project_root, "R", "ingest.R"), local = .GlobalEnv)

fec_mode_text_reference <- function(values) {
  value <- trim_text(values)
  value <- value[nzchar(value)]
  if (!length(value)) return("")
  counts <- sort(table(value), decreasing = TRUE)
  sort(names(counts)[counts == counts[[1L]]])[[1L]]
}
fec_join_unique_reference <- function(values) {
  value <- sort(unique(trim_text(values)))
  paste(value[nzchar(value)], collapse = "; ")
}
fec_min_text_reference <- function(values) {
  value <- sort(unique(trim_text(values)))
  value <- value[nzchar(value)]
  if (length(value)) value[[1L]] else ""
}
fec_max_text_reference <- function(values) {
  value <- sort(unique(trim_text(values)))
  value <- value[nzchar(value)]
  if (length(value)) value[[length(value)]] else ""
}

source(
  file.path(project_root, "R", "collect.R"),
  local = .GlobalEnv
)
source(
  file.path(project_root, "R", "fec_beneficiary.R"),
  local = .GlobalEnv
)
source(
  file.path(project_root, "R", "export_guards.R"),
  local = .GlobalEnv
)
require_package("data.table")
data.table::setDTthreads(2L)

set.seed(20260723)
pool <- c("", " ", NA_character_, "Alpha", "ALPHA", "Beta", "Gamma", "Zeta")
for (case in seq_len(5000L)) {
  values <- sample(pool, sample.int(50L, 1L) - 1L, replace = TRUE)
  stopifnot(
    identical(fec_mode_text(values), fec_mode_text_reference(values)),
    identical(fec_join_unique(values), fec_join_unique_reference(values)),
    identical(fec_min_text(values), fec_min_text_reference(values)),
    identical(fec_max_text(values), fec_max_text_reference(values))
  )
}

# Candidate/source fields are normally constant within downstream groups.
# This benchmark pins the fast path that matters for the 870k-row workload.
constant_groups <- rep(list(rep("Candidate Name", 8L)), 20000L)
reference_seconds <- unname(system.time(
  reference <- vapply(
    constant_groups,
    fec_mode_text_reference,
    character(1L)
  )
)[["elapsed"]])
optimized_seconds <- unname(system.time(
  optimized <- vapply(constant_groups, fec_mode_text, character(1L))
)[["elapsed"]])
stopifnot(identical(optimized, reference))

fec_build_beneficiary_tables_reference <- function(raw_export, downstream_activity) {
  downstream <- downstream_activity[include_in_total == "Yes", .(
    preliminary_downstream_amount = sum(transaction_amount, na.rm = TRUE),
    support_amount = sum(
      transaction_amount[support_oppose == "Support"], na.rm = TRUE
    ),
    oppose_amount = sum(
      transaction_amount[support_oppose == "Oppose"], na.rm = TRUE
    ),
    transaction_count = .N,
    activity_relationships = fec_join_unique_reference(activity_type),
    directions = fec_join_unique_reference(support_oppose),
    first_activity_date = fec_min_text_reference(transaction_date),
    last_activity_date = fec_max_text_reference(transaction_date),
    candidate_name = fec_mode_text_reference(candidate_name),
    candidate_party = fec_mode_text_reference(candidate_party),
    office = fec_mode_text_reference(office),
    office_label = fec_mode_text_reference(office_label),
    office_state = fec_mode_text_reference(office_state),
    district = fec_mode_text_reference(district)
  ), by = .(cycle, source_committee_id = committee_id, candidate_id)]
  funded <- raw_export[
    include_in_analysis == "Yes" & nzchar(committee_id),
    .(
      preliminary_crypto_funding = sum(amount, na.rm = TRUE),
      source_committee = fec_mode_text_reference(recipient)
    ),
    by = .(cycle, company, source_committee_id = committee_id)
  ]
  industry <- funded[, .(
    preliminary_crypto_funding = sum(preliminary_crypto_funding, na.rm = TRUE),
    source_committee = fec_mode_text_reference(source_committee)
  ), by = .(cycle, source_committee_id)]
  industry[, `:=`(view = "Industry", company = "Industry Total")]
  company <- data.table::copy(funded)
  company[, view := "Company"]
  funding <- data.table::rbindlist(list(industry, company), use.names = TRUE, fill = TRUE)
  bridge <- merge(
    funding, downstream,
    by = c("cycle", "source_committee_id"),
    allow.cartesian = TRUE,
    sort = FALSE
  )
  bridge[, candidate_key := ifelse(
    nzchar(candidate_id),
    paste0("CAND:", candidate_id),
    paste0("NAME:", normalize_variant_key(candidate_name))
  )]
  bridge[, bridge_key := paste(
    cycle, view, company, source_committee_id, candidate_key, sep = "|"
  )]
  data.table::setorder(
    bridge, cycle, view, company, -preliminary_downstream_amount,
    candidate_name
  )
  summary <- bridge[, .(
    preliminary_downstream_amount = sum(preliminary_downstream_amount, na.rm = TRUE),
    support_amount = sum(support_amount, na.rm = TRUE),
    oppose_amount = sum(oppose_amount, na.rm = TRUE),
    preliminary_crypto_funding = sum(preliminary_crypto_funding, na.rm = TRUE),
    source_committee_count = data.table::uniqueN(source_committee_id),
    source_committees = fec_join_unique_reference(paste0(
      source_committee, " (", source_committee_id, ")"
    )),
    activity_relationships = fec_join_unique_reference(activity_relationships),
    directions = fec_join_unique_reference(directions),
    candidate_name = fec_mode_text_reference(candidate_name),
    candidate_party = fec_mode_text_reference(candidate_party),
    office = fec_mode_text_reference(office),
    office_label = fec_mode_text_reference(office_label),
    office_state = fec_mode_text_reference(office_state),
    district = fec_mode_text_reference(district)
  ), by = .(cycle, view, company, candidate_id, candidate_key)]
  data.table::setorder(
    summary, cycle, view, company, -preliminary_downstream_amount,
    candidate_name
  )
  summary <- summary[office %in% c("H", "S")]
  summary[, preliminary_rank := seq_len(.N), by = .(cycle, view, company)]
  summary <- summary[preliminary_rank <= 100L]
  retained <- unique(summary[, .(cycle, view, company, candidate_key)])
  bridge <- merge(
    bridge,
    retained,
    by = c("cycle", "view", "company", "candidate_key"),
    sort = FALSE
  )
  data.table::setorder(
    bridge, cycle, view, company, -preliminary_downstream_amount,
    candidate_name
  )
  list(bridge = bridge, summary = summary)
}

set.seed(737)
downstream_rows <- 15000L
downstream_fixture <- data.table::data.table(
  cycle = sample(c(2022L, 2024L, 2026L), downstream_rows, replace = TRUE),
  committee_id = sample(sprintf("C%03d", 1:30), downstream_rows, replace = TRUE),
  candidate_id = sample(sprintf("H%03d", 1:80), downstream_rows, replace = TRUE),
  include_in_total = sample(c("Yes", "No"), downstream_rows, replace = TRUE, prob = c(0.94, 0.06)),
  transaction_amount = sample(c(-500, 0, 100, 250, 1000), downstream_rows, replace = TRUE),
  activity_type = sample(c("Support", "Oppose", "Contribution", ""), downstream_rows, replace = TRUE),
  support_oppose = sample(c("Support", "Oppose", "Contribution / Other"), downstream_rows, replace = TRUE),
  transaction_date = sample(c("", "2022-01-01", "2024-05-10", "2026-07-23"), downstream_rows, replace = TRUE),
  candidate_name = sample(c("ALPHA, ALEX", "BETA, BLAKE", "GAMMA, GREY", ""), downstream_rows, replace = TRUE),
  candidate_party = sample(c("D", "R", "Other / Unclassified"), downstream_rows, replace = TRUE),
  office = sample(c("H", "S", ""), downstream_rows, replace = TRUE),
  office_label = sample(c("House", "Senate", ""), downstream_rows, replace = TRUE),
  office_state = sample(c("CA", "MN", "NY", ""), downstream_rows, replace = TRUE),
  district = sample(c("01", "02", "00", ""), downstream_rows, replace = TRUE)
)
raw_fixture <- unique(downstream_fixture[, .(cycle, committee_id)])[rep(
  seq_len(.N), each = 2L
)]
raw_fixture[, `:=`(
  company = rep(c("Company A", "Company B"), length.out = .N),
  include_in_analysis = "Yes",
  amount = rep(c(10, 25), length.out = .N),
  recipient = paste("Committee", committee_id)
)]
reference_tables <- fec_build_beneficiary_tables_reference(
  raw_fixture, downstream_fixture
)
optimized_tables <- fec_build_beneficiary_tables(
  raw_fixture, downstream_fixture
)
# All existing financial columns retain exact parity when no routes are supplied.
# The new route and comparison evidence is additive to the return contract.
stopifnot(identical(optimized_tables$bridge[, names(reference_tables$bridge), with=FALSE], reference_tables$bridge),
          identical(optimized_tables$summary, reference_tables$summary),
          all(optimized_tables$measurement_ledger$delta == 0),
          !anyDuplicated(optimized_tables$reach[, .(cycle, view, company, source_committee_id)]))

# Direction split (F3). A candidate reached by BOTH support and oppose spending
# is the case the old text-matching guard got wrong: it read "Oppose" in the
# joined [Directions] string and discarded the support dollars too. The split
# must report each direction as its own component, and must not net them.
direction_fixture <- data.table::data.table(
  cycle = 2024L,
  committee_id = "C00000001",
  candidate_id = "H0MIXED001",
  transaction_amount = c(1000, 250, 40),
  transaction_type = c("24E", "24A", "24K"),
  transaction_date = "2024-03-01",
  activity_type = "Independent expenditure",
  candidate_name = "MIXED, CANDIDATE",
  candidate_party = "R",
  office = "H",
  office_label = "House",
  office_state = "MI",
  district = "01",
  include_in_total = "Yes"
)
direction_fixture[, support_oppose := fec_downstream_direction(transaction_type)]
direction_raw <- data.table::data.table(
  cycle = 2024L,
  committee_id = "C00000001",
  company = "Company A",
  include_in_analysis = "Yes",
  amount = 500,
  recipient = "Committee C00000001"
)
direction_summary <- fec_build_beneficiary_tables(
  direction_raw, direction_fixture
)$summary
stopifnot(nrow(direction_summary) > 0L)
direction_row <- direction_summary[view == "Company"][1L]
# 24E support, 24A oppose, 24K neither: each lands in exactly one place and the
# unsigned total still carries all three, so support+oppose <= total.
stopifnot(
  isTRUE(all.equal(direction_row$support_amount, 1000)),
  isTRUE(all.equal(direction_row$oppose_amount, 250)),
  isTRUE(all.equal(direction_row$preliminary_downstream_amount, 1290)),
  direction_row$support_amount + direction_row$oppose_amount <=
    direction_row$preliminary_downstream_amount + 1e-9,
  grepl("Support", direction_row$directions),
  grepl("Oppose", direction_row$directions)
)
# High-activity presidential candidates must not consume congressional slots.
rank_fixture <- direction_fixture[rep(1L, 102L)]
rank_fixture[, `:=`(candidate_id = sprintf("H%08d", .I),
  candidate_name = sprintf("Candidate %03d", .I), transaction_amount = 103 - .I)]
rank_fixture[1:2, `:=`(office = "P", candidate_id = sprintf("P%08d", .I))]
rank_summary <- fec_build_beneficiary_tables(direction_raw, rank_fixture)$summary
stopifnot(nrow(rank_summary[view == "Industry"]) == 100L,
          max(rank_summary$preliminary_rank) == 100L,
          all(rank_summary$office == "H"))

stopifnot(mentat_guard_date_sanity(rep("2026-01-02", 1500))$passed,
          !mentat_guard_date_sanity("0021-12-12")$passed,
          !mentat_guard_date_sanity("not-a-date")$passed)

# G7: a rendered category nothing populates is reported, except an optional one.
# Conduit (ActBlue, WinRed) is a real zero for an industry with no earmark-platform
# receipts, so its absence is not a finding; a missing Carey still is.
population_declared <- c("Super PAC", "Carey", "Conduit", "Candidate")
stopifnot(
  mentat_guard_category_population(
    population_declared, c("Super PAC", "Carey", "Candidate"),
    optional = "Conduit"
  )$passed,
  !mentat_guard_category_population(
    population_declared, c("Super PAC", "Candidate"), optional = "Conduit"
  )$passed,
  grepl("Carey", mentat_guard_category_population(
    population_declared, c("Super PAC", "Candidate"), optional = "Conduit"
  )$detail, fixed = TRUE),
  !grepl("Conduit", mentat_guard_category_population(
    population_declared, c("Super PAC", "Candidate"), optional = "Conduit"
  )$detail, fixed = TRUE),
  # Without the optional argument the old behaviour stands.
  !mentat_guard_category_population(
    population_declared, c("Super PAC", "Carey", "Candidate")
  )$passed
)

# Dates cannot establish whether repeated transaction IDs are amendments or
# separate quarterly reports. Retain distinct observations provisionally by
# default, and exercise the two explicitly selected sensitivity policies.
pas2_root <- tempfile("pas2-year-scope-")
dir.create(file.path(pas2_root, "pas224"), recursive = TRUE)
pas2_row <- function(date, value, file, sub) {
  fields <- rep("", 22L)
  fields[c(1,2,3,6,14,15,17,18,19,22)] <-
    c("C00000001","N","Q1","24K",date,value,"H00000001","T1",file,sub)
  paste(fields, collapse = "|")
}
writeLines(c(pas2_row("01022023","100","1","1"),
             pas2_row("01022024","100","2","2"),
             pas2_row("01022024","150","3","3")),
           file.path(pas2_root,"pas224","itpas2.txt"))
# Parser v6 retains every distinct source observation under the count_all
# default. Ambiguity stays visible and a larger filing number alone cannot
# suppress an observation. The legacy count_latest and withhold behaviors
# remain pinned independently; their existing assertions are preserved below.
stopifnot(identical(FEC_PAS2_AMBIGUOUS_VERSION_POLICY, "count_all"))
default_pas2_policy <- FEC_PAS2_AMBIGUOUS_VERSION_POLICY
default_fixture <- fec_build_pas2_index(pas2_root, 2024L)
default_cache <- fec_pas2_cache_file(pas2_root, 2024L)
stopifnot(nrow(default_fixture) == 3L,
          all(default_fixture$unverified_version == 1L),
          all(default_fixture$include_in_total == "Yes"),
          all(default_fixture$superseded_by_amendment == 0L),
          sum(default_fixture$transaction_amount) == 350,
          all(substr(default_fixture$transaction_date,1,4) %in% c("2023","2024")))
FEC_PAS2_AMBIGUOUS_VERSION_POLICY <<- "count_latest"
counted_fixture <- fec_build_pas2_index(pas2_root, 2024L)
latest_cache <- fec_pas2_cache_file(pas2_root, 2024L)
stopifnot(nrow(counted_fixture) == 3L,
          all(counted_fixture$unverified_version == 1L),
          sum(counted_fixture$include_in_total == "Yes") == 1L,
          identical(counted_fixture[file_num == 3, include_in_total], "Yes"),
          sum(counted_fixture[include_in_total == "Yes", transaction_amount]) == 150,
          all(substr(counted_fixture$transaction_date,1,4) %in% c("2023","2024")))
FEC_PAS2_AMBIGUOUS_VERSION_POLICY <<- "withhold"
parsed_fixture <- fec_build_pas2_index(pas2_root, 2024L)
withheld_cache <- fec_pas2_cache_file(pas2_root, 2024L)
stopifnot(nrow(parsed_fixture) == 3L, all(parsed_fixture$unresolved_version == 1L),
          all(parsed_fixture$include_in_total == "No"),
          all(substr(parsed_fixture$transaction_date,1,4) %in% c("2023","2024")))
stopifnot(length(unique(c(default_cache, latest_cache, withheld_cache))) == 3L)
FEC_PAS2_AMBIGUOUS_VERSION_POLICY <<- default_pas2_policy
unlink(pas2_root, recursive = TRUE)

# Candidate-less rows are retained now, so the placeholder name must say that
# no ID was reported rather than printing an empty parenthesis. An ID the
# candidate master does not carry still prints the ID.
label_root <- tempfile("pas2-candidate-label-")
dir.create(file.path(label_root, "pas224"), recursive = TRUE)
label_row <- function(candidate, transaction, sub) {
  fields <- rep("", 22L)
  fields[c(1,2,3,6,14,15,17,18,19,22)] <-
    c("C00000001","N","Q1","24K","01022024","100",candidate,transaction,"1",sub)
  paste(fields, collapse = "|")
}
writeLines(c(label_row("H00000001","T1","1"),
             label_row("","T2","2"),
             label_row("H00000009","T3","3")),
           file.path(label_root,"pas224","itpas2.txt"))
labelled <- fec_read_downstream_activity(
  label_root,
  data.table::data.table(cycle = 2024L, committee_id = "C00000001"),
  data.table::data.table(cycle = 2024L, candidate_id = "H00000001",
    candidate_name = "KNOWN, CANDIDATE", candidate_party = "D", office = "H",
    office_label = "House", office_state = "MI", district = "01",
    source_url = "https://www.fec.gov/data/candidates/"),
  cycles = 2024L
)
stopifnot(nrow(labelled) == 3L,
          identical(labelled[candidate_id == "H00000001", candidate_name],
                    "KNOWN, CANDIDATE"),
          identical(labelled[candidate_id == "H00000009", candidate_name],
                    "Unresolved candidate (H00000009)"),
          identical(labelled[!nzchar(candidate_id), candidate_name],
                    "Unresolved candidate (no ID reported)"))
unlink(label_root, recursive = TRUE)

direction_guard <- mentat_guard_direction_integrity(direction_summary)
stopifnot(
  isTRUE(direction_guard$passed[[1L]]),
  identical(direction_guard$severity[[1L]], "fail")
)
missing_direction_guard <- mentat_guard_direction_integrity(
  direction_summary[
    ,
    setdiff(names(direction_summary), "oppose_amount"),
    with = FALSE
  ]
)
stopifnot(
  !isTRUE(missing_direction_guard$passed[[1L]]),
  identical(missing_direction_guard$severity[[1L]], "fail")
)
inconsistent_direction <- data.table::copy(direction_summary)
inconsistent_direction[, directions := "Support"]
inconsistent_direction[, oppose_amount := 250]
inconsistent_direction_guard <- mentat_guard_direction_integrity(
  inconsistent_direction
)
stopifnot(
  !isTRUE(inconsistent_direction_guard$passed[[1L]]),
  identical(inconsistent_direction_guard$severity[[1L]], "fail")
)

cat(sprintf(
  paste0(
    "test_fec_beneficiary_helpers: randomized=5000 parity_rows=%d groups=%d ",
    "reference=%.3fs optimized=%.3fs speedup=%.1fx PASS\n"
  ),
  downstream_rows,
  length(constant_groups),
  reference_seconds,
  optimized_seconds,
  reference_seconds / max(optimized_seconds, .Machine$double.eps)
))

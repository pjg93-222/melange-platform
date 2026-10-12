args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 2L || length(args) > 6L) {
  stop(
    paste(
      "usage: Rscript tools/export_crypto_spending_data.R",
      "<mentat.db> <output.json> [fec-committee-master-dir]",
      "[fec-beneficiary-dir] [industry] [tidy-staging.sqlite]"
    ),
    call. = FALSE
  )
}

database_path <- normalizePath(args[[1L]], winslash = "/", mustWork = TRUE)
output_path <- normalizePath(args[[2L]], winslash = "/", mustWork = FALSE)
committee_master_dir <- if (length(args) == 3L) {
  normalizePath(args[[3L]], winslash = "/", mustWork = TRUE)
} else {
  ""
}
if (length(args) >= 3L) {
  committee_master_dir <- normalizePath(args[[3L]], winslash = "/", mustWork = TRUE)
}
beneficiary_dir <- if (length(args) >= 4L) {
  normalizePath(args[[4L]], winslash = "/", mustWork = TRUE)
} else {
  ""
}
script_argument <- grep("^--file=", commandArgs(trailingOnly = FALSE), value = TRUE)
script_path <- normalizePath(
  sub("^--file=", "", script_argument[[1L]]),
  winslash = "/",
  mustWork = TRUE
)
MENTAT_PROJECT_ROOT <- dirname(dirname(script_path))
MENTAT_INDUSTRY <- if (length(args) >= 5L && nzchar(trimws(args[[5L]]))) {
  trimws(args[[5L]])
} else {
  trimws(Sys.getenv("MENTAT_INDUSTRY", unset = "Crypto"))
}
if (!nzchar(MENTAT_INDUSTRY)) {
  stop("Industry cannot be empty.", call. = FALSE)
}
# Sixth positional argument: where to write the tidy staging store that
# tools/build_tidy_workbook.py turns into the companion
# `<Industry> Industry Spending Data.xlsx`. ABSENT means "skip staging": a
# five-argument invocation - every existing caller, every ad-hoc console run -
# behaves exactly as before and only prints that it skipped. The path is NOT
# mustWork: the orchestrator passes a pending name that does not exist yet.
tidy_stage_path <- if (length(args) >= 6L && nzchar(trimws(args[[6L]]))) {
  normalizePath(trimws(args[[6L]]), winslash = "/", mustWork = FALSE)
} else {
  ""
}

for (module in c(
  "paths.R", "schema.R", "ingest.R", "fec_page.R", "collect.R",
  "fec_api.R", "reconcile.R", "report.R", "fec_beneficiary.R",
  "identity_components.R", "export_guards.R", "fec_transfer_graph.R",
  "state_destinations.R", "tidy_stage.R"
)) {
  source(file.path(MENTAT_PROJECT_ROOT, "R", module), local = .GlobalEnv)
}

# A memo the memo plan removed as its counted parent's duplicate (duplicate_memo 1;
# F13's memo_redisclosure_of_counted_parent, or an FEC back reference) was matched:
# it carries the match as its reason, never "disclosure_memo_unmatched".
# tests/test_export_memo_reason.R reads this definition from this file.
export_memo_duplicate_reason <- function(reason, duplicate_memo, duplicate_reason) {
  matched <- !is.na(duplicate_memo) & duplicate_memo == 1L & reason %in% "disclosure_memo_unmatched"
  reason[matched] <- ifelse(is.na(duplicate_reason[matched]), "", as.character(duplicate_reason[matched]))
  reason
}

require_package("data.table")
require_package("DBI")
require_package("RSQLite")
require_package("jsonlite")

export_started_at <- unname(proc.time()[["elapsed"]])
export_phase_started_at <- export_started_at
export_phase <- function(name, rows = NA_integer_) {
  now <- unname(proc.time()[["elapsed"]])
  detail <- if (length(rows) == 1L && !is.na(rows)) {
    paste0(" rows=", format(rows, scientific = FALSE, trim = TRUE))
  } else {
    ""
  }
  cat(sprintf(
    "EXPORT PHASE %-32s phase=%.3fs total=%.3fs%s\n",
    name,
    now - export_phase_started_at,
    now - export_started_at,
    detail
  ))
  flush(stdout())
  export_phase_started_at <<- now
  invisible(now)
}

map_unique_text <- function(values, mapper) {
  values <- as.character(values)
  unique_values <- unique(values)
  mapped <- vapply(unique_values, mapper, character(1L))
  mapped[match(values, unique_values)]
}

supported_cycles <- c(2022L, 2024L, 2026L)
# The cycles the pipeline covers at all, independent of any per-run filter.
# Cycle exceptions are rows outside THIS set - a row from another supported cycle
# is not an exception, it simply belongs to a different per-cycle build.
mentat_all_cycles <- supported_cycles

# MENTAT_CYCLE_FILTER narrows the export to one cycle.
#
# Not an optimisation - it is what makes a large industry exportable at all. The
# payload is read back with JSON.parse(readFileSync(path,"utf8")) by both the
# build's validator and the workbook builder, and V8 caps a single string at
# 536,870,888 bytes. Telecom's three-cycle payload is 591,338,574 and fails
# before anything can look at it. One cycle is roughly a third of that.
# The build script already sets this variable for -CycleLabel, but only around
# the workbook step, long after the export has written the full payload.
mentat_cycle_filter <- trimws(Sys.getenv("MENTAT_CYCLE_FILTER", unset = ""))
if (nzchar(mentat_cycle_filter)) {
  # Accepts either the label ("2021-2022") or the cycle year ("2022").
  requested <- suppressWarnings(as.integer(
    sub("^\\s*[0-9]{4}\\s*-\\s*", "", mentat_cycle_filter)
  ))
  if (is.na(requested) || !requested %in% supported_cycles) {
    stop(
      "MENTAT_CYCLE_FILTER must name a supported cycle (2022, 2024, 2026 or a ",
      "label such as 2021-2022); got: ", mentat_cycle_filter,
      call. = FALSE
    )
  }
  supported_cycles <- requested
  cat(sprintf(
    "cycle filter: exporting %d only (payload split to stay under the 512MB
JSON string limit)\n",
    requested
  ))
}

contribution_cycle <- function(values) {
  dates <- normalize_reconcile_date(values)
  years <- as.integer(format(dates, "%Y"))
  years + years %% 2L
}

read_fec_committee_master <- function(directory) {
  if (!nzchar(directory)) {
    return(data.table::data.table(
      cycle = integer(), committee_id = character(), committee_name = character(),
      designation = character(), committee_type = character(),
      party_affiliation = character(), party = character(),
      connected_organization = character(), candidate_id = character(),
      source_url = character(), state = character()
    ))
  }
  headers <- c(
    "committee_id", "committee_name", "treasurer", "street_1", "street_2",
    "city", "state", "zip", "designation", "committee_type",
    "party_affiliation", "filing_frequency", "organization_type",
    "connected_organization", "candidate_id"
  )
  output <- lapply(supported_cycles, function(cycle) {
    suffix <- substr(as.character(cycle), 3L, 4L)
    path <- file.path(directory, paste0("cm", suffix), "cm.txt")
    if (!file.exists(path)) {
      stop("Missing official FEC committee master: ", path, call. = FALSE)
    }
    master <- data.table::fread(
      path,
      sep = "|",
      header = FALSE,
      fill = TRUE,
      quote = "",
      col.names = headers,
      encoding = "UTF-8",
      showProgress = FALSE
    )
    master[, cycle := cycle]
    master[, source_url := paste0(
      "https://www.fec.gov/files/bulk-downloads/", cycle, "/cm", suffix, ".zip"
    )]
    master[, .(
      cycle,
      committee_id = toupper(trim_text(committee_id)),
      committee_name = trim_text(committee_name),
      state = toupper(trim_text(state)),
      designation = toupper(trim_text(designation)),
      committee_type = toupper(trim_text(committee_type)),
      party_affiliation = toupper(trim_text(party_affiliation)),
      connected_organization = trim_text(connected_organization),
      candidate_id = toupper(trim_text(candidate_id)),
      source_url
    )]
  })
  master <- data.table::rbindlist(output, use.names = TRUE, fill = TRUE)
  master[, party := ifelse(
    party_affiliation %in% c("DEM", "DEMOCRATIC", "DFL"),
    "D",
    ifelse(party_affiliation %in% c("REP", "REPUBLICAN", "GOP"), "R", "")
  )]
  master[nzchar(committee_id)]
}

fec_committee_master <- read_fec_committee_master(committee_master_dir)
export_phase("committee_master_loaded", nrow(fec_committee_master))

connection <- DBI::dbConnect(
  RSQLite::SQLite(),
  database_path,
  flags = RSQLite::SQLITE_RO
)
on.exit(DBI::dbDisconnect(connection), add = TRUE)

lookups <- spending_report_lookups(connection)
raw_rows <- spending_raw_report_rows(connection, versioning_applied = TRUE)
raw_rows[, company_attribution_status := fec_attribution_status(raw_rows,
  split(lookups$companies$variant, lookups$companies$company), fec_committee_master)]
export_phase("raw_database_rows_loaded", nrow(raw_rows))
# S2 HANDOFF: this fill is a defensive guard for databases or report.R
# versions whose spending_raw_report_rows() SELECT lacks
# contributor_street_1 / contributor_street_2, so raw_rows could still
# arrive without them. Backfill both columns here, before raw_export is
# copied from raw_rows below, so the .() projection does not error with
# "object not found"; report.R's SELECTs carry the two columns as of S2.
for (column in c("contributor_street_1", "contributor_street_2")) {
  if (!column %in% names(raw_rows)) raw_rows[, (column) := NA_character_]
}
evidence_scope <- contribution_cycle(raw_rows$contribution_receipt_date) %in% supported_cycles
unresolved <- evidence_scope & fec_receipt_countable(raw_rows) &
  fec_receipt_role_unresolved(raw_rows$receipt_role)
if (any(unresolved)) {
  atomic_write_csv(raw_rows[unresolved], paste0(output_path, ".receipt-review.csv"))
}
# 2026-09-17 ruling: unresolved receipt roles no longer block the export. The
# CSV above is the review list for rows that ship counted and flagged
# (receipt_role_unverified = 1); the gate below stops publication only when a
# row's own evidence contradicts itself.
fec_assert_receipt_evidence(raw_rows[evidence_scope])
analysis_rows <- spending_report_rows(
  connection,
  lookups = lookups,
  raw_rows = raw_rows
)
export_phase("analysis_rows_prepared", nrow(analysis_rows))

if (!nrow(raw_rows) || !nrow(analysis_rows)) {
  stop(MENTAT_INDUSTRY, " report source contains no rows.", call. = FALSE)
}

analysis_rows[, analysis_cycle := contribution_cycle(contribution_receipt_date)]
analysis_rows[, committee_master_key := paste(
  as.integer(two_year_transaction_period),
  toupper(trim_text(committee_id)),
  sep = "\r"
)]
fec_committee_master[, committee_master_key := paste(
  cycle,
  committee_id,
  sep = "\r"
)]
master_index <- match(
  analysis_rows$committee_master_key,
  fec_committee_master$committee_master_key
)
analysis_rows[, type_filled_from_master := FALSE]
analysis_rows[, designation_filled_from_master := FALSE]
type_fill <- !is.na(master_index) &
  nzchar(fec_committee_master$committee_type[master_index])
designation_fill <- !is.na(master_index) &
  nzchar(fec_committee_master$designation[master_index])
analysis_rows[type_fill, recipient_committee_type :=
  fec_committee_master$committee_type[master_index[type_fill]]]
analysis_rows[designation_fill, recipient_committee_designation :=
  fec_committee_master$designation[master_index[designation_fill]]]
analysis_rows[type_fill, type_filled_from_master := TRUE]
analysis_rows[designation_fill, designation_filled_from_master := TRUE]

classifications <- spending_recipient_classification(
  connection,
  analysis_rows,
  lookups = lookups
)
# Resolve generic registered PACs without pretending they are company PACs.
# The legacy Stats template keeps its historical grouping; these are the
# independently rendered industry workbooks with an explicit Other PAC channel.
other_pac <- classifications$recipient_category == "Unclassified" &
  analysis_rows$recipient_committee_type %in% c("N", "Q")
classifications$recipient_category[other_pac] <- "Other PAC"
classifications$category_source[other_pac] <- "fec:registered_nonparty_pac_type"
master_party <- ifelse(
  is.na(master_index),
  "",
  fec_committee_master$party[master_index]
)
party_fill <- !nzchar(trim_text(classifications$party)) &
  master_party %in% c("D", "R")
classifications$party[party_fill] <- master_party[party_fill]
classifications$party_source[party_fill] <- paste0(
  "fec_committee_master:",
  analysis_rows$two_year_transaction_period[party_fill]
)
# A registered earmark conduit (ActBlue, WinRed) is Conduit by committee ID, not
# by type or designation, so its source keeps saying so instead of being
# rewritten to the committee master's type-and-designation wording.
master_category_fill <- (
  analysis_rows$type_filled_from_master |
    analysis_rows$designation_filled_from_master
) & grepl("^fec:", classifications$category_source) &
  classifications$recipient_category != "Conduit"
classifications$category_source[master_category_fill] <- paste0(
  "fec_committee_master:",
  analysis_rows$two_year_transaction_period[master_category_fill],
  ":committee_type_designation"
)
analysis_rows[, recipient_category := classifications$recipient_category]
analysis_rows[, party := trim_text(classifications$party)]
analysis_rows[, category_source := trim_text(classifications$category_source)]
analysis_rows[, party_source := trim_text(classifications$party_source)]
analysis_rows[!nzchar(party), party := "Other / Unclassified"]
export_phase("recipient_classification", nrow(analysis_rows))

executive_keys <- unique(paste(
  spending_report_company_labels(lookups$executives$company),
  map_unique_text(lookups$executives$executive_name, person_identity_key),
  sep = "\r"
))
verified_employee_names <- if (identical(MENTAT_INDUSTRY, "Crypto")) {
  data.table::data.table(
    company = rep("Coinbase", 5L),
    person = c(
      "POLLAK, JESSE",
      "TUSAR, GREG",
      "GREWAL, PAUL",
      "GUPTA, MANISH",
      "BAUGUESS, SCOTT"
    )
  )
} else {
  data.table::data.table(company = character(), person = character())
}
verified_employee_keys <- unique(paste(
  verified_employee_names$company,
  map_unique_text(verified_employee_names$person, person_identity_key),
  sep = "\r"
))
analysis_rows[, display_company := spending_report_company_labels(company)]
analysis_rows[, canonical_person_key := map_unique_text(
  contributor_name,
  person_identity_key
)]
analysis_rows[, known_executive := paste(
  display_company,
  canonical_person_key,
  sep = "\r"
) %in% executive_keys]
analysis_rows[, verified_employee := paste(
  display_company,
  canonical_person_key,
  sep = "\r"
) %in% verified_employee_keys]
analysis_rows[, employee_tier := spending_contributor_tiers(
  contributor_occupation,
  known_executive,
  is_entity_contributor == 1L
)]
analysis_rows[, included_summary := !recipient_category %in% c("527", "501c")]
export_phase("person_fields_normalized", nrow(analysis_rows))

person_rows <- analysis_rows[
  is_entity_contributor == 0L,
  .(
    sub_id,
    cycle = as.integer(analysis_cycle),
    company = display_company,
    contributor_name = trim_text(contributor_name),
    exact_name_key = normalize_variant_key(contributor_name),
    base_key = gsub("\034", "|", canonical_person_key, fixed = TRUE),
    city = trim_text(contributor_city),
    state = trim_text(contributor_state),
    zipcode = trim_text(contributor_zip),
    employer = trim_text(contributor_employer),
    employer_key = map_unique_text(contributor_employer, strip_legal_suffix),
    occupation = trim_text(contributor_occupation),
    occupation_key = normalize_occupation(contributor_occupation),
    amount = as.numeric(contribution_receipt_amount),
    party,
    included_summary,
    known_executive,
    verified_employee
  )
]
person_rows[!nzchar(base_key), base_key := exact_name_key]
# Company-wide pass: an employer's own address must not split one person into a
# home identity and a work identity. Computed before the by-person grouping
# because the signature is only visible across all of a company's contributors.
person_rows[, business_address := identify_business_addresses(.SD)]
cat(sprintf(
  "Business-address rows suppressed as identity evidence: %d of %d\n",
  sum(person_rows$business_address),
  nrow(person_rows)
))
export_phase("identity_rows_prepared", nrow(person_rows))

identity_groups <- person_rows[, assign_identity_components(.SD), by = .(company, base_key)]
identity_groups[, component_count := data.table::uniqueN(identity_component), by = .(company, base_key)]
identity_groups[, person_key := paste0(
  company, "|", base_key, "|C", sprintf("%02d", identity_component)
)]
identity_groups[, identity_basis := ifelse(
  known_executive,
  "Known executive: canonical name + company",
  ifelse(
    verified_employee,
    "Verified employee identity: canonical name + company + legacy role continuity",
    ifelse(
      component_count > 1L,
      "Conservative split: same-name evidence was insufficient",
      ifelse(
        data.table::uniqueN(state[nzchar(state)]) > 1L,
        "Exact/canonical name + company + shared title/location evidence",
        "Exact/canonical name + company + location evidence"
      )
    )
  )
), by = person_key]

identity_map <- unique(identity_groups[, .(
  sub_id,
  person_key,
  identity_basis
)], by = "sub_id")

person_cycle <- identity_groups[
  included_summary == TRUE & cycle %in% supported_cycles,
  .(
  preliminary_total = sum(amount, na.rm = TRUE),
  contribution_count = .N,
  person = names(sort(table(contributor_name), decreasing = TRUE))[[1L]],
  locations = paste(sort(unique(trim_text(paste0(
    ifelse(nzchar(city), paste0(city, ", "), ""),
    state
  ))))[nzchar(sort(unique(trim_text(paste0(
    ifelse(nzchar(city), paste0(city, ", "), ""),
    state
  )))))], collapse = "; "),
  zipcodes = paste(sort(unique(zipcode[nzchar(zipcode)])), collapse = "; "),
  titles = paste(sort(unique(occupation[nzchar(occupation)])), collapse = "; "),
  identity_basis = identity_basis[[1L]]
), by = .(cycle, company, person_key)]
data.table::setorder(person_cycle, cycle, -preliminary_total, company, person)
export_phase("identity_components", nrow(identity_groups))

analysis_map <- analysis_rows[, .(
  sub_id,
  record_type,
  display_company,
  employee_tier,
  recipient_category,
  party,
  category_source,
  party_source,
  recipient_committee_type,
  recipient_committee_designation,
  included_summary
)]
analysis_map <- merge(
  analysis_map,
  identity_map,
  by = "sub_id",
  all.x = TRUE,
  sort = FALSE
)

raw_export <- data.table::copy(raw_rows)
raw_export <- merge(
  raw_export,
  analysis_map,
  by = "sub_id",
  all.x = TRUE,
  sort = FALSE,
  suffixes = c("_raw", "")
)
raw_export[, source_cycle := as.integer(two_year_transaction_period)]
raw_export[, cycle := contribution_cycle(contribution_receipt_date)]
raw_export[, cycle_label := sprintf("%d-%d", cycle - 1L, cycle)]
raw_export[, company_display := spending_report_company_labels(company)]
raw_export[, amount := as.numeric(contribution_receipt_amount)]
raw_export[, date := format(
  normalize_reconcile_date(contribution_receipt_date),
  "%Y-%m-%d"
)]
raw_export[, include_in_analysis := ifelse(
  cycle %in% supported_cycles & !is.na(included_summary) & included_summary,
  "Yes",
  "No"
)]
raw_export[, inclusion_reason := ifelse(
  !cycle %in% supported_cycles,
  "Outside requested contribution-date cycles",
  ifelse(
    include_in_analysis == "Yes",
  "Included",
  ifelse(
    !is.na(recipient_category) & recipient_category %in% c("527", "501c"),
    "Excluded recipient class from summaries",
    ifelse(
      duplicate_memo == 1L,
      paste0("Memo duplicate: ", duplicate_reason, "; retained source ", duplicate_of_sub_id),
      ifelse(
        nzchar(trim_text(excluded_reason)),
        trim_text(excluded_reason),
        paste0("Company match: ", trim_text(company_match_status))
      )
    )
  ))
)]
raw_export[fec_receipt_role_excluded(receipt_role),
           inclusion_reason := paste0("Receipt role: ", receipt_role)]
# Counted, but the receipt role could not be resolved from the filing. The row
# ships with the money in the total and the caveat attached, so a reader can
# filter on it (2026-09-17 ruling: contradictions, not ambiguity).
raw_export[, receipt_role_unverified := fec_receipt_role_unverified(receipt_role)]
raw_export[, receipt_role_reason := fec_receipt_role_reason(receipt_role)]
raw_export[, receipt_role_reason := export_memo_duplicate_reason(receipt_role_reason, duplicate_memo, duplicate_reason)]
raw_export[, company_attribution_reason := ifelse(company_attribution_status == "accepted", "",
  paste0(company_attribution_status, "; original matcher: ", company_match_reason))]
raw_export[include_in_analysis == "Yes" & company_attribution_status != "accepted",
           inclusion_reason := paste0("Included provisionally: ", company_attribution_reason)]
raw_export[include_in_analysis == "No" & company_attribution_status == "contradicted_by_reported_organization" &
             !fec_receipt_role_excluded(receipt_role),
           inclusion_reason := paste0("Attribution contradicted by reported organization: ",
             ifelse(toupper(trim_text(entity_type)) == "IND", contributor_employer, contributor_name))]
raw_export[, cycle_alignment := ifelse(
  cycle == source_cycle,
  "OK",
  ifelse(
    cycle %in% supported_cycles,
    "Reassigned by contribution date",
    "Outside requested cycles"
  )
)]
raw_export[is.na(party) | !nzchar(party), party := "Other / Unclassified"]
raw_export[is.na(recipient_category), recipient_category := ""]
raw_export[is.na(category_source), category_source := ""]
raw_export[is.na(party_source), party_source := ""]
raw_export[is.na(employee_tier), employee_tier := ""]
raw_export[is.na(person_key), person_key := ""]
raw_export[is.na(identity_basis), identity_basis := ""]

raw_export <- raw_export[, .(
  source_id = sub_id,
  cycle,
  cycle_label,
  source_cycle,
  cycle_alignment,
  company = company_display,
  contributor = trim_text(contributor_name),
  employee_tier,
  amount,
  party,
  recipient_category,
  category_source = trim_text(category_source),
  party_source = trim_text(party_source),
  recipient = trim_text(committee_name),
  committee_id = toupper(trim_text(committee_id)),
  committee_type = toupper(trim_text(recipient_committee_type)),
  committee_designation = toupper(trim_text(recipient_committee_designation)),
  recipient_key = ifelse(
    nzchar(toupper(trim_text(committee_id))),
    paste0("FEC:", toupper(trim_text(committee_id))),
    paste0("NAME:", normalize_variant_key(committee_name))
  ),
  date,
  employer = trim_text(contributor_employer),
  occupation = trim_text(contributor_occupation),
  street_1 = trim_text(contributor_street_1),
  street_2 = trim_text(contributor_street_2),
  city = trim_text(contributor_city),
  state = trim_text(contributor_state),
  zipcode = trim_text(contributor_zip),
  record_type = ifelse(is.na(record_type), trim_text(record_type_raw), record_type),
  source_table = trim_text(source_table),
  funding_source_type = fec_funding_source(entity_type),
  funding_direction = ifelse(recipient_category == "Company PAC", "Into company PAC", "To named recipient"),
  company_match_status = trim_text(company_match_status),
  company_match_reason = trim_text(company_match_reason),
  company_attribution_status,
  company_attribution_reason,
  matched_alias = trim_text(matched_variant),
  include_in_analysis,
  inclusion_reason,
  receipt_role,
  receipt_role_unverified,
  receipt_role_reason,
  receipt_type_full = trim_text(receipt_type_full),
  filing_form = trim_text(filing_form),
  line_number = trim_text(line_number),
  memo_text = trim_text(memo_text),
  transaction_id = trim_text(transaction_id),
  back_reference_transaction_id = trim_text(back_reference_transaction_id),
  source_url = trim_text(pdf_url),
  person_key,
  identity_basis,
  is_memo_leg = as.integer(is_memo_leg),
  is_conduit_leg = as.integer(is_conduit_leg),
  superseded_by_amendment = as.integer(superseded_by_amendment),
  duplicate_filing_version = as.integer(duplicate_filing_version),
  duplicate_memo = as.integer(duplicate_memo)
  , duplicate_of_sub_id = trim_text(duplicate_of_sub_id)
  , duplicate_reason = trim_text(duplicate_reason)
)]
raw_export[, sort_date := as.integer(as.Date(date))]
data.table::setorder(raw_export, cycle, -sort_date, contributor, source_id)
raw_export[, sort_date := NULL]
export_phase("raw_export_built", nrow(raw_export))

if (!nzchar(beneficiary_dir)) {
  stop(
    "FEC beneficiary directory is required for candidate and downstream resolution.",
    call. = FALSE
  )
}

recipient_universe <- raw_export[
  cycle %in% supported_cycles & nzchar(committee_id),
  .(
    committee_name = fec_mode_text(recipient),
    recipient_category = fec_mode_text(recipient_category),
    preliminary_crypto_funding = sum(
      ifelse(include_in_analysis == "Yes", amount, 0),
      na.rm = TRUE
    )
  ),
  by = .(cycle, committee_id)
]
candidate_master <- fec_read_candidate_master(beneficiary_dir, supported_cycles)
candidate_links <- fec_read_candidate_committee_links(
  beneficiary_dir,
  supported_cycles
)
leadership_pacs <- fec_read_leadership_pacs(beneficiary_dir, supported_cycles)
export_phase("beneficiary_masters_loaded")
direct_candidate_keys <- unique(paste(
  candidate_links$cycle[candidate_links$designation %in% c("P", "A")],
  candidate_links$committee_id[candidate_links$designation %in% c("P", "A")],
  sep = "|"
))
downstream_universe <- recipient_universe[
  !paste(cycle, committee_id, sep = "|") %in% direct_candidate_keys
]
# Collect routes needed by ANY company view. A different company's refunds can
# make the industry net nonpositive without invalidating this company's route.
positive_company_roots <- raw_export[include_in_analysis == "Yes" & nzchar(committee_id),
  .(net = sum(amount)), by = .(cycle, company, committee_id)][net > 0]
downstream_universe[, traversal_seed := paste(cycle, committee_id, sep = "|") %in%
  paste(positive_company_roots$cycle, positive_company_roots$committee_id, sep = "|")]
# F15 (Paul approved 2026-09-26): each company view is dated from its own roots.
transfer_context <- fec_transfer_context(beneficiary_dir, downstream_universe, candidate_links, supported_cycles,
  views = positive_company_roots[, .(cycle, view = "Company", company, committee_id)])
downstream_activity <- fec_read_downstream_activity(
  beneficiary_dir,
  transfer_context$universe,
  candidate_master,
  supported_cycles
)
export_phase("downstream_activity_loaded", nrow(downstream_activity))
committee_map <- fec_build_committee_map(
  recipient_universe,
  fec_committee_master,
  candidate_master,
  candidate_links,
  leadership_pacs,
  downstream_activity
)
export_phase("committee_map_built", nrow(committee_map))
beneficiary_tables <- fec_build_beneficiary_tables(
  raw_export,
  downstream_activity,
  transfer_context$edges,
  candidate_links,
  networks = transfer_context$networks,
  industry_universe = transfer_context$universe
)
export_phase("beneficiary_bridge_built", nrow(beneficiary_tables$bridge))
downstream_activity_export <- fec_aggregate_downstream_activity(
  downstream_activity,
  transfer_context$universe
)
export_phase(
  "downstream_activity_aggregated",
  nrow(downstream_activity_export)
)

candidate_link_coverage <- sum(nzchar(committee_map$direct_person))
downstream_coverage <- sum(nzchar(committee_map$top_downstream_person))

companies <- sort(unique(spending_report_company_labels(lookups$companies$company)))
cycles <- data.frame(
  cycle = c(2022L, 2024L, 2026L),
  label = c("2021-2022", "2023-2024", "2025-2026"),
  table_name = c("Cycle_2021_2022", "Cycle_2023_2024", "Cycle_2025_2026"),
  stringsAsFactors = FALSE
)
expected_cycle_totals <- setNames(
  rep(0, nrow(cycles)),
  cycles$label
)
calculated_cycle_totals <- raw_export[
  include_in_analysis == "Yes" & cycle %in% supported_cycles,
  .(total = sum(amount, na.rm = TRUE)),
  by = cycle_label
]
expected_cycle_totals[calculated_cycle_totals$cycle_label] <-
  calculated_cycle_totals$total
omitted_non_accepted_by_cycle <- setNames(
  rep(0L, nrow(cycles)),
  as.character(cycles$cycle)
)
# Retain the legacy metadata field at zero: no query-status rows are omitted.
raw_export <- mentat_add_destinations(raw_export, fec_committee_master, candidate_master, candidate_links)
workbook_raw_export <- raw_export[
  cycle %in% supported_cycles
]

# app/report_insights.py REFUSES a worksheet over 50,000 rows outright rather
# than truncating it, so the fine-grain sheets are capped HERE instead of being
# discovered unchartable after publication. The cap is overridable for tests and
# for a console whose limit changes; below 100 rows the fold would dominate the
# sheet and the detail would be meaningless.
detail_row_cap <- suppressWarnings(as.integer(
  Sys.getenv("MENTAT_DETAIL_ROW_CAP", "45000")
))
if (length(detail_row_cap) != 1L || is.na(detail_row_cap) ||
  detail_row_cap < 100L) {
  stop("MENTAT_DETAIL_ROW_CAP must be an integer of at least 100.", call. = FALSE)
}
detail_frames <- spending_detail_frames(workbook_raw_export, detail_row_cap)
export_phase(
  "detail_frames_built",
  detail_frames$stats$recipient_rows + detail_frames$stats$contributor_rows
)

# Guards run HERE because this is the only point where the roster, the complete
# unfiltered rows (every status, every cycle) and the supported cycles are all in
# scope. Downstream of this the payload is already filtered, so a company that
# was never collected is indistinguishable from one that gave nothing.
# All O(n) aggregates on data already in memory - see R/export_guards.R.

# The two independent category views G6 compares. They must be reached by
# different code paths or the check is the tautology it replaces:
#   view_a - what the workbook renders. Post-reshape, post-merge: category comes
#            from analysis_map joined onto raw_rows by sub_id, amount from
#            raw_rows. This is the path the per-cycle sheets' SUMIFS sum.
#   view_b - the classification frame itself. Category and amount sit on the
#            same row and were never joined, so nothing can misalign them.
# A sub_id join that multiplies rows, drops a match, or collides on a column
# suffix moves dollars between categories while leaving the cycle grand total
# untouched - the exact F6 signature (Candidate/Leadership PAC swapped in one
# cycle, offsetting to zero) that every total-level check passed straight over.
guard_category_view_a <- workbook_raw_export[
  include_in_analysis == "Yes",
  .(amount = sum(amount, na.rm = TRUE)),
  by = .(cycle_label, category = recipient_category)
]
guard_category_view_b <- analysis_rows[
  contribution_cycle(contribution_receipt_date) %in% supported_cycles &
    included_summary == TRUE,
  .(amount = sum(as.numeric(contribution_receipt_amount), na.rm = TRUE)),
  by = .(
    cycle_label = sprintf(
      "%d-%d",
      contribution_cycle(contribution_receipt_date) - 1L,
      contribution_cycle(contribution_receipt_date)
    ),
    category = recipient_category
  )
]

# The tidy staging frames are built HERE, before the guard run, for the same
# reason the guards themselves are: this is the last point at which every frame
# is in scope AND nothing has been filtered for presentation. Building them
# ahead of the guards is what lets mentat_guard_tidy_reconciliation refuse to
# publish a store whose fact tables disagree with the cycle totals the Overview
# prints - a check that is worthless after the store is already on disk.
#
# data_quality is filled in after the guards run, because the guard results ARE
# its R-side rows. Nothing else is deferred.
tidy_frames <- NULL
if (nzchar(tidy_stage_path)) {
  tidy_started_at <- unname(proc.time()[["elapsed"]])
  tidy_frames <- mentat_build_tidy_frames(
    raw_export = workbook_raw_export,
    committee_map = committee_map,
    committee_master = fec_committee_master,
    candidate_links = candidate_links,
    candidate_master = candidate_master,
    downstream_activity_export = downstream_activity_export,
    bridge = beneficiary_tables$bridge_all,
    state_destinations = mentat_state_destinations(workbook_raw_export),
    recipient_locations = unique(workbook_raw_export[
      , c("cycle", "committee_id", "recipient", mentat_destination_fields),
      with = FALSE
    ]),
    guard_results = NULL,
    # Leadership-PAC sponsor links exist only inside fec_build_committee_map's
    # internal mappings frame; it is carried out as an attribute so the
    # committee_sponsors table can hold them beside the FEC ccl rows.
    mappings = fec_committee_map_mappings(committee_map)
  )
  cat(sprintf(
    "  tidy staging frames built in %.2fs\n",
    unname(proc.time()[["elapsed"]]) - tidy_started_at
  ))
}

guard_progress <- data.table::as.data.table(DBI::dbGetQuery(connection,
  "SELECT * FROM scrape_progress WHERE variant_set LIKE 'P1_employer%' OR variant_set LIKE 'P2_variant%' OR variant_set LIKE 'P2_company_name%'"))
guard_progress[, destination := ifelse(grepl("^P2_company_name", variant_set), "corporate", "contributions")]
guard_api <- if (DBI::dbExistsTable(connection, "api_work_progress"))
  DBI::dbGetQuery(connection, "SELECT * FROM api_work_progress") else data.frame()
guard_views <- mentat_guard_progress_views(guard_progress, guard_api)
guard_started_at <- unname(proc.time()[["elapsed"]])
guard_results <- mentat_run_export_guards(
  list(
    mentat_guard_roster_coverage(companies, raw_export, supported_cycles),
    mentat_guard_cell_coverage(companies, raw_export, supported_cycles),
    mentat_guard_category_agreement(guard_category_view_a, guard_category_view_b),
    # The exporter opens the database SQLITE_RO, so init_schema's migration never
    # runs here: a database collected before zero_verified existed simply has no
    # such column, and selecting it by name would abort the whole export. Ask the
    # schema first and let the guard report the column's absence as "every zero
    # unverified", which is exactly what it means.
    # Reported-vs-stored, split by destination: the employer/variant passes fill
    # contributions, the company-as-contributor pass fills corporate_giving.
    mentat_guard_collection_completeness(
      guard_views$completeness,
      DBI::dbGetQuery(
        connection,
        paste(
          "SELECT company, two_year_transaction_period AS cycle,",
          "'contributions' AS destination, COUNT(*) AS rows FROM contributions",
          "GROUP BY company, two_year_transaction_period",
          "UNION ALL",
          "SELECT company, cycle, 'corporate' AS destination, COUNT(*) AS rows",
          "FROM corporate_giving GROUP BY company, cycle"
        )
      )
    ),
    mentat_guard_zero_provenance(guard_views$zero),
    guard_views$api_guard,
    mentat_guard_direction_integrity(beneficiary_tables$summary),
    mentat_guard_date_sanity(
      downstream_activity_export$first_activity_date, "downstream_activity"
    ),
    mentat_guard_date_sanity(
      downstream_activity_export$last_activity_date, "downstream_last_activity"
    ),
    # The two fine-grain sheets are NEW publication surfaces, not a view of an
    # existing one: nothing else in the workbook re-sums them, so a fold that
    # lost a group would ship a chartable sheet that silently understates the
    # cycle total printed one tab away.
    mentat_guard_detail_reconciliation(detail_frames, expected_cycle_totals),
    # The companion Data workbook is a SECOND publication of the same dollars,
    # regrouped four ways. Nothing downstream re-sums it against the Overview,
    # so a fact table that lost a group would ship a chartable sheet quietly
    # disagreeing with the figure printed in the other book. NULL when no
    # staging path was supplied - mentat_run_export_guards drops NULL entries,
    # so a five-argument invocation neither builds nor checks the store.
    if (nzchar(tidy_stage_path)) {
      mentat_guard_tidy_reconciliation(tidy_frames, expected_cycle_totals)
    } else {
      NULL
    },
    # Mirrors the category list the workbook template renders
    # (tools/crypto_workbook_builder.mjs masterCategories). Kept here so a
    # category the template shows but nothing ever populates is reported rather
    # than silently rendering $0.00, as Trade Association did in six books.
    # Conduit (ActBlue, WinRed) is rendered too, but an industry or cycle with no
    # earmark-platform receipts has a real zero there, so it is optional.
    mentat_guard_category_population(
      c("Super PAC", "Carey", "Conduit", "Other PAC", "Company PAC",
        "Leadership PAC", "Trade Association", "Candidate", "Party Committee",
        "JFC", "Unclassified"),
      workbook_raw_export$recipient_category,
      optional = "Conduit"
    )
  ),
  context = MENTAT_INDUSTRY
)
cat(sprintf(
  "  guards completed in %.2fs\n",
  unname(proc.time()[["elapsed"]]) - guard_started_at
))

payload <- list(
  metadata = list(
    destination_schema = 1L,
    transfer_coverage = transfer_context$coverage,
    # F12: what the date rule removed from, or flagged in, downstream activity.
    downstream_date_rule = attr(downstream_activity, "date_rule"),
    # F15: each view's own walk - its roots, carryover status and the committees it dated.
    view_date_rule = transfer_context$networks$census,
    industry = MENTAT_INDUSTRY,
    database_path = database_path,
    database_bytes = file.info(database_path)$size,
    raw_rows = nrow(raw_export),
    workbook_raw_rows = nrow(workbook_raw_export),
    cycle_table_rows = sum(raw_export$cycle %in% supported_cycles),
    cycle_exception_rows = sum(!raw_export$cycle %in% mentat_all_cycles),
    reassigned_by_date_rows = sum(
      raw_export$cycle %in% supported_cycles &
        raw_export$cycle != raw_export$source_cycle
    ),
    included_rows = sum(raw_export$include_in_analysis == "Yes"),
    official_party_rows = sum(grepl(
      "^fec_committee_master:",
      raw_export$party_source
    )),
    employee_groups = nrow(person_cycle),
    identity_review_groups = sum(grepl(
      "^Conservative split",
      person_cycle$identity_basis
    )),
    recipient_committee_cycle_groups = nrow(recipient_universe),
    candidate_linked_committee_cycle_groups = candidate_link_coverage,
    downstream_resolved_committee_cycle_groups = downstream_coverage,
    downstream_organization_committee_cycle_groups = nrow(downstream_universe),
    downstream_rows = nrow(downstream_activity),
    downstream_included_rows = sum(
      downstream_activity$include_in_total == "Yes",
      na.rm = TRUE
    ),
    downstream_superseded_rows = sum(
      downstream_activity$superseded_by_amendment == 1L,
      na.rm = TRUE
    ),
    downstream_memo_rows = sum(
      downstream_activity$memo_code == "X",
      na.rm = TRUE
    ),
    beneficiary_bridge_rows = nrow(beneficiary_tables$bridge),
    beneficiary_summary_rows = nrow(beneficiary_tables$summary),
    downstream_aggregate_rows = nrow(downstream_activity_export),
    expected_cycle_totals = as.list(expected_cycle_totals),
    detail_row_cap = detail_frames$stats$row_cap,
    recipient_detail_rows = detail_frames$stats$recipient_rows,
    recipient_detail_folded_rows = detail_frames$stats$recipient_folded_rows,
    contributor_detail_rows = detail_frames$stats$contributor_rows,
    contributor_detail_folded_rows = detail_frames$stats$contributor_folded_rows,
    omitted_non_accepted_by_cycle = as.list(
      omitted_non_accepted_by_cycle
    ),
    export_guards = lapply(seq_len(nrow(guard_results)), function(index) {
      list(
        id = as.character(guard_results$id[[index]]),
        severity = as.character(guard_results$severity[[index]]),
        passed = isTRUE(guard_results$passed[[index]]),
        detail = as.character(guard_results$detail[[index]]),
        metric = if (is.na(guard_results$metric[[index]])) {
          NULL
        } else {
          as.numeric(guard_results$metric[[index]])
        }
      )
    }),
    export_guard_warning_count = sum(
      !guard_results$passed & guard_results$severity == "warn"
    ),
    generated_at = format(Sys.time(), "%Y-%m-%dT%H:%M:%OS%z")
  ),
  cycles = cycles,
  companies = companies,
  raw_rows = workbook_raw_export,
  state_destinations = mentat_state_destinations(workbook_raw_export),
  recipient_locations = unique(workbook_raw_export[, c("cycle","committee_id","recipient",mentat_destination_fields), with=FALSE]),
  # Against every covered cycle, not the filtered one: under a per-cycle build
  # the other cycles' rows belong to their own build and are not exceptions.
  # Using supported_cycles here made a filtered Telecom export LARGER than the
  # unfiltered one (920MB vs 591MB) by re-adding both other cycles as exceptions.
  cycle_exceptions = raw_export[!cycle %in% mentat_all_cycles],
  # I() so jsonlite keeps these as ARRAYS. With auto_unbox = TRUE a length-1
  # vector serialises as a bare string, and the builder calls .join() on it:
  # "TypeError: payload.committee_master_sources.join is not a function". It only
  # bit once a per-cycle Telecom export narrowed the sources to a single URL.
  committee_master_sources = I(unique(fec_committee_master$source_url)),
  beneficiary_sources = I(unique(c(
    candidate_master$source_url,
    candidate_links$source_url,
    leadership_pacs$source_url,
    downstream_activity$source_url
  ))),
  # Their own payload keys, never a view of raw_rows: prepare_workbook_payload.py
  # strips raw_rows to 18 fields that carry no geography, employer or occupation,
  # and formula-fact mode replaces raw_rows entirely.
  recipient_detail = detail_frames$recipient_detail,
  contributor_detail = detail_frames$contributor_detail,
  employees = person_cycle,
  committee_map = committee_map,
  transfer_edges = transfer_context$edges,
  candidate_committee_links = candidate_links,
  candidate_master = candidate_master,
  downstream_activity = downstream_activity_export,
  downstream_reach = beneficiary_tables$reach,
  downstream_measurement_ledger = beneficiary_tables$measurement_ledger,
  downstream_sources = data.table::rbindlist(lapply(supported_cycles, function(year) {
    paths <- c(file.path(beneficiary_dir, paste0("pas2", substr(year, 3, 4)), "itpas2.txt"),
               file.path(beneficiary_dir, paste0("oth", substr(year, 3, 4)), "itoth.txt"),
               file.path(beneficiary_dir, paste0("oth", substr(year, 3, 4)), "transfer-info.json"),
               file.path(beneficiary_dir, paste0("oth", substr(year, 3, 4)), "transfer-edges.csv"))
    data.table::data.table(cycle = year, source = basename(paths), path = paths,
      sha256 = vapply(paths, function(path) if (file.exists(path))
        digest::digest(file = path, algo = "sha256") else "", character(1)),
      status = ifelse(file.exists(paths), "saved source; complete filing coverage not established", "missing source; not zero activity"),
      policy = paste0("PAS2 parser ", FEC_PAS2_PARSER_VERSION, "; ", FEC_PAS2_AMBIGUOUS_VERSION_POLICY,
        "; OTH v6 count_all; earliest arrival (F12), carryover onward; company views dated from their own roots (F15)"))
  })),
  beneficiary_bridge = beneficiary_tables$bridge,
  beneficiary_summary = beneficiary_tables$summary
)
export_phase("payload_assembled", nrow(workbook_raw_export))

# Tidy staging store. Written from the frames above, NOT from the JSON payload:
# tools/prepare_workbook_payload.py projects raw_rows onto 18 fields that carry
# no ZIP, city, state, employer or occupation, and formula-fact mode replaces
# raw_rows outright. Going through the payload would give a large industry a
# companion workbook silently narrower than a small one's.
if (nzchar(tidy_stage_path)) {
  tidy_frames$data_quality <- mentat_tidy_data_quality(guard_results)
  tidy_blank_columns <- unlist(lapply(names(tidy_frames), function(name) {
    frame <- tidy_frames[[name]]
    if (is.null(frame) || !nrow(frame)) return(character())
    empty <- names(frame)[vapply(frame, function(column) all(is.na(column)), logical(1))]
    if (!length(empty)) return(character())
    paste0(name, ".", empty)
  }), use.names = FALSE)
  mentat_write_tidy_stage(tidy_stage_path, tidy_frames, list(
    schema_version = mentat_tidy_schema_version,
    attribution_policy = "contradictions-only-v1",
    industry = MENTAT_INDUSTRY,
    industry_slug = gsub(
      "^_+|_+$", "", gsub("[^a-z0-9]+", "_", tolower(MENTAT_INDUSTRY))
    ),
    # Empty for an all-cycles build. The orchestrator sets MENTAT_CYCLE_FILTER
    # before the export for exactly this reason.
    cycle_label = trimws(Sys.getenv("MENTAT_CYCLE_FILTER", unset = "")),
    supported_cycles = as.integer(supported_cycles),
    cycle_labels = sprintf("%d-%d", supported_cycles - 1L, supported_cycles),
    database_path = database_path,
    # The database SHA-256 is the orchestrator's quiesced hash, not something R
    # can recompute without re-reading a multi-gigabyte file; it reaches the
    # writer as --database-sha256 and is recorded here as unknown.
    database_sha256 = NA_character_,
    generated_at = format(Sys.time(), "%Y-%m-%dT%H:%M:%OS%z"),
    exporter_version = paste0("md5:", unname(tools::md5sum(script_path))),
    expected_cycle_totals = as.list(expected_cycle_totals),
    accounting_note = paste(
      "Analysis Amount counts included itemized receipts at the named recipient",
      "and is 0 for memo, conduit, superseded, duplicate and out-of-cycle legs;",
      "Amount is the figure as filed. Neither is verified unique giving."
    ),
    blank_columns = if (length(tidy_blank_columns)) tidy_blank_columns else character()
  ))
  export_phase("tidy_stage_written", nrow(tidy_frames$contributions))
  cat(sprintf("  tidy staging store: %s\n", tidy_stage_path))
} else {
  cat("  tidy staging skipped: no staging path argument supplied.\n")
}

write_payload_json <- function(payload, path, chunk_rows = 10000L) {
  connection <- file(path, open = "wb")
  on.exit(close(connection), add = TRUE)
  write_text <- function(value) {
    writeBin(charToRaw(enc2utf8(as.character(value))), connection)
  }
  encode <- function(value, dataframe = "rows") {
    jsonlite::toJSON(
      value,
      dataframe = dataframe,
      auto_unbox = TRUE,
      digits = NA,
      na = "null",
      pretty = FALSE
    )
  }
  write_frame <- function(value) {
    row_count <- nrow(value)
    write_text("[")
    if (row_count) {
      starts <- seq.int(1L, row_count, by = as.integer(chunk_rows))
      for (chunk_index in seq_along(starts)) {
        start <- starts[[chunk_index]]
        finish <- min(start + as.integer(chunk_rows) - 1L, row_count)
        encoded <- as.character(encode(value[start:finish, , drop = FALSE]))
        inner <- substr(encoded, 2L, nchar(encoded) - 1L)
        if (chunk_index > 1L && nzchar(inner)) write_text(",")
        write_text(inner)
      }
    }
    write_text("]")
  }

  write_text("{")
  payload_names <- names(payload)
  for (index in seq_along(payload_names)) {
    name <- payload_names[[index]]
    if (index > 1L) write_text(",")
    write_text(encode(name))
    write_text(":")
    value <- payload[[name]]
    if (is.data.frame(value)) {
      write_frame(value)
    } else {
      write_text(encode(value))
    }
  }
  write_text("}")
  invisible(path)
}

dir.create(dirname(output_path), recursive = TRUE, showWarnings = FALSE)
write_payload_json(payload, output_path)
export_phase("json_written")
cat(sprintf(
  paste0(
    "%s workbook export: raw=%d included=%d employee_groups=%d ",
    "identity_review_groups=%d downstream=%d beneficiary_summary=%d output=%s\n"
  ),
  MENTAT_INDUSTRY,
  payload$metadata$raw_rows,
  payload$metadata$included_rows,
  payload$metadata$employee_groups,
  payload$metadata$identity_review_groups,
  payload$metadata$downstream_rows,
  payload$metadata$beneficiary_summary_rows,
  output_path
))

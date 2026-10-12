# Tidy staging store (schema_version "tidy-1").
#
# The published Overview workbook is a PRESENTATION artifact: merged banners, a
# header on row 5, five-column blocks that pivot channel and tier into columns,
# `;`-joined multi-value cells and ~347k structured-reference formulas whose
# values exist only as Excel's cached results. Tableau's "structure data for
# analysis" rules - and the console's own worksheet reader - want the opposite.
#
# This module writes the SAME numbers a second time, tall and typed, into a
# SQLite staging store that tools/build_tidy_workbook.py turns into the
# companion `<Industry> Industry Spending Data.xlsx`. It runs inside the
# exporter, between export_phase("payload_assembled") and write_payload_json,
# because that is the only point where every frame is still in scope AND the
# rows have not yet been through tools/prepare_workbook_payload.py, whose
# RAW_FIELDS projection drops geography, employer and occupation from large
# industries. A Banking build therefore gets full-width receipt rows here
# regardless of MENTAT_COMPACT_RAW.
#
# Contract rules this file implements (S\STAGING_CONTRACT.md section 1):
#   * TEXT for everything that is not a number, REAL for money, INTEGER for
#     counts and flags.
#   * Dates are ISO `YYYY-MM-DD` TEXT. R Date class is NEVER written: RSQLite
#     stores a Date as a REAL day number, which the Python writer would then
#     render as 19000-odd in a date-formatted cell.
#   * Flags are INTEGER 0/1, never "Yes"/"No".
#   * Missing is SQL NULL. The exporter uses "" as its in-frame blank and 0 as a
#     sentinel in places; both become NULL here so Tableau and openpyxl see a
#     blank rather than a category called "".
#   * Identifiers stay TEXT (sub_id is 19 digits and does not survive a double).
#
# Nothing in this file may name a company, committee or cycle: it runs for every
# industry the pipeline is pointed at.

mentat_tidy_schema_version <- "tidy-1"
mentat_tidy_module_version <- "tidy_stage.R/1"

# ---------------------------------------------------------------------------
# Column specifications. These are the contract, in code.
#
# The spec drives dbWriteTable's field.types, so a table with ZERO rows still
# lands with the right column names and affinities instead of whatever R types
# an empty data.frame happened to carry. It also fixes column ORDER, which is
# the sheet column order the writer emits.
# ---------------------------------------------------------------------------

MENTAT_TIDY_TABLE_SPECS <- list(
  metadata = c(key = "TEXT", value = "TEXT"),
  contributions = c(
    source_id = "TEXT", transaction_id = "TEXT",
    back_reference_transaction_id = "TEXT", cycle_label = "TEXT",
    cycle = "INTEGER", source_cycle = "INTEGER", cycle_alignment = "TEXT",
    company = "TEXT", contributor = "TEXT", person_key = "TEXT",
    identity_basis = "TEXT", employee_tier = "TEXT", employer = "TEXT",
    occupation = "TEXT", street_1 = "TEXT", street_2 = "TEXT",
    city = "TEXT", state = "TEXT", zip5 = "TEXT",
    zipcode = "TEXT", date = "TEXT", amount = "REAL",
    analysis_amount = "REAL", include_in_analysis = "INTEGER",
    inclusion_reason = "TEXT", company_match_status = "TEXT",
    company_match_reason = "TEXT", company_attribution_status = "TEXT", company_attribution_reason = "TEXT",
    receipt_role_unverified = "INTEGER", receipt_role_reason = "TEXT", matched_alias = "TEXT", recipient = "TEXT",
    committee_id = "TEXT", recipient_key = "TEXT", recipient_category = "TEXT",
    category_source = "TEXT", party = "TEXT", party_source = "TEXT",
    committee_type_code = "TEXT", committee_type = "TEXT",
    committee_designation_code = "TEXT", committee_designation = "TEXT",
    record_type = "TEXT", source_table = "TEXT", funding_source_type = "TEXT",
    funding_direction = "TEXT", receipt_role = "TEXT",
    receipt_type_full = "TEXT", filing_form = "TEXT", line_number = "TEXT",
    memo_text = "TEXT", is_memo_leg = "INTEGER", is_conduit_leg = "INTEGER",
    superseded_by_amendment = "INTEGER", duplicate_filing_version = "INTEGER",
    duplicate_memo = "INTEGER", duplicate_of_sub_id = "TEXT",
    duplicate_reason = "TEXT", recipient_state = "TEXT",
    represented_state = "TEXT", source_url = "TEXT"
  ),
  giving_facts = c(
    cycle_label = "TEXT", cycle = "INTEGER", company = "TEXT",
    employee_tier = "TEXT", recipient_category = "TEXT", party = "TEXT",
    giving = "REAL", gift_count = "INTEGER", distinct_contributors = "INTEGER",
    distinct_recipients = "INTEGER"
  ),
  recipient_facts = c(
    cycle_label = "TEXT", cycle = "INTEGER", company = "TEXT",
    committee_id = "TEXT", employee_tier = "TEXT", recipient = "TEXT",
    recipient_category = "TEXT", category_source = "TEXT", party = "TEXT",
    party_source = "TEXT", committee_type_code = "TEXT",
    committee_type = "TEXT", committee_designation_code = "TEXT",
    committee_designation = "TEXT", sponsor_candidate_id = "TEXT",
    sponsor_candidate = "TEXT", sponsor_office = "TEXT",
    sponsor_state = "TEXT", sponsor_district = "TEXT",
    sponsor_count = "INTEGER", connected_organization = "TEXT",
    committee_relationship = "TEXT", ownership_status = "TEXT",
    giving = "REAL", gift_count = "INTEGER", distinct_contributors = "INTEGER",
    first_gift_date = "TEXT", last_gift_date = "TEXT",
    fec_committee_url = "TEXT"
  ),
  contributor_facts = c(
    cycle_label = "TEXT", cycle = "INTEGER", company = "TEXT",
    person_key = "TEXT", party = "TEXT", contributor = "TEXT",
    employee_tier = "TEXT", identity_basis = "TEXT", city = "TEXT",
    state = "TEXT", zip5 = "TEXT", employer = "TEXT", occupation = "TEXT",
    distinct_addresses = "INTEGER", giving = "REAL", gift_count = "INTEGER"
  ),
  location_facts = c(
    cycle_label = "TEXT", cycle = "INTEGER", company = "TEXT", state = "TEXT",
    city = "TEXT", zip5 = "TEXT", giving = "REAL", gift_count = "INTEGER",
    distinct_contributors = "INTEGER"
  ),
  committees = c(
    committee_id = "TEXT", cycle_label = "TEXT", cycle = "INTEGER",
    committee_name = "TEXT", committee_type_code = "TEXT",
    committee_type = "TEXT", committee_designation_code = "TEXT",
    committee_designation = "TEXT", party = "TEXT",
    party_affiliation = "TEXT", committee_state = "TEXT",
    recipient_category = "TEXT", category_source = "TEXT",
    party_source = "TEXT", connected_organization = "TEXT",
    ownership_status = "TEXT", committee_relationship = "TEXT",
    mapping_source = "TEXT", sponsor_count = "INTEGER",
    top_downstream_candidate_id = "TEXT", top_downstream_candidate = "TEXT",
    top_downstream_activity = "REAL", fec_committee_url = "TEXT"
  ),
  committee_sponsors = c(
    committee_id = "TEXT", candidate_id = "TEXT", cycle_label = "TEXT",
    cycle = "INTEGER", relationship = "TEXT", link_source = "TEXT",
    candidate = "TEXT", source_url = "TEXT"
  ),
  candidates = c(
    candidate_id = "TEXT", candidate = "TEXT", party = "TEXT",
    party_affiliation = "TEXT", office_code = "TEXT", office = "TEXT",
    state = "TEXT", district = "TEXT", incumbent_status = "TEXT",
    principal_committee_id = "TEXT", fec_candidate_url = "TEXT",
    source_url = "TEXT"
  ),
  downstream_activity = c(
    cycle_label = "TEXT", cycle = "INTEGER", source_committee = "TEXT",
    source_committee_id = "TEXT", source_recipient_category = "TEXT",
    candidate = "TEXT", candidate_id = "TEXT", candidate_party = "TEXT",
    office = "TEXT", state = "TEXT", district = "TEXT",
    activity_type = "TEXT", support_oppose = "TEXT",
    transaction_type = "TEXT", first_activity_date = "TEXT",
    last_activity_date = "TEXT", source_rows = "INTEGER",
    included_rows = "INTEGER", excluded_rows = "INTEGER",
    superseded_rows = "INTEGER", memo_rows = "INTEGER",
    duplicate_rows = "INTEGER",
    # Retained observations with unresolved filing relationships are provisional.
    unverified_version_rows = "INTEGER", unresolved_version_rows = "INTEGER",
    unresolved_version_amount = "REAL", repeated_itemization_rows = "INTEGER",
    reported_amount = "REAL",
    excluded_amount = "REAL", net_included_amount = "REAL",
    fec_committee_url = "TEXT", fec_candidate_url = "TEXT",
    source_url = "TEXT", transfer_records = "TEXT", filing_records = "TEXT"
  ),
  beneficiary_bridge = c(
    bridge_key = "TEXT", cycle_label = "TEXT", cycle = "INTEGER",
    company = "TEXT", source_committee = "TEXT",
    source_committee_id = "TEXT", source_recipient_category = "TEXT",
    candidate = "TEXT", candidate_id = "TEXT", candidate_party = "TEXT",
    office = "TEXT", state = "TEXT", district = "TEXT",
    transaction_count = "INTEGER", first_activity_date = "TEXT",
    last_activity_date = "TEXT", downstream_activity = "REAL",
    support_amount = "REAL", oppose_amount = "REAL", other_activity_amount = "REAL",
    associated_funding = "REAL"
    # plus one INTEGER `has_<token>` flag column per observed relationship and
    # direction token; appended at build time because the vocabulary is data.
  ),
  state_destinations = c(
    cycle_label = "TEXT", cycle = "INTEGER", company = "TEXT", party = "TEXT",
    location_view = "TEXT", destination = "TEXT", accounting_basis = "TEXT",
    net_amount = "REAL", receipt_count = "INTEGER"
  ),
  recipient_locations = c(
    cycle_label = "TEXT", cycle = "INTEGER", committee_id = "TEXT",
    recipient = "TEXT", recipient_state = "TEXT", represented_state = "TEXT",
    recipient_location_status = "TEXT", represented_location_status = "TEXT"
  ),
  data_quality = c(
    check_id = "TEXT", source = "TEXT", severity = "TEXT",
    passed = "INTEGER", detail = "TEXT", metric = "TEXT"
  )
)

# Unique-row keys, per contract section 2. Declared here rather than inline in
# the guard so the key and the column spec sit together and an amendment is one
# edit in one place.
#
# downstream_activity carries `candidate` in its key by AMENDMENT D2
# (2026-09-15). The six-column key the contract shipped with collided on 92
# groups of the real Credit Union export: a single candidate_id reaches the
# aggregate under several filed name spellings, and
# fec_aggregate_downstream_activity groups by candidate_name as well as
# candidate_id, so two rows differing only in spelling are two legitimate rows.
# Keying without `candidate` would make the guard refuse a correct export.
MENTAT_TIDY_TABLE_SPECS$beneficiary_activity <- MENTAT_TIDY_TABLE_SPECS$beneficiary_bridge
MENTAT_TIDY_TABLE_KEYS <- list(
  contributions = "source_id",
  giving_facts = c("cycle_label", "company", "employee_tier",
                   "recipient_category", "party"),
  recipient_facts = c("cycle_label", "company", "committee_id", "employee_tier"),
  contributor_facts = c("cycle_label", "company", "person_key", "party"),
  location_facts = c("cycle_label", "company", "state", "city", "zip5"),
  committees = c("committee_id", "cycle_label"),
  committee_sponsors = c("committee_id", "candidate_id", "cycle_label",
                         "link_source"),
  candidates = "candidate_id",
  downstream_activity = c("cycle_label", "source_committee_id", "candidate_id",
                          "candidate", "activity_type", "support_oppose",
                          "transaction_type"),
  beneficiary_bridge = "bridge_key",
  beneficiary_activity = c("cycle_label", "source_committee_id", "candidate_id")
)

# ---------------------------------------------------------------------------
# Coercion helpers
# ---------------------------------------------------------------------------

# "" and whitespace-only become NA so the store holds SQL NULL. Factors are
# flattened first: a factor written by dbWriteTable lands as its integer level.
mentat_tidy_text <- function(values) {
  if (is.factor(values)) values <- as.character(values)
  values <- as.character(values)
  values <- trimws(values)
  values[!is.na(values) & !nzchar(values)] <- NA_character_
  values
}

# Identifiers reach this module already normalised (the exporter uppercases
# committee and candidate ids at projection). They are kept as TEXT and never
# coerced: sub_id is 19 digits and does not survive a double.
mentat_tidy_id <- function(values) {
  mentat_tidy_text(values)
}

# Dates travel as ISO TEXT. Anything that is not exactly YYYY-MM-DD becomes NULL
# rather than a guess: a half-parsed date charted as a category is worse than a
# blank, and the exporter already normalises real dates upstream.
mentat_tidy_date <- function(values) {
  if (inherits(values, "Date")) {
    values <- format(values, "%Y-%m-%d")
  } else if (inherits(values, "POSIXt")) {
    values <- format(values, "%Y-%m-%d")
  }
  values <- mentat_tidy_text(values)
  values[!is.na(values) & !grepl("^[0-9]{4}-[0-9]{2}-[0-9]{2}$", values)] <- NA_character_
  values
}

mentat_tidy_money <- function(values) {
  values <- suppressWarnings(as.numeric(values))
  values[!is.na(values) & !is.finite(values)] <- NA_real_
  values
}

mentat_tidy_count <- function(values) {
  values <- suppressWarnings(as.numeric(values))
  values[!is.na(values) & !is.finite(values)] <- NA_real_
  as.integer(round(values))
}

# Accepts logicals, 0/1 integers and the exporter's "Yes"/"No" text.
mentat_tidy_flag <- function(values) {
  if (is.logical(values)) return(as.integer(values))
  if (is.numeric(values)) {
    result <- as.integer(values != 0)
    result[is.na(values)] <- NA_integer_
    return(result)
  }
  text <- toupper(mentat_tidy_text(values))
  result <- rep(NA_integer_, length(text))
  result[text %in% c("YES", "TRUE", "T", "Y", "1")] <- 1L
  result[text %in% c("NO", "FALSE", "F", "N", "0")] <- 0L
  result
}

# A frame the exporter built under a different code vintage can be missing a
# column outright. Failing the whole export on that is worse than publishing the
# column as NULL and letting the writer list it under "blank columns", so every
# read of an upstream frame goes through here.
mentat_tidy_column <- function(frame, name, rows = NULL) {
  if (is.null(rows)) rows <- if (is.null(frame)) 0L else nrow(frame)
  if (!is.null(frame) && name %in% names(frame)) return(frame[[name]])
  rep(NA_character_, rows)
}

mentat_tidy_cycle_label <- function(cycle) {
  cycle <- suppressWarnings(as.integer(cycle))
  label <- sprintf("%d-%d", cycle - 1L, cycle)
  label[is.na(cycle)] <- NA_character_
  label
}

mentat_tidy_committee_url <- function(committee_id) {
  id <- mentat_tidy_id(committee_id)
  url <- paste0("https://www.fec.gov/data/committee/", id, "/")
  url[is.na(id)] <- NA_character_
  url
}

mentat_tidy_candidate_url <- function(candidate_id) {
  id <- mentat_tidy_id(candidate_id)
  url <- paste0("https://www.fec.gov/data/candidate/", id, "/")
  url[is.na(id)] <- NA_character_
  url
}

# `;`-joined cells are exactly what the tidy layout exists to remove. Where the
# collapse is a genuine one-to-many the row becomes a bridge row; where it is an
# attribute of a single entity (committee_map$relationship, $mapping_source) the
# honest tidy answer is the single value when there is one and "Multiple" when
# the upstream join found several - never the joined text, which Tableau would
# treat as its own category.
mentat_tidy_single_or_multiple <- function(values) {
  values <- mentat_tidy_text(values)
  ifelse(is.na(values), NA_character_,
         ifelse(grepl(";", values, fixed = TRUE), "Multiple", values))
}

# Modal value, alphabetical tie-break: R/fec_beneficiary.R:15-24 already
# implements exactly that (sort(unique(value)) then which.max on the counts,
# which returns the FIRST maximum in sorted order). Reused rather than
# reimplemented so the staging store and the Overview agree on every modal cell.
mentat_tidy_mode <- function(values) {
  value <- fec_mode_text(values)
  if (!length(value) || is.na(value) || !nzchar(value)) return(NA_character_)
  value
}

# ---------------------------------------------------------------------------
# Frame builders (contract section 2)
# ---------------------------------------------------------------------------

# 2.2 contributions. Universe: workbook_raw_export - accepted company match in a
# supported cycle, INCLUDED AND EXCLUDED. The excluded legs are what make
# `Analysis Amount` meaningful; dropping them would leave a reader unable to see
# why the raw and analysed totals differ.
mentat_tidy_contributions <- function(raw_export) {
  require_package("data.table")
  rows <- data.table::as.data.table(raw_export)
  count <- nrow(rows)
  include <- mentat_tidy_flag(mentat_tidy_column(rows, "include_in_analysis", count))
  amount <- mentat_tidy_money(mentat_tidy_column(rows, "amount", count))
  if (any(!is.finite(amount))) stop("A source amount is missing or invalid; tidy staging cannot infer zero.", call. = FALSE)
  zipcode <- mentat_tidy_text(mentat_tidy_column(rows, "zipcode", count))
  # R/report.R:1885-1886. ZIP+4, a 9-digit string and a plain 5-digit code share
  # their leading five characters; anything else is NULL rather than a truncated
  # guess, so a geography chart cannot invent a ZIP that was never filed.
  zip5 <- substr(ifelse(is.na(zipcode), "", zipcode), 1L, 5L)
  zip5[!grepl("^[0-9]{5}$", zip5)] <- NA_character_
  type_code <- mentat_tidy_text(mentat_tidy_column(rows, "committee_type", count))
  designation_code <- mentat_tidy_text(
    mentat_tidy_column(rows, "committee_designation", count)
  )
  result <- data.table::data.table(
    source_id = mentat_tidy_id(mentat_tidy_column(rows, "source_id", count)),
    transaction_id = mentat_tidy_id(mentat_tidy_column(rows, "transaction_id", count)),
    back_reference_transaction_id = mentat_tidy_id(
      mentat_tidy_column(rows, "back_reference_transaction_id", count)
    ),
    cycle_label = mentat_tidy_text(mentat_tidy_column(rows, "cycle_label", count)),
    cycle = mentat_tidy_count(mentat_tidy_column(rows, "cycle", count)),
    source_cycle = mentat_tidy_count(mentat_tidy_column(rows, "source_cycle", count)),
    cycle_alignment = mentat_tidy_text(mentat_tidy_column(rows, "cycle_alignment", count)),
    company = mentat_tidy_text(mentat_tidy_column(rows, "company", count)),
    contributor = mentat_tidy_text(mentat_tidy_column(rows, "contributor", count)),
    person_key = mentat_tidy_text(mentat_tidy_column(rows, "person_key", count)),
    identity_basis = mentat_tidy_text(mentat_tidy_column(rows, "identity_basis", count)),
    employee_tier = mentat_tidy_text(mentat_tidy_column(rows, "employee_tier", count)),
    employer = mentat_tidy_text(mentat_tidy_column(rows, "employer", count)),
    occupation = mentat_tidy_text(mentat_tidy_column(rows, "occupation", count)),
    street_1 = mentat_tidy_text(mentat_tidy_column(rows, "street_1", count)),
    street_2 = mentat_tidy_text(mentat_tidy_column(rows, "street_2", count)),
    city = mentat_tidy_text(mentat_tidy_column(rows, "city", count)),
    state = mentat_tidy_text(mentat_tidy_column(rows, "state", count)),
    zip5 = zip5,
    zipcode = zipcode,
    date = mentat_tidy_date(mentat_tidy_column(rows, "date", count)),
    amount = amount,
    # exporter :572 semantics, computed here rather than read back from Excel:
    # the analysis column is 0 (not blank) for an excluded row so a SUM over the
    # column is the published included total with no filter applied.
    analysis_amount = ifelse(!is.na(include) & include == 1L, amount, 0),
    include_in_analysis = include,
    inclusion_reason = mentat_tidy_text(mentat_tidy_column(rows, "inclusion_reason", count)),
    company_match_status = mentat_tidy_text(
      mentat_tidy_column(rows, "company_match_status", count)
    ),
    company_match_reason = mentat_tidy_text(
      mentat_tidy_column(rows, "company_match_reason", count)
    ),
    company_attribution_status = mentat_tidy_text(mentat_tidy_column(rows, "company_attribution_status", count)),
    company_attribution_reason = mentat_tidy_text(mentat_tidy_column(rows, "company_attribution_reason", count)),
    receipt_role_unverified = mentat_tidy_flag(mentat_tidy_column(rows, "receipt_role_unverified", count)),
    receipt_role_reason = mentat_tidy_text(mentat_tidy_column(rows, "receipt_role_reason", count)),
    matched_alias = mentat_tidy_text(mentat_tidy_column(rows, "matched_alias", count)),
    recipient = mentat_tidy_text(mentat_tidy_column(rows, "recipient", count)),
    committee_id = mentat_tidy_id(mentat_tidy_column(rows, "committee_id", count)),
    recipient_key = mentat_tidy_text(mentat_tidy_column(rows, "recipient_key", count)),
    recipient_category = mentat_tidy_text(
      mentat_tidy_column(rows, "recipient_category", count)
    ),
    category_source = mentat_tidy_text(mentat_tidy_column(rows, "category_source", count)),
    party = mentat_tidy_text(mentat_tidy_column(rows, "party", count)),
    party_source = mentat_tidy_text(mentat_tidy_column(rows, "party_source", count)),
    committee_type_code = type_code,
    committee_type = mentat_tidy_text(
      mentat_fec_code_label(type_code, MENTAT_FEC_COMMITTEE_TYPE_LABELS)
    ),
    committee_designation_code = designation_code,
    committee_designation = mentat_tidy_text(
      mentat_fec_code_label(designation_code, MENTAT_FEC_COMMITTEE_DESIGNATION_LABELS)
    ),
    record_type = mentat_tidy_text(mentat_tidy_column(rows, "record_type", count)),
    source_table = mentat_tidy_text(mentat_tidy_column(rows, "source_table", count)),
    funding_source_type = mentat_tidy_text(
      mentat_tidy_column(rows, "funding_source_type", count)
    ),
    funding_direction = mentat_tidy_text(mentat_tidy_column(rows, "funding_direction", count)),
    receipt_role = mentat_tidy_text(mentat_tidy_column(rows, "receipt_role", count)),
    receipt_type_full = mentat_tidy_text(mentat_tidy_column(rows, "receipt_type_full", count)),
    filing_form = mentat_tidy_text(mentat_tidy_column(rows, "filing_form", count)),
    line_number = mentat_tidy_id(mentat_tidy_column(rows, "line_number", count)),
    memo_text = mentat_tidy_text(mentat_tidy_column(rows, "memo_text", count)),
    is_memo_leg = mentat_tidy_flag(mentat_tidy_column(rows, "is_memo_leg", count)),
    is_conduit_leg = mentat_tidy_flag(mentat_tidy_column(rows, "is_conduit_leg", count)),
    superseded_by_amendment = mentat_tidy_flag(
      mentat_tidy_column(rows, "superseded_by_amendment", count)
    ),
    duplicate_filing_version = mentat_tidy_flag(
      mentat_tidy_column(rows, "duplicate_filing_version", count)
    ),
    duplicate_memo = mentat_tidy_flag(mentat_tidy_column(rows, "duplicate_memo", count)),
    duplicate_of_sub_id = mentat_tidy_id(
      mentat_tidy_column(rows, "duplicate_of_sub_id", count)
    ),
    duplicate_reason = mentat_tidy_text(mentat_tidy_column(rows, "duplicate_reason", count)),
    recipient_state = mentat_tidy_text(mentat_tidy_column(rows, "recipient_state", count)),
    represented_state = mentat_tidy_text(mentat_tidy_column(rows, "represented_state", count)),
    source_url = mentat_tidy_text(mentat_tidy_column(rows, "source_url", count))
  )
  data.table::setorder(result, cycle, company, date, source_id, na.last = TRUE)
  result[]
}

# Convenience view: the included rows every fact table aggregates. Derived from
# the CONTRIBUTIONS frame, not from raw_export a second time, so the facts and
# the receipt sheet cannot drift apart under a later change to the projection.
mentat_tidy_included <- function(contributions) {
  contributions[!is.na(include_in_analysis) & include_in_analysis == 1L]
}

# 2.3 giving_facts
mentat_tidy_giving_facts <- function(contributions) {
  included <- mentat_tidy_included(contributions)
  if (!nrow(included)) return(mentat_tidy_empty("giving_facts"))
  result <- included[, .(
    giving = sum(analysis_amount, na.rm = TRUE),
    gift_count = .N,
    distinct_contributors = data.table::uniqueN(person_key[!is.na(person_key)]),
    distinct_recipients = data.table::uniqueN(committee_id[!is.na(committee_id)])
  ), by = .(cycle_label, cycle, company, employee_tier, recipient_category, party)]
  result[, gift_count := as.integer(gift_count)]
  result[, distinct_contributors := as.integer(distinct_contributors)]
  result[, distinct_recipients := as.integer(distinct_recipients)]
  data.table::setcolorder(result, names(MENTAT_TIDY_TABLE_SPECS$giving_facts))
  data.table::setorder(result, cycle_label, company, employee_tier,
                       recipient_category, party, na.last = TRUE)
  result[]
}

# 2.5 contributor_facts. Entity (corporate) rows have no person_key and are
# EXCLUDED here by design - they stay in giving_facts under their tier, so no
# dollar disappears, but a person-grain sheet that carried them would report a
# company as a contributor.
mentat_tidy_contributor_facts <- function(contributions) {
  included <- mentat_tidy_included(contributions)
  included <- included[!is.na(person_key)]
  if (!nrow(included)) return(mentat_tidy_empty("contributor_facts"))
  # Distinct addresses are counted over the person's whole cycle, across every
  # party, because an address is an attribute of the person and not of the
  # recipient they gave to. Computed before the party split for that reason.
  address <- included[, .(
    distinct_addresses = data.table::uniqueN(paste(
      ifelse(is.na(city), "", city),
      ifelse(is.na(state), "", state),
      ifelse(is.na(zip5), "", zip5),
      sep = "|"
    ))
  ), by = .(cycle_label, company, person_key)]
  result <- included[, .(
    contributor = mentat_tidy_mode(contributor),
    employee_tier = mentat_tidy_mode(employee_tier),
    identity_basis = mentat_tidy_mode(identity_basis),
    city = mentat_tidy_mode(city),
    state = mentat_tidy_mode(state),
    zip5 = mentat_tidy_mode(zip5),
    employer = mentat_tidy_mode(employer),
    occupation = mentat_tidy_mode(occupation),
    giving = sum(analysis_amount, na.rm = TRUE),
    gift_count = .N
  ), by = .(cycle_label, cycle, company, person_key, party)]
  result <- merge(result, address, by = c("cycle_label", "company", "person_key"),
                  all.x = TRUE, sort = FALSE)
  result[, gift_count := as.integer(gift_count)]
  result[, distinct_addresses := as.integer(distinct_addresses)]
  data.table::setcolorder(result, names(MENTAT_TIDY_TABLE_SPECS$contributor_facts))
  data.table::setorder(result, cycle_label, company, person_key, party, na.last = TRUE)
  result[]
}

# 2.6 location_facts
mentat_tidy_location_facts <- function(contributions) {
  included <- mentat_tidy_included(contributions)
  if (!nrow(included)) return(mentat_tidy_empty("location_facts"))
  result <- included[, .(
    giving = sum(analysis_amount, na.rm = TRUE),
    gift_count = .N,
    distinct_contributors = data.table::uniqueN(person_key[!is.na(person_key)])
  ), by = .(cycle_label, cycle, company, state, city, zip5)]
  result[, gift_count := as.integer(gift_count)]
  result[, distinct_contributors := as.integer(distinct_contributors)]
  data.table::setcolorder(result, names(MENTAT_TIDY_TABLE_SPECS$location_facts))
  data.table::setorder(result, cycle_label, company, state, city, zip5, na.last = TRUE)
  result[]
}

# 2.8 committee_sponsors. Two independent link sources, kept as separate rows
# rather than merged: the FEC ccl file states a filed linkage, the leadership
# roster states a sponsorship the ccl file does not carry. link_source is what
# lets a reader tell which evidence a row rests on.
mentat_tidy_committee_sponsors <- function(universe, candidate_links, mappings,
                                           candidate_master) {
  require_package("data.table")
  if (is.null(universe) || !nrow(universe)) {
    return(mentat_tidy_empty("committee_sponsors"))
  }
  keys <- unique(paste(universe$cycle, universe$committee_id, sep = "|"))
  names_by_id <- NULL
  if (!is.null(candidate_master) && nrow(candidate_master)) {
    names_by_id <- unique(data.table::data.table(
      cycle = mentat_tidy_count(candidate_master$cycle),
      candidate_id = mentat_tidy_id(candidate_master$candidate_id),
      candidate = mentat_tidy_text(candidate_master$candidate_name)
    ), by = c("cycle", "candidate_id"))
  }
  pieces <- list()
  if (!is.null(candidate_links) && nrow(candidate_links)) {
    links <- data.table::as.data.table(candidate_links)
    links <- links[paste(cycle, toupper(trimws(committee_id)), sep = "|") %in% keys]
    if (nrow(links)) {
      pieces[[length(pieces) + 1L]] <- data.table::data.table(
        committee_id = mentat_tidy_id(links$committee_id),
        candidate_id = mentat_tidy_id(links$candidate_id),
        cycle = mentat_tidy_count(links$cycle),
        relationship = mentat_tidy_text(mentat_tidy_column(links, "relationship", nrow(links))),
        link_source = "FEC ccl",
        source_url = mentat_tidy_text(mentat_tidy_column(links, "source_url", nrow(links)))
      )
    }
  }
  if (!is.null(mappings) && nrow(mappings)) {
    roster <- data.table::as.data.table(mappings)
    roster <- roster[grepl("Leadership PAC sponsor", relationship, fixed = TRUE)]
    roster <- roster[paste(cycle, toupper(trimws(committee_id)), sep = "|") %in% keys]
    if (nrow(roster)) {
      pieces[[length(pieces) + 1L]] <- data.table::data.table(
        committee_id = mentat_tidy_id(roster$committee_id),
        candidate_id = mentat_tidy_id(roster$candidate_id),
        cycle = mentat_tidy_count(roster$cycle),
        relationship = mentat_tidy_text(roster$relationship),
        link_source = "Leadership PAC roster",
        source_url = mentat_tidy_text(
          mentat_tidy_column(roster, "mapping_source", nrow(roster))
        )
      )
    }
  }
  if (!length(pieces)) return(mentat_tidy_empty("committee_sponsors"))
  result <- data.table::rbindlist(pieces, use.names = TRUE, fill = TRUE)
  result <- result[!is.na(committee_id) & !is.na(candidate_id)]
  if (!nrow(result)) return(mentat_tidy_empty("committee_sponsors"))
  result[, cycle_label := mentat_tidy_cycle_label(cycle)]
  if (!is.null(names_by_id)) {
    result <- merge(result, names_by_id, by = c("cycle", "candidate_id"),
                    all.x = TRUE, sort = FALSE)
  } else {
    result[, candidate := NA_character_]
  }
  result <- unique(result, by = c("committee_id", "candidate_id", "cycle_label", "link_source"))
  data.table::setcolorder(result, names(MENTAT_TIDY_TABLE_SPECS$committee_sponsors))
  data.table::setorder(result, committee_id, cycle_label, candidate_id, link_source,
                       na.last = TRUE)
  result[]
}

# 2.7 committees. One row per committee that appears in contributions, per
# cycle. Classification columns are taken from CONTRIBUTIONS (modal), not from
# committee_map, so the Committees sheet and the Contributions sheet can never
# disagree about what category a committee was counted under; the map supplies
# only the fields that exist nowhere else.
mentat_tidy_committees <- function(contributions, committee_map, committee_master,
                                   sponsors) {
  require_package("data.table")
  rows <- contributions[!is.na(committee_id)]
  if (!nrow(rows)) return(mentat_tidy_empty("committees"))
  result <- rows[, .(
    committee_name = mentat_tidy_mode(recipient),
    committee_type_code = mentat_tidy_mode(committee_type_code),
    committee_designation_code = mentat_tidy_mode(committee_designation_code),
    party = mentat_tidy_mode(party),
    recipient_category = mentat_tidy_mode(recipient_category),
    category_source = mentat_tidy_mode(category_source),
    party_source = mentat_tidy_mode(party_source)
  ), by = .(committee_id, cycle_label, cycle)]
  result[, committee_type := mentat_tidy_text(
    mentat_fec_code_label(committee_type_code, MENTAT_FEC_COMMITTEE_TYPE_LABELS)
  )]
  result[, committee_designation := mentat_tidy_text(
    mentat_fec_code_label(committee_designation_code,
                          MENTAT_FEC_COMMITTEE_DESIGNATION_LABELS)
  )]

  map_fields <- data.table::data.table(
    cycle = integer(), committee_id = character(),
    map_committee_name = character(), connected_organization = character(),
    ownership_status = character(), committee_relationship = character(),
    mapping_source = character(), top_downstream_candidate_id = character(),
    top_downstream_candidate = character(), top_downstream_activity = numeric()
  )
  if (!is.null(committee_map) && nrow(committee_map)) {
    map <- data.table::as.data.table(committee_map)
    count <- nrow(map)
    map_fields <- data.table::data.table(
      cycle = mentat_tidy_count(map$cycle),
      committee_id = mentat_tidy_id(map$committee_id),
      map_committee_name = mentat_tidy_text(
        mentat_tidy_column(map, "master_committee_name", count)
      ),
      connected_organization = mentat_tidy_text(
        mentat_tidy_column(map, "connected_organization", count)
      ),
      ownership_status = mentat_tidy_text(
        mentat_tidy_column(map, "ownership_status", count)
      ),
      committee_relationship = mentat_tidy_single_or_multiple(
        mentat_tidy_column(map, "relationship", count)
      ),
      mapping_source = mentat_tidy_single_or_multiple(
        mentat_tidy_column(map, "mapping_source", count)
      ),
      top_downstream_candidate_id = mentat_tidy_id(
        mentat_tidy_column(map, "top_downstream_candidate_id", count)
      ),
      top_downstream_candidate = mentat_tidy_text(
        mentat_tidy_column(map, "top_downstream_person", count)
      ),
      top_downstream_activity = mentat_tidy_money(
        mentat_tidy_column(map, "preliminary_top_downstream_amount", count)
      )
    )
    map_fields <- unique(map_fields, by = c("cycle", "committee_id"))
  }
  result <- merge(result, map_fields, by = c("cycle", "committee_id"),
                  all.x = TRUE, sort = FALSE)

  master_fields <- data.table::data.table(
    cycle = integer(), committee_id = character(),
    committee_state = character(), party_affiliation = character()
  )
  if (!is.null(committee_master) && nrow(committee_master)) {
    master <- data.table::as.data.table(committee_master)
    count <- nrow(master)
    master_fields <- unique(data.table::data.table(
      cycle = mentat_tidy_count(master$cycle),
      committee_id = mentat_tidy_id(master$committee_id),
      committee_state = mentat_tidy_text(mentat_tidy_column(master, "state", count)),
      party_affiliation = mentat_tidy_text(
        mentat_tidy_column(master, "party_affiliation", count)
      )
    ), by = c("cycle", "committee_id"))
  }
  result <- merge(result, master_fields, by = c("cycle", "committee_id"),
                  all.x = TRUE, sort = FALSE)

  counts <- data.table::data.table(committee_id = character(),
                                   cycle_label = character(),
                                   sponsor_count = integer())
  if (!is.null(sponsors) && nrow(sponsors)) {
    counts <- sponsors[, .(sponsor_count = data.table::uniqueN(candidate_id)),
                       by = .(committee_id, cycle_label)]
    counts[, sponsor_count := as.integer(sponsor_count)]
  }
  result <- merge(result, counts, by = c("committee_id", "cycle_label"),
                  all.x = TRUE, sort = FALSE)
  result[is.na(sponsor_count), sponsor_count := 0L]
  # committee_map's name is the official master spelling; fall back to the
  # filed spelling rather than leaving the committee unnamed.
  result[!is.na(map_committee_name), committee_name := map_committee_name]
  result[, map_committee_name := NULL]
  result[, fec_committee_url := mentat_tidy_committee_url(committee_id)]
  data.table::setcolorder(result, names(MENTAT_TIDY_TABLE_SPECS$committees))
  data.table::setorder(result, committee_id, cycle_label, na.last = TRUE)
  result[]
}

# 2.4 recipient_facts
mentat_tidy_recipient_facts <- function(contributions, committees, sponsors,
                                        candidates) {
  require_package("data.table")
  included <- mentat_tidy_included(contributions)
  included <- included[!is.na(committee_id)]
  if (!nrow(included)) return(mentat_tidy_empty("recipient_facts"))
  result <- included[, .(
    recipient = mentat_tidy_mode(recipient),
    giving = sum(analysis_amount, na.rm = TRUE),
    gift_count = .N,
    distinct_contributors = data.table::uniqueN(person_key[!is.na(person_key)]),
    first_gift_date = mentat_tidy_date(fec_min_text(date[!is.na(date)])),
    last_gift_date = mentat_tidy_date(fec_max_text(date[!is.na(date)]))
  ), by = .(cycle_label, cycle, company, committee_id, employee_tier)]
  result[, gift_count := as.integer(gift_count)]
  result[, distinct_contributors := as.integer(distinct_contributors)]

  attributes_frame <- committees[, .(
    committee_id, cycle_label, recipient_category, category_source, party,
    party_source, committee_type_code, committee_type,
    committee_designation_code, committee_designation, sponsor_count,
    connected_organization, committee_relationship, ownership_status
  )]
  result <- merge(result, attributes_frame, by = c("committee_id", "cycle_label"),
                  all.x = TRUE, sort = FALSE)
  result[is.na(sponsor_count), sponsor_count := 0L]

  # A single sponsor is a fact about the committee and belongs on the row; two
  # or more is a many-to-many and stays in the bridge sheet, where a reader can
  # see every link instead of one arbitrarily chosen winner.
  sole <- data.table::data.table(committee_id = character(), cycle_label = character(),
                                 sponsor_candidate_id = character())
  if (!is.null(sponsors) && nrow(sponsors)) {
    sole <- sponsors[, .(candidates_linked = data.table::uniqueN(candidate_id),
                         sponsor_candidate_id = candidate_id[[1L]]),
                     by = .(committee_id, cycle_label)]
    sole <- sole[candidates_linked == 1L, .(committee_id, cycle_label, sponsor_candidate_id)]
  }
  result <- merge(result, sole, by = c("committee_id", "cycle_label"),
                  all.x = TRUE, sort = FALSE)
  if (!"sponsor_candidate_id" %in% names(result)) {
    result[, sponsor_candidate_id := NA_character_]
  }
  candidate_fields <- data.table::data.table(
    sponsor_candidate_id = character(), sponsor_candidate = character(),
    sponsor_office = character(), sponsor_state = character(),
    sponsor_district = character()
  )
  if (!is.null(candidates) && nrow(candidates)) {
    candidate_fields <- unique(data.table::data.table(
      sponsor_candidate_id = candidates$candidate_id,
      sponsor_candidate = candidates$candidate,
      sponsor_office = candidates$office,
      sponsor_state = candidates$state,
      sponsor_district = candidates$district
    ), by = "sponsor_candidate_id")
  }
  result <- merge(result, candidate_fields, by = "sponsor_candidate_id",
                  all.x = TRUE, sort = FALSE)
  result[, fec_committee_url := mentat_tidy_committee_url(committee_id)]
  data.table::setcolorder(result, names(MENTAT_TIDY_TABLE_SPECS$recipient_facts))
  data.table::setorder(result, cycle_label, company, committee_id, employee_tier,
                       na.last = TRUE)
  result[]
}

# 2.9 candidates. Filtered to the candidate ids anything else actually
# references, so the sheet is a dimension for THIS industry's rows and not a
# 15,000-row copy of the FEC candidate master.
mentat_tidy_candidates <- function(candidate_master, referenced_ids) {
  require_package("data.table")
  referenced <- unique(mentat_tidy_id(referenced_ids))
  referenced <- referenced[!is.na(referenced)]
  if (is.null(candidate_master) || !nrow(candidate_master) || !length(referenced)) {
    return(mentat_tidy_empty("candidates"))
  }
  master <- data.table::as.data.table(candidate_master)
  count <- nrow(master)
  result <- data.table::data.table(
    candidate_id = mentat_tidy_id(master$candidate_id),
    candidate = mentat_tidy_text(mentat_tidy_column(master, "candidate_name", count)),
    party = mentat_tidy_text(mentat_tidy_column(master, "candidate_party", count)),
    party_affiliation = mentat_tidy_text(
      mentat_tidy_column(master, "party_affiliation", count)
    ),
    office_code = mentat_tidy_text(mentat_tidy_column(master, "office", count)),
    office = mentat_tidy_text(mentat_tidy_column(master, "office_label", count)),
    state = mentat_tidy_text(mentat_tidy_column(master, "office_state", count)),
    district = mentat_tidy_id(mentat_tidy_column(master, "district", count)),
    incumbent_raw = mentat_tidy_text(
      mentat_tidy_column(master, "incumbent_challenger", count)
    ),
    principal_committee_id = mentat_tidy_id(
      mentat_tidy_column(master, "principal_committee_id", count)
    ),
    source_url = mentat_tidy_text(mentat_tidy_column(master, "source_url", count)),
    cycle = mentat_tidy_count(mentat_tidy_column(master, "cycle", count))
  )
  result <- result[candidate_id %in% referenced]
  if (!nrow(result)) return(mentat_tidy_empty("candidates"))
  # One row per candidate id across cycles: keep the most recent cycle's record
  # so office/district describe the seat the candidate most recently sought.
  data.table::setorder(result, candidate_id, -cycle, na.last = TRUE)
  result <- unique(result, by = "candidate_id")
  # FEC codes I/C/O. An unrecognised code stays NULL rather than defaulting to
  # a status the filing never claimed.
  result[, incumbent_status := ifelse(
    is.na(incumbent_raw), NA_character_,
    ifelse(incumbent_raw == "I", "Incumbent",
           ifelse(incumbent_raw == "C", "Challenger",
                  ifelse(incumbent_raw == "O", "Open seat", NA_character_)))
  )]
  result[, fec_candidate_url := mentat_tidy_candidate_url(candidate_id)]
  result[, c("incumbent_raw", "cycle") := NULL]
  data.table::setcolorder(result, names(MENTAT_TIDY_TABLE_SPECS$candidates))
  data.table::setorder(result, candidate_id, na.last = TRUE)
  result[]
}

# 2.10 downstream_activity
mentat_tidy_downstream_activity <- function(downstream_activity_export) {
  require_package("data.table")
  if (is.null(downstream_activity_export) || !nrow(downstream_activity_export)) {
    return(mentat_tidy_empty("downstream_activity"))
  }
  rows <- data.table::as.data.table(downstream_activity_export)
  count <- nrow(rows)
  cycle <- mentat_tidy_count(mentat_tidy_column(rows, "cycle", count))
  reported <- mentat_tidy_money(mentat_tidy_column(rows, "reported_amount", count))
  excluded <- mentat_tidy_money(mentat_tidy_column(rows, "excluded_amount", count))
  included <- mentat_tidy_money(
    mentat_tidy_column(rows, "preliminary_included_amount", count)
  )
  committee_id <- mentat_tidy_id(mentat_tidy_column(rows, "source_committee_id", count))
  candidate_id <- mentat_tidy_id(mentat_tidy_column(rows, "candidate_id", count))
  result <- data.table::data.table(
    cycle_label = mentat_tidy_cycle_label(cycle),
    cycle = cycle,
    source_committee = mentat_tidy_text(mentat_tidy_column(rows, "source_committee", count)),
    source_committee_id = committee_id,
    source_recipient_category = mentat_tidy_text(
      mentat_tidy_column(rows, "source_recipient_category", count)
    ),
    candidate = mentat_tidy_text(mentat_tidy_column(rows, "candidate_name", count)),
    candidate_id = candidate_id,
    candidate_party = mentat_tidy_text(mentat_tidy_column(rows, "candidate_party", count)),
    office = mentat_tidy_text(mentat_tidy_column(rows, "office_label", count)),
    state = mentat_tidy_text(mentat_tidy_column(rows, "office_state", count)),
    district = mentat_tidy_id(mentat_tidy_column(rows, "district", count)),
    activity_type = mentat_tidy_text(mentat_tidy_column(rows, "activity_type", count)),
    support_oppose = mentat_tidy_text(mentat_tidy_column(rows, "support_oppose", count)),
    transaction_type = mentat_tidy_text(mentat_tidy_column(rows, "transaction_type", count)),
    first_activity_date = mentat_tidy_date(
      mentat_tidy_column(rows, "first_activity_date", count)
    ),
    last_activity_date = mentat_tidy_date(
      mentat_tidy_column(rows, "last_activity_date", count)
    ),
    source_rows = mentat_tidy_count(mentat_tidy_column(rows, "source_rows", count)),
    included_rows = mentat_tidy_count(mentat_tidy_column(rows, "included_rows", count)),
    excluded_rows = mentat_tidy_count(mentat_tidy_column(rows, "excluded_rows", count)),
    superseded_rows = mentat_tidy_count(mentat_tidy_column(rows, "superseded_rows", count)),
    memo_rows = mentat_tidy_count(mentat_tidy_column(rows, "memo_rows", count)),
    duplicate_rows = mentat_tidy_count(
      mentat_tidy_column(rows, "exact_duplicate_rows", count)
    ),
    unverified_version_rows = mentat_tidy_count(
      mentat_tidy_column(rows, "unverified_version_rows", count)
    ),
    unresolved_version_rows = mentat_tidy_count(mentat_tidy_column(rows, "unresolved_version_rows", count)),
    unresolved_version_amount = mentat_tidy_money(mentat_tidy_column(rows, "unresolved_version_amount", count)),
    repeated_itemization_rows = mentat_tidy_count(mentat_tidy_column(rows, "repeated_itemization_rows", count)),
    reported_amount = reported,
    excluded_amount = excluded,
    net_included_amount = included,
    fec_committee_url = mentat_tidy_committee_url(committee_id),
    fec_candidate_url = mentat_tidy_candidate_url(candidate_id),
    source_url = mentat_tidy_text(mentat_tidy_column(rows, "source_url", count)),
    # JSON stays a single TEXT cell. It is evidence a reader can open, not a
    # dimension; splitting it into rows here would multiply every amount.
    transfer_records = mentat_tidy_text(mentat_tidy_column(rows, "transfer_records", count)),
    filing_records = mentat_tidy_text(mentat_tidy_column(rows, "filing_records", count))
  )
  data.table::setorder(result, cycle_label, source_committee_id, candidate_id,
                       activity_type, support_oppose, transaction_type, na.last = TRUE)
  result[]
}

# 2.11 beneficiary_bridge is company ASSOCIATION, so shared PAC spending repeats
# between companies. beneficiary_activity stores the unique Industry view as a
# separate additive fact table. Neither table is truncated to the Overview's 100.
#
# `activity_relationships` and `directions` are `;`-joined sets. They become one
# 0/1 flag column per observed token, which is the tidy form of a set-valued
# attribute and lets Tableau filter on "has oppose activity" without parsing.
mentat_tidy_beneficiary_bridge <- function(bridge, selected_view = "Company") {
  require_package("data.table")
  if (is.null(bridge) || !nrow(bridge)) return(mentat_tidy_empty("beneficiary_bridge"))
  rows <- data.table::as.data.table(bridge)
  if ("view" %in% names(rows)) rows <- rows[view == selected_view]
  if (!nrow(rows)) return(mentat_tidy_empty("beneficiary_bridge"))
  count <- nrow(rows)
  cycle <- mentat_tidy_count(mentat_tidy_column(rows, "cycle", count))
  committee_id <- mentat_tidy_id(mentat_tidy_column(rows, "source_committee_id", count))
  candidate_id <- mentat_tidy_id(mentat_tidy_column(rows, "candidate_id", count))
  net <- mentat_tidy_money(mentat_tidy_column(rows, "preliminary_downstream_amount", count))
  support <- mentat_tidy_money(mentat_tidy_column(rows, "support_amount", count))
  oppose <- mentat_tidy_money(mentat_tidy_column(rows, "oppose_amount", count))
  result <- data.table::data.table(
    bridge_key = mentat_tidy_text(mentat_tidy_column(rows, "bridge_key", count)),
    cycle_label = mentat_tidy_cycle_label(cycle),
    cycle = cycle,
    company = mentat_tidy_text(mentat_tidy_column(rows, "company", count)),
    source_committee = mentat_tidy_text(mentat_tidy_column(rows, "source_committee", count)),
    source_committee_id = committee_id,
    source_recipient_category = mentat_tidy_text(
      mentat_tidy_column(rows, "source_recipient_category", count)
    ),
    candidate = mentat_tidy_text(mentat_tidy_column(rows, "candidate_name", count)),
    candidate_id = candidate_id,
    candidate_party = mentat_tidy_text(mentat_tidy_column(rows, "candidate_party", count)),
    office = mentat_tidy_text(mentat_tidy_column(rows, "office_label", count)),
    state = mentat_tidy_text(mentat_tidy_column(rows, "office_state", count)),
    district = mentat_tidy_id(mentat_tidy_column(rows, "district", count)),
    transaction_count = mentat_tidy_count(
      mentat_tidy_column(rows, "transaction_count", count)
    ),
    first_activity_date = mentat_tidy_date(
      mentat_tidy_column(rows, "first_activity_date", count)
    ),
    last_activity_date = mentat_tidy_date(
      mentat_tidy_column(rows, "last_activity_date", count)
    ),
    downstream_activity = mentat_tidy_money(
      mentat_tidy_column(rows, "preliminary_downstream_amount", count)
    ),
    support_amount = mentat_tidy_money(mentat_tidy_column(rows, "support_amount", count)),
    oppose_amount = mentat_tidy_money(mentat_tidy_column(rows, "oppose_amount", count)),
    other_activity_amount = net - support - oppose,
    associated_funding = mentat_tidy_money(
      mentat_tidy_column(rows, "preliminary_crypto_funding", count)
    )
  )
  tokens <- mentat_tidy_flag_columns(
    list(
      relationship = mentat_tidy_column(rows, "activity_relationships", count),
      direction = mentat_tidy_column(rows, "directions", count)
    )
  )
  for (name in names(tokens)) data.table::set(result, j = name, value = tokens[[name]])
  data.table::setorder(result, cycle_label, company, source_committee_id, candidate_id,
                       na.last = TRUE)
  result[]
}

# Turn `;`-joined set columns into one 0/1 column per observed token. Column
# names are snake_case `has_<token>`; the writer renders them as
# "Has <Token> Activity". Tokens are sorted so two runs over the same data
# produce the same column order.
mentat_tidy_flag_columns <- function(sets) {
  result <- list()
  for (group in names(sets)) {
    values <- mentat_tidy_text(sets[[group]])
    split_values <- strsplit(ifelse(is.na(values), "", values), ";", fixed = TRUE)
    tokens <- sort(unique(trimws(unlist(split_values, use.names = FALSE))))
    tokens <- tokens[nzchar(tokens)]
    for (token in tokens) {
      key <- paste0("has_", tolower(gsub("[^A-Za-z0-9]+", "_", token)))
      key <- sub("_+$", "", key)
      result[[key]] <- as.integer(vapply(
        split_values, function(item) token %in% trimws(item), logical(1)
      ))
    }
  }
  result
}

# 2.12 state_destinations / recipient_locations, minus the `;`-joined evidence
# columns. The evidence lives in the payload and in the Overview; a cell holding
# four URLs is not a field.
mentat_tidy_state_destinations <- function(frame) {
  require_package("data.table")
  if (is.null(frame) || !nrow(frame)) return(mentat_tidy_empty("state_destinations"))
  rows <- data.table::as.data.table(frame)
  count <- nrow(rows)
  cycle <- mentat_tidy_count(mentat_tidy_column(rows, "cycle", count))
  label <- mentat_tidy_text(mentat_tidy_column(rows, "cycle_label", count))
  label[is.na(label)] <- mentat_tidy_cycle_label(cycle)[is.na(label)]
  result <- data.table::data.table(
    cycle_label = label,
    cycle = cycle,
    company = mentat_tidy_text(mentat_tidy_column(rows, "company", count)),
    party = mentat_tidy_text(mentat_tidy_column(rows, "party", count)),
    location_view = mentat_tidy_text(mentat_tidy_column(rows, "location_view", count)),
    destination = mentat_tidy_text(mentat_tidy_column(rows, "destination", count)),
    accounting_basis = mentat_tidy_text(
      mentat_tidy_column(rows, "accounting_basis", count)
    ),
    net_amount = mentat_tidy_money(mentat_tidy_column(rows, "net_amount", count)),
    receipt_count = mentat_tidy_count(mentat_tidy_column(rows, "receipt_count", count))
  )
  data.table::setorder(result, cycle_label, company, location_view, destination,
                       na.last = TRUE)
  result[]
}

mentat_tidy_recipient_locations <- function(frame) {
  require_package("data.table")
  if (is.null(frame) || !nrow(frame)) return(mentat_tidy_empty("recipient_locations"))
  rows <- data.table::as.data.table(frame)
  count <- nrow(rows)
  cycle <- mentat_tidy_count(mentat_tidy_column(rows, "cycle", count))
  result <- unique(data.table::data.table(
    cycle_label = mentat_tidy_cycle_label(cycle),
    cycle = cycle,
    committee_id = mentat_tidy_id(mentat_tidy_column(rows, "committee_id", count)),
    recipient = mentat_tidy_text(mentat_tidy_column(rows, "recipient", count)),
    recipient_state = mentat_tidy_text(mentat_tidy_column(rows, "recipient_state", count)),
    represented_state = mentat_tidy_text(
      mentat_tidy_column(rows, "represented_state", count)
    ),
    recipient_location_status = mentat_tidy_text(
      mentat_tidy_column(rows, "recipient_location_status", count)
    ),
    represented_location_status = mentat_tidy_text(
      mentat_tidy_column(rows, "represented_location_status", count)
    )
  ), by = c("cycle_label", "committee_id"))
  data.table::setorder(result, cycle_label, committee_id, na.last = TRUE)
  result[]
}

# 2.13 data_quality, R side. The Python enrichment appends its own rows with
# source "enrichment" and the workbook writer appends its own with source
# "writer"; keeping the producer on every row is what lets an operator tell a
# stale guard from a fresh one.
mentat_tidy_data_quality <- function(guard_results) {
  require_package("data.table")
  if (is.null(guard_results) || !nrow(guard_results)) {
    return(mentat_tidy_empty("data_quality"))
  }
  rows <- data.table::as.data.table(guard_results)
  count <- nrow(rows)
  metric <- suppressWarnings(as.numeric(mentat_tidy_column(rows, "metric", count)))
  result <- data.table::data.table(
    check_id = mentat_tidy_text(mentat_tidy_column(rows, "id", count)),
    source = "r_guard",
    severity = mentat_tidy_text(mentat_tidy_column(rows, "severity", count)),
    passed = mentat_tidy_flag(mentat_tidy_column(rows, "passed", count)),
    detail = mentat_tidy_text(mentat_tidy_column(rows, "detail", count)),
    # Metric is TEXT in the store: the three producers disagree about what a
    # metric is (a count, a dollar figure, a ratio) and a REAL column would
    # invite a chart that sums them.
    metric = ifelse(is.na(metric), NA_character_, format(metric, scientific = FALSE, trim = TRUE))
  )
  data.table::setorder(result, check_id, na.last = TRUE)
  result[]
}

# 2.1 metadata
mentat_tidy_metadata <- function(values) {
  require_package("data.table")
  require_package("jsonlite")
  keys <- names(values)
  if (is.null(keys)) keys <- character()
  flattened <- vapply(values, function(item) {
    if (is.null(item) || (length(item) == 1L && is.na(item))) return(NA_character_)
    if (length(item) == 1L && !is.list(item)) return(as.character(item))
    as.character(jsonlite::toJSON(item, auto_unbox = TRUE, digits = NA, na = "null"))
  }, character(1))
  # as.data.table(list(...)), NOT data.table(key = ...): `key` is a reserved
  # argument of data.table() and a column called `key` is swallowed as the
  # table's key instead of becoming a column.
  data.table::as.data.table(list(key = keys, value = unname(flattened)))
}

mentat_tidy_empty <- function(name) {
  require_package("data.table")
  spec <- MENTAT_TIDY_TABLE_SPECS[[name]]
  if (is.null(spec)) stop("Unknown tidy table: ", name, call. = FALSE)
  columns <- lapply(spec, function(type) switch(
    type,
    REAL = numeric(),
    INTEGER = integer(),
    character()
  ))
  data.table::as.data.table(stats::setNames(columns, names(spec)))
}

# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

mentat_build_tidy_frames <- function(raw_export, committee_map, committee_master,
                                     candidate_links, candidate_master,
                                     downstream_activity_export, bridge,
                                     state_destinations, recipient_locations,
                                     guard_results, mappings = NULL) {
  require_package("data.table")
  contributions <- mentat_tidy_contributions(raw_export)
  universe <- unique(contributions[!is.na(committee_id), .(cycle, committee_id)])
  sponsors <- mentat_tidy_committee_sponsors(universe, candidate_links, mappings,
                                             candidate_master)
  committees <- mentat_tidy_committees(contributions, committee_map, committee_master,
                                       sponsors)
  downstream <- mentat_tidy_downstream_activity(downstream_activity_export)
  bridge_rows <- mentat_tidy_beneficiary_bridge(bridge)
  candidates <- mentat_tidy_candidates(candidate_master, c(
    sponsors$candidate_id, downstream$candidate_id, bridge_rows$candidate_id,
    committees$top_downstream_candidate_id
  ))
  list(
    contributions = contributions,
    giving_facts = mentat_tidy_giving_facts(contributions),
    recipient_facts = mentat_tidy_recipient_facts(contributions, committees,
                                                  sponsors, candidates),
    contributor_facts = mentat_tidy_contributor_facts(contributions),
    location_facts = mentat_tidy_location_facts(contributions),
    committees = committees,
    committee_sponsors = sponsors,
    candidates = candidates,
    downstream_activity = downstream,
    beneficiary_bridge = bridge_rows,
    beneficiary_activity = mentat_tidy_beneficiary_bridge(bridge, "Industry"),
    state_destinations = mentat_tidy_state_destinations(state_destinations),
    recipient_locations = mentat_tidy_recipient_locations(recipient_locations),
    data_quality = mentat_tidy_data_quality(guard_results)
  )
}

# ---------------------------------------------------------------------------
# Guard
# ---------------------------------------------------------------------------

# W-style reconciliation, R side. Every fact sheet is a REGROUPING of the same
# included receipts: recipient, person, geography. A grouping key that splits on
# an NA, a join that multiplies rows, or a filter applied one step too early all
# publish a chartable sheet whose total quietly disagrees with the cycle total
# printed one tab away - and a chart built on the smaller number is wrong in a
# way no reader can see. Every dollar has to survive every regrouping.
#
# contributor_facts is compared against the person-keyed SUBSET of the receipts,
# not the cycle total, because entity rows legitimately have no person.
mentat_guard_tidy_reconciliation <- function(frames, expected, tolerance = 0.005) {
  require_package("data.table")
  expected <- unlist(expected)
  contributions <- data.table::as.data.table(frames$contributions)
  bad <- character()
  if (!nrow(contributions)) {
    return(mentat_guard_result(
      "tidy_reconciliation", "fail", TRUE,
      "staging store holds no receipt rows; nothing to reconcile", 0
    ))
  }
  included <- contributions[!is.na(include_in_analysis) & include_in_analysis == 1L]
  receipts <- included[, .(total = sum(analysis_amount, na.rm = TRUE)), by = cycle_label]
  receipt_totals <- stats::setNames(receipts$total, as.character(receipts$cycle_label))
  person <- included[!is.na(person_key),
                     .(total = sum(analysis_amount, na.rm = TRUE)), by = cycle_label]
  person_totals <- stats::setNames(person$total, as.character(person$cycle_label))

  sheet_totals <- function(name) {
    frame <- data.table::as.data.table(frames[[name]])
    if (is.null(frame) || !nrow(frame)) return(stats::setNames(numeric(), character()))
    totals <- frame[, .(total = sum(giving, na.rm = TRUE)), by = cycle_label]
    stats::setNames(totals$total, as.character(totals$cycle_label))
  }
  observed <- list(
    giving_facts = sheet_totals("giving_facts"),
    recipient_facts = sheet_totals("recipient_facts"),
    contributor_facts = sheet_totals("contributor_facts"),
    location_facts = sheet_totals("location_facts")
  )
  labels <- unique(c(names(expected), names(receipt_totals),
                     unlist(lapply(observed, names))))
  labels <- labels[!is.na(labels)]
  pick <- function(values, label) {
    if (label %in% names(values)) unname(values[[label]]) else 0
  }
  for (label in labels) {
    want <- if (label %in% names(expected)) as.numeric(expected[[label]]) else 0
    if (is.na(want)) want <- 0
    got <- pick(receipt_totals, label)
    if (abs(got - want) > tolerance) {
      bad <- c(bad, sprintf("%s/contributions %.2f vs cycle total %.2f", label, got, want))
    }
    for (name in names(observed)) {
      reference <- if (identical(name, "contributor_facts")) {
        pick(person_totals, label)
      } else {
        got
      }
      value <- pick(observed[[name]], label)
      if (abs(value - reference) > tolerance) {
        bad <- c(bad, sprintf("%s/%s %.2f vs receipts %.2f", label, name, value, reference))
      }
    }
  }
  # A duplicated key is not a rounding difference: it means the writer would
  # emit two rows the console cannot tell apart, and any join onto that sheet
  # would double the money. Keys come from MENTAT_TIDY_TABLE_KEYS so the guard
  # and the contract cannot drift apart.
  key_columns <- MENTAT_TIDY_TABLE_KEYS
  for (name in names(key_columns)) {
    frame <- frames[[name]]
    if (is.null(frame) || !nrow(frame)) next
    # A key column the frame does not carry would silently WEAKEN the check, so
    # say so rather than checking a shorter key.
    missing <- setdiff(key_columns[[name]], names(frame))
    if (length(missing)) {
      bad <- c(bad, sprintf("%s is missing key column(s) %s",
                            name, paste(missing, collapse = ", ")))
      next
    }
    columns <- key_columns[[name]]
    if (!length(columns)) next
    duplicates <- nrow(frame) - nrow(unique(data.table::as.data.table(frame), by = columns))
    if (duplicates > 0L) {
      bad <- c(bad, sprintf("%s has %d duplicate key row(s)", name, duplicates))
    }
  }
  if (!length(bad)) {
    return(mentat_guard_result(
      "tidy_reconciliation", "fail", TRUE,
      sprintf(
        "tidy staging tables reconcile to the cycle totals in %d cycle(s) and hold unique keys",
        length(labels)
      ),
      length(labels)
    ))
  }
  mentat_guard_result(
    "tidy_reconciliation", "fail", FALSE,
    paste0(
      "tidy staging tables do not reconcile to their cycle totals or repeat a ",
      "row key - the companion Data workbook would state a different figure ",
      "from the Overview: ",
      mentat_guard_summarise(bad)
    ),
    length(bad)
  )
}

# ---------------------------------------------------------------------------
# Store writer
# ---------------------------------------------------------------------------

# One SQLite file per export. Written to `<path>.pending-<pid>` and renamed, so
# a crashed or killed export never leaves a half-written store that the next
# cache hit would accept as complete. The pending suffix carries the pid because
# two industries can build concurrently into the same stage directory.
mentat_write_tidy_stage <- function(path, frames, metadata = list()) {
  require_package("data.table")
  require_package("DBI")
  require_package("RSQLite")
  if (!length(path) || !nzchar(path)) {
    stop("Tidy staging path cannot be empty.", call. = FALSE)
  }
  directory <- dirname(path)
  if (!dir.exists(directory) &&
      !dir.create(directory, recursive = TRUE, showWarnings = FALSE)) {
    stop("Could not create the tidy staging directory: ", directory, call. = FALSE)
  }
  pending <- paste0(path, ".pending-", Sys.getpid())
  if (file.exists(pending)) unlink(pending, force = TRUE)
  installed <- FALSE
  connected <- FALSE
  connection <- NULL

  frames <- frames[!vapply(frames, is.null, logical(1))]
  if (!"metadata" %in% names(frames)) {
    frames$metadata <- mentat_tidy_metadata(metadata)
  }
  # ONE exit handler covering both resources. Two on.exit calls with
  # add = FALSE later in the function would silently drop the first, leaving the
  # connection open and the pending file locked on Windows.
  on.exit({
    if (connected) try(DBI::dbDisconnect(connection), silent = TRUE)
    if (!installed && file.exists(pending)) unlink(pending, force = TRUE)
  }, add = TRUE)
  connection <- DBI::dbConnect(RSQLite::SQLite(), pending)
  connected <- TRUE
  # One transaction for the whole store: a store is valid as a set of tables or
  # not at all, and per-table commits would let a failure halfway through leave
  # a file that opens cleanly and is missing three sheets.
  DBI::dbWithTransaction(connection, {
    for (name in names(frames)) {
      frame <- as.data.frame(frames[[name]], stringsAsFactors = FALSE)
      spec <- MENTAT_TIDY_TABLE_SPECS[[name]]
      types <- NULL
      if (!is.null(spec)) {
        known <- intersect(names(spec), names(frame))
        extra <- setdiff(names(frame), names(spec))
        frame <- frame[, c(known, extra), drop = FALSE]
        types <- c(
          spec[known],
          stats::setNames(
            vapply(extra, function(column) mentat_tidy_affinity(frame[[column]]),
                   character(1)),
            extra
          )
        )
      }
      frame <- mentat_tidy_storable(frame)
      DBI::dbWriteTable(connection, name, frame, overwrite = TRUE,
                        field.types = types)
    }
  })
  DBI::dbDisconnect(connection)
  connected <- FALSE
  # file.rename onto an existing file fails on Windows, so the previous store is
  # removed first. Doing it AFTER the pending store is complete keeps the window
  # in which neither file exists to a single rename.
  if (file.exists(path)) {
    removed <- unlink(path, force = TRUE)
    if (!identical(removed, 0L)) {
      stop("Could not replace the existing tidy staging store: ", path, call. = FALSE)
    }
  }
  if (!file.rename(pending, path)) {
    stop("Could not install the tidy staging store: ", path, call. = FALSE)
  }
  installed <- TRUE
  invisible(path)
}

mentat_tidy_affinity <- function(values) {
  if (is.logical(values) || is.integer(values)) return("INTEGER")
  if (is.numeric(values)) return("REAL")
  "TEXT"
}

# Last line of defence before the driver sees a column: a Date or POSIXct would
# be stored as a day or epoch number and re-emerge from the Python writer as a
# 1970-era cell, and a factor would be stored as its integer level.
mentat_tidy_storable <- function(frame) {
  for (column in names(frame)) {
    value <- frame[[column]]
    if (inherits(value, "Date") || inherits(value, "POSIXt")) {
      frame[[column]] <- format(value, "%Y-%m-%d")
    } else if (is.factor(value)) {
      frame[[column]] <- as.character(value)
    } else if (is.logical(value) && !all(is.na(value))) {
      frame[[column]] <- as.integer(value)
    } else if (is.logical(value)) {
      frame[[column]] <- as.integer(value)
    }
    if (is.character(frame[[column]])) {
      frame[[column]] <- enc2utf8(frame[[column]])
    }
  }
  frame
}

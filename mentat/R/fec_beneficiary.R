fec_party_bucket <- function(values) {
  value <- toupper(trim_text(values))
  ifelse(
    value %in% c("DEM", "DEMOCRATIC", "DFL", "D"),
    "D",
    ifelse(value %in% c("REP", "REPUBLICAN", "GOP", "R"), "R", "Other / Unclassified")
  )
}

fec_office_label <- function(values) {
  value <- toupper(trim_text(values))
  ifelse(value == "H", "House", ifelse(value == "S", "Senate", ifelse(value == "P", "President", "")))
}

fec_mode_text <- function(values) {
  value <- trim_text(values)
  value <- value[nzchar(value)]
  if (!length(value)) return("")
  first <- value[[1L]]
  if (all(value == first)) return(first)
  ordered <- sort(unique(value))
  counts <- tabulate(match(value, ordered), nbins = length(ordered))
  ordered[[which.max(counts)]]
}

fec_join_unique <- function(values) {
  value <- unique(trim_text(values))
  value <- value[nzchar(value)]
  if (!length(value)) return("")
  if (length(value) == 1L) return(value[[1L]])
  paste(sort(value), collapse = "; ")
}

fec_min_text <- function(values) {
  value <- unique(trim_text(values))
  value <- value[nzchar(value)]
  if (!length(value)) return("")
  if (length(value) == 1L) return(value[[1L]])
  sort(value)[[1L]]
}

fec_max_text <- function(values) {
  value <- unique(trim_text(values))
  value <- value[nzchar(value)]
  if (!length(value)) return("")
  if (length(value) == 1L) return(value[[1L]])
  ordered <- sort(value)
  ordered[[length(ordered)]]
}

fec_grouped_join_unique <- function(rows, key_columns, value_column, output_column) {
  values <- rows[, c(key_columns, value_column), with = FALSE]
  data.table::setnames(values, value_column, ".fec_value")
  values[, .fec_value := trim_text(.fec_value)]
  values <- unique(values[nzchar(.fec_value)])
  if (!nrow(values)) {
    result <- rows[0L, ..key_columns]
    result[, (output_column) := character()]
    return(result[])
  }
  data.table::setorderv(values, c(key_columns, ".fec_value"))
  result <- values[, .(
    .fec_output = if (.N == 1L) .fec_value[[1L]] else paste(.fec_value, collapse = "; ")
  ), by = key_columns]
  data.table::setnames(result, ".fec_output", output_column)
  result[]
}

fec_grouped_text_bounds <- function(rows, key_columns, value_column) {
  values <- rows[, c(key_columns, value_column), with = FALSE]
  data.table::setnames(values, value_column, ".fec_value")
  values[, .fec_value := trim_text(.fec_value)]
  values <- unique(values[nzchar(.fec_value)])
  if (!nrow(values)) {
    result <- rows[0L, ..key_columns]
    result[, `:=`(
      first_activity_date = character(),
      last_activity_date = character()
    )]
    return(result[])
  }
  data.table::setorderv(values, c(key_columns, ".fec_value"))
  first_rows <- values[!duplicated(values, by = key_columns)]
  last_rows <- values[!duplicated(values, by = key_columns, fromLast = TRUE)]
  data.table::setnames(first_rows, ".fec_value", "first_activity_date")
  data.table::setnames(last_rows, ".fec_value", "last_activity_date")
  merge(
    first_rows,
    last_rows,
    by = key_columns,
    all = TRUE,
    sort = FALSE
  )[]
}

fec_grouped_candidate_metadata <- function(rows, key_columns, metadata_columns) {
  metadata <- unique(rows[, c(key_columns, metadata_columns), with = FALSE])
  metadata[, (metadata_columns) := lapply(.SD, trim_text), .SDcols = metadata_columns]
  metadata <- unique(metadata)
  group_sizes <- metadata[, .N, by = key_columns]
  stable_keys <- group_sizes[N == 1L, ..key_columns]
  stable <- metadata[stable_keys, on = key_columns, nomatch = 0L]
  ambiguous_keys <- group_sizes[N > 1L, ..key_columns]
  if (!nrow(ambiguous_keys)) return(stable[])

  ambiguous <- rows[ambiguous_keys, on = key_columns, nomatch = 0L]
  resolved <- ambiguous[, lapply(.SD, fec_mode_text), by = key_columns, .SDcols = metadata_columns]
  data.table::rbindlist(list(stable, resolved), use.names = TRUE)[]
}

fec_aggregate_beneficiary_downstream <- function(downstream_activity) {
  key_columns <- c("cycle", "source_committee_id", "candidate_id")
  metadata_columns <- c(
    "candidate_name", "candidate_party", "office", "office_label",
    "office_state", "district"
  )
  included <- downstream_activity[include_in_total == "Yes", c(
    list(
      cycle = as.integer(cycle),
      source_committee_id = trim_text(committee_id),
      candidate_id = trim_text(candidate_id),
      transaction_amount = as.numeric(transaction_amount),
      activity_type = trim_text(activity_type),
      support_oppose = trim_text(support_oppose),
      transaction_date = trim_text(transaction_date)
    ),
    lapply(.SD, trim_text)
  ), .SDcols = metadata_columns]
  if (!nrow(included)) return(data.table::data.table())

  # Direction has to be split HERE, at the only grain where it still exists as a
  # per-row fact. Every roll-up above this groups rows together and keeps
  # direction only as the joined text in `directions`, so a single unsigned
  # amount is all that survives - which is how money spent to DEFEAT a candidate
  # ended up displayed as activity benefiting their party. Carrying the split as
  # its own components lets the workbook attribute support to a party and report
  # opposition separately, instead of inferring direction from a text field.
  # Not netted: support minus oppose would hide the magnitude of both.
  totals <- included[, .(
    preliminary_downstream_amount = sum(transaction_amount, na.rm = TRUE),
    support_amount = sum(
      transaction_amount[support_oppose == "Support"], na.rm = TRUE
    ),
    oppose_amount = sum(
      transaction_amount[support_oppose == "Oppose"], na.rm = TRUE
    ),
    transaction_count = .N
  ), by = key_columns]
  relationships <- fec_grouped_join_unique(
    included, key_columns, "activity_type", "activity_relationships"
  )
  directions <- fec_grouped_join_unique(
    included, key_columns, "support_oppose", "directions"
  )
  dates <- fec_grouped_text_bounds(
    included, key_columns, "transaction_date"
  )
  metadata <- fec_grouped_candidate_metadata(
    included, key_columns, metadata_columns
  )
  result <- Reduce(
    function(left, right) merge(
      left, right,
      by = key_columns,
      all.x = TRUE,
      sort = FALSE
    ),
    list(totals, relationships, directions, dates, metadata)
  )
  text_columns <- c(
    "activity_relationships", "directions", "first_activity_date",
    "last_activity_date", metadata_columns
  )
  for (column in text_columns) {
    data.table::set(result, which(is.na(result[[column]])), column, "")
  }
  result[]
}

fec_aggregate_beneficiary_summary <- function(bridge) {
  key_columns <- c("cycle", "view", "company", "candidate_id", "candidate_key")
  metadata_columns <- c(
    "candidate_name", "candidate_party", "office", "office_label",
    "office_state", "district"
  )
  totals <- bridge[, .(
    preliminary_downstream_amount = sum(preliminary_downstream_amount, na.rm = TRUE),
    support_amount = sum(support_amount, na.rm = TRUE),
    oppose_amount = sum(oppose_amount, na.rm = TRUE),
    # Keep base-R summation order identical to the legacy mixed aggregation.
    # data.table's GForce sum is faster but can differ in the final floating
    # point bit, which would make formula-cache parity needlessly noisy.
    preliminary_crypto_funding = sum(preliminary_crypto_funding + 0, na.rm = TRUE)
  ), by = key_columns]
  source_rows <- unique(bridge[, c(key_columns, "source_committee_id"), with = FALSE])
  source_counts <- source_rows[, .(source_committee_count = .N), by = key_columns]

  source_labels <- bridge[, c(key_columns, "source_committee", "source_committee_id"), with = FALSE]
  source_labels[, source_committee_label := paste0(
    trim_text(source_committee), " (", trim_text(source_committee_id), ")"
  )]
  source_committees <- fec_grouped_join_unique(
    source_labels, key_columns, "source_committee_label", "source_committees"
  )
  relationships <- fec_grouped_join_unique(
    bridge, key_columns, "activity_relationships", "activity_relationships"
  )
  directions <- fec_grouped_join_unique(
    bridge, key_columns, "directions", "directions"
  )
  metadata <- fec_grouped_candidate_metadata(
    bridge, key_columns, metadata_columns
  )
  result <- Reduce(
    function(left, right) merge(
      left, right,
      by = key_columns,
      all.x = TRUE,
      sort = FALSE
    ),
    list(
      totals, source_counts, source_committees, relationships, directions,
      metadata
    )
  )
  text_columns <- c(
    "source_committees", "activity_relationships", "directions",
    metadata_columns
  )
  for (column in text_columns) {
    data.table::set(result, which(is.na(result[[column]])), column, "")
  }
  result[]
}

fec_read_candidate_master <- function(directory, cycles = c(2022L, 2024L, 2026L)) {
  headers <- c(
    "candidate_id", "candidate_name", "party_affiliation", "election_year",
    "office_state", "office", "district", "incumbent_challenger", "status",
    "principal_committee_id", "street_1", "street_2", "city", "state", "zip"
  )
  output <- lapply(cycles, function(cycle) {
    suffix <- substr(as.character(cycle), 3L, 4L)
    path <- file.path(directory, paste0("cn", suffix), "cn.txt")
    if (!file.exists(path)) stop("Missing official FEC candidate master: ", path, call. = FALSE)
    rows <- data.table::fread(
      path, sep = "|", header = FALSE, fill = TRUE, quote = "",
      col.names = headers, encoding = "UTF-8", showProgress = FALSE
    )
    rows[, .(
      cycle = cycle,
      candidate_id = toupper(trim_text(candidate_id)),
      candidate_name = trim_text(candidate_name),
      candidate_name_key = normalize_variant_key(candidate_name),
      candidate_party = fec_party_bucket(party_affiliation),
      party_affiliation = toupper(trim_text(party_affiliation)),
      office = toupper(trim_text(office)),
      office_label = fec_office_label(office),
      office_state = toupper(trim_text(office_state)),
      district = trim_text(district),
      incumbent_challenger = toupper(trim_text(incumbent_challenger)),
      principal_committee_id = toupper(trim_text(principal_committee_id)),
      source_url = paste0("https://www.fec.gov/files/bulk-downloads/", cycle, "/cn", suffix, ".zip")
    )]
  })
  rows <- data.table::rbindlist(output, use.names = TRUE, fill = TRUE)
  unique(rows[nzchar(candidate_id)], by = c("cycle", "candidate_id"))
}

fec_read_candidate_committee_links <- function(directory, cycles = c(2022L, 2024L, 2026L)) {
  headers <- c(
    "candidate_id", "candidate_election_year", "fec_election_year",
    "committee_id", "committee_type", "designation", "linkage_id"
  )
  labels <- c(
    P = "Principal campaign committee",
    A = "Authorized candidate committee",
    D = "Leadership PAC linked to candidate",
    J = "Joint fundraiser linked to candidate",
    U = "Unauthorized committee linked to candidate"
  )
  output <- lapply(cycles, function(cycle) {
    suffix <- substr(as.character(cycle), 3L, 4L)
    path <- file.path(directory, paste0("ccl", suffix), "ccl.txt")
    if (!file.exists(path)) stop("Missing official FEC candidate-committee linkage: ", path, call. = FALSE)
    rows <- data.table::fread(
      path, sep = "|", header = FALSE, fill = TRUE, quote = "",
      col.names = headers, encoding = "UTF-8", showProgress = FALSE
    )
    rows[, designation := toupper(trim_text(designation))]
    rows[, .(
      cycle = cycle,
      candidate_id = toupper(trim_text(candidate_id)),
      committee_id = toupper(trim_text(committee_id)),
      committee_type = toupper(trim_text(committee_type)),
      designation,
      relationship = ifelse(designation %in% names(labels), unname(labels[designation]), "Candidate-committee linkage"),
      source_url = paste0("https://www.fec.gov/files/bulk-downloads/", cycle, "/ccl", suffix, ".zip")
    )]
  })
  unique(data.table::rbindlist(output, use.names = TRUE, fill = TRUE)[
    nzchar(candidate_id) & nzchar(committee_id)
  ], by = c("cycle", "candidate_id", "committee_id", "designation"))
}

fec_read_leadership_pacs <- function(directory, cycles = c(2022L, 2024L, 2026L)) {
  output <- lapply(cycles, function(cycle) {
    path <- file.path(directory, paste0("leadership", cycle, ".csv"))
    if (!file.exists(path)) stop("Missing official FEC leadership PAC file: ", path, call. = FALSE)
    rows <- data.table::fread(path, encoding = "UTF-8", showProgress = FALSE)
    data.table::setnames(rows, names(rows), tolower(names(rows)))
    rows[, .(
      cycle = cycle,
      committee_id = toupper(trim_text(committee_id)),
      committee_name = trim_text(committee_name),
      sponsor_name = trim_text(sponsor_name),
      sponsor_name_key = normalize_variant_key(sponsor_name),
      source_url = paste0(
        "https://www.fec.gov/files/bulk-downloads/data.fec.gov/leadership",
        cycle, ".csv"
      )
    )]
  })
  unique(data.table::rbindlist(output, use.names = TRUE, fill = TRUE)[
    nzchar(committee_id) & nzchar(sponsor_name)
  ], by = c("cycle", "committee_id", "sponsor_name"))
}

fec_downstream_relationship <- function(values) {
  codes <- toupper(trim_text(values))
  labels <- c(
    "24A" = "Independent expenditure - oppose",
    "24C" = "Coordinated party expenditure",
    "24E" = "Independent expenditure - support",
    "24F" = "Communication cost - support",
    "24H" = "Honorarium to candidate",
    "24K" = "Contribution to nonaffiliated committee",
    "24N" = "Communication cost - oppose",
    "24P" = "Contribution to possible federal candidate",
    "24R" = "Election recount disbursement",
    "24Z" = "In-kind contribution to registered filer"
  )
  ifelse(codes %in% names(labels), unname(labels[codes]), paste0("FEC transaction ", codes))
}

fec_downstream_direction <- function(values) {
  codes <- toupper(trim_text(values))
  ifelse(codes %in% c("24A", "24N"), "Oppose", ifelse(codes %in% c("24E", "24F"), "Support", "Contribution / Other"))
}

# Bump whenever the pas2 PARSE changes - column typing, date handling, filtering,
# derived fields. The cache signature was the md5 of the source files alone, so a
# parser fix could not invalidate it: the MMDDYYYY date correction had no effect
# because the corrupted dates were served straight back out of the .rds. Source
# data changing is not the only reason a cached parse goes stale.
#   1 = original
#   2 = transaction_date forced to character (leading zeros preserved)
#   3 = version key scoped to the transaction-date year
#   4 = competing report versions remain unresolved without filing evidence
#   5 = rows inside a reused transaction id are partitioned by identity
#   6 = count_all default; conflicting sub-IDs and missing amounts refuse
#   7 = cycle IDs participate in the cache signature
#   8 = preserve image numbers and original FEC filing links per observation
#   9 = an FEC reload (FEC_PAS2_RELOAD_KEY equal; only the SUB_ID differs) counts once
FEC_PAS2_PARSER_VERSION <- "9"

# F11 (Paul, 2026-09-25): the key of an FEC reload, one record loaded twice under
# two SUB_IDs - same committee, report type and transaction id (version_key),
# same file number, same identity (candidate and committee counterparty, amount,
# date, type), same payee name and same memo code. Only rows equal on all of it
# count once, and a row without a transaction id or a file number never does.
# Genuine repeats carry distinct transaction ids and cross-file observations
# distinct file numbers, so neither meets here. tools/fec_transfer_graph.py's
# reload_key() is the same key for OTH transfers.
FEC_PAS2_RELOAD_KEY <- c("version_key", "file_num_sort", "identity_key", "recipient_name", "memo_code")

# Mirror the Python default: distinct observations remain provisional until
# supersession is established. PAS2 has no preserved filing-family headers,
# so file number alone cannot remove a row. Legacy count_latest/withhold are
# sensitivity scenarios, separately keyed, not production evidence rules.
FEC_PAS2_AMBIGUOUS_VERSION_POLICY <- "count_all"

fec_pas2_cache_file <- function(directory, cycles) {
  paths <- vapply(cycles, function(cycle) {
    suffix <- substr(as.character(cycle), 3L, 4L)
    file.path(directory, paste0("pas2", suffix), "itpas2.txt")
  }, character(1L))
  missing <- paths[!file.exists(paths)]
  if (length(missing)) stop("Missing official FEC committee-to-candidate file: ", missing[[1L]], call. = FALSE)
  signature <- paste(
    c(
      paste0("p", FEC_PAS2_PARSER_VERSION,
             FEC_PAS2_AMBIGUOUS_VERSION_POLICY),
      paste0(cycles, "_", substr(unname(tools::md5sum(paths)), 1L, 10L))
    ),
    collapse = "-"
  )
  cache_directory <- file.path(directory, "cache")
  if (!dir.exists(cache_directory) && !dir.create(cache_directory, recursive = TRUE, showWarnings = FALSE)) {
    stop("Could not create FEC beneficiary cache directory: ", cache_directory, call. = FALSE)
  }
  file.path(cache_directory, paste0("pas2_index_", signature, ".rds"))
}

fec_build_pas2_index <- function(directory, cycles) {
  headers <- c(
    "committee_id", "amendment_indicator", "report_type", "transaction_pgi",
    "image_number", "transaction_type", "entity_type", "recipient_name",
    "city", "state", "zip", "employer", "occupation", "transaction_date",
    "transaction_amount", "other_id", "candidate_id", "transaction_id",
    "file_num", "memo_code", "memo_text", "sub_id"
  )
  output <- lapply(cycles, function(target_cycle) {
    suffix <- substr(as.character(target_cycle), 3L, 4L)
    path <- file.path(directory, paste0("pas2", suffix), "itpas2.txt")
    # transaction_date MUST be read as character. FEC writes it as MMDDYYYY,
    # which fread infers as integer and then strips the leading zero from every
    # January-September date: 01022021 -> 1022021 -> parsed as %m=10 %d=22
    # %Y=021 -> "0021-10-22". That silently destroyed 1,146,540 of 1,624,528
    # rows (70.6%), and left no Jan-Sep date anywhere in any shipped workbook.
    # Indexed, not named: col.names is applied AFTER type inference, so at
    # colClasses time the columns are still V1..Vn and a name silently no-ops.
    rows <- data.table::fread(
      path, sep = "|", header = FALSE, fill = TRUE, quote = "",
      col.names = headers, encoding = "UTF-8", showProgress = FALSE,
      colClasses = list(character = which(headers == "transaction_date"))
    )
    rows <- rows[, .(
      cycle = target_cycle,
      committee_id = toupper(trim_text(committee_id)),
      candidate_id = toupper(trim_text(candidate_id)),
      amendment_indicator = toupper(trim_text(amendment_indicator)),
      report_type = toupper(trim_text(report_type)),
      image_number = trim_text(image_number),
      transaction_type = toupper(trim_text(transaction_type)),
      recipient_name = trim_text(recipient_name),
      destination_committee_id = toupper(trim_text(other_id)),
      transaction_date_raw = trim_text(transaction_date),
      transaction_amount = suppressWarnings(as.numeric(transaction_amount)),
      transaction_id = trim_text(transaction_id),
      file_num = suppressWarnings(as.numeric(file_num)),
      memo_code = toupper(trim_text(memo_code)),
      memo_text = trim_text(memo_text),
      sub_id = trim_text(sub_id)
    )]
    rows <- rows[nzchar(committee_id)]
    duplicate_ids <- rows$sub_id[nzchar(rows$sub_id) &
      (duplicated(rows$sub_id) | duplicated(rows$sub_id, fromLast = TRUE))]
    if (length(duplicate_ids)) {
      conflicts <- unique(rows[sub_id %in% duplicate_ids])
      if (any(duplicated(conflicts$sub_id))) {
        stop("Conflicting FEC sub-ID; reconcile preserved source evidence before indexing.", call. = FALSE)
      }
    }
    if (any(!is.finite(rows$transaction_amount)) ||
        any(abs(rows$transaction_amount * 100 - round(rows$transaction_amount * 100)) > 1e-6)) {
      stop("Missing or invalid PAS2 amount; no zero-valued measurement may be published.", call. = FALSE)
    }
    rows[, original_order := .I]
    parsed_date <- suppressWarnings(as.Date(rows$transaction_date_raw, format = "%m%d%Y"))
    rows[, transaction_date := ifelse(is.na(parsed_date), "", format(parsed_date, "%Y-%m-%d"))]
    rows[, version_key := ifelse(
      nzchar(transaction_id),
      paste(committee_id, report_type, transaction_id, sep = "|"),
      paste0("SUB|", ifelse(nzchar(sub_id), sub_id, original_order))
    )]
    rows[, amendment_priority := ifelse(
      amendment_indicator == "T", 2L,
      ifelse(amendment_indicator == "A", 1L, 0L)
    )]
    rows[, file_num_sort := ifelse(is.na(file_num), -Inf, file_num)]
    rows[, sub_id_sort := ifelse(nzchar(sub_id), sub_id, sprintf("%020d", original_order))]
    data.table::setorder(
      rows, version_key, -file_num_sort, -amendment_priority, -sub_id_sort,
      na.last = TRUE
    )
    rows[, exact_duplicate := as.integer(
      nzchar(sub_id) & duplicated(sub_id)
    )]
    # TRAN_ID is report-scoped AND it is only a label: filers reuse one schedule
    # line label (`SE.5`) on every row of every filing they make, so the key
    # collides across genuinely different expenditures. What a row IS is its
    # counterparty, its amount, its date and its transaction type; the version
    # question is asked only among rows that claim to be the same thing.
    rows[, identity_key := paste(candidate_id, destination_committee_id, round(transaction_amount * 100),
                                 transaction_date, transaction_type, sep = "|")]
    rows[, live := exact_duplicate == 0L]
    # Every grouped expression below is written so data.table's optimized
    # aggregates apply: a sentinel column instead of `x[live]` inside max(), and
    # sum() over a first-occurrence flag instead of uniqueN(). Written the
    # obvious way these five expressions are evaluated once per group, which on
    # 1.66M rows and ~1.6M groups costs over an hour per cycle.
    #
    # The same identity in a later filing is that transaction re-reported, and
    # the amendment contradicts the earlier version of it.
    rows[, live_file := ifelse(live, file_num_sort, -Inf)]
    rows[, latest_identity_file := max(live_file), by = .(version_key, identity_key)]
    rows[, superseded_by_amendment := as.integer(live & file_num_sort < latest_identity_file)]
    if (identical(FEC_PAS2_AMBIGUOUS_VERSION_POLICY, "count_all")) {
      rows[, superseded_by_amendment := 0L]
    }
    rows[, live := live & superseded_by_amendment == 0L]
    # The same identity several times inside the WINNING filing is ONE
    # itemization loaded again under a second sub-ID. The group is already keyed
    # by the FILER'S OWN transaction id, so two genuine equal gifts carry two
    # transaction ids and never meet here. Rows are already ordered by filing,
    # amendment and sub-ID descending, so the first one is the one to keep.
    rows[, repeated_itemization := 0L]
    rows[live == TRUE,
         repeated_itemization := as.integer(duplicated(.SD)),
         .SDcols = c("version_key", "identity_key")]
    # Identical attributes under a reused line label are not a duplicate ID. An
    # FEC reload is (F11): one record loaded again under a second SUB_ID. It
    # counts once - the highest SUB_ID, first in this order - and cross-file rows
    # and genuine repeats under distinct transaction ids stay counted and flagged.
    if (identical(FEC_PAS2_AMBIGUOUS_VERSION_POLICY, "count_all")) {
      rows[, repeated_itemization := 0L]
      rows[live == TRUE & nzchar(transaction_id) & !is.na(file_num),
           repeated_itemization := as.integer(duplicated(.SD)),
           .SDcols = FEC_PAS2_RELOAD_KEY]
    }
    rows[, live := live & repeated_itemization == 0L]
    # What remains in question: two surviving identities in DIFFERENT filings
    # are candidate VERSIONS of one transaction only when they name the same
    # candidate AND agree on the amount or on the date. An amendment corrects a
    # figure or a date; rows that share neither have nothing in common but a
    # recycled label - the same `SE.5` spent on the same candidate in two
    # filings months apart for different sums is two expenditures, not a
    # version. A blank candidate names nobody, so it never settles anything and
    # keeps the conservative merge across the whole key.
    surviving <- rows[live == TRUE, .(version_key, candidate_id, file_num_sort,
                                      amount_cents = round(transaction_amount * 100),
                                      transaction_date)]
    data.table::setorder(surviving, version_key, candidate_id, amount_cents, file_num_sort)
    surviving[, first_amount_file := as.integer(!duplicated(
      surviving, by = c("version_key", "candidate_id", "amount_cents", "file_num_sort")))]
    data.table::setorder(surviving, version_key, candidate_id, transaction_date, file_num_sort)
    surviving[, first_date_file := as.integer(!duplicated(
      surviving, by = c("version_key", "candidate_id", "transaction_date", "file_num_sort")))]
    data.table::setorder(surviving, version_key, file_num_sort)
    surviving[, first_file := as.integer(!duplicated(
      surviving, by = c("version_key", "file_num_sort")))]
    surviving[, blank := as.integer(!nzchar(candidate_id))]
    version_stats <- surviving[, .(surviving_files = sum(first_file),
                                   blank_rows = max(blank)), by = version_key]
    amount_stats <- surviving[, .(amount_files = sum(first_amount_file)),
                              by = .(version_key, candidate_id, amount_cents)]
    date_stats <- surviving[, .(date_files = sum(first_date_file)),
                            by = .(version_key, candidate_id, transaction_date)]
    rows[, `:=`(surviving_files = 0L, blank_rows = 0L, amount_files = 0L, date_files = 0L,
                amount_cents = round(transaction_amount * 100))]
    rows[version_stats, on = "version_key",
         `:=`(surviving_files = i.surviving_files, blank_rows = i.blank_rows)]
    rows[amount_stats, on = c("version_key", "candidate_id", "amount_cents"),
         amount_files := i.amount_files]
    rows[date_stats, on = c("version_key", "candidate_id", "transaction_date"),
         date_files := i.date_files]
    rows[, unresolved_version := as.integer(
      live & ((nzchar(candidate_id) & (amount_files > 1L | date_files > 1L)) |
                (blank_rows > 0L & surviving_files > 1L))
    )]
    rows[, conflicted := unresolved_version == 1L & nzchar(candidate_id)]
    # An identity that is alone in its group is a distinct transaction that
    # merely reuses the label. It is counted, and the reuse stays visible.
    distinct_identities <- rows[exact_duplicate == 0L, .(version_key, identity_key)]
    data.table::setorder(distinct_identities, version_key, identity_key)
    distinct_identities[, first_identity := as.integer(!duplicated(distinct_identities))]
    reuse <- distinct_identities[, .(identities = sum(first_identity)), by = version_key]
    rows[, tran_id_reused := 0L]
    rows[reuse, on = "version_key", tran_id_reused := as.integer(i.identities > 1L)]
    # Candidate-less rows are retained as evidence and never counted.
    rows[!nzchar(candidate_id) & memo_code != "X", unresolved_version := 1L]
    # The withheld dollars leave as an explicit share of the committee's
    # outflow, sized at what a "latest filing wins" build would have counted in
    # each withheld group rather than at the group's gross competing claims.
    rows[, withheld_group := paste(version_key, candidate_id, sep = "|")]
    rows[unresolved_version == 1L & blank_rows > 0L & surviving_files > 1L,
         withheld_group := version_key]
    rows[unresolved_version == 1L & !nzchar(candidate_id) &
           !(blank_rows > 0L & surviving_files > 1L),
         withheld_group := paste0("ROW|", original_order)]
    rows[, unverified_version := 0L]
    if (identical(FEC_PAS2_AMBIGUOUS_VERSION_POLICY, "count_all")) {
      rows[conflicted == TRUE, `:=`(unresolved_version = 0L, unverified_version = 1L)]
      # An unidentified candidate is a coverage gap, not a reason to erase a
      # reported expenditure. Keep its dollars in the unknown-identity bucket.
      rows[live & !nzchar(candidate_id) & memo_code != "X",
           `:=`(unresolved_version = 0L, unverified_version = 1L)]
      rows[live == TRUE, unverified_version := pmax(unverified_version, as.integer(.N > 1L)),
           by = .(version_key, identity_key)]
    }
    if (identical(FEC_PAS2_AMBIGUOUS_VERSION_POLICY, "count_latest")) {
      rows[conflicted == TRUE, `:=`(
        unresolved_version = 0L,
        unverified_version = 1L,
        superseded_by_amendment = as.integer(seq_len(.N) != which.max(file_num_sort))
      ), by = withheld_group]
    }
    rows[, unresolved_version_amount := 0]
    rows[unresolved_version == 1L, unresolved_version_amount := {
      pick <- which.max(file_num_sort)
      share <- rep(0, .N)
      share[[pick]] <- max(transaction_amount[[pick]], 0)
      share
    }, by = withheld_group]
    if (identical(FEC_PAS2_AMBIGUOUS_VERSION_POLICY, "count_all")) {
      rows[unresolved_version == 1L & live & memo_code != "X",
           unresolved_version_amount := pmax(transaction_amount, 0)]
    }
    rows[, include_in_total := ifelse(
      exact_duplicate == 0L & repeated_itemization == 0L &
        superseded_by_amendment == 0L & unresolved_version == 0L & memo_code != "X",
      "Yes", "No"
    )]
    rows[, exclusion_reason := ifelse(
      exact_duplicate == 1L, "Duplicate FEC sub-ID",
      ifelse(repeated_itemization == 1L, "Repeated itemization in one filing",
        ifelse(superseded_by_amendment == 1L, "Superseded filing amendment",
          ifelse(unresolved_version == 1L, "Unresolved filing relationship",
            ifelse(memo_code == "X", "Memo-only transaction", "Included"))))
    )]
    rows[, activity_type := fec_downstream_relationship(transaction_type)]
    rows[, support_oppose := fec_downstream_direction(transaction_type)]
    rows[, source_url := ifelse(
      grepl("^[0-9]+$", image_number),
      paste0("https://docquery.fec.gov/cgi-bin/fecimg/?", image_number),
      paste0("https://www.fec.gov/files/bulk-downloads/", target_cycle, "/pas2", suffix, ".zip")
    )]
    data.table::setorder(rows, original_order)
    rows[, c(
      "original_order", "version_key", "amendment_priority", "file_num_sort",
      "sub_id_sort", "transaction_date_raw", "identity_key", "live", "live_file",
      "latest_identity_file", "surviving_files", "blank_rows",
      "amount_files", "date_files", "amount_cents", "conflicted", "withheld_group"
    ) := NULL]
    rows
  })
  data.table::rbindlist(output, use.names = TRUE, fill = TRUE)
}

fec_read_pas2_index <- function(directory, cycles) {
  cache_file <- fec_pas2_cache_file(directory, cycles)
  if (file.exists(cache_file)) {
    index <- readRDS(cache_file)
    if (!data.table::is.data.table(index)) data.table::setDT(index)
    return(index)
  }
  index <- fec_build_pas2_index(directory, cycles)
  pending <- paste0(cache_file, ".pending-", Sys.getpid())
  saveRDS(index, pending, compress = "gzip")
  if (!file.rename(pending, cache_file)) {
    if (!file.copy(pending, cache_file, overwrite = FALSE)) {
      stop("Could not publish FEC beneficiary cache: ", cache_file, call. = FALSE)
    }
    unlink(pending)
  }
  index
}

fec_read_downstream_activity <- function(directory, recipient_universe, candidate_master,
                                         cycles = c(2022L, 2024L, 2026L)) {
  relevant_keys <- paste(recipient_universe$cycle, recipient_universe$committee_id, sep = "|")
  index <- fec_read_pas2_index(directory, cycles)
  activity <- index[paste(cycle, committee_id, sep = "|") %in% relevant_keys]
  if (!nrow(activity)) return(activity)
  # F12 (Paul approved 2026-09-25): a committee the roots' money reached only
  # by transfer counts from its gate on - the day that money could first have
  # arrived (a carried-over committee: this cycle's first day). A row with no
  # date cannot be ordered, so it counts, flagged date_unverified (D-040). The
  # roots and every recipient the industry funded directly count in full.
  activity[, date_unverified := 0L]
  date_rule <- NULL
  if (all(c("arrival_gate", "gate_source") %in% names(recipient_universe))) {
    gated <- unique(recipient_universe[gate_source %in% c("carryover", "in-cycle") & !is.na(arrival_gate),
                                       .(cycle, committee_id, arrival_gate)])
    activity[, gate := NA_character_]
    activity[gated, on = .(cycle, committee_id), gate := i.arrival_gate]
    day <- function(value) suppressWarnings(as.integer(gsub("-", "", value, fixed = TRUE)))
    dated <- !is.na(activity$transaction_date) & nzchar(activity$transaction_date)
    before <- !is.na(activity$gate) & dated & day(activity$transaction_date) < day(activity$gate)
    undated <- !is.na(activity$gate) & !dated
    included <- activity$include_in_total == "Yes"
    date_rule <- list(rule = "earliest_arrival", gated_committees = nrow(gated),
      rows_before_gate = sum(before), amount_before_gate = sum(activity$transaction_amount[before & included]),
      rows_date_unverified = sum(undated),
      amount_date_unverified = sum(activity$transaction_amount[undated & included]))
    activity[, date_unverified := as.integer(undated)]
    activity <- activity[!before]
    activity[, gate := NULL]
    if (!nrow(activity)) return(structure(activity, date_rule = date_rule))
  }
  candidate_columns <- candidate_master[, .(
    cycle, candidate_id, candidate_name, candidate_party, office, office_label,
    office_state, district, candidate_source_url = source_url
  )]
  activity <- merge(
    activity, candidate_columns, by = c("cycle", "candidate_id"),
    all.x = TRUE, sort = FALSE
  )
  # On Schedule E/PAS2 the recipient can be an advertising vendor. Its name
  # cannot identify the candidate. Even a historical candidate-master match
  # may conflict with the name as filed, so do not infer an identity here.
  # A PAS2 row can also carry no candidate ID at all - keep its provisional
  # amount in an unknown-identity bucket, named instead of printing an
  # empty parenthesis.
  activity[is.na(candidate_name) | !nzchar(candidate_name),
           candidate_name := paste0(
             "Unresolved candidate (",
             ifelse(nzchar(candidate_id), candidate_id, "no ID reported"), ")"
           )]
  activity[is.na(candidate_party) | !nzchar(candidate_party), candidate_party := "Other / Unclassified"]
  for (column in c("office", "office_label", "office_state", "district", "candidate_source_url")) {
    data.table::set(activity, which(is.na(activity[[column]])), column, "")
  }
  if (!is.null(date_rule)) attr(activity, "date_rule") <- date_rule
  activity
}

fec_build_committee_map <- function(recipient_universe, committee_master, candidate_master,
                                    candidate_links, leadership_pacs, downstream_activity) {
  candidate_columns <- candidate_master[, .(
    cycle, candidate_id, candidate_name, candidate_name_key, candidate_party, office, office_label,
    office_state, district
  )]
  linked <- merge(
    candidate_links[committee_id %in% recipient_universe$committee_id],
    candidate_columns, by = c("cycle", "candidate_id"), all.x = TRUE, sort = FALSE
  )
  linked[, mapping_source := source_url]

  sponsors <- merge(
    leadership_pacs[committee_id %in% recipient_universe$committee_id],
    candidate_columns,
    by.x = c("cycle", "sponsor_name_key"),
    by.y = c("cycle", "candidate_name_key"),
    all.x = TRUE,
    allow.cartesian = TRUE,
    sort = FALSE
  )
  if (nrow(sponsors)) {
    sponsors[, candidate_name := sponsor_name]
    sponsors[, relationship := "Leadership PAC sponsor"]
    sponsors[, mapping_source := source_url]
    sponsors <- sponsors[, .(
      cycle, committee_id, candidate_id, candidate_name, candidate_party,
      office, office_label, office_state, district, relationship, mapping_source
    )]
  }

  mappings <- data.table::rbindlist(list(
    linked[, .(
      cycle, committee_id, candidate_id, candidate_name, candidate_party,
      office, office_label, office_state, district, relationship, mapping_source
    )],
    sponsors
  ), use.names = TRUE, fill = TRUE)
  if (nrow(mappings)) {
    for (column in names(mappings)) data.table::set(mappings, which(is.na(mappings[[column]])), column, "")
    mappings <- unique(mappings, by = c("cycle", "committee_id", "candidate_id", "candidate_name", "relationship"))
  }

  master_fields <- committee_master[, .(
    cycle, committee_id,
    master_committee_name = committee_name,
    connected_organization,
    master_designation = designation,
    master_committee_type = committee_type
  )]
  base <- merge(recipient_universe, master_fields, by = c("cycle", "committee_id"), all.x = TRUE, sort = FALSE)
  for (column in c("master_committee_name", "connected_organization", "master_designation", "master_committee_type")) {
    data.table::set(base, which(is.na(base[[column]])), column, "")
  }
  base[!nzchar(committee_name), committee_name := master_committee_name]

  direct <- if (nrow(mappings)) mappings[, .(
    direct_person = fec_join_unique(candidate_name),
    direct_candidate_ids = fec_join_unique(candidate_id),
    direct_party = fec_join_unique(candidate_party),
    direct_offices = fec_join_unique(paste(
      office_label,
      trim_text(paste(office_state, district, sep = "-"))
    )),
    relationship = fec_join_unique(relationship),
    mapping_source = fec_join_unique(mapping_source)
  ), by = .(cycle, committee_id)] else data.table::data.table(
    cycle = integer(), committee_id = character(), direct_person = character(),
    direct_candidate_ids = character(), direct_party = character(),
    direct_offices = character(), relationship = character(), mapping_source = character()
  )
  result <- merge(base, direct, by = c("cycle", "committee_id"), all.x = TRUE, sort = FALSE)
  for (column in c("direct_person", "direct_candidate_ids", "direct_party", "direct_offices", "relationship", "mapping_source")) {
    data.table::set(result, which(is.na(result[[column]])), column, "")
  }
  result[, ownership_status := ifelse(
    nzchar(direct_person),
    ifelse(grepl("Leadership PAC", relationship, fixed = TRUE), "Candidate/officeholder-sponsored PAC", "Candidate-linked committee"),
    "Organization/committee; no individual ownership link in FEC files"
  )]
  result[!nzchar(relationship), relationship := "No direct candidate/officeholder link"]

  downstream <- downstream_activity[
    include_in_total == "Yes" & office %in% c("H", "S"),
    .(downstream_amount = sum(transaction_amount, na.rm = TRUE)),
    by = .(cycle, committee_id, candidate_id, candidate_name)
  ]
  if (nrow(downstream)) {
    data.table::setorder(downstream, cycle, committee_id, -downstream_amount, candidate_name)
    top <- downstream[, .SD[1L], by = .(cycle, committee_id)]
    data.table::setnames(top, c("candidate_id", "candidate_name", "downstream_amount"), c(
      "top_downstream_candidate_id", "top_downstream_person", "preliminary_top_downstream_amount"
    ))
    result <- merge(result, top, by = c("cycle", "committee_id"), all.x = TRUE, sort = FALSE)
  } else {
    result[, `:=`(
      top_downstream_candidate_id = "",
      top_downstream_person = "",
      preliminary_top_downstream_amount = 0
    )]
  }
  for (column in c("top_downstream_candidate_id", "top_downstream_person")) {
    data.table::set(result, which(is.na(result[[column]])), column, "")
  }
  result[is.na(preliminary_top_downstream_amount), preliminary_top_downstream_amount := 0]
  result[, lookup_key := paste(cycle, committee_id, sep = "|")]
  # The tall mappings frame is the ONLY place the leadership-PAC sponsor links
  # survive: `direct` collapses them into a `; `-joined relationship string, and
  # a joined string cannot say which candidate a given relationship belongs to.
  # It travels as an attribute rather than as a new column or a list return
  # because every existing caller treats this function's value as the committee
  # map data.table itself - it is indexed by column, subset by row and written
  # straight into the JSON payload, none of which an attribute disturbs.
  data.table::setattr(result, "mentat_committee_mappings", mappings)
  result
}

# Accessor for the tall committee -> candidate mappings carried on a
# fec_build_committee_map result. Returns NULL for a map built by an older
# vintage of this file, which callers must treat as "no sponsor rows".
fec_committee_map_mappings <- function(committee_map) {
  attr(committee_map, "mentat_committee_mappings", exact = TRUE)
}

fec_aggregate_downstream_activity <- function(downstream_activity, recipient_universe) {
  require_package("jsonlite")
  if (!nrow(downstream_activity)) return(data.table::data.table())
  source_names <- recipient_universe[, .(
    cycle, committee_id,
    source_committee = committee_name,
    source_recipient_category = recipient_category
  )]
  rows <- merge(
    downstream_activity,
    source_names,
    by = c("cycle", "committee_id"),
    all.x = TRUE,
    sort = FALSE
  )
  rows[is.na(source_committee), source_committee := ""]
  rows[is.na(source_recipient_category), source_recipient_category := ""]
  if (!"date_unverified" %in% names(rows)) rows[, date_unverified := 0L]
  result <- rows[, .(
    reported_amount = sum(transaction_amount, na.rm = TRUE),
    excluded_amount = sum(transaction_amount[include_in_total != "Yes"], na.rm = TRUE),
    preliminary_included_amount = sum(transaction_amount[include_in_total == "Yes"], na.rm = TRUE),
    source_rows = .N,
    included_rows = sum(include_in_total == "Yes"),
    excluded_rows = sum(include_in_total != "Yes"),
    superseded_rows = sum(superseded_by_amendment == 1L),
    memo_rows = sum(memo_code == "X"),
    exact_duplicate_rows = sum(exact_duplicate == 1L),
    repeated_itemization_rows = sum(repeated_itemization == 1L),
    unresolved_version_rows = sum(unresolved_version == 1L),
    # The withheld dollars this committee cannot place. They belong in its
    # outflow denominator, not on a blacklist that voids its clean activity.
    unresolved_version_amount = sum(unresolved_version_amount, na.rm = TRUE),
    unverified_version_rows = sum(unverified_version == 1L),
    date_unverified_rows = sum(date_unverified == 1L),
    transfer_records = as.character(jsonlite::toJSON(data.table::data.table(
      destination = destination_committee_id, file_num = file_num,
      transaction_id = transaction_id, sub_id = sub_id, date = transaction_date,
      cents = round(transaction_amount * 100), type = transaction_type
    )[include_in_total == "Yes" & transaction_type %in% c("24G", "24K")],
      dataframe = "rows", auto_unbox = TRUE, digits = NA)),
    filing_records = as.character(jsonlite::toJSON(data.table::data.table(
      date = transaction_date, amount = transaction_amount, type = transaction_type,
      file_num = file_num, transaction_id = transaction_id, sub_id = sub_id,
      source_url = source_url
    )[include_in_total == "Yes"],
      dataframe = "rows", auto_unbox = TRUE, digits = NA)),
    first_activity_date = fec_min_text(transaction_date),
    last_activity_date = fec_max_text(transaction_date),
    source_url = fec_mode_text(source_url),
    candidate_source_url = fec_mode_text(candidate_source_url)
  ), by = .(
    cycle,
    source_committee,
    source_committee_id = committee_id,
    source_recipient_category,
    candidate_name,
    candidate_id,
    candidate_party,
    office,
    office_label,
    office_state,
    district,
    activity_type,
    support_oppose,
    transaction_type
  )]
  data.table::setorder(
    result, cycle, source_committee, -preliminary_included_amount,
    candidate_name, activity_type
  )
  result
}

fec_beneficiary_reach <- function(funding, transfer_edges, candidate_links = NULL, networks = NULL) {
  # Multi-source breadth-first traversal: one spending committee per view, even
  # when two funded roots or a diamond reach it. Routes explain association;
  # they never multiply candidate activity or allocate the funding to it.
  # With `networks` (fec_transfer_context, F15) each view follows ITS OWN walk's
  # counted transfers, and a committee its roots' previous-cycle money opened
  # (carryover) is reached too, with that route; each row carries its gate.
  result <- list()
  if (!nrow(funding)) return(data.table::data.table(cycle = integer(), view = character(),
    company = character(), source_committee_id = character(), representative_root = character(),
    representative_path = character()))
  by_view <- !is.null(networks) && NROW(networks$gates) > 0L
  scopes <- unique(funding[, .(cycle, view, company)])
  for (i in seq_len(nrow(scopes))) {
    scope <- scopes[i]
    seeds <- funding[cycle == scope$cycle & view == scope$view & company == scope$company]
    seeds <- seeds[order(source_committee_id)]
    seed_mask <- if ("traversal_seed" %in% names(seeds)) seeds$traversal_seed else seeds$preliminary_crypto_funding > 0
    visited <- seeds$source_committee_id[seed_mask]
    frontier <- visited
    roots <- stats::setNames(visited, visited)
    routes <- stats::setNames(visited, visited)
    stops <- if (is.null(candidate_links)) character() else
      candidate_links[cycle == scope$cycle & designation %in% c("P", "A"), committee_id]
    in_scope <- function(table) table[cycle == scope$cycle & view == scope$view & company == scope$company]
    edges <- if (by_view) {
      if (NROW(networks$pairs)) in_scope(networks$pairs)[order(source, destination)] else data.table::data.table()
    } else if (is.null(transfer_edges) || !nrow(transfer_edges)) data.table::data.table() else
      transfer_edges[cycle == scope$cycle & amount > 0][order(source, destination)]
    carried <- if (by_view && NROW(networks$carryover)) in_scope(networks$carryover)[order(committee_id)] else NULL
    repeat {
      while (length(frontier) && nrow(edges)) {
        outgoing <- edges[source %in% setdiff(frontier, stops)]
        next_frontier <- character()
        for (j in seq_len(nrow(outgoing))) {
          destination <- outgoing$destination[j]
          if (destination %in% c(visited, stops)) next
          origin <- outgoing$source[j]
          visited <- c(visited, destination)
          roots[destination] <- roots[origin]
          routes[destination] <- paste(routes[origin], destination, sep = " > ")
          next_frontier <- c(next_frontier, destination)
        }
        frontier <- next_frontier
      }
      # Then the committees opened by the previous cycle's transfers, which no
      # in-cycle route reaches, and everything their counted transfers reach.
      if (is.null(carried)) break
      opened <- carried[!committee_id %in% c(visited, stops)]
      carried <- NULL
      if (!nrow(opened)) break
      visited <- c(visited, opened$committee_id)
      roots[opened$committee_id] <- opened$representative_root
      routes[opened$committee_id] <- opened$representative_path
      frontier <- opened$committee_id
    }
    for (id in setdiff(seeds$source_committee_id, visited)) {
      visited <- c(visited, id); roots[id] <- id; routes[id] <- id
    }
    result[[i]] <- data.table::data.table(cycle = scope$cycle, view = scope$view,
      company = scope$company, source_committee_id = visited,
      representative_root = unname(roots[visited]), representative_path = unname(routes[visited]))
    if (by_view) {
      gates <- in_scope(networks$gates)
      result[[i]][gates, on = .(source_committee_id = committee_id),
                  `:=`(arrival_gate = i.arrival_gate, gate_source = i.gate_source)]
      if (!"gate_source" %in% names(result[[i]])) result[[i]][, `:=`(arrival_gate = NA_character_, gate_source = NA_character_)]
      result[[i]][is.na(gate_source), gate_source := "recipient"]
    }
  }
  data.table::rbindlist(result, fill = TRUE)
}

# F15 (Paul approved 2026-09-26): a company view dates each committee its money
# reached by transfer from THAT company's own arrival - its own walk's gate - never
# from another company's earlier money. downstream_activity is already dated from
# the industry-wide gate, which is never later than any company's (a company's
# roots are among the industry's), so a company whose gate is later drops the rows
# before it; an undated row still counts (D-040). A committee the company funded
# directly counts in full, as a funded recipient does for the industry. Returns
# (cycle, view, company, source_committee_id, view_gate) for the rows to re-date.
fec_company_view_gates <- function(networks, funded, industry_universe = NULL) {
  empty <- data.table::data.table(cycle = integer(), view = character(), company = character(),
                                  source_committee_id = character(), view_gate = character())
  if (is.null(networks) || !NROW(networks$gates)) return(empty)
  gated <- networks$gates[view == "Company" & gate_source %in% c("carryover", "in-cycle") & !is.na(arrival_gate),
    .(cycle = as.integer(cycle), view, company, source_committee_id = committee_id, view_gate = arrival_gate)]
  direct <- unique(funded[, .(cycle = as.integer(cycle), company, source_committee_id)])
  gated <- gated[!direct, on = .(cycle, company, source_committee_id)]
  industry <- networks$gates[view == "Industry" & gate_source %in% c("carryover", "in-cycle") & !is.na(arrival_gate),
    .(cycle = as.integer(cycle), source_committee_id = committee_id, industry_gate = arrival_gate)]
  # The industry dates a committee it funded directly (a recipient) in full.
  if (!is.null(industry_universe) && "gate_source" %in% names(industry_universe)) {
    recipients <- industry_universe[gate_source %in% c("root", "recipient"),
      .(cycle = as.integer(cycle), source_committee_id = committee_id)]
    industry <- industry[!recipients, on = .(cycle, source_committee_id)]
  }
  gated[industry, on = .(cycle, source_committee_id), industry_gate := i.industry_gate]
  if (!"industry_gate" %in% names(gated)) gated[, industry_gate := NA_character_]
  gated <- gated[is.na(industry_gate) | fec_day(view_gate) > fec_day(industry_gate)]
  if (!nrow(gated)) return(empty)
  gated[, industry_gate := NULL]
  gated[]
}

# fec_aggregate_beneficiary_downstream over each (cycle, committee, gate) the company
# views need: rows dated on or after the gate, and undated rows, per gate.
fec_gated_beneficiary_downstream <- function(downstream_activity, view_gates) {
  combos <- unique(view_gates[, .(cycle, committee_id = source_committee_id, view_gate)])
  if (!nrow(combos) || !nrow(downstream_activity)) return(data.table::data.table())
  rows <- downstream_activity[committee_id %in% combos$committee_id]
  rows[, cycle_key := as.integer(cycle)]
  rows <- combos[, .(cycle_key = cycle, committee_id, view_gate)][rows, on = .(cycle_key, committee_id),
    nomatch = NULL, allow.cartesian = TRUE]
  dated <- !is.na(rows$transaction_date) & nzchar(rows$transaction_date)
  rows <- rows[!dated | fec_day(transaction_date) >= fec_day(view_gate)]
  if (!nrow(rows)) return(data.table::data.table())
  rows[, committee_id := paste(committee_id, view_gate, sep = "|")]
  result <- fec_aggregate_beneficiary_downstream(rows)
  if (!nrow(result)) return(result)
  parts <- data.table::tstrsplit(result$source_committee_id, "|", fixed = TRUE)
  result[, `:=`(source_committee_id = parts[[1L]], view_gate = parts[[2L]])]
  result[]
}

fec_build_beneficiary_tables <- function(raw_export, downstream_activity, transfer_edges = NULL,
                                        candidate_links = NULL, networks = NULL, industry_universe = NULL) {
  downstream <- fec_aggregate_beneficiary_downstream(downstream_activity)
  funded <- raw_export[
    include_in_analysis == "Yes" & nzchar(committee_id),
    .(
      preliminary_crypto_funding = sum(amount, na.rm = TRUE),
      source_committee = fec_mode_text(recipient)
    ),
    by = .(cycle, company, source_committee_id = committee_id)
  ]
  industry <- funded[, .(
    preliminary_crypto_funding = sum(preliminary_crypto_funding, na.rm = TRUE),
    source_committee = fec_mode_text(source_committee),
    traversal_seed = any(preliminary_crypto_funding > 0)
  ), by = .(cycle, source_committee_id)]
  industry[, `:=`(view = "Industry", company = "Industry Total")]
  company <- data.table::copy(funded)
  company[, `:=`(view = "Company", traversal_seed = preliminary_crypto_funding > 0)]
  funding <- data.table::rbindlist(list(industry, company), use.names = TRUE, fill = TRUE)
  reach <- fec_beneficiary_reach(funding, transfer_edges, candidate_links, networks)
  if (!nrow(downstream)) return(list(bridge = data.table::data.table(), bridge_all = data.table::data.table(),
    summary = data.table::data.table(), reach = reach, measurement_ledger = data.table::data.table()))
  old_bridge <- merge(funding, downstream, by = c("cycle", "source_committee_id"),
                      allow.cartesian = TRUE, sort = FALSE)
  # Preserve funding at its actual recipient. A descendant's observed spending
  # does not prove it received the root's company dollars.
  if (is.null(transfer_edges) || !nrow(transfer_edges)) {
    # Preserve legacy row ordering, including ties, when there is no route input.
    funding[, `:=`(representative_root = source_committee_id, representative_path = source_committee_id)]
  } else {
    funding <- merge(reach[, .(cycle, view, company, source_committee_id, representative_root, representative_path)],
                     funding, by = c("cycle", "view", "company", "source_committee_id"),
                     all.x = TRUE, sort = FALSE)
  }
  funding[is.na(preliminary_crypto_funding), preliminary_crypto_funding := 0]
  funding[is.na(source_committee) | !nzchar(source_committee), source_committee := source_committee_id]
  view_gates <- fec_company_view_gates(networks, funded, industry_universe)
  if (!nrow(view_gates)) {
    bridge <- merge(
      funding, downstream,
      by = c("cycle", "source_committee_id"),
      allow.cartesian = TRUE,
      sort = FALSE
    )
  } else {
    funding[, cycle_key := as.integer(cycle)]
    funding[view_gates[, .(cycle_key = cycle, view, company, source_committee_id, view_gate)],
            on = .(cycle_key, view, company, source_committee_id), view_gate := i.view_gate]
    funding[, cycle_key := NULL]
    later <- fec_gated_beneficiary_downstream(downstream_activity, view_gates)
    bridge <- data.table::rbindlist(list(
      merge(funding[is.na(view_gate)][, view_gate := NULL], downstream,
            by = c("cycle", "source_committee_id"), allow.cartesian = TRUE, sort = FALSE),
      if (nrow(later)) merge(funding[!is.na(view_gate)], later, by = c("cycle", "source_committee_id", "view_gate"),
            allow.cartesian = TRUE, sort = FALSE)[, view_gate := NULL]), use.names = TRUE, fill = TRUE)
    funding[, view_gate := NULL]
  }
  bridge[, candidate_key := ifelse(
    nzchar(candidate_id), paste0("CAND:", candidate_id), paste0("NAME:", normalize_variant_key(candidate_name))
  )]
  bridge[, bridge_key := paste(cycle, view, company, source_committee_id, candidate_key, sep = "|")]
  data.table::setorder(bridge, cycle, view, company, -preliminary_downstream_amount, candidate_name)

  # Preserve the measurement change before top-100 presentation filtering.
  # This compares algorithms on identical current inputs, not published vintages.
  ledger_keys <- c("cycle", "view", "company", "candidate_id")
  before <- old_bridge[, .(before_amount = sum(preliminary_downstream_amount)), by = ledger_keys]
  after <- bridge[, .(after_amount = sum(preliminary_downstream_amount)), by = ledger_keys]
  measurement_ledger <- merge(before, after, by = ledger_keys, all = TRUE, sort = TRUE)
  measurement_ledger[is.na(before_amount), before_amount := 0]
  measurement_ledger[is.na(after_amount), after_amount := 0]
  measurement_ledger[, delta := after_amount - before_amount]
  measurement_ledger[, basis := "Same source rows: direct-only association versus unique reachable spending committees; not a published-report comparison"]
  bridge_all <- data.table::copy(bridge)

  summary <- fec_aggregate_beneficiary_summary(bridge)[office %in% c("H", "S")]
  data.table::setorder(summary, cycle, view, company, -preliminary_downstream_amount, candidate_name)
  summary[, preliminary_rank := seq_len(.N), by = .(cycle, view, company)]
  summary <- summary[preliminary_rank <= 100L]
  retained <- unique(summary[, .(cycle, view, company, candidate_key)])
  bridge <- merge(
    bridge,
    retained,
    by = c("cycle", "view", "company", "candidate_key"),
    sort = FALSE
  )
  data.table::setorder(bridge, cycle, view, company, -preliminary_downstream_amount, candidate_name)
  list(bridge = bridge, bridge_all = bridge_all, summary = summary, reach = reach, measurement_ledger = measurement_ledger)
}

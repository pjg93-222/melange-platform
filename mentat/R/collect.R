if (!exists("MENTAT_INDUSTRY")) MENTAT_INDUSTRY <- "Crypto"
contribution_columns <- function() {
  c(
    "sub_id", "transaction_id", "original_sub_id", "contributor_id",
    "contributor_name", "contributor_first_name", "contributor_last_name",
    "contributor_suffix", "contributor_employer", "contributor_occupation",
    "contributor_street_1", "contributor_street_2", "contributor_city",
    "contributor_state", "contributor_zip",
    "contributor_aggregate_ytd", "contribution_receipt_amount",
    "contribution_receipt_date", "committee_id", "committee_name",
    "recipient_committee_type", "recipient_committee_designation", "entity_type",
    "election_type", "fec_election_type_desc", "receipt_type_full", "memo_code",
    "memoed_subtotal", "memo_text", "conduit_committee_id",
    "conduit_committee_name", "amendment_indicator", "report_year", "report_type",
    "file_num", "filing_form", "receipt_type", "line_number_label",
    "back_reference_transaction_id", "back_reference_schedule_name",
    "two_year_transaction_period", "line_number", "pdf_url", "load_date",
    "industry", "company", "matched_variant", "phase", "match_confidence",
    "scraped_at", "is_conduit_leg", "is_memo_leg", "is_passthrough_leg",
    "memo_review", "possible_amendment", "superseded_by_amendment",
    "duplicate_filing_version", "duplicate_memo", "is_refund",
    "is_entity_contributor",
    "company_match_status", "company_match_reason", "excluded_reason"
  )
}

# A name match identifies the named party, not its role in the transaction.
# Keep conversions and disclosure-only asset memos in raw evidence, but never
# add them as another contribution from the exchange or original donor.
fec_receipt_rules <- local({
  cached <- NULL
  function() {
    if (is.null(cached)) cached <<- jsonlite::fromJSON(
      file.path(mentat_input_dir(), "fec_receipt_rules.json"), simplifyVector = FALSE)
    cached
  }
})

# A blank line number is missing information, not evidence against the row.
# FEC transaction ids carry the schedule line in their prefix (SA17.621125),
# so recover it before classifying. A row with neither a line nor a derivable
# one stays unresolved: counted, flagged, never refused.
fec_receipt_line_code <- function(line_number, transaction_id) {
  line <- sub("^SA", "", gsub("[() ]", "", line_number))
  derived <- sub("^SA([0-9]+[A-Z]*)\\..*$", "\\1", transaction_id)
  derived[derived == transaction_id] <- ""
  blank <- !nzchar(line)
  line[blank] <- derived[blank]
  line
}

# Row-level eligibility that does not depend on the receipt role, so the
# disclosure pairing below can ask "is there a COUNTED line-11 gift" without
# recursing back into fec_receipt_role().
fec_attribution_status <- function(rows, company_variants = list(), connected_orgs = NULL) {
  policy <- jsonlite::fromJSON(file.path(mentat_project_root(), "config", "attribution-policy.json"))
  column <- function(name, default = "") {
    value <- if (name %in% names(rows)) rows[[name]] else rep(default, nrow(rows))
    value[is.na(value)] <- ""
    toupper(trim_text(value))
  }
  normalize <- function(value) trim_text(gsub("[^A-Z0-9]+", " ", value))
  match <- column("company_match_status", "accepted")
  company <- column("company")
  reason <- tolower(column("company_match_reason"))
  explicit <- nzchar(column("excluded_reason")) & !column("excluded_reason") %in% toupper(policy$non_evidentiary_exclusions)
  for (prefix in policy$explicit_rejection_prefixes) {
    explicit <- explicit | (match %in% c("REJECTED", "EXCLUDED") & startsWith(reason, prefix))
  }
  individual <- column("entity_type", "IND") == "IND"
  own <- normalize(ifelse(individual, column("contributor_employer"), column("contributor_name")))
  occupation <- normalize(column("contributor_occupation"))
  unknown <- strsplit(policy$unknown_tokens, " ", fixed = TRUE)[[1L]]
  distinct <- unique(own)
  has_name <- vapply(strsplit(distinct, " ", fixed = TRUE), function(tokens) any(!tokens %in% c("", unknown)), logical(1L))
  organization <- has_name[match(own, distinct)] &
    (column("entity_type") %in% c("ORG", "PAC", "COM", "CCM", "PTY") |
     grepl(policy$organization_pattern, own, perl = TRUE))
  named <- recipient <- rep(FALSE, nrow(rows))
  affiliations_path <- file.path(mentat_input_dir(), "verified_company_affiliations.json")
  affiliations <- if (file.exists(affiliations_path)) jsonlite::fromJSON(affiliations_path, simplifyVector = FALSE)$records else list()
  for (key in unique(company)) {
    indices <- which(company == key)
    supplied <- unlist(company_variants[toupper(names(company_variants)) == key], use.names = FALSE)
    registered <- Filter(function(a) toupper(a$company) == key, affiliations)
    names <- unique(normalize(toupper(c(key, supplied, unlist(lapply(registered, function(a) c(a$entity_name, unlist(a$aliases))))))))
    names <- names[nzchar(names)]
    names_text <- function(text) {
      answer <- rep(FALSE, length(text))
      for (name in names) {
        answer <- answer | grepl(paste0(" ", name, " "), paste0(" ", text, " "), fixed = TRUE)
        squashed <- gsub(" ", "", name, fixed = TRUE)
        if (nchar(squashed) >= 8L) answer <- answer | grepl(squashed, gsub(" ", "", text, fixed = TRUE), fixed = TRUE)
      }
      answer
    }
    named[indices] <- names_text(own[indices]) | (individual[indices] & names_text(occupation[indices]))
    for (affiliation in registered) {
      if (identical(affiliation$relationship, "connected_pac") &&
          nzchar(affiliation$source_url %||% "") && nzchar(affiliation$evidence_id %||% "")) {
        dates <- substr(column("contribution_receipt_date")[indices], 1L, 10L)
        valid <- nzchar(dates) & dates >= affiliation$valid_from & dates <= (affiliation$valid_to %||% "9999-12-31")
        recipient[indices] <- recipient[indices] | (individual[indices] & valid &
          column("committee_id")[indices] == toupper(affiliation$entity_id %||% ""))
      }
    }
    if (!is.null(connected_orgs) && nrow(connected_orgs)) {
      org_key <- paste(connected_orgs$cycle, toupper(connected_orgs$committee_id))
      org <- connected_orgs$connected_organization[match(paste(column("two_year_transaction_period")[indices], column("committee_id")[indices]), org_key)]
      org[is.na(org)] <- ""
      recipient[indices] <- recipient[indices] | (individual[indices] & names_text(normalize(toupper(org))))
    }
  }
  status <- ifelse(match == "ACCEPTED", "accepted", "unverified")
  status[match != "ACCEPTED" & organization & !named & !recipient & nzchar(company)] <- "contradicted_by_reported_organization"
  status[match != "ACCEPTED" & named] <- "company_named_unverified"
  status[match != "ACCEPTED" & recipient] <- "recipient_supported"
  status[explicit] <- "explicit_source_exclusion"
  status
}

fec_receipt_row_eligible <- function(rows) {
  value <- function(field, default) {
    if (field %in% names(rows)) rows[[field]] else rep(default, nrow(rows))
  }
  status <- if ("company_attribution_status" %in% names(rows)) rows$company_attribution_status else fec_attribution_status(rows)
  keep <- !status %in% c("contradicted_by_reported_organization", "explicit_source_exclusion") &
    as.integer(value("duplicate_memo", 0L)) == 0L
  keep[is.na(keep)] <- FALSE
  keep
}

# "Previously disclosed" is a claim of a repeat, not proof of one on its own.
# mentat_memo_duplicate_plan() (schema.R) removes a disclosure memo with an
# explicit, same-filing FEC back reference, or (F13, 2026-09-26) when a counted
# parent matches the statement: same company, committee, contributor and
# amount, dated on or before the memo. Equal committee/cycle/amount without
# the memo's own statement is not proof.

fec_receipt_role <- function(rows) {
  column <- function(name) {
    if (!name %in% names(rows)) return(rep("", nrow(rows)))
    value <- toupper(trim_text(rows[[name]]))
    value[is.na(value)] <- ""
    value
  }
  normalized <- function(x) trim_text(gsub("[^A-Z0-9]+", " ", x))
  receipt_description <- normalized(column("receipt_type_full"))
  memo_description <- normalized(column("memo_text"))
  description <- trim_text(paste(receipt_description, memo_description))
  role <- rep("unverified_receipt_role", nrow(rows))
  # FEC form-specific receipt lines. A line number alone is ambiguous (17 is
  # contributions on F3P, but other receipts on F3X).
  # https://www.fec.gov/campaign-finance-data/about-campaign-finance-data/about-receipts-data/
  form <- column("filing_form")
  line <- fec_receipt_line_code(column("line_number"), column("transaction_id"))
  rules <- fec_receipt_rules()
  for (f in names(rules$forms)) for (r in names(rules$forms[[f]])) {
    role[form == f & line %in% unlist(rules$forms[[f]][[r]])] <- r
  }
  # "Other federal receipts" includes genuine hybrid-PAC and party-account
  # contributions. The line label alone cannot establish economic meaning.
  special <- form == "F3X" & line == "17" & grepl(rules$special_account_pattern, description, perl = TRUE)
  exact_descriptions <- normalized(toupper(unlist(rules$non_giving_exact_descriptions)))
  receipt_exact <- grepl(rules$non_giving_exact_pattern, receipt_description, perl = TRUE) |
    receipt_description %in% exact_descriptions
  memo_exact <- grepl(rules$non_giving_exact_pattern, memo_description, perl = TRUE) |
    memo_description %in% exact_descriptions
  exact_non_giving <- receipt_exact | memo_exact
  receipt_event_description <- trim_text(gsub(
    rules$special_account_pattern, "", receipt_description, perl = TRUE))
  memo_event_description <- trim_text(gsub(
    rules$special_account_pattern, "", memo_description, perl = TRUE))
  receipt_non_giving_event <- grepl(rules$non_giving_event_pattern, receipt_event_description, perl = TRUE)
  memo_non_giving_event <- grepl(rules$non_giving_event_pattern, memo_event_description, perl = TRUE)
  non_giving <- exact_non_giving | receipt_non_giving_event | memo_non_giving_event |
    grepl(rules$non_giving_event_pattern, description, perl = TRUE)
  giving_description <- trim_text(paste(
    gsub(rules$special_account_pattern, "", ifelse(receipt_exact | receipt_non_giving_event, "", receipt_description), perl = TRUE),
    gsub(rules$special_account_pattern, "", ifelse(memo_exact | memo_non_giving_event, "", memo_description), perl = TRUE)
  ))
  # A redesignation moves money that was already given from one election to
  # another. The word CONTRIBUTION beside it names that same money, so it is
  # not a second, conflicting claim and must not make the row look
  # self-contradictory: the redesignation classification wins.
  redesignation <- grepl(rules$redesignation_event_pattern, description, perl = TRUE)
  giving_event <- grepl(rules$giving_event_pattern, giving_description, perl = TRUE) &
    !redesignation
  role[special & !non_giving] <- "contribution"
  role[role == "unverified_other_receipt" & non_giving & !special & !giving_event] <- "other_receipt"
  role[special & non_giving] <- "unverified_other_receipt"
  role[special & exact_non_giving & !giving_event] <- "other_receipt"
  transfer_event <- grepl(rules$transfer_event_pattern, receipt_description, perl = TRUE)
  role[role == "unverified_other_receipt" & transfer_event & !giving_event] <- "transfer_receipt"
  # A CONTRADICTION, not an ambiguity: one row's own text both asserts a gift
  # and asserts the money is not a gift. Missing or ambiguous evidence is
  # counted and flagged (2026-09-17 ruling); a self-contradicting row is the
  # only thing fec_assert_receipt_evidence() still stops the export for. The
  # role must already be unresolved, so this never refuses a row that today's
  # classifier accepts.
  role[role %in% c("unverified_other_receipt", "unverified_receipt_role") &
       non_giving & giving_event] <- "unverified_contradicted_receipt"
  # Names identify parties, never the economic role of an unknown receipt.
  sale <- grepl(rules$sale_identity_pattern, description, perl = TRUE) &
    grepl(rules$sale_event_pattern, description, perl = TRUE)
  role[sale] <- "asset_liquidation"
  memo <- column("memo_code") == "X" | column("memoed_subtotal") %in% c("1", "TRUE") |
    column("is_memo_leg") %in% c("1", "TRUE")
  disclosed_asset <- grepl(rules$disclosure_pattern, description, perl = TRUE)
  # Disclosure wording is a clue, not proof that a counted counterpart exists.
  # Known non-contribution lines retain that classification; other disclosure
  # memos need a linked parent or review before a changed total is published.
  role[memo & disclosed_asset & !role %in% c("other_receipt", "asset_liquidation")] <- "unverified_disclosure_memo"
  # Proven repeat -> excluded. Unproven claim -> the row keeps the unverified
  # role, stays countable, and carries receipt_role_reason
  # "disclosure_memo_unmatched" so a reader can filter it.
  # A refund promise is not an observed refund. Retain the receipt and require
  # the actual adjustment before certifying a net-giving amount.
  role[role == "contribution" & grepl(rules$refund_review_pattern, description, perl = TRUE)] <- "unverified_refund_adjustment"
  role
}

fec_funding_source <- function(entity_type) {
  entity <- toupper(trim_text(entity_type))
  result <- rep("Unverified funding source", length(entity))
  result[entity == "IND"] <- "Individual"
  result[entity == "ORG"] <- "Organization (treasury attribution unverified)"
  result[entity %in% c("PAC", "COM", "CCM", "PTY")] <- "PAC / committee"
  result
}

fec_receipt_role_excluded <- function(role) {
  role %in% c("asset_liquidation", "other_receipt", "disclosure_memo")
}

# The only refusal that survives the 2026-09-17 ruling. Ambiguity is counted
# and flagged; contradiction stops publication.
fec_receipt_role_contradicted <- function(role) {
  role %in% "unverified_contradicted_receipt"
}

# Reader-facing pair for a countable row whose role could not be resolved:
# receipt_role_unverified = 1 plus the reason a reader can filter on.
fec_receipt_role_unverified <- function(role) {
  as.integer(fec_receipt_role_unresolved(role))
}

fec_receipt_role_reason <- function(role) {
  reason <- ifelse(fec_receipt_role_unresolved(role), as.character(role), "")
  reason[is.na(reason)] <- ""
  reason[role %in% "unverified_disclosure_memo"] <- "disclosure_memo_unmatched"
  reason
}

fec_receipt_role_unresolved <- function(role) {
  is.na(role) | role %in% c("", "intermediary_role_review") | startsWith(role, "unverified_")
}

# One eligibility predicate for summaries, memo parent checks, and QA.
# Unknowns remain in raw/provisional views; publication requires evidence.
fec_receipt_countable <- function(rows, verified_only = FALSE) {
  role <- fec_receipt_role(rows)
  keep <- fec_receipt_row_eligible(rows) & !fec_receipt_role_excluded(role)
  if (verified_only) keep <- keep & !fec_receipt_role_unresolved(role)
  keep[is.na(keep)] <- FALSE
  keep
}

# 2026-09-17 ruling: a cross-verification step may remove or zero a row only
# when the evidence CONTRADICTS it. Missing or ambiguous information is never
# grounds for exclusion - those rows stay counted and carry
# receipt_role_unverified = 1 with receipt_role_reason. Only self-contradicting
# evidence still stops the export.
fec_assert_receipt_evidence <- function(rows) {
  contradicted <- fec_receipt_countable(rows) &
    fec_receipt_role_contradicted(fec_receipt_role(rows))
  if (any(contradicted)) {
    ids <- if ("sub_id" %in% names(rows)) rows$sub_id[contradicted] else which(contradicted)
    stop(sprintf(
      "Receipt evidence contradicts itself for %d rows (examples: %s). Raw records are retained. Reconcile the conflicting receipt text before publishing; no blanket exclusion is permitted.",
      sum(contradicted), paste(head(ids, 8L), collapse = ", ")
    ), call. = FALSE)
  }
  invisible(TRUE)
}

row_scalar <- function(row, field, default = NA_character_) {
  value <- row[[field]]
  if (is.null(value) || !length(value)) {
    return(default)
  }
  if (is.list(value) && length(value) == 1L) {
    value <- value[[1L]]
  }
  if (!length(value) || is.null(value)) default else value[[1L]]
}

as_db_bool <- function(x) {
  if (is.logical(x)) {
    return(as.integer(isTRUE(x)))
  }
  value <- toupper(trim_text(x))
  as.integer(value %in% c("1", "TRUE", "T", "Y", "YES", "X"))
}

as_db_number <- function(x) {
  value <- suppressWarnings(as.numeric(x))
  if (!length(value) || is.na(value)) NA_real_ else value[[1L]]
}

as_db_integer <- function(x) {
  value <- suppressWarnings(as.integer(x))
  if (!length(value) || is.na(value)) NA_integer_ else value[[1L]]
}

MENTAT_CORPORATE_QUALIFIER_TOKENS <- c(
  "HOLDINGS", "HOLDING", "INC", "INCORPORATED", "LLC", "LLP", "LP",
  "LTD", "CORP", "CORPORATION", "CO", "COMPANY", "GROUP", "TECH",
  "TECHNOLOGIES", "TECHNOLOGY", "US", "USA", "AMERICA", "GLOBAL",
  "INTERNATIONAL", "LABS", "TRADING", "MARKETS", "EXCHANGE",
  "SECURITIES", "DIGITAL", "CAPITAL", "SERVICES", "SOLUTIONS",
  "PARTNERS", "VENTURES", "FX", "ASIA", "EUROPE", "AMERICAS"
)

MENTAT_JUNK_EMPLOYER_KEYS <- c(
  "NOT EMPLOYED", "NOTEMPLOYED", "UNEMPLOYED", "NONE", "N A", "NA",
  "SELF", "SELF EMPLOYED", "RETIRED", "HOMEMAKER", "STUDENT", "REQUESTED",
  "INFORMATION REQUESTED", "BEST EFFORTS", "REFUSED", "DECLINED"
)

MENTAT_LEGAL_SUFFIX_TOKENS <- c(
  "CO", "COMPANY", "CORP", "CORPORATION", "INC", "INCORPORATED", "LLC",
  "LLP", "LP", "LTD", "LIMITED", "PLC"
)

MENTAT_MANUAL_TRADES_DENYLIST <- c(
  "TRUCK DRIVER", "CNC PROGRAMMER", "DRIVER", "LAYOUT ARTIST",
  "GRAPHIC DESIGN", "SIGNS", "FABRICATOR", "ASSEMBLER", "LABORER",
  "PRINTER", "PRINTMAKER"
)

normalize_variant_key <- function(x) {
  value <- toupper(enc2utf8(as.character(x)))
  value[is.na(value)] <- ""
  value <- gsub("L[.]?L[.]?C[.]?", "LLC", value, perl = TRUE)
  trim_text(gsub("[^A-Z0-9]+", " ", value, perl = TRUE))
}

strip_legal_suffix <- function(x) {
  keys <- normalize_variant_key(x)
  suffix_pattern <- paste0(
    "(?:\\s+(?:",
    paste(MENTAT_LEGAL_SUFFIX_TOKENS, collapse = "|"),
    "))+$"
  )
  stripped <- sub(suffix_pattern, "", keys, perl = TRUE)
  stripped[!nzchar(stripped) & nzchar(keys)] <- keys[
    !nzchar(stripped) & nzchar(keys)
  ]
  if (length(stripped) == 1L) stripped[[1L]] else stripped
}

company_employer_keys <- function(company, values) {
  keys <- strip_legal_suffix(values)
  # N.A. is a bank legal suffix. Keep this normalization scoped to bank
  # companies so a generic employer's trailing words are not new aliases.
  if (grepl("BANK", normalize_variant_key(company), fixed = TRUE)) {
    keys <- sub(
      "(?:\\s+(?:N A|NA|NATIONAL ASSOCIATION|NATIONAL ASSOC))+$",
      "", keys, perl = TRUE
    )
    keys <- strip_legal_suffix(keys)
  }
  keys
}

employer_has_token_prefix <- function(employer_key, prefix_key) {
  employer_tokens <- strsplit(employer_key, " ", fixed = TRUE)[[1L]]
  prefix_tokens <- strsplit(prefix_key, " ", fixed = TRUE)[[1L]]
  length(prefix_tokens) <= length(employer_tokens) &&
    identical(prefix_tokens, employer_tokens[seq_along(prefix_tokens)])
}

squashed_remainder_is_corporate_qualifiers <- function(remainder) {
  remainder <- gsub("[^A-Z0-9]", "", toupper(trim_text(remainder)), perl = TRUE)
  if (!nzchar(remainder)) return(TRUE)
  qualifiers <- unique(gsub(
    "[^A-Z0-9]",
    "",
    MENTAT_CORPORATE_QUALIFIER_TOKENS,
    perl = TRUE
  ))
  qualifiers <- qualifiers[nzchar(qualifiers)]
  reachable <- rep(FALSE, nchar(remainder) + 1L)
  reachable[[1L]] <- TRUE
  for (offset in seq_len(nchar(remainder)) - 1L) {
    if (!reachable[[offset + 1L]]) next
    suffix <- substring(remainder, offset + 1L)
    matches <- qualifiers[startsWith(suffix, qualifiers)]
    for (qualifier in matches) {
      reachable[[offset + nchar(qualifier) + 1L]] <- TRUE
    }
  }
  reachable[[length(reachable)]]
}

junk_employer_reason <- function(employer) {
  key <- normalize_variant_key(employer)
  if (!nzchar(key) || !key %in% MENTAT_JUNK_EMPLOYER_KEYS) {
    return(NA_character_)
  }
  paste0("junk_employer:", key)
}

verified_employer_alias_register <- local({
  cached <- NULL
  function() {
    if (!is.null(cached)) return(cached)
    path <- file.path(mentat_input_dir(), "verified_employer_aliases.csv")
    cached <<- if (file.exists(path)) {
      register <- read.csv(path, stringsAsFactors = FALSE, check.names = FALSE,
                           fileEncoding = "UTF-8-BOM")
      required <- c("company", "alias", "evidence_url", "evidence_note")
      if (!all(required %in% names(register))) {
        stop("Verified employer alias register has invalid columns.", call. = FALSE)
      }
      register <- register[, required, drop = FALSE]
      for (field in required) register[[field]] <- trim_text(register[[field]])
      if (any(!nzchar(register$company) | !nzchar(register$alias) |
              !nzchar(register$evidence_url))) {
        stop("Verified employer aliases require company, alias and evidence URL.", call. = FALSE)
      }
      unique(register)
    } else {
      data.frame(company = character(), alias = character(),
                 evidence_url = character(), evidence_note = character(),
                 stringsAsFactors = FALSE)
    }
    cached
  }
})

verified_employer_alias_evidence <- function(company, employer) {
  register <- verified_employer_alias_register()
  if (!nrow(register)) return(NULL)
  company_key <- mentat_internal_company_name(company)
  employer_key <- company_employer_keys(company, employer)
  indexes <- which(
    mentat_internal_company_name(register$company) == company_key &
      company_employer_keys(company, register$alias) == employer_key
  )
  if (!length(indexes)) return(NULL)
  row <- register[indexes[[1L]], , drop = FALSE]
  list(alias = row$alias[[1L]], source_url = row$evidence_url[[1L]],
       evidence_note = row$evidence_note[[1L]])
}

verified_employer_aliases <- function(company) {
  aliases <- list(
    Gemini = c(
      "Gemini Trust Company",
      "Gemini Space Station",
      "Gemini Exchange",
      "Gemini Moonbase",
      "Gemini Constellation"
    ),
    Coinbase = "Coinbase Global"
  )
  if (identical(company, "Foris Dax Inc")) {
    return("CRYPTO.COM (FORIS DAX INC.)")
  }
  register <- verified_employer_alias_register()
  registered <- register$alias[
    mentat_internal_company_name(register$company) ==
      mentat_internal_company_name(company)
  ]
  unique(c(aliases[[company]] %||% character(), registered))
}

person_identity_key <- function(name) {
  parts <- person_name_parts(name)
  if (!nzchar(parts$last)) return("")
  paste(
    parts$last,
    nickname_canonical(parts$first),
    parts$suffix,
    sep = "\034"
  )
}

company_match_override <- function(
  company,
  employer,
  overrides = NULL,
  contributor_state = "",
  contributor_occupation = ""
) {
  if (is.null(overrides) || !nrow(overrides)) {
    return(NULL)
  }
  # MUST be a plain data.frame before subsetting. `overrides` arrives as a
  # data.table from read_company_match_overrides(), and DT[cond, , drop = FALSE]
  # does NOT filter like data.frame[ - it silently returns EVERY row, so one
  # company's rows get matched against another company's overrides. Verified
  # 2026-07-19: condition had 0 TRUEs yet the subset returned all 49 rows,
  # matching a Binance row against Gemini's accept/reject rules.
  overrides <- as.data.frame(overrides, stringsAsFactors = FALSE)
  employer_key <- normalize_variant_key(employer)
  keep <- which(
    normalize_variant_key(overrides$employer_key) == employer_key &
      mentat_internal_company_name(overrides$company) ==
        mentat_internal_company_name(company)
  )
  rows <- overrides[keep, , drop = FALSE]
  if (!nrow(rows)) {
    return(NULL)
  }
  # rep() not a scalar: a zero-row override table errors on scalar assignment.
  if (!"state" %in% names(rows)) rows$state <- rep("", nrow(rows))
  if (!"occupation_pattern" %in% names(rows)) {
    rows$occupation_pattern <- rep("", nrow(rows))
  }
  row_state <- normalize_register_key(contributor_state)
  row_occupation <- trim_text(contributor_occupation)
  scoped_state <- normalize_register_key(rows$state)
  scoped_occupation <- trim_text(rows$occupation_pattern)
  state_match <- !nzchar(scoped_state) | scoped_state == row_state
  occupation_match <- vapply(scoped_occupation, function(pattern) {
    !nzchar(pattern) || grepl(pattern, row_occupation, ignore.case = TRUE, perl = TRUE)
  }, logical(1L))
  rows <- rows[which(state_match & occupation_match), , drop = FALSE]
  if (!nrow(rows)) return(NULL)
  specificity <- as.integer(nzchar(normalize_register_key(rows$state))) +
    as.integer(nzchar(trim_text(rows$occupation_pattern)))
  rows <- rows[which(specificity == max(specificity)), , drop = FALSE]
  decisions <- unique(tolower(trim_text(rows$decision)))
  if (length(decisions) != 1L) {
    stop(
      "Conflicting company-match overrides for ", company,
      " / employer '", employer, "'",
      if (nzchar(contributor_state)) paste0(" [state ", contributor_state, "]") else "",
      " -- decisions: ", paste(decisions, collapse = ", "),
      call. = FALSE
    )
  }
  decision <- decisions[[1L]]
  if (!decision %in% c("accept", "reject")) {
    stop("Invalid company-match override decision for ", company, call. = FALSE)
  }
  list(
    status = if (decision == "accept") "accepted" else "rejected",
    reason = paste0("persistent_override:", decision)
  )
}

company_home_state_values <- function(company, home_states = NULL) {
  if (is.null(home_states) || !length(home_states)) return(character())
  values <- if (is.data.frame(home_states)) {
    if (!all(c("company", "state") %in% names(home_states))) {
      stop("Home-state register requires company and state columns.", call. = FALSE)
    }
    home_states$state[
      mentat_internal_company_name(home_states$company) ==
        mentat_internal_company_name(company)
    ]
  } else if (is.list(home_states) && !is.null(names(home_states))) {
    home_states[[mentat_internal_company_name(company)]] %||% character()
  } else {
    home_states
  }
  values <- unique(normalize_register_key(values))
  values[nzchar(values)]
}

occupation_is_manual_trade <- function(occupation) {
  value <- normalize_register_key(occupation)
  if (!nzchar(value)) return(FALSE)
  any(vapply(
    MENTAT_MANUAL_TRADES_DENYLIST,
    function(pattern) grepl(pattern, value, fixed = TRUE),
    logical(1L)
  ))
}

company_match_context <- function(company, variants, home_states = NULL) {
  verified_values <- verified_employer_aliases(company)
  variant_values <- unique(c(variants, verified_values))
  variant_keys <- unique(company_employer_keys(company, variant_values))
  variant_keys <- variant_keys[nzchar(variant_keys)]
  list(
    verified_keys = unique(company_employer_keys(company, verified_values)),
    variant_values = variant_values,
    variant_keys = variant_keys,
    variant_squashed_keys = gsub(" ", "", variant_keys, fixed = TRUE),
    company_home_states = company_home_state_values(company, home_states)
  )
}

company_employer_match <- function(
  company,
  employer,
  contributor_name,
  variants,
  trusted_name_keys = character(),
  overrides = NULL,
  excluded_reason = NA_character_,
  contributor_state = "",
  contributor_occupation = "",
  home_states = NULL,
  match_context = NULL
) {
  override <- company_match_override(
    company,
    employer,
    overrides,
    contributor_state,
    contributor_occupation
  )
  if (!is.null(override)) return(override)
  employer_key <- company_employer_keys(company, employer)
  result <- NULL
  if (!nzchar(employer_key)) {
    result <- list(status = "rejected", reason = "blank_employer")
  }
  if (is.null(result)) {
    junk_reason <- junk_employer_reason(employer)
    if (!is.na(junk_reason)) {
      result <- list(status = "rejected", reason = junk_reason)
    }
  }
  context <- match_context %||% company_match_context(company, variants, home_states)
  verified_keys <- context$verified_keys
  variant_values <- context$variant_values
  variant_keys <- context$variant_keys
  exact_keys <- variant_keys[
    gsub(" ", "", variant_keys, fixed = TRUE) ==
      gsub(" ", "", employer_key, fixed = TRUE)
  ]
  if (is.null(result) && nzchar(employer_key) && length(exact_keys)) {
    company_home_states <- context$company_home_states
    ambiguous_single_token <- any(!grepl(" ", exact_keys, fixed = TRUE)) &&
      length(company_home_states) > 0L
    if (ambiguous_single_token) {
      row_state <- normalize_register_key(contributor_state)
      home_state_match <- nzchar(row_state) && row_state %in% company_home_states
      manual_trade <- occupation_is_manual_trade(contributor_occupation)
      if (home_state_match && !manual_trade) {
        result <- list(
          status = "accepted",
          reason = "ambiguous_brand_home_state"
        )
      } else if (!home_state_match && manual_trade) {
        result <- list(
          status = "rejected",
          reason = "ambiguous_brand_non_home_state_manual_trade"
        )
      } else {
        result <- list(
          status = "review",
          reason = if (home_state_match) {
            "ambiguous_brand_home_state_manual_trade"
          } else {
            "ambiguous_brand_outside_home_state"
          }
        )
      }
    } else {
      reason <- if (!employer_key %in% variant_keys) {
        "squashed_form_exact"
      } else if (employer_key %in% verified_keys) {
        "verified_employer_alias"
      } else {
        "verified_roster_alias"
      }
      result <- list(status = "accepted", reason = reason)
    }
  }
  if (is.null(result)) {
    prefix_keys <- variant_keys[vapply(
      variant_keys,
      function(key) employer_has_token_prefix(employer_key, key),
      logical(1L)
    )]
    if (length(prefix_keys)) {
      prefix_key <- prefix_keys[[which.max(nchar(prefix_keys))]]
      employer_tokens <- strsplit(employer_key, " ", fixed = TRUE)[[1L]]
      prefix_tokens <- strsplit(prefix_key, " ", fixed = TRUE)[[1L]]
      trailing <- employer_tokens[-seq_along(prefix_tokens)]
      if (!length(trailing) ||
          all(trailing %in% MENTAT_CORPORATE_QUALIFIER_TOKENS)) {
        result <- list(
          status = "accepted",
          reason = "corporate_qualifier_prefix"
        )
      } else {
        result <- list(
          status = "review",
          reason = paste0(
            "prefix_with_unverified_trailing_tokens:",
            paste(trailing, collapse = " ")
          )
        )
      }
    }
  }
  if (is.null(result)) {
    employer_squashed <- gsub(
      "[^A-Z0-9]",
      "",
      toupper(enc2utf8(as.character(employer))),
      perl = TRUE
    )
    squashed_keys <- unique(gsub(
      "[^A-Z0-9]",
      "",
      toupper(enc2utf8(as.character(variant_values))),
      perl = TRUE
    ))
    squashed_keys <- squashed_keys[nzchar(squashed_keys)]
    prefix_keys <- squashed_keys[startsWith(employer_squashed, squashed_keys)]
    if (length(prefix_keys)) {
      prefix_keys <- prefix_keys[order(nchar(prefix_keys), decreasing = TRUE)]
      qualified <- vapply(prefix_keys, function(key) {
        remainder <- substring(employer_squashed, nchar(key) + 1L)
        squashed_remainder_is_corporate_qualifiers(remainder)
      }, logical(1L))
      if (any(qualified)) {
        matched_key <- prefix_keys[[which(qualified)[[1L]]]]
        remainder <- substring(employer_squashed, nchar(matched_key) + 1L)
        result <- list(
          status = "accepted",
          reason = if (nzchar(remainder)) {
            "squashed_form_corporate_qualifier"
          } else {
            "squashed_form_exact"
          }
        )
      } else {
        result <- list(
          status = "review",
          reason = paste0(
            "squashed_prefix_unverified_remainder:",
            substring(employer_squashed, nchar(prefix_keys[[1L]]) + 1L)
          )
        )
      }
    }
  }
  if (is.null(result)) {
    result <- list(status = "rejected", reason = "employer_prefix_not_verified")
  }
  if (!is.na(excluded_reason) && nzchar(trim_text(excluded_reason))) {
    result <- list(
      status = "rejected",
      reason = trim_text(excluded_reason)
    )
  }
  result
}

company_employer_match_page <- function(
  company,
  employer,
  contributor_name,
  variants,
  trusted_name_keys = character(),
  overrides = NULL,
  excluded_reason = NA_character_,
  contributor_state = "",
  contributor_occupation = "",
  home_states = NULL,
  evaluate_identity = TRUE
) {
  count <- length(employer)
  recycle <- function(value, default = "") {
    if (!length(value)) value <- default
    rep_len(value, count)
  }
  contributor_name <- recycle(contributor_name)
  excluded_reason <- recycle(excluded_reason, NA_character_)
  contributor_state <- recycle(contributor_state)
  contributor_occupation <- recycle(contributor_occupation)
  if (!count) return(list())
  if (!isTRUE(evaluate_identity)) {
    return(lapply(seq_len(count), function(index) {
      junk_reason <- junk_employer_reason(employer[[index]])
      if (is.na(junk_reason)) {
        list(status = "rejected", reason = "identity_match_not_evaluated")
      } else {
        list(status = "rejected", reason = junk_reason)
      }
    }))
  }
  context <- company_match_context(company, variants, home_states)
  status <- rep(NA_character_, count)
  reason <- rep(NA_character_, count)

  # Overrides are typically sparse, but their state/regex scopes require
  # per-row evaluation. Everything after this block is page-vectorized.
  override_results <- vector("list", count)
  if (!is.null(overrides) && nrow(overrides)) {
    override_frame <- as.data.frame(overrides, stringsAsFactors = FALSE)
    company_key <- mentat_internal_company_name(company)
    company_overrides <- override_frame[
      mentat_internal_company_name(override_frame$company) == company_key,
      ,
      drop = FALSE
    ]
    if (nrow(company_overrides)) {
      page_employer_keys <- normalize_variant_key(employer)
      override_employer_keys <- unique(
        normalize_variant_key(company_overrides$employer_key)
      )
      candidate_indexes <- which(page_employer_keys %in% override_employer_keys)
      override_results[candidate_indexes] <- lapply(
        candidate_indexes,
        function(index) {
          company_match_override(
            company,
            employer[[index]],
            company_overrides,
            contributor_state[[index]],
            contributor_occupation[[index]]
          )
        }
      )
    }
  }
  override_index <- which(!vapply(override_results, is.null, logical(1L)))
  if (length(override_index)) {
    status[override_index] <- vapply(
      override_results[override_index], `[[`, character(1L), "status"
    )
    reason[override_index] <- vapply(
      override_results[override_index], `[[`, character(1L), "reason"
    )
  }

  employer_keys <- company_employer_keys(company, employer)
  unresolved <- is.na(status)
  blank <- unresolved & !nzchar(employer_keys)
  status[blank] <- "rejected"
  reason[blank] <- "blank_employer"

  unresolved <- is.na(status)
  junk_keys <- normalize_variant_key(employer)
  junk_reasons <- rep(NA_character_, count)
  is_junk <- nzchar(junk_keys) & junk_keys %in% MENTAT_JUNK_EMPLOYER_KEYS
  junk_reasons[is_junk] <- paste0("junk_employer:", junk_keys[is_junk])
  junk <- unresolved & !is.na(junk_reasons)
  status[junk] <- "rejected"
  reason[junk] <- junk_reasons[junk]

  unresolved <- is.na(status)
  employer_squashed_keys <- gsub(" ", "", employer_keys, fixed = TRUE)
  exact <- unresolved & nzchar(employer_keys) &
    employer_squashed_keys %in% context$variant_squashed_keys
  ambiguous_alias_keys <- context$variant_squashed_keys[
    !grepl(" ", context$variant_keys, fixed = TRUE)
  ]
  ambiguous <- exact & employer_squashed_keys %in% ambiguous_alias_keys &
    length(context$company_home_states) > 0L
  if (any(ambiguous)) {
    row_states <- normalize_register_key(contributor_state)
    home_match <- nzchar(row_states) & row_states %in% context$company_home_states
    manual_trade <- vapply(
      contributor_occupation,
      occupation_is_manual_trade,
      logical(1L)
    )
    accept <- ambiguous & home_match & !manual_trade
    reject <- ambiguous & !home_match & manual_trade
    review_home <- ambiguous & home_match & manual_trade
    review_outside <- ambiguous & !home_match & !manual_trade
    status[accept] <- "accepted"
    reason[accept] <- "ambiguous_brand_home_state"
    status[reject] <- "rejected"
    reason[reject] <- "ambiguous_brand_non_home_state_manual_trade"
    status[review_home] <- "review"
    reason[review_home] <- "ambiguous_brand_home_state_manual_trade"
    status[review_outside] <- "review"
    reason[review_outside] <- "ambiguous_brand_outside_home_state"
  }
  exact_unambiguous <- exact & !ambiguous
  if (any(exact_unambiguous)) {
    verified <- employer_keys %in% context$verified_keys
    status[exact_unambiguous] <- "accepted"
    reason[exact_unambiguous & verified] <- "verified_employer_alias"
    reason[exact_unambiguous & !verified] <- "verified_roster_alias"
    squashed_only <- exact_unambiguous & !employer_keys %in% context$variant_keys
    reason[squashed_only] <- "squashed_form_exact"
  }

  # Resolve the same anchored-prefix and punctuation-squashed cases as the
  # scalar matcher, but do it page-wise. Broad FEC searches can return tens of
  # thousands of unrelated rows; calling the scalar matcher for each one made
  # those pages CPU-bound for minutes.
  unresolved_index <- which(is.na(status))
  if (length(unresolved_index)) {
    unresolved_employers <- employer_keys[unresolved_index]
    token_prefix_key <- rep("", length(unresolved_index))
    for (key in context$variant_keys) {
      matches <- unresolved_employers == key |
        startsWith(unresolved_employers, paste0(key, " "))
      replace <- matches & nchar(key) > nchar(token_prefix_key)
      token_prefix_key[replace] <- key
    }
    token_prefix <- nzchar(token_prefix_key)
    if (any(token_prefix)) {
      page_indexes <- unresolved_index[token_prefix]
      trailing <- trim_text(substring(
        unresolved_employers[token_prefix],
        nchar(token_prefix_key[token_prefix]) + 1L
      ))
      qualified <- vapply(strsplit(trailing, " ", fixed = TRUE), function(tokens) {
        !length(tokens) || all(tokens %in% MENTAT_CORPORATE_QUALIFIER_TOKENS)
      }, logical(1L))
      status[page_indexes[qualified]] <- "accepted"
      reason[page_indexes[qualified]] <- "corporate_qualifier_prefix"
      review_indexes <- page_indexes[!qualified]
      status[review_indexes] <- "review"
      reason[review_indexes] <- paste0(
        "prefix_with_unverified_trailing_tokens:",
        trailing[!qualified]
      )
    }

    unresolved_index <- which(is.na(status))
    if (length(unresolved_index)) {
      unresolved_squashed <- gsub(
        "[^A-Z0-9]",
        "",
        toupper(enc2utf8(as.character(employer[unresolved_index]))),
        perl = TRUE
      )
      squashed_variant_keys <- unique(gsub(
        "[^A-Z0-9]",
        "",
        toupper(enc2utf8(as.character(context$variant_values))),
        perl = TRUE
      ))
      squashed_variant_keys <- squashed_variant_keys[nzchar(squashed_variant_keys)]
      squashed_prefix_key <- rep("", length(unresolved_index))
      qualified_prefix_key <- rep("", length(unresolved_index))
      for (key in squashed_variant_keys) {
        matches <- startsWith(unresolved_squashed, key)
        replace <- matches & nchar(key) > nchar(squashed_prefix_key)
        squashed_prefix_key[replace] <- key
        matched_indexes <- which(matches)
        if (length(matched_indexes)) {
          remainders <- substring(
            unresolved_squashed[matched_indexes],
            nchar(key) + 1L
          )
          qualified <- vapply(
            remainders,
            squashed_remainder_is_corporate_qualifiers,
            logical(1L)
          )
          qualified_indexes <- matched_indexes[qualified]
          replace_qualified <- qualified_indexes[
            nchar(key) > nchar(qualified_prefix_key[qualified_indexes])
          ]
          qualified_prefix_key[replace_qualified] <- key
        }
      }
      squashed_prefix <- nzchar(squashed_prefix_key)
      if (any(squashed_prefix)) {
        page_indexes <- unresolved_index[squashed_prefix]
        selected_key <- ifelse(
          nzchar(qualified_prefix_key[squashed_prefix]),
          qualified_prefix_key[squashed_prefix],
          squashed_prefix_key[squashed_prefix]
        )
        remainder <- substring(
          unresolved_squashed[squashed_prefix],
          nchar(selected_key) + 1L
        )
        qualified <- nzchar(qualified_prefix_key[squashed_prefix])
        status[page_indexes[qualified]] <- "accepted"
        reason[page_indexes[qualified]] <- ifelse(
          nzchar(remainder[qualified]),
          "squashed_form_corporate_qualifier",
          "squashed_form_exact"
        )
        review_indexes <- page_indexes[!qualified]
        status[review_indexes] <- "review"
        reason[review_indexes] <- paste0(
          "squashed_prefix_unverified_remainder:",
          remainder[!qualified]
        )
      }
    }

    unresolved_index <- which(is.na(status))
    status[unresolved_index] <- "rejected"
    reason[unresolved_index] <- "employer_prefix_not_verified"
  }

  excluded <- !seq_len(count) %in% override_index &
    !is.na(excluded_reason) & nzchar(trim_text(excluded_reason))
  status[excluded] <- "rejected"
  reason[excluded] <- trim_text(excluded_reason[excluded])
  Map(function(match_status, match_reason) {
    list(status = match_status, reason = match_reason)
  }, status, reason)
}

company_cross_reference_people <- function(connection, company) {
  executive_names <- DBI::dbGetQuery(
    connection,
    paste(
      "SELECT executive_name AS contributor_name FROM executives",
      "WHERE company = ? AND executive_name IS NOT NULL AND executive_name <> ''"
    ),
    params = list(company)
  )$contributor_name
  contribution_names <- DBI::dbGetQuery(
    connection,
    paste(
      "SELECT DISTINCT contributor_name FROM contributions",
      "WHERE company = ? AND company_match_status = 'accepted'",
      "AND company_match_reason IN",
      "('verified_employer_alias','verified_roster_alias')"
    ),
    params = list(company)
  )$contributor_name
  keys <- unique(vapply(
    c(executive_names, contribution_names),
    person_identity_key,
    character(1L)
  ))
  keys[nzchar(keys)]
}

variant_subsumes <- function(kept, candidate) {
  # Attribution normalization is not evidence that two upstream searches have
  # identical results. Only case-equivalent literal queries are redundant.
  kept_key <- toupper(trim_text(kept))
  candidate_key <- toupper(trim_text(candidate))
  if (!nzchar(kept_key) || !nzchar(candidate_key)) {
    return(FALSE)
  }
  if (identical(kept_key, candidate_key)) {
    return(TRUE)
  }
  FALSE
}

partition_company_variants <- function(variants) {
  variants <- unique(trim_text(variants))
  variants <- variants[nzchar(variants)]
  if (length(variants)) {
    original_order <- seq_along(variants)
    variants <- variants[order(
      nchar(normalize_variant_key(variants)),
      original_order,
      method = "radix"
    )]
  }
  if (!length(variants)) {
    return(list(phase1 = character(), phase2 = character(), covered = character()))
  }
  phase1 <- variants[[1L]]
  phase2 <- character()
  covered <- character()
  if (length(variants) > 1L) {
    for (candidate in variants[-1L]) {
      kept <- c(phase1, phase2)
      if (any(vapply(kept, variant_subsumes, logical(1L), candidate = candidate))) {
        covered <- c(covered, candidate)
      } else {
        phase2 <- c(phase2, candidate)
      }
    }
  }
  list(phase1 = phase1, phase2 = phase2, covered = covered)
}

matched_variant_for_employer <- function(employer, variants) {
  employer_key <- normalize_variant_key(employer)
  candidates <- variants[vapply(variants, function(variant) {
    key <- normalize_variant_key(variant)
    nzchar(key) && grepl(key, employer_key, fixed = TRUE)
  }, logical(1L))]
  if (!length(candidates)) {
    return(paste(variants, collapse = " | "))
  }
  candidates[[which.max(nchar(candidates))]]
}

exclusion_reason <- function(
  employer,
  occupation,
  exclusions,
  contributor_name = ""
) {
  exclusions <- trim_text(exclusions)
  exclusions <- exclusions[nzchar(exclusions)]
  if (!length(exclusions)) {
    return(NA_character_)
  }
  haystacks <- toupper(trim_text(c(
    contributor_name,
    employer,
    occupation
  )))
  for (pattern in exclusions) {
    if (any(grepl(toupper(pattern), haystacks, fixed = TRUE))) {
      return(paste0("exclusion_pattern:", pattern))
    }
  }
  NA_character_
}

is_passthrough_memo <- function(memo_text) {
  # Analytical flag only. Paul's recipient-as-shown convention keeps these
  # rows in reports and reconciliation along with every other current FEC row.
  grepl(
    "\\b(?:ATTRIBUTION\\s+(?:FROM|TO)|JOINT\\s+FUNDRAISING|JFC)\\b",
    toupper(trim_text(memo_text)),
    perl = TRUE
  )
}

is_memo_review <- function(memo_text) {
  value <- toupper(trim_text(memo_text))
  !is_passthrough_memo(value) & grepl(
    paste0(
      "\\b(?:REATTRIBUT[A-Z]*|REDESIGNAT[A-Z]*|TRANSFER(?:RED)?|",
      "EARMARKED\\s+(?:THROUGH|FOR))\\b"
    ),
    value,
    perl = TRUE
  )
}

company_match_overrides_data <- function(connection) {
  if (!DBI::dbExistsTable(connection, "company_match_overrides")) {
    return(data.frame(
      employer_key = character(),
      company = character(),
      decision = character(),
      state = character(),
      occupation_pattern = character(),
      stringsAsFactors = FALSE
    ))
  }
  DBI::dbGetQuery(
    connection,
    paste(
      "SELECT employer_key, company, decision, state, occupation_pattern",
      "FROM company_match_overrides ORDER BY company, employer_key"
    )
  )
}

company_home_states_data <- function(connection) {
  if (!DBI::dbExistsTable(connection, "company_home_states")) {
    return(data.frame(
      company = character(), state = character(), source = character(),
      stringsAsFactors = FALSE
    ))
  }
  DBI::dbGetQuery(
    connection,
    "SELECT company, state, source FROM company_home_states ORDER BY company, state"
  )
}

company_name_entity_match <- function(contributor_name, company, variants) {
  contributor_key <- strip_legal_suffix(contributor_name)
  if (!nzchar(contributor_key)) return(FALSE)
  company_keys <- unique(strip_legal_suffix(c(company, variants)))
  company_keys <- company_keys[nzchar(company_keys)]
  if (contributor_key %in% company_keys) return(TRUE)
  contributor_squashed <- gsub("[^A-Z0-9]", "", contributor_key, perl = TRUE)
  company_squashed <- unique(gsub(
    "[^A-Z0-9]",
    "",
    company_keys,
    perl = TRUE
  ))
  nzchar(contributor_squashed) && contributor_squashed %in% company_squashed
}

is_entity_contributor_value <- function(entity_type, contributor_name, company, variants) {
  entity_is_not_individual <- toupper(trim_text(entity_type)) %in%
    c("ORG", "COM", "PAC", "CCM")
  as.integer(
    entity_is_not_individual ||
      company_name_entity_match(contributor_name, company, variants)
  )
}

flatten_fec_rows <- function(
  rows,
  industry,
  company,
  variants,
  phase,
  exclusions = character(),
  match_confidence = NA_character_,
  trusted_name_keys = character(),
  match_overrides = NULL,
  home_states = NULL,
  scraped_at_value = NULL
) {
  require_package("data.table")
  tabular_rows <- is.data.frame(rows)
  row_count <- if (tabular_rows) nrow(rows) else length(rows)
  if (!row_count) {
    empty <- data.table::as.data.table(
      setNames(replicate(length(contribution_columns()), logical(0), simplify = FALSE),
               contribution_columns())
    )
    return(empty)
  }
  scraped_at <- scraped_at_value %||% format(Sys.time(), "%Y-%m-%dT%H:%M:%OS3%z")
  page_raw <- function(field, default) {
    if (!tabular_rows) return(NULL)
    if (!field %in% names(rows)) return(rep(default, row_count))
    value <- rows[[field]]
    if (is.list(value)) {
      return(vapply(value, function(item) {
        if (is.null(item) || !length(item)) default else item[[1L]]
      }, FUN.VALUE = default, USE.NAMES = FALSE))
    }
    rep_len(value, row_count)
  }
  page_text <- function(field, default = "") {
    if (tabular_rows) {
      return(trim_text(page_raw(field, default)))
    }
    vapply(rows, function(row) {
      trim_text(row_scalar(row, field, default))
    }, character(1L))
  }
  page_number <- function(field, default = NA_real_) {
    if (tabular_rows) {
      return(suppressWarnings(as.numeric(page_raw(field, default))))
    }
    vapply(rows, function(row) {
      as_db_number(row_scalar(row, field, default))
    }, numeric(1L))
  }
  page_integer <- function(field, default = NA_integer_) {
    if (tabular_rows) {
      return(suppressWarnings(as.integer(page_raw(field, default))))
    }
    vapply(rows, function(row) {
      as_db_integer(row_scalar(row, field, default))
    }, integer(1L))
  }
  page_bool <- function(field, default = FALSE) {
    if (tabular_rows) {
      value <- page_raw(field, default)
      if (is.logical(value)) {
        return(as.integer(!is.na(value) & value))
      }
      return(as.integer(
        toupper(trim_text(value)) %in% c("1", "TRUE", "T", "Y", "YES", "X")
      ))
    }
    vapply(rows, function(row) {
      as_db_bool(row_scalar(row, field, default))
    }, integer(1L))
  }
  # A page whose source rows carry no street column at all yields a blank
  # street for every row on it. That is a shape fact about the source, not a
  # bad row, so it is reported once per page and never interrupts the walk.
  if (tabular_rows && row_count > 0L &&
      !any(c("contributor_street_1", "contributor_street_2") %in% names(rows))) {
    cat(sprintf(
      paste0(
        "flatten_fec_rows: source rows carry no contributor_street_1/",
        "contributor_street_2; street will be blank for this page ",
        "(phase=%s)\n"
      ),
      as.character(phase)
    ))
  }
  employer_values <- page_text("contributor_employer")
  occupation_values <- page_text("contributor_occupation")
  contributor_names <- page_text("contributor_name")
  contributor_states <- page_text("contributor_state")
  exclusions <- trim_text(exclusions)
  exclusions <- exclusions[nzchar(exclusions)]
  excluded_values <- rep(NA_character_, row_count)
  if (length(exclusions)) {
    upper_names <- toupper(contributor_names)
    upper_employers <- toupper(employer_values)
    upper_occupations <- toupper(occupation_values)
    for (pattern in exclusions) {
      unresolved <- is.na(excluded_values)
      if (!any(unresolved)) break
      upper_pattern <- toupper(pattern)
      matched <- unresolved & (
        grepl(upper_pattern, upper_names, fixed = TRUE) |
          grepl(upper_pattern, upper_employers, fixed = TRUE) |
          grepl(upper_pattern, upper_occupations, fixed = TRUE)
      )
      excluded_values[matched] <- paste0("exclusion_pattern:", pattern)
    }
  }
  match_values <- if (identical(phase, "P2_company_name")) {
    contributor_names
  } else {
    employer_values
  }
  company_matches <- company_employer_match_page(
    company = company,
    employer = match_values,
    contributor_name = contributor_names,
    variants = variants,
    trusted_name_keys = trusted_name_keys,
    overrides = match_overrides,
    excluded_reason = excluded_values,
    contributor_state = contributor_states,
    contributor_occupation = occupation_values,
    home_states = home_states,
    evaluate_identity = phase %in% c(
      "P1_employer", "P2_variant", "P2_company_name", "P3_bulk_rescue"
    )
  )
  committee_names <- if (tabular_rows) {
    page_text("committee_name")
  } else {
    vapply(rows, function(row) {
      committee <- row$committee
      committee_name <- if (is.list(committee)) {
        trim_text(committee$name %||% "")
      } else {
        ""
      }
      if (!nzchar(committee_name)) {
        committee_name <- trim_text(row_scalar(row, "committee_name", ""))
      }
      committee_name
    }, character(1L))
  }
  memo_codes <- page_text("memo_code")
  memoed_values <- page_bool("memoed_subtotal")
  committee_ids <- page_text("committee_id")
  memo_texts <- page_text("memo_text")
  entity_types <- page_text("entity_type")
  amounts <- page_number("contribution_receipt_amount")
  original_sub_ids <- page_text("original_sub_id")
  amendments <- page_text("amendment_indicator")
  employer_keys <- normalize_variant_key(employer_values)
  variant_keys <- normalize_variant_key(variants)
  matched_variants <- rep(paste(variants, collapse = " | "), row_count)
  matched_variant_width <- rep(-1L, row_count)
  for (variant_index in which(nzchar(variant_keys))) {
    matched <- grepl(variant_keys[[variant_index]], employer_keys, fixed = TRUE)
    variant_width <- nchar(variants[[variant_index]])
    replace <- matched & variant_width > matched_variant_width
    matched_variants[replace] <- variants[[variant_index]]
    matched_variant_width[replace] <- variant_width
  }
  file_nums <- if (tabular_rows) {
    if ("file_num" %in% names(rows)) {
      page_integer("file_num")
    } else {
      page_integer("file_number")
    }
  } else {
    vapply(rows, function(row) {
      as_db_integer(row_scalar(
        row,
        "file_num",
        row_scalar(row, "file_number", NA_integer_)
      ))
    }, integer(1L))
  }
  contributor_entity_keys <- strip_legal_suffix(contributor_names)
  company_entity_keys <- unique(strip_legal_suffix(c(company, variants)))
  company_entity_keys <- company_entity_keys[nzchar(company_entity_keys)]
  contributor_entity_squashed <- gsub(
    "[^A-Z0-9]",
    "",
    contributor_entity_keys,
    perl = TRUE
  )
  company_entity_squashed <- unique(gsub(
    "[^A-Z0-9]",
    "",
    company_entity_keys,
    perl = TRUE
  ))
  entity_contributors <- as.integer(
    toupper(entity_types) %in% c("ORG", "COM", "PAC", "CCM") |
      (
        nzchar(contributor_entity_keys) &
          (
            contributor_entity_keys %in% company_entity_keys |
              contributor_entity_squashed %in% company_entity_squashed
          )
      )
  )
  result <- data.table::data.table(
    sub_id = page_text("sub_id"),
    transaction_id = page_text("transaction_id"),
    original_sub_id = original_sub_ids,
    contributor_id = page_text("contributor_id"),
    contributor_name = contributor_names,
    contributor_first_name = page_text("contributor_first_name"),
    contributor_last_name = page_text("contributor_last_name"),
    contributor_suffix = page_text("contributor_suffix"),
    contributor_city = page_text("contributor_city"),
    contributor_state = contributor_states,
    contributor_zip = page_text("contributor_zip"),
    contributor_employer = employer_values,
    contributor_occupation = occupation_values,
    contributor_street_1 = page_text("contributor_street_1"),
    contributor_street_2 = page_text("contributor_street_2"),
    contributor_aggregate_ytd = page_number("contributor_aggregate_ytd"),
    contribution_receipt_amount = amounts,
    contribution_receipt_date = page_text("contribution_receipt_date"),
    committee_id = committee_ids,
    committee_name = committee_names,
    recipient_committee_type = page_text("recipient_committee_type"),
    recipient_committee_designation = page_text("recipient_committee_designation"),
    entity_type = entity_types,
    election_type = page_text("election_type"),
    fec_election_type_desc = page_text("fec_election_type_desc"),
    receipt_type_full = page_text("receipt_type_full"),
    filing_form = page_text("filing_form"),
    receipt_type = page_text("receipt_type"),
    line_number_label = page_text("line_number_label"),
    back_reference_transaction_id = page_text("back_reference_transaction_id"),
    back_reference_schedule_name = page_text("back_reference_schedule_name"),
    memo_code = memo_codes,
    memoed_subtotal = memoed_values,
    memo_text = memo_texts,
    conduit_committee_id = page_text("conduit_committee_id"),
    conduit_committee_name = page_text("conduit_committee_name"),
    amendment_indicator = amendments,
    report_year = page_integer("report_year"),
    report_type = page_text("report_type"),
    file_num = file_nums,
    two_year_transaction_period = page_integer("two_year_transaction_period"),
    line_number = page_text("line_number"),
    pdf_url = page_text("pdf_url"),
    load_date = page_text("load_date"),
    industry = industry,
    company = company,
    matched_variant = matched_variants,
    phase = phase,
    match_confidence = match_confidence,
    scraped_at = scraped_at,
    is_conduit_leg = as.integer(
      committee_ids %in% c("C00401224", "C00694323")
    ),
    is_memo_leg = as.integer(toupper(memo_codes) == "X" | memoed_values == 1L),
    is_passthrough_leg = as.integer(is_passthrough_memo(memo_texts)),
    memo_review = as.integer(is_memo_review(memo_texts)),
    possible_amendment = as.integer(
      nzchar(original_sub_ids) | toupper(amendments) %in% c("A", "C", "T")
    ),
    superseded_by_amendment = 0L,
    duplicate_filing_version = 0L,
    duplicate_memo = 0L,
    is_refund = as.integer(!is.na(amounts) & amounts < 0),
    is_entity_contributor = entity_contributors,
    company_match_status = vapply(
      company_matches, `[[`, character(1L), "status"
    ),
    company_match_reason = vapply(
      company_matches, `[[`, character(1L), "reason"
    ),
    excluded_reason = excluded_values
  )
  if (any(!nzchar(result$sub_id))) {
    stop("A collected row has an empty sub_id.", call. = FALSE)
  }
  result[]
}

mentat_record_row_evidence <- function(connection, table, rows, stage) {
  if (!table %in% c("contributions", "corporate_giving") || !nrow(rows)) return(invisible(NULL))
  # The indexed page-sized staging table keeps evidence checks independent of
  # the size of the historical database. No report attribution is rewritten.
  observed_at <- format(Sys.time(), "%Y-%m-%dT%H:%M:%OS3%z")
  claim_fields <- c("sub_id", "company", "industry", "phase", "matched_variant",
    "company_match_status", "company_match_reason", "contributor_id")
  claims <- data.table::copy(data.table::as.data.table(rows))
  for (field in setdiff(claim_fields, names(claims))) {
    claims[, (field) := if (field == "phase" && table == "corporate_giving") "P2_company_name" else ""]
  }
  for (field in claim_fields) claims[, (field) := trim_text(get(field))]
  evidence_fields <- intersect(c(claim_fields, "contributor_name", "contributor_employer",
    "contributor_state", "contributor_occupation", "entity_type", "committee_id",
    "contribution_receipt_amount", "contribution_receipt_date", "transaction_id", "file_num",
    "two_year_transaction_period", "cycle"), names(claims))
  claims <- unique(claims[, ..evidence_fields])
  claim_payloads <- vapply(seq_len(nrow(claims)), function(i) {
    mentat_evidence_json(as.list(claims[i]))
  }, character(1L))
  claim_rows <- claims[, ..claim_fields]
  claim_rows[, `:=`(claim_id = vapply(paste(table, claim_payloads, sep = "\n"), mentat_evidence_sha256, character(1L)),
    schema_version = 1L, source_table = table, evidence_json = claim_payloads, observed_at = observed_at)]
  insert_rows_or_ignore(connection, "attribution_claims", claim_rows, names(claim_rows))

  quoted_table <- as.character(DBI::dbQuoteIdentifier(connection, table))
  quoted_stage <- as.character(DBI::dbQuoteIdentifier(connection, stage))
  # Financial identity disagreements are evidence, not permission to silently
  # overwrite the accepted row. Missing incoming metadata is not a correction.
  financial_fields <- intersect(c("contributor_id", "contributor_name", "committee_id",
    "contribution_receipt_amount", "contribution_receipt_date"), names(rows))
  differences <- list()
  for (field in financial_fields) {
    qfield <- as.character(DBI::dbQuoteIdentifier(connection, field))
    comparison <- paste0(
      " WHERE s.", qfield, " IS NOT NULL AND t.", qfield, " IS NOT NULL",
      " AND TRIM(s.", qfield, ")<>'' AND TRIM(t.", qfield, ")<>''",
      " AND s.", qfield, "<>t.", qfield)
    values <- DBI::dbGetQuery(connection, paste0(
      "SELECT s.sub_id, s.company AS incoming_company, t.", qfield, " AS before_value, s.", qfield,
      " AS after_value FROM ", quoted_stage, " s JOIN ", quoted_table, " t ON t.sub_id=s.sub_id",
      comparison, " UNION SELECT s.sub_id, s.company, t.", qfield, ", s.", qfield,
      " FROM ", quoted_stage, " s JOIN ", quoted_stage, " t ON t.sub_id=s.sub_id AND t.rowid<s.rowid",
      comparison))
    if (!nrow(values)) next
    for (i in seq_len(nrow(values))) {
      before <- mentat_evidence_json(values$before_value[[i]])
      after <- mentat_evidence_json(values$after_value[[i]])
      evidence <- mentat_evidence_json(list(source_table = table, sub_id = values$sub_id[[i]],
        incoming_company = values$incoming_company[[i]], field_name = field, before_json = before, after_json = after))
      differences[[length(differences) + 1L]] <- data.table::data.table(
        conflict_id = mentat_evidence_sha256(evidence), schema_version = 1L, source_table = table,
        sub_id = values$sub_id[[i]], field_name = field, before_json = before, after_json = after,
        incoming_company = values$incoming_company[[i]], evidence_json = evidence,
        observed_at = observed_at, resolution_status = "unresolved")
    }
  }
  if (length(differences)) {
    conflicts <- unique(data.table::rbindlist(differences))
    insert_rows_or_ignore(connection, "source_record_conflicts", conflicts, names(conflicts))
  }
  invisible(NULL)
}

MENTAT_SQL_IN_CHUNK <- 900L

insert_rows_or_ignore <- function(connection, table, rows, columns) {
  if (!nrow(rows)) {
    return(invisible(0L))
  }
  if (table %in% c("contributions", "corporate_giving") &&
      "contributor_id" %in% columns && !"contributor_id" %in% names(rows)) {
    # Older browser/bulk row producers do not contain this optional API field.
    rows <- data.table::copy(data.table::as.data.table(rows))
    rows[, contributor_id := NA_character_]
  }
  if (table %in% c("contributions", "corporate_giving") &&
      all(c("sub_id", "company") %in% names(rows))) {
    incoming <- unique(data.table::data.table(
      sub_id = as.character(rows$sub_id),
      incoming_company = as.character(rows$company),
      incoming_phase = if ("phase" %in% names(rows)) as.character(rows$phase) else "P2_company_name"
    ))
    ids <- unique(incoming$sub_id[nzchar(incoming$sub_id)])
    if (length(ids)) {
      # SQLite caps bound variables per statement (32766 in RSQLite), so a large
      # unit's sub_ids are looked up in chunks; the union of chunk results is
      # the same row set as one IN over every id.
      stored <- data.table::rbindlist(lapply(
        split(ids, ceiling(seq_along(ids) / MENTAT_SQL_IN_CHUNK)),
        function(chunk) data.table::as.data.table(DBI::dbGetQuery(
          connection,
          paste0(
            "SELECT sub_id, company AS stored_company FROM ", table, " ",
            "WHERE sub_id IN (", paste(rep("?", length(chunk)), collapse = ", "), ")"
          ),
          params = as.list(chunk)
        ))
      ))
      if (nrow(stored)) {
        conflicts <- merge(incoming, stored, by = "sub_id", allow.cartesian = TRUE)
        conflicts <- conflicts[stored_company != incoming_company]
        if (nrow(conflicts)) {
          conflicts[, created_at := format(Sys.time(), "%Y-%m-%dT%H:%M:%OS3%z")]
          insert_rows_or_ignore(
            connection,
            "attribution_conflicts",
            conflicts,
            c(
              "sub_id", "stored_company", "incoming_company",
              "incoming_phase", "created_at"
            )
          )
        }
      }
    }
  }
  quoted_table <- as.character(DBI::dbQuoteIdentifier(connection, table))
  quoted_columns <- as.character(DBI::dbQuoteIdentifier(connection, columns))
  sql <- paste0(
    "INSERT OR IGNORE INTO ", quoted_table,
    " (", paste(quoted_columns, collapse = ", "), ") SELECT ",
    paste(quoted_columns, collapse = ", "), " FROM "
  )
  stage <- paste0(
    "_mentat_insert_",
    gsub("[^A-Za-z0-9_]", "_", table, perl = TRUE)
  )
  quoted_stage <- as.character(DBI::dbQuoteIdentifier(connection, stage))
  DBI::dbExecute(connection, paste0("DROP TABLE IF EXISTS temp.", quoted_stage))
  on.exit(try(
    DBI::dbExecute(connection, paste0("DROP TABLE IF EXISTS temp.", quoted_stage)),
    silent = TRUE
  ), add = TRUE)
  DBI::dbExecute(
    connection,
    paste0(
      "CREATE TEMP TABLE ", quoted_stage, " AS SELECT ",
      paste(quoted_columns, collapse = ", "),
      " FROM ", quoted_table, " WHERE 0"
    )
  )
  DBI::dbAppendTable(connection, stage, as.data.frame(rows[, ..columns]))
  if ("sub_id" %in% columns) {
    stage_sub_id_index <- paste0(stage, "_sub_id")
    quoted_stage_sub_id_index <- as.character(
      DBI::dbQuoteIdentifier(connection, stage_sub_id_index)
    )
    quoted_sub_id <- as.character(
      DBI::dbQuoteIdentifier(connection, "sub_id")
    )
    DBI::dbExecute(
      connection,
      paste0(
        "CREATE INDEX ", quoted_stage_sub_id_index,
        " ON ", quoted_stage, " (", quoted_sub_id, ")"
      )
    )
  }
  mentat_record_row_evidence(connection, table, rows, stage)
  DBI::dbExecute(connection, paste0(sql, quoted_stage))
  inserted <- DBI::dbGetQuery(connection, "SELECT changes() AS n")$n[[1L]]
  if (table %in% c("contributions", "corporate_giving")) {
    version_fields <- intersect(
      c(
        "transaction_id", "original_sub_id", "amendment_indicator", "contributor_id",
        "report_year", "report_type", "file_num", "pdf_url", "load_date",
        "filing_form", "receipt_type", "receipt_type_full", "line_number",
        "line_number_label", "memo_code", "memo_text", "back_reference_transaction_id",
        "back_reference_schedule_name"
      ),
      columns
    )
    for (field in version_fields) {
      quoted_field <- as.character(DBI::dbQuoteIdentifier(connection, field))
      incoming <- paste0(
        "(SELECT ", quoted_field, " FROM ", quoted_stage,
        " WHERE ", quoted_stage, ".sub_id=", quoted_table, ".sub_id)"
      )
      replacement <- if (field %in% c("report_year", "file_num")) {
        paste0("COALESCE(", quoted_field, ", ", incoming, ")")
      } else {
        paste0(
          "CASE WHEN ", quoted_field, " IS NULL OR TRIM(", quoted_field,
          ")='' THEN ", incoming, " ELSE ", quoted_field, " END"
        )
      }
      DBI::dbExecute(
        connection,
        paste0(
          "UPDATE ", quoted_table, " SET ", quoted_field, "=", replacement,
          " WHERE sub_id IN (SELECT sub_id FROM ", quoted_stage, ")",
          " AND NOT EXISTS (SELECT 1 FROM source_record_conflicts f WHERE f.source_table=",
          as.character(DBI::dbQuoteString(connection, table)), " AND f.sub_id=", quoted_table,
          ".sub_id AND f.resolution_status='unresolved')"
        )
      )
    }
    # Browser checkpoints historically lost memo status. A hydrated API row
    # must repair the stored flag too, even when its sub-ID already exists.
    if (all(c("filing_form", "is_memo_leg", "memoed_subtotal") %in% columns)) {
      DBI::dbExecute(connection, paste0(
        "UPDATE ", quoted_table, " SET is_memo_leg=(SELECT is_memo_leg FROM ", quoted_stage,
        " WHERE ", quoted_stage, ".sub_id=", quoted_table, ".sub_id), ",
        "memoed_subtotal=(SELECT memoed_subtotal FROM ", quoted_stage,
        " WHERE ", quoted_stage, ".sub_id=", quoted_table, ".sub_id) ",
        "WHERE sub_id IN (SELECT sub_id FROM ", quoted_stage,
        " WHERE COALESCE(filing_form,'')<>'')",
        " AND NOT EXISTS (SELECT 1 FROM source_record_conflicts f WHERE f.source_table=",
        as.character(DBI::dbQuoteString(connection, table)), " AND f.sub_id=", quoted_table,
        ".sub_id AND f.resolution_status='unresolved')"
      ))
    }
  }
  DBI::dbExecute(connection, paste0("DROP TABLE temp.", quoted_stage))
  invisible(inserted)
}

# zero_verified defaults to 0 (unverified) and must be passed explicitly by a
# lane that actually confirms emptiness. The browser lane requires DataTables'
# confirmed-empty row. The API lane requires a successful HTTP response plus
# count/cursor completion, and fails closed if an unservable query has no safe
# decomposition. Older collectors left the default and remain visibly
# unverified.
checkpoint_contribution_page <- function(
  connection,
  rows,
  company,
  cycle,
  variant_set,
  page,
  records_total,
  reviews = NULL,
  use_transaction = TRUE,
  zero_verified = 0L
) {
  require_package("DBI")
  require_package("data.table")
  action <- function() {
    insert_rows_or_ignore(
      connection,
      "contributions",
      rows,
      contribution_columns()
    )
    if (!is.null(reviews) && nrow(reviews)) {
      insert_rows_or_ignore(
        connection,
        "review_queue_identity",
        reviews,
        setdiff(names(reviews), "review_id")
      )
    }
    # Never downgrade an already-verified checkpoint: a later unverified write
    # for the same key keeps the 1 via MAX, so one confirmed walk is enough.
    DBI::dbExecute(
      connection,
      paste(
        "INSERT INTO scrape_progress",
        "(company, cycle, variant_set, last_page, records_total, updated_at,",
        "zero_verified)",
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        "ON CONFLICT(company, cycle, variant_set) DO UPDATE SET",
        "last_page=excluded.last_page, records_total=excluded.records_total,",
        "updated_at=excluded.updated_at,",
        "zero_verified=MAX(scrape_progress.zero_verified, excluded.zero_verified)"
      ),
      params = list(
        company,
        as.integer(cycle),
        variant_set,
        as.integer(page),
        as.integer(records_total),
        format(Sys.time(), "%Y-%m-%dT%H:%M:%OS3%z"),
        as.integer(zero_verified)
      )
    )
  }
  if (isTRUE(use_transaction)) {
    DBI::dbWithTransaction(connection, action())
  } else {
    action()
  }
  invisible(TRUE)
}

log_page_canaries <- function(
  connection,
  company,
  cycle,
  variant_set,
  page,
  checks,
  use_transaction = TRUE
) {
  require_package("data.table")
  checked_at <- format(Sys.time(), "%Y-%m-%dT%H:%M:%OS3%z")
  rows <- data.table::data.table(
    company = company,
    cycle = as.integer(cycle),
    variant_set = variant_set,
    page = as.integer(page),
    check_name = checks$check_name,
    passed = as.integer(checks$passed),
    detail = checks$detail,
    checked_at = checked_at
  )
  action <- function() {
    DBI::dbAppendTable(connection, "canary_log", as.data.frame(rows))
  }
  if (isTRUE(use_transaction)) {
    DBI::dbWithTransaction(connection, action())
  } else {
    action()
  }
  invisible(TRUE)
}

completed_scrape_checkpoint <- function(
  connection,
  company,
  cycle,
  variant_set,
  resume_after = NULL
) {
  resume_after <- trim_text(resume_after %||% "")
  if (!nzchar(resume_after)) {
    return(NULL)
  }
  # Pre-migration callers have no verification stamp; preserve positive
  # resume points while treating their zero checkpoints as unverified.
  zero_verified_column <- if ("zero_verified" %in%
      DBI::dbListFields(connection, "scrape_progress")) {
    "zero_verified"
  } else {
    "0 AS zero_verified"
  }
  progress <- data.table::as.data.table(DBI::dbGetQuery(
    connection,
    paste(
    "SELECT last_page, records_total, updated_at,",
    zero_verified_column, "FROM scrape_progress",
      "WHERE company = ? AND cycle = ? AND variant_set = ?",
      "AND updated_at >= ?"
    ),
    params = list(
      company,
      as.integer(cycle),
      variant_set,
      resume_after
    )
  ))
  if (!nrow(progress)) {
    return(NULL)
  }
  # A legacy empty checkpoint does not prove the query was empty.
  # Nonzero legacy checkpoints retain their existing resume behavior.
  if (isTRUE(as.integer(progress$records_total[[1L]]) == 0L) &&
      !isTRUE(as.integer(progress$zero_verified[[1L]]) == 1L)) {
    return(NULL)
  }
  final_page <- max(
    0L,
    as.integer(ceiling(progress$records_total[[1L]] / 100L)) - 1L
  )
  if (progress$last_page[[1L]] < final_page) {
    return(NULL)
  }
  list(
    records_total = as.integer(progress$records_total[[1L]]),
    pages = final_page + 1L,
    retries = 0L,
    skipped = TRUE,
    updated_at = progress$updated_at[[1L]]
  )
}

collect_employer_set <- function(
  connection,
  session,
  industry,
  company,
  cycle,
  variants,
  phase,
  exclusions = character(),
  resume_after = NULL,
  trusted_name_keys = NULL,
  match_overrides = NULL,
  home_states = NULL,
  match_variants = NULL
) {
  variants <- unique(trim_text(variants))
  variants <- variants[nzchar(variants)]
  if (!length(variants)) {
    return(list(records_total = 0L, pages = 0L, retries = 0L))
  }
  # The QUERY runs on this phase's variant subset, but matching must see the
  # company's FULL roster - partition_company_variants() keeps only the shortest
  # variant in phase1 and parks subsumed ones in `covered`, which is never
  # queried. Matching against the subset made an exact roster variant
  # ("VERIZON WIRELESS" against kept variant "VERIZON") register as a prefix hit
  # with unverified trailing tokens, sending real employees to `review`.
  # The API lane already does this correctly - see fec_api.R, flatten_fec_rows(
  # variants = unit$company_variants). Default preserves the old behaviour.
  match_variants <- unique(trim_text(match_variants %||% variants))
  match_variants <- match_variants[nzchar(match_variants)]
  if (!length(match_variants)) {
    match_variants <- variants
  }
  variant_set <- paste0(
    phase, ":employer:",
    paste(variants, collapse = " || ")
  )
  completed <- completed_scrape_checkpoint(
    connection,
    company,
    cycle,
    variant_set,
    resume_after
  )
  if (!is.null(completed)) {
    cat(
      "Resume: skipping completed ", company, " ", cycle, " ",
      variant_set, "\n",
      sep = ""
    )
    return(completed)
  }
  if (is.null(trusted_name_keys)) {
    trusted_name_keys <- company_cross_reference_people(connection, company)
  }
  if (is.null(match_overrides)) {
    match_overrides <- company_match_overrides_data(connection)
  }
  fec_walk_variant_set(
    evidence_connection = connection,
    session = session,
    company = company,
    cycle = cycle,
    variants = variants,
    variant_set = variant_set,
    query_field = "contributor_employer",
    canary_callback = function(company, cycle, variant_set, page, checks) {
      log_page_canaries(
        connection, company, cycle, variant_set, page, checks
      )
    },
    page_callback = function(rows, page, records_total) {
      records <- flatten_fec_rows(
        rows,
        industry = industry,
        company = company,
        variants = match_variants,
        phase = phase,
        exclusions = exclusions,
        trusted_name_keys = trusted_name_keys,
        match_overrides = match_overrides,
        home_states = home_states
      )
      checkpoint_contribution_page(
        connection,
        records,
        company,
        cycle,
        variant_set,
        page,
        records_total,
        # Browser lane: fec_results_ready() has already confirmed this page, so a
        # zero recorded here is a real zero and not a rate-limited one.
        zero_verified = 1L
      )
    }
  )
}

person_name_parts <- function(name) {
  value <- toupper(trim_text(name))
  value <- gsub("[.]", "", value, perl = TRUE)
  suffix_match <- regmatches(
    value,
    regexec("(?:,|\\s)\\s*(JR|SR|II|III|IV)\\s*$", value, perl = TRUE)
  )[[1L]]
  suffix <- if (length(suffix_match)) suffix_match[[2L]] else ""
  if (nzchar(suffix)) {
    value <- sub("(?:,|\\s)\\s*(JR|SR|II|III|IV)\\s*$", "", value, perl = TRUE)
  }
  pieces <- strsplit(value, ",", fixed = TRUE)[[1L]]
  if (grepl(",", value, fixed = TRUE)) {
    last <- trim_text(pieces[[1L]])
    first_block <- trim_text(pieces[2L] %||% "")
  } else {
    words <- strsplit(trim_text(value), " ", fixed = TRUE)[[1L]]
    last <- if (length(words)) words[[length(words)]] else ""
    first_block <- if (length(words) > 1L) paste(words[-length(words)], collapse = " ") else ""
  }
  first <- strsplit(first_block, " ", fixed = TRUE)[[1L]][1L] %||% ""
  if (!nzchar(first)) {
    first <- ""
  }
  base <- if (nzchar(first_block)) {
    trim_text(paste(last, first_block, sep = ", "))
  } else {
    last
  }
  list(base = base, first = first, last = last, suffix = suffix)
}

plausible_person_query <- function(name) {
  parts <- person_name_parts(name)
  first_letters <- gsub("[^A-Z]", "", toupper(parts$first), perl = TRUE)
  last_letters <- gsub("[^A-Z]", "", toupper(parts$last), perl = TRUE)
  nchar(first_letters) >= 2L && nchar(last_letters) >= 2L
}

mentat_name_diminutive_register <- local({
  cached_path <- ""
  cached_mtime <- as.POSIXct(NA)
  cached <- NULL
  function(path = file.path(mentat_input_dir(), "name_diminutives.csv")) {
    normalized <- normalizePath(path, winslash = "/", mustWork = TRUE)
    modified <- file.info(normalized)$mtime
    if (is.null(cached) || !identical(normalized, cached_path) ||
        is.na(cached_mtime) || modified != cached_mtime) {
      data <- utils::read.csv(
        normalized,
        stringsAsFactors = FALSE,
        check.names = FALSE,
        colClasses = "character"
      )
      required <- c("canonical", "variant")
      if (!all(required %in% names(data))) {
        stop("Name-diminutive register requires canonical,variant columns.", call. = FALSE)
      }
      data <- data[required]
      data$canonical <- normalize_register_key(data$canonical)
      data$variant <- normalize_register_key(data$variant)
      cached_path <<- normalized
      cached_mtime <<- modified
      cached <<- data
    }
    cached
  }
})

nickname_canonical <- function(first_name) {
  upper <- normalize_register_key(first_name)
  if (!nzchar(upper)) return(upper)
  register <- mentat_name_diminutive_register()
  matches <- unique(c(
    register$canonical[register$canonical == upper],
    register$canonical[register$variant == upper]
  ))
  if (length(matches) == 1L) matches[[1L]] else upper
}

normalize_zip5 <- function(x) {
  digits <- gsub("[^0-9]", "", trim_text(x), perl = TRUE)
  substr(digits, 1L, 5L)
}

identity_query_plans <- function(seed, broad_total, threshold = 100L) {
  broad_total <- as.integer(broad_total)
  threshold <- as.integer(threshold)
  if (is.na(broad_total) || broad_total <= threshold) {
    return(list(list(
      leg = "broad",
      value = "",
      query_filters = list()
    )))
  }
  locations <- list(
    zip = normalize_zip5(seed$contributor_zip),
    city = toupper(trim_text(seed$contributor_city))
  )
  plans <- list()
  for (leg in names(locations)) {
    value <- locations[[leg]]
    if (!nzchar(value)) {
      next
    }
    filter_name <- switch(
      leg,
      zip = "contributor_zip",
      city = "contributor_city"
    )
    plans[[length(plans) + 1L]] <- list(
      leg = leg,
      value = value,
      query_filters = stats::setNames(list(value), filter_name)
    )
  }
  plans
}

normalize_occupation <- function(x) {
  value <- toupper(trim_text(x))
  replacements <- c(
    "\\bSVP\\b" = "SENIOR VICE PRESIDENT",
    "\\bEVP\\b" = "EXECUTIVE VICE PRESIDENT",
    "\\bVP\\b" = "VICE PRESIDENT",
    "\\bCEO\\b" = "CHIEF EXECUTIVE OFFICER",
    "\\bCFO\\b" = "CHIEF FINANCIAL OFFICER",
    "\\bCOO\\b" = "CHIEF OPERATING OFFICER"
  )
  for (pattern in names(replacements)) {
    value <- gsub(pattern, replacements[[pattern]], value, perl = TRUE)
  }
  trim_text(gsub("[^A-Z0-9]+", " ", value, perl = TRUE))
}

occupation_similar <- function(a, b) {
  first <- normalize_occupation(a)
  second <- normalize_occupation(b)
  nzchar(first) && nzchar(second) &&
    (identical(first, second) ||
       grepl(first, second, fixed = TRUE) ||
       grepl(second, first, fixed = TRUE))
}

employer_matches_company <- function(employer, company_variants) {
  key <- normalize_variant_key(employer)
  if (!nzchar(key)) {
    return(TRUE)
  }
  any(vapply(company_variants, function(variant) {
    grepl(normalize_variant_key(variant), key, fixed = TRUE)
  }, logical(1L)))
}

identity_match_ladder <- function(seed, candidate, company_variants) {
  seed_parts <- person_name_parts(seed$contributor_name)
  candidate_parts <- person_name_parts(candidate$contributor_name)
  suffix_mismatch <- !identical(seed_parts$suffix, candidate_parts$suffix) &&
    (nzchar(seed_parts$suffix) || nzchar(candidate_parts$suffix))
  exact_name <- identical(seed_parts$base, candidate_parts$base)
  nickname_name <- identical(seed_parts$last, candidate_parts$last) &&
    identical(
      nickname_canonical(seed_parts$first),
      nickname_canonical(candidate_parts$first)
    )
  same_zip <- nzchar(normalize_zip5(seed$contributor_zip)) &&
    identical(
      normalize_zip5(seed$contributor_zip),
      normalize_zip5(candidate$contributor_zip)
    )
  same_city <- nzchar(trim_text(seed$contributor_city)) &&
    identical(
      toupper(trim_text(seed$contributor_city)),
      toupper(trim_text(candidate$contributor_city))
    )
  same_state <- nzchar(trim_text(seed$contributor_state)) &&
    identical(
      toupper(trim_text(seed$contributor_state)),
      toupper(trim_text(candidate$contributor_state))
    )
  contradiction <- !employer_matches_company(
    candidate$contributor_employer,
    company_variants
  )
  if (suffix_mismatch) {
    return(list(level = "REVIEW", reason = "suffix_mismatch"))
  }
  if (exact_name && same_zip) {
    if (contradiction) {
      return(list(level = "HIGH", reason = "exact_name_same_zip_cross_employer"))
    }
    return(list(level = "AUTO", reason = "exact_name_same_zip"))
  }
  if (exact_name && same_city) {
    return(list(
      level = "HIGH",
      reason = if (contradiction) {
        "exact_name_same_city_cross_employer"
      } else {
        "exact_name_same_city"
      }
    ))
  }
  if (nickname_name && same_zip) {
    return(list(
      level = "HIGH",
      reason = if (contradiction) {
        "nickname_same_zip_cross_employer"
      } else {
        "nickname_same_zip"
      }
    ))
  }
  if (nickname_name && same_city) {
    return(list(level = "REVIEW", reason = "nickname_same_city"))
  }
  if (exact_name && same_state &&
      occupation_similar(seed$contributor_occupation, candidate$contributor_occupation)) {
    return(list(level = "REVIEW", reason = "exact_name_state_similar_occupation"))
  }
  if (contradiction && (exact_name || nickname_name)) {
    return(list(level = "REVIEW", reason = "employer_contradiction"))
  }
  list(level = "DISCARD", reason = "below_identity_threshold")
}

identity_name_match_keys <- function(names) {
  parts <- lapply(names, person_name_parts)
  data.table::data.table(
    identity_exact_key = vapply(
      parts,
      function(part) part$base,
      character(1L)
    ),
    identity_nickname_key = vapply(
      parts,
      function(part) paste0(
        part$last,
        "\034",
        nickname_canonical(part$first)
      ),
      character(1L)
    )
  )
}

retag_employer_company_matches <- function(
  connection,
  company = NULL,
  cycle = NULL
) {
  require_package("data.table")
  where_clauses <- c("phase IN ('P1_employer','P2_variant')")
  query_params <- list()
  if (!is.null(company)) {
    if (length(company) != 1L || !nzchar(trim_text(company))) {
      stop("company must be a single non-empty value.", call. = FALSE)
    }
    where_clauses <- c(where_clauses, "company = ?")
    query_params <- c(query_params, list(trim_text(company)))
  }
  if (!is.null(cycle)) {
    if (length(cycle) != 1L || is.na(as.integer(cycle))) {
      stop("cycle must be a single integer value.", call. = FALSE)
    }
    where_clauses <- c(
      where_clauses,
      "two_year_transaction_period = ?"
    )
    query_params <- c(query_params, list(as.integer(cycle)))
  }
  rows_sql <- paste(
    "SELECT sub_id, industry, company, contributor_name, contributor_employer,",
    "contributor_state, contributor_occupation, excluded_reason FROM contributions WHERE",
    paste(where_clauses, collapse = " AND ")
  )
  rows <- data.table::as.data.table(if (length(query_params)) {
    DBI::dbGetQuery(connection, rows_sql, params = query_params)
  } else {
    DBI::dbGetQuery(connection, rows_sql)
  })
  if (!nrow(rows)) {
    return(invisible(data.table::data.table(
      company_match_status = character(),
      rows = integer()
    )))
  }
  variants <- data.table::as.data.table(DBI::dbGetQuery(
    connection,
    "SELECT industry, company, variant FROM companies"
  ))
  executives <- data.table::as.data.table(DBI::dbGetQuery(
    connection,
    paste(
      "SELECT industry, company, executive_name FROM executives",
      "WHERE executive_name IS NOT NULL AND executive_name <> ''"
    )
  ))
  match_overrides <- company_match_overrides_data(connection)
  home_states <- company_home_states_data(connection)
  rows[, `:=`(
    company_match_status_new = "rejected",
    company_match_reason_new = "employer_name_not_verified"
  )]
  groups <- unique(rows[, .(industry, company)])
  for (group_index in seq_len(nrow(groups))) {
    group <- groups[group_index]
    index <- which(
      rows$industry == group$industry &
        rows$company == group$company
    )
    company_variants <- variants[
      industry == group$industry & company == group$company,
      variant
    ]
    trusted_keys <- unique(vapply(
      executives[
        industry == group$industry & company == group$company,
        executive_name
      ],
      person_identity_key,
      character(1L)
    ))
    row_match_inputs <- rows[index, .(
      contributor_employer,
      contributor_name,
      excluded_reason,
      contributor_state,
      contributor_occupation
    )]
    match_inputs <- unique(row_match_inputs)
    match_inputs[, match_input_id := .I]
    input_ids <- match_inputs[
      row_match_inputs,
      on = .(
        contributor_employer,
        contributor_name,
        excluded_reason,
        contributor_state,
        contributor_occupation
      ),
      match_input_id
    ]
    # The scalar matcher does not currently use trusted_name_keys, so the old
    # two-pass expansion evaluated every unique row twice without changing a
    # decision. Reuse the ingestion-time vectorized matcher once here. Its
    # defensive scalar fallback retains override, prefix, squashed-form, home
    # state, occupation, and exclusion behavior for unresolved rows.
    second_pass <- company_employer_match_page(
      company = group$company,
      employer = match_inputs$contributor_employer,
      contributor_name = match_inputs$contributor_name,
      variants = company_variants,
      trusted_name_keys = trusted_keys,
      overrides = match_overrides,
      excluded_reason = match_inputs$excluded_reason,
      contributor_state = match_inputs$contributor_state,
      contributor_occupation = match_inputs$contributor_occupation,
      home_states = home_states
    )
    rows$company_match_status_new[index] <- vapply(
      second_pass,
      function(result) result$status,
      character(1L)
    )[input_ids]
    rows$company_match_reason_new[index] <- vapply(
      second_pass,
      function(result) result$reason,
      character(1L)
    )[input_ids]
  }
  tags <- rows[, .(
    sub_id,
    company_match_status = company_match_status_new,
    company_match_reason = company_match_reason_new
  )]
  DBI::dbWithTransaction(connection, {
    DBI::dbExecute(
      connection,
      "DROP TABLE IF EXISTS temp._mentat_company_match_retag"
    )
    DBI::dbExecute(
      connection,
      paste(
        "CREATE TEMP TABLE _mentat_company_match_retag (",
        "sub_id TEXT PRIMARY KEY, company_match_status TEXT NOT NULL,",
        "company_match_reason TEXT NOT NULL)"
      )
    )
    DBI::dbAppendTable(
      connection,
      "_mentat_company_match_retag",
      as.data.frame(tags)
    )
    DBI::dbExecute(
      connection,
      paste(
        "UPDATE contributions SET",
        "company_match_status = (SELECT company_match_status",
        "FROM _mentat_company_match_retag t WHERE t.sub_id = contributions.sub_id),",
        "company_match_reason = (SELECT company_match_reason",
        "FROM _mentat_company_match_retag t WHERE t.sub_id = contributions.sub_id)",
        "WHERE sub_id IN (SELECT sub_id FROM _mentat_company_match_retag)"
      )
    )
    DBI::dbExecute(
      connection,
      "DROP TABLE _mentat_company_match_retag"
    )
  })
  invisible(rows[, .(rows = .N), by = company_match_status_new])
}

retag_corporate_company_matches <- function(
  connection,
  company = NULL,
  cycle = NULL
) {
  require_package("data.table")
  where_clauses <- character()
  query_params <- list()
  if (!is.null(company)) {
    if (length(company) != 1L || !nzchar(trim_text(company))) {
      stop("company must be a single non-empty value.", call. = FALSE)
    }
    where_clauses <- c(where_clauses, "company = ?")
    query_params <- c(query_params, list(trim_text(company)))
  }
  if (!is.null(cycle)) {
    if (length(cycle) != 1L || is.na(as.integer(cycle))) {
      stop("cycle must be a single integer value.", call. = FALSE)
    }
    where_clauses <- c(where_clauses, "cycle = ?")
    query_params <- c(query_params, list(as.integer(cycle)))
  }
  rows_sql <- paste(
    "SELECT sub_id, industry, company, cycle, contributor_name, entity_type,",
    "contributor_state, contributor_occupation, excluded_reason",
    "FROM corporate_giving",
    if (length(where_clauses)) {
      paste("WHERE", paste(where_clauses, collapse = " AND "))
    } else {
      ""
    }
  )
  rows <- data.table::as.data.table(if (length(query_params)) {
    DBI::dbGetQuery(connection, rows_sql, params = query_params)
  } else {
    DBI::dbGetQuery(connection, rows_sql)
  })
  if (!nrow(rows)) {
    return(invisible(data.table::data.table(
      company_match_status = character(),
      rows = integer()
    )))
  }
  variants <- data.table::as.data.table(DBI::dbGetQuery(
    connection,
    "SELECT industry, company, variant FROM companies"
  ))
  match_overrides <- company_match_overrides_data(connection)
  rows[, `:=`(
    company_match_status_new = "review",
    company_match_reason_new = "corporate_name_not_verified",
    is_entity_contributor_new = 0L
  )]
  groups <- unique(rows[, .(industry, company)])
  for (group_index in seq_len(nrow(groups))) {
    group <- groups[group_index]
    index <- which(
      rows$industry == group$industry &
        rows$company == group$company
    )
    group_rows <- rows[index]
    company_variants <- variants[
      industry == group$industry & company == group$company,
      variant
    ]
    company_entity_keys <- unique(strip_legal_suffix(c(
      group$company,
      company_variants
    )))
    company_entity_keys <- company_entity_keys[nzchar(company_entity_keys)]
    company_entity_squashed <- unique(gsub(
      "[^A-Z0-9]",
      "",
      company_entity_keys,
      perl = TRUE
    ))
    contributor_entity_keys <- strip_legal_suffix(group_rows$contributor_name)
    contributor_entity_squashed <- gsub(
      "[^A-Z0-9]",
      "",
      contributor_entity_keys,
      perl = TRUE
    )
    exact_company_entity <- nzchar(contributor_entity_keys) & (
      contributor_entity_keys %in% company_entity_keys |
        contributor_entity_squashed %in% company_entity_squashed
    )

    match_inputs <- unique(group_rows[, .(
      contributor_name,
      contributor_state,
      contributor_occupation
    )])
    match_inputs[, match_input_id := .I]
    input_ids <- match_inputs[
      group_rows,
      on = .(
        contributor_name,
        contributor_state,
        contributor_occupation
      ),
      match_input_id
    ]
    qualified_matches <- company_employer_match_page(
      company = group$company,
      employer = match_inputs$contributor_name,
      contributor_name = match_inputs$contributor_name,
      variants = company_variants,
      overrides = match_overrides,
      contributor_state = match_inputs$contributor_state,
      contributor_occupation = match_inputs$contributor_occupation
    )
    qualified_status <- vapply(
      qualified_matches,
      `[[`,
      character(1L),
      "status"
    )[input_ids]
    qualified_reason <- vapply(
      qualified_matches,
      `[[`,
      character(1L),
      "reason"
    )[input_ids]

    entity_type <- toupper(trim_text(group_rows$entity_type))
    excluded <- nzchar(trim_text(group_rows$excluded_reason))
    is_entity <- exact_company_entity |
      entity_type %in% c("ORG", "COM", "PAC", "CCM")
    status <- rep("review", length(index))
    reason <- rep("corporate_name_not_verified", length(index))
    status[excluded] <- "rejected"
    reason[excluded] <- "corporate_excluded"
    individual <- !excluded & entity_type == "IND"
    status[individual] <- "rejected"
    reason[individual] <- "corporate_query_individual_entity"
    exact <- !excluded & !individual & exact_company_entity
    status[exact] <- "accepted"
    reason[exact] <- "verified_company_name_entity"
    accepted <- !excluded & !individual & !exact_company_entity &
      qualified_status == "accepted"
    status[accepted] <- "accepted"
    reason[accepted] <- paste0(
      "verified_company_name:",
      qualified_reason[accepted]
    )
    is_entity[accepted] <- TRUE
    override_reject <- !excluded & !individual & !exact_company_entity &
      qualified_reason == "persistent_override:reject"
    status[override_reject] <- "rejected"
    reason[override_reject] <- "corporate_name_persistent_override_reject"

    rows$company_match_status_new[index] <- status
    rows$company_match_reason_new[index] <- reason
    rows$is_entity_contributor_new[index] <- as.integer(is_entity)
  }
  tags <- rows[, .(
    sub_id,
    company_match_status = company_match_status_new,
    company_match_reason = company_match_reason_new,
    is_entity_contributor = is_entity_contributor_new
  )]
  DBI::dbWithTransaction(connection, {
    DBI::dbExecute(
      connection,
      "DROP TABLE IF EXISTS temp._mentat_corporate_match_retag"
    )
    DBI::dbExecute(
      connection,
      paste(
        "CREATE TEMP TABLE _mentat_corporate_match_retag (",
        "sub_id TEXT PRIMARY KEY, company_match_status TEXT NOT NULL,",
        "company_match_reason TEXT NOT NULL,",
        "is_entity_contributor INTEGER NOT NULL)"
      )
    )
    DBI::dbAppendTable(
      connection,
      "_mentat_corporate_match_retag",
      as.data.frame(tags)
    )
    DBI::dbExecute(
      connection,
      paste(
        "UPDATE corporate_giving SET",
        "company_match_status = (SELECT company_match_status",
        "FROM _mentat_corporate_match_retag t",
        "WHERE t.sub_id = corporate_giving.sub_id),",
        "company_match_reason = (SELECT company_match_reason",
        "FROM _mentat_corporate_match_retag t",
        "WHERE t.sub_id = corporate_giving.sub_id),",
        "is_entity_contributor = (SELECT is_entity_contributor",
        "FROM _mentat_corporate_match_retag t",
        "WHERE t.sub_id = corporate_giving.sub_id)",
        "WHERE sub_id IN (SELECT sub_id FROM _mentat_corporate_match_retag)"
      )
    )
    DBI::dbExecute(connection, "DROP TABLE _mentat_corporate_match_retag")
  })
  invisible(rows[, .(rows = .N), by = company_match_status_new])
}

identity_candidate_initial_match <- function(
  company,
  employer,
  excluded_reason,
  overrides,
  contributor_state = "",
  contributor_occupation = ""
) {
  result <- list(
    status = "rejected",
    reason = "identity_seed_no_longer_verified"
  )
  junk_reason <- junk_employer_reason(employer)
  if (!is.na(junk_reason)) {
    result <- list(status = "rejected", reason = junk_reason)
  }
  if (!is.na(excluded_reason) && nzchar(trim_text(excluded_reason))) {
    result <- list(status = "rejected", reason = trim_text(excluded_reason))
  }
  override <- company_match_override(
    company,
    employer,
    overrides,
    contributor_state,
    contributor_occupation
  )
  if (!is.null(override)) override else result
}

retag_identity_company_matches <- function(
  connection,
  company = NULL,
  cycle = NULL
) {
  require_package("data.table")
  scope_clauses <- character()
  query_params <- list()
  if (!is.null(company)) {
    if (length(company) != 1L || !nzchar(trim_text(company))) {
      stop("company must be a single non-empty value.", call. = FALSE)
    }
    scope_clauses <- c(scope_clauses, "company = ?")
    query_params <- c(query_params, list(trim_text(company)))
  }
  if (!is.null(cycle)) {
    cycle_value <- suppressWarnings(as.integer(cycle))
    if (length(cycle_value) != 1L || is.na(cycle_value)) {
      stop("cycle must be a single integer value.", call. = FALSE)
    }
    scope_clauses <- c(
      scope_clauses,
      "two_year_transaction_period = ?"
    )
    query_params <- c(query_params, list(cycle_value))
  }
  scope_sql <- if (length(scope_clauses)) {
    paste0(" AND ", paste(scope_clauses, collapse = " AND "))
  } else {
    ""
  }
  identity_columns <- paste(
    "sub_id, industry, company, two_year_transaction_period,",
    "contributor_name, contributor_city, contributor_state, contributor_zip,",
    "contributor_employer, contributor_occupation, excluded_reason"
  )
  query_rows <- function(phase_sql, extra_sql = "") {
    sql <- paste0(
      "SELECT ", identity_columns, " FROM contributions WHERE ",
      phase_sql, scope_sql, extra_sql
    )
    data.table::as.data.table(if (length(query_params)) {
      DBI::dbGetQuery(connection, sql, params = query_params)
    } else {
      DBI::dbGetQuery(connection, sql)
    })
  }
  candidates <- query_rows("phase = 'P2_name'")
  if (!nrow(candidates)) {
    return(invisible(data.table::data.table(
      company_match_status = character(),
      rows = integer()
    )))
  }
  seeds <- query_rows(
    "phase IN ('P1_employer','P2_variant')",
    paste0(
      " AND company_match_status = 'accepted'",
      " AND (excluded_reason IS NULL OR excluded_reason = '')"
    )
  )
  if (nrow(seeds)) {
    seeds <- seeds[vapply(
      contributor_name,
      plausible_person_query,
      logical(1L)
    )]
    seeds <- unique(
      seeds,
      by = c(
        "industry",
        "company",
        "two_year_transaction_period",
        "contributor_name",
        "contributor_zip",
        "contributor_city"
      )
    )
  }
  variants <- data.table::as.data.table(DBI::dbGetQuery(
    connection,
    "SELECT industry, company, variant FROM companies"
  ))
  match_overrides <- company_match_overrides_data(connection)
  candidate_name_keys <- identity_name_match_keys(
    candidates$contributor_name
  )
  candidates[, `:=`(
    identity_exact_key = candidate_name_keys$identity_exact_key,
    identity_nickname_key = candidate_name_keys$identity_nickname_key
  )]
  if (nrow(seeds)) {
    seed_name_keys <- identity_name_match_keys(seeds$contributor_name)
    seeds[, `:=`(
      identity_exact_key = seed_name_keys$identity_exact_key,
      identity_nickname_key = seed_name_keys$identity_nickname_key
    )]
  }
  initial_matches <- lapply(seq_len(nrow(candidates)), function(index) {
    identity_candidate_initial_match(
      candidates$company[[index]],
      candidates$contributor_employer[[index]],
      candidates$excluded_reason[[index]],
      match_overrides,
      candidates$contributor_state[[index]],
      candidates$contributor_occupation[[index]]
    )
  })
  candidates[, `:=`(
    company_match_status_new = vapply(
      initial_matches,
      function(result) result$status,
      character(1L)
    ),
    company_match_reason_new = vapply(
      initial_matches,
      function(result) result$reason,
      character(1L)
    )
  )]
  groups <- unique(candidates[, .(
    industry,
    company,
    two_year_transaction_period
  )])
  level_rank <- c(AUTO = 2L, HIGH = 1L)
  for (group_index in seq_len(nrow(groups))) {
    group <- groups[group_index]
    candidate_index <- which(
      candidates$industry == group$industry &
        candidates$company == group$company &
        candidates$two_year_transaction_period ==
          group$two_year_transaction_period
    )
    group_seeds <- seeds[
      industry == group$industry &
        company == group$company &
        two_year_transaction_period == group$two_year_transaction_period
    ]
    if (!nrow(group_seeds)) {
      next
    }
    company_variants <- variants[
      industry == group$industry & company == group$company,
      variant
    ]
    exact_seed_index <- split(
      seq_len(nrow(group_seeds)),
      group_seeds$identity_exact_key
    )
    nickname_seed_index <- split(
      seq_len(nrow(group_seeds)),
      group_seeds$identity_nickname_key
    )
    for (row_index in candidate_index) {
      if (!identical(
        candidates$company_match_reason_new[[row_index]],
        "identity_seed_no_longer_verified"
      )) {
        next
      }
      candidate <- as.list(candidates[row_index])
      best_match <- NULL
      best_rank <- 0L
      matching_seed_index <- sort(unique(c(
        exact_seed_index[[candidate$identity_exact_key]],
        nickname_seed_index[[candidate$identity_nickname_key]]
      )))
      if (!length(matching_seed_index)) {
        next
      }
      for (seed_index in matching_seed_index) {
        match <- identity_match_ladder(
          as.list(group_seeds[seed_index]),
          candidate,
          company_variants
        )
        rank <- if (match$level %in% names(level_rank)) {
          unname(level_rank[[match$level]])
        } else {
          0L
        }
        if (rank > best_rank) {
          best_match <- match
          best_rank <- rank
        }
        if (best_rank == level_rank[["AUTO"]]) {
          break
        }
      }
      if (best_rank > 0L) {
        candidates$company_match_status_new[[row_index]] <- "accepted"
        candidates$company_match_reason_new[[row_index]] <- paste0(
          "identity_cross_reference:",
          best_match$reason
        )
      }
    }
  }
  tags <- candidates[, .(
    sub_id,
    company_match_status = company_match_status_new,
    company_match_reason = company_match_reason_new
  )]
  DBI::dbWithTransaction(connection, {
    DBI::dbExecute(
      connection,
      "DROP TABLE IF EXISTS temp._mentat_identity_match_retag"
    )
    DBI::dbExecute(
      connection,
      paste(
        "CREATE TEMP TABLE _mentat_identity_match_retag (",
        "sub_id TEXT PRIMARY KEY, company_match_status TEXT NOT NULL,",
        "company_match_reason TEXT NOT NULL)"
      )
    )
    DBI::dbAppendTable(
      connection,
      "_mentat_identity_match_retag",
      as.data.frame(tags)
    )
    DBI::dbExecute(
      connection,
      paste(
        "UPDATE contributions SET",
        "company_match_status = (SELECT company_match_status",
        "FROM _mentat_identity_match_retag t",
        "WHERE t.sub_id = contributions.sub_id),",
        "company_match_reason = (SELECT company_match_reason",
        "FROM _mentat_identity_match_retag t",
        "WHERE t.sub_id = contributions.sub_id)",
        "WHERE sub_id IN (SELECT sub_id FROM _mentat_identity_match_retag)"
      )
    )
    DBI::dbExecute(
      connection,
      "DROP TABLE _mentat_identity_match_retag"
    )
  })
  invisible(candidates[, .(rows = .N), by = company_match_status_new])
}

checkpoint_identity_query_page <- function(
  connection,
  rows,
  page,
  records_total,
  variant_set,
  industry,
  company,
  cycle,
  seed,
  company_variants,
  exclusions = character(),
  scraped_at_value = NULL,
  match_overrides = NULL,
  use_transaction = TRUE
) {
  candidates <- flatten_fec_rows(
    rows,
    industry = industry,
    company = company,
    variants = company_variants,
    phase = "P2_name",
    exclusions = exclusions,
    match_overrides = match_overrides %||% company_match_overrides_data(connection),
    scraped_at_value = scraped_at_value
  )
  if (nrow(candidates)) {
    initial_matches <- lapply(seq_len(nrow(candidates)), function(index) {
      identity_candidate_initial_match(
        company,
        candidates$contributor_employer[[index]],
        candidates$excluded_reason[[index]],
        match_overrides %||% company_match_overrides_data(connection),
        candidates$contributor_state[[index]],
        candidates$contributor_occupation[[index]]
      )
    })
    candidates[, `:=`(
      company_match_status = vapply(
        initial_matches, `[[`, character(1L), "status"
      ),
      company_match_reason = vapply(
        initial_matches, `[[`, character(1L), "reason"
      )
    )]
  }
  keep <- logical(nrow(candidates))
  levels <- character(nrow(candidates))
  reasons <- character(nrow(candidates))
  if (nrow(candidates)) {
    for (candidate_index in seq_len(nrow(candidates))) {
      initial_status <- candidates$company_match_status[[candidate_index]]
      initial_reason <- candidates$company_match_reason[[candidate_index]]
      if (identical(initial_status, "accepted") &&
          identical(initial_reason, "persistent_override:accept")) {
        levels[[candidate_index]] <- "OVERRIDE"
        reasons[[candidate_index]] <- initial_reason
        keep[[candidate_index]] <- TRUE
        next
      }
      if (grepl(
        "^(junk_employer:|exclusion_pattern:|occupation:|persistent_override:reject)",
        initial_reason,
        perl = TRUE
      )) {
        levels[[candidate_index]] <- "REJECT"
        reasons[[candidate_index]] <- initial_reason
        next
      }
      match <- identity_match_ladder(
        seed,
        as.list(candidates[candidate_index]),
        company_variants
      )
      levels[[candidate_index]] <- match$level
      reasons[[candidate_index]] <- match$reason
      keep[[candidate_index]] <- match$level %in% c("AUTO", "HIGH")
    }
  }
  if (nrow(candidates)) {
    candidates[, match_confidence := levels]
    accepted_index <- which(keep)
    if (length(accepted_index)) {
      candidates$company_match_status[accepted_index] <- "accepted"
      candidates$company_match_reason[accepted_index] <- ifelse(
        levels[accepted_index] == "OVERRIDE",
        reasons[accepted_index],
        paste0("identity_cross_reference:", reasons[accepted_index])
      )
    }
    review_status_index <- which(levels == "REVIEW")
    if (length(review_status_index)) {
      candidates$company_match_status[review_status_index] <- "review"
      candidates$company_match_reason[review_status_index] <- paste0(
        "identity_review:",
        reasons[review_status_index]
      )
    }
    rejected_index <- which(
      !keep & levels != "REVIEW" & nzchar(reasons)
    )
    if (length(rejected_index)) {
      candidates$company_match_status[rejected_index] <- "rejected"
      candidates$company_match_reason[rejected_index] <- ifelse(
        grepl(
          "^(junk_employer:|exclusion_pattern:|occupation:|persistent_override:reject)",
          reasons[rejected_index],
          perl = TRUE
        ),
        reasons[rejected_index],
        paste0("identity_rejected:", reasons[rejected_index])
      )
    }
  }
  review_index <- which(levels == "REVIEW")
  reviews <- data.table::data.table()
  if (length(review_index)) {
    reviews <- data.table::data.table(
      seed_sub_id = seed$sub_id,
      candidate_sub_id = candidates$sub_id[review_index],
      company = company,
      cycle = as.integer(cycle),
      seed_name = seed$contributor_name,
      candidate_name = candidates$contributor_name[review_index],
      match_level = "REVIEW",
      reason = reasons[review_index],
      created_at = format(Sys.time(), "%Y-%m-%dT%H:%M:%OS3%z")
    )
  }
  checkpoint_contribution_page(
    connection,
    candidates,
    company,
    cycle,
    variant_set,
    page,
    records_total,
    reviews = reviews,
    use_transaction = use_transaction,
    # Browser lane, same reasoning as the employer pass above.
    zero_verified = 1L
  )
}

collect_contributor_name_pass <- function(
  connection,
  session,
  industry,
  company,
  cycle,
  company_variants,
  exclusions = character(),
  resume_after = NULL
) {
  seeds <- data.table::as.data.table(DBI::dbGetQuery(
    connection,
    paste(
      "SELECT sub_id, contributor_name, contributor_city, contributor_state,",
      "contributor_zip, contributor_employer, contributor_occupation",
      "FROM contributions WHERE company = ? AND two_year_transaction_period = ?",
      "AND phase IN ('P1_employer', 'P2_variant')",
      "AND company_match_status = 'accepted'",
      "AND (excluded_reason IS NULL OR excluded_reason = '')"
    ),
    params = list(company, as.integer(cycle))
  ))
  if (!nrow(seeds)) {
    return(list(retries = 0L, searches = 0L, executed = 0L))
  }
  seeds <- unique(seeds, by = c("contributor_name", "contributor_zip", "contributor_city"))
  blank_seed <- !nzchar(trim_text(seeds$contributor_name))
  if (any(blank_seed)) {
    for (sub_id in seeds$sub_id[blank_seed]) {
      cat("Skipping P2_name seed with blank contributor_name: ", sub_id, "\n", sep = "")
    }
    seeds <- seeds[!blank_seed]
  }
  if (!nrow(seeds)) {
    return(list(retries = 0L, searches = 0L, executed = 0L))
  }
  plausible_seed <- vapply(
    seeds$contributor_name,
    plausible_person_query,
    logical(1L)
  )
  if (any(!plausible_seed)) {
    for (seed_index in which(!plausible_seed)) {
      cat(
        "Skipping P2_name seed with structurally invalid person name: ",
        seeds$sub_id[[seed_index]], " [",
        seeds$contributor_name[[seed_index]], "]\n",
        sep = ""
      )
    }
    seeds <- seeds[plausible_seed]
  }
  if (!nrow(seeds)) {
    return(list(retries = 0L, searches = 0L, executed = 0L))
  }
  retry_count <- 0L
  executed <- 0L
  for (seed_index in seq_len(nrow(seeds))) {
    seed <- as.list(seeds[seed_index])
    legacy_variant_set <- paste0("P2_name:", seed$sub_id)
    completed <- completed_scrape_checkpoint(
      connection,
      company,
      cycle,
      legacy_variant_set,
      resume_after
    )
    if (!is.null(completed)) {
      next
    }

    plans <- identity_query_plans(seed, 101L)
    scoped_completed <- lapply(plans, function(plan) {
      variant_set <- paste0(
        "P2_name_", plan$leg, ":",
        seed$sub_id, ":", plan$value
      )
      completed_scrape_checkpoint(
        connection,
        company,
        cycle,
        variant_set,
        resume_after
      )
    })
    scoped_resume <- length(scoped_completed) &&
      any(!vapply(scoped_completed, is.null, logical(1L)))
    if (scoped_resume) {
      cat(
        "Resume: skipping broad P2_name probe for ", seed$sub_id, " [",
        seed$contributor_name, "]; scoped legs=",
        paste(vapply(
          plans,
          function(plan) plan$leg,
          character(1L)
        ), collapse = ","), "\n",
        sep = ""
      )
    } else {
      probe_processed <- FALSE
      executed <- executed + 1L
      probe <- fec_walk_variant_set(
        evidence_connection = connection,
        session = session,
        company = company,
        cycle = cycle,
        variants = seed$contributor_name,
        variant_set = legacy_variant_set,
        query_field = "contributor_name",
        max_pages = 1L,
        canary_callback = function(company, cycle, variant_set, page, checks) {
          log_page_canaries(connection, company, cycle, variant_set, page, checks)
        },
        page_callback = function(rows, page, records_total) {
          if (records_total <= 100L) {
            checkpoint_identity_query_page(
              connection = connection,
              rows = rows,
              page = page,
              records_total = records_total,
              variant_set = legacy_variant_set,
              industry = industry,
              company = company,
              cycle = cycle,
              seed = seed,
              company_variants = company_variants,
              exclusions = exclusions
            )
            probe_processed <<- TRUE
          }
        }
      )
      retry_count <- retry_count + probe$retries
      if (probe_processed) {
        next
      }

      plans <- identity_query_plans(seed, probe$records_total)
      scoped_completed <- vector("list", length(plans))
      if (!length(plans)) {
        cat(
          "Skipping broad P2_name seed without a usable location: ",
          seed$sub_id, " [", seed$contributor_name, "]\n",
          sep = ""
        )
        next
      }
      if (probe$records_total > 100L) {
        cat(
          "Scoping broad P2_name query: ", seed$sub_id, " [",
          seed$contributor_name, "] recordsTotal=", probe$records_total,
          " legs=", paste(vapply(
            plans,
            function(plan) plan$leg,
            character(1L)
          ), collapse = ","), "\n",
          sep = ""
        )
      }
    }

    for (plan_index in seq_along(plans)) {
      plan <- plans[[plan_index]]
      variant_set <- if (identical(plan$leg, "broad")) {
        legacy_variant_set
      } else {
        paste0(
          "P2_name_", plan$leg, ":",
          seed$sub_id, ":", plan$value
        )
      }
      completed <- scoped_completed[[plan_index]]
      if (is.null(completed) && !scoped_resume) {
        completed <- completed_scrape_checkpoint(
          connection,
          company,
          cycle,
          variant_set,
          resume_after
        )
      }
      if (!is.null(completed)) {
        next
      }
      executed <- executed + 1L
      result <- fec_walk_variant_set(
        evidence_connection = connection,
        session = session,
        company = company,
        cycle = cycle,
        variants = seed$contributor_name,
        variant_set = variant_set,
        query_field = "contributor_name",
        query_filters = plan$query_filters,
        canary_callback = function(company, cycle, variant_set, page, checks) {
          log_page_canaries(
            connection,
            company,
            cycle,
            variant_set,
            page,
            checks
          )
        },
        page_callback = function(rows, page, records_total) {
          checkpoint_identity_query_page(
            connection = connection,
            rows = rows,
            page = page,
            records_total = records_total,
            variant_set = variant_set,
            industry = industry,
            company = company,
            cycle = cycle,
            seed = seed,
            company_variants = company_variants,
            exclusions = exclusions
          )
        }
      )
      retry_count <- retry_count + result$retries
    }
  }
  list(retries = retry_count, searches = nrow(seeds), executed = executed)
}

checkpoint_corporate_page <- function(
  connection,
  rows,
  company,
  cycle,
  variant_set,
  page,
  records_total,
  use_transaction = TRUE,
  zero_verified = 0L
) {
  corporate_columns <- c(
    "sub_id", "contributor_id", "contributor_name", "contributor_employer",
    "contributor_occupation", "contributor_street_1", "contributor_street_2",
    "contributor_city", "contributor_state", "contributor_zip",
    "entity_type", "is_entity_contributor", "committee_id", "committee_name",
    "recipient_committee_type", "recipient_committee_designation",
    "contribution_receipt_date", "contribution_receipt_amount", "industry",
    "company", "matched_variant", "cycle", "scraped_at", "transaction_id",
    "original_sub_id", "amendment_indicator", "report_year", "report_type",
    "file_num", "pdf_url", "load_date", "is_conduit_leg", "is_memo_leg",
    "filing_form", "receipt_type", "receipt_type_full", "line_number", "line_number_label",
    "memo_code", "memoed_subtotal", "memo_text", "back_reference_transaction_id", "back_reference_schedule_name",
    "is_passthrough_leg", "superseded_by_amendment", "duplicate_filing_version",
    "duplicate_memo", "company_match_status",
    "company_match_reason",
    "excluded_reason"
  )
  action <- function() {
    insert_rows_or_ignore(connection, "corporate_giving", rows, corporate_columns)
    DBI::dbExecute(
      connection,
      paste(
        "INSERT INTO scrape_progress",
        "(company, cycle, variant_set, last_page, records_total, updated_at,",
        "zero_verified)",
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        "ON CONFLICT(company, cycle, variant_set) DO UPDATE SET",
        "last_page=excluded.last_page, records_total=excluded.records_total,",
        "updated_at=excluded.updated_at,",
        "zero_verified=MAX(scrape_progress.zero_verified, excluded.zero_verified)"
      ),
      params = list(
        company, as.integer(cycle), variant_set, as.integer(page),
        as.integer(records_total), format(Sys.time(), "%Y-%m-%dT%H:%M:%OS3%z"),
        as.integer(zero_verified)
      )
    )
  }
  if (isTRUE(use_transaction)) {
    DBI::dbWithTransaction(connection, action())
  } else {
    action()
  }
  invisible(TRUE)
}

company_contributor_queries <- function(company, variants) {
  # The FEC query must carry the RAW variant text: "Crypto.com" and "Crypto COM"
  # are different searches upstream. normalize_variant_key() is a comparison key
  # only. Preserve punctuation variants until source evidence proves coverage.
  raw <- trim_text(c(company, variants))
  raw <- raw[!is.na(raw) & nzchar(raw)]
  keys <- toupper(raw)
  raw[nzchar(keys) & !duplicated(keys)]
}

collect_company_contributor_pass <- function(
  connection,
  session,
  industry,
  company,
  cycle,
  variants,
  exclusions = character(),
  resume_after = NULL,
  trusted_name_keys = NULL,
  match_overrides = NULL,
  home_states = NULL
) {
  if (is.null(trusted_name_keys)) {
    trusted_name_keys <- company_cross_reference_people(connection, company)
  }
  if (is.null(match_overrides)) {
    match_overrides <- company_match_overrides_data(connection)
  }
  retry_count <- 0L
  executed <- 0L
  query_variants <- company_contributor_queries(company, variants)
  for (variant in query_variants) {
    variant_set <- paste0("P2_company_name:", variant)
    completed <- completed_scrape_checkpoint(
      connection,
      company,
      cycle,
      variant_set,
      resume_after
    )
    if (!is.null(completed)) {
      next
    }
    executed <- executed + 1L
    result <- fec_walk_variant_set(
      evidence_connection = connection,
      session = session,
      company = company,
      cycle = cycle,
      variants = variant,
      variant_set = variant_set,
      query_field = "contributor_name",
      canary_callback = function(company, cycle, variant_set, page, checks) {
        log_page_canaries(connection, company, cycle, variant_set, page, checks)
      },
      page_callback = function(rows, page, records_total) {
        records <- flatten_fec_rows(
          rows,
          industry = industry,
          company = company,
          variants = variants,
          phase = "P2_company_name",
          exclusions = exclusions,
          trusted_name_keys = trusted_name_keys,
          match_overrides = match_overrides,
          home_states = home_states
        )
        if (nrow(records)) {
          records[toupper(trim_text(entity_type)) == "IND", `:=`(
            company_match_status = "rejected",
            company_match_reason = "corporate_query_individual_entity"
          )]
          records[!nzchar(trim_text(entity_type)), `:=`(
            company_match_status = "review",
            company_match_reason = "corporate_query_missing_entity_type"
          )]
          junk <- vapply(
            records$contributor_employer,
            junk_employer_reason,
            character(1L)
          )
          records[!is.na(junk), `:=`(
            company_match_status = "rejected",
            company_match_reason = junk[!is.na(junk)]
          )]
        }
        corporate <- records[, .(
          sub_id, contributor_id, contributor_name, contributor_employer,
          contributor_occupation, contributor_street_1, contributor_street_2,
          contributor_city, contributor_state, contributor_zip,
          entity_type, is_entity_contributor, committee_id, committee_name,
          recipient_committee_type,
          recipient_committee_designation, contribution_receipt_date,
          contribution_receipt_amount, industry, company, matched_variant,
          cycle = as.integer(cycle), scraped_at, transaction_id, original_sub_id,
          amendment_indicator, report_year, report_type, file_num, pdf_url, load_date,
          filing_form, receipt_type, receipt_type_full, line_number, line_number_label,
          memo_code, memoed_subtotal, memo_text, back_reference_transaction_id, back_reference_schedule_name,
          is_conduit_leg, is_memo_leg, is_passthrough_leg,
          superseded_by_amendment, duplicate_filing_version, duplicate_memo,
          company_match_status, company_match_reason,
          excluded_reason
        )]
        checkpoint_corporate_page(
          connection, corporate, company, cycle, variant_set, page, records_total,
          # Browser lane, same reasoning as the employer pass.
          zero_verified = 1L
        )
      }
    )
    retry_count <- retry_count + result$retries
  }
  list(
    retries = retry_count,
    searches = length(query_variants),
    executed = executed
  )
}

collection_company_store_counts <- function(connection, company) {
  require_package("DBI")
  counts <- DBI::dbGetQuery(
    connection,
    paste(
      "SELECT",
      "(SELECT COUNT(*) FROM contributions WHERE company = ?) AS contributions,",
      "(SELECT COUNT(*) FROM corporate_giving WHERE company = ?) AS corporate_giving"
    ),
    params = list(company, company)
  )
  contributions <- as.integer(counts$contributions[[1L]] %||% 0L)
  corporate_giving <- as.integer(counts$corporate_giving[[1L]] %||% 0L)
  c(
    contributions = contributions,
    corporate_giving = corporate_giving,
    total = contributions + corporate_giving
  )
}

collection_receipt_status <- function(outcome, gained) {
  if (identical(outcome, "error")) {
    return("ERROR")
  }
  if (as.integer(gained[["total"]] %||% 0L) > 0L) {
    return("GAINED")
  }
  "unchanged"
}

collection_completion_detail <- function(engine, collection, skipped = FALSE) {
  engine <- tolower(trim_text(engine))
  collection <- collection %||% list()
  if (isTRUE(skipped)) {
    return("engine=offline; network_requests=0")
  }
  if (identical(engine, "api")) {
    request_count <- collection$request_count %||% 0L
    return(paste0(
      "engine=api; network_requests=", as.integer(request_count)
    ))
  }
  if (!identical(engine, "chrome")) {
    stop("Collection completion engine must be api or chrome.", call. = FALSE)
  }
  rows <- as.integer(collection$rows %||% 0L)
  wall <- as.numeric(collection$wall_clock_seconds %||% NA_real_)
  wall_text <- if (length(wall) == 1L && is.finite(wall)) {
    sprintf("%.3f", wall)
  } else {
    "n/a"
  }
  paste0(
    "engine=chrome; network_requests=n/a; rows=", rows,
    "; collection_wall_seconds=", wall_text
  )
}

collect_crypto <- function(
  connection,
  cycles = c(2022L, 2024L, 2026L),
  phase2_names = FALSE,
  session = NULL,
  resume_after = NULL,
  company_filter = NULL
) {
  require_package("data.table")
  collection_started <- proc.time()[["elapsed"]]
  cycles <- as.integer(cycles)
  if (any(!cycles %in% c(2022L, 2024L, 2026L))) {
    stop("Cycles must be a subset of 2022, 2024, and 2026.", call. = FALSE)
  }
  companies <- data.table::as.data.table(DBI::dbGetQuery(
    connection,
    paste(
      "SELECT company, variant, variant_order, exclusions",
      paste0("FROM companies WHERE industry = '", MENTAT_INDUSTRY, "'"),
      "ORDER BY company, variant_order"
    )
  ))
  if (!nrow(companies)) {
    stop("Crypto company register is empty; run Phase 0 ingest first.", call. = FALSE)
  }
  if (!is.null(company_filter)) {
    company_filter <- unique(trim_text(company_filter))
    company_filter <- company_filter[nzchar(company_filter)]
    missing_companies <- setdiff(
      company_filter,
      unique(companies$company)
    )
    if (length(missing_companies)) {
      stop(
        "Unknown company filter: ",
        paste(missing_companies, collapse = ", "),
        call. = FALSE
      )
    }
    companies <- companies[company %in% company_filter]
  }
  own_session <- is.null(session)
  if (own_session) {
    session <- new_fec_session()
  }
  session <- new_fec_session_state(session)
  if (own_session) {
    on.exit(try(fec_active_session(session)$close(), silent = TRUE), add = TRUE)
  }
  retries <- 0L
  determinism <- NA
  walked_sets <- 0L
  match_overrides <- company_match_overrides_data(connection)
  home_states <- company_home_states_data(connection)
  for (company_name in unique(companies$company)) {
    roster <- companies[company == company_name]
    variants <- roster$variant
    exclusions <- unique(unlist(strsplit(
      paste(na.omit(roster$exclusions), collapse = "|"),
      "|",
      fixed = TRUE
    )))
    exclusions <- exclusions[nzchar(exclusions)]
    trusted_name_keys <- company_cross_reference_people(connection, company_name)
    partition <- partition_company_variants(variants)
    for (cycle in cycles) {
      phase1 <- collect_employer_set(
        connection,
        session,
        MENTAT_INDUSTRY,
        company_name,
        cycle,
        partition$phase1,
        "P1_employer",
        exclusions,
        resume_after,
        trusted_name_keys = trusted_name_keys,
        match_overrides = match_overrides,
        home_states = home_states,
        match_variants = variants
      )
      retries <- retries + phase1$retries
      walked_sets <- walked_sets + 1L
      if (is.na(determinism) && !isTRUE(phase1$skipped)) {
        determinism <- fec_determinism_spot_check(session)
      }
      if (length(partition$phase2)) {
        phase2 <- collect_employer_set(
          connection,
          session,
          MENTAT_INDUSTRY,
          company_name,
          cycle,
          partition$phase2,
          "P2_variant",
          exclusions,
          resume_after,
          trusted_name_keys = trusted_name_keys,
          match_overrides = match_overrides,
          home_states = home_states,
          match_variants = variants
        )
        retries <- retries + phase2$retries
        walked_sets <- walked_sets + 1L
        if (is.na(determinism) && !isTRUE(phase2$skipped)) {
          determinism <- fec_determinism_spot_check(session)
        }
      }
      if (isTRUE(phase2_names)) {
        # The contributor-name phase selects identity seeds from the accepted
        # employer rows, so it needs current tags before it starts. When that
        # optional phase is disabled, these per-cycle retags are redundant:
        # mentat_main performs one authoritative full retag after collection.
        retag_employer_company_matches(
          connection,
          company = company_name,
          cycle = cycle
        )
        retag_identity_company_matches(
          connection,
          company = company_name,
          cycle = cycle
        )
        names_result <- collect_contributor_name_pass(
          connection,
          session,
          MENTAT_INDUSTRY,
          company_name,
          cycle,
          variants,
          exclusions,
          resume_after
        )
        if (is.na(determinism) && names_result$executed > 0L) {
          determinism <- fec_determinism_spot_check(session)
        }
        retries <- retries + names_result$retries
      }
      company_result <- collect_company_contributor_pass(
        connection,
        session,
        MENTAT_INDUSTRY,
        company_name,
        cycle,
        variants,
        exclusions,
        resume_after,
        trusted_name_keys = trusted_name_keys,
        match_overrides = match_overrides,
        home_states = home_states
      )
      if (is.na(determinism) && company_result$executed > 0L) {
        determinism <- fec_determinism_spot_check(session)
      }
      retries <- retries + company_result$retries
      # Completed company-cycle checkpoints are durable at this boundary.
      # Reclaim transient export/data.table allocations before the next unit
      # so a whole-industry Chrome session does not grow without a bound.
      gc(verbose = FALSE)
    }
  }
  wall_clock_seconds <- unname(proc.time()[["elapsed"]] - collection_started)
  rows_processed <- DBI::dbGetQuery(
    connection,
    paste0("SELECT COUNT(*) AS rows FROM contributions WHERE industry='", MENTAT_INDUSTRY, "'")
  )$rows[[1L]]
  rows_per_second <- if (wall_clock_seconds > 0) {
    rows_processed / wall_clock_seconds
  } else {
    0
  }
  cat(sprintf(
    "Collection benchmark (chrome): rows=%d wall=%.3fs rows/sec=%.2f\n",
    as.integer(rows_processed),
    wall_clock_seconds,
    rows_per_second
  ))
  list(
    retries = retries,
    determinism = determinism,
    variant_sets_walked = walked_sets,
    rows = as.integer(rows_processed),
    wall_clock_seconds = wall_clock_seconds,
    rows_per_second = rows_per_second
  )
}

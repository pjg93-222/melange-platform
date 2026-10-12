mentat_evidence_json <- function(value) {
  as.character(jsonlite::toJSON(value, auto_unbox = TRUE, null = "null", na = "null", digits = NA))
}

mentat_evidence_sha256 <- function(value) {
  require_package("digest")
  digest::digest(enc2utf8(as.character(value)), algo = "sha256", serialize = FALSE)
}

mentat_schema_sql <- function() {
  c(
    paste(
      "CREATE TABLE IF NOT EXISTS contributions (",
      "sub_id TEXT PRIMARY KEY,",
      "transaction_id TEXT, original_sub_id TEXT, contributor_id TEXT,",
      "contributor_name TEXT, contributor_first_name TEXT,",
      "contributor_last_name TEXT, contributor_suffix TEXT,",
      "contributor_city TEXT, contributor_state TEXT, contributor_zip TEXT,",
      "contributor_employer TEXT, contributor_occupation TEXT,",
      "contributor_aggregate_ytd REAL, contribution_receipt_amount REAL,",
      "contribution_receipt_date TEXT, committee_id TEXT, committee_name TEXT,",
      "recipient_committee_type TEXT, recipient_committee_designation TEXT,",
      "entity_type TEXT, election_type TEXT, fec_election_type_desc TEXT,",
      "receipt_type_full TEXT, memo_code TEXT, memoed_subtotal INTEGER,",
      "filing_form TEXT, receipt_type TEXT, line_number_label TEXT,",
      "back_reference_transaction_id TEXT, back_reference_schedule_name TEXT,",
      "memo_text TEXT, conduit_committee_id TEXT, conduit_committee_name TEXT,",
      "amendment_indicator TEXT, report_year INTEGER, report_type TEXT,",
      "file_num INTEGER,",
      "two_year_transaction_period INTEGER, line_number TEXT, pdf_url TEXT,",
      "load_date TEXT, industry TEXT NOT NULL, company TEXT NOT NULL,",
      "matched_variant TEXT NOT NULL, phase TEXT NOT NULL,",
      "match_confidence TEXT, scraped_at TEXT NOT NULL,",
      "is_conduit_leg INTEGER NOT NULL DEFAULT 0 CHECK (is_conduit_leg IN (0,1)),",
      "is_memo_leg INTEGER NOT NULL DEFAULT 0 CHECK (is_memo_leg IN (0,1)),",
      "is_passthrough_leg INTEGER NOT NULL DEFAULT 0 CHECK (is_passthrough_leg IN (0,1)),",
      "memo_review INTEGER NOT NULL DEFAULT 0 CHECK (memo_review IN (0,1)),",
      "possible_amendment INTEGER NOT NULL DEFAULT 0 CHECK (possible_amendment IN (0,1)),",
      "superseded_by_amendment INTEGER NOT NULL DEFAULT 0",
      "CHECK (superseded_by_amendment IN (0,1)),",
      "duplicate_filing_version INTEGER NOT NULL DEFAULT 0",
      "CHECK (duplicate_filing_version IN (0,1)),",
      "duplicate_memo INTEGER NOT NULL DEFAULT 0 CHECK (duplicate_memo IN (0,1)),",
      "is_refund INTEGER NOT NULL DEFAULT 0 CHECK (is_refund IN (0,1)),",
      "is_entity_contributor INTEGER NOT NULL DEFAULT 0",
      "CHECK (is_entity_contributor IN (0,1)),",
      "company_match_status TEXT NOT NULL DEFAULT 'accepted'",
      "CHECK (company_match_status IN ('accepted','review','rejected')),",
      "company_match_reason TEXT,",
      "excluded_reason TEXT,",
      "contributor_street_1 TEXT, contributor_street_2 TEXT",
      ")"
    ),
    paste(
      # zero_verified records HOW a zero was established, because nothing else
      # can. A rate-limited walk and a genuinely empty one are byte-identical
      # afterwards: Farmers Insurance recorded 24 variant sets / 0 rows under an
      # FEC 429, and Bitnomial recorded a real 0 - both at ~0.7s per set, so even
      # timing cannot separate them. 1 means the walk ran under a hardened,
      # source-specific readiness rule: DataTables rendered a confirmed-empty
      # row, or the API returned successfully and count/cursor completion was
      # proven. Rows collected before those rules keep 0 and are reported as
      # unverified rather than trusted as real zeros.
      "CREATE TABLE IF NOT EXISTS scrape_progress (",
      "company TEXT NOT NULL, cycle INTEGER NOT NULL, variant_set TEXT NOT NULL,",
      "last_page INTEGER NOT NULL, records_total INTEGER NOT NULL,",
      "updated_at TEXT NOT NULL,",
      "zero_verified INTEGER NOT NULL DEFAULT 0,",
      "PRIMARY KEY (company, cycle, variant_set)",
      ")"
    ),
    paste(
      "CREATE TABLE IF NOT EXISTS api_work_progress (",
      "unit_id TEXT PRIMARY KEY, company TEXT NOT NULL, cycle INTEGER NOT NULL,",
      "kind TEXT NOT NULL, last_page INTEGER NOT NULL DEFAULT -1,",
      "rows_seen INTEGER NOT NULL DEFAULT 0, records_total INTEGER,",
      "last_index TEXT, last_contribution_receipt_date TEXT,",
      "seen_keysets TEXT, fetched_complete INTEGER NOT NULL DEFAULT 0",
      "CHECK (fetched_complete IN (0,1)),",
      "stored_complete INTEGER NOT NULL DEFAULT 0",
      "CHECK (stored_complete IN (0,1)), updated_at TEXT NOT NULL, query_contract TEXT",
      ")"
    ),
    paste(
      "CREATE TABLE IF NOT EXISTS api_page_checkpoints (",
      "unit_id TEXT NOT NULL, page INTEGER NOT NULL, payload_json TEXT NOT NULL,",
      "PRIMARY KEY (unit_id, page)",
      ")"
    ),
    paste(
      "CREATE TABLE IF NOT EXISTS api_request_budget (",
      "budget_id TEXT PRIMARY KEY, window_started_epoch REAL NOT NULL,",
      "requests_used INTEGER NOT NULL DEFAULT 0",
      ")"
    ),
    paste(
      "CREATE TABLE IF NOT EXISTS fec_response_archive (",
      "archive_sha256 TEXT PRIMARY KEY, schema_version INTEGER NOT NULL,",
      "payload_kind TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL)"
    ),
    paste(
      "CREATE TABLE IF NOT EXISTS fec_response_observations (",
      "observation_id TEXT PRIMARY KEY, archive_sha256 TEXT NOT NULL,",
      "unit_id TEXT NOT NULL, query_sha256 TEXT NOT NULL, query_json TEXT NOT NULL,",
      "page INTEGER NOT NULL, http_status INTEGER, observed_at TEXT NOT NULL, last_observed_at TEXT NOT NULL,",
      "FOREIGN KEY (archive_sha256) REFERENCES fec_response_archive(archive_sha256))"
    ),
    "CREATE INDEX IF NOT EXISTS idx_fec_response_unit ON fec_response_observations(unit_id, page)",
    paste(
      "CREATE TABLE IF NOT EXISTS attribution_claims (",
      "claim_id TEXT PRIMARY KEY, schema_version INTEGER NOT NULL, source_table TEXT NOT NULL,",
      "sub_id TEXT NOT NULL, company TEXT NOT NULL, industry TEXT NOT NULL, phase TEXT NOT NULL,",
      "matched_variant TEXT NOT NULL, company_match_status TEXT NOT NULL, company_match_reason TEXT NOT NULL,",
      "contributor_id TEXT, evidence_json TEXT NOT NULL, observed_at TEXT NOT NULL)"
    ),
    "CREATE INDEX IF NOT EXISTS idx_attribution_claim_source ON attribution_claims(source_table, sub_id)",
    paste(
      "CREATE TABLE IF NOT EXISTS source_record_conflicts (",
      "conflict_id TEXT PRIMARY KEY, schema_version INTEGER NOT NULL, source_table TEXT NOT NULL,",
      "sub_id TEXT NOT NULL, field_name TEXT NOT NULL, before_json TEXT NOT NULL, after_json TEXT NOT NULL,",
      "incoming_company TEXT NOT NULL, evidence_json TEXT NOT NULL, observed_at TEXT NOT NULL,",
      "resolution_status TEXT NOT NULL DEFAULT 'unresolved')"
    ),
    "CREATE INDEX IF NOT EXISTS idx_source_conflict_record ON source_record_conflicts(source_table, sub_id)",
    paste(
      "CREATE TABLE IF NOT EXISTS observed_employers (",
      "cycle INTEGER NOT NULL, employer_string TEXT NOT NULL,",
      "n_rows INTEGER NOT NULL CHECK (n_rows >= 0),",
      "PRIMARY KEY (cycle, employer_string)",
      ")"
    ),
    paste(
      "CREATE TABLE IF NOT EXISTS bulk_candidates (",
      "cycle INTEGER NOT NULL, company TEXT NOT NULL, sub_id TEXT NOT NULL,",
      "contributor_name TEXT, contributor_city TEXT, contributor_state TEXT,",
      "contributor_zip TEXT, employer_string TEXT NOT NULL,",
      "contributor_occupation TEXT, committee_id TEXT, amount REAL,",
      "contributor_street_1 TEXT, contributor_street_2 TEXT,",
      "PRIMARY KEY (company, cycle, sub_id)",
      ")"
    ),
    paste(
      "CREATE TABLE IF NOT EXISTS companies (",
      "industry TEXT NOT NULL, company TEXT NOT NULL, parent TEXT NOT NULL,",
      "variant TEXT NOT NULL, variant_order INTEGER NOT NULL,",
      "confidence TEXT NOT NULL, exclusions TEXT, metadata_json TEXT,",
      "source_sheet TEXT NOT NULL, source_row INTEGER NOT NULL,",
      "PRIMARY KEY (industry, company, variant)",
      ")"
    ),
    paste(
      "CREATE TABLE IF NOT EXISTS company_display_names (",
      "canonical_name TEXT PRIMARY KEY, display_name TEXT NOT NULL,",
      "source TEXT NOT NULL",
      ")"
    ),
    paste(
      "CREATE TABLE IF NOT EXISTS company_match_overrides (",
      "employer_key TEXT NOT NULL, company TEXT NOT NULL,",
      "state TEXT NOT NULL DEFAULT '', occupation_pattern TEXT NOT NULL DEFAULT '',",
      "decision TEXT NOT NULL CHECK (decision IN ('accept','reject')),",
      "PRIMARY KEY (employer_key, company, state, occupation_pattern)",
      ")"
    ),
    paste(
      "CREATE TABLE IF NOT EXISTS company_home_states (",
      "company TEXT NOT NULL, state TEXT NOT NULL, source TEXT NOT NULL,",
      "PRIMARY KEY (company, state)",
      ")"
    ),
    paste(
      "CREATE TABLE IF NOT EXISTS name_diminutives (",
      "canonical TEXT NOT NULL, variant TEXT NOT NULL,",
      "PRIMARY KEY (canonical, variant)",
      ")"
    ),
    paste(
      "CREATE TABLE IF NOT EXISTS categories (",
      "cycle INTEGER NOT NULL, group_name TEXT NOT NULL, category TEXT NOT NULL,",
      "lean TEXT, committee_id TEXT, source_sheet TEXT NOT NULL,",
      "source_marks TEXT, provisional INTEGER NOT NULL DEFAULT 0",
      "CHECK (provisional IN (0,1)),",
      "resolution_status TEXT NOT NULL DEFAULT 'unresolved',",
      "PRIMARY KEY (cycle, group_name, category)",
      ")"
    ),
    paste(
      "CREATE TABLE IF NOT EXISTS leadership (",
      "cycle INTEGER NOT NULL, pac_name TEXT NOT NULL, affiliate TEXT, lean TEXT NOT NULL,",
      "provisional INTEGER NOT NULL CHECK (provisional IN (0,1)),",
      "committee_id TEXT, source TEXT NOT NULL,",
      "resolution_status TEXT NOT NULL DEFAULT 'unresolved',",
      "PRIMARY KEY (cycle, pac_name)",
      ")"
    ),
    paste(
      "CREATE TABLE IF NOT EXISTS trade (",
      "association_name TEXT PRIMARY KEY, section TEXT NOT NULL,",
      "committee_id TEXT, resolution_status TEXT NOT NULL DEFAULT 'unresolved',",
      "source TEXT NOT NULL",
      ")"
    ),
    paste(
      "CREATE TABLE IF NOT EXISTS executives (",
      "executive_id INTEGER PRIMARY KEY, industry TEXT NOT NULL, company TEXT NOT NULL,",
      "executive_name TEXT, role TEXT NOT NULL, source_industry TEXT NOT NULL,",
      "source_company TEXT NOT NULL, source_executive TEXT, source_row INTEGER NOT NULL,",
      "UNIQUE (source_row)",
      ")"
    ),
    paste(
      "CREATE TABLE IF NOT EXISTS industry_assets (",
      "industry TEXT PRIMARY KEY, total_assets_trillion REAL NOT NULL,",
      "source_link TEXT, source_row INTEGER NOT NULL",
      ")"
    ),
    paste(
      "CREATE TABLE IF NOT EXISTS fixture_crypto (",
      "fixture_id INTEGER PRIMARY KEY, contributor_name TEXT NOT NULL,",
      "recipient_name TEXT NOT NULL, receipt_date TEXT NOT NULL, amount REAL NOT NULL,",
      "contributor_state TEXT, city_state_zip TEXT, employer TEXT, occupation TEXT",
      ")"
    ),
    paste(
      "CREATE TABLE IF NOT EXISTS review_queue_identity (",
      "review_id INTEGER PRIMARY KEY, seed_sub_id TEXT, candidate_sub_id TEXT,",
      "company TEXT NOT NULL, cycle INTEGER NOT NULL, seed_name TEXT, candidate_name TEXT,",
      "match_level TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL,",
      "UNIQUE (seed_sub_id, candidate_sub_id, company, cycle)",
      ")"
    ),
    paste(
      "CREATE TABLE IF NOT EXISTS attribution_conflicts (",
      "sub_id TEXT NOT NULL, stored_company TEXT NOT NULL,",
      "incoming_company TEXT NOT NULL, incoming_phase TEXT NOT NULL,",
      "created_at TEXT NOT NULL,",
      "PRIMARY KEY (sub_id, stored_company, incoming_company, incoming_phase)",
      ")"
    ),
    paste(
      "CREATE TABLE IF NOT EXISTS canary_log (",
      "canary_id INTEGER PRIMARY KEY, company TEXT NOT NULL, cycle INTEGER NOT NULL,",
      "variant_set TEXT NOT NULL, page INTEGER NOT NULL, check_name TEXT NOT NULL,",
      "passed INTEGER NOT NULL CHECK (passed IN (0,1)), detail TEXT,",
      "checked_at TEXT NOT NULL",
      ")"
    ),
    paste(
      "CREATE TABLE IF NOT EXISTS run_log (",
      "run_id TEXT NOT NULL, cycle INTEGER NOT NULL, status TEXT NOT NULL,",
      "started_at TEXT NOT NULL, ended_at TEXT, retry_count INTEGER NOT NULL DEFAULT 0,",
      "determinism_result TEXT, decomposition_log TEXT, message TEXT,",
      "PRIMARY KEY (run_id, cycle)",
      ")"
    ),
    paste(
      "CREATE TABLE IF NOT EXISTS corporate_giving (",
      "sub_id TEXT PRIMARY KEY, contributor_id TEXT, contributor_name TEXT, contributor_city TEXT,",
      "contributor_state TEXT, contributor_zip TEXT, contributor_employer TEXT,",
      "contributor_occupation TEXT, entity_type TEXT, committee_id TEXT,",
      "is_entity_contributor INTEGER NOT NULL DEFAULT 0",
      "CHECK (is_entity_contributor IN (0,1)),",
      "committee_name TEXT, recipient_committee_type TEXT,",
      "recipient_committee_designation TEXT, contribution_receipt_date TEXT,",
      "contribution_receipt_amount REAL, industry TEXT NOT NULL, company TEXT NOT NULL,",
      "matched_variant TEXT NOT NULL, cycle INTEGER NOT NULL, scraped_at TEXT NOT NULL,",
      "transaction_id TEXT, original_sub_id TEXT, amendment_indicator TEXT,",
      "report_year INTEGER, report_type TEXT, file_num INTEGER, pdf_url TEXT, load_date TEXT,",
      "filing_form TEXT, receipt_type TEXT, receipt_type_full TEXT, line_number TEXT, line_number_label TEXT,",
      "memo_code TEXT, memoed_subtotal INTEGER, memo_text TEXT,",
      "back_reference_transaction_id TEXT, back_reference_schedule_name TEXT,",
      "is_conduit_leg INTEGER NOT NULL DEFAULT 0 CHECK (is_conduit_leg IN (0,1)),",
      "is_memo_leg INTEGER NOT NULL DEFAULT 0 CHECK (is_memo_leg IN (0,1)),",
      "is_passthrough_leg INTEGER NOT NULL DEFAULT 0 CHECK (is_passthrough_leg IN (0,1)),",
      "superseded_by_amendment INTEGER NOT NULL DEFAULT 0",
      "CHECK (superseded_by_amendment IN (0,1)),",
      "duplicate_filing_version INTEGER NOT NULL DEFAULT 0",
      "CHECK (duplicate_filing_version IN (0,1)),",
      "duplicate_memo INTEGER NOT NULL DEFAULT 0 CHECK (duplicate_memo IN (0,1)),",
      "company_match_status TEXT NOT NULL DEFAULT 'review'",
      "CHECK (company_match_status IN ('accepted','review','rejected')),",
      "company_match_reason TEXT, excluded_reason TEXT,",
      "contributor_street_1 TEXT, contributor_street_2 TEXT",
      ")"
    ),
    paste(
      "CREATE TABLE IF NOT EXISTS run_metrics (",
      "run_id TEXT NOT NULL, phase TEXT NOT NULL, started_at TEXT NOT NULL,",
      "ended_at TEXT NOT NULL, wall_clock_seconds REAL NOT NULL,",
      "rows_processed INTEGER NOT NULL DEFAULT 0, request_count INTEGER NOT NULL DEFAULT 0,",
      "rows_per_second REAL NOT NULL DEFAULT 0, detail TEXT,",
      "PRIMARY KEY (run_id, phase)",
      ")"
    ),
    paste(
      "CREATE INDEX IF NOT EXISTS idx_contributions_company_cycle",
      "ON contributions (company, two_year_transaction_period)"
    ),
    paste(
      "CREATE INDEX IF NOT EXISTS idx_contributions_reconcile",
      "ON contributions (contributor_name, contribution_receipt_date, contribution_receipt_amount)"
    ),
    paste(
      "CREATE INDEX IF NOT EXISTS idx_canary_company_cycle",
      "ON canary_log (company, cycle)"
    )
  )
}

init_schema <- function(connection) {
  require_package("DBI")
  DBI::dbWithTransaction(connection, {
    for (statement in mentat_schema_sql()) {
      DBI::dbExecute(connection, statement)
    }
    if (!"last_observed_at" %in% DBI::dbListFields(connection, "fec_response_observations")) {
      DBI::dbExecute(connection, "ALTER TABLE fec_response_observations ADD COLUMN last_observed_at TEXT")
      DBI::dbExecute(connection, "UPDATE fec_response_observations SET last_observed_at=observed_at")
    }
    for (table in c("contributions", "corporate_giving")) {
      if (!"contributor_id" %in% DBI::dbListFields(connection, table)) {
        DBI::dbExecute(connection, paste("ALTER TABLE", table, "ADD COLUMN contributor_id TEXT"))
      }
    }
    if (!"memo_review" %in% DBI::dbListFields(connection, "contributions")) {
      DBI::dbExecute(
        connection,
        paste(
          "ALTER TABLE contributions ADD COLUMN memo_review INTEGER",
          "NOT NULL DEFAULT 0 CHECK (memo_review IN (0,1))"
        )
      )
    }
    contribution_fields <- DBI::dbListFields(connection, "contributions")
    if (!"company_match_status" %in% contribution_fields) {
      DBI::dbExecute(
        connection,
        paste(
          "ALTER TABLE contributions ADD COLUMN company_match_status TEXT",
          "NOT NULL DEFAULT 'accepted'",
          "CHECK (company_match_status IN ('accepted','review','rejected'))"
        )
      )
    }
    if (!"company_match_reason" %in% contribution_fields) {
      DBI::dbExecute(
        connection,
        "ALTER TABLE contributions ADD COLUMN company_match_reason TEXT"
      )
    }
    if (!"is_entity_contributor" %in% contribution_fields) {
      DBI::dbExecute(
        connection,
        paste(
          "ALTER TABLE contributions ADD COLUMN is_entity_contributor INTEGER",
          "NOT NULL DEFAULT 0 CHECK (is_entity_contributor IN (0,1))"
        )
      )
    }
    contribution_migrations <- c(
      filing_form = "TEXT", receipt_type = "TEXT", line_number_label = "TEXT",
      back_reference_transaction_id = "TEXT", back_reference_schedule_name = "TEXT",
      file_num = "INTEGER",
      superseded_by_amendment = paste(
        "INTEGER NOT NULL DEFAULT 0",
        "CHECK (superseded_by_amendment IN (0,1))"
      ),
      duplicate_filing_version = paste(
        "INTEGER NOT NULL DEFAULT 0",
        "CHECK (duplicate_filing_version IN (0,1))"
      ),
      duplicate_memo = "INTEGER NOT NULL DEFAULT 0 CHECK (duplicate_memo IN (0,1))"
    )
    for (column in setdiff(names(contribution_migrations), contribution_fields)) {
      DBI::dbExecute(
        connection,
        paste(
          "ALTER TABLE contributions ADD COLUMN",
          as.character(DBI::dbQuoteIdentifier(connection, column)),
          contribution_migrations[[column]]
        )
      )
    }
    override_info <- DBI::dbGetQuery(
      connection,
      "PRAGMA table_info(company_match_overrides)"
    )
    override_pk <- override_info$name[override_info$pk > 0L][
      order(override_info$pk[override_info$pk > 0L])
    ]
    desired_override_pk <- c(
      "employer_key", "company", "state", "occupation_pattern"
    )
    if (!identical(override_pk, desired_override_pk)) {
      legacy_overrides <- DBI::dbReadTable(connection, "company_match_overrides")
      if (!"state" %in% names(legacy_overrides)) {
        legacy_overrides$state <- rep("", nrow(legacy_overrides))
      }
      if (!"occupation_pattern" %in% names(legacy_overrides)) {
        legacy_overrides$occupation_pattern <- rep("", nrow(legacy_overrides))
      }
      legacy_overrides <- legacy_overrides[, c(
        "employer_key", "company", "state", "occupation_pattern", "decision"
      ), drop = FALSE]
      DBI::dbExecute(
        connection,
        "ALTER TABLE company_match_overrides RENAME TO _mentat_legacy_match_overrides"
      )
      DBI::dbExecute(
        connection,
        paste(
          "CREATE TABLE company_match_overrides (",
          "employer_key TEXT NOT NULL, company TEXT NOT NULL,",
          "state TEXT NOT NULL DEFAULT '', occupation_pattern TEXT NOT NULL DEFAULT '',",
          "decision TEXT NOT NULL CHECK (decision IN ('accept','reject')),",
          "PRIMARY KEY (employer_key, company, state, occupation_pattern))"
        )
      )
      if (nrow(legacy_overrides)) {
        DBI::dbAppendTable(connection, "company_match_overrides", legacy_overrides)
      }
      DBI::dbExecute(connection, "DROP TABLE _mentat_legacy_match_overrides")
    }
    run_log_fields <- DBI::dbListFields(connection, "run_log")
    if (!"decomposition_log" %in% run_log_fields) {
      DBI::dbExecute(
        connection,
        "ALTER TABLE run_log ADD COLUMN decomposition_log TEXT"
      )
    }
    category_fields <- DBI::dbListFields(connection, "categories")
    if (!"provisional" %in% category_fields) {
      DBI::dbExecute(
        connection,
        paste(
          "ALTER TABLE categories ADD COLUMN provisional INTEGER",
          "NOT NULL DEFAULT 0 CHECK (provisional IN (0,1))"
        )
      )
    }
    corporate_fields <- DBI::dbListFields(connection, "corporate_giving")
    corporate_migrations <- c(
      filing_form = "TEXT", receipt_type = "TEXT", receipt_type_full = "TEXT",
      line_number = "TEXT", line_number_label = "TEXT", memo_code = "TEXT",
      memoed_subtotal = "INTEGER", memo_text = "TEXT", back_reference_transaction_id = "TEXT",
      back_reference_schedule_name = "TEXT",
      contributor_city = "TEXT", contributor_state = "TEXT",
      contributor_zip = "TEXT", contributor_employer = "TEXT",
      contributor_occupation = "TEXT", entity_type = "TEXT",
      is_entity_contributor = "INTEGER NOT NULL DEFAULT 0 CHECK (is_entity_contributor IN (0,1))",
      recipient_committee_type = "TEXT", recipient_committee_designation = "TEXT",
      transaction_id = "TEXT", original_sub_id = "TEXT",
      amendment_indicator = "TEXT", report_year = "INTEGER", report_type = "TEXT",
      file_num = "INTEGER", pdf_url = "TEXT", load_date = "TEXT",
      is_conduit_leg = "INTEGER NOT NULL DEFAULT 0 CHECK (is_conduit_leg IN (0,1))",
      is_memo_leg = "INTEGER NOT NULL DEFAULT 0 CHECK (is_memo_leg IN (0,1))",
      is_passthrough_leg = "INTEGER NOT NULL DEFAULT 0 CHECK (is_passthrough_leg IN (0,1))",
      superseded_by_amendment = paste(
        "INTEGER NOT NULL DEFAULT 0",
        "CHECK (superseded_by_amendment IN (0,1))"
      ),
      duplicate_filing_version = paste(
        "INTEGER NOT NULL DEFAULT 0",
        "CHECK (duplicate_filing_version IN (0,1))"
      ),
      duplicate_memo = "INTEGER NOT NULL DEFAULT 0 CHECK (duplicate_memo IN (0,1))",
      company_match_status = paste(
        "TEXT NOT NULL DEFAULT 'review'",
        "CHECK (company_match_status IN ('accepted','review','rejected'))"
      ),
      company_match_reason = "TEXT", excluded_reason = "TEXT",
      # Street is migrated LAST so a legacy database ends up with the same
      # PRAGMA column order as one created today, where street is also last.
      contributor_street_1 = "TEXT", contributor_street_2 = "TEXT"
    )
    for (column in setdiff(names(corporate_migrations), corporate_fields)) {
      DBI::dbExecute(
        connection,
        paste(
          "ALTER TABLE corporate_giving ADD COLUMN",
          as.character(DBI::dbQuoteIdentifier(connection, column)),
          corporate_migrations[[column]]
        )
      )
    }
    # The API returns contributor_street_1/contributor_street_2 on every
    # receipt. Databases collected before the address block was stored carry
    # neither column; ADD COLUMN is the only migration SQLite offers that
    # keeps the existing rows. It can only APPEND, so street is last in the
    # CREATE TABLE lists above and this loop runs after every other column
    # migration: a migrated database and a fresh one then agree on PRAGMA
    # order, which is what a shard merge compares
    # (tools/merge_company_shards.py:264).
    for (table in c("contributions", "bulk_candidates")) {
      street_fields <- DBI::dbListFields(connection, table)
      street_missing <- setdiff(
        c("contributor_street_1", "contributor_street_2"), street_fields
      )
      for (column in street_missing) {
        DBI::dbExecute(
          connection,
          paste(
            "ALTER TABLE",
            as.character(DBI::dbQuoteIdentifier(connection, table)),
            "ADD COLUMN",
            as.character(DBI::dbQuoteIdentifier(connection, column)),
            "TEXT"
          )
        )
      }
    }
    if (!"company_match_status" %in% corporate_fields) {
      DBI::dbExecute(
        connection,
        paste(
          "UPDATE corporate_giving SET company_match_status='review',",
          "company_match_reason='migration_requires_revalidation'"
        )
      )
    }
    if (!"query_contract" %in% DBI::dbListFields(connection, "api_work_progress")) {
      DBI::dbExecute(connection, "ALTER TABLE api_work_progress ADD COLUMN query_contract TEXT")
    }
    # Existing checkpoints predate the hardened readiness rule, so their zeros
    # were never confirmed against the empty-table marker. The DEFAULT 0 is the
    # honest value for them - do not backfill it to 1.
    if (!"zero_verified" %in% DBI::dbListFields(connection, "scrape_progress")) {
      DBI::dbExecute(
        connection,
        paste(
          "ALTER TABLE scrape_progress ADD COLUMN zero_verified",
          "INTEGER NOT NULL DEFAULT 0 CHECK (zero_verified IN (0,1))"
        )
      )
    }
    DBI::dbExecute(
      connection,
      paste(
        "CREATE INDEX IF NOT EXISTS idx_contributions_amendment_version",
        "ON contributions (committee_id, transaction_id, report_year, report_type)"
      )
    )
    DBI::dbExecute(
      connection,
      paste(
        "CREATE INDEX IF NOT EXISTS idx_corporate_amendment_version",
        "ON corporate_giving (committee_id, transaction_id, report_year, report_type)"
      )
    )
    DBI::dbExecute(
      connection,
      paste(
        "CREATE INDEX IF NOT EXISTS idx_contributions_filing_identity",
        "ON contributions (company, committee_id, contribution_receipt_date,",
        "contribution_receipt_amount, contributor_name, contributor_zip)"
      )
    )
    DBI::dbExecute(
      connection,
      paste(
        "CREATE INDEX IF NOT EXISTS idx_corporate_filing_identity",
        "ON corporate_giving (company, committee_id, contribution_receipt_date,",
        "contribution_receipt_amount)"
      )
    )
  })
  invisible(connection)
}

mentat_version_text <- function(x) {
  value <- as.character(x)
  value[is.na(value)] <- ""
  trimws(toupper(value))
}

mentat_sub_id_filing_date <- function(x) {
  value <- gsub("[^0-9]", "", as.character(x), perl = TRUE)
  result <- rep("", length(value))
  eligible <- nchar(value) >= 9L
  if (!any(eligible)) return(result)
  month_day <- substr(value[eligible], 2L, 5L)
  year <- substr(value[eligible], 6L, 9L)
  candidate <- paste0(year, month_day)
  parsed <- suppressWarnings(as.Date(candidate, format = "%Y%m%d"))
  good <- !is.na(parsed)
  result[which(eligible)[good]] <- format(parsed[good], "%Y%m%d")
  result
}

mentat_pdf_filing_id <- function(x) {
  value <- as.character(x)
  value[is.na(value)] <- ""
  result <- sub("^.*[?=/]([0-9]{12,})[^0-9]*$", "\\1", value, perl = TRUE)
  result[result == value & !grepl("^[0-9]{12,}$", value)] <- ""
  result
}

mentat_amendment_version_plan <- function(rows) {
  rows <- as.data.frame(rows, stringsAsFactors = FALSE)
  if (!nrow(rows)) {
    return(data.frame(
      sub_id = character(), version_key = character(),
      superseded_by_amendment = integer(), stringsAsFactors = FALSE
    ))
  }
  column <- function(name, default = "") {
    if (name %in% names(rows)) rows[[name]] else rep(default, nrow(rows))
  }
  sub_id <- mentat_version_text(column("sub_id"))
  committee <- mentat_version_text(column("committee_id"))
  company <- mentat_version_text(column("company"))
  transaction <- mentat_version_text(column("transaction_id"))
  original <- mentat_version_text(column("original_sub_id"))
  report_type <- mentat_version_text(column("report_type"))
  report_year <- mentat_version_text(column("report_year"))
  cycle <- mentat_version_text(column("cycle"))
  report_year[!nzchar(report_year)] <- cycle[!nzchar(report_year)]

  parent_candidates <- which(nzchar(sub_id) & nzchar(original))
  parent_map <- setNames(original[parent_candidates], sub_id[parent_candidates])
  parent_lookup <- list2env(
    as.list(parent_map),
    envir = new.env(hash = TRUE, parent = emptyenv())
  )
  find_root <- function(seed) {
    current <- seed
    seen <- character()
    while (nzchar(current) &&
           exists(current, envir = parent_lookup, inherits = FALSE) &&
           !current %in% seen) {
      seen <- c(seen, current)
      next_value <- get(current, envir = parent_lookup, inherits = FALSE)
      if (!nzchar(next_value)) break
      current <- next_value
    }
    current
  }
  referenced <- sub_id %in% original[nzchar(original)]
  linked_seed <- ifelse(nzchar(original), original, ifelse(referenced, sub_id, ""))
  linked_root <- vapply(linked_seed, function(value) {
    if (nzchar(value)) find_root(value) else ""
  }, character(1L))
  transaction_key <- ifelse(
    nzchar(transaction),
    paste(company, committee, report_year, report_type, transaction, sep = "|"),
    ""
  )
  transaction_index <- which(nzchar(transaction_key))
  transaction_groups <- split(
    transaction_index,
    transaction_key[transaction_index],
    drop = TRUE
  )
  for (index in transaction_groups) {
    roots <- unique(linked_root[index][nzchar(linked_root[index])])
    if (length(roots) == 1L) linked_root[index] <- roots[[1L]]
  }
  version_key <- ifelse(
    nzchar(linked_root),
    paste("ORIGINAL", company, committee, linked_root, sep = "|"),
    ifelse(
      nzchar(transaction_key),
      paste0("TRANSACTION|", transaction_key),
      paste0("SUB_ID|", sub_id)
    )
  )

  file_num <- suppressWarnings(as.numeric(column("file_num", NA_real_)))
  pdf_id <- mentat_pdf_filing_id(column("pdf_url"))
  load_date <- mentat_version_text(column("load_date"))
  sub_date <- mentat_sub_id_filing_date(sub_id)
  amendment <- mentat_version_text(column("amendment_indicator"))
  amendment_priority <- ifelse(
    amendment == "N" | !nzchar(amendment),
    0L,
    ifelse(amendment == "T", 2L, 1L)
  )
  superseded <- rep(FALSE, nrow(rows))
  latest_value <- function(value) sort(unique(value), method = "radix")[[
    length(unique(value))
  ]]
  version_groups <- split(seq_along(version_key), version_key, drop = TRUE)
  for (index in version_groups) {
    key <- version_key[[index[[1L]]]]
    if (length(index) < 2L || startsWith(key, "SUB_ID|")) next
    candidates <- index
    if (all(!is.na(file_num[index]))) {
      candidates <- index[file_num[index] == max(file_num[index])]
      superseded[setdiff(index, candidates)] <- TRUE
    } else if (all(nzchar(pdf_id[index]))) {
      latest <- latest_value(pdf_id[index])
      candidates <- index[pdf_id[index] == latest]
      superseded[setdiff(index, candidates)] <- TRUE
    } else if (all(nzchar(load_date[index]))) {
      latest <- latest_value(load_date[index])
      candidates <- index[load_date[index] == latest]
      superseded[setdiff(index, candidates)] <- TRUE
    } else if (all(nzchar(sub_date[index]))) {
      latest <- latest_value(sub_date[index])
      candidates <- index[sub_date[index] == latest]
      superseded[setdiff(index, candidates)] <- TRUE
    }
    priority <- amendment_priority[candidates]
    if (length(candidates) > 1L && length(unique(priority)) > 1L) {
      keep <- candidates[priority == max(priority)]
      superseded[setdiff(candidates, keep)] <- TRUE
    }
  }
  data.frame(
    sub_id = as.character(column("sub_id")),
    version_key = version_key,
    superseded_by_amendment = as.integer(superseded),
    stringsAsFactors = FALSE
  )
}

mentat_filing_identity_date <- function(x) {
  value <- trimws(as.character(x))
  value[is.na(value)] <- ""
  has_iso_date <- grepl("^[0-9]{4}-[0-9]{2}-[0-9]{2}", value)
  value[has_iso_date] <- substr(value[has_iso_date], 1L, 10L)
  toupper(value)
}

mentat_filing_identity_amount <- function(x) {
  value <- suppressWarnings(as.numeric(x))
  ifelse(
    is.na(value),
    "",
    format(round(value, 2L), scientific = FALSE, trim = TRUE, nsmall = 2L)
  )
}

mentat_filing_identity_zip <- function(x) {
  value <- mentat_version_text(x)
  digits <- gsub("[^0-9]", "", value, perl = TRUE)
  has_digits <- nzchar(digits)
  value[has_digits] <- digits[has_digits]
  value
}

mentat_filing_link_components <- function(sub_id, transaction_id, original_sub_id) {
  count <- length(sub_id)
  parent <- seq_len(count)
  find_root <- function(index) {
    while (parent[[index]] != index) {
      parent[[index]] <<- parent[[parent[[index]]]]
      index <- parent[[index]]
    }
    index
  }
  join <- function(left, right) {
    left_root <- find_root(left)
    right_root <- find_root(right)
    if (left_root != right_root) parent[[right_root]] <<- left_root
  }
  transaction_index <- which(nzchar(transaction_id))
  transaction_groups <- split(
    transaction_index,
    transaction_id[transaction_index],
    drop = TRUE
  )
  for (index in transaction_groups) {
    if (length(index) > 1L) {
      for (other in index[-1L]) join(index[[1L]], other)
    }
  }
  linked_index <- which(nzchar(original_sub_id))
  original_index <- match(original_sub_id[linked_index], sub_id)
  for (position in which(!is.na(original_index))) {
    join(linked_index[[position]], original_index[[position]])
  }
  vapply(seq_len(count), find_root, integer(1L))
}

mentat_filing_version_winner <- function(index, rows) {
  column <- function(name, default = "") {
    if (name %in% names(rows)) rows[[name]] else rep(default, nrow(rows))
  }
  candidates <- index
  file_num <- suppressWarnings(as.numeric(column("file_num", NA_real_)))
  known_file <- candidates[!is.na(file_num[candidates])]
  if (length(known_file)) {
    candidates <- known_file[file_num[known_file] == max(file_num[known_file])]
  }
  amendment <- mentat_version_text(column("amendment_indicator"))
  amendment_priority <- ifelse(
    amendment == "N" | !nzchar(amendment),
    0L,
    ifelse(amendment == "T", 2L, 1L)
  )
  if (length(candidates) > 1L && any(amendment_priority[candidates] > 0L)) {
    candidates <- candidates[
      amendment_priority[candidates] == max(amendment_priority[candidates])
    ]
  }
  load_date <- mentat_version_text(column("load_date"))
  known_load <- candidates[nzchar(load_date[candidates])]
  if (length(known_load)) {
    latest_load <- sort(unique(load_date[known_load]), method = "radix")
    candidates <- known_load[
      load_date[known_load] == latest_load[[length(latest_load)]]
    ]
  }
  sub_id <- mentat_version_text(column("sub_id"))
  candidates[[order(sub_id[candidates], method = "radix", decreasing = TRUE)[[1L]]]]
}

mentat_filing_version_plan <- function(rows, table) {
  table <- match.arg(table, c("contributions", "corporate_giving"))
  rows <- as.data.frame(rows, stringsAsFactors = FALSE)
  if (!nrow(rows)) {
    return(data.frame(
      sub_id = character(), identity_key = character(),
      duplicate_filing_version = integer(), stringsAsFactors = FALSE
    ))
  }
  column <- function(name, default = "") {
    if (name %in% names(rows)) rows[[name]] else rep(default, nrow(rows))
  }
  sub_id <- mentat_version_text(column("sub_id"))
  company <- mentat_version_text(column("company"))
  committee <- mentat_version_text(column("committee_id"))
  receipt_date <- mentat_filing_identity_date(column("contribution_receipt_date"))
  amount <- mentat_filing_identity_amount(column(
    "contribution_receipt_amount",
    NA_real_
  ))
  contributor <- mentat_version_text(column("contributor_name"))
  contributor_zip <- mentat_filing_identity_zip(column("contributor_zip"))
  required <- nzchar(company) & nzchar(committee) & nzchar(receipt_date) &
    nzchar(amount)
  if (identical(table, "contributions")) required <- required & nzchar(contributor)
  identity_value <- if (identical(table, "contributions")) {
    paste(
      company, committee, receipt_date, amount, contributor, contributor_zip,
      sep = "|"
    )
  } else {
    paste(company, committee, receipt_date, amount, sep = "|")
  }
  identity_key <- ifelse(required, identity_value, "")
  excluded_reason <- mentat_version_text(column("excluded_reason"))
  eligible <- !nzchar(excluded_reason) |
    excluded_reason == "DUPLICATE_FILING_VERSION"
  transaction <- mentat_version_text(column("transaction_id"))
  original <- mentat_version_text(column("original_sub_id"))
  file_num <- suppressWarnings(as.numeric(column("file_num", NA_real_)))
  amendment <- mentat_version_text(column("amendment_indicator"))
  amendment_priority <- ifelse(
    amendment == "N" | !nzchar(amendment),
    0L,
    ifelse(amendment == "T", 2L, 1L)
  )
  load_date <- mentat_version_text(column("load_date"))
  winner_for <- function(candidates) {
    known_file <- candidates[!is.na(file_num[candidates])]
    if (length(known_file)) {
      candidates <- known_file[
        file_num[known_file] == max(file_num[known_file])
      ]
    }
    if (length(candidates) > 1L &&
        any(amendment_priority[candidates] > 0L)) {
      candidates <- candidates[
        amendment_priority[candidates] ==
          max(amendment_priority[candidates])
      ]
    }
    known_load <- candidates[nzchar(load_date[candidates])]
    if (length(known_load)) {
      latest_load <- max(load_date[known_load])
      candidates <- known_load[load_date[known_load] == latest_load]
    }
    candidates[[
      order(
        sub_id[candidates],
        method = "radix",
        decreasing = TRUE
      )[[1L]]
    ]]
  }
  duplicate <- rep(FALSE, nrow(rows))
  eligible_index <- which(nzchar(identity_key) & eligible)
  identity_groups <- split(
    eligible_index,
    identity_key[eligible_index],
    drop = TRUE
  )
  for (index in identity_groups) {
    if (length(index) < 2L) next
    groups <- if (identical(table, "corporate_giving")) {
      rep(1L, length(index))
    } else {
      mentat_filing_link_components(
        sub_id[index],
        transaction[index],
        original[index]
      )
    }
    for (group in unique(groups)) {
      candidates <- index[groups == group]
      if (length(candidates) < 2L) next
      winner <- winner_for(candidates)
      duplicate[setdiff(candidates, winner)] <- TRUE
    }
  }
  data.frame(
    sub_id = as.character(column("sub_id")),
    identity_key = identity_key,
    duplicate_filing_version = as.integer(duplicate),
    stringsAsFactors = FALSE
  )
}

# F13, the memo re-disclosure rule (Paul approved 2026-09-26, "Approve F13").
# D-040(a) removes "a proven duplicate of a counted parent"; D-043 lets a row's
# own text decide. A memo whose own text states that it re-discloses a gift
# already itemized - collect.R's unverified_disclosure_memo role, i.e. a memo
# row matching fec_receipt_rules.json's disclosure_pattern - duplicates the
# counted parent it restates: same company, same recipient committee, same
# contributor (FEC contributor_id when both rows carry one, else the normalised
# name), the same nonzero amount to the cent, dated on or before the memo, and
# not itself a memo. The money then counts once, via the parent; with several
# such parents the latest-dated one is recorded (then the lowest sub_id). A
# memo that calls the gift unitemized (redisclosure_unitemized_pattern)
# restates no itemized parent. Missing or ambiguous information never excludes
# (D-040): with no such parent the memo stays counted and keeps its
# disclosure_memo_unmatched flag, and equal amounts without the statement stay
# a clue, never proof (the 2026-09-20 contradictions-only pass). A counted
# parent is an eligible row (company match accepted or blank, no
# excluded_reason, a giving role) whose attribution, when the rows carry it,
# is not contradicted. Returns each row's parent sub_id ("" for none).
# tools/pac_evidence.py memo_redisclosure_parents() is the Python twin, and
# tests/fixtures/memo_redisclosure_cases.json pins both.
mentat_memo_redisclosure_parents <- function(rows, roles, eligible) {
  rows <- as.data.frame(rows, stringsAsFactors = FALSE)
  count <- nrow(rows)
  parent <- rep("", count)
  if (!count || is.null(roles) || !exists("fec_receipt_rules", mode = "function")) return(parent)
  unitemized <- fec_receipt_rules()$redisclosure_unitemized_pattern
  if (!is.character(unitemized) || length(unitemized) != 1L || !nzchar(unitemized)) {
    stop("fec_receipt_rules.json lacks redisclosure_unitemized_pattern; the F13 memo rule cannot run.", call. = FALSE)
  }
  # Work on row subsets: the global plan runs over every row of both tables
  # (about 850k for Banking) and only a handful are re-disclosure memos.
  column <- function(name, index, default = "") {
    if (name %in% names(rows)) rows[[name]][index] else rep(default, length(index))
  }
  text <- function(name, index) mentat_version_text(column(name, index))
  words <- function(x) gsub("^ +| +$", "", gsub("[^A-Z0-9]+", " ", x, perl = TRUE), perl = TRUE)
  eligible <- as.logical(eligible)
  eligible[is.na(eligible)] <- FALSE
  # Facts about the rows in `index`: memo flag, counted, amount key, day.
  describe <- function(index) {
    memo <- text("memo_code", index) == "X" | text("memoed_subtotal", index) %in% c("1", "TRUE") |
      text("is_memo_leg", index) %in% c("1", "TRUE")
    counted <- eligible[index]
    if ("company_attribution_status" %in% names(rows)) {
      counted <- counted & !text("company_attribution_status", index) %in%
        c("CONTRADICTED_BY_REPORTED_ORGANIZATION", "EXPLICIT_SOURCE_EXCLUSION")
    }
    amount <- mentat_filing_identity_amount(column("contribution_receipt_amount", index, NA_real_))
    money <- nzchar(amount) & suppressWarnings(as.numeric(amount)) != 0
    money[is.na(money)] <- FALSE
    day <- mentat_filing_identity_date(column("contribution_receipt_date", index))
    dated <- grepl("^[0-9]{4}-[0-9]{2}-[0-9]{2}$", day)
    day_number <- rep(NA_integer_, length(index))
    day_number[dated] <- as.integer(gsub("-", "", day[dated], fixed = TRUE))
    committee <- text("committee_id", index)
    sub_id <- as.character(column("sub_id", index))
    sub_id[is.na(sub_id)] <- ""
    data.frame(row = index, memo = memo, counted = counted & money & dated, committee = committee,
               key = paste(text("company", index), committee, amount, sep = "\034"),
               day_number = day_number, sub_id = sub_id,
               entity = text("contributor_id", index), name = words(text("contributor_name", index)),
               stringsAsFactors = FALSE)
  }
  memos <- describe(which(roles %in% "unverified_disclosure_memo" & eligible))
  memos <- memos[memos$memo & memos$counted & nzchar(memos$committee), , drop = FALSE]
  if (nrow(memos)) {
    statement <- words(paste(text("receipt_type_full", memos$row), text("memo_text", memos$row)))
    memos <- memos[!grepl(unitemized, statement, perl = TRUE), , drop = FALSE]
  }
  if (!nrow(memos)) return(parent)
  pool <- describe(which(eligible & mentat_version_text(column("committee_id", seq_len(count))) %in% memos$committee))
  pool <- pool[!pool$memo & pool$counted & nzchar(trimws(pool$sub_id)) & pool$key %in% memos$key, , drop = FALSE]
  if (!nrow(pool)) return(parent)
  for (m in seq_len(nrow(memos))) {
    candidates <- pool[pool$key == memos$key[[m]] & pool$day_number <= memos$day_number[[m]], , drop = FALSE]
    same <- if (nzchar(memos$entity[[m]])) {
      ifelse(nzchar(candidates$entity), candidates$entity == memos$entity[[m]],
             nzchar(memos$name[[m]]) & candidates$name == memos$name[[m]])
    } else {
      nzchar(memos$name[[m]]) & candidates$name == memos$name[[m]]
    }
    candidates <- candidates[same, , drop = FALSE]
    if (!nrow(candidates)) next
    best <- order(-candidates$day_number, candidates$sub_id, method = "radix")[[1L]]
    parent[[memos$row[[m]]]] <- candidates$sub_id[[best]]
  }
  parent
}

mentat_memo_duplicate_plan <- function(rows) {
  rows <- as.data.frame(rows, stringsAsFactors = FALSE)
  column <- function(name, default = "") {
    if (name %in% names(rows)) rows[[name]] else rep(default, nrow(rows))
  }
  text <- function(name) mentat_version_text(column(name))
  count <- nrow(rows)
  company <- text("company"); committee <- text("committee_id")
  date <- mentat_filing_identity_date(column("contribution_receipt_date"))
  amount <- mentat_filing_identity_amount(column("contribution_receipt_amount", NA_real_))
  contributor <- text("contributor_name"); sub_id <- text("sub_id")
  required <- nzchar(company) & nzchar(committee) & nzchar(date) & nzchar(amount) & nzchar(contributor)
  memo_key <- ifelse(required, paste(company, committee, date, amount, contributor, sep = "|"), "")
  is_memo <- suppressWarnings(as.integer(column("is_memo_leg", 0L))) == 1L
  is_memo[is.na(is_memo)] <- FALSE
  eligible <- text("company_match_status") %in% c("", "ACCEPTED") & !nzchar(text("excluded_reason"))
  verified_parent <- rep(TRUE, count)
  receipt_roles <- NULL
  if (exists("fec_receipt_role", mode = "function")) {
    receipt_roles <- fec_receipt_role(rows)
    eligible <- eligible & !fec_receipt_role_excluded(receipt_roles)
    verified_parent <- !fec_receipt_role_unresolved(receipt_roles)
  }
  parents <- which(!is_memo & eligible & verified_parent & nzchar(sub_id))
  transaction <- text("transaction_id")
  signed_amount <- suppressWarnings(as.numeric(column("contribution_receipt_amount", NA_real_)))
  key <- paste(company, committee, transaction, sep = "\034")
  refs <- split(parents[nzchar(transaction[parents])], key[parents][nzchar(transaction[parents])])
  file <- text("file_num")
  file[file == "0"] <- ""
  same_context <- function(i, candidates) {
    if (!length(candidates)) return(integer())
    known_file <- nzchar(file[i]) & nzchar(file[candidates])
    # Transaction references are local to a filing. Report year/type or matching
    # amount/date alone do not establish a unique parent across amendments.
    valid <- known_file & file[candidates] == file[i]
    candidates[valid]
  }
  choose <- function(candidates) {
    if (length(candidates) != 1L) return(NA_integer_)
    candidates[[1L]]
  }
  duplicate_of <- reason <- rep("", count)
  description <- paste(text("receipt_type_full"), text("memo_text"))
  structured <- text("back_reference_transaction_id")
  schedule <- text("back_reference_schedule_name")
  for (i in which(is_memo & eligible)) {
    candidates <- integer()
    basis <- ""
    # Consume FEC's structured relationship first; text is a legacy fallback.
    reference <- structured[i]
    if (nzchar(reference)) {
      alternatives <- unique(c(reference, if (nzchar(schedule[i])) paste(schedule[i], reference, sep = ".")))
      basis_ref <- "fec_structured_back_reference"
    } else {
      hit <- regexpr("\\[[A-Z0-9]+:[A-Z0-9.]+\\]", description[i], perl = TRUE)
      reference <- if (hit[1L] > 0L) sub(":", ".", gsub("[][]", "", regmatches(description[i], hit)), fixed = TRUE) else ""
      alternatives <- reference
      basis_ref <- "fec_text_back_reference"
    }
    if (nzchar(reference)) {
      linked <- unique(unlist(lapply(alternatives, function(ref) refs[[paste(company[i], committee[i], ref, sep = "\034")]])))
      # Similarity only screens the explicit FEC relationship; it cannot create one.
      candidates <- same_context(i, linked)
      # A reference is insufficient if it points to another donor/date, the
      # opposite cash direction, or a smaller amount than this memo claims.
      covered <- contributor[candidates] == contributor[i] & date[candidates] == date[i] &
        sign(signed_amount[candidates]) == sign(signed_amount[i]) &
        abs(signed_amount[candidates]) + 0.005 >= abs(signed_amount[i])
      candidates <- candidates[!is.na(covered) & covered]
      basis <- basis_ref
    }
    winner <- choose(candidates)
    if (!is.na(winner)) {
      duplicate_of[i] <- as.character(column("sub_id"))[winner]
      reason[i] <- basis
    }
  }
  # F13: the memo's own re-disclosure statement plus a matching counted parent
  # (mentat_memo_redisclosure_parents). An FEC back reference resolved above
  # keeps precedence.
  restated <- mentat_memo_redisclosure_parents(rows, receipt_roles, eligible)
  restated[nzchar(duplicate_of)] <- ""
  duplicate_of[nzchar(restated)] <- restated[nzchar(restated)]
  reason[nzchar(restated)] <- "memo_redisclosure_of_counted_parent"
  # Conduit legs remain visible and count to their named recipients (R5/R8).
  # Only same-recipient memos with a retained, eligible counterpart are removed.
  data.frame(sub_id = as.character(column("sub_id")), memo_key = memo_key,
             duplicate_memo = as.integer(nzchar(duplicate_of)),
             duplicate_of_sub_id = duplicate_of, duplicate_reason = reason,
             stringsAsFactors = FALSE)
}

apply_amendment_versioning <- function(connection, print_summary = FALSE) {
  require_package("DBI")
  summaries <- list()
  tables <- intersect(
    c("contributions", "corporate_giving"),
    DBI::dbListTables(connection)
  )
  # transaction_id / receipt_type_full / memo_text feed the parent back-reference
  # rule in mentat_memo_duplicate_plan(), and contributor_id F13's parent match.
  # Older corporate snapshots lack these fields, so each frame is padded before
  # combining both source tables.
  memo_plan_columns <- c(
    "sub_id", "company", "committee_id", "contribution_receipt_date",
    "contribution_receipt_amount", "contributor_name", "is_memo_leg",
    "transaction_id", "receipt_type_full", "memo_text", "back_reference_transaction_id",
    "back_reference_schedule_name", "file_num", "report_year", "report_type",
    "filing_form", "line_number", "memo_code", "memoed_subtotal",
    "company_match_status", "excluded_reason", "contributor_id"
  )
  DBI::dbWithTransaction(connection, {
    global_memo_rows <- do.call(rbind, lapply(tables, function(table) {
      fields <- DBI::dbListFields(connection, table)
      wanted <- intersect(memo_plan_columns, fields)
      rows <- DBI::dbGetQuery(
        connection,
        paste("SELECT", paste(wanted, collapse = ", "), "FROM", table)
      )
      for (column in setdiff(memo_plan_columns, names(rows))) {
        rows[[column]] <- rep(
          if (identical(column, "is_memo_leg")) 0L else "",
          nrow(rows)
        )
      }
      rows <- rows[, memo_plan_columns, drop = FALSE]
      # rep() not a scalar: assigning a length-1 value to a zero-row frame
      # errors, which an empty corporate_giving table triggers.
      rows$.source_table <- rep(table, nrow(rows))
      rows
    }))
    global_memo_plan <- mentat_memo_duplicate_plan(global_memo_rows)
    global_duplicate_memo_ids <- unique(global_memo_plan$sub_id[
      global_memo_plan$duplicate_memo == 1L
    ])
    for (table in tables) {
      fields <- DBI::dbListFields(connection, table)
      wanted <- intersect(c(
        "sub_id", "company", "committee_id", "transaction_id",
        "original_sub_id", "amendment_indicator", "report_year", "report_type",
        "cycle", "file_num", "pdf_url", "load_date",
        "contributor_name", "committee_name", "contribution_receipt_date",
        "contribution_receipt_amount", "contributor_zip", "is_memo_leg",
        "excluded_reason"
      ), fields)
      quoted_fields <- paste(vapply(
        wanted,
        function(field) as.character(DBI::dbQuoteIdentifier(connection, field)),
        character(1L)
      ), collapse = ", ")
      rows <- DBI::dbGetQuery(
        connection,
        paste("SELECT", quoted_fields, "FROM", table, "ORDER BY sub_id")
      )
      plan <- mentat_amendment_version_plan(rows)
      filing_plan <- mentat_filing_version_plan(rows, table)
      memo_plan <- mentat_memo_duplicate_plan(rows)
      memo_plan$duplicate_memo <- as.integer(
        memo_plan$sub_id %in% global_duplicate_memo_ids
      )
      DBI::dbExecute(
        connection,
        paste0("UPDATE ", table, " SET superseded_by_amendment=0")
      )
      DBI::dbExecute(
        connection,
        paste0(
          "UPDATE ", table, " SET excluded_reason=NULL ",
          "WHERE LOWER(TRIM(excluded_reason))='duplicate_filing_version'"
        )
      )
      DBI::dbExecute(
        connection,
        paste0(
          "UPDATE ", table,
          " SET duplicate_filing_version=0, duplicate_memo=0"
        )
      )
      if (nrow(plan) && any(plan$superseded_by_amendment == 1L)) {
        stage <- paste0("_mentat_amendment_", table)
        DBI::dbExecute(connection, paste("DROP TABLE IF EXISTS temp.", stage))
        DBI::dbExecute(
          connection,
          paste("CREATE TEMP TABLE", stage, "(sub_id TEXT PRIMARY KEY)")
        )
        DBI::dbAppendTable(
          connection,
          stage,
          plan[plan$superseded_by_amendment == 1L, "sub_id", drop = FALSE]
        )
        DBI::dbExecute(
          connection,
          paste0(
            "UPDATE ", table, " SET superseded_by_amendment=1 ",
            "WHERE sub_id IN (SELECT sub_id FROM ", stage, ")"
          )
        )
        DBI::dbExecute(connection, paste("DROP TABLE", stage))
      }
      if (nrow(filing_plan) && any(filing_plan$duplicate_filing_version == 1L)) {
        stage <- paste0("_mentat_filing_", table)
        DBI::dbExecute(connection, paste("DROP TABLE IF EXISTS temp.", stage))
        DBI::dbExecute(
          connection,
          paste("CREATE TEMP TABLE", stage, "(sub_id TEXT PRIMARY KEY)")
        )
        DBI::dbAppendTable(
          connection,
          stage,
          filing_plan[
            filing_plan$duplicate_filing_version == 1L,
            "sub_id",
            drop = FALSE
          ]
        )
        DBI::dbExecute(
          connection,
          paste0(
            "UPDATE ", table,
            " SET duplicate_filing_version=1 ",
            "WHERE sub_id IN (SELECT sub_id FROM ", stage, ")"
          )
        )
        DBI::dbExecute(connection, paste("DROP TABLE", stage))
      }
      if (nrow(memo_plan) && any(memo_plan$duplicate_memo == 1L)) {
        stage <- paste0("_mentat_memo_", table)
        DBI::dbExecute(connection, paste("DROP TABLE IF EXISTS temp.", stage))
        DBI::dbExecute(
          connection,
          paste("CREATE TEMP TABLE", stage, "(sub_id TEXT PRIMARY KEY)")
        )
        DBI::dbAppendTable(
          connection,
          stage,
          memo_plan[memo_plan$duplicate_memo == 1L, "sub_id", drop = FALSE]
        )
        DBI::dbExecute(
          connection,
          paste0(
            "UPDATE ", table, " SET duplicate_memo=1 ",
            "WHERE sub_id IN (SELECT sub_id FROM ", stage, ")"
          )
        )
        DBI::dbExecute(connection, paste("DROP TABLE", stage))
      }
      if (nrow(rows)) {
        amount <- suppressWarnings(as.numeric(rows$contribution_receipt_amount))
        amount[is.na(amount)] <- 0
        company_values <- as.character(rows$company)
        company_values[is.na(company_values)] <- ""
        for (company in sort(unique(company_values), method = "radix")) {
          index <- which(company_values == company)
          superseded <- plan$superseded_by_amendment[index] == 1L
          duplicate <- filing_plan$duplicate_filing_version[index] == 1L
          memo_duplicate <- memo_plan$duplicate_memo[index] == 1L
          excluded_from_analysis <- memo_duplicate
          summaries[[length(summaries) + 1L]] <- data.frame(
            table = table,
            company = company,
            before_rows = length(index),
            current_rows = sum(!excluded_from_analysis),
            rows_superseded = sum(superseded),
            rows_duplicate_filing_versions = sum(duplicate),
            rows_memo_duplicates = sum(memo_duplicate),
            rows_collapsed = sum(excluded_from_analysis),
            before_total = sum(amount[index]),
            current_total = sum(amount[index][!excluded_from_analysis]),
            filing_version_dollar_delta = 0,
            memo_duplicate_dollar_delta = -sum(amount[index][memo_duplicate]),
            dollar_delta = -sum(amount[index][excluded_from_analysis]),
            stringsAsFactors = FALSE
          )
        }
      }
    }
  })
  summary <- if (length(summaries)) {
    do.call(rbind, summaries)
  } else {
    data.frame(
      table = character(), company = character(), before_rows = integer(),
      current_rows = integer(), rows_superseded = integer(),
      rows_duplicate_filing_versions = integer(), rows_memo_duplicates = integer(),
      rows_collapsed = integer(),
      before_total = numeric(), current_total = numeric(),
      filing_version_dollar_delta = numeric(), memo_duplicate_dollar_delta = numeric(),
      dollar_delta = numeric(),
      stringsAsFactors = FALSE
    )
  }
  if (isTRUE(print_summary)) {
    cat("R8 row flags (only memos with retained, matched counterparts are excluded here):\n")
    print(summary, row.names = FALSE)
    coinbase <- summary[
      summary$table == "corporate_giving" & summary$company == "Coinbase",
      , drop = FALSE
    ]
    if (nrow(coinbase)) {
      cat(sprintf(
        paste0(
          "Coinbase corporate raw/analysis: $%.2f -> $%.2f ",
          "(%d amendment flags; %d filing-version flags; %d memo duplicates)\n"
        ),
        coinbase$before_total[[1L]], coinbase$current_total[[1L]],
        coinbase$rows_superseded[[1L]],
        coinbase$rows_duplicate_filing_versions[[1L]],
        coinbase$rows_memo_duplicates[[1L]]
      ))
    }
  }
  summary
}

replace_table_atomic <- function(connection, table, data) {
  require_package("DBI")
  if (!DBI::dbExistsTable(connection, table)) {
    stop("Schema table is missing: ", table, call. = FALSE)
  }
  expected <- DBI::dbListFields(connection, table)
  unknown <- setdiff(names(data), expected)
  if (length(unknown)) {
    stop(
      "Unexpected columns for ", table, ": ", paste(unknown, collapse = ", "),
      call. = FALSE
    )
  }
  quoted <- as.character(DBI::dbQuoteIdentifier(connection, table))
  DBI::dbWithTransaction(connection, {
    DBI::dbExecute(connection, paste("DELETE FROM", quoted))
    if (nrow(data)) {
      DBI::dbAppendTable(connection, table, as.data.frame(data))
    }
  })
  invisible(nrow(data))
}

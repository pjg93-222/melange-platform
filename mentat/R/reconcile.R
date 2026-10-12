normalize_reconcile_text <- function(x) {
  value <- as.character(x)
  value[is.na(value)] <- ""
  trimws(gsub("[[:space:]]+", " ", toupper(value), perl = TRUE))
}

normalize_reconcile_date <- function(x) {
  if (inherits(x, "Date")) {
    return(as.Date(x))
  }
  if (inherits(x, "POSIXt")) {
    return(as.Date(x, tz = "UTC"))
  }
  if (is.numeric(x)) {
    return(as.Date(x, origin = "1899-12-30"))
  }
  value <- trim_text(x)
  result <- rep(as.Date(NA), length(value))
  numeric_like <- grepl("^[0-9]+(?:\\.[0-9]+)?$", value)
  if (any(numeric_like)) {
    result[numeric_like] <- as.Date(
      as.numeric(value[numeric_like]),
      origin = "1899-12-30"
    )
  }
  remaining <- which(is.na(result) & nzchar(value))
  for (format in c("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%m/%d/%Y", "%m/%d/%y")) {
    if (!length(remaining)) {
      break
    }
    parsed <- as.Date(value[remaining], format = format)
    good <- !is.na(parsed)
    result[remaining[good]] <- parsed[good]
    remaining <- remaining[!good]
  }
  result
}

first_existing_column <- function(data, candidates, required = TRUE) {
  match <- candidates[candidates %in% names(data)]
  if (!length(match)) {
    if (required) {
      stop(
        "Missing reconciliation column; expected one of: ",
        paste(candidates, collapse = ", "),
        call. = FALSE
      )
    }
    return(NULL)
  }
  match[[1L]]
}

prepare_fixture_reconcile <- function(fixture) {
  require_package("data.table")
  fixture <- data.table::as.data.table(data.table::copy(fixture))
  name_column <- first_existing_column(
    fixture,
    c("contributor_name", "name", "Name")
  )
  recipient_column <- first_existing_column(
    fixture,
    c("recipient_name", "committee_name", "recipient")
  )
  date_column <- first_existing_column(
    fixture,
    c("receipt_date", "contribution_receipt_date", "date")
  )
  amount_column <- first_existing_column(
    fixture,
    c("amount", "contribution_receipt_amount")
  )
  if (!"fixture_id" %in% names(fixture)) {
    fixture[, fixture_id := seq_len(.N)]
  }
  fixture[, `:=`(
    .fixture_row_id = seq_len(.N),
    .norm_name = normalize_reconcile_text(get(name_column)),
    .norm_recipient = normalize_reconcile_text(get(recipient_column)),
    .norm_date = normalize_reconcile_date(get(date_column)),
    .norm_amount = round(as.numeric(get(amount_column)), 2L)
  )]
  if (any(!nzchar(fixture$.norm_name)) ||
      any(!nzchar(fixture$.norm_recipient)) ||
      anyNA(fixture$.norm_date) ||
      anyNA(fixture$.norm_amount)) {
    stop("Fixture contains invalid reconciliation keys.", call. = FALSE)
  }
  fixture[]
}

prepare_pipeline_reconcile <- function(pipeline) {
  require_package("data.table")
  pipeline <- data.table::as.data.table(data.table::copy(pipeline))
  required_defaults <- list(
    contributor_name = character(),
    committee_name = character(),
    contribution_receipt_date = character(),
    contribution_receipt_amount = numeric(),
    sub_id = character()
  )
  if (!nrow(pipeline)) {
    for (column in names(required_defaults)) {
      if (!column %in% names(pipeline)) {
        pipeline[[column]] <- required_defaults[[column]]
      }
    }
  }
  name_column <- first_existing_column(
    pipeline,
    c("contributor_name", "name", "Name")
  )
  recipient_column <- first_existing_column(
    pipeline,
    c("committee_name", "recipient_name", "recipient")
  )
  date_column <- first_existing_column(
    pipeline,
    c("contribution_receipt_date", "receipt_date", "date")
  )
  amount_column <- first_existing_column(
    pipeline,
    c("contribution_receipt_amount", "amount")
  )
  if (!"sub_id" %in% names(pipeline)) {
    pipeline[, sub_id := paste0("pipeline-", seq_len(.N))]
  }
  pipeline[, `:=`(
    .pipeline_row_id = seq_len(.N),
    .norm_name = normalize_reconcile_text(get(name_column)),
    .norm_recipient = normalize_reconcile_text(get(recipient_column)),
    .norm_date = normalize_reconcile_date(get(date_column)),
    .norm_amount = round(as.numeric(get(amount_column)), 2L)
  )]
  pipeline[, .valid_key := nzchar(.norm_name) &
    nzchar(.norm_recipient) &
    !is.na(.norm_date) &
    !is.na(.norm_amount)]
  pipeline[]
}

recipient_prefix25_equal <- function(a, b) {
  substr(a, 1L, 25L) == substr(b, 1L, 25L)
}

stage_candidate_indices <- function(stage, fixture_row, pipeline, available) {
  if (!length(available)) {
    return(integer())
  }
  candidates <- pipeline[available]
  name_equal <- candidates$.norm_name == fixture_row$.norm_name
  date_equal <- candidates$.norm_date == fixture_row$.norm_date
  amount_equal <- candidates$.norm_amount == fixture_row$.norm_amount
  recipient_equal <- candidates$.norm_recipient == fixture_row$.norm_recipient
  selected <- switch(
    stage,
    S1 = name_equal & date_equal & amount_equal & recipient_equal,
    S2 = name_equal & date_equal & amount_equal &
      recipient_prefix25_equal(
        candidates$.norm_recipient,
        fixture_row$.norm_recipient
      ),
    S3 = name_equal & date_equal & amount_equal,
    S4 = name_equal & amount_equal &
      abs(as.integer(candidates$.norm_date - fixture_row$.norm_date)) <= 3L,
    stop("Unknown reconciliation stage: ", stage, call. = FALSE)
  )
  available[which(selected)]
}

choose_stage_candidate <- function(stage, fixture_row, pipeline, candidates) {
  if (length(candidates) == 1L) {
    return(candidates)
  }
  if (identical(stage, "S4")) {
    distance <- abs(as.integer(pipeline$.norm_date[candidates] - fixture_row$.norm_date))
    return(candidates[order(distance, pipeline$.pipeline_row_id[candidates])][[1L]])
  }
  candidates[order(pipeline$.pipeline_row_id[candidates])][[1L]]
}

diagnose_nearest_miss <- function(fixture_row, pipeline) {
  if (!nrow(pipeline)) {
    return(list(nearest_pipeline_id = NA_character_, failed_components = "no_pipeline_rows"))
  }
  same_name <- pipeline$.norm_name == fixture_row$.norm_name
  same_date <- pipeline$.norm_date == fixture_row$.norm_date
  same_amount <- pipeline$.norm_amount == fixture_row$.norm_amount
  same_recipient <- pipeline$.norm_recipient == fixture_row$.norm_recipient
  score <- same_name + same_date + same_amount + same_recipient
  date_distance <- abs(as.integer(pipeline$.norm_date - fixture_row$.norm_date))
  amount_distance <- abs(pipeline$.norm_amount - fixture_row$.norm_amount)
  best <- order(-score, date_distance, amount_distance, pipeline$.pipeline_row_id)[[1L]]
  failures <- c(
    if (!same_name[[best]]) "name",
    if (!same_date[[best]]) "date",
    if (!same_amount[[best]]) "amount",
    if (!same_recipient[[best]]) "recipient"
  )
  if (!length(failures)) {
    failures <- "duplicate_consumed"
  }
  list(
    nearest_pipeline_id = as.character(pipeline$sub_id[[best]]),
    failed_components = paste(failures, collapse = "|")
  )
}

pipeline_only_cause <- function(pipeline, fixture_cutoff = as.Date("2026-05-31")) {
  if (!nrow(pipeline)) {
    return(character())
  }
  get_flag <- function(name) {
    if (name %in% names(pipeline)) {
      as.integer(pipeline[[name]]) == 1L
    } else {
      rep(FALSE, nrow(pipeline))
    }
  }
  amendment <- get_flag("superseded_by_amendment")
  data.table::fcase(
    pipeline$.norm_date > fixture_cutoff, "post_fixture_cutoff",
    get_flag("is_memo_leg"), "memo_leg",
    get_flag("is_conduit_leg"), "conduit_leg",
    get_flag("is_passthrough_leg"), "passthrough_leg",
    amendment, "amendment",
    default = "unexplained"
  )
}

annotate_fixture_residuals <- function(unmatched, database_rows) {
  require_package("data.table")
  unmatched <- data.table::as.data.table(data.table::copy(unmatched))
  if (!nrow(unmatched)) {
    unmatched[, `:=`(
      residual_status = character(),
      database_nearest_sub_id = character(),
      database_failed_components = character()
    )]
    return(unmatched[])
  }
  fixture <- prepare_fixture_reconcile(unmatched)
  database <- prepare_pipeline_reconcile(database_rows)
  database <- database[(.valid_key)]
  diagnostics <- lapply(seq_len(nrow(fixture)), function(index) {
    if (!nrow(database)) {
      return(list(
        residual_status = "genuinely_absent",
        database_nearest_sub_id = NA_character_,
        database_failed_components = "no_database_rows"
      ))
    }
    same_name <- database$.norm_name == fixture$.norm_name[[index]]
    same_date <- database$.norm_date == fixture$.norm_date[[index]]
    same_amount <- database$.norm_amount == fixture$.norm_amount[[index]]
    same_recipient <- database$.norm_recipient == fixture$.norm_recipient[[index]]
    score <- same_name + same_date + same_amount + same_recipient
    date_distance <- abs(as.integer(
      database$.norm_date - fixture$.norm_date[[index]]
    ))
    amount_distance <- abs(
      database$.norm_amount - fixture$.norm_amount[[index]]
    )
    best <- order(
      -score,
      date_distance,
      amount_distance,
      database$.pipeline_row_id
    )[[1L]]
    failures <- c(
      if (!same_name[[best]]) "name",
      if (!same_date[[best]]) "date",
      if (!same_amount[[best]]) "amount",
      if (!same_recipient[[best]]) "recipient"
    )
    if (!length(failures)) failures <- "duplicate_consumed"
    list(
      residual_status = if (score[[best]] >= 3L) {
        "present_in_database"
      } else {
        "genuinely_absent"
      },
      database_nearest_sub_id = as.character(database$sub_id[[best]]),
      database_failed_components = paste(failures, collapse = "|")
    )
  })
  unmatched[, residual_status := vapply(
    diagnostics, `[[`, character(1L), "residual_status"
  )]
  unmatched[, database_nearest_sub_id := vapply(
    diagnostics, `[[`, character(1L), "database_nearest_sub_id"
  )]
  unmatched[, database_failed_components := vapply(
    diagnostics, `[[`, character(1L), "database_failed_components"
  )]
  unmatched[]
}

count_duplicate_tuples <- function(data, id_columns) {
  if (!nrow(data)) {
    return(list(tuples = 0L, rows = 0L))
  }
  counts <- data[, .N, by = id_columns][N > 1L]
  list(
    tuples = nrow(counts),
    rows = if (nrow(counts)) sum(counts$N) else 0L
  )
}

reconcile_tables <- function(
  fixture,
  pipeline,
  fixture_cutoff = as.Date("2026-05-31")
) {
  require_package("data.table")
  fixture <- prepare_fixture_reconcile(fixture)
  pipeline_all <- prepare_pipeline_reconcile(pipeline)
  invalid_pipeline <- data.table::copy(pipeline_all[(.valid_key) == FALSE])
  pipeline <- pipeline_all[(.valid_key)]
  fixture_used <- rep(FALSE, nrow(fixture))
  pipeline_used <- rep(FALSE, nrow(pipeline))
  matches <- list()
  for (stage in c("S1", "S2", "S3", "S4")) {
    for (fixture_index in which(!fixture_used)) {
      available <- which(!pipeline_used)
      candidate_indices <- stage_candidate_indices(
        stage,
        fixture[fixture_index],
        pipeline,
        available
      )
      if (!length(candidate_indices)) {
        next
      }
      chosen <- choose_stage_candidate(
        stage,
        fixture[fixture_index],
        pipeline,
        candidate_indices
      )
      fixture_used[[fixture_index]] <- TRUE
      pipeline_used[[chosen]] <- TRUE
      matches[[length(matches) + 1L]] <- data.table::data.table(
        fixture_row_id = fixture$.fixture_row_id[[fixture_index]],
        pipeline_row_id = pipeline$.pipeline_row_id[[chosen]],
        fixture_id = fixture$fixture_id[[fixture_index]],
        sub_id = pipeline$sub_id[[chosen]],
        stage = stage
      )
    }
  }
  match_table <- if (length(matches)) {
    data.table::rbindlist(matches)
  } else {
    data.table::data.table(
      fixture_row_id = integer(),
      pipeline_row_id = integer(),
      fixture_id = integer(),
      sub_id = character(),
      stage = character()
    )
  }
  stage_counts <- data.table::data.table(stage = c("S1", "S2", "S3", "S4"))
  observed <- match_table[, .(matches = .N), by = stage]
  stage_counts <- observed[stage_counts, on = "stage"]
  stage_counts[is.na(matches), matches := 0L]

  fixture_unmatched <- data.table::copy(fixture[!fixture_used])
  if (nrow(fixture_unmatched)) {
    diagnoses <- lapply(seq_len(nrow(fixture_unmatched)), function(i) {
      diagnose_nearest_miss(fixture_unmatched[i], pipeline)
    })
    fixture_unmatched[, nearest_pipeline_id := vapply(
      diagnoses,
      `[[`,
      character(1L),
      "nearest_pipeline_id"
    )]
    fixture_unmatched[, failed_components := vapply(
      diagnoses,
      `[[`,
      character(1L),
      "failed_components"
    )]
  } else {
    fixture_unmatched[, `:=`(
      nearest_pipeline_id = character(),
      failed_components = character()
    )]
  }
  fixture_unmatched <- annotate_fixture_residuals(
    fixture_unmatched,
    pipeline_all
  )
  pipeline_only <- data.table::copy(pipeline[!pipeline_used])
  pipeline_only[, probable_cause := pipeline_only_cause(
    pipeline_only,
    fixture_cutoff = fixture_cutoff
  )]
  if (nrow(invalid_pipeline)) {
    invalid_pipeline[, probable_cause := "invalid_key"]
    pipeline_only <- data.table::rbindlist(
      list(pipeline_only, invalid_pipeline),
      use.names = TRUE,
      fill = TRUE
    )
  }
  normalized_columns <- c(
    ".fixture_row_id", ".pipeline_row_id", ".norm_name",
    ".norm_recipient", ".norm_date", ".norm_amount", ".valid_key"
  )
  fixture_unmatched[, (intersect(normalized_columns, names(fixture_unmatched))) := NULL]
  pipeline_only[, (intersect(normalized_columns, names(pipeline_only))) := NULL]

  fixture_duplicates <- count_duplicate_tuples(
    fixture,
    c(".norm_name", ".norm_date", ".norm_amount", ".norm_recipient")
  )
  pipeline_duplicates <- count_duplicate_tuples(
    pipeline,
    c(".norm_name", ".norm_date", ".norm_amount", ".norm_recipient")
  )
  present_residuals <- sum(
    fixture_unmatched$residual_status == "present_in_database",
    na.rm = TRUE
  )
  covered_fixture <- nrow(match_table) + present_residuals
  list(
    matches = match_table,
    stage_counts = stage_counts,
    total_fixture = nrow(fixture),
    total_pipeline = nrow(pipeline_all),
    total_matches = nrow(match_table),
    match_percent = if (nrow(fixture)) 100 * nrow(match_table) / nrow(fixture) else NA_real_,
    present_residuals = present_residuals,
    covered_fixture = covered_fixture,
    coverage_percent = if (nrow(fixture)) {
      100 * covered_fixture / nrow(fixture)
    } else {
      NA_real_
    },
    fixture_unmatched = fixture_unmatched,
    pipeline_only = pipeline_only,
    duplicate_counts = list(
      fixture_duplicate_tuples = fixture_duplicates$tuples,
      fixture_duplicate_rows = fixture_duplicates$rows,
      pipeline_duplicate_tuples = pipeline_duplicates$tuples,
      pipeline_duplicate_rows = pipeline_duplicates$rows
    ),
    invalid_key_rows = nrow(invalid_pipeline),
    empty_pipeline = nrow(pipeline_all) == 0L
  )
}

markdown_count_table <- function(data, columns = names(data)) {
  if (!nrow(data)) {
    return("_None._")
  }
  header <- paste0("| ", paste(columns, collapse = " | "), " |")
  separator <- paste0("| ", paste(rep("---", length(columns)), collapse = " | "), " |")
  rows <- apply(
    as.data.frame(data[, ..columns], stringsAsFactors = FALSE),
    1L,
    function(row) {
      row[is.na(row)] <- ""
      row <- gsub("\\|", "\\\\|", as.character(row), perl = TRUE)
      paste0("| ", paste(row, collapse = " | "), " |")
    }
  )
  c(header, separator, rows)
}

accuracy_report_lines <- function(result) {
  stage <- result$stage_counts
  percent <- if (is.na(result$match_percent)) "n/a" else sprintf("%.2f%%", result$match_percent)
  coverage_percent <- if (is.na(result$coverage_percent)) {
    "n/a"
  } else {
    sprintf("%.2f%%", result$coverage_percent)
  }
  duplicate <- result$duplicate_counts
  cause_counts <- if (nrow(result$pipeline_only)) {
    result$pipeline_only[, .(rows = .N), by = probable_cause][order(probable_cause)]
  } else {
    data.table::data.table(probable_cause = character(), rows = integer())
  }
  residual <- data.table::as.data.table(data.table::copy(result$fixture_unmatched))
  residual_counts <- if (nrow(residual) && "residual_status" %in% names(residual)) {
    residual[, .(rows = .N), by = residual_status][order(residual_status)]
  } else {
    data.table::data.table(residual_status = character(), rows = integer())
  }
  residual_table <- if (nrow(residual)) {
    name_column <- first_existing_column(
      residual,
      c("contributor_name", "name", "Name")
    )
    recipient_column <- first_existing_column(
      residual,
      c("recipient_name", "committee_name", "recipient")
    )
    date_column <- first_existing_column(
      residual,
      c("receipt_date", "contribution_receipt_date", "date")
    )
    amount_column <- first_existing_column(
      residual,
      c("amount", "contribution_receipt_amount")
    )
    residual[, .(
      fixture_id,
      contributor = get(name_column),
      recipient = get(recipient_column),
      receipt_date = as.character(get(date_column)),
      amount = get(amount_column),
      status = residual_status,
      failed_components = database_failed_components
    )]
  } else {
    data.table::data.table()
  }
  lines <- c(
    "# Mentat Crypto Accuracy Report",
    "",
    if (isTRUE(result$empty_pipeline)) {
      "Pipeline contains no contribution rows; reconciliation completed with 0 matches."
    } else {
      "Reconciliation completed against the ingested Crypto fixture."
    },
    "",
    "## Match stages",
    "",
    paste0("- S1 exact: ", stage[stage == "S1", matches]),
    paste0("- S2 recipient prefix-25: ", stage[stage == "S2", matches]),
    paste0("- S3 name/date/amount: ", stage[stage == "S3", matches]),
    paste0("- S4 name/amount within +/-3 days: ", stage[stage == "S4", matches]),
    paste0("- Total matched: ", result$total_matches, " / ", result$total_fixture),
    paste0("- Strict one-to-one match rate: ", percent),
    paste0("- Present residual rows: ", result$present_residuals),
    paste0(
      "- Database coverage: ", result$covered_fixture, " / ", result$total_fixture,
      " (", coverage_percent, ")"
    ),
    paste0("- Invalid pipeline key rows excluded from matching: ", result$invalid_key_rows),
    "",
    "## Unmatched fixture rows",
    "",
    paste0(
      result$total_fixture - result$total_matches,
      " fixture rows remain unmatched. See `fixture_unmatched.csv` for nearest-miss diagnostics."
    ),
    "",
    markdown_count_table(residual_counts),
    "",
    "### Named residual rows",
    "",
    markdown_count_table(residual_table),
    "",
    "## Pipeline-only rows by probable cause",
    "",
    markdown_count_table(cause_counts),
    "",
    "See `pipeline_only.csv` for the row-level detail.",
    "",
    "## Duplicate normalized tuples",
    "",
    paste0("- Fixture duplicate tuples: ", duplicate$fixture_duplicate_tuples),
    paste0("- Fixture rows in duplicate tuples: ", duplicate$fixture_duplicate_rows),
    paste0("- Pipeline duplicate tuples: ", duplicate$pipeline_duplicate_tuples),
    paste0("- Pipeline rows in duplicate tuples: ", duplicate$pipeline_duplicate_rows)
  )
  lines
}

write_accuracy_outputs <- function(result, data_root = mentat_default_data_root()) {
  paths <- mentat_bootstrap_dirs(data_root)
  report_path <- file.path(paths$reports, "accuracy_report.md")
  fixture_path <- file.path(paths$reports, "fixture_unmatched.csv")
  pipeline_path <- file.path(paths$reports, "pipeline_only.csv")
  atomic_write_lines(accuracy_report_lines(result), report_path)
  atomic_write_csv(result$fixture_unmatched, fixture_path)
  pipeline_only <- data.table::copy(result$pipeline_only)
  if ("company" %in% names(pipeline_only)) {
    pipeline_only[, company := mentat_company_display_name(company)]
  }
  atomic_write_csv(pipeline_only, pipeline_path)
  invisible(list(
    accuracy_report = report_path,
    fixture_unmatched = fixture_path,
    pipeline_only = pipeline_path
  ))
}

reconcile_fixture <- function(
  connection,
  data_root = mentat_default_data_root(),
  write_outputs = TRUE
) {
  require_package("data.table")
  apply_amendment_versioning(connection, print_summary = FALSE)
  fixture <- data.table::as.data.table(DBI::dbReadTable(connection, "fixture_crypto"))
  pipeline <- data.table::as.data.table(DBI::dbGetQuery(
    connection,
    paste(
      "SELECT * FROM contributions",
      "WHERE industry = 'Crypto'",
      "AND company_match_status = 'accepted'",
      "AND (excluded_reason IS NULL OR excluded_reason = '')",
      "AND duplicate_memo = 0"
    )
  ))
  result <- reconcile_tables(fixture, pipeline)
  all_database_rows <- data.table::as.data.table(DBI::dbGetQuery(
    connection,
    "SELECT * FROM contributions WHERE industry = 'Crypto'"
  ))
  result$fixture_unmatched <- annotate_fixture_residuals(
    result$fixture_unmatched,
    all_database_rows
  )
  result$present_residuals <- sum(
    result$fixture_unmatched$residual_status == "present_in_database",
    na.rm = TRUE
  )
  result$covered_fixture <- result$total_matches + result$present_residuals
  result$coverage_percent <- if (result$total_fixture) {
    100 * result$covered_fixture / result$total_fixture
  } else {
    NA_real_
  }
  if (isTRUE(write_outputs)) {
    result$output_paths <- write_accuracy_outputs(result, data_root)
  }
  result
}

# Recover one oversized employer query with complete, audited date partitions.
#
# Usage:
#   Rscript collect_employer_date_partition.R DATABASE INDUSTRY COMPANY
#     CYCLE PHASE VARIANT|VARIANT

options(stringsAsFactors = FALSE)

arguments <- commandArgs(trailingOnly = TRUE)
if (length(arguments) != 6L) {
  stop(
    paste(
      "usage: collect_employer_date_partition.R",
      "DATABASE INDUSTRY COMPANY CYCLE PHASE VARIANT|VARIANT"
    ),
    call. = FALSE
  )
}

database_path <- normalizePath(
  arguments[[1L]],
  winslash = "/",
  mustWork = TRUE
)
industry <- trimws(arguments[[2L]])
company <- trimws(arguments[[3L]])
cycle <- suppressWarnings(as.integer(arguments[[4L]]))
phase <- trimws(arguments[[5L]])
variants <- unique(trimws(strsplit(
  arguments[[6L]],
  "|",
  fixed = TRUE
)[[1L]]))
variants <- variants[nzchar(variants)]
if (
  !nzchar(industry) ||
    !nzchar(company) ||
    is.na(cycle) ||
    !phase %in% c("P1_employer", "P2_variant") ||
    !length(variants)
) {
  stop("Invalid employer date-partition arguments.", call. = FALSE)
}

script_path <- normalizePath(
  sub("^--file=", "", grep(
    "^--file=",
    commandArgs(trailingOnly = FALSE),
    value = TRUE
  )[[1L]]),
  winslash = "/",
  mustWork = TRUE
)
project_root <- dirname(dirname(script_path))
assign("MENTAT_PROJECT_ROOT", project_root, envir = .GlobalEnv)
assign("MENTAT_INDUSTRY", industry, envir = .GlobalEnv)
for (module in c(
  "paths.R", "schema.R", "ingest.R", "fec_page.R", "collect.R",
  "fec_api.R", "reconcile.R", "report.R"
)) {
  source(file.path(project_root, "R", module), local = .GlobalEnv)
}

require_package("DBI")
require_package("RSQLite")
require_package("data.table")
connection <- DBI::dbConnect(RSQLite::SQLite(), database_path)
on.exit(DBI::dbDisconnect(connection), add = TRUE)
DBI::dbExecute(connection, "PRAGMA busy_timeout=30000")
init_schema(connection)

roster <- data.table::as.data.table(DBI::dbGetQuery(
  connection,
  paste(
    "SELECT variant, exclusions FROM companies",
    "WHERE industry = ? AND company = ? ORDER BY variant_order"
  ),
  params = list(industry, company)
))
if (!nrow(roster)) stop("Company roster is missing.", call. = FALSE)
company_variants <- roster$variant
exclusions <- unique(unlist(strsplit(
  paste(na.omit(roster$exclusions), collapse = "|"),
  "|",
  fixed = TRUE
)))
exclusions <- exclusions[nzchar(exclusions)]
trusted_name_keys <- company_cross_reference_people(connection, company)
match_overrides <- company_match_overrides_data(connection)
home_states <- company_home_states_data(connection)

raw_session <- new_fec_session()
session <- new_fec_session_state(raw_session)
on.exit(try(fec_active_session(session)$close(), silent = TRUE), add = TRUE)
broad_variant_set <- paste0(
  phase,
  ":employer:",
  paste(variants, collapse = " || ")
)
broad_url <- fec_build_url(
  cycle,
  variants,
  query_field = "contributor_employer"
)
fec_navigate(fec_active_session(session), broad_url)
broad_total <- as.integer(
  fec_read_page(fec_active_session(session))$info$recordsTotal
)
if (is.na(broad_total) || broad_total < 0L) {
  stop("Broad employer query returned an invalid total.", call. = FALSE)
}
cat(
  "Broad employer total: ",
  company,
  " ",
  cycle,
  " ",
  broad_variant_set,
  " = ",
  broad_total,
  "\n",
  sep = ""
)

partitions <- data.frame(
  label = c("pre_cycle", "cycle_year_1", "cycle_year_2", "post_cycle"),
  min_date = c(
    "01/01/1900",
    sprintf("01/01/%d", cycle - 1L),
    sprintf("01/01/%d", cycle),
    sprintf("01/01/%d", cycle + 1L)
  ),
  max_date = c(
    sprintf("12/31/%d", cycle - 2L),
    sprintf("12/31/%d", cycle - 1L),
    sprintf("12/31/%d", cycle),
    "12/31/2100"
  ),
  stringsAsFactors = FALSE
)
seen_sub_ids <- new.env(hash = TRUE, parent = emptyenv())
for (partition_index in seq_len(nrow(partitions))) {
  partition <- partitions[partition_index, , drop = FALSE]
  partition_variant_set <- paste0(
    broad_variant_set,
    ":date:",
    partition$label
  )
  result <- fec_walk_variant_set(
    session = session,
    company = company,
    cycle = cycle,
    variants = variants,
    variant_set = partition_variant_set,
    query_field = "contributor_employer",
    query_filters = list(
      min_date = partition$min_date,
      max_date = partition$max_date
    ),
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
      tabular <- is.data.frame(rows)
      sub_ids <- if (tabular) {
        trim_text(rows$sub_id)
      } else {
        vapply(
          rows,
          function(row) trim_text(row$sub_id %||% ""),
          character(1L)
        )
      }
      for (sub_id in sub_ids[nzchar(sub_ids)]) {
        assign(sub_id, TRUE, envir = seen_sub_ids)
      }
      records <- flatten_fec_rows(
        rows,
        industry = industry,
        company = company,
        variants = company_variants,
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
        partition_variant_set,
        page,
        records_total
      )
    }
  )
  cat(
    "Partition ",
    partition$label,
    ": ",
    result$records_total,
    " row(s).\n",
    sep = ""
  )
}

observed_total <- length(ls(seen_sub_ids, all.names = TRUE))
if (!identical(observed_total, broad_total)) {
  stop(
    "Date partitions did not reconcile to broad employer total: expected=",
    broad_total,
    " observed_unique_sub_ids=",
    observed_total,
    call. = FALSE
  )
}
final_page <- max(0L, as.integer(ceiling(broad_total / 100L)) - 1L)
DBI::dbExecute(
  connection,
  paste(
    "INSERT INTO scrape_progress",
    "(company, cycle, variant_set, last_page, records_total, updated_at,",
    "zero_verified)",
    "VALUES (?, ?, ?, ?, ?, ?, 1)",
    "ON CONFLICT(company, cycle, variant_set) DO UPDATE SET",
    "last_page=excluded.last_page, records_total=excluded.records_total,",
    "updated_at=excluded.updated_at,",
    "zero_verified=MAX(scrape_progress.zero_verified, excluded.zero_verified)"
  ),
  params = list(
    company,
    cycle,
    broad_variant_set,
    final_page,
    broad_total,
    format(Sys.time(), "%Y-%m-%dT%H:%M:%OS3%z")
  )
)
quick_check <- DBI::dbGetQuery(
  connection,
  "PRAGMA quick_check(1)"
)[[1L]][[1L]]
if (!identical(quick_check, "ok")) {
  stop("Date-partition database quick_check failed: ", quick_check, call. = FALSE)
}
cat(
  "Date-partition employer recovery complete: expected=",
  broad_total,
  " observed=",
  observed_total,
  " quick_check=",
  quick_check,
  "\n",
  sep = ""
)

# Re-evaluate saved employer questions with the same matcher as collection.
# The source database is opened read-only; decisions are derived run evidence.
.recheck_script <- if (sys.nframe() > 0L) {
  sys.frame(1L)$ofile
} else {
  sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[[1L]])
}
.recheck_root <- normalizePath(file.path(dirname(.recheck_script), ".."), winslash = "/")
for (.recheck_module in c("paths.R", "schema.R", "ingest.R", "collect.R")) {
  source(file.path(.recheck_root, "R", .recheck_module))
}

recheck_attribution <- function(database, input, output) {
  require_package("jsonlite")
  require_package("DBI")
  require_package("RSQLite")
  if (!file.exists(database)) stop("Attribution source database is missing.", call. = FALSE)
  rows <- jsonlite::read_json(input, simplifyVector = FALSE)
  if (!is.list(rows) || !is.null(names(rows))) {
    stop("Attribution input must be a JSON array.", call. = FALSE)
  }
  indexes <- vapply(rows, function(row) {
    index <- row$request_index
    if (!is.numeric(index) || length(index) != 1L || is.na(index) ||
        !is.finite(index) || index != floor(index) || index < 0) {
      stop("Each attribution input requires a nonnegative request_index.", call. = FALSE)
    }
    as.numeric(index)
  }, numeric(1L))
  if (anyDuplicated(indexes)) stop("Duplicate attribution request_index.", call. = FALSE)
  field <- function(name, required = FALSE) vapply(rows, function(row) {
    value <- row[[name]] %||% ""
    if (!is.character(value) || length(value) != 1L || is.na(value) ||
        (required && !nzchar(trimws(value)))) {
      stop("Invalid attribution input field: ", name, call. = FALSE)
    }
    value
  }, character(1L))
  industry <- field("industry", TRUE)
  company <- field("company", TRUE)
  employer <- field("contributor_employer")
  contributor <- field("contributor_name")
  state <- field("contributor_state")
  occupation <- field("contributor_occupation")
  excluded <- field("excluded_reason")
  connection <- DBI::dbConnect(RSQLite::SQLite(), dbname = database, flags = RSQLite::SQLITE_RO)
  on.exit(DBI::dbDisconnect(connection), add = TRUE)
  if (!DBI::dbExistsTable(connection, "companies") ||
      !all(c("industry", "company", "variant") %in% DBI::dbListFields(connection, "companies"))) {
    stop("Attribution source has no company alias register.", call. = FALSE)
  }
  overrides <- company_match_overrides_data(connection)
  home_states <- company_home_states_data(connection)
  result <- vector("list", length(rows))
  groups <- split(seq_along(rows), paste(industry, company, sep = "\034"))
  for (group in groups) {
    first <- group[[1L]]
    variants <- DBI::dbGetQuery(connection,
      "SELECT variant FROM companies WHERE industry = ? AND company = ? ORDER BY variant",
      params = list(industry[[first]], company[[first]])
    )$variant
    matches <- if (!length(variants)) {
      lapply(group, function(index) list(status = "review", reason = "company_alias_register_missing"))
    } else {
      company_employer_match_page(
        company = company[[first]], employer = employer[group],
        contributor_name = contributor[group],
        variants = unique(c(company[[first]], variants)), overrides = overrides,
        excluded_reason = excluded[group], contributor_state = state[group],
        contributor_occupation = occupation[group], home_states = home_states
      )
    }
    for (offset in seq_along(group)) {
      index <- group[[offset]]
      match <- matches[[offset]]
      alias_evidence <- if (identical(match$reason, "verified_employer_alias")) {
        verified_employer_alias_evidence(company[[index]], employer[[index]])
      } else NULL
      result[[index]] <- c(
        list(request_index = indexes[[index]]), match,
        if (is.null(alias_evidence)) list() else list(alias_evidence = alias_evidence)
      )
    }
  }
  jsonlite::write_json(result, output, auto_unbox = TRUE, pretty = TRUE, null = "null")
  invisible(result)
}

if (sys.nframe() == 0L) {
  .recheck_args <- commandArgs(TRUE)
  if (length(.recheck_args) != 6L ||
      !identical(.recheck_args[c(1L, 3L, 5L)], c("--database", "--input", "--output"))) {
    stop("Usage: Rscript recheck_attribution.R --database DB --input JSON --output JSON", call. = FALSE)
  }
  recheck_attribution(.recheck_args[[2L]], .recheck_args[[4L]], .recheck_args[[6L]])
}

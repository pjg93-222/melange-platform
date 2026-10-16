if (!exists("MENTAT_INDUSTRY")) MENTAT_INDUSTRY <- "Crypto"

# Reporting is scoped to ONE industry per workbook. This filter used to be the
# literal industry='Crypto' in eight queries, so a workbook always rendered the
# crypto roster no matter which industry had actually been collected - collect.R
# and fec_api.R honour MENTAT_INDUSTRY, but the report builder did not. Reusing
# the same variable here makes the builder industry-aware with no call-site
# changes, and the default keeps existing crypto behaviour byte-identical.
report_industry_filter <- function(column = "industry") {
  paste0(column, "='", gsub("'", "''", MENTAT_INDUSTRY, fixed = TRUE), "'")
}

spending_report_company_labels <- function(x) {
  mentat_company_display_name(x)
}

spending_stats_category_groups <- function() {
  list(
    "Super PAC" = "Super PAC",
    "Carey" = "Carey",
    # ActBlue and WinRed (spending_recipient_classification). Its own column
    # group directly after Carey, where the same two committees used to land.
    "Conduit" = "Conduit",
    "Company PAC" = c("Company PAC", "Leadership PAC"),
    "Trade Association" = "Trade Association",
    "Candidate" = c(
      "Candidate", "Party Committee", "JFC", "Unclassified"
    )
  )
}

spending_supplementary_categories <- function() {
  c("527", "501c")
}

# The recipient categories a Company PAC's disbursements are split across, in
# column order. Every one of them has its own share column, so a disbursement
# to a conduit is reported as a conduit disbursement rather than falling out of
# the allocation (it used to be counted under Carey).
spending_stats_allocation_categories <- function() {
  c("Super PAC", "Candidate", "Trade Association", "Carey", "Conduit")
}

# The Conduit column groups are inserted directly after each Carey group, so
# the header vector below stays the single description of the sheet's layout.
spending_stats_metric_count <- function() {
  length(spending_stats_headers(2026L)) - 1L
}

spending_stats_headers <- function(year) {
  role_headers <- function(role) {
    c(
      paste(role, "Spending"),
      "% of Company Spending",
      "Super PAC", "% of Total Giving", "Partisan Split",
      "Carey", "% of Total Giving", "Partisan Split",
      "Conduit", "% of Total Giving", "Partisan Split",
      "Company PAC", "% of Total Giving", "Partisan Split",
      "Trade Association", "% of Total Giving", "Partisan Split",
      "Candidate", "% of Total Giving", "Partisan Split"
    )
  }
  c(
    paste("Company", year),
    "Total Giving", "% of Total Industry Spending", "Partisan Split",
    "Corporate Giving", "% of Total Giving", "Partisan Split",
    "Super PAC", "% of Total Giving", "Partisan Split",
    "Carey", "% of Total Giving", "Partisan Split",
    "Conduit", "% of Total Giving", "Partisan Split",
    "Company PAC", "% of Total Giving", "Partisan Split",
    "Trade Association", "% of Total Giving", "Partisan Split",
    "Candidate", "% of Total Giving", "Partisan Split",
    role_headers("Chief"),
    role_headers("Middle Management"),
    role_headers("Employee"),
    "Company PAC Cash on Hand", "% Left",
    "Company PAC: To Super PAC", "% to Super PAC", "Partisan Split",
    "Company PAC: Direct to Candidate", "% to Candidate", "Partisan Split",
    "Company PAC: Trade Association", "% to Trade Association",
    "Company PAC: Carey", "% to Carey", "Partisan Split",
    "Company PAC: Conduit", "% to Conduit", "Partisan Split"
  )
}

spending_stats_companies <- function(companies = NULL) {
  crypto_companies <- c(
    "Coinbase", "Binance", "Gemini", "Bakkt", "Kraken",
    "Crypto.com", "MIAXdx", "CoinList", "tZero", "Bitnomial"
  )
  roster <- unique(spending_report_company_labels(as.character(companies)))
  roster <- roster[!is.na(roster) & nzchar(roster)]
  if (identical(MENTAT_INDUSTRY, "Crypto") || !length(companies)) {
    unexpected <- setdiff(roster, crypto_companies)
    if (length(unexpected)) {
      stop(
        "Crypto Stats grid would omit roster companies: ",
        paste(unexpected, collapse = ", "),
        call. = FALSE
      )
    }
    return(crypto_companies)
  }
  if (length(roster) > 10L) {
    stop(
      "Spending Report Stats template supports at most ten companies; ",
      length(roster), " are configured for ", MENTAT_INDUSTRY, ".",
      call. = FALSE
    )
  }
  c(roster, rep("", 10L - length(roster)))
}

spending_excel_column <- function(column) {
  column <- as.integer(column)
  if (is.na(column) || column < 1L) {
    stop("Excel column index must be positive.", call. = FALSE)
  }
  result <- character()
  while (column > 0L) {
    remainder <- (column - 1L) %% 26L
    result <- c(LETTERS[[remainder + 1L]], result)
    column <- (column - 1L) %/% 26L
  }
  paste0(result, collapse = "")
}

spending_excel_string <- function(x) {
  paste0("\"", gsub("\"", "\"\"", x, fixed = TRUE), "\"")
}

spending_fact_ranges <- function(last_row) {
  last_row <- max(2L, as.integer(last_row))
  range <- function(column) {
    paste0("'Sheet4'!$", column, "$2:$", column, "$", last_row)
  }
  list(
    record_type = range("A"),
    year = range("B"),
    company = range("C"),
    employee_tier = range("D"),
    recipient_category = range("E"),
    party = range("F"),
    amount = range("G")
  )
}

spending_sumifs <- function(
  ranges,
  year,
  company_ref = NULL,
  record_type = "CONTRIBUTION",
  employee_tier = NULL,
  recipient_category = NULL,
  party = NULL
) {
  if (length(record_type) > 1L) {
    return(paste0(
      "(",
      paste(vapply(record_type, function(type) {
        spending_sumifs(
          ranges, year, company_ref, type, employee_tier,
          recipient_category, party
        )
      }, character(1L)), collapse = "+"),
      ")"
    ))
  }
  if (length(recipient_category) > 1L) {
    return(paste0(
      "(",
      paste(vapply(recipient_category, function(category) {
        spending_sumifs(
          ranges, year, company_ref, record_type, employee_tier,
          category, party
        )
      }, character(1L)), collapse = "+"),
      ")"
    ))
  }
  criteria <- c(
    ranges$record_type, spending_excel_string(record_type),
    ranges$year, as.character(as.integer(year))
  )
  if (!is.null(company_ref)) {
    criteria <- c(criteria, ranges$company, company_ref)
  }
  if (!is.null(employee_tier)) {
    criteria <- c(
      criteria,
      ranges$employee_tier,
      spending_excel_string(employee_tier)
    )
  }
  if (!is.null(recipient_category)) {
    criteria <- c(
      criteria,
      ranges$recipient_category,
      spending_excel_string(recipient_category)
    )
  }
  if (!is.null(party)) {
    criteria <- c(criteria, ranges$party, spending_excel_string(party))
  }
  paste0("SUMIFS(", paste(c(ranges$amount, criteria), collapse = ","), ")")
}

spending_countifs <- function(
  ranges,
  year,
  company_ref = NULL,
  record_type = "CONTRIBUTION",
  employee_tier = NULL,
  recipient_category = NULL,
  party = NULL
) {
  criteria <- c(
    ranges$record_type, spending_excel_string(record_type),
    ranges$year, as.character(as.integer(year))
  )
  if (!is.null(company_ref)) {
    criteria <- c(criteria, ranges$company, company_ref)
  }
  if (!is.null(employee_tier)) {
    criteria <- c(
      criteria,
      ranges$employee_tier,
      spending_excel_string(employee_tier)
    )
  }
  if (!is.null(recipient_category)) {
    criteria <- c(
      criteria,
      ranges$recipient_category,
      spending_excel_string(recipient_category)
    )
  }
  if (!is.null(party)) {
    criteria <- c(criteria, ranges$party, spending_excel_string(party))
  }
  paste0("COUNTIFS(", paste(criteria, collapse = ","), ")")
}

spending_partisan_expression <- function(
  ranges,
  year,
  company_ref,
  record_type = c("CONTRIBUTION", "CORPORATE"),
  employee_tier = NULL,
  recipient_category = NULL
) {
  republican <- spending_sumifs(
    ranges,
    year,
    company_ref,
    record_type,
    employee_tier,
    recipient_category,
    "R"
  )
  democratic <- spending_sumifs(
    ranges,
    year,
    company_ref,
    record_type,
    employee_tier,
    recipient_category,
    "D"
  )
  paste0(
    "IFERROR(", republican, "/(", republican, "+", democratic, "),\"\")"
  )
}

spending_stats_company_formulas <- function(year, company_ref, last_fact_row) {
  ranges <- spending_fact_ranges(last_fact_row)
  giving_types <- c("CONTRIBUTION", "CORPORATE")
  total <- spending_sumifs(ranges, year, company_ref, record_type = giving_types)
  corporate_total <- spending_sumifs(
    ranges,
    year,
    company_ref,
    record_type = "CORPORATE"
  )
  person_total <- spending_sumifs(
    ranges,
    year,
    company_ref,
    record_type = "CONTRIBUTION"
  )
  industry_total <- spending_sumifs(
    ranges,
    year,
    company_ref = NULL,
    record_type = giving_types
  )
  formulas <- c(
    paste0("=", total),
    paste0("=IFERROR(", total, "/", industry_total, ",0)"),
    paste0("=", spending_partisan_expression(ranges, year, company_ref)),
    paste0("=", corporate_total),
    paste0("=IFERROR(", corporate_total, "/", total, ",0)"),
    paste0(
      "=",
      spending_partisan_expression(
        ranges,
        year,
        company_ref,
        record_type = "CORPORATE"
      )
    )
  )

  category_groups <- spending_stats_category_groups()
  categories <- names(category_groups)
  for (category in categories) {
    category_values <- category_groups[[category]]
    category_total <- spending_sumifs(
      ranges,
      year,
      company_ref,
      record_type = giving_types,
      recipient_category = category_values
    )
    formulas <- c(
      formulas,
      paste0("=", category_total),
      paste0("=IFERROR(", category_total, "/", total, ",0)"),
      paste0(
        "=",
        spending_partisan_expression(
          ranges,
          year,
          company_ref,
          record_type = giving_types,
          recipient_category = category_values
        )
      )
    )
  }

  for (role in c("Chief", "Middle Management", "Employee")) {
    role_total <- spending_sumifs(
      ranges,
      year,
      company_ref,
      employee_tier = role
    )
    formulas <- c(
      formulas,
      paste0("=", role_total),
      paste0("=IFERROR(", role_total, "/", person_total, ",0)")
    )
    for (category in categories) {
      category_values <- category_groups[[category]]
      category_total <- spending_sumifs(
        ranges,
        year,
        company_ref,
        employee_tier = role,
        recipient_category = category_values
      )
      formulas <- c(
        formulas,
        paste0("=", category_total),
        paste0("=IFERROR(", category_total, "/", role_total, ",0)"),
        paste0(
          "=",
          spending_partisan_expression(
            ranges,
            year,
            company_ref,
            employee_tier = role,
            recipient_category = category_values
          )
        )
      )
    }
  }

  cash_count <- spending_countifs(
    ranges,
    year,
    company_ref,
    record_type = "PAC_CASH"
  )
  cash <- spending_sumifs(
    ranges,
    year,
    company_ref,
    record_type = "PAC_CASH"
  )
  receipts <- spending_sumifs(
    ranges,
    year,
    company_ref,
    record_type = "PAC_RECEIPTS"
  )
  disbursement_count <- spending_countifs(
    ranges,
    year,
    company_ref,
    record_type = "PAC_DISBURSEMENT"
  )
  total_disbursements <- spending_sumifs(
    ranges,
    year,
    company_ref,
    record_type = "PAC_DISBURSEMENT"
  )
  formulas <- c(
    formulas,
    paste0("=IF(", cash_count, "=0,\"\",", cash, ")"),
    paste0(
      "=IF(OR(", cash_count, "=0,", receipts, "=0),\"\",",
      cash, "/", receipts, ")"
    )
  )
  for (category in spending_stats_allocation_categories()) {
    category_total <- spending_sumifs(
      ranges,
      year,
      company_ref,
      record_type = "PAC_DISBURSEMENT",
      recipient_category = category
    )
    formulas <- c(
      formulas,
      paste0(
        "=IF(", disbursement_count, "=0,\"\",", category_total, ")"
      ),
      paste0(
        "=IF(", total_disbursements, "=0,\"\",",
        category_total, "/", total_disbursements, ")"
      )
    )
    if (!identical(category, "Trade Association")) {
      formulas <- c(
        formulas,
        paste0(
          "=",
          spending_partisan_expression(
            ranges,
            year,
            company_ref,
            record_type = "PAC_DISBURSEMENT",
            recipient_category = category
          )
        )
      )
    }
  }
  if (length(formulas) != spending_stats_metric_count()) {
    stop(
      "Spending Report formula contract drifted: expected ",
      spending_stats_metric_count(), " metrics, got ",
      length(formulas),
      call. = FALSE
    )
  }
  formulas
}

spending_stats_sheet <- function(last_fact_row = 2L, memo_duplicate_count = 0L,
                                 companies = NULL) {
  require_package("writexl")
  # Column B carries the ten company labels that every Stats formula matches
  # facts against ($B<row>), so they must be the CURRENT industry's companies -
  # a crypto-labelled grid laid over telecom facts resolves nothing and computes
  # zeros across every metric. Crypto keeps its curated template order (the
  # ordering came from the source Spending Report workbook, not from the data),
  # so existing crypto output stays byte-identical; other industries have no
  # curated order and fall back to the roster's alphabetical order.
  template_companies <- spending_stats_companies(companies)
  metric_count <- spending_stats_metric_count()
  values <- matrix("", nrow = 84L, ncol = metric_count + 2L)
  values[1L, 2L] <- paste0(
    "R8 memo-X dedup rule: exclude a memo-X row only when a non-memo row has ",
    "the same company, committee_id, date, amount, and contributor_name; flagged ",
    as.integer(memo_duplicate_count), " row(s)."
  )
  formulas <- matrix(NA_character_, nrow = 84L, ncol = metric_count)
  for (index in seq_along(2026:2021)) {
    year <- (2026:2021)[[index]]
    header_row <- 2L + (index - 1L) * 14L
    values[header_row, 2L] <- spending_stats_headers(year)[[1L]]
    formulas[header_row, ] <- paste0(
      "=",
      vapply(
        spending_stats_headers(year)[-1L],
        spending_excel_string,
        character(1L)
      )
    )
    values[(header_row + 1L):(header_row + 10L), 2L] <- template_companies
    values[header_row + 11L, 2L] <- "Average"
    values[header_row + 12L, 2L] <- "Change from last year"
    for (row in (header_row + 1L):(header_row + 10L)) {
      formulas[row, ] <- spending_stats_company_formulas(
        year,
        paste0("$B", row),
        last_fact_row
      )
    }
    for (column_index in seq_len(ncol(formulas))) {
      column <- column_index + 2L
      column_name <- spending_excel_column(column)
      company_range <- paste0(
        column_name, header_row + 1L, ":",
        column_name, header_row + 10L
      )
      formulas[header_row + 11L, column_index] <- paste0(
        "=IF(COUNT(", company_range, ")=0,\"\",AVERAGE(",
        company_range, "))"
      )
      if (year > 2021L) {
        prior_average_row <- header_row + 25L
        formulas[header_row + 12L, column_index] <- paste0(
          "=IF(OR(", column_name, header_row + 11L, "=\"\",",
          column_name, prior_average_row, "=\"\"),\"\",",
          column_name, header_row + 11L, "-",
          column_name, prior_average_row, ")"
        )
      }
    }
  }
  result <- as.data.frame(values, stringsAsFactors = FALSE, check.names = FALSE)
  for (column in 3:ncol(result)) {
    result[[column]] <- writexl::xl_formula(formulas[, column - 2L])
  }
  result
}

spending_stats_company_cached_values <- function(aggregate, year, company) {
  sum_metric <- function(
    record_type,
    employee_tier = NULL,
    recipient_category = NULL,
    party = NULL,
    company_value = company
  ) {
    keep <- aggregate$year == as.integer(year) &
      aggregate$record_type %in% record_type
    if (!is.null(company_value)) {
      keep <- keep & aggregate$company == company_value
    }
    if (!is.null(employee_tier)) {
      keep <- keep & aggregate$employee_tier %in% employee_tier
    }
    if (!is.null(recipient_category)) {
      keep <- keep & aggregate$recipient_category %in% recipient_category
    }
    if (!is.null(party)) keep <- keep & aggregate$party %in% party
    sum(aggregate$amount[keep], na.rm = TRUE)
  }
  count_metric <- function(record_type) {
    keep <- aggregate$year == as.integer(year) &
      aggregate$company == company &
      aggregate$record_type %in% record_type
    sum(aggregate$records[keep], na.rm = TRUE)
  }
  zero_ratio <- function(numerator, denominator) {
    if (isTRUE(denominator == 0)) 0 else numerator / denominator
  }
  blank_ratio <- function(numerator, denominator) {
    if (isTRUE(denominator == 0)) NA_real_ else numerator / denominator
  }
  partisan <- function(
    record_type,
    employee_tier = NULL,
    recipient_category = NULL
  ) {
    republican <- sum_metric(
      record_type, employee_tier, recipient_category, "R"
    )
    democratic <- sum_metric(
      record_type, employee_tier, recipient_category, "D"
    )
    blank_ratio(republican, republican + democratic)
  }

  giving_types <- c("CONTRIBUTION", "CORPORATE")
  total <- sum_metric(giving_types)
  corporate_total <- sum_metric("CORPORATE")
  person_total <- sum_metric("CONTRIBUTION")
  industry_total <- sum_metric(giving_types, company_value = NULL)
  values <- c(
    total,
    zero_ratio(total, industry_total),
    partisan(giving_types),
    corporate_total,
    zero_ratio(corporate_total, total),
    partisan("CORPORATE")
  )

  category_groups <- spending_stats_category_groups()
  for (category_values in category_groups) {
    category_total <- sum_metric(
      giving_types,
      recipient_category = category_values
    )
    values <- c(
      values,
      category_total,
      zero_ratio(category_total, total),
      partisan(giving_types, recipient_category = category_values)
    )
  }

  for (role in c("Chief", "Middle Management", "Employee")) {
    role_total <- sum_metric("CONTRIBUTION", employee_tier = role)
    values <- c(values, role_total, zero_ratio(role_total, person_total))
    for (category_values in category_groups) {
      category_total <- sum_metric(
        "CONTRIBUTION",
        employee_tier = role,
        recipient_category = category_values
      )
      values <- c(
        values,
        category_total,
        zero_ratio(category_total, role_total),
        partisan(
          "CONTRIBUTION",
          employee_tier = role,
          recipient_category = category_values
        )
      )
    }
  }

  cash_count <- count_metric("PAC_CASH")
  cash <- sum_metric("PAC_CASH")
  receipts <- sum_metric("PAC_RECEIPTS")
  disbursement_count <- count_metric("PAC_DISBURSEMENT")
  total_disbursements <- sum_metric("PAC_DISBURSEMENT")
  values <- c(
    values,
    if (cash_count == 0) NA_real_ else cash,
    if (cash_count == 0 || receipts == 0) NA_real_ else cash / receipts
  )
  for (category in spending_stats_allocation_categories()) {
    category_total <- sum_metric(
      "PAC_DISBURSEMENT",
      recipient_category = category
    )
    values <- c(
      values,
      if (disbursement_count == 0) NA_real_ else category_total,
      blank_ratio(category_total, total_disbursements)
    )
    if (!identical(category, "Trade Association")) {
      values <- c(
        values,
        partisan(
          "PAC_DISBURSEMENT",
          recipient_category = category
        )
      )
    }
  }
  if (length(values) != spending_stats_metric_count()) {
    stop(
      "Spending Report cached-value contract drifted: expected ",
      spending_stats_metric_count(), " metrics, got ",
      length(values),
      call. = FALSE
    )
  }
  as.numeric(values)
}

spending_stats_cached_values <- function(support, companies = NULL) {
  require_package("data.table")
  facts <- data.table::data.table(
    record_type = as.character(support[["Record Type"]]),
    year = as.integer(support[["Year"]]),
    company = as.character(support[["Company"]]),
    employee_tier = as.character(support[["Employee Tier"]]),
    recipient_category = as.character(support[["Recipient Category"]]),
    party = as.character(support[["Party"]]),
    amount = as.numeric(support[["Amount"]])
  )
  facts <- facts[
    record_type %in% c(
      "CONTRIBUTION", "CORPORATE", "PAC_CASH", "PAC_RECEIPTS",
      "PAC_DISBURSEMENT"
    ) & !is.na(year)
  ]
  aggregate <- facts[, .(
    amount = sum(amount, na.rm = TRUE),
    records = .N
  ), by = .(
    record_type, year, company, employee_tier,
    recipient_category, party
  )]
  template_companies <- spending_stats_companies(companies)
  years <- 2026:2021
  company_values <- lapply(years, function(year) {
    do.call(rbind, lapply(template_companies, function(company) {
      spending_stats_company_cached_values(aggregate, year, company)
    }))
  })
  names(company_values) <- as.character(years)
  averages <- lapply(company_values, function(values) {
    apply(values, 2L, function(column) {
      if (all(is.na(column))) NA_real_ else mean(column, na.rm = TRUE)
    })
  })

  cells <- character()
  values <- numeric()
  append_values <- function(row, row_values) {
    cells <<- c(
      cells,
      paste0(
        vapply(
          seq_len(spending_stats_metric_count()) + 2L,
          spending_excel_column,
          character(1L)
        ),
        as.integer(row)
      )
    )
    values <<- c(values, as.numeric(row_values))
  }
  for (index in seq_along(years)) {
    year <- years[[index]]
    header_row <- 2L + (index - 1L) * 14L
    matrix_values <- company_values[[as.character(year)]]
    for (company_index in seq_len(10L)) {
      append_values(header_row + company_index, matrix_values[company_index, ])
    }
    append_values(header_row + 11L, averages[[as.character(year)]])
    if (year > 2021L) {
      append_values(
        header_row + 12L,
        averages[[as.character(year)]] -
          averages[[as.character(year - 1L)]]
      )
    }
  }
  # Six years x (10 companies + Average) + five Change rows = 71 value rows.
  if (length(cells) != spending_stats_metric_count() * 71L ||
      anyDuplicated(cells)) {
    stop("Spending Report formula-cache cell map is incomplete.", call. = FALSE)
  }
  data.frame(cell = cells, value = values, stringsAsFactors = FALSE)
}

spending_xml_escape <- function(x) {
  x <- gsub("&", "&amp;", x, fixed = TRUE)
  x <- gsub("<", "&lt;", x, fixed = TRUE)
  x <- gsub(">", "&gt;", x, fixed = TRUE)
  x
}

spending_report_template_path <- function() {
  configured <- trimws(Sys.getenv("MENTAT_SPENDING_TEMPLATE", unset = ""))
  if (nzchar(configured)) {
    if (!file.exists(configured) || dir.exists(configured)) {
      stop(
        "MENTAT_SPENDING_TEMPLATE does not name an existing workbook: ",
        configured,
        call. = FALSE
      )
    }
    return(normalizePath(configured, winslash = "/", mustWork = TRUE))
  }
  project_root <- tryCatch(
    mentat_project_root(),
    error = function(condition) ""
  )
  candidates <- unique(c(
    if (nzchar(project_root)) {
      file.path(project_root, "inputs", "Spending Report template.xlsx")
    },
    file.path(getwd(), "mentat", "inputs", "Spending Report template.xlsx"),
    file.path(getwd(), "inputs", "Spending Report template.xlsx")
  ))
  matches <- candidates[file.exists(candidates) & !dir.exists(candidates)]
  if (!length(matches)) {
    stop(
      paste(
        "Could not locate Spending Report template.xlsx.",
        "Set MENTAT_SPENDING_TEMPLATE or place it in mentat/inputs."
      ),
      call. = FALSE
    )
  }
  normalizePath(matches[[1L]], winslash = "/", mustWork = TRUE)
}

spending_read_xml <- function(path) {
  readChar(path, nchars = file.info(path)$size, useBytes = TRUE)
}

spending_replace_xml_span <- function(xml, start, length, replacement) {
  finish <- start + length - 1L
  paste0(
    if (start > 1L) substr(xml, 1L, start - 1L) else "",
    replacement,
    if (finish < nchar(xml)) substr(xml, finish + 1L, nchar(xml)) else ""
  )
}

spending_xml_number <- function(x) {
  if (is.na(x)) return("")
  value <- sprintf("%.17g", as.numeric(x))
  if (identical(value, "-0")) "0" else value
}

spending_set_formula_caches <- function(xml, cached_values) {
  cached_values <- as.data.frame(cached_values, stringsAsFactors = FALSE)
  if (!identical(names(cached_values), c("cell", "value")) ||
      anyDuplicated(cached_values$cell)) {
    stop("Spending Report formula-cache map is invalid.", call. = FALSE)
  }
  matches <- gregexpr(
    "<c r=\"[A-Z]+[0-9]+\"[^>]*><f>[\\s\\S]*?</f><v>[^<]*</v></c>",
    xml,
    perl = TRUE
  )[[1L]]
  if (matches[[1L]] == -1L) {
    stop("Spending Report Stats formulas are missing.", call. = FALSE)
  }
  match_lengths <- attr(matches, "match.length")
  starts <- as.integer(matches)
  finishes <- starts + as.integer(match_lengths) - 1L
  blocks <- substring(xml, starts, finishes)
  cells <- sub(
    '^<c r="([A-Z]+[0-9]+)"[\\s\\S]*$',
    "\\1",
    blocks,
    perl = TRUE
  )
  cache_index <- match(cells, cached_values$cell)
  if (length(blocks) != nrow(cached_values) || anyNA(cache_index) ||
      any(!cached_values$cell %in% cells)) {
    stop(
      "Spending Report formula-cache map does not match generated formulas.",
      call. = FALSE
    )
  }
  updated <- vapply(seq_along(blocks), function(index) {
    block <- blocks[[index]]
    value <- cached_values$value[[cache_index[[index]]]]
    opening_match <- regexpr("^<c[^>]*>", block, perl = TRUE)
    opening <- regmatches(block, opening_match)
    opening <- gsub('\\s+t="[^"]*"', "", opening, perl = TRUE)
    if (is.na(value)) opening <- sub("<c", '<c t="str"', opening, fixed = TRUE)
    block <- spending_replace_xml_span(
      block,
      as.integer(opening_match[[1L]]),
      attr(opening_match, "match.length"),
      opening
    )
    sub(
      "<v>[^<]*</v>",
      paste0("<v>", spending_xml_number(value), "</v>"),
      block,
      perl = TRUE
    )
  }, character(1L), USE.NAMES = FALSE)
  pieces <- character(length(starts) * 2L + 1L)
  cursor <- 1L
  for (index in seq_along(starts)) {
    pieces[[index * 2L - 1L]] <- if (starts[[index]] > cursor) {
      substr(xml, cursor, starts[[index]] - 1L)
    } else {
      ""
    }
    pieces[[index * 2L]] <- updated[[index]]
    cursor <- finishes[[index]] + 1L
  }
  pieces[[length(pieces)]] <- if (cursor <= nchar(xml)) {
    substr(xml, cursor, nchar(xml))
  } else {
    ""
  }
  paste0(pieces, collapse = "")
}

spending_template_calc_id <- function(template) {
  staging <- tempfile("mentat-spending-template-calc-")
  if (!dir.create(staging, recursive = TRUE, showWarnings = FALSE)) {
    stop("Could not create template calculation staging directory", call. = FALSE)
  }
  on.exit(unlink(staging, recursive = TRUE, force = TRUE), add = TRUE)
  utils::unzip(template, files = "xl/workbook.xml", exdir = staging)
  template_workbook <- file.path(staging, "xl", "workbook.xml")
  if (!file.exists(template_workbook)) {
    stop("Spending Report template workbook XML is missing.", call. = FALSE)
  }
  xml <- spending_read_xml(template_workbook)
  match <- regexec('<calcPr[^>]*\\scalcId="([0-9]+)"', xml, perl = TRUE)
  values <- regmatches(xml, match)[[1L]]
  if (length(values) != 2L || !nzchar(values[[2L]])) {
    stop("Spending Report template calculation ID is missing.", call. = FALSE)
  }
  values[[2L]]
}

spending_set_calculation_properties <- function(staging, template) {
  workbook_path <- file.path(staging, "xl", "workbook.xml")
  if (!file.exists(workbook_path)) {
    stop("Spending Report workbook XML is missing.", call. = FALSE)
  }
  xml <- spending_read_xml(workbook_path)
  match <- regexpr("<calcPr[^>]*/>", xml, perl = TRUE)
  if (match[[1L]] == -1L) {
    stop("Spending Report calculation properties are missing.", call. = FALSE)
  }
  opening <- regmatches(xml, match)
  opening <- gsub(
    '\\s+(calcId|calcMode|fullCalcOnLoad|forceFullCalc)="[^"]*"',
    "",
    opening,
    perl = TRUE
  )
  calc_id <- spending_template_calc_id(template)
  opening <- sub(
    "/>$",
    paste0(
      ' calcId="', calc_id,
      '" calcMode="auto" fullCalcOnLoad="0" forceFullCalc="0"/>'
    ),
    opening,
    perl = TRUE
  )
  xml <- spending_replace_xml_span(
    xml,
    as.integer(match[[1L]]),
    attr(match, "match.length"),
    opening
  )
  writeLines(enc2utf8(xml), workbook_path, useBytes = TRUE)
  invisible(TRUE)
}

spending_xml_cols_block <- function(xml) {
  match <- regexpr(
    "<cols(?:\\s[^>]*)?(?:/>|>[\\s\\S]*?</cols>)",
    xml,
    perl = TRUE
  )
  if (match[[1L]] == -1L) "" else regmatches(xml, match)
}

# The template's Stats column widths predate the Conduit column groups. Each
# Conduit group (three columns, directly after a Carey group) takes the widths
# of the Carey group it follows, and every later template column moves right by
# the width of the groups inserted before it, so each width stays on the header
# it was set for. A column the template never sized stays unsized.
spending_stats_expand_template_cols <- function(template_cols) {
  if (!nzchar(template_cols)) return(template_cols)
  headers <- spending_stats_headers(2026L)[-1L]
  group_width <- 3L
  group_starts <- which(headers %in% c("Conduit", "Company PAC: Conduit")) + 2L
  blocks <- regmatches(
    template_cols,
    gregexpr("<col\\s[^>]*/>", template_cols, perl = TRUE)
  )[[1L]]
  attribute <- function(name) {
    as.integer(sub(
      paste0('^.*\\b', name, '="([0-9]+)".*$'),
      "\\1",
      blocks,
      perl = TRUE
    ))
  }
  template_min <- attribute("min")
  if (!length(blocks) || anyNA(template_min) ||
      !identical(template_min, attribute("max"))) {
    stop(
      "Spending Report template column widths must be one column per entry.",
      call. = FALSE
    )
  }
  template_column <- function(column) {
    if (any(column >= group_starts & column < group_starts + group_width)) {
      column <- column - group_width
    }
    column - group_width * sum(group_starts < column)
  }
  new_columns <- seq(
    min(template_min),
    max(template_min) + group_width * length(group_starts)
  )
  source <- match(vapply(new_columns, template_column, numeric(1L)), template_min)
  keep <- !is.na(source)
  expanded <- vapply(which(keep), function(index) {
    column <- new_columns[[index]]
    block <- blocks[[source[[index]]]]
    block <- sub('\\bmin="[0-9]+"', paste0('min="', column, '"'), block, perl = TRUE)
    sub('\\bmax="[0-9]+"', paste0('max="', column, '"'), block, perl = TRUE)
  }, character(1L))
  paste0("<cols>", paste0(expanded, collapse = ""), "</cols>")
}

spending_set_xml_cols_block <- function(xml, template_cols) {
  cols_match <- regexpr(
    "<cols(?:\\s[^>]*)?(?:/>|>[\\s\\S]*?</cols>)",
    xml,
    perl = TRUE
  )
  if (cols_match[[1L]] != -1L) {
    start <- as.integer(cols_match[[1L]])
    return(spending_replace_xml_span(
      xml,
      start,
      attr(cols_match, "match.length"),
      template_cols
    ))
  }
  if (!nzchar(template_cols)) {
    return(xml)
  }
  format_start <- regexpr("<sheetFormatPr", xml, fixed = TRUE)
  if (format_start[[1L]] == -1L) {
    stop(
      "Could not locate sheetFormatPr while restoring template columns",
      call. = FALSE
    )
  }
  format_start <- as.integer(format_start[[1L]])
  format_suffix <- substr(xml, format_start, nchar(xml))
  opening_end <- regexpr(">", format_suffix, fixed = TRUE)
  if (opening_end[[1L]] == -1L) {
    stop(
      "Could not locate the end of sheetFormatPr while restoring template columns",
      call. = FALSE
    )
  }
  opening_end <- format_start + as.integer(opening_end[[1L]]) - 1L
  opening_tag <- substr(xml, format_start, opening_end)
  finish <- opening_end
  if (!grepl("/\\s*>$", opening_tag, perl = TRUE)) {
    closing_suffix <- substr(xml, opening_end + 1L, nchar(xml))
    closing_start <- regexpr("</sheetFormatPr>", closing_suffix, fixed = TRUE)
    if (closing_start[[1L]] == -1L) {
      stop(
        "Could not locate the sheetFormatPr closing tag while restoring template columns",
        call. = FALSE
      )
    }
    finish <- opening_end + as.integer(closing_start[[1L]]) - 1L +
      nchar("</sheetFormatPr>")
  }
  paste0(
    substr(xml, 1L, finish),
    template_cols,
    if (finish < nchar(xml)) {
      substr(xml, finish + 1L, nchar(xml))
    } else {
      ""
    }
  )
}

spending_set_column_width <- function(xml, column, width) {
  column <- as.integer(column)
  width <- as.numeric(width)
  if (is.na(column) || column < 1L || !is.finite(width) || width <= 0) {
    stop("Spending Report column width request is invalid.", call. = FALSE)
  }
  matches <- gregexpr("<col\\s[^>]*/>", xml, perl = TRUE)[[1L]]
  if (matches[[1L]] == -1L) {
    stop("Spending Report column layout is missing.", call. = FALSE)
  }
  blocks <- regmatches(xml, list(matches))[[1L]]
  target <- which(vapply(blocks, function(block) {
    grepl(paste0('\\bmin="', column, '"'), block, perl = TRUE) &&
      grepl(paste0('\\bmax="', column, '"'), block, perl = TRUE)
  }, logical(1L)))
  if (length(target) != 1L) {
    stop(
      "Spending Report column layout is not unique for column ",
      column,
      call. = FALSE
    )
  }
  block <- blocks[[target]]
  block <- gsub('\\s+(width|customWidth)="[^"]*"', "", block, perl = TRUE)
  replacement <- sub(
    "/>$",
    paste0(' width="', format(width, scientific = FALSE, trim = TRUE),
           '" customWidth="1"/>'),
    block,
    perl = TRUE
  )
  spending_replace_xml_span(
    xml,
    as.integer(matches[[target]]),
    attr(matches, "match.length")[[target]],
    replacement
  )
}

spending_append_raw_input_styles <- function(styles_xml) {
  existing_ids <- regmatches(
    styles_xml,
    gregexpr("numFmtId=\"[0-9]+\"", styles_xml, perl = TRUE)
  )[[1L]]
  existing_ids <- if (identical(existing_ids, "")) {
    integer()
  } else {
    as.integer(gsub("[^0-9]", "", existing_ids))
  }
  currency_num_fmt_id <- max(c(163L, existing_ids), na.rm = TRUE) + 1L
  currency_num_fmt <- paste0(
    "<numFmt numFmtId=\"", currency_num_fmt_id,
    "\" formatCode=\"&quot;$&quot;#,##0.00_);",
    "[Red]\\(&quot;$&quot;#,##0.00\\)\"/>"
  )
  num_fmts_open <- regexpr(
    "<numFmts(?:\\s[^>]*)?>",
    styles_xml,
    perl = TRUE
  )
  if (num_fmts_open[[1L]] == -1L) {
    fonts <- regexpr("<fonts(?:\\s[^>]*)?>", styles_xml, perl = TRUE)
    if (fonts[[1L]] == -1L) {
      stop("Could not locate fonts in Spending Report styles", call. = FALSE)
    }
    styles_xml <- spending_replace_xml_span(
      styles_xml,
      as.integer(fonts[[1L]]),
      0L,
      paste0(
        "<numFmts count=\"1\">",
        currency_num_fmt,
        "</numFmts>"
      )
    )
  } else {
    opening <- regmatches(styles_xml, num_fmts_open)
    count <- suppressWarnings(as.integer(sub(
      ".*count=\"([0-9]+)\".*",
      "\\1",
      opening,
      perl = TRUE
    )))
    if (is.na(count)) {
      stop("Spending Report numFmts count is invalid", call. = FALSE)
    }
    updated_opening <- sub(
      "count=\"[0-9]+\"",
      paste0("count=\"", count + 1L, "\""),
      opening,
      perl = TRUE
    )
    styles_xml <- spending_replace_xml_span(
      styles_xml,
      as.integer(num_fmts_open[[1L]]),
      attr(num_fmts_open, "match.length"),
      updated_opening
    )
    closing <- regexpr("</numFmts>", styles_xml, fixed = TRUE)
    if (closing[[1L]] == -1L) {
      stop("Spending Report numFmts block is incomplete", call. = FALSE)
    }
    styles_xml <- spending_replace_xml_span(
      styles_xml,
      as.integer(closing[[1L]]),
      0L,
      currency_num_fmt
    )
  }
  cell_xfs_open <- regexpr(
    "<cellXfs(?:\\s[^>]*)?>",
    styles_xml,
    perl = TRUE
  )
  if (cell_xfs_open[[1L]] == -1L) {
    stop("Could not locate cellXfs in Spending Report styles", call. = FALSE)
  }
  opening <- regmatches(styles_xml, cell_xfs_open)
  count <- suppressWarnings(as.integer(sub(
    ".*count=\"([0-9]+)\".*",
    "\\1",
    opening,
    perl = TRUE
  )))
  if (is.na(count)) {
    stop("Spending Report cellXfs count is invalid", call. = FALSE)
  }
  date_style <- count
  currency_style <- count + 1L
  updated_opening <- sub(
    "count=\"[0-9]+\"",
    paste0("count=\"", count + 2L, "\""),
    opening,
    perl = TRUE
  )
  styles_xml <- spending_replace_xml_span(
    styles_xml,
    as.integer(cell_xfs_open[[1L]]),
    attr(cell_xfs_open, "match.length"),
    updated_opening
  )
  closing <- regexpr("</cellXfs>", styles_xml, fixed = TRUE)
  if (closing[[1L]] == -1L) {
    stop("Spending Report cellXfs block is incomplete", call. = FALSE)
  }
  additions <- paste0(
    "<xf numFmtId=\"14\" fontId=\"0\" fillId=\"0\" borderId=\"0\"",
    " xfId=\"0\" applyNumberFormat=\"1\" applyAlignment=\"1\">",
    "<alignment shrinkToFit=\"1\"/></xf>",
    "<xf numFmtId=\"", currency_num_fmt_id,
    "\" fontId=\"0\" fillId=\"0\" borderId=\"0\"",
    " xfId=\"0\" applyNumberFormat=\"1\" applyAlignment=\"1\">",
    "<alignment horizontal=\"right\" shrinkToFit=\"1\"/></xf>"
  )
  styles_xml <- spending_replace_xml_span(
    styles_xml,
    as.integer(closing[[1L]]),
    0L,
    additions
  )
  list(
    xml = styles_xml,
    date_style = date_style,
    currency_style = currency_style,
    currency_num_fmt_id = currency_num_fmt_id
  )
}

spending_set_column_cell_style <- function(xml, column, style) {
  matches <- gregexpr(
    paste0("<c r=\"", column, "[0-9]+\"[^>]*>"),
    xml,
    perl = TRUE
  )[[1L]]
  if (matches[[1L]] == -1L) {
    return(xml)
  }
  match_lengths <- attr(matches, "match.length")
  starts <- as.integer(matches)
  finishes <- starts + as.integer(match_lengths) - 1L
  openings <- substring(xml, starts, finishes)
  updated <- vapply(openings, function(opening) {
    if (grepl("\\s+s=\"[0-9]+\"", opening, perl = TRUE)) {
      sub(
        "\\s+s=\"[0-9]+\"",
        paste0(" s=\"", style, "\""),
        opening,
        perl = TRUE
      )
    } else {
      sub(
        ">",
        paste0(" s=\"", style, "\">"),
        opening,
        fixed = TRUE
      )
    }
  }, character(1L), USE.NAMES = FALSE)
  pieces <- character(length(starts) * 2L + 1L)
  cursor <- 1L
  for (index in seq_along(starts)) {
    pieces[[index * 2L - 1L]] <- if (starts[[index]] > cursor) {
      substr(xml, cursor, starts[[index]] - 1L)
    } else {
      ""
    }
    pieces[[index * 2L]] <- updated[[index]]
    cursor <- finishes[[index]] + 1L
  }
  pieces[[length(pieces)]] <- if (cursor <= nchar(xml)) {
    substr(xml, cursor, nchar(xml))
  } else {
    ""
  }
  paste0(pieces, collapse = "")
}

spending_apply_raw_input_formats <- function(staging, raw_sheet_indices) {
  if (!length(raw_sheet_indices)) return(invisible(NULL))
  styles_path <- file.path(staging, "xl", "styles.xml")
  if (!file.exists(styles_path)) {
    stop("Spending Report styles XML is missing", call. = FALSE)
  }
  styles <- spending_append_raw_input_styles(spending_read_xml(styles_path))
  for (sheet_index in raw_sheet_indices) {
    raw_path <- file.path(
      staging,
      "xl",
      "worksheets",
      paste0("sheet", sheet_index, ".xml")
    )
    if (!file.exists(raw_path)) {
      stop("Spending Report raw-input XML is missing: ", raw_path, call. = FALSE)
    }
    raw_xml <- spending_read_xml(raw_path)
    raw_xml <- spending_set_column_cell_style(raw_xml, "C", styles$date_style)
    raw_xml <- spending_set_column_cell_style(raw_xml, "D", styles$currency_style)
    writeLines(enc2utf8(raw_xml), raw_path, useBytes = TRUE)
  }
  writeLines(enc2utf8(styles$xml), styles_path, useBytes = TRUE)
  invisible(styles)
}

spending_transplant_column_layouts <- function(
  staging,
  template,
  raw_sheet_indices,
  support_sheet_index
) {
  template_staging <- tempfile("mentat-spending-template-")
  if (!dir.create(template_staging, recursive = TRUE, showWarnings = FALSE)) {
    stop("Could not create template staging directory", call. = FALSE)
  }
  on.exit(if (dir.exists(template_staging)) {
    unlink(template_staging, recursive = TRUE, force = TRUE)
  }, add = TRUE)
  utils::unzip(template, exdir = template_staging)
  template_sheet_dir <- file.path(template_staging, "xl", "worksheets")
  output_sheet_dir <- file.path(staging, "xl", "worksheets")
  template_paths <- file.path(
    template_sheet_dir,
    paste0("sheet", 1:4, ".xml")
  )
  if (any(!file.exists(template_paths))) {
    stop("Spending Report template must contain four worksheet XML files.", call. = FALSE)
  }
  restore_layout <- function(template_index, output_index, adjust = identity) {
    output_sheet <- file.path(
      output_sheet_dir,
      paste0("sheet", output_index, ".xml")
    )
    if (!file.exists(output_sheet)) {
      stop("Generated Spending Report is missing ", basename(output_sheet), call. = FALSE)
    }
    template_cols <- adjust(spending_xml_cols_block(
      spending_read_xml(template_paths[[template_index]])
    ))
    output_xml <- spending_set_xml_cols_block(
      spending_read_xml(output_sheet),
      template_cols
    )
    writeLines(enc2utf8(output_xml), output_sheet, useBytes = TRUE)
    if (!identical(spending_xml_cols_block(output_xml), template_cols)) {
      stop("Could not restore template column layout for ", basename(output_sheet), call. = FALSE)
    }
  }
  restore_layout(1L, 1L, spending_stats_expand_template_cols)
  restore_layout(2L, 2L)
  for (sheet_index in raw_sheet_indices) restore_layout(3L, sheet_index)
  restore_layout(4L, support_sheet_index)
  invisible(TRUE)
}

spending_restore_header_literals <- function(
  workbook,
  sheet_names,
  raw_sheet_names,
  stats_cached_values = NULL,
  template = spending_report_template_path()
) {
  require_package("zip")
  if (anyDuplicated(toupper(sheet_names)) || any(nchar(sheet_names) > 31L)) {
    stop("Spending Report sheet names are not valid and unique.", call. = FALSE)
  }
  support_sheet_index <- match("Sheet4", sheet_names)
  raw_sheet_indices <- match(raw_sheet_names, sheet_names)
  if (is.na(support_sheet_index) || anyNA(raw_sheet_indices)) {
    stop("Spending Report worksheet index mapping is incomplete.", call. = FALSE)
  }
  staging <- tempfile("mentat-spending-xlsx-")
  if (!dir.create(staging, recursive = TRUE, showWarnings = FALSE)) {
    stop("Could not create workbook staging directory", call. = FALSE)
  }
  on.exit(if (dir.exists(staging)) {
    unlink(staging, recursive = TRUE, force = TRUE)
  }, add = TRUE)
  utils::unzip(workbook, exdir = staging)
  spending_transplant_column_layouts(
    staging,
    template,
    raw_sheet_indices,
    support_sheet_index
  )
  sheet_path <- file.path(staging, "xl", "worksheets", "sheet1.xml")
  if (!file.exists(sheet_path)) {
    stop("Spending Report Stats worksheet XML is missing", call. = FALSE)
  }
  xml <- spending_read_xml(sheet_path)
  # The source template's Super PAC value column is only ~5 characters wide;
  # real production totals visibly render as ####. Widen only that value column
  # while preserving every other template width.
  xml <- spending_set_column_width(xml, 9L, 14)
  for (index in seq_along(2026:2021)) {
    year <- (2026:2021)[[index]]
    header_row <- 2L + (index - 1L) * 14L
    headers <- spending_stats_headers(year)[-1L]
    for (metric in seq_along(headers)) {
      column <- metric + 2L
      column_name <- spending_excel_column(column)
      cell <- paste0(column_name, header_row)
      old <- paste0(
        "<c r=\"", cell, "\"><f>",
        spending_xml_escape(spending_excel_string(headers[[metric]])),
        "</f><v>0</v></c>"
      )
      new <- paste0(
        "<c r=\"", cell, "\" t=\"inlineStr\"><is><t>",
        spending_xml_escape(headers[[metric]]),
        "</t></is></c>"
      )
      if (!grepl(old, xml, fixed = TRUE)) {
        stop(
          "Could not restore literal Spending Report header at ",
          cell,
          call. = FALSE
        )
      }
      xml <- sub(old, new, xml, fixed = TRUE)
    }
  }
  if (!is.null(stats_cached_values)) {
    xml <- spending_set_formula_caches(xml, stats_cached_values)
  }
  writeLines(enc2utf8(xml), sheet_path, useBytes = TRUE)
  for (raw_sheet_index in raw_sheet_indices) {
    raw_path <- file.path(
      staging,
      "xl",
      "worksheets",
      paste0("sheet", raw_sheet_index, ".xml")
    )
    raw_xml <- spending_read_xml(raw_path)
    anchor <- paste0(
      "<c r=\"B1\" t=\"inlineStr\"><is><t>Recipient</t></is></c>"
    )
    additions <- paste0(
      "<c r=\"C1\" t=\"inlineStr\"><is><t>Date</t></is></c>",
      "<c r=\"D1\" t=\"inlineStr\"><is><t>Amount</t></is></c>"
    )
    if (!grepl(anchor, raw_xml, fixed = TRUE)) {
      stop("Could not restore raw-input headers for sheet index ", raw_sheet_index, call. = FALSE)
    }
    raw_xml <- sub(anchor, paste0(anchor, additions), raw_xml, fixed = TRUE)
    writeLines(enc2utf8(raw_xml), raw_path, useBytes = TRUE)
  }
  support_path <- file.path(
    staging,
    "xl",
    "worksheets",
    paste0("sheet", support_sheet_index, ".xml")
  )
  if (!file.exists(support_path)) {
    stop("Spending Report calculation-support worksheet XML is missing", call. = FALSE)
  }
  support_xml <- spending_read_xml(support_path)
  support_headers <- list(
    list(
      after = "Record Type",
      cells = c(B1 = "Year")
    ),
    list(
      after = "Party",
      cells = c(G1 = "Amount")
    ),
    list(
      after = "Contributor Name",
      cells = c(J1 = "Raw Input Row", K1 = "Employee Row")
    ),
    list(
      after = "Party Source",
      cells = c(
        O1 = "Is Entity Contributor",
        P1 = "Raw Input Sheet"
      )
    )
  )
  for (group in support_headers) {
    anchor_cells <- c(B1 = "A1", G1 = "F1", J1 = "I1", O1 = "N1")
    anchor_cell <- anchor_cells[[names(group$cells)[[1L]]]]
    anchor <- paste0(
      "<c r=\"", anchor_cell, "\" t=\"inlineStr\"><is><t>",
      spending_xml_escape(group$after),
      "</t></is></c>"
    )
    additions <- paste(vapply(names(group$cells), function(cell) {
      paste0(
        "<c r=\"", cell, "\" t=\"inlineStr\"><is><t>",
        spending_xml_escape(group$cells[[cell]]),
        "</t></is></c>"
      )
    }, character(1L)), collapse = "")
    if (!grepl(anchor, support_xml, fixed = TRUE)) {
      stop(
        "Could not restore Spending Report support headers after ",
        group$after,
        call. = FALSE
      )
    }
    support_xml <- sub(
      anchor,
      paste0(anchor, additions),
      support_xml,
      fixed = TRUE
    )
  }
  writeLines(enc2utf8(support_xml), support_path, useBytes = TRUE)
  spending_apply_raw_input_formats(staging, raw_sheet_indices)
  spending_set_calculation_properties(staging, template)
  core_path <- file.path(staging, "docProps", "core.xml")
  if (file.exists(core_path)) {
    core_xml <- spending_read_xml(core_path)
    core_xml <- gsub(
      "<dcterms:(created|modified)[^>]*>[^<]*</dcterms:\1>",
      "<dcterms:\1 xsi:type=\"dcterms:W3CDTF\">1980-01-01T00:00:00Z</dcterms:\1>",
      core_xml,
      perl = TRUE
    )
    writeLines(enc2utf8(core_xml), core_path, useBytes = TRUE)
  }
  files <- sort(list.files(
    staging,
    recursive = TRUE,
    all.files = TRUE,
    include.dirs = FALSE,
    no.. = TRUE
  ), method = "radix")
  fixed_time <- as.POSIXct("1980-01-01T00:00:00Z", tz = "UTC")
  Sys.setFileTime(file.path(staging, files), fixed_time)
  rebuilt <- tempfile(
    pattern = ".mentat-spending-rebuilt-",
    tmpdir = dirname(workbook),
    fileext = ".xlsx"
  )
  on.exit(if (file.exists(rebuilt)) unlink(rebuilt, force = TRUE), add = TRUE)
  zip::zip(
    rebuilt,
    files = files,
    root = staging,
    mode = "mirror",
    include_directories = FALSE
  )
  if (!file.exists(rebuilt) || file.info(rebuilt)$size <= 0) {
    stop("Could not rebuild Spending Report workbook", call. = FALSE)
  }
  atomic_replace(rebuilt, workbook)
  invisible(workbook)
}

spending_leg_class_counts <- function(connection) {
  apply_amendment_versioning(connection, print_summary = FALSE)
  data.table::as.data.table(DBI::dbGetQuery(
    connection,
    paste(
      "SELECT COUNT(*) AS total_rows,",
      "SUM(is_conduit_leg) AS conduit_rows,",
      "SUM(is_memo_leg) AS memo_rows,",
      "SUM(is_passthrough_leg) AS passthrough_rows,",
      "SUM(CASE WHEN is_conduit_leg=1 AND duplicate_memo=0",
      "THEN 1 ELSE 0 END) AS kept_conduit_rows,",
      "SUM(CASE WHEN duplicate_memo=0 THEN 1 ELSE 0 END)",
      "AS kept_before_match_filter",
      paste0("FROM contributions WHERE ", report_industry_filter())
    )
  ))
}

spending_downstream_earmark_mask <- function(rows) {
  # Retained as an analytical flag helper only. It must not select report,
  # Sheet4, Stats, or reconciliation rows under the recipient-as-shown rule.
  if (!nrow(rows)) return(logical())
  receipt_type <- if ("receipt_type_full" %in% names(rows)) {
    toupper(trim_text(rows$receipt_type_full))
  } else {
    rep("", nrow(rows))
  }
  conduit_id <- if ("conduit_committee_id" %in% names(rows)) {
    toupper(trim_text(rows$conduit_committee_id))
  } else {
    rep("", nrow(rows))
  }
  committee_id <- if ("committee_id" %in% names(rows)) {
    toupper(trim_text(rows$committee_id))
  } else {
    rep("", nrow(rows))
  }
  is_conduit <- if ("is_conduit_leg" %in% names(rows)) {
    !is.na(rows$is_conduit_leg) & rows$is_conduit_leg == 1L
  } else {
    rep(FALSE, nrow(rows))
  }
  receipt_marker <- grepl(
    "^EARMARKED[[:space:]]+THROUGH\\b",
    receipt_type,
    perl = TRUE
  )
  conduit_relationship <- nzchar(conduit_id) &
    conduit_id != committee_id &
    !is_conduit
  receipt_marker | conduit_relationship
}

spending_assert_no_duplicate_kept_tuples <- function(rows) {
  if (!nrow(rows)) return(invisible(TRUE))
  if (!"sub_id" %in% names(rows) || any(!nzchar(trim_text(rows$sub_id)))) {
    stop("Double-count guard failed: kept receipts require a nonblank sub_id.", call. = FALSE)
  }
  duplicate_sub_ids <- unique(rows$sub_id[duplicated(rows$sub_id)])
  if (length(duplicate_sub_ids)) {
    stop(
      paste0(
        "Double-count guard failed: kept FEC receipt sub_ids are duplicated; first=",
        duplicate_sub_ids[[1L]]
      ),
      call. = FALSE
    )
  }
  if ("duplicate_memo" %in% names(rows) &&
      any(rows$duplicate_memo == 1L, na.rm = TRUE)) {
    stop(
      "Double-count guard failed: an exact five-key memo duplicate was kept.",
      call. = FALSE
    )
  }
  invisible(TRUE)
}

spending_report_lookups <- function(connection) {
  # Trade-association committee IDs are applied HERE, at the point of use, rather
  # than relying on the copy persisted in the `trade` table. The exporter
  # (tools/export_crypto_spending_data.R) opens the database SQLITE_RO and never
  # calls resolve_recipient_register_committee_ids(), so a resolution that only
  # lived in the database would silently do nothing on the actual report path.
  # Applying it on read is read-only safe, idempotent, and covers every caller.
  apply_trade_overrides <- function(trade) {
    # Must not throw: mentat_input_dir() aborts when the process starts outside
    # the project, and this runs on the report path where the inputs directory
    # is otherwise not needed. An unguarded call here killed every export
    # launched from another working directory.
    input_dir <- mentat_input_dir_or_na()
    if (is.na(input_dir)) {
      warning(
        "Could not locate the inputs directory; trade-association committee-ID ",
        "overrides were not applied and the Trade Association category will be ",
        "empty. Set MENTAT_INPUT_DIR to fix.",
        call. = FALSE
      )
      return(trade)
    }
    overrides <- read_trade_committee_overrides(
      file.path(input_dir, "trade_committee_overrides.csv")
    )
    if (!nrow(trade) || !nrow(overrides)) {
      return(trade)
    }
    matched <- match(
      normalize_register_key(trade$association_name),
      normalize_register_key(overrides$association_name)
    )
    hit <- !is.na(matched)
    if (any(hit)) {
      trade[hit, committee_id := overrides$committee_id[matched[hit]]]
    }
    trade
  }

  # Broad-net resolution for associations no curated override names.
  #
  # An association's PAC almost never files under the association's exact name,
  # which is all the register carried - so all 335 rows resolved to nothing and
  # the Trade Association category read $0.00 in every shipped book. The money
  # did not vanish: a trade PAC is committee_type Q with designation B or U, so
  # it matches no fallback rule, keeps the default "Unclassified" category, and
  # Unclassified folds into the Candidate Stats column. That is the ~$163k
  # "Candidate" discrepancy - trade money filed as candidate money, not a
  # Candidate/Leadership swap.
  #
  # The filing pattern is regular: committee name = association name + a
  # committee suffix, sometimes behind a leading article or a "<BRAND> PAC OF
  # THE <ASSOCIATION>" wrapper. Matching the association's name ANYWHERE in the
  # committee name resolves those (verified against the live snapshots:
  # NATIONAL ASSOCIATION OF REALTORS POLITICAL ACTION COMMITTEE,
  # AMERICAN BANKERS ASSOCIATION PAC (BANKPAC), and
  # BUILD POLITICAL ACTION COMMITTEE OF THE NATIONAL ASSOCIATION OF HOME
  # BUILDERS (BUILDPAC) all resolve, with no ambiguous hits).
  #
  # Two guards keep the net from over-reaching. Names under 10 characters are
  # never netted - short ones are acronym-like and would collide. An association
  # matching MORE than one committee ID is left unresolved rather than guessed,
  # per the register's standing rule that ambiguity goes to review and is never
  # auto-resolved. Matching on the full normalized name (not acronyms) is what
  # keeps American Bankers Association and American Bar Association apart.
  trade_resolve_broad_net <- function(trade) {
    if (!nrow(trade)) return(trade)
    normalize <- function(values) {
      text <- toupper(as.character(values))
      text <- gsub("[^A-Z0-9 ]", " ", text)
      trim_text(gsub("[[:space:]]+", " ", text))
    }
    # Strip committee-flavoured words only from the END, so the association's
    # own words survive (Association of Equipment Manufacturers keeps
    # "Manufacturers"; only a trailing "PAC"/"COMMITTEE"/"FUND" is removed).
    suffix <- paste0(
      "\\b(POLITICAL ACTION COMMITTEE|POLITICAL VICTORY FUND|POLITICAL FUND|",
      "FEDERAL PAC|PAC|INC|LLC|FUND|COMMITTEE)\\s*$"
    )
    strip_suffix <- function(text) {
      repeat {
        stripped <- trim_text(sub(suffix, "", text))
        if (identical(stripped, text)) return(text)
        text <- stripped
      }
    }
    association <- vapply(normalize(trade$association_name), strip_suffix, character(1L))
    unresolved <- which(
      !nzchar(trim_text(trade$committee_id %||% "")) & nchar(association) >= 10L
    )
    if (!length(unresolved)) return(trade)
    observed <- data.table::as.data.table(DBI::dbGetQuery(
      connection,
      paste(
        "SELECT DISTINCT UPPER(TRIM(committee_id)) AS committee_id, committee_name",
        "FROM contributions",
        "WHERE committee_id IS NOT NULL AND TRIM(committee_id) <> ''",
        "AND committee_name IS NOT NULL AND TRIM(committee_name) <> ''"
      )
    ))
    if (!nrow(observed)) return(trade)
    observed[, match_name := normalize(committee_name)]
    resolved <- 0L
    ambiguous <- character(0L)
    for (index in unresolved) {
      needle <- association[[index]]
      # Prefix first, containment second. Containment alone is ambiguous whenever
      # one association's name is contained in another's: "American Financial
      # Services Association" is a substring of "Native American Financial
      # Services Association", so a book holding both PACs matches two IDs and
      # the ambiguity rule drops AFSA even though its own PAC is named exactly.
      # A committee whose name STARTS with the association resolves that cleanly,
      # and only falls back to containment for the wrapper forms the net exists
      # to catch ("BUILD PAC OF THE NATIONAL ASSOCIATION OF HOME BUILDERS").
      candidates <- unique(observed$committee_id[
        which(startsWith(observed$match_name, needle))
      ])
      candidates <- candidates[nzchar(candidates)]
      found <- if (length(candidates) == 1L) {
        candidates
      } else {
        unique(observed$committee_id[
          which(grepl(needle, observed$match_name, fixed = TRUE))
        ])
      }
      found <- unique(found[nzchar(found)])
      if (length(found) == 1L) {
        data.table::set(trade, index, "committee_id", found)
        resolved <- resolved + 1L
      } else if (length(found) > 1L) {
        ambiguous <- c(ambiguous, trade$association_name[[index]])
      }
    }
    if (resolved || length(ambiguous)) {
      message(sprintf(
        "Trade associations resolved by name: %d of %d unresolved%s",
        resolved,
        length(unresolved),
        if (length(ambiguous)) {
          sprintf(
            "; %d left unresolved as ambiguous (%s)",
            length(ambiguous),
            paste(utils::head(ambiguous, 5L), collapse = "; ")
          )
        } else {
          ""
        }
      ))
    }
    trade
  }
  list(
    committee_master = list(
      categories = data.table::as.data.table(DBI::dbGetQuery(
        connection,
        paste(
          "SELECT cycle, group_name, category, lean, committee_id, provisional",
          "FROM categories ORDER BY cycle, group_name, category"
        )
      )),
      # Curated overrides win; the broad net only fills what is still unresolved.
      trade = trade_resolve_broad_net(apply_trade_overrides(
        data.table::as.data.table(DBI::dbGetQuery(
          connection,
          "SELECT association_name, committee_id FROM trade ORDER BY association_name"
        ))
      )),
      leadership = data.table::as.data.table(DBI::dbGetQuery(
        connection,
        paste(
          "SELECT cycle, pac_name, lean, committee_id, provisional",
          "FROM leadership ORDER BY cycle, pac_name"
        )
      ))
    ),
    companies = data.table::as.data.table(DBI::dbGetQuery(
      connection,
      paste(
        "SELECT company, variant FROM companies",
        paste0("WHERE ", report_industry_filter(), " ORDER BY company, variant_order")
      )
    )),
    executives = data.table::as.data.table(DBI::dbGetQuery(
      connection,
      paste(
        "SELECT company, executive_name FROM executives",
        paste0("WHERE ", report_industry_filter())
      )
    ))
  )
}

# FEC committee type and designation code books.
#
# The detail sheets publish the raw one-letter code AND a label beside it: the
# code is what the FEC register actually stores and what a chart can group on
# without ambiguity, the label is what makes that chart readable. Codes are
# transcribed from the FEC committee master data dictionary. Anything the book
# does not contain - including a blank - resolves to "", never to a guess,
# because an invented label would make an unclassified committee look
# classified.
MENTAT_FEC_COMMITTEE_TYPE_LABELS <- c(
  C = "Communication cost",
  D = "Delegate committee",
  E = "Electioneering communication",
  H = "House",
  I = "Independent expenditor (person or group)",
  N = "PAC - nonqualified",
  O = "Independent expenditure-only (Super PAC)",
  P = "Presidential",
  Q = "PAC - qualified",
  S = "Senate",
  U = "Single-candidate independent expenditure",
  V = "Hybrid PAC (Carey) - nonqualified",
  W = "Hybrid PAC (Carey) - qualified",
  X = "Party - nonqualified",
  Y = "Party - qualified",
  Z = "National party nonfederal account"
)

MENTAT_FEC_COMMITTEE_DESIGNATION_LABELS <- c(
  A = "Authorized by a candidate",
  B = "Lobbyist/Registrant PAC",
  D = "Leadership PAC",
  J = "Joint fundraiser",
  P = "Principal campaign committee",
  U = "Unauthorized"
)

mentat_fec_code_label <- function(codes, book) {
  code <- toupper(trim_text(codes))
  label <- unname(book[match(code, names(book))])
  label[is.na(label)] <- ""
  label
}

# Fine-grain detail frames for the workbook's chartable sheets.
#
# The Chart Studio console can only chart columns that a worksheet actually
# PUBLISHES, and the raw transaction sheet is the first thing a large industry
# loses: tools/prepare_workbook_payload.py projects raw_rows onto 18 fields that
# carry no ZIP, city, state, employer or occupation, and formula-fact mode
# replaces raw_rows outright with aggregate facts. These two frames are built
# here, on the full accepted rows, and travel as their OWN payload keys, so no
# compaction mode can silently empty the sheets that make committee, geography,
# employer and occupation chartable.
#
# The row cap exists because app/report_insights.py REFUSES a worksheet over
# 50,000 rows - it raises rather than truncating, so an oversized sheet is not a
# degraded chart, it is no chart at all. Past the cap the smallest rows fold into
# one "Other" row per company and cycle, which keeps the cap and the
# reconciliation invariant (per-cycle detail total == per-cycle included total)
# simultaneously true. R\export_guards.R re-checks that invariant against the
# cycle totals the rest of the workbook prints.
#
# nrow <= row_cap holds whenever row_cap is at least the number of
# (cycle, company) pairs, because each pair can need an Other row of its own.
# The caller's floor (MENTAT_DETAIL_ROW_CAP >= 100, ten companies, three
# cycles) keeps that true; the function itself accepts any positive cap so a
# test can exercise the fold on a handful of rows.
spending_detail_frames <- function(rows, row_cap = 45000L) {
  require_package("data.table")
  row_cap <- suppressWarnings(as.integer(row_cap))
  if (length(row_cap) != 1L || is.na(row_cap) || row_cap < 1L) {
    stop("spending_detail_frames needs a positive row cap.", call. = FALSE)
  }
  detail <- data.table::as.data.table(rows)
  required <- c(
    "cycle", "cycle_label", "company", "committee_id", "recipient",
    "committee_type", "committee_designation", "recipient_category", "party",
    "state", "city", "zipcode", "employer", "occupation", "street_1",
    "street_2", "amount", "person_key", "include_in_analysis", "date"
  )
  absent <- setdiff(required, names(detail))
  if (length(absent)) {
    stop(
      "spending_detail_frames is missing source columns: ",
      paste(absent, collapse = ", "),
      call. = FALSE
    )
  }
  recipient_keys <- c("cycle", "cycle_label", "company", "committee_id")
  contributor_keys <- c(
    "cycle", "cycle_label", "company", "state", "city", "zip5", "employer",
    "occupation", "street_1", "street_2"
  )
  recipient_columns <- c(
    recipient_keys, "recipient", "committee_type", "committee_type_label",
    "designation", "designation_label", "recipient_category", "party",
    "total_giving", "gift_count", "contributors", "first_date", "last_date",
    "folded_rows"
  )
  contributor_columns <- c(
    contributor_keys, "total_giving", "gift_count", "contributors",
    "folded_rows"
  )
  empty_frame <- function(columns) {
    frame <- data.table::data.table(
      cycle = integer(), cycle_label = character(), company = character(),
      committee_id = character(), recipient = character(),
      committee_type = character(), committee_type_label = character(),
      designation = character(), designation_label = character(),
      recipient_category = character(), party = character(),
      state = character(), city = character(), zip5 = character(),
      employer = character(), occupation = character(),
      street_1 = character(), street_2 = character(),
      total_giving = numeric(), gift_count = integer(),
      contributors = integer(), first_date = character(),
      last_date = character(), folded_rows = integer()
    )
    frame[, columns, with = FALSE]
  }
  detail <- data.table::copy(detail[include_in_analysis == "Yes"])
  if (!nrow(detail)) {
    return(list(
      recipient_detail = empty_frame(recipient_columns),
      contributor_detail = empty_frame(contributor_columns),
      stats = list(
        row_cap = row_cap, recipient_rows = 0L, recipient_folded_rows = 0L,
        contributor_rows = 0L, contributor_folded_rows = 0L
      )
    ))
  }
  detail[, cycle := as.integer(cycle)]
  detail[, cycle_label := trim_text(cycle_label)]
  detail[, company := trim_text(company)]
  detail[, committee_id := toupper(trim_text(committee_id))]
  detail[, committee_type := toupper(trim_text(committee_type))]
  detail[, committee_designation := toupper(trim_text(committee_designation))]
  detail[, state := trim_text(state)]
  detail[, city := trim_text(city)]
  detail[, employer := trim_text(employer)]
  detail[, occupation := trim_text(occupation)]
  detail[, street_1 := trim_text(street_1)]
  detail[, street_2 := trim_text(street_2)]
  detail[, person_key := trim_text(person_key)]
  detail[, amount := as.numeric(amount)]
  detail[, date := trim_text(date)]
  # ZIP+4, a 9-digit string and a plain 5-digit code all share the same leading
  # five characters; anything else (blank, truncated, alphabetic foreign codes)
  # is counted under "" rather than dropped, so the geography sheet still
  # reconciles to the cycle total.
  detail[, zip5 := substr(trim_text(zipcode), 1L, 5L)]
  detail[!grepl("^[0-9]{5}$", zip5), zip5 := ""]

  # Deterministic cap and fold.
  #
  # Ordering is total_giving descending with the key columns ascending as the
  # tie-break, so the same input folds the same rows no matter what order
  # data.table produced the groups in. One row per (cycle, company) is held back
  # from the cap because every folded group has to land in its own company's
  # "Other" row and those rows are additional. The Other row's numbers are
  # recomputed from the SOURCE rows of the folded groups, not summed from the
  # group rows: summing per-group contributor counts would double-count a person
  # who gave to more than one folded recipient.
  fold_frame <- function(frame, key_columns, marker_column, dated) {
    folded_count <- 0L
    if (nrow(frame) > row_cap) {
      data.table::setorderv(
        frame,
        c("total_giving", key_columns),
        c(-1L, rep(1L, length(key_columns)))
      )
      reserve <- nrow(unique(frame[, .(cycle, company)]))
      keep <- max(row_cap - reserve, 0L)
      folded <- frame[seq.int(keep + 1L, nrow(frame))]
      frame <- frame[seq_len(keep)]
      folded_count <- nrow(folded)
      group_key <- function(source) {
        do.call(paste, c(as.list(source[, key_columns, with = FALSE]), list(sep = "\r")))
      }
      members <- detail[group_key(detail) %in% group_key(folded)]
      other <- if (dated) {
        members[, .(
          total_giving = sum(amount, na.rm = TRUE),
          gift_count = .N,
          contributors = data.table::uniqueN(person_key[nzchar(person_key)]),
          first_date = fec_min_text(date),
          last_date = fec_max_text(date)
        ), by = .(cycle, cycle_label, company)]
      } else {
        members[, .(
          total_giving = sum(amount, na.rm = TRUE),
          gift_count = .N,
          contributors = data.table::uniqueN(person_key[nzchar(person_key)])
        ), by = .(cycle, cycle_label, company)]
      }
      other <- merge(
        other,
        folded[, .(folded_rows = .N), by = .(cycle, cycle_label, company)],
        by = c("cycle", "cycle_label", "company"),
        sort = FALSE
      )
      for (column in setdiff(names(frame), names(other))) {
        value <- if (identical(column, marker_column)) {
          sprintf("Other (%d rows folded)", other$folded_rows)
        } else if (is.character(frame[[column]])) {
          ""
        } else {
          frame[[column]][NA_integer_]
        }
        other[, (column) := value]
      }
      frame <- data.table::rbindlist(
        list(frame, other[, names(frame), with = FALSE]),
        use.names = TRUE
      )
    }
    sort_columns <- c("cycle", "total_giving", setdiff(key_columns, "cycle"))
    data.table::setorderv(
      frame,
      sort_columns,
      c(1L, -1L, rep(1L, length(sort_columns) - 2L))
    )
    list(frame = frame[], folded = folded_count)
  }

  recipient_detail <- detail[, .(
    recipient = fec_mode_text(recipient),
    committee_type = fec_mode_text(committee_type),
    designation = fec_mode_text(committee_designation),
    recipient_category = fec_mode_text(recipient_category),
    party = fec_mode_text(party),
    total_giving = sum(amount, na.rm = TRUE),
    gift_count = .N,
    contributors = data.table::uniqueN(person_key[nzchar(person_key)]),
    first_date = fec_min_text(date),
    last_date = fec_max_text(date),
    folded_rows = 0L
  ), by = c(recipient_keys)]
  contributor_detail <- detail[, .(
    total_giving = sum(amount, na.rm = TRUE),
    gift_count = .N,
    contributors = data.table::uniqueN(person_key[nzchar(person_key)]),
    folded_rows = 0L
  ), by = c(contributor_keys)]

  # The marker goes in the first text column the SHEET shows - Recipient, State -
  # so a chart grouped on that column gets a visible "Other" bucket instead of a
  # blank one, and Committee ID stays clean because the PAC & Candidate Map
  # sheet joins on it.
  recipient_folded <- fold_frame(recipient_detail, recipient_keys, "recipient", TRUE)
  contributor_folded <- fold_frame(contributor_detail, contributor_keys, "state", FALSE)
  recipient_detail <- recipient_folded$frame
  contributor_detail <- contributor_folded$frame
  recipient_detail[, committee_type_label := mentat_fec_code_label(
    committee_type, MENTAT_FEC_COMMITTEE_TYPE_LABELS
  )]
  recipient_detail[, designation_label := mentat_fec_code_label(
    designation, MENTAT_FEC_COMMITTEE_DESIGNATION_LABELS
  )]
  data.table::setcolorder(recipient_detail, recipient_columns)
  data.table::setcolorder(contributor_detail, contributor_columns)
  list(
    recipient_detail = recipient_detail[],
    contributor_detail = contributor_detail[],
    stats = list(
      row_cap = row_cap,
      recipient_rows = nrow(recipient_detail),
      recipient_folded_rows = recipient_folded$folded,
      contributor_rows = nrow(contributor_detail),
      contributor_folded_rows = contributor_folded$folded
    )
  )
}
spending_raw_report_rows <- function(connection, versioning_applied = FALSE) {
  require_package("data.table")
  if (!isTRUE(versioning_applied)) {
    apply_amendment_versioning(connection, print_summary = FALSE)
  }
  # Exports read immutable snapshots, including pre-migration databases. Missing
  # source metadata stays blank/unverified instead of inventing receipt roles.
  optional_projection <- function(table, fields) {
    present <- DBI::dbListFields(connection, table)
    paste0(paste(vapply(fields, function(field) {
      if (field %in% present) field else paste0("NULL AS ", field)
    }, character(1L)), collapse = ", "), ",")
  }
  receipt_projection <- function(table) {
    optional_projection(table, c(
      "receipt_type_full", "filing_form", "receipt_type", "line_number", "line_number_label",
      "file_num", "report_year", "report_type",
      "memo_code", "memoed_subtotal", "memo_text", "back_reference_transaction_id", "back_reference_schedule_name",
      "contributor_id"))
  }
  # contributor_street_1/2 arrived with the street-column migration. Snapshots
  # taken before it do not carry the columns, and SELECTing them
  # unconditionally aborts the whole export with "no such column" before any
  # guard downstream can run. Give them the same tolerance the receipt
  # metadata above already has: absent means blank, never a failed export.
  street_projection <- function(table) {
    optional_projection(table, c("contributor_street_1", "contributor_street_2"))
  }
  people <- data.table::as.data.table(DBI::dbGetQuery(
    connection,
    paste(
      "SELECT contributor_name, committee_name, contribution_receipt_date,",
      "contribution_receipt_amount, contributor_city, contributor_state,",
      "contributor_zip, contributor_employer, contributor_occupation,",
      street_projection("contributions"), "company, sub_id, matched_variant,",
      "committee_id, recipient_committee_type, recipient_committee_designation,",
      "two_year_transaction_period, entity_type, is_entity_contributor,",
      receipt_projection("contributions"), "transaction_id, pdf_url,",
      "conduit_committee_id, conduit_committee_name, is_conduit_leg,",
      "is_memo_leg, is_passthrough_leg, memo_review, possible_amendment, is_refund,",
      "superseded_by_amendment, duplicate_filing_version, duplicate_memo,",
      "company_match_status, company_match_reason, excluded_reason,",
      "'contributions' AS source_table,",
      "CASE WHEN is_entity_contributor=1 THEN 'CORPORATE'",
      "ELSE 'CONTRIBUTION' END AS record_type",
      paste0("FROM contributions WHERE ", report_industry_filter())
    )
  ))
  corporate <- data.table::as.data.table(DBI::dbGetQuery(
    connection,
    paste(
      "SELECT contributor_name, committee_name, contribution_receipt_date,",
      "contribution_receipt_amount, contributor_city, contributor_state,",
      "contributor_zip, contributor_employer, contributor_occupation,",
      street_projection("corporate_giving"), "company, sub_id, matched_variant,",
      "committee_id, recipient_committee_type, recipient_committee_designation,",
      "cycle AS two_year_transaction_period, entity_type, is_entity_contributor,",
      receipt_projection("corporate_giving"), "transaction_id, pdf_url, '' AS conduit_committee_id,",
      "'' AS conduit_committee_name, is_conduit_leg, is_memo_leg,",
      "is_passthrough_leg, 0 AS memo_review,",
      "CASE WHEN original_sub_id IS NOT NULL AND TRIM(original_sub_id)<>''",
      "OR UPPER(TRIM(amendment_indicator)) IN ('A','C','T') THEN 1 ELSE 0 END",
      "AS possible_amendment,",
      "CASE WHEN contribution_receipt_amount < 0 THEN 1 ELSE 0 END AS is_refund,",
      "superseded_by_amendment, duplicate_filing_version, duplicate_memo,",
      "company_match_status, company_match_reason, excluded_reason,",
      "'corporate_giving' AS source_table,",
      "'CORPORATE' AS record_type",
      paste0("FROM corporate_giving WHERE ", report_industry_filter())
    )
  ))
  rows <- data.table::rbindlist(list(people, corporate), use.names = TRUE, fill = TRUE)
  if (!nrow(rows)) return(rows)
  rows[, source_priority :=
    ifelse(company_match_status == "accepted", 0L, 2L) +
      ifelse(record_type == "CORPORATE", 1L, 0L)]
  data.table::setorder(rows, source_priority, sub_id)
  rows <- unique(rows, by = "sub_id")
  rows[, source_priority := NULL]
  variants <- spending_report_lookups(connection)$companies
  rows[, company_attribution_status := fec_attribution_status(rows, split(variants$variant, variants$company))]
  rows[, receipt_role := fec_receipt_role(rows)]
  # Never trust flags computed by an older matcher on an immutable snapshot.
  memo_plan <- mentat_memo_duplicate_plan(rows)
  rows[, `:=`(duplicate_memo = memo_plan$duplicate_memo,
              duplicate_of_sub_id = memo_plan$duplicate_of_sub_id,
              duplicate_reason = memo_plan$duplicate_reason)]
  rows[]
}

spending_report_rows <- function(connection, lookups = NULL, raw_rows = NULL) {
  require_package("data.table")
  rows <- raw_rows %||% spending_raw_report_rows(connection)
  if (!nrow(rows)) return(rows)
  rows <- data.table::copy(rows)
  rows[, receipt_role := fec_receipt_role(rows)]
  rows <- rows[fec_receipt_countable(rows)]
  lookups <- lookups %||% spending_report_lookups(connection)
  variants <- lookups$companies
  company_variants <- split(variants$variant, variants$company)
  entity_flags <- spending_entity_contributor_flags(rows, company_variants)
  # Reclassification can discover an entity, but it must never downgrade an
  # entity already validated by the source-specific retag pass. In particular,
  # corporate_giving rows whose names use approved corporate qualifiers (for
  # example COINBASE GLOBAL) are not literal
  # roster aliases, yet the corporate matcher has already verified them.
  persisted_entity_flags <- as.integer(rows$source_table == "corporate_giving" & toupper(trim_text(rows$entity_type)) != "IND")
  rows[, is_entity_contributor := pmax(
    persisted_entity_flags,
    entity_flags,
    na.rm = TRUE
  )]
  rows[, record_type := ifelse(
    is_entity_contributor == 1L,
    "CORPORATE",
    "CONTRIBUTION"
  )]
  spending_assert_no_duplicate_kept_tuples(rows)
  rows[]
}

spending_entity_contributor_flags <- function(rows, company_variants) {
  require_package("data.table")
  if (!nrow(rows)) return(integer())

  # The source entity type resolves the common case without any name work.
  # For the remaining rows, classify each distinct company/name pair once
  # instead of rebuilding the same company alias set for every contribution.
  flags <- as.integer(
    toupper(trim_text(rows$entity_type)) %in% c("ORG", "COM", "PAC", "CCM")
  )
  unresolved <- which(flags == 0L)
  if (!length(unresolved)) return(flags)

  pairs <- unique(rows[unresolved, .(
    contributor_name = as.character(contributor_name),
    company = as.character(company)
  )])
  pairs[, entity_match := FALSE]
  for (company_value in unique(pairs$company)) {
    indices <- which(pairs$company == company_value)
    variants <- company_variants[[company_value]] %||% company_value
    company_keys <- unique(vapply(
      c(company_value, variants),
      strip_legal_suffix,
      character(1L)
    ))
    company_keys <- company_keys[nzchar(company_keys)]
    contributor_keys <- vapply(
      pairs$contributor_name[indices],
      strip_legal_suffix,
      character(1L)
    )
    contributor_squashed <- gsub(
      "[^A-Z0-9]",
      "",
      contributor_keys,
      perl = TRUE
    )
    company_squashed <- unique(gsub(
      "[^A-Z0-9]",
      "",
      company_keys,
      perl = TRUE
    ))
    pairs$entity_match[indices] <- nzchar(contributor_keys) & (
      contributor_keys %in% company_keys |
        (
          nzchar(contributor_squashed) &
            contributor_squashed %in% company_squashed
        )
    )
  }
  pair_keys <- paste0(
    nchar(pairs$contributor_name),
    ":",
    pairs$contributor_name,
    "\034",
    nchar(pairs$company),
    ":",
    pairs$company
  )
  row_keys <- paste0(
    nchar(rows$contributor_name[unresolved]),
    ":",
    rows$contributor_name[unresolved],
    "\034",
    nchar(rows$company[unresolved]),
    ":",
    rows$company[unresolved]
  )
  pair_match <- pairs$entity_match[match(row_keys, pair_keys)]
  flags[unresolved] <- as.integer(pair_match)
  flags
}

spending_ordered_report_rows <- function(
  connection,
  lookups = NULL,
  raw_rows = NULL
) {
  rows <- spending_report_rows(
    connection,
    lookups = lookups,
    raw_rows = raw_rows
  )
  if (!nrow(rows)) return(rows)
  rows[, sort_date := as.integer(normalize_reconcile_date(contribution_receipt_date))]
  data.table::setorder(rows, -sort_date, contributor_name, sub_id)
  rows
}

spending_employee_rows <- function(rows) {
  if (!nrow(rows)) {
    return(data.table::data.table(
      employee_name = character(),
      employee_title = character(),
      company = character(),
      zipcode = character(),
      state = character(),
      employee_row = integer()
    ))
  }
  employees <- unique(rows[is_entity_contributor == 0L, .(
    employee_name = contributor_name,
    employee_title = contributor_occupation,
    company = spending_report_company_labels(company),
    zipcode = contributor_zip,
    state = contributor_state
  )])
  data.table::setorder(
    employees,
    company,
    employee_name,
    zipcode,
    employee_title
  )
  employees[, employee_row := .I + 1L]
  employees
}

spending_employees_sheet <- function(connection, rows = NULL) {
  if (is.null(rows)) {
    rows <- spending_ordered_report_rows(connection)
  }
  employees <- spending_employee_rows(rows)
  rbind(
    data.frame(
      V1 = "Employee Name",
      V2 = "Employee Title",
      V3 = "Company",
      V4 = "Zipcode",
      V5 = "State",
      stringsAsFactors = FALSE
    ),
    data.frame(
      V1 = employees$employee_name,
      V2 = employees$employee_title,
      V3 = employees$company,
      V4 = employees$zipcode,
      V5 = employees$state,
      stringsAsFactors = FALSE
    )
  )
}

spending_cycle_label <- function(cycle) {
  values <- c(
    `2022` = "2021-2022",
    `2024` = "2023-2024",
    `2026` = "2025-2026"
  )
  key <- as.character(as.integer(cycle))
  label <- unname(values[key])
  if (length(label) != 1L || is.na(label)) {
    stop("Unsupported report cycle: ", cycle, call. = FALSE)
  }
  label
}

spending_unique_sheet_names <- function(names) {
  names <- enc2utf8(as.character(names))
  sanitize_one <- function(value) {
    value <- trim_text(value)
    for (illegal in c("[", "]", ":", "*", "?", "/", "\\")) {
      value <- gsub(illegal, " ", value, fixed = TRUE)
    }
    value <- trim_text(value)
    if (!nzchar(value)) value <- "Sheet"
    substr(value, 1L, 31L)
  }
  result <- character(length(names))
  used <- character()
  for (index in seq_along(names)) {
    base <- sanitize_one(names[[index]])
    candidate <- base
    suffix_number <- 1L
    while (toupper(candidate) %in% toupper(used)) {
      suffix_number <- suffix_number + 1L
      suffix <- paste0("~", suffix_number)
      candidate <- paste0(
        substr(base, 1L, max(1L, 31L - nchar(suffix))),
        suffix
      )
    }
    result[[index]] <- candidate
    used <- c(used, candidate)
  }
  result
}

spending_csz <- function(city, state, zip) {
  city <- trim_text(city)
  state <- trim_text(state)
  zip <- trim_text(zip)
  city_state <- ifelse(
    nzchar(city) & nzchar(state),
    paste0(city, ", ", state),
    paste0(city, state)
  )
  trim_text(ifelse(
    nzchar(city_state) & nzchar(zip),
    paste(city_state, zip),
    paste0(city_state, zip)
  ))
}

spending_raw_input_view <- function(rows) {
  dates <- normalize_reconcile_date(rows$contribution_receipt_date)
  amounts <- as.numeric(rows$contribution_receipt_amount)
  data.frame(
    V1 = c("Name", rows$contributor_name),
    V2 = c("Recipient", rows$committee_name),
    V3 = as.Date(c(NA_character_, format(dates, "%Y-%m-%d"))),
    V4 = c(NA_real_, amounts),
    V5 = c("CSZ", spending_csz(
      rows$contributor_city,
      rows$contributor_state,
      rows$contributor_zip
    )),
    V6 = c("Employer", rows$contributor_employer),
    V7 = c("Occupation", rows$contributor_occupation),
    V8 = c("Excluded Reason", trim_text(rows$excluded_reason)),
    V9 = c("Is Memo Leg", as.integer(rows$is_memo_leg)),
    V10 = c("Is Conduit Leg", as.integer(rows$is_conduit_leg)),
    V11 = c(
      "Superseded By Amendment",
      as.integer(rows$superseded_by_amendment)
    ),
    V12 = c(
      "Duplicate Filing Version",
      as.integer(rows$duplicate_filing_version)
    ),
    V13 = c("Duplicate Memo", as.integer(rows$duplicate_memo)),
    V14 = c("Company Match Status", trim_text(rows$company_match_status)),
    V15 = c("Company Match Reason", trim_text(rows$company_match_reason)),
    stringsAsFactors = FALSE
  )
}

spending_raw_input_views <- function(rows) {
  if (!nrow(rows)) {
    return(list(
      views = list(),
      raw_sheet_names = character(),
      fact_map = data.table::data.table(
        source_sub_id = character(),
        raw_input_sheet = character(),
        raw_input_row = integer()
      )
    ))
  }
  groups <- unique(data.table::data.table(
    company = rows$company,
    display_name = spending_report_company_labels(rows$company),
    cycle = as.integer(rows$two_year_transaction_period)
  ))
  data.table::setorder(groups, display_name, cycle, company)
  desired_names <- character()
  view_values <- list()
  view_rows <- list()
  map_groups <- list()
  for (group_index in seq_len(nrow(groups))) {
    group <- groups[group_index]
    index <- which(
      rows$company == group$company &
        as.integer(rows$two_year_transaction_period) == group$cycle
    )
    desired_names <- c(
      desired_names,
      paste(group$display_name, spending_cycle_label(group$cycle))
    )
    view_values[[length(view_values) + 1L]] <- spending_raw_input_view(rows[index])
    view_rows[[length(view_rows) + 1L]] <- index
    map_groups[[length(map_groups) + 1L]] <- data.table::data.table(
      source_sub_id = rows$sub_id[index],
      view_index = length(view_values),
      raw_input_row = seq_along(index) + 1L
    )
  }
  for (cycle in sort(unique(as.integer(rows$two_year_transaction_period)))) {
    index <- which(as.integer(rows$two_year_transaction_period) == cycle)
    desired_names <- c(
      desired_names,
      # Excel truncates sheet names at 31 chars, so the industry name cannot be
      # embedded here: "Property & Casualty Insurance Industry 2021-2022" would
      # truncate to the same prefix for every cycle and collide. The workbook is
      # already per-industry, so a generic rollup label is unambiguous. Crypto
      # keeps its original label so shipped crypto workbooks stay unchanged.
      paste(
        if (identical(MENTAT_INDUSTRY, "Crypto")) "Crypto Industry" else "Industry Total",
        spending_cycle_label(cycle)
      )
    )
    view_values[[length(view_values) + 1L]] <- spending_raw_input_view(rows[index])
    view_rows[[length(view_rows) + 1L]] <- index
  }
  fixed_before <- c("Stats (XXX Company)", "Employees (XXX Company)")
  all_names <- c(fixed_before, desired_names, "Sheet4")
  safe_names <- spending_unique_sheet_names(all_names)
  if (!identical(safe_names[seq_along(fixed_before)], fixed_before) ||
      !identical(tail(safe_names, 1L), "Sheet4")) {
    stop("Fixed Spending Report sheet names collided after sanitization.", call. = FALSE)
  }
  raw_names <- safe_names[seq.int(3L, length(safe_names) - 1L)]
  names(view_values) <- raw_names
  fact_map <- data.table::rbindlist(map_groups, use.names = TRUE)
  fact_map[, raw_input_sheet := raw_names[view_index]]
  fact_map[, view_index := NULL]
  data.table::setorder(fact_map, source_sub_id)
  list(
    views = view_values,
    raw_sheet_names = raw_names,
    fact_map = fact_map
  )
}

spending_title_role <- function(title, known_executive = FALSE) {
  if (isTRUE(known_executive)) {
    return("Chief")
  }
  value <- normalize_occupation(title)
  chief_pattern <- paste(
    "\\b(CHIEF (EXECUTIVE|FINANCIAL|OPERATING|TECHNOLOGY|INFORMATION|",
    "LEGAL|MARKETING|PEOPLE|PRODUCT|RISK|COMPLIANCE|REVENUE|STRATEGY|",
    "ACCOUNTING) OFFICER|CEO|CFO|COO|CTO|CIO|CLO|CMO|GENERAL COUNSEL|",
    "FOUNDER|OWNER|CHAIRMAN|CHAIRWOMAN|CHAIRPERSON)\\b",
    sep = ""
  )
  if (grepl(chief_pattern, value, perl = TRUE) ||
      (grepl("\\bPRESIDENT\\b", value, perl = TRUE) &&
       !grepl("\\bVICE PRESIDENT\\b", value, perl = TRUE))) {
    return("Chief")
  }
  if (grepl(
    "\\b(VICE PRESIDENT|DIRECTOR|PARTNER|HEAD|MANAGER|MANAGEMENT)\\b",
    value,
    perl = TRUE
  )) {
    return("Middle Management")
  }
  "Employee"
}

spending_contributor_tier <- function(
  title,
  known_executive = FALSE,
  is_entity_contributor = FALSE
) {
  if (isTRUE(as.logical(is_entity_contributor))) {
    return("Corporate")
  }
  spending_title_role(title, known_executive)
}

spending_contributor_tiers <- function(
  title,
  known_executive = FALSE,
  is_entity_contributor = FALSE
) {
  count <- max(
    length(title),
    length(known_executive),
    length(is_entity_contributor)
  )
  if (!count) return(character())
  title <- rep_len(title, count)
  known_executive <- rep_len(as.logical(known_executive), count)
  is_entity_contributor <- rep_len(
    as.logical(is_entity_contributor),
    count
  )
  known_executive <- !is.na(known_executive) & known_executive
  is_entity_contributor <-
    !is.na(is_entity_contributor) & is_entity_contributor

  result <- rep("Employee", count)
  result[is_entity_contributor] <- "Corporate"
  person_indices <- which(!is_entity_contributor)
  if (!length(person_indices)) return(result)
  executive_indices <- person_indices[known_executive[person_indices]]
  result[executive_indices] <- "Chief"

  title_indices <- person_indices[!known_executive[person_indices]]
  if (!length(title_indices)) return(result)
  values <- normalize_occupation(title[title_indices])
  chief_pattern <- paste(
    "\\b(CHIEF (EXECUTIVE|FINANCIAL|OPERATING|TECHNOLOGY|INFORMATION|",
    "LEGAL|MARKETING|PEOPLE|PRODUCT|RISK|COMPLIANCE|REVENUE|STRATEGY|",
    "ACCOUNTING) OFFICER|CEO|CFO|COO|CTO|CIO|CLO|CMO|GENERAL COUNSEL|",
    "FOUNDER|OWNER|CHAIRMAN|CHAIRWOMAN|CHAIRPERSON)\\b",
    sep = ""
  )
  is_chief <- grepl(chief_pattern, values, perl = TRUE) |
    (
      grepl("\\bPRESIDENT\\b", values, perl = TRUE) &
        !grepl("\\bVICE PRESIDENT\\b", values, perl = TRUE)
    )
  is_middle <- !is_chief & grepl(
    "\\b(VICE PRESIDENT|DIRECTOR|PARTNER|HEAD|MANAGER|MANAGEMENT)\\b",
    values,
    perl = TRUE
  )
  result[title_indices[is_chief]] <- "Chief"
  result[title_indices[is_middle]] <- "Middle Management"
  result
}

spending_company_pac_match <- function(
  company,
  committee_name,
  company_variants
) {
  committee_key <- normalize_variant_key(committee_name)
  if (!nzchar(committee_key) ||
      !grepl("\\b(PAC|POLITICAL ACTION COMMITTEE)\\b", committee_key, perl = TRUE)) {
    return(FALSE)
  }
  variants <- company_variants[[company]] %||% company
  keys <- unique(normalize_variant_key(c(company, variants)))
  keys <- keys[nchar(keys) >= 4L]
  any(vapply(
    keys,
    function(key) grepl(key, committee_key, fixed = TRUE),
    logical(1L)
  ))
}

spending_first_lookup_match <- function(
  lookup,
  row_cycle,
  row_committee_id,
  row_committee_key,
  use_cycle = TRUE
) {
  row_count <- length(row_committee_id)
  if (!nrow(lookup) || !row_count) {
    return(rep(NA_integer_, row_count))
  }
  lookup_order <- seq_len(nrow(lookup))
  lookup_cycle <- if (isTRUE(use_cycle)) {
    as.integer(lookup$cycle)
  } else {
    rep(0L, nrow(lookup))
  }
  query_cycle <- if (isTRUE(use_cycle)) {
    as.integer(row_cycle)
  } else {
    rep(0L, row_count)
  }

  lookup_id_key <- paste(
    lookup_cycle,
    lookup$committee_id,
    sep = "\r"
  )
  lookup_name_key <- paste(
    lookup_cycle,
    lookup$group_key,
    sep = "\r"
  )
  first_id <- which(
    nzchar(lookup$committee_id) & !duplicated(lookup_id_key)
  )
  first_name <- which(
    nzchar(lookup$group_key) & !duplicated(lookup_name_key)
  )

  id_match <- rep(NA_integer_, row_count)
  valid_id <- !is.na(query_cycle) & nzchar(row_committee_id)
  if (length(first_id) && any(valid_id)) {
    positions <- match(
      paste(
        query_cycle[valid_id],
        row_committee_id[valid_id],
        sep = "\r"
      ),
      lookup_id_key[first_id]
    )
    found <- !is.na(positions)
    id_indices <- which(valid_id)
    id_match[id_indices[found]] <- lookup_order[
      first_id[positions[found]]
    ]
  }

  name_match <- rep(NA_integer_, row_count)
  valid_name <- !is.na(query_cycle) & nzchar(row_committee_key)
  if (length(first_name) && any(valid_name)) {
    positions <- match(
      paste(
        query_cycle[valid_name],
        row_committee_key[valid_name],
        sep = "\r"
      ),
      lookup_name_key[first_name]
    )
    found <- !is.na(positions)
    name_indices <- which(valid_name)
    name_match[name_indices[found]] <- lookup_order[
      first_name[positions[found]]
    ]
  }

  ifelse(
    is.na(id_match),
    name_match,
    ifelse(is.na(name_match), id_match, pmin(id_match, name_match))
  )
}

spending_recipient_classification <- function(connection, rows, lookups = NULL) {
  require_package("data.table")
  if (!nrow(rows)) {
    return(data.table::data.table(
      recipient_category = character(),
      party = character(),
      category_source = character(),
      party_source = character()
    ))
  }
  lookups <- lookups %||% spending_report_lookups(connection)
  categories <- data.table::copy(lookups$committee_master$categories)
  trade <- data.table::copy(lookups$committee_master$trade)
  leadership <- data.table::copy(lookups$committee_master$leadership)
  companies <- lookups$companies
  company_variants <- split(companies$variant, companies$company)
  categories[, group_key := normalize_variant_key(group_name)]
  categories[, committee_id := toupper(trim_text(committee_id))]
  trade[, group_key := normalize_variant_key(association_name)]
  trade[, committee_id := toupper(trim_text(committee_id))]
  leadership[, group_key := normalize_variant_key(pac_name)]
  leadership[, committee_id := toupper(trim_text(committee_id))]

  row_count <- nrow(rows)
  row_cycle <- as.integer(rows$two_year_transaction_period)
  row_committee_id <- toupper(trim_text(rows$committee_id))
  committee_name <- trim_text(rows$committee_name)
  row_committee_key <- normalize_variant_key(committee_name)
  category_match <- spending_first_lookup_match(
    categories,
    row_cycle,
    row_committee_id,
    row_committee_key
  )
  leadership_match <- spending_first_lookup_match(
    leadership,
    row_cycle,
    row_committee_id,
    row_committee_key
  )
  trade_match <- spending_first_lookup_match(
    trade,
    row_cycle,
    row_committee_id,
    row_committee_key,
    use_cycle = FALSE
  )

  category <- rep("Unclassified", row_count)
  party <- rep("", row_count)
  category_source <- rep("unclassified", row_count)
  party_source <- rep("", row_count)

  registered_conduit <- row_committee_id %in%
    c("C00401224", "C00694323") |
    row_committee_key %in% c("ACTBLUE", "WINRED")
  # ActBlue and WinRed are registered hybrid committees, but a receipt routed
  # through them is earmarked money passed on to the committee named on the
  # row, not giving to a Carey and not independent-expenditure-only Super PAC
  # giving. They are filed under their own Conduit category, ahead of every
  # other rule. Same IDs and name as app/evidence_csv.py EARMARK_CONDUITS and
  # CONDUIT, which relabel the same two committees when the console reads an
  # older book. Only the label moves: the receipt still counts once, to this
  # row's recipient, exactly as before.
  category[registered_conduit] <- "Conduit"
  category_source[registered_conduit] <-
    "fec:registered_conduit:earmark_platform"

  category_rows <- which(!registered_conduit & !is.na(category_match))
  if (length(category_rows)) {
    lookup_rows <- category_match[category_rows]
    category[category_rows] <- categories$category[lookup_rows]
    matched_by_id <- nzchar(row_committee_id[category_rows]) &
      categories$committee_id[lookup_rows] ==
        row_committee_id[category_rows]
    category_source[category_rows] <- ifelse(
      matched_by_id,
      "categories:committee_id",
      "categories:exact_name"
    )
    provisional <- !is.na(categories$provisional[lookup_rows]) &
      as.integer(categories$provisional[lookup_rows]) == 1L
    category_source[category_rows[provisional]] <- paste0(
      category_source[category_rows[provisional]],
      ":provisional_2024_carry_forward"
    )
    party_rows <- category_rows[
      categories$lean[lookup_rows] %in% c("R", "D")
    ]
    if (length(party_rows)) {
      party[party_rows] <- categories$lean[
        category_match[party_rows]
      ]
      party_source[party_rows] <- category_source[party_rows]
    }
  }

  leadership_rows <- which(
    !registered_conduit &
      is.na(category_match) &
      !is.na(leadership_match)
  )
  if (length(leadership_rows)) {
    lookup_rows <- leadership_match[leadership_rows]
    category[leadership_rows] <- "Leadership PAC"
    matched_by_id <- nzchar(row_committee_id[leadership_rows]) &
      leadership$committee_id[lookup_rows] ==
        row_committee_id[leadership_rows]
    category_source[leadership_rows] <- ifelse(
      matched_by_id,
      "leadership:committee_id",
      "leadership:exact_name"
    )
    provisional <- !is.na(leadership$provisional[lookup_rows]) &
      as.integer(leadership$provisional[lookup_rows]) == 1L
    category_source[leadership_rows[provisional]] <- paste0(
      category_source[leadership_rows[provisional]],
      ":provisional"
    )
    party_rows <- leadership_rows[
      leadership$lean[lookup_rows] %in% c("R", "D")
    ]
    if (length(party_rows)) {
      party[party_rows] <- leadership$lean[
        leadership_match[party_rows]
      ]
      party_source[party_rows] <- category_source[party_rows]
    }
  }

  pac_candidates <- which(
    !registered_conduit &
      is.na(category_match) &
      is.na(leadership_match)
  )
  company_pac <- rep(FALSE, row_count)
  if (length(pac_candidates)) {
    pairs <- unique(data.table::data.table(
      company = as.character(rows$company[pac_candidates]),
      committee_name = committee_name[pac_candidates]
    ))
    pairs[, is_company_pac := vapply(seq_len(.N), function(index) {
      spending_company_pac_match(
        company[[index]],
        committee_name[[index]],
        company_variants
      )
    }, logical(1L))]
    pair_keys <- paste0(
      nchar(pairs$company),
      ":",
      pairs$company,
      "\034",
      nchar(pairs$committee_name),
      ":",
      pairs$committee_name
    )
    row_keys <- paste0(
      nchar(rows$company[pac_candidates]),
      ":",
      rows$company[pac_candidates],
      "\034",
      nchar(committee_name[pac_candidates]),
      ":",
      committee_name[pac_candidates]
    )
    company_pac[pac_candidates] <- pairs$is_company_pac[
      match(row_keys, pair_keys)
    ]
  }
  company_pac_rows <- which(company_pac)
  category[company_pac_rows] <- "Company PAC"
  category_source[company_pac_rows] <-
    "company_pac:company_alias_and_pac"

  trade_rows <- which(
    !registered_conduit &
      is.na(category_match) &
      is.na(leadership_match) &
      !company_pac &
      !is.na(trade_match)
  )
  if (length(trade_rows)) {
    lookup_rows <- trade_match[trade_rows]
    category[trade_rows] <- "Trade Association"
    matched_by_id <- nzchar(row_committee_id[trade_rows]) &
      trade$committee_id[lookup_rows] == row_committee_id[trade_rows]
    category_source[trade_rows] <- ifelse(
      matched_by_id,
      "trade:committee_id",
      "trade:exact_name"
    )
  }

  fallback_rows <- which(
    !registered_conduit &
      is.na(category_match) &
      is.na(leadership_match) &
      !company_pac &
      is.na(trade_match)
  )
  if (length(fallback_rows)) {
    committee_type <- toupper(trim_text(
      rows$recipient_committee_type[fallback_rows]
    ))
    designation <- toupper(trim_text(
      rows$recipient_committee_designation[fallback_rows]
    ))
    fallback_category <- rep("Unclassified", length(fallback_rows))
    fallback_source <- rep("unclassified", length(fallback_rows))
    assign_fallback <- function(condition, value, source) {
      indices <- which(
        fallback_category == "Unclassified" & condition
      )
      if (length(indices)) {
        fallback_category[indices] <<- value
        fallback_source[indices] <<- source
      }
      invisible(NULL)
    }
    assign_fallback(
      designation == "J",
      "JFC",
      "fec:joint_fundraising_designation"
    )
    assign_fallback(
      designation == "D",
      "Leadership PAC",
      "fec:leadership_designation"
    )
    assign_fallback(
      committee_type %in% c("X", "Y", "Z", "PARTY"),
      "Party Committee",
      "fec:party_committee_type"
    )
    assign_fallback(
      committee_type %in% c("O", "U"),
      "Super PAC",
      "fec:independent_expenditure_committee_type"
    )
    assign_fallback(
      committee_type %in% c("V", "W"),
      "Carey",
      "fec:hybrid_committee_type"
    )
    assign_fallback(
      committee_type %in%
        c("H", "S", "P", "HOUSE", "SENATE", "PRESIDENTIAL") |
        designation %in% c("A", "P"),
      "Candidate",
      "fec:committee_type_or_designation"
    )
    category[fallback_rows] <- fallback_category
    category_source[fallback_rows] <- fallback_source
  }

  leadership_party_rows <- which(
    !nzchar(party) &
      !is.na(leadership_match) &
      leadership$lean[leadership_match] %in% c("R", "D")
  )
  if (length(leadership_party_rows)) {
    lookup_rows <- leadership_match[leadership_party_rows]
    party[leadership_party_rows] <- leadership$lean[lookup_rows]
    matched_by_id <- nzchar(row_committee_id[leadership_party_rows]) &
      leadership$committee_id[lookup_rows] ==
        row_committee_id[leadership_party_rows]
    party_source[leadership_party_rows] <- ifelse(
      matched_by_id,
      "leadership:committee_id",
      "leadership:exact_name"
    )
  }

  data.table::data.table(
    recipient_category = category,
    party,
    category_source,
    party_source
  )
}

spending_assert_entity_tiers <- function(facts) {
  if (!nrow(facts)) return(invisible(TRUE))
  entity_wrong <- facts$is_entity_contributor == 1L &
    facts$employee_tier != "Corporate"
  person_wrong <- facts$is_entity_contributor == 0L &
    facts$employee_tier == "Corporate"
  if (any(entity_wrong) || any(person_wrong)) {
    stop(
      "Entity-contributor invariant failed: corporate and person tiers overlap.",
      call. = FALSE
    )
  }
  invisible(TRUE)
}

spending_assert_category_reconciliation <- function(facts, tolerance = 1e-7) {
  if (!nrow(facts)) return(invisible(TRUE))
  giving <- facts[record_type %in% c("CONTRIBUTION", "CORPORATE")]
  if (!nrow(giving)) return(invisible(TRUE))
  groups <- spending_stats_category_groups()
  category_values <- unlist(groups, use.names = FALSE)
  if (anyDuplicated(category_values)) {
    stop("Stats recipient-category groups overlap.", call. = FALSE)
  }
  supplementary <- spending_supplementary_categories()
  known_categories <- c(category_values, supplementary)
  unknown <- sort(unique(facts$recipient_category[
    nzchar(facts$recipient_category) &
      !facts$recipient_category %in% known_categories
  ]))
  if (length(unknown)) {
    stop(
      "Recipient categories have no Stats or supplementary destination: ",
      paste(unknown, collapse = ", "),
      call. = FALSE
    )
  }
  supplementary_wrong <- facts$recipient_category %in% supplementary &
    facts$record_type != "EXCLUDED_527_501C"
  if (any(supplementary_wrong)) {
    stop(
      "Supplementary 527/501c rows must be excluded from the Stats formula range.",
      call. = FALSE
    )
  }
  category_destinations <- rep(names(groups), lengths(groups))
  category_group <- category_destinations[
    match(giving$recipient_category, category_values)
  ]
  if (anyNA(category_group)) {
    missing <- sort(unique(giving$recipient_category[is.na(category_group)]))
    stop(
      "Stats category columns omit reported categories: ",
      paste(missing, collapse = ", "),
      call. = FALSE
    )
  }
  giving[, category_group := category_group]
  totals <- giving[, .(total = sum(amount, na.rm = TRUE)), by = .(year, company)]
  categorized <- giving[, .(
    categorized = sum(amount, na.rm = TRUE)
  ), by = .(year, company, category_group)][, .(
    categorized = sum(categorized, na.rm = TRUE)
  ), by = .(year, company)]
  check <- categorized[totals, on = c("year", "company")]
  scale <- pmax(1, abs(check$total))
  if (any(abs(check$categorized - check$total) > tolerance * scale)) {
    stop(
      "Stats category columns do not reconcile to Total Giving.",
      call. = FALSE
    )
  }
  invisible(TRUE)
}

spending_calculation_support <- function(
  connection,
  rows,
  employees,
  raw_map = NULL,
  memo_duplicate_count = 0L,
  lookups = NULL
) {
  columns <- c(
    "record_type", "year", "company", "employee_tier",
    "recipient_category", "party", "amount", "recipient",
    "contributor_name", "raw_input_row", "employee_row", "source_sub_id",
    "category_source", "party_source", "is_entity_contributor",
    "raw_input_sheet"
  )
  if (!nrow(rows)) {
    facts <- data.table::data.table(
      record_type = character(),
      year = integer(),
      company = character(),
      employee_tier = character(),
      recipient_category = character(),
      party = character(),
      amount = numeric(),
      recipient = character(),
      contributor_name = character(),
      raw_input_row = integer(),
      employee_row = integer(),
      source_sub_id = character(),
      category_source = character(),
      party_source = character(),
      is_entity_contributor = integer(),
      raw_input_sheet = character()
    )
  } else {
    lookups <- lookups %||% spending_report_lookups(connection)
    executives <- lookups$executives
    executive_keys <- unique(paste(
      spending_report_company_labels(executives$company),
      vapply(executives$executive_name, person_identity_key, character(1L)),
      sep = "\r"
    ))
    company_labels <- spending_report_company_labels(rows$company)
    row_keys <- paste(
      company_labels,
      vapply(rows$contributor_name, person_identity_key, character(1L)),
      sep = "\r"
    )
    roles <- spending_contributor_tiers(
      rows$contributor_occupation,
      row_keys %in% executive_keys,
      rows$is_entity_contributor == 1L
    )
    classifications <- spending_recipient_classification(
      connection,
      rows,
      lookups = lookups
    )
    employee_keys <- paste(
      employees$employee_name,
      employees$employee_title,
      employees$company,
      employees$zipcode,
      employees$state,
      sep = "\r"
    )
    row_employee_keys <- paste(
      rows$contributor_name,
      rows$contributor_occupation,
      company_labels,
      rows$contributor_zip,
      rows$contributor_state,
      sep = "\r"
    )
    employee_match <- match(row_employee_keys, employee_keys)
    if (anyNA(employee_match[rows$is_entity_contributor == 0L])) {
      stop(
        "Spending Report cross-sheet employee reference could not be resolved",
        call. = FALSE
      )
    }
    if (is.null(raw_map)) {
      raw_input_rows <- seq_len(nrow(rows))
      raw_input_sheets <- rep("", nrow(rows))
    } else {
      raw_match <- match(rows$sub_id, raw_map$source_sub_id)
      if (anyNA(raw_match)) {
        stop(
          "Spending Report raw-input sheet reference could not be resolved.",
          call. = FALSE
        )
      }
      raw_input_rows <- as.integer(raw_map$raw_input_row[raw_match])
      raw_input_sheets <- raw_map$raw_input_sheet[raw_match]
    }
    fact_record_type <- rows$record_type
    fact_record_type[
      classifications$recipient_category %in% c("527", "501c")
    ] <- "EXCLUDED_527_501C"
    facts <- data.table::data.table(
      record_type = fact_record_type,
      year = as.integer(format(
        normalize_reconcile_date(rows$contribution_receipt_date),
        "%Y"
      )),
      company = company_labels,
      employee_tier = roles,
      recipient_category = classifications$recipient_category,
      party = classifications$party,
      amount = as.numeric(rows$contribution_receipt_amount),
      recipient = rows$committee_name,
      contributor_name = rows$contributor_name,
      raw_input_row = raw_input_rows,
      employee_row = ifelse(
        rows$is_entity_contributor == 1L,
        NA_integer_,
        employees$employee_row[employee_match]
      ),
      source_sub_id = rows$sub_id,
      category_source = classifications$category_source,
      party_source = classifications$party_source,
      is_entity_contributor = as.integer(rows$is_entity_contributor),
      raw_input_sheet = raw_input_sheets
    )
    spending_assert_entity_tiers(facts)
    spending_assert_category_reconciliation(facts)
  }
  notes <- c(
    "Percent of Total Industry Spending = company total / the ten-company industry total for the same year.",
    "Partisan Split = R dollars / (R + D dollars); neutral and unclassified dollars are excluded; blank when R + D is zero.",
    "Category share = category dollars / the relevant company or employee-tier total.",
    "Chief/Middle/Employee role share = role dollars / person giving for the same company and year.",
    "Corporate Giving is reported separately and never receives a person tier.",
    "Leadership PAC is grouped with Company PAC; Party Committee, JFC, and Unclassified are grouped with Candidate so category columns reconcile to Total Giving.",
    "Registered 527 and 501c recipients remain classified in Sheet4 but use EXCLUDED_527_501C and are excluded from reported totals.",
    "Every FEC row remains in the raw extract; memo, conduit, amendment, filing-version, refund, and redesignation flags never cull raw rows.",
    paste0(
      "R8 memo-X dedup rule: a memo-X row is excluded from analysis only when a non-memo row has the same company, committee_id, date, amount, and contributor_name; ",
      as.integer(memo_duplicate_count), " row(s) were flagged. Different-recipient earmark legs count."
    ),
    "Superseded amendment and duplicate filing-version rows are flagged but are not filtered from analysis totals.",
    "Average = arithmetic mean of the ten company rows; Change = current average minus prior-year average in the same units.",
    "% Left = Company PAC cash on hand / Company PAC total receipts.",
    "Company PAC allocation share = category disbursements / all Company PAC disbursements.",
    "Refunds retain their negative sign; only accepted, countable contribution rows enter calculations.",
    "The second category column in each role block is headed Carey and calculates Carey; the template once repeated the Super PAC header there.",
    "Conduit holds receipts to the registered earmark conduits ActBlue and WinRed. It has its own column group directly after Carey in the company block, in each role block and in the Company PAC allocation; Carey no longer includes them, and every dollar stays in exactly one category column.",
    "Company PAC cash, receipts, and disbursement cells stay blank until Phase 3 supplies those fact records."
  )
  methods <- data.table::data.table(
    record_type = "METHOD",
    year = NA_integer_,
    company = "",
    employee_tier = "",
    recipient_category = "",
    party = "",
    amount = NA_real_,
    recipient = notes,
    contributor_name = "",
    raw_input_row = NA_integer_,
    employee_row = NA_integer_,
    source_sub_id = "",
    category_source = "",
    party_source = "",
    is_entity_contributor = NA_integer_,
    raw_input_sheet = ""
  )
  header <- data.table::data.table(
    record_type = "Record Type",
    year = NA_integer_,
    company = "Company",
    employee_tier = "Employee Tier",
    recipient_category = "Recipient Category",
    party = "Party",
    amount = NA_real_,
    recipient = "Recipient / Formula Definition",
    contributor_name = "Contributor Name",
    raw_input_row = NA_integer_,
    employee_row = NA_integer_,
    source_sub_id = "Source Sub ID",
    category_source = "Category Source",
    party_source = "Party Source",
    is_entity_contributor = NA_integer_,
    raw_input_sheet = ""
  )
  support <- data.table::rbindlist(
    list(header[, ..columns], facts[, ..columns], methods[, ..columns]),
    use.names = TRUE,
    fill = TRUE
  )
  names(support) <- c(
    "Record Type", "Year", "Company", "Employee Tier",
    "Recipient Category", "Party", "Amount", "Recipient / Formula Definition",
    "Contributor Name", "Raw Input Row", "Employee Row", "Source Sub ID",
    "Category Source", "Party Source", "Is Entity Contributor",
    "Raw Input Sheet"
  )
  as.data.frame(support, stringsAsFactors = FALSE)
}

write_spending_report <- function(
  connection,
  data_root = mentat_default_data_root(),
  versioning_applied = FALSE,
  lookups = NULL,
  raw_rows = NULL,
  rows = NULL
) {
  paths <- mentat_bootstrap_dirs(data_root)
  target <- file.path(paths$reports, "spending_report.xlsx")
  if (is.null(lookups)) lookups <- spending_report_lookups(connection)
  if (is.null(raw_rows)) {
    raw_rows <- spending_raw_report_rows(
      connection,
      versioning_applied = versioning_applied
    )
  }
  if (is.null(rows)) {
    rows <- spending_ordered_report_rows(
      connection,
      lookups = lookups,
      raw_rows = raw_rows
    )
  }
  unresolved <- fec_receipt_countable(raw_rows) &
    fec_receipt_role_unresolved(fec_receipt_role(raw_rows))
  if (any(unresolved)) {
    atomic_write_csv(raw_rows[unresolved], file.path(paths$reports, "receipt_role_review.csv"))
  }
  fec_assert_receipt_evidence(raw_rows)
  if (nrow(raw_rows)) {
    raw_rows[, sort_date := as.integer(normalize_reconcile_date(
      contribution_receipt_date
    ))]
    data.table::setorder(raw_rows, -sort_date, contributor_name, sub_id)
  }
  memo_duplicate_count <- sum(raw_rows$duplicate_memo, na.rm = TRUE)
  employees <- spending_employee_rows(rows)
  raw_inputs <- spending_raw_input_views(raw_rows)
  support <- spending_calculation_support(
    connection,
    rows,
    employees,
    raw_map = raw_inputs$fact_map,
    memo_duplicate_count = memo_duplicate_count,
    lookups = lookups
  )
  last_fact_row <- max(2L, nrow(rows) + 1L)
  report_companies <- DBI::dbGetQuery(
    connection,
    paste(
      "SELECT DISTINCT company FROM companies",
      paste0("WHERE ", report_industry_filter(), " ORDER BY company")
    )
  )$company
  stats_cached_values <- spending_stats_cached_values(
    support,
    companies = report_companies
  )
  sheets <- c(
    list(
      `Stats (XXX Company)` = spending_stats_sheet(
        last_fact_row,
        memo_duplicate_count,
        companies = report_companies
      ),
      `Employees (XXX Company)` = spending_employees_sheet(connection, rows)
    ),
    raw_inputs$views,
    list(Sheet4 = support)
  )
  atomic_write_xlsx(
    sheets,
    target,
    col_names = FALSE
  )
  spending_restore_header_literals(
    target,
    sheet_names = names(sheets),
    raw_sheet_names = raw_inputs$raw_sheet_names,
    stats_cached_values = stats_cached_values
  )
  invisible(target)
}

reliability_markdown_table <- function(data) {
  data <- data.table::as.data.table(data)
  if (!nrow(data)) {
    return("_No data._")
  }
  columns <- names(data)
  header <- paste0("| ", paste(columns, collapse = " | "), " |")
  separator <- paste0("| ", paste(rep("---", length(columns)), collapse = " | "), " |")
  rows <- apply(as.data.frame(data), 1L, function(row) {
    row[is.na(row)] <- ""
    row <- gsub("\\|", "\\\\|", as.character(row), perl = TRUE)
    paste0("| ", paste(row, collapse = " | "), " |")
  })
  c(header, separator, rows)
}

parse_run_timestamp <- function(x) {
  value <- trim_text(x)
  parsed <- rep(NA_real_, length(value))
  formats <- c(
    "%Y-%m-%dT%H:%M:%OS%z",
    "%Y-%m-%d %H:%M:%OS%z",
    "%Y-%m-%dT%H:%M:%OS",
    "%Y-%m-%d %H:%M:%OS"
  )
  for (format in formats) {
    remaining <- which(is.na(parsed) & nzchar(value))
    if (!length(remaining)) {
      break
    }
    candidate <- as.POSIXct(value[remaining], format = format, tz = "UTC")
    good <- !is.na(candidate)
    parsed[remaining[good]] <- as.numeric(candidate[good])
  }
  as.POSIXct(parsed, origin = "1970-01-01", tz = "UTC")
}

reliability_report_lines <- function(connection, amendment_summary = NULL) {
  require_package("data.table")
  if (is.null(amendment_summary)) {
    amendment_summary <- apply_amendment_versioning(
      connection,
      print_summary = FALSE
    )
  }
  amendment_summary <- data.table::as.data.table(amendment_summary)
  if (nrow(amendment_summary)) {
    amendment_summary[, company := spending_report_company_labels(company)]
  }
  progress <- data.table::as.data.table(DBI::dbGetQuery(
    connection,
    paste(
      "SELECT company, cycle, variant_set, last_page, records_total, updated_at",
      "FROM scrape_progress ORDER BY company, cycle, variant_set"
    )
  ))
  company_cycle <- if (nrow(progress)) {
    progress[, .(
      variant_sets = .N,
      pages_walked = sum(last_page + 1L),
      rows_reported_by_fec = sum(records_total),
      last_checkpoint = max(updated_at)
    ), by = .(company, cycle)]
  } else {
    data.table::data.table(
      company = character(),
      cycle = integer(),
      variant_sets = integer(),
      pages_walked = integer(),
      rows_reported_by_fec = integer(),
      last_checkpoint = character()
    )
  }
  if (nrow(company_cycle)) {
    company_cycle[, company := spending_report_company_labels(company)]
  }
  canaries <- data.table::as.data.table(DBI::dbGetQuery(
    connection,
    paste(
      "SELECT check_name, COUNT(*) AS checks,",
      "SUM(CASE WHEN passed = 1 THEN 1 ELSE 0 END) AS passed,",
      "SUM(CASE WHEN passed = 0 THEN 1 ELSE 0 END) AS failed",
      "FROM canary_log GROUP BY check_name ORDER BY check_name"
    )
  ))
  run_log <- data.table::as.data.table(DBI::dbGetQuery(
    connection,
    "SELECT * FROM run_log ORDER BY started_at, cycle"
  ))
  run_metrics <- data.table::as.data.table(DBI::dbGetQuery(
    connection,
    paste(
      "SELECT run_id, phase, wall_clock_seconds, rows_processed,",
      "request_count, rows_per_second, detail",
      "FROM run_metrics ORDER BY started_at, run_id, phase"
    )
  ))
  retry_count <- if (nrow(run_log)) sum(run_log$retry_count, na.rm = TRUE) else 0L
  determinism <- unique(na.omit(run_log$determinism_result))
  determinism_text <- if (length(determinism)) {
    paste(determinism, collapse = ", ")
  } else {
    "not run"
  }
  wall_clock <- if (nrow(run_log)) {
    completed <- run_log[nzchar(trim_text(ended_at))]
    if (nrow(completed)) {
      completed[, seconds := as.numeric(
        parse_run_timestamp(ended_at) - parse_run_timestamp(started_at),
        units = "secs"
      )]
      completed[, .(
        cycles = paste(sort(unique(cycle)), collapse = ","),
        wall_clock_seconds = {
          valid_seconds <- seconds[is.finite(seconds)]
          if (length(valid_seconds)) {
            round(max(valid_seconds), 3L)
          } else {
            NA_real_
          }
        }
      ), by = run_id]
    } else {
      data.table::data.table(
        run_id = character(), cycles = character(), wall_clock_seconds = numeric()
      )
    }
  } else {
    data.table::data.table(
      run_id = character(), cycles = character(), wall_clock_seconds = numeric()
    )
  }
  contribution_summary <- data.table::as.data.table(DBI::dbGetQuery(
    connection,
    paste(
      "SELECT COUNT(*) AS total_rows,",
      "(SELECT COUNT(*) FROM attribution_conflicts) AS attribution_conflicts,",
      "SUM(is_conduit_leg) AS conduit_legs,",
      "SUM(is_memo_leg) AS memo_legs,",
      "SUM(is_passthrough_leg) AS passthrough_legs,",
      "SUM(possible_amendment) AS possible_amendments,",
      "SUM(superseded_by_amendment) AS superseded_amendment_rows,",
      "SUM(duplicate_filing_version) AS duplicate_filing_version_rows,",
      "SUM(duplicate_memo) AS duplicate_memo_rows,",
      "SUM(company_match_status='review') AS company_match_review_rows,",
      "SUM(company_match_status='rejected') AS company_match_rejected_rows,",
      "SUM(CASE WHEN company_match_status='accepted' AND",
      "duplicate_memo=0 AND",
      "(excluded_reason IS NULL OR excluded_reason='') THEN 1 ELSE 0 END)",
      "AS countable_rows,",
      "MAX(load_date) AS max_load_date",
      "FROM contributions"
    )
  ))
  for (column in c(
    "total_rows", "attribution_conflicts", "conduit_legs", "memo_legs", "passthrough_legs",
    "possible_amendments", "company_match_review_rows",
    "superseded_amendment_rows", "duplicate_filing_version_rows",
    "duplicate_memo_rows", "company_match_rejected_rows", "countable_rows"
  )) {
    if (is.na(contribution_summary[[column]][[1L]])) {
      contribution_summary[[column]][[1L]] <- 0L
    }
  }
  corporate_summary <- data.table::as.data.table(DBI::dbGetQuery(
    connection,
    paste(
      "SELECT COUNT(*) AS total_rows,",
      "SUM(company_match_status='accepted') AS accepted_rows,",
      "SUM(company_match_status='review') AS review_rows,",
      "SUM(company_match_status='rejected') AS rejected_rows,",
      "SUM(CASE WHEN company_match_status='accepted' AND duplicate_memo=0",
      "AND (excluded_reason IS NULL OR excluded_reason='') THEN 1 ELSE 0 END)",
      "AS countable_rows,",
      "SUM(CASE WHEN company_match_status='accepted' AND duplicate_memo=0",
      "AND (excluded_reason IS NULL OR excluded_reason='')",
      "THEN contribution_receipt_amount ELSE 0 END) AS countable_dollars",
      "FROM corporate_giving"
    )
  ))
  for (column in c(
    "total_rows", "accepted_rows", "review_rows", "rejected_rows",
    "countable_rows", "countable_dollars"
  )) {
    if (is.na(corporate_summary[[column]][[1L]])) {
      corporate_summary[[column]][[1L]] <- 0
    }
  }
  # The old SQL predicates above describe raw collection flags. Override all
  # countability metrics with the exact report predicate and cross-table ID
  # handling, including receipt-role exclusions and refreshed memo evidence.
  qa_rows <- spending_raw_report_rows(connection, versioning_applied = TRUE)
  qa_keep <- fec_receipt_countable(qa_rows)
  qa_roles <- if (nrow(qa_rows)) qa_rows$receipt_role else character()
  for (table in c("contributions", "corporate_giving")) {
    selected <- qa_rows$source_table == table
    summary <- if (table == "contributions") contribution_summary else corporate_summary
    summary[, countable_rows := sum(qa_keep & selected)]
    summary[, countable_dollars := sum(qa_rows$contribution_receipt_amount[qa_keep & selected], na.rm = TRUE)]
    summary[, unresolved_receipt_rows := sum(qa_keep & selected & fec_receipt_role_unresolved(qa_roles))]
  }
  max_load <- contribution_summary$max_load_date[[1L]]
  if (is.na(max_load) || !nzchar(trim_text(max_load))) {
    max_load <- "not available"
  }
  pages_walked <- if (nrow(progress)) sum(progress$last_page + 1L) else 0L
  c(
    "# Mentat Collection Reliability Report",
    "",
    paste0("- Pages walked/checkpointed: ", pages_walked),
    paste0("- Retry count: ", retry_count),
    paste0("- Determinism spot-check (identical sub_id set): ", determinism_text),
    paste0("- Latest observed FEC row load_date: ", max_load),
    "- Collection completeness requires coverage evidence.",
    "",
    "## Rows and pages by company-cycle",
    "",
    reliability_markdown_table(company_cycle),
    "",
    "## Canary summary",
    "",
    reliability_markdown_table(canaries),
    "",
    "## Run wall-clock",
    "",
    reliability_markdown_table(wall_clock),
    "",
    "## API benchmark metrics",
    "",
    reliability_markdown_table(run_metrics),
    "",
    "## Contribution rows and leg flags",
    "",
    reliability_markdown_table(contribution_summary),
    "",
    "## Corporate rows and match coverage",
    "",
    reliability_markdown_table(corporate_summary),
    "",
    "## Amendment versioning by company",
    "",
    reliability_markdown_table(amendment_summary)
  )
}

write_reliability_report <- function(
  connection,
  data_root = mentat_default_data_root(),
  amendment_summary = NULL
) {
  paths <- mentat_bootstrap_dirs(data_root)
  target <- file.path(paths$reports, "reliability_report.md")
  atomic_write_lines(
    reliability_report_lines(connection, amendment_summary = amendment_summary),
    target
  )
  invisible(target)
}

write_identity_review_queue <- function(
  connection,
  data_root = mentat_default_data_root()
) {
  paths <- mentat_bootstrap_dirs(data_root)
  target <- file.path(paths$reports, "review_queue_identity.csv")
  queue <- data.table::as.data.table(DBI::dbGetQuery(
    connection,
    "SELECT * FROM review_queue_identity ORDER BY review_id"
  ))
  if (nrow(queue)) queue[, company := spending_report_company_labels(company)]
  atomic_write_csv(queue, target)
  invisible(target)
}

write_company_match_review_queue <- function(
  connection,
  data_root = mentat_default_data_root()
) {
  paths <- mentat_bootstrap_dirs(data_root)
  target <- file.path(paths$reports, "review_queue_company_match.csv")
  queue <- data.table::as.data.table(DBI::dbGetQuery(
    connection,
    paste(
      "SELECT source_table, match_value, company, SUM(rows) AS rows,",
      "SUM(dollar_total) AS dollar_total FROM (",
      "SELECT 'contributions' AS source_table,",
      "contributor_employer AS match_value, company, COUNT(*) AS rows,",
      "SUM(contribution_receipt_amount) AS dollar_total",
      "FROM contributions WHERE company_match_status = 'review'",
      "GROUP BY contributor_employer, company",
      "UNION ALL",
      "SELECT 'corporate_giving' AS source_table,",
      "contributor_name AS match_value, company, COUNT(*) AS rows,",
      "SUM(contribution_receipt_amount) AS dollar_total",
      "FROM corporate_giving WHERE company_match_status = 'review'",
      "GROUP BY contributor_name, company)",
      "GROUP BY source_table, match_value, company",
      "ORDER BY ABS(SUM(dollar_total)) DESC, company, match_value"
    )
  ))
  if (nrow(queue)) queue[, company := spending_report_company_labels(company)]
  queue[, decision := ""]
  atomic_write_csv(queue, target)
  invisible(target)
}

write_unclassified_recipients <- function(
  connection,
  data_root = mentat_default_data_root(),
  rows = NULL,
  lookups = NULL
) {
  paths <- mentat_bootstrap_dirs(data_root)
  target <- file.path(paths$reports, "unclassified_recipients.csv")
  if (is.null(rows)) rows <- spending_ordered_report_rows(connection)
  if (!nrow(rows)) {
    queue <- data.table::data.table(
      recipient = character(), committee_id = character(),
      rows = integer(), dollars = numeric()
    )
  } else {
    classification <- spending_recipient_classification(
      connection,
      rows,
      lookups = lookups
    )
    rows[, recipient_category := classification$recipient_category]
    queue <- rows[recipient_category == "Unclassified", .(
      rows = .N,
      dollars = sum(contribution_receipt_amount, na.rm = TRUE)
    ), by = .(
      recipient = committee_name,
      committee_id
    )]
    queue[, abs_dollars := abs(dollars)]
    data.table::setorder(queue, -abs_dollars, recipient, committee_id)
    queue[, abs_dollars := NULL]
  }
  atomic_write_csv(queue, target)
  invisible(target)
}

write_attribution_conflicts <- function(
  connection,
  data_root = mentat_default_data_root()
) {
  conflicts <- data.table::as.data.table(DBI::dbGetQuery(
    connection,
    paste(
      "SELECT sub_id, stored_company, incoming_company, incoming_phase, created_at",
      "FROM attribution_conflicts",
      "ORDER BY sub_id, stored_company, incoming_company, incoming_phase"
    )
  ))
  if (!nrow(conflicts)) {
    return(NULL)
  }
  paths <- mentat_bootstrap_dirs(data_root)
  conflicts[, stored_company := spending_report_company_labels(stored_company)]
  conflicts[, incoming_company := spending_report_company_labels(incoming_company)]
  target <- file.path(paths$reports, "attribution_conflicts.csv")
  atomic_write_csv(conflicts, target)
  target
}

bulk_audit_tables <- function(connection) {
  require_package("data.table")
  companies <- DBI::dbGetQuery(
    connection,
    paste(
      "SELECT DISTINCT company FROM companies",
      paste0("WHERE ", report_industry_filter(), " ORDER BY company")
    )
  )$company
  grid <- data.table::CJ(
    company = sort(unique(trim_text(companies)), method = "radix"),
    cycle = c(2022L, 2024L, 2026L),
    unique = TRUE
  )
  if (!DBI::dbExistsTable(connection, "bulk_candidates")) {
    candidates <- data.table::data.table(
      company = character(), cycle = integer(), sub_id = character(),
      contributor_name = character(), contributor_city = character(),
      contributor_state = character(), contributor_zip = character(),
      employer_string = character(), contributor_occupation = character(),
      committee_id = character(), amount = numeric(), phase = character()
    )
  } else {
    candidates <- data.table::as.data.table(DBI::dbGetQuery(
      connection,
      paste(
        "SELECT b.company, b.cycle, b.sub_id, b.contributor_name,",
        "b.contributor_city, b.contributor_state, b.contributor_zip,",
        "b.employer_string, b.contributor_occupation, b.committee_id, b.amount,",
        "c.phase FROM bulk_candidates b LEFT JOIN contributions c",
        "ON c.sub_id=b.sub_id AND c.company=b.company",
        "ORDER BY b.company, b.cycle, b.sub_id"
      )
    ))
  }
  if (nrow(candidates)) {
    candidates[, `:=`(
      captured = nzchar(trim_text(phase)),
      rescued_flag = trim_text(phase) == "P3_bulk_rescue"
    )]
    counts <- candidates[, .(
      bulk_candidate_rows = .N,
      api_captured = sum(captured & !rescued_flag),
      rescued = sum(rescued_flag),
      unreachable = sum(!captured)
    ), by = .(company, cycle)]
    unmatched <- candidates[captured == FALSE, .(
      company, cycle, sub_id, contributor_name, contributor_city,
      contributor_state, contributor_zip, employer_string,
      contributor_occupation, committee_id, amount
    )]
    data.table::setorder(unmatched, company, cycle, sub_id)
  } else {
    counts <- data.table::data.table(
      company = character(), cycle = integer(), bulk_candidate_rows = integer(),
      api_captured = integer(), rescued = integer(), unreachable = integer()
    )
    unmatched <- data.table::data.table(
      company = character(), cycle = integer(), sub_id = character(),
      contributor_name = character(), contributor_city = character(),
      contributor_state = character(), contributor_zip = character(),
      employer_string = character(), contributor_occupation = character(),
      committee_id = character(), amount = numeric()
    )
  }
  audit <- merge(grid, counts, by = c("company", "cycle"), all.x = TRUE, sort = FALSE)
  for (column in c("bulk_candidate_rows", "api_captured", "rescued", "unreachable")) {
    audit[is.na(get(column)), (column) := 0L]
    data.table::set(audit, j = column, value = as.integer(audit[[column]]))
  }
  data.table::setorder(audit, company, cycle)
  list(audit = audit[], unmatched = unmatched[])
}

write_bulk_audit_reports <- function(
  connection,
  data_root = mentat_default_data_root(),
  print_summary = TRUE
) {
  paths <- mentat_bootstrap_dirs(data_root)
  tables <- bulk_audit_tables(connection)
  audit_output <- data.table::copy(tables$audit)
  unmatched_output <- data.table::copy(tables$unmatched)
  if (nrow(audit_output)) {
    audit_output[, company := spending_report_company_labels(company)]
  }
  if (nrow(unmatched_output)) {
    unmatched_output[, company := spending_report_company_labels(company)]
  }
  audit_target <- file.path(paths$reports, "bulk_audit.csv")
  unmatched_target <- file.path(paths$reports, "bulk_audit_unmatched.csv")
  atomic_write_csv(audit_output, audit_target)
  atomic_write_csv(unmatched_output, unmatched_target)
  if (isTRUE(print_summary)) {
    cat("Bulk completeness audit (candidate / API / rescued / unreachable):\n")
    print(audit_output)
  }
  list(audit = audit_target, unmatched = unmatched_target, tables = tables)
}

write_reports <- function(
  connection,
  data_root = mentat_default_data_root(),
  retag = TRUE
) {
  report_started <- proc.time()[["elapsed"]]
  if (isTRUE(retag)) {
    retag_employer_company_matches(connection)
    retag_identity_company_matches(connection)
    retag_corporate_company_matches(connection)
  }
  amendment_summary <- apply_amendment_versioning(
    connection,
    print_summary = TRUE
  )
  resolve_recipient_register_committee_ids(connection, log = TRUE)
  bulk_audit <- write_bulk_audit_reports(connection, data_root, print_summary = TRUE)
  lookups <- spending_report_lookups(connection)
  raw_rows <- spending_raw_report_rows(
    connection,
    versioning_applied = TRUE
  )
  rows <- spending_ordered_report_rows(
    connection,
    lookups = lookups,
    raw_rows = raw_rows
  )
  reports <- list(
    spending_report = write_spending_report(
      connection,
      data_root,
      versioning_applied = TRUE,
      lookups = lookups,
      raw_rows = raw_rows,
      rows = rows
    ),
    reliability_report = write_reliability_report(
      connection,
      data_root,
      amendment_summary = amendment_summary
    ),
    identity_review_queue = write_identity_review_queue(connection, data_root),
    company_match_review_queue = write_company_match_review_queue(
      connection,
      data_root
    ),
    unclassified_recipients = write_unclassified_recipients(
      connection,
      data_root,
      rows = rows,
      lookups = lookups
    ),
    bulk_audit = bulk_audit$audit,
    bulk_audit_unmatched = bulk_audit$unmatched
  )
  attribution_conflicts <- write_attribution_conflicts(connection, data_root)
  if (!is.null(attribution_conflicts)) {
    reports$attribution_conflicts <- attribution_conflicts
  }
  report_seconds <- unname(proc.time()[["elapsed"]] - report_started)
  report_rows <- DBI::dbGetQuery(
    connection,
    paste(
      "SELECT",
      paste0("(SELECT COUNT(*) FROM contributions WHERE ", report_industry_filter(), ") +"),
      paste0("(SELECT COUNT(*) FROM corporate_giving WHERE ", report_industry_filter(), ") AS rows")
    )
  )$rows[[1L]]
  report_rate <- if (report_seconds > 0) report_rows / report_seconds else 0
  cat(sprintf(
    "Report generation benchmark: rows=%d wall=%.3fs rows/sec=%.2f\n",
    as.integer(report_rows),
    report_seconds,
    report_rate
  ))
  reports
}

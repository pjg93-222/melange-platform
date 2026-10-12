mentat_company_display_register <- function() {
  data.frame(
    canonical_name = c(
      "BINANCE US", "Foris Dax Inc", "Coinlist", "Tzero", "Krakken",
      "Coinbase Exchange", "Binance Holdings", "Coinbase", "Kraken",
      "Gemini", "Bakkt", "Bitnomial", "MIAXdx"
    ),
    display_name = c(
      "Binance", "Crypto.com", "CoinList", "tZero", "Kraken",
      "Coinbase", "Binance", "Coinbase", "Kraken",
      "Gemini", "Bakkt", "Bitnomial", "MIAXdx"
    ),
    source = "user_canonical_name_register",
    stringsAsFactors = FALSE
  )
}

mentat_company_display_name <- function(x) {
  values <- trim_text(x)
  register <- mentat_company_display_register()
  lookup <- stats::setNames(register$display_name, register$canonical_name)
  mapped <- unname(lookup[values])
  mapped[is.na(mapped)] <- values[is.na(mapped)]
  mapped
}

mentat_internal_company_name <- function(x) {
  values <- normalize_template_spelling(x)
  lookup <- c(
    "Coinbase Exchange" = "Coinbase",
    "Binance Holdings" = "BINANCE US",
    "Binance" = "BINANCE US",
    "Crypto.com" = "Foris Dax Inc",
    "CoinList" = "Coinlist",
    "tZero" = "Tzero",
    "Krakken" = "Kraken"
  )
  mapped <- unname(lookup[values])
  mapped[is.na(mapped)] <- values[is.na(mapped)]
  mapped
}

normalize_register_key <- function(x) {
  value <- as.character(x)
  trim_text(gsub("[^A-Z0-9]+", " ", toupper(enc2utf8(value)), perl = TRUE))
}

read_company_match_overrides <- function(path) {
  require_package("data.table")
  if (!file.exists(path)) {
    stop("Company-match override file is missing: ", path, call. = FALSE)
  }
  overrides <- data.table::fread(
    path,
    encoding = "UTF-8",
    na.strings = character(),
    strip.white = TRUE
  )
  required <- c("employer_key", "company", "decision")
  missing <- setdiff(required, names(overrides))
  if (length(missing)) {
    stop(
      "Company-match override file is missing columns: ",
      paste(missing, collapse = ", "),
      call. = FALSE
    )
  }
  optional <- c("state", "occupation_pattern")
  # rep() not "" - assigning a length-1 value to a zero-row table errors, which
  # a header-only override file (the shipped default) triggers on every run.
  for (column in setdiff(optional, names(overrides))) {
    data.table::set(overrides, j = column, value = rep("", nrow(overrides)))
  }
  columns <- c(required, optional)
  overrides <- overrides[, ..columns]
  overrides[, (columns) := lapply(.SD, as.character), .SDcols = columns]
  overrides[, employer_key := normalize_register_key(employer_key)]
  overrides[, company := mentat_internal_company_name(company)]
  overrides[, decision := tolower(trim_text(decision))]
  overrides[, state := normalize_register_key(state)]
  overrides[, occupation_pattern := trim_text(occupation_pattern)]
  if (any(!nzchar(overrides$employer_key)) || any(!nzchar(overrides$company)) ||
      any(!overrides$decision %in% c("accept", "reject"))) {
    stop("Company-match override rows must have a key, company, and accept/reject decision.", call. = FALSE)
  }
  invalid_pattern <- vapply(overrides$occupation_pattern, function(pattern) {
    if (!nzchar(pattern)) return(FALSE)
    inherits(try(grepl(pattern, "", ignore.case = TRUE, perl = TRUE), silent = TRUE), "try-error")
  }, logical(1L))
  if (any(invalid_pattern)) {
    stop("Company-match override occupation_pattern values must be valid regular expressions.", call. = FALSE)
  }
  if (anyDuplicated(overrides[, .(employer_key, company, state, occupation_pattern)])) {
    stop("Company-match override scopes must be unique per company.", call. = FALSE)
  }
  overrides[]
}

# Curated association -> PAC committee-ID map for the trade register. Exact-name
# resolution cannot do this job on its own: a trade association's PAC almost never
# files under the association's own name (NAR -> RPAC, NAHB -> BUILD PAC,
# America's Credit Unions -> "AMERICA'S CREDIT UNIONS PAC OF CREDIT UNION NATIONAL
# ASSOCIATION, INC."), so every trade row stayed unresolved and the Trade
# Association category was never assigned. Resolving by committee ID is also the
# only safe route - FEC CONNECTED_ORG_NM is unreliable (state bankers associations
# list "AMERICAN BANKERS ASSOCIATION PAC" as their connected org) and ORG_TP is not
# stable across cycles (Consumer Bankers Association PAC is 'T' one cycle, 'C' the
# next). A missing file is not fatal: report.R and fec_api.R also call the resolver.
read_trade_committee_overrides <- function(path) {
  require_package("data.table")
  empty <- data.table::data.table(
    association_name = character(),
    committee_id = character(),
    source = character(),
    note = character()
  )
  if (length(path) != 1L || is.na(path) || !nzchar(path) || !file.exists(path)) {
    return(empty)
  }
  overrides <- data.table::fread(
    path,
    encoding = "UTF-8",
    na.strings = character(),
    strip.white = TRUE,
    colClasses = "character"
  )
  if (!nrow(overrides)) {
    return(empty)
  }
  required <- c("association_name", "committee_id")
  missing <- setdiff(required, names(overrides))
  if (length(missing)) {
    stop(
      "Trade-committee override file is missing columns: ",
      paste(missing, collapse = ", "),
      call. = FALSE
    )
  }
  optional <- c("source", "note")
  for (column in setdiff(optional, names(overrides))) {
    data.table::set(overrides, j = column, value = rep("", nrow(overrides)))
  }
  columns <- c(required, optional)
  overrides <- overrides[, ..columns]
  overrides[, (columns) := lapply(.SD, as.character), .SDcols = columns]
  overrides[, association_name := trim_text(association_name)]
  overrides[, committee_id := toupper(trim_text(committee_id))]
  if (any(!nzchar(overrides$association_name))) {
    stop("Trade-committee override rows must name an association.", call. = FALSE)
  }
  invalid <- !grepl("^C[0-9]{8}$", overrides$committee_id, perl = TRUE)
  if (any(invalid)) {
    stop(
      "Trade-committee override committee_id values must look like C########: ",
      paste(unique(overrides$committee_id[invalid]), collapse = ", "),
      call. = FALSE
    )
  }
  if (anyDuplicated(overrides$association_name)) {
    stop(
      "Trade-committee override rows must be unique per association: ",
      paste(unique(overrides$association_name[duplicated(overrides$association_name)]),
            collapse = ", "),
      call. = FALSE
    )
  }
  overrides[]
}

read_company_home_states <- function(path) {
  require_package("data.table")
  if (!file.exists(path)) {
    stop("Company home-state register is missing: ", path, call. = FALSE)
  }
  register <- data.table::fread(
    path,
    encoding = "UTF-8",
    na.strings = character(),
    strip.white = TRUE,
    colClasses = "character"
  )
  required <- c("company", "state")
  missing <- setdiff(required, names(register))
  if (length(missing)) {
    stop(
      "Company home-state register is missing columns: ",
      paste(missing, collapse = ", "),
      call. = FALSE
    )
  }
  register <- register[, ..required]
  register[, `:=`(
    company = mentat_internal_company_name(company),
    state = normalize_register_key(state),
    source = "company_home_states.csv"
  )]
  if (any(!nzchar(register$company)) || any(!grepl("^[A-Z]{2}$", register$state))) {
    stop("Company home-state rows require a company and two-letter state.", call. = FALSE)
  }
  register <- unique(register, by = c("company", "state"))
  data.table::setorder(register, company, state)
  register[]
}

read_name_diminutives <- function(path) {
  require_package("data.table")
  if (!file.exists(path)) {
    stop("Name-diminutive register is missing: ", path, call. = FALSE)
  }
  register <- data.table::fread(
    path,
    encoding = "UTF-8",
    na.strings = character(),
    strip.white = TRUE,
    colClasses = "character"
  )
  required <- c("canonical", "variant")
  missing <- setdiff(required, names(register))
  if (length(missing)) {
    stop(
      "Name-diminutive register is missing columns: ",
      paste(missing, collapse = ", "),
      call. = FALSE
    )
  }
  register <- register[, ..required]
  register[, `:=`(
    canonical = normalize_register_key(canonical),
    variant = normalize_register_key(variant)
  )]
  if (any(!nzchar(register$canonical)) || any(!nzchar(register$variant))) {
    stop("Name-diminutive rows require canonical and variant values.", call. = FALSE)
  }
  if (anyDuplicated(register)) {
    stop("Name-diminutive canonical/variant pairs must be unique.", call. = FALSE)
  }
  data.table::setorder(register, canonical, variant)
  register[]
}

empty_bulk_superset <- function() {
  data.table::data.table(
    cycle = integer(), committee_id = character(), contributor_name = character(),
    contributor_city = character(), contributor_state = character(),
    contributor_zip = character(), employer_string = character(),
    contributor_occupation = character(), amount = numeric(), sub_id = character()
  )
}

read_bulk_supersets <- function(
  bulk_dir = mentat_bulk_dir(),
  cycles = c(2022L, 2024L, 2026L)
) {
  require_package("data.table")
  rows <- list()
  for (cycle in sort(unique(as.integer(cycles)))) {
    path <- file.path(
      bulk_dir,
      sprintf("indiv%02d_crypto_superset.psv", cycle %% 100L)
    )
    if (!file.exists(path)) {
      warning(
        "Optional FEC bulk superset is missing: ", path,
        "; observed-employer fallback for cycle ", cycle,
        " will use roster variants.",
        call. = FALSE
      )
      next
    }
    values <- data.table::fread(
      path,
      sep = "|",
      header = FALSE,
      quote = "",
      fill = TRUE,
      encoding = "UTF-8",
      na.strings = character(),
      colClasses = "character",
      select = c(1L, 8L, 9L, 10L, 11L, 12L, 13L, 15L, 21L),
      showProgress = FALSE
    )
    if (ncol(values) != 9L) {
      stop(
        "FEC bulk superset does not expose the required 21-column layout: ",
        path,
        call. = FALSE
      )
    }
    data.table::setnames(values, c(
      "committee_id", "contributor_name", "contributor_city",
      "contributor_state", "contributor_zip", "employer_string",
      "contributor_occupation", "amount", "sub_id"
    ))
    values[, `:=`(
      cycle = as.integer(cycle),
      committee_id = trim_text(committee_id),
      contributor_name = trim_text(contributor_name),
      contributor_city = trim_text(contributor_city),
      contributor_state = trim_text(contributor_state),
      contributor_zip = trim_text(contributor_zip),
      employer_string = trim_text(employer_string),
      contributor_occupation = trim_text(contributor_occupation),
      amount = suppressWarnings(as.numeric(amount)),
      sub_id = trim_text(sub_id)
    )]
    values <- values[nzchar(employer_string)]
    rows[[length(rows) + 1L]] <- values[, .(
      cycle, committee_id, contributor_name, contributor_city,
      contributor_state, contributor_zip, employer_string,
      contributor_occupation, amount, sub_id
    )]
  }
  if (!length(rows)) return(empty_bulk_superset())
  result <- data.table::rbindlist(rows, use.names = TRUE, fill = TRUE)
  data.table::setorder(result, cycle, employer_string, sub_id)
  result[]
}

build_observed_employer_register <- function(
  bulk_rows,
  companies,
  overrides = NULL,
  home_states = NULL
) {
  require_package("data.table")
  bulk_rows <- data.table::as.data.table(bulk_rows)
  companies <- data.table::as.data.table(companies)
  empty_observed <- data.table::data.table(
    cycle = integer(), employer_string = character(), n_rows = integer()
  )
  empty_candidates <- data.table::data.table(
    cycle = integer(), company = character(), sub_id = character(),
    contributor_name = character(), contributor_city = character(),
    contributor_state = character(), contributor_zip = character(),
    employer_string = character(), contributor_occupation = character(),
    committee_id = character(), amount = numeric()
  )
  if (!nrow(bulk_rows)) {
    return(list(observed_employers = empty_observed, bulk_candidates = empty_candidates))
  }
  required <- c(
    "cycle", "committee_id", "contributor_name", "contributor_city",
    "contributor_state", "contributor_zip", "employer_string",
    "contributor_occupation", "amount", "sub_id"
  )
  missing <- setdiff(required, names(bulk_rows))
  if (length(missing)) {
    stop(
      "Bulk superset rows are missing columns: ", paste(missing, collapse = ", "),
      call. = FALSE
    )
  }
  crypto <- companies[industry == "Crypto"]
  if (!nrow(crypto)) {
    stop("Crypto company register is empty while matching bulk supersets.", call. = FALSE)
  }
  rosters <- split(crypto, crypto$company)
  matches <- list()
  for (row_index in seq_len(nrow(bulk_rows))) {
    row <- bulk_rows[row_index]
    for (company in sort(names(rosters), method = "radix")) {
      roster <- rosters[[company]]
      exclusions <- unique(unlist(strsplit(
        paste(na.omit(roster$exclusions), collapse = "|"),
        "|",
        fixed = TRUE
      )))
      exclusions <- exclusions[nzchar(trim_text(exclusions))]
      excluded <- exclusion_reason(
        row$employer_string,
        row$contributor_occupation,
        exclusions,
        row$contributor_name
      )
      matched <- company_employer_match(
        company = company,
        employer = row$employer_string,
        contributor_name = row$contributor_name,
        variants = roster$variant,
        trusted_name_keys = character(),
        overrides = overrides,
        excluded_reason = excluded,
        contributor_state = row$contributor_state,
        contributor_occupation = row$contributor_occupation,
        home_states = home_states
      )
      if (!identical(matched$status, "accepted")) next
      matches[[length(matches) + 1L]] <- data.table::data.table(
        cycle = as.integer(row$cycle),
        company = company,
        sub_id = trim_text(row$sub_id),
        contributor_name = trim_text(row$contributor_name),
        contributor_city = trim_text(row$contributor_city),
        contributor_state = trim_text(row$contributor_state),
        contributor_zip = trim_text(row$contributor_zip),
        employer_string = trim_text(row$employer_string),
        contributor_occupation = trim_text(row$contributor_occupation),
        committee_id = trim_text(row$committee_id),
        amount = suppressWarnings(as.numeric(row$amount))
      )
    }
  }
  if (!length(matches)) {
    return(list(observed_employers = empty_observed, bulk_candidates = empty_candidates))
  }
  candidates <- data.table::rbindlist(matches, use.names = TRUE, fill = TRUE)
  blank_ids <- !nzchar(candidates$sub_id)
  if (any(blank_ids)) {
    warning(
      sum(blank_ids),
      " matched FEC bulk row(s) had blank sub_id and were omitted from rescue/audit.",
      call. = FALSE
    )
    candidates <- candidates[!blank_ids]
  }
  candidates <- unique(candidates, by = c("company", "cycle", "sub_id"))
  data.table::setorder(candidates, company, cycle, sub_id)
  observed <- unique(candidates[, .(cycle, employer_string, sub_id)])[
    , .(n_rows = .N), by = .(cycle, employer_string)
  ]
  data.table::setorder(observed, cycle, employer_string)
  list(observed_employers = observed[], bulk_candidates = candidates[])
}

trim_text <- function(x) {
  x <- as.character(x)
  x[is.na(x)] <- ""
  trimws(gsub("[[:space:]]+", " ", x, perl = TRUE))
}

nonempty_text <- function(x) {
  value <- trim_text(x)
  value[nzchar(value)]
}

read_sheet_matrix <- function(path, sheet, col_types = NULL) {
  require_package("readxl")
  raw <- readxl::read_excel(
    path,
    sheet = sheet,
    col_names = FALSE,
    col_types = col_types,
    trim_ws = FALSE,
    .name_repair = "minimal"
  )
  as.data.frame(raw, stringsAsFactors = FALSE, check.names = FALSE)
}

is_blank_row <- function(row) {
  !any(nzchar(trim_text(unlist(row, use.names = FALSE))))
}

roster_confidence <- function(text) {
  upper <- toupper(enc2utf8(text))
  if (grepl("\u26a0|SPOT[- ]?CHECK", upper, perl = TRUE)) {
    "spot_check"
  } else if (grepl("\\bSAFE\\b|DISTINCTIVE", upper, perl = TRUE)) {
    "safe"
  } else {
    "normal"
  }
}

strip_parenthetical_notes <- function(text) {
  trim_text(gsub("\\s*\\([^)]*\\)", "", text, perl = TRUE))
}

is_roster_prose <- function(text) {
  upper <- toupper(trim_text(text))
  grepl(
    "^(BUT|HOWEVER)\\b|\\bSO YOU NEED:?$|^THE INSURANCE ACTIVITY\\b",
    upper,
    perl = TRUE
  )
}

parse_roster_cell <- function(cell, source_row) {
  original <- trim_text(cell)
  empty <- list(
    variants = list(),
    exclusions = character(),
    metadata = list(),
    prose = FALSE
  )
  if (!nzchar(original)) {
    return(empty)
  }
  upper <- toupper(original)
  if (grepl("^NOT\\s+", upper, perl = TRUE)) {
    pattern <- trim_text(sub("^NOT\\s+", "", original, ignore.case = TRUE, perl = TRUE))
    empty$exclusions <- pattern
    return(empty)
  }
  cik <- regmatches(original, regexec("^CIK\\s+([0-9]+)$", original, ignore.case = TRUE))[[1L]]
  if (length(cik)) {
    empty$metadata <- list(cik = cik[[2L]])
    return(empty)
  }
  just <- regmatches(
    original,
    regexec("^JUST\\s+[\"']?(.+?)[\"']?$", original, ignore.case = TRUE, perl = TRUE)
  )[[1L]]
  if (length(just)) {
    empty$metadata <- list(search_instruction = trim_text(just[[2L]]))
    return(empty)
  }
  if (is_roster_prose(original)) {
    empty$prose <- TRUE
    return(empty)
  }
  confidence <- roster_confidence(original)
  pieces <- strsplit(original, "\\s+/\\s+", perl = TRUE)[[1L]]
  variants <- lapply(pieces, function(piece) {
    cleaned <- strip_parenthetical_notes(piece)
    if (!nzchar(cleaned)) {
      return(NULL)
    }
    list(
      variant = cleaned,
      confidence = confidence,
      source_row = as.integer(source_row)
    )
  })
  empty$variants <- Filter(Negate(is.null), variants)
  empty
}

roster_blocks <- function(sheet_data, crypto = FALSE) {
  if (crypto) {
    blocks <- list()
    for (row_number in seq_len(nrow(sheet_data))) {
      row <- sheet_data[row_number, , drop = FALSE]
      if (!is_blank_row(row)) {
        blocks[[length(blocks) + 1L]] <- list(
          rows = list(row),
          row_numbers = row_number
        )
      }
    }
    return(blocks)
  }
  blocks <- list()
  rows <- list()
  row_numbers <- integer()
  flush <- function() {
    if (length(rows)) {
      blocks[[length(blocks) + 1L]] <<- list(
        rows = rows,
        row_numbers = row_numbers
      )
      rows <<- list()
      row_numbers <<- integer()
    }
  }
  for (row_number in seq_len(nrow(sheet_data))) {
    row <- sheet_data[row_number, , drop = FALSE]
    if (is_blank_row(row)) {
      flush()
    } else {
      rows[[length(rows) + 1L]] <- row
      row_numbers <- c(row_numbers, row_number)
    }
  }
  flush()
  blocks
}

rollup_parent <- function(industry, company) {
  if (identical(industry, "Property & Casualty Insurance") &&
      grepl("^BERKSHIRE HATHAWAY$", toupper(company))) {
    return("Berkshire Hathaway")
  }
  if (identical(industry, "Credit Arms") &&
      grepl("^HYUNDAI CAPITAL AMERICA$", toupper(company))) {
    return("Hyundai Capital America")
  }
  if (identical(industry, "Credit Arms") &&
      grepl("^PACCAR FINANCIAL CORP$", toupper(company))) {
    return("PACCAR Inc")
  }
  company
}

parse_company_workbook <- function(
  path,
  require_legacy_sheets = !identical(Sys.getenv("MENTAT_CUSTOM_ROSTER"), "1")
) {
  require_package("data.table")
  require_package("jsonlite")
  require_package("readxl")
  # These five must exist; any ADDITIONAL sheet is treated as a new industry so
  # the roster can grow without a code change (Telecom added 2026-07-19).
  required_sheets <- c(
    "Crypto",
    "Credit Union",
    "Community Banks",
    "Property & Casualty Insurance",
    "Credit Arms"
  )
  actual_sheets <- readxl::excel_sheets(path)
  # The upload console uses a run-local roster containing only the requested
  # industries. Standard pipeline runs keep the original completeness guard.
  missing <- if (isTRUE(require_legacy_sheets)) {
    setdiff(required_sheets, actual_sheets)
  } else {
    character()
  }
  if (length(missing)) {
    stop("Company workbook is missing sheets: ", paste(missing, collapse = ", "), call. = FALSE)
  }
  expected_sheets <- if (isTRUE(require_legacy_sheets)) {
    c(required_sheets, setdiff(actual_sheets, required_sheets))
  } else {
    actual_sheets
  }
  output <- list()
  for (sheet in expected_sheets) {
    data <- read_sheet_matrix(path, sheet)
    blocks <- roster_blocks(data, crypto = identical(sheet, "Crypto"))
    if (!length(blocks)) {
      stop("No company blocks found on sheet: ", sheet, call. = FALSE)
    }
    for (block in blocks) {
      parsed_cells <- list()
      for (i in seq_along(block$rows)) {
        values <- unlist(block$rows[[i]], use.names = FALSE)
        for (value in values) {
          parsed_cells[[length(parsed_cells) + 1L]] <-
            parse_roster_cell(value, block$row_numbers[[i]])
        }
      }
      variants <- unlist(lapply(parsed_cells, `[[`, "variants"), recursive = FALSE)
      if (!length(variants)) {
        stop("Company block has no usable variants on sheet: ", sheet, call. = FALSE)
      }
      company <- variants[[1L]]$variant
      parent <- rollup_parent(sheet, company)
      exclusions <- unique(unlist(lapply(parsed_cells, `[[`, "exclusions"), use.names = FALSE))
      exclusions <- exclusions[nzchar(exclusions)]
      metadata_items <- unlist(
        lapply(parsed_cells, `[[`, "metadata"),
        recursive = FALSE,
        use.names = TRUE
      )
      metadata <- if (length(metadata_items)) {
        jsonlite::toJSON(metadata_items, auto_unbox = TRUE, null = "null")
      } else {
        "{}"
      }
      seen <- character()
      variant_order <- 0L
      for (variant in variants) {
        dedup_key <- trim_text(variant$variant)
        if (dedup_key %in% seen) {
          next
        }
        seen <- c(seen, dedup_key)
        variant_order <- variant_order + 1L
        output[[length(output) + 1L]] <- data.table::data.table(
          industry = sheet,
          company = company,
          parent = parent,
          variant = variant$variant,
          variant_order = variant_order,
          confidence = variant$confidence,
          exclusions = if (length(exclusions)) paste(exclusions, collapse = "|") else NA_character_,
          metadata_json = as.character(metadata),
          source_sheet = sheet,
          source_row = as.integer(variant$source_row)
        )
      }
    }
  }
  companies <- data.table::rbindlist(output, use.names = TRUE, fill = TRUE)
  data.table::setorder(companies, industry, company, variant_order)
  companies[]
}

marked_x <- function(x) {
  value <- tolower(trim_text(x))
  nzchar(value) & value == "x"
}

strip_trailing_nbsp <- function(x) {
  sub("[\u00a0[:space:]]+$", "", as.character(x), perl = TRUE)
}

unmarked_recipient_category <- function(group_name) {
  key <- normalize_register_key(group_name)
  if (grepl("\\b(JOINT FUNDRAISING|JFC)\\b", key, perl = TRUE)) {
    return("JFC")
  }
  party_pattern <- paste0(
    "^(DNC|RNC|DSCC|DCCC|NRCC|NRSC)\\b|",
    "\\b(DEMOCRATIC|REPUBLICAN|LIBERTARIAN|GREEN)\\b.*",
    "\\b(PARTY|COMMITTEE|CMTE|COMM)\\b"
  )
  if (grepl(party_pattern, key, perl = TRUE)) {
    return("Party Committee")
  }
  "Unclassified"
}

parse_category_workbook <- function(path) {
  require_package("data.table")
  require_package("readxl")
  sheets <- c("2021-2022", "2023-2024")
  missing <- setdiff(sheets, readxl::excel_sheets(path))
  if (length(missing)) {
    stop("Category workbook is missing sheets: ", paste(missing, collapse = ", "), call. = FALSE)
  }
  output <- list()
  for (sheet in sheets) {
    raw <- readxl::read_excel(
      path,
      sheet = sheet,
      col_names = TRUE,
      trim_ws = FALSE,
      .name_repair = "minimal"
    )
    data <- data.table::as.data.table(raw)
    required <- c("Group", "View", "Super PAC", "527s", "Carey", "501c")
    missing_columns <- setdiff(required, names(data))
    if (length(missing_columns)) {
      stop(
        "Category sheet ", sheet, " is missing columns: ",
        paste(missing_columns, collapse = ", "),
        call. = FALSE
      )
    }
    data[, group_name := trim_text(strip_trailing_nbsp(Group))]
    data <- data[nzchar(group_name)]
    data[, `:=`(
      is_super = marked_x(`Super PAC`),
      is_527 = marked_x(`527s`),
      is_carey = marked_x(Carey),
      is_501 = marked_x(`501c`)
    )]
    data[, category := data.table::fcase(
      is_super, "Super PAC",
      is_carey, "Carey",
      is_527, "527",
      is_501, "501c",
      default = vapply(group_name, unmarked_recipient_category, character(1L))
    )]
    data[, lean := data.table::fcase(
      category %in% c("Party Committee", "Candidate", "JFC"), NA_character_,
      toupper(trim_text(View)) == "C", "R",
      toupper(trim_text(View)) == "L", "D",
      toupper(trim_text(View)) == "X", NA_character_,
      default = NA_character_
    )]
    data[, source_marks := apply(
      cbind(is_super, is_527, is_carey, is_501),
      1L,
      function(flags) {
        labels <- c("Super PAC", "527s", "Carey", "501c")
        paste(labels[as.logical(flags)], collapse = "|")
      }
    )]
    output[[sheet]] <- data.table::data.table(
      cycle = if (identical(sheet, "2021-2022")) 2022L else 2024L,
      group_name = data$group_name,
      category = data$category,
      lean = data$lean,
      committee_id = NA_character_,
      source_sheet = sheet,
      source_marks = data$source_marks,
      provisional = 0L,
      resolution_status = "unresolved"
    )
  }
  categories <- data.table::rbindlist(output, use.names = TRUE)
  provisional <- data.table::copy(categories[cycle == 2024L])
  provisional[, `:=`(
    cycle = 2026L,
    source_sheet = paste0(source_sheet, " (carried forward)"),
    provisional = 1L
  )]
  categories <- data.table::rbindlist(
    list(categories, provisional),
    use.names = TRUE
  )
  data.table::setorder(categories, cycle, group_name, category)
  categories[]
}

normalize_leadership_lean <- function(x) {
  value <- trim_text(x)
  data.table::fcase(
    value == "Solidly Republican/Conservative", "R",
    value == "Solidly Democrat/Liberal", "D",
    default = "REVIEW"
  )
}

parse_leadership_files <- function(path_2022, path_2024) {
  require_package("data.table")
  parse_one <- function(path, cycle) {
    data <- data.table::fread(
      path,
      encoding = "UTF-8",
      na.strings = c("", "NA"),
      strip.white = TRUE
    )
    required <- c("PAC Name", "Affiliate", "Lean")
    missing <- setdiff(required, names(data))
    if (length(missing)) {
      stop("Leadership CSV is missing columns: ", paste(missing, collapse = ", "), call. = FALSE)
    }
    parsed <- data.table::data.table(
      cycle = as.integer(cycle),
      pac_name = trim_text(data[["PAC Name"]]),
      affiliate = trim_text(data[["Affiliate"]]),
      lean = normalize_leadership_lean(data[["Lean"]]),
      provisional = 0L,
      committee_id = NA_character_,
      source = basename(path),
      resolution_status = "unresolved"
    )
    # The source CSVs contain exact duplicate rows (verified: 3 in 2023-24,
    # identical affiliate+lean). Collapse exact dupes; a name that repeats
    # with CONFLICTING data is a labeling question -> lean = 'REVIEW'.
    parsed <- unique(parsed, by = c("pac_name", "affiliate", "lean"))
    conflicted <- parsed[, .N, by = pac_name][N > 1L, pac_name]
    if (length(conflicted)) {
      parsed[pac_name %in% conflicted, lean := "REVIEW"]
      parsed <- unique(parsed, by = "pac_name")
    }
    parsed
  }
  leadership_2022 <- parse_one(path_2022, 2022L)
  leadership_2024 <- parse_one(path_2024, 2024L)
  provisional <- data.table::copy(leadership_2024)
  provisional[, `:=`(
    cycle = 2026L,
    provisional = 1L,
    source = paste0(source, " (carried forward)")
  )]
  leadership <- data.table::rbindlist(
    list(leadership_2022, leadership_2024, provisional),
    use.names = TRUE
  )
  if (any(!nzchar(leadership$pac_name))) {
    stop("Leadership register contains a blank PAC name.", call. = FALSE)
  }
  leadership[]
}

cleanup_trade_name <- function(x) {
  value <- trim_text(x)
  value <- sub("^\\[(.*)\\]\\]\\[1\\]$", "\\1", value, perl = TRUE)
  trim_text(value)
}

parse_trade_associations <- function(path) {
  require_package("data.table")
  lines <- readLines(path, warn = FALSE, encoding = "UTF-8")
  current_section <- NA_character_
  output <- list()
  for (line in lines) {
    value <- trim_text(line)
    if (!nzchar(value)) {
      next
    }
    if (grepl("^-\\s+", value, perl = TRUE)) {
      if (is.na(current_section)) {
        stop("Trade-association bullet appeared before a category header.", call. = FALSE)
      }
      name <- cleanup_trade_name(sub("^-\\s+", "", value, perl = TRUE))
      if (!nzchar(name)) {
        stop("Trade-association file contains an empty bullet.", call. = FALSE)
      }
      output[[length(output) + 1L]] <- data.table::data.table(
        association_name = name,
        section = current_section,
        committee_id = NA_character_,
        resolution_status = "unresolved",
        source = basename(path)
      )
    } else {
      current_section <- value
    }
  }
  trade <- data.table::rbindlist(output, use.names = TRUE)
  trade <- unique(trade, by = "association_name")
  data.table::setorder(trade, association_name)
  trade[]
}

normalize_template_spelling <- function(x) {
  value <- trim_text(x)
  value <- gsub("Krakken", "Kraken", value, fixed = TRUE)
  value <- gsub("Winkelvoss", "Winklevoss", value, fixed = TRUE)
  value
}

normalize_template_industry <- function(x) {
  value <- trim_text(x)
  data.table::fcase(
    value == "Crypto Exchange", "Crypto",
    value == "Credit Union", "Credit Union",
    value == "FDIC Designated: Community Bank", "Community Banks",
    value == "Property & Casualty Insurers", "Property & Casualty Insurance",
    value == "Finance/Credit/Sales arms of Companies", "Credit Arms",
    default = value
  )
}

parse_template_registers <- function(path) {
  require_package("data.table")
  require_package("readxl")
  assets_raw <- readxl::read_excel(
    path,
    sheet = "Sheet1",
    col_names = TRUE,
    trim_ws = TRUE,
    .name_repair = "minimal"
  )
  assets <- data.table::as.data.table(assets_raw)
  required <- c("Industry", "Total Assets (Trillion)", "Link")
  missing <- setdiff(required, names(assets))
  if (length(missing)) {
    stop("Template Sheet1 is missing columns: ", paste(missing, collapse = ", "), call. = FALSE)
  }
  assets[, source_row := .I + 1L]
  assets <- assets[trim_text(Industry) != "ABS issuers"]
  assets <- data.table::data.table(
    industry = trim_text(assets$Industry),
    total_assets_trillion = as.numeric(assets[["Total Assets (Trillion)"]]),
    source_link = trim_text(assets$Link),
    source_row = as.integer(assets$source_row)
  )
  if (anyNA(assets$total_assets_trillion)) {
    stop("Template Sheet1 contains a nonnumeric in-scope asset value.", call. = FALSE)
  }

  executive_raw <- read_sheet_matrix(path, "Sheet2")
  while (ncol(executive_raw) < 5L) {
    executive_raw[[ncol(executive_raw) + 1L]] <- NA_character_
  }
  source_industry <- trim_text(executive_raw[[2L]])
  source_company <- trim_text(executive_raw[[3L]])
  source_executive <- trim_text(executive_raw[[5L]])
  keep <- nzchar(source_company) &
    toupper(source_industry) != "INDUSTRY" &
    toupper(source_company) != "NAME"
  source_rows <- seq_len(nrow(executive_raw))[keep] + 1L
  executives <- data.table::data.table(
    executive_id = seq_len(sum(keep)),
    industry = normalize_template_industry(source_industry[keep]),
    company = mentat_internal_company_name(source_company[keep]),
    executive_name = normalize_template_spelling(source_executive[keep]),
    role = "Chief",
    source_industry = source_industry[keep],
    source_company = source_company[keep],
    source_executive = source_executive[keep],
    source_row = as.integer(source_rows)
  )
  executives[!nzchar(executive_name), executive_name := NA_character_]
  executives[!nzchar(source_executive), source_executive := NA_character_]
  list(assets = assets, executives = executives)
}

as_fixture_date <- function(x) {
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
  result[numeric_like] <- as.Date(
    as.numeric(value[numeric_like]),
    origin = "1899-12-30"
  )
  formats <- c("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y")
  remaining <- which(is.na(result) & nzchar(value))
  for (format in formats) {
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

read_crypto_fixture <- function(path) {
  require_package("data.table")
  # col_types = "text": the sheet mixes row layouts, so readxl's majority-vote
  # type guessing destroys the minority rows (state into a date column -> NA,
  # amount into a logical column -> TRUE). Read raw, type everything ourselves.
  raw <- read_sheet_matrix(path, "Crypto", col_types = "text")
  while (ncol(raw) < 7L) {
    raw[[ncol(raw) + 1L]] <- NA_character_
  }
  # The hand-built sheet mixes two row layouts (the extraction method was
  # enriched partway through):
  #   narrow: name | recipient | date | amount
  #   wide:   name | recipient | state | employer | date | amount
  # A row is wide iff column 5 or 6 is populated.
  c3 <- trim_text(raw[[3L]])
  c4 <- trim_text(raw[[4L]])
  c5 <- trim_text(raw[[5L]])
  c6 <- trim_text(raw[[6L]])
  wide <- nzchar(c5) | nzchar(c6)
  fixture <- data.table::data.table(
    fixture_id = seq_len(nrow(raw)),
    contributor_name = trim_text(raw[[1L]]),
    recipient_name = trim_text(raw[[2L]]),
    receipt_date = as_fixture_date(ifelse(wide, c5, c3)),
    amount = suppressWarnings(round(as.numeric(ifelse(wide, c6, c4)), 2L)),
    contributor_state = ifelse(wide, c3, NA_character_),
    employer = ifelse(wide, c4, NA_character_),
    city_state_zip = NA_character_,
    occupation = NA_character_
  )
  fixture <- fixture[
    nzchar(contributor_name) | nzchar(recipient_name) | !is.na(receipt_date) | !is.na(amount)
  ]
  fixture[, fixture_id := seq_len(.N)]
  if (any(!nzchar(fixture$contributor_name)) ||
      any(!nzchar(fixture$recipient_name)) ||
      anyNA(fixture$receipt_date) ||
      anyNA(fixture$amount)) {
    stop("Crypto fixture violates its four-column required-field contract.", call. = FALSE)
  }
  fixture[]
}

build_rosters_summary <- function(companies, categories, leadership, trade, executives, assets) {
  industries <- unique(companies$industry)
  pages <- lapply(industries, function(industry_name) {
    industry <- companies[industry == industry_name]
    company_names <- unique(industry$company)
    lines <- c(
      paste0("MENTAT ROSTER: ", industry_name),
      paste0("Companies: ", length(company_names)),
      ""
    )
    for (company_name in company_names) {
      rows <- industry[company == company_name]
      variants <- paste0(rows$variant, " [", rows$confidence, "]")
      lines <- c(
        lines,
        paste0(
          mentat_company_display_name(company_name),
          " -> parent: ",
          mentat_company_display_name(rows$parent[[1L]])
        ),
        paste0("  variants: ", paste(variants, collapse = " | "))
      )
      exclusions <- rows$exclusions[[1L]]
      if (!is.na(exclusions) && nzchar(exclusions)) {
        lines <- c(lines, paste0("  exclusions: ", exclusions))
      }
    }
    lines
  })
  register_summary <- c(
    "PHASE 0 REGISTER COUNTS",
    paste0("Category rows: ", nrow(categories)),
    paste0("Leadership rows (including provisional 2026): ", nrow(leadership)),
    paste0("Trade associations: ", nrow(trade)),
    paste0("Template company/executive rows: ", nrow(executives)),
    paste0("Industry asset denominators: ", nrow(assets))
  )
  c(unlist(Map(function(page, index) {
    if (index < length(pages)) c(page, "\f") else page
  }, pages, seq_along(pages)), use.names = FALSE), "", register_summary)
}

validate_ingest_counts <- function(
  companies,
  categories,
  leadership,
  trade,
  executives,
  assets,
  fixture,
  require_legacy_roster = !identical(Sys.getenv("MENTAT_CUSTOM_ROSTER"), "1")
) {
  # The five original sheets have verified block counts. Additional sheets are
  # new industries (Telecom added 2026-07-19) and only need >= 1 company, so the
  # roster can grow without a code change.
  expected_company_counts <- data.table::data.table(
    industry = c(
      "Crypto", "Credit Union", "Community Banks",
      "Property & Casualty Insurance", "Credit Arms"
    ),
    expected = c(10L, 10L, 10L, 10L, 10L)
  )
  company_counts <- companies[, .(companies = data.table::uniqueN(company)), by = industry]
  verified <- company_counts[expected_company_counts, on = "industry"]
  if (isTRUE(require_legacy_roster) &&
      (nrow(verified) != 5L || any(is.na(verified$companies)) ||
       any(verified$companies != verified$expected))) {
    stop("Company workbook block counts do not match the verified five sheets.", call. = FALSE)
  }
  extra <- company_counts[!industry %in% expected_company_counts$industry]
  if (nrow(extra) && any(extra$companies < 1L)) {
    stop("Additional industry sheets must contain at least one company block.", call. = FALSE)
  }
  crypto <- companies[industry == "Crypto"]
  if (isTRUE(require_legacy_roster) &&
      (data.table::uniqueN(crypto$company) != 10L || nrow(crypto) != 16L)) {
    stop("Crypto roster must contain 10 companies and 16 clean variants.", call. = FALSE)
  }
  if (!nrow(companies) || any(!nzchar(trim_text(companies$industry))) ||
      any(!nzchar(trim_text(companies$company))) ||
      any(!nzchar(trim_text(companies$variant)))) {
    stop("Company roster must contain nonempty industries, companies, and variants.", call. = FALSE)
  }
  allowed_categories <- c(
    "Super PAC", "Carey", "527", "501c", "Leadership PAC",
    "Party Committee", "JFC", "Candidate", "Unclassified"
  )
  if (any(!categories$category %in% allowed_categories) ||
      nrow(categories) != 425L ||
      categories[cycle == 2022L, .N] != 175L ||
      categories[cycle == 2024L, .N] != 125L ||
      categories[cycle == 2026L, .N] != 125L ||
      !categories[cycle == 2026L, all(provisional == 1L)]) {
    stop("Category register parsing or 2026 carry-forward is incomplete.", call. = FALSE)
  }
  # 2023-24 CSV holds 791 rows but 3 are exact duplicates -> 788 after dedup;
  # the 2026 register is the 2023-24 register carried forward.
  if (nrow(leadership) != 2322L ||
      leadership[cycle == 2022L, .N] != 746L ||
      leadership[cycle == 2024L, .N] != 788L ||
      leadership[cycle == 2026L, .N] != 788L) {
    stop("Leadership register counts do not match the verified CSV files.", call. = FALSE)
  }
  if (nrow(trade) != 342L) {
    stop("Trade-association register must contain 342 exact-name-deduplicated rows.", call. = FALSE)
  }
  if (nrow(executives) != 50L || nrow(assets) != 5L) {
    stop("Template registers must contain 50 company rows and five asset rows.", call. = FALSE)
  }
  if (nrow(fixture) != 1189L) {
    stop("Crypto fixture must contain exactly 1,189 data rows.", call. = FALSE)
  }
  invisible(TRUE)
}

# Trade-association committee-ID overrides are NOT applied here. They are read
# and folded in at report time by spending_report_lookups()/apply_trade_overrides
# (report.R), which is the path every consumer actually uses - the exporter opens
# the database SQLITE_RO and never calls this resolver. Keeping the override in
# one place stops the two copies from drifting apart.
resolve_recipient_register_committee_ids <- function(connection, log = TRUE) {
  require_package("data.table")
  observed <- data.table::as.data.table(DBI::dbGetQuery(
    connection,
    paste(
      "SELECT DISTINCT UPPER(TRIM(committee_id)) AS committee_id, committee_name",
      "FROM contributions WHERE committee_id IS NOT NULL AND TRIM(committee_id) <> ''",
      "AND committee_name IS NOT NULL AND TRIM(committee_name) <> ''"
    )
  ))
  if (!nrow(observed)) {
    summary <- data.table::data.table(
      register = c("categories", "leadership", "trade"),
      rows = c(
        DBI::dbGetQuery(connection, "SELECT COUNT(*) AS n FROM categories")$n,
        DBI::dbGetQuery(connection, "SELECT COUNT(*) AS n FROM leadership")$n,
        DBI::dbGetQuery(connection, "SELECT COUNT(*) AS n FROM trade")$n
      ),
      resolved = 0L,
      ambiguous = 0L,
      resolution_rate = 0
    )
    if (isTRUE(log)) {
      cat("Recipient register committee-ID resolution: no observed committees yet.
")
    }
    return(invisible(summary))
  }
  observed[, group_key := normalize_register_key(committee_name)]
  observed <- observed[nzchar(group_key) & nzchar(committee_id)]
  ids_by_key <- observed[, .(
    committee_ids = list(sort(unique(committee_id))),
    id_count = data.table::uniqueN(committee_id)
  ), by = group_key]
  specs <- list(
    categories = list(
      name = "group_name",
      key = c("cycle", "group_name", "category")
    ),
    leadership = list(
      name = "pac_name",
      key = c("cycle", "pac_name")
    ),
    trade = list(
      name = "association_name",
      key = "association_name"
    )
  )
  summaries <- list()
  DBI::dbWithTransaction(connection, {
    for (table in names(specs)) {
      spec <- specs[[table]]
      quoted_table <- as.character(DBI::dbQuoteIdentifier(connection, table))
      register <- data.table::as.data.table(DBI::dbReadTable(connection, table))
      register[, group_key := normalize_register_key(get(spec$name))]
      register <- ids_by_key[register, on = "group_key"]
      register[, resolution_status_new := data.table::fcase(
        is.na(id_count), "unresolved",
        id_count == 1L, "resolved_exact_name",
        default = "ambiguous_exact_name"
      )]
      register[, committee_id_new := vapply(
        committee_ids,
        function(ids) if (length(ids) == 1L) ids[[1L]] else NA_character_,
        character(1L)
      )]
      for (index in seq_len(nrow(register))) {
        where <- paste(
          vapply(
            spec$key,
            function(column) paste0(
              as.character(DBI::dbQuoteIdentifier(connection, column)),
              " = ?"
            ),
            character(1L)
          ),
          collapse = " AND "
        )
        DBI::dbExecute(
          connection,
          paste0(
            "UPDATE ", quoted_table,
            " SET committee_id = ?, resolution_status = ? WHERE ", where
          ),
          params = c(
            list(
              register$committee_id_new[[index]],
              register$resolution_status_new[[index]]
            ),
            unname(as.list(register[index, spec$key, with = FALSE]))
          )
        )
      }
      resolved <- sum(register$resolution_status_new == "resolved_exact_name")
      ambiguous <- sum(register$resolution_status_new == "ambiguous_exact_name")
      summaries[[table]] <- data.table::data.table(
        register = table,
        rows = nrow(register),
        resolved = resolved,
        ambiguous = ambiguous,
        resolution_rate = if (nrow(register)) resolved / nrow(register) else 0
      )
    }
  })
  summary <- data.table::rbindlist(summaries, use.names = TRUE)
  if (isTRUE(log)) {
    cat("Recipient register committee-ID resolution:
")
    print(summary)
  }
  invisible(summary)
}

ingest_all <- function(
  connection,
  input_dir = mentat_input_dir(),
  data_root = mentat_default_data_root(),
  print_summary = TRUE,
  bulk_dir = mentat_bulk_dir(data_root)
) {
  require_package("data.table")
  require_package("DBI")
  init_schema(connection)
  files <- list(
    companies = file.path(input_dir, "Company Name.xlsx"),
    categories = file.path(input_dir, "Super PAC, 527, Carey, 501c.xlsx"),
    leadership_2022 = file.path(
      input_dir,
      "Leadership PACs to candidates, 2021-2022.csv"
    ),
    leadership_2024 = file.path(
      input_dir,
      "Leadership PACs to candidates, 2023-2024.csv"
    ),
    trade = file.path(input_dir, "Trade Associations.txt"),
    template = file.path(input_dir, "Credit Union Cross Industry Spending.xlsx"),
    overrides = file.path(input_dir, "company_match_overrides.csv"),
    trade_overrides = file.path(input_dir, "trade_committee_overrides.csv"),
    home_states = file.path(input_dir, "company_home_states.csv"),
    diminutives = file.path(input_dir, "name_diminutives.csv")
  )
  if (!file.exists(files$overrides)) {
    atomic_write_lines(
      "employer_key,company,decision",
      files$overrides
    )
  }
  if (!file.exists(files$trade_overrides)) {
    atomic_write_lines(
      "association_name,committee_id,source,note",
      files$trade_overrides
    )
  }
  missing <- names(files)[!file.exists(unlist(files, use.names = FALSE))]
  if (length(missing)) {
    stop("Missing Phase 0 inputs: ", paste(missing, collapse = ", "), call. = FALSE)
  }
  companies <- parse_company_workbook(files$companies)
  categories <- parse_category_workbook(files$categories)
  leadership <- parse_leadership_files(files$leadership_2022, files$leadership_2024)
  trade <- parse_trade_associations(files$trade)
  template_registers <- parse_template_registers(files$template)
  fixture <- read_crypto_fixture(files$template)
  assets <- template_registers$assets
  executives <- template_registers$executives
  display_names <- mentat_company_display_register()
  overrides <- read_company_match_overrides(files$overrides)
  home_states <- read_company_home_states(files$home_states)
  diminutives <- read_name_diminutives(files$diminutives)
  bulk_rows <- read_bulk_supersets(bulk_dir)
  bulk_register <- build_observed_employer_register(
    bulk_rows,
    companies,
    overrides,
    home_states
  )

  validate_ingest_counts(
    companies,
    categories,
    leadership,
    trade,
    executives,
    assets,
    fixture
  )

  fixture_db <- data.table::copy(fixture)
  fixture_db[, receipt_date := format(receipt_date, "%Y-%m-%d")]
  tables <- list(
    companies = companies,
    company_display_names = display_names,
    company_match_overrides = overrides,
    company_home_states = home_states,
    name_diminutives = diminutives,
    observed_employers = bulk_register$observed_employers,
    bulk_candidates = bulk_register$bulk_candidates,
    categories = categories,
    leadership = leadership,
    trade = trade,
    executives = executives,
    industry_assets = assets,
    fixture_crypto = fixture_db
  )
  DBI::dbWithTransaction(connection, {
    for (table in names(tables)) {
      quoted <- as.character(DBI::dbQuoteIdentifier(connection, table))
      DBI::dbExecute(connection, paste("DELETE FROM", quoted))
      DBI::dbAppendTable(connection, table, as.data.frame(tables[[table]]))
    }
  })
  resolution <- resolve_recipient_register_committee_ids(
    connection,
    log = print_summary
  )

  paths <- mentat_bootstrap_dirs(data_root)
  summary <- build_rosters_summary(
    companies,
    categories,
    leadership,
    trade,
    executives,
    assets
  )
  atomic_write_lines(summary, paths$rosters_summary)
  if (isTRUE(print_summary)) {
    cat(paste(summary, collapse = "\n"), "\n")
  }
  invisible(c(
    tables,
    list(
      fixture = fixture,
      bulk_rows = bulk_rows,
      recipient_resolution = resolution,
      summary_path = paths$rosters_summary
    )
  ))
}

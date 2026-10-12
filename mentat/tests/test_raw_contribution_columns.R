# Assert the R-side raw-contribution producers against the column-order
# contract in audits/mentat_failings_20260915/contracts/
# raw_contribution_columns.json.
#
# Run from the mentat/ working directory:
#   Rscript tests/test_raw_contribution_columns.R
#
# Prints one PASS/FAIL line per check and exits nonzero if any check failed.

script_argument <- grep("^--file=", commandArgs(trailingOnly = FALSE), value = TRUE)
stopifnot(length(script_argument) == 1L)
script_path <- normalizePath(
  sub("^--file=", "", script_argument[[1L]]),
  winslash = "/",
  mustWork = TRUE
)
project_root <- normalizePath(
  file.path(dirname(script_path), ".."),
  winslash = "/",
  mustWork = TRUE
)
for (file in c(
  "paths.R", "schema.R", "ingest.R", "collect.R", "fec_api.R", "tidy_stage.R"
)) {
  source(file.path(project_root, "R", file), local = .GlobalEnv)
}
suppressPackageStartupMessages(library(jsonlite))
suppressPackageStartupMessages(library(data.table))

CONTRACT_RELATIVE <- file.path(
  "audits", "mentat_failings_20260915", "contracts",
  "raw_contribution_columns.json"
)
contract_candidates <- c(
  file.path(project_root, "..", CONTRACT_RELATIVE),
  file.path("..", CONTRACT_RELATIVE),
  CONTRACT_RELATIVE
)
contract_path <- contract_candidates[file.exists(contract_candidates)]
if (!length(contract_path)) {
  stop("column-order contract not found; looked in: ",
       paste(contract_candidates, collapse = ", "))
}
contract <- jsonlite::fromJSON(contract_path[[1L]], simplifyVector = TRUE)
block_db <- as.character(contract$block_db)
street_columns <- c("contributor_street_1", "contributor_street_2")

# Keep the historical address contract intact. The September 20 accounting
# correction adds explicit company-attribution evidence to both projections,
# and preserves receipt-role evidence in tidy staging. Require these exact
# additions at their published position; do not ignore arbitrary new columns.
tidy_columns_current <- append(
  contract$surfaces$tidy_contributions$columns,
  c("company_attribution_status", "company_attribution_reason",
    "receipt_role_unverified", "receipt_role_reason"),
  after = match("company_match_reason", contract$surfaces$tidy_contributions$columns)
)
workbook_columns_current <- append(
  contract$surfaces$workbook_r_stage$columns,
  c("company_attribution_status", "company_attribution_reason"),
  after = match("company_match_reason", contract$surfaces$workbook_r_stage$columns)
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

block_adjacent <- function(names, block) {
  names <- as.character(names)
  block <- as.character(block)
  positions <- match(block, names)
  if (anyNA(positions)) {
    return(FALSE)
  }
  all(positions == seq(positions[[1L]], length.out = length(block)))
}

adjacency_detail <- function(label, names, block) {
  names <- as.character(names)
  block <- as.character(block)
  absent <- setdiff(block, names)
  if (length(absent)) {
    return(sprintf("%s: missing %s", label, paste(absent, collapse = ", ")))
  }
  if (!block_adjacent(names, block)) {
    return(sprintf(
      "%s: block not contiguous/in contract order; positions %s",
      label,
      paste(match(block, names), collapse = ",")
    ))
  }
  TRUE
}

identical_detail <- function(label, actual, expected) {
  actual <- as.character(actual)
  expected <- as.character(expected)
  if (identical(actual, expected)) {
    return(TRUE)
  }
  if (length(actual) != length(expected)) {
    return(sprintf(
      "%s: %d columns, contract wants %d; absent from producer: %s",
      label, length(actual), length(expected),
      paste(setdiff(expected, actual), collapse = ", ")
    ))
  }
  index <- which(actual != expected)[[1L]]
  sprintf(
    "%s: first difference at position %d: producer %s, contract %s",
    label, index, actual[[index]], expected[[index]]
  )
}

# Walk a parsed node, collecting every sub-node the predicate accepts.
walk_calls <- function(node, predicate) {
  found <- list()
  visit <- function(current) {
    if (!is.call(current)) {
      return(invisible(NULL))
    }
    if (isTRUE(predicate(current))) {
      found[[length(found) + 1L]] <<- current
    }
    for (index in seq_along(current)) {
      # An element can be the empty symbol - the i slot of `x[, j]`. Binding
      # it to a variable makes that variable "missing" and every later use
      # throws; testing it inline inside tryCatch does not.
      recurse <- tryCatch(
        is.call(current[[index]]),
        error = function(condition) FALSE
      )
      if (isTRUE(recurse)) {
        visit(current[[index]])
      }
    }
    invisible(NULL)
  }
  visit(node)
  found
}

# `corporate_columns` is LOCAL to checkpoint_corporate_page(), so it cannot be
# read as a top-level object. Find its assignment in the function body and
# evaluate that expression on its own.
local_vector <- function(fn, target) {
  matches <- walk_calls(body(fn), function(current) {
    length(current) >= 3L &&
      is.symbol(current[[1L]]) &&
      as.character(current[[1L]]) %in% c("<-", "=", "<<-") &&
      is.symbol(current[[2L]]) &&
      identical(as.character(current[[2L]]), target)
  })
  if (!length(matches)) {
    stop("no assignment to ", target, " found in the function body")
  }
  eval(matches[[1L]][[3L]], envir = new.env(parent = baseenv()))
}

# data.table's .() names a column by its argument name, or - for a bare symbol
# argument - by the symbol itself.
projection_names <- function(node) {
  labels <- names(node)
  result <- character(0)
  for (index in seq_along(node)[-1L]) {
    label <- if (!is.null(labels) && nzchar(labels[[index]])) {
      labels[[index]]
    } else {
      paste(deparse(node[[index]]), collapse = "")
    }
    result <- c(result, label)
  }
  result
}

find_dot_projection <- function(path, anchor_name, anchor_value) {
  expressions <- parse(path, keep.source = FALSE)
  for (expression in expressions) {
    subsets <- walk_calls(expression, function(current) {
      is.symbol(current[[1L]]) && identical(as.character(current[[1L]]), "[")
    })
    for (subset in subsets) {
      for (index in seq_along(subset)) {
        is_dot <- tryCatch(
          is.call(subset[[index]]) &&
            is.symbol(subset[[index]][[1L]]) &&
            identical(as.character(subset[[index]][[1L]]), "."),
          error = function(condition) FALSE
        )
        if (!isTRUE(is_dot)) {
          next
        }
        child <- subset[[index]]
        labels <- names(child)
        if (is.null(labels) || !(anchor_name %in% labels)) {
          next
        }
        argument <- child[[which(labels == anchor_name)[[1L]]]]
        if (identical(paste(deparse(argument), collapse = ""), anchor_value)) {
          return(child)
        }
      }
    }
  }
  stop("no .() projection with ", anchor_name, " = ", anchor_value, " in ", path)
}

failures <- 0L
check <- function(id, description, thunk) {
  outcome <- tryCatch(
    {
      value <- thunk()
      if (isTRUE(value)) {
        list(passed = TRUE, detail = "")
      } else if (is.character(value)) {
        list(passed = FALSE, detail = paste(value, collapse = " "))
      } else {
        list(passed = FALSE, detail = "check returned FALSE")
      }
    },
    error = function(condition) {
      list(passed = FALSE, detail = paste0("ERROR: ", conditionMessage(condition)))
    }
  )
  if (!outcome$passed) {
    failures <<- failures + 1L
  }
  cat(sprintf(
    "%s check (%s) %s%s\n",
    if (outcome$passed) "PASS" else "FAIL",
    id,
    description,
    if (nzchar(outcome$detail)) paste0(" -- ", outcome$detail) else ""
  ))
}

# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

check("a", "collect.R contribution_columns() keeps the address block", function() {
  adjacency_detail("contribution_columns()", contribution_columns(), block_db)
})

check("b", "collect.R corporate_columns keeps the address block", function() {
  columns <- local_vector(checkpoint_corporate_page, "corporate_columns")
  adjacency_detail("corporate_columns", columns, block_db)
})

check("c", "fec_api.R fec_api_corporate_records() keeps the address block", function() {
  connection <- DBI::dbConnect(RSQLite::SQLite(), ":memory:")
  on.exit(DBI::dbDisconnect(connection), add = TRUE)
  init_schema(connection)
  unit <- list(
    industry = "Crypto",
    company = "Acme",
    company_variants = "Acme",
    exclusions = character(),
    cycle = 2024L
  )
  api_rows <- list(list(
    sub_id = "fixture-1",
    contributor_name = "ACME",
    committee_id = "C00000001",
    entity_type = "ORG",
    contribution_receipt_amount = 10,
    contribution_receipt_date = "2024-01-01",
    contributor_employer = "ACME",
    contributor_occupation = "CORPORATION",
    contributor_street_1 = "123 MAIN ST",
    contributor_street_2 = "",
    contributor_city = "CAMBRIDGE",
    contributor_state = "MA",
    contributor_zip = "021341234",
    filing_form = "F3X",
    line_number = "11AI"
  ))
  records <- fec_api_corporate_records(
    connection,
    unit,
    api_rows,
    match_overrides = data.frame(),
    home_states = data.frame()
  )
  adjacency_detail("fec_api_corporate_records()", names(records), block_db)
})

check("d", "tidy_stage.R contributions spec matches the address and attribution contract", function() {
  identical_detail(
    "MENTAT_TIDY_TABLE_SPECS$contributions",
    names(MENTAT_TIDY_TABLE_SPECS$contributions),
    tidy_columns_current
  )
})

check("e", "export_crypto_spending_data.R raw_export projection matches the address and attribution contract", function() {
  projection <- find_dot_projection(
    file.path(project_root, "tools", "export_crypto_spending_data.R"),
    "source_id",
    "sub_id"
  )
  identical_detail(
    "raw_export projection",
    projection_names(projection),
    workbook_columns_current
  )
})

check("f", "init_schema() migrates an old database to the street columns", function() {
  tables <- c("contributions", "bulk_candidates", "corporate_giving")
  current_database <- tempfile(fileext = ".sqlite")
  current <- DBI::dbConnect(RSQLite::SQLite(), current_database)
  on.exit({
    DBI::dbDisconnect(current)
    unlink(current_database)
  }, add = TRUE)
  init_schema(current)
  definitions <- character(0)
  for (table in tables) {
    info <- DBI::dbGetQuery(current, sprintf("PRAGMA table_info(%s)", table))
    info <- info[!(info$name %in% street_columns), , drop = FALSE]
    columns <- sprintf(
      '"%s" %s', info$name, ifelse(nzchar(info$type), info$type, "TEXT")
    )
    keys <- info$name[info$pk > 0L][order(info$pk[info$pk > 0L])]
    if (length(keys)) {
      columns <- c(columns, sprintf(
        "PRIMARY KEY (%s)", paste(sprintf('"%s"', keys), collapse = ", ")
      ))
    }
    definitions[[table]] <- sprintf(
      "CREATE TABLE %s (%s)", table, paste(columns, collapse = ", ")
    )
  }

  # An OLD database: the current column set minus street, built by hand so that
  # CREATE TABLE IF NOT EXISTS cannot quietly supply the new shape.
  old_database <- tempfile(fileext = ".sqlite")
  old <- DBI::dbConnect(RSQLite::SQLite(), old_database)
  on.exit({
    DBI::dbDisconnect(old)
    unlink(old_database)
  }, add = TRUE)
  before <- integer(0)
  for (table in tables) {
    DBI::dbExecute(old, definitions[[table]])
    for (index in 1:3) {
      DBI::dbExecute(
        old,
        sprintf('INSERT INTO %s ("sub_id") VALUES (?)', table),
        params = list(sprintf("%s-row-%d", table, index))
      )
    }
    before[[table]] <- DBI::dbGetQuery(
      old, sprintf("SELECT COUNT(*) AS n FROM %s", table)
    )$n[[1L]]
  }

  init_schema(old)

  problems <- character(0)
  for (table in tables) {
    after <- DBI::dbGetQuery(
      old, sprintf("SELECT COUNT(*) AS n FROM %s", table)
    )$n[[1L]]
    if (!identical(as.integer(after), as.integer(before[[table]]))) {
      problems <- c(problems, sprintf(
        "%s lost rows: %d before, %d after", table, before[[table]], after
      ))
    }
    absent <- setdiff(street_columns, DBI::dbListFields(old, table))
    if (length(absent)) {
      problems <- c(problems, sprintf(
        "%s not migrated: missing %s", table, paste(absent, collapse = ", ")
      ))
    }
  }
  if (length(problems)) paste(problems, collapse = "; ") else TRUE
})

check("g", "negative control: the shuffled block is rejected", function() {
  if (block_adjacent(contract$negative_control, block_db)) {
    "negative control was accepted; block_adjacent() is not discriminating"
  } else {
    TRUE
  }
})

check("h", "report.R spending_raw_report_rows() SELECTs return the street columns", function() {
  # Runtime proof (preferred over a parse-level check): source the live
  # report.R the way test_report.R does, build a tiny in-memory database via
  # init_schema(), insert one contributions row and one corporate_giving row
  # carrying contributor_street_1, and confirm spending_raw_report_rows()
  # actually projects both street columns out.
  source(file.path(project_root, "R", "report.R"), local = .GlobalEnv)
  connection <- DBI::dbConnect(RSQLite::SQLite(), ":memory:")
  on.exit(DBI::dbDisconnect(connection), add = TRUE)
  init_schema(connection)
  DBI::dbExecute(
    connection,
    paste(
      "INSERT INTO contributions (",
      "sub_id, contributor_name, contributor_employer, contributor_occupation,",
      "contributor_street_1, contributor_street_2, contribution_receipt_date,",
      "contribution_receipt_amount, industry, company, matched_variant, phase, scraped_at",
      ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    ),
    params = list(
      "check-h-people-1", "DOE, JANE", "EXAMPLE EXCHANGE INC", "SOFTWARE ENGINEER",
      "123 MAIN ST", NA_character_, "2025-06-01",
      2500.0, "Crypto", "Example Exchange", "Example Exchange", "P1_employer",
      "2026-09-15T00:00:00Z"
    )
  )
  DBI::dbExecute(
    connection,
    paste(
      "INSERT INTO corporate_giving (",
      "sub_id, contributor_name, contributor_employer, contributor_occupation,",
      "contributor_street_1, contributor_street_2, contribution_receipt_date,",
      "contribution_receipt_amount, industry, company, matched_variant, cycle, scraped_at",
      ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    ),
    params = list(
      "check-h-corp-1", "ACME CORP", "", "",
      "456 OTHER AVE", NA_character_, "2025-06-02",
      100.0, "Crypto", "Example Exchange", "Example Exchange", 2026L,
      "2026-09-15T00:00:00Z"
    )
  )
  rows <- spending_raw_report_rows(connection, versioning_applied = TRUE)
  missing <- setdiff(street_columns, names(rows))
  if (length(missing)) {
    return(sprintf(
      "spending_raw_report_rows() is missing %s",
      paste(missing, collapse = ", ")
    ))
  }
  people_row <- rows[rows$sub_id == "check-h-people-1", ]
  actual_street_1 <- as.character(people_row$contributor_street_1[[1L]])
  if (!identical(actual_street_1, "123 MAIN ST")) {
    return(sprintf(
      "people row contributor_street_1 = %s, want 123 MAIN ST",
      actual_street_1
    ))
  }
  TRUE
})

cat(sprintf("test_raw_contribution_columns: %d check(s) failed\n", failures))
if (failures > 0L) {
  cat("test_raw_contribution_columns: FAIL\n")
  quit(status = 1L)
}
cat("test_raw_contribution_columns: PASS\n")

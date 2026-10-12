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
  "paths.R", "schema.R", "ingest.R", "collect.R", "reconcile.R", "report.R"
)) {
  source(file.path(project_root, "R", file), local = .GlobalEnv)
}
suppressPackageStartupMessages(library(data.table))

# Frozen row-wise implementation replaced by the indexed classifier. Keeping
# it here provides an executable, output-exact compatibility oracle.
spending_recipient_classification_reference <- function(
  connection,
  rows,
  lookups = NULL
) {
  if (!nrow(rows)) {
    return(data.table(
      recipient_category = character(),
      party = character(),
      category_source = character(),
      party_source = character()
    ))
  }
  lookups <- lookups %||% spending_report_lookups(connection)
  categories <- copy(lookups$committee_master$categories)
  trade <- copy(lookups$committee_master$trade)
  leadership <- copy(lookups$committee_master$leadership)
  companies <- lookups$companies
  company_variants <- split(companies$variant, companies$company)
  categories[, group_key := normalize_variant_key(group_name)]
  categories[, committee_id := toupper(trim_text(committee_id))]
  trade[, group_key := normalize_variant_key(association_name)]
  trade[, committee_id := toupper(trim_text(committee_id))]
  leadership[, group_key := normalize_variant_key(pac_name)]
  leadership[, committee_id := toupper(trim_text(committee_id))]

  records <- lapply(seq_len(nrow(rows)), function(index) {
    row <- rows[index]
    row_cycle <- as.integer(row$two_year_transaction_period[[1L]])
    row_committee_id <- toupper(trim_text(row$committee_id[[1L]]))
    committee_name <- row$committee_name[[1L]]
    row_committee_key <- normalize_variant_key(committee_name)
    category <- "Unclassified"
    party <- ""
    category_source <- "unclassified"
    party_source <- ""

    is_registered_conduit <- row_committee_id %in%
      c("C00401224", "C00694323") ||
      row_committee_key %in% c("ACTBLUE", "WINRED")
    category_match <- categories[
      cycle == row_cycle &
        (
          (nzchar(row_committee_id) & committee_id == row_committee_id) |
            (nzchar(row_committee_key) & group_key == row_committee_key)
        )
    ]
    leadership_match <- leadership[
      cycle == row_cycle &
        (
          (nzchar(row_committee_id) & committee_id == row_committee_id) |
            (nzchar(row_committee_key) & group_key == row_committee_key)
        )
    ]
    if (is_registered_conduit) {
      category <- "Conduit"
      category_source <- "fec:registered_conduit:earmark_platform"
    } else if (nrow(category_match)) {
      category <- category_match$category[[1L]]
      category_source <- if (
        nzchar(row_committee_id) &&
          identical(category_match$committee_id[[1L]], row_committee_id)
      ) {
        "categories:committee_id"
      } else {
        "categories:exact_name"
      }
      if (isTRUE(as.integer(category_match$provisional[[1L]]) == 1L)) {
        category_source <- paste0(
          category_source,
          ":provisional_2024_carry_forward"
        )
      }
      if (category_match$lean[[1L]] %in% c("R", "D")) {
        party <- category_match$lean[[1L]]
        party_source <- category_source
      }
    } else if (nrow(leadership_match)) {
      category <- "Leadership PAC"
      category_source <- if (
        nzchar(row_committee_id) &&
          identical(leadership_match$committee_id[[1L]], row_committee_id)
      ) {
        "leadership:committee_id"
      } else {
        "leadership:exact_name"
      }
      if (isTRUE(as.integer(leadership_match$provisional[[1L]]) == 1L)) {
        category_source <- paste0(category_source, ":provisional")
      }
      if (leadership_match$lean[[1L]] %in% c("R", "D")) {
        party <- leadership_match$lean[[1L]]
        party_source <- category_source
      }
    } else if (spending_company_pac_match(
      row$company[[1L]],
      committee_name,
      company_variants
    )) {
      category <- "Company PAC"
      category_source <- "company_pac:company_alias_and_pac"
    } else {
      trade_match <- trade[
        (nzchar(row_committee_id) & committee_id == row_committee_id) |
          (nzchar(row_committee_key) & group_key == row_committee_key)
      ]
      if (nrow(trade_match)) {
        category <- "Trade Association"
        category_source <- if (
          nzchar(row_committee_id) &&
            identical(trade_match$committee_id[[1L]], row_committee_id)
        ) {
          "trade:committee_id"
        } else {
          "trade:exact_name"
        }
      } else {
        committee_type <- toupper(trim_text(
          row$recipient_committee_type[[1L]]
        ))
        designation <- toupper(trim_text(
          row$recipient_committee_designation[[1L]]
        ))
        if (designation == "J") {
          category <- "JFC"
          category_source <- "fec:joint_fundraising_designation"
        } else if (designation == "D") {
          category <- "Leadership PAC"
          category_source <- "fec:leadership_designation"
        } else if (committee_type %in% c("X", "Y", "Z", "PARTY")) {
          category <- "Party Committee"
          category_source <- "fec:party_committee_type"
        } else if (committee_type %in% c("O", "U")) {
          category <- "Super PAC"
          category_source <- "fec:independent_expenditure_committee_type"
        } else if (committee_type %in% c("V", "W")) {
          category <- "Carey"
          category_source <- "fec:hybrid_committee_type"
        } else if (
          committee_type %in%
            c("H", "S", "P", "HOUSE", "SENATE", "PRESIDENTIAL") ||
            designation %in% c("A", "P")
        ) {
          category <- "Candidate"
          category_source <- "fec:committee_type_or_designation"
        }
      }
    }

    if (!nzchar(party)) {
      if (nrow(leadership_match) &&
          leadership_match$lean[[1L]] %in% c("R", "D")) {
        party <- leadership_match$lean[[1L]]
        party_source <- if (
          nzchar(row_committee_id) &&
            identical(leadership_match$committee_id[[1L]], row_committee_id)
        ) {
          "leadership:committee_id"
        } else {
          "leadership:exact_name"
        }
      }
    }
    data.table(
      recipient_category = category,
      party = party,
      category_source = category_source,
      party_source = party_source
    )
  })
  rbindlist(records, use.names = TRUE, fill = TRUE)
}

lookups <- list(
  committee_master = list(
    categories = data.table(
      cycle = c(2022L, 2024L, 2024L, 2024L, 2026L),
      group_name = c(
        "EARLY NAME", "CATEGORY ALPHA", "CATEGORY BETA",
        "LEADERSHIP PARTY BACKFILL", "PROVISIONAL CATEGORY"
      ),
      category = c(
        "527", "Candidate", "Party Committee", "Candidate", "501c"
      ),
      lean = c("", "D", "R", "", ""),
      committee_id = c(
        "C_EARLY", "C_CAT_A", "C_CAT_B", "C_NO_PARTY", "C_PROVISIONAL"
      ),
      provisional = c(0L, 0L, 0L, 0L, 1L)
    ),
    trade = data.table(
      association_name = c("TRADE EARLY", "TRADE LATE"),
      committee_id = c("C_TRADE_EARLY", "C_TRADE_LATE")
    ),
    leadership = data.table(
      cycle = c(2024L, 2024L, 2024L, 2026L),
      pac_name = c(
        "LEAD EARLY", "LEAD LATE", "LEADERSHIP PARTY BACKFILL",
        "PROVISIONAL LEAD"
      ),
      lean = c("R", "D", "R", "D"),
      committee_id = c("C_LEAD_EARLY", "C_LEAD_LATE", "C_LEAD_BACK", "C_LEAD_PROV"),
      provisional = c(0L, 0L, 0L, 1L)
    )
  ),
  companies = data.table(
    company = c("Alpha", "Alpha", "Beta"),
    variant = c("Alpha", "Alpha Holdings", "Beta")
  ),
  executives = data.table(company = character(), executive_name = character())
)

cases <- data.table(
  two_year_transaction_period = c(
    2024L, 2024L, 2024L, 2024L, 2024L, 2024L, 2024L, 2024L,
    2026L, 2024L, 2024L, 2024L, 2024L, 2024L, 2024L, 2024L,
    2024L, 2024L, 2024L, 2024L, 2024L, 2024L
  ),
  committee_id = c(
    "C00401224", "C_CAT_A", "", "C_CAT_B", "C_LEAD_EARLY",
    "C_LEAD_LATE", "", "C_ALPHA_PAC", "C_PROVISIONAL", "C_TRADE_EARLY",
    "C_TRADE_LATE", "", "", "", "", "", "", "", "C_NO_PARTY",
    "C_LEAD_BACK", "", ""
  ),
  committee_name = c(
    "NOT ACTBLUE", "UNRELATED", "CATEGORY BETA", "CATEGORY ALPHA",
    "UNRELATED", "LEAD EARLY", "ALPHA HOLDINGS PAC",
    "ALPHA POLITICAL ACTION COMMITTEE", "UNRELATED", "UNRELATED",
    "TRADE EARLY", "JOINT FUND", "LEADER PAC", "PARTY", "SUPER",
    "CAREY", "CANDIDATE", "UNCLASSIFIED", "LEADERSHIP PARTY BACKFILL",
    "LEADERSHIP PARTY BACKFILL", "WINRED", "PROVISIONAL LEAD"
  ),
  company = rep(c("Alpha", "Beta"), 11L),
  recipient_committee_type = c(
    "", "", "", "", "", "", "", "", "", "", "", "", "", "X", "O",
    "V", "H", "N", "", "", "", ""
  ),
  recipient_committee_designation = c(
    "", "", "", "", "", "", "", "", "", "", "", "J", "D", "", "",
    "", "", "", "", "", "", ""
  )
)

set.seed(20260723)
fixture_rows <- cases[sample.int(nrow(cases), 2000L, replace = TRUE)]
fixture_rows[, sub_id := sprintf("fixture-%06d", .I)]

reference_seconds <- unname(system.time(
  expected <- spending_recipient_classification_reference(
    NULL,
    fixture_rows,
    lookups
  )
)[["elapsed"]])
vector_seconds <- unname(system.time(
  observed <- spending_recipient_classification(
    NULL,
    fixture_rows,
    lookups
  )
)[["elapsed"]])
if (!identical(as.data.frame(observed), as.data.frame(expected))) {
  print(table(expected$recipient_category, observed$recipient_category))
  mismatch <- which(Reduce(
    `|`,
    Map(
      function(left, right) left != right,
      observed,
      expected
    )
  ))
  print(cbind(
    fixture_rows[mismatch[seq_len(min(20L, length(mismatch)))]],
    expected = expected[mismatch[seq_len(min(20L, length(mismatch)))]],
    observed = observed[mismatch[seq_len(min(20L, length(mismatch)))]]
  ))
  stop("Vectorized recipient classification changed output.", call. = FALSE)
}

# The registered earmark conduits are Conduit by committee ID or exact name, and
# only they: an ordinary hybrid (type V) committee named "CAREY" stays Carey.
conduit_case <- fixture_rows$committee_id == "C00401224" |
  fixture_rows$committee_name == "WINRED"
carey_case <- fixture_rows$committee_name == "CAREY"
stopifnot(
  any(conduit_case), any(carey_case),
  all(observed$recipient_category[conduit_case] == "Conduit"),
  all(observed$category_source[conduit_case] ==
    "fec:registered_conduit:earmark_platform"),
  all(observed$recipient_category[carey_case] == "Carey"),
  !"Conduit" %in% observed$recipient_category[!conduit_case]
)

entity_rows <- data.table(
  entity_type = rep(c("IND", "ORG", "COM", "IND"), 2500L),
  contributor_name = rep(
    c("ALPHA, INC.", "PERSON, PAT", "BETA", "ALPHA HOLDINGS LLC"),
    2500L
  ),
  company = rep(c("Alpha", "Alpha", "Beta", "Alpha"), 2500L)
)
company_variants <- split(
  lookups$companies$variant,
  lookups$companies$company
)
entity_reference_seconds <- unname(system.time(
  expected_entity <- vapply(seq_len(nrow(entity_rows)), function(index) {
    is_entity_contributor_value(
      entity_rows$entity_type[[index]],
      entity_rows$contributor_name[[index]],
      entity_rows$company[[index]],
      company_variants[[entity_rows$company[[index]]]] %||%
        entity_rows$company[[index]]
    )
  }, integer(1L))
)[["elapsed"]])
entity_vector_seconds <- unname(system.time(
  observed_entity <- spending_entity_contributor_flags(
    entity_rows,
    company_variants
  )
)[["elapsed"]])
stopifnot(identical(observed_entity, expected_entity))

titles <- rep(
  c(
    "CEO", "PRESIDENT", "VICE PRESIDENT", "DIRECTOR", "ENGINEER",
    "GENERAL COUNSEL", NA_character_
  ),
  length.out = 10000L
)
known <- rep(c(FALSE, FALSE, FALSE, TRUE), length.out = length(titles))
entities <- rep(c(FALSE, FALSE, TRUE, FALSE, FALSE), length.out = length(titles))
tier_reference_seconds <- unname(system.time(
  expected_tiers <- vapply(seq_along(titles), function(index) {
    spending_contributor_tier(
      titles[[index]],
      known[[index]],
      entities[[index]]
    )
  }, character(1L))
)[["elapsed"]])
tier_vector_seconds <- unname(system.time(
  observed_tiers <- spending_contributor_tiers(titles, known, entities)
)[["elapsed"]])
stopifnot(identical(observed_tiers, expected_tiers))

production_rows <- cases[sample.int(nrow(cases), 423000L, replace = TRUE)]
production_classification_seconds <- unname(system.time(
  production_classification <- spending_recipient_classification(
    NULL,
    production_rows,
    lookups
  )
)[["elapsed"]])
stopifnot(nrow(production_classification) == nrow(production_rows))
rm(production_classification)

cat(sprintf(
  paste0(
    "test_report_vectorization: classification_rows=%d reference=%.3fs ",
    "vector=%.3fs speedup=%.1fx entity_rows=%d reference=%.3fs ",
    "vector=%.3fs speedup=%.1fx tier_rows=%d reference=%.3fs ",
    "vector=%.3fs speedup=%.1fx production_rows=%d vector=%.3fs PASS\n"
  ),
  nrow(fixture_rows),
  reference_seconds,
  vector_seconds,
  reference_seconds / max(vector_seconds, .Machine$double.eps),
  nrow(entity_rows),
  entity_reference_seconds,
  entity_vector_seconds,
  entity_reference_seconds / max(
    entity_vector_seconds,
    .Machine$double.eps
  ),
  length(titles),
  tier_reference_seconds,
  tier_vector_seconds,
  tier_reference_seconds / max(tier_vector_seconds, .Machine$double.eps),
  nrow(production_rows),
  production_classification_seconds
))

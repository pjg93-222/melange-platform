script <- sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[[1L]])
root <- normalizePath(file.path(dirname(script), ".."), winslash = "/")
source(file.path(root, "tools", "recheck_attribution.R"))

cases <- list(
  list("Citibank", c("Citi", "Citibank"), c("CITI BANK", "CITI BANK N.A.", "CITIBANK NA"), "accepted"),
  list("Bank of America NA", c("Bank of America NA", "Bank of America", "BofA"),
       c("BANK OF AMERICA, N.A.", "BOFA NA", "BANK OF AMERICA NATIONAL ASSOCIATION"), "accepted"),
  list("US Bank NA", c("US Bank", "U.S. Bank", "US Bank NA"),
       c("U.S. BANK N. A.", "US BANK NATIONAL ASSOC"), "accepted"),
  list("JP Morgan Chase Bank NA", c("JP Morgan", "JP Morgan Chase Bank NA"),
       c("JP MORGAN CHASE", "JPMORGAN CHASE", "J.P. MORGAN CHASE"), "accepted"),
  list("JP Morgan Chase Bank NA", c("JP Morgan", "JP Morgan Chase Bank NA"),
       c("JP MORGAN CHASE CONTRACTOR", "JP MORGAN UNRELATED", "JPMORGAN CHASE CONSULTING"), "review"),
  list("Citibank", c("Citi", "Citibank"),
       c("CITIZENS BANK", "CITIZENS BUSINESS BANK", "CITI BANK RETIRED", "CITI BANK CONTRACTOR"), "review"),
  list("Wells Fargo Bank NA", c("Wells Fargo Bank NA", "Wells Fargo"),
       c("WELLS FARGO ADVISORS", "WELLS FARGO CLEARING SERVICES, LLC",
         "WELLS FARGO PRIVATE BANK", "WELLS FARGO DEALER SERVICES"), "accepted"),
  list("Wells Fargo Bank NA", c("Wells Fargo Bank NA", "Wells Fargo"),
       c("WELLS FARGO BANK RETIRED", "WELLS FARGO ADVISORS CONTRACTOR"), "review"),
  list("Capital One Bank NA", c("Capital One Bank NA", "Capital One"),
       c("CAPITAL ONE FINANCIAL CORP.", "CAPITAL ONE FINANCIAL CORPORATION"), "accepted"),
  list("PNC Bank NA", c("PNC Bank NA", "PNC"),
       c("PNC INVESTMENTS", "PNC FINANCIAL SERVICES GROUP"), "accepted"),
  list("Example", "Example", "EXAMPLE NATIONAL ASSOCIATION", "review")
)
for (case in cases) {
  scalar <- lapply(case[[3L]], function(employer) {
    company_employer_match(case[[1L]], employer, "PERSON, TEST", case[[2L]])
  })
  page <- company_employer_match_page(case[[1L]], case[[3L]], "PERSON, TEST", case[[2L]])
  stopifnot(identical(scalar, unname(page)), all(vapply(page, `[[`, "", "status") == case[[4L]]))
}
stopifnot(company_employer_match("Citibank", "CITI BANK", "", c("Citi", "Citibank"))$reason == "squashed_form_exact")
alias_evidence <- verified_employer_alias_evidence("Wells Fargo Bank NA", "WELLS FARGO ADVISORS")
stopifnot(alias_evidence$alias == "Wells Fargo Advisors", grepl("wellsfargo.com", alias_evidence$source_url))
# Full aliases are order-independent and cannot be shadowed by a shorter prefix.
stopifnot(identical(
  company_employer_match("Citibank", "CITI BANK", "", c("Citi", "Citibank")),
  company_employer_match("Citibank", "CITI BANK", "", c("Citibank", "Citi"))
))
# Spacing cannot bypass an existing ambiguous-brand or persistent rejection rule.
homes <- data.frame(company = "Gemini", state = "NY", source = "fixture")
for (matcher in list(company_employer_match, company_employer_match_page)) {
  match <- matcher("Gemini", "G E M I N I", "PERSON, TEST", "Gemini",
    contributor_state = "MN", contributor_occupation = "GRAPHIC DESIGN", home_states = homes)
  if (identical(matcher, company_employer_match_page)) match <- match[[1L]]
  stopifnot(match$status == "rejected", match$reason == "ambiguous_brand_non_home_state_manual_trade")
}
overrides <- data.frame(company = "Citibank", employer_key = "CITI BANK", decision = "reject",
  state = "", occupation_pattern = "")
stopifnot(company_employer_match_page("Citibank", "CITI BANK", "", c("Citi", "Citibank"),
  overrides = overrides)[[1L]]$reason == "persistent_override:reject")
stopifnot(company_employer_match_page("Citibank", "CITI BANK", "", c("Citi", "Citibank"),
  excluded_reason = "source_exclusion")[[1L]]$status == "rejected")

# End-to-end bridge reads roster scope, overrides and home-state constraints
# without modifying any byte of its source database.
fixture_dir <- tempfile("attribution_recheck_")
dir.create(fixture_dir)
database <- file.path(fixture_dir, "fixture.sqlite")
input <- file.path(fixture_dir, "input.json")
output <- file.path(fixture_dir, "output.json")
connection <- DBI::dbConnect(RSQLite::SQLite(), database)
DBI::dbWriteTable(connection, "companies", data.frame(
  industry = c("Banking", "Banking", "Crypto"),
  company = c("Citibank", "Citibank", "Gemini"), variant = c("Citi", "Citibank", "Gemini")))
DBI::dbWriteTable(connection, "company_match_overrides", overrides)
DBI::dbWriteTable(connection, "company_home_states", homes)
DBI::dbDisconnect(connection)
hash_before <- unname(tools::md5sum(database))
requests <- list(
  list(request_index = 0L, industry = "Banking", company = "Citibank", contributor_employer = "CITI BANK N.A."),
  list(request_index = 1L, industry = "Banking", company = "Citibank", contributor_employer = "CITIZENS BANK"),
  list(request_index = 2L, industry = "Banking", company = "Citibank", contributor_employer = "CITI BANK"),
  list(request_index = 3L, industry = "Crypto", company = "Gemini", contributor_employer = "G E M I N I",
       contributor_state = "MN", contributor_occupation = "GRAPHIC DESIGN"),
  list(request_index = 4L, industry = "Wrong Scope", company = "Citibank", contributor_employer = "Citibank")
)
jsonlite::write_json(requests, input, auto_unbox = TRUE)
bridge <- file.path(root, "tools", "recheck_attribution.R")
status <- system2(file.path(R.home("bin"), "Rscript.exe"),
  shQuote(c(bridge, "--database", database, "--input", input, "--output", output)),
  stdout = TRUE, stderr = TRUE)
stopifnot(is.null(attr(status, "status")), file.exists(output))
result <- jsonlite::read_json(output)
stopifnot(identical(unname(tools::md5sum(database)), hash_before), length(result) == length(requests),
  result[[1L]]$status == "accepted", result[[2L]]$status == "review",
  result[[3L]]$reason == "persistent_override:reject", result[[4L]]$status == "rejected",
  result[[5L]]$reason == "company_alias_register_missing")
cat("test_attribution_recheck: PASS\n")

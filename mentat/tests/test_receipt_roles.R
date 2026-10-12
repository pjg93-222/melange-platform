script <- sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[[1L]])
project_root <- normalizePath(file.path(dirname(script), ".."), winslash = "/")
for (file in c("paths.R", "schema.R", "ingest.R", "collect.R", "fec_api.R", "report.R")) {
  source(file.path(project_root, "R", file), local = .GlobalEnv)
}
suppressPackageStartupMessages(library(data.table))
rows <- data.table(
  contributor_name = c("ACME", "ACME EXCHANGE", "ACME", "ACME EXCHANGE", "ACME"),
  filing_form = c("F3X", "F3X", "F3X", "", "F3X"),
  line_number = c("11AI", "17", "17", "", "11AI"),
  receipt_type_full = c("IN-KIND - USDC NOT LIQUIDATED", "25M USDC SOLD VIA ACME - PURCHASER UNKNOWN",
                        "CONTRIBUTION PREVIOUSLY DISCLOSED", "", ""),
  is_memo_leg = c(0L, 0L, 1L, 0L, 0L)
)
roles <- fec_receipt_role(rows)
stopifnot(identical(roles, c("contribution", "asset_liquidation", "unverified_disclosure_memo", "unverified_receipt_role", "contribution")))
stopifnot(identical(fec_receipt_role_excluded(roles), c(FALSE, TRUE, FALSE, FALSE, FALSE)))
stopifnot(isTRUE(fec_assert_receipt_evidence(rows)))
unlinked <- data.table(filing_form="F3X", line_number="11AI", memo_code="X",
                      memo_text="CONTRIBUTION PREVIOUSLY DISCLOSED")
# An unproven "previously disclosed" claim is counted and flagged, not refused.
stopifnot(!fec_receipt_role_excluded(fec_receipt_role(unlinked)),
          fec_receipt_countable(unlinked),
          fec_receipt_role_unverified(fec_receipt_role(unlinked)) == 1L,
          fec_receipt_role_reason(fec_receipt_role(unlinked)) == "disclosure_memo_unmatched",
          isTRUE(fec_assert_receipt_evidence(unlinked)))
stopifnot(identical(fec_receipt_role(data.table(contributor_name=c("ACME", "ACME EXCHANGE", "ACME PROCESSOR"))),
                    rep("unverified_receipt_role", 3L)))
form_rows <- data.table(filing_form=c("F3P","F3P","F4","F4","F3X"),
                       line_number=c("17AI","20A","14A","17A","12"))
stopifnot(identical(fec_receipt_role(form_rows), c("contribution","other_receipt","contribution","other_receipt","transfer_receipt")))
stopifnot(identical(fec_receipt_countable(form_rows), c(TRUE,FALSE,TRUE,FALSE,TRUE)))
connection <- DBI::dbConnect(RSQLite::SQLite(), ":memory:")
init_schema(connection)
for (table in c("contributions", "corporate_giving")) {
  stopifnot(all(c("filing_form", "receipt_type_full", "memo_text", "line_number", "back_reference_transaction_id") %in% DBI::dbListFields(connection, table)))
}
unit <- list(industry = "Crypto", company = "Acme", company_variants = "Acme", exclusions = character(), cycle = 2024L)
api_rows <- list(list(sub_id = "fixture-1", contributor_name = "ACME", committee_id = "C00000001", entity_type = "ORG",
                     contribution_receipt_amount = 10, contribution_receipt_date = "2024-01-01", filing_form = "F3X",
                     line_number = "17", receipt_type_full = "CONTRIBUTION PREVIOUSLY DISCLOSED", memo_code = "X",
                     memoed_subtotal = TRUE, transaction_id = "MEMO1", back_reference_transaction_id = "ORIGINAL1"))
corporate <- fec_api_corporate_records(connection, unit, api_rows, match_overrides = data.frame(), home_states = data.frame())
stopifnot(corporate$is_memo_leg == 1L, corporate$receipt_type_full == "CONTRIBUTION PREVIOUSLY DISCLOSED")
# Mimic an old browser row, then hydrate the same sub-ID from the API.
legacy <- copy(corporate)
legacy[, `:=`(filing_form = "", receipt_type_full = "", memo_code = "", memoed_subtotal = 0L, is_memo_leg = 0L)]
checkpoint_corporate_page(connection, legacy, "Acme", 2024L, "fixture", 1L, 1L)
checkpoint_corporate_page(connection, corporate, "Acme", 2024L, "fixture", 1L, 1L)
stored <- DBI::dbReadTable(connection, "corporate_giving")
stopifnot(nrow(stored) == 1L, stored$is_memo_leg == 1L, stored$memoed_subtotal == 1L,
          stored$back_reference_transaction_id == "ORIGINAL1", stored$receipt_type_full == "CONTRIBUTION PREVIOUSLY DISCLOSED")
DBI::dbDisconnect(connection)
memos <- data.frame(sub_id=c("parent", "memo"), company="Acme", committee_id="C1",
  contributor_name="PERSON, ONE", contribution_receipt_date=c("2022-01-01","2024-01-01"),
  contribution_receipt_amount=c(100,90), is_memo_leg=c(0L,1L),
  transaction_id=c("SA11AI.42","MEMO1"), receipt_type_full=c("","[SA11AI:42]"),
  file_num=c(1,2), report_year=c(2022,2024), report_type="Q1", company_match_status="accepted",
  filing_form="F3X", line_number="11AI")
stopifnot(!any(mentat_memo_duplicate_plan(memos)$duplicate_memo))
missing_file <- memos; missing_file$file_num <- 0
stopifnot(!any(mentat_memo_duplicate_plan(missing_file)$duplicate_memo))
memos$file_num <- 2; memos$report_year <- 2024
memos$contribution_receipt_date <- "2024-01-01"
plan <- mentat_memo_duplicate_plan(memos)
stopifnot(plan$duplicate_memo[2] == 1L, plan$duplicate_of_sub_id[2] == "parent")
memos$receipt_type_full <- ""
memos$back_reference_transaction_id <- c("", "SA11AI.42")
plan <- mentat_memo_duplicate_plan(memos)
stopifnot(plan$duplicate_memo[2] == 1L, plan$duplicate_reason[2] == "fec_structured_back_reference")
memos$contribution_receipt_amount[2] <- 150
stopifnot(!any(mentat_memo_duplicate_plan(memos)$duplicate_memo))
memos$contribution_receipt_amount[2] <- -90
stopifnot(!any(mentat_memo_duplicate_plan(memos)$duplicate_memo))
memos$contribution_receipt_amount[2] <- 90
memos$contributor_name[2] <- "PERSON, DIFFERENT"
stopifnot(!any(mentat_memo_duplicate_plan(memos)$duplicate_memo))
memos$contributor_name[2] <- "PERSON, ONE"
memos$company_match_status[1] <- "rejected"
stopifnot(!any(mentat_memo_duplicate_plan(memos)$duplicate_memo))
memos$company_match_status[1] <- "accepted"; memos$line_number[1] <- "17"
stopifnot(!any(mentat_memo_duplicate_plan(memos)$duplicate_memo))
memos$line_number[1] <- "11AI"
ambiguous <- rbind(memos,memos[1,]); ambiguous$sub_id[3] <- "second-parent"
stopifnot(!any(mentat_memo_duplicate_plan(ambiguous)$duplicate_memo))
memos$back_reference_transaction_id <- ""; memos$contribution_receipt_amount <- 100
stopifnot(!any(mentat_memo_duplicate_plan(memos)$duplicate_memo))
qa <- data.table(company_match_status="accepted", excluded_reason="", duplicate_memo=0L,
                 filing_form="F3X", line_number=c("11AI","17"), contribution_receipt_amount=c(100,190000),
                 memo_text=c("", "RETURN OF ERRONEOUS DEBIT MADE BY PROCESSOR"))
stopifnot(sum(qa$contribution_receipt_amount[fec_receipt_countable(qa)]) == 100)
specials <- data.table(filing_form="F3X", line_number="17", receipt_type_full=c(
  "NON-CONTRIBUTION ACCOUNT", "LEGAL PROCEEDINGS ACCOUNT CONTRIBUTION", "HEADQUARTERS ACCOUNT",
  "NON-CONTRIBUTION", "OTHER FEDERAL RECEIPTS (DIVIDENDS, INTEREST, ETC.)", "INTEREST",
  "NON-CONTRIBUTION ACCOUNT; 1500000 USDC LIQUIDATED VIA EXCHANGE - PURCHASER UNKNOWN",
  "NON-CONTRIBUTION ACCOUNT; REFUND FOR PROCESSING FEES", "LEGAL PROCEEDINGS ACCOUNT CONTRIBUTION"),
  memo_text=c(rep("",8),"IMPERMISSIBLE CONTRIBUTION; SEE JUNE MONTHLY REPORT FOR REFUND"))
stopifnot(identical(fec_receipt_role(specials), c(rep("contribution",3),
  "other_receipt","unverified_other_receipt","other_receipt","asset_liquidation",
  "unverified_other_receipt","unverified_refund_adjustment")))
stopifnot(identical(fec_receipt_countable(specials), c(TRUE,TRUE,TRUE,FALSE,TRUE,FALSE,FALSE,TRUE,TRUE)))
stopifnot(identical(fec_receipt_countable(specials,verified_only=TRUE), c(TRUE,TRUE,TRUE,rep(FALSE,6))))
explicit_income <- data.table(filing_form="F3X", line_number="17", receipt_type_full=c(
  "NON-CONTRIBUTION: INTEREST", "NONCONTRIBUTION ACCOUNT - INTEREST", "HEADQUARTERS INTEREST INCOME",
  "RECOUNT INTEREST INCOME", "PAC BANK INTEREST", "CD INTEREST INCOME", "MONTHLY INTEREST",
  "INVESTMENT ACCOUNT INTEREST", "INTEREST/DIVIDENDS", "INVESTMENT INCOME"))
stopifnot(all(fec_receipt_role(explicit_income) == "other_receipt"),
          !any(fec_receipt_countable(explicit_income)))
account_income <- data.table(filing_form="F3X", line_number="17", receipt_type_full="INTEREST INCOME",
  memo_text=c("NON-CONTRIBUTION ACCOUNT", "NONCONTRIBUTION ACCOUNT", "HEADQUARTERS ACCOUNT",
              "LEGAL PROCEEDINGS ACCOUNT", "RECOUNT ACCOUNT", "CONVENTION ACCOUNT", "NON-CONTRIBUTION"))
stopifnot(all(fec_receipt_role(account_income) == "other_receipt"))
ambiguous_income <- data.table(filing_form="F3X", line_number="17", receipt_type_full=c(
  "OTHER FEDERAL RECEIPTS (DIVIDENDS, INTEREST, ETC.)", "INVESTMENT OPPORTUNITY",
  "INTEREST IN PUBLIC POLICY"))
stopifnot(all(fec_receipt_role(ambiguous_income) == "unverified_other_receipt"))
explicit_financial_events <- data.table(filing_form="F3X", line_number="17", receipt_type_full=c(
  "INTEREST RECEIVED", "INVESTMENT CHANGE IN VALUE", "PAC INVESTMENT CHANGE IN VALUE",
  "INVESTMENT LOSS", "BANK INTEREST LOST", "GAIN ON SALE OF STOCK", "LOSS ON SALE OF BOND",
  "REINVESTED DIVIDEND", "UNREALIZED INVESTMENT GAINS", "NET INVESTMENT EARNINGS",
  "INTEREST/DIVIDENDS INCOME PLUS UNREALIZED LOSS ON INVESTMENTS",
  "INTEREST, DIVIDENDS, CAPITAL GAIN (LOSS)", "SALE OF INVESTMENT",
  "NET INVESTMENT EARNINGS/LOSS", "SERVICE FEE", "JANUARY INTEREST",
  "PROCEEDS FROM SALE OF 8 SHS VANGUARD REAL ESTATE", "CREDIT CARD REWARDS",
  "MUTUAL FUND INVESTMENT ACTIVITY FOR PERIOD 1-1-26 THROUGH 3-31-26",
  "INTEREST FOR THE MONTH OF AUGUST 2023", "NTEREST INCOME",
  "DUPLICATE BANK FEE CREDIT", "ONE TIME ADJUSTMENT",
  "COMMERCIAL CARD REBATE", "VENDOR REFUND", "RETURN OF FRAUDULENT DISBURSEMENT - SEE MEMO TEXT",
  "REFUND OF 9/18/22 FRAUDULENT DISBURSEMENT",
  "CREDIT FOR UNAUTHORIZED DISBURSEMENT ON 2/20/2024",
  "RETURNED EXAMPLE CONSULTING BILL PAYMENT",
  "REFUND OF ERRONEOUS BANK FEES FROM MAY 2023",
  "REFUND OF UNAUTHORIZED TRANSACTION THAT OCCURRED ON 07/31/2023",
  "BANK ERRONEOUSLY DEPOSITED 251 17 INTO PAC ACCOUNT THIS ERROR WILL BE REVERSED",
  "REVERSAL OF 08/27 ARKPAC FOR SEN. JOHN BOOZMAN CONTRIBUTION",
  "BANK REFUND OF STOLEN 7/26/21 CONTRIBUTION TO EXAMPLE FOR CONGRESS",
  "CREDIT FOR UNAUTHORIZED ACTIVITY", "RECOVERED FUNDS FROM BANK FRAUD", "NON-CONTRIBUTION AMOUNT",
  "REFUND OF BANK FEES ERRONEOUSLY CHARGED", "REFUND OF MERCHANT FEES",
  "REFUND OF CHECK ORDER FEE", "BANK STOP PAYMENT FEE REFUND", "BANK FEE REVERSAL",
  "BANK FEE REFUND", "INTEREST DISTRIBUTION FUND", "INTEREST (DISTRIBUTION FUND)",
  "INTEREST EARNE", "INTEREST EARNER", "JULY INTEREST EARNED",
  "OCTOBER MONTHLY INTEREST", "ACCOUNT INTEREST", "INTEREST FROM BANK ACCT",
  "INTEREST ON DEPOSIT ACCT", "INTEREST ON BANK ACCOUNT", "CASH BACK REWARD",
  "JPM TEST DEPOSIT", "NON-CONTRIBUTORY ACCOUNT RECEIPT",
  "BANK ERROR DEPOSIT. HOPE TO HAVE THE BANK REVERSE THE ERROR IN APRIL 2022.",
  "BANK DEPOSIT ERROR - REFUNDED 08/10/2022", "INTEREST ($0.32)", "TEST DEPOSIT"))
stopifnot(all(fec_receipt_role(explicit_financial_events) == "other_receipt"))
stopifnot(fec_receipt_role(data.table(filing_form="F3X", line_number="17",
  receipt_type_full="JFC TRANSFER: EXAMPLE JOINT FUNDRAISING COMMITTEE")) == "transfer_receipt")
stopifnot(fec_receipt_role(data.table(filing_form="F3X", line_number="17",
  receipt_type_full="TRANSFER", memo_text="TRANSFER FROM EXAMPLE JFC")) == "transfer_receipt")
stopifnot(fec_receipt_role(data.table(filing_form="F3X", line_number="17",
  receipt_type_full="REVERSAL OF EXAMPLE CONTRIBUTION", memo_text="CONTRIBUTION")) ==
  "unverified_contradicted_receipt")
stopifnot(fec_receipt_role(data.table(filing_form="F3X", line_number="17",
  receipt_type_full="CONTRIBUTION", memo_text="BANK FEE REFUND")) ==
  "unverified_contradicted_receipt")
conflicting_income <- data.table(filing_form="F3X", line_number="17", receipt_type_full=c(
  "CONTRIBUTION", "HEADQUARTERS ACCOUNT CONTRIBUTION", "NON-CONTRIBUTION ACCOUNT DONATION"),
  memo_text="INTEREST INCOME")
stopifnot(all(fec_receipt_role(conflicting_income) == "unverified_contradicted_receipt"))
conflicting_income[, c("receipt_type_full", "memo_text") := .(memo_text, receipt_type_full)]
stopifnot(all(fec_receipt_role(conflicting_income) == "unverified_contradicted_receipt"))

# --- 2026-09-17 ruling ------------------------------------------------------
# A cross-verification step may remove or zero a row only when the evidence
# CONTRADICTS it. Missing or ambiguous information is counted and flagged.
stopifnot(fec_receipt_rules()$version == 6)
# True exact non-giving descriptions are still excluded.
stopifnot(all(fec_receipt_role(data.table(filing_form="F3X", line_number="17",
  receipt_type_full=c("NON-CONTRIBUTION", "INTEREST", "SERVICE FEE"))) == "other_receipt"))
# Redesignations, bank artifacts and fair-market-value sales: the filer's own
# words say the money is not new giving.
non_giving_v5 <- data.table(filing_form="F3X", line_number="17", receipt_type_full=c(
  "CONTRIBUTION REDESIGNATION FROM GENERAL", "REDESIGNATION TO RECOUNT",
  "NON CONFORMING IMAGE CHECK REDEPOSIT", "NON CONFIRMING CHECK REDEPOSIT",
  "ACH FAILURE TO SETTLE RETURN", "DUPLICATE TRANSACTION SENT IN ERROR RETURNED ON 3/13/25",
  "STALE CHECK", "MISDIRECTED WIRE REFUND", "CREDIT",
  "OVERPAYMENT OF PAYROLL TO BE CORRECTED IN AUGUST",
  "REFUND FOR WORKERS COMPENSATION INSURANCE", "VOTER FILE PURCHASE AT FAIR MARKET VALUE"))
stopifnot(all(fec_receipt_role(non_giving_v5) == "other_receipt"),
          !any(fec_receipt_countable(non_giving_v5)))
# A redesignation split across the two description fields is one claim about
# one gift, not a contradiction, and it is excluded rather than refused.
split_redesignation <- data.table(filing_form="F3", line_number="15",
  receipt_type_full="CONTRIBUTION", memo_text="REDESIGNATION FROM GENERAL")
stopifnot(fec_receipt_role(split_redesignation) == "other_receipt",
          !fec_receipt_countable(split_redesignation),
          isTRUE(fec_assert_receipt_evidence(split_redesignation)))
# Special accounts the v5 ruleset now names: a contribution on one is giving.
stopifnot(identical(fec_receipt_role(data.table(filing_form="F3X", line_number="17",
  receipt_type_full=c("RECOUNT LEGAL FUND CONTRIBUTION", "LEGAL FUND CONTRIBUTION",
                      "HEADQUARTERS BUILDING FUND CONTRIBUTION", "IE ONLY ACCOUNT CONTRIBUTION"))),
  rep("contribution", 4L)))
# A non-federal transfer in is a transfer, not a new gift.
stopifnot(fec_receipt_role(data.table(filing_form="F3X", line_number="17",
  receipt_type_full="NON-FEDERAL CONTRIBUTION TRANSFERRED FROM STATE ACCOUNT")) ==
  "transfer_receipt")
# F13 inaugural-committee donations count.
f13 <- data.table(filing_form="F13", line_number="F132", receipt_type_full="")
stopifnot(fec_receipt_role(f13) == "contribution", fec_receipt_countable(f13),
          fec_receipt_role_unverified(fec_receipt_role(f13)) == 0L)
# A blank line number is recovered from the transaction id prefix.
stopifnot(fec_receipt_role(data.table(filing_form="F3X", line_number="",
  transaction_id="SA17.621125")) == "unverified_other_receipt")
stopifnot(fec_receipt_role(data.table(filing_form="F3X", line_number="",
  transaction_id="SA11AI.42")) == "contribution")
# Blank line and no derivable line: counted, flagged, never refused.
blank_line <- data.table(filing_form="F3X", line_number="",
  transaction_id="E966384FC6024738878E", contribution_receipt_amount=8092475)
stopifnot(fec_receipt_role(blank_line) == "unverified_receipt_role",
          fec_receipt_countable(blank_line),
          isTRUE(fec_assert_receipt_evidence(blank_line)))
# A blank description on an other-receipts line is missing information.
blank_description <- data.table(filing_form=c("F3X","F3","F3P","F4"),
  line_number=c("17","15","21","19A"), receipt_type_full="", memo_text="")
blank_roles <- fec_receipt_role(blank_description)
stopifnot(all(blank_roles == "unverified_other_receipt"),
          all(fec_receipt_countable(blank_description)),
          all(fec_receipt_role_unverified(blank_roles) == 1L),
          all(fec_receipt_role_reason(blank_roles) == "unverified_other_receipt"),
          isTRUE(fec_assert_receipt_evidence(blank_description)))
# A disclosure memo is excluded only when the payload PROVES the repeat.
disclosure <- data.table(sub_id=c("parent","memo"), filing_form="F3X",
  line_number=c("11AI","17"), receipt_type_full=c("CONTRIBUTION",
  "24,999,995 USDC CONTRIBUTION PREVIOUSLY DISCLOSED"), memo_code=c("","X"),
  committee_id="C00835959", two_year_transaction_period=2024L,
  contribution_receipt_amount=c(24999995, 24999995))
stopifnot(identical(fec_receipt_role(disclosure), c("contribution","unverified_disclosure_memo")),
          identical(fec_receipt_countable(disclosure), c(TRUE, TRUE)),
          isTRUE(fec_assert_receipt_evidence(disclosure)))
unpaired <- copy(disclosure)
unpaired[, contribution_receipt_amount := c(100, 24999995)]
unpaired_roles <- fec_receipt_role(unpaired)
stopifnot(identical(unpaired_roles, c("contribution","unverified_disclosure_memo")),
          all(fec_receipt_countable(unpaired)),
          identical(fec_receipt_role_unverified(unpaired_roles), c(0L, 1L)),
          identical(fec_receipt_role_reason(unpaired_roles), c("", "disclosure_memo_unmatched")),
          isTRUE(fec_assert_receipt_evidence(unpaired)))
# A different committee is not a proven repeat either.
other_committee <- copy(disclosure)
other_committee[, committee_id := c("C00000001", "C00835959")]
stopifnot(fec_receipt_role(other_committee)[[2L]] == "unverified_disclosure_memo")
# CONTRADICTION is the only thing that still stops the export.
contradicted <- data.table(sub_id=c("ok","bad"), filing_form="F3X", line_number="17",
  receipt_type_full=c("CONTRIBUTION","CONTRIBUTION"), memo_text=c("","BANK FEE REFUND"))
contradicted_roles <- fec_receipt_role(contradicted)
stopifnot(identical(contradicted_roles,
                    c("unverified_other_receipt","unverified_contradicted_receipt")),
          identical(fec_receipt_role_contradicted(contradicted_roles), c(FALSE, TRUE)),
          all(fec_receipt_countable(contradicted)))
refusal <- try(fec_assert_receipt_evidence(contradicted), silent=TRUE)
refusal_text <- conditionMessage(attr(refusal, "condition"))
stopifnot(inherits(refusal, "try-error"),
          grepl("contradicts itself for 1 rows", refusal_text, fixed=TRUE),
          grepl("examples: bad", refusal_text, fixed=TRUE))
cat("test_receipt_roles: PASS\n")

# Search status is not an attribution verdict. Shared policy counterexamples.
attribution <- data.table(company="Acme", entity_type="IND", company_match_status="rejected",
  company_match_reason="employer_prefix_not_verified", excluded_reason="",
  contributor_employer=c("CONSULTANT", "REFUSED", "NOTEMPLOYED", "ACME", "OTHER BANK", "UNKNOWN LABEL"))
stopifnot(identical(fec_attribution_status(attribution),
  c("unverified", "unverified", "unverified", "company_named_unverified", "contradicted_by_reported_organization", "unverified")))
stopifnot(identical(fec_receipt_countable(attribution), c(TRUE,TRUE,TRUE,TRUE,FALSE,TRUE)))
attribution[1, company_match_reason := "persistent_override:reject"]
stopifnot(!fec_receipt_countable(attribution)[1])
attribution[1, `:=`(company_match_reason="blank_employer", excluded_reason="occupation:HOCKEY")]
stopifnot(fec_receipt_countable(attribution)[1])
attribution[1, excluded_reason := "exclusion_pattern:UNRELATED COMPANY"]
stopifnot(!fec_receipt_countable(attribution)[1])
cat("Attribution evidence policy: PASS\n")

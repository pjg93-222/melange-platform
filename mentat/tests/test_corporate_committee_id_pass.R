# The corporate pass searches Schedule A by contributor NAME, and FEC's name
# search is token-based. Ken Calvert's committee files the SchoolsFirst PAC
# (C00390633) as "SCHOOLS FIRST FCU EMPLOYEES FEDERAL PAC", which no roster
# variant matches (sub_id 4050720241921815426, $3,300, 2024-03-26), so the
# receipt was never stored. The committee-id pass must re-query by the id that
# a NAMED stored row carries, and only by such ids.
script <- sub('^--file=', '', grep('^--file=', commandArgs(FALSE), value=TRUE)[[1L]])
root <- normalizePath(file.path(dirname(script), '..'), winslash='/', mustWork=TRUE)
for (name in c('paths','schema','ingest','collect','fec_api'))
  source(file.path(root,'R',paste0(name,'.R')))
data.table::setDTthreads(2L)
company <- 'SchoolsFirst Federal Credit Union'
connection <- DBI::dbConnect(RSQLite::SQLite(), ':memory:')
init_schema(connection)
variants <- c('SchoolsFirst Federal Credit Union','SchoolsFirst FCU','SchoolsFirst',
  'Schools First Federal Credit Union','Orange County Teachers Federal Credit Union','OCTFCU')
for (i in seq_along(variants)) DBI::dbExecute(connection, paste(
  "INSERT INTO companies (industry,company,parent,variant,variant_order,confidence,source_sheet,source_row)",
  "VALUES (?,?,?,?,?,'normal','Fixture',?)"), params=list(MENTAT_INDUSTRY,company,company,variants[[i]],i,i))
stored <- function(sub_id, contributor_id, name, entity, committee) data.table::data.table(
  sub_id=sub_id, contributor_id=contributor_id, contributor_name=name, entity_type=entity,
  committee_id=committee, contribution_receipt_date='2024-09-20', contribution_receipt_amount=5000,
  industry=MENTAT_INDUSTRY, company=company, cycle=2024L, company_match_status='review',
  company_match_reason='corporate_name_not_verified',
  matched_variant='SchoolsFirst', scraped_at='2026-07-28T21:05:48.000-0400')
seed <- data.table::rbindlist(list(
  # A named PAC row: its committee id seeds the id pass.
  stored('4112120241071831203','C00390633','SCHOOLSFIRST FEDERAL CREDIT UNION EMPLOYEES FEDERAL PAC','PAC','C00850420'),
  # A committee row that does not name the company must never seed a query.
  stored('4021320241824642115','C00999999','AMERICAN SECURITY PAC','PAC','C00257337'),
  # An individual never seeds a query.
  stored('4000000000000000001','C00000001','ALVAREZ, SCHOOLSFIRST','IND','C00257337')))
insert_rows_or_ignore(connection, 'corporate_giving', seed, names(seed))
rosters <- fec_api_company_rosters(connection)
units <- fec_api_corporate_id_units(connection, 2024L, rosters=rosters)
stopifnot(length(units)==1L, identical(units[[1L]]$variants,'C00390633'),
  identical(units[[1L]]$query_field,'contributor_id'), identical(units[[1L]]$kind,'corporate'),
  startsWith(units[[1L]]$id,'P2_company_name:'))
spec <- fec_api_request_spec(units[[1L]])
stopifnot(identical(spec$params$contributor_id,'C00390633'), is.null(spec$params$contributor_name),
  identical(spec$params$two_year_transaction_period,'2024'))
# End to end: the FEC answer by id is the two receipts; the Calvert one is new.
body <- paste0('{"results":[',
  '{"sub_id":4050720241921815426,"contributor_id":"C00390633",',
  '"contributor_name":"SCHOOLS FIRST FCU EMPLOYEES FEDERAL PAC","entity_type":"COM",',
  '"committee_id":"C00257337","committee_name":"KEN CALVERT FOR CONGRESS COMMITTEE",',
  '"filing_form":"F3","line_number":"11C","receipt_type":"15K","amendment_indicator":"A",',
  '"contribution_receipt_date":"2024-03-26","contribution_receipt_amount":3300,"two_year_transaction_period":2024},',
  '{"sub_id":4112120241071831203,"contributor_id":"C00390633",',
  '"contributor_name":"SCHOOLSFIRST FEDERAL CREDIT UNION EMPLOYEES FEDERAL PAC","entity_type":"PAC",',
  '"committee_id":"C00850420","committee_name":"CISNEROS FOR CONGRESS",',
  '"filing_form":"F3","line_number":"11C","receipt_type":"15K","amendment_indicator":"N",',
  '"contribution_receipt_date":"2024-09-20","contribution_receipt_amount":5000,"two_year_transaction_period":2024}],',
  '"pagination":{"count":2,"is_count_exact":true,"last_indexes":null}}')
before <- DBI::dbGetQuery(connection,'SELECT sub_id,contribution_receipt_amount,company FROM corporate_giving ORDER BY sub_id')
lookups <- list(match_overrides=company_match_overrides_data(connection),
  home_states=company_home_states_data(connection),
  trusted_by_company=stats::setNames(list(character()), company))
invisible(fec_api_collect_checkpointed(connection, units, 'fixture', 1L,
  transport=function(specs,...) lapply(specs,function(spec) {
    stopifnot(identical(spec$params$contributor_id,'C00390633'))
    list(status=200L,body=body,headers=list())}),
  sleep=function(...) stop('Unexpected retry'), jitter=function(...) 0,
  before_request=function(...) NULL, lookup_cache=lookups))
after <- DBI::dbGetQuery(connection,'SELECT sub_id,contribution_receipt_amount,company,contributor_name FROM corporate_giving ORDER BY sub_id')
added <- after[!after$sub_id %in% before$sub_id,]
stopifnot(nrow(added)==1L, identical(added$sub_id,'4050720241921815426'),
  added$contribution_receipt_amount==3300, identical(added$company,company))
# Equivalence: every row stored before keeps its amount and company.
kept <- merge(before, after, by='sub_id')
stopifnot(nrow(kept)==nrow(before), all(kept$contribution_receipt_amount.x==kept$contribution_receipt_amount.y),
  all(kept$company.x==kept$company.y))
# The new row is attributed by the ordinary name rules, not by the id: it counts.
row <- DBI::dbGetQuery(connection,"SELECT * FROM corporate_giving WHERE sub_id='4050720241921815426'")
status <- fec_attribution_status(data.table::as.data.table(row),
  list(`SchoolsFirst Federal Credit Union`=variants))
stopifnot(identical(status,'company_named_unverified'))
DBI::dbDisconnect(connection)
cat('corporate committee-id pass: OK\n')

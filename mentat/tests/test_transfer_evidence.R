script <- sub('^--file=', '', grep('^--file=', commandArgs(FALSE), value=TRUE)[[1L]])
project <- normalizePath(file.path(dirname(script), '..'), winslash='/', mustWork=TRUE)
for (name in c('paths', 'ingest', 'collect', 'fec_beneficiary', 'fec_transfer_graph'))
  source(file.path(project, 'R', paste0(name, '.R')))
data.table::setDTthreads(2L)

check_transfer_evidence <- function() {
  directory <- tempfile('mentat-transfer-evidence-')
  dir.create(file.path(directory, 'pas224'), recursive=TRUE)
  on.exit(unlink(directory, recursive=TRUE))
  path <- file.path(directory, 'pas224', 'itpas2.txt')
  row <- function(date='12312023', file='1', sub='1', kind='24K', amendment='N') {
    fields <- rep('', 22L)
    fields[c(1,2,3,6,14,15,16,17,18,19,22)] <-
      c('C00000001', amendment, 'Q1', kind, date, '100', 'C00000002', 'H00000001', 'T1', file, sub)
    paste(fields, collapse='|')
  }
  aggregate <- function(index) {
    index[, `:=`(candidate_name='Candidate', candidate_party='Other', office='H',
      office_label='House', office_state='CA', district='1', candidate_source_url='fixture')]
    fec_aggregate_downstream_activity(index, data.table::data.table(cycle=2024L,
      committee_id='C00000001', committee_name='PAC', recipient_category='PAC'))
  }
  writeLines(row(), path)
  indexed <- fec_build_pas2_index(directory, 2024L)
  retained <- aggregate(indexed)
  evidence <- jsonlite::fromJSON(retained$transfer_records[[1L]])
  stopifnot(nrow(evidence)==1L, evidence$transaction_id=='T1', evidence$file_num==1,
    evidence$destination=='C00000002', evidence$cents==10000,
    retained$preliminary_included_amount==100)
  # An amended date with no filing header. The default retains both observations
  # and flags them; `withhold` keeps neither row. Both readings are pinned here.
  writeLines(c(row(), row(date='01012024',file='2',sub='2',amendment='A')), path)
  stopifnot(identical(FEC_PAS2_AMBIGUOUS_VERSION_POLICY, 'count_all'))
  counted <- aggregate(fec_build_pas2_index(directory, 2024L))
  stopifnot(counted$unresolved_version_rows==0L, counted$unverified_version_rows==2L,
    counted$preliminary_included_amount==200, counted$superseded_rows==0L)
  FEC_PAS2_AMBIGUOUS_VERSION_POLICY <<- 'withhold'
  redated <- aggregate(fec_build_pas2_index(directory, 2024L))
  stopifnot(redated$unresolved_version_rows==2L, redated$preliminary_included_amount==0,
    redated$transfer_records=='[]')
  writeLines(c(row(), row(file='2',sub='2',kind='24Z',amendment='A')), path)
  reclassified <- fec_build_pas2_index(directory, 2024L)
  stopifnot(nrow(reclassified)==2L, all(reclassified$include_in_total=='No'))
  FEC_PAS2_AMBIGUOUS_VERSION_POLICY <<- 'count_latest'
  # The reclassified 24K is never counted beside the 24Z that replaced it.
  reclassified_default <- fec_build_pas2_index(directory, 2024L)
  stopifnot(nrow(reclassified_default)==2L,
    sum(reclassified_default$include_in_total=='Yes')==1L,
    identical(reclassified_default[transaction_type=='24Z', include_in_total], 'Yes'))

  dir.create(file.path(directory, 'oth24'))
  info <- file.path(directory, 'oth24', 'transfer-info.json')
  jsonlite::write_json(list(version=2), info, auto_unbox=TRUE)
  roots <- data.table::data.table(cycle=2024L,committee_id='C00000001',preliminary_crypto_funding=100)
  links <- data.table::data.table(cycle=2024L,committee_id='C00000002',designation='P')
  stale <- fec_transfer_context(directory, roots, links, 2024L)
  stopifnot(stale$coverage[[1L]]$status=='stale_transfer_index', nrow(stale$edges)==0L)
  edges <- file.path(dirname(info), 'transfer-edges.csv')
  data.table::fwrite(data.table::data.table(cycle=2024L,source='C00000001',destination='C00000002',
    amount=100,records='[]'),edges)
  # An index built under the old identity-free rule is stale now.
  jsonlite::write_json(list(version=3,edges_sha256=digest::digest(file=edges,algo='sha256')),
    info,auto_unbox=TRUE)
  stopifnot(fec_transfer_context(directory,roots,links,2024L)$coverage[[1L]]$status=='stale_transfer_index')
  jsonlite::write_json(list(version=6,ambiguous_version_policy='count_all',edges_sha256=digest::digest(file=edges,algo='sha256'),
    unresolved_sources=list('C00000001'),unresolved_source_amounts=list(C00000001=90),
    unresolved_all_sources=FALSE,counts=list(unresolved_rows=2)),
    info,auto_unbox=TRUE)
  current <- fec_transfer_context(directory,roots,links,2024L)
  encoded <- jsonlite::toJSON(current$coverage,auto_unbox=TRUE)
  stopifnot(grepl('"unresolved_sources":\\["C00000001"\\]',encoded),
            grepl('"unresolved_source_amounts":\\{"C00000001":90\\}',encoded))
  args <- commandArgs(TRUE)
  if (length(args)) jsonlite::write_json(list(downstream_activity=retained),args[[1L]],
    dataframe='rows',auto_unbox=TRUE,digits=NA)
}
check_transfer_evidence()
cat('Transfer evidence: retained identities, amendment review, cache version and coverage serialization PASS\n')
# 2026-09-25, cycle 2020 Crypto: tools/fec_transfer_graph.py publishes
# transfer-edges.csv with Python's csv module, which quotes the JSON `records`
# field and doubles every quote inside it. data.table::fread returned the field
# with the doubled quotes still in it, and enrich_downstream.py's json.loads
# refused the payload. These are the bytes the csv module writes, CRLF included.
check_edge_records_stay_json <- function() {
  directory <- tempfile('mentat-edge-records-')
  dir.create(file.path(directory, 'oth24'), recursive=TRUE)
  on.exit(unlink(directory, recursive=TRUE))
  edges <- file.path(directory, 'oth24', 'transfer-edges.csv')
  publish <- function(value) {
    field <- paste0('"', gsub('"', '""', value, fixed=TRUE), '"')
    writeBin(charToRaw(paste0('cycle,source,destination,amount,record_count,first_date,last_date,records\r\n',
      '2024,C00000001,C00000002,100.0,1,2024-01-02,2024-01-02,', field, '\r\n')), edges)
    jsonlite::write_json(list(version=6, ambiguous_version_policy='count_all',
      edges_sha256=digest::digest(file=edges, algo='sha256'), counts=list()),
      file.path(directory, 'oth24', 'transfer-info.json'), auto_unbox=TRUE)
  }
  roots <- data.table::data.table(cycle=2024L, committee_id='C00000001', preliminary_crypto_funding=100)
  links <- data.table::data.table(cycle=2024L, committee_id='C00000002', designation='P')
  # An empty sub-ID is an empty JSON string, four quotes in the CSV.
  evidence <- '[{"file_num":1,"transaction_id":"T1","sub_id":"","date":"2024-01-02","cents":10000,"type":"24K"}]'
  publish(evidence)
  read <- fec_transfer_context(directory, roots, links, 2024L)$edges
  stopifnot(nrow(read) == 1L, identical(read$records, evidence), read$amount == 100)
  # A reader that does undo the escape changes nothing: JSON is kept as it is.
  stopifnot(identical(fec_transfer_edge_records(evidence, edges), evidence))
  # A value that is still not JSON stops the export; evidence is never dropped.
  publish('[{"file_num":1,"cents":')
  failed <- tryCatch(fec_transfer_context(directory, roots, links, 2024L), error=identity)
  stopifnot(inherits(failed, 'error'), grepl('not JSON', conditionMessage(failed)))
}
check_edge_records_stay_json()
cat('Transfer edge records: the CSV quote escape is undone, JSON is kept, anything else stops PASS\n')
# VERSION 4 / parser "5": inside a reused transaction-id group, identity -- the
# candidate, the amount, the date and the transaction type -- decides which rows
# are versions of one transaction and which are separate transactions.
check_pas2_identity_partition <- function() {
  directory <- tempfile('mentat-pas2-identity-')
  dir.create(file.path(directory, 'pas224'), recursive=TRUE)
  on.exit(unlink(directory, recursive=TRUE))
  path <- file.path(directory, 'pas224', 'itpas2.txt')
  row <- function(candidate='H00000001', amount='100', date='12312023', file='1',
                  sub='1', kind='24K', tran='SE.5', amendment='N') {
    fields <- rep('', 22L)
    fields[c(1,2,3,6,14,15,16,17,18,19,22)] <-
      c('C00000001', amendment, 'Q1', kind, date, amount, 'C00000002', candidate,
        tran, file, sub)
    paste(fields, collapse='|')
  }
  parse <- function(...) { writeLines(c(...), path); fec_build_pas2_index(directory, 2024L) }

  # A: an exactly repeated observation is a duplicate; conflicting facts are not.
  a <- parse(row(sub='1'), row(sub='1'))
  stopifnot(sum(a$exact_duplicate) == 1L,
            identical(sort(a$exclusion_reason), c('Duplicate FEC sub-ID', 'Included')))
  conflict <- tryCatch(parse(row(sub='1'), row(sub='1', amount='150')), error=identity)
  stopifnot(inherits(conflict, 'error'), grepl('Conflicting FEC sub-ID', conditionMessage(conflict)))

  # B: one transaction re-reported in a later filing. The earlier row is
  # contradicted by the amendment, so `superseded_by_amendment` is real again.
  b <- parse(row(sub='1', file='1'), row(sub='2', file='2', amendment='A'))
  stopifnot(nrow(b) == 2L,
            identical(b[file_num == 2, include_in_total], 'Yes'),
            identical(b[file_num == 1, include_in_total], 'No'),
            identical(b[file_num == 1, exclusion_reason], 'Superseded filing amendment'),
            b[file_num == 1, superseded_by_amendment] == 1L,
            b[file_num == 2, superseded_by_amendment] == 0L,
            sum(b[include_in_total == 'Yes', transaction_amount]) == 100)

  # C: the same itemization loaded twice in ONE filing under two sub-IDs is
  # one transaction. Two genuine equal gifts carry two transaction ids and
  # land in two different groups, so both of those are counted.
  repeated <- parse(row(sub='1'), row(sub='2'))
  stopifnot(nrow(repeated) == 2L,
            sum(repeated$include_in_total == 'Yes') == 1L,
            identical(repeated[sub_id == '2', include_in_total], 'Yes'),
            identical(repeated[sub_id == '1', exclusion_reason],
                      'Repeated itemization in one filing'),
            sum(repeated[include_in_total == 'Yes', transaction_amount]) == 100)
  equal_pair <- parse(row(sub='1', tran='SB23.1'), row(sub='2', tran='SB23.2'))
  stopifnot(nrow(equal_pair) == 2L, all(equal_pair$include_in_total == 'Yes'),
            sum(equal_pair$transaction_amount) == 200)

  # D: one reused label, several candidates, inside one filing and across
  # filings. This is the C00864926 `SE.5` shape from the measurement.
  d <- parse(row(candidate='H00000001', sub='1', file='1', amount='100'),
             row(candidate='H00000002', sub='2', file='1', amount='250'),
             row(candidate='H00000003', sub='3', file='2', amount='700'))
  stopifnot(nrow(d) == 3L, all(d$include_in_total == 'Yes'),
            all(d$tran_id_reused == 1L), sum(d$transaction_amount) == 1050)

  # A reused label spent on the SAME candidate in two filings, different
  # amount AND different date: two expenditures, not a version.
  apart <- parse(row(sub='1', file='1', amount='586067', date='06302023'),
                 row(sub='2', file='2', amount='878065', date='06302024'))
  stopifnot(nrow(apart) == 2L, all(apart$include_in_total == 'Yes'),
            all(apart$tran_id_reused == 1L), all(apart$unresolved_version == 0L),
            all(apart$unverified_version == 0L),
            sum(apart$transaction_amount) == 586067 + 878065)
  # Agreeing on the amount, or on the date, still leaves a version question.
  FEC_PAS2_AMBIGUOUS_VERSION_POLICY <<- 'withhold'
  same_amount <- parse(row(sub='1', file='1', amount='100', date='06302023'),
                       row(sub='2', file='2', amount='100', date='06302024'))
  same_date <- parse(row(sub='1', file='1', amount='100', date='06302024'),
                     row(sub='2', file='2', amount='150', date='06302024'))
  stopifnot(all(same_amount$unresolved_version == 1L), all(same_date$unresolved_version == 1L))
  FEC_PAS2_AMBIGUOUS_VERSION_POLICY <<- 'count_latest'

  # E: same candidate, different amount, different filings, no way to tell.
  # The default counts the later filing and flags it unverified.
  stopifnot(identical(FEC_PAS2_AMBIGUOUS_VERSION_POLICY, 'count_latest'))
  e_default <- parse(row(sub='1', file='1', amount='100'), row(sub='2', file='2', amount='150'))
  stopifnot(nrow(e_default) == 2L, all(e_default$unverified_version == 1L),
            all(e_default$unresolved_version == 0L),
            identical(e_default[file_num == 2, include_in_total], 'Yes'),
            identical(e_default[file_num == 1, exclusion_reason], 'Superseded filing amendment'),
            sum(e_default[include_in_total == 'Yes', transaction_amount]) == 150,
            all(e_default$unresolved_version_amount == 0))
  FEC_PAS2_AMBIGUOUS_VERSION_POLICY <<- 'withhold'
  e <- parse(row(sub='1', file='1', amount='100'), row(sub='2', file='2', amount='150'))
  stopifnot(nrow(e) == 2L, all(e$unresolved_version == 1L),
            all(e$include_in_total == 'No'),
            all(e$exclusion_reason == 'Unresolved filing relationship'))
  FEC_PAS2_AMBIGUOUS_VERSION_POLICY <<- 'count_latest'

  # Candidate-less rows stay retained and excluded, exactly as before.
  blank <- parse(row(candidate='', sub='1', tran='T9'))
  stopifnot(nrow(blank) == 1L, blank$unresolved_version == 1L,
            identical(blank$exclusion_reason, 'Unresolved filing relationship'))

  # The withheld dollars leave as an explicit share of the committee's outflow,
  # sized at what a "latest filing wins" build would have counted.
  FEC_PAS2_AMBIGUOUS_VERSION_POLICY <<- 'withhold'
  aggregated <- local({
    index <- parse(row(sub='1', file='1', amount='100'), row(sub='2', file='2', amount='150'))
    index[, `:=`(candidate_name='Candidate', candidate_party='Other', office='H',
      office_label='House', office_state='CA', district='1', candidate_source_url='fixture')]
    fec_aggregate_downstream_activity(index, data.table::data.table(cycle=2024L,
      committee_id='C00000001', committee_name='PAC', recipient_category='PAC'))
  })
  stopifnot(aggregated$unresolved_version_rows == 2L,
            aggregated$unresolved_version_amount == 150,
            aggregated$preliminary_included_amount == 0)

  # The other reading is one constant, and it invalidates its own cache.
  stopifnot(identical(FEC_PAS2_PARSER_VERSION, '9'),
            identical(FEC_PAS2_AMBIGUOUS_VERSION_POLICY, 'withhold'))
  withheld_cache <- fec_pas2_cache_file(directory, 2024L)
  FEC_PAS2_AMBIGUOUS_VERSION_POLICY <<- 'count_latest'
  on.exit(FEC_PAS2_AMBIGUOUS_VERSION_POLICY <<- 'count_latest', add=TRUE)
  counted <- parse(row(sub='1', file='1', amount='100'), row(sub='2', file='2', amount='150'))
  stopifnot(!identical(withheld_cache, fec_pas2_cache_file(directory, 2024L)),
            identical(counted[file_num == 2, include_in_total], 'Yes'),
            identical(counted[file_num == 1, include_in_total], 'No'),
            all(counted$unverified_version == 1L),
            sum(counted[include_in_total == 'Yes', transaction_amount]) == 150)
}
check_pas2_identity_partition()
cat('PAS2 identity partition: classes A-E, superseded amendments and the withheld share PASS\n')

check_retained_ambiguity <- function() {
  FEC_PAS2_AMBIGUOUS_VERSION_POLICY <<- 'count_all'
  directory <- tempfile('mentat-pas2-retained-')
  dir.create(file.path(directory, 'pas224'), recursive=TRUE)
  on.exit(unlink(directory, recursive=TRUE))
  path <- file.path(directory, 'pas224', 'itpas2.txt')
  row <- function(id, file='1', value='100') {
    fields <- rep('',22L)
    fields[c(1,2,3,6,14,15,16,17,18,19,22)] <- c('C00000001','N','Q1','24K','01012024',value,'C00000002','H00000001','SE.5',file,id)
    paste(fields,collapse='|')
  }
  # Across filings the same identity is two observations: both counted, both flagged.
  writeLines(c(row('1'), row('2','2')),path)
  result <- fec_build_pas2_index(directory,2024L)
  stopifnot(all(result$include_in_total=='Yes'),all(result$unverified_version==1L),sum(result$transaction_amount)==200)
  # In one filing, differing only in the SUB_ID, it is an FEC reload (F11): counted
  # once, the highest SUB_ID kept, nothing left to flag.
  writeLines(c(row('1'), row('2','1')),path)
  result <- fec_build_pas2_index(directory,2024L)
  stopifnot(identical(result[sub_id=='2', include_in_total],'Yes'),
            identical(result[sub_id=='1', exclusion_reason],'Repeated itemization in one filing'),
            all(result$unverified_version==0L),
            sum(result[include_in_total=='Yes', transaction_amount])==100)
  retained_cache <- fec_pas2_cache_file(directory,2024L)
  FEC_PAS2_AMBIGUOUS_VERSION_POLICY <<- 'count_latest'
  stopifnot(retained_cache != fec_pas2_cache_file(directory,2024L))
  FEC_PAS2_AMBIGUOUS_VERSION_POLICY <<- 'count_all'
  writeLines(row('missing',value=''),path)
  stopifnot(inherits(try(fec_build_pas2_index(directory,2024L),silent=TRUE),'try-error'))
}
check_retained_ambiguity()
cat('Retained ambiguity, separate policy caches and missing amounts: PASS\n')

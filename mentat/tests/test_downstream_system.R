script <- sub('^--file=', '', grep('^--file=', commandArgs(FALSE), value=TRUE)[[1L]])
project <- normalizePath(file.path(dirname(script), '..'), winslash='/', mustWork=TRUE)
for (name in c('paths', 'ingest', 'collect', 'fec_beneficiary', 'fec_transfer_graph'))
  source(file.path(project, 'R', paste0(name, '.R')))
data.table::setDTthreads(2L)

spend <- function(committee = 'DAJ', candidate = 'H0KY06104', amount = 106708, year = 2024L) {
  data.table::data.table(cycle=year,committee_id=committee,candidate_id=candidate,
    candidate_name='BARR, GARLAND ANDY',candidate_party='R',office='H',office_label='House',
    office_state='KY',district='06',transaction_amount=amount,activity_type='Independent expenditure',
    support_oppose='Support',transaction_date=paste0(year,'-05-01'),include_in_total='Yes')
}
funding <- data.table::data.table(cycle=2024L, company=c('Coinbase','Coinbase','Other'),
  committee_id=c('FAIRSHAKE','SECOND','DAJ'),amount=c(100,20,10),
  recipient=c('Fairshake','Second','Defend American Jobs'),include_in_analysis='Yes')
edges <- data.table::data.table(cycle=2024L,
  source=c('FAIRSHAKE','FAIRSHAKE','SECOND','DAJ','X1','X2','X3'),
  destination=c('DAJ','X1','DAJ','FAIRSHAKE','X2','X3','DEEP'),amount=10)
rows <- data.table::rbindlist(list(spend(),spend('DEEP',amount=50),spend(year=2026L,amount=999)))
result <- fec_build_beneficiary_tables(funding,rows,edges)
# Two roots, direct+indirect reach, diamond and cycle: each spender only once.
stopifnot(result$summary[view=='Industry',preliminary_downstream_amount]==106758,
  result$summary[view=='Company' & company=='Coinbase',preliminary_downstream_amount]==106758,
  result$summary[view=='Company' & company=='Other',preliminary_downstream_amount]==106758,
  !anyDuplicated(result$bridge$bridge_key), !any(result$bridge$cycle==2026L),
  result$bridge[view=='Company' & company=='Coinbase' & source_committee_id=='DAJ',preliminary_crypto_funding]==0,
  result$measurement_ledger[view=='Company' & company=='Coinbase',before_amount]==0,
  result$measurement_ledger[view=='Company' & company=='Coinbase',delta]==106758)
links <- data.table::data.table(cycle=2024L,committee_id='X1',designation='P')
terminal <- fec_build_beneficiary_tables(funding,rows,edges,links)
stopifnot(!'DEEP' %in% terminal$reach$source_committee_id)
negative <- data.table::copy(edges); negative[source=='FAIRSHAKE',amount:=-10]
stopifnot(!'DEEP' %in% fec_build_beneficiary_tables(funding,rows,negative)$reach$source_committee_id)
stopifnot(!nrow(fec_build_beneficiary_tables(funding[0],rows,edges)$bridge))
# A root with net zero may still be reached legitimately from a funded root.
zero <- data.table::rbindlist(list(funding,data.table::data.table(cycle=2024L,company='Coinbase',
  committee_id='X1',amount=0,recipient='X1',include_in_analysis='Yes')))
stopifnot('DEEP' %in% fec_build_beneficiary_tables(zero,rows,edges)$reach$source_committee_id)

# OTH producer's default must actually discover beyond three transfers.
directory <- tempfile('downstream-system-'); dir.create(file.path(directory,'oth24'),recursive=TRUE)
edge_path <- file.path(directory,'oth24','transfer-edges.csv')
data.table::fwrite(edges,edge_path)
jsonlite::write_json(list(version=6,ambiguous_version_policy='count_all',
  edges_sha256=digest::digest(file=edge_path,algo='sha256'),counts=list()),
  file.path(directory,'oth24','transfer-info.json'),auto_unbox=TRUE)
context <- fec_transfer_context(directory,data.table::data.table(cycle=2024L,
  committee_id='FAIRSHAKE',preliminary_crypto_funding=100),links[0],2024L)
stopifnot('DEEP' %in% context$universe$committee_id)
unlink(directory,recursive=TRUE)

# Optional preserved-source replay: outputs remain audit artifacts, never reports.
args <- commandArgs(TRUE)
if (length(args)) {
  fixture <- jsonlite::fromJSON(args[[1]])
  actual <- data.table::as.data.table(fixture$activity)
  roots <- data.table::as.data.table(fixture$funding)
  observed <- fec_build_beneficiary_tables(roots,actual,data.table::as.data.table(fixture$edges))
  stopifnot(!anyDuplicated(observed$bridge$bridge_key), !anyDuplicated(fixture$source_rows$sub_id),
    observed$measurement_ledger[view=='Industry' & cycle==2024L,after_amount]==106708,
    abs(observed$measurement_ledger[view=='Industry' & cycle==2026L,after_amount]-7170138.4)<0.005)
  jsonlite::write_json(list(source=fixture$source,source_sha256=fixture$source_sha256,
    source_rows=fixture$source_rows,measurement_ledger=observed$measurement_ledger,
    bridge=observed$bridge),args[[2]],auto_unbox=TRUE,dataframe='rows',pretty=TRUE,digits=NA)
}
cat('Downstream system: shared roots, diamonds, cycles, long routes, signed edges, candidate stops and cycle isolation PASS\n')

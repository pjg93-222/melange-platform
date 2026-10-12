script <- sub('^--file=', '', grep('^--file=', commandArgs(FALSE), value=TRUE)[[1L]])
project <- normalizePath(file.path(dirname(script),'..'),winslash='/',mustWork=TRUE)
for (name in c('paths','ingest','collect','fec_beneficiary','fec_transfer_graph','tidy_stage'))
  source(file.path(project,'R',paste0(name,'.R')))
data.table::setDTthreads(2L)
root <- tempfile('downstream-review-'); dir.create(root)
dir.create(file.path(root,'pas224')); dir.create(file.path(root,'pas226'))
raw <- function(candidate='', amount='123.45', id='1') {
  fields <- rep('',22L)
  fields[c(1,2,3,6,14,15,17,18,19,22)] <- c('C00000001','N','Q1','24E','01022024',amount,candidate,'SE1','1',id)
  paste(fields,collapse='|')
}
for (year in c(2024,2026)) writeLines(raw(),file.path(root,paste0('pas2',substr(year,3,4)),'itpas2.txt'))
stopifnot(fec_pas2_cache_file(root,2024L)!=fec_pas2_cache_file(root,2026L))
unknown <- fec_build_pas2_index(root,2024L)
stopifnot(unknown$include_in_total=='Yes',unknown$transaction_amount==123.45,
          unknown$unverified_version==1L,unknown$unresolved_version_amount==0)
staged_flags <- mentat_tidy_downstream_activity(data.table::data.table(cycle=2024L,
  unverified_version_rows=7L,unresolved_version_rows=2L,unresolved_version_amount=12.34,
  repeated_itemization_rows=3L))
stopifnot(staged_flags$unverified_version_rows==7L,staged_flags$unresolved_version_rows==2L,
  staged_flags$unresolved_version_amount==12.34,staged_flags$repeated_itemization_rows==3L)
writeLines(raw(amount='1.001'),file.path(root,'pas224','itpas2.txt'))
stopifnot(inherits(try(fec_build_pas2_index(root,2024L),silent=TRUE),'try-error'))

dir.create(file.path(root,'oth24'))
edge_path <- file.path(root,'oth24','transfer-edges.csv')
raw_path <- file.path(root,'oth24','itoth.txt'); writeLines('source version 1',raw_path)
edges <- data.table::data.table(cycle=2024L,source='ROOT',destination='SPENDER',amount=50)
data.table::fwrite(edges,edge_path)
jsonlite::write_json(list(version=6,ambiguous_version_policy='count_all',
  source_sha256=digest::digest(file=raw_path,algo='sha256'),
  edges_sha256=digest::digest(file=edge_path,algo='sha256'),counts=list()),
  file.path(root,'oth24','transfer-info.json'),auto_unbox=TRUE)
roots <- data.table::data.table(cycle=2024L,committee_id='ROOT',preliminary_crypto_funding=-100,
  traversal_seed=TRUE)
links <- data.table::data.table(cycle=integer(),committee_id=character(),designation=character())
found <- fec_transfer_context(root,roots,links,2024L)
stopifnot('SPENDER' %in% found$universe$committee_id)
terminal <- fec_transfer_context(root,roots,
  data.table::data.table(cycle=2024L,committee_id='ROOT',designation='P'),2024L)
stopifnot(!'SPENDER' %in% terminal$universe$committee_id,!nrow(terminal$edges))
writeLines('source version 2',raw_path)
stale <- fec_transfer_context(root,roots,links,2024L)
stopifnot(stale$coverage[[1]]$status=='stale_transfer_index',!nrow(stale$edges))

funding <- data.table::data.table(cycle=2024L,company=c('Positive','Refund'),committee_id='ROOT',
  amount=c(100,-200),recipient='Root',include_in_analysis='Yes')
activity <- data.table::data.table(cycle=2024L,committee_id='SPENDER',
  candidate_id=sprintf('H%08d',1:105),candidate_name=sprintf('Candidate %03d',1:105),
  candidate_party='R',office='H',office_label='House',office_state='KY',district='06',
  transaction_amount=1:105,activity_type='Independent expenditure',support_oppose='Support',
  transaction_date='2024-01-02',include_in_total='Yes')
result <- fec_build_beneficiary_tables(funding,activity,found$edges)
stopifnot(nrow(result$bridge_all[company=='Positive'])==105,
  nrow(result$bridge_all[view=='Industry'])==105,
  nrow(result$bridge[company=='Positive'])==100,
  sum(result$bridge_all[company=='Positive',preliminary_downstream_amount])==sum(1:105))
tidy <- mentat_tidy_beneficiary_bridge(result$bridge_all)
stopifnot(nrow(tidy)==105,sum(tidy$support_amount)==sum(1:105),sum(tidy$oppose_amount)==0)
# Multiple company associations are not an additive industry partition.
funding[, amount := 100]
shared <- fec_build_beneficiary_tables(funding,activity,found$edges)
canonical <- mentat_tidy_beneficiary_bridge(shared$bridge_all,'Industry')
associations <- mentat_tidy_beneficiary_bridge(shared$bridge_all)
stopifnot(nrow(canonical)==105,nrow(associations)==210,
  sum(canonical$downstream_activity)==sum(1:105),sum(associations$downstream_activity)==2*sum(1:105))
empty <- fec_build_beneficiary_tables(funding,activity[0],found$edges)
stopifnot(nrow(empty$reach)>0,!nrow(empty$bridge_all))
unlink(root,recursive=TRUE)
cat('Downstream review: unknown identity, cent precision, cycle-keyed cache, source drift, refund cancellation, full detail and unique network facts PASS\n')

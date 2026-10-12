# F11 (Paul 2026-09-25): a PAS2 row the FEC loaded twice counts once; nothing that merely
# looks like one does. FEC_PAS2_RELOAD_KEY (version key, file number, identity, memo code) is
# the same key tools/fec_transfer_graph.py reload_key() applies to OTH (tests/test_reload_rule.py).
# Each case has a negative control: the key weakened the way a regression would weaken it, and
# the same check failing.
script <- sub('^--file=', '', grep('^--file=', commandArgs(FALSE), value=TRUE)[[1L]])
project <- normalizePath(file.path(dirname(script), '..'), winslash='/', mustWork=TRUE)
for (name in c('paths', 'ingest', 'collect', 'fec_beneficiary'))
  source(file.path(project, 'R', paste0(name, '.R')))
data.table::setDTthreads(2L)

stopifnot(identical(FEC_PAS2_PARSER_VERSION, '9'),
          identical(FEC_PAS2_AMBIGUOUS_VERSION_POLICY, 'count_all'),
          identical(FEC_PAS2_RELOAD_KEY, c('version_key', 'file_num_sort', 'identity_key', 'recipient_name', 'memo_code')))
directory <- tempfile('mentat-pas2-reload-')
dir.create(file.path(directory, 'pas222'), recursive=TRUE)
path <- file.path(directory, 'pas222', 'itpas2.txt')
row <- function(sub, file='1592379', tran='SE.5', amount='50000', date='06152022', memo='', image='202206159001',
                name='KINETICS CAMPAIGNS') {
  fields <- rep('', 22L)
  fields[c(1,2,3,5,6,8,14,15,16,17,18,19,20,22)] <-
    c('C00000001', 'N', 'Q3', image, '24E', name, date, amount, '', 'H2MI07123', tran, file, memo, sub)
  paste(fields, collapse='|')
}
parse <- function(...) { writeLines(c(...), path); fec_build_pas2_index(directory, 2022L) }
counted <- function(index) sum(index[include_in_total == 'Yes', transaction_amount])
fails <- function(check) inherits(try(check, silent=TRUE), 'try-error')
default_key <- FEC_PAS2_RELOAD_KEY
with_key <- function(key, expr) {
  FEC_PAS2_RELOAD_KEY <<- key
  on.exit(FEC_PAS2_RELOAD_KEY <<- default_key)
  expr
}

# 1. A reload (only the SUB_ID differs; a second page image is still the same record) counts once.
check_reload <- function(index) stopifnot(
  counted(index) == 50000,
  identical(index[sub_id == '2', include_in_total], 'Yes'),
  identical(index[sub_id == '1', exclusion_reason], 'Repeated itemization in one filing'),
  all(index$unverified_version == 0L))
check_reload(parse(row('1'), row('2')))
check_reload(parse(row('1', image='202206159001'), row('2', image='202206159002')))
# Negative control: a key that includes the SUB_ID never collapses the reload.
stopifnot(fails(check_reload(with_key(c(default_key, 'sub_id'), parse(row('1'), row('2'))))))

# 2. Genuine repeats: the same identity under distinct transaction ids stays counted twice.
check_both <- function(index) stopifnot(counted(index) == 100000, all(index$include_in_total == 'Yes'))
check_both(parse(row('1', tran='SE.4201'), row('2', tran='SE.4202')))
# Negative control: a key without the version key would merge them.
stopifnot(fails(check_both(with_key(setdiff(default_key, 'version_key'),
  parse(row('1', tran='SE.4201'), row('2', tran='SE.4202'))))))

# 3. The CLF 2022 Barrett shape: two $50,000 items in files 1592379 and 1598276 stay counted, flagged.
barrett <- parse(row('1', file='1592379'), row('2', file='1598276'))
check_both(barrett)
stopifnot(all(barrett$unverified_version == 1L))
# Negative control: a key without the file number would merge the two filings.
stopifnot(fails(check_both(with_key(setdiff(default_key, 'file_num_sort'),
  parse(row('1', file='1592379'), row('2', file='1598276'))))))

# 4. Another payee name, or no file number at all, is not a reload either.
check_both(parse(row('1', name='KINETICS CAMPAIGNS'), row('2', name='RBC MEDIA')))
check_both(parse(row('1', file=''), row('2', file='')))
# Negative control: a key without the payee name would merge the two payees.
stopifnot(fails(check_both(with_key(setdiff(default_key, 'recipient_name'),
  parse(row('1', name='KINETICS CAMPAIGNS'), row('2', name='RBC MEDIA'))))))

# 5. A memo line is not a second copy of the real line it shares an identity with.
memo <- parse(row('1'), row('2', memo='X'))
stopifnot(counted(memo) == 50000, identical(memo[sub_id == '1', include_in_total], 'Yes'))
# Negative control: without the memo code the memo line (higher SUB_ID) would swallow the real one.
stopifnot(counted(with_key(setdiff(default_key, 'memo_code'), parse(row('1'), row('2', memo='X')))) == 0)

unlink(directory, recursive=TRUE)
cat('PAS2 reload rule: PASS (reload collapsed; genuine repeats, cross-file, other-payee, no-file and memo pairs kept; 5 negative controls)\n')

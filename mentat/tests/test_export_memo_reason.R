# F13 follow-up (2026-09-26): the export labels a memo the memo plan removed as its
# counted parent's duplicate with that match, never "disclosure_memo_unmatched".
# The definition is read from tools/export_crypto_spending_data.R itself (parsed,
# not run), so this test holds the exporter's own bytes. Ends with a negative control.
script <- sub('^--file=', '', grep('^--file=', commandArgs(FALSE), value = TRUE)[[1L]])
project <- normalizePath(file.path(dirname(script), '..'), winslash = '/', mustWork = TRUE)
for (name in c('paths', 'schema', 'ingest', 'collect'))
  source(file.path(project, 'R', paste0(name, '.R')))
exporter <- file.path(project, 'tools', 'export_crypto_spending_data.R')
expressions <- parse(exporter, keep.source = FALSE)
is_definition <- function(expression, name) is.call(expression) && identical(expression[[1L]], as.name('<-')) &&
  identical(expression[[2L]], as.name(name))
definition <- Filter(function(expression) is_definition(expression, 'export_memo_duplicate_reason'), as.list(expressions))
stopifnot(length(definition) == 1L)
eval(definition[[1L]])
# The exporter applies it to raw_export right after the receipt-role reason.
calls <- vapply(as.list(expressions), function(expression) gsub('[[:space:]]+', '', paste(deparse(expression), collapse = '')), '')
first <- grep('`:=`(receipt_role_reason,fec_receipt_role_reason(receipt_role))', calls, fixed = TRUE)
second <- grep('`:=`(receipt_role_reason,export_memo_duplicate_reason(receipt_role_reason,duplicate_memo,duplicate_reason))',
               calls, fixed = TRUE)
stopifnot(length(first) == 1L, length(second) == 1L, second == first + 1L)

roles <- c('unverified_disclosure_memo', 'unverified_disclosure_memo', 'unverified_disclosure_memo',
           'contribution', 'unverified_disclosure_memo')
duplicate_memo <- c(1L, 1L, 0L, 1L, NA)
duplicate_reason <- c('memo_redisclosure_of_counted_parent', 'fec_structured_back_reference', '',
                      'fec_text_back_reference', '')
expected <- c('memo_redisclosure_of_counted_parent', 'fec_structured_back_reference',
              'disclosure_memo_unmatched', '', 'disclosure_memo_unmatched')
labels <- function(relabel) relabel(fec_receipt_role_reason(roles), duplicate_memo, duplicate_reason)
stopifnot(identical(fec_receipt_role_reason(roles)[1:3], rep('disclosure_memo_unmatched', 3L)),
          identical(labels(export_memo_duplicate_reason), expected))
cat('Memo reason: a removed duplicate memo carries its match; an unmatched counted memo keeps its flag PASS\n')
# Negative control: without the relabel (the export before this fix) the removed memos
# read "disclosure_memo_unmatched".
unchanged <- function(reason, duplicate_memo, duplicate_reason) reason
stopifnot(!identical(labels(unchanged), expected),
          identical(labels(unchanged)[1:2], rep('disclosure_memo_unmatched', 2L)))
cat('Negative control: the unfixed export labels removed memos unmatched PASS\n')

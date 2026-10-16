# Evaluate only the actual literal anchor-contract function: no model/refit run.
args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 1L) stop("Supply the candidate rf_bundle2.R path and optional original baseline")
contract_from <- function(path) {
  expressions <- parse(path, keep.source = FALSE)
  matches <- Filter(function(item) is.call(item) && identical(item[[1L]], as.name("<-")) &&
    is.symbol(item[[2L]]) && identical(as.character(item[[2L]]), "rf_v2_anchor_contract"), as.list(expressions))
  stopifnot(length(matches) == 1L)
  environment <- new.env(parent = baseenv())
  eval(matches[[1L]], envir = environment)
  environment$rf_v2_anchor_contract()
}
current <- contract_from(args[[1L]])
expected <- "Included receipts to House/Senate candidates across all funding sources except non-giving; funding identity is not necessarily verified"
stopifnot(identical(current$share$bases[[1L]]$id, "employee_candidate"),
          identical(current$share$bases[[1L]]$anchor, "candidate_receipts.cycles[A]"),
          identical(current$share$bases[[1L]]$label, expected))
stopifnot(identical(current$schema_version, 1L),
          identical(vapply(current$share$anchor_options, `[[`, 1L, "cycle_offset"), c(2L, 4L)),
          identical(vapply(current$share$anchor_options, `[[`, TRUE, "chained"), c(TRUE, FALSE)))
if (length(args) >= 2L) {
  original <- contract_from(args[[2L]])
  stopifnot(identical(original$share$bases[[1L]]$label, "Employees' included giving to candidate committees"))
  original$share$bases[[1L]]$label <- expected
  stopifnot(identical(current, original))
}
cat("Forecast anchor contract copy: PASS (actual named function, legacy ID/anchor, complete old/new contract parity except label)\n")

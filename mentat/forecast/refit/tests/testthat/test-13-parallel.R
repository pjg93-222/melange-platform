# Parallel runs are BIT-IDENTICAL to serial runs (REFIT_CORES). Every unit of work owns a deterministic stream (rf_rng.R entropy
# or the case counter) and results are combined in input order, so scheduling cannot change a number. These tests force two
# workers even on a machine whose default is serial.

with_cores <- function(n, code) {
  old <- Sys.getenv("REFIT_CORES", unset = NA)
  on.exit(if (is.na(old)) Sys.unsetenv("REFIT_CORES") else Sys.setenv(REFIT_CORES = old), add = TRUE)
  Sys.setenv(REFIT_CORES = as.character(n))
  force(code)
}

test_that("rf_cores reads REFIT_CORES and never drops below one", {
  with_cores(1, expect_identical(rf_cores(), 1L))
  with_cores(4, expect_identical(rf_cores(), 4L))
  with_cores(0, expect_identical(rf_cores(), 1L))
  with_cores("junk", expect_identical(rf_cores(), 1L))
})

test_that("rf_pmap keeps input order and equals lapply; an error in a unit stops the map", {
  x <- 1:7
  serial <- lapply(x, function(i) i^2)
  par <- with_cores(2, rf_pmap(x, function(i) i^2))
  expect_identical(par, serial)
  expect_error(with_cores(2, rf_pmap(1:3, function(i) if (i == 2L) stop("boom") else i)), "boom")
})

test_that("a wild-bootstrap enumeration (46,656 draws in blocks) gives the same hits serially and on two workers", {
  ctx <- test_ctx()
  m <- ctx$fits$lobby; d <- ctx$lobby$diffs
  X <- model.matrix(m); y <- model.response(model.frame(m)); k <- match("dg", colnames(X))
  old_chunk <- RF$CHUNK
  on.exit(RF$CHUNK <<- old_chunk, add = TRUE)
  RF$CHUNK <<- 8000                                   # several blocks so the workers really split the draws
  a <- with_cores(1, rf_wcr(X, y, d$study_sector, k, "webb", entropy = c(1, 2, 3, 4)))
  b <- with_cores(2, rf_wcr(X, y, d$study_sector, k, "webb", entropy = c(1, 2, 3, 4)))
  expect_identical(a, b)
  expect_identical(a$hits, 1432L)
  # random draws too: the stream belongs to the unit, not to the worker
  ra <- with_cores(1, rf_wcr(X, y, d$year, k, "webb", entropy = c(20260929, 5, 6, 7), B = 2999))
  rb <- with_cores(2, rf_wcr(X, y, d$year, k, "webb", entropy = c(20260929, 5, 6, 7), B = 2999))
  expect_identical(ra, rb)
})

test_that("the backtest folds give identical predictions serially and on two workers", {
  s <- test_specs()$pac
  a <- with_cores(1, rf_backtest(s, s$d, "loeo"))
  b <- with_cores(2, rf_backtest(s, s$d, "loeo"))
  expect_identical(a, b)
  expect_identical(rf_backtest_metrics(a), rf_backtest_metrics(b))
  expect_identical(attr(a, "feasibility"), attr(b, "feasibility"))
  expect_identical(attr(a, "slopes"), attr(b, "slopes"))
})

test_that("the stepwise grid (selection-repeated bootstrap, nested validation) is identical serially and on two workers", {
  cases <- test_ctx()$cases
  keep <- cases[cases$case %in% unique(cases$case)[c(1, 2, 3, 13, 14, 25, 26, 27)], ]
  a <- with_cores(1, rf_stepwise_grid(keep))
  b <- with_cores(2, rf_stepwise_grid(keep))
  expect_identical(a$selected, b$selected)
  expect_identical(a$validation, b$validation)
  expect_identical(a$stability, b$stability)
  # and each case equals the full serial grid's row (the stream is seeded by the case counter, not by the subset)
  full <- test_ctx()$stepwise$selected
  expect_identical(a$selected$selection_repeated_p, full$selection_repeated_p[match(a$selected$case, full$case)])
})

# A refit's in-process parity suite must never leave production publishing in the
# helper's temporary runtime. Exercise the actual helper, then acquire the real lock
# and publish into the real persistent archive (not the helper runtime).

test_that("the actual parity helper restores the caller runtime before locking and publishing", {
  old <- Sys.getenv()
  on.exit({ Sys.unsetenv(setdiff(names(Sys.getenv()), names(old))); do.call(Sys.setenv, as.list(old)) }, add = TRUE)
  runtime <- tempfile("production-runtime-"); dir.create(runtime)
  helper_runtime <- tempfile("helper-runtime-")
  gb <- file.path(runtime, "events")
  Sys.setenv(RF_RUNTIME = runtime, RF_TEST_RUNTIME = helper_runtime, RF_GLASSBOX_DIR = gb)
  Sys.unsetenv("REFIT_CORES")  # the helper supplies 1; absence must be restored too
  before <- Sys.getenv()
  result <- rf_parity_suite(filter = "^01-")
  expect_equal(result$failed + result$errors, 0)
  after <- Sys.getenv()
  expect_identical(after[order(names(after))], before[order(names(before))])
  expect_identical(rf_runtime(), normalizePath(runtime, winslash = "/"))
  expect_false("REFIT_CORES" %in% names(after))
  expect_identical(Sys.getenv("RF_GLASSBOX_DIR"), gb)
  lock <- rf_lock_acquire()
  expect_identical(lock$path, file.path(normalizePath(runtime, winslash = "/"), "refit.lock"))
  expect_true(dir.exists(lock$path))
  expect_false(dir.exists(file.path(helper_runtime, "refit.lock")))
  rf_lock_release(lock$path)
  # The actual publisher must now leave its rollback archive under the caller root.
  bundle <- jsonlite::fromJSON(file.path(rf_contracts_dir(), "giving-models-v2.example.json"), simplifyVector = FALSE)
  dest <- file.path(runtime, "published-models")
  dir.create(dest)
  file.copy(rf_roots()$BUNDLE_V1, file.path(dest, "giving-models-v1.json"))
  gates <- data.frame(gate = "isolated publication test", status = "PASS", detail = "")
  published <- rf_publish(bundle, gates, dir = dest)
  expect_identical(published$status, "published")
  expect_identical(published$archive, file.path(normalizePath(runtime, winslash = "/"), "published", bundle$model_version))
  expect_identical(rf_sha256(file.path(published$archive, "giving-models-v2.json")), published$sha256)
  expect_false(dir.exists(file.path(helper_runtime, "published")))
  expect_false(dir.exists(rf_lock_path()))
})

test_that("an error while loading parity helpers restores changed, removed and newly added environment variables", {
  old <- Sys.getenv()
  on.exit({ Sys.unsetenv(setdiff(names(Sys.getenv()), names(old))); do.call(Sys.setenv, as.list(old)) }, add = TRUE)
  Sys.setenv(RF_RUNTIME = "persistent-runtime", RF_PARITY_REMOVED = "restore-me")
  Sys.unsetenv("RF_PARITY_ADDED")
  before <- Sys.getenv()
  local_mocked_bindings(test_dir = function(...) {
    Sys.setenv(RF_RUNTIME = "temporary-runtime", RF_PARITY_ADDED = "sandbox-only")
    Sys.unsetenv("RF_PARITY_REMOVED")
    stop("injected parity helper error")
  }, .package = "testthat")
  expect_error(rf_parity_suite(), "injected parity helper error")
  after <- Sys.getenv()
  expect_identical(after[order(names(after))], before[order(names(before))])
})

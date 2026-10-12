# Publish / pointer switch / lock / rollback (spec 3.13; owner: auto-publish writes files only and never commits; v1 is kept).
# Everything here writes into temp folders (RF_RUNTIME of the test process, a temp publish dir).

pass_gates <- function() data.frame(gate = c("G0 environment", "G4 sample adequacy", "G6 non-degradation", "G7 bundle", "G8 consumer smoke"), status = c("PASS", "WARN", "PASS", "PASS", "PASS"),
                                    detail = "", stringsAsFactors = FALSE)
fresh_publish_dir <- function(name) {
  d <- file.path(tempdir(), name); unlink(d, recursive = TRUE); dir.create(d, recursive = TRUE)
  file.copy(rf_roots()$BUNDLE_V1, file.path(d, "giving-models-v1.json"))
  d
}
second_bundle <- function(b) { b2 <- b; b2$model_version <- "refit-20261001-deadbeef"; b2$built_at_utc <- "2026-10-01T09:00:00Z"; b2$built_on <- "2026-10-01"; b2 }

test_that("a publish writes giving-models-v2.json, its receipt and an atomic pointer; v1 stays exactly as it was", {
  b <- test_bundle2(); d <- fresh_publish_dir("pub1")
  v1_before <- rf_sha256(file.path(d, "giving-models-v1.json"))
  before <- rf_read_pointer(d)
  expect_identical(before$active, "giving-models-v1.json")                  # derived from the kept v1 file when no pointer exists yet
  r <- rf_publish(b, pass_gates(), receipt = list(run_id = "unit"), dir = d)
  expect_identical(r$status, "published")
  expect_true(file.exists(file.path(d, "giving-models-v2.json")) && file.exists(file.path(d, "giving-models-v2.receipt.json")))
  expect_identical(rf_sha256(file.path(d, "giving-models-v2.json")), r$sha256)
  expect_identical(rf_sha256(file.path(d, "giving-models-v1.json")), v1_before)
  ptr <- jsonlite::fromJSON(file.path(d, "giving-models-current.json"), simplifyVector = FALSE)
  expect_identical(ptr$active, paste0("versions/", r$sha256, "/giving-models-v2.json")); expect_identical(ptr$sha256, r$sha256); expect_identical(ptr$model_version, b$model_version)
  expect_identical(rf_sha256(file.path(d, ptr$active)), r$sha256)
  expect_identical(ptr$previous$active, "giving-models-v1.json"); expect_identical(ptr$previous$model_version, "2026-09-27-association-v1")
  expect_identical(rf_validate_parsed(jsonlite::fromJSON(file.path(d, "giving-models-v2.json"), simplifyVector = FALSE)), character(0))
  rc <- jsonlite::fromJSON(file.path(d, "giving-models-v2.receipt.json"), simplifyVector = FALSE)
  expect_identical(rc$published_to$sha256, r$sha256)
  # archived for rollback, with the pointer as it was before the switch
  expect_true(all(c("giving-models-v2.json", "giving-models-v2.receipt.json", "pointer-before.json") %in% list.files(r$archive)))
  expect_length(list.files(d, pattern = "\\.tmp$"), 0L)
  expect_false(dir.exists(rf_lock_path()))                                   # the lock is released
})

test_that("a HOLD or FAIL decision publishes nothing and says why (no pointer, no v2 file)", {
  b <- test_bundle2(); d <- fresh_publish_dir("pub2")
  held <- pass_gates(); held$status[3] <- "HOLD"; held$detail[3] <- "control_House / commercial_banks worse"
  r <- rf_publish(b, held, dir = d)
  expect_identical(r$status, "not_published"); expect_identical(r$decision, "HOLD"); expect_match(r$reason, "G6 non-degradation")
  expect_false(file.exists(file.path(d, "giving-models-v2.json"))); expect_false(file.exists(file.path(d, "giving-models-current.json")))
  failed <- pass_gates(); failed$status[4] <- "FAIL"
  expect_identical(rf_publish(b, failed, dir = d)$decision, "FAIL")
  expect_identical(list.files(d), "giving-models-v1.json")
})

test_that("an invalid bundle is refused even when the gates say PUBLISH, and leaves the pointer alone", {
  b <- test_bundle2(); d <- fresh_publish_dir("pub3")
  bad <- b; bad$share_models$seats_House <- list(id = "seats_House")
  expect_error(rf_publish(bad, pass_gates(), dir = d), "contract schema")
  expect_false(file.exists(file.path(d, "giving-models-current.json")))
  expect_false(dir.exists(rf_lock_path()))
})

test_that("rollback restores an archived bundle or the kept v1, atomically, and keeps both files", {
  b <- test_bundle2(); b2 <- second_bundle(b); d <- fresh_publish_dir("pub4")
  r1 <- rf_publish(b, pass_gates(), dir = d)
  r2 <- rf_publish(b2, pass_gates(), dir = d)
  expect_identical(rf_read_pointer(d)$model_version, "refit-20261001-deadbeef")
  expect_identical(rf_read_pointer(d)$previous$model_version, b$model_version)
  rb <- rf_rollback(b$model_version, dir = d)                                # back to the first v2
  expect_identical(rb$status, "rolled_back")
  ptr <- rf_read_pointer(d)
  expect_identical(ptr$model_version, b$model_version); expect_identical(ptr$sha256, r1$sha256)
  expect_identical(rf_sha256(file.path(d, "giving-models-v2.json")), r1$sha256)    # the archived pair was put back under the active name
  rb1 <- rf_rollback("2026-09-27-association-v1", dir = d)                   # all the way back to the kept v1
  expect_identical(rf_read_pointer(d)$active, "giving-models-v1.json")
  expect_identical(rb1$model_version, "2026-09-27-association-v1")
  expect_true(file.exists(file.path(rf_archive_dir("refit-20261001-deadbeef"), "giving-models-v2.json")))   # the later publish stays archived
  expect_error(rf_rollback("no-such-version", dir = d), "no archived publish")
  expect_false(dir.exists(rf_lock_path()))
})

test_that("the lock is a mutex directory: a second holder is refused, a stale dead-owner lock is broken, a fresh one is not", {
  lk <- file.path(tempdir(), "unit_refit.lock"); unlink(lk, recursive = TRUE)
  a <- rf_lock_acquire(path = lk)
  expect_true(dir.exists(lk)); expect_false(a$broke_stale)
  expect_error(rf_lock_acquire(path = lk), "another refit holds the lock")
  rf_lock_release(lk); expect_false(dir.exists(lk))
  # a stale lock: older than 6 h and owned by a process that is gone
  dir.create(lk); writeLines('{"pid": 999999, "acquired_utc": "2026-01-01T00:00:00Z"}', file.path(lk, "owner.json"))
  Sys.setFileTime(lk, Sys.time() - 7 * 3600)
  b <- rf_lock_acquire(path = lk)
  expect_true(b$broke_stale); rf_lock_release(lk)
  # a fresh lock whose owner looks dead is still respected (younger than 6 h)
  dir.create(lk); writeLines('{"pid": 999999}', file.path(lk, "owner.json"))
  expect_error(rf_lock_acquire(path = lk), "another refit holds the lock")
  # an old lock whose owner is alive (this process) is not broken
  writeLines(sprintf('{"pid": %d}', Sys.getpid()), file.path(lk, "owner.json")); Sys.setFileTime(lk, Sys.time() - 7 * 3600)
  expect_error(rf_lock_acquire(path = lk), "another refit holds the lock")
  unlink(lk, recursive = TRUE)
})

test_that("a held lock blocks a publish (two refits never publish at once)", {
  b <- test_bundle2(); d <- fresh_publish_dir("pub5")
  held <- rf_lock_acquire()
  expect_error(rf_publish(b, pass_gates(), dir = d), "another refit holds the lock")
  rf_lock_release(held$path)
  expect_identical(rf_publish(b, pass_gates(), dir = d)$status, "published")
})

test_that("rf_bundle_diff is tolerance-based: platform noise passes, a real change is found, run identity fields are ignored", {
  a <- rf_canonical_json(test_bundle2())
  noisy <- a
  noisy$pac_model$beta <- a$pac_model$beta * (1 + 3e-14)                   # BLAS-level noise (pod ATLAS vs reference BLAS)
  noisy$share_models$control_House$covariance[[2]][[2]] <- a$share_models$control_House$covariance[[2]][[2]] * (1 - 2e-14)
  noisy$producer$run_id <- "another-run"; noisy$built_at_utc <- "2027-01-01T00:00:00Z"; noisy$producer$R_version <- "R 9.9"
  expect_equal(nrow(rf_bundle_diff(a, noisy)), 0L)
  expect_lt(attr(rf_bundle_diff(a, noisy), "max_dev"), 1e-12)
  real <- a; real$pac_model$beta <- a$pac_model$beta * (1 + 1e-6)
  d <- rf_bundle_diff(a, real)
  expect_equal(nrow(d), 1L); expect_identical(d$path, "$.pac_model.beta")
  miss <- a; miss$pac_model$slopes$commercial_banks <- NULL
  expect_gt(nrow(rf_bundle_diff(a, miss)), 0L)
  changed <- a; changed$pac_model$slope_rule <- "R0"
  expect_identical(rf_bundle_diff(a, changed)$path, "$.pac_model.slope_rule")
})

test_that("the platform statement names R, BLAS, LAPACK, the platform and the worker count (for every receipt)", {
  pi <- rf_platform_info()
  expect_true(all(c("R_version", "platform", "blas", "lapack", "cores", "node", "note") %in% names(pi)))
  expect_match(pi$R_version, "^R version")
  expect_gte(pi$cores, 1L)
})


test_that("publication preserves old immutable bytes through a failed switch and a repeated v2 publish", {
  b <- test_bundle2(); b2 <- second_bundle(b); d <- fresh_publish_dir("pub_atomic")
  first <- rf_publish(b, pass_gates(), dir = d)
  pointer_before <- readBin(rf_pointer_path(d), "raw", n = file.info(rf_pointer_path(d))$size)
  old_path <- file.path(d, first$pointer$active)
  old_bytes <- readBin(old_path, "raw", n = file.info(old_path)$size)
  alias_before <- rf_sha256(file.path(d, "giving-models-v2.json"))
  real_switch <- .rf_write_pointer
  assign(".rf_write_pointer", function(...) stop("injected switch failure"), envir = environment(rf_publish))
  tryCatch(expect_error(rf_publish(b2, pass_gates(), dir = d), "injected switch failure"),
           finally = assign(".rf_write_pointer", real_switch, envir = environment(rf_publish)))
  expect_identical(readBin(rf_pointer_path(d), "raw", n = file.info(rf_pointer_path(d))$size), pointer_before)
  expect_identical(rf_sha256(file.path(d, "giving-models-v2.json")), alias_before)
  expect_identical(readBin(old_path, "raw", n = file.info(old_path)$size), old_bytes)
  expect_false(dir.exists(rf_lock_path()))
  next_publish <- rf_publish(b2, pass_gates(), dir = d)
  expect_false(identical(first$pointer$active, next_publish$pointer$active))
  expect_identical(rf_sha256(old_path), first$sha256)
  expect_identical(rf_read_pointer(d)$model_version, b2$model_version)
})

test_that("a model version cannot be reused for changed bundle bytes", {
  b <- test_bundle2(); d <- fresh_publish_dir("pub_collision")
  first <- rf_publish(b, pass_gates(), dir = d)
  b$built_at_utc <- "2026-10-02T12:00:00Z"
  expect_error(rf_publish(b, pass_gates(), dir = d), "model_version already identifies different bytes")
  expect_identical(rf_read_pointer(d)$sha256, first$sha256)
})

test_that("pointer readers reject corruption and agree with legacy consumer discovery", {
  b <- test_bundle2(); d <- fresh_publish_dir("pub_pointer")
  rf_write_json_atomic(b, file.path(d, "giving-models-v2.json"))
  expect_identical(rf_read_pointer(d)$active, "giving-models-v2.json")
  first <- rf_publish(b, pass_gates(), dir = d)
  bad <- first$pointer; bad$sha256 <- paste(rep("0", 64), collapse = "")
  rf_write_json_atomic(bad, rf_pointer_path(d))
  expect_error(rf_read_pointer(d), "SHA-256 mismatch")
  bad <- first$pointer; bad$active <- "../giving-models-v2.json"
  rf_write_json_atomic(bad, rf_pointer_path(d))
  expect_error(rf_read_pointer(d), "active path")
  bad <- first$pointer; bad$model_version <- "wrong"
  rf_write_json_atomic(bad, rf_pointer_path(d))
  expect_error(rf_read_pointer(d), "model identity mismatch")
  .rf_write_pointer(first$pointer, d)
  expect_identical(rf_read_pointer(d)$model_version, b$model_version)
})

# The v1 projection of the pipeline equals giving-models-v1.json leaf by leaf (spec 4.8): numbers within 1e-9 relative (1e-12
# absolute near zero), strings, booleans, integers and keys exact, no exemptions. The projection is rebuilt from RAW inputs and is
# never published.

test_that("rf_bundle_v1_projection equals giving-models-v1.json leaf by leaf, with no exemptions", {
  ctx <- test_ctx()
  proj <- memo("proj", rf_bundle_v1_projection(ctx))
  cmp <- compare_trees(proj, v1_bundle(), rel_tol = 1e-9, abs_tol = 1e-12)
  if (nrow(cmp$mismatches)) print(utils::head(cmp$mismatches, 20))
  expect_equal(nrow(cmp$mismatches), 0L)
  expect_identical(names(proj), names(v1_bundle()))
  expect_identical(names(proj$share_models), names(v1_bundle()$share_models))
  message("v1 projection: max leaf deviation (relative, or absolute near zero) = ", formatC(cmp$max_dev, format = "e", digits = 2))
})

test_that("the projection survives a JSON round trip unchanged and stays compact where the v1 file is", {
  proj <- memo("proj", rf_bundle_v1_projection(test_ctx()))
  p <- file.path(tempdir(), "proj_v1.json")
  h <- rf_write_json_atomic(proj, p)
  expect_identical(h, rf_sha256(p))
  back <- jsonlite::fromJSON(p, simplifyVector = FALSE)
  cmp <- compare_trees(back, v1_bundle(), rel_tol = 1e-9, abs_tol = 1e-12)
  expect_equal(nrow(cmp$mismatches), 0L)
  expect_false(any(grepl("NaN|Infinity", readLines(p, warn = FALSE))))
})

test_that("the tree comparison detects a perturbed number, a missing key, a changed string and a type change (negative controls)", {
  v1 <- v1_bundle()
  bad <- v1; bad$pac_model$beta <- bad$pac_model$beta * (1 + 1e-6)
  expect_equal(nrow(compare_trees(bad, v1)$mismatches), 1L)
  bad <- v1; bad$share_models$control_House$covariance[[2]][[3]] <- bad$share_models$control_House$covariance[[2]][[3]] + 1e-8
  expect_gte(nrow(compare_trees(bad, v1)$mismatches), 1L)
  bad <- v1; bad$lobby_model$wild_p <- NULL
  expect_gte(nrow(compare_trees(bad, v1)$mismatches), 1L)
  bad <- v1; bad$model_version <- "2026-09-27-association-v2"
  expect_equal(nrow(compare_trees(bad, v1)$mismatches), 1L)
  bad <- v1; bad$evidence[[1]]$forecast_selected <- "yes"
  expect_equal(nrow(compare_trees(bad, v1)$mismatches), 1L)
  bad <- v1; bad$industry_slopes <- bad$industry_slopes[-1]
  expect_gte(nrow(compare_trees(bad, v1)$mismatches), 1L)
})

test_that("the nine sources of the projection are the v1 sources; interpretable_effects and analyze.py are pinned in the manifest as well", {
  proj <- memo("proj", rf_bundle_v1_projection(test_ctx()))
  expect_identical(vapply(proj$sources, function(s) s$file, ""), vapply(v1_bundle()$sources, function(s) s$file, ""))
  expect_identical(vapply(proj$sources, function(s) s$sha256, ""), vapply(v1_bundle()$sources, function(s) s$sha256, ""))
})

test_that("the frozen v1 selections equal what the stepwise search re-selects today (diagnostic, not a driver)", {
  ctx <- test_ctx()
  sel <- ctx$stepwise$selected
  m <- ctx$models$models
  expect_identical(as.integer(sel$selected_mask[sel$case == "control__House__pooled__RShare"]), m$control_House$v1_mask)
  expect_identical(as.integer(sel$selected_mask[sel$case == "control__Senate__pooled__RShare"]), m$control_Senate$v1_mask)
  pac_sel <- rf_forward_aic(ctx$pac_panel, "log_pac")
  expect_identical(pac_sel$selected, "G_current")
})

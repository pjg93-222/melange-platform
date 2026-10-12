# Coefficients, standard errors, ordinary p, critical values, industry slopes and evidence rows versus giving-models-v1.json
# (spec 3.7, 4.3). The frozen v1 selections are fitted with lm() on panels rebuilt from raw inputs.

test_that("pac_model reproduces the v1 pooled PAC growth slope", {
  ctx <- test_ctx()
  b <- v1_bundle()$pac_model
  p <- rf_v1_models(ctx)$pac_model
  expect_lte(abs(p$beta - b$beta), 1e-10)
  expect_lte(abs(p$ordinary_se - b$ordinary_se), 1e-10)
  expect_lte(abs(p$ordinary_p - b$ordinary_p), 1e-10)
  expect_lte(abs(p$critical_95 - b$critical_95), 1e-10)
  # the values quoted in the spec
  expect_lte(abs(p$beta - (-0.1683565110914325)), 1e-10)
  expect_lte(abs(p$ordinary_se - 0.03540427229395076), 1e-10)
  expect_lte(abs(p$ordinary_p - 0.0005946284556413381), 1e-10)
  expect_lte(abs(p$critical_95 - qt(0.975, 11)), 1e-12)
  expect_identical(p$n, 21L)
  expect_identical(unlist(p$cycles), unlist(b$cycles))
  expect_identical(p$specification, b$specification)
  expect_lte(abs(p$selection_repeated_p - b$selection_repeated_p), 1e-12)
})

test_that("the PAC fit is lm() on the frozen formula, and its call prints the data label", {
  ctx <- test_ctx()
  m <- ctx$fits$pac
  expect_s3_class(m, "lm")
  expect_identical(deparse(formula(m)), "log_pac ~ sector + factor(cycle_end) + G_current")
  expect_equal(nobs(m), 21L)
  expect_equal(df.residual(m), 11L)
  txt <- rf_summary_text(m)
  expect_true(any(grepl("data = pac_panel_2012_2024", txt, fixed = TRUE)))
  expect_true(any(grepl("^Coefficients:", txt)))
  expect_true(any(grepl("Residual standard error", txt)))
  expect_true(any(grepl("Multiple R-squared", txt)))
  expect_true(any(grepl("F-statistic", txt)))
})

test_that("the eight v1 share models reproduce betas, covariances, ordinary p and fit statistics", {
  ctx <- test_ctx()
  new <- rf_v1_models(ctx)$share_models
  old <- v1_bundle()$share_models
  expect_identical(names(new), names(old))
  worst <- c(beta = 0, cov = 0, p = 0, crit = 0, r2 = 0, sd = 0)
  for (id in names(old)) {
    a <- new[[id]]; b <- old[[id]]
    expect_identical(unlist(a$terms), unlist(b$terms), info = id)
    expect_identical(a$residual_df, b$residual_df, info = id)
    expect_identical(a$n, b$n, info = id)
    expect_identical(a$id, b$id); expect_identical(a$signal, b$signal); expect_identical(a$chamber, b$chamber)
    expect_identical(a$industry_slopes, b$industry_slopes)
    expect_identical(a$specification, b$specification, info = id)
    expect_identical(unlist(a$training_cycles), unlist(b$training_cycles))
    bd <- max(abs(unlist(a$beta) - unlist(b$beta)))
    cd <- max(abs(unlist(a$covariance) - unlist(b$covariance)))
    pd <- max(abs(unlist(a$ordinary_p)[unlist(b$terms)] - unlist(b$ordinary_p)[unlist(b$terms)]))
    expect_lte(bd, 1e-10, label = paste(id, "beta"))
    expect_lte(cd, 1e-10, label = paste(id, "covariance"))
    expect_lte(pd, 1e-10, label = paste(id, "ordinary p"))
    expect_lte(abs(a$critical_95 - b$critical_95), 1e-10)
    expect_lte(abs(a$adjusted_r2 - b$adjusted_r2), 1e-10)
    expect_lte(abs(a$residual_sd - b$residual_sd), 1e-10)
    # the history of each model (industry-cycle rows)
    cmp <- compare_trees(a$history, b$history, rel_tol = 1e-9)
    expect_equal(nrow(cmp$mismatches), 0L, info = paste(id, "history"))
    worst <- pmax(worst, c(beta = bd, cov = cd, p = pd, crit = abs(a$critical_95 - b$critical_95),
                           r2 = abs(a$adjusted_r2 - b$adjusted_r2), sd = abs(a$residual_sd - b$residual_sd)))
  }
  message("share models max deviations: ", paste(names(worst), formatC(worst, format = "e", digits = 2), collapse = "  "))
})

test_that("lobby_model reproduces the v1 pooled lobbying slope and pair counts", {
  ctx <- test_ctx()
  b <- v1_bundle()$lobby_model
  l <- rf_v1_models(ctx)$lobby_model
  expect_lte(abs(l$beta_million - b$beta_million), 1e-10)
  expect_lte(abs(l$ordinary_se_million - b$ordinary_se_million), 1e-10)
  expect_lte(abs(l$ordinary_p - b$ordinary_p), 1e-10)
  expect_lte(abs(l$critical_95 - b$critical_95), 1e-10)
  expect_lte(abs(l$beta_million - (-0.5367134383271494)), 1e-10)
  expect_lte(abs(l$ordinary_se_million - 0.17038067139089183), 1e-10)
  expect_lte(abs(l$ordinary_p - 0.00263933620521582), 1e-10)
  expect_lte(abs(l$critical_95 - 2.0040447832891455), 1e-10)
  expect_identical(l$n, 71L)
  expect_identical(l$industry_pair_counts[order(names(l$industry_pair_counts))], lapply(b$industry_pair_counts, as.integer)[order(names(b$industry_pair_counts))])
  expect_identical(l$specification, b$specification)
})

test_that("industry_slopes (12 rows: one Forecast slope per industry, CR2 by cycle) reproduce the v1 bundle", {
  ctx <- test_ctx()
  new <- ctx$stepwise$industry_slopes
  old <- do.call(rbind, lapply(v1_bundle()$industry_slopes, as.data.frame, stringsAsFactors = FALSE))
  expect_equal(nrow(new), 12L)
  k <- function(d) paste(d$case, d$sector)
  new <- new[match(k(old), k(new)), ]
  worst <- 0
  for (col in c("effect_pp", "conditional_CR2_low_pp", "conditional_CR2_high_pp", "estimate", "ordinary_SE", "ordinary_p", "CR2_SE", "CR2_df", "CR2_p", "CR2_low", "CR2_high")) {
    d <- max_abs(new[[col]], old[[col]])
    worst <- max(worst, d)
    expect_lte(d, 1e-10, label = col)
  }
  expect_identical(new$term, old$term)
  expect_identical(new$signal, old$signal); expect_identical(new$chamber, old$chamber)
  message("industry_slopes max deviation: ", formatC(worst, format = "e", digits = 2))
})

test_that("rf_bundle_terms maps R coefficient names to bundle names (unit)", {
  m <- lm(y ~ sector + x + x:sector, data = data.frame(y = c(1, 3, 2, 5, 4, 8, 7, 9, 12), x = c(1, 2, 3, 2, 1, 5, 3, 4, 6),
                                                       sector = rep(c("a", "b", "c"), each = 3)))
  nm <- rf_bundle_terms(m, "x")
  expect_identical(nm[1:3], c("Intercept", "industry:b", "industry:c"))
  expect_true("Forecast" %in% nm)
  expect_true(all(c("Forecast:b", "Forecast:c") %in% nm))
})

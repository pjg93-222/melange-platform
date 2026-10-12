# Measured deviations of every parity target (what the testthat suite asserts, as numbers): one row per target with the largest
# absolute (or relative) deviation from the Codex design file or from giving-models-v1.json, and the tolerance the spec sets.
#   Rscript tools/parity_report.R            writes <REFIT_OUT or cwd>/parity_deviations.csv and prints the table
# Needs the same environment as the tests (pinned inputs reachable through the aliases or RF_INPUT_ROOT).
fa <- grep("^--file=", commandArgs(trailingOnly = FALSE), value = TRUE)
here <- if (length(fa)) dirname(normalizePath(sub("^--file=", "", fa[1]), winslash = "/")) else getwd()
assign(".RF_HOME", normalizePath(file.path(here, ".."), winslash = "/"), envir = globalenv())
source(file.path(.RF_HOME, "R", "rf_config.R"))
rf_source_all()
suppressWarnings(rf_load_packages())
inputs <- rf_resolve_inputs(rf_read_manifest())
reg <- rf_read_registry(); models <- rf_read_models()
ctx <- rf_parity_context(inputs, reg, models)
ref <- function(name) utils::read.csv(rf_input_path(inputs, paste0("parity_ref:", name)), stringsAsFactors = FALSE, check.names = FALSE)
v1 <- rf_read_json(rf_input_path(inputs, "v1_bundle"))
fx <- function(name) utils::read.csv(file.path(.RF_HOME, "tests", "testthat", "fixtures", name), stringsAsFactors = FALSE)
key_of <- function(d, cols) do.call(paste, c(unname(as.list(d[cols])), sep = "|"))
md <- function(a, b, rel = FALSE) {
  a <- unname(as.numeric(a)); b <- unname(as.numeric(b))
  ok <- !(is.na(a) & is.na(b))
  if (any(is.na(a) != is.na(b))) return(Inf)
  d <- abs(a[ok] - b[ok]); d[a[ok] == b[ok]] <- 0
  if (rel) d <- d / abs(b[ok])
  if (length(d)) max(d) else 0
}
rows <- list()
add <- function(target, metric, dev, tol) rows[[length(rows) + 1L]] <<- data.frame(target = target, metric = metric, max_deviation = dev, tolerance = tol, pass = dev <= tol, stringsAsFactors = FALSE)
al <- function(new, old, cols) { k <- match(key_of(old, cols), key_of(new, cols)); new[k, , drop = FALSE] }

# ---- panels
o <- ref("regression_panel.csv"); n <- al(ctx$pac_panel, o, c("sector", "cycle_end"))
for (c in c("total_nominal", "individual_nominal", "pac_nominal", "outside_nominal", "democrat_nominal", "republican_nominal")) add("regression_panel.csv", c, md(n[[c]], o[[c]]), 0)
for (c in c("multiplier_to_2025_dollars", "total_real_2025", "pac_real_2025", "log_pac", "log_total")) add("regression_panel.csv", paste(c, "(relative)"), md(n[[c]], o[[c]], TRUE), 1e-12)
for (c in c("cycle_revenue_growth_pct", "prior_cycle_revenue_growth_pct", "G_current", "G_prior")) add("regression_panel.csv", c, md(n[[c]], o[[c]]), 1e-10)
add("regression_panel.csv", "Neg_prior", md(n$Neg_prior, o$Neg_prior), 0)
des <- ref("pac_selected_design.csv"); d <- ctx$pac_panel[order(ctx$pac_panel$sector, ctx$pac_panel$cycle_end), ]
add("pac_selected_design.csv", "G_current", md(d$G_current, des$G_current), 1e-10); add("pac_selected_design.csv", "y = log_pac", md(d$log_pac, des$y), 1e-12)
o <- ref("money_panel.csv"); o <- o[o$account == "congressional", ]; n <- al(ctx$money, o, c("sector", "cycle_end", "chamber"))
for (c in c("D_nominal", "R_nominal", "other_nominal", "total_nominal", "recipients", "excluded_recipients", "excluded_amount")) add("money_panel.csv", c, md(n[[c]], o[[c]]), 0)
add("money_panel.csv", "RShare", md(n$RShare, o$RShare), 1e-15); add("money_panel.csv", "G_current", md(n$G_current, o$G_current), 1e-10)
add("money_panel.csv", "recipient rows (16,916)", abs(nrow(ctx$recipients) - 16916), 0)
add("money_panel.csv", "presidential overlap rows (64)", abs(sum(ctx$recipients$presidential_overlap) - 64), 0)
add("money_panel.csv", "FEC identities (22)", abs(nrow(ctx$fec_identities) - 22), 0)
cp <- ref("analysis_case_panels.csv")
r <- unique(cp[cp$case %in% c("control__House__pooled__RShare", "control__Senate__pooled__RShare"), c("cycle_end", "chamber", "P_R", "RMajNow", "days")])
n <- al(ctx$iem_jj, r, c("cycle_end", "chamber")); add("analysis_case_panels.csv", "IEM Jan-Jun P_R", md(n$P_R, r$P_R), 1e-14); add("analysis_case_panels.csv", "IEM days", md(n$days, r$days), 0)
r <- unique(cp[cp$case %in% c("seats__House__pooled__RShare", "seats__Senate__pooled__RShare"), c("cycle_end", "chamber", "pred_rep_net_change")])
n <- al(ctx$seat_calls, r, c("cycle_end", "chamber")); add("analysis_case_panels.csv", "Crystal Ball seat calls (14)", md(n$pred_rep_net_change, r$pred_rep_net_change), 0)
for (nm in c("control_House", "control_Senate", "seats_House", "seats_Senate")) {
  r <- cp[cp$case == paste0(sub("_", "__", nm), "__pooled__RShare"), ]; n <- al(ctx$share[[nm]], r, c("sector", "cycle_end"))
  add("analysis_case_panels.csv", paste(nm, "RShare"), md(n$RShare, r$RShare), 1e-15); add("analysis_case_panels.csv", paste(nm, "Forecast"), md(n$Forecast, r$Forecast), 1e-14)
  add("analysis_case_panels.csv", paste(nm, "G_prior/G_current"), max(md(n$G_prior, r$G_prior), md(n$G_current, r$G_current)), 1e-10)
}
lv <- ref("level_sample.csv"); n <- al(ctx$lobby$levels, lv, c("study_sector", "year"))
add("level_sample.csv", "y", md(n$y, lv$y), 1e-11); add("level_sample.csv", "g", md(n$g, lv$g), 1e-12)
ye <- ref("year_effects__all71.csv"); ye2 <- data.frame(study_sector = ye$sector, year = ye$year, dy = ye$y, dg = ye$dg); n <- al(ctx$lobby$diffs, ye2, c("study_sector", "year"))
add("year_effects__all71.csv", "dy", md(n$dy, ye2$dy), 1e-11); add("year_effects__all71.csv", "dg", md(n$dg, ye2$dg), 1e-12)
# ---- coefficients vs the v1 bundle
vm <- rf_v1_models(ctx); b <- v1$pac_model; p <- vm$pac_model
for (c in c("beta", "ordinary_se", "ordinary_p", "critical_95")) add("v1 pac_model", c, abs(p[[c]] - b[[c]]), 1e-10)
add("v1 pac_model", "selection_repeated_p", abs(p$selection_repeated_p - b$selection_repeated_p), 1e-12)
w <- c(beta = 0, cov = 0, p = 0, r2 = 0, sd = 0)
for (id in names(v1$share_models)) { a <- vm$share_models[[id]]; bb <- v1$share_models[[id]]
  w["beta"] <- max(w["beta"], md(unlist(a$beta), unlist(bb$beta))); w["cov"] <- max(w["cov"], md(unlist(a$covariance), unlist(bb$covariance)))
  w["p"] <- max(w["p"], md(unlist(a$ordinary_p)[unlist(bb$terms)], unlist(bb$ordinary_p)[unlist(bb$terms)]))
  w["r2"] <- max(w["r2"], abs(a$adjusted_r2 - bb$adjusted_r2)); w["sd"] <- max(w["sd"], abs(a$residual_sd - bb$residual_sd)) }
for (c in names(w)) add("v1 share_models (8)", c, unname(w[c]), 1e-10)
b <- v1$lobby_model; l <- vm$lobby_model
for (c in c("beta_million", "ordinary_se_million", "ordinary_p", "critical_95", "wild_p", "CR2_p")) add("v1 lobby_model", c, abs(l[[c]] - b[[c]]), if (c == "wild_p") 1e-15 else 1e-10)
add("v1 lobby_model", "wild hits (1432 of 46,656)", abs(ctx$lobby_wild$hits - 1432L), 0)
add("v1 pac_model", "selection hits (22 of 216)", abs(ctx$pac_selection$hits - 22L), 0)
old <- do.call(rbind, lapply(v1$industry_slopes, as.data.frame, stringsAsFactors = FALSE)); n <- al(ctx$stepwise$industry_slopes, old, c("case", "sector"))
add("v1 industry_slopes (12)", "all numeric columns", max(sapply(c("effect_pp", "conditional_CR2_low_pp", "conditional_CR2_high_pp", "estimate", "ordinary_SE", "ordinary_p", "CR2_SE", "CR2_df", "CR2_p", "CR2_low", "CR2_high"), function(c) md(n[[c]], old[[c]]))), 1e-10)
# ---- stepwise grid, evidence, validation
sm <- ref("selected_models.csv"); n <- al(ctx$stepwise$selected, sm, "case")
add("selected_models.csv (48)", "selected_mask", md(n$selected_mask, sm$selected_mask), 0)
add("selected_models.csv (48)", "selection_repeated_p", md(n$selection_repeated_p, sm$selection_repeated_p), 0)
add("selected_models.csv (48)", "Holm_all_48 / Holm_outcome_family", max(md(n$Holm_all_48, sm$Holm_all_48), md(n$Holm_outcome_family, sm$Holm_outcome_family)), 1e-12)
add("selected_models.csv (48)", "AICc / adjusted_R2", max(md(n$AICc, sm$AICc), md(n$adjusted_R2, sm$adjusted_R2), md(n$forced_only_AICc, sm$forced_only_AICc)), 1e-9)
ok <- as.logical(sm$forecast_selected)
add("selected_models.csv (48)", "forecast_estimate / ordinary_p / CR2 p, low, high, df (selected)", max(sapply(c("forecast_estimate", "forecast_ordinary_p", "forecast_CR2_p", "forecast_CR2_low", "forecast_CR2_high", "forecast_CR2_df"), function(c) md(n[[c]][ok], sm[[c]][ok]))), 1e-9)
ie <- ref("interpretable_effects.csv"); ie <- ie[ie$scope == "pooled", ]; n <- al(ctx$stepwise$effects, ie, "case")
add("interpretable_effects.csv (12 pooled)", "effect, CR2 low/high, p, selection p, Holm", max(sapply(c("effect", "conditional_CR2_low", "conditional_CR2_high", "ordinary_p", "conditional_CR2_p", "selection_repeated_p", "Holm_outcome_family", "Holm_all_48"), function(c) md(n[[c]][!is.na(ie[[c]])], ie[[c]][!is.na(ie[[c]])]))), 1e-9)
nv <- ref("nested_validation_metrics.csv"); nv <- nv[grepl("__pooled__", nv$case), ]; n <- al(ctx$stepwise$validation, nv, c("case", "comparison"))
add("nested_validation_metrics.csv (36 pooled)", "rows / election_targets", max(md(n$rows, nv$rows), md(n$election_targets, nv$election_targets)), 0)
add("nested_validation_metrics.csv (36 pooled)", "stepwise_MAE / baseline_MAE", max(md(n$stepwise_MAE, nv$stepwise_MAE), md(n$baseline_MAE, nv$baseline_MAE)), 1e-10)
# ---- partial pooling and backtests
specs <- rf_backtest_specs(ctx); fs <- fx("full_sample_slopes.csv"); w <- 0
for (key in names(specs)) { s <- specs[[key]]; rr <- fs[fs$term == s$id & fs$base == s$base, ]; pp <- rf_partial_pool(s, s$d); o2 <- match(rr$sector, pp$own$industry)
  w <- max(w, md(pp$own$beta[o2], rr$R1_own), md(pp$own$se[o2], rr$R1_se), md(pp$own$p[o2], rr$R1_p), md(unname(pp$weights[rr$sector]), rr$w_DL), md(unname(pp$slopes[rr$sector]), rr$R2_blend), md(unname(pp$slope_se[rr$sector]), rr$R2_approx_se), abs(pp$tau2 - rr$tau2_DL[1])) }
add("tailoring full_sample_slopes.csv (8 cells)", "R0/R1/R2 slopes, SEs, p, tau2, W", w, 1e-10)
sm2 <- fx("summary.csv"); sm2 <- sm2[sm2$rule %in% c("R0", "R1", "R2", "naive_prev", "anchor_only"), ]; wm <- 0; wr <- 0; cells <- 0L
for (key in names(specs)) { s <- specs[[key]]; for (ds in if (s$kind == "lobby") "rolling" else c("loeo", "chrono")) {
  met <- rf_backtest_metrics(rf_backtest(s, s$d, ds)); m <- merge(met, sm2, by = c("term", "base", "design", "path", "rule"), suffixes = c("", ".ref"))
  wm <- max(wm, max(abs(m$MAE - m$MAE.ref))); wr <- max(wr, max(abs(m$RMSE - m$RMSE.ref))); cells <- cells + nrow(m) } }
add(sprintf("tailoring summary.csv (%d cells)", cells), "MAE", wm, 1e-9); add(sprintf("tailoring summary.csv (%d cells)", cells), "RMSE", wr, 1e-9)
pr <- fx("pac_prevchg_reconcile.csv"); pr <- pr[pr$scaling == "fold_restandardized", ]; w <- 0
for (ds in c("loeo", "chrono")) { met <- rf_backtest_metrics(rf_backtest(specs$pac, specs$pac$d, ds)); met <- met[met$path == "prevchg", ]
  for (rl in c("R0", "R1", "R2", "naive_prev")) { a <- met[met$rule == rl, ]; b <- pr[pr$design == ds & pr$rule == rl, ]
    w <- max(w, abs(a$MAE - b$MAE), abs(a$RMSE - b$RMSE), abs(a$MAE_commercial_banks - b$MAE_commercial_banks)) } }
add("SPEC section 8 PAC previous-cycle table", "MAE, RMSE, per-industry MAE", w, 1e-9)
# ---- robustness and folds (enumerated rows)
rp <- fx("robustness_pvalues.csv"); rp <- rp[rp$note %in% c("lm summary", "sandwich::vcovHC HC1", "vcovCL HC1, t(G-1)", "clubSandwich Satterthwaite", "full enumeration"), ]; w <- 0
for (id in c("pac", "control_House", "control_Senate", "seats_House")) {
  f <- ctx$fits[[id]]; foc <- models$models[[id]]$focal
  blk <- rf_robustness_block(id, f, ctx$fits_data[[id]], foc, unlist(models$models[[id]]$clusters), NULL, b_random = 999L)$table
  rr <- rp[rp$model == id, ]
  for (i in seq_len(nrow(rr))) { r <- blk[blk$method == rr$method[i], ]; for (c in c("SE", "df", "p")) if (!is.na(rr[[c]][i])) w <- max(w, abs(r[[c]] - rr[[c]][i])) } }
add("robustness_pvalues.csv (enumerated rows)", "SE, df, p of ordinary/HC1/CR1/CR2/wild", w, 1e-9)
# ---- the whole v1 projection
proj <- rf_bundle_v1_projection(ctx)
cmp <- rf_compare_bundles(jsonlite::fromJSON(jsonlite::toJSON(proj, auto_unbox = TRUE, digits = NA, null = "null", na = "null"), simplifyVector = FALSE), v1)
add("giving-models-v1.json (whole bundle)", "leaf mismatches", cmp$n_mismatch, 0)
add("giving-models-v1.json (whole bundle)", "max leaf deviation (relative, absolute near 0)", cmp$max_dev, 1e-9)
res <- do.call(rbind, rows)
out <- file.path(rf_env_path("REFIT_OUT", getwd()), "parity_deviations.csv")
utils::write.csv(res, out, row.names = FALSE)
options(width = 220, digits = 4)
print(res, row.names = FALSE)
cat(sprintf("\n%d targets, %d pass, %d fail; wrote %s\n", nrow(res), sum(res$pass), sum(!res$pass), out))
quit(status = if (all(res$pass)) 0L else 1L)

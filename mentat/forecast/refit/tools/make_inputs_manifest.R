# One-time (re)generation of registry/inputs.json: the raw-input manifest with pinned sha256.
#   & "C:\Program Files\R\R-4.6.0\bin\Rscript.exe" tools\make_inputs_manifest.R
# Run it only when Paul supplies a new vintage; the committed manifest is what the pipeline enforces (gate G1).
# It reads the files at their alias locations and writes registry/inputs.json; nothing else.

fa <- grep("^--file=", commandArgs(trailingOnly = FALSE), value = TRUE)
here <- if (length(fa)) dirname(normalizePath(sub("^--file=", "", fa[1]), winslash = "/")) else getwd()
.RF_HOME <- normalizePath(file.path(here, ".."), winslash = "/")
source(file.path(.RF_HOME, "R", "rf_config.R"))
source(file.path(.RF_HOME, "R", "rf_io.R"))
roots <- rf_roots()
entries <- list()
add <- function(role, path, purpose = "raw", member = NULL, sheet = NULL, vintage = "", supplied_by = "") {
  p <- rf_expand(path, roots)
  if (!file.exists(p)) stop("missing input: ", p)
  entries[[length(entries) + 1L]] <<- list(role = role, path = path, member = member, sheet = sheet, sha256 = rf_sha256(p),
                                           bytes = as.integer(file.size(p)), pinned = TRUE, purpose = purpose,
                                           vintage = vintage, supplied_by = supplied_by)
}
OSB <- "{LOO}/outputs/opensecrets_baseline/direct_sources"
OSP <- "{LOO}/outputs/opensecrets_partisanship/direct_sources"
codes <- c(commercial_banks = "F03", credit_unions = "F05", auto_light_duty_manufacturing = "T2100")
cycles <- seq(2012, 2024, 2)
for (id in names(codes)) {
  cd <- codes[[id]]
  add(paste0("os_totals:", id), sprintf("%s/%s_2024_totals.json", OSB, cd), vintage = "OpenSecrets capture 2026-09-27",
      supplied_by = "browser capture (loo/work/opensecrets_baseline/collect_direct.py)")
  for (cy in cycles) {
    for (ch in c("H", "S")) {
      add(sprintf("os_recipients:%s:%d:%s", id, cy, ch), sprintf("%s/%s_%d_candidates_%s.json", OSP, cd, cy, ch),
          vintage = "OpenSecrets capture 2026-09-27", supplied_by = "browser capture (loo/work/opensecrets_partisanship/collect.py)")
    }
    add(sprintf("os_presidential:%s:%d", id, cy), sprintf("%s/%s_%d_presidential.json", OSP, cd, cy),
        vintage = "OpenSecrets capture 2026-09-27", supplied_by = "browser capture (loo/work/opensecrets_partisanship/collect.py)")
  }
}
for (cy in c(2012, 2016, 2020, 2024)) {
  add(sprintf("fec_cn:%d", cy), sprintf("{LOO}/outputs/opensecrets_partisanship/fec_sources/cn%02d.zip", cy %% 100), member = "cn.txt",
      vintage = "FEC candidate master bulk file", supplied_by = "fec.gov bulk download (loo/work/opensecrets_partisanship/download_fec.py)")
}
add("ibis_actual", "{SGSD}/07_actual_industry_growth.xlsx", sheet = "data", vintage = "IBISWorld 2026-07/08 vintages",
    supplied_by = "sector growth study workbook 07")
add("ibis_predicted", "{SGSD}/06_predicted_industry_growth.xlsx", sheet = "data", vintage = "IBISWorld forecasts 2008-2025",
    supplied_by = "sector growth study workbook 06 (sheets data, component_forecasts)")
add("lobbyview_sector_year", "{SGSD}/01_lobbying_expenditures.xlsx", sheet = "lobbyview_sector_year_all",
    vintage = "LobbyView export 2024-10-25, dedup ruling 2026-09-25", supplied_by = "sector growth study workbook 01")
add("cpi", "{SGSD}/00_controls.xlsx", sheet = "data", vintage = "CPI-U annual average, 1982-84=100; 2025 = 11 months",
    supplied_by = "sector growth study workbook 00 (Minneapolis Fed table)")
add("current_control", "{SGSD}/03_actual_seat_gain.xlsx", sheet = "current_control", vintage = "actual congressional control 2008-2026",
    supplied_by = "sector growth study workbook 03")
add("election_workbook", "{SGSD}/02_election_forecasting.xlsx", sheet = "current_2026", vintage = "read 2026-09-24/25",
    supplied_by = "sector growth study workbook 02")
add("iem_zip", "{ZIPC}", member = "iem_contract_prices_raw.csv; predicted_house.csv; predicted_senate.csv",
    vintage = "IEM contract prices 2012-2022; Crystal Ball seat calls 2004-2024", supplied_by = "House_Senate_Control_Forecasts_CSV.zip (Downloads)")
for (m in c("kalshi", "polymarket")) for (ch in c("house", "senate")) {
  add(sprintf("market_daily:%s:%s:2026", m, ch), sprintf("{SGSA}/expected_control/daily_%s_%s_2026.csv", m, ch),
      vintage = "daily history to 2026-09-24", supplied_by = "sector growth study archive (build_receipt_02_expected_control_markets.json)")
  add(sprintf("market_daily:%s:%s:2024", m, ch), sprintf("{SGSA}/expected_control/daily_%s_%s_2024.csv", m, ch),
      vintage = "daily history of the 2024 market (Kalshi starts 2024-09-12: no Jan-Jun data)", supplied_by = "sector growth study archive (build_receipt_02_expected_control_markets.json)")
}
# Codex design files: parity references only (the pipeline rebuilds every one of them from the raw inputs above).
refs <- c("{CHE}/outputs/stepwise/selected_models.csv", "{CHE}/work/stepwise/analysis_case_panels.csv",
          "{LOO}/outputs/opensecrets_tests/regression_panel.csv", "{LOO}/outputs/opensecrets_forward/pac_selected_design.csv",
          "{LOO}/outputs/r2_lobbying_audit/designs/year_effects__all71.csv", "{LOO}/outputs/opensecrets_partisanship/money_panel.csv",
          "{CHE}/outputs/stepwise/industry_forecast_slopes.csv", "{CHE}/outputs/stepwise/nested_validation_metrics.csv",
          "{CHE}/outputs/stepwise/interpretable_effects.csv", "{CHE}/work/stepwise/analyze.py",
          "{LOO}/outputs/lobbying_followup/level_sample.csv", "{LOO}/outputs/forecast_followup/lobbying_comparison_sample.csv")
for (r in refs) add(paste0("parity_ref:", basename(r)), r, purpose = "parity_ref", vintage = "Codex design file of the v1 build",
                    supplied_by = "Codex 2026-09-26/27 lanes; hashed in giving-models-v1.json sources (interpretable_effects.csv and analyze.py were read by the v1 builder but not listed there)")
add("v1_bundle", "{BUNDLE_V1}", purpose = "parity_ref", vintage = "giving-models-v1.json, model_version 2026-09-27-association-v1",
    supplied_by = "mentat/app/data (committed in Mentat 7287dbb50)")
# Property and casualty (owner ruling 2026-09-30): OpenSecrets F09 (all insurance, scope caveat in the registry). The totals are the frozen baseline capture of
# 2026-09-27; the 21 recipient and presidential pages were fetched by the feeds ingest (run local-f09-proposals-20260930) and are read from its run folder.
# The 2024 current-year row comes from the 2024-11 IBISWorld edition (tools/make_pc_ibis_current.R): IBISWorld's current-year ESTIMATE, single vintage.
PCO <- "{INGEST}/local-f09-proposals-20260930/outputs/os"
add("os_totals:property_casualty", sprintf("%s/F09_2024_totals.json", OSB), vintage = "OpenSecrets capture 2026-09-27 (frozen baseline)",
    supplied_by = "frozen baseline capture of 2026-09-27 (not re-fetched)")
for (cy in cycles) {
  for (ch in c("H", "S")) add(sprintf("os_recipients:property_casualty:%d:%s", cy, ch), sprintf("%s/F09_%d_candidates_%s.json", PCO, cy, ch),
                               vintage = "OpenSecrets F09 page fetched 2026-09-30 (run local-f09-proposals-20260930)", supplied_by = "giving-forecast-ingest")
  add(sprintf("os_presidential:property_casualty:%d", cy), sprintf("%s/F09_%d_presidential.json", PCO, cy),
      vintage = "OpenSecrets F09 page fetched 2026-09-30 (run local-f09-proposals-20260930)", supplied_by = "giving-forecast-ingest")
}
add("ibis_actual_current:property_casualty", "{RFDATA}/ibis_pc_2024_11_current_year.csv", vintage = "IBISWorld 52412 edition 2024-11, current-year (2024) ESTIMATE",
    supplied_by = "tools/make_pc_ibis_current.R (feeds ingester parser on the 2024-11 edition, sha256 f484c65f...)")
rf_write_json_atomic(list(schema_version = 1L, note = "Raw-input manifest. A pinned input whose sha256 changes FAILS gate G1 until this file is regenerated for a new vintage. purpose 'parity_ref' files are Codex design files the pipeline rebuilds and compares against; they are never model inputs.",
                          inputs = entries), file.path(.RF_HOME, "registry", "inputs.json"))
cat(sprintf("Wrote %d manifest entries\n", length(entries)))

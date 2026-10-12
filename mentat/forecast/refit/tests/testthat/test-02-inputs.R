# Raw-input manifest, sha256 pinning, the vault, and gate G1 (spec 3.5, 4.1).

# sha256 prefixes quoted in SPEC.md sections 0-2 (the v1 bundle sources and every raw input).
PINNED <- c(
  "parity_ref:selected_models.csv" = "b1e43bf5", "parity_ref:analysis_case_panels.csv" = "f010d3bf",
  "parity_ref:regression_panel.csv" = "cd16608d", "parity_ref:pac_selected_design.csv" = "49b170d1",
  "parity_ref:year_effects__all71.csv" = "1e7c5c10", "parity_ref:money_panel.csv" = "62c2dc13",
  "parity_ref:industry_forecast_slopes.csv" = "2717a101", "parity_ref:nested_validation_metrics.csv" = "a9145d9a",
  "election_workbook" = "79d05103", "parity_ref:interpretable_effects.csv" = "51f3ae14", "parity_ref:analyze.py" = "c6b6a133",
  "cpi" = "253aaadd", "lobbyview_sector_year" = "908a23fd", "current_control" = "d7181b61", "ibis_predicted" = "6ee67a89",
  "ibis_actual" = "996b89e0", "iem_zip" = "852d894f",
  "fec_cn:2012" = "53a07683", "fec_cn:2016" = "7dd5b2df", "fec_cn:2020" = "f0229bb4", "fec_cn:2024" = "d3a99d3e",
  "os_totals:commercial_banks" = "a8ec8aad", "os_totals:credit_unions" = "bddd9f2c", "os_totals:auto_light_duty_manufacturing" = "5d8ec0e6",
  "market_daily:kalshi:house:2026" = "4f73e7af", "market_daily:polymarket:house:2026" = "7a7f0fa4",
  "market_daily:kalshi:senate:2026" = "6633296c", "market_daily:polymarket:senate:2026" = "c8f9a475",
  "v1_bundle" = "75c382af")

test_that("every manifest input resolves to a hash-checked vault copy and gate G1 passes", {
  inp <- test_inputs()
  expect_equal(nrow(inp), nrow(RF_TEST_MANIFEST))
  expect_true(all(inp$status %in% c("ok", "ok_from_vault")), info = paste(inp$role[!inp$status %in% c("ok", "ok_from_vault")], collapse = ", "))
  g1 <- rf_gate_inputs(inp)
  expect_identical(g1$status, "PASS")
  # vault copies are byte-equal to what the manifest pins
  for (i in seq_len(nrow(inp))) expect_identical(rf_sha256(inp$vault_path[i]), inp$expected[i])
  expect_true(all(startsWith(normalizePath(inp$vault_path, winslash = "/"), rf_vault_dir())))
})

test_that("the pinned hashes match the values quoted in the spec", {
  inp <- test_inputs()
  for (role in names(PINNED)) {
    i <- match(role, inp$role)
    expect_false(is.na(i), info = role)
    expect_identical(substr(rf_sha256(inp$vault_path[i]), 1, 8), PINNED[[role]], info = role)
  }
})

test_that("the nine v1 bundle sources hash to the values the v1 bundle records", {
  b <- v1_bundle()
  expect_equal(length(b$sources), 9L)
  inp <- test_inputs()
  role_of <- function(f) if (f == "02_election_forecasting.xlsx") "election_workbook" else paste0("parity_ref:", f)
  for (s in b$sources) expect_identical(rf_sha256(rf_input_path(inp, role_of(s$file))), s$sha256, info = s$file)
})

test_that("interpretable_effects.csv and analyze.py (read by the v1 builder, absent from its sources) are pinned", {
  m <- RF_TEST_MANIFEST
  expect_true(all(c("parity_ref:interpretable_effects.csv", "parity_ref:analyze.py") %in% m$role))
  expect_true(all(m$pinned))
})

test_that("a changed pinned hash fails G1 (negative control), and a vaulted copy survives a missing source", {
  m <- RF_TEST_MANIFEST
  k <- match("cpi", m$role)
  m$sha256[k] <- paste(rep("0", 64), collapse = "")
  inp <- rf_resolve_inputs(m, vault = FALSE)
  expect_identical(inp$status[k], "hash_mismatch")
  expect_identical(rf_gate_inputs(inp)$status, "FAIL")
  expect_error(rf_input_path(inp, "cpi"), "hash_mismatch")
  expect_error(rf_input_path(inp, "no_such_role"), "not in the manifest")
  # a source that is gone is served from the vault when its pinned hash is vaulted
  roots <- rf_roots()
  roots$SGSD <- file.path(tempdir(), "nonexistent_sgsd")
  inp2 <- rf_resolve_inputs(RF_TEST_MANIFEST, roots = roots, vault = FALSE)
  expect_identical(inp2$status[match("cpi", inp2$role)], "ok_from_vault")
  expect_identical(rf_gate_inputs(inp2[match(c("cpi", "ibis_actual"), inp2$role), ])$status, "PASS")
})

test_that("Glass Box events are written atomically with the SGS field shape", {
  dir <- file.path(tempdir(), "gb_unit")
  e <- rf_gb_run("unit-run-1", events_dir = dir)
  e$start("start of a unit run")
  e$stage_start("panels")
  e$stage_finish("panels", "panels built")
  e$gate("G1 inputs", "PASS", "94 inputs")
  e$finish("done")
  files <- list.files(dir, pattern = "^evt-.*\\.json$")
  expect_equal(length(files), 5L)
  expect_equal(length(list.files(dir, pattern = "\\.tmp$")), 0L)
  first <- jsonlite::fromJSON(file.path(dir, files[1]), simplifyVector = FALSE)
  expect_identical(first$schema_version, 1L)
  expect_identical(first$lane, "claude")
  expect_identical(first$source, "giving-forecast-refit")
  expect_identical(first$kind, "run.start")
  expect_identical(first$correlation_id, first$id)
  expect_match(first$id, "^evt-\\d{8}T\\d{6}\\d{7}Z-\\d{10}$")
  last <- jsonlite::fromJSON(file.path(dir, files[5]), simplifyVector = FALSE)
  expect_identical(last$kind, "run.finish")
  expect_identical(last$parent_id, first$id)
  kinds <- vapply(files, function(f) jsonlite::fromJSON(file.path(dir, f))$kind, "")
  expect_setequal(unname(kinds), c("run.start", "stage.start", "stage.finish", "gate.result", "run.finish"))
  raw <- readBin(file.path(dir, files[1]), "raw", 3)
  expect_false(identical(raw, as.raw(c(0xef, 0xbb, 0xbf))))
})

test_that("rf_write_json_atomic refuses non-finite numbers, writes UTF-8 without BOM, and returns the file hash", {
  p <- file.path(tempdir(), "atomic_unit", "x.json")
  expect_error(rf_write_json_atomic(list(a = 1, b = list(c = NaN)), p), "non-finite")
  expect_error(rf_write_json_atomic(list(a = c(1, Inf)), p), "non-finite")
  expect_false(file.exists(p))
  h <- rf_write_json_atomic(list(a = 1.5, b = "é", c = NA_real_, d = list(1, 2)), p)
  expect_identical(h, rf_sha256(p))
  expect_false(identical(readBin(p, "raw", 3), as.raw(c(0xef, 0xbb, 0xbf))))
  back <- jsonlite::fromJSON(p, simplifyVector = FALSE)
  expect_identical(back$b, "é")
  expect_null(back$c)
  expect_equal(length(list.files(dirname(p), pattern = "\\.tmp$")), 0L)
})

test_that("RF_INPUT_ROOT and RF_ROOT_<ALIAS> redirect the pinned input roots (same code, another machine)", {
  old <- Sys.getenv(c("RF_INPUT_ROOT", "RF_ROOT_SGSD"), unset = NA)
  on.exit({ for (k in names(old)) if (is.na(old[[k]])) Sys.unsetenv(k) else do.call(Sys.setenv, stats::setNames(list(old[[k]]), k)) }, add = TRUE)
  mirror <- file.path(tempdir(), "input_mirror")
  dir.create(file.path(mirror, "SGSD"), recursive = TRUE, showWarnings = FALSE)
  local_roots <- rf_roots()
  Sys.unsetenv("RF_ROOT_SGSD")             # a caller's override (the staged runs point SGSD at a mirror inside staging) must not outrank the umbrella this test checks; restored on exit
  file.copy(file.path(local_roots$SGSD, c("00_controls.xlsx", "07_actual_industry_growth.xlsx")), file.path(mirror, "SGSD"), overwrite = TRUE)
  Sys.setenv(RF_INPUT_ROOT = mirror)
  r <- rf_roots()
  expect_identical(r$SGSD, file.path(mirror, "SGSD"))
  expect_identical(r$LOO, file.path(mirror, "LOO"))
  expect_identical(r$ZIPC, file.path(mirror, "House_Senate_Control_Forecasts_CSV.zip"))
  expect_identical(r$BUNDLE_V1, local_roots$BUNDLE_V1)   # the v1 bundle stays in the checkout
  expect_identical(rf_expand("{SGSD}/00_controls.xlsx"), file.path(mirror, "SGSD", "00_controls.xlsx"))
  # the manifest still pins every file: the mirrored copies verify, an absent one is reported missing
  sub <- RF_TEST_MANIFEST[RF_TEST_MANIFEST$role %in% c("cpi", "ibis_actual", "current_control"), ]
  inp <- rf_resolve_inputs(sub, roots = rf_roots(), vault = FALSE)
  expect_identical(inp$status[inp$role == "cpi"], "ok")
  expect_identical(inp$status[inp$role == "ibis_actual"], "ok")
  expect_true(inp$status[inp$role == "current_control"] %in% c("missing", "ok_from_vault"))
  expect_true(startsWith(inp$path[inp$role == "cpi"], mirror))
  # a single alias override wins over the umbrella
  Sys.setenv(RF_ROOT_SGSD = file.path(tempdir(), "elsewhere"))
  expect_identical(rf_roots()$SGSD, file.path(tempdir(), "elsewhere"))
  expect_identical(rf_roots()$LOO, file.path(mirror, "LOO"))
  # a changed byte in the mirror fails the pin
  Sys.setenv(RF_ROOT_SGSD = file.path(mirror, "SGSD"))
  bad <- file.path(mirror, "SGSD", "00_controls.xlsx")
  bytes <- readBin(bad, "raw", file.size(bad)); bytes[length(bytes) - 1L] <- as.raw(bitwXor(as.integer(bytes[length(bytes) - 1L]), 1L))
  writeBin(bytes, bad)
  inp2 <- rf_resolve_inputs(sub[sub$role == "cpi", ], roots = rf_roots(), vault = FALSE)
  expect_identical(inp2$status, "hash_mismatch")
})

# Registry, frozen model specifications, package pins and the vendored library (spec 3.3, 3.4, 5).

test_that("the shipped registry is valid and carries the six fitted industries and four declined ones", {
  expect_identical(rf_validate_registry(RF_TEST_REG), character(0))
  ids <- names(RF_TEST_REG$industries)
  expect_setequal(ids[vapply(RF_TEST_REG$industries, function(i) i$status != "declined", logical(1))],
                  c("commercial_banks", "credit_unions", "auto_light_duty_manufacturing", "life_insurance", "telecom", "property_casualty"))
  declined <- Filter(function(i) i$status == "declined", RF_TEST_REG$industries)
  expect_setequal(names(declined), c("community_banks", "credit_arms", "crypto", "fintech"))
  for (i in declined) {
    expect_false(any(unlist(i$models)))
    expect_true(nzchar(i$reason))
  }
})

test_that("telecom is a scored-only calculator industry (owner ruling 2026-10-08: OpenSecrets B09 only, IBISWorld wireless + cable): the PAC and lobbying models, no control model, a stated reason for it, no lobbying_only flag", {
  tel <- RF_TEST_REG$industries$telecom
  expect_true(isTRUE(tel$calculator)); expect_null(tel$lobbying_only); expect_identical(tel$mentat_key, "telecom")
  expect_identical(unlist(tel$models), c(pac = TRUE, control = FALSE, lobbying = TRUE))
  expect_identical(tel$estimation, "scored_only"); expect_identical(tel$status, "pilot")
  # OpenSecrets scope = category B09 only: B08 (Telephone Utilities) is not part of it, as F09 is the one code of property and casualty
  expect_identical(unlist(tel$opensecrets$codes), "B09"); expect_identical(tel$opensecrets$code_status, "owner_ruling_b09_only")
  expect_match(tel$opensecrets$baseline_scope_caveat, "B09 = Telecom Services & Equipment; B08 (Telephone Utilities) is left out", fixed = TRUE)
  expect_match(tel$opensecrets$totals, "B09_totals.json", fixed = TRUE); expect_null(tel$opensecrets$recipients); expect_null(tel$opensecrets$presidential)
  # IBISWorld = wireless (51721) + cable (51711a), revenue-weighted
  expect_identical(unlist(tel$ibisworld$components), c("51721", "51711a")); expect_identical(unlist(tel$ibisworld$actual_components), c("telecom_wireless", "telecom_cable"))
  expect_identical(tel$ibisworld$actual_sector, "telecom"); expect_identical(tel$ibisworld$predicted_rule, "revenue_weighted_components")
  expect_true(is.na(rf_withheld_reason(tel, "pac"))); expect_true(is.na(rf_withheld_reason(tel, "lobbying")))
  expect_match(rf_withheld_reason(tel, "control"), "general-giving model only")
  # the registry checks the mode; the lobbying-only flag mechanism is kept for future industries
  bad <- RF_TEST_REG; bad$industries$telecom$models$control <- TRUE
  expect_true(any(grepl("a scored-only industry is a pilot with the PAC model, no control model", rf_validate_registry(bad))))
  bad <- RF_TEST_REG; bad$industries$telecom$status <- "fitted"
  expect_true(any(grepl("a scored-only industry", rf_validate_registry(bad))))
  bad <- RF_TEST_REG; bad$industries$telecom$estimation <- "pooled"
  expect_true(any(grepl("unknown estimation mode", rf_validate_registry(bad))))
  bad <- RF_TEST_REG; bad$industries$telecom$lobbying_only <- TRUE
  expect_true(any(grepl("a lobbying-only industry is a calculator industry with the lobbying model only", rf_validate_registry(bad))))
  expect_identical(rf_scored_ids(RF_TEST_REG), "telecom")
})

test_that("capabilities follow the inputs that exist: the frozen v1 sample has three industries, property and casualty (F09) is a ready pilot", {
  fr <- rf_frozen_registry(RF_TEST_REG)
  three <- c("commercial_banks", "credit_unions", "auto_light_duty_manufacturing")
  expect_setequal(rf_capable(fr, "pac"), three); expect_setequal(rf_capable(fr, "control"), three)
  expect_setequal(rf_capable(RF_TEST_REG, "pac"), c(three, "property_casualty", "telecom"))          # F09 supplied, the 2024-11 IBISWorld edition is its one vintage; B09 supplied, the 2024-10 / 2024-12 wireless + cable editions are telecom's
  expect_setequal(rf_capable(RF_TEST_REG, "lobbying"), c(three, "life_insurance", "telecom", "property_casualty"))
  expect_setequal(rf_capable(fr, "lobbying"), rf_capable(RF_TEST_REG, "lobbying"))         # the lobbying panel is LobbyView's: pilots keep it
  pc <- RF_TEST_REG$industries$property_casualty
  expect_true(is.na(rf_withheld_reason(pc, "pac"))); expect_true(is.na(rf_withheld_reason(pc, "control"))); expect_true(is.na(rf_withheld_reason(pc, "lobbying")))
  expect_identical(unlist(pc$opensecrets$codes), "F09"); expect_identical(pc$opensecrets$code_status, "owner_ruling_f09_all_insurance")
  expect_match(pc$opensecrets$baseline_scope_caveat, "F09 = all insurance; Mentat P&C roster and IBISWorld 52412 are P&C only")
  expect_length(pc$caveats, 1L); expect_match(pc$ibisworld$actual_current_year$caveat, "current-year estimate from the 2024-11 edition; replaced automatically when a 2025\\+ edition is ingested")
  # the OpenSecrets code -> id map used by the panel builders
  expect_identical(rf_code_map(fr), c(F03 = "commercial_banks", F05 = "credit_unions", T2100 = "auto_light_duty_manufacturing"))
  expect_identical(rf_code_map(RF_TEST_REG), c(F03 = "commercial_banks", F05 = "credit_unions", T2100 = "auto_light_duty_manufacturing", B09 = "telecom", F09 = "property_casualty"))
})

test_that("a ready pilot joins the v2 sample; without its 2024 growth it waits for data; a scored-only industry (telecom) never joins the pooled sample", {
  inp <- test_inputs()
  expect_identical(rf_ready_pilots(RF_TEST_REG, inp), "property_casualty")
  v2 <- rf_v2_registry(RF_TEST_REG, inp)
  expect_setequal(rf_capable(v2, "pac"), c("commercial_banks", "credit_unions", "auto_light_duty_manufacturing", "property_casualty"))
  expect_identical(v2$scored, "telecom")                                                                                         # ready (B09 totals, wireless + cable with the 2024 estimates), listed beside the pool
  expect_null(rf_v2_registry(RF_TEST_REG, inp[inp$role != "ibis_actual_current:telecom", ])$scored)                           # without its 2024 estimates it waits like any other
  st <- rf_agent_data_status(RF_TEST_REG, "telecom", inp[inp$role != "ibis_actual_current:telecom", ])
  expect_false(st$ready); expect_match(st$missing[[1]]$detail, "revenue ends before the 2024 cycle")
  expect_true(rf_agent_data_status(RF_TEST_REG, "telecom", inp)$ready)                                                         # no recipient captures are asked of an industry without a control model
  expect_null(rf_v2_registry(RF_TEST_REG, inp)$industries$telecom$lobbying_only)
  no_est <- inp[inp$role != "ibis_actual_current:property_casualty", ]
  expect_identical(rf_ready_pilots(RF_TEST_REG, no_est), character(0))                      # workbook 07 ends 2023: the 2024 cycle needs the estimate row
  st <- rf_agent_data_status(RF_TEST_REG, "property_casualty", no_est)
  expect_false(st$ready); expect_match(st$missing[[1]]$detail, "revenue ends before the 2024 cycle")
  expect_identical(names(rf_v2_registry(RF_TEST_REG, no_est)$industries), names(RF_TEST_REG$industries))
  expect_setequal(rf_capable(rf_v2_registry(RF_TEST_REG, no_est), "pac"), c("commercial_banks", "credit_unions", "auto_light_duty_manufacturing"))
})

test_that("registry validation catches a duplicate id, a pattern without {cycle}, and a calculator industry with no model", {
  bad <- RF_TEST_REG
  bad$industries$dup <- bad$industries$credit_unions
  bad$industries$dup$id <- "commercial_banks"
  expect_true(any(grepl("duplicate ids", rf_validate_registry(bad))))
  bad <- RF_TEST_REG
  bad$industries$credit_unions$opensecrets$recipients <- "x/F05_candidates_{chamber}.json"
  expect_true(any(grepl("no \\{cycle\\}", rf_validate_registry(bad))))
  bad <- RF_TEST_REG
  bad$industries$telecom$calculator <- TRUE
  bad$industries$telecom$models <- list(pac = FALSE, control = FALSE, lobbying = FALSE)
  expect_true(any(grepl("calculator industry with no model", rf_validate_registry(bad))))
  bad <- RF_TEST_REG
  bad$industries$crypto$models$pac <- TRUE
  expect_true(any(grepl("declined industry with a model", rf_validate_registry(bad))))
})

test_that("the v1 variable selections are frozen in models.json", {
  m <- RF_TEST_MODELS$models
  expect_identical(m$pac$formula, "log_pac ~ sector + factor(cycle_end) + G_current")
  expect_identical(m$control_House$v1_mask, 11L)
  expect_identical(m$control_Senate$v1_mask, 3L)
  expect_identical(m$seats_House$v1_mask, 15L)
  expect_identical(m$lobby$formula, "dy ~ dg + factor(year)")
  expect_false(isTRUE(m$seats_House$calculator))
  expect_true(isTRUE(m$pac$calculator) && isTRUE(m$lobby$calculator))
})

test_that("packages load at the pinned versions from the vendored library, and the Rlib matches its manifest", {
  expect_true(all(RF_TEST_PKGS$ok))
  expect_identical(RF_TEST_PKGS$version[RF_TEST_PKGS$package == "clubSandwich"], "0.7.0")
  expect_identical(RF_TEST_PKGS$version[RF_TEST_PKGS$package == "sandwich"], "3.1-3")
  expect_true(all(normalizePath(RF_TEST_PKGS$lib[RF_TEST_PKGS$package %in% c("clubSandwich", "sandwich")], winslash = "/") == rf_rlib_dir()))
  ver <- rf_verify_rlib()
  expect_gt(nrow(ver), 1L)
  expect_true(all(ver$ok))                                       # recorded platform: every file hash; otherwise the version only
  expect_true(all(ver$status %in% c("ok", "unrecorded_platform")))
  expect_true(all(c("clubSandwich", "sandwich") %in% ver$package))
  # negative control: a wrong pin is refused, never silently accepted
  expect_error(rf_load_packages(required = "jsonlite", pins = list(jsonlite = "0.0.1")), "pins")
})

test_that("the package manifest detects a tampered vendored file (negative control)", {
  tmp <- file.path(tempdir(), "rlib_tamper"); unlink(tmp, recursive = TRUE); dir.create(tmp)
  file.copy(file.path(rf_rlib_dir(), "sandwich"), tmp, recursive = TRUE)
  man <- file.path(tempdir(), "rlib_tamper_manifest.json"); unlink(man)
  rf_record_rlib_platform(target = tmp, packages = "sandwich", manifest_path = man)
  expect_true(all(rf_verify_rlib(tmp, man)$ok))
  f <- file.path(tmp, "sandwich", "R", "sandwich.rdx")
  b <- readBin(f, "raw", file.size(f)); b[5] <- as.raw(bitwXor(as.integer(b[5]), 1L)); writeBin(b, f)
  v <- rf_verify_rlib(tmp, man)
  expect_identical(v$status[v$file == "R/sandwich.rdx"], "mismatch")
  expect_false(all(v$ok))
})

test_that("the manifest is checked against the platform the library was built for, which is not always the platform R runs on (the container loads the Windows-built vendored library)", {
  tmp <- file.path(tempdir(), "rlib_platform"); unlink(tmp, recursive = TRUE); dir.create(tmp)
  file.copy(file.path(rf_rlib_dir(), "sandwich"), tmp, recursive = TRUE)
  man <- file.path(tempdir(), "rlib_platform_manifest.json"); unlink(man)
  rf_record_rlib_platform(target = tmp, packages = "sandwich", manifest_path = man)
  real <- RF_PLATFORM
  assign("RF_PLATFORM", function() "some-other-r-platform", envir = globalenv())
  on.exit(assign("RF_PLATFORM", real, envir = globalenv()), add = TRUE)
  expect_true(all(rf_verify_rlib(tmp, man)$status == "ok"))        # R "runs" elsewhere; the library's own Built OS picks the recorded hashes
  # an entry for the other OS only is not this library's: unrecorded, the version still checked
  m <- jsonlite::fromJSON(man, simplifyVector = FALSE)
  b <- m$packages$sandwich$platforms[[real()]]$built
  m$packages$sandwich$platforms[[real()]]$built <- sub("; (windows|unix)$", if (grepl("; windows$", b)) "; unix" else "; windows", b)
  rf_write_json_atomic(m, man)
  v <- rf_verify_rlib(tmp, man)
  expect_identical(unique(v$status), "unrecorded_platform")
  expect_true(all(v$ok))
})

test_that("relabelling the Built OS word cannot skip the hash check when R's own platform has a recorded entry", {
  tmp <- file.path(tempdir(), "rlib_relabel"); unlink(tmp, recursive = TRUE); dir.create(tmp)
  file.copy(file.path(rf_rlib_dir(), "sandwich"), tmp, recursive = TRUE)
  man <- file.path(tempdir(), "rlib_relabel_manifest.json"); unlink(man)
  rf_record_rlib_platform(target = tmp, packages = "sandwich", manifest_path = man)
  expect_true(all(rf_verify_rlib(tmp, man)$ok))
  m <- jsonlite::fromJSON(man, simplifyVector = FALSE)
  b <- m$packages$sandwich$platforms[[RF_PLATFORM()]]$built
  m$packages$sandwich$platforms[[RF_PLATFORM()]]$built <- sub("; (windows|unix)$", if (grepl("; windows$", b)) "; unix" else "; windows", b)
  rf_write_json_atomic(m, man)
  v <- rf_verify_rlib(tmp, man)                                      # the entry now names the other OS, but it is still R's own platform's entry
  expect_identical(unique(v$status), "unrecorded_platform")
  expect_false(any(v$ok))
})

test_that("rf_rng.R is the byte copy of the SGS numpy-stream port", {
  expect_identical(rf_sha256(file.path(.RF_HOME, "R", "rf_rng.R")), RF$RNG_SHA256)
  # zlib.crc32(b"abc") == 891568578
  expect_identical(sgs_crc32("abc"), 891568578)
})

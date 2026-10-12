# The refit agent: a read-only watcher of Mentat's export receipts, a queue for unknown industries, a clean WAITING_FOR_DATA outcome for
# the property and casualty pilot, the refit run end to end (gates, packet, receipt, hold), and a Glass Box event for every step.

events_of <- function(dir) lapply(list.files(dir, pattern = "^evt-.*\\.json$", full.names = TRUE), jsonlite::fromJSON, simplifyVector = FALSE)
tree_sha <- function(d) { f <- list.files(d, recursive = TRUE, full.names = TRUE, all.files = TRUE); stats::setNames(unname(tools::sha256sum(f)), sub(paste0("^", d, "/?"), "", f)) }

make_report_dir <- function(name) {
  d <- file.path(tempdir(), name); unlink(d, recursive = TRUE); dir.create(d, recursive = TRUE)
  for (w in c("Banking Industry Spending Overview.xlsx", "Credit Union Industry Spending Overview.xlsx", "Crypto Industry Spending Overview.xlsx",
              "Property & Casualty Insurance Industry Spending Overview.xlsx", "Quantum Widgets Industry Spending Overview.xlsx",
              "~$Credit Union Industry Spending Overview.xlsx", "Rust FEC Verified Industry Spending Overview 2023-2024.xlsx", "Community Banks Industry Spending Data.xlsx"))
    writeBin(charToRaw(paste("workbook", w)), file.path(d, w))
  ad <- file.path(d, "Quantum Widgets Industry Spending Overview.audit"); dir.create(ad)
  writeLines('{"version":1,"workbook_name":"Quantum Widgets Industry Spending Overview.xlsx","workbook_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","payload_sha256":"bbbb"}', file.path(ad, "audit-manifest.json"))
  d
}

test_that("the watcher reads the report directory and its export receipts read-only and classifies every workbook", {
  rd <- make_report_dir("report_a"); before <- tree_sha(rd)
  det <- rf_agent_detect(test_ctx()$reg, rd, state_file = file.path(tempdir(), "seen_a.json"))
  expect_identical(tree_sha(rd), before)                                     # nothing in the report directory changed
  st <- stats::setNames(det$status, det$workbook)
  expect_identical(st[["Banking Industry Spending Overview.xlsx"]], "known")
  expect_identical(st[["Credit Union Industry Spending Overview.xlsx"]], "known")
  expect_identical(st[["Crypto Industry Spending Overview.xlsx"]], "declined")             # declined industries are not re-flagged on every run
  expect_identical(st[["Property & Casualty Insurance Industry Spending Overview.xlsx"]], "known")   # pilot entry exists
  expect_identical(st[["Quantum Widgets Industry Spending Overview.xlsx"]], "new")
  expect_false(any(startsWith(det$workbook, "~$")))                          # lock files are not workbooks
  expect_false(any(grepl("Rust FEC Verified", det$workbook)))
  expect_false(any(grepl("Industry Spending Data", det$workbook)))
  q <- det[det$workbook == "Quantum Widgets Industry Spending Overview.xlsx", ]
  expect_true(q$receipt); expect_identical(q$sha256, strrep("a", 64)); expect_identical(q$slug, "quantum_widgets")
})

test_that("detect mode queues a new industry once, writes its DATA_REQUEST.md and registry stub, and remembers what it saw", {
  rd <- make_report_dir("report_b"); runs <- file.path(tempdir(), "runs_detect"); unlink(runs, recursive = TRUE)
  rt <- Sys.getenv("RF_RUNTIME"); on.exit(Sys.setenv(RF_RUNTIME = rt, RF_GLASSBOX_DIR = Sys.getenv("RF_GLASSBOX_DIR")), add = TRUE)
  Sys.setenv(RF_RUNTIME = file.path(tempdir(), "rt_detect")); unlink(Sys.getenv("RF_RUNTIME"), recursive = TRUE)
  gb <- file.path(tempdir(), "gb_detect"); unlink(gb, recursive = TRUE); Sys.setenv(RF_GLASSBOX_DIR = gb)
  before <- tree_sha(rd)
  r1 <- rf_run_detect(run_id = "d1", runs_dir = runs, reg = test_ctx()$reg, report_dir = rd)
  expect_identical(r1$status, "ok")
  expect_equal(sum(r1$det$status == "new"), 1L)
  expect_true(r1$queued$created)
  q <- jsonlite::fromJSON(file.path(rf_queue_dir(), "quantum_widgets.json"), simplifyVector = FALSE)
  expect_identical(q$status, "queued"); expect_identical(q$workbook_sha256, strrep("a", 64))
  dr <- readLines(file.path(runs, "d1", "DATA_REQUEST_quantum_widgets.md"), warn = FALSE)
  expect_true(any(grepl("OpenSecrets category code", dr))); expect_true(any(grepl("IBISWorld Key Statistics", dr)))
  stub <- jsonlite::fromJSON(file.path(runs, "d1", "registry_stub_quantum_widgets.json"), simplifyVector = FALSE)
  expect_null(stub$ibisworld$code); expect_null(stub$opensecrets$totals); expect_false(stub$models$pac)
  # second run: nothing new to queue, the request is not rewritten; the watcher saw the workbooks already
  r2 <- rf_run_detect(run_id = "d2", runs_dir = runs, reg = test_ctx()$reg, report_dir = rd)
  expect_false(r2$queued$created)
  expect_false(any(r2$det$changed))
  expect_false(file.exists(file.path(runs, "d2", "DATA_REQUEST_quantum_widgets.md")))
  expect_identical(tree_sha(rd), before)
  # Glass Box: start, scan, queue, finish for each run
  ev <- events_of(gb); kinds <- vapply(ev, function(e) paste(e$kind, e$stage), "")
  expect_equal(sum(kinds == "run.start start"), 2L); expect_equal(sum(kinds == "run.finish finish"), 2L)
  expect_true(all(c("stage.start scan", "stage.finish scan", "stage.start queue", "stage.finish queue") %in% kinds))
})

test_that("an unreachable report directory is reported, not an error", {
  runs <- file.path(tempdir(), "runs_detect2")
  r <- rf_run_detect(run_id = "nodir", runs_dir = runs, reg = test_ctx()$reg, report_dir = file.path(tempdir(), "no_such_report_dir"))
  expect_identical(r$status, "no_report_directory")
})

test_that("Paul's real report directory (when reachable) holds no industry the registry does not know", {
  rd <- rf_report_dir()
  skip_if(is.na(rd) || !dir.exists(rd), "report directory not reachable on this machine")
  det <- rf_agent_detect(test_ctx()$reg, rd, state_file = file.path(tempdir(), "seen_real.json"))
  expect_gt(nrow(det), 5L)
  expect_equal(sum(det$status == "new"), 0L)
})

# Missing-data behavior is tested with deliberately absent inputs; the shipped F09 pilot is now ready.
missing_pc_registry <- function() {
  reg <- RF_TEST_REG
  pc <- reg$industries$property_casualty
  pc$opensecrets$totals <- NULL; pc$opensecrets$recipients <- NULL; pc$opensecrets$presidential <- NULL
  pc$opensecrets$reason <- "OpenSecrets F09 capture not supplied"
  reg$industries$property_casualty <- pc
  reg
}

test_that("property and casualty insurance reports 'waiting for data' with the exact missing items, and banks are ready", {
  reg <- missing_pc_registry(); inp <- test_inputs()
  inp <- inp[inp$role != "ibis_actual_current:property_casualty", ]
  pc <- rf_agent_data_status(reg, "property_casualty", inp)
  expect_false(pc$ready)
  items <- vapply(pc$missing, function(m) m$item, "")
  expect_true(all(c("OpenSecrets totals", "OpenSecrets recipients and presidential lists", "IBISWorld actual revenue") %in% items))
  d <- stats::setNames(vapply(pc$missing, function(m) m$detail, ""), items)
  expect_identical(d[["OpenSecrets totals"]], "OpenSecrets F09 capture not supplied")
  expect_match(d[["IBISWorld actual revenue"]], "revenue ends before the 2024 cycle \\(latest cycle with growth: 2022\\)")
  expect_true(pc$models$lobbying); expect_false(pc$ready)
  for (id in c("commercial_banks", "credit_unions", "auto_light_duty_manufacturing")) expect_true(rf_agent_data_status(reg, id, inp)$ready, info = id)
})

test_that("a refit requested for property and casualty ends WAITING_FOR_DATA cleanly: request file, no bundle, no publish, success event", {
  runs <- file.path(tempdir(), "runs_wait"); unlink(runs, recursive = TRUE)
  gb <- file.path(tempdir(), "gb_wait"); unlink(gb, recursive = TRUE)
  old <- Sys.getenv("RF_GLASSBOX_DIR"); on.exit(Sys.setenv(RF_GLASSBOX_DIR = old), add = TRUE); Sys.setenv(RF_GLASSBOX_DIR = gb)
  pdir <- file.path(tempdir(), "pub_wait"); dir.create(pdir, showWarnings = FALSE)
  r <- rf_run_refit(industry = "property_casualty", publish = "auto", run_id = "w1", runs_dir = runs, run_tests = FALSE, publish_dir = pdir, reg = missing_pc_registry())
  expect_identical(r$decision, "WAITING_FOR_DATA")
  d <- file.path(runs, "w1")
  expect_true(file.exists(file.path(d, "DATA_REQUEST.md"))); expect_true(file.exists(file.path(d, "waiting.json")))
  expect_false(dir.exists(file.path(d, "bundle"))); expect_false(file.exists(file.path(d, "receipt.json")))
  expect_length(list.files(pdir), 0L)
  req <- readLines(file.path(d, "DATA_REQUEST.md"), warn = FALSE)
  expect_true(any(grepl("OpenSecrets F09 capture not supplied", req, fixed = TRUE)))
  ev <- events_of(gb)
  fin <- Filter(function(e) e$kind == "run.finish", ev)
  expect_length(fin, 1L); expect_identical(fin[[1]]$status, "succeeded"); expect_match(fin[[1]]$summary, "waiting for data: property_casualty")
  expect_false(any(vapply(ev, function(e) e$status, "") == "failed"))
})

test_that("a refit run on the real inputs: gates, review packet, receipt with platform, Glass Box per step, and a HOLD that publishes nothing", {
  runs <- file.path(tempdir(), "runs_refit"); unlink(runs, recursive = TRUE)
  gb <- file.path(tempdir(), "gb_refit"); unlink(gb, recursive = TRUE)
  old <- Sys.getenv("RF_GLASSBOX_DIR"); on.exit(Sys.setenv(RF_GLASSBOX_DIR = old), add = TRUE); Sys.setenv(RF_GLASSBOX_DIR = gb)
  pdir <- file.path(tempdir(), "pub_refit"); unlink(pdir, recursive = TRUE); dir.create(pdir)
  file.copy(rf_roots()$BUNDLE_V1, file.path(pdir, "giving-models-v1.json"))
  v1_hash <- rf_sha256(file.path(pdir, "giving-models-v1.json"))
  # A repeat refit must name the pointer-selected v2 predecessor, not the frozen v1.
  previous <- test_bundle2()
  previous_hash <- rf_write_json_atomic(previous, file.path(pdir, "giving-models-v2.json"))
  .rf_write_pointer(list(schema_version = 1L, active = "giving-models-v2.json", sha256 = previous_hash,
                         model_version = previous$model_version, model_schema_version = 2L), pdir)
  pointer_hash <- rf_sha256(rf_pointer_path(pdir))
  before_files <- list.files(pdir)
  r <- rf_run_refit(publish = "auto", run_id = "r1", runs_dir = runs, run_tests = FALSE, b_random = 999L, publish_dir = pdir, consumer_cmd = "")
  expect_identical(r$decision, "HOLD")                                         # Deliberately absent G8 consumer command holds publication; all other gates remain enforced.
  expect_identical(r$published$status, "not_published")
  expect_identical(list.files(pdir), before_files); expect_identical(rf_sha256(file.path(pdir, "giving-models-v1.json")), v1_hash)
  expect_identical(rf_sha256(file.path(pdir, "giving-models-v2.json")), previous_hash)
  expect_identical(rf_sha256(rf_pointer_path(pdir)), pointer_hash)
  d <- file.path(runs, "r1")
  expect_true(all(c("bundle/giving-models-v2.json", "receipt.json", "log.txt", "review/REVIEW.md", "review/regressions.txt", "review/backtests.csv", "review/rule_selection.csv",
                    "review/dl_slopes.csv", "review/robustness_pvalues.csv", "review/bundle_diff.csv", "review/gates.json", "review/inputs.csv", "review/g6_nondegradation.csv") %in% list.files(d, recursive = TRUE)))
  # the bundle on disk is schema-valid and carries its own gate summary
  b <- jsonlite::fromJSON(file.path(d, "bundle", "giving-models-v2.json"), simplifyVector = FALSE)
  expect_identical(rf_validate_parsed(b), character(0))
  expect_identical(b$gates$decision, "HOLD")
  expect_identical(b$previous_model_version, previous$model_version)
  expect_true(any(vapply(b$gates$checks, function(c) c$gate == "G8 consumer smoke" && c$status == "HOLD", logical(1))))
  # receipt: platform statement, gates, rule selection, hashes
  rc <- jsonlite::fromJSON(file.path(d, "receipt.json"), simplifyVector = FALSE)
  expect_identical(rc$decision, "HOLD"); expect_identical(rc$mode, "refit")
  expect_true(all(c("R_version", "blas", "lapack", "platform", "cores") %in% names(rc$platform)))
  expect_identical(vapply(rc$rule_selection, function(s) s$reported_rule, ""), c(pac = "R1", control_House = "R0", control_Senate = "R0", lobby = "R0"))
  expect_identical(rc$bundle_sha256, rf_sha256(file.path(d, "bundle", "giving-models-v2.json")))
  expect_length(rc$waiting_for_data, 0L)   # F09 captures and the 2024 estimate make the pilot ready
  # the packet: decision, gates, rule selection, slopes, inputs, platform; real summary(lm()) blocks incl. the lobbying log refit
  md <- readLines(file.path(d, "review", "REVIEW.md"), warn = FALSE)
  expect_true(any(grepl("## Decision: HOLD", md, fixed = TRUE))); expect_true(any(grepl("Rule selection", md))); expect_true(any(grepl("## Platform", md)))
  expect_false(any(grepl("OpenSecrets F09 capture not supplied", md, fixed = TRUE)))
  expect_true(r$bundle$industries$property_casualty$capabilities$pac)
  expect_true(r$bundle$industries$property_casualty$capabilities$control)
  expect_length(r$bundle$industries$property_casualty$caveats, 2L)
  expect_true(any(grepl("current-year estimate", unlist(r$bundle$industries$property_casualty$caveats), fixed = TRUE)))
  txt <- readLines(file.path(d, "review", "regressions.txt"), warn = FALSE)
  expect_true(any(grepl("MODEL lobby_log", txt, fixed = TRUE))); expect_true(any(grepl("data = lobbying_log_all71", txt, fixed = TRUE)))
  expect_gt(sum(grepl("^Coefficients:", txt)), 10L)
  # Glass Box: a start, a stage.start/finish for each stage, a gate.result per gate, one finish; nothing failed
  ev <- events_of(gb); kinds <- vapply(ev, function(e) e$kind, ""); stages <- vapply(ev[kinds == "stage.start"], function(e) e$stage, "")
  expect_true(all(c("environment", "inputs", "panels", "models", "parity", "gates", "packet", "publish") %in% stages))
  expect_equal(sum(kinds == "run.start"), 1L); expect_equal(sum(kinds == "run.finish"), 1L)
  expect_gte(sum(kinds == "gate.result"), 10L)
  expect_true(any(kinds == "publish.hold"))
  expect_false(any(vapply(ev[kinds == "run.finish"], function(e) e$status, "") == "failed"))
})

test_that("the agent's refit run and its gates are tolerance-based against another platform's bundle (BLAS noise does not change a decision)", {
  b <- test_bundle2(); noisy <- b
  noisy$pac_model$slopes$commercial_banks$applied$beta <- b$pac_model$slopes$commercial_banks$applied$beta * (1 + 3e-14)
  expect_equal(nrow(rf_bundle_diff(rf_canonical_json(b), rf_canonical_json(noisy))), 0L)
  g <- rf_gate_nondegradation(test_v2(), noisy, cycle = FALSE)       # published baseline read from a bundle whose evidence differs at the 1e-14 level
  expect_identical(g$gate$status, "PASS")
})

test_that("run_refit.R exposes detect, refit and rollback and refuses unknown modes", {
  src <- readLines(file.path(.RF_HOME, "run_refit.R"), warn = FALSE)
  for (pat in c("--mode detect", "--mode refit", "--rollback", "--publish auto|never", "unknown mode")) expect_true(any(grepl(pat, src, fixed = TRUE)), info = pat)
})

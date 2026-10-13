# Container path seams (D-173..D-182): every location the refit reads or writes keeps today's Windows value by default,
# and each environment variable moves what it names. Nothing here touches the inputs, the network or the runtime folder.

rf24_with_env <- function(vars, code) {
  old <- Sys.getenv(names(vars), unset = NA, names = TRUE)
  on.exit(for (k in names(old)) if (is.na(old[[k]])) Sys.unsetenv(k) else do.call(Sys.setenv, stats::setNames(list(old[[k]]), k)), add = TRUE)
  for (k in names(vars)) if (is.na(vars[[k]])) Sys.unsetenv(k) else do.call(Sys.setenv, stats::setNames(list(vars[[k]]), k))
  force(code)
}
RF24_ALIASES <- c("LOO", "CHE", "SGSD", "SGSA", "ZIPC", "RLIBX", "INGEST", "RFDATA", "BUNDLE_V1", "CYCLE")
rf24_unset_roots <- function() stats::setNames(rep(list(NA_character_), length(RF24_ALIASES) + 1L), c("RF_INPUT_ROOT", paste0("RF_ROOT_", RF24_ALIASES)))

test_that("the pinned input roots and the Glass Box folder keep their Windows values when nothing is set", {
  skip_if_not(.Platform$OS.type == "windows" && identical(normalizePath(Sys.getenv("LOCALAPPDATA"), winslash = "/"), "C:/Users/pgoff/AppData/Local"))
  rf24_with_env(c(rf24_unset_roots(), RF_GLASSBOX_DIR = NA), {
    r <- rf_roots()
    expect_identical(r$LOO, "C:/Users/pgoff/Documents/Codex/2026-09-26/loo")
    expect_identical(r$CHE, "C:/Users/pgoff/Documents/Codex/2026-09-27/che")
    expect_identical(r$SGSD, "C:/Users/pgoff/Mentat/sector_growth_study/data")
    expect_identical(r$SGSA, "C:/Users/pgoff/Mentat/sector_growth_study/archive/session_d613b40c_scratchpad")
    expect_identical(r$ZIPC, "C:/Users/pgoff/Downloads/House_Senate_Control_Forecasts_CSV.zip")
    expect_identical(r$RLIBX, "C:/Users/pgoff/Documents/Codex/2026-09-26/loo/work/lobbying_followup/Rlib")
    expect_identical(r$INGEST, "C:/Users/pgoff/AppData/Local/Mentat/forecast/ingest/runs")
    expect_identical(r$RFDATA, "C:/Users/pgoff/AppData/Local/Mentat/forecast/refit/data")
    expect_identical(rf_glassbox_dir(), "C:/Users/pgoff/.claude/glassbox/events")
  })
})

test_that("LOCALAPPDATA moves the ingest and refit-data roots with the runtime default (a container sets it once)", {
  lad <- file.path(tempdir(), "rf24_localappdata"); dir.create(lad, showWarnings = FALSE)
  rf24_with_env(c(rf24_unset_roots(), LOCALAPPDATA = lad, MENTAT_DATA_ROOT = NA, RF_RUNTIME = NA), {
    base <- file.path(normalizePath(lad, winslash = "/"), "Mentat")
    expect_identical(rf_localappdata_mentat(), base)
    expect_identical(rf_roots()$INGEST, file.path(base, "forecast/ingest/runs"))
    expect_identical(rf_roots()$RFDATA, file.path(base, "forecast/refit/data"))
    expect_identical(rf_runtime(), file.path(base, "forecast/refit"))
  })
  rf24_with_env(c(rf24_unset_roots(), LOCALAPPDATA = ""), expect_identical(rf_localappdata_mentat(), "C:/Users/pgoff/AppData/Local/Mentat"))
})

test_that("RF_ROOT_<ALIAS> and RF_INPUT_ROOT move every root, the alias winning over the umbrella", {
  rf24_with_env(rf24_unset_roots(), {
    for (a in setdiff(RF24_ALIASES, c("BUNDLE_V1", "CYCLE"))) {
      v <- stats::setNames(list(file.path("/mount", a)), paste0("RF_ROOT_", a))
      rf24_with_env(v, expect_identical(rf_roots()[[a]], file.path("/mount", a)))
    }
    rf24_with_env(c(RF_INPUT_ROOT = "/inputs"), {
      r <- rf_roots()
      expect_identical(r$INGEST, "/inputs/INGEST")
      expect_identical(r$RFDATA, "/inputs/RFDATA")
      expect_identical(r$SGSD, "/inputs/SGSD")
      rf24_with_env(c(RF_ROOT_INGEST = "/elsewhere"), expect_identical(rf_roots()$INGEST, "/elsewhere"))
    })
  })
})

test_that("RF_GLASSBOX_DIR, RF_RUNTIME, RF_RLIB, REFIT_OUT, RF_REPORT_DIR and RF_PUBLISH_DIR move what they name", {
  rf24_with_env(c(RF_GLASSBOX_DIR = "/glassbox/events"), expect_identical(rf_glassbox_dir(), "/glassbox/events"))
  d <- normalizePath(tempdir(), winslash = "/")
  rf24_with_env(c(RF_RUNTIME = d, RF_RLIB = NA, REFIT_OUT = NA), {
    expect_identical(rf_runtime(), d)
    expect_identical(rf_rlib_dir(), file.path(d, "Rlib"))
    expect_identical(rf_runs_dir(), file.path(d, "runs"))
  })
  sub <- file.path(d, "rf24_out"); dir.create(sub, showWarnings = FALSE)
  rf24_with_env(c(RF_RUNTIME = d, RF_RLIB = file.path(d, "elsewhere"), REFIT_OUT = sub), {
    expect_identical(rf_rlib_dir(), file.path(d, "elsewhere"))
    expect_identical(rf_runs_dir(), file.path(sub, "runs"))
  })
  rf24_with_env(c(RF_REPORT_DIR = "/reports"), expect_identical(rf_report_dir(), "/reports"))
  rf24_with_env(c(RF_PUBLISH_DIR = "/publish"), expect_identical(rf_publish_dir(), "/publish"))
  rf24_with_env(c(RF_PUBLISH_DIR = NA), expect_identical(rf_publish_dir(), file.path(dirname(dirname(rf_home())), "app", "data")))
})

test_that("rf_write_csv writes CRLF explicitly, so detect.csv and the review CSVs are the same bytes on every platform (compute parity, D-177)", {
  p <- file.path(tempdir(), "rf24_csv", "x.csv")
  rf_write_csv(data.frame(a = c(1.5, NA), b = c("x", "é \"q\""), stringsAsFactors = FALSE), p)
  expect_identical(readBin(p, "raw", file.size(p)), charToRaw(enc2utf8("\"a\",\"b\"\r\n1.5,\"x\"\r\nNA,\"é \"\"q\"\"\"\r\n")))
  rf_write_csv(data.frame(a = "l1\nl2", stringsAsFactors = FALSE), p)                          # a newline inside a field is translated too, as Windows text mode does
  expect_identical(readBin(p, "raw", file.size(p)), charToRaw("\"a\"\r\n\"l1\r\nl2\"\r\n"))
  rf_write_csv(data.frame(a = character()), p)
  expect_identical(readBin(p, "raw", file.size(p)), charToRaw("\"a\"\r\n"))
})

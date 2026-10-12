script_argument <- grep("^--file=", commandArgs(trailingOnly = FALSE), value = TRUE)
stopifnot(length(script_argument) == 1L)
script_path <- normalizePath(
  sub("^--file=", "", script_argument[[1L]]),
  winslash = "/",
  mustWork = TRUE
)
project_root <- normalizePath(
  file.path(dirname(script_path), ".."),
  winslash = "/",
  mustWork = TRUE
)
for (file in c("paths.R", "schema.R", "ingest.R", "reconcile.R")) {
  source(file.path(project_root, "R", file), local = .GlobalEnv)
}

data_root <- tempfile("mentat-test-reconcile-")
dir.create(data_root, recursive = TRUE)
on.exit({
  if (dir.exists(data_root)) {
    unlink(data_root, recursive = TRUE, force = TRUE)
  }
}, add = TRUE)

fixture_full <- read_crypto_fixture(
  file.path(project_root, "inputs", "Credit Union Cross Industry Spending.xlsx")
)
fixture_full[, selection_name := normalize_reconcile_text(contributor_name)]
unique_names <- fixture_full[, .N, by = selection_name][N == 1L, selection_name]
fixture <- fixture_full[
  selection_name %in% unique_names &
    nchar(normalize_reconcile_text(recipient_name)) >= 30L
][1:8]
fixture[, selection_name := NULL]
stopifnot(nrow(fixture) == 8L)

pipeline <- fixture[, .(
  sub_id = paste0("fixture-derived-", seq_len(.N)),
  contributor_name,
  committee_name = recipient_name,
  contribution_receipt_date = format(receipt_date, "%Y-%m-%d"),
  contribution_receipt_amount = amount,
  amendment_indicator = "",
  possible_amendment = 0L,
  is_memo_leg = 0L,
  is_conduit_leg = 0L,
  is_passthrough_leg = 0L
)]
pipeline[2L, committee_name := paste0(
  substr(committee_name, 1L, 25L),
  " PIPELINE DISPLAY LABEL"
)]
pipeline[3L, committee_name := "A DIFFERENT RECIPIENT DISPLAY"]
pipeline[4L, contribution_receipt_date := format(
  as.Date(contribution_receipt_date) + 2L,
  "%Y-%m-%d"
)]
pipeline[5L, contribution_receipt_amount := contribution_receipt_amount + 0.01]

noise <- data.table::data.table(
  sub_id = c("noise-post-cutoff", "noise-amendment", "noise-memo"),
  contributor_name = c("NOISE, POST", "NOISE, AMENDMENT", "NOISE, MEMO"),
  committee_name = c("NOISE COMMITTEE A", "NOISE COMMITTEE B", "NOISE COMMITTEE C"),
  contribution_receipt_date = c("2026-06-10", "2026-04-01", "2026-03-01"),
  contribution_receipt_amount = c(11, 12, 13),
  amendment_indicator = c("", "A", ""),
  possible_amendment = c(0L, 1L, 0L),
  is_memo_leg = c(0L, 0L, 1L),
  is_conduit_leg = c(0L, 0L, 0L),
  is_passthrough_leg = c(0L, 0L, 0L)
)
pipeline <- data.table::rbindlist(list(pipeline, noise), use.names = TRUE)

result <- reconcile_tables(fixture, pipeline)
expected_stages <- data.table::data.table(
  stage = c("S1", "S2", "S3", "S4"),
  matches = c(4L, 1L, 1L, 1L)
)
stopifnot(
  identical(result$stage_counts, expected_stages),
  result$total_fixture == 8L,
  result$total_pipeline == 11L,
  result$total_matches == 7L,
  result$match_percent == 87.5,
  result$covered_fixture == 8L,
  result$coverage_percent == 100,
  result$invalid_key_rows == 0L,
  nrow(result$fixture_unmatched) == 1L,
  result$fixture_unmatched$failed_components[[1L]] == "amount",
  result$fixture_unmatched$residual_status[[1L]] == "present_in_database",
  nrow(result$pipeline_only) == 4L,
  setequal(
    result$pipeline_only$probable_cause,
    c("unexplained", "post_fixture_cutoff", "memo_leg")
  ),
  result$duplicate_counts$fixture_duplicate_tuples == 0L,
  result$duplicate_counts$pipeline_duplicate_tuples == 0L
)

invalid_rows <- data.table::data.table(
  sub_id = paste0("invalid-key-", 1:4),
  contributor_name = c("", "VALID, NAME", "VALID, NAME", "VALID, NAME"),
  committee_name = c("VALID COMMITTEE", "", "VALID COMMITTEE", "VALID COMMITTEE"),
  contribution_receipt_date = c(
    "2026-01-01", "2026-01-01", "", "2026-01-01"
  ),
  contribution_receipt_amount = c(10, 10, 10, NA_real_),
  amendment_indicator = "",
  possible_amendment = 0L,
  is_memo_leg = 0L,
  is_conduit_leg = 0L,
  is_passthrough_leg = 0L
)
invalid_result <- reconcile_tables(
  fixture,
  data.table::rbindlist(list(pipeline, invalid_rows), use.names = TRUE, fill = TRUE)
)
stopifnot(
  invalid_result$total_pipeline == 15L,
  invalid_result$total_matches == 7L,
  invalid_result$invalid_key_rows == 4L,
  invalid_result$pipeline_only[probable_cause == "invalid_key", .N] == 4L,
  any(grepl(
    "Invalid pipeline key rows excluded from matching: 4",
    accuracy_report_lines(invalid_result),
    fixed = TRUE
  ))
)

paths <- write_accuracy_outputs(result, data_root)
stopifnot(
  all(file.exists(unlist(paths, use.names = FALSE))),
  nrow(data.table::fread(paths$fixture_unmatched)) == 1L,
  nrow(data.table::fread(paths$pipeline_only)) == 4L
)
report <- paste(readLines(paths$accuracy_report, encoding = "UTF-8"), collapse = "\n")
stopifnot(
  grepl("- S1 exact: 4", report, fixed = TRUE),
  grepl("- S2 recipient prefix-25: 1", report, fixed = TRUE),
  grepl("- S3 name/date/amount: 1", report, fixed = TRUE),
  grepl("- S4 name/amount within +/-3 days: 1", report, fixed = TRUE),
  grepl("- Strict one-to-one match rate: 87.50%", report, fixed = TRUE),
  grepl("- Database coverage: 8 / 8 (100.00%)", report, fixed = TRUE),
  grepl("### Named residual rows", report, fixed = TRUE),
  grepl("present_in_database", report, fixed = TRUE)
)

empty_result <- reconcile_tables(fixture, pipeline[0])
stopifnot(
  empty_result$total_matches == 0L,
  empty_result$empty_pipeline,
  any(grepl(
    "Pipeline contains no contribution rows",
    accuracy_report_lines(empty_result),
    fixed = TRUE
  ))
)

cat(
  "test_reconcile: S1=4 S2=1 S3=1 S4=1 unmatched=1 pipeline_only=4\n",
  "test_reconcile: empty-pipeline degradation verified\n",
  "test_reconcile: PASS\n",
  sep = ""
)

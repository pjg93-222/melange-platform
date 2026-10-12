suppressPackageStartupMessages(library(data.table))
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
source(file.path(project_root, "R", "export_guards.R"), local = .GlobalEnv)
p <- data.table(company = "Fixture", cycle = 2024L,
  variant_set = c("P1_employer:legacy", "P1_employer:current"),
  records_total = c(100, 50), zero_verified = c(0L, 1L),
  updated_at = c("2026-07-01T12:00:00.000-0400", "2026-09-08T12:00:00.000-0400"))
a <- data.table(company = "Fixture", cycle = 2024L, unit_id = "P1_employer:current",
  kind = "employer", rows_seen = 50, records_total = 50, fetched_complete = 1L,
  stored_complete = 1L, updated_at = "2026-09-08T12:00:01.000-0400")
v <- mentat_guard_progress_views(p, a)
stopifnot(nrow(v$completeness) == 0L, nrow(v$zero) == 1L, v$api_guard$passed)

# A partial or unstamped search must never clear the previous warning.
a[, stored_complete := 0L]
v <- mentat_guard_progress_views(p, a)
stopifnot(nrow(v$completeness) == 2L, !v$api_guard$passed)
a[, stored_complete := 1L]
p[, zero_verified := 0L]
stopifnot(nrow(mentat_guard_progress_views(p, a)$completeness) == 2L)
p[variant_set == "P1_employer:current", zero_verified := 1L]
a[, rows_seen := 49]
stopifnot(!mentat_guard_progress_views(p, a)$api_guard$passed)
a[, rows_seen := 50]
p[variant_set == "P1_employer:legacy", updated_at := "2026-09-09T12:00:00.000-0400"]
stopifnot(nrow(mentat_guard_progress_views(p, a)$completeness) == 2L)

# A current verified zero supersedes historical unverified zeros.
p[, records_total := 0]
p[variant_set == "P1_employer:legacy", updated_at := "2026-07-01T12:00:00.000-0400"]
a[, `:=`(records_total = 0, rows_seen = 0)]
stopifnot(mentat_guard_zero_provenance(mentat_guard_progress_views(p, a)$zero)$passed)
# Completed window fetches are covered by their stored parent union.
a[, `:=`(records_total = 50, rows_seen = 50)]
p[variant_set == "P1_employer:current", records_total := 50]
children <- a[rep(1L, 2L)]
children[, `:=`(unit_id = paste0(unit_id, "::window::", 1:2),
  rows_seen = c(20, 30), records_total = c(20, 30), stored_complete = 0L)]
window_api <- rbind(a, children)
v <- mentat_guard_progress_views(p, window_api)
stopifnot(v$api_guard$passed, nrow(v$zero) == 1L)
for (damage in c("partial", "mismatch", "orphan", "unstored")) {
  broken <- copy(window_api)
  if (damage == "partial") broken[2L, fetched_complete := 0L]
  if (damage == "mismatch") broken[2L, rows_seen := 19]
  if (damage == "orphan") broken <- broken[-1L]
  if (damage == "unstored") broken[1L, stored_complete := 0L]
  stopifnot(!mentat_guard_progress_views(p, broken)$api_guard$passed)
}
# G8: the detail itemizes EVERY short cell, "<Company>/<year> <destination> <held> of <reported>",
# largest gap first, so the console (collection_gaps.py) can name them all instead of "... and N more".
reported <- data.table(company = sprintf("Co%02d", 1:15), cycle = 2024L, destination = "contributions",
  records_total = 1000 + 10 * (1:15))
held <- data.table(company = reported$company, cycle = 2024L, destination = "contributions", rows = 500)
extra <- data.table(company = c("Whole", "ShortCorp"), cycle = 2026L, destination = c("contributions", "corporate"),
  records_total = c(1000, 4365))
reported <- rbind(reported, extra)
held <- rbind(held, data.table(company = c("Whole", "ShortCorp"), cycle = 2026L,
  destination = c("contributions", "corporate"), rows = c(990, 1369)))
g8 <- mentat_guard_collection_completeness(reported, held)
cells <- strsplit(sub("^.*recollect before trusting it: ", "", g8$detail), "; ", fixed = TRUE)[[1L]]
stopifnot(!g8$passed, !grepl("more", g8$detail, fixed = TRUE), length(cells) == 16L,
  identical(cells[[1L]], "ShortCorp/2026 corporate 1369 of 4365"),
  identical(cells[[2L]], "Co15/2024 contributions 500 of 1150"),
  identical(cells[[16L]], "Co01/2024 contributions 500 of 1010"),
  !any(grepl("Whole", cells, fixed = TRUE)),
  g8$metric == (4365 - 1369) + sum(1000 + 10 * (1:15) - 500))
# Few cells read as before, and a complete collection still passes.
few <- mentat_guard_collection_completeness(reported[company %in% c("Co01", "Whole")], held)
stopifnot(!few$passed, grepl("recollect before trusting it: Co01/2024 contributions 500 of 1010$", few$detail))
done <- mentat_guard_collection_completeness(reported[company == "Whole"], held)
stopifnot(done$passed, done$detail == "1 company x cycle walks stored what FEC reported")
cat("test_export_progress_guards: PASS\n")

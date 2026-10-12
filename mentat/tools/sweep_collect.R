# Re-collect every company in a job list, in one process.
#
# This replaces a bash driver that could not be trusted. That wrapper counted
# rows through a subprocess whose failure path printed 0, so a database held open
# by the collector reported "0 rows" - the same silent-plausible-zero defect this
# whole effort exists to remove. It also ran the collector inside a
# `while read ... done < file` loop, and Rscript inherits and consumes stdin, so
# the collector ate its own job list and entries were skipped.
#
# One R process, one loop, contribution and corporate counts taken on the same
# connection that just wrote: nothing to misreport and no destination omitted.
#
# Usage:
#   Rscript tools/sweep_collect.R <jobs.tsv> [report.tsv]
# where jobs.tsv is: <database>\t<industry>\t<company>

args <- commandArgs(trailingOnly = TRUE)
if (!length(args)) stop("Usage: Rscript tools/sweep_collect.R <jobs.tsv> [report.tsv]", call. = FALSE)
jobs_path <- args[[1L]]
report_path <- if (length(args) >= 2L) args[[2L]] else "sweep_report.tsv"

script_dir <- dirname(
  sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[[1L]])
)
project_root <- dirname(script_dir)
for (module in c(
  "paths.R", "schema.R", "ingest.R", "identity_components.R",
  "fec_page.R", "fec_api.R", "collect.R"
)) {
  source(file.path(project_root, "R", module))
}
require_package("DBI")
require_package("RSQLite")

jobs <- utils::read.delim(
  jobs_path, header = FALSE, sep = "\t", quote = "",
  col.names = c("database", "industry", "company"),
  stringsAsFactors = FALSE, colClasses = "character"
)
cat(sprintf("sweep: %d jobs\n", nrow(jobs)))

open_db <- function(path) {
  connection <- DBI::dbConnect(RSQLite::SQLite(), dbname = path)
  DBI::dbExecute(connection, "PRAGMA busy_timeout = 120000")
  DBI::dbExecute(connection, "PRAGMA journal_mode = WAL")
  connection
}

results <- vector("list", nrow(jobs))
for (index in seq_len(nrow(jobs))) {
  job <- jobs[index, ]
  started <- Sys.time()
  connection <- open_db(job$database)
  assign("MENTAT_INDUSTRY", job$industry, envir = .GlobalEnv)
  init_schema(connection)
  before <- collection_company_store_counts(connection, job$company)
  status <- "ok"
  message_text <- ""
  outcome <- tryCatch(
    {
      collect_crypto(connection, company_filter = job$company)
      "ok"
    },
    error = function(e) {
      message_text <<- conditionMessage(e)
      "error"
    }
  )
  after <- collection_company_store_counts(connection, job$company)
  DBI::dbDisconnect(connection)
  elapsed <- round(as.numeric(difftime(Sys.time(), started, units = "secs")), 1)
  gained <- after - before
  status <- collection_receipt_status(outcome, gained)
  line <- sprintf(
    paste0(
      "[%s] %-30s %-32s %-9s before=%d after=%d gained=%d ",
      "(contributions=%+d corporate=%+d) %.1fs %s"
    ),
    format(Sys.time(), "%H:%M:%S"), job$industry, job$company, status,
    before[["total"]], after[["total"]], gained[["total"]],
    gained[["contributions"]], gained[["corporate_giving"]], elapsed,
    substr(message_text, 1, 90)
  )
  cat(line, "\n", sep = "")
  flush.console()
  results[[index]] <- data.frame(
    industry = job$industry, company = job$company, status = status,
    before = before[["total"]], after = after[["total"]],
    gained = gained[["total"]],
    contributions_before = before[["contributions"]],
    contributions_after = after[["contributions"]],
    contributions_gained = gained[["contributions"]],
    corporate_before = before[["corporate_giving"]],
    corporate_after = after[["corporate_giving"]],
    corporate_gained = gained[["corporate_giving"]],
    seconds = elapsed,
    error = substr(message_text, 1, 200), stringsAsFactors = FALSE
  )
  utils::write.table(
    do.call(rbind, results[seq_len(index)]), report_path,
    sep = "\t", row.names = FALSE, quote = FALSE
  )
}
report <- do.call(rbind, results)
cat(sprintf(
  "\nsweep complete: %d jobs, %d gained rows, %d errors, %d rows recovered\n",
  nrow(report), sum(report$gained > 0), sum(report$status == "ERROR"),
  sum(report$gained)
))
if (any(report$status == "ERROR")) {
  quit(save = "no", status = 1L, runLast = FALSE)
}

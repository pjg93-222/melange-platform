# File and format helpers: hashing, workbook / zip / JSON readers, the atomic JSON writer, and small utilities.
# Nothing here opens a network connection; inputs are read-only and outputs go where the caller says.

rf_sha256 <- function(path) {
  out <- unname(tools::sha256sum(path))
  if (any(is.na(out))) stop("rf_sha256: cannot read ", path[is.na(out)][1], call. = FALSE)
  out
}

rf_read_xlsx <- function(path, sheet) {
  if (!requireNamespace("readxl", quietly = TRUE)) stop("Required installed package is unavailable: readxl", call. = FALSE)
  as.data.frame(readxl::read_excel(path, sheet = sheet), stringsAsFactors = FALSE)
}

rf_read_json <- function(path) jsonlite::fromJSON(path, simplifyVector = FALSE)

# A CSV member of a zip archive, read as text (deduplication is the caller's business).
rf_read_zip_csv <- function(zip_path, member) {
  utils::read.csv(unz(zip_path, member), stringsAsFactors = FALSE)   # read.csv opens and closes the connection itself
}

rf_read_zip_lines <- function(zip_path, member) {
  con <- unz(zip_path, member)
  on.exit(close(con))
  readLines(con, warn = FALSE, encoding = "UTF-8")   # readLines leaves an unopened connection allocated, so it is closed here
}

rf_now_utc <- function() format(Sys.time(), "%Y-%m-%dT%H:%M:%SZ", tz = "UTC")

rf_fmt_num <- function(x, digits = 6) formatC(x, digits = digits, format = "g")

# One row of a named-check table (name, rows examined, failures, detail); builders attach these as attr(x, "checks").
rf_check <- function(name, rows, failures, detail = "") {
  data.frame(name = name, rows_examined = as.integer(rows), failures = as.integer(failures), detail = detail,
             stringsAsFactors = FALSE)
}

rf_checks_bind <- function(...) {
  parts <- Filter(Negate(is.null), list(...))
  if (!length(parts)) return(rf_check("none", 0L, 0L)[0, ])
  do.call(rbind, parts)
}

# Every value of a nested list / vector is finite (or NULL / NA in a string); returns the paths of offenders.
rf_nonfinite_paths <- function(x, path = "$") {
  if (is.null(x)) return(character())
  if (is.list(x)) {
    nm <- names(x)
    out <- character()
    for (i in seq_along(x)) {
      key <- if (!is.null(nm) && nzchar(nm[i])) paste0(path, ".", nm[i]) else paste0(path, "[", i - 1L, "]")
      out <- c(out, rf_nonfinite_paths(x[[i]], key))
    }
    return(out)
  }
  if (is.numeric(x)) {
    bad <- which(is.nan(x) | is.infinite(x))   # NA is written as null; NaN and Inf are forbidden
    if (length(bad)) return(paste0(path, if (length(x) > 1L) paste0("[", bad - 1L, "]") else ""))
  }
  character()
}

# UTF-8 without BOM, temporary file in the target folder then rename (retried: a reader may hold the target briefly).
# NaN / Inf are refused; NA becomes null. Returns the sha256 of the written file.
rf_write_json_atomic <- function(x, path, pretty = TRUE, attempts = 40L) {
  bad <- rf_nonfinite_paths(x)
  if (length(bad)) stop("rf_write_json_atomic: non-finite numbers at ", paste(utils::head(bad, 5), collapse = ", "), call. = FALSE)
  txt <- as.character(jsonlite::toJSON(x, auto_unbox = TRUE, digits = NA, na = "null", null = "null", pretty = pretty))
  rf_write_text_atomic(paste0(txt, "\n"), path, attempts)
}

rf_write_text_atomic <- function(txt, path, attempts = 40L) {
  dir.create(dirname(path), recursive = TRUE, showWarnings = FALSE)
  tmp <- paste0(path, ".", paste(sample(c(0:9, letters[1:6]), 16, TRUE), collapse = ""), ".tmp")
  con <- file(tmp, open = "wb")
  writeBin(charToRaw(enc2utf8(txt)), con)
  close(con)
  for (k in seq_len(attempts)) {
    if (suppressWarnings(file.rename(tmp, path))) return(rf_sha256(path))
    Sys.sleep(stats::runif(1, 0.05, 0.25))
  }
  unlink(tmp)
  stop("rf_write_text_atomic: could not replace ", path, call. = FALSE)
}

# Text -> file with LF line endings (used for the report files).
rf_write_lines <- function(lines, path) rf_write_text_atomic(paste0(paste(lines, collapse = "\n"), "\n"), path)

# A condition with a class, like the SGS port's PanelError / LoadError.
rf_condition <- function(class, msg) structure(class = c(class, "error", "condition"), list(message = msg, call = NULL))
rf_raise <- function(class, msg) stop(rf_condition(class, msg))

# Money text of OpenSecrets tables: "$1,234", "($5)" is not used there, U+2212 is a minus sign.
rf_money_int <- function(s) as.numeric(gsub("[$, ]", "", gsub("−", "-", s)))

# rbind of data frames with different columns: missing columns are filled with NA (the union keeps first-seen order).
rf_rbind_fill <- function(parts) {
  cols <- unique(unlist(lapply(parts, names)))
  do.call(rbind, lapply(parts, function(x) { x[setdiff(cols, names(x))] <- NA; x[cols] }))
}

# Sort key of a set of sector ids: alphabetical, reference level first (Python sorted()).
rf_sorted <- function(x) sort(unique(x), method = "radix")

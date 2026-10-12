# File helpers of the ingest. The refit's rf_io.R (sha256, atomic JSON / text writer, money parser) and rf_inputs.R (the
# content-addressed vault) are sourced by fi_source_all(); nothing is duplicated here except what the refit does not have:
# a string hash and an atomic CSV writer.

# sha256 of a raw vector or a string (UTF-8 bytes), through tools::sha256sum so no extra package is needed.
fi_sha256_raw <- function(x) {
  if (is.character(x)) x <- charToRaw(enc2utf8(paste(x, collapse = "")))
  tmp <- tempfile("fi_sha_")
  on.exit(unlink(tmp), add = TRUE)
  writeBin(x, tmp)
  rf_sha256(tmp)
}

# First n bytes of a file (the magic bytes of the classifier).
fi_head_bytes <- function(path, n = 8L) {
  con <- file(path, open = "rb")
  on.exit(close(con))
  readBin(con, what = "raw", n = n)
}

# Whole file as a raw vector.
fi_read_raw <- function(path) readBin(path, what = "raw", n = file.size(path))

# UTF-8 without BOM, LF line endings, NA as empty; temporary file then rename. Returns the sha256 of the written file.
fi_write_csv_atomic <- function(df, path) {
  # rendered in memory and written in binary mode (a Windows text-mode connection would turn LF into CRLF)
  tc <- textConnection("fi_csv_lines", open = "w", local = TRUE)
  utils::write.table(df, tc, sep = ",", row.names = FALSE, col.names = TRUE, quote = TRUE, na = "", eol = "\n", qmethod = "double")
  close(tc)
  rf_write_text_atomic(paste0(paste(fi_csv_lines, collapse = "\n"), "\n"), path)
}

# A CSV written by fi_write_csv_atomic, read back as character columns (callers convert what they need).
fi_read_csv <- function(path) utils::read.csv(path, stringsAsFactors = FALSE, colClasses = "character", na.strings = "", check.names = FALSE,
                                              fileEncoding = "UTF-8")

# Number or NA from text ("" / NA -> NA).
fi_num <- function(x) suppressWarnings(as.numeric(x))

# Whitespace normalisation of a cell: collapse every run of Unicode whitespace (non-breaking spaces included) to one space.
fi_clean_cell <- function(x) trimws(gsub("[\\s ]+", " ", x, perl = TRUE), whitespace = "[\\h\\v]")

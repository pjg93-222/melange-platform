# Inbox scan (FEEDS_SPEC 6 step 1): hash every file, classify it by its magic bytes, deduplicate by hash, copy new files to the
# vault. Inbox files are NEVER modified, moved or deleted: the scan only reads them, and fi_inbox_state() lets a caller (the
# run, the tests) assert that nothing changed. First-seen times live in a ledger under FRT, not in the inbox.

# "pdf" | "xlsx" | "zip" | "html" | "text" | "unsupported", from the bytes and never from the name.
fi_classify_file <- function(path) {
  if (!file.exists(path) || dir.exists(path)) return("unsupported")
  n <- file.size(path)
  if (is.na(n) || n == 0) return("unsupported")
  head <- fi_head_bytes(path, min(n, 4096L))
  if (length(head) >= 4L && identical(head[1:4], charToRaw("%PDF"))) return("pdf")
  if (length(head) >= 4L && identical(head[1:2], charToRaw("PK"))) {
    members <- tryCatch(utils::unzip(path, list = TRUE)$Name, error = function(e) character())
    if (!length(members)) return("unsupported")
    return(if ("xl/workbook.xml" %in% members) "xlsx" else "zip")
  }
  if (any(head == as.raw(0L))) return("unsupported")
  txt <- tolower(iconv(rawToChar(head[head != as.raw(0L)]), from = "latin1", to = "UTF-8", sub = "byte"))   # bulk files are Latin-1
  if (grepl("<html|<!doctype html|<table", txt)) return("html")
  "text"
}

# Name, size, mtime and sha256 of every file under dir (sorted). Used to prove the inbox is unchanged after a run.
fi_inbox_state <- function(dir) {
  files <- sort(list.files(dir, recursive = TRUE, full.names = TRUE, all.files = TRUE, no.. = TRUE))
  files <- files[!dir.exists(files)]
  if (!length(files)) return(data.frame(name = character(), bytes = numeric(), mtime = numeric(), sha256 = character(), stringsAsFactors = FALSE))
  data.frame(name = substring(files, nchar(dir) + 2L), bytes = as.numeric(file.size(files)),
             mtime = as.numeric(file.mtime(files)), sha256 = vapply(files, rf_sha256, ""), stringsAsFactors = FALSE, row.names = NULL)
}

.fi_ledger_read <- function(path) {
  if (!file.exists(path)) return(list())
  tryCatch(jsonlite::fromJSON(path, simplifyVector = FALSE), error = function(e) list())
}

# Scan one inbox sub-folder. `allowed` are the kinds the feed can read; anything else is recorded as "unsupported" and left in
# place. With write = FALSE (dry run) nothing is written to the vault or the ledger.
# -> data.frame(inbox_name, path, sha256, bytes, kind, status, duplicate_of, first_seen_utc, vault_path); attr "summary".
fi_inbox_scan <- function(dir, allowed = c("pdf", "xlsx"), ledger_path = file.path(fi_frt_dir(), "inbox_seen.json"), write = TRUE,
                          now = rf_now_utc()) {
  empty <- data.frame(inbox_name = character(), path = character(), sha256 = character(), bytes = numeric(), kind = character(),
                      status = character(), duplicate_of = character(), first_seen_utc = character(), vault_path = character(),
                      stringsAsFactors = FALSE)
  files <- if (dir.exists(dir)) sort(list.files(dir, recursive = TRUE, full.names = TRUE, all.files = FALSE)) else character()
  files <- files[!dir.exists(files)]
  ledger <- .fi_ledger_read(ledger_path)
  rows <- list()
  first_by_sha <- character()   # sha256 -> inbox name of the first file with those bytes
  for (f in files) {
    sha <- rf_sha256(f)
    kind <- fi_classify_file(f)
    name <- substring(f, nchar(dir) + 2L)
    status <- "new"
    dup <- NA_character_
    if (!(kind %in% allowed)) {
      status <- "unsupported"
    } else if (sha %in% names(first_by_sha)) {
      status <- "duplicate"
      dup <- first_by_sha[[sha]]
    } else {
      first_by_sha[sha] <- name
      if (!is.null(ledger[[sha]])) status <- "seen_before"
    }
    seen <- ledger[[sha]] %||% now
    vp <- NA_character_
    if (write && status %in% c("new", "seen_before")) vp <- rf_vault_put(f, sha)
    if (is.null(ledger[[sha]])) ledger[[sha]] <- now
    rows[[length(rows) + 1L]] <- data.frame(inbox_name = name, path = f, sha256 = sha, bytes = as.numeric(file.size(f)), kind = kind,
                                            status = status, duplicate_of = dup, first_seen_utc = seen, vault_path = vp,
                                            stringsAsFactors = FALSE)
  }
  out <- if (length(rows)) do.call(rbind, rows) else empty
  if (write && length(rows)) rf_write_json_atomic(ledger, ledger_path)
  attr(out, "summary") <- list(files = nrow(out), new = sum(out$status %in% c("new", "seen_before")),
                               duplicate = sum(out$status == "duplicate"), unsupported = sum(out$status == "unsupported"))
  out
}

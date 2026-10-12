# OpenSecrets bulk-data fallback (FEEDS_SPEC 4): the reader and the reconstruction recipe. The owner downloads the files with his
# own account and drops them unchanged into INBOX\opensecrets\bulk\; nothing here logs in or downloads. The bulk route is used
# only when the page fetcher is unavailable or blocked, and its numbers are used only after fi_os_calibrate accepts the recipe
# against the page captures already on disk.
#
# File layouts are the ones OpenSecrets publishes for its campaign-finance bulk data, as the spec recalls them; every layout the
# recipe relies on is verified here (column counts asserted, the category file checked) and a mismatch stops the run.

FI_OS_BULK_COLS <- list(
  cands = c("Cycle", "FECCandID", "CID", "FirstLastP", "Party", "DistIDRunFor", "DistIDCurr", "CurrCand", "CycleCand", "CRPICO", "RecipCode", "NoPacs"),
  cmtes = c("Cycle", "CmteID", "PACShort", "Affiliate", "Ultorg", "RecipID", "RecipCode", "FECCandID", "Party", "PrimCode", "Source", "Sensitive", "Foreign", "Active"),
  pacs = c("Cycle", "FECRecNo", "PACID", "CID", "Amount", "Date", "RealCode", "Type", "DI", "FECCandID"),
  indivs = c("Cycle", "FECTransID", "ContribID", "Contrib", "RecipID", "Orgname", "UltOrg", "RealCode", "Date", "Amount", "Street", "City", "State", "Zip",
             "RecipCode", "Type", "CmteID", "OtherID", "Gender", "Microfilm", "Occupation", "Employer", "Source"),
  pac_other = c("Cycle", "FECRecNo", "Filerid", "DonorCmte", "ContribLendTrans", "City", "State", "Zip", "FECOccEmp", "Primcode", "Date", "Amount", "RecipID",
                "Party", "Otherid", "RecipCode", "RecipPrimcode", "Amend", "Report", "PG", "Microfilm", "Type", "RealCode", "Source")
)
FI_OS_STATE_NAMES <- c(stats::setNames(state.name, state.abb), DC = "District of Columbia", PR = "Puerto Rico", GU = "Guam", VI = "Virgin Islands",
                       AS = "American Samoa", MP = "Northern Mariana Islands")

# ---- categories file ------------------------------------------------------------------------------------------------------

# CRP_Categories.txt: tab-delimited with a header row (Catcode, Catname, Catorder, Industry, Sector, Sector Long) after any preamble.
fi_os_read_categories <- function(path) {
  raw <- readLines(path, warn = FALSE, encoding = "latin1")
  raw <- iconv(raw, "latin1", "UTF-8", sub = "byte")
  h <- which(startsWith(trimws(raw), "Catcode"))[1]
  if (is.na(h)) stop("fi_os_read_categories: no header row starting with Catcode in ", basename(path), call. = FALSE)
  body <- raw[h:length(raw)]
  body <- body[nzchar(trimws(body))]
  d <- utils::read.delim(text = paste(body, collapse = "\n"), sep = "\t", quote = "", stringsAsFactors = FALSE, colClasses = "character", check.names = FALSE)
  names(d)[1:min(ncol(d), 6L)] <- c("Catcode", "Catname", "Catorder", "Industry", "Sector", "SectorLong")[1:min(ncol(d), 6L)]
  d
}

# The bulk-route verification of the OpenSecrets code (FEEDS_SPEC 2.1): the code is present, its name matches, its order code is
# the expected parent industry. -> list(verified, row, issues)
fi_os_verify_categories <- function(cats, code, expect) {
  row <- cats[cats$Catcode == code, , drop = FALSE]
  if (nrow(row) != 1L) return(list(verified = FALSE, row = NULL, issues = sprintf("code_%s_not_found_exactly_once_in_categories", code)))
  issues <- character()
  if (!grepl(expect$catname_regex, row$Catname, ignore.case = TRUE, perl = TRUE)) issues <- c(issues, sprintf("catname_'%s'_does_not_match", row$Catname))
  if (!is.null(expect$catorder) && !identical(row$Catorder, expect$catorder)) issues <- c(issues, sprintf("catorder_is_%s_not_%s", row$Catorder, expect$catorder))
  list(verified = !length(issues), row = row, issues = issues)
}

# ---- locating and reading bulk tables ---------------------------------------------------------------------------------------

# A table file of one cycle under the bulk folder: {table}{yy}.txt, or that member inside any zip. -> list(path, member) or NULL.
fi_os_bulk_locate <- function(dir, table, cycle) {
  yy <- sprintf("%02d", cycle %% 100L)
  want <- tolower(paste0(table, yy, ".txt"))
  files <- list.files(dir, recursive = TRUE, full.names = TRUE)
  plain <- files[tolower(basename(files)) == want]
  if (length(plain)) return(list(path = plain[1], member = NULL))
  for (z in files[grepl("\\.zip$", files, ignore.case = TRUE)]) {
    mem <- tryCatch(utils::unzip(z, list = TRUE)$Name, error = function(e) character())
    hit <- mem[tolower(basename(mem)) == want]
    if (length(hit)) return(list(path = z, member = hit[1]))
  }
  NULL
}

.fi_bulk_open <- function(loc) if (is.null(loc$member)) file(loc$path, open = "rb") else unz(loc$path, loc$member, open = "rb")

# Number of lines of a file or zip member, counted independently of the reader (LF bytes, plus a final unterminated line).
fi_count_lines <- function(loc, block = 16L * 1024L * 1024L) {
  con <- .fi_bulk_open(loc)
  on.exit(close(con))
  n <- 0; last <- NULL
  repeat {
    b <- readBin(con, what = "raw", n = block)
    if (!length(b)) break
    n <- n + sum(b == as.raw(10L))
    last <- b[length(b)]
  }
  if (!is.null(last) && last != as.raw(10L)) n <- n + 1
  n
}

# Read one bulk table in chunks of chunk_lines lines. `codes` keeps only the lines holding |<code>| for one of the codes (a cheap
# prefilter before the CSV parser; the code is matched exactly on RealCode afterwards). The run asserts that the lines read equal
# the file's line count and records the rows kept per chunk. The result never depends on chunk_lines.
# -> data.frame (character columns named by FI_OS_BULK_COLS[[table]]); attr "lines" = list(read, counted, kept_per_chunk)
fi_os_bulk_read <- function(loc, table, codes = NULL, chunk_lines = 1e6) {
  if (!requireNamespace("data.table", quietly = TRUE)) stop("fi_os_bulk_read: package data.table is unavailable", call. = FALSE)
  cols <- FI_OS_BULK_COLS[[table]]
  if (is.null(cols)) stop("fi_os_bulk_read: unknown table ", table, call. = FALSE)
  counted <- fi_count_lines(loc)
  con <- .fi_bulk_open(loc)
  on.exit(close(con))
  n_read <- 0; kept_per_chunk <- integer(); parts <- list()
  repeat {
    lines <- readLines(con, n = chunk_lines, warn = FALSE)
    if (!length(lines)) break
    n_read <- n_read + length(lines)
    lines <- iconv(lines, "latin1", "UTF-8", sub = "byte")
    if (!is.null(codes)) {
      keep <- Reduce(`|`, lapply(codes, function(cd) grepl(paste0("|", cd, "|"), lines, fixed = TRUE)))
      lines <- lines[keep]
    }
    kept_per_chunk <- c(kept_per_chunk, length(lines))
    if (!length(lines)) next
    d <- data.table::fread(text = paste(lines, collapse = "\n"), sep = ",", quote = "|", header = FALSE, colClasses = "character", na.strings = NULL,
                           data.table = FALSE, fill = FALSE, showProgress = FALSE)
    if (ncol(d) != length(cols)) stop(sprintf("fi_os_bulk_read: %s has %d fields per line, the layout expects %d", basename(loc$path), ncol(d), length(cols)), call. = FALSE)
    names(d) <- cols
    parts[[length(parts) + 1L]] <- d
  }
  if (n_read != counted) stop(sprintf("fi_os_bulk_read: read %d lines of %s but the file has %d", n_read, basename(loc$path), counted), call. = FALSE)
  out <- if (length(parts)) do.call(rbind, parts) else as.data.frame(stats::setNames(rep(list(character()), length(cols)), cols), stringsAsFactors = FALSE)
  if (!is.null(codes) && "RealCode" %in% names(out)) out <- out[out$RealCode %in% codes, , drop = FALSE]
  attr(out, "lines") <- list(read = n_read, counted = counted, kept_per_chunk = kept_per_chunk)
  rownames(out) <- NULL
  out
}

# ---- reconstruction ---------------------------------------------------------------------------------------------------------

# Chamber of a candidate from DistIDRunFor: PRES -> P; third character S -> Senate; otherwise House. State = first two characters.
fi_os_chamber <- function(dist) ifelse(dist == "PRES", "P", ifelse(substr(dist, 3, 3) == "S", "S", "H"))

# "Bob Corker (R)" -> "Corker, Bob (R-TN)" (President: "Romney, Mitt (R)"); suffixes stay with the first name.
fi_os_label <- function(first_last_p, party, state, chamber) {
  nm <- trimws(sub("\\s*\\([^()]*\\)\\s*$", "", first_last_p))
  toks <- strsplit(nm, "\\s+")[[1]]
  suffix <- grepl("^(jr|sr|ii|iii|iv|v)\\.?$", tolower(toks))
  core <- toks[!suffix]
  last <- if (length(core)) core[length(core)] else nm
  first <- paste(c(core[-length(core)], toks[suffix]), collapse = " ")
  base <- if (nzchar(first)) paste0(last, ", ", first) else last
  if (chamber == "P") sprintf("%s (%s)", base, party) else sprintf("%s (%s-%s)", base, party, state)
}

.fi_money_text <- function(a) paste0(ifelse(a < 0, "-", ""), "$", formatC(abs(a), format = "f", digits = 0, big.mark = ","))

# Build one industry's candidate recipients and industry totals for the given cycles from a bulk folder and a recipe.
#   codes    the OpenSecrets category codes of the industry
#   recipe   a feeds$bulk_recipes entry
# -> list(recipients = data.frame (page-shaped, FEEDS_SPEC 7.2 columns), totals = data.frame (cycle, pacs, individuals, democrats,
#         republicans), checks = data.frame, lines = per-file line accounting)
fi_os_bulk_build <- function(dir, codes, recipe, cycles, industry_id = NA_character_) {
  rec <- list(); tot <- list(); checks <- list(); lines <- list()
  add_check <- function(name, examined, failures, detail = "") checks[[length(checks) + 1L]] <<- rf_check(name, examined, failures, detail)
  for (cy in cycles) {
    loc <- lapply(c("cands", "pacs", "indivs"), function(t) fi_os_bulk_locate(dir, t, cy))
    names(loc) <- c("cands", "pacs", "indivs")
    miss <- names(loc)[vapply(loc, is.null, NA)]
    if (length(miss)) stop(sprintf("fi_os_bulk_build: cycle %d is missing bulk table(s): %s", cy, paste(miss, collapse = ", ")), call. = FALSE)
    cands <- fi_os_bulk_read(loc$cands, "cands")
    pacs <- fi_os_bulk_read(loc$pacs, "pacs", codes)
    indivs <- fi_os_bulk_read(loc$indivs, "indivs", codes)
    lines[[as.character(cy)]] <- list(cands = attr(cands, "lines"), pacs = attr(pacs, "lines"), indivs = attr(indivs, "lines"))
    cands <- cands[cands$Cycle == as.character(cy), , drop = FALSE]
    pacs$Amount <- as.numeric(pacs$Amount); indivs$Amount <- as.numeric(indivs$Amount)
    # candidate money: individual gifts to N-prefixed recipients + direct PAC gifts; refunds (negatives) stay
    pre <- recipe$individuals$recipient_prefix %||% "N"
    ind_c <- indivs[startsWith(indivs$RecipID, pre), , drop = FALSE]
    ex <- unlist(recipe$individuals$exclude_types)
    if (length(ex)) ind_c <- ind_c[!(ind_c$Type %in% ex), , drop = FALSE]
    pac_c <- pacs[pacs$DI %in% unlist(recipe$pacs$di), , drop = FALSE]
    money <- rbind(data.frame(CID = ind_c$RecipID, Amount = ind_c$Amount, stringsAsFactors = FALSE), data.frame(CID = pac_c$CID, Amount = pac_c$Amount, stringsAsFactors = FALSE))
    by <- if (nrow(money)) stats::aggregate(Amount ~ CID, money, sum) else data.frame(CID = character(), Amount = numeric())
    m <- merge(by, cands[!duplicated(cands$CID), ], by = "CID", all.x = TRUE)
    unmatched <- sum(is.na(m$FirstLastP))
    add_check(sprintf("every recipient CID is a candidate of cycle %d", cy), nrow(m), unmatched, if (unmatched) paste(utils::head(m$CID[is.na(m$FirstLastP)], 5), collapse = ",") else "")
    m <- m[!is.na(m$FirstLastP), , drop = FALSE]
    if (nrow(m)) {
      ch <- fi_os_chamber(m$DistIDRunFor)
      st <- substr(m$DistIDRunFor, 1, 2)
      lab <- mapply(fi_os_label, m$FirstLastP, m$Party, st, ch, USE.NAMES = FALSE)
      rec[[length(rec) + 1L]] <- data.frame(industry_id = industry_id, os_code = codes[1], cycle = cy, chamber = ch, recipient_label = lab, name_key = rf_name_key(lab),
                                            party = m$Party, state = ifelse(ch == "P", NA_character_, unname(FI_OS_STATE_NAMES[st])), amount = m$Amount,
                                            cid = m$CID, fec_cand_id = m$FECCandID, dist_id_run_for = m$DistIDRunFor, route = "bulk",
                                            source_sha256 = NA_character_, stringsAsFactors = FALSE)
    }
    # industry totals (PAC model): pacs, individuals, and the D / R split by the recipient's party
    pac_t <- pacs[pacs$DI %in% unlist(recipe$totals$pacs_di %||% recipe$pacs$di), , drop = FALSE]
    ind_t <- indivs[startsWith(indivs$RecipID, recipe$totals$individuals_recipient_prefix %||% pre), , drop = FALSE]
    pparty <- cands$Party[match(pac_t$CID, cands$CID)]
    iparty <- substr(ind_t$RecipCode, 1, 1)
    tot[[length(tot) + 1L]] <- data.frame(industry_id = industry_id, os_code = codes[1], cycle = cy, pacs = sum(pac_t$Amount), individuals = sum(ind_t$Amount),
                                          democrats = sum(pac_t$Amount[pparty %in% "D"]) + sum(ind_t$Amount[iparty == "D"]),
                                          republicans = sum(pac_t$Amount[pparty %in% "R"]) + sum(ind_t$Amount[iparty == "R"]), stringsAsFactors = FALSE)
  }
  list(recipients = if (length(rec)) do.call(rbind, rec) else NULL, totals = if (length(tot)) do.call(rbind, tot) else NULL,
       checks = if (length(checks)) do.call(rbind, checks) else rf_check("none", 0L, 0L)[0, ], lines = lines)
}

# Page-shaped envelopes of a bulk build (route "bulk"): one per cycle and chamber, the rows as the pages list them. Totals are
# written as a `bulk_totals` block with null for every column the recipe did not calibrate, and with an empty `tables`, so the
# page-route loaders fail loudly instead of reading an uncalibrated number.
fi_os_bulk_envelopes <- function(build, code, recipe_version, calibration_sha256 = NA_character_, source_sha = NA_character_) {
  out <- list()
  rec <- build$recipients
  mk <- function(kind, cycle, tbl_rows) list(url = NA_character_, final_url = NA_character_, title = NA_character_, retrieved_utc = rf_now_utc(), http = NA_integer_, route = "bulk",
                                              recipe_version = recipe_version, calibration_receipt_sha256 = calibration_sha256, source_file_sha256 = source_sha, os_code = code,
                                              cycle = as.integer(cycle), kind = kind, source_tables = list(list(rows = tbl_rows, row_links = list())), tables = list(),
                                              fec_release_note = NA_character_, parser = list(name = "fi_os_bulk", version = FI$VERSION))
  if (!is.null(rec)) for (cy in sort(unique(rec$cycle))) for (ch in c("H", "S", "P")) {
    r <- rec[rec$cycle == cy & rec$chamber == ch, , drop = FALSE]
    if (!nrow(r)) next
    r <- r[order(-r$amount, r$recipient_label), ]
    if (ch == "P") {
      rows <- c(list(I(c("Rank", "Recipient", "Chamber", "Total"))), lapply(seq_len(nrow(r)), function(i) I(c(as.character(i), r$recipient_label[i], "President", .fi_money_text(r$amount[i])))))
      out[[fi_os_envelope_name(code, cy, "presidential")]] <- mk("presidential", cy, rows)
    } else {
      rows <- c(list(I(c(if (ch == "H") "Representative" else "Senator", "State", "Amount"))), lapply(seq_len(nrow(r)), function(i) I(c(r$recipient_label[i], r$state[i], .fi_money_text(r$amount[i])))))
      out[[fi_os_envelope_name(code, cy, paste0("candidates_", ch))]] <- mk(paste0("candidates_", ch), cy, rows)
    }
  }
  if (!is.null(build$totals)) {
    tb <- build$totals
    out[[sprintf("%s_totals.json", code)]] <- list(url = NA_character_, route = "bulk", recipe_version = recipe_version, calibration_receipt_sha256 = calibration_sha256,
                                                   retrieved_utc = rf_now_utc(), os_code = code, kind = "totals", source_tables = list(), tables = list(),
                                                   bulk_totals = lapply(seq_len(nrow(tb)), function(i) list(cycle = tb$cycle[i], pacs = tb$pacs[i], individuals = tb$individuals[i],
                                                                                                          democrats = tb$democrats[i], republicans = tb$republicans[i], total = NULL, outside = NULL)),
                                                   parser = list(name = "fi_os_bulk", version = FI$VERSION))
  }
  out
}

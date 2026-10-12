# Calibration gate of the bulk route (FEEDS_SPEC 4.3). A bulk recipe is used for numbers only if, for every calibration code and
# every cycle 2012-2024 that the frozen page captures cover,
#   - the PAC total is within 0.5% of the page,
#   - each chamber's Democratic and Republican sums are within 1%,
#   - at least 98% of the page's recipients match by name key with amounts within $1.
# If no recipe passes, the run STOPS with the residual table and the bulk-route capabilities stay withheld. The page captures are
# the frozen baseline (never re-fetched, never overwritten); they are only read.

FI_CAL_TOL <- list(pac = 0.005, party = 0.01, match = 0.98, amount = 1)

.fi_rel <- function(a, b) if (is.na(a) || is.na(b)) NA_real_ else if (b == 0) (if (a == 0) 0 else Inf) else abs(a - b) / abs(b)

# Compare one code's bulk build with its page captures.
#   bulk_rec / page_rec  data.frame(cycle, chamber ("H"|"S"), name_key, party, amount)
#   bulk_tot / page_tot  data.frame(cycle, pac)
# -> data.frame(cycle, pac_rel_delta, dem_H, rep_H, dem_S, rep_S (relative deltas), match_H, match_S (match rates), pass)
fi_os_calibrate_compare <- function(bulk_rec, bulk_tot, page_rec, page_tot, tol = FI_CAL_TOL) {
  cycles <- sort(intersect(page_tot$cycle, bulk_tot$cycle))
  rows <- lapply(cycles, function(cy) {
    pac <- .fi_rel(bulk_tot$pac[bulk_tot$cycle == cy], page_tot$pac[page_tot$cycle == cy])
    one <- function(ch, p) {
      b <- bulk_rec[bulk_rec$cycle == cy & bulk_rec$chamber == ch, , drop = FALSE]
      g <- page_rec[page_rec$cycle == cy & page_rec$chamber == ch, , drop = FALSE]
      if (!nrow(g)) return(c(NA_real_, NA_real_))
      c(.fi_rel(sum(b$amount[b$party == p]), sum(g$amount[g$party == p])), NA_real_)
    }
    mr <- function(ch) {
      b <- bulk_rec[bulk_rec$cycle == cy & bulk_rec$chamber == ch, , drop = FALSE]
      g <- page_rec[page_rec$cycle == cy & page_rec$chamber == ch, , drop = FALSE]
      if (!nrow(g)) return(NA_real_)
      j <- match(g$name_key, b$name_key)
      mean(!is.na(j) & abs(b$amount[j] - g$amount) <= tol$amount, na.rm = FALSE)
    }
    d <- c(dem_H = one("H", "D")[1], rep_H = one("H", "R")[1], dem_S = one("S", "D")[1], rep_S = one("S", "R")[1])
    m <- c(match_H = mr("H"), match_S = mr("S"))
    ok <- !is.na(pac) && pac <= tol$pac && all(is.na(d) | d <= tol$party) && all(is.na(m) | m >= tol$match)
    data.frame(cycle = cy, pac_rel_delta = pac, t(d), t(m), pass = ok, stringsAsFactors = FALSE)
  })
  if (length(rows)) do.call(rbind, rows) else data.frame(cycle = integer(), pac_rel_delta = numeric(), pass = logical())
}

# Paths of the frozen page captures of a calibration code (under the LOO root of the refit's aliases).
fi_os_frozen_paths <- function(code, cycles = seq(2012L, 2024L, 2L), roots = rf_roots()) {
  base <- roots$LOO
  list(totals = file.path(base, "outputs/opensecrets_baseline/direct_sources", sprintf("%s_2024_totals.json", code)),
       candidates = lapply(stats::setNames(cycles, cycles), function(cy) c(H = file.path(base, "outputs/opensecrets_partisanship/direct_sources", sprintf("%s_%d_candidates_H.json", code, cy)),
                                                                           S = file.path(base, "outputs/opensecrets_partisanship/direct_sources", sprintf("%s_%d_candidates_S.json", code, cy)))))
}

# Page-side data of a code: totals (cycle, pac) and recipients (cycle, chamber, name_key, party, amount; empty for a code with no
# recipient captures, e.g. F09).
fi_os_frozen_page_data <- function(code, cycles = seq(2012L, 2024L, 2L), roots = rf_roots()) {
  p <- fi_os_frozen_paths(code, cycles, roots)
  tot <- NULL
  if (file.exists(p$totals)) { t <- rf_os_totals(p$totals); tot <- data.frame(cycle = t$cycle_end, pac = t$pac_nominal, stringsAsFactors = FALSE) }
  recs <- list()
  for (cy in cycles) for (ch in c("H", "S")) {
    f <- p$candidates[[as.character(cy)]][[ch]]
    if (!file.exists(f)) next
    x <- rf_os_candidates(f, ch)
    recs[[length(recs) + 1L]] <- data.frame(cycle = cy, chamber = ch, name_key = x$name_key, party = x$party, amount = x$amount, stringsAsFactors = FALSE)
  }
  list(totals = tot, recipients = if (length(recs)) do.call(rbind, recs) else NULL)
}

# Run the gate. `recipes` are the registry's bulk recipes in order of preference; the first that passes everywhere is accepted.
# -> list(accepted (recipe version or NULL), table (recipe, code, cycle, deltas, pass), passed, max_delta)
fi_os_calibrate <- function(dir, feeds, cycles = seq(2012L, 2024L, 2L), roots = rf_roots(), recipes = feeds$bulk_recipes, tol = FI_CAL_TOL, build = fi_os_bulk_build) {
  codes <- unlist(feeds$industries[[1]]$opensecrets$calibration_codes)
  if (!length(codes)) stop("fi_os_calibrate: no calibration_codes in the feeds registry", call. = FALSE)
  pages <- lapply(stats::setNames(codes, codes), fi_os_frozen_page_data, cycles = cycles, roots = roots)
  table <- list(); accepted <- NULL
  for (rc in recipes) {
    ok_all <- TRUE
    for (cd in codes) {
      pg <- pages[[cd]]
      # the frozen captures are the only yardstick: a missing one is an error, never a silent pass
      if (is.null(pg$totals)) stop("fi_os_calibrate: the frozen totals capture of calibration code ", cd, " is missing; calibration cannot run", call. = FALSE)
      cyc <- intersect(cycles, pg$totals$cycle)
      b <- build(dir, cd, rc, cyc, industry_id = cd)
      bt <- if (is.null(b$totals)) data.frame(cycle = integer(), pac = numeric()) else data.frame(cycle = b$totals$cycle, pac = b$totals$pacs)
      br <- if (is.null(b$recipients)) data.frame(cycle = integer(), chamber = character(), name_key = character(), party = character(), amount = numeric()) else
        b$recipients[b$recipients$chamber %in% c("H", "S"), c("cycle", "chamber", "name_key", "party", "amount")]
      cmp <- fi_os_calibrate_compare(br, bt, if (is.null(pg$recipients)) br[0, ] else pg$recipients, pg$totals, tol)
      if (nrow(cmp)) table[[length(table) + 1L]] <- cbind(data.frame(recipe = rc$version, code = cd, stringsAsFactors = FALSE), cmp)
      ok_all <- ok_all && nrow(cmp) > 0L && all(cmp$pass)
    }
    if (ok_all) { accepted <- rc$version; break }
  }
  tb <- if (length(table)) do.call(rbind, table) else NULL
  num <- if (!is.null(tb)) unlist(tb[, intersect(c("pac_rel_delta", "dem_H", "rep_H", "dem_S", "rep_S"), names(tb))]) else numeric()
  list(accepted = accepted, table = tb, passed = !is.null(accepted), max_delta = if (length(num) && any(is.finite(num))) max(num[is.finite(num)]) else NA_real_)
}

# ---- the frozen page inventory --------------------------------------------------------------------------------------------------------------
# "Frozen" means the existing captures of 2026-09-27 (the 63 recipient captures, the totals and summary pages) are never re-fetched or
# overwritten. It does not mean a code is off limits: a page of an industry that no capture holds may be fetched. The inventory lists every
# captured page (code, cycle, kind), its file, sha256 and retrieval time, so the fetcher can plan only the missing pages and refuse the
# captured ones. It holds no page content. It is generated from the capture folders by tools/make_frozen_inventory.R.

FI_CAPTURE_DIRS <- c(baseline = "outputs/opensecrets_baseline/direct_sources", partisanship = "outputs/opensecrets_partisanship/direct_sources")

# Page kind of a capture file stem ("F09_2024_totals" -> totals); probes and index pages keep their own kinds.
.fi_capture_kind <- function(tail) {
  if (tail %in% c("totals", "summary", "presidential", "candidates_H", "candidates_S")) return(tail)
  if (tail == "recips") return("recips")
  if (tail == "house_candidates") return("house_candidates")
  NA_character_
}

# Scan the capture folders under `root` (the LOO root). -> data.frame(code, cycle, kind, group, file, sha256, url, retrieved_utc), sorted.
fi_os_scan_captures <- function(root = rf_roots()$LOO) {
  rows <- list()
  for (g in names(FI_CAPTURE_DIRS)) {
    d <- file.path(root, FI_CAPTURE_DIRS[[g]])
    for (f in sort(list.files(d, pattern = "\\.json$", full.names = TRUE))) {
      m <- regmatches(basename(f), regexec("^([A-Z][0-9]{2,4})_([0-9]{4})_(.+)\\.json$", basename(f)))[[1]]
      if (length(m) != 4L) next
      kind <- .fi_capture_kind(m[4])
      if (is.na(kind)) next
      j <- tryCatch(jsonlite::fromJSON(f, simplifyVector = FALSE), error = function(e) NULL)
      rows[[length(rows) + 1L]] <- data.frame(code = m[2], cycle = as.integer(m[3]), kind = kind, group = g, file = file.path(FI_CAPTURE_DIRS[[g]], basename(f)),
                                              sha256 = rf_sha256(f), url = j$url %||% NA_character_, retrieved_utc = j$retrieved_utc %||% NA_character_, stringsAsFactors = FALSE)
    }
  }
  out <- if (length(rows)) do.call(rbind, rows) else NULL
  if (!is.null(out)) out <- out[order(out$group, out$code, out$cycle, out$kind, out$file), ]
  rownames(out) <- NULL
  out
}

fi_os_inventory_path <- function(feeds = NULL) {
  rel <- feeds$opensecrets_frozen$inventory %||% "registry/frozen_os_pages.json"
  if (grepl("^([A-Za-z]:)?[/\\]", rel)) rel else file.path(fi_home(), rel)      # an absolute path is used as given (tests)
}

# Read the committed inventory. NULL when the registry names none; an error when it names one that is missing or unreadable (the gate must
# fail closed: without the inventory a captured page could be fetched again).
fi_os_frozen_inventory <- function(feeds) {
  if (is.null(feeds$opensecrets_frozen$inventory)) return(NULL)
  p <- fi_os_inventory_path(feeds)
  if (!file.exists(p)) stop("fi_os_frozen_inventory: the registry names the frozen page inventory ", feeds$opensecrets_frozen$inventory, " but it is missing", call. = FALSE)
  j <- jsonlite::fromJSON(p, simplifyVector = FALSE)
  rows <- lapply(j$pages, function(z) data.frame(code = z$code, cycle = as.integer(z$cycle), kind = z$kind, group = z$group %||% NA_character_, file = z$file %||% NA_character_,
                                                  sha256 = z$sha256 %||% NA_character_, url = z$url %||% NA_character_, retrieved_utc = z$retrieved_utc %||% NA_character_, stringsAsFactors = FALSE))
  out <- do.call(rbind, rows)
  attr(out, "generated") <- j$generated_from
  out
}

fi_os_write_inventory <- function(inv, path, generated_from = "") {
  rf_write_json_atomic(list(schema_version = 1L, note = paste("Every page of the 2026-09-27 OpenSecrets captures (code, cycle, kind, file, sha256). No page content.",
                                                              "These pages are never re-fetched or overwritten; pages missing from them may be fetched."),
                            generated_from = generated_from, pages = lapply(seq_len(nrow(inv)), function(i) as.list(inv[i, ]))), path)
}

# Is the page (code, cycle, kind) in the inventory?
fi_os_in_inventory <- function(inv, code, cycle, kind) {
  if (is.null(inv) || !nrow(inv)) return(rep(FALSE, length(code)))
  paste(code, cycle, kind) %in% paste(inv$code, inv$cycle, inv$kind)
}

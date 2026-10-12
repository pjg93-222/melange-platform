# OpenSecrets page captures (industry totals, candidate recipients by chamber, presidential recipients), the FEC candidate
# masters, and the congressional-account money panel. Port of loo/work/opensecrets_baseline/build_test.py:12-54 and
# loo/work/opensecrets_partisanship/build.py:13-92; every parity claim is proven in raw_rebuild_probe.R.
# The captures are browser downloads (a separate human-triggered step); the pipeline reads their JSON only.

RF_TOTALS_HEADER <- c("Election Cycle", "Total Contributions", "Contributions from Individuals", "Contributions from PACs",
                      "Soft/Outside Money", "Contributions to Democrats", "Contributions to Republicans",
                      "Percentage to Democrats", "Percentage to Republicans")

# Aliases the Codex build used to match FEC presidential filers to congressional recipients that OpenSecrets lists under a
# nickname (build.py:36-37): FEC "SURNAME, GIVEN" (upper-case) -> the recipient's name key.
RF_FEC_ALIASES <- c('CRUZ, RAFAEL EDWARD "TED"' = "cruz, ted", "SANDERS, BERNARD" = "sanders, bernie",
                    "O'ROURKE, ROBERT BETO" = "o'rourke, beto", "RYAN, TIMOTHY J." = "ryan, tim",
                    "SESTAK, JOSEPH A JR" = "sestak, joe", "SCOTT, TIMOTHY E." = "scott, tim")

# Recipient key: strip a trailing "(P-ST)" then lower-case (Python: re.sub(...).strip().casefold()).
rf_name_key <- function(s) tolower(trimws(sub("\\s*\\([^()]+\\)\\s*$", "", s)))

# One industry's totals capture -> one row per election cycle (nominal USD, whole dollars).
# Asserts the header and total == individuals + PACs + soft/outside money (build_test.py:21-22).
rf_os_totals <- function(json_path) {
  v <- rf_read_json(json_path)
  is_totals <- vapply(v$tables, function(t) length(t$rows) > 0L && identical(t$rows[[1]][[1]], "Election Cycle"), logical(1))
  if (sum(is_totals) != 1L) rf_raise("PanelError", paste("expected one totals table in", basename(json_path)))
  rows <- v$tables[[which(is_totals)]]$rows
  if (!identical(unlist(rows[[1]]), RF_TOTALS_HEADER)) rf_raise("PanelError", paste("unexpected totals header in", basename(json_path)))
  m <- do.call(rbind, lapply(rows[-1], function(r) unlist(r)[1:7]))
  cyc <- as.integer(m[, 1])
  a <- apply(m[, 2:7, drop = FALSE], 2, rf_money_int)
  if (!all(a[, 1] == a[, 2] + a[, 3] + a[, 4])) rf_raise("PanelError", paste("totals identity (total = individuals + PACs + soft money) fails in", basename(json_path)))
  if (anyDuplicated(cyc)) rf_raise("PanelError", paste("duplicate cycles in", basename(json_path)))
  out <- data.frame(cycle_end = cyc, total_nominal = a[, 1], individual_nominal = a[, 2], pac_nominal = a[, 3], outside_nominal = a[, 4],
                    democrat_nominal = a[, 5], republican_nominal = a[, 6], source_url = v$url, retrieved_utc = v$retrieved_utc,
                    stringsAsFactors = FALSE)
  out[order(out$cycle_end), ]
}

# One candidate-recipient capture (chamber "H" or "S"): recipient, name_key, state, party, amount (build.py:23-32).
rf_os_candidates <- function(json_path, chamber) {
  v <- rf_read_json(json_path)
  if (length(v$source_tables) != 1L) rf_raise("PanelError", paste("expected one source table in", basename(json_path)))
  rows <- v$source_tables[[1]]$rows
  hdr <- unlist(rows[[1]])
  if (!identical(tail(hdr, 2), c("State", "Amount"))) rf_raise("PanelError", paste("unexpected recipient header in", basename(json_path)))
  m <- do.call(rbind, lapply(rows[-1], function(r) { r <- unlist(r); if (length(r) != 3L) rf_raise("PanelError", paste("recipient row is not 3 cells in", basename(json_path))); r }))
  pm <- regmatches(m[, 1], regexec("\\(([A-Z0-9]+)(?:-[A-Z]{2})?\\)$", m[, 1]))
  if (!all(lengths(pm) == 2L)) rf_raise("PanelError", paste("recipient without a party code in", basename(json_path)))
  data.frame(recipient = m[, 1], name_key = rf_name_key(m[, 1]), state = m[, 2], party = vapply(pm, `[`, "", 2L),
             amount = rf_money_int(m[, 3]), stringsAsFactors = FALSE)
}

# One presidential-recipient capture: name_key, amount (build.py:18-21).
rf_os_presidential <- function(json_path) {
  v <- rf_read_json(json_path)
  out <- lapply(v$source_tables, function(t) {
    if (!identical(unlist(t$rows[[1]]), c("Rank", "Recipient", "Chamber", "Total"))) rf_raise("PanelError", paste("unexpected presidential header in", basename(json_path)))
    m <- do.call(rbind, lapply(t$rows[-1], function(r) unlist(r)))
    if (length(m) && !all(m[, 3] == "President")) rf_raise("PanelError", paste("non-presidential row in", basename(json_path)))
    if (!length(m)) return(data.frame(name_key = character(), amount = numeric(), stringsAsFactors = FALSE))
    data.frame(name_key = rf_name_key(m[, 2]), amount = rf_money_int(m[, 4]), stringsAsFactors = FALSE)
  })
  do.call(rbind, out)
}

# All recipients of every industry with OpenSecrets recipient captures, cycles 2012-2024, both chambers.
# -> code, sector, cycle_end, chamber, recipient, name_key, state, party, amount
rf_os_recipients <- function(reg, inputs, cycles = seq(2012, 2024, 2)) {
  cm <- rf_code_map(reg)
  out <- list()
  used <- character()
  for (code in names(cm)) for (cy in cycles) for (ch in c("H", "S")) {
    role <- sprintf("os_recipients:%s:%d:%s", cm[[code]], cy, ch)
    used <- c(used, role)
    x <- rf_os_candidates(rf_input_path(inputs, role), ch)
    out[[length(out) + 1L]] <- cbind(data.frame(code = code, sector = cm[[code]], cycle_end = cy, chamber = if (ch == "H") "House" else "Senate", stringsAsFactors = FALSE), x)
  }
  rec <- do.call(rbind, out)
  if (anyDuplicated(rec[c("code", "cycle_end", "chamber", "recipient", "state")])) rf_raise("PanelError", "duplicate recipient rows")
  attr(rec, "inputs") <- rf_input_records(inputs, used)
  rec
}

rf_os_presidential_all <- function(reg, inputs, cycles = seq(2012, 2024, 2)) {
  cm <- rf_code_map(reg)
  out <- list()
  for (code in names(cm)) for (cy in cycles) {
    x <- rf_os_presidential(rf_input_path(inputs, sprintf("os_presidential:%s:%d", cm[[code]], cy)))
    if (nrow(x)) out[[length(out) + 1L]] <- cbind(data.frame(code = code, cycle_end = cy, stringsAsFactors = FALSE), x)
  }
  pre <- do.call(rbind, out)
  if (anyDuplicated(pre[c("code", "cycle_end", "name_key")])) rf_raise("PanelError", "duplicate presidential name key requires inspection")
  pre
}

# FEC candidate master rows of presidential filers for one election year: fec_id, name, party, status.
# cn.txt is pipe-delimited: id | name | party | election year | state | office | district | incumbent | status | ...
rf_fec_presidential <- function(cn_zip, cycle) {
  lines <- rf_read_zip_lines(cn_zip, "cn.txt")
  f <- strsplit(lines, "|", fixed = TRUE)
  f <- Filter(function(x) length(x) >= 9L && x[6] == "P" && suppressWarnings(as.integer(x[4])) == cycle, f)
  data.frame(cycle_end = as.integer(cycle), fec_id = vapply(f, `[`, "", 1L), name = vapply(f, `[`, "", 2L),
             party = vapply(f, `[`, "", 3L), status = vapply(f, `[`, "", 9L), stringsAsFactors = FALSE)
}

# Match major-party FEC presidential filers to congressional recipients of the same cycle by surname + first given name
# (build.py:38-66). Only statutory candidates (status C or P, or the named id) count as presidential identities.
# -> data.frame(cycle_end, name_key, fec_id, fec_name, fec_party, fec_status) and attr "unaccepted"
rf_presidential_crosswalk <- function(recipients, fec, aliases = RF_FEC_ALIASES, accept_ids = "P60006723") {
  out <- list(); unaccepted <- list()
  for (cy in sort(unique(fec$cycle_end))) {
    people <- unique(recipients[recipients$cycle_end == cy, c("name_key", "recipient", "state", "chamber")])
    keys <- people$name_key
    parts <- strsplit(keys, ",", fixed = TRUE)
    p_sur <- vapply(parts, `[`, "", 1L)
    p_giv <- vapply(parts, function(pp) if (length(pp) > 1L) strsplit(trimws(pp[2]), "\\s+")[[1]][1] else NA_character_, "")
    f <- fec[fec$cycle_end == cy & fec$party %in% c("DEM", "REP"), ]
    for (i in seq_len(nrow(f))) {
      nm <- f$name[i]
      key <- if (nm %in% names(aliases)) aliases[[nm]] else tolower(nm)
      if (!grepl(",", key, fixed = TRUE)) next
      sur <- sub(",.*$", "", key)
      giv <- strsplit(trimws(sub("^[^,]*,", "", key)), "\\s+")[[1]][1]
      hit <- people[!is.na(p_giv) & p_sur == sur & p_giv == giv, ]
      if (!nrow(hit)) next
      if (length(unique(hit$name_key)) != 1L || length(unique(hit$state)) != 1L) rf_raise("PanelError", paste("ambiguous FEC identity match for", nm))
      if (!(f$status[i] %in% c("C", "P")) && !(f$fec_id[i] %in% accept_ids)) {
        unaccepted[[length(unaccepted) + 1L]] <- data.frame(cycle_end = cy, fec_id = f$fec_id[i], fec_name = nm, fec_status = f$status[i], source_name = hit$recipient[1], stringsAsFactors = FALSE)
        next
      }
      out[[length(out) + 1L]] <- data.frame(cycle_end = cy, name_key = hit$name_key[1], fec_id = f$fec_id[i], fec_name = nm,
                                            fec_party = f$party[i], fec_status = f$status[i], stringsAsFactors = FALSE)
    }
  }
  cw <- if (length(out)) do.call(rbind, out) else data.frame(cycle_end = integer(), name_key = character(), fec_id = character(), stringsAsFactors = FALSE)
  attr(cw, "unaccepted") <- if (length(unaccepted)) do.call(rbind, unaccepted) else data.frame()
  cw
}

# Presidential-overlap flag: the recipient is on that industry's presidential list, or matches a same-cycle FEC identity.
rf_flag_presidential <- function(recipients, presidential, crosswalk) {
  in_list <- paste(recipients$code, recipients$cycle_end, recipients$name_key) %in% paste(presidential$code, presidential$cycle_end, presidential$name_key)
  in_fec <- paste(recipients$cycle_end, recipients$name_key) %in% paste(crosswalk$cycle_end, crosswalk$name_key)
  recipients$presidential_overlap <- in_list | in_fec
  recipients
}

# The congressional-account money panel (build.py:69-92): D / R / other / total per industry x cycle x chamber with
# presidential recipients removed, RShare = R / (D + R), growth columns merged from the PAC panel, log_R / log_D.
# -> code, sector, cycle_end, chamber, D_nominal, R_nominal, other_nominal, total_nominal, RShare, recipients, negative_recipients,
#    negative_amount, excluded_recipients, excluded_amount, G_prior, G_current, Pres, multiplier_to_2025_dollars,
#    cycle_revenue_growth_pct, prior_cycle_revenue_growth_pct, log_R, log_D ; attr "recipients" = the flagged recipient rows
rf_money_panel <- function(recipients, presidential, crosswalk, growth_panel) {
  rec <- rf_flag_presidential(recipients, presidential, crosswalk)
  groups <- split(rec, list(rec$code, rec$cycle_end, rec$chamber), drop = TRUE)
  agg <- lapply(groups, function(q) {
    z <- q[!q$presidential_overlap, ]
    D <- sum(z$amount[z$party == "D"]); R <- sum(z$amount[z$party == "R"])
    if (!(D > 0 && R > 0)) rf_raise("PanelError", sprintf("no D or no R money in %s %d %s", q$code[1], q$cycle_end[1], q$chamber[1]))
    other <- sum(z$amount[!(z$party %in% c("D", "R"))])
    data.frame(code = q$code[1], sector = q$sector[1], cycle_end = q$cycle_end[1], chamber = q$chamber[1], D_nominal = D, R_nominal = R,
               other_nominal = other, total_nominal = sum(z$amount), RShare = R / (D + R), recipients = nrow(z),
               negative_recipients = sum(z$amount < 0), negative_amount = sum(z$amount[z$amount < 0]),
               excluded_recipients = sum(q$presidential_overlap), excluded_amount = sum(q$amount[q$presidential_overlap]),
               stringsAsFactors = FALSE)
  })
  a <- do.call(rbind, agg)
  if (!all(a$D_nominal + a$R_nominal + a$other_nominal == a$total_nominal)) rf_raise("PanelError", "party totals do not reconcile")
  keep <- c("sector", "cycle_end", "G_prior", "G_current", "presidential_cycle", "multiplier_to_2025_dollars",
            "cycle_revenue_growth_pct", "prior_cycle_revenue_growth_pct")
  g <- growth_panel[, keep]
  names(g)[names(g) == "presidential_cycle"] <- "Pres"
  a <- merge(a, g, by = c("sector", "cycle_end"))
  a$log_R <- log(a$R_nominal * a$multiplier_to_2025_dollars)
  a$log_D <- log(a$D_nominal * a$multiplier_to_2025_dollars)
  a <- a[order(a$code, a$cycle_end, a$chamber), ]
  rownames(a) <- NULL
  attr(a, "recipients") <- rec
  a
}

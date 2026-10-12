# F12 (Paul approved 2026-09-25) in the R export: the downstream walk follows dates,
# with one-cycle carryover (option (b)). tools/arrival_gates.py is the rule; the R port
# in R/fec_transfer_graph.R must answer every case in tests/fixtures/arrival_gates_cases.json
# exactly as the Python suite (tests/test_arrival_gates.py) does from the same file.
# Each group ends with a negative control: the rule weakened the way a regression would
# weaken it, and the same fixture cases failing.
script <- sub('^--file=', '', grep('^--file=', commandArgs(FALSE), value=TRUE)[[1L]])
project <- normalizePath(file.path(dirname(script), '..'), winslash='/', mustWork=TRUE)
for (name in c('paths', 'ingest', 'collect', 'fec_beneficiary', 'fec_transfer_graph'))
  source(file.path(project, 'R', paste0(name, '.R')))
data.table::setDTthreads(2L)
cases_path <- file.path(project, 'tests', 'fixtures', 'arrival_gates_cases.json')
if (!file.exists(cases_path)) stop('The shared F12 fixture is missing: ', cases_path, call. = FALSE)
fixture <- jsonlite::fromJSON(cases_path, simplifyVector = FALSE)

# One fixture case through the R port: its saved units are the only filings there are.
run_case <- function(case, carryover = NULL, arrival = fec_arrival_network, counts = fec_gate_counts,
                     roots = case$root$committee) {
  cycle <- case$root$cycle
  previous <- fec_previous_cycle(cycle)
  units <- list()
  for (unit in case$units) units[[paste(unit$cycle, unit$committee, sep = '|')]] <- unit
  legs_in <- function(target) function(key) {
    unit <- units[[paste(target, key, sep = '|')]]
    if (is.null(unit)) return(NULL)
    rows <- unit$transfers
    list(destination = vapply(rows, function(row) row$to, character(1L)),
         when = vapply(rows, function(row) if (is.null(row$date)) '' else row$date, character(1L)),
         value = vapply(rows, function(row) round(as.numeric(row$amount) * 100), numeric(1L)))
  }
  network <- arrival(cycle, roots, legs_in(cycle), previous_legs = legs_in(previous),
    previous_saved = function(key) !is.null(units[[paste(previous, key, sep = '|')]]),
    carryover = if (is.null(carryover)) case$carryover else carryover)
  gates <- network$gates
  counted <- list()
  for (key in names(gates)) {
    unit <- units[[paste(cycle, key, sep = '|')]]
    if (is.null(unit) || !length(unit$spending)) next
    gate <- if (identical(network$gate_source[[key]], 'root')) NA_character_ else gates[[key]]
    when <- vapply(unit$spending, function(row) if (is.null(row$date)) '' else row$date, character(1L))
    value <- vapply(unit$spending, function(row) as.numeric(row$amount), numeric(1L))
    total <- sum(value[counts(when, gate)])
    if (total != 0) counted[[paste(cycle, key, sep = '|')]] <- total
  }
  list(gates = stats::setNames(lapply(names(gates), function(key) c(gates[[key]], network$gate_source[[key]])),
                               paste(cycle, names(gates), sep = '|')),
       counted = counted, status = network$carryover$status,
       missing = if (length(network$carryover$missing_units)) paste(previous, network$carryover$missing_units, sep = '|') else character())
}

# TRUE when the R port answers a case exactly as the fixture (and so Python) expects.
agrees <- function(case, ...) {
  found <- run_case(case, ...)
  expect <- case$expect
  for (key in names(expect$gates))
    if (!identical(found$gates[[key]], unlist(expect$gates[[key]]))) return(FALSE)
  if (any(unlist(expect$not_reached) %in% names(found$gates))) return(FALSE)
  want <- vapply(expect$counted_spending, as.numeric, numeric(1L))
  got <- unlist(found$counted)
  if (!setequal(names(want), names(got)) || any(abs(got[names(want)] - want) > 1e-9)) return(FALSE)
  identical(found$status, expect$carryover_status) && identical(found$missing, as.character(unlist(expect$missing_units)))
}

names_of <- vapply(fixture$cases, function(case) case$name, character(1L))
results <- vapply(fixture$cases, agrees, logical(1L))
if (!all(results)) stop('R port disagrees with the shared fixture on: ', paste(names_of[!results], collapse = ', '))
stopifnot(length(results) >= 14L)
cat(sprintf('Shared fixture: %d of %d cases agree with tools/arrival_gates.py PASS\n', sum(results), length(results)))

by_name <- function(name) fixture$cases[[match(name, names_of)]]
# Negative control 1: a walk blind to dates opens everything its money ever touched.
blind <- function(cycle, pinned, legs, ...) {
  undated <- function(key) { found <- legs(key); if (!is.null(found)) found$when <- rep('', length(found$when)); found }
  fec_arrival_network(cycle, pinned, undated, ...)
}
for (name in c('date_impossible_branch_is_left_out', 'a_chain_in_date_order_is_unchanged'))
  stopifnot(!agrees(by_name(name), arrival = blind))
# Negative control 2: without carryover the previous cycle opens nothing.
for (name in c('carryover_opens_a_gate', 'a_carried_over_committee_needs_no_new_money'))
  stopifnot(!agrees(by_name(name), carryover = 'off'))
# Negative control 3: the direct variant stops at the root's own recipients.
stopifnot(!agrees(by_name('onward_previous_cycle_transfers_open_gates'), carryover = 'direct'),
          agrees(by_name('direct_variant_opens_only_the_roots_own_recipients')))
# Negative control 4: a rule that drops an undated row instead of counting it (D-040).
dropping <- function(when, gate) is.na(gate) | (nzchar(when) & fec_day(when) >= fec_day(gate))
stopifnot(agrees(by_name('an_undated_row_counts')), !agrees(by_name('an_undated_row_counts'), counts = dropping))
# Negative control 5 (F15): company B's view walked from both companies' roots - the
# industry-wide gates - dates the shared committee from company A's earlier money.
company_b <- by_name('company_view_second_company_counts_the_shared_committee_from_its_own_arrival')
stopifnot(agrees(company_b), agrees(by_name('company_view_first_company_opens_the_shared_committee')),
          !agrees(company_b, roots = c('C90000012', 'C90000011')))
cat('Negative controls: date-blind walk, carryover off, direct variant, undated rows dropped, industry gates for a company PASS\n')

# The export's walk on a published index: the date-impossible branch from the fixture,
# written as tools/fec_transfer_graph.py publishes it. Edges out of a committee count
# only from its gate on; the universe carries each reached committee's gate.
check_export_walk <- function() {
  directory <- tempfile('mentat-arrival-gates-')
  dir.create(file.path(directory, 'oth24'), recursive = TRUE)
  on.exit(unlink(directory, recursive = TRUE))
  record <- function(date, cents, sub) sprintf(
    '{"file_num":1,"transaction_id":"T%s","sub_id":"%s","date":"%s","cents":%d,"type":"24G"}', sub, sub, date, cents)
  edges <- data.table::data.table(cycle = 2024L,
    source = c('C90000001', 'C90000005', 'C90000005'),
    destination = c('C90000005', 'C90000006', 'C90000007'),
    amount = c(895000, 41727.99, 100),
    record_count = c(1L, 1L, 2L),
    first_date = c('2023-12-04', '2023-02-08', '2023-12-01'),
    last_date = c('2023-12-04', '2023-02-08', '2024-01-05'),
    records = c(paste0('[', record('2023-12-04', 89500000L, '1'), ']'),
                paste0('[', record('2023-02-08', 4172799L, '2'), ']'),
                paste0('[', record('2023-12-01', 4000L, '3'), ',', record('2024-01-05', 6000L, '4'), ']')))
  path <- file.path(directory, 'oth24', 'transfer-edges.csv')
  data.table::fwrite(edges, path)
  jsonlite::write_json(list(version = 6L, ambiguous_version_policy = 'count_all',
    edges_sha256 = digest::digest(file = path, algo = 'sha256'), counts = list()),
    file.path(directory, 'oth24', 'transfer-info.json'), auto_unbox = TRUE)
  roots <- data.table::data.table(cycle = 2024L, committee_id = 'C90000001', committee_name = 'ROOT',
    recipient_category = 'Super PAC', preliminary_crypto_funding = 1, traversal_seed = TRUE)
  links <- data.table::data.table(cycle = integer(), committee_id = character(), designation = character())
  context <- fec_transfer_context(directory, roots, links, 2024L)
  rule <- context$coverage[[1L]]$date_rule
  stopifnot(identical(context$coverage[[1L]]$status, 'indexed'),
            identical(rule$carryover_status, 'unknown'), identical(rule$previous_index, 'missing_transfer_source'),
            # C90000006 is never reached: its only transfer predates C90000005's gate.
            setequal(context$universe$committee_id, c('C90000001', 'C90000005', 'C90000007')),
            identical(context$universe[committee_id == 'C90000005', arrival_gate], '2023-12-04'),
            identical(context$universe[committee_id == 'C90000005', gate_source], 'in-cycle'),
            identical(context$universe[committee_id == 'C90000001', gate_source], 'root'),
            # The C90000005 -> C90000007 edge keeps only its record dated on or after the gate.
            identical(context$edges$destination, c('C90000005', 'C90000007')),
            isTRUE(all.equal(context$edges[destination == 'C90000007', amount], 60)),
            identical(context$edges[destination == 'C90000007', record_count], 1L),
            identical(as.character(context$edges[destination == 'C90000007', first_date]), '2024-01-05'),
            identical(as.character(context$edges[destination == 'C90000007', last_date]), '2024-01-05'),
            identical(jsonlite::fromJSON(context$edges[destination == 'C90000007', records])$sub_id, '4'),
            rule$excluded_before_gate_rows == 2L, isTRUE(all.equal(rule$excluded_before_gate_amount, 41767.99)),
            isTRUE(rule$walk_matches_gates),
            # F14: each edge names its source's gate and what the date rule left out of it.
            identical(context$edges[destination == 'C90000007', c(source_gate, source_gate_source)],
                      c('2023-12-04', 'in-cycle')),
            identical(context$edges[destination == 'C90000007', excluded_before_gate_records], 1L),
            isTRUE(all.equal(context$edges[destination == 'C90000007', excluded_before_gate_amount], 40)),
            identical(context$edges[destination == 'C90000005', c(source_gate, source_gate_source, source_carryover_route)],
                      c('2023-01-01', 'root', '')),
            identical(context$edges[destination == 'C90000005', excluded_before_gate_records], 0L))
  # Negative control: the undated walk this replaces reaches C90000006 and counts all of it.
  undated <- data.table::copy(edges)[, records := NA_character_]
  data.table::fwrite(undated, path)
  jsonlite::write_json(list(version = 6L, ambiguous_version_policy = 'count_all',
    edges_sha256 = digest::digest(file = path, algo = 'sha256'), counts = list()),
    file.path(directory, 'oth24', 'transfer-info.json'), auto_unbox = TRUE)
  blind <- fec_transfer_context(directory, roots, links, 2024L)
  stopifnot('C90000006' %in% blind$universe$committee_id, isTRUE(all.equal(sum(blind$edges$amount), 936827.99)))
}
check_export_walk()
cat('Export walk: gated edges, reached committees and their gates, undated fallback PASS\n')

# Downstream activity: a committee reached by transfer counts from its gate on; an undated
# row counts and is flagged; the funded recipients themselves count in full.
check_activity_filter <- function() {
  directory <- tempfile('mentat-arrival-activity-')
  dir.create(file.path(directory, 'pas224'), recursive = TRUE)
  on.exit(unlink(directory, recursive = TRUE))
  row <- function(committee, date, amount, sub) {
    fields <- rep('', 22L)
    fields[c(1,2,3,6,8,14,15,17,18,19,22)] <- c(committee, 'N', 'Q1', '24E', 'VENDOR', date, amount,
                                                  'H00000001', paste0('SE.', sub), '1', sub)
    paste(fields, collapse = '|')
  }
  writeLines(c(row('C90000001', '01152023', '500', '1'), row('C90000002', '02012024', '7', '2'),
               row('C90000002', '04102024', '30', '3'), row('C90000002', '', '11', '4')),
             file.path(directory, 'pas224', 'itpas2.txt'))
  universe <- data.table::data.table(cycle = 2024L, committee_id = c('C90000001', 'C90000002'),
    committee_name = c('ROOT', 'B'), recipient_category = c('Super PAC', ''), preliminary_crypto_funding = c(1, 0),
    arrival_gate = c('2023-01-01', '2024-03-01'), gate_source = c('root', 'in-cycle'))
  master <- data.table::data.table(cycle = 2024L, candidate_id = 'H00000001', candidate_name = 'CANDIDATE',
    candidate_party = 'DEM', office = 'H', office_label = 'House', office_state = 'KY', district = '06',
    source_url = 'fixture')
  activity <- fec_read_downstream_activity(directory, universe, master, 2024L)
  rule <- attr(activity, 'date_rule')
  stopifnot(setequal(activity$sub_id, c('1', '3', '4')),
            identical(activity[sub_id == '4', date_unverified], 1L),
            identical(activity[sub_id == '1', date_unverified], 0L),
            rule$rows_before_gate == 1L, rule$amount_before_gate == 7, rule$rows_date_unverified == 1L)
  aggregated <- fec_aggregate_downstream_activity(activity, universe)
  stopifnot(identical(aggregated[source_committee_id == 'C90000002', date_unverified_rows], 1L))
  # Negative control: a universe without gates (the undated walk) counts every row.
  plain <- fec_read_downstream_activity(directory, universe[, .(cycle, committee_id, committee_name,
    recipient_category, preliminary_crypto_funding)], master, 2024L)
  stopifnot(nrow(plain) == 4L, is.null(attr(plain, 'date_rule')))
}
check_activity_filter()
cat('Downstream activity: gated from the arrival date, undated rows flagged PASS\n')

# F15 (Paul approved 2026-09-26) through the export: the fixture's two companies on a
# published index (2024, and 2022 for carryover), with PAS2 spending. Each company view
# is walked and dated from its own roots; the industry view keeps the industry-wide
# walk; a committee the roots' previous-cycle money opened is in every view that
# opened it, with its route.
check_company_views <- function() {
  directory <- tempfile('mentat-company-views-')
  on.exit(unlink(directory, recursive = TRUE))
  record <- function(date, cents, sub) sprintf(
    '{"file_num":1,"transaction_id":"T%s","sub_id":"%s","date":"%s","cents":%d,"type":"24G"}', sub, sub, date, cents)
  publish <- function(folder, cycle, source, destination, date, cents, sub) {
    dir.create(file.path(directory, folder), recursive = TRUE, showWarnings = FALSE)
    edges <- data.table::data.table(cycle = cycle, source = source, destination = destination, amount = cents / 100,
      record_count = 1L, first_date = date, last_date = date,
      records = vapply(seq_along(source), function(i) paste0('[', record(date[[i]], cents[[i]], sub[[i]]), ']'), ''))
    path <- file.path(directory, folder, 'transfer-edges.csv')
    data.table::fwrite(edges, path)
    jsonlite::write_json(list(version = 6L, ambiguous_version_policy = 'count_all',
      edges_sha256 = digest::digest(file = path, algo = 'sha256'), counts = list()),
      file.path(directory, folder, 'transfer-info.json'), auto_unbox = TRUE)
  }
  # A = C90000011 (Alpha's PAC), B = C90000012 (Beta's PAC), S = C90000013 shared,
  # T = C90000014 beyond S, X = C90000015 opened by A's 2022 money, Y = C90000016 beyond X.
  publish('oth24', 2024L, c('C90000011', 'C90000012', 'C90000013', 'C90000015'),
          c('C90000013', 'C90000013', 'C90000014', 'C90000016'),
          c('2023-02-01', '2024-04-01', '2023-06-01', '2023-03-01'), c(100000L, 50000L, 30000L, 20000L), c('1', '2', '3', '4'))
  publish('oth22', 2022L, 'C90000011', 'C90000015', '2022-05-01', 10000L, '5')
  # The previous cycle's candidate-committee list (none of these committees is one).
  dir.create(file.path(directory, 'ccl22'))
  writeLines('H0XX00009|2022|2022|C90000099|H|P|1', file.path(directory, 'ccl22', 'ccl.txt'))
  dir.create(file.path(directory, 'pas224'), recursive = TRUE)
  row <- function(committee, date, amount, sub) {
    fields <- rep('', 22L)
    fields[c(1,2,3,6,8,14,15,17,18,19,22)] <- c(committee, 'N', 'Q1', '24E', 'VENDOR', date, amount,
                                                  'H00000001', paste0('SE.', sub), '1', sub)
    paste(fields, collapse = '|')
  }
  writeLines(c(row('C90000013', '03012023', '100', '11'), row('C90000013', '05012024', '50', '12'),
               row('C90000014', '07012023', '70', '13'), row('C90000015', '04012023', '40', '14'),
               row('C90000016', '05012023', '25', '15')),
             file.path(directory, 'pas224', 'itpas2.txt'))
  raw_export <- data.table::data.table(cycle = 2024L, company = c('Alpha', 'Beta'),
    committee_id = c('C90000011', 'C90000012'), recipient = c('ALPHA PAC', 'BETA PAC'),
    amount = c(1000, 500), include_in_analysis = 'Yes')
  universe <- data.table::data.table(cycle = 2024L, committee_id = c('C90000011', 'C90000012'),
    committee_name = c('ALPHA PAC', 'BETA PAC'), recipient_category = 'Company PAC',
    preliminary_crypto_funding = c(1000, 500), traversal_seed = TRUE)
  views <- raw_export[, .(cycle, view = 'Company', company, committee_id)]
  links <- data.table::data.table(cycle = integer(), committee_id = character(), candidate_id = character(),
    designation = character())
  master <- data.table::data.table(cycle = 2024L, candidate_id = 'H00000001', candidate_name = 'CANDIDATE',
    candidate_party = 'DEM', office = 'H', office_label = 'House', office_state = 'KY', district = '06',
    source_url = 'fixture')
  context <- fec_transfer_context(directory, universe, links, 2024L, views = views)
  activity <- fec_read_downstream_activity(directory, context$universe, master, 2024L)
  tables <- fec_build_beneficiary_tables(raw_export, activity, context$edges, links,
    networks = context$networks, industry_universe = context$universe)
  spent <- function(result, view_name, company_name) {
    found <- result$bridge_all[view == view_name & company == company_name,
      .(amount = sum(preliminary_downstream_amount)), by = source_committee_id]
    stats::setNames(found$amount, found$source_committee_id)[order(found$source_committee_id)]
  }
  reached <- function(result, view_name, company_name)
    sort(result$reach[view == view_name & company == company_name, source_committee_id])
  gate <- function(company_name, key) context$networks$gates[company == company_name & committee_id == key, arrival_gate]
  stopifnot(
    # The industry view: the industry-wide walk, as before, now with its carried-over committees.
    identical(spent(tables, 'Industry', 'Industry Total'),
              c(C90000013 = 150, C90000014 = 70, C90000015 = 40, C90000016 = 25)),
    identical(reached(tables, 'Industry', 'Industry Total'),
              c('C90000011', 'C90000012', 'C90000013', 'C90000014', 'C90000015', 'C90000016')),
    # Alpha's money reaches S first: its view counts S from 2023-02-01, T, and X/Y via carryover.
    identical(spent(tables, 'Company', 'Alpha'), c(C90000013 = 150, C90000014 = 70, C90000015 = 40, C90000016 = 25)),
    identical(gate('Alpha', 'C90000013'), '2023-02-01'),
    # Beta's money reaches S only on 2024-04-01: S counts from then, and T and X are not Beta's.
    identical(spent(tables, 'Company', 'Beta'), c(C90000013 = 50)),
    identical(reached(tables, 'Company', 'Beta'), c('C90000012', 'C90000013')),
    identical(gate('Beta', 'C90000013'), '2024-04-01'),
    identical(tables$reach[view == 'Company' & company == 'Beta' & source_committee_id == 'C90000013', gate_source], 'in-cycle'),
    # The carried-over committee's route names the previous cycle that opened it.
    identical(tables$reach[view == 'Industry' & source_committee_id == 'C90000015', representative_path],
              'C90000011 > C90000015 [gate opened by 2021-2022 transfers]'),
    identical(tables$reach[view == 'Industry' & source_committee_id == 'C90000016', representative_path],
              'C90000011 > C90000015 [gate opened by 2021-2022 transfers] > C90000016'),
    identical(tables$reach[view == 'Industry' & source_committee_id == 'C90000015', c(representative_root, gate_source)],
              c('C90000011', 'carryover')),
    # Every view's census is named.
    setequal(vapply(context$networks$census, function(entry) entry$company, ''), c('Industry Total', 'Alpha', 'Beta')),
    # F14: the carried-over source's published edge carries the route that opened it.
    identical(context$edges[source == 'C90000015', c(source_gate, source_gate_source, source_carryover_route)],
              c('2023-01-01', 'carryover', 'C90000011 > C90000015 [gate opened by 2021-2022 transfers]')))
  # Negative control: without the per-view walks (the F12 export), Beta's view follows the
  # industry's gated edges - S counted in full from Alpha's arrival, T reached - and no view
  # reaches the carried-over committees.
  industry_only <- fec_build_beneficiary_tables(raw_export, activity, context$edges, links)
  stopifnot(identical(spent(industry_only, 'Company', 'Beta'), c(C90000013 = 150, C90000014 = 70)),
            !'C90000015' %in% reached(industry_only, 'Industry', 'Industry Total'))
}
check_company_views()
cat('Company views: each dated from its own roots, industry view unchanged, carried-over committees reached PASS\n')

# The previous-cycle (carryover) walk stops at the previous cycle's candidate committees,
# whatever cycles the build itself covers. Here the build covers 2024 only, so its
# candidate_links hold no 2022 rows; the 2022 list is read from the ccl22 file.
check_carryover_stops_at_candidates <- function() {
  directory <- tempfile('mentat-carryover-stops-')
  on.exit(unlink(directory, recursive = TRUE))
  record <- function(date, cents, sub) sprintf(
    '{"file_num":1,"transaction_id":"T%s","sub_id":"%s","date":"%s","cents":%d,"type":"24G"}', sub, sub, date, cents)
  publish <- function(folder, cycle, source, destination, date, cents) {
    dir.create(file.path(directory, folder), recursive = TRUE, showWarnings = FALSE)
    edges <- data.table::data.table(cycle = cycle, source = source, destination = destination, amount = cents / 100,
      record_count = 1L, first_date = date, last_date = date,
      records = vapply(seq_along(source), function(i) paste0('[', record(date[[i]], cents[[i]], as.character(i)), ']'), ''))
    path <- file.path(directory, folder, 'transfer-edges.csv')
    data.table::fwrite(edges, path)
    jsonlite::write_json(list(version = 6L, ambiguous_version_policy = 'count_all',
      edges_sha256 = digest::digest(file = path, algo = 'sha256'), counts = list()),
      file.path(directory, folder, 'transfer-info.json'), auto_unbox = TRUE)
  }
  # 2022: root A pays candidate committee K, which pays Z; A also pays M. 2024: Z pays W, M pays N.
  publish('oth22', 2022L, c('C90000041', 'C90000042', 'C90000041'), c('C90000042', 'C90000043', 'C90000045'),
          c('2022-03-01', '2022-04-01', '2022-05-01'), c(10000L, 5000L, 7000L))
  publish('oth24', 2024L, c('C90000043', 'C90000045'), c('C90000044', 'C90000046'),
          c('2023-05-01', '2023-06-01'), c(3000L, 4000L))
  dir.create(file.path(directory, 'ccl22'))
  writeLines('H0XX00041|2022|2022|C90000042|H|P|1', file.path(directory, 'ccl22', 'ccl.txt'))
  roots <- data.table::data.table(cycle = 2024L, committee_id = 'C90000041', committee_name = 'ROOT',
    recipient_category = 'Super PAC', preliminary_crypto_funding = 1, traversal_seed = TRUE)
  links <- data.table::data.table(cycle = 2024L, committee_id = character(), candidate_id = character(),
    designation = character())
  carried <- function(context) sort(context$universe[gate_source == 'carryover', committee_id])
  fixed <- fec_transfer_context(directory, roots, links, 2024L)
  stopifnot(identical(carried(fixed), 'C90000045'),
            !any(c('C90000043', 'C90000044') %in% fixed$universe$committee_id),
            'C90000046' %in% fixed$universe$committee_id,
            identical(fixed$coverage[[1L]]$date_rule$carryover_status, 'known'))
  # No candidate list for the previous cycle anywhere: the carryover is unknown, not widened.
  unlink(file.path(directory, 'ccl22'), recursive = TRUE)
  blind <- fec_transfer_context(directory, roots, links, 2024L)
  stopifnot(identical(blind$coverage[[1L]]$date_rule$carryover_status, 'unknown'),
            identical(blind$coverage[[1L]]$date_rule$previous_index, 'missing_candidate_links'),
            !length(carried(blind)))
  # Negative control: a previous-cycle list that misses K (the defect: no stops) walks through
  # the candidate committee and carries Z over.
  leaky <- data.table::data.table(cycle = c(2024L, 2022L), committee_id = c(NA, 'C90000099'),
    candidate_id = c(NA, 'H0XX00099'), designation = c(NA, 'P'))[!is.na(committee_id)]
  through <- fec_transfer_context(directory, roots, leaky, 2024L)
  stopifnot(identical(carried(through), c('C90000042', 'C90000043', 'C90000045')),
            'C90000044' %in% through$universe$committee_id)
}
check_carryover_stops_at_candidates()
cat('Carryover: the previous-cycle walk stops at that cycle\'s candidate committees; no list means unknown PASS\n')

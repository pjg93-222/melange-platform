"""Read the complete saved committee network, without attributing pooled dollars.

Each committee/cycle is selected once, from its newest complete saved unit.
Traversal is cycle-safe and uncapped; absent destination snapshots are disclosed.
A set of committee/cycles is always read in one stable order (_stable), never in the
process's string-hash order, so every console gives the same answer.
"""
from collections import ChainMap, OrderedDict, defaultdict
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from functools import lru_cache
import json
import hashlib
from pathlib import Path
import re
import sys
import threading

import drawer_sidecars

# F8 (Paul, 2026-09-25): "one rule, by transaction type, everywhere". What a
# Schedule B row IS - a transfer, a candidate payment or neither - is decided by
# tools/transfer_rule.py, the rule the collector and the map also use, with the
# transfer index's own transaction-type set.
_TOOLS = str(Path(__file__).resolve().parents[1] / 'tools')
if _TOOLS not in sys.path:
    sys.path.append(_TOOLS)
import transfer_rule  # noqa: E402
# F12 (Paul approved 2026-09-25): the walk follows dates - earliest arrival,
# tools/arrival_gates.py. network() gives each reached committee/cycle a gate,
# and activity(), observations(gate=) and the Spent map count only its rows
# dated on or after that gate (undated rows count, flagged date_unverified).
import arrival_gates  # noqa: E402

schedule_b_leg = transfer_rule.schedule_b_leg

ROOT = Path(__file__).resolve().parents[1] / '.cache/pac-evidence'
NOTE = ('Saved downstream committee network, counted once per committee/cycle. '
        'Includes spending by recipient PACs, not dollars attributed to the starting PAC or its donors. '
        'Coverage is limited to saved evidence; committee coverage windows may differ, and missing destination snapshots remain unresolved.')

def memo(row):
    return str(row.get('memoed_subtotal') or '').strip().upper() in ('1','TRUE') or str(row.get('memo_code') or '').strip().upper() == 'X'


def filing_record(row):
    """Carry the saved transaction's citation into views, without fetching it."""
    image = str(row.get('image_number') or '')
    value = next((row[key] for key in ('contribution_receipt_amount', 'expenditure_amount',
                                      'disbursement_amount', 'amount') if row.get(key) is not None), None)
    return {
        'source_url': row.get('source_url') or row.get('pdf_url') or (
            'https://docquery.fec.gov/cgi-bin/fecimg/?' + image if image.isdigit() else ''),
        'sub_id': str(row.get('sub_id') or ''),
        'transaction_id': str(row.get('transaction_id') or ''),
        'amount': float(amount(value)) if value is not None else None,
        'date': str(row.get('contribution_receipt_date') or row.get('expenditure_date')
                    or row.get('disbursement_date') or row.get('date') or '')[:10],
        'counterparty': row.get('contributor_name') or row.get('candidate_name') or row.get('recipient_name') or '',
        'city': row.get('contributor_city') or '',
        'state': row.get('contributor_state') or '',
        'zip': row.get('contributor_zip') or '',
        'employer': row.get('contributor_employer') or '',
        'purpose': row.get('expenditure_description') or row.get('disbursement_description') or '',
    }

def observations(unit, schedule, census=None, gate=None):
    """One observation per source ID; equal dollars never identify a duplicate.

    Notices are an alternative disclosure, excluded only when explicitly marked.
    Legacy rows without IDs/notice metadata remain provisional observations.
    F11 (Paul, 2026-09-25): a reload - a row sharing an earlier row's committee,
    report type, transaction id, file number, counterparty, amount, date and
    type under another SUB_ID - counts once (transfer_rule.count_reloads_once).
    `census`, from transfer_rule.new_reload_census(), counts what that rule did.
    F12: with a `gate` ('YYYY-MM-DD', from gate_of), a row dated before it is
    left out - the network's money had not reached this committee yet - and a
    row with no usable date stays in (D-040), for the caller to flag.
    """
    if gate is not None and schedule in arrival_gates.DATE_FIELDS:
        yield from (row for row in observations(unit, schedule, census)
                    if arrival_gates.counts(arrival_gates.row_date(row, schedule), gate))
        return
    def unique():
        seen = {}
        for row in unit.get(schedule) or []:
            if not isinstance(row, dict):
                raise ValueError('Invalid saved downstream observation.')
            identifier = str(row.get('sub_id') or '').strip()
            if identifier and identifier in seen:
                if row != seen[identifier]:
                    raise ValueError('Conflicting saved downstream facts for one source ID.')
                continue
            if identifier:
                seen[identifier] = row
            if schedule == 'expenditures' and str(row.get('is_notice') or '').strip().lower() in ('true','1'):
                continue
            yield row
    if schedule not in transfer_rule.RELOAD_FIELDS:
        yield from unique()
        return
    yield from transfer_rule.count_reloads_once(unique(), schedule, str(unit.get('committee_id') or ''), census)

def amount(value):
    try:
        result = Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError('Missing or invalid amount in saved downstream evidence; no zero is inferred.') from error
    if not result.is_finite() or result * 100 != (result * 100).to_integral_value():
        raise ValueError('Invalid monetary amount in saved downstream evidence.')
    return result

def inventory(root=None):
    files = sorted(Path(root or ROOT).glob('*/snapshots.json'))
    return _inventory(tuple((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in files))


def _inventory(stamps):
    """The saved units _read_inventory returns, as a mapping that reads each unit when it is first
    used (perf.md H8: that read parsed all 312 MB of snapshots, about 1.4 GB of objects at the peak
    and 700 MB kept, for the first drawer of every process, whichever committee it opened).

    The selection is _read_inventory's, made from each file's index (drawer_sidecars): the same
    validation, the same newest-coverage priority, the same order, and each file still hashed in
    full once per process. A unit is its own bytes, hashed against the index and parsed, plus the
    same _source and _sha256. A snapshot set the index cannot describe exactly (a unit that fails
    validation, one committee/cycle twice in one file, anything but plain ASCII JSON) is read by
    _read_inventory itself, which answers or raises exactly as it always has."""
    selection = _selection(stamps)
    if selection is None:
        return _read_inventory(stamps)
    return SavedUnits(stamps, *selection)


class SavedUnits(Mapping):
    """_read_inventory's dict, read lazily. get, [] and `in` read one unit; iterating (dict(...),
    items(), {**...}) reads them all and keeps them for the process, as the old dict was kept."""

    def __init__(self, stamps, entries, kept):
        self._stamps, self._entries, self._kept, self._read, self._whole = stamps, entries, kept, {}, False

    def __getitem__(self, key):
        location = self._entries[key]
        unit = self._read.get(key)
        if unit is None:
            unit = self._kept.get(key)
            if unit is None:
                path, modified, size, offset, length, digest, fingerprint = location
                try:
                    saved = drawer_sidecars.read_unit(path, modified, size, offset, length, digest)
                except ValueError:
                    _forget(self._stamps)
                    raise
                unit = {**saved, '_source': path, '_sha256': fingerprint}
                if self._whole:
                    self._kept[key] = unit
            self._read[key] = unit
        return unit

    def __iter__(self):
        self._whole = True
        return iter(self._entries)

    def __len__(self):
        return len(self._entries)

    def __contains__(self, key):
        return key in self._entries

    def __repr__(self):
        return f'SavedUnits({len(self._entries)} committee/cycles from {len(self._stamps)} snapshots)'


_SELECTIONS = OrderedDict()   # stamps -> (entries, kept) or None; bounded like the old lru_cache(4)
_SELECTION_LIMIT = 4
_selection_guard = threading.Lock()
_selection_locks = {}


def _selection(stamps):
    """(entries, kept) for these stamps, or None when _read_inventory must read them. Single flight:
    one selection per stamps at a time (DE-026: lru_cache is not single-flight)."""
    with _selection_guard:
        if stamps in _SELECTIONS:
            _SELECTIONS.move_to_end(stamps)
            return _SELECTIONS[stamps]
        lock = _selection_locks.setdefault(stamps, threading.Lock())
    with lock:
        with _selection_guard:
            if stamps in _SELECTIONS:
                return _SELECTIONS[stamps]
        try:
            entries = _select(stamps)
        except Exception as error:  # noqa: BLE001
            # A file moved or cannot be read (or the index failed): _read_inventory reads the
            # files and answers or raises exactly as before. Not remembered: the next call retries.
            if not isinstance(error, (OSError, ValueError)):
                drawer_sidecars._emit('mentat.drawer.sidecar', 'failed',
                                      f'Snapshot index skipped: {type(error).__name__}: {error}', stage='inventory')
            with _selection_guard:
                _selection_locks.pop(stamps, None)
            return None
        value = None if entries is None else (entries, {})
        with _selection_guard:
            _SELECTIONS[stamps] = value
            while len(_SELECTIONS) > _SELECTION_LIMIT:
                _SELECTIONS.popitem(last=False)
            _selection_locks.pop(stamps, None)
        return value


def _select(stamps):
    """_read_inventory's selection, from the index of each file: key -> where its unit is. None
    when any unit would fail _read_inventory's checks, or repeats in its file (compared whole
    there); _read_inventory then raises or answers itself."""
    selected = {}
    for path, modified, size in stamps:
        try:
            fingerprint, units = drawer_sidecars.snapshot_index(path, modified, size)
        except drawer_sidecars.Unindexable:
            return None
        keys = set()
        for unit in units:
            kinds = unit['kinds']
            if not all(kinds.get(field) == 'list' for field in ('receipts','disbursements','expenditures')):
                return None
            totals = unit.get('totals')
            if kinds.get('totals') != 'dict' or not totals or not totals['disbursements']:
                return None
            if 'cycle' not in unit or kinds.get('committee_id') != 'str':
                return None
            key = (str(unit['cycle']), unit['committee_id'])
            if key in keys:
                return None
            keys.add(key)
            priority = (str(totals['coverage_end_date'] or ''), modified, path)
            if key not in selected or priority > selected[key][0]:
                selected[key] = (priority, (path, modified, size, unit['offset'], unit['length'],
                                            unit['sha256'], fingerprint))
    return {key: value[1] for key, value in selected.items()}


def _forget(stamps):
    with _selection_guard:
        _SELECTIONS.pop(stamps, None)


def cache_clear():
    """Forget every selection, kept unit and snapshot index this process holds."""
    with _selection_guard:
        _SELECTIONS.clear()
    _read_inventory.cache_clear()
    drawer_sidecars.forget()


def index_inventory(root=None):
    """Build or verify the snapshot indexes the drawer reads, without reading a unit (for the
    prewarm harness). Returns {'files', 'indexed', 'units'}."""
    files = sorted(Path(root or ROOT).glob('*/snapshots.json'))
    stamps = tuple((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in files)
    selection = _selection(stamps)
    return {'files': len(stamps), 'indexed': selection is not None,
            'units': len(selection[0]) if selection is not None else None}


@lru_cache(maxsize=4)
def _read_inventory(stamps):
    selected = {}
    for path, modified, size in stamps:
        raw = Path(path).read_bytes()
        units = json.loads(raw)
        fingerprint = hashlib.sha256(raw).hexdigest()
        if not isinstance(units, list):
            raise ValueError('Saved downstream snapshot must contain committee units.')
        file_units = {}
        for unit in units:
            if not all(isinstance(unit.get(field), list) for field in ('receipts','disbursements','expenditures')):
                raise ValueError('Saved downstream unit is missing a complete schedule array.')
            if not isinstance(unit.get('totals'), dict) or 'disbursements' not in unit['totals']:
                raise ValueError('Saved downstream unit is missing its reported disbursements.')
            key = (str(unit['cycle']), unit['committee_id'])
            if key in file_units and file_units[key] != unit:
                raise ValueError('Conflicting committee/cycle units inside one saved snapshot.')
            file_units[key] = unit
            priority = (str((unit.get('totals') or {}).get('coverage_end_date') or ''), modified, path)
            if key not in selected or priority > selected[key][0]:
                selected[key] = (priority, {**unit, '_source': path, '_sha256':fingerprint})
        current = Path(path).stat()
        if (current.st_mtime_ns, current.st_size) != (modified, size):
            raise ValueError('Saved downstream evidence changed during the read. Refresh.')
    return {key: value[1] for key, value in selected.items()}

def transfers(unit, gate=None):
    """(destination, amount, row) for each row that sends money to another committee.

    Transfers (followed) and candidate payments (terminal) alike, as
    transfer_rule.schedule_b_leg names them; network() keeps them apart by the
    row's candidate id. A row with no recipient committee id has no destination.
    F12: with a `gate`, only the rows observations(gate=) keeps.
    """
    for row in observations(unit, 'disbursements', gate=gate):
        destination = str(row.get('recipient_committee_id') or '')
        if destination and schedule_b_leg(row)[0]:
            yield destination, amount(row.get('disbursement_amount')), row

def _stable(key):
    """The one order for (cycle, committee) keys (Paul, 2026-09-24: "Deterministic"): ascending
    by each part's text, then by its type, so mixed keys neither raise nor tie. A set's own
    iteration order follows the string-hash seed (PYTHONHASHSEED) and differs per process."""
    return tuple(str(part) for part in key), tuple(type(part).__name__ for part in key)


def gate_of(found, key):
    """F12: the date a network's money first reached `key`, or None for a root (all of a
    root's activity counts) and for a network built without gates (count everything)."""
    if key in (found.get('roots') or ()):
        return None
    return (found.get('gates') or {}).get(key)


def network(roots, units=None, report_edges=(), carryover='onward'):
    units = inventory() if units is None else units
    if carryover not in arrival_gates.CARRYOVER_MODES:
        raise ValueError(f'Unknown carryover rule {carryover!r}.')
    visited, missing, edges = set(), set(), []
    # A set of roots (graph_data passes one) is walked in _stable order, smallest first (the
    # stack pops from its end), not in its per-process hash order: that order decides the
    # order of `edges`. An ordered collection (a list, snapshot_view's dict) keeps its own.
    pending = (sorted(roots, key=_stable, reverse=True) if isinstance(roots, (set, frozenset))
               else list(roots))
    fallback = defaultdict(dict)
    for edge in report_edges:
        key = (str(edge['cycle']), edge['source'])
        destination = edge['destination']
        value = amount(edge['amount'])
        previous = fallback[key].get(destination)
        if previous is not None and previous != value:
            raise ValueError('Saved reports disagree on a downstream transfer; select one report vintage.')
        fallback[key][destination] = value
    # F12: earliest arrival first - every reachable committee/cycle's gate, the date the
    # roots' money could first have reached it - then the walk below, which follows and
    # counts only transfers dated on or after their source's gate. Each unit is read once.
    pinned = {key: arrival_gates.cycle_start(key[0]) for key in pending}
    read = {}

    def unit_of(key):
        if key not in read:
            read[key] = units.get(key)
        return read[key]

    def legs(key):
        unit = unit_of(key)
        if unit is None:
            # A workbook's saved route carries no transaction dates: undated, it
            # passes at its source's own gate (D-040).
            return [((key[0], destination), '', value) for destination, value in fallback[key].items()]
        return [((key[0], destination), arrival_gates.row_date(row, 'disbursements'), value)
                for destination, value, row in transfers(unit)
                if schedule_b_leg(row)[0] != transfer_rule.CANDIDATE]

    # Carryover (Paul approved option (b), 2026-09-25): the roots' transfers in the cycle
    # before open their recipients' gates at this cycle's first day - onward along
    # time-respecting C-1 transfers by default. A C-1 transfer only opens a gate; it is
    # disclosed in `carryover`, never counted as a flow. One cycle back only.
    seeds, carried, statuses = {}, {'rule': carryover, 'opened': [], 'missing_units': []}, []
    for cycle in (sorted({key[0] for key in pinned}) if carryover != 'off' else ()):
        previous = arrival_gates.previous_cycle(cycle)
        earlier = {(previous, key[1]): arrival_gates.cycle_start(previous) for key in pinned if key[0] == cycle}
        known = {key: gate for key, gate in earlier.items() if unit_of(key) is not None or fallback[key]}
        gaps = [key for key in earlier if key not in known]
        if known:
            reached = arrival_gates.carryover_arrivals(known, legs, onward=carryover == 'onward')
            if carryover == 'onward':
                gaps += [key for key in reached if unit_of(key) is None and not fallback[key]]
            sources = arrival_gates.opened_by(known, reached, legs)
            for key in sorted(reached, key=_stable):
                target = (cycle, key[1])
                if target in pinned:
                    continue
                seeds[target] = arrival_gates.cycle_start(cycle)
                carried['opened'].append({
                    'cycle': cycle, 'committee': key[1], 'gate': seeds[target],
                    'gate_source': arrival_gates.CARRYOVER, 'arrival': reached[key],
                    'label': f'gate opened by {arrival_gates.cycle_words(previous)} transfers',
                    'opened_by': [{'cycle': previous, 'source': source[1], 'first_date': first, 'amount': float(total)}
                                  for source, first, total in sources.get(key, [])]})
        carried['missing_units'] += [{'cycle': key[0], 'committee': key[1]} for key in sorted(gaps, key=_stable)]
        statuses.append('unknown' if not known else 'partial' if gaps else 'known')
    carried['status'] = ('off' if carryover == 'off' else 'unknown' if set(statuses) == {'unknown'}
                         else 'known' if set(statuses) == {'known'} else 'partial')
    gates = arrival_gates.earliest_arrival({**pinned, **seeds}, legs)
    excluded = []
    # The walk still starts from the roots, first; a carried-over committee that no
    # cycle-C transfer reaches is walked after them, from its own opened gate.
    pending = [*sorted(seeds, key=_stable, reverse=True), *pending]
    while pending:
        key = pending.pop()
        if key in visited:
            continue
        visited.add(key)
        unit = unit_of(key)
        if unit is None:
            missing.add(key)
        gate = None if key in pinned else gates.get(key)
        destinations = defaultdict(Decimal)
        candidate_destinations = defaultdict(Decimal)
        dated, unverified, before = defaultdict(list), defaultdict(Decimal), {}
        for destination, value, row in transfers(unit or {}):
            terminal = schedule_b_leg(row)[0] == transfer_rule.CANDIDATE
            when = arrival_gates.row_date(row, 'disbursements')
            if not arrival_gates.counts(when, gate):
                entry = before.setdefault((destination, terminal), [Decimal(0), 0, ''])
                entry[0] += value
                entry[1] += 1
                entry[2] = max(entry[2], when)
                continue
            (candidate_destinations if terminal else destinations)[destination] += value
            if when:
                dated[destination, terminal].append(when)
            elif gate is not None:
                unverified[destination, terminal] += value
        if unit is None:
            destinations.update(fallback[key])
            if gate is not None:
                for destination, value in fallback[key].items():
                    unverified[destination, False] += value

        def dates(destination, terminal):
            """F12 fields for an edge: its counted transfers' first and last dates, and the
            undated dollars counted with a date_unverified flag (D-040)."""
            found = sorted(dated[destination, terminal])
            extra = {'first_date': found[0], 'last_date': found[-1]} if found else {}
            if unverified[destination, terminal]:
                extra[arrival_gates.UNVERIFIED] = float(unverified[destination, terminal])
            return extra

        for destination, value in destinations.items():
            if value <= 0:
                continue
            target = (key[0], destination)
            edges.append({'cycle':key[0], 'source':key[1], 'destination':destination, 'amount':float(value),
                          **dates(destination, False)})
            pending.append(target)
        for destination, value in candidate_destinations.items():
            if value > 0:
                edges.append({'cycle':key[0], 'source':key[1], 'destination':destination,
                              'amount':float(value), 'terminal_candidate':True, **dates(destination, True)})
        for (destination, terminal), (value, count, last) in before.items():
            excluded.append({'cycle':key[0], 'source':key[1], 'destination':destination, 'amount':float(value),
                             'rows':count, 'last_date':last, 'gate':gate,
                             **({'terminal_candidate':True} if terminal else {})})
    # The units in _stable order, not the visited set's per-process hash order. activity() reads
    # them in this order, so the order of the candidate rows (candidate_details) and the label a
    # candidate keeps (graph_data.grouped keeps the last one it reads) are the same everywhere.
    return {'keys':visited, 'units':{key:units[key] for key in sorted(visited, key=_stable) if key in units},
            'missing':sorted(missing), 'edges':edges, 'note':NOTE,
            # F12: the roots (counted whole), each reached committee/cycle's gate, and the
            # transfers left out because they were dated before their source's gate.
            'roots':set(pinned), 'gates':{key:gates[key] for key in sorted(visited, key=_stable) if key in gates},
            'excluded_before_gate':excluded,
            # Carryover: where each gate came from (root, carryover or in-cycle), the committees
            # the previous cycle's transfers opened and what could not be read for it.
            'gate_sources':{key:(arrival_gates.ROOT if key in pinned else arrival_gates.CARRYOVER if key in seeds
                                 else arrival_gates.IN_CYCLE)
                            for key in sorted(visited, key=_stable) if key in gates},
            'carryover':carried}

def activity(network):
    grouped, labels = defaultdict(Decimal), {}
    filings = defaultdict(list)
    # A Schedule B candidate payment's FEC code (disbursement_type: 24K, 24Z,
    # ...) is kept beside the recorded 'Contribution / Other' amount, so a
    # reader can split that one bucket by code (graph_data.activity_sides:
    # Paul, 2026-09-23, "It should be two separate segments"). The grouping
    # and every existing field are unchanged.
    parts = defaultdict(lambda: defaultdict(lambda: [Decimal(0), []]))
    # F12: a reached committee's rows count from its gate on (gate_of: none for a root, or
    # for a network built without gates); an undated row counts, flagged date_unverified.
    unverified = defaultdict(int)

    def record_of(row, schedule, key, gate):
        record = filing_record(row)
        if gate is not None and not arrival_gates.row_date(row, schedule):
            record[arrival_gates.UNVERIFIED] = True
            unverified[key] += 1
        return record

    for (year, committee), unit in network['units'].items():
        gate = gate_of(network, (year, committee))
        for row in observations(unit, 'expenditures', gate=gate):
            if memo(row):
                continue
            candidate = str(row.get('candidate_id') or '')
            direction = {'S':'Support', 'O':'Oppose'}.get(row.get('support_oppose_indicator'), 'Unknown')
            key = (year, committee, candidate, direction)
            grouped[key] += amount(row.get('expenditure_amount'))
            filings[key].append(record_of(row, 'expenditures', key, gate))
            labels[key] = str(row.get('candidate_name') or candidate or 'Unidentified candidate')
        for row in observations(unit, 'disbursements', gate=gate):
            if schedule_b_leg(row)[0] != transfer_rule.CANDIDATE:
                continue
            key = (year,committee,str(row['candidate_id']),'Contribution / Other')
            value, record = amount(row.get('disbursement_amount')), record_of(row, 'disbursements', key, gate)
            grouped[key] += value
            filings[key].append(record)
            part = parts[key][str(row.get('disbursement_type') or '').strip().upper()]
            part[0] += value
            part[1].append(record)
            labels[key] = str(row.get('candidate_name') or row.get('recipient_name') or row['candidate_id'])
    return [{'cycle':k[0], 'committee':k[1], 'candidate_id':k[2], 'direction':k[3],
             'candidate':labels[k], 'party':'', 'amount':value, 'filings':filings[k],
             **({'transaction_parts':{code:{'amount':total, 'filings':records}
                                      for code,(total,records) in parts[k].items()}} if k in parts else {}),
             **({'date_unverified_rows':unverified[k]} if unverified[k] else {}),
             'source':network['units'][k[:2]]['_source']} for k,value in grouped.items()]

def merge_activity(rows, roots, units=None, report_edges=()):
    found = network(roots, units, report_edges)
    saved = activity(found)
    # Select whole schedule partitions, never a candidate-by-candidate hybrid
    # that retains a gift deleted by an amendment. Different source totals are
    # disclosed, not treated as proof that either source is wrong. This is a
    # saved-snapshot measurement, not an update to the workbook or its ledger.
    comparisons = []
    def partition(row):
        return (row['cycle'],row['committee'],
                'contributions' if row['direction'] == 'Contribution / Other' else 'independent_expenditures')
    old, new = defaultdict(Decimal), defaultdict(Decimal)
    old_details, new_details = defaultdict(lambda:defaultdict(Decimal)), defaultdict(lambda:defaultdict(Decimal))
    rows = [row for row in rows if (row['cycle'], row['committee']) in found['keys']]
    for row in rows:
        old[partition(row)] += row['amount']
        old_details[partition(row)][row['candidate_id'],row['direction']] += row['amount']
    for row in saved:
        new[partition(row)] += row['amount']
        new_details[partition(row)][row['candidate_id'],row['direction']] += row['amount']
    for key in sorted(set(old) | set(new)):
        if key[:2] in found['units'] and dict(old_details[key]) != dict(new_details[key]):
            comparisons.append({'cycle':key[0], 'committee':key[1], 'schedule':key[2],
                                'workbook_amount':float(old[key]) if key in old else None,
                                'snapshot_amount':float(new[key]),
                                'basis':'Different saved sources; coverage/filing changes are not reconciled.'})
    found['measurement_comparisons'] = comparisons
    # F12: a workbook row for a reached committee with no saved snapshot carries no
    # transaction dates, so a gate inside its cycle cannot be checked against it. It
    # counts (D-040) and says so.
    result = [{**r, arrival_gates.UNVERIFIED: True}
              if (gate_of(found, (r['cycle'], r['committee'])) or '') > arrival_gates.cycle_start(r['cycle']) else r
              for r in rows if (r['cycle'],r['committee']) in found['keys']
              and (r['cycle'],r['committee']) not in found['units']]
    parties = defaultdict(set)
    for row in rows:
        if row.get('party'):
            parties[row['cycle'],row['candidate_id']].add(row['party'])
    result.extend({**r,'party':' / '.join(sorted(parties[r['cycle'],r['candidate_id']]))} for r in saved)
    return result, found


# F4 (Paul approved 2026-09-25): PAC evidence showed one coverage window for a
# whole network and checked only the pane's own committee against its FEC
# summary. Every committee in a network now carries its own coverage - the last
# itemized transaction on each schedule and its summary's window - and its own
# reconciliation to the summary, for independent expenditures and for the
# transfer lines, with "no summary saved" where there is none.
#
# The Form 3X summary lines that report money sent to other committees, beside
# the openFEC totals field carrying each (F6): line 22, transfers to affiliated
# and other party committees; line 23, contributions to federal candidates and
# other political committees.
SUMMARY_TRANSFER_LINES = (('22', 'transfers_to_affiliated_committee'),
                          ('23', 'fed_candidate_committee_contributions'))
ITEMIZED_DATES = (('receipts', 'contribution_receipt_date'), ('disbursements', 'disbursement_date'),
                  ('expenditures', 'expenditure_date'))
RECONCILED_WITHIN = Decimal('0.01')
_DAY = re.compile(r'\d{4}-\d{2}-\d{2}')


def summary_transfers(unit):
    """(stated, itemized) for Form 3X lines 22-23, compared line for line.

    stated is what the summary reports on the lines it states; itemized is the
    non-memo rows filed on those same lines (one per source ID, a reload once).
    (None, None) when the summary states neither line. evidence_geo's F6 gap
    (transfers_not_itemized) is stated less itemized, never negative.
    """
    totals = unit.get('totals') or {}
    stated, lines = Decimal(0), set()
    for line, field in SUMMARY_TRANSFER_LINES:
        value = totals.get(field)
        if value in (None, ''):
            continue
        try:
            stated += amount(value)
        except ValueError:
            continue
        lines.add(line)
    if not lines:
        return None, None
    itemized = sum((amount(row.get('disbursement_amount')) for row in observations(unit, 'disbursements')
                    if not memo(row) and transfer_rule.line_code(row) in lines), Decimal(0))
    return stated, itemized


def unit_coverage(unit):
    """A saved unit's own coverage: its summary's window and, per schedule, the
    date of its last itemized (non-memo) transaction."""
    totals = unit.get('totals') or {}
    last = {}
    for schedule, field in ITEMIZED_DATES:
        dates = [str(row.get(field) or '')[:10] for row in observations(unit, schedule) if not memo(row)]
        dates = [value for value in dates if _DAY.fullmatch(value)]
        last[schedule] = max(dates) if dates else None
    known = [value for value in last.values() if value]
    return {'coverage_start': str(totals.get('coverage_start_date') or '')[:10] or None,
            'coverage_end': str(totals.get('coverage_end_date') or '')[:10] or None,
            'last_itemized': last, 'last_itemized_date': max(known) if known else None}


def _checked(itemized, stated):
    """One reconciliation: itemized against the summary, difference = itemized - summary."""
    if stated is None:
        return {'status': 'no_summary', 'summary': None,
                'itemized': float(itemized) if itemized is not None else None, 'difference': None}
    difference = itemized - stated
    return {'status': 'reconciled' if abs(difference) <= RECONCILED_WITHIN else 'differs',
            'summary': float(stated), 'itemized': float(itemized), 'difference': float(difference)}


def summary_reconciliation(unit):
    """{independent_expenditures, transfers}: each a _checked() against the unit's own summary.

    Independent expenditures: the regular Schedule E rows the network counts
    (observations: one per source ID, notices out, a reload once, memos out)
    against the summary's independent_expenditures. Transfers: summary_transfers.
    """
    totals = unit.get('totals') or {}
    itemized = sum((amount(row.get('expenditure_amount')) for row in observations(unit, 'expenditures')
                    if not memo(row)), Decimal(0))
    value = totals.get('independent_expenditures')
    try:
        stated = amount(value) if value not in (None, '') else None
    except ValueError:
        stated = None
    stated_lines, itemized_lines = summary_transfers(unit)
    if stated_lines is None:
        itemized_lines = sum((amount(row.get('disbursement_amount')) for row in observations(unit, 'disbursements')
                              if not memo(row) and transfer_rule.line_code(row) in ('22', '23')), Decimal(0))
    return {'independent_expenditures': _checked(itemized, stated),
            'transfers': _checked(itemized_lines, stated_lines)}


def committees(found, names=None):
    """Every committee/cycle in a network, saved or not, each with its own
    coverage and reconciliation; the ones without a saved snapshot say so."""
    names = names or {}
    rows = []
    for key in sorted(found['units'], key=_stable):
        unit = found['units'][key]
        rows.append({'cycle': key[0], 'committee': key[1], 'committee_name': names.get(key[1], key[1]),
                     'saved': True, **unit_coverage(unit), 'reconciliation': summary_reconciliation(unit)})
    for year, committee in found['missing']:
        rows.append({'cycle': year, 'committee': committee, 'committee_name': names.get(committee, committee),
                     'saved': False, 'coverage_start': None, 'coverage_end': None,
                     'last_itemized': {schedule: None for schedule, _ in ITEMIZED_DATES},
                     'last_itemized_date': None,
                     'reconciliation': {'independent_expenditures': _checked(None, None),
                                        'transfers': _checked(None, None)}})
    return rows


def network_problems(payload):
    """Every way an /api/evidence-network answer breaks contracts/evidence-network.json,
    as plain sentences; [] when it holds. Used on the producer's own output, on the
    contract's examples and on its negative controls."""
    if not isinstance(payload, dict):
        return ['the answer is not an object']
    problems = [f'the answer has no {field}' for field in ('relationships', 'edges', 'sources', 'missing',
                                                            'committees', 'note') if field not in payload]
    rows = payload.get('committees')
    if not isinstance(rows, list):
        return problems + ['committees must be a list']
    listed = set()
    for row in rows:
        if not isinstance(row, dict):
            problems.append('a committees row is not an object')
            continue
        who = f"{row.get('committee')} {row.get('cycle')}"
        listed.add((str(row.get('cycle')), row.get('committee'), bool(row.get('saved'))))
        if not re.fullmatch(r'C\d{8}', str(row.get('committee') or '')) or not re.fullmatch(r'\d{4}', str(row.get('cycle') or '')):
            problems.append(f'{who}: a committees row names no committee id and cycle')
        last = row.get('last_itemized')
        if not isinstance(last, dict) or set(last) != {schedule for schedule, _ in ITEMIZED_DATES}:
            problems.append(f'{who}: last_itemized must name receipts, disbursements and expenditures')
            last = {}
        dates = [value for value in last.values() if value is not None]
        if any(not _DAY.fullmatch(str(value)) for value in dates):
            problems.append(f'{who}: a last itemized date is not a date')
        if row.get('last_itemized_date') != (max(dates) if dates else None):
            problems.append(f'{who}: last_itemized_date is not the latest of its schedules')
        checks = row.get('reconciliation')
        if not isinstance(checks, dict) or set(checks) != {'independent_expenditures', 'transfers'}:
            problems.append(f'{who}: reconciliation must check independent_expenditures and transfers')
            continue
        if not row.get('saved') and (dates or row.get('coverage_end') or row.get('coverage_start')
                                     or any(check.get('status') != 'no_summary' for check in checks.values()
                                            if isinstance(check, dict))):
            problems.append(f'{who}: a committee with no saved snapshot carries coverage or a checked summary')
        for name, check in checks.items():
            if not isinstance(check, dict) or check.get('status') not in ('reconciled', 'differs', 'no_summary'):
                problems.append(f'{who}: the {name} check states no status')
                continue
            if check['status'] == 'no_summary':
                if check.get('summary') is not None or check.get('difference') is not None:
                    problems.append(f'{who}: the {name} check has no summary yet states one')
                continue
            try:
                itemized, stated = Decimal(str(check['itemized'])), Decimal(str(check['summary']))
                difference = Decimal(str(check['difference']))
            except (KeyError, InvalidOperation, TypeError):
                problems.append(f'{who}: the {name} check states no amounts')
                continue
            if abs(difference - (itemized - stated)) > RECONCILED_WITHIN:
                problems.append(f'{who}: the {name} difference is not itemized minus summary')
            if (check['status'] == 'reconciled') != (abs(itemized - stated) <= RECONCILED_WITHIN):
                problems.append(f'{who}: the {name} check says {check["status"]} against its own amounts')
    for entry in payload.get('missing') or []:
        if (str(entry.get('cycle')), entry.get('committee'), False) not in listed:
            problems.append(f"{entry.get('committee')} {entry.get('cycle')}: a missing committee is not listed as unsaved")
    for entry in payload.get('sources') or []:
        if (str(entry.get('cycle')), entry.get('committee'), True) not in listed:
            problems.append(f"{entry.get('committee')} {entry.get('cycle')}: a saved source is not listed as saved")
    return problems


def sources(found):
    return [{'cycle':key[0], 'committee':key[1], 'source':unit['_source'],
             'sha256':unit.get('_sha256',''),
             'coverage_start':unit['totals'].get('coverage_start_date'),
             'coverage_end':unit['totals'].get('coverage_end_date'),
             # F12: the date this committee's rows start counting (a root's cycle start).
             **({'gate':found['gates'][key]} if key in (found.get('gates') or {}) else {}),
             **({'gate_source':found['gate_sources'][key]} if key in (found.get('gate_sources') or {}) else {})}
            for key,unit in sorted(found['units'].items())]


def snapshot_view(identifier, committee, cycle='', root=None):
    """The selected snapshot owns the root; saved recipient PACs supply onward activity."""
    import re
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,99}', identifier):
        raise ValueError('Invalid evidence snapshot.')
    root = Path(root or ROOT)
    path = root / identifier / 'snapshots.json'
    raw = path.read_bytes()
    fingerprint = hashlib.sha256(raw).hexdigest()
    selected = {(str(u['cycle']), u['committee_id']):
                {**u, '_source':str(path), '_sha256':fingerprint}
                for u in json.loads(raw) if u['committee_id'] == committee
                and (not cycle or str(u['cycle']) == str(cycle))}
    if not selected:
        raise ValueError('This committee/cycle is not in the selected snapshot.')
    # The selected units first, then the saved inventory: {**inventory(root), **selected} read
    # lazily, so only the committees the traversal reaches are read.
    units = ChainMap(selected, inventory(root))
    found = network(selected, units)
    rows = relationships(activity(found), selected, found)
    names = {key[1]: next(((r.get('committee') or {}).get('name')
                          for r in unit.get('receipts') or []
                          if (r.get('committee') or {}).get('name')), key[1])
             for key, unit in found['units'].items()}
    for row in rows:
        row['route_names'] = [names.get(c, c) for c in row['route']]
    return {'relationships':rows, 'edges':found['edges'], 'sources':sources(found),
            'missing':[{'cycle':year, 'committee':c} for year,c in found['missing']],
            # F4: each committee's own coverage and summary reconciliation.
            'committees':committees(found, names),
            # F12: the gates the walk followed and what they left out.
            'date_rule':date_rule(found),
            'note':NOTE}


def date_rule(found):
    """F12, for an answer: the rule, each committee/cycle's gate (a root's is its cycle
    window's first day), and the transfers left out as dated before their source's gate."""
    roots = found.get('roots') or set()
    gates = found.get('gates') or {}
    return {'rule': 'earliest_arrival',
            'gates': [{'cycle': key[0], 'committee': key[1], 'gate': gates[key], 'root': key in roots,
                       'gate_source': (found.get('gate_sources') or {}).get(key, '')}
                      for key in sorted(gates, key=_stable)],
            # Carryover: the committees the previous cycle's transfers opened ('gate opened
            # by 2023-2024 transfers'), and 'unknown' with the units not saved.
            'carryover': found.get('carryover'),
            'excluded_before_gate': list(found.get('excluded_before_gate') or []),
            'excluded_amount': float(sum((amount(edge['amount'])
                                          for edge in found.get('excluded_before_gate') or []), Decimal(0)))}


def relationships(rows, roots, found):
    """Observed ties and concentration, not a claim of candidate control."""
    totals, pairs, labels = defaultdict(Decimal), defaultdict(Decimal), {}
    filings = defaultdict(list)
    paths = {key:[key[1]] for key in roots}
    # A set of roots is searched in _stable order, not its per-process hash order, so a committee
    # reached by equally short routes from two roots always takes the route of the first.
    queue = sorted(roots, key=_stable) if isinstance(roots, (set, frozenset)) else list(roots)
    outgoing = defaultdict(list)
    for edge in found['edges']:
        if edge.get('terminal_candidate'):
            continue
        outgoing[edge['cycle'],edge['source']].append(edge['destination'])
    for key in queue:
        for destination in outgoing[key]:
            target = (key[0],destination)
            if target not in paths:
                paths[target] = [*paths[key], destination]
                queue.append(target)
    for row in rows:
        source = (row['cycle'],row['committee'],row['direction'])
        totals[source] += row['amount']
        key = (*source,row['candidate_id'])
        pairs[key] += row['amount']
        filings[key].extend(row.get('filings') or [])
        labels[key] = row['candidate']
    result = []
    reversed_sources = {key[:3] for key,value in pairs.items() if value < 0}
    for key, value in pairs.items():
        year, committee, direction, candidate = key
        unit = found['units'].get((year,committee),{})
        name = next(((r.get('committee') or {}).get('name') for r in unit.get('receipts') or []
                     if (r.get('committee') or {}).get('name')), committee)
        result.append({'cycle':year,'committee':committee,'committee_name':name,
                       'candidate_id':candidate,'candidate':labels[key], 'direction':direction,
                       'amount':float(value), 'filings':filings[key],
                       # Signed adjustments can make a "share" misleading or
                       # greater than 100%. Keep amounts visible; suppress only
                       # the percentage when any candidate has a net reversal.
                       'share_of_pac_direction':float(value/totals[key[:3]]) if totals[key[:3]] > 0 and
                           key[:3] not in reversed_sources else None,
                       'route':paths.get((year,committee),[committee]),
                       'basis':'Observed spending; candidate control is not established'})
    return sorted(result,key=lambda r:(-r['amount'],r['committee'],r['candidate_id']))

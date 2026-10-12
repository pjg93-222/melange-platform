"""Where a PAC's money came from and where it went, by congressional district.

S8 of the PAC evidence lane. One route, `/api/evidence-geo`, answers for one
pane: a committee, a cycle and a direction (raised or spent). The geography
itself is district_geo's job (boundaries, the ZIP crosswalk, the FEC candidate
and committee masters); this module decides which rows are which KIND of money
and which basis places each one, then reconciles located plus unlocated back to
the total the committee reported.

Nothing here invents a location. A row the bases cannot place keeps its amount
and joins `unlocated` with the reason, because a map that quietly drops money
is worse than a map with a gap in it (S6_DESIGN, Paul 2026-09-17).

The four spend kinds and their bases, Paul's ruling:
  candidates   Schedule E independent spending and Schedule B candidate
               payments, by the candidate's own seat; support and oppose kept
               apart.
  transfers    Schedule B transfers to another committee, by that committee's
               registered state IN THAT CYCLE'S master - where the paperwork
               lived, not where the money finally went.
  operating    Schedule B line 21B, by the payee's state (and ZIP when it has
               one that resolves).
  conversions  the in-kind crypto the committee received and liquidated, filed
               as an operating payment back to the contributor. Its own kind,
               included on the state heatmap by default under the all-spend rule.

Which Schedule B rows are transfers and which are candidate payments is
tools/transfer_rule.py's call (F8, Paul 2026-09-25: "one rule, by transaction
type, everywhere"), the same rule the collector and the downstream network use.

Transfers a committee's summary reports on lines 22-23 but its saved Schedule B
does not itemize have no known recipient. They are disclosed on their own line,
`transfers_not_itemized`, and kept out of the Spent geography and its total
(F6, Paul 2026-09-25: no dollar may count twice).
"""
from __future__ import annotations

from collections import defaultdict
from contextlib import closing
from decimal import Decimal, InvalidOperation
import json
import hashlib
from pathlib import Path
import re
import sqlite3
import threading

import district_geo
import evidence_csv
import fec_citation
import geocode
import downstream
from view_cache import ViewCache, file_signature

_views = ViewCache(entries=24)
_lookup_guard = threading.Lock()
_lookup_version = None

class _MapInputsChanged(ValueError):
    pass


def cached_geography(source, identifier, committee='', cycle='', direction='spent', *,
                     store=None, snapshot_root=None, data_root=None):
    """Reuse a map only while source, crosswalks and geocoder facts are unchanged."""
    if not IDENTIFIER.fullmatch(str(identifier or '')):
        raise ValueError(M_SOURCE)
    root = Path(snapshot_root or downstream.ROOT)
    def dependencies():
        # Lane P (boards #47-#55): sign only what this payload can depend on -
        # the top-level boundary and crosswalk files district_geo reads, and the
        # legislative bundles as before. geo/v2 holds the map layers the page
        # draws (866 files the payload never reads); signed on every request it
        # cost 213 ms a scan and put the PAC map at 408-998 ms (lane G measured).
        geo = district_geo.geo_dir()
        paths = list(geo.glob('*.json')) + list((geo / 'sld').rglob('*.json'))
        # Candidate and committee masters can change placement without a new
        # snapshot. Include the source files, not just the rendered boundaries.
        paths.extend(district_geo.inputs_dir().glob('fec_beneficiary/cn*/cn.txt'))
        paths.extend(district_geo.inputs_dir().glob('fec_committee_master/cm*/cm.txt'))
        # district_geo's reference caches are keyed by directory. Invalidate
        # those too when files in the same directory change.
        global _lookup_version
        lookup_version = file_signature(paths)
        with _lookup_guard:
            if lookup_version != _lookup_version:
                district_geo.reset_caches()
                _lookup_version = lookup_version
        if source == 'snapshot':
            paths += [root / identifier / 'snapshots.json', root / identifier / 'evidence.json']
            if direction == 'spent':
                paths += list(root.glob('*/snapshots.json'))
        elif source == 'csv' and store is not None:
            database = store.database_path(identifier)
            paths += [database, Path(str(database) + '-wal'), store.record_path(identifier)]
            if direction == 'spent':
                paths += list(root.glob('*/snapshots.json'))
        if data_root is not None:
            database = Path(data_root) / 'geocode/addresses.sqlite'
            paths += [database, Path(str(database) + '-wal')]
        return file_signature(paths)
    before = dependencies()
    key_ = (str(root.resolve()), str(data_root), source, identifier, committee, cycle, direction, before)
    def build():
        result = geography(source, identifier, committee, cycle, direction,
                           store=store, snapshot_root=root, data_root=data_root)
        # A geocoder read can create/checkpoint its WAL. Retry once against
        # the settled version; never label a mixed-source result as cached.
        if dependencies() != before:
            raise _MapInputsChanged('Map inputs changed while preparing this view. Refresh the map.')
        return result
    try:
        return _views.get(key_, build)
    except _MapInputsChanged:
        before = dependencies()
        key_ = (str(root.resolve()), str(data_root), source, identifier, committee, cycle, direction, before)
        return _views.get(key_, build)

# Legacy stops retained for older clients. The interactive map derives its
# log range from the positive amounts in the selected PAC/layer/filter scope.
STOPS = (1000, 10000, 100000, 1000000, 10000000)

DISTRICT_KEY = re.compile(r'^[A-Z]{2}-(\d{2}|AL|SEN)$')
STATE_KEY = re.compile(r'^[A-Z]{2}$')
DIRECTIONS = ('raised', 'spent')
SOURCES = ('csv', 'snapshot')
IDENTIFIER = re.compile(r'[a-z0-9][a-z0-9_-]{0,99}')
COMMITTEE = re.compile(r'C\d{8}')
CENT = Decimal('0.01')

# An in-kind asset the committee never converted to cash reads the same on
# both sides of the ledger; the receipt says it arrived, the 21B row says it
# was sold back. Both phrasings appear in the real filings.
CONVERSION_TEXT = re.compile(r'IN[- ]?KIND|NOT LIQUIDATED|NOT LIQ\b')

# F6. The Form 3X summary lines that report money sent to other committees,
# beside the openFEC totals field carrying each: line 22, transfers to
# affiliated and other party committees; line 23, contributions to federal
# candidates and other political committees.
SUMMARY_TRANSFER_LINES = downstream.SUMMARY_TRANSFER_LINES
NOT_ITEMIZED = 'transfers reported, recipient not itemized'

M_SOURCE = 'Choose an imported file or a saved snapshot.'
M_DIRECTION = 'Choose Raised or Spent.'
M_COMMITTEE = 'Choose a committee in this source.'
M_SNAPSHOT = 'That saved snapshot is unavailable.'
M_CYCLE = 'Choose a cycle this source records.'


def M_BLANK_CYCLE(cycles):
    """Ruling A (Paul, 2026-09-18): "There should be no all cycles" on the map.

    A district map is drawn on ONE Congress's boundaries, so a blank cycle has
    no vintage and would draw an empty country. The refusal names the cycles
    this source actually carries rather than answering with a blank map, and
    choosing one of them is the client's job.
    """
    known = ', '.join(str(value) for value in cycles if str(value or '').strip())
    return ('A map is drawn on one Congress, so it needs one cycle, not all of them. '
            + (f'Choose {known}.' if known else 'This source records no cycle to draw.'))
M_NO_OUTBOUND = ('This import records money this committee RECEIVED, not money it sent, so it '
                 'carries no outbound rows to place on a map. Open a saved FEC snapshot for '
                 'this committee to see where it spent.')
M_NO_RAISED = 'This source records no receipts for this committee and cycle, so there is nothing to draw.'
M_NO_SPENT = 'This source records no disbursements for this committee and cycle, so there is nothing to draw.'

KIND_LABELS = {
    'candidates': ('Candidates', "by the candidate's district"),
    'transfers': ('Transfers to committees', "by the committee's registered state"),
    'operating': ('Operating', "by the payee's state"),
    'conversions': ('Asset conversions', 'in-kind assets liquidated back to the contributor'),
    'contributors': ('Contributors', 'by the contributor ZIP, then the state'),
}
DEFAULT_OFF = frozenset()

# The ZIP layer ships the largest ZIPs and says how many it left out, in
# dollars as well as in rows: a cap that hid money would break the same
# reconciliation every other layer keeps.
ZIP_CAP = 3000
# A state the Census region table does not know. It is a ROW in by_region
# rather than a silent drop, so sum(by_region) is still the located total.
REGION_UNPLACED = 'Unplaced'


def money(value):
    """A Decimal for anything the filings write, and zero for anything else."""
    try:
        result = Decimal(str(value if value not in (None, '') else 0))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(0)
    return result if result.is_finite() else Decimal(0)


def cash(value):
    return float(money(value).quantize(CENT))


def name_key(value):
    return ' '.join(re.findall(r'[A-Z0-9]+', str(value or '').upper()))


def is_memo(row):
    """Exactly tools/pac_evidence.py's test, so the same rows are excluded."""
    return downstream.memo(row)


def line_of(row):
    """'21B', '22', '23' - the Schedule B line, punctuation removed."""
    return re.sub(r'[^0-9A-Z]', '', str(row.get('line_number') or '').strip().upper())


def zip5(value):
    digits = re.sub(r'\D', '', str(value or ''))
    return digits[:5] if len(digits) >= 5 else ''


def state_of(value):
    text = str(value or '').strip().upper()
    return text if STATE_KEY.fullmatch(text) else ''


# ---------------------------------------------------------------- the buckets

class Bucket:
    """One kind's districts, states and the rows it could not place.

    `add_district` and `add_state` take an already-decided key; deciding is the
    caller's job, because each kind has its own basis and its own reason for
    failing. Shares are computed at the end, over the kind's own total, so a
    kind that is toggled off never changes another kind's numbers.
    """

    def __init__(self, kind):
        self.kind = kind
        self.districts = defaultdict(lambda: {'amount': Decimal(0), 'rows': 0, 'support': Decimal(0),
                                              'oppose': Decimal(0), 'who': {}, 'split': False,
                                              'bases': set(), 'filings': defaultdict(list)})
        self.states = defaultdict(lambda: {'amount': Decimal(0), 'rows': 0, 'support': Decimal(0),
                                           'oppose': Decimal(0), 'who': {}, 'split': False,
                                           'bases': set(), 'filings': defaultdict(list)})

    def _add(self, table, key, amount, rows=1, who='', direction='', split=False, basis='', filing=None,
             filings=None):
        entry = table[key]
        entry['amount'] += amount
        entry['rows'] += rows
        entry['split'] = entry['split'] or split
        if basis:
            entry['bases'].add(basis)
        if direction == 'support':
            entry['support'] += amount
        elif direction == 'oppose':
            entry['oppose'] += amount
        if who:
            entry['who'][who] = entry['who'].get(who, Decimal(0)) + amount
        if filing is not None:
            entry['filings'][who].append(filing)
        if filings:
            entry['filings'][who].extend(filings)

    def add_district(self, key, amount, **fields):
        self._add(self.districts, key, amount, **fields)

    def add_state(self, key, amount, **fields):
        self._add(self.states, key, amount, **fields)

    def total(self):
        return (sum((entry['amount'] for entry in self.districts.values()), Decimal(0))
                + sum((entry['amount'] for entry in self.states.values()), Decimal(0)))

    def payload(self, basis):
        total = self.total()
        share_total = total if total > 0 and all(entry['amount'] >= 0 for entry in
            [*self.districts.values(), *self.states.values()]) else None
        label, basis_note = KIND_LABELS.get(self.kind, (self.kind.title(), basis))
        return {'kind': self.kind, 'label': label, 'total': cash(total),
                'rows': sum(entry['rows'] for entry in list(self.districts.values()) + list(self.states.values())),
                'basis': basis or basis_note, 'default_on': self.kind not in DEFAULT_OFF,
                'districts': _rank(self.districts, share_total, DISTRICT_KEY),
                'states': _rank(self.states, share_total, STATE_KEY)}


def basis_of(bases):
    """One word for how a row's district was decided: street, zip or zip_split.

    S8b. '' for a row no contributor basis placed - every SPENT kind, whose
    basis is the candidate's seat or the payee's state and has nothing to do
    with a contributor address. A district reached only by street matches is
    exact, so it carries neither the split mark nor the ZIP caveat.
    """
    if not bases:
        return ''
    if bases == {'street'}:
        return 'street'
    if 'zip_split' in bases:
        return 'zip_split'
    return 'zip'


def _rank(table, total, pattern):
    rows = []
    for key, entry in table.items():
        if not pattern.fullmatch(key):
            # Never silently drop: a key the map cannot draw is still money, and
            # the caller put it here, so it is a bug worth seeing in the list.
            continue
        who = sorted(entry['who'].items(), key=lambda item: (-item[1], item[0]))
        rows.append({'key': key, 'amount': cash(entry['amount']), 'rows': entry['rows'],
                     'basis': basis_of(entry['bases']),
                     'share': float((entry['amount'] / total).quantize(Decimal('0.000001')))
                              if total is not None and total > 0 else None,
                     'support': cash(entry['support']), 'oppose': cash(entry['oppose']),
                     'split': bool(entry['split']),
                     'recipients': [{'name': name, 'amount': cash(value),
                                     'filings': entry['filings'].get(name, [])} for name, value in who],
                     'who': ', '.join(name for name, _ in who[:3]) + (' and others' if len(who) > 3 else '')})
    return sorted(rows, key=lambda row: (-row['amount'], row['key']))


class Unlocated:
    """Money no basis could place, grouped by the reason it could not."""

    def __init__(self):
        self.rows = defaultdict(lambda: {'amount': Decimal(0), 'rows': 0})

    def add(self, reason, amount, rows=1):
        entry = self.rows[reason]
        entry['amount'] += amount
        entry['rows'] += rows

    def total(self):
        return sum((entry['amount'] for entry in self.rows.values()), Decimal(0))

    def payload(self):
        return sorted(({'reason': reason, 'amount': cash(entry['amount']), 'rows': entry['rows']}
                       for reason, entry in self.rows.items()),
                      key=lambda row: (-abs(row['amount']), row['reason']))


class Mix:
    """How many raised rows each basis placed - S8b's honesty counter.

    The three counts are DISJOINT and add to every row the raised side read:

      geocoded  the address was in the geocode cache and resolved to a seat
      pending   the row has an address this console could geocode, but has not
                yet; it is placed by ZIP today and would sharpen if Start ran
      zip       everything else: no usable address at all, or a cached answer
                the service could not place, or a census tract that straddles
                two districts. Never dropped - S6_DESIGN's rule stands.

    A Spent map reads no contributor address, so its Mix stays all zero and
    the page shows no geocoding line for it.
    """

    def __init__(self):
        self.geocoded = 0
        self.pending = 0
        self.zip = 0

    def street(self, cache, values, cycle, bucket, unlocated, amount, who, filing=None):
        """Place one raised row by street if the cache can, counting either way.

        True when the row was placed here and the caller must not place it
        again. False leaves the row to the ZIP path, with the count already
        recorded, so no row can be counted twice or not at all.
        """
        parts = geocode.normalise(*geocode.address_of(values))
        if parts is None:
            self.zip += 1
            return False
        if cache is None:
            self.pending += 1
            return False
        row = cache.get(parts[0])
        if row is None:
            self.pending += 1
            return False
        key = geocode.district_of(row, cycle)
        if not key:
            self.zip += 1
            return False
        if place_by_street(bucket, unlocated, amount, key, who=who, filing=filing):
            self.geocoded += 1
            return True
        self.geocoded += 1
        return True

    def payload(self):
        return {'geocoded_rows': self.geocoded, 'zip_rows': self.zip,
                'pending_rows': self.pending}


class Zips:
    """Per-ZIP receipts, collected beside the district fold.

    place_by_zip reads a ZIP, applies the land-area shares and throws the digits
    away; drawing a circle on a town needs them back. Gathering them here, in the
    same loop that already has the row in hand, costs no extra I/O.

    EVERY raised row is counted, including one with no readable ZIP (key '') and
    one the geocoder placed by street, so the sum over this table is the reported
    total to the cent. The ZIP layer then reconciles exactly like the district and
    state layers, and a ZIP the point file cannot draw is listed, never dropped.
    """

    def __init__(self):
        self.rows = defaultdict(lambda: {'amount': Decimal(0), 'rows': 0, 'who': set(),
                                         'city': '', 'state': ''})

    def add(self, digits, amount, who='', city='', state=''):
        entry = self.rows[str(digits or '')]
        entry['amount'] += amount
        entry['rows'] += 1
        if who:
            entry['who'].add(who)
        if city and not entry['city']:
            entry['city'] = city
        if state and not entry['state']:
            entry['state'] = state

    def total(self):
        return sum((entry['amount'] for entry in self.rows.values()), Decimal(0))

    def payload(self, cap=ZIP_CAP):
        placed = sorted(((digits, entry) for digits, entry in self.rows.items() if digits),
                        key=lambda item: (-item[1]['amount'], item[0]))
        kept, dropped = placed[:cap], placed[cap:]
        missing = self.rows.get('') or {'amount': Decimal(0), 'rows': 0}
        return {
            'by_zip': [{'zip': digits, 'city': entry['city'], 'state': entry['state'],
                        'amount': cash(entry['amount']), 'rows': entry['rows'],
                        'contributors': len(entry['who'])} for digits, entry in kept],
            'zip_truncated': len(dropped),
            'zip_truncated_amount': cash(sum((entry['amount'] for _key, entry in dropped), Decimal(0))),
            'zip_missing': {'amount': cash(missing['amount']), 'rows': missing['rows']},
        }


# ---------------------------------------------------------- the wider layers

def geography_levels(buckets, cycle):
    """by_state and by_region, folded from the same bucket tables the map draws.

    Both are derived from `Bucket.districts` / `Bucket.states` DIRECTLY, in
    Decimal, before any rounding, so sum(by_state) and sum(by_region) are each
    the located total to the cent and reconcile against the reported total the
    same way the district layer does. A row carries its per-kind split as well
    as its own total, so turning a kind off on the page subtracts a number the
    server computed instead of asking the browser to re-do the geography.
    """
    vintage = district_geo.load_vintage(cycle) or {}
    names = {key: (entry.get('name') or key) for key, entry in (vintage.get('states') or {}).items()}
    states = defaultdict(lambda: {'amount': Decimal(0), 'rows': 0, 'who': set(),
                                  'kinds': defaultdict(lambda: Decimal(0))})
    for kind, bucket in buckets.items():
        for table in (bucket.districts, bucket.states):
            for key, entry in table.items():
                slot = states[str(key)[:2].upper()]
                slot['amount'] += entry['amount']
                slot['rows'] += entry['rows']
                slot['who'].update(entry['who'])
                slot['kinds'][kind] += entry['amount']
    by_state = sorted(({'key': key, 'name': names.get(key, key),
                        'region': district_geo.region_of(key) or REGION_UNPLACED,
                        'amount': cash(slot['amount']), 'rows': slot['rows'],
                        'contributors': len(slot['who']),
                        'kinds': {kind: cash(value) for kind, value in slot['kinds'].items()}}
                       for key, slot in states.items()),
                      key=lambda row: (-row['amount'], row['key']))
    regions = defaultdict(lambda: {'amount': Decimal(0), 'rows': 0, 'who': set(), 'states': set(),
                                   'kinds': defaultdict(lambda: Decimal(0))})
    for key, slot in states.items():
        region = regions[district_geo.region_of(key) or REGION_UNPLACED]
        region['amount'] += slot['amount']
        region['rows'] += slot['rows']
        region['who'].update(slot['who'])
        region['states'].add(key)
        for kind, value in slot['kinds'].items():
            region['kinds'][kind] += value
    order = list(district_geo.REGION_ORDER) + [REGION_UNPLACED]
    by_region = sorted(({'key': key, 'name': key, 'states': sorted(slot['states']),
                         'amount': cash(slot['amount']), 'rows': slot['rows'],
                         'contributors': len(slot['who']),
                         'kinds': {kind: cash(value) for kind, value in slot['kinds'].items()}}
                        for key, slot in regions.items()),
                       key=lambda row: order.index(row['key']) if row['key'] in order else 99)
    return by_state, by_region


# ------------------------------------------------------------ shared placing

def place_by_zip(bucket, unlocated, amount, zip_value, state_value, cycle, who='', reason_prefix='', filing=None):
    """A contributor's ZIP into districts by share, else the state, else nowhere."""
    digits = zip5(zip_value)
    parts = district_geo.district_for_zip(digits, cycle) if digits else []
    if parts:
        split = len(parts) > 1
        for key, share in parts:
            if not DISTRICT_KEY.fullmatch(key):
                unlocated.add(f'ZIP {digits} is in {key}, which this map has no seat for',
                              amount * money(share))
                continue
            bucket.add_district(key, amount * money(share), rows=1 if key == parts[0][0] else 0,
                                who=who, split=split,
                                basis='zip_split' if split else 'zip', filing=filing)
        return
    state = state_of(state_value)
    if state:
        reason = ('the ZIP was missing or unknown' if not digits or not parts else '')
        bucket.add_state(state, amount, who=who or reason, basis='zip', filing=filing)
        return
    problem = district_geo.last_problem or 'no usable ZIP or state on the row'
    unlocated.add(f'{reason_prefix}{problem}', amount)


def place_by_street(bucket, unlocated, amount, key, who='', filing=None):
    """An address the geocoder matched: one district, no share, no caveat.

    S8b's precise layer. The key has already been read off the cache for this
    cycle's boundary vintage, so this function only records it - and refuses a
    key the map has no line for rather than drawing a seat that does not exist.
    """
    if DISTRICT_KEY.fullmatch(str(key or '')):
        bucket.add_district(key, amount, who=who, basis='street', filing=filing)
        return True
    unlocated.add(f'a geocoded address resolved to {key!r}, which this map has no seat for', amount)
    return False


def place_by_state(bucket, unlocated, amount, zip_value, state_value, cycle, who='',
                   reason_prefix='', filing=None):
    """A payee's STATE, recovering it from the ZIP when the column is blank.

    Transfers, operating payments and asset conversions heat the whole state
    (S6_DESIGN): the payee's street address says where a vendor banks, not
    which electorate the money was aimed at, so colouring a district by it
    would claim more than the filing supports. The ZIP is still read, but only
    to recover a state the filer left blank.
    """
    state = state_of(state_value)
    if not state:
        digits = zip5(zip_value)
        parts = district_geo.district_for_zip(digits, cycle) if digits else []
        state = parts[0][0][:2] if parts else ''
    if state and state_exists(state, cycle):
        bucket.add_state(state, amount, who=who, filing=filing)
        return
    if state:
        unlocated.add(f'{state} is not a state this map draws', amount)
        return
    unlocated.add(f'{reason_prefix}the row carries no usable state or ZIP', amount)


def state_exists(state, cycle):
    payload = district_geo.load_vintage(cycle)
    return bool(payload) and state in (payload.get('states') or {})


def place_candidate(bucket, unlocated, amount, candidate_id, cycle, who='', direction='', filing=None,
                    filings=None):
    """A candidate's own seat; a seat this map has no line for stays unlocated.

    A Senate race has no district of its own: its key is '<ST>-SEN' and the
    client heats the whole state, so it is kept as a district row (that is what
    the ranked list reads as 'CA, Senate') and only its state has to exist.
    """
    # Schedule E states the race actually targeted. A filer may retain the
    # candidate's old House ID while reporting a Senate race (Barr, 2026).
    # Use those explicit office fields before consulting the ID's master row.
    # F7: the cycle lets a single-seat state's filed district 01 read as the
    # at-large seat this cycle's map draws.
    filing = filing or {}
    if filing.get('candidate_office'):
        key = district_geo._candidate_key(str(filing.get('candidate_office_state') or ''),
                                          str(filing['candidate_office']),
                                          str(filing.get('candidate_office_district') or ''),
                                          cycle)
        if key is None:
            unlocated.add(f'{candidate_id}: filed candidate office geography is incomplete', amount)
            return
    else:
        key = district_geo.district_for_candidate(candidate_id, cycle)
    senate = bool(key) and key.endswith('-' + district_geo.OFFICE_SENATE)
    drawable = (state_exists(key[:2], cycle) if senate
                else bool(key) and district_geo.district_exists(key, cycle))
    if key and DISTRICT_KEY.fullmatch(key) and drawable:
        bucket.add_district(key, amount, who=who, direction=direction,
                            filing=downstream.filing_record(filing) if filing else None,
                            filings=filings)
        return
    if key == district_geo.OFFICE_PRESIDENT:
        unlocated.add('presidential race: national, with no district or state of its own', amount)
        return
    if key:
        unlocated.add(f'{candidate_id} is filed in {key}, a seat this cycle\'s map has no line for',
                      amount)
        return
    unlocated.add(district_geo.last_problem
                  or f'{candidate_id or "an unnamed candidate"} is in no FEC candidate master', amount)


# -------------------------------------------------------------- the snapshots

def snapshot_units(root, identifier):
    directory = Path(root) / identifier
    try:
        snapshot_path = directory / 'snapshots.json'
        raw = snapshot_path.read_bytes()
        payload = json.loads(raw)
    except (OSError, ValueError) as error:
        raise ValueError(M_SNAPSHOT) from error
    if not isinstance(payload, list) or not payload:
        raise ValueError(M_SNAPSHOT)
    signature = hashlib.sha256(raw).hexdigest()
    payload = [{**unit, '_source':str(snapshot_path), '_sha256':signature} for unit in payload]
    try:
        evidence = json.loads((directory / 'evidence.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        evidence = {}
    return payload, evidence


def company_keys(evidence):
    """The snapshot's own contributor company and its aliases, as name keys."""
    names = [evidence.get('company') or '', *(evidence.get('aliases') or [])]
    return {name_key(name) for name in names if name_key(name)}


def snapshot_unit(units, committee, cycle):
    year = district_geo.normalise_cycle(cycle)
    for unit in units:
        if str(unit.get('committee_id') or '').upper() != committee:
            continue
        if year is None or district_geo.normalise_cycle(unit.get('cycle')) == year:
            return unit
    raise ValueError(M_CYCLE if any(str(unit.get('committee_id') or '').upper() == committee
                                    for unit in units) else M_COMMITTEE)


def snapshot_spent(unit, evidence, cycle):
    """All disbursements, in four kinds, reconciled to the reported total."""
    buckets = {name: Bucket(name) for name in ('candidates', 'transfers', 'operating', 'conversions')}
    unlocated = Unlocated()
    aliases = company_keys(evidence)

    for row in downstream.observations(unit, 'expenditures'):
        if is_memo(row):
            continue
        amount = downstream.amount(row.get('expenditure_amount'))
        direction = {'S': 'support', 'O': 'oppose'}.get(
            str(row.get('support_oppose_indicator') or '').strip().upper(), '')
        place_candidate(buckets['candidates'], unlocated, amount, row.get('candidate_id'), cycle,
                        who=str(row.get('candidate_name') or row.get('candidate_id') or ''),
                        direction=direction, filing=row)

    for row in downstream.observations(unit, 'disbursements'):
        if is_memo(row):
            continue
        amount = downstream.amount(row.get('disbursement_amount'))
        line = line_of(row)
        recipient = str(row.get('recipient_committee_id') or '').strip().upper()
        name = str(row.get('recipient_name') or row.get('recipient_committee') or '')
        # F8: transfer_rule names the leg by transaction type, whatever line
        # the row was filed on; a row with no type falls back to lines 22-23.
        leg, _basis = downstream.schedule_b_leg(row)
        if leg == downstream.transfer_rule.CANDIDATE:
            place_candidate(buckets['candidates'], unlocated, amount, row['candidate_id'],
                            cycle, who=name, direction='', filing=row)
            continue
        if leg == downstream.transfer_rule.TRANSFER:
            if not COMMITTEE.fullmatch(recipient):
                unlocated.add(f'a line {line or "(blank)"} payment with no recipient committee id', amount)
                continue
            state = district_geo.committee_state(recipient, cycle)
            if state:
                buckets['transfers'].add_state(state, amount, who=name, filing=downstream.filing_record(row))
            else:
                unlocated.add(district_geo.last_problem
                              or f'{recipient} is in no FEC committee master', amount)
            continue
        if line in ('22', '23'):
            # Filed on a transfer line, yet no transfer: its own type says it is
            # something else (an in-kind 24Z, say), or it names no committee.
            code = downstream.transfer_rule.transaction_type(row)
            unlocated.add(f'a line {line} payment of transaction type {code}, which is not a transfer'
                          if code and code not in downstream.transfer_rule.TRANSFER_TYPES
                          else f'a line {line} payment with no recipient committee id', amount)
            continue
        if line != '21B':
            unlocated.add(f'Schedule B line {line or "(blank)"} carries no recipient geography', amount)
            continue
        description = str(row.get('disbursement_description') or '')
        conversion = bool(aliases and name_key(name) in aliases and CONVERSION_TEXT.search(description.upper()))
        bucket = buckets['conversions'] if conversion else buckets['operating']
        place_by_state(bucket, unlocated, amount, row.get('recipient_zip'), row.get('recipient_state'),
                       cycle, who=name, reason_prefix='an operating payment could not be placed: ',
                       filing=downstream.filing_record(row))

    reported = downstream.amount((unit.get('totals') or {}).get('disbursements'))
    return buckets, unlocated, reported


def transfers_not_itemized(unit):
    """(amount, stated, itemized): lines 22-23 money the summary reports and the
    saved Schedule B does not itemize.

    F6 (Paul, 2026-09-25): no dollar may count twice. A transfer the summary
    reports but no Schedule B row itemizes has no known recipient, so it can be
    neither followed nor removed as internal; counted as spending it counts
    again wherever its recipient spends it. FAIRSHAKE 2026 in the 8 Sep
    snapshot reports $65,000,000 on line 22 and itemizes $51,000,000, while the
    saved network already holds both recipients' spending. The gap is compared
    line for line - the summary's own lines against the itemized rows filed on
    them - and is never negative. A summary stating neither line discloses
    nothing: (0, None, None).
    """
    # The comparison itself is downstream.summary_transfers, the one the PAC
    # network's per-committee reconciliation also reads (F4).
    stated, itemized = downstream.summary_transfers(unit)
    if stated is None:
        return Decimal(0), None, None
    return max(Decimal(0), stated - itemized), stated, itemized


def _unit_name(unit, committee):
    return next(((row.get('committee') or {}).get('name') for row in unit.get('receipts') or []
                 if (row.get('committee') or {}).get('name')), committee)


def network_spent(found, evidence, cycle, notes=None):
    """All saved network disbursements, with internal transfers counted once.

    Transfer dollars leave one PAC and enter another; only the destination's
    eventual disbursements belong in the consolidated outflow. Unexpanded
    transfers remain visible at their registered state. Transfers the summary
    reports but no row itemizes are left out of the total (F6).

    `notes`, when a dict is passed, receives what the total leaves out and what
    the old line rule decided: 'transfers_not_itemized' (one line per
    committee/cycle), 'transfer_rule' (rows with no transaction type, and the
    legs and dollars the line fallback placed), 'reload_rule' (F11: the
    reload rows counted once, and the rows it could not be applied to) and
    'date_rule' (F12: the activity dated before each reached committee's gate,
    left out of the total, and the undated rows counted with a flag).
    """
    buckets = {name: Bucket(name) for name in ('candidates', 'transfers', 'operating', 'conversions')}
    unlocated, reported = Unlocated(), Decimal(0)
    rule = downstream.transfer_rule
    disclosed = []
    census = {'transaction_types': sorted(rule.TRANSFER_TYPES), 'rows_without_transaction_type': 0,
              'legs_by_line_fallback': 0, 'amount_by_line_fallback': Decimal(0)}
    reloads = rule.new_reload_census()
    dates = {'rule': 'earliest_arrival', 'gated_committees': 0, 'rows_before_gate': 0,
             'amount_before_gate': Decimal(0), 'rows_date_unverified': 0,
             'amount_date_unverified': Decimal(0), 'before_gate': []}
    for key, unit in found['units'].items():
        # F12: a reached committee counts only its rows dated on or after its gate, the
        # date the network's money could first have reached it (a root counts whole).
        # The rows before it are left out of both the places and the total, so the
        # balancing residual between summary and itemized rows is the same as before.
        gate = downstream.gate_of(found, key)
        paid = list(downstream.observations(unit, 'disbursements', gate=gate))
        spent = list(downstream.observations(unit, 'expenditures', gate=gate))
        internal = {id(row) for destination, value, row in downstream.transfers(unit, gate=gate)
                    if (key[0], destination) in found['units']}
        removed = sum((money(row.get('disbursement_amount')) for row in paid if id(row) in internal), Decimal(0))
        before, before_rows = Decimal(0), 0
        if gate is not None:
            dates['gated_committees'] += 1
            kept = {id(row) for row in paid} | {id(row) for row in spent}
            for schedule, field in (('disbursements', 'disbursement_amount'), ('expenditures', 'expenditure_amount')):
                for row in downstream.observations(unit, schedule):
                    if is_memo(row):
                        continue
                    if id(row) not in kept:
                        before += money(row.get(field))
                        before_rows += 1
                    elif not downstream.arrival_gates.row_date(row, schedule):
                        dates['rows_date_unverified'] += 1
                        dates['amount_date_unverified'] += money(row.get(field))
            if before_rows:
                dates['rows_before_gate'] += before_rows
                dates['amount_before_gate'] += before
                dates['before_gate'].append({'committee': key[1], 'cycle': key[0],
                                             'committee_name': _unit_name(unit, key[1]), 'gate': gate,
                                             'rows': before_rows, 'amount': cash(before)})
        visible = {**unit, 'disbursements': [row for row in paid if id(row) not in internal],
                   'expenditures': spent}
        own, missing, total = snapshot_spent(visible, evidence, cycle)
        gap, stated, itemized = transfers_not_itemized(unit)
        reported += total - removed - gap - before
        if notes is not None:
            if gap > 0:
                disclosed.append({'committee': key[1], 'cycle': key[0],
                                  'committee_name': _unit_name(unit, key[1]), 'reason': NOT_ITEMIZED,
                                  'amount': cash(gap), 'summary_amount': cash(stated),
                                  'itemized_amount': cash(itemized)})
            for row in downstream.observations(unit, 'disbursements'):
                if is_memo(row):
                    continue
                if not rule.transaction_type(row):
                    census['rows_without_transaction_type'] += 1
                leg, basis = downstream.schedule_b_leg(row)
                if leg and basis == rule.FALLBACK_BASIS:
                    census['legs_by_line_fallback'] += 1
                    census['amount_by_line_fallback'] += money(row.get('disbursement_amount'))
            for schedule in ('disbursements', 'expenditures'):
                for _row in downstream.observations(unit, schedule, census=reloads):
                    pass
        for name, bucket in own.items():
            for table in ('districts', 'states'):
                for place, entry in getattr(bucket, table).items():
                    target = getattr(buckets[name], table)[place]
                    for field in ('amount','rows','support','oppose'):
                        target[field] += entry[field]
                    target['split'] |= entry['split']
                    target['bases'].update(entry['bases'])
                    for who, value in entry['who'].items():
                        label = f'{who} via {key[1]}'
                        target['who'][label] = target['who'].get(label, Decimal(0)) + value
                        target['filings'][label].extend(entry['filings'].get(who, []))
        for reason, entry in missing.rows.items():
            unlocated.add(f'{key[1]}: {reason}', entry['amount'], entry['rows'])
    if notes is not None:
        notes['transfers_not_itemized'] = disclosed
        notes['transfer_rule'] = {**census, 'amount_by_line_fallback': cash(census['amount_by_line_fallback'])}
        notes['reload_rule'] = {'key': rule.RELOAD_KEY, **reloads, 'reload_amount': cash(reloads['reload_amount'])}
        notes['date_rule'] = {**dates, 'amount_before_gate': cash(dates['amount_before_gate']),
                              # The total counts each reached committee from its gate (a root
                              # whole); the balancing residual still compares each committee's
                              # full-period summary with all its itemized rows.
                              'total_basis': 'from_gates', 'residual_basis': 'full_period',
                              'amount_date_unverified': cash(dates['amount_date_unverified']),
                              'excluded_transfers': list(found.get('excluded_before_gate') or []),
                              # Carryover (option (b)): each gate's source - root, carryover or
                              # in-cycle - and the committees the previous cycle's transfers opened.
                              'gates': [{'cycle': key[0], 'committee': key[1], 'gate': gate,
                                         'gate_source': (found.get('gate_sources') or {}).get(key, '')}
                                        for key, gate in (found.get('gates') or {}).items()],
                              'carryover': found.get('carryover')}
    return buckets, unlocated, reported


def snapshot_raised(unit, cycle, cache=None):
    bucket = Bucket('contributors')
    unlocated = Unlocated()
    mix = Mix()
    zips = Zips()
    # F11: the receipts downstream reads - one per source ID, a reload once.
    for row in downstream.observations(unit, 'receipts'):
        if is_memo(row):
            continue
        amount = downstream.amount(row.get('contribution_receipt_amount'))
        who = str(row.get('contributor_name') or '')
        filing = downstream.filing_record(row)
        zips.add(zip5(row.get('contributor_zip')), amount, who=who,
                 city=str(row.get('contributor_city') or '').strip(),
                 state=state_of(row.get('contributor_state')))
        # The cache is content-addressed by ADDRESS, so a snapshot receipt is
        # placed by street whenever some import already paid for that lookup.
        if mix.street(cache, row, cycle, bucket, unlocated, amount, who, filing=filing):
            continue
        place_by_zip(bucket, unlocated, amount, row.get('contributor_zip'),
                     row.get('contributor_state'), cycle, who=who,
                     reason_prefix='a receipt could not be placed: ', filing=filing)
    reported = downstream.amount((unit.get('totals') or {}).get('receipts'))
    return {'contributors': bucket}, unlocated, reported, mix, zips


# ---------------------------------------------------------------- the imports

ZIP_FIELDS = ('contributor_zip', 'zip', 'zip_code', 'contributor_zip_code', 'zip5', 'postal_code')
STATE_FIELDS = ('contributor_state', 'state', 'contributor_st', 'st')


def import_rows(store, identifier, committee, cycle):
    """(headers, columns, [(line_num, cells)]) for one committee's counted rows.

    Only SUPPORTED rows: an excluded row is out of the workbook's own totals
    (S7), so putting it on the map would place money the file says not to count.
    """
    value = store.ready(identifier)
    catalog = evidence_csv.stored_catalog(value)
    committee = committee or (catalog['committees'][0]['id'] if catalog['committees'] else '')
    chosen = next((item for item in catalog['committees'] if item['id'] == committee), None)
    if chosen is None:
        raise ValueError(M_COMMITTEE)
    if cycle and cycle not in catalog['cycles']:
        raise ValueError(M_CYCLE)
    database = store.database_path(identifier)
    if not database.is_file():
        raise ValueError(evidence_csv.M_GONE(identifier))
    stored = '' if committee == 'unassigned' else committee
    where, parameters = 'committee_id = ?', [stored]
    if cycle:
        where, parameters = where + ' AND cycle = ?', parameters + [cycle]
    try:
        with closing(sqlite3.connect(database)) as connection:
            rows = connection.execute(
                f'SELECT line_num, cells FROM csv_rows WHERE {where} AND status = ? ORDER BY row_number',
                parameters + ['supported']).fetchall()
    except sqlite3.Error as error:
        raise ValueError(evidence_csv.M_GONE(identifier)) from error
    headers = value.get('columns', [])
    return catalog, chosen, headers, [evidence_csv.key(item) for item in headers], rows


def import_raised(store, identifier, committee, cycle, cache=None):
    catalog, chosen, headers, columns, rows = import_rows(store, identifier, committee, cycle)
    bucket, unlocated, reported = Bucket('contributors'), Unlocated(), Decimal(0)
    mix = Mix()
    zips = Zips()
    for line_number, cells in rows:
        row = evidence_csv.rebuild(headers, columns, line_number, json.loads(cells))
        values = dict(zip(columns, row['raw'].values()))
        amount = row['_amount'] or Decimal(0)
        reported += amount
        who = row['counterparty'] or evidence_csv.BLANK_COUNTERPARTY
        # The ZIP is recorded for EVERY row, before the street/ZIP branch below,
        # so the ZIP layer counts a street-matched row too and its sum is the
        # reported total. geocode.CITY_FIELDS is the same column vocabulary the
        # geocoder reads, so the circle is labelled with the town the filing
        # names rather than one derived from the ZIP.
        zips.add(zip5(evidence_csv.first(values, ZIP_FIELDS)), amount, who=who,
                 city=evidence_csv.first(values, geocode.CITY_FIELDS),
                 state=state_of(evidence_csv.first(values, STATE_FIELDS)))
        if mix.street(cache, values, cycle, bucket, unlocated, amount, who):
            continue
        place_by_zip(bucket, unlocated, amount,
                     evidence_csv.first(values, ZIP_FIELDS), evidence_csv.first(values, STATE_FIELDS),
                     cycle, who=who,
                     reason_prefix='a contributor row could not be placed: ')
    return catalog, chosen, {'contributors': bucket}, unlocated, reported, mix, zips


def counterparty_count(store, identifier, committee, cycle):
    """How many DISTINCT counterparties one pane's supported rows name.

    The pane's own summary ranks only the top ten counterparties, so the count
    is not in it. Counting here, over exactly the rows the map places, keeps the
    figure the pane prints and the figure the map draws the same number.
    """
    _catalog, _chosen, headers, columns, rows = import_rows(store, identifier, committee, cycle)
    names = set()
    for line_number, cells in rows:
        row = evidence_csv.rebuild(headers, columns, line_number, json.loads(cells))
        names.add(row['counterparty'] or evidence_csv.BLANK_COUNTERPARTY)
    return {'contributors': len(names), 'rows': len(rows)}


def _direct_filing_records(records, expected):
    """A citation set must account for the whole displayed amount, not a slice."""
    if not records or any(not isinstance(row, dict) or not re.fullmatch(
            r'https://docquery\.fec\.gov/cgi-bin/fecimg/\?\d+',
            str(row.get('source_url') or '')) for row in records):
        return []
    try:
        if sum((downstream.amount(row.get('amount')) for row in records), Decimal(0)) != expected:
            return []
    except (ValueError, TypeError):
        return []
    return records


def _saved_candidate_filings(committee, cycle, snapshot_root):
    """Supplement older reports only from an exact saved committee/cycle."""
    key = (str(district_geo.normalise_cycle(cycle)), committee)
    try:
        unit = downstream.inventory(snapshot_root).get(key)
        if unit is None:
            return {}
        rows = downstream.activity({'units': {key: unit}})
    except (OSError, ValueError):
        return {}
    return {(row['candidate_id'], row['direction']): (row['amount'], row['filings'])
            for row in rows}


def import_spent(store, identifier, committee, cycle, snapshot_root=None):
    """The workbook's own Downstream Activity sheet, read by Graph's reader.

    The imported cycle sheets are RECEIPT rows: every row's committee id is the
    recipient and every contributor is a person or a company, so a committee is
    never the source there. The same workbook's Downstream Activity sheet is
    the PAC-to-candidate ledger, and graph_data already streams exactly the
    seven columns needed, so it is reused rather than reimplemented. A file
    without that sheet honestly has no outbound rows and says so.
    """
    catalog, chosen, _headers, _columns, _rows = import_rows(store, identifier, committee, cycle)
    committee = chosen['id']
    bucket, unlocated, reported = Bucket('candidates'), Unlocated(), Decimal(0)
    try:
        import graph_data
        source = store.source_path(identifier)
        activity = graph_data.report_activity(graph_data.stamp(source), include_filings=True)
    except Exception as error:  # a CSV, a workbook without the sheet, a bad zip
        return catalog, chosen, {}, Unlocated(), Decimal(0), f'{M_NO_OUTBOUND} ({error})'
    year = district_geo.normalise_cycle(cycle)
    # New reports carry transaction records in the worksheet. Older reports
    # carry only a bulk ZIP URL; a saved FEC snapshot may supply citations, but
    # only when its same candidate/direction total exactly matches the report.
    saved_filings = None
    selected = [row for row in activity if row['committee'] == committee and
                (year is None or district_geo.normalise_cycle(row.get('cycle')) == year)]
    resolved = []
    for row in selected:
        amount = row['amount']
        reported += amount
        filings = _direct_filing_records(row.get('filings') or [], amount)
        if not filings:
            if saved_filings is None:
                saved_filings = _saved_candidate_filings(committee, cycle, snapshot_root)
            saved_amount, saved_records = saved_filings.get(
                (row.get('candidate_id'), row.get('direction')), (None, []))
            if saved_amount == amount:
                filings = _direct_filing_records(saved_records, amount)
        resolved.append((row, filings))
    # The map folds several directions into one candidate cell. A link to just
    # one component must never appear to document the whole folded amount.
    incomplete = {str(row.get('candidate') or row.get('candidate_id') or '')
                  for row, filings in resolved if not filings}
    for row, filings in resolved:
        amount = row['amount']
        direction = {'support': 'support', 'oppose': 'oppose'}.get(
            str(row.get('direction') or '').strip().lower(), '')
        who = str(row.get('candidate') or row.get('candidate_id') or '')
        place_candidate(bucket, unlocated, amount, row.get('candidate_id'), cycle,
                        who=who, direction=direction,
                        filings=[] if who in incomplete else filings)
    if not selected:
        return catalog, chosen, {}, Unlocated(), Decimal(0), M_NO_OUTBOUND
    return catalog, chosen, {'candidates': bucket}, unlocated, reported, ''


def workbook_filing_pointer(store, identifier, committee):
    """S3, 2026-09-24: what a spend row says when its workbook carries no filing links.

    The published Overview workbooks predate per-transaction citations: their
    Downstream Activity sheet has no Filing Records column and its Source URL
    is the whole-cycle PAS2 bulk ZIP, so a row with no exactly matching saved
    snapshot read only "FEC filing link unavailable". Such a row now says that
    the links are not in this workbook and points at the source committee's
    FEC page instead (fec_citation.workbook_pointer). A workbook whose sheet
    DOES carry filing records gets no pointer: a row there without a citation
    is a partial or folded amount, and "unavailable" stays the true answer.
    The activity read is the one import_spent just made (graph_data memoises
    it by the file's stamp), so this costs no second pass over the workbook.
    """
    try:
        import graph_data
        activity = graph_data.report_activity(graph_data.stamp(store.source_path(identifier)),
                                              include_filings=True)
    except Exception:  # the same failures import_spent reports as its reason
        return None
    if any(row.get('filings') for row in activity):
        return None
    return fec_citation.workbook_pointer(committee)


def activity_totals(store, identifier, committee, cycle):
    """(total, rows, reason) for the workbook's outbound rows - no geography.

    The same selection import_spent makes, with the placing left out, so the
    'which side has money' question can be answered without drawing a map. A
    workbook with no Downstream Activity sheet answers the reason import_spent
    would have answered, which is what the disabled button's title says.
    """
    try:
        import graph_data
        source = store.source_path(identifier)
        activity = graph_data.report_activity(graph_data.stamp(source))
    except Exception as error:  # a CSV, a workbook without the sheet, a bad zip
        return Decimal(0), 0, f'{M_NO_OUTBOUND} ({error})'
    year = district_geo.normalise_cycle(cycle)
    total, count = Decimal(0), 0
    for row in activity:
        if row['committee'] != committee:
            continue
        if year is not None and district_geo.normalise_cycle(row.get('cycle')) != year:
            continue
        count += 1
        total += row['amount']
    return (total, count, '') if count else (Decimal(0), 0, M_NO_OUTBOUND)


def raised_totals(store, identifier, committee, cycle):
    """(total, rows, reason) for one import's supported receipt rows."""
    _catalog, _chosen, headers, columns, rows = import_rows(store, identifier, committee, cycle)
    total = Decimal(0)
    for line_number, cells in rows:
        row = evidence_csv.rebuild(headers, columns, line_number, json.loads(cells))
        total += row['_amount'] or Decimal(0)
    return (total, len(rows), '') if rows else (Decimal(0), 0, M_NO_RAISED)


def side_totals(source, identifier, committee, cycle, store=None, unit=None, known=None, snapshot_root=None):
    """What each direction would report, so the page opens on the side with money.

    Cheap on purpose: no buckets, no boundary file, no crosswalk - just the sum
    of the rows each direction reads. The direction already computed is copied
    from `known` rather than read twice. A probe that raises is reported as a
    refusal sentence, never as a 500: the map the reader asked for still opens.
    """
    sides = {}
    for direction in DIRECTIONS:
        if known and known.get('direction') == direction:
            sides[direction] = dict(known['side'])
            continue
        try:
            root_key = (str(district_geo.normalise_cycle(cycle)), committee)
            units = dict(downstream.inventory(snapshot_root)) if direction == 'spent' and source == 'snapshot' and snapshot_root is not None else {}
            if direction == 'spent' and source == 'snapshot' and unit is not None:
                units[root_key] = unit
            if direction == 'spent' and root_key in units:
                found = downstream.network([root_key], units)
                buckets, missing, total = network_spent(found, {}, cycle)
                count = sum(sum(e['rows'] for e in list(b.districts.values()) + list(b.states.values()))
                            for b in buckets.values()) + sum(e['rows'] for e in missing.rows.values())
                sides[direction] = {'reported_total':cash(total), 'rows':count, 'reason':downstream.NOTE}
                continue
            if source == 'snapshot':
                totals = (unit or {}).get('totals') or {}
                field = 'receipts' if direction == 'raised' else 'disbursements'
                counted = [row for row in (downstream.observations(unit or {}, 'receipts') if direction == 'raised'
                                           else list((unit or {}).get('expenditures') or [])
                                                + list((unit or {}).get('disbursements') or []))
                           if not is_memo(row)]
                total, count, reason = downstream.amount(totals.get(field)), len(counted), ''
                if not count and not total:
                    reason = M_NO_RAISED if direction == 'raised' else M_NO_SPENT
            elif direction == 'raised':
                total, count, reason = raised_totals(store, identifier, committee, cycle)
            else:
                total, count, reason = activity_totals(store, identifier, committee, cycle)
        except ValueError as error:
            total, count, reason = Decimal(0), 0, str(error)
        except Exception as error:  # a probe must never cost the reader the map
            total, count, reason = Decimal(0), 0, f'This side could not be read ({error}).'
        sides[direction] = {'reported_total': cash(total), 'rows': int(count), 'reason': reason}
    return sides


# ------------------------------------------------------------------ the route

def source_cycles(source, identifier, committee='', store=None, snapshot_root=None):
    """The concrete cycles this pane's source records, newest first.

    Ruling A's other half: the refusal has to be able to NAME them, and the
    client's newest-cycle fallback reads the same list off the pane.
    """
    years = set()
    if source == 'snapshot':
        units, _evidence = snapshot_units(snapshot_root, identifier)
        for unit in units:
            if committee and str(unit.get('committee_id') or '').upper() != committee:
                continue
            year = district_geo.normalise_cycle(unit.get('cycle'))
            if year:
                years.add(year)
    elif store is not None:
        catalog = evidence_csv.stored_catalog(store.ready(identifier))
        for value in catalog.get('cycles') or []:
            year = district_geo.normalise_cycle(value)
            if year:
                years.add(year)
    return sorted(years, reverse=True)


def geography(source, identifier, committee='', cycle='', direction='spent',
              store=None, snapshot_root=None, data_root=None):
    """The map payload for one pane. Raises ValueError with a reader's message."""
    source = str(source or '').strip().lower()
    direction = str(direction or 'spent').strip().lower()
    committee = str(committee or '').strip().upper()
    cycle = str(cycle or '').strip()
    if source not in SOURCES:
        raise ValueError(M_SOURCE)
    if direction not in DIRECTIONS:
        raise ValueError(M_DIRECTION)
    if not IDENTIFIER.fullmatch(str(identifier or '')):
        raise ValueError(M_SOURCE)
    # RULING A. A blank cycle is refused HERE, before any reading, and the
    # refusal names the cycles this source will accept. It is deliberately not
    # a quiet fallback to the newest cycle: the map would then disagree with
    # the pane that opened it without saying so.
    if not cycle:
        raise ValueError(M_BLANK_CYCLE(source_cycles(source, identifier, committee, store,
                                                     snapshot_root)))

    cache = None
    if data_root is None and store is not None:
        data_root = getattr(store, 'data_root', None)
    if data_root is not None:
        try:
            # create=False: reading a map never writes into the data root, so a
            # read-only preview sharing another console's root stays read-only.
            cache = geocode.Cache(data_root, create=False)
        except (OSError, sqlite3.Error):
            # No cache yet, or one this console cannot open: that costs
            # precision, never a map. Every row stays ZIP-based and is counted.
            cache = None

    reason, label, name = '', '', ''
    pointer = None
    mix = Mix()
    zips = Zips()
    unit = None
    if source == 'snapshot':
        if not COMMITTEE.fullmatch(committee):
            raise ValueError(M_COMMITTEE)
        units, evidence = snapshot_units(snapshot_root, identifier)
        unit = snapshot_unit(units, committee, cycle)
        cycle = str(district_geo.normalise_cycle(unit.get('cycle')) or cycle)
        name = next((item.get('committee_name') for item in evidence.get('committee_finances') or []
                     if str(item.get('committee_id') or '').upper() == committee), '') or committee
        label = str(evidence.get('company') or identifier)
        if direction == 'spent':
            buckets, unlocated, reported = snapshot_spent(unit, evidence, cycle)
        else:
            buckets, unlocated, reported, mix, zips = snapshot_raised(unit, cycle, cache)
    else:
        if store is None:
            raise ValueError(M_SOURCE)
        if direction == 'raised':
            catalog, chosen, buckets, unlocated, reported, mix, zips = import_raised(
                store, identifier, committee, cycle, cache)
        else:
            catalog, chosen, buckets, unlocated, reported, reason = import_spent(
                store, identifier, committee, cycle, snapshot_root)
            pointer = workbook_filing_pointer(store, identifier, chosen['id']) if buckets else None
        committee, name = chosen['id'], chosen.get('name') or chosen['id']
        label = catalog.get('industry') or catalog.get('name') or identifier

    network = None
    notes = {'transfers_not_itemized': [], 'transfer_rule': None, 'reload_rule': None, 'date_rule': None}
    if direction == 'spent' and source == 'snapshot':
        units = dict(downstream.inventory(snapshot_root))
        root_key = (str(district_geo.normalise_cycle(cycle)), committee)
        # The root is the snapshot the reader selected, not whichever artifact
        # happens to have the newest filesystem timestamp. Imported CSVs retain
        # their own scope too; they are not silently replaced by a snapshot.
        units[root_key] = unit
        if root_key in units:
            network = downstream.network([root_key], units)
            buckets, unlocated, reported = network_spent(network, evidence if source == 'snapshot' else {}, cycle,
                                                         notes=notes)
            reason = downstream.NOTE
            if network['missing']:
                reason += ' Missing destination snapshots: ' + ', '.join(k[1] for k in network['missing']) + '.'
            reason += date_rule_sentence(notes['date_rule'])
    located = sum((bucket.total() for bucket in buckets.values()), Decimal(0))
    residual = money(reported) - located - unlocated.total()
    residual_reason = None
    if residual.copy_abs() > CENT:
        residual_reason = _residual_reason(residual)
        unlocated.add(residual_reason, residual)
    unlocated_lines = marked_unlocated(unlocated.payload(), residual_reason)
    reversals = sum((entry['amount'] for reason, entry in unlocated.rows.items()
                     if reason != residual_reason and entry['amount'] < 0), Decimal(0))

    vintage = district_geo.vintage_for_cycle(cycle) if cycle else None
    basis = district_geo.zip_basis(cycle) if cycle else None
    index = district_geo.index() or {}
    entry = ((index.get('vintages') or {}).get(vintage) or {}) if vintage else {}
    kinds = [buckets[name_].payload('') for name_ in
             ('candidates', 'transfers', 'operating', 'conversions', 'contributors')
             if name_ in buckets]
    by_state, by_region = geography_levels(buckets, cycle)
    zip_layer = zips.payload() if direction == 'raised' else {
        'by_zip': [], 'zip_truncated': 0, 'zip_truncated_amount': 0.0,
        'zip_missing': {'amount': 0.0, 'rows': 0}}
    # Item 7 of F4 (Paul approved 2026-09-25): shares are computed on positive
    # located spending - the sum of the places that net above zero (every
    # district and state row of every kind), or on a raised map's ZIP layers the
    # ZIP-placed receipts that do. A place that nets below zero is a reversal: it
    # keeps its signed amount and gets no share, as do unlocated money and the
    # refunds and reversals among the unlocated lines, which stay on their own
    # lines. There is no base only when nothing placed nets above zero.
    shares = share_bases(kinds, zip_layer, direction)
    sides = side_totals(source, identifier, committee, cycle, store=store, unit=unit,
                        snapshot_root=snapshot_root,
                        known={'direction': direction,
                               'side': {'reported_total': cash(reported),
                                        'rows': sum(kind['rows'] for kind in kinds),
                                        'reason': reason}})
    payload = {
        'source': source, 'id': identifier, 'direction': direction, 'cycle': cycle,
        'committee': {'id': committee, 'name': name}, 'label': label,
        'vintage': vintage, 'congress': entry.get('congress') or '',
        'canvas': {'width': index.get('width') or 975, 'height': index.get('height') or 610},
        'boundary_file': entry.get('file') or '',
        'zip_basis': basis['basis'] if basis else '',
        'reported_total': cash(reported), 'located_total': cash(located),
        'unlocated_total': cash(unlocated.total()),
        'reconciles': residual.copy_abs() <= CENT,
        'reconciliation_residual': cash(residual),
        'kinds': kinds, 'unlocated': unlocated_lines, 'stops': list(STOPS),
        # Contract v4 (item 7 of F4): the unlocated lines that net below zero -
        # refunds and reversals, not missing money - and what shares are of.
        'reversals_total': cash(reversals), 'share_base': shares,
        'tract_basis': (district_geo.tract_basis(cycle) or {}).get('basis') or '',
        # Contract v3. Three wider layers, each folded from the same bucket
        # tables the district layer draws, so every one of them adds back to the
        # reported total. The page picks a layer; it never re-aggregates.
        'by_state': by_state, 'by_region': by_region,
        'region_order': list(district_geo.REGION_ORDER) + [REGION_UNPLACED],
        'zip_points_file': 'zcta_points.json',
        'sides': sides,
        'spend_basis': 'consolidated_saved_network' if network else 'source',
        # F6: reported on a summary's lines 22-23, itemized nowhere. Disclosed
        # here and in no total: not in reported, located or unlocated.
        'transfers_not_itemized': notes['transfers_not_itemized'],
        'transfers_not_itemized_total': cash(sum((money(row['amount']) for row in
                                                  notes['transfers_not_itemized']), Decimal(0))),
        # F12: each line compares its committee's full-period summary with all of
        # its itemized rows, never the date-limited subset the total counts.
        'transfers_not_itemized_basis': 'full_period',
        # F8: the legs the old line rule still decided, for rows that carry no
        # transaction type. None when no saved network was read.
        'transfer_rule': notes['transfer_rule'],
        # F11: reload rows the network's totals counted once, and the rows with
        # no transaction id or file number the rule was not applied to.
        'reload_rule': notes['reload_rule'],
        # F12: each reached committee's gate - the date the network's money could
        # first have reached it - and the activity dated before it, left out of
        # every total here; undated rows count, flagged date_unverified (D-040).
        'date_rule': notes['date_rule'],
        'downstream_note': reason if network else '',
        'downstream_edges': network['edges'] if network else [],
        'downstream_committees': sorted(k[1] for k in network['units']) if network else [],
        'downstream_sources': downstream.sources(network) if network else [],
        **zip_layer, **mix.payload()}
    if reason:
        payload['reason'] = reason
    if pointer:
        # S3: only an imported workbook's spend map, and only when that
        # workbook carries no filing links at all (workbook_filing_pointer).
        payload['filing_fallback'] = pointer
    if not vintage and cycle:
        payload['reason'] = payload.get('reason') or (district_geo.last_problem or '')
    return payload


def date_rule_sentence(rule):
    """F12: what the date rule left out of this map, in the words the page already shows."""
    if not rule:
        return ''
    words = ''
    if rule['rows_before_gate']:
        words += (f" Date rule: ${rule['amount_before_gate']:,.2f} in {rule['rows_before_gate']:,} rows of reached "
                  f"committees' activity is dated before the network's money could reach them and is not counted.")
    if rule['rows_date_unverified']:
        words += (f" ${rule['amount_date_unverified']:,.2f} in {rule['rows_date_unverified']:,} undated rows "
                  f"is counted, flagged date unverified.")
    carried = rule.get('carryover') or {}
    if carried.get('opened'):
        words += (f" Carryover: the previous cycle's transfers opened the gate of {len(carried['opened']):,} "
                  f"committee(s), so their spending counts from the cycle's first day.")
    if carried.get('status') == 'unknown':
        words += ' Carryover unknown: the previous cycle is not saved for this network.'
    elif carried.get('status') == 'partial':
        words += ' Carryover partial: some previous-cycle snapshots are not saved.'
    return words


def share_bases(kinds, zip_layer, direction):
    """{places, zips}: the positive located money shares are computed on (contract v4)."""
    places = sum((money(row.get('amount')) for kind in kinds
                  for row in (*(kind.get('districts') or []), *(kind.get('states') or []))
                  if money(row.get('amount')) > 0), Decimal(0))
    zips = (sum((money(row.get('amount')) for row in zip_layer.get('by_zip') or []
                 if money(row.get('amount')) > 0), Decimal(0))
            + max(Decimal(0), money(zip_layer.get('zip_truncated_amount'))))
    return {'places': cash(places) if places > 0 else None,
            'zips': cash(zips) if direction == 'raised' and zips > 0 else None}


def marked_unlocated(lines, residual_reason=None):
    """Each unlocated line says what it is (contract v4, item 7 of F4).

    'residual' is the balancing line between the summary and the itemized rows;
    'reversal' is a line that nets below zero - a refund or a reversal, not
    missing money - so the page lists it signed, outside the shares, instead of
    withdrawing every share; 'unlocated' is money no basis could place.
    """
    for line in lines:
        line['kind'] = ('residual' if line['reason'] == residual_reason
                        else 'reversal' if line['amount'] < 0 else 'unlocated')
    return lines


def _residual_reason(residual):
    if residual > 0:
        return ('unreconciled residual: the committee reported more than the saved itemised rows; '
                'unitemised activity or incomplete coverage may explain it, but neither is established')
    return ('the itemised rows add to more than the committee reported; '
            'the FEC summary and the schedules disagree')


# ----------------------------------------------------------- contract support

def contract_problems(payload):
    """Every way a payload breaks the evidence-geo contract, as plain sentences.

    Used by the contract test on both the recorded examples and the negative
    controls, and by the real producer output, so an example that drifts from
    what the producer emits fails in the same place a bad payload does.
    """
    problems = []
    if not isinstance(payload, dict):
        return ['the payload is not an object']
    for field in ('direction', 'cycle', 'reported_total', 'located_total',
                  'unlocated_total', 'kinds', 'unlocated', 'stops',
                  'geocoded_rows', 'zip_rows', 'pending_rows'):
        if field not in payload:
            problems.append(f'the payload has no {field}')
    if payload.get('direction') not in DIRECTIONS:
        problems.append('direction must be raised or spent')
    if not str(payload.get('cycle') or '').strip():
        problems.append('a map payload states no cycle; a map is drawn on one Congress')
    for field in ('geocoded_rows', 'zip_rows', 'pending_rows'):
        count = payload.get(field)
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            problems.append(f'{field} is not a row count')
    if not isinstance(payload.get('kinds'), list):
        problems.append('kinds must be a list')
        return problems
    located = Decimal(0)
    for kind in payload['kinds']:
        if not isinstance(kind, dict):
            problems.append('a kind is not an object')
            continue
        for field in ('kind', 'label', 'total', 'basis', 'default_on', 'districts', 'states'):
            if field not in kind:
                problems.append(f'kind {kind.get("kind")!r} has no {field}')
        if not kind.get('basis'):
            problems.append(f'kind {kind.get("kind")!r} states no basis')
        entries = list(kind.get('districts') or []) + list(kind.get('states') or [])
        inner = Decimal(0)
        for row in entries:
            if not isinstance(row, dict):
                problems.append(f'kind {kind.get("kind")!r} has a row that is not an object')
                continue
            key = str(row.get('key') or '')
            pattern = DISTRICT_KEY if row in (kind.get('districts') or []) else STATE_KEY
            if pattern is DISTRICT_KEY and not DISTRICT_KEY.fullmatch(key):
                problems.append(f'district key {key!r} is not a district')
            if pattern is STATE_KEY and not STATE_KEY.fullmatch(key):
                problems.append(f'state key {key!r} is not a state')
            share = row.get('share')
            if share is not None and (not isinstance(share, (int, float)) or not 0.0 <= float(share) <= 1.0):
                problems.append(f'share {share!r} on {key} is outside 0..1')
            if row.get('basis') not in ('', 'street', 'zip', 'zip_split'):
                problems.append(f'basis {row.get("basis")!r} on {key} is not a placing basis')
            if row.get('basis') == 'street' and row.get('split'):
                problems.append(f'{key} is street-matched and still marked split')
            inner += money(row.get('amount'))
        if (inner - money(kind.get('total'))).copy_abs() > CENT:
            problems.append(f'kind {kind.get("kind")!r} does not add to its own total')
        located += money(kind.get('total'))
    unlocated = Decimal(0)
    for item in payload.get('unlocated') or []:
        if not isinstance(item, dict) or not item.get('reason'):
            problems.append('an unlocated bucket states no reason')
            continue
        unlocated += money(item.get('amount'))
    if (located - money(payload.get('located_total'))).copy_abs() > CENT:
        problems.append('located_total does not add up from the kinds')
    if (unlocated - money(payload.get('unlocated_total'))).copy_abs() > CENT:
        problems.append('unlocated_total does not add up from the unlocated buckets')
    if (located + unlocated - money(payload.get('reported_total'))).copy_abs() > CENT:
        problems.append('located plus unlocated does not reconcile to the reported total')
    if list(payload.get('stops') or []) != list(STOPS):
        problems.append('the legend stops are not the published stops')
    problems.extend(level_problems(payload, located))
    problems.extend(not_itemized_problems(payload))
    problems.extend(rule_problems(payload))
    problems.extend(share_problems(payload))
    problems.extend(date_rule_problems(payload))
    return problems


def date_rule_problems(payload):
    """F12: the date rule is an object exactly when the map read a saved network, its
    counts are counts, and its left-out lines add up. Payloads written before F12
    carry no date_rule and are not refused."""
    if 'date_rule' not in payload:
        return []
    rule, problems = payload.get('date_rule'), []
    network = payload.get('spend_basis') == 'consolidated_saved_network'
    if 'spend_basis' in payload and network != isinstance(rule, dict):
        problems.append('date_rule is an object exactly when the map read a saved network')
    if not isinstance(rule, dict):
        return problems
    if rule.get('rule') != 'earliest_arrival':
        problems.append('date_rule names no earliest-arrival rule')
    for field in ('gated_committees', 'rows_before_gate', 'rows_date_unverified'):
        if not _count(rule.get(field)):
            problems.append(f'date_rule.{field} is not a row count')
    for field in ('amount_before_gate', 'amount_date_unverified'):
        if not _amount(rule.get(field)):
            problems.append(f'date_rule.{field} is not an amount')
    lines = rule.get('before_gate') if isinstance(rule.get('before_gate'), list) else []
    if (sum((money(line.get('amount')) for line in lines if isinstance(line, dict)), Decimal(0))
            - money(rule.get('amount_before_gate'))).copy_abs() > CENT:
        problems.append('date_rule.before_gate does not add up to amount_before_gate')
    carried = rule.get('carryover')
    if isinstance(carried, dict) and carried.get('status') not in ('known', 'partial', 'unknown', 'off'):
        problems.append('date_rule.carryover states no known, partial, unknown or off status')
    for gate in rule.get('gates') or []:
        if isinstance(gate, dict) and gate.get('gate_source') not in ('root', 'carryover', 'in-cycle'):
            problems.append(f"date_rule gate for {gate.get('committee')} states no source")
    return problems


def _count(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _amount(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def rule_problems(payload):
    """Contract v4: the F8 transfer-rule census and the F11 reload census.

    Each is an object on a map that read a saved network and null otherwise.
    Payloads written before lane B carry neither and are not refused.
    """
    problems = []
    rule = payload.get('transfer_rule')
    if rule is not None and 'transfer_rule' in payload:
        if not isinstance(rule, dict):
            problems.append('transfer_rule must be an object or null')
        else:
            if rule.get('transaction_types') != sorted(downstream.transfer_rule.TRANSFER_TYPES):
                problems.append("transfer_rule names transaction types other than the transfer index's")
            for field in ('rows_without_transaction_type', 'legs_by_line_fallback'):
                if not _count(rule.get(field)):
                    problems.append(f'transfer_rule.{field} is not a row count')
            if not _amount(rule.get('amount_by_line_fallback')):
                problems.append('transfer_rule.amount_by_line_fallback is not an amount')
            elif (_count(rule.get('legs_by_line_fallback')) and _count(rule.get('rows_without_transaction_type'))
                  and rule['legs_by_line_fallback'] > rule['rows_without_transaction_type']):
                problems.append('transfer_rule counts more line-fallback legs than rows without a transaction type')
    reload = payload.get('reload_rule')
    if reload is not None and 'reload_rule' in payload:
        if not isinstance(reload, dict):
            problems.append('reload_rule must be an object or null')
        else:
            if reload.get('key') != downstream.transfer_rule.RELOAD_KEY:
                problems.append('reload_rule does not name the reload key')
            for field in ('rows_without_transaction_id_or_file_number', 'reload_rows'):
                if not _count(reload.get(field)):
                    problems.append(f'reload_rule.{field} is not a row count')
            if not _amount(reload.get('reload_amount')):
                problems.append('reload_rule.reload_amount is not an amount')
            elif reload.get('reload_rows') == 0 and reload['reload_amount'] != 0:
                problems.append('reload_rule counts reload dollars with no reload rows')
    network = payload.get('spend_basis') == 'consolidated_saved_network'
    for field in ('transfer_rule', 'reload_rule'):
        if field in payload and 'spend_basis' in payload and network != isinstance(payload[field], dict):
            problems.append(f'{field} is an object exactly when the map read a saved network')
    return problems


def share_problems(payload):
    """Contract v4 (item 7 of F4): the unlocated lines say what they are, and the
    share bases are the positive located money. Payloads written before v4 carry
    neither and are not refused."""
    problems = []
    lines = [line for line in payload.get('unlocated') or [] if isinstance(line, dict)]
    if 'reversals_total' in payload or any('kind' in line for line in lines):
        reversals = Decimal(0)
        for line in lines:
            kind, value = line.get('kind'), money(line.get('amount'))
            if kind not in ('unlocated', 'reversal', 'residual'):
                problems.append(f'the unlocated line {line.get("reason")!r} states no kind')
            elif kind == 'reversal' and value >= 0:
                problems.append(f'the unlocated line {line.get("reason")!r} is marked a reversal but does not net below zero')
            elif kind == 'unlocated' and value < 0:
                problems.append(f'the unlocated line {line.get("reason")!r} nets below zero and is not marked a reversal')
            elif kind == 'residual' and (value - money(payload.get('reconciliation_residual'))).copy_abs() > CENT:
                problems.append('the residual line is not the reconciliation residual')
            if kind == 'reversal':
                reversals += value
        if (reversals - money(payload.get('reversals_total'))).copy_abs() > CENT:
            problems.append('reversals_total does not add up from the reversal lines')
    if 'share_base' in payload:
        base = payload.get('share_base')
        if not isinstance(base, dict) or set(base) != {'places', 'zips'}:
            problems.append('share_base must state places and zips')
            return problems
        kinds = [kind for kind in payload.get('kinds') or [] if isinstance(kind, dict)]
        expected = share_bases(kinds, payload, str(payload.get('direction') or ''))
        for field, label in (('places', 'share_base.places is not the positive located total'),
                             ('zips', 'share_base.zips is not the positive ZIP-placed receipts of a raised map')):
            want = None if expected[field] is None else money(expected[field])
            have = None if base[field] is None else money(base[field])
            if (want is None) != (have is None) or (want is not None and (want - have).copy_abs() > CENT):
                problems.append(label)
    return problems


def not_itemized_problems(payload):
    """F6: each disclosed not-itemized transfer is its summary less its itemized
    rows, positive, and the lines add to their total. Payloads written before F6
    carry no such field and are not refused for lacking it."""
    if 'transfers_not_itemized' not in payload:
        return []
    rows = payload.get('transfers_not_itemized')
    if not isinstance(rows, list):
        return ['transfers_not_itemized must be a list']
    problems, total = [], Decimal(0)
    for row in rows:
        if not isinstance(row, dict) or not row.get('committee') or not row.get('reason'):
            problems.append('a transfers_not_itemized line states no committee or reason')
            continue
        amount = money(row.get('amount'))
        if amount <= 0:
            problems.append(f'the not-itemized transfer line for {row["committee"]} is not positive')
        if (money(row.get('summary_amount')) - money(row.get('itemized_amount')) - amount).copy_abs() > CENT:
            problems.append(f'the not-itemized transfer line for {row["committee"]} is not its summary '
                            f'less its itemized rows')
        total += amount
    if (total - money(payload.get('transfers_not_itemized_total'))).copy_abs() > CENT:
        problems.append('transfers_not_itemized_total does not add up from its lines')
    return problems


def level_problems(payload, located):
    """Contract v3: every wider layer adds back to the same located total.

    by_state and by_region are folds of the SAME rows the kinds carry, so each
    must equal located_total to the cent. by_zip is a fold of every raised row,
    so it must equal the REPORTED total once the cap's dollars and the rows with
    no readable ZIP are added back. A layer that quietly loses money is the one
    failure this whole module exists to prevent.
    """
    problems = []
    for field in ('by_state', 'by_region', 'by_zip', 'zip_truncated',
                  'zip_truncated_amount', 'zip_missing', 'sides'):
        if field not in payload:
            problems.append(f'the payload has no {field}')
    for field in ('by_state', 'by_region'):
        rows = payload.get(field)
        if not isinstance(rows, list):
            problems.append(f'{field} must be a list')
            continue
        total = sum((money(row.get('amount')) for row in rows if isinstance(row, dict)), Decimal(0))
        if (total - located).copy_abs() > CENT:
            problems.append(f'{field} does not add up to located_total')
        for row in rows:
            if not isinstance(row, dict) or not str(row.get('key') or '').strip():
                problems.append(f'a {field} row states no key')
                continue
            if field == 'by_state' and not STATE_KEY.fullmatch(str(row['key'])):
                problems.append(f'by_state key {row["key"]!r} is not a state')
    zips = payload.get('by_zip')
    if not isinstance(zips, list):
        problems.append('by_zip must be a list')
        return problems
    for row in zips:
        if not isinstance(row, dict) or not re.fullmatch(r'\d{5}', str(row.get('zip') or '')):
            problems.append(f'by_zip row {row!r} states no five-digit ZIP')
    if payload.get('direction') == 'raised':
        counted = (sum((money(row.get('amount')) for row in zips if isinstance(row, dict)), Decimal(0))
                   + money(payload.get('zip_truncated_amount'))
                   + money((payload.get('zip_missing') or {}).get('amount')))
        if (counted - money(payload.get('reported_total'))).copy_abs() > CENT:
            problems.append('by_zip plus the truncated and unzipped dollars '
                            'does not reconcile to the reported total')
    elif zips:
        problems.append('a spent map reads no contributor ZIP and must carry no by_zip rows')
    sides = payload.get('sides')
    if not isinstance(sides, dict) or set(sides) != set(DIRECTIONS):
        problems.append('sides must state both raised and spent')
        return problems
    for direction, side in sides.items():
        if not isinstance(side, dict) or 'reported_total' not in side or 'rows' not in side:
            problems.append(f'sides.{direction} states no total and row count')
            continue
        if not side.get('reported_total') and not side.get('rows') and not side.get('reason'):
            problems.append(f'sides.{direction} is empty and says nothing about why')
    own = sides.get(str(payload.get('direction') or ''))
    if isinstance(own, dict) and (money(own.get('reported_total'))
                                  - money(payload.get('reported_total'))).copy_abs() > CENT:
        problems.append('sides disagrees with this payload about its own direction')
    return problems

"""GET /api/flow: where a committee's money came from and where it went (boards #44-#46).

Lane F, Paul approved 2026-09-25 ("Finish this up"). One measurement, not a second one:

- The committee network and its candidate activity are downstream.network() and
  downstream.activity() over exactly the units /api/evidence-network reads: the chosen
  snapshot owns the root committee/cycle, the saved inventory supplies every committee it
  reaches (downstream.snapshot_view's selection, rebuilt here from the same functions).
- Donors are the root's own saved Schedule A rows (downstream.observations: one per
  source id, F11 reloads once), classified by the collector's own receipt rule
  (pac_evidence.receipt_role) and grouped by the name as filed.
- Cash comes from each committee's own FEC summary (the saved unit's totals).

Money semantics (packet section 4, binding):
- Every committee node balances against its own summary: receipts counted once + cash
  from before this window = transfers + candidate spending + operating (the remainder)
  + cash on hand at the end + donated assets not yet sold. A committee whose summary
  cannot carry what the network sent it shows the gap as its own balance node.
- Donated assets count once, at their value when given: an in-kind gift booked back out
  to its donor is the gift; the sale proceeds of donated assets are not new money; what
  is not sold yet stays in "Not yet spent" (first in, first out, per kind of asset).
- Money reaching a downstream committee from outside the network is an external_in stub
  ("Other donors"), drawn beside that committee, never a column node.
- Pooled money is not traced money: basis=reported draws each committee's own spending;
  basis=attributed needs an estimate bundle and answers 409 without one.
- The network visits each committee/cycle once. A transfer back to a committee already
  drawn (a loop) is a "returned to <committee>" stub, and money from a committee in the
  network is never counted again as a donor (the map removes it on both sides too).

The route refuses (409, FlowRefused) when the candidate spending and transfers it draws
differ from /api/evidence-network's for the same committees, to the cent. The evidence
network's side is network_view(): downstream.snapshot_view's relationships and edges for
the same selection and walk, grouped from committee_flow's own walk rather than by reading
the whole snapshot file a second time (lane P1: culac-20260921's is 320 MB).
"""
from __future__ import annotations

from collections import ChainMap, OrderedDict, defaultdict
import copy
from datetime import date
from decimal import Decimal
from functools import lru_cache
import json
import os
from pathlib import Path
import re
import threading
import time

import downstream
import drawer_sidecars
import transfer_rule

VERSION = 1
CENT = Decimal('0.01')
ZERO = Decimal(0)
DEFAULT_TOP = 8
DEFAULT_DEPTH = 3
TOP_LIMIT = 60
DEPTH_RANGE = (2, 6)
# Named nodes below this share of their column are grouped (the "Other" rule). With the
# default Top 8 it reproduces #44's donor column exactly; top=0 draws every node.
MIN_SHARE = Decimal('0.01')
EVIDENCE_LIMIT = 12
MEMBER_LIMIT = 200
SNAPSHOT_ID = re.compile(r'[a-z0-9][a-z0-9_-]{0,99}')
COMMITTEE_ID = re.compile(r'C\d{8}')
CONTRIBUTION_LINES = frozenset({'11A', '11AI', '11AII', '11B', '11C'})
COMMITTEE_ENTITIES = frozenset({'PAC', 'CCM', 'PTY', 'COM', 'ORG_PAC'})
# The kinds of donated asset a committee can hold and later sell, as filings name them.
ASSET_KINDS = (('bitcoin', re.compile(r'\bBTC\b|\bBITCOINS?\b')),
               ('USD coin', re.compile(r'\bUSDC\b|\bUS DOLLAR COIN\b|\bUSD COIN\b')),
               ('ether', re.compile(r'\bETH\b|\bETHER\b|\bETHEREUM\b')),
               ('tether', re.compile(r'\bUSDT\b|\bTETHER\b')),
               ('solana', re.compile(r'\bSOL\b|\bSOLANA\b')),
               ('stock', re.compile(r'\bSTOCK\b|\bSHARES?\b')))
CRYPTO = frozenset({'bitcoin', 'USD coin', 'ether', 'tether', 'solana'})
DIRECTION_KIND = {'Support': 'ie_support', 'Oppose': 'ie_oppose', 'Unknown': 'ie_unknown',
                  'Contribution / Other': 'contribution'}
DIRECTION_WORD = {'Support': 'support', 'Oppose': 'oppose', 'Unknown': 'unknown',
                  'Contribution / Other': 'contribution'}
CANDIDATE_KINDS = frozenset({'ie_support', 'ie_oppose', 'ie_unknown', 'contribution'})
PARTY_LETTER = {'REP': 'R', 'DEM': 'D', 'IND': 'I', 'LIB': 'L', 'GRE': 'G', 'DFL': 'D', 'CON': 'C'}
NO_ESTIMATE_BUNDLE = 'No estimate bundle for this industry yet; rebuild the workbook.'
MONTHS = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')


class FlowRefused(Exception):
    """A refusal the route answers 409: the reason travels with the sentence."""

    def __init__(self, message, reason, **detail):
        super().__init__(message)
        self.reason = reason
        self.detail = detail

    def body(self):
        """The 409 answer: the sentence, its reason code, and what the reason carries."""
        return {'error': str(self), 'reason': self.reason, **self.detail}


# ------------------------------------------------------------------ small helpers

def dec(value):
    """A money value as Decimal cents; a missing summary field is 0, never a guess at a row."""
    if value in (None, ''):
        return ZERO
    return downstream.amount(value)


def cents(value):
    return float(Decimal(value).quantize(CENT))


def _collector():
    import pac_evidence  # the collector's own receipt rules (tools/, on sys.path via downstream)
    return pac_evidence


def name_key(value):
    return ' '.join(re.findall(r'[A-Z0-9]+', str(value or '').upper()))


# ------------------------------------------------------------------ committee names (B4, 2026-10-15)
# A committee reached only as a recipient of a transfer has no summary or snapshot of its own, so the
# saved data names it by its ID. The FEC committee master on disk (cm*/cm.txt, the file the PAC picker
# searches) names it; nothing is fetched. A committee with no name anywhere is "Unnamed committee",
# its ID kept in name_as_filed and on the node (unnamed), never a name made up for it.
UNNAMED = 'Unnamed committee'
MASTER_ROOT = None                      # tests point this at a directory of cm*/cm.txt; None = the console's inputs


@lru_cache(maxsize=4)
def _master_file(entry):
    import pac_flow_access
    return dict(pac_flow_access._master_names((entry,)))


def committee_names(cycle=None):
    """[(year, {committee ID: name as filed})] from the FEC committee master on disk, in the order a lookup tries
    them: the file for the flow's cycle first (a committee is renamed from cycle to cycle: C00728980 is
    "TIPPINGPOINT 2020" in cm22 and "DEMOCRACY SOLVES IT" in cm26), then the newest first; [] when this machine
    has none. Names only: nothing here is an amount."""
    import evidence_csv
    base = Path(MASTER_ROOT) if MASTER_ROOT else evidence_csv.INPUTS / 'fec_committee_master'
    mine = f'cm{int(cycle) % 100:02d}' if str(cycle or '').isdigit() else ''
    try:
        folders = sorted(base.glob('cm*'), reverse=True)
        folders.sort(key=lambda folder: folder.name != mine)       # stable: the cycle's own file, the rest newest first
        found = []
        for folder in folders:
            path = folder / 'cm.txt'
            if path.is_file():
                year = '20' + folder.name[2:] if re.fullmatch(r'cm\d\d', folder.name) else ''
                found.append((year, _master_file((str(path), path.stat().st_mtime_ns, path.stat().st_size))))
        return found
    except OSError:
        return []


class Names(dict):
    """The names a flow's committees are filed under: the saved data's own first, then the FEC master (the file for
    `cycle` first). A value equal to the ID is no name (the ledgers fall back to it)."""

    def __init__(self, *args, cycle=None):
        super().__init__(*args)
        self.master = committee_names(cycle)

    def _master(self, key):
        return next(((names[key], year) for year, names in self.master if names.get(key)), (None, ''))

    def get(self, key, default=None):
        value = dict.get(self, key)
        if value and value != key:
            return value
        return self._master(key)[0] or value or default

    def master_year(self, ident):
        """The year of the FEC committee master file that names a committee here; '' when the saved data does."""
        value = dict.get(self, ident)
        return '' if value and value != ident else self._master(ident)[1]

    def named(self, ident):
        value = self.get(ident)
        return bool(value) and value != ident


def committee_label(names, ident):
    """A committee's label: its filed name for display, else 'Unnamed committee' (never an invented name)."""
    return display_org(names.get(ident), committee=True) if names.named(ident) else UNNAMED


def committee_tag(names, ident):
    """The label inside a sentence: an unnamed committee carries its ID there."""
    return committee_label(names, ident) if names.named(ident) else f'{UNNAMED} {ident}'


def unnamed_fields(names, ident):
    return {} if names.named(ident) else {'unnamed': True}


def master_fields(names, ident):
    """A committee node whose name is the FEC committee master's says which year's file it comes from (the tooltip
    says so instead of "As filed")."""
    year = names.master_year(ident) if names.named(ident) else ''
    return {'name_master': year} if year else {}


SMALL_WORDS = frozenset({'OF', 'AND', 'THE', 'FOR', 'TO', 'IN', 'ON', 'AT', 'BY', 'AN', 'OR', 'A', 'AS'})
KEEP_UPPER = frozenset({'PAC', 'LLC', 'LP', 'LLP', 'USA', 'US', 'II', 'III', 'IV', 'NA', 'DC', 'JFC', 'NRSC', 'DSCC',
                        'DCCC', 'NRCC', 'RNC', 'DNC', 'AFL', 'CIO', 'SEIU', 'UAW', 'IBEW', 'AFSCME', 'NEA', 'AFT'})
CORPORATE_TAIL = frozenset({'INC', 'LLC', 'CORP', 'CORPORATION', 'CO', 'LTD', 'LP', 'LLP', 'PLLC', 'PC', 'NA'})
HONORIFICS = frozenset({'MR', 'MRS', 'MS', 'MISS', 'DR', 'HON', 'HONORABLE', 'REP', 'SEN', 'SENATOR', 'DEL', 'GOV',
                        'JR', 'SR', 'II', 'III', 'IV', 'ESQ', 'PHD', 'MD', 'REV', 'GEN', 'COL', 'LT', 'CAPT'})


# Short words an organisation name spells out; any other word of three letters or fewer,
# and any word without a vowel, is read as an acronym (NHA Action, ARK Invest, LGBTQ, DRW).
COMMON_SHORT = frozenset({'ONE', 'TWO', 'NEW', 'OUR', 'ALL', 'OLD', 'BIG', 'WAY', 'WAR', 'AIR', 'OIL', 'GAS', 'SUN',
                          'SEA', 'JOB', 'PRO', 'TAX', 'LAW', 'ACT', 'AGE', 'END', 'ART', 'AID', 'CAR', 'RED', 'KEY',
                          'TOP', 'WIN', 'MEN', 'GUN', 'NOW', 'YES', 'FUN', 'HER', 'HIS', 'MAN', 'SON', 'DAY', 'SKY',
                          'BAY', 'LAB', 'BAR', 'HUB', 'NET', 'WEB', 'LIFE', 'GOOD', 'FAIR', 'BEST', 'HOME'})


def _cap(piece):
    low = piece.lower()
    if not low:
        return low
    if low.startswith('mc') and len(low) > 3:
        return 'Mc' + low[2].upper() + low[3:]
    return low[0].upper() + low[1:]


def _title(token):
    out = []
    for part in re.split(r'([-/&.])', token):
        if part in ('-', '/', '&', '.'):
            out.append(part)
            continue
        pieces = part.split("'")
        out.append("'".join([_cap(pieces[0])] + [_cap(piece) if len(piece) > 1 else piece.lower()
                                                 for piece in pieces[1:]]))
    return ''.join(out)


def _org_word(token, first):
    bare = re.sub(r'[^A-Z]', '', token.upper())
    if not bare:
        return token
    if bare in KEEP_UPPER:
        return token.upper()
    if not first and bare in SMALL_WORDS:
        return token.lower()
    if not re.search(r'[AEIOUY]', bare) or (len(bare) <= 3 and bare not in COMMON_SHORT and bare not in SMALL_WORDS):
        return token.upper()
    return _title(token)


def display_org(name, *, committee=False):
    """A filed organisation name for a label; the name as filed stays on every tooltip."""
    text = ' '.join(str(name or '').split())
    if not text:
        return 'Name not reported'
    if committee and len(text.split()) == 1:
        return text                        # FAIRSHAKE, ACTBLUE: a one-word committee is its brand
    words = text.replace(',', ' ').split()
    while len(words) > 1 and re.sub(r'[^A-Z]', '', words[-1].upper()) in CORPORATE_TAIL:
        words.pop()
    return ' '.join(_org_word(word, index == 0) for index, word in enumerate(words))


def display_person(name):
    """'MORENO, BERNIE' -> 'Bernie Moreno'; 'GALLEGO, RUBEN M. REP.' -> 'Ruben Gallego'."""
    text = ' '.join(str(name or '').split())
    if not text:
        return 'Name not reported'
    if ',' not in text:
        return display_org(text)
    last, _, rest = text.partition(',')
    given = [token for token in rest.replace('.', ' ').split()
             if re.sub(r'[^A-Z]', '', token.upper()) not in HONORIFICS and len(re.sub(r'[^A-Z]', '', token.upper())) > 1]
    surname = ' '.join(_title(token) for token in last.split()
                       if re.sub(r'[^A-Z]', '', token.upper()) not in HONORIFICS)
    return (_title(given[0]) + ' ' + surname).strip() if given else surname or text


def acronym(label):
    words = [word for word in re.findall(r"[A-Za-z][A-Za-z']*", label) if word.upper() not in SMALL_WORDS]
    return ''.join(word[0].upper() for word in words) or label


def short_money(value):
    value = Decimal(value)
    size = abs(value)
    sign = '-' if value < 0 else ''
    if size >= Decimal('999950'):
        return f'{sign}${size / Decimal(1_000_000):,.1f}M'
    if size >= Decimal('1000'):
        return f'{sign}${size / Decimal(1000):,.0f}K'
    return f'{sign}${size:,.0f}'


def day_label(text):
    try:
        moment = date.fromisoformat(str(text)[:10])
    except ValueError:
        return str(text or '')
    return f'{moment.day} {MONTHS[moment.month - 1]} {moment.year}'


def cycle_label(cycle):
    return f'{int(cycle) - 1}–{cycle}'


def committee_url(committee, cycle):
    return f'https://www.fec.gov/data/committee/{committee}/?cycle={cycle}'


def candidate_url(candidate):
    return f'https://www.fec.gov/data/candidate/{candidate}/' if re.fullmatch(r'[HSP][0-9A-Z]{8}', candidate or '') else ''


# ------------------------------------------------------------------ the request

def parse_request(query):
    """The route's parameters from a parse_qs mapping; ValueError (400) on anything malformed."""
    def get(key, default=''):
        values = query.get(key) or [default]
        return str(values[0]).strip()

    raw_cycle = get('cycle')
    match = re.fullmatch(r'(?:(\d{4})\s*[-–]\s*)?(\d{4})', raw_cycle)
    if not match or int(match[2]) % 2 or (match[1] and int(match[1]) != int(match[2]) - 1):
        raise ValueError('Choose an election cycle (for example 2024 or 2023-2024).')
    cycle = match[2]
    root = get('root') or ('committee:' + get('committee') if get('committee') else '')
    if root == 'industry':
        kind, committee = 'industry', ''
    elif root.startswith('committee:') and COMMITTEE_ID.fullmatch(root.split(':', 1)[1]):
        kind, committee = 'committee', root.split(':', 1)[1]
    else:
        raise ValueError('Choose a root: committee:<FEC committee id> or industry.')
    basis = get('basis', 'reported') or 'reported'
    if basis not in ('reported', 'attributed'):
        raise ValueError('Choose a basis: reported or attributed.')
    try:
        top = int(get('top', str(DEFAULT_TOP)) or DEFAULT_TOP)
        depth = int(get('depth', str(DEFAULT_DEPTH)) or DEFAULT_DEPTH)
    except ValueError:
        raise ValueError('Top and depth must be whole numbers.') from None
    if not 0 <= top <= TOP_LIMIT:
        raise ValueError(f'Top must be between 0 (every node) and {TOP_LIMIT}.')
    if not DEPTH_RANGE[0] <= depth <= DEPTH_RANGE[1]:
        raise ValueError(f'Depth must be between {DEPTH_RANGE[0]} and {DEPTH_RANGE[1]}.')
    identifier = get('id')
    if identifier and not SNAPSHOT_ID.fullmatch(identifier):
        raise ValueError('Invalid evidence snapshot.')
    direct = get('direct') == '1'
    if direct and (kind != 'committee' or identifier):
        raise ValueError('Direct Flow needs a committee and no selected snapshot.')
    return {'root_kind': kind, 'committee': committee, 'cycle': cycle, 'basis': basis, 'top': top,
            'depth': depth, 'id': identifier, 'industry': get('industry'), 'direct': direct}


# ------------------------------------------------------------------ the saved units

def _snapshot_path(root, identifier):
    return Path(root) / identifier / 'snapshots.json'


def newest_snapshot(committee, cycle, root):
    """The snapshot the saved inventory selects for this committee/cycle (newest coverage)."""
    unit = downstream.inventory(root).get((str(cycle), committee))
    if unit is None:
        raise ValueError(f'No saved snapshot covers {committee} in {cycle_label(cycle)}.')
    return Path(unit['_source']).parent.name


def selected_unit(identifier, committee, cycle, root):
    """snapshot_view's selection for one committee/cycle, read through the drawer index."""
    path = _snapshot_path(root, identifier)
    info = path.stat()
    try:
        fingerprint, entries = drawer_sidecars.snapshot_index(str(path), info.st_mtime_ns, info.st_size)
        found = [entry for entry in entries
                 if entry.get('committee_id') == committee and str(entry.get('cycle')) == str(cycle)]
        if len(found) != 1:
            raise drawer_sidecars.Unindexable('not exactly one unit')
        entry = found[0]
        unit = drawer_sidecars.read_unit(str(path), info.st_mtime_ns, info.st_size, entry['offset'],
                                         entry['length'], entry['sha256'])
    except drawer_sidecars.Unindexable:
        raw = path.read_bytes()
        import hashlib
        fingerprint = hashlib.sha256(raw).hexdigest()
        units = [u for u in json.loads(raw) if u.get('committee_id') == committee and str(u.get('cycle')) == str(cycle)]
        if not units:
            raise ValueError('This committee/cycle is not in the selected snapshot.') from None
        unit = units[-1]
    return {(str(cycle), committee): {**unit, '_source': str(path), '_sha256': fingerprint}}


def snapshot_facts(identifier, root):
    """The saved snapshot's own evidence.json facts this view names: date and collection gaps."""
    try:
        value = json.loads((Path(root) / identifier / 'evidence.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {'retrieved_at': '', 'gaps': {}, 'names': {}}
    gaps = {(str(row.get('cycle')), str(row.get('committee_id'))): str(row.get('status') or '')
            for row in value.get('downstream_coverage') or [] if isinstance(row, dict)}
    names = {}
    for row in value.get('transfers') or []:
        if isinstance(row, dict) and row.get('destination') and row.get('destination_name'):
            names.setdefault(str(row['destination']), str(row['destination_name']))
    for row in value.get('committee_finances') or []:
        if isinstance(row, dict) and row.get('committee_id') and row.get('committee_name'):
            names[str(row['committee_id'])] = str(row['committee_name'])
    return {'retrieved_at': str(value.get('retrieved_at') or ''), 'gaps': gaps, 'names': names,
            'company': str(value.get('company') or '')}


# ------------------------------------------------------------------ one committee's ledger

def _describe(row):
    return name_key(' '.join(str(row.get(key) or '') for key in ('receipt_type_full', 'memo_text',
                                                                   'disbursement_description')))


def _asset_kind(text):
    for label, pattern in ASSET_KINDS:
        if pattern.search(text):
            return label
    return None


def _source_committee(row):
    for key in ('contributor_id', 'contributor_committee_id'):
        value = str(row.get(key) or '').strip().upper()
        if COMMITTEE_ID.fullmatch(value):
            return value
    return ''


def _negative_line(rows, collector):
    """The filed line of a contributor's largest negative entry (11AI, 16, ...)."""
    lows = sorted(rows, key=lambda row: dec(row.get('contribution_receipt_amount')))
    return collector.receipt_line_code(lows[0]) if lows else ''


def ledger(key, unit, network_committees):
    """One committee/cycle's money as its own filings state it, counted once.

    Returns None when the unit has no usable summary (then nothing past its inflow is
    known and the flow says so). Every figure is Decimal; nothing here is drawn yet.
    """
    totals = unit.get('totals') or {}
    if any(totals.get(field) in (None, '') for field in ('receipts', 'disbursements', 'last_cash_on_hand_end_period')):
        return None
    collector = _collector()
    own = key[1]
    receipts, disbursements = dec(totals['receipts']), dec(totals['disbursements'])
    cash_end = dec(totals['last_cash_on_hand_end_period'])
    cash_begin = dec(totals.get('cash_on_hand_beginning_period'))
    offsets = dec(totals.get('offsets_to_operating_expenditures'))
    donor_refunds = dec(totals.get('contribution_refunds'))
    donors = OrderedDict()
    internal = defaultdict(lambda: {'amount': ZERO, 'rows': []})
    sales, gifts = [], []
    vendor_refunds = ZERO
    for row in downstream.observations(unit, 'receipts'):
        if downstream.memo(row):
            continue
        value = dec(row.get('contribution_receipt_amount'))
        line = collector.receipt_line_code(row)
        text = _describe(row)
        source = _source_committee(row)
        role = collector.receipt_role(row)
        if line == '15':
            continue                               # offsets: the summary carries them, itemized or not
        if line == '16' and not source and str(row.get('entity_type') or '').upper() not in COMMITTEE_ENTITIES:
            vendor_refunds += value                # a vendor's refund offsets operating, like line 15
            continue
        if role == 'asset_liquidation':
            sales.append({'row': row, 'amount': value, 'asset': _asset_kind(text),
                          'date': str(row.get('contribution_receipt_date') or '')[:10]})
            continue
        if source and source != own and source in network_committees:
            internal[source]['amount'] += value
            internal[source]['rows'].append(row)
            continue
        entity = str(row.get('entity_type') or '').upper()
        name = str(row.get('contributor_name') or '').strip() or 'Name not reported'
        group = name_key(name) or 'NAME NOT REPORTED'
        if group not in donors:
            donors[group] = {'key': group, 'names': defaultdict(Decimal), 'amount': ZERO, 'rows': [],
                             'entity': entity, 'committee': source, 'contribution': True, 'held': ZERO,
                             'held_kinds': set()}
        donor = donors[group]
        donor['amount'] += value
        donor['names'][name] += value
        donor['rows'].append(row)
        donor['contribution'] = donor['contribution'] and line in CONTRIBUTION_LINES
        if role == 'in_kind_contribution' and line in CONTRIBUTION_LINES:
            gifts.append({'row': row, 'donor': group, 'amount': value, 'asset': _asset_kind(text),
                          'date': str(row.get('contribution_receipt_date') or '')[:10],
                          'sub_id': str(row.get('sub_id') or '')})
    # The in-kind gifts booked back out to their donor (same name, same amount): the
    # bookkeeping pair that FEC filings use for a donated asset.
    outs = defaultdict(list)
    for row in downstream.observations(unit, 'disbursements'):
        if downstream.memo(row):
            continue
        outs[(name_key(row.get('recipient_name')), dec(row.get('disbursement_amount')))].append(row)
    booked = []
    for gift in gifts:
        pair = outs.get((gift['donor'], gift['amount']))
        if pair:
            pair.pop(0)
            booked.append(gift)
    # Sold first in, first out, per kind of asset; what is left is still held.
    booked.sort(key=lambda gift: (gift['date'], gift['sub_id']))
    remaining = {id(gift): gift['amount'] for gift in booked if gift['asset']}
    proceeds = defaultdict(Decimal)
    for sale in sales:
        proceeds[sale['asset']] += sale['amount']
    for kind in {gift['asset'] for gift in booked if gift['asset']}:
        left = proceeds.pop(kind, ZERO)
        for gift in booked:
            if gift['asset'] != kind or left <= 0:
                continue
            used = min(left, remaining[id(gift)])
            remaining[id(gift)] -= used
            left -= used
        proceeds[kind] = left                      # proceeds beyond the itemized gifts (an unitemized gift)
    unclassified = sum((value for kind, value in proceeds.items() if kind is None), ZERO)
    for gift in booked:                            # proceeds that name no asset: oldest gifts first
        if unclassified <= 0 or not gift['asset']:
            continue
        used = min(unclassified, remaining[id(gift)])
        remaining[id(gift)] -= used
        unclassified -= used
    held = ZERO
    for gift in booked:
        left = remaining.get(id(gift), ZERO)
        if left > 0:
            held += left
            donors[gift['donor']]['held'] += left
            donors[gift['donor']]['held_kinds'].add(gift['asset'])
    # A donor's charge-backs and refunds net against that donor's gifts in this window. One that nets
    # to zero or below gave before the window: it is never drawn as a donor (never a negative link),
    # and what it nets below zero left this committee as a refund (lane I, SLF 2022: Robert Weirman's
    # only row is a -$500 charge-back). A committee in the network that nets below zero is the same.
    refunded_donors, netted_donors = [], 0
    for group in [group for group, donor in donors.items() if donor['amount'] <= 0]:
        donor = donors.pop(group)
        netted_donors += donor['amount'] == 0
        if donor['amount'] < 0:
            filed = max(donor['names'].items(), key=lambda item: (abs(item[1]), item[0]))[0]
            refunded_donors.append({'key': group, 'name': filed, 'amount': -donor['amount'], 'rows': donor['rows'],
                                    'entity': donor['entity'], 'committee': donor['committee'],
                                    'line': _negative_line(donor['rows'], collector)})
    for source in [source for source, item in internal.items() if item['amount'] <= 0]:
        item = internal.pop(source)
        if item['amount'] < 0:
            refunded_donors.append({'key': source, 'name': source, 'amount': -item['amount'], 'rows': item['rows'],
                                    'entity': 'COM', 'committee': source,
                                    'line': _negative_line(item['rows'], collector)})
    refunded_donors.sort(key=lambda entry: (-entry['amount'], entry['key']))
    refunds_to_donors = sum((entry['amount'] for entry in refunded_donors), ZERO)
    sold = sum((sale['amount'] for sale in sales), ZERO)
    refunds = offsets + vendor_refunds
    counted_in = receipts - sold - refunds
    internal_total = sum((item['amount'] for item in internal.values()), ZERO)
    itemized = sum((donor['amount'] for donor in donors.values()), ZERO)
    return {'key': key, 'unit': unit, 'totals': totals, 'receipts': receipts, 'disbursements': disbursements,
            'cash_begin': cash_begin, 'cash_end': cash_end, 'offsets': offsets, 'vendor_refunds': vendor_refunds,
            'refunds': refunds, 'donor_refunds': donor_refunds, 'sold': sold, 'sales': sales,
            'gifts': booked, 'gift_kinds': sorted({gift['asset'] for gift in booked if gift['asset']}),
            'booked': sum((gift['amount'] for gift in booked), ZERO), 'held': held,
            'held_kinds': sorted({kind for donor in donors.values() for kind in donor['held_kinds']}),
            'excess_proceeds': sum((value for kind, value in proceeds.items() if kind is not None and value > 0), ZERO)
                               + max(unclassified, ZERO),
            'counted_in': counted_in, 'donors': donors, 'internal': dict(internal), 'internal_total': internal_total,
            # Intake is gross of what donors were refunded beyond their gifts in this window.
            'refunds_to_donors': refunds_to_donors, 'refunded_donors': refunded_donors, 'netted_donors': netted_donors,
            'intake': counted_in + refunds_to_donors,
            'itemized_donors': itemized, 'unitemized': counted_in + refunds_to_donors - internal_total - itemized,
            'operating_expenditures': dec(totals.get('operating_expenditures')),
            'independent_expenditures': dec(totals.get('independent_expenditures')),
            'coverage_start': str(totals.get('coverage_start_date') or '')[:10],
            'coverage_end': str(totals.get('coverage_end_date') or '')[:10],
            'name': str(totals.get('committee_name') or own)}


# ------------------------------------------------------------------ graph assembly

class Graph:
    """Nodes and links in Decimal, merged by (source, target, kind)."""

    def __init__(self):
        self.nodes = OrderedDict()
        self.links = OrderedDict()

    def node(self, node_id, **fields):
        if node_id not in self.nodes:
            self.nodes[node_id] = {'id': node_id, **fields}
        else:
            for field, value in fields.items():
                self.nodes[node_id].setdefault(field, value)
        return self.nodes[node_id]

    def link(self, source, target, value, kind, rows=(), evidence=(), **fields):
        value = Decimal(value)
        if value == 0:
            return None
        if value < 0:
            raise ValueError(f'Negative flow {source} -> {target} ({kind}).')
        key = (source, target, kind)
        if key not in self.links:
            self.links[key] = {'source': source, 'target': target, 'value': ZERO, 'kind': kind, 'rows': 0,
                               'evidence': [], **fields}
        entry = self.links[key]
        entry['value'] += value
        entry['rows'] += len(rows) if not isinstance(rows, int) else rows
        entry['evidence'].extend(evidence)
        for field, value_ in fields.items():
            entry.setdefault(field, value_)
        return entry


def _records(rows):
    records = [downstream.filing_record(row) for row in rows]
    records.sort(key=lambda record: (-(record['amount'] or 0), record['date'], record['sub_id']))
    return records[:EVIDENCE_LIMIT]


def _dates(rows, field):
    return sorted(value for value in (str(row.get(field) or '')[:10] for row in rows) if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value))


BEFORE_GATE_TEXT = "Not counted: paid before the network's money arrived"


def before_gate(found, key, unit):
    """(amount, rows) a reached committee paid before the network's money reached it (F12).

    Its non-memo disbursements and expenditures that downstream.observations(gate=) leaves
    out: the measurement's own rule (tools/arrival_gates.py), read here only to disclose the
    dollars beside the committee. None of it is drawn as a flow. (0, 0) for a root."""
    gate = downstream.gate_of(found, key)
    if gate is None or not unit:
        return ZERO, 0
    total, rows = ZERO, 0
    for schedule, field in (('disbursements', 'disbursement_amount'), ('expenditures', 'expenditure_amount')):
        kept = {id(row) for row in downstream.observations(unit, schedule, gate=gate)}
        for row in downstream.observations(unit, schedule):
            if id(row) in kept or downstream.memo(row):
                continue
            total += dec(row.get(field))
            rows += 1
    return total, rows


def unverified(rows=(), edge=None):
    """{'date_unverified': True} when the measurement flags an undated row it still counts."""
    flagged = bool(edge and edge.get('date_unverified')) or any(
        isinstance(row, dict) and row.get('date_unverified') for row in rows or ())
    return {'date_unverified': True} if flagged else {}


def committee_flow(committee, cycle, *, identifier='', depth=DEFAULT_DEPTH, root=None):
    """The ungrouped Reported flow for one committee/cycle, with what reconciliation needs."""
    root = Path(root or downstream.ROOT)
    cycle = str(cycle)
    identifier = identifier or newest_snapshot(committee, cycle, root)
    selected = selected_unit(identifier, committee, cycle, root)
    units = ChainMap(selected, downstream.inventory(root))
    found = downstream.network(selected, units)
    # The walk read the previous cycle's units (carryover) through `units`. They are not in
    # found['units'] and nothing below reads them, so they are let go before the ledgers run
    # (culac-20260921's 2022 unit is 111 MB of JSON).
    del units
    activity = downstream.activity(found)
    facts = snapshot_facts(identifier, root)
    last = depth
    transfer_edges = [edge for edge in found['edges'] if edge['cycle'] == cycle and not edge.get('terminal_candidate')]
    # Hops by breadth-first search over the network's own edges (it visits each committee once).
    hop, queue, outgoing = {committee: 0}, [committee], defaultdict(list)
    for edge in transfer_edges:
        outgoing[edge['source']].append(edge)
    for current in queue:
        for edge in outgoing[current]:
            if edge['destination'] not in hop:
                hop[edge['destination']] = hop[current] + 1
                queue.append(edge['destination'])
    drawn = {ident for ident, level in hop.items() if level <= depth - 2}
    network_ids = {key[1] for key in found['keys'] if key[0] == cycle}
    names = Names(facts['names'], cycle=cycle)
    ledgers = {}
    for key, unit in found['units'].items():
        if key[0] == cycle:
            item = ledger(key, unit, network_ids)
            ledgers[key[1]] = item
            names[key[1]] = item['name'] if item else str((unit.get('totals') or {}).get('committee_name') or key[1])
    root_ledger = ledgers.get(committee)
    if root_ledger is None:
        raise ValueError(f'{committee} has no FEC summary saved for {cycle_label(cycle)}; its flow cannot balance.')

    def label(ident):
        return committee_label(names, ident)

    def tag(ident):
        return committee_tag(names, ident)

    graph = Graph()
    stages = [{'index': 0, 'key': 'donors', 'label': 'Received from'},
              {'index': 1, 'key': 'root', 'label': label(committee)}]
    stages += [{'index': index, 'key': f'hop{index - 1}', 'label': 'Passed to' if index == 2 else 'Then to'}
               for index in range(2, last)]
    stages.append({'index': last, 'key': 'landed', 'label': 'Landed on'})
    by_committee = defaultdict(list)
    for row in activity:
        if row['cycle'] == cycle:
            by_committee[row['committee']].append(row)
    parties, spelled = {}, defaultdict(lambda: defaultdict(int))
    for unit in found['units'].values():
        for schedule in ('expenditures', 'disbursements'):
            for row in unit.get(schedule) or []:
                candidate = str(row.get('candidate_id') or '')
                if not candidate:
                    continue
                party = str(row.get('candidate_party') or '').upper()
                if party:
                    parties.setdefault(candidate, PARTY_LETTER.get(party, party[:1]))
                if row.get('candidate_name'):
                    spelled[candidate][str(row['candidate_name']).strip()] += 1

    def candidate_name(candidate, fallback):
        # The label reads the spelling the filings use most, preferring "LAST, FIRST".
        names = spelled.get(candidate)
        if not names:
            return fallback
        return max(names.items(), key=lambda item: (',' in item[0], item[1], len(item[0]), item[0]))[0]
    source_rows = {}
    for key, unit in found['units'].items():
        if key[0] == cycle:
            rows = defaultdict(list)
            for destination, _value, row in downstream.transfers(unit, gate=downstream.gate_of(found, key)):
                rows[destination].append(row)
            source_rows[key[1]] = rows

    def committee_node(ident):
        node = graph.node('committee:' + ident, stage=1 + hop[ident], label=label(ident), kind='committee',
                          committee_id=ident, name_as_filed=names.get(ident, ident),
                          fec_url=committee_url(ident, cycle), root=ident == committee,
                          saved=(cycle, ident) in found['units'], **unnamed_fields(names, ident),
                          **master_fields(names, ident))
        if (cycle, ident) not in found['units']:
            node['unreached'] = facts['gaps'].get((cycle, ident)) or 'no saved snapshot'
        node.update(carryover_of(found, cycle, ident, label))
        return node

    def candidate_node(row):
        candidate = row['candidate_id'] or ''
        word = DIRECTION_WORD.get(row['direction'], 'unknown')
        filed = candidate_name(candidate, str(row.get('candidate') or candidate or 'Unidentified candidate'))
        return graph.node(f'candidate:{candidate or "unidentified"}:{word}', stage=last,
                          label=display_person(filed) if candidate else 'Unidentified candidate', kind='candidate',
                          candidate_id=candidate, direction=word, name_as_filed=filed,
                          party=parties.get(candidate, ''), office=candidate[:1] if candidate else '',
                          state=candidate[2:4] if candidate[:1] in ('H', 'S') else '', fec_url=candidate_url(candidate))

    def sink(kind, text, stage, **fields):
        return graph.node(f'balance:{kind}:{stage}', stage=stage, label=text, kind='balance', balance=kind, **fields)

    def stub(kind, attach, ident, text, **fields):
        node = graph.nodes[attach]
        return graph.node(f'stub:{kind}:{attach}:{ident}', stage=node['stage'], label=text,
                          kind=fields.pop('node_kind', 'committee'), stub=kind, attach=attach, committee_id=ident,
                          fec_url=committee_url(ident, cycle), **fields)

    # ------------------------------------------------------------ the root's own receipts
    root_node = committee_node(committee)
    for group, donor in root_ledger['donors'].items():
        if donor['amount'] == 0:
            continue
        filed = max(donor['names'].items(), key=lambda item: (item[1], item[0]))[0]
        person = donor['entity'] == 'IND' or (not donor['entity'] and ',' in filed)
        committee_donor = bool(donor['committee']) or donor['entity'] in COMMITTEE_ENTITIES
        node = graph.node('donor:' + group, stage=0, label=display_person(filed) if person
                          else display_org(filed, committee=committee_donor),
                          kind='donor', name_as_filed=filed, entity=donor['entity'], contribution=donor['contribution'])
        if donor['held'] > 0:
            node['held'], node['held_kinds'] = cents(donor['held']), sorted(donor['held_kinds'])
        graph.link(node['id'], root_node['id'], donor['amount'], 'receipt', donor['rows'], _records(donor['rows']))
    if root_ledger['unitemized'] > 0:
        node = graph.node('donor:unitemized', stage=0, label='Unitemized contributions', kind='donor',
                          name_as_filed='', entity='', contribution=True, unitemized=True)
        graph.link(node['id'], root_node['id'], root_ledger['unitemized'], 'receipt', 0, [])
    if root_ledger['cash_begin'] > 0:
        node = sink('prior_cash', 'Cash from before this window', 0)
        graph.link(node['id'], root_node['id'], root_ledger['cash_begin'], 'prior_cash', 0, [],
                   evidence_url=committee_url(committee, cycle))
    for source, item in root_ledger['internal'].items():
        node = stub('return_in', root_node['id'], source, 'back from ' + tag(source))
        graph.link(node['id'], root_node['id'], item['amount'], 'transfer', item['rows'], _records(item['rows']))

    # ------------------------------------------------------------ every drawn committee's outflows
    kept_parts = defaultdict(lambda: defaultdict(Decimal))
    adjustments = defaultdict(Decimal)
    own_buckets = {}
    for ident in sorted(drawn, key=lambda value: (hop[value], value)):
        node = committee_node(ident)
        item = ledgers.get(ident)
        is_root = ident == committee
        spent_transfers = ZERO
        for edge in outgoing.get(ident, []):
            destination, value = edge['destination'], Decimal(str(edge['amount']))
            rows = source_rows.get(ident, {}).get(destination, [])
            spent_transfers += value
            if destination in drawn and hop[destination] == hop[ident] + 1:
                target = committee_node(destination)
            elif hop.get(destination, hop[ident] + 1) <= hop[ident]:
                target = stub('return', node['id'], destination, 'returned to ' + tag(destination))
            else:
                target = stub('onward', node['id'], destination, 'passed to ' + tag(destination), beyond=True,
                              unreached=facts['gaps'].get((cycle, destination))
                              or ('' if (cycle, destination) in found['units'] else 'no saved snapshot'))
            graph.link(node['id'], target['id'], value, 'transfer', rows, _records(rows), committee=ident,
                       **unverified(rows, edge))
        spent_candidates = ZERO
        for row in by_committee.get(ident, []):
            value = Decimal(str(row['amount']))
            if value <= 0:
                # A net reversal is never a ribbon; it is counted for the reconciliation
                # and disclosed, and the committee's operating remainder carries it.
                adjustments[ident] += value
                continue
            kind = DIRECTION_KIND.get(row['direction'], 'ie_unknown')
            spent_candidates += value
            target = candidate_node(row)
            filings = row.get('filings') or []
            extra = unverified(filings, row)
            evidence = sorted(filings, key=lambda record: (-(record.get('amount') or 0), record.get('date') or ''))
            if is_root and last >= 3:
                bucket_kind = 'contributions' if kind == 'contribution' else 'ads'
                if bucket_kind not in own_buckets:
                    own_buckets[bucket_kind] = graph.node(
                        f'bucket:own-{bucket_kind}', stage=2, kind='bucket', bucket='own_' + bucket_kind,
                        label='Contributions to candidates' if bucket_kind == 'contributions'
                        else 'Own ads for and against candidates', committee_id=ident)
                bucket = own_buckets[bucket_kind]
                graph.link(node['id'], bucket['id'], value, kind, len(filings), [], committee=ident)
                graph.link(bucket['id'], target['id'], value, kind, len(filings), evidence[:EVIDENCE_LIMIT],
                           committee=ident, **extra)
            else:
                graph.link(node['id'], target['id'], value, kind, len(filings), evidence[:EVIDENCE_LIMIT],
                           committee=ident, **extra)
        inbound = [link for link in graph.links.values() if link['target'] == node['id']]
        if item is None:
            # No usable summary: what arrived and was not spent onward is not yet traced.
            received = sum((link['value'] for link in inbound), ZERO)
            spent = spent_transfers + spent_candidates
            node['untraced'] = True
            if received > spent:
                kept_parts[ident]['untraced'] += received - spent
            elif spent > received:
                extra_in = stub('external', node['id'], ident, 'Other money (no summary saved)', node_kind='donor')
                graph.link(extra_in['id'], node['id'], spent - received, 'external_in', 0, [])
            continue
        unspent = item['cash_end'] + item['held']
        if is_root:
            refunded = item['donor_refunds'] + item['refunds_to_donors']
            operating = item['intake'] + item['cash_begin'] - spent_transfers - spent_candidates - unspent - refunded
        else:
            network_in = sum((link['value'] for link in inbound if link['kind'] == 'transfer'), ZERO)
            back = defaultdict(Decimal)
            for edge in transfer_edges:
                if edge['destination'] == ident and edge['source'] in hop and hop[edge['source']] >= hop[ident]:
                    back[edge['source']] += Decimal(str(edge['amount']))
            for source, value in sorted(back.items()):
                back_stub = stub('return_in', node['id'], source, 'back from ' + tag(source))
                rows = source_rows.get(source, {}).get(ident, [])
                graph.link(back_stub['id'], node['id'], value, 'transfer', rows, _records(rows))
            external = item['intake'] - network_in - sum(back.values(), ZERO)
            node['pooled'] = True
            node['share_from_upstream'] = [
                {'id': link['source'], 'label': graph.nodes[link['source']]['label'], 'amount': cents(link['value']),
                 'share': float((link['value'] / item['receipts']).quantize(Decimal('0.000001')))
                 if item['receipts'] > 0 else None,
                 'denominator': 'receipts as reported', 'receipts': cents(item['receipts'])}
                for link in inbound if link['kind'] == 'transfer' and not graph.nodes[link['source']].get('stub')]
            if external > 0:
                outside = stub('external', node['id'], ident, 'Other donors', node_kind='donor')
                graph.link(outside['id'], node['id'], external, 'external_in', 0, [],
                           evidence_url=committee_url(ident, cycle))
            elif external < 0:
                kept_parts[ident]['unreported'] += -external
            if item['cash_begin'] > 0:
                prior = stub('prior_cash', node['id'], ident, 'Cash from before this window', node_kind='balance',
                             balance='prior_cash',
                             **({'carryover': node['carryover']['label']} if node.get('carryover') else {}))
                graph.link(prior['id'], node['id'], item['cash_begin'], 'prior_cash', 0, [],
                           evidence_url=committee_url(ident, cycle))
            refunded = ZERO
            early, early_rows = before_gate(found, (cycle, ident), item['unit'])
            if early > 0:
                gated = stub('before_gate', node['id'], ident, BEFORE_GATE_TEXT, node_kind='balance',
                             balance='before_gate', gate=downstream.gate_of(found, (cycle, ident)))
                graph.link(node['id'], gated['id'], early, 'uncounted', early_rows, [], committee=ident)
            operating = (item['intake'] + item['cash_begin'] - spent_transfers - spent_candidates - unspent
                         - early)
        node['operating_check'] = {'operating_expenditures': cents(item['operating_expenditures']),
                                   'refunds_netted': cents(item['refunds']), 'booked_in_kind': cents(item['booked']),
                                   'refunded_to_donors': cents(item['donor_refunds']),
                                   'refunds_to_donors': cents(item['refunds_to_donors']), 'net_operating': cents(operating)}
        if operating < 0:
            # More went out than the summary can carry: disclosed, never drawn as a negative ribbon.
            node['operating_short'] = cents(-operating)
            outside = stub('external', node['id'], ident, 'Not in the summary', node_kind='balance',
                           balance='unreconciled')
            graph.link(outside['id'], node['id'], -operating, 'external_in', 0, [])
            operating = ZERO
        if is_root:
            stage = min(2, last)
            if refunded > 0:
                graph.link(node['id'], sink('refunded', 'Refunded to donors', stage, details=refund_details(item, label))['id'],
                           refunded, 'operating', 0, [], evidence_url=committee_url(ident, cycle))
            if operating > 0:
                graph.link(node['id'], sink('operating', 'Operating, net of refunds', stage)['id'], operating,
                           'operating', 0, [], evidence_url=committee_url(ident, cycle))
            if unspent > 0:
                target = sink('unspent', 'Not yet spent', stage, cash=cents(item['cash_end']), held=cents(item['held']),
                              held_kinds=item['held_kinds'])
                graph.link(node['id'], target['id'], unspent, 'unspent', 0, [], evidence_url=committee_url(ident, cycle))
        else:
            parts = kept_parts[ident]
            parts['operating'] += operating
            parts['cash'] += item['cash_end']
            parts['held'] += item['held']
            parts['refunded_to_donors'] += item['donor_refunds'] + item['refunds_to_donors']
    # ------------------------------------------------------------ the last column's balance sinks
    kept = [ident for ident, parts in kept_parts.items()
            if parts.get('operating', ZERO) + parts.get('cash', ZERO) + parts.get('held', ZERO) > 0]
    if kept:
        labels = [tag(ident) for ident in kept]
        text = 'Kept by ' + ' and '.join(labels) if len(labels) <= 2 else f'Kept by {len(labels)} committees'
        if len(labels) <= 2 and len(text) > 30:
            text = 'Kept by ' + ' and '.join(acronym(value) for value in labels)
        node = sink('kept', text, last, committees=kept,
                    parts={ident: {part: cents(value) for part, value in kept_parts[ident].items() if value}
                           for ident in kept})
        for ident in kept:
            parts = kept_parts[ident]
            if parts['operating'] > 0:
                graph.link('committee:' + ident, node['id'], parts['operating'], 'operating', 0, [], committee=ident,
                           evidence_url=committee_url(ident, cycle))
            if parts['cash'] + parts['held'] > 0:
                graph.link('committee:' + ident, node['id'], parts['cash'] + parts['held'], 'unspent', 0, [],
                           committee=ident, evidence_url=committee_url(ident, cycle))
    for kind, text, link_kind in (('untraced', 'Not yet traced', 'untraced'),
                                  ('unreported', 'Not reported by the recipient', 'untraced')):
        owners = [ident for ident, parts in kept_parts.items() if parts.get(kind, ZERO) > 0]
        if owners:
            node = sink(kind, text, last, committees=owners)
            for ident in owners:
                graph.link('committee:' + ident, node['id'], kept_parts[ident][kind], link_kind, 0, [], committee=ident)
    parents = {}
    for edge in transfer_edges:
        destination = edge['destination']
        if destination in hop and edge['source'] in hop and hop[destination] == hop[edge['source']] + 1:
            parents.setdefault(destination, edge['source'])
    return {'graph': graph, 'found': found, 'ledgers': ledgers, 'root_ledger': root_ledger, 'hop': hop,
            'drawn': drawn, 'names': names, 'facts': facts, 'identifier': identifier, 'stages': stages,
            'last': last, 'cycle': cycle, 'committee': committee, 'root': root, 'parents': parents,
            'label': label, 'adjustments': adjustments, 'selected': selected, 'activity': activity, 'tag': tag}


# ------------------------------------------------------------------ values, checks and grouping

def _value(node):
    return max(node.get('value_in', ZERO), node.get('value_out', ZERO))


def measure(nodes, links):
    """value_in and value_out of every node, from its links (one source of truth)."""
    for node in nodes.values():
        node['value_in'], node['value_out'] = ZERO, ZERO
    for link in links:
        nodes[link['source']]['value_out'] += link['value']
        nodes[link['target']]['value_in'] += link['value']


def conservation_errors(nodes):
    """Every node with both inflows and outflows must balance to the cent."""
    return [(node['id'], node['value_in'], node['value_out']) for node in nodes.values()
            if node['value_in'] > 0 and node['value_out'] > 0 and abs(node['value_in'] - node['value_out']) > Decimal('0.005')]


GROUPABLE = frozenset({'donor', 'committee', 'candidate'})


def other_label(members):
    kinds = {member['kind'] for member in members}
    if kinds == {'donor'} and all(member.get('industry') for member in members):
        return f"Other {str(members[0]['industry']).lower()} companies"
    if kinds == {'donor'}:
        return 'All other contributors' if all(member.get('contribution', True) for member in members) \
            else 'All other sources'
    if kinds == {'candidate'}:
        return 'Other candidates'
    if kinds == {'committee'}:
        return 'Other committees'
    return 'Other'


def group(nodes, links, top, min_share=MIN_SHARE):
    """Top N per column plus one "Other" node (the rule flow-layout.js applies too).

    A column keeps every node when it has at most `top` nodes and each groupable one is at
    least `min_share` of the column. Otherwise its groupable nodes (donors, non-root
    committees, candidates) are named by value, largest first, into the slots left after
    the fixed nodes (root, buckets, balances) and the Other node, dropping any under
    `min_share`; the rest, when there are two or more, become one Other node. Stubs keep
    their committee (re-attached to its Other node). top=0 draws every node.
    """
    if not top:
        return nodes, links
    remap = {}
    others = OrderedDict()
    for stage in sorted({node['stage'] for node in nodes.values()}):
        column = [node for node in nodes.values() if node['stage'] == stage and not node.get('stub')]
        total = sum((_value(node) for node in column), ZERO)
        floor = total * min_share
        groupable = [node for node in column if node['kind'] in GROUPABLE and not node.get('root')
                     and not node.get('fixed')]
        fixed = len(column) - len(groupable)
        ordered = sorted(groupable, key=lambda node: (-_value(node), node['label'], node['id']))
        if len(column) <= top and all(_value(node) >= floor for node in ordered):
            continue
        room = max(top - fixed - 1, 0)
        named = []
        for node in ordered[:room]:
            if _value(node) < floor:
                break
            named.append(node)
        rest = ordered[len(named):]
        if len(rest) < 2:
            continue
        other_id = f'other:{stage}'
        members = sorted(rest, key=lambda node: (-_value(node), node['label'], node['id']))
        others[other_id] = {'id': other_id, 'stage': stage, 'kind': 'bucket', 'bucket': 'other',
                            'label': other_label(members), 'member_count': len(members),
                            'members': [{'id': node['id'], 'label': node['label'], 'kind': node['kind'],
                                         'value': cents(_value(node)),
                                         **({'direction': node['direction']} if node.get('direction') else {}),
                                         **({'party': node['party']} if node.get('party') else {}),
                                         **({'carryover': node['carryover']['label']}
                                            if isinstance(node.get('carryover'), dict) else {})}
                                        for node in members[:MEMBER_LIMIT]]}
        for node in rest:
            remap[node['id']] = other_id
    if not remap:
        return nodes, links
    result = OrderedDict((key, node) for key, node in nodes.items() if key not in remap)
    stub_ids = {}
    for key, node in list(result.items()):
        if node.get('stub') and node.get('attach') in remap:
            attach = remap[node['attach']]
            merged_id = f"stub:{node['stub']}:{attach}:{node.get('committee_id') or node['label']}" \
                if node['stub'] in ('return', 'onward', 'return_in') else f"stub:{node['stub']}:{attach}:{node['label']}"
            stub_ids[key] = merged_id
            del result[key]
            if merged_id not in result:
                result[merged_id] = {**node, 'id': merged_id, 'attach': attach,
                                     'stage': others[attach]['stage']}
                result[merged_id].pop('carryover', None)       # each belongs to one committee
                result[merged_id].pop('gate', None)
                if node['stub'] in ('external',):
                    result[merged_id].pop('committee_id', None)
                    result[merged_id].pop('fec_url', None)
    result.update(others)
    target = lambda ident: remap.get(ident, stub_ids.get(ident, ident))
    merged = OrderedDict()
    for link in links:
        source, sink_ = target(link['source']), target(link['target'])
        key = (source, sink_, link['kind'])
        if key not in merged:
            merged[key] = {**link, 'source': source, 'target': sink_, 'evidence': list(link.get('evidence') or [])}
            continue
        entry = merged[key]
        entry['value'] += link['value']
        entry['rows'] += link['rows']
        entry['evidence'] = (entry['evidence'] + list(link.get('evidence') or []))[:EVIDENCE_LIMIT]
        if entry.get('committee') != link.get('committee'):
            entry.pop('committee', None)
        if link.get('date_unverified') or entry.get('date_unverified'):
            entry['date_unverified'] = True
    return result, list(merged.values())


# ------------------------------------------------------------------ reconciliation with /api/evidence-network

def _flow_side(built):
    """What the flow draws for the committees it draws: their candidate spending (with any
    net reversals it does not draw as ribbons) and their transfers (links and stubs)."""
    nodes, links = built['graph'].nodes, built['graph'].links.values()
    drawn = built['drawn']
    candidates = sum((link['value'] for link in links
                      if nodes[link['target']]['kind'] == 'candidate' and link.get('committee') in drawn), ZERO)
    candidates += sum(built.get('adjustments', {}).values(), ZERO)
    transfers = sum((link['value'] for link in links if link['kind'] == 'transfer'
                     and nodes[link['source']]['kind'] == 'committee' and not nodes[link['source']].get('stub')), ZERO)
    return candidates, transfers


def _reconciliation(built, network_candidates, network_transfers, scope):
    flow_candidates, flow_transfers = _flow_side(built)
    equal = abs(network_candidates - flow_candidates) <= Decimal('0.005') and \
        abs(network_transfers - flow_transfers) <= Decimal('0.005')
    return {'evidence_network_total': cents(network_candidates + network_transfers),
            'flow_total': cents(flow_candidates + flow_transfers), 'equal': equal,
            'candidates': {'evidence_network': cents(network_candidates), 'flow': cents(flow_candidates)},
            'transfers': {'evidence_network': cents(network_transfers), 'flow': cents(flow_transfers)},
            'committees': sorted(built['drawn']), 'scope': scope}


def network_view(built):
    """/api/evidence-network's relationships and edges for this flow's snapshot, committee and cycle.

    downstream.snapshot_view selects the snapshot's unit for the committee/cycle (the last one when
    a file repeats it, as selected_unit does), walks downstream.network over it and the saved
    inventory, and groups downstream.activity with downstream.relationships. committee_flow made
    that same selection and walk; this groups its activity the same way, so the reconciliation is
    against the evidence network's own figures without parsing the whole snapshot file and walking
    the network a second time. reconcile() reads only these two fields (lane P1, 2026-09-27)."""
    found = built['found']
    return {'relationships': downstream.relationships(built['activity'], built['selected'], found),
            'edges': found['edges']}


def reconcile(built, view):
    """The drawn committees' candidate spending and transfers against the evidence network's."""
    cycle = built['cycle']
    drawn = {(cycle, ident) for ident in built['drawn']}
    network_candidates = sum((Decimal(str(row['amount'])) for row in view['relationships']
                              if (str(row['cycle']), row['committee']) in drawn), ZERO)
    network_transfers = sum((Decimal(str(edge['amount'])) for edge in view['edges']
                             if not edge.get('terminal_candidate') and (str(edge['cycle']), edge['source']) in drawn), ZERO)
    return _reconciliation(built, network_candidates, network_transfers,
                           'candidate spending and committee transfers of the committees this flow draws')


# ------------------------------------------------------------------ the contract (contracts/flow.json)

NODE_KINDS = frozenset({'donor', 'committee', 'candidate', 'bucket', 'balance'})
LINK_KINDS = frozenset({'receipt', 'transfer', 'ie_support', 'ie_oppose', 'ie_unknown', 'contribution', 'operating',
                        'uncounted',
                        'unspent', 'prior_cash', 'external_in', 'untraced'})
DIRECTIONS = frozenset({'support', 'oppose', 'contribution', 'unknown'})
REFUSALS = frozenset({'no_estimate_bundle', 'estimate_not_drawn', 'reconciliation', 'conservation',
                      'industry_unavailable'})
TOP_FIELDS = ('version', 'industry', 'build_stamp', 'root', 'cycle', 'window', 'basis', 'stages', 'nodes', 'links',
              'totals', 'coverage', 'reconciliation')


def _money(value):
    try:
        number = Decimal(str(value))
    except Exception:  # noqa: BLE001
        return None
    if not number.is_finite() or number * 100 != (number * 100).to_integral_value():
        return None
    return number


def contract_problems(payload, status=200):
    """Every way a /api/flow answer breaks contracts/flow.json, as sentences; [] when it holds.

    The same rules flow-layout.js validate() applies. Used on the producer's own output, on
    the contract's examples and on its negative controls (which must each fail)."""
    if not isinstance(payload, dict):
        return ['the answer is not an object']
    if status == 409:
        problems = [] if isinstance(payload.get('error'), str) and payload.get('error') else ['a refusal states no error']
        if payload.get('reason') not in REFUSALS:
            problems.append('a refusal names no known reason')
        if payload.get('reason') == 'reconciliation' and (payload.get('reconciliation') or {}).get('equal') is not False:
            problems.append('a reconciliation refusal must carry the unequal totals')
        return problems
    problems = [f'the answer has no {field}' for field in TOP_FIELDS if field not in payload]
    if problems:
        return problems
    if payload['basis'] not in ('reported', 'attributed'):
        problems.append('basis must be reported or attributed')
    recon = payload['reconciliation']
    if not isinstance(recon, dict) or recon.get('equal') is not True:
        problems.append('a drawn flow must reconcile with the evidence network (equal: true)')
    elif _money(recon.get('evidence_network_total')) != _money(recon.get('flow_total')):
        problems.append('reconciliation says equal but its totals differ')
    coverage = payload['coverage']
    if not isinstance(coverage, dict) or not all(key in coverage for key in ('through', 'per_committee', 'unreached')):
        problems.append('coverage must state through, per_committee and unreached')
    window = payload['window']
    if not isinstance(window, dict) or not {'start', 'end'} <= set(window):
        problems.append('window must state start and end')
    stages = payload['stages']
    if not isinstance(stages, list) or not stages:
        return problems + ['stages must be a non-empty list']
    last = len(stages) - 1
    if [stage.get('index') for stage in stages] != list(range(len(stages))):
        problems.append('stages must be indexed 0..n-1 in order')
    nodes, links = payload['nodes'], payload['links']
    if not isinstance(nodes, list) or not isinstance(links, list):
        return problems + ['nodes and links must be lists']
    by_id = {}
    for node in nodes:
        ident = node.get('id') if isinstance(node, dict) else None
        if not isinstance(ident, str) or not ident or ident in by_id:
            problems.append(f'node {ident!r} has no unique id')
            continue
        by_id[ident] = node
        if node.get('kind') not in NODE_KINDS:
            problems.append(f'node {ident} has an unknown kind {node.get("kind")!r}')
        if not isinstance(node.get('stage'), int) or not 0 <= node['stage'] <= last:
            problems.append(f'node {ident} sits on no stage')
        if not isinstance(node.get('label'), str):
            problems.append(f'node {ident} has no label')
        for field in ('value_in', 'value_out'):
            value = _money(node.get(field))
            if value is None or value < 0:
                problems.append(f'node {ident} {field} is not a whole-cent amount of zero or more')
        if node.get('kind') == 'candidate':
            if node.get('stage') != last:
                problems.append(f'candidate {ident} is not on the last stage')
            if node.get('direction') not in DIRECTIONS:
                problems.append(f'candidate {ident} states no direction')
        if node.get('kind') == 'donor' and node.get('stage', 0) > 0 and not node.get('stub'):
            problems.append(f'donor {ident} is drawn as a column node past the first stage (external money is a stub)')
    sums_in, sums_out = defaultdict(Decimal), defaultdict(Decimal)
    for link in links:
        if not isinstance(link, dict):
            problems.append('a link is not an object')
            continue
        source, target = by_id.get(link.get('source')), by_id.get(link.get('target'))
        where = f"{link.get('source')} -> {link.get('target')}"
        if source is None or target is None:
            problems.append(f'link {where} names a node that does not exist')
            continue
        value = _money(link.get('value'))
        if value is None or value <= 0:
            problems.append(f'link {where} is not a positive whole-cent amount')
            continue
        if link.get('kind') not in LINK_KINDS:
            problems.append(f'link {where} has an unknown kind {link.get("kind")!r}')
        if not isinstance(link.get('rows'), int) or link['rows'] < 0:
            problems.append(f'link {where} states no row count')
        if 'date_unverified' in link and not isinstance(link['date_unverified'], bool):
            problems.append(f'link {where} date_unverified is not true or false')
        if (link.get('kind') == 'uncounted') != (target.get('stub') == 'before_gate'):
            problems.append(f'link {where}: money paid before the network\'s arrived is drawn as a flow '
                            '(it is a before_gate stub, and only that)')
        stub = source.get('stub') or target.get('stub')
        if stub:
            attached = source if source.get('stub') else target
            other = target if source.get('stub') else source
            if attached.get('attach') != other['id']:
                problems.append(f'stub link {where} does not connect the stub to the node it is attached to')
        elif target['stage'] <= source['stage']:
            problems.append(f'link {where} runs backwards or within a column (a loop is a stub)')
        sums_out[source['id']] += value
        sums_in[target['id']] += value
    for ident, node in by_id.items():
        value_in, value_out = _money(node.get('value_in')), _money(node.get('value_out'))
        if value_in is None or value_out is None:
            continue
        if abs(value_in - sums_in[ident]) > Decimal('0.005') or abs(value_out - sums_out[ident]) > Decimal('0.005'):
            problems.append(f'node {ident} values are not the sums of its links')
        if sums_in[ident] > 0 and sums_out[ident] > 0 and abs(sums_in[ident] - sums_out[ident]) > Decimal('0.005'):
            problems.append(f'node {ident} does not balance: in {sums_in[ident]} out {sums_out[ident]}')
        if node.get('stub') and node.get('attach') not in by_id:
            problems.append(f'stub {ident} is attached to no node')
        # Carryover (b): a carried-over committee says so in the measurement's words, and only it.
        carried = node.get('carryover')
        if node.get('stub'):
            if carried and ((by_id.get(node.get('attach')) or {}).get('carryover') or {}).get('label') != carried:
                problems.append(f'stub {ident} says carried over but its committee is not')
        elif (node.get('gate_source') == 'carryover') != bool(isinstance(carried, dict) and carried.get('label')):
            problems.append(f'node {ident} is carried over without the measurement\'s label, or labelled without being')
    root = payload['root']
    if isinstance(root, dict) and root.get('kind') == 'committee':
        root_node = by_id.get('committee:' + str(root.get('id')))
        totals_in = _money((payload.get('totals') or {}).get('in'))
        if root_node is None:
            problems.append('the root committee is not drawn')
        else:
            # A 'Not in the summary' stub is disclosed spending the summary cannot carry, not intake.
            disclosed = sum((_money(link.get('value')) or Decimal(0) for link in payload['links']
                             if isinstance(link, dict) and link.get('target') == root_node['id']
                             and (by_id.get(link.get('source')) or {}).get('balance') == 'unreconciled'),
                            Decimal(0))
            if totals_in is None or abs(sums_in[root_node['id']] - disclosed - totals_in) > Decimal('0.005'):
                problems.append('the root takes in more or less than totals.in (money counted twice or lost)')
    return problems


# ------------------------------------------------------------------ the response

def _node_out(node):
    skip = {'value_in', 'value_out'}
    out = {key: value for key, value in node.items() if key not in skip and value not in (None, '', [], {})
           or key in ('id', 'stage', 'label', 'kind')}
    out['value_in'], out['value_out'] = cents(node.get('value_in', ZERO)), cents(node.get('value_out', ZERO))
    return out


def _link_out(link):
    out = {'source': link['source'], 'target': link['target'], 'value': cents(link['value']), 'kind': link['kind'],
           'rows': int(link.get('rows') or 0)}
    if link.get('evidence'):
        out['evidence'] = [{key: record.get(key) for key in ('source_url', 'sub_id', 'date', 'amount', 'counterparty',
                                                             'purpose') if record.get(key) not in (None, '')}
                           for record in link['evidence'][:EVIDENCE_LIMIT]]
    for key in ('committee', 'date_unverified', 'evidence_url'):
        if link.get(key) is not None:
            out[key] = link[key]
    return out


def routes_of(built, limit=10):
    """The largest committee-to-candidate routes, each with the committees it passed through."""
    graph, parents, label = built['graph'], built['parents'], built.get('tag') or built['label']
    result = []
    for link in graph.links.values():
        target = graph.nodes[link['target']]
        if target['kind'] != 'candidate' or not link.get('committee'):
            continue
        path, current = [], link['committee']
        while current is not None and current not in path:
            path.insert(0, current)
            current = parents.get(current)
        result.append({'committee': link['committee'], 'committee_label': label(link['committee']),
                       'candidate_id': target.get('candidate_id', ''), 'candidate_label': target['label'],
                       'direction': target.get('direction', ''), 'party': target.get('party', ''),
                       'state': target.get('state', ''), 'value': link['value'], 'kind': link['kind'],
                       'path': [{'id': ident, 'label': label(ident)} for ident in path],
                       'target': target['id'],
                       **({'date_unverified': True} if link.get('date_unverified') else {})})
    result.sort(key=lambda route: (-route['value'], route['committee'], route['candidate_id']))
    for route in result:
        route['value'] = cents(route['value'])
    return result[:limit]


def notes_of(built, totals):
    root = built['root_ledger']
    name = built['label'](built['committee'])
    notes = []
    if root['booked'] > 0:
        kinds = set(root['gift_kinds'])
        noun = 'crypto' if kinds and kinds <= CRYPTO else 'stock' if kinds == {'stock'} else 'assets'
        notes.append({'key': 'in_kind', 'text': f'Donated {noun} counted once, at its value when given '
                                                f'({name} booked {short_money(root["booked"])} in and out again)'})
    pooled = [built['label'](ident) for ident in sorted(built['drawn'], key=lambda value: (built['hop'][value], value))
              if ident != built['committee'] and built['ledgers'].get(ident)]
    if pooled:
        through = ' and '.join(pooled) if len(pooled) <= 2 else f'{len(pooled)} committees'
        notes.append({'key': 'pooled', 'text': f'routes through {through} show where pooled money went'})
    retrieved = built['facts']['retrieved_at']
    if built['identifier'].startswith('direct-'):
        source = 'FEC filings retrieved ' + day_label(retrieved) if retrieved else 'FEC filings'
    else:
        source = 'FEC filings, snapshot of ' + day_label(retrieved) if retrieved else 'FEC filings, saved snapshot'
    notes.append({'key': 'source', 'text': source})
    unreached = totals.get('unreached_count', 0)
    if unreached:
        plural = unreached != 1
        notes.append({'key': 'unreached', 'text': f'{unreached} committee{"s" if plural else ""} reached but not '
                                                  f'collected; {"their" if plural else "its"} spending is not traced'})
    notes.extend(date_notes(totals))
    notes.extend(carry_notes(built['found'], built['cycle']))
    notes.extend(refund_notes(built['ledgers'], built['committee'], built['label'], built['drawn']))
    return notes


def date_notes(totals):
    """F12's outcome, disclosed: what the gate left out, and undated money it kept."""
    notes = []
    if totals.get('before_gate'):
        notes.append({'key': 'before_gate', 'text': f"{BEFORE_GATE_TEXT} "
                                                    f"{short_money(Decimal(str(totals['before_gate'])))}"})
    if totals.get('date_unverified'):
        notes.append({'key': 'date_unverified', 'text': f"{short_money(Decimal(str(totals['date_unverified'])))} "
                                                        'undated, counted and flagged'})
    return notes


CARRYOVER_UNKNOWN = 'Carryover unknown: the previous cycle is not saved for this network'
CARRYOVER_PARTIAL = 'Carryover partial: some previous-cycle snapshots are not saved'


def carryover_of(found, cycle, ident, name):
    """Carryover (option (b), Paul 2026-09-25): where a committee's gate came from (root,
    carryover or in-cycle) and, when the previous cycle's transfers opened it, the
    measurement's own words for it (date_rule.carryover.opened[].label) and who opened it."""
    source = (found.get('gate_sources') or {}).get((str(cycle), ident), '')
    fields = {'gate_source': source} if source else {}
    if source != 'carryover':
        return fields
    for entry in (found.get('carryover') or {}).get('opened') or []:
        if str(entry.get('cycle')) == str(cycle) and entry.get('committee') == ident:
            fields['carryover'] = {'label': entry.get('label') or '', 'gate': entry.get('gate') or '',
                                   'opened_by': [{'source': item.get('source'), 'label': name(item.get('source')),
                                                  'first_date': item.get('first_date') or '',
                                                  'amount': cents(Decimal(str(item.get('amount') or 0)))}
                                                 for item in entry.get('opened_by') or []]}
            break
    return fields


def carry_notes(found, cycle):
    """The carryover the measurement applied to this flow's cycle, and its status, in its words."""
    carried = found.get('carryover') or {}
    opened = [entry for entry in carried.get('opened') or [] if str(entry.get('cycle')) == str(cycle)]
    notes = []
    if opened:
        count = len(opened)
        notes.append({'key': 'carryover', 'text': f"{count} committee{'s' if count != 1 else ''} counted from the "
                                                  f"cycle's first day: {opened[0].get('label') or 'gate opened by the previous cycle'}"})
    if carried.get('status') in ('unknown', 'partial'):
        notes.append({'key': 'carryover_status',
                      'text': CARRYOVER_UNKNOWN if carried['status'] == 'unknown' else CARRYOVER_PARTIAL})
    return notes


def exact_money(value):
    return f'${Decimal(value):,.2f}'


def _contributor(entry, name):
    """A refunded contributor's display name, as the donor nodes name theirs."""
    if entry.get('committee') and entry['name'] == entry['committee']:
        return name(entry['committee'])               # a committee in the network, read by its id
    if entry.get('entity') == 'IND' or (not entry.get('entity') and ',' in entry['name']):
        return display_person(entry['name'])
    return display_org(entry['name'], committee=bool(entry.get('committee')) or entry.get('entity') in COMMITTEE_ENTITIES)


def refund_details(item, name):
    """The "Refunded to donors" sink's words: the refunds the committee filed on its summary, and the
    contributors whose receipts in this window net below zero, in the filing's own terms (negative
    entries: a charge-back, a refund of a gift, a reversed refund), each with its line."""
    lines = []
    if item['donor_refunds'] > 0:
        lines.append(f"{exact_money(item['donor_refunds'])} refunds filed on the committee's summary")
    refunded = item['refunded_donors']
    if refunded:
        count = len(refunded)
        names = ', '.join(f"{_contributor(entry, name)} {exact_money(entry['amount'])} (line {entry['line']})"
                          for entry in refunded[:3])
        lines.append(f"{exact_money(item['refunds_to_donors'])} in negative receipts from {count} contributor"
                     f"{'s' if count != 1 else ''} beyond their receipts in this window: {names}"
                     f"{f', and {count - 3} more' if count > 3 else ''}")
    if item['netted_donors']:
        count = item['netted_donors']
        lines.append(f"{count} contributor{'s' if count != 1 else ''} netted to zero in this window (not drawn)")
    return lines


def refund_notes(ledgers, root, name, drawn):
    """Contributors whose receipts in the window net to zero or below: never a donor, never a
    negative link; what nets below zero is refunded to donors."""
    notes = []
    for ident, item in sorted(ledgers.items()):
        if ident not in drawn or item is None or (item['refunds_to_donors'] <= 0 and not item['netted_donors']):
            continue
        count, zero = len(item['refunded_donors']), item['netted_donors']
        where = '' if ident == root else f' by {name(ident)}'
        parts = [f"{count} contributor{'s' if count != 1 else ''} whose receipts in this window net below zero "
                 '(negative entries as filed)'] if count else []
        if zero:
            parts.append(f"{zero} netted to zero")
        notes.append({'key': 'refunds_to_donors',
                      'text': f"Refunds to donors {exact_money(item['refunds_to_donors'])}{where}: "
                              + '; '.join(parts) + '; not drawn as donors'})
    return notes


def carry_summary(found):
    carried = found.get('carryover') or {}
    return {'rule': carried.get('rule', ''), 'status': carried.get('status', ''),
            'opened': len(carried.get('opened') or []), 'missing_units': len(carried.get('missing_units') or [])}


# The prepared flow of each snapshot/committee/cycle/depth (_prepare): everything its answer holds
# but the Top N grouping. An entry keeps no saved unit (lane P1: six entries each holding a
# ~117 MB culac-20260921 unit, parsed, kept the console's memory for every later request).
_CACHE = OrderedDict()
_CACHE_LIMIT = 12
_cache_guard = threading.Lock()
_preparing = {}                 # key -> the lock of the one request preparing it (single flight, DE-026)


def _stamp(path):
    info = Path(path).stat()
    return (str(path), info.st_mtime_ns, info.st_size)


def _inventory_stamps(root):
    return tuple(_stamp(path) for path in sorted(Path(root).glob('*/snapshots.json')))


def _facts_stamp(root, identifier):
    """snapshot_facts' file, so a rewritten evidence.json is read again (None when there is none)."""
    try:
        return _stamp(Path(root) / identifier / 'evidence.json')
    except OSError:
        return None


def _prepared(key, prepare):
    """The prepared flow for key: from the cache, or prepared by one request however many ask for
    it at once (a cold culac-20260921 cycle is seconds and most of a gigabyte; two at once, as a
    Top change during a load asked, doubled both)."""
    with _cache_guard:
        prepared = _CACHE.get(key)
        if prepared is not None:
            _CACHE.move_to_end(key)
            return prepared
        lock = _preparing.setdefault(key, threading.Lock())
    with lock:
        try:
            with _cache_guard:
                prepared = _CACHE.get(key)
                if prepared is not None:
                    _CACHE.move_to_end(key)
                    return prepared
            prepared = prepare()
            with _cache_guard:
                _CACHE[key] = prepared
                while len(_CACHE) > _CACHE_LIMIT:
                    _CACHE.popitem(last=False)
            return prepared
        finally:
            with _cache_guard:
                if _preparing.get(key) is lock:
                    del _preparing[key]


def _key(root, identifier, committee, cycle, depth):
    return (_inventory_stamps(root), _stamp(_snapshot_path(root, identifier)), _facts_stamp(root, identifier),
            committee, str(cycle), depth)


def committee_payload(committee, cycle, *, identifier='', basis='reported', top=DEFAULT_TOP, depth=DEFAULT_DEPTH,
                      root=None, evidence_view=None, industry=None, build_stamp=None):
    """The /api/flow answer for root=committee:<id>. evidence_view is for tests only."""
    root = Path(root or downstream.ROOT)
    if basis == 'attributed':
        raise FlowRefused(NO_ESTIMATE_BUNDLE, 'no_estimate_bundle', basis='attributed', offer='reported')
    identifier = identifier or newest_snapshot(committee, cycle, root)
    if evidence_view is not None:
        return _answer(_prepare(committee, cycle, identifier, depth, root, evidence_view), top, depth, industry,
                       build_stamp)
    cold = []

    def prepare():
        cold.append(True)
        return _prepare(committee, cycle, identifier, depth, root)
    answer = _answer(_prepared(_key(root, identifier, committee, cycle, depth), prepare), top, depth, industry,
                     build_stamp)
    if cold:
        warm_cycles(committee, cycle, identifier, depth, root)
    return answer


# Lane P1: a cycle switch on a large snapshot is a cold preparation of its own (each culac-20260921
# cycle is its own ~100 MB unit). After a cold preparation, the snapshot's other cycles for the same
# committee are prepared on one daemon thread, newest first, so the switch finds its flow ready.
# server.py turns this on for the console that warms (its own data root, or MENTAT_PREWARM=1);
# MENTAT_FLOW_WARM=0 turns it off. Each cycle waits for the console's low-memory floor.
WARM_CYCLES = False
WARM_MIN_FREE_BYTES = drawer_sidecars.MIN_FREE_BYTES    # prewarm.MIN_FREE_BYTES, 1.5 GB
WARM_DELAY_SECONDS = 0.5                               # the answer that asked goes out first
_warming = set()


def _snapshot_cycles(identifier, committee, root):
    """The cycles the saved snapshot holds for this committee (its index), newest first."""
    path = _snapshot_path(root, identifier)
    info = path.stat()
    try:
        _fingerprint, entries = drawer_sidecars.snapshot_index(str(path), info.st_mtime_ns, info.st_size)
    except drawer_sidecars.Unindexable:
        return []
    return sorted({str(entry.get('cycle')) for entry in entries if entry.get('committee_id') == committee},
                  reverse=True)


def warm_cycles(committee, cycle, identifier, depth, root):
    """Prepare the snapshot's other cycles for this committee on one daemon thread. Never raises; a
    cycle that cannot be drawn is left to the click that asks for it."""
    if not WARM_CYCLES or os.environ.get('MENTAT_FLOW_WARM', '').strip().lower() in ('0', 'off', 'false', 'no'):
        return None
    job = (str(root), identifier, committee, depth)
    with _cache_guard:
        if job in _warming:
            return None
        _warming.add(job)

    def run():
        began, done, skipped = time.perf_counter(), [], ''
        try:
            time.sleep(WARM_DELAY_SECONDS)
            for other in _snapshot_cycles(identifier, committee, root):
                if other == str(cycle):
                    continue
                # A preparation a click started runs alone: the next cycle waits for it (up to a minute).
                deadline = time.monotonic() + 60
                while _preparing and time.monotonic() < deadline:
                    time.sleep(0.2)
                if drawer_sidecars.free_physical_bytes() < WARM_MIN_FREE_BYTES:
                    skipped = f'low memory before {other}'
                    break
                key = _key(root, identifier, committee, other, depth)
                with _cache_guard:
                    if key in _CACHE:
                        continue
                try:
                    _prepared(key, lambda other=other: _prepare(committee, other, identifier, depth, root))
                    done.append(other)
                except Exception:  # noqa: BLE001 - the click that asks for it reports it
                    continue
        except Exception as error:  # noqa: BLE001 - a warm never breaks the console
            skipped = f'{type(error).__name__}: {error}'
        finally:
            with _cache_guard:
                _warming.discard(job)
            try:
                import glassbox_emit
                glassbox_emit.emit('mentat.flow.warm', 'completed' if not skipped else 'skipped',
                                   f'Flow warm {identifier} {committee}: prepared {", ".join(done) or "none"}'
                                   f'{"; " + skipped if skipped else ""}', source='mentat-flow', stage='flow_warm',
                                   duration_ms=int((time.perf_counter() - began) * 1000))
            except Exception:  # noqa: BLE001 - emission never breaks the console it observes
                pass
    thread = threading.Thread(target=run, name='mentat-flow-warm', daemon=True)
    thread.start()
    return thread


def _answer(prepared, top, depth, industry, build_stamp):
    """One request's answer from a prepared flow: its refusal, or its Top N grouping."""
    reconciliation = prepared['reconciliation']
    if not reconciliation['equal']:
        raise FlowRefused(
            'The flow does not reconcile with the evidence network for the same committees '
            f"(flow ${reconciliation['flow_total']:,.2f}, evidence network "
            f"${reconciliation['evidence_network_total']:,.2f}); it is not drawn.",
            'reconciliation', reconciliation=reconciliation)
    broken = prepared['broken']
    if broken:
        raise FlowRefused('A committee in this flow does not balance: ' + ', '.join(
            f'{ident} in {cents(value_in)} out {cents(value_out)}' for ident, value_in, value_out in broken[:3]),
            'conservation')
    # Each request groups its own copies (measure() writes value_in/value_out on the nodes it is given).
    nodes = OrderedDict((key, dict(node)) for key, node in prepared['nodes'].items())
    links = [dict(link) for link in prepared['links']]
    grouped_nodes, grouped_links = group(nodes, links, top)
    measure(grouped_nodes, grouped_links)
    shared = copy.deepcopy(prepared['shared'])
    return {
        'version': VERSION, 'industry': industry, 'build_stamp': build_stamp or prepared['build_stamp'],
        'root': shared['root'], 'cycle': shared['cycle'], 'window': shared['window'],
        'basis': 'reported', 'top': top, 'depth': depth, 'min_share': float(MIN_SHARE),
        'stages': shared['stages'],
        'nodes': [_node_out(node) for node in grouped_nodes.values()],
        'links': [_link_out(link) for link in grouped_links],
        'routes': shared['routes'], 'totals': shared['totals'],
        'coverage': shared['coverage'],
        'reconciliation': reconciliation,
        'carryover': shared['carryover'],
        'notes': shared['notes'],
    }


def _prepare(committee, cycle, identifier, depth, root, evidence_view=None):
    """Everything the answer for one snapshot, committee, cycle and depth holds but the Top N
    grouping, worked out once: the reconciliation, the conservation check, the measured ungrouped
    graph, routes, totals, coverage and notes. It keeps none of the saved units it was read from."""
    built = committee_flow(committee, cycle, identifier=identifier, depth=depth, root=root)
    view = evidence_view if evidence_view is not None else network_view(built)
    prepared = {'reconciliation': reconcile(built, view), 'broken': []}
    if not prepared['reconciliation']['equal']:
        return prepared
    graph = built['graph']
    nodes = OrderedDict((key_, dict(node)) for key_, node in graph.nodes.items())
    links = [dict(link) for link in graph.links.values()]
    measure(nodes, links)
    prepared['broken'] = conservation_errors(nodes)
    if prepared['broken']:
        return prepared
    routes = routes_of(built)
    item = built['root_ledger']
    graph_links = list(graph.links.values())
    root_id = 'committee:' + committee

    def total(kind=None, source=None, target=None, predicate=None):
        return sum((link['value'] for link in graph_links
                    if (kind is None or link['kind'] == kind) and (source is None or link['source'] == source)
                    and (target is None or link['target'] == target) and (predicate is None or predicate(link))), ZERO)

    own = total(source=root_id, predicate=lambda link: link['kind'] in CANDIDATE_KINDS)
    totals = {
        'in': cents(item['intake'] + item['cash_begin']),
        'counted_in': cents(item['counted_in']),
        'refunds_to_donors': cents(item['refunds_to_donors']),
        'donors': cents(total(kind='receipt', target=root_id)),
        'unitemized': cents(max(item['unitemized'], ZERO)),
        'from_network': cents(item['internal_total']),
        'cash_before': cents(item['cash_begin']),
        'transfers_out': cents(total(kind='transfer', source=root_id)),
        'own_candidates': cents(own),
        'operating_net': cents(total(kind='operating', source=root_id, target='balance:operating:' + str(min(2, built['last'])))),
        'refunded_to_donors': cents(item['donor_refunds']),
        'not_yet_spent': {'cash': cents(item['cash_end']), 'held': cents(item['held']),
                          'total': cents(item['cash_end'] + item['held']), 'held_kinds': item['held_kinds']},
        'in_kind': {'booked': cents(item['booked']), 'proceeds': cents(item['sold']), 'held': cents(item['held']),
                    'excess_proceeds': cents(item['excess_proceeds'])},
        'refunds_netted': cents(item['refunds']),
        'receipts_as_reported': cents(item['receipts']),
        'disbursements_as_reported': cents(item['disbursements']),
        'network_candidates': cents(sum((link['value'] for link in graph_links
                                         if graph.nodes[link['target']]['kind'] == 'candidate'), ZERO)),
        'external_in': cents(total(kind='external_in')),
        'before_gate': cents(total(kind='uncounted')),
        'date_unverified': cents(total(predicate=lambda link: bool(link.get('date_unverified')))),
    }
    found, facts = built['found'], built['facts']
    per_committee, unreached = [], []
    for row in downstream.committees(found, built['names']):
        if str(row['cycle']) != built['cycle']:
            continue
        entry = {'id': row['committee'], 'name': row['committee_name'], 'cycle': str(row['cycle']),
                 'itemized_through': row['last_itemized_date'], 'snapshot_through': row['coverage_end'],
                 'snapshot_from': row['coverage_start'], 'drawn': row['committee'] in built['drawn'],
                 'summary': row['reconciliation']}
        if row['saved']:
            per_committee.append(entry)
        else:
            unreached.append({'id': row['committee'], 'name': row['committee_name'], 'cycle': str(row['cycle']),
                              'reason': facts['gaps'].get((str(row['cycle']), row['committee'])) or 'no saved snapshot'})
    totals['unreached_count'] = len(unreached)
    ended_early = [entry['id'] for entry in per_committee if entry['drawn'] and entry['snapshot_through']
                   and entry['snapshot_through'] < item['coverage_end']]
    prepared.update(nodes=nodes, links=links, build_stamp=item['unit'].get('_sha256'), shared={
        'root': {'kind': 'committee', 'id': committee, 'label': built['label'](committee),
                 'name_as_filed': item['name'], 'cycle': built['cycle'], 'snapshot': built['identifier'],
                 'committee_type': str(item['totals'].get('committee_type_full') or ''),
                 'fec_url': committee_url(committee, built['cycle'])},
        'cycle': built['cycle'], 'window': {'start': item['coverage_start'], 'end': item['coverage_end']},
        'stages': built['stages'],
        'routes': routes, 'totals': totals,
        'coverage': {'through': item['coverage_end'], 'ended_early': ended_early,
                     'per_committee': per_committee, 'unreached': unreached,
                     'beyond_depth': sorted(ident for ident in built['hop'] if ident not in built['drawn'])},
        'carryover': carry_summary(built['found']),
        'notes': notes_of(built, totals),
    })
    return prepared


# ------------------------------------------------------------------ the industry flow (Graph, board #45)

# First recipients past Top N fall into the category the workbook gives them.
CATEGORY_BUCKETS = {'Company PAC': ('company-pacs', 'Company PACs'), 'Party Committee': ('parties', 'Party committees')}
OTHER_COMMITTEES = ('other-committees', 'Other committees')
OUTCOME_ORDER = ('support:R', 'support:D', 'support:other', 'oppose:R', 'oppose:D', 'oppose:other', 'contribution',
                 'unknown')


def outcome_of(direction, party):
    """The last column of the industry flow: spending grouped by direction and party."""
    word = DIRECTION_WORD.get(direction, direction if direction in DIRECTIONS else 'unknown')
    if word == 'contribution':
        return 'contribution', 'Contributions to candidates', ''
    if word == 'unknown':
        return 'unknown', 'Direction not reported', ''
    side = party if party in ('R', 'D') else 'other'
    people = {'R': 'Republicans', 'D': 'Democrats'}.get(side, 'other candidates')
    return f'{word}:{side}', f'{word.capitalize()} {people}', side if side != 'other' else ''


def contributor_companies(stamps, cycle):
    """(recipient committee, contributor name key) -> company, from the workbook's own included rows.

    The industry's company attribution, as the workbook made it; nothing is re-attributed here.
    """
    import graph_data
    import native_scan
    columns = ['company', 'contributor', 'committee_id', 'recipient_key', 'include_in_analysis']
    mapping = {}
    for version in stamps:
        sheets = [name for name in graph_data.graph_schema(version) if graph_data.cycle(name) == cycle]
        for sheet in sheets:
            scanned = native_scan.tables(version, [{'name': sheet, 'header': 4, 'columns': columns}])
            if scanned:
                table = scanned['tables'][sheet]
                rows = [dict(zip(table['headers'], row)) for row in table['rows']]
            else:
                import openpyxl
                book = openpyxl.load_workbook(version[0], read_only=True, data_only=True)
                try:
                    rows = list(graph_data.table_rows(book[sheet], 4, ('company',)))
                finally:
                    book.close()
            for row in rows:
                if str(row.get('include_in_analysis') or '').lower() != 'yes' or not row.get('company'):
                    continue
                recipient = graph_data.committee_id(row.get('committee_id')) or graph_data.committee_id(row.get('recipient_key'))
                if recipient and row.get('contributor'):
                    mapping.setdefault((recipient, name_key(row['contributor'])), str(row['company']))
    return mapping


def industry_flow(label, cycle, paths, *, depth=DEFAULT_DEPTH, top=DEFAULT_TOP, root=None):
    """The ungrouped Reported flow for an industry's giving in one cycle (Graph's Follow the money).

    Companies (the workbook's company attribution) -> first recipients -> the committees they
    pass money to -> outcomes (spending by direction and party). A first recipient with a saved
    snapshot is counted from its own filings (donated assets once); one without is the workbook's
    figure and is "Not yet traced" past it (board #45). Only the Top N first recipients by the
    industry's money are followed; the rest are grouped by the workbook's category and stop there.
    """
    import graph_data
    root = Path(root or downstream.ROOT)
    cycle = str(cycle)
    stamps = [graph_data.stamp(Path(path)) for path in paths]
    reports = [graph_data.report_facts(version) for version in stamps]
    facts = [row for report in reports for row in report['facts'] if row['cycle'] == cycle]
    if not facts:
        raise ValueError(f'{label} has no giving recorded in {cycle_label(cycle)}.')
    funded, info = defaultdict(Decimal), {}
    for row in facts:
        funded[row['recipient_id']] += row['amount']
        info.setdefault(row['recipient_id'], {'label': row['recipient'], 'category': row['category'],
                                              'candidate_id': row['candidate_id'], 'party': row['party']})
    eligible = {(cycle, ident) for ident, value in funded.items() if value > 0}
    rows, found = downstream.merge_activity([row for version in stamps for row in graph_data.report_activity(version)],
                                            eligible, report_edges=[row for version in stamps
                                                                    for row in graph_data.report_transfers(version)])
    last = depth
    committees = {ident for ident in funded if COMMITTEE_ID.fullmatch(ident) and funded[ident] > 0
                  and not info[ident]['candidate_id'] and info[ident]['category'] != 'Candidate'}
    transfer_edges = [edge for edge in found['edges'] if edge['cycle'] == cycle and not edge.get('terminal_candidate')]
    pairs = {(edge['source'], edge['destination']) for edge in transfer_edges}
    network_ids = {key[1] for key in found['keys'] if key[0] == cycle}
    names = Names({ident: item['label'] for ident, item in info.items()}, cycle=cycle)
    ledgers = {}
    for key, unit in found['units'].items():
        if key[0] == cycle:
            item = ledger(key, unit, network_ids)
            ledgers[key[1]] = item
            if item:
                names[key[1]] = item['name']
    companies_of = contributor_companies(stamps, cycle)
    # The industry's money into each funded committee: its own filings when saved (counted once),
    # else the workbook's rows.
    money = {}
    for ident in committees:
        item = ledgers.get(ident)
        by_company = defaultdict(lambda: [ZERO, []])
        if item is not None:
            for group, donor in item['donors'].items():
                company = companies_of.get((ident, group))
                if company:
                    by_company[company][0] += donor['amount']
                    by_company[company][1].extend(donor['rows'])
            basis = 'filings'
        else:
            for row in facts:
                if row['recipient_id'] == ident:
                    by_company[row['company']][0] += row['amount']
            basis = 'workbook'
        money[ident] = {'companies': {company: pair for company, pair in by_company.items() if pair[0] > 0},
                        'total': sum((pair[0] for pair in by_company.values()), ZERO), 'basis': basis}
    # A funded committee that another funded committee pays (and does not pay back) is passed to,
    # not a first recipient: FAIRSHAKE's Defend American Jobs stands in the Passed-to column.
    fed_by = defaultdict(set)
    for source, destination in pairs:
        if source in committees and destination in committees and source != destination \
                and (destination, source) not in pairs:
            fed_by[destination].add(source)
    first = sorted((ident for ident in committees if not fed_by.get(ident)),
                   key=lambda ident: (-money[ident]['total'], names.get(ident, ident), ident))
    industry_total = sum((money[ident]['total'] for ident in first), ZERO)
    if top:
        room = max(top - 4, 1)
        followed = [ident for ident in first[:room] if money[ident]['total'] >= industry_total * MIN_SHARE] or first[:1]
    else:
        followed = list(first)
    hop = {ident: 0 for ident in followed}
    queue = list(followed)
    outgoing = defaultdict(list)
    for edge in transfer_edges:
        outgoing[edge['source']].append(edge)
    for current in queue:
        if hop[current] >= depth - 2:
            continue
        for edge in outgoing[current]:
            if edge['destination'] not in hop:
                hop[edge['destination']] = hop[current] + 1
                queue.append(edge['destination'])
    drawn = {ident for ident, level in hop.items() if level <= depth - 2}
    traced = {ident for ident in drawn if ledgers.get(ident)}

    def label_of(ident):
        return committee_label(names, ident)

    def tag_of(ident):
        return committee_tag(names, ident)

    graph = Graph()
    stages = [{'index': 0, 'key': 'companies', 'label': 'Companies'},
              {'index': 1, 'key': 'first', 'label': 'First recipient'}]
    stages += [{'index': index, 'key': f'hop{index - 1}', 'label': 'Passed to' if index == 2 else 'Then to'}
               for index in range(2, last)]
    stages.append({'index': last, 'key': 'outcome', 'label': 'Outcome'})
    parties = {}
    for unit in found['units'].values():
        for row in unit.get('expenditures') or []:
            party = str(row.get('candidate_party') or '').upper()
            if row.get('candidate_id') and party:
                parties.setdefault(str(row['candidate_id']), PARTY_LETTER.get(party, party[:1]))
    for row in rows:
        party = str(row.get('party') or '').upper()
        if row.get('candidate_id') and party:
            parties.setdefault(str(row['candidate_id']), PARTY_LETTER.get(party, party[:1]))

    def company_node(company):
        return graph.node('company:' + company, stage=0, label=company, kind='donor', name_as_filed=company,
                          contribution=True, industry=label)

    def committee_node(ident):
        node = graph.node('committee:' + ident, stage=1 + hop[ident], label=label_of(ident), kind='committee',
                          committee_id=ident, name_as_filed=names.get(ident, ident), fec_url=committee_url(ident, cycle),
                          saved=(cycle, ident) in found['units'], category=info.get(ident, {}).get('category', ''),
                          pooled=True, fixed=True, **unnamed_fields(names, ident), **master_fields(names, ident))
        if (cycle, ident) not in found['units']:
            node['unreached'] = 'no saved snapshot'
        node.update(carryover_of(found, cycle, ident, label_of))
        return node

    def outcome_node(direction, party):
        key, text, side = outcome_of(direction, party)
        return graph.node('outcome:' + key, stage=last, label=text, kind='candidate', direction=key.split(':')[0],
                          party=side, outcome=True, fixed=True, sequence=OUTCOME_ORDER.index(key))

    def sink(kind, text, stage, **fields):
        return graph.node(f'balance:{kind}:{stage}', stage=stage, label=text, kind='balance', balance=kind, **fields)

    def stub(kind, attach, ident, text, **fields):
        node = graph.nodes[attach]
        return graph.node(f'stub:{kind}:{attach}:{ident}', stage=node['stage'], label=text,
                          kind=fields.pop('node_kind', 'committee'), stub=kind, attach=attach, committee_id=ident,
                          fec_url=committee_url(ident, cycle), **fields)

    # ------------------------------------------------------------ companies into first recipients
    grouped = defaultdict(list)
    refunds_back = {True: [], False: []}          # company-recipient pairs below zero: candidates, others
    for ident in sorted(funded):
        if ident in committees and ident in drawn:
            target = committee_node(ident)
            for company, (value, rows_) in sorted(money[ident]['companies'].items()):
                graph.link(company_node(company)['id'], target['id'], value, 'receipt', rows_, _records(rows_),
                           basis=money[ident]['basis'])
            continue
        if ident in committees:
            key, text = CATEGORY_BUCKETS.get(info[ident]['category'], OTHER_COMMITTEES)
            bucket = graph.node(f'bucket:{key}', stage=1, kind='bucket', bucket=key.replace('-', '_'), label=text,
                                followed=False)
            grouped[bucket['id']].append({'id': ident, 'label': tag_of(ident), 'kind': 'committee',
                                          'value': cents(money[ident]['total'])})
            for company, (value, rows_) in sorted(money[ident]['companies'].items()):
                graph.link(company_node(company)['id'], bucket['id'], value, 'receipt', rows_, _records(rows_),
                           basis=money[ident]['basis'])
            continue
        by_company = defaultdict(Decimal)
        for row in facts:
            if row['recipient_id'] == ident:
                by_company[row['company']] += row['amount']
        # Each company-recipient pair nets on its own (the donor-refund rule): a pair at or below zero is
        # not drawn, and what a recipient passes on is exactly what was drawn into it. A pair below zero
        # is a refund from the recipient, disclosed (the industry sweep: Banking 2022's $265.00).
        drawn_in = {company: value for company, value in by_company.items() if value > 0}
        below = [(company, -value) for company, value in by_company.items() if value < 0]
        direct = bool(info[ident]['candidate_id'] or info[ident]['category'] == 'Candidate')
        for company, value in below:
            refunds_back[direct].append({'company': company, 'recipient': ident, 'amount': value})
        given = sum(drawn_in.values(), ZERO)
        if given <= 0:
            continue
        if direct:
            bucket = graph.node('bucket:candidates', stage=1, kind='bucket', bucket='candidates',
                                label='Candidates directly')
            for company, value in sorted(drawn_in.items()):
                graph.link(company_node(company)['id'], bucket['id'], value, 'receipt', 0, [], basis='workbook')
            party = parties.get(info[ident]['candidate_id'], PARTY_LETTER.get(str(info[ident]['party']).upper()[:3], ''))
            graph.link(bucket['id'], outcome_node('Contribution / Other', party)['id'], given, 'contribution', 0, [])
        else:
            bucket = graph.node('bucket:other-recipients', stage=1, kind='bucket', bucket='other_recipients',
                                label='Other recipients', followed=False)
            grouped[bucket['id']].append({'id': ident, 'label': display_org(info[ident]['label']), 'kind': 'recipient',
                                          'value': cents(given)})
            for company, value in sorted(drawn_in.items()):
                graph.link(company_node(company)['id'], bucket['id'], value, 'receipt', 0, [], basis='workbook')
    for bucket_id, members in grouped.items():
        members.sort(key=lambda member: (-member['value'], member['label']))
        graph.nodes[bucket_id]['members'] = members[:MEMBER_LIMIT]
        graph.nodes[bucket_id]['member_count'] = len(members)

    # ------------------------------------------------------------ every drawn committee's outflows
    source_rows = {}
    for key, unit in found['units'].items():
        if key[0] == cycle:
            mapped = defaultdict(list)
            for destination, _value, row in downstream.transfers(unit, gate=downstream.gate_of(found, key)):
                mapped[destination].append(row)
            source_rows[key[1]] = mapped
    by_committee = defaultdict(list)
    for row in rows:
        if row['cycle'] == cycle:
            by_committee[row['committee']].append(row)
    adjustments = defaultdict(Decimal)
    parts = defaultdict(lambda: defaultdict(Decimal))
    for ident in sorted(drawn, key=lambda value: (hop[value], value)):
        node = committee_node(ident)
        item = ledgers.get(ident)
        stage_sink = 2 if hop[ident] == 0 else last
        inbound = [link for link in graph.links.values() if link['target'] == node['id']]
        if item is None:
            # No saved snapshot: the money reached it and is not traced past it (board #45).
            node['untraced'] = True
            received = sum((link['value'] for link in inbound), ZERO)
            if received > 0:
                parts[(stage_sink, 'untraced')][(ident, 'untraced')] += received
            continue
        spent_transfers = ZERO
        for edge in outgoing.get(ident, []):
            destination, value = edge['destination'], Decimal(str(edge['amount']))
            rows_ = source_rows.get(ident, {}).get(destination, [])
            spent_transfers += value
            if destination in drawn and hop[destination] == hop[ident] + 1:
                target = committee_node(destination)
            elif hop.get(destination, hop[ident] + 1) <= hop[ident]:
                target = stub('return', node['id'], destination, 'returned to ' + tag_of(destination))
            else:
                target = stub('onward', node['id'], destination, 'passed to ' + tag_of(destination), beyond=True)
            graph.link(node['id'], target['id'], value, 'transfer', rows_, _records(rows_), committee=ident,
                       **unverified(rows_, edge))
        spent_candidates = ZERO
        for row in by_committee.get(ident, []):
            value = Decimal(str(row['amount']))
            if value <= 0:
                adjustments[ident] += value
                continue
            spent_candidates += value
            party = parties.get(str(row.get('candidate_id') or ''), '')
            filings = row.get('filings') or []
            graph.link(node['id'], outcome_node(row['direction'], party)['id'], value,
                       DIRECTION_KIND.get(row['direction'], 'ie_unknown'), len(filings), [], committee=ident,
                       **unverified(filings, row))
        network_in = sum((link['value'] for link in inbound if link['kind'] == 'transfer'), ZERO)
        company_in = sum((link['value'] for link in inbound if link['kind'] == 'receipt'), ZERO)
        back = defaultdict(Decimal)
        for edge in transfer_edges:
            if edge['destination'] == ident and edge['source'] in hop and hop[edge['source']] >= hop[ident]:
                back[edge['source']] += Decimal(str(edge['amount']))
        for source, value in sorted(back.items()):
            back_stub = stub('return_in', node['id'], source, 'back from ' + tag_of(source))
            rows_ = source_rows.get(source, {}).get(ident, [])
            graph.link(back_stub['id'], node['id'], value, 'transfer', rows_, _records(rows_))
        external = item['intake'] - company_in - network_in - sum(back.values(), ZERO)
        shares = [{'id': 'industry', 'label': f'{label} companies', 'amount': cents(company_in),
                   'share': float((company_in / item['receipts']).quantize(Decimal('0.000001'))) if item['receipts'] > 0 else None,
                   'denominator': 'receipts as reported', 'receipts': cents(item['receipts'])}] if company_in > 0 else []
        shares += [{'id': link['source'], 'label': graph.nodes[link['source']]['label'], 'amount': cents(link['value']),
                    'share': float((link['value'] / item['receipts']).quantize(Decimal('0.000001'))) if item['receipts'] > 0 else None,
                    'denominator': 'receipts as reported', 'receipts': cents(item['receipts'])}
                   for link in inbound if link['kind'] == 'transfer' and not graph.nodes[link['source']].get('stub')]
        node['share_from_upstream'] = shares
        if external > 0:
            outside = stub('external', node['id'], ident, 'Other donors', node_kind='donor')
            graph.link(outside['id'], node['id'], external, 'external_in', 0, [], evidence_url=committee_url(ident, cycle))
        elif external < 0:
            parts[(stage_sink, 'unreported')][(ident, 'untraced')] += -external
        if item['cash_begin'] > 0:
            prior = stub('prior_cash', node['id'], ident, 'Cash from before this window', node_kind='balance',
                         balance='prior_cash',
                         **({'carryover': node['carryover']['label']} if node.get('carryover') else {}))
            graph.link(prior['id'], node['id'], item['cash_begin'], 'prior_cash', 0, [], evidence_url=committee_url(ident, cycle))
        unspent = item['cash_end'] + item['held']
        early, early_rows = before_gate(found, (cycle, ident), item['unit'])
        if early > 0:
            gated = stub('before_gate', node['id'], ident, BEFORE_GATE_TEXT, node_kind='balance',
                         balance='before_gate', gate=downstream.gate_of(found, (cycle, ident)))
            graph.link(node['id'], gated['id'], early, 'uncounted', early_rows, [], committee=ident)
        operating = item['intake'] + item['cash_begin'] - spent_transfers - spent_candidates - unspent - early
        node['operating_check'] = {'operating_expenditures': cents(item['operating_expenditures']),
                                   'refunds_netted': cents(item['refunds']), 'booked_in_kind': cents(item['booked']),
                                   'refunds_to_donors': cents(item['refunds_to_donors']),
                                   'net_operating': cents(operating)}
        if operating < 0:
            node['operating_short'] = cents(-operating)
            outside = stub('external', node['id'], ident, 'Not in the summary', node_kind='balance', balance='unreconciled')
            graph.link(outside['id'], node['id'], -operating, 'external_in', 0, [])
            operating = ZERO
        if hop[ident] == 0:
            parts[(2, 'kept')][(ident, 'operating')] += operating
            parts[(2, 'kept')][(ident, 'unspent')] += unspent
        else:
            parts[(last, 'operating')][(ident, 'operating')] += operating
            parts[(last, 'unspent')][(ident, 'unspent')] += unspent
    texts = {'kept': 'Kept by first recipients', 'operating': 'Operating costs', 'unspent': 'Not yet spent',
             'untraced': 'Not yet traced', 'unreported': 'Not reported by the recipient'}
    for (stage, kind), owners in sorted(parts.items(), key=lambda item: (item[0][0], BALANCE_KEYS.index(item[0][1]))):
        owners = {key: value for key, value in owners.items() if value > 0}
        if not owners:
            continue
        node = sink(kind, texts[kind], stage, committees=sorted({ident for ident, _ in owners}))
        for (ident, link_kind), value in sorted(owners.items()):
            graph.link('committee:' + ident, node['id'], value, link_kind, 0, [], committee=ident)
    parents = {}
    for edge in transfer_edges:
        destination = edge['destination']
        if destination in hop and edge['source'] in hop and hop[destination] == hop[edge['source']] + 1:
            parents.setdefault(destination, edge['source'])
    counted = {ident: money[ident]['total'] for ident in drawn if ident in money and money[ident]['basis'] == 'filings'}
    return {'graph': graph, 'found': found, 'rows': rows, 'ledgers': ledgers, 'hop': hop, 'drawn': traced,
            'shown': drawn, 'names': names, 'stages': stages, 'last': last, 'cycle': cycle, 'label': label_of,
            'parents': parents, 'tag': tag_of, 'adjustments': adjustments, 'counted': counted, 'funded': funded, 'facts': facts,
            'info': info, 'industry': label, 'workbook_total': sum(funded.values(), ZERO), 'reports': reports,
            'committees': committees, 'followed': followed, 'first': first, 'money': money,
            'refunds_back': refunds_back,
            'unfollowed': len(first) - len(followed)}


BALANCE_KEYS = ('kept', 'operating', 'unspent', 'untraced', 'unreported')


def group_first_recipients(nodes, links, top, min_share=MIN_SHARE):
    """First recipients past Top N go into their workbook category (Company PACs, Party committees,
    Other committees) rather than one Other node; the generic rule then leaves those buckets be."""
    if not top:
        return nodes, links
    column = [node for node in nodes.values() if node['stage'] == 1 and not node.get('stub')]
    total = sum((_value(node) for node in column), ZERO)
    committees = sorted((node for node in column if node['kind'] == 'committee'),
                        key=lambda node: (-_value(node), node['label'], node['id']))
    fixed = len(column) - len(committees)
    if len(column) <= top and all(_value(node) >= total * min_share for node in committees):
        return nodes, links
    room = max(top - fixed - 2, 1)
    named = []
    for node in committees[:room]:
        if _value(node) < total * min_share:
            break
        named.append(node)
    remap, others = {}, OrderedDict()
    for node in committees[len(named):]:
        key, text = CATEGORY_BUCKETS.get(node.get('category'), OTHER_COMMITTEES)
        bucket_id = f'bucket:{key}'
        if bucket_id not in others:
            others[bucket_id] = {'id': bucket_id, 'stage': 1, 'kind': 'bucket', 'bucket': key.replace('-', '_'),
                                 'label': text, 'members': [], 'member_count': 0}
        others[bucket_id]['members'].append({'id': node['id'], 'label': node['label'], 'kind': node['kind'],
                                             'value': cents(_value(node))})
        others[bucket_id]['member_count'] += 1
        remap[node['id']] = bucket_id
    return _merge(nodes, links, remap, others)


def _merge(nodes, links, remap, others):
    """Nodes in remap become the node they map to; their links and stubs follow (group's merge)."""
    result = OrderedDict((key, node) for key, node in nodes.items() if key not in remap)
    stub_ids = {}
    for key, node in list(result.items()):
        if node.get('stub') and node.get('attach') in remap:
            attach = remap[node['attach']]
            merged_id = f"stub:{node['stub']}:{attach}:{node.get('committee_id') or node['label']}" \
                if node['stub'] in ('return', 'onward', 'return_in') else f"stub:{node['stub']}:{attach}:{node['label']}"
            stub_ids[key] = merged_id
            del result[key]
            if merged_id not in result:
                result[merged_id] = {**node, 'id': merged_id, 'attach': attach, 'stage': others[attach]['stage']}
                if node['stub'] == 'external':
                    result[merged_id].pop('committee_id', None)
                    result[merged_id].pop('fec_url', None)
    result.update(others)
    target = lambda ident: remap.get(ident, stub_ids.get(ident, ident))  # noqa: E731
    merged = OrderedDict()
    for link in links:
        source, sink_ = target(link['source']), target(link['target'])
        key = (source, sink_, link['kind'])
        if key not in merged:
            merged[key] = {**link, 'source': source, 'target': sink_, 'evidence': list(link.get('evidence') or [])}
            continue
        entry = merged[key]
        entry['value'] += link['value']
        entry['rows'] += link['rows']
        entry['evidence'] = (entry['evidence'] + list(link.get('evidence') or []))[:EVIDENCE_LIMIT]
        if entry.get('committee') != link.get('committee'):
            entry.pop('committee', None)
        if link.get('date_unverified') or entry.get('date_unverified'):
            entry['date_unverified'] = True
    return result, list(merged.values())


def industry_payload(label, cycle, paths, *, basis='reported', top=DEFAULT_TOP, depth=DEFAULT_DEPTH, root=None,
                     build_stamp=None):
    """The /api/flow answer for root=industry: the industry's companies to the outcome of their money."""
    root = Path(root or downstream.ROOT)
    depth = max(depth, 3)
    built = industry_flow(label, cycle, paths, depth=depth, top=top, root=root)
    graph = built['graph']
    drawn_keys = {(built['cycle'], ident) for ident in built['drawn']}
    network_candidates = sum((Decimal(str(row['amount'])) for row in built['rows']
                              if (row['cycle'], row['committee']) in drawn_keys), ZERO)
    network_transfers = sum((Decimal(str(edge['amount'])) for edge in built['found']['edges']
                             if not edge.get('terminal_candidate') and (edge['cycle'], edge['source']) in drawn_keys), ZERO)
    reconciliation = _reconciliation(built, network_candidates, network_transfers,
                                     "candidate spending and committee transfers of the committees this flow draws, "
                                     "against Graph's saved-activity measurement (downstream.merge_activity)")
    if not reconciliation['equal']:
        raise FlowRefused('The flow does not reconcile with the saved downstream activity for the same committees '
                          f"(flow ${reconciliation['flow_total']:,.2f}, activity "
                          f"${reconciliation['evidence_network_total']:,.2f}); it is not drawn.",
                          'reconciliation', reconciliation=reconciliation)
    nodes = OrderedDict((key, dict(node)) for key, node in graph.nodes.items())
    links = [dict(link) for link in graph.links.values()]
    measure(nodes, links)
    broken = conservation_errors(nodes)
    if broken:
        raise FlowRefused('A committee in this flow does not balance: ' + ', '.join(
            f'{ident} in {cents(value_in)} out {cents(value_out)}' for ident, value_in, value_out in broken[:3]),
            'conservation')
    routes = routes_of(built)
    grouped_nodes, grouped_links = group(nodes, links, top)
    measure(grouped_nodes, grouped_links)
    company_links = [link for link in graph.links.values() if graph.nodes[link['source']]['kind'] == 'donor'
                     and graph.nodes[link['source']]['id'].startswith('company:')]
    companies_total = sum((link['value'] for link in company_links), ZERO)
    counted = sum(built['counted'].values(), ZERO)
    workbook_for_counted = sum((built['funded'][ident] for ident in built['counted']), ZERO)
    totals = {
        'companies': cents(companies_total),
        'company_groups': len({link['source'] for link in company_links}),
        'workbook_giving': cents(built['workbook_total']),
        'counted_from_filings': cents(counted),
        'workbook_for_those_committees': cents(workbook_for_counted),
        'network_candidates': cents(_flow_side(built)[0]),
        'external_in': cents(sum((link['value'] for link in graph.links.values() if link['kind'] == 'external_in'), ZERO)),
        'before_gate': cents(sum((link['value'] for link in graph.links.values() if link['kind'] == 'uncounted'), ZERO)),
        'date_unverified': cents(sum((link['value'] for link in graph.links.values() if link.get('date_unverified')), ZERO)),
        'adjustments': cents(sum(built['adjustments'].values(), ZERO)),
    }
    per_committee, unreached = [], []
    for row in downstream.committees(built['found'], built['names']):
        if str(row['cycle']) != built['cycle'] or row['committee'] not in built['shown']:
            continue
        entry = {'id': row['committee'], 'name': row['committee_name'], 'cycle': str(row['cycle']),
                 'itemized_through': row['last_itemized_date'], 'snapshot_through': row['coverage_end'],
                 'snapshot_from': row['coverage_start'], 'drawn': row['committee'] in built['shown'],
                 'summary': row['reconciliation']}
        if row['saved']:
            per_committee.append(entry)
        else:
            unreached.append({'id': row['committee'], 'name': row['committee_name'], 'cycle': str(row['cycle']),
                              'reason': 'no saved snapshot', 'drawn': row['committee'] in built['shown']})
    ends = sorted(entry['snapshot_through'] for entry in per_committee if entry['drawn'] and entry['snapshot_through'])
    groups = totals['company_groups']
    notes = [{'key': 'companies', 'text': f"{short_money(companies_total)} from {groups} compan{'y' if groups == 1 else 'ies'}"
                                          f" in {label} to {len(built['funded'])} recipients"}]
    if counted and workbook_for_counted != counted:
        notes.append({'key': 'counted_once', 'text': f"company giving to {len(built['counted'])} saved committee"
                                                     f"{'s' if len(built['counted']) != 1 else ''} counted once from their own "
                                                     f"filings: {short_money(counted)} (the workbook counts "
                                                     f"{short_money(workbook_for_counted)})"})
    untraced = [row for row in unreached if row['drawn']]
    if untraced:
        notes.append({'key': 'unreached', 'text': f"{len(untraced)} committee{'s' if len(untraced) != 1 else ''} reached but "
                                                  f"not collected {'are' if len(untraced) != 1 else 'is'} shown as Not yet traced"})
    if built['unfollowed']:
        count = built['unfollowed']
        notes.append({'key': 'unfollowed', 'text': f"{count} more first recipient{'s' if count != 1 else ''} grouped by type "
                                                   f"and not followed at Top {top}"})
    notes.append({'key': 'pooled', 'text': 'reported activity: money a committee pools is shown by the committee that '
                                           'spent it, not traced to a company'})
    notes.extend(date_notes(totals))
    notes.extend(carry_notes(built['found'], built['cycle']))
    notes.extend(refund_notes(built['ledgers'], None, built['label'], built['drawn']))
    for direct, who in ((True, 'candidates'), (False, 'other recipients')):
        pairs = built['refunds_back'][direct]
        if pairs:
            amount = sum((pair['amount'] for pair in pairs), ZERO)
            notes.append({'key': 'refunds_back', 'text': f"Refunds from {who} {exact_money(amount)}: {len(pairs)} "
                                                    f"company-recipient pair{'s' if len(pairs) != 1 else ''} net below "
                                                    'zero in this cycle (negative entries in the workbook), not drawn'})
    return {
        'version': VERSION, 'industry': label, 'build_stamp': build_stamp,
        'root': {'kind': 'industry', 'id': 'industry', 'label': label, 'cycle': built['cycle'],
                 'reports': [report['report'] for report in built['reports']]},
        'cycle': built['cycle'], 'window': {'start': f"{int(built['cycle']) - 1}-01-01", 'end': f"{built['cycle']}-12-31"},
        'basis': 'reported', 'top': top, 'depth': depth, 'min_share': float(MIN_SHARE),
        'stages': built['stages'],
        'nodes': [_node_out(node) for node in grouped_nodes.values()],
        'links': [_link_out(link) for link in grouped_links],
        'routes': routes, 'totals': totals,
        'coverage': {'through': ends[0] if ends else '', 'ended_early': [], 'per_committee': per_committee,
                     'unreached': unreached,
                     'beyond_depth': sorted(ident for ident in built['hop'] if ident not in built['drawn'])},
        'reconciliation': reconciliation,
        'carryover': carry_summary(built['found']),
        'notes': notes,
    }


def flow(query, *, report_directory=None, root=None, workbooks=None, allow_direct_collect=True):
    """The route: parse, resolve the industry label when one is named, build or refuse."""
    request = parse_request(query)
    industry, build_stamp = None, None
    if request['industry'] and report_directory is None:
        industry = request['industry']
    elif request['industry']:
        import industry_scope
        import snapshot_registry
        registry = snapshot_registry.registry(report_directory)
        industry = industry_scope.label_or_value(registry, request['industry'])
        build_stamp = next((item.get('build_stamp') for item in registry.get('snapshots') or []
                            if item.get('industry') == industry), None)
    if request['basis'] == 'attributed':
        refuse_attributed(industry, workbooks)
    if request['root_kind'] == 'industry':
        if not industry:
            raise ValueError('Name an industry for its flow.')
        if workbooks is None:
            raise FlowRefused('This console cannot read the industry workbooks.', 'industry_unavailable')
        return industry_payload(industry, request['cycle'], workbooks(industry), basis=request['basis'],
                                top=request['top'], depth=request['depth'], root=root, build_stamp=build_stamp)
    identifier = request['id']
    if request['direct']:
        # A saved unit is reusable without asking the reader to choose a
        # snapshot. Only an uncovered committee/cycle needs a new FEC read.
        try:
            identifier = newest_snapshot(request['committee'], request['cycle'], Path(root or downstream.ROOT))
        except ValueError:
            from pac_flow_access import direct_evidence
            identifier = direct_evidence(request['committee'], request['cycle'], root or downstream.ROOT,
                                         allow_collect=allow_direct_collect)
    return committee_payload(request['committee'], request['cycle'], identifier=identifier, basis=request['basis'],
                             top=request['top'], depth=request['depth'], root=root, industry=industry,
                             build_stamp=build_stamp)


def refuse_attributed(industry, workbooks):
    """basis=attributed: 409 without an estimate bundle (graph_data's own sentence); with one,
    409 too, because this console draws Reported flows only and says so."""
    import graph_data
    paths = []
    if industry and workbooks is not None:
        try:
            paths = workbooks(industry)
        except ValueError:
            paths = []
    for path in paths:
        try:
            manifest = graph_data.audit_manifest(path)
        except (OSError, ValueError):
            manifest = None
        if manifest:
            raise FlowRefused('This industry has an estimate bundle, but attributed flows are not drawn yet; '
                              'the reported flow is.', 'estimate_not_drawn', basis='attributed', offer='reported')
    raise FlowRefused(graph_data.NO_ESTIMATE_BUNDLE, 'no_estimate_bundle', basis='attributed', offer='reported')

"""Resolve money to a congressional district: the read side of the S8 map.

Four questions, four functions, no network and no new dependency:

    load_vintage(cycle)                 the boundary file a cycle is drawn on
    district_for_zip(zip5, cycle)       ZIP -> [(district, share)], approximate
    district_for_candidate(cid, cycle)  a candidate id -> the seat they sought
    committee_state(committee_id)       a committee id -> its registered state

The geography itself is built by ``mentat/tools/build_district_geo.py`` and
shipped under ``mentat/app/geo/``; this module only reads it. The candidate and
committee masters are the FEC bulk files already in the repo under
``mentat/inputs/``. Everything is loaded lazily, once per process, on first
use: a console that never opens the map pays nothing, and a console whose data
files are missing still serves every other tab.

NOTHING HERE RAISES on missing or malformed data. An unknown input, an absent
file and an unreadable file all return None (or an empty list) and leave the
reason in the module-level ``last_problem``. That field is diagnostics for the
page's footnote line, not control flow: it is a plain global, so under the
server's threads it holds the reason from whichever call wrote last. Never
branch on it - branch on the returned value.

WHAT IS EXACT AND WHAT IS APPROXIMATE

  district_for_candidate is exact and local: the FEC candidate master states
  the office, state and district the candidate ran for. It is also only as
  good as the master, which carries out-of-range districts (cn22 has TX-66,
  cn26 has MT-22, neither of which exists); ``district_exists`` is provided so
  a caller can send those to an unmatched bucket instead of drawing them.
  Money is never dropped for want of a district - that is S6_DESIGN's rule and
  this module's one job is to make the unmatched case visible.

  district_for_zip is approximate twice over and must be labelled so (S6
  ruling 8): a ZIP is not a ZCTA, and a ZCTA that straddles districts is split
  by LAND AREA, because the Census relationship file carries no population
  column at all. A single-district ZIP comes back as [(key, 1.0)].

  committee_state is the committee's registered address, which is where the
  paperwork lives, not where the money was spent.

CYCLE TO VINTAGE (S6_DESIGN ruling 9, as Paul corrected it on 2026-09-17)

  2026 -> the 119th map, 2024 -> the 118th, 2022 -> the 118th. index.json is
  the source of truth; this module never hard-codes the mapping. Two facts a
  caller should know:

  * Cycle 2022 draws the 118th map. The 2021-2022 candidate master already
    numbers House seats on the redistricted 118th map (cn22 carries NC-14, a
    seat the 116th map does not have), because 2022 candidates ran in the new
    seats, and this map answers where the money went. Ruling 9 first picked
    the 116th (= 117th; both Congresses sat on the same 2011 lines); Paul's
    2026-09-17 correction moved 2022 to the 118th, and index.json's notes
    record why. So a 2022 ZIP is answered from the 118th crosswalk.
  * The 116th paths stay built and shipped (cd116.json) for reading where the
    members who SAT during 2021-2022 sat, but no cycle points at them, and
    there is no ZIP crosswalk for them: the Census only tabulated the 2020
    ZCTAs against the 118th and 119th districts. A cycle an index did map to
    the 116th would get [] from district_for_zip rather than 118th-map ZIP
    shares - keys like CA-45 exist on both maps and mean different ground.
    ``zip_basis`` says which crosswalk answered.

MEMORY. The boundary file for one vintage parses to a couple of megabytes. A
ZIP crosswalk is 33,791 keys and costs about 20 MB resident once loaded, and
is loaded only when a ZIP is actually looked up. Both stay for the life of the
process, as the committee master already does in evidence_csv.py.
"""
from functools import lru_cache
import json
import os
from pathlib import Path
import re

APP = Path(__file__).resolve().parent
MENTAT = APP.parent

#: The reason the last unanswered call could not answer, or None. Diagnostics
#: only - see the module docstring.
last_problem = None

OFFICE_SENATE = 'SEN'
OFFICE_PRESIDENT = 'US-PRES'
#: The six jurisdictions that send exactly one non-voting member to the House.
#: The two masters disagree on the code for that seat - the Census writes
#: CD..FP 98 (so the map's key is *-AL) while the FEC candidate master writes
#: the district as 00 for five of them and 01 for the Northern Marianas (7 rows
#: across the three cn files, 2026-09-17). Because these places have one seat
#: by statute, folding any district code to AL is a translation between the two
#: coding systems, not a guess: without it those rows resolve to MP-01, which
#: no map has, and the delegate's money would land in the unmatched bucket.
AT_LARGE_JURISDICTIONS = frozenset({'DC', 'PR', 'AS', 'GU', 'MP', 'VI'})

#: The four Census regions, verbatim from the Census Bureau's own state
#: groupings (Northeast, Midwest, South, West), plus a fifth bucket for the
#: territories, which the Census places in no region at all. This table is a
#: LABELLING of states the map already draws - it adds no geometry, because a
#: region is drawn by filling its member states alike. DC sits in the South,
#: which is the Census's placement, not a judgement of this console's.
CENSUS_REGIONS = {
    'Northeast': ('CT', 'ME', 'MA', 'NH', 'RI', 'VT', 'NJ', 'NY', 'PA'),
    'Midwest': ('IL', 'IN', 'MI', 'OH', 'WI', 'IA', 'KS', 'MN', 'MO', 'NE', 'ND', 'SD'),
    'South': ('DE', 'DC', 'FL', 'GA', 'MD', 'NC', 'SC', 'VA', 'WV', 'AL', 'KY', 'MS',
              'TN', 'AR', 'LA', 'OK', 'TX'),
    'West': ('AZ', 'CO', 'ID', 'MT', 'NV', 'NM', 'UT', 'WY', 'AK', 'CA', 'HI', 'OR', 'WA'),
    'Territories': ('PR', 'VI', 'GU', 'AS', 'MP'),
}
REGION_ORDER = ('Northeast', 'Midwest', 'South', 'West', 'Territories')
_REGION_OF_STATE = {state: region for region, states in CENSUS_REGIONS.items() for state in states}


def region_of(state):
    """The Census region a two-letter state code belongs to, or ''.

    '' rather than a guess: a key this table does not know is money the region
    layer must show as unplaced, exactly as the district layer does, instead of
    being folded into whichever region happens to sort first.
    """
    return _REGION_OF_STATE.get(str(state or '').strip().upper(), '')
_CYCLE_DIGITS = re.compile(r'\d{4}')
_STATE = re.compile(r'[A-Z]{2}')
_COMMITTEE = re.compile(r'C\d{8}')
# Two shapes, both nine characters, counted over every cn.txt in the repo
# (2026-09-17: 21,588 + 5,025 rows, nothing else): a House or Senate id carries
# the office state (H0AL01055, S4NY00123) and a presidential id does not
# (P80001571). A pattern that only allowed the first would reject every
# presidential candidate.
_CANDIDATE = re.compile(r'[A-Z]\d(?:[A-Z]{2}\d{5}|\d{7})')


def _problem(reason):
    global last_problem
    last_problem = reason
    return None


def _solved():
    global last_problem
    last_problem = None


def geo_dir():
    """Where the built geography lives; MENTAT_GEO_DIR overrides it for tests."""
    return Path(os.environ.get('MENTAT_GEO_DIR') or (APP / 'geo'))


def inputs_dir():
    """Where the FEC bulk masters live; MENTAT_INPUTS_DIR overrides it."""
    return Path(os.environ.get('MENTAT_INPUTS_DIR') or (MENTAT / 'inputs'))


def normalise_cycle(cycle):
    """Any way a cycle is written -> the even FEC year, or None.

    Accepts 2024, '2024', '2023-2024', '2023–2024' (the en dash the panes
    use) and takes the last four-digit year it finds, because '2023-2024' is
    the 2024 cycle. An odd year is the first half of its cycle, so it rounds
    up: 2023 -> 2024.
    """
    if cycle is None:
        return None
    if isinstance(cycle, bool):
        return None
    if isinstance(cycle, int):
        year = cycle
    else:
        found = _CYCLE_DIGITS.findall(str(cycle))
        if not found:
            return None
        year = int(found[-1])
    if not 1976 <= year <= 2100:
        return None
    return year + (year % 2)


# ----------------------------------------------------------------- the index

@lru_cache(maxsize=4)
def _index(root):
    path = Path(root) / 'index.json'
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except OSError as error:
        return {'error': f'the district map index {path.name} is not available '
                         f'({type(error).__name__}); no district geography is built yet'}
    except ValueError as error:
        return {'error': f'the district map index {path.name} is not valid JSON ({error})'}
    if not isinstance(payload, dict) or not isinstance(payload.get('cycles'), dict):
        return {'error': f'the district map index {path.name} has no cycle map'}
    return payload


def index():
    """The parsed index.json, or None with `last_problem` set."""
    payload = _index(str(geo_dir()))
    if 'error' in payload:
        return _problem(payload['error'])
    _solved()
    return payload


def vintage_for_cycle(cycle):
    """The boundary vintage ('119', '118', '116') a cycle is drawn on, or None."""
    year = normalise_cycle(cycle)
    if year is None:
        return _problem(f'{cycle!r} is not a cycle')
    payload = index()
    if payload is None:
        return None
    vintage = payload['cycles'].get(str(year))
    if not vintage:
        known = ', '.join(sorted(payload['cycles'])) or 'none'
        return _problem(f'no district boundaries are built for the {year} cycle '
                        f'(built cycles: {known})')
    _solved()
    return vintage


@lru_cache(maxsize=8)
def _vintage_file(root, vintage):
    path = Path(root) / f'cd{vintage}.json'
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except OSError as error:
        return {'error': f'the {vintage}th-Congress boundary file {path.name} is not '
                         f'available ({type(error).__name__})'}
    except ValueError as error:
        return {'error': f'the boundary file {path.name} is not valid JSON ({error})'}
    if not isinstance(payload.get('districts'), dict):
        return {'error': f'the boundary file {path.name} carries no districts'}
    return payload


def load_vintage(cycle):
    """The boundary payload for `cycle`: districts, states, canvas size.

    Cached per vintage for the life of the process, so the dict returned is
    SHARED: a caller that mutates it corrupts every later reader. Copy what you
    need. Returns None with `last_problem` set when the cycle is unknown or the
    file is missing.
    """
    vintage = vintage_for_cycle(cycle)
    if vintage is None:
        return None
    payload = _vintage_file(str(geo_dir()), vintage)
    if 'error' in payload:
        return _problem(payload['error'])
    _solved()
    return payload


@lru_cache(maxsize=8)
def _single_seat(root, vintage):
    payload = _vintage_file(root, vintage)
    if 'error' in payload:
        return frozenset()
    seats = {}
    for key in list(payload.get('districts') or {}) + list(payload.get('undrawn') or {}):
        state, _, seat = str(key).partition('-')
        seats.setdefault(state, set()).add(seat)
    return frozenset(state for state, found in seats.items() if found == {'AL'})


def single_seat_states(cycle):
    """The states with exactly one House seat on the map `cycle` is drawn on.

    Read from that map itself, never from a list: the Census codes a lone seat
    00 and the builder draws it as <ST>-AL, so a state whose only seat is -AL
    has one seat in that Congress (MT on the 116th map, not on the 118th or
    119th). Pure - it leaves `last_problem` alone - and empty when the cycle
    has no map.
    """
    year = normalise_cycle(cycle)
    if year is None:
        return frozenset()
    root = str(geo_dir())
    payload = _index(root)
    vintage = None if 'error' in payload else payload['cycles'].get(str(year))
    return _single_seat(root, vintage) if vintage else frozenset()


def _at_large(key, cycle):
    """'<ST>-01' -> '<ST>-AL' when <ST> has one seat on this cycle's map (F7).

    Filings number a single-seat state's seat 01 (FAIRSHAKE's Schedule E rows
    for AK, SD and DE, 2026-09-25) while the map, like the Census, draws it as
    at-large; without this the money lands in `unlocated` as a seat no map has.
    A state with two or more seats keeps its 01. No cycle, no translation: the
    seat count belongs to one Congress.
    """
    if cycle is None or not key or not key.endswith('-01'):
        return key
    return f'{key[:2]}-AL' if key[:2] in single_seat_states(cycle) else key


def district_exists(district_key, cycle):
    """True when `district_key` is a seat on this cycle's map.

    False for a district the FEC master reports but no map has (TX-66), for a
    Senate or presidential key, and whenever the map itself is unavailable -
    the caller's unmatched bucket is the right home for all of those. A
    territory with no 20m boundary counts as existing: it has a seat, it just
    cannot be coloured, and the builder lists it under `undrawn`.
    """
    payload = load_vintage(cycle)
    if payload is None:
        return False
    key = str(district_key or '').strip().upper()
    return key in payload['districts'] or key in (payload.get('undrawn') or {})


# --------------------------------------------------------------- ZIP crosswalk

@lru_cache(maxsize=8)
def _crosswalk(root, vintage):
    path = Path(root) / f'zcta_cd{vintage}.json'
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except OSError as error:
        return {'__error__': f'no ZIP-to-district crosswalk is built for the {vintage}th '
                             f'Congress ({path.name}, {type(error).__name__}); the Census '
                             f'only tabulated the 2020 ZCTAs against the 118th and 119th'}
    except ValueError as error:
        return {'__error__': f'the crosswalk {path.name} is not valid JSON ({error})'}
    return payload


def zip_basis(cycle):
    """What a ZIP lookup for `cycle` would be answered from, without doing one.

    {'vintage': '119', 'file': 'zcta_cd119.json', 'basis': 'land area ...'} or
    None. Pure: unlike `last_problem` it is safe to read from any thread, and
    it is what the map's footnote line should quote.
    """
    vintage = vintage_for_cycle(cycle)
    if vintage is None:
        return None
    payload = index()
    if payload is None:
        return None
    entry = (payload.get('vintages') or {}).get(vintage) or {}
    if not entry.get('zcta_file'):
        return _problem(f'no ZIP-to-district crosswalk exists for the {vintage}th-Congress '
                        f'map, so ZIP geography cannot be shown for this cycle')
    _solved()
    return {'vintage': vintage, 'file': entry['zcta_file'],
            'source': entry.get('zcta_source'), 'basis': entry.get('zcta_basis')}


def district_for_zip(zip5, cycle):
    """[(district key, share)] for a ZIP, largest share first; [] if unknown.

    The shares sum to 1.0 and are land area, not population (the source has no
    population column). A ZIP inside one district returns one pair with share
    1.0; a ZIP that straddles districts returns each part, and the caller must
    show it as approximate and split.
    """
    digits = re.sub(r'\D', '', str(zip5 or ''))
    if len(digits) < 5:
        _problem(f'{zip5!r} is not a five-digit ZIP')
        return []
    basis = zip_basis(cycle)
    if basis is None:
        return []
    payload = _crosswalk(str(geo_dir()), basis['vintage'])
    if '__error__' in payload:
        _problem(payload['__error__'])
        return []
    parts = payload.get(digits[:5])
    if not parts:
        _problem(f'ZIP {digits[:5]} is in no ZCTA on the {basis["vintage"]}th-Congress '
                 f'crosswalk (a PO-box-only or retired ZIP has no ZCTA)')
        return []
    _solved()
    return [(key, share) for key, share in parts]


# --------------------------------------------------- tract crosswalk (S8b)

@lru_cache(maxsize=8)
def _tract_crosswalk(root, vintage):
    path = Path(root) / f'tract_cd{vintage}.json'
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except OSError as error:
        return {'__error__': f'no census-tract-to-district crosswalk is built for the {vintage}th '
                             f'Congress ({path.name}, {type(error).__name__}); run '
                             f'tools/build_tract_geo.py'}
    except ValueError as error:
        return {'__error__': f'the tract crosswalk {path.name} is not valid JSON ({error})'}
    if not isinstance(payload.get('districts'), dict):
        return {'__error__': f'the tract crosswalk {path.name} carries no districts'}
    return payload


def district_for_tract(tract_geoid, vintage):
    """The seat an 11-digit 2020 census tract sits in, or '' - S8b.

    EXACT where it answers, and silent where it cannot. The US Census BATCH
    geocoder does not return a congressional district (measured 2026-09-18: its
    geography columns are STATE, COUNTY, TRACT, BLOCK under every vintage and
    the layers parameter is ignored), so a geocoded street address reaches its
    district through the Census's own CD-to-tract tabulation, built by
    tools/build_tract_geo.py.

    Only tracts lying WHOLLY inside one district are in those files. A tract
    split between two districts is absent on purpose: placing an address in it
    would need a land-area share, which is exactly the approximation the ZIP
    crosswalk already makes, so such a row keeps its ZIP basis rather than
    being reported as an exact street match. `vintage` is a boundary vintage
    ('119', '118'), not a cycle - one geocode serves every cycle and the caller
    decides which map it is reading.
    """
    digits = re.sub(r'\D', '', str(tract_geoid or ''))
    if len(digits) != 11:
        return _problem(f'{tract_geoid!r} is not an 11-digit 2020 census tract') or ''
    key = str(vintage or '').strip()
    if not key:
        return _problem('no boundary vintage was named for the tract lookup') or ''
    payload = _tract_crosswalk(str(geo_dir()), key)
    if '__error__' in payload:
        _problem(payload['__error__'])
        return ''
    found = payload['districts'].get(digits)
    if not found:
        _problem(f'census tract {digits} is not wholly inside one {key}th-Congress district, '
                 f'so this address keeps its approximate ZIP basis')
        return ''
    _solved()
    return found


def tract_basis(cycle):
    """{'vintage', 'file', 'source', 'basis'} for a cycle's tract crosswalk, or None."""
    vintage = vintage_for_cycle(cycle)
    if vintage is None:
        return None
    payload = index()
    if payload is None:
        return None
    entry = (payload.get('vintages') or {}).get(vintage) or {}
    if not entry.get('tract_file'):
        return _problem(f'no census-tract-to-district crosswalk is recorded for the {vintage}th-'
                        f'Congress map, so street addresses cannot be placed for this cycle')
    _solved()
    return {'vintage': vintage, 'file': entry['tract_file'],
            'source': entry.get('tract_source'), 'basis': entry.get('tract_basis')}


# ----------------------------------------------------------- candidate master

@lru_cache(maxsize=4)
def _candidate_master(root):
    """{cycle year: {CAND_ID: district key}} from every cn*/cn.txt present.

    Pipe-delimited, no header:
      CAND_ID|CAND_NAME|PTY|CAND_ELECTION_YR|CAND_OFFICE_ST|CAND_OFFICE|
      CAND_OFFICE_DISTRICT|ICI|STATUS|PCC|...
    A folder named cn24 is the 2023-24 cycle. Nothing here raises: a file that
    cannot be read contributes nothing and says so in `warnings`.
    """
    base = Path(root) / 'fec_beneficiary'
    by_cycle, warnings = {}, []
    try:
        folders = sorted((path for path in base.glob('cn*') if (path / 'cn.txt').is_file()),
                         key=lambda path: path.name, reverse=True)
    except OSError as error:
        folders = []
        warnings.append(f'the FEC candidate master folder could not be listed '
                        f'({type(error).__name__})')
    for folder in folders:
        suffix = folder.name[2:]
        if not suffix.isdigit():
            continue
        year = 2000 + int(suffix)
        try:
            text = (folder / 'cn.txt').read_text(encoding='utf-8', errors='replace')
        except OSError as error:
            warnings.append(f'the FEC candidate master {folder.name}/cn.txt could not be read '
                            f'({type(error).__name__}); its candidates stay unmatched')
            continue
        rows = {}
        for line in text.splitlines():
            fields = line.split('|')
            if len(fields) < 7:
                continue
            identifier = fields[0].strip().upper()
            if not _CANDIDATE.fullmatch(identifier) or identifier in rows:
                continue
            key = _candidate_key(fields[4], fields[5], fields[6])
            if key:
                rows[identifier] = key
        by_cycle[year] = rows
    return {'by_cycle': by_cycle, 'cycles': sorted(by_cycle, reverse=True),
            'warnings': warnings}


def _candidate_key(state, office, district, cycle=None):
    """The district key for one candidate-master row, or None.

    House  -> '<ST>-NN', and '<ST>-AL' for the at-large '00' the FEC writes for
              a single-seat state, and for any district code in one of the six
              one-seat non-voting jurisdictions (see AT_LARGE_JURISDICTIONS).
              With a cycle, district 01 (or 1) is at-large too when the state
              has exactly one seat on that cycle's map (_at_large, F7).
              A blank district in a single-seat state (three rows across the
              three masters) stays None: the master simply did not say, and
              guessing is not this module's job.
    Senate -> '<ST>-SEN' (a state-wide race: the map heats the whole state).
    White House -> 'US-PRES' (no district at all).
    The district number is passed through exactly as the master states it, even
    when no such seat exists, so the caller can show it as unmatched rather
    than have this module silently invent or drop a seat.
    """
    state = (state or '').strip().upper()
    office = (office or '').strip().upper()
    district = (district or '').strip().upper()
    if office == 'P':
        return OFFICE_PRESIDENT
    if not _STATE.fullmatch(state):
        return None
    if office == 'S':
        return f'{state}-{OFFICE_SENATE}'
    if office != 'H':
        return None
    if district in ('00', '0', 'AL') or state in AT_LARGE_JURISDICTIONS:
        return f'{state}-AL'
    if district.isdigit():
        return _at_large(f'{state}-{int(district):02d}', cycle)
    return None


def district_for_candidate(candidate_id, cycle=None):
    """The seat a candidate ran for: '<ST>-NN', '<ST>-AL', '<ST>-SEN', 'US-PRES'.

    The cycle's own master answers first, because redistricting moved seats
    between them and a candidate's district is a fact about the election they
    ran in, not about the newest file. When that cycle's master does not carry
    the id (an id first seen in a later filing, or no cycle given), the newest
    master that does carry it answers, and the fallback is recorded in
    `last_problem` so the page can footnote it. None when no master knows the
    id or when none can be read.
    """
    identifier = str(candidate_id or '').strip().upper()
    if not _CANDIDATE.fullmatch(identifier):
        return _problem(f'{candidate_id!r} is not an FEC candidate id')
    master = _candidate_master(str(inputs_dir()))
    if not master['cycles']:
        return _problem('the FEC candidate master is not available, so candidate districts '
                        'cannot be resolved: ' + ('; '.join(master['warnings'])
                                                  or 'no cn*/cn.txt under inputs'))
    year = normalise_cycle(cycle)
    if year is not None and year in master['by_cycle']:
        key = master['by_cycle'][year].get(identifier)
        if key:
            _solved()
            return _at_large(key, year)
    for other in master['cycles']:
        if other == year:
            continue
        key = master['by_cycle'][other].get(identifier)
        if key:
            if year is not None:
                _problem(f'{identifier} is not in the {year} candidate master; the seat comes '
                         f'from the {other} master, where redistricting may have renumbered it')
            else:
                _solved()
            return _at_large(key, year)
    return _problem(f'{identifier} is in no FEC candidate master '
                    f'({", ".join(str(item) for item in master["cycles"])}), so its spending '
                    f'stays unmatched')


# ----------------------------------------------------------- committee master

@lru_cache(maxsize=4)
def _committee_states(root):
    """{CMTE_ID: (state, cycle)} from cm*/cm.txt, newest cycle first.

    evidence_csv.committee_master() reads the same files but keeps only
    designation, type and org type (fields 9, 10 and 13), so the registered
    state is read here instead of widening that function - S6 owns that file
    this session. Field 7 (index 6) is CMTE_ST:
      CMTE_ID|CMTE_NM|TRES_NM|ST1|ST2|CITY|CMTE_ST|ZIP|DSGN|TP|PTY|FREQ|ORG_TP|...
    """
    base = Path(root) / 'fec_committee_master'
    rows, by_cycle, cycles, warnings = {}, {}, [], []
    try:
        folders = sorted((path for path in base.glob('cm*') if (path / 'cm.txt').is_file()),
                         key=lambda path: path.name, reverse=True)
    except OSError as error:
        folders = []
        warnings.append(f'the FEC committee master folder could not be listed '
                        f'({type(error).__name__})')
    for folder in folders:
        try:
            text = (folder / 'cm.txt').read_text(encoding='utf-8', errors='replace')
        except OSError as error:
            warnings.append(f'the FEC committee master {folder.name}/cm.txt could not be read '
                            f'({type(error).__name__})')
            continue
        cycles.append(folder.name)
        per_cycle = by_cycle.setdefault(folder.name, {})
        for line in text.splitlines():
            fields = line.split('|')
            if len(fields) < 13:
                continue
            identifier = fields[0].strip().upper()
            if not _COMMITTEE.fullmatch(identifier):
                continue
            state = fields[6].strip().upper()
            if not _STATE.fullmatch(state):
                continue
            per_cycle.setdefault(identifier, state)
            rows.setdefault(identifier, (state, folder.name))
    return {'rows': rows, 'by_cycle': by_cycle, 'cycles': cycles, 'warnings': warnings}


def committee_state(committee_id, cycle=None):
    """The state a committee is registered in ('VA'), or None.

    Where the paperwork lives, not where the money went: S6_DESIGN maps
    transfers to other committees by this state and says so on the map.

    S8: a committee MOVES. Protect Progress (C00848440) is registered in FL in
    the 2023-2024 master and in MN in the 2025-2026 one, so a transfer made in
    the 2024 cycle belongs to FL and only a cycle-less call should answer with
    the newest paperwork. Passing `cycle` reads that cycle's own master first
    and records the fallback in `last_problem` when it has to use another one;
    omitting it keeps the original newest-first behaviour exactly.
    """
    identifier = str(committee_id or '').strip().upper()
    if not _COMMITTEE.fullmatch(identifier):
        return _problem(f'{committee_id!r} is not an FEC committee id')
    master = _committee_states(str(inputs_dir()))
    if not master['cycles']:
        return _problem('the FEC committee master is not available, so committee states '
                        'cannot be resolved: ' + ('; '.join(master['warnings'])
                                                  or 'no cm*/cm.txt under inputs'))
    year = normalise_cycle(cycle)
    if year is not None:
        state = (master.get('by_cycle') or {}).get(f'cm{year % 100:02d}', {}).get(identifier)
        if state:
            _solved()
            return state
    entry = master['rows'].get(identifier)
    if entry is None:
        return _problem(f'{identifier} is in no FEC committee master '
                        f'({", ".join(master["cycles"])})')
    if year is not None:
        _problem(f'{identifier} is not in the {year} committee master; its state comes from '
                 f'{entry[1]}, where the committee may have been registered elsewhere')
    else:
        _solved()
    return entry[0]


def reset_caches():
    """Drop every cached file. For tests that swap the data root mid-process."""
    global last_problem
    for cache in (_index, _vintage_file, _single_seat, _crosswalk, _tract_crosswalk,
                  _candidate_master, _committee_states):
        cache.cache_clear()
    last_problem = None

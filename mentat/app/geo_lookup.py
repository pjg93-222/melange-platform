"""Where a row is on the v2 maps: the console side of the lane G geography.

Plain Python, standard library only, ON PURPOSE. The console's Python is managed
by the Codex app and was reset on the 2026-09-25 reboot, losing everything
installed into it; GeoPandas, Shapely, pyproj and GeoPy live only in the system
Python that runs the offline tools. So every spatial join is done offline by
mentat/tools/geo_assign.py, which writes the lookup tables this module reads
from mentat/app/geo/v2/lookup/, and the one thing computed here - a hexagon id
for a latitude/longitude - is plain math: the EPSG:5070 projection (Snyder's
ellipsoidal Albers on GRS80) and the pointy-top axial hexagon formula, pinned to
the tool's pyproj answers by mentat/tests/test_geo_lookup.py (console Python)
and test_geo_v2_assign.py (system Python). Importing this module imports nothing
outside the standard library.

    locate(latitude=, longitude=, tract=, zip=, city=, state=)   -> an answer dict
    for_zcta(zip) / for_tract(geoid) / for_place(geoid) / for_state(code)
    find_place(city, state)                    the place a free-text city names
    hex_id(latitude, longitude, size)          'h25:<q>:<r>'
    albers_5070(latitude, longitude)           EPSG:5070 metres
    albers_5070_inverse(x, y)                  (latitude, longitude)
    hex_center(hex_id)                         {'x', 'y', 'latitude', 'longitude'}
    located_line(answers, level)               '1,942 of 2,017 located'
    assignment(source_sha256)                  a bulk assignment geo_assign.py cached

PRECISION TRAVELS WITH EVERY ANSWER

Every answer carries "precision", one of:
  address   a street address the Census batch geocoder placed (app/geocode.py):
            its latitude/longitude and 2020 census tract
  zip       the ZIP is a ZCTA; the point is the ZCTA's gazetteer internal point
  city      a city + state the place gazetteer names; the point is the place's
            internal point
  state     only the state is known: no point, no hexagon
  none      nothing usable
The GEOIDs come from the most precise key the row has (tract, then ZCTA, then
place, then state) and "geoid_basis" says which; a level that key cannot answer
is None, never guessed. A tract or ZCTA that straddles a district answers its
largest share and lists every share under "shares".

Legends say how many rows a view could place: located_line(answers, level)
counts the answers whose precision the level can use (PLACEABLE); a tract view
places only addresses, a ZIP view addresses and ZIPs, a state view everything
but "none".

Nothing here raises on bad input or a missing table: an unknown key answers
None and a missing table leaves the answer at a lower precision;
tables_available() says what is on disk.
"""
from __future__ import annotations

from functools import lru_cache
import json
import math
import os
from pathlib import Path
import re
import unicodedata

APP = Path(__file__).resolve().parent
MENTAT = APP.parent
PRECISIONS = ('address', 'zip', 'city', 'state', 'none')
HEX_SIZES = (10, 25, 50)
LEVELS = ('state', 'county', 'tract', 'zcta', 'place', 'cd116', 'cd118', 'cd119', 'sldu', 'sldl')
DISTRICT_LEVELS = ('cd116', 'cd118', 'cd119', 'sldu', 'sldl')

#: The precisions each aggregation can place (build packet s3, "Aggregate by"):
#: a row below the level's need is listed, not placed.
PLACEABLE = {
    'point': ('address', 'zip', 'city'),
    'hexbin': ('address', 'zip', 'city'),
    'density': ('address', 'zip', 'city'),
    'tract': ('address',),
    'zip': ('address', 'zip'),
    'place': ('address', 'zip', 'city'),
    'county': ('address', 'zip', 'city'),
    'district': ('address', 'zip', 'city'),
    'sld': ('address', 'zip', 'city'),
    'state': ('address', 'zip', 'city', 'state'),
}


def v2_dir():
    """The v2 geography folder; MENTAT_GEO_V2 overrides it for tests."""
    return Path(os.environ.get('MENTAT_GEO_V2') or (APP / 'geo' / 'v2'))


def lookup_dir():
    return v2_dir() / 'lookup'


def assigned_dir():
    """Where geo_assign.py caches bulk assignments (runtime state, never versioned)."""
    return Path(os.environ.get('MENTAT_GEO_ASSIGNED') or (MENTAT / 'state' / 'geography'
                                                          / 'assigned-v2'))


# ------------------------------------------------------------ EPSG:5070 math
#
# NAD83 / Conus Albers: GRS80, standard parallels 29.5 and 45.5 N, origin 23 N
# 96 W, no false easting or northing. Snyder, Map Projections - A Working
# Manual (1987): q from eq. 3-12, n/C/rho from eqs. 14-3 to 14-4 and 14-12 to
# 14-15, the inverse latitude from eq. 3-16 by iteration. The longitude offset
# is wrapped into [-180, 180) as PROJ does, so the Aleutians east of 180
# degrees stay beside the rest of Alaska.

GRS80_A = 6378137.0
GRS80_F = 1.0 / 298.257222101
E2 = GRS80_F * (2.0 - GRS80_F)
E = math.sqrt(E2)
LAT0, LON0, LAT1, LAT2 = 23.0, -96.0, 29.5, 45.5
MILE = 1609.344
SQRT3 = math.sqrt(3.0)


def _q(sin_phi):
    con = E * sin_phi
    return (1.0 - E2) * (sin_phi / (1.0 - con * con)
                         - (0.5 / E) * math.log((1.0 - con) / (1.0 + con)))


def _m(phi):
    sin_phi = math.sin(phi)
    return math.cos(phi) / math.sqrt(1.0 - E2 * sin_phi * sin_phi)


_M1 = _m(math.radians(LAT1))
_M2 = _m(math.radians(LAT2))
_Q0 = _q(math.sin(math.radians(LAT0)))
_Q1 = _q(math.sin(math.radians(LAT1)))
_Q2 = _q(math.sin(math.radians(LAT2)))
N = (_M1 * _M1 - _M2 * _M2) / (_Q2 - _Q1)
C = _M1 * _M1 + N * _Q1
RHO0 = GRS80_A * math.sqrt(C - N * _Q0) / N


def albers_5070(latitude, longitude):
    """(x, y) metres in EPSG:5070 for a NAD83 latitude/longitude in degrees."""
    delta = (float(longitude) - LON0 + 180.0) % 360.0 - 180.0
    inner = C - N * _q(math.sin(math.radians(float(latitude))))
    rho = GRS80_A * math.sqrt(max(inner, 0.0)) / N
    theta = N * math.radians(delta)
    return rho * math.sin(theta), RHO0 - rho * math.cos(theta)


def albers_5070_inverse(x, y):
    """(latitude, longitude) degrees for EPSG:5070 metres."""
    dy = RHO0 - float(y)
    rho = math.hypot(float(x), dy)
    theta = math.atan2(float(x), dy)
    q = (C - (rho * N / GRS80_A) ** 2) / N
    phi = math.asin(max(-1.0, min(1.0, q / 2.0)))
    for _ in range(30):
        sin_phi = math.sin(phi)
        con = E * sin_phi
        cos_phi = math.cos(phi)
        if abs(cos_phi) < 1e-15:
            break
        step = ((1.0 - E2 * sin_phi * sin_phi) ** 2 / (2.0 * cos_phi)
                * (q / (1.0 - E2) - sin_phi / (1.0 - con * con)
                   + (0.5 / E) * math.log((1.0 - con) / (1.0 + con))))
        phi += step
        if abs(step) < 1e-15:
            break
    longitude = (LON0 + math.degrees(theta / N) + 180.0) % 360.0 - 180.0
    return math.degrees(phi), longitude


# ------------------------------------------------------------- hexagon math

def hex_side(size_miles):
    return float(size_miles) * MILE / SQRT3


def hex_axial(x, y, size_miles):
    """Axial (q, r) of the pointy-top hexagon holding EPSG:5070 point (x, y).

    size = the distance between neighbouring centres (flat-to-flat width);
    side = size / sqrt(3). Rounding is floor(v + 0.5), never Python's
    round-half-to-even, so a JS or numpy port lands on the same hexagon; the
    cube-coordinate correction then fixes the component rounding moved most.
    """
    side = hex_side(size_miles)
    qf = (SQRT3 / 3.0 * x - y / 3.0) / side
    rf = (2.0 / 3.0 * y) / side
    sf = -qf - rf
    q = math.floor(qf + 0.5)
    r = math.floor(rf + 0.5)
    s = math.floor(sf + 0.5)
    dq, dr, ds = abs(q - qf), abs(r - rf), abs(s - sf)
    if dq > dr and dq > ds:
        q = -r - s
    elif dr > ds:
        r = -q - s
    return int(q), int(r)


def _point(latitude, longitude):
    try:
        latitude, longitude = float(latitude), float(longitude)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(latitude) and math.isfinite(longitude)):
        return None
    if not (-90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0):
        return None
    if latitude == 0.0 and longitude == 0.0:             # the geocoder's "no answer"
        return None
    return latitude, longitude


def hex_id(latitude, longitude, size_miles):
    """'h<size>:<q>:<r>' for a latitude/longitude, or None when unusable."""
    point = _point(latitude, longitude)
    try:
        size = int(size_miles)
    except (TypeError, ValueError):
        return None
    if point is None or size not in HEX_SIZES:
        return None
    x, y = albers_5070(point[0], point[1])
    q, r = hex_axial(x, y, size)
    return f'h{size}:{q}:{r}'


def hex_ids(latitude, longitude):
    return {str(size): hex_id(latitude, longitude, size) for size in HEX_SIZES}


_HEX_ID = re.compile(r'^h(10|25|50):(-?\d+):(-?\d+)$')


def hex_center(identifier):
    """{'x', 'y', 'latitude', 'longitude'} of a hexagon's centre, or None."""
    match = _HEX_ID.match(str(identifier or ''))
    if not match:
        return None
    size, q, r = int(match.group(1)), int(match.group(2)), int(match.group(3))
    side = hex_side(size)
    x = side * SQRT3 * (q + r / 2.0)
    y = side * 1.5 * r
    latitude, longitude = albers_5070_inverse(x, y)
    return {'x': x, 'y': y, 'latitude': latitude, 'longitude': longitude}


# ------------------------------------------------------------- place names

#: The legal/statistical words the gazetteer appends to a place's NAME,
#: lower case as the gazetteer writes them (a proper name uses capitals:
#: "Carson City" keeps its City).
LSAD_SUFFIXES = (' (balance)', ' metropolitan government', ' metro government',
                 ' unified government', ' consolidated government', ' urban county',
                 ' city and borough', ' zona urbana', ' comunidad', ' municipality', ' borough',
                 ' village', ' town', ' city', ' CDP', ' township', ' corporation',
                 ' government')
_ABBREVIATIONS = {'SAINT': 'ST', 'SAINTE': 'STE', 'FORT': 'FT', 'MOUNT': 'MT'}
_COMPASS = {'NORTH': 'N', 'SOUTH': 'S', 'EAST': 'E', 'WEST': 'W'}


def strip_lsad(name):
    """A gazetteer NAME without its suffix: 'St. Louis city' -> 'St. Louis'."""
    text = str(name or '').strip()
    changed = True
    while changed:
        changed = False
        for suffix in LSAD_SUFFIXES:
            if text.endswith(suffix) and len(text) > len(suffix):
                text = text[:-len(suffix)].rstrip()
                changed = True
    return text


def place_words(text):
    """Upper-case words, accents and punctuation gone, SAINT/FORT/MOUNT and a
    leading compass word abbreviated the way USPS city lines write them."""
    folded = unicodedata.normalize('NFKD', str(text or ''))
    folded = ''.join(ch for ch in folded if not unicodedata.combining(ch)).upper()
    words = re.sub(r'[^A-Z0-9]+', ' ', folded.replace("'", '')).split()
    if words[:2] == ['CITY', 'OF']:
        words = words[2:]
    words = [_ABBREVIATIONS.get(word, word) for word in words]
    if len(words) > 1 and words[0] in _COMPASS:
        words[0] = _COMPASS[words[0]]
    return words


def normalise_place_name(text):
    """The key both the tool's name index and every query use: place_words
    joined WITHOUT spaces, so 'MC KINNEY' meets 'McKinney' and 'COEUR D ALENE'
    meets "Coeur d'Alene". The tool imports this function; the two sides
    cannot drift."""
    return ''.join(place_words(text))


def city_keys(city, state_usps=''):
    """Candidate keys for a free-text city, most literal first."""
    words = place_words(city)
    if not words:
        return []
    variants = [words]
    if state_usps and len(words) > 1 and words[-1] == str(state_usps).upper():
        variants.append(words[:-1])                           # 'WASHINGTON DC' in DC
    for tail in ('CITY', 'TOWN', 'VILLAGE', 'BORO'):
        for variant in list(variants):
            if len(variant) > 1 and variant[-1] == tail:
                variants.append(variant[:-1])                 # 'NEW YORK CITY' -> 'NEW YORK'
    return list(dict.fromkeys(''.join(variant) for variant in variants))


# ----------------------------------------------------------------- tables

def _read(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


@lru_cache(maxsize=4)
def _states(root):
    return _read(Path(root) / 'states.json') or {}


@lru_cache(maxsize=16)
def _zcta_file(root, digit):
    return _read(Path(root) / 'zcta' / f'{digit}.json') or {}


@lru_cache(maxsize=64)
def _tract_file(root, statefp):
    return _read(Path(root) / 'tract' / f'{statefp}.json') or {}


@lru_cache(maxsize=64)
def _place_file(root, statefp):
    return _read(Path(root) / 'place' / f'{statefp}.json') or {}


def reset_caches():
    for cache in (_states, _zcta_file, _tract_file, _place_file):
        cache.cache_clear()


def tables_available():
    """{'root', 'states': bool, 'zcta': files, 'tract': files, 'place': files}."""
    root = lookup_dir()

    def count(name):
        folder = root / name
        return len(list(folder.glob('*.json'))) if folder.is_dir() else 0
    return {'root': str(root), 'states': (root / 'states.json').is_file(),
            'zcta': count('zcta'), 'tract': count('tract'), 'place': count('place')}


def _row(payload, key):
    values = (payload.get('rows') or {}).get(key)
    if values is None:
        return None
    return dict(zip(payload.get('fields') or [], values))


def for_state(code):
    """{'statefp', 'usps', 'name', ...} for a postal or FIPS code, or None."""
    payload = _states(str(lookup_dir()))
    text = str(code or '').strip().upper()
    if not text or not payload:
        return None
    fips = text.zfill(2) if text.isdigit() else (payload.get('by_usps') or {}).get(text)
    row = _row(payload, fips) if fips else None
    return dict(row, statefp=fips) if row else None


def for_zcta(zip_value):
    """The lookup row for a ZIP's first five digits when they are a ZCTA, else None."""
    digits = re.sub(r'\D', '', str(zip_value or ''))[:5]
    if len(digits) != 5:
        return None
    row = _row(_zcta_file(str(lookup_dir()), digits[0]), digits)
    return dict(row, zcta=digits) if row else None


def for_tract(geoid):
    """The lookup row for an 11-digit census tract GEOID, else None.

    Connecticut answers both numberings: the Census geocoder's 2020 codes
    (counties 09001-09015) and the planning-region codes of the 2024 files
    (09110-09190) meet by TRACTCE, which is unique statewide; the answer then
    carries the current code in "tract" and the one asked in "tract_asked".
    """
    digits = re.sub(r'\D', '', str(geoid or ''))
    if len(digits) != 11:
        return None
    payload = _tract_file(str(lookup_dir()), digits[:2])
    row = _row(payload, digits)
    current = digits
    if row is None and payload.get('by_tractce'):
        current = payload['by_tractce'].get(digits[5:]) or ''
        row = _row(payload, current) if current else None
    if row is None:
        return None
    answer = dict(row, tract=current, state=current[:2], county=current[:5])
    if current != digits:
        answer['tract_asked'] = digits
    return answer


def for_place(geoid):
    """The lookup row for a 7-digit place GEOID, else None."""
    digits = re.sub(r'\D', '', str(geoid or ''))
    if len(digits) != 7:
        return None
    row = _row(_place_file(str(lookup_dir()), digits[:2]), digits)
    return dict(row, place=digits, state=digits[:2]) if row else None


def find_place(city, state_code):
    """The place a free-text city names within one state, or None - never a
    guess across states. Several places sharing a key: the tool ordered them
    (exact name before alias, incorporated before CDP, larger land area first)."""
    found = for_state(state_code)
    if not found:
        return None
    names = _place_file(str(lookup_dir()), found['statefp']).get('names') or {}
    for key in city_keys(city, found.get('usps', '')):
        candidates = names.get(key)
        if candidates:
            row = for_place(candidates[0])
            if row:
                return dict(row, matched_key=key, candidates=len(candidates))
    return None


def assignment(source_sha256):
    """A bulk assignment geo_assign.py cached for a source, or None."""
    digest = str(source_sha256 or '').lower()
    if not re.fullmatch(r'[0-9a-f]{64}', digest):
        return None
    for path in sorted(assigned_dir().glob(f'{digest[:16]}-*.json')):
        payload = _read(path)
        if payload and (payload.get('source') or {}).get('sha256') == digest:
            return payload
    return None


# ----------------------------------------------------------------- answers

def _primary(value):
    """(geoid, shares or None) from a lookup value: a GEOID or [[geoid, share], ...]."""
    if value is None or value == '':
        return None, None
    if isinstance(value, str):
        return value, None
    if isinstance(value, list) and value and isinstance(value[0], list):
        return str(value[0][0]), [[str(g), float(s)] for g, s in value]
    return None, None


def district_key(cd_geoid, usps_of):
    """'NY-12' / 'AK-AL' from a district GEOID, keyed as geo/cd*.json key them."""
    text = str(cd_geoid or '')
    if len(text) != 4 or not text[:2].isdigit():
        return None
    usps = usps_of.get(text[:2])
    number = text[2:]
    if not usps or not number.isdigit():
        return None
    if number in ('00', '98'):
        return f'{usps}-AL'
    return f'{usps}-{int(number):02d}'


def _answer(precision, basis, inputs):
    return {'precision': precision, 'located': precision in ('address', 'zip', 'city'),
            'point': None, 'basis': basis, 'geoid_basis': None,
            'geoid': {level: None for level in LEVELS},
            'district': {'116': None, '118': None, '119': None},
            'hex': {str(size): None for size in HEX_SIZES}, 'shares': {}, 'state': None,
            'input': inputs}


def _fill(answer, row, basis, levels):
    answer['geoid_basis'] = basis
    for level in levels:
        geoid, shares = _primary(row.get(level))
        if geoid:
            answer['geoid'][level] = geoid
        if shares and len(shares) > 1:
            answer['shares'][level] = shares


def _finish(answer):
    rows = _states(str(lookup_dir())).get('rows') or {}
    usps_of = {fips: row[0] for fips, row in rows.items() if isinstance(row, list) and row}
    if answer['geoid'].get('state'):
        answer['state'] = usps_of.get(answer['geoid']['state'])
    for vintage in ('116', '118', '119'):
        answer['district'][vintage] = district_key(answer['geoid'].get(f'cd{vintage}'), usps_of)
    return answer


def _table_hexes(row):
    return {str(size): row.get(f'h{size}') or hex_id(row.get('lat'), row.get('lon'), size)
            for size in HEX_SIZES}


def locate(latitude=None, longitude=None, tract=None, zip=None, city=None, state=None):  # noqa: A002
    """The best answer for one row, with its precision (see the module docstring)."""
    inputs = {'latitude': latitude, 'longitude': longitude, 'tract': tract, 'zip': zip,
              'city': city, 'state': state}
    point = _point(latitude, longitude)
    tract_row = for_tract(tract) if tract else None
    zip_row = for_zcta(zip) if zip else None
    state_row = for_state(state) if state else None
    if point is not None or tract_row is not None:
        answer = _answer('address', 'Census geocoder street address', inputs)
        if point is None:
            point = _point(tract_row.get('lat'), tract_row.get('lon'))
            answer['basis'] = ('census tract internal point (the row has a tract, no coordinates)'
                               if point else 'census tract only: a water tract the 500k file '
                                             'omits, so no point')
        if point is not None:
            answer['point'] = [point[0], point[1]]
            answer['hex'] = hex_ids(point[0], point[1])
        answer['located'] = point is not None
        if tract_row is not None:
            _fill(answer, tract_row, 'tract', DISTRICT_LEVELS)
            answer['geoid'].update(state=tract_row['state'], county=tract_row['county'],
                                   tract=tract_row['tract'])
            if zip_row is not None:
                answer['geoid']['zcta'] = zip_row['zcta']
        elif zip_row is not None:
            _fill(answer, zip_row, 'zcta', ('state', 'county') + DISTRICT_LEVELS)
            answer['geoid']['zcta'] = zip_row['zcta']
            answer['basis'] += '; its GEOIDs from its ZIP (the row has no tract)'
        elif state_row is not None:
            answer['geoid']['state'] = state_row['statefp']
            answer['geoid_basis'] = 'state'
        return _finish(answer)
    if zip_row is not None:
        answer = _answer('zip', f'ZCTA {zip_row["zcta"]} internal point (2024 gazetteer)', inputs)
        answer['point'] = [zip_row['lat'], zip_row['lon']]
        _fill(answer, zip_row, 'zcta', ('state', 'county', 'tract', 'place') + DISTRICT_LEVELS)
        answer['geoid']['zcta'] = zip_row['zcta']
        answer['hex'] = _table_hexes(zip_row)
        return _finish(answer)
    if city and state_row is not None:
        found = find_place(city, state_row['statefp'])
        if found is not None:
            answer = _answer('city', f'place {found["place"]} internal point (2024 gazetteer)',
                             inputs)
            answer['point'] = [found['lat'], found['lon']]
            _fill(answer, found, 'place', ('county', 'tract', 'zcta') + DISTRICT_LEVELS)
            answer['geoid'].update(state=found['state'], place=found['place'])
            answer['hex'] = _table_hexes(found)
            answer['matched'] = {'key': found['matched_key'], 'name': found.get('name'),
                                 'candidates': found['candidates']}
            return _finish(answer)
    if state_row is not None:
        answer = _answer('state', 'state only', inputs)
        answer['geoid']['state'] = state_row['statefp']
        answer['geoid_basis'] = 'state'
        return _finish(answer)
    return _finish(_answer('none', 'nothing usable', inputs))


POINT_LEVELS = ('point', 'hexbin', 'density')


def placeable(answer, level):
    """Whether this level can place the answer: its precision is good enough and,
    for the point-drawn levels, it has a point."""
    answer = answer or {}
    if answer.get('precision') not in PLACEABLE.get(level, ()):
        return False
    return answer.get('point') is not None if level in POINT_LEVELS else True


def precision_counts(answers):
    counts = {name: 0 for name in PRECISIONS}
    for answer in answers:
        name = (answer or {}).get('precision', 'none')
        counts[name] = counts.get(name, 0) + 1
    return counts


def located_line(answers, level='point'):
    """'1,942 of 2,017 located': the rows this level can place, of all rows."""
    answers = list(answers)
    placed = sum(1 for answer in answers if placeable(answer, level))
    return f'{placed:,} of {len(answers):,} located'

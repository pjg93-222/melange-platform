# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT  # centralized root (arch review 2026-07-02)
"""DISTRICT INDUSTRY VALIDATOR — independent QCEW, BEA, and Census checks.

Collector-cadence Phase E of the Melange demographic expansion.  This standalone
collector/validator is deliberately not wired into run_pipeline.py.  It mirrors
annual QCEW singlefiles and BEA CAEMP25N, then runs the outline's cross-validation
battery against the Phase B/C/D data-plane artifacts.

Inputs:
  DEMO_DATA_ROOT/lodes/lodes_cd_panel.csv.gz
  DEMO_DATA_ROOT/geo/county_cd_weights.csv.gz
  DEMO_DATA_ROOT/cbp/cbp23co.zip
  DEMO_DATA_ROOT/cbp/cbp23cd.xlsx
  MB_ROOT/district_geography.json
  MB_ROOT/district_industry.json
  MB_ROOT/district_industry_4digit.json

Raw/cache outputs under DEMO_DATA_ROOT/validate:
  {year}_annual_singlefile.zip
  CAEMP25N.zip

Repo-plane output:
  MB_ROOT/district_industry_validation.json, unless VAL_OUT overrides it.

Dials:
  DEMO_DATA_ROOT — out-of-OneDrive raw-data root; defaults to
                   %LOCALAPPDATA%/Melange/demographics
  QCEW_GAP      — state-total absolute-gap bar; defaults to "0.10"
  VAL_OUT       — optional output-path override
  VAL_FORCE     — set to "1" to re-download validation sources
"""
import csv
import datetime
import gzip
import hashlib
import io
import json
import math
import os
import re
import sys
import time
import urllib.request
import zipfile
from collections import defaultdict

from moneyball_core import dump_atomic

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

USER_AGENT = 'MelangeDemographics/1.0 (research; contact: repo owner)'
CHUNK_SIZE = 1024 * 1024
REQUEST_TIMEOUT = 120
DOWNLOAD_ATTEMPTS = 4
RETRY_BASE_SECONDS = 3
DIRECT_WEIGHT_MIN = 0.9999

QCEW_URL = (
    'https://data.bls.gov/cew/data/files/{year}/csv/'
    '{year}_annual_singlefile.zip'
)
BEA_URL = 'https://apps.bea.gov/regional/zip/CAEMP25N.zip'

FIPS_TO_USPS = {
    '01': 'AL', '02': 'AK', '04': 'AZ', '05': 'AR', '06': 'CA',
    '08': 'CO', '09': 'CT', '10': 'DE', '11': 'DC', '12': 'FL',
    '13': 'GA', '15': 'HI', '16': 'ID', '17': 'IL', '18': 'IN',
    '19': 'IA', '20': 'KS', '21': 'KY', '22': 'LA', '23': 'ME',
    '24': 'MD', '25': 'MA', '26': 'MI', '27': 'MN', '28': 'MS',
    '29': 'MO', '30': 'MT', '31': 'NE', '32': 'NV', '33': 'NH',
    '34': 'NJ', '35': 'NM', '36': 'NY', '37': 'NC', '38': 'ND',
    '39': 'OH', '40': 'OK', '41': 'OR', '42': 'PA', '44': 'RI',
    '45': 'SC', '46': 'SD', '47': 'TN', '48': 'TX', '49': 'UT',
    '50': 'VT', '51': 'VA', '53': 'WA', '54': 'WV', '55': 'WI',
    '56': 'WY', '60': 'AS', '66': 'GU', '69': 'MP', '72': 'PR',
    '78': 'VI',
}
USPS_TO_FIPS = {usps: fips for fips, usps in FIPS_TO_USPS.items()}
VOTING_STATES = (
    'AK', 'AL', 'AR', 'AZ', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA',
    'HI', 'IA', 'ID', 'IL', 'IN', 'KS', 'KY', 'LA', 'MA', 'MD',
    'ME', 'MI', 'MN', 'MO', 'MS', 'MT', 'NC', 'ND', 'NE', 'NH',
    'NJ', 'NM', 'NV', 'NY', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC',
    'SD', 'TN', 'TX', 'UT', 'VA', 'VT', 'WA', 'WI', 'WV', 'WY',
)
STATE_GEOS = VOTING_STATES + ('DC',)
VOTING_FIPS = frozenset(USPS_TO_FIPS[state] for state in VOTING_STATES)
STATE_FIPS = frozenset(USPS_TO_FIPS[state] for state in STATE_GEOS)

COUNT_COLUMNS = (
    'C000',
    'CNS01', 'CNS02', 'CNS03', 'CNS04', 'CNS05',
    'CNS06', 'CNS07', 'CNS08', 'CNS09', 'CNS10',
    'CNS11', 'CNS12', 'CNS13', 'CNS14', 'CNS15',
    'CNS16', 'CNS17', 'CNS18', 'CNS19', 'CNS20',
)
SECTOR_COLUMNS = COUNT_COLUMNS[1:]
PANEL_COLUMNS = ('geo', 'year') + COUNT_COLUMNS
QCEW_SECTORS = frozenset((
    '11', '21', '22', '23', '31-33', '42', '44-45', '48-49',
    '51', '52', '53', '54', '55', '56', '61', '62', '71',
    '72', '81', '92',
))
QCEW_RANGE_MAP = {
    'CNS05': '31-33',
    'CNS07': '44-45',
    'CNS08': '48-49',
}
CANONICAL_QCEW_MAP = {
    'CNS01': '11',
    'CNS02': '21',
    'CNS03': '22',
    'CNS04': '23',
    'CNS05': '31-33',
    'CNS06': '42',
    'CNS07': '44-45',
    'CNS08': '48-49',
    'CNS09': '51',
    'CNS10': '52',
    'CNS11': '53',
    'CNS12': '54',
    'CNS13': '55',
    'CNS14': '56',
    'CNS15': '61',
    'CNS16': '62',
    'CNS17': '71',
    'CNS18': '72',
    'CNS19': '81',
    'CNS20': '92',
}
QCEW_OWN_CODES = frozenset(('0', '1', '2', '3', '5'))
TOTAL_NAICS = '------'
UNKNOWN = object()
EXTERNAL_VALUE_COUNT_KEYS = (
    'known',
    'empty',
    'n_sentinel',
    'parenthesized',
    'disclosure',
)


def _utc_iso(timestamp=None):
    if timestamp is None:
        value = datetime.datetime.now(datetime.timezone.utc)
    else:
        value = datetime.datetime.fromtimestamp(
            timestamp, datetime.timezone.utc
        )
    return value.replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as source:
        while True:
            chunk = source.read(CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _parse_float_dial(name, default):
    text = os.environ.get(name, str(default))
    try:
        value = float(text)
    except (TypeError, ValueError) as exc:
        raise SystemExit(
            'invalid %s %r; expected a finite non-negative number' %
            (name, text)
        ) from exc
    if not math.isfinite(value) or value < 0:
        raise SystemExit(
            'invalid %s %r; expected a finite non-negative number' %
            (name, text)
        )
    return value


def _load_json(path, label):
    if not os.path.exists(path):
        raise SystemExit('%s does not exist: %s' % (label, path))
    try:
        with open(
            path, 'r', encoding='utf-8-sig', errors='strict'
        ) as source:
            value = json.load(source)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SystemExit(
            'failed to load %s %s: %s' % (label, path, str(exc))
        ) from exc
    if not isinstance(value, dict):
        raise SystemExit(
            'invalid %s %s: expected a JSON object' % (label, path)
        )
    return value


def _require_input_files(inputs):
    missing = [
        '%s: %s' % (label, path)
        for label, path in inputs
        if not os.path.isfile(path)
    ]
    if missing:
        raise SystemExit(
            'required validation input(s) absent: %s' %
            '; '.join(missing)
        )


def _discard_part(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def _validate_zip(path, label):
    try:
        with zipfile.ZipFile(path, 'r') as archive:
            bad_member = archive.testzip()
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise OSError(
            '%s ZIP integrity check failed: %s' % (label, str(exc))
        ) from exc
    if bad_member is not None:
        raise OSError(
            '%s ZIP integrity check failed at member %s' %
            (label, bad_member)
        )


def _preserve_corrupt_cache(path, label):
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime(
        '%Y%m%dT%H%M%S%fZ'
    )
    corrupt_path = path + '.corrupt-' + stamp
    try:
        os.rename(path, corrupt_path)
    except OSError as exc:
        raise SystemExit(
            'failed to preserve invalid cached %s %s: %s' %
            (label, path, str(exc))
        ) from exc
    print('!! preserved invalid cached %s as %s' % (label, corrupt_path))


def _download_to_part(url, part_path):
    request = urllib.request.Request(
        url, headers={'User-Agent': USER_AGENT}
    )
    with urllib.request.urlopen(
        request, timeout=REQUEST_TIMEOUT
    ) as response:
        with open(part_path, 'wb') as target:
            while True:
                chunk = response.read(CHUNK_SIZE)
                if not chunk:
                    break
                target.write(chunk)


def _download_source(label, url, path, force):
    if os.path.exists(path) and not force:
        try:
            _validate_zip(path, label)
        except Exception:
            _preserve_corrupt_cache(path, label)
        else:
            print('cached %s: %s' % (label, path))
            return {
                'url': url,
                'sha256': _sha256(path),
                'retrieved': _utc_iso(os.path.getmtime(path)),
            }

    part_path = path + '.part'
    failure = None
    for attempt in range(DOWNLOAD_ATTEMPTS):
        try:
            print(
                'downloading %s (attempt %d/%d): %s' %
                (label, attempt + 1, DOWNLOAD_ATTEMPTS, url)
            )
            _download_to_part(url, part_path)
            _validate_zip(part_path, label)
            os.replace(part_path, path)
            print('downloaded %s: %s' % (label, path))
            return {
                'url': url,
                'sha256': _sha256(path),
                'retrieved': _utc_iso(),
            }
        except Exception as exc:
            failure = exc
            _discard_part(part_path)
            if attempt == DOWNLOAD_ATTEMPTS - 1:
                break
            delay = RETRY_BASE_SECONDS * (attempt + 1)
            print(
                '!! download retry for %s in %d seconds: %s' %
                (label, delay, str(exc))
            )
            time.sleep(delay)

    raise SystemExit(
        'download failed for %s after %d attempts: %s' %
        (label, DOWNLOAD_ATTEMPTS, str(failure))
    ) from failure


def _header_map(fieldnames, label):
    if not fieldnames:
        raise SystemExit('%s has no header row' % label)
    result = {}
    for header in fieldnames:
        normalized = (header or '').strip().lower()
        if not normalized:
            raise SystemExit(
                '%s has a blank header; headers=%r' % (label, fieldnames)
            )
        if normalized in result:
            raise SystemExit(
                '%s has duplicate case-insensitive header %r; headers=%r' %
                (label, normalized, fieldnames)
            )
        result[normalized] = header
    return result


def _required_header(headers, name, label, fieldnames):
    header = headers.get(name)
    if header is None:
        raise SystemExit(
            '%s is missing required column %s; headers=%r' %
            (label, name, fieldnames)
        )
    return header


def _row_cell(row, header, label, line_num):
    value = row.get(header)
    if value is None:
        raise SystemExit(
            '%s row %d is missing column %r' %
            (label, line_num, header)
        )
    return value


def _parse_nonnegative_int(text, label, allow_commas=False):
    value_text = (text or '').strip()
    if allow_commas:
        value_text = value_text.replace(',', '')
    try:
        value = int(value_text)
    except ValueError as exc:
        raise SystemExit(
            '%s has invalid integer count %r' % (label, value_text)
        ) from exc
    if value < 0:
        raise SystemExit('%s has negative count %d' % (label, value))
    return value


def _new_external_value_counts():
    return {key: 0 for key in EXTERNAL_VALUE_COUNT_KEYS}


def _classify_external_numeric(
    value, label, counters, allow_commas=False, disclosed=False
):
    if disclosed:
        counters['disclosure'] += 1
        return UNKNOWN
    if value is None:
        counters['empty'] += 1
        return UNKNOWN
    if isinstance(value, bool):
        raise SystemExit('%s has invalid boolean count %r' % (label, value))
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            raise SystemExit(
                '%s has invalid integer count %r' % (label, value)
            )
        parsed = int(value)
    else:
        text = str(value).strip()
        if not text:
            counters['empty'] += 1
            return UNKNOWN
        if text.upper() == 'N':
            counters['n_sentinel'] += 1
            return UNKNOWN
        if text.startswith('(') and text.endswith(')'):
            counters['parenthesized'] += 1
            return UNKNOWN
        if allow_commas:
            text = text.replace(',', '')
        try:
            parsed = int(text)
        except ValueError as exc:
            raise SystemExit(
                '%s has invalid integer count %r' % (label, text)
            ) from exc
    if parsed < 0:
        raise SystemExit('%s has negative count %d' % (label, parsed))
    counters['known'] += 1
    return parsed


def _parse_count_cell(
    row, header, label, line_num, counters,
    disclosed=False
):
    return _classify_external_numeric(
        row.get(header),
        '%s row %d column %s' % (label, line_num, header),
        counters,
        disclosed=disclosed,
    )


def _normalize_fips(value, width, label):
    text = '' if value is None else str(value).strip()
    if text.endswith('.0') and text[:-2].isdigit():
        text = text[:-2]
    if not text.isdigit() or len(text) > width:
        raise SystemExit('%s has invalid FIPS component %r' % (label, text))
    return text.zfill(width)


def _single_csv_member(archive, label):
    members = [
        member for member in archive.namelist()
        if not member.endswith('/') and member.lower().endswith('.csv')
    ]
    if len(members) != 1:
        listing = ', '.join(archive.namelist()) or '<empty archive>'
        raise SystemExit(
            '%s archive has %d CSV members; members: %s' %
            (label, len(members), listing)
        )
    return members[0]


def _single_cbp_member(archive, label):
    members = [
        member for member in archive.namelist()
        if (
            not member.endswith('/') and
            member.lower().endswith(('.txt', '.csv'))
        )
    ]
    if len(members) != 1:
        listing = ', '.join(archive.namelist()) or '<empty archive>'
        raise SystemExit(
            '%s archive has %d TXT/CSV members; members: %s' %
            (label, len(members), listing)
        )
    return members[0]


def _validate_geography(artifact, path):
    if artifact.get('schema') != 'district-geography-v1':
        raise SystemExit(
            'invalid district geography schema in %s: %r' %
            (path, artifact.get('schema'))
        )
    states = artifact.get('states')
    if not isinstance(states, dict):
        raise SystemExit(
            'district geography %s has no states object' % path
        )
    missing = [state for state in VOTING_STATES if state not in states]
    if missing:
        raise SystemExit(
            'district geography %s is missing state(s): %s' %
            (path, ', '.join(missing))
        )
    expected_districts = []
    for state in VOTING_STATES:
        detail = states[state]
        districts = detail.get('districts') if isinstance(detail, dict) else None
        redrawn = (
            detail.get('redrawn_since_118')
            if isinstance(detail, dict) else None
        )
        if (
            not isinstance(districts, list) or
            not isinstance(redrawn, list) or
            any(
                not isinstance(cd, str) or
                re.fullmatch(r'[0-9]{2}', cd) is None
                for cd in districts
            )
        ):
            raise SystemExit(
                'district geography %s has invalid %s detail' %
                (path, state)
            )
        expected_districts.extend(
            '%s-%s' % (state, cd) for cd in districts
        )
    if len(expected_districts) != 435:
        raise SystemExit(
            'district geography %s has %d districts; expected 435' %
            (path, len(expected_districts))
        )
    return {
        'states': states,
        'districts': tuple(sorted(expected_districts)),
    }


def _validate_phase_c(artifact, path, expected_districts):
    if artifact.get('schema') != 'district-industry-v1':
        raise SystemExit(
            'invalid Phase C schema in %s: %r' %
            (path, artifact.get('schema'))
        )
    sector_meta = artifact.get('sector_meta')
    geos = artifact.get('geos')
    if not isinstance(sector_meta, dict) or set(sector_meta) != set(SECTOR_COLUMNS):
        raise SystemExit(
            'Phase C artifact %s has invalid sector_meta keys' % path
        )
    if not isinstance(geos, dict):
        raise SystemExit('Phase C artifact %s has no geos object' % path)

    latest_by_state = {}
    latest_years = set()
    district_geos = set()
    for geo, detail in geos.items():
        if not isinstance(detail, dict):
            raise SystemExit(
                'Phase C artifact %s has invalid geo %r' % (path, geo)
            )
        latest_year = detail.get('latest_year')
        if type(latest_year) is not int:
            raise SystemExit(
                'Phase C artifact %s geo %s has invalid latest_year %r' %
                (path, geo, latest_year)
            )
        latest_years.add(latest_year)
        if geo in STATE_GEOS:
            latest_by_state[geo] = latest_year
        elif detail.get('kind') == 'district':
            district_geos.add(geo)

    missing_states = [
        state for state in STATE_GEOS if state not in latest_by_state
    ]
    if missing_states:
        raise SystemExit(
            'Phase C artifact %s is missing state geo(s): %s' %
            (path, ', '.join(missing_states))
        )
    expected_set = set(expected_districts)
    if district_geos != expected_set:
        raise SystemExit(
            'Phase C district universe mismatch in %s: missing=%r; '
            'unexpected=%r' %
            (
                path,
                sorted(expected_set - district_geos),
                sorted(district_geos - expected_set),
            )
        )

    if (
        set(CANONICAL_QCEW_MAP) != set(SECTOR_COLUMNS) or
        len(set(CANONICAL_QCEW_MAP.values())) != len(SECTOR_COLUMNS) or
        set(CANONICAL_QCEW_MAP.values()) != set(QCEW_SECTORS)
    ):
        raise SystemExit('internal canonical CNS/QCEW mapping invariant failed')

    sector_to_naics = {}
    for sector in SECTOR_COLUMNS:
        detail = sector_meta[sector]
        if not isinstance(detail, dict):
            raise SystemExit(
                'Phase C artifact %s has invalid sector %s metadata' %
                (path, sector)
            )
        actual_naics = detail.get('naics')
        expected_naics = CANONICAL_QCEW_MAP[sector]
        if actual_naics != expected_naics:
            raise SystemExit(
                'Phase C artifact %s sector %s maps to QCEW NAICS %r; '
                'expected canonical %r' %
                (path, sector, actual_naics, expected_naics)
            )
        sector_to_naics[sector] = expected_naics

    return {
        'latest_by_state': latest_by_state,
        'latest_years': tuple(sorted(latest_years)),
        'sector_to_naics': sector_to_naics,
    }


def _validate_phase_d(artifact, path, expected_districts):
    if artifact.get('schema') != 'district-industry-4digit-v1':
        raise SystemExit(
            'invalid Phase D schema in %s: %r' %
            (path, artifact.get('schema'))
        )
    weights = artifact.get('weights')
    if not isinstance(weights, dict):
        raise SystemExit('Phase D artifact %s has no weights object' % path)
    districts = artifact.get('districts')
    if not isinstance(districts, dict):
        raise SystemExit('Phase D artifact %s has no districts object' % path)
    if set(districts) != set(expected_districts):
        raise SystemExit(
            'Phase D district universe mismatch in %s' % path
        )
    required = (
        'unallocated_est_share',
        'unallocated_emp_share',
        'per_state_source_year',
    )
    missing = [field for field in required if field not in weights]
    if missing:
        raise SystemExit(
            'Phase D weights %s is missing field(s): %s' %
            (path, ', '.join(missing))
        )
    for field in ('unallocated_est_share', 'unallocated_emp_share'):
        value = weights[field]
        if (
            isinstance(value, bool) or
            not isinstance(value, (int, float)) or
            not math.isfinite(value) or
            value < 0 or value > 1
        ):
            raise SystemExit(
                'Phase D weights %s has invalid %s %r' %
                (path, field, value)
            )
    return weights


def _validate_weight_vintages(phase_d_weights, latest_by_state):
    declared = phase_d_weights.get('per_state_source_year')
    if not isinstance(declared, dict):
        raise SystemExit(
            'Phase D weights per_state_source_year is not an object'
        )
    missing = [state for state in STATE_GEOS if state not in declared]
    if missing:
        raise SystemExit(
            'Phase D weights per_state_source_year is missing state(s): %s' %
            ', '.join(missing)
        )
    invalid = [
        '%s=%r' % (state, declared[state])
        for state in STATE_GEOS
        if type(declared[state]) is not int
    ]
    if invalid:
        raise SystemExit(
            'Phase D weights has invalid per-state source year(s): %s' %
            ', '.join(invalid)
        )
    mismatches = [
        '%s weights=%d PhaseC=%d' %
        (state, declared[state], latest_by_state[state])
        for state in STATE_GEOS
        if declared[state] != latest_by_state[state]
    ]
    if mismatches:
        raise SystemExit(
            'weights-cache/Phase C latest-year mismatch: %s' %
            '; '.join(mismatches)
        )


def _load_lodes_states(path, latest_by_state):
    if not os.path.exists(path):
        raise SystemExit('LODES panel does not exist: %s' % path)
    retained = {}
    with gzip.open(path, 'rb') as compressed:
        with io.TextIOWrapper(
            compressed,
            encoding='utf-8-sig',
            errors='strict',
            newline=''
        ) as source:
            reader = csv.DictReader(source)
            if reader.fieldnames != list(PANEL_COLUMNS):
                raise SystemExit(
                    'invalid LODES panel header in %s: expected %r; found %r' %
                    (path, list(PANEL_COLUMNS), reader.fieldnames)
                )
            for row in reader:
                geo = (row.get('geo') or '').strip()
                if geo not in latest_by_state:
                    continue
                year = _parse_nonnegative_int(
                    row.get('year'),
                    '%s row %d year' % (path, reader.line_num),
                )
                if year != latest_by_state[geo]:
                    continue
                if geo in retained:
                    raise SystemExit(
                        '%s row %d duplicates latest state/year %s/%d' %
                        (path, reader.line_num, geo, year)
                    )
                counts = {}
                for column in COUNT_COLUMNS:
                    counts[column] = _parse_nonnegative_int(
                        row.get(column),
                        '%s row %d %s' %
                        (path, reader.line_num, column),
                    )
                retained[geo] = counts
    missing = [state for state in STATE_GEOS if state not in retained]
    if missing:
        raise SystemExit(
            'LODES panel %s is missing latest row(s) for %s' %
            (path, ', '.join(missing))
        )
    return retained


def _load_weights(path, expected_districts):
    if not os.path.exists(path):
        raise SystemExit('county/CD weights do not exist: %s' % path)
    expected_set = set(expected_districts)
    by_county = {}
    with gzip.open(path, 'rb') as compressed:
        with io.TextIOWrapper(
            compressed,
            encoding='utf-8-sig',
            errors='strict',
            newline=''
        ) as source:
            reader = csv.DictReader(source)
            expected_header = [
                'county_fips', 'cd', 'weight', 'employment'
            ]
            if reader.fieldnames != expected_header:
                raise SystemExit(
                    'invalid weight-cache header in %s: expected %r; '
                    'found %r' %
                    (path, expected_header, reader.fieldnames)
                )
            seen = set()
            for row in reader:
                county = (row.get('county_fips') or '').strip()
                district = (row.get('cd') or '').strip()
                if (
                    re.fullmatch(r'[0-9]{5}', county) is None or
                    county[:2] not in VOTING_FIPS
                ):
                    raise SystemExit(
                        '%s row %d has invalid voting-state county FIPS %r' %
                        (path, reader.line_num, county)
                    )
                if district not in expected_set:
                    raise SystemExit(
                        '%s row %d has invalid district %r' %
                        (path, reader.line_num, district)
                    )
                pair = (county, district)
                if pair in seen:
                    raise SystemExit(
                        '%s row %d duplicates county/district %s/%s' %
                        (path, reader.line_num, county, district)
                    )
                seen.add(pair)
                weight_text = (row.get('weight') or '').strip()
                try:
                    weight = float(weight_text)
                except ValueError as exc:
                    raise SystemExit(
                        '%s row %d has invalid weight %r' %
                        (path, reader.line_num, weight_text)
                    ) from exc
                if not math.isfinite(weight) or weight <= 0 or weight > 1:
                    raise SystemExit(
                        '%s row %d has out-of-range weight %r' %
                        (path, reader.line_num, weight)
                    )
                employment = _parse_nonnegative_int(
                    row.get('employment'),
                    '%s row %d employment' % (path, reader.line_num),
                )
                by_county.setdefault(county, []).append({
                    'district': district,
                    'weight': weight,
                    'employment': employment,
                })
    if not by_county:
        raise SystemExit('county/CD weights has no data rows: %s' % path)
    for county, rows in by_county.items():
        rows.sort(key=lambda row: row['district'])
        weight_sum = sum(row['weight'] for row in rows)
        if weight_sum > 1.0 + 1e-4:
            raise SystemExit(
                'county %s weights sum above 1.0: %.12f' %
                (county, weight_sum)
            )
    return by_county


def _parse_qcew(path, year):
    label = 'QCEW %d annual singlefile' % year
    selected = {}
    value_counts = _new_external_value_counts()
    required_names = (
        'area_fips', 'own_code', 'industry_code', 'agglvl_code',
        'year', 'annual_avg_emplvl',
    )
    with zipfile.ZipFile(path, 'r') as archive:
        member = _single_csv_member(archive, label)
        print('streaming %s member: %s' % (label, member))
        with io.TextIOWrapper(
            archive.open(member, 'r'),
            encoding='utf-8-sig',
            errors='strict',
            newline=''
        ) as source:
            reader = csv.DictReader(source)
            fieldnames = reader.fieldnames or []
            headers = _header_map(fieldnames, label)
            resolved = {
                name: _required_header(
                    headers, name, label, fieldnames
                )
                for name in required_names
            }
            disclosure_matches = [
                header for normalized, header in headers.items()
                if (
                    re.sub(r'[^a-z0-9]+', '_', normalized).strip('_') ==
                    'disclosure_code'
                )
            ]
            if len(disclosure_matches) != 1:
                raise SystemExit(
                    '%s has %d disclosure_code-like columns; headers=%r' %
                    (label, len(disclosure_matches), fieldnames)
                )
            disclosure_header = disclosure_matches[0]
            for row in reader:
                if None in row:
                    raise SystemExit(
                        '%s row %d has surplus field(s): %r' %
                        (label, reader.line_num, row[None])
                    )
                area = _row_cell(
                    row, resolved['area_fips'], label, reader.line_num
                ).strip()
                own = _row_cell(
                    row, resolved['own_code'], label, reader.line_num
                ).strip()
                industry = _row_cell(
                    row, resolved['industry_code'], label, reader.line_num
                ).strip()
                if own not in QCEW_OWN_CODES:
                    continue
                is_state = (
                    re.fullmatch(r'[0-9]{5}', area) is not None and
                    area[:2] in STATE_FIPS and
                    area[2:] == '000' and
                    (industry == '10' or industry in QCEW_SECTORS)
                )
                is_county = (
                    re.fullmatch(r'[0-9]{5}', area) is not None and
                    area[:2] in VOTING_FIPS and
                    area[2:] != '000' and
                    industry == '10'
                )
                if not is_state and not is_county:
                    continue
                row_year = _parse_nonnegative_int(
                    _row_cell(
                        row, resolved['year'], label, reader.line_num
                    ),
                    '%s row %d year' % (label, reader.line_num),
                )
                if row_year != year:
                    raise SystemExit(
                        '%s row %d retained key %s/%s has year %d' %
                        (label, reader.line_num, area, industry, row_year)
                    )
                agglvl = _parse_nonnegative_int(
                    _row_cell(
                        row, resolved['agglvl_code'], label, reader.line_num
                    ),
                    '%s row %d agglvl_code' %
                    (label, reader.line_num),
                )
                disclosure = _row_cell(
                    row,
                    disclosure_header,
                    label,
                    reader.line_num,
                ).strip()
                employment = _classify_external_numeric(
                    row.get(resolved['annual_avg_emplvl']),
                    '%s row %d annual_avg_emplvl' %
                    (label, reader.line_num),
                    value_counts,
                    disclosed=bool(disclosure),
                )
                key = (area, industry, own)
                prior = selected.get(key)
                if prior is None or agglvl < prior['agglvl']:
                    selected[key] = {
                        'agglvl': agglvl,
                        'employment': employment,
                        'disclosed': bool(disclosure),
                        'line_num': reader.line_num,
                        'duplicate_line_num': None,
                    }
                elif agglvl == prior['agglvl']:
                    if prior['duplicate_line_num'] is None:
                        prior['duplicate_line_num'] = reader.line_num

    by_cell = defaultdict(dict)
    audit_counts = {
        'state_total': defaultdict(int),
        'state_sector': defaultdict(int),
        'county_total': defaultdict(int),
    }
    for (area, industry, own), detail in selected.items():
        if detail['duplicate_line_num'] is not None:
            raise SystemExit(
                '%s duplicate area/industry/own %s/%s/%s at chosen '
                'agglvl %d on rows %d and %d' %
                (
                    label,
                    area,
                    industry,
                    own,
                    detail['agglvl'],
                    detail['line_num'],
                    detail['duplicate_line_num'],
                )
            )
        by_cell[(area, industry)][own] = detail
        if area[2:] == '000':
            scope = 'state_total' if industry == '10' else 'state_sector'
        else:
            scope = 'county_total'
        audit_counts[scope][str(detail['agglvl'])] += 1

    totals = {}
    ownership_counts = {'own0': 0, 'summed': 0}
    suppressed_cells = 0
    for key, by_own in by_cell.items():
        if '0' in by_own:
            detail = by_own['0']
            value = detail['employment']
            suppressed = detail['disclosed']
            ownership = 'own0'
        else:
            components = [
                by_own[own] for own in ('1', '2', '3', '5')
                if own in by_own
            ]
            suppressed = any(
                detail['disclosed'] for detail in components
            )
            if any(
                detail['employment'] is UNKNOWN
                for detail in components
            ):
                value = UNKNOWN
            else:
                value = sum(
                    detail['employment'] for detail in components
                )
            ownership = 'summed'
        ownership_counts[ownership] += 1
        if suppressed:
            suppressed_cells += 1
        totals[key] = {
            'value': value,
            'ownership': ownership,
            'suppressed': suppressed,
        }

    retained_state_cells = sum(
        area[2:] == '000' for area, _industry in totals
    )
    retained_county_total_cells = sum(
        area[2:] != '000' and industry == '10'
        for area, industry in totals
    )
    audit = {
        'retained_area_industry_ownership_keys': len(selected),
        'retained_state_cells': retained_state_cells,
        'retained_county_total_cells': retained_county_total_cells,
        'ownership_method_counts': ownership_counts,
        'suppressed_cells': suppressed_cells,
        'external_value_classification': value_counts,
        'chosen_agglvl_code_counts': {
            scope: {
                code: counts[code] for code in sorted(counts, key=int)
            }
            for scope, counts in audit_counts.items()
        },
    }
    print(
        'parsed %s: %d retained ownership keys -> %d cells '
        '(own-0/summed %d/%d, suppressed %d)' %
        (
            label,
            len(selected),
            len(totals),
            ownership_counts['own0'],
            ownership_counts['summed'],
            suppressed_cells,
        )
    )
    return totals, audit


def _validate_qcew_coverage(
    qcew_audit, required_years, county_years
):
    for year in required_years:
        audit = qcew_audit.get(str(year))
        if not isinstance(audit, dict):
            raise SystemExit(
                'QCEW coverage audit is missing required year %d' % year
            )
        if audit.get('retained_state_cells', 0) <= 0:
            raise SystemExit(
                'QCEW required year %d retained zero state rows' % year
            )
        if (
            year in county_years and
            audit.get('retained_county_total_cells', 0) <= 0
        ):
            raise SystemExit(
                'QCEW required county vintage %d retained zero '
                'county totals' % year
            )


def _bea_all_areas_member(archive):
    members = [
        member for member in archive.namelist()
        if not member.endswith('/')
    ]
    matches = [
        member for member in members
        if 'all_areas' in member.lower() and
        member.lower().endswith('.csv')
    ]
    if len(matches) != 1:
        listing = ', '.join(archive.namelist()) or '<empty archive>'
        raise SystemExit(
            'BEA CAEMP25N archive has %d ALL_AREAS CSV matches; '
            'members: %s' % (len(matches), listing)
        )
    return matches[0]


def _parse_bea(path, required_years):
    label = 'BEA CAEMP25N ALL_AREAS'
    values = {}
    unavailable = set()
    value_counts = _new_external_value_counts()
    footer_rows_skipped = 0
    with zipfile.ZipFile(path, 'r') as archive:
        member = _bea_all_areas_member(archive)
        print('streaming %s member: %s' % (label, member))
        with io.TextIOWrapper(
            archive.open(member, 'r'),
            encoding='latin-1',
            errors='strict',
            newline=''
        ) as source:
            reader = csv.DictReader(source)
            fieldnames = reader.fieldnames or []
            headers = _header_map(fieldnames, label)
            geo_header = _required_header(
                headers, 'geofips', label, fieldnames
            )
            line_header = _required_header(
                headers, 'linecode', label, fieldnames
            )
            year_headers = {
                year: _required_header(
                    headers, str(year), label, fieldnames
                )
                for year in required_years
            }
            for row in reader:
                geo_value = row.get(geo_header)
                county = (
                    '' if geo_value is None else geo_value
                ).strip().strip('"').strip()
                if re.fullmatch(r'[0-9]{5}', county) is None:
                    footer_rows_skipped += 1
                    continue
                if None in row:
                    raise SystemExit(
                        '%s row %d has surplus field(s): %r' %
                        (label, reader.line_num, row[None])
                    )
                line_text = _row_cell(
                    row, line_header, label, reader.line_num
                ).strip()
                try:
                    line_code = int(line_text)
                except ValueError as exc:
                    raise SystemExit(
                        '%s row %d has invalid LineCode %r' %
                        (label, reader.line_num, line_text)
                    ) from exc
                if line_code != 10:
                    continue
                if (
                    county[:2] not in VOTING_FIPS or
                    county[2:] == '000'
                ):
                    continue
                for year, header in year_headers.items():
                    key = (county, year)
                    if key in values or key in unavailable:
                        raise SystemExit(
                            '%s row %d duplicates county/year %s/%d' %
                            (label, reader.line_num, county, year)
                        )
                    value = _classify_external_numeric(
                        row.get(header),
                        '%s row %d year %d' %
                        (label, reader.line_num, year),
                        value_counts,
                        allow_commas=True,
                    )
                    if value is UNKNOWN:
                        unavailable.add(key)
                        continue
                    values[key] = value
    if not values:
        raise SystemExit('%s has no retained county rows' % label)
    empty_years = [
        year for year in required_years
        if not any(key_year == year for _county, key_year in values)
    ]
    if empty_years:
        raise SystemExit(
            'BEA CAEMP25N has no available county values for required '
            'year(s): %s' %
            ', '.join(str(year) for year in empty_years)
        )
    print(
        'parsed %s: %d county/year values, %d unavailable, '
        '%d footer rows skipped' %
        (label, len(values), len(unavailable), footer_rows_skipped)
    )
    return values, unavailable, footer_rows_skipped, value_counts


def _suppression_header(headers, label, fieldnames):
    if 'empflag' in headers:
        return headers['empflag']
    if 'emp_nf' in headers:
        return headers['emp_nf']
    raise SystemExit(
        '%s is missing suppression column empflag or emp_nf; headers=%r' %
        (label, fieldnames)
    )


def _parse_cbp_county_totals(path):
    if not os.path.exists(path):
        raise SystemExit('CBP county archive does not exist: %s' % path)
    _validate_zip(path, 'CBP county')
    label = 'CBP county totals'
    totals = {}
    value_counts = _new_external_value_counts()
    with zipfile.ZipFile(path, 'r') as archive:
        member = _single_cbp_member(archive, label)
        print('streaming %s member: %s' % (label, member))
        with io.TextIOWrapper(
            archive.open(member, 'r'),
            encoding='utf-8-sig',
            errors='strict',
            newline=''
        ) as source:
            reader = csv.DictReader(source)
            fieldnames = reader.fieldnames or []
            headers = _header_map(fieldnames, label)
            naics_header = _required_header(
                headers, 'naics', label, fieldnames
            )
            state_header = _required_header(
                headers, 'fipstate', label, fieldnames
            )
            county_header = _required_header(
                headers, 'fipscty', label, fieldnames
            )
            est_header = _required_header(
                headers, 'est', label, fieldnames
            )
            emp_header = _required_header(
                headers, 'emp', label, fieldnames
            )
            suppression_header = _suppression_header(
                headers, label, fieldnames
            )
            lfo_header = headers.get('lfo')
            for row in reader:
                if None in row:
                    raise SystemExit(
                        '%s row %d has surplus field(s): %r' %
                        (label, reader.line_num, row[None])
                    )
                if lfo_header is not None:
                    lfo = _row_cell(
                        row, lfo_header, label, reader.line_num
                    ).strip()
                    if lfo != '-':
                        continue
                naics = _row_cell(
                    row, naics_header, label, reader.line_num
                ).strip()
                if naics != TOTAL_NAICS:
                    continue
                state_fips = _normalize_fips(
                    _row_cell(
                        row, state_header, label, reader.line_num
                    ),
                    2,
                    '%s row %d fipstate' %
                    (label, reader.line_num),
                )
                county_part = _normalize_fips(
                    _row_cell(
                        row, county_header, label, reader.line_num
                    ),
                    3,
                    '%s row %d fipscty' %
                    (label, reader.line_num),
                )
                if state_fips not in VOTING_FIPS:
                    continue
                county = state_fips + county_part
                if county in totals:
                    raise SystemExit(
                        '%s row %d duplicates county total %s' %
                        (label, reader.line_num, county)
                    )
                suppression = _row_cell(
                    row, suppression_header, label, reader.line_num
                ).strip().upper()
                est = _parse_count_cell(
                    row,
                    est_header,
                    label,
                    reader.line_num,
                    value_counts,
                )
                if est is UNKNOWN:
                    raise SystemExit(
                        '%s row %d county %s has UNKNOWN total EST' %
                        (label, reader.line_num, county)
                    )
                emp_value = _parse_count_cell(
                    row,
                    emp_header,
                    label,
                    reader.line_num,
                    value_counts,
                    disclosed=suppression in ('D', 'S'),
                )
                emp_suppressed = emp_value is UNKNOWN
                totals[county] = {
                    'est': est,
                    'emp_suppressed': emp_suppressed,
                }
    if not totals:
        raise SystemExit('%s has no voting-state total rows' % label)
    print('parsed %s: %d voting-state counties' % (label, len(totals)))
    return totals, value_counts


def _median(values):
    if not values:
        raise SystemExit('cannot compute median of an empty sequence')
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _nearest_rank(values, probability):
    if not values:
        raise SystemExit('cannot compute percentile of an empty sequence')
    ordered = sorted(values)
    index = int(math.ceil(probability * len(ordered))) - 1
    return ordered[index]


def _round_metric(value):
    if value is None:
        return None
    return round(value, 6)


def _headline_metric(value):
    if value is None:
        return 'null'
    return '%.6f' % value


def _sort_worst(rows, key_field, gap_field='gap', limit=20):
    return sorted(
        rows,
        key=lambda row: (-abs(row[gap_field]), row[key_field]),
    )[:limit]


def _public_rows(rows):
    result = []
    for row in rows:
        public = dict(row)
        for field in ('gap', 'ratio'):
            if field in public:
                public[field] = _round_metric(public[field])
        result.append(public)
    return result


def _public_worst(rows, key_field, gap_field='gap', limit=20):
    return _public_rows(
        _sort_worst(rows, key_field, gap_field, limit)
    )


def _qcew_value(qcew_by_year, year, area, industry, label):
    cell = qcew_by_year.get(year, {}).get((area, industry))
    if cell is None:
        raise SystemExit(
            'QCEW %d is missing %s %s/%s' %
            (year, label, area, industry)
        )
    value = cell['value']
    if value is UNKNOWN:
        raise SystemExit(
            'QCEW %d %s %s/%s is unavailable/suppressed' %
            (year, label, area, industry)
        )
    if value <= 0:
        raise SystemExit(
            'QCEW %d %s %s/%s has non-positive employment %d' %
            (year, label, area, industry, value)
        )
    return cell


def _build_state_bars(
    qcew_by_year, latest_by_state, lodes_states,
    sector_to_naics, qcew_gap
):
    total_rows = []
    sector_rows = []
    n_qcew_missing_sector_cells = 0
    n_qcew_unknown_sector_cells = 0
    n_qcew_suppressed_sector_cells = 0
    total_ownership = {'own0': 0, 'summed': 0}
    sector_ownership = {'own0': 0, 'summed': 0}
    for state in STATE_GEOS:
        year = latest_by_state[state]
        area = USPS_TO_FIPS[state] + '000'
        lodes = lodes_states[state]
        qcew_total_cell = _qcew_value(
            qcew_by_year, year, area, '10', 'state total'
        )
        total_ownership[qcew_total_cell['ownership']] += 1
        qcew_total = qcew_total_cell['value']
        gap = (qcew_total - lodes['C000']) / qcew_total
        verdict = 'pass' if abs(gap) <= qcew_gap else 'fail'
        total_rows.append({
            'state': state,
            'year': year,
            'lodes': lodes['C000'],
            'qcew': qcew_total,
            'gap': gap,
            'verdict': verdict,
        })
        for sector in SECTOR_COLUMNS:
            naics = sector_to_naics[sector]
            qcew_sector_cell = qcew_by_year.get(
                year, {}
            ).get((area, naics))
            if qcew_sector_cell is None:
                n_qcew_missing_sector_cells += 1
                # A cell QCEW does not publish is a GAP, not an employment of
                # zero.  Emitting 0 here made a missing cell indistinguishable
                # from a genuine zero in the published rows.
                qcew_sector = None
            else:
                sector_ownership[
                    qcew_sector_cell['ownership']
                ] += 1
                if qcew_sector_cell['value'] is UNKNOWN:
                    n_qcew_unknown_sector_cells += 1
                    if qcew_sector_cell['suppressed']:
                        n_qcew_suppressed_sector_cells += 1
                    qcew_sector = None
                else:
                    qcew_sector = qcew_sector_cell['value']
            if qcew_sector is None or qcew_sector == 0:
                sector_gap = None
            else:
                sector_gap = (
                    qcew_sector - lodes[sector]
                ) / qcew_sector
            sector_rows.append({
                'key': '%s/%s' % (state, sector),
                'state': state,
                'year': year,
                'sector': sector,
                'naics': naics,
                'lodes': lodes[sector],
                'qcew': qcew_sector,
                'gap': sector_gap,
            })

    n_fail = sum(row['verdict'] == 'fail' for row in total_rows)
    state_totals = {
        'params': {
            'gap_formula': '(qcew-lodes)/qcew',
            'gap_convention': '(external-ours)/external',
            'absolute_gap_bar': qcew_gap,
        },
        'n': len(total_rows),
        'n_qcew_cells_own0': total_ownership['own0'],
        'n_qcew_cells_summed': total_ownership['summed'],
        'n_qcew_suppressed_cells': 0,
        'n_pass': len(total_rows) - n_fail,
        'n_fail': n_fail,
        'fail_states': [
            row['state'] for row in total_rows
            if row['verdict'] == 'fail'
        ],
        'rows': _public_rows(total_rows),
        'worst': _public_worst(total_rows, 'state'),
    }

    sector_summary = []
    for sector in SECTOR_COLUMNS:
        rows = [row for row in sector_rows if row['sector'] == sector]
        comparable = [
            row for row in rows if row['gap'] is not None
        ]
        detail = {
            'sector': sector,
            'naics': sector_to_naics[sector],
            'n': len(rows),
            'n_comparable': len(comparable),
            'median_gap': (
                None if not comparable else _round_metric(
                    _median([row['gap'] for row in comparable])
                )
            ),
        }
        if sector == 'CNS01':
            detail['universe_caveat'] = (
                'QCEW NAICS 11 and LODES CNS01 have different '
                'agricultural coverage universes'
            )
        sector_summary.append(detail)
    state_sectors = {
        'params': {
            'gap_formula': '(qcew-lodes)/qcew',
            'gap_convention': '(external-ours)/external',
            'verdict': 'report_only',
            'mapping': sector_to_naics,
            'cns01_universe_caveat': (
                'QCEW NAICS 11 and LODES CNS01 have different '
                'agricultural coverage universes'
            ),
        },
        'n': len(sector_rows),
        'n_qcew_cells_own0': sector_ownership['own0'],
        'n_qcew_cells_summed': sector_ownership['summed'],
        'n_qcew_unknown_cells': n_qcew_unknown_sector_cells,
        'n_qcew_suppressed_cells':
            n_qcew_suppressed_sector_cells,
        'n_qcew_zero_sector_cells': sum(
            row['qcew'] == 0 for row in sector_rows
        ),
        'n_qcew_missing_sector_cells':
            n_qcew_missing_sector_cells,
        'median_gap_by_sector': sector_summary,
        'worst': _public_worst(
            [
                row for row in sector_rows
                if row['gap'] is not None
            ],
            'key',
        ),
    }
    return state_totals, state_sectors


def _build_whole_county_bar(
    qcew_by_year, latest_by_state, weights
):
    rows = []
    county_absent_source = []
    qcew_zero_county_totals = []
    qcew_unknown_cells = []
    qcew_suppressed_cells = []
    ownership_counts = {'own0': 0, 'summed': 0}
    for county in sorted(weights):
        county_rows = weights[county]
        if (
            len(county_rows) != 1 or
            county_rows[0]['weight'] < DIRECT_WEIGHT_MIN
        ):
            continue
        state = FIPS_TO_USPS[county[:2]]
        year = latest_by_state[state]
        qcew_cell = qcew_by_year.get(
            year, {}
        ).get((county, '10'))
        if qcew_cell is None:
            county_absent_source.append(county)
            continue
        ownership_counts[qcew_cell['ownership']] += 1
        if qcew_cell['value'] is UNKNOWN:
            qcew_unknown_cells.append(county)
            if qcew_cell['suppressed']:
                qcew_suppressed_cells.append(county)
            continue
        qcew = qcew_cell['value']
        if qcew == 0:
            qcew_zero_county_totals.append(county)
            continue
        if qcew < 0:
            raise SystemExit(
                'QCEW %d county total %s/10 has non-positive '
                'employment %d' % (year, county, qcew)
            )
        lodes = sum(row['employment'] for row in county_rows)
        gap = (qcew - lodes) / qcew
        rows.append({
            'key': county,
            'county_fips': county,
            'district': county_rows[0]['district'],
            'year': year,
            'lodes': lodes,
            'qcew': qcew,
            'gap': gap,
        })
    county_absent_source = sorted(county_absent_source)
    print(
        '!! whole-county QCEW counties absent from comparison '
        'source: %d (%s)' %
        (
            len(county_absent_source),
            ','.join(county_absent_source)
            if county_absent_source else 'none',
        )
    )
    qcew_zero_county_totals = sorted(qcew_zero_county_totals)
    qcew_unknown_cells = sorted(qcew_unknown_cells)
    qcew_suppressed_cells = sorted(qcew_suppressed_cells)
    print(
        '!! whole-county QCEW zero county totals skipped: %d (%s)' %
        (
            len(qcew_zero_county_totals),
            ','.join(qcew_zero_county_totals)
            if qcew_zero_county_totals else 'none',
        )
    )
    gaps = [row['gap'] for row in rows]
    return {
        'params': {
            'direct_county_rule': (
                'exactly one district weight row with weight >= 0.9999'
            ),
            'gap_formula': '(qcew-lodes)/qcew',
            'gap_convention': '(external-ours)/external',
            'p90_method': 'nearest_rank',
            'verdict': 'report_only',
        },
        'n': len(rows),
        'n_qcew_cells_own0': ownership_counts['own0'],
        'n_qcew_cells_summed': ownership_counts['summed'],
        'n_qcew_unknown_cells': len(qcew_unknown_cells),
        'qcew_unknown_cells': qcew_unknown_cells,
        'n_qcew_suppressed_cells': len(qcew_suppressed_cells),
        'qcew_suppressed_cells': qcew_suppressed_cells,
        'n_county_absent_source': len(county_absent_source),
        'county_absent_source': county_absent_source,
        'n_qcew_zero_county_totals':
            len(qcew_zero_county_totals),
        'qcew_zero_county_totals': qcew_zero_county_totals,
        'median_gap': (
            None if not gaps else _round_metric(_median(gaps))
        ),
        'p90_abs_gap': (
            None if not gaps else _round_metric(
                _nearest_rank([abs(value) for value in gaps], 0.9)
            )
        ),
        'worst': _public_worst(rows, 'key'),
    }


def _build_bea_bar(
    bea_values, bea_unavailable, footer_rows_skipped,
    value_counts, latest_by_state, weights
):
    rows = []
    county_absent_source = []
    n_bea_unavailable = 0
    n_bea_zero_values = 0
    for county in sorted(weights):
        state = FIPS_TO_USPS[county[:2]]
        lodes_year = latest_by_state[state]
        bea_year = min(2022, lodes_year)
        key = (county, bea_year)
        if key in bea_unavailable:
            n_bea_unavailable += 1
            continue
        bea = bea_values.get(key)
        if bea is None:
            county_absent_source.append(county)
            continue
        if bea == 0:
            n_bea_zero_values += 1
            continue
        lodes = sum(row['employment'] for row in weights[county])
        if lodes <= 0:
            raise SystemExit(
                'weights county %s has non-positive summed employment %d' %
                (county, lodes)
            )
        ratio = bea / lodes
        gap = (bea - lodes) / bea
        rows.append({
            'key': county,
            'county_fips': county,
            'bea_year': bea_year,
            'lodes_year': lodes_year,
            'lodes': lodes,
            'bea': bea,
            'gap': gap,
            'ratio': ratio,
        })
    county_absent_source = sorted(county_absent_source)
    print(
        '!! BEA counties absent from comparison source: %d (%s)' %
        (
            len(county_absent_source),
            ','.join(county_absent_source)
            if county_absent_source else 'none',
        )
    )
    lower = [row for row in rows if row['bea'] < row['lodes']]
    n_bea_higher = sum(
        row['bea'] > row['lodes'] for row in rows
    )
    return {
        'params': {
            'line_code': 10,
            'gap_formula': '(bea-lodes)/bea',
            'gap_convention': '(external-ours)/external',
            'ratio_formula': 'bea/lodes',
            'verdict': 'report_only',
        },
        'n': len(rows),
        'n_county_absent_source': len(county_absent_source),
        'county_absent_source': county_absent_source,
        'n_bea_higher': n_bea_higher,
        'n_bea_lower': len(lower),
        'share_bea_higher': (
            None if not rows else _round_metric(
                n_bea_higher / len(rows)
            )
        ),
        'median_ratio': (
            None if not rows else _round_metric(
                _median([row['ratio'] for row in rows])
            )
        ),
        'footer_rows_skipped': footer_rows_skipped,
        'n_bea_unavailable': n_bea_unavailable,
        'n_bea_zero_values': n_bea_zero_values,
        'external_value_classification': value_counts,
        'vintage_note': (
            'BEA county employment lags; comparisons may span a '
            '1-year vintage gap — directional check only'
        ),
        'worst': _public_worst(rows, 'key'),
    }


def _allocate_cbp_totals(county_totals, weights, expected_districts):
    missing_counties = sorted(set(weights) - set(county_totals))
    print(
        '!! weighted counties absent from CBP county totals '
        '(treated as zero establishments): %d (%s)' %
        (
            len(missing_counties),
            ','.join(missing_counties) if missing_counties else 'none',
        )
    )
    district_est = {district: 0.0 for district in expected_districts}
    national_est = 0.0
    split_allocated_est = 0.0
    split_counties = []
    for county, detail in county_totals.items():
        establishments = detail['est']
        national_est += establishments
        county_rows = weights.get(county, ())
        weight_sum = sum(row['weight'] for row in county_rows)
        if len(county_rows) >= 2:
            split_counties.append(county)
            split_allocated_est += establishments * weight_sum
        for row in county_rows:
            district_est[row['district']] += (
                establishments * row['weight']
            )
    if national_est <= 0:
        raise SystemExit('CBP voting-state national establishment total is zero')
    empty_districts = sorted(
        district for district, value in district_est.items()
        if value <= 0
    )
    if empty_districts:
        raise SystemExit(
            'allocated CBP totals are non-positive for district(s): %s' %
            ', '.join(empty_districts)
        )
    return {
        'district_est': district_est,
        'n_split_counties': len(split_counties),
        'split_counties': split_counties,
        'weighted_counties_absent_cbp': missing_counties,
        'national_est': national_est,
        'split_allocated_est': split_allocated_est,
        'split_allocated_share': split_allocated_est / national_est,
    }


def _build_allocation_bar(
    phase_d_weights, allocation, weights, value_counts
):
    return {
        'params': {
            'split_county_rule': (
                'county appears in two or more district-weight rows'
            ),
            'split_est_share_denominator': (
                'all published voting-state CBP county-total '
                'establishments'
            ),
            'gap_convention': '(external-ours)/external',
            'verdict': 'report_only',
        },
        'n': len(weights),
        'unallocated_est_share':
            phase_d_weights['unallocated_est_share'],
        'unallocated_emp_share':
            phase_d_weights['unallocated_emp_share'],
        'n_split_counties': allocation['n_split_counties'],
        'weighted_counties_absent_cbp':
            allocation['weighted_counties_absent_cbp'],
        'n_weighted_counties_absent_cbp':
            len(allocation['weighted_counties_absent_cbp']),
        'share_national_est_allocated_through_split_counties':
            _round_metric(allocation['split_allocated_share']),
        'declared_largest_error_term': True,
        'external_value_classification': value_counts,
        'worst': [],
    }


def _normalize_excel_header(value):
    if value is None:
        return ''
    return re.sub(r'\s+', ' ', str(value).strip()).lower()


def _discover_excel_columns(values):
    normalized = [_normalize_excel_header(value) for value in values]

    def single_index(predicate):
        matches = [
            index for index, label in enumerate(normalized)
            if predicate(label)
        ]
        return matches[0] if len(matches) == 1 else None

    district_index = single_index(
        lambda label: 'congressional district' in label
    )
    naics_index = single_index(
        lambda label: 'naics' in label and 'code' in label
    )
    est_index = single_index(
        lambda label: 'establishment' in label and
        'number' in label
    )
    state_fips_index = single_index(
        lambda label: 'state' in label and 'fips' in label
    )
    state_index = single_index(
        lambda label: label in (
            'state', 'state code', 'state abbreviation',
            'state postal code',
        )
    )
    if (
        district_index is None or
        naics_index is None or
        est_index is None or
        (state_fips_index is None and state_index is None)
    ):
        return None
    return {
        'district': district_index,
        'naics': naics_index,
        'est': est_index,
        'state_fips': state_fips_index,
        'state': state_index,
        'headers': [
            None if value is None else str(value) for value in values
        ],
    }


def _excel_state(value, is_fips, label):
    text = '' if value is None else str(value).strip()
    if text.endswith('.0') and text[:-2].isdigit():
        text = text[:-2]
    if is_fips:
        fips = _normalize_fips(text, 2, label)
        state = FIPS_TO_USPS.get(fips)
        if state not in VOTING_STATES:
            raise SystemExit('%s has non-voting state FIPS %r' % (label, fips))
        return state
    upper = text.upper()
    if upper not in VOTING_STATES:
        raise SystemExit(
            '%s has unsupported state code %r' % (label, text)
        )
    return upper


def _excel_district(value, state, label):
    text = (
        '' if value is None else str(value).strip().upper()
    )
    if text.endswith('.0') and text[:-2].isdigit():
        text = text[:-2]
    combined = re.fullmatch(r'([A-Z]{2})[- ]?([0-9]{1,2})', text)
    if combined is not None:
        if combined.group(1) != state:
            raise SystemExit(
                '%s district state %s conflicts with row state %s' %
                (label, combined.group(1), state)
            )
        district = combined.group(2).zfill(2)
    elif text.isdigit() and len(text) <= 2:
        district = text.zfill(2)
    else:
        raise SystemExit('%s has invalid district value %r' % (label, text))
    return '%s-%s' % (state, district)


def _excel_est(value, label, counters):
    return _classify_external_numeric(
        value, label, counters, allow_commas=True
    )


def _parse_census_cd_xlsx(path, expected_districts):
    if not os.path.exists(path):
        raise SystemExit('CBP CD workbook does not exist: %s' % path)
    try:
        import openpyxl
    except ImportError as exc:
        raise SystemExit(
            'openpyxl is required for the CBP CD workbook check'
        ) from exc
    try:
        workbook = openpyxl.load_workbook(
            path, read_only=True, data_only=True
        )
    except Exception as exc:
        raise SystemExit(
            'failed to open CBP CD workbook %s: %s' % (path, str(exc))
        ) from exc

    discovery = None
    congress_candidates = set()
    inspected_headers = []
    n_nonvoting_cd_rows_skipped = 0
    unavailable = set()
    value_counts = _new_external_value_counts()
    try:
        for worksheet in workbook.worksheets:
            congress_text = [worksheet.title]
            for row_number, values in enumerate(
                worksheet.iter_rows(
                    min_row=1, max_row=50, values_only=True
                ),
                1,
            ):
                congress_text.extend(
                    str(value) for value in values
                    if isinstance(value, str)
                )
                columns = _discover_excel_columns(values)
                if columns is not None and discovery is None:
                    discovery = {
                        'sheet': worksheet.title,
                        'header_row': row_number,
                        'columns': columns,
                    }
                elif any(value is not None for value in values):
                    inspected_headers.append({
                        'sheet': worksheet.title,
                        'row': row_number,
                        'values': [
                            None if value is None else str(value)
                            for value in values
                        ],
                    })
            for text in congress_text:
                for match in re.finditer(
                    r'\b([0-9]{3})(?:st|nd|rd|th)\s+'
                    r'(?:congress|congressional)\b',
                    text,
                    re.IGNORECASE,
                ):
                    congress_candidates.add(int(match.group(1)))

        if discovery is None:
            raise SystemExit(
                'CBP CD workbook has no discoverable district, state, '
                'NAICS, and establishment header row; inspected=%r' %
                inspected_headers[:10]
            )
        worksheet = workbook[discovery['sheet']]
        columns = discovery['columns']
        totals = {}
        for row_number, values in enumerate(
            worksheet.iter_rows(
                min_row=discovery['header_row'] + 1,
                values_only=True,
            ),
            discovery['header_row'] + 1,
        ):
            if not values or not any(value is not None for value in values):
                continue
            naics_value = (
                values[columns['naics']]
                if columns['naics'] < len(values) else None
            )
            if str(naics_value or '').strip() != TOTAL_NAICS:
                continue
            state_index = columns['state_fips']
            is_fips = state_index is not None
            if state_index is None:
                state_index = columns['state']
            if (
                state_index >= len(values) or
                columns['district'] >= len(values) or
                columns['est'] >= len(values)
            ):
                raise SystemExit(
                    'CBP CD workbook %s row %d is shorter than '
                    'discovered columns' %
                    (discovery['sheet'], row_number)
                )
            if is_fips:
                state_fips = _normalize_fips(
                    values[state_index],
                    2,
                    '%s row %d state FIPS' %
                    (discovery['sheet'], row_number),
                )
                if state_fips not in VOTING_FIPS:
                    n_nonvoting_cd_rows_skipped += 1
                    continue
            state = _excel_state(
                values[state_index],
                is_fips,
                '%s row %d state' %
                (discovery['sheet'], row_number),
            )
            district = _excel_district(
                values[columns['district']],
                state,
                '%s row %d district' %
                (discovery['sheet'], row_number),
            )
            if district not in expected_districts:
                raise SystemExit(
                    'CBP CD workbook %s row %d has unexpected '
                    'district %s' %
                    (discovery['sheet'], row_number, district)
                )
            if district in totals or district in unavailable:
                raise SystemExit(
                    'CBP CD workbook %s row %d duplicates total for %s' %
                    (discovery['sheet'], row_number, district)
                )
            establishments = _excel_est(
                values[columns['est']],
                '%s row %d EST' %
                (discovery['sheet'], row_number),
                value_counts,
            )
            if establishments is UNKNOWN:
                unavailable.add(district)
                continue
            totals[district] = establishments
    finally:
        workbook.close()

    if len(congress_candidates) == 1:
        congress = next(iter(congress_candidates))
    else:
        congress = None
    return {
        'totals': totals,
        'congress': congress,
        'congress_candidates': sorted(congress_candidates),
        'sheet': discovery['sheet'],
        'header_row': discovery['header_row'],
        'headers': discovery['columns']['headers'],
        'n_nonvoting_cd_rows_skipped':
            n_nonvoting_cd_rows_skipped,
        'unavailable_districts': sorted(unavailable),
        'external_value_classification': value_counts,
    }


def _build_census_bar(
    census, district_est, geography_states, expected_districts
):
    vintage_match = census['congress'] == 119
    if vintage_match:
        included_states = list(VOTING_STATES)
        excluded_states = []
        vintage_status = 'matched_119th'
    else:
        included_states = [
            state for state in VOTING_STATES
            if not geography_states[state]['redrawn_since_118']
        ]
        excluded_states = sorted(
            set(VOTING_STATES) - set(included_states)
        )
        vintage_status = 'vintage_mismatch'
    included_set = set(included_states)
    scoped_districts = [
        district for district in expected_districts
        if district[:2] in included_set
    ]
    missing = [
        district for district in scoped_districts
        if (
            district not in census['totals'] and
            district not in census['unavailable_districts']
        )
    ]
    if missing:
        raise SystemExit(
            'CBP CD workbook is missing scoped district total(s): %s' %
            ', '.join(missing)
        )

    rows = []
    zero_excluded = []
    unavailable_scoped = [
        district for district in scoped_districts
        if district in census['unavailable_districts']
    ]
    for district in scoped_districts:
        if district in census['unavailable_districts']:
            continue
        census_est = census['totals'][district]
        if census_est == 0:
            zero_excluded.append(district)
            continue
        ours = district_est[district]
        pct_diff = (census_est - ours) / census_est
        rows.append({
            'key': district,
            'district': district,
            'census': census_est,
            'ours': round(ours, 1),
            'gap': pct_diff,
        })
    abs_diffs = [abs(row['gap']) for row in rows]
    return {
        'params': {
            'comparison': 'district total establishments',
            'pct_diff_formula': '(census-ours)/census',
            'gap_convention': '(external-ours)/external',
            'p90_method': 'nearest_rank',
            'workbook_sheet': census['sheet'],
            'header_row': census['header_row'],
            'discovered_headers': census['headers'],
            'congress': census['congress'],
            'congress_candidates': census['congress_candidates'],
            'vintage_status': vintage_status,
            'included_states': included_states,
            'excluded_redrawn_states': excluded_states,
            'verdict': 'report_only',
        },
        'n': len(rows),
        'n_scoped_districts': len(scoped_districts),
        'n_nonvoting_cd_rows_skipped':
            census['n_nonvoting_cd_rows_skipped'],
        'n_census_unavailable': len(unavailable_scoped),
        'census_unavailable': unavailable_scoped,
        'external_value_classification':
            census['external_value_classification'],
        'n_census_zero_excluded': len(zero_excluded),
        'census_zero_excluded': zero_excluded,
        'median_abs_pct_diff': (
            None if not abs_diffs else
            _round_metric(_median(abs_diffs))
        ),
        'p90_abs_pct_diff': (
            None if not abs_diffs else _round_metric(
                _nearest_rank(abs_diffs, 0.9)
            )
        ),
        'worst': _public_worst(rows, 'key'),
    }


def main():
    data_root = os.environ.get('DEMO_DATA_ROOT')
    if not data_root:
        base = (
            os.environ.get('LOCALAPPDATA') or
            os.path.expanduser(r'~\AppData\Local')
        )
        data_root = os.path.join(base, 'Melange', 'demographics')
    qcew_gap = _parse_float_dial('QCEW_GAP', 0.10)
    force = os.environ.get('VAL_FORCE', '0') == '1'
    output_path = os.environ.get('VAL_OUT')
    if not output_path:
        output_path = os.path.join(
            MB_ROOT, 'data', 'lobbying', 'district_industry_validation.json'
        )

    lodes_path = os.path.join(
        data_root, 'lodes', 'lodes_cd_panel.csv.gz'
    )
    weights_path = os.path.join(
        data_root, 'geo', 'county_cd_weights.csv.gz'
    )
    cbp_county_path = os.path.join(
        data_root, 'cbp', 'cbp23co.zip'
    )
    cbp_cd_path = os.path.join(
        data_root, 'cbp', 'cbp23cd.xlsx'
    )
    geography_path = os.path.join(
        MB_ROOT, 'data', 'district', 'district_geography.json'
    )
    phase_c_path = os.path.join(
        MB_ROOT, 'data', 'lobbying', 'district_industry.json'
    )
    phase_d_path = os.path.join(
        MB_ROOT, 'data', 'lobbying', 'district_industry_4digit.json'
    )
    validate_root = os.path.join(data_root, 'validate')

    _require_input_files((
        ('LODES panel', lodes_path),
        ('county/CD weights', weights_path),
        ('CBP county archive', cbp_county_path),
        ('CBP CD workbook', cbp_cd_path),
        ('district geography', geography_path),
        ('Phase C artifact', phase_c_path),
        ('Phase D artifact', phase_d_path),
    ))
    print('loading district geography: %s' % geography_path)
    geography = _validate_geography(
        _load_json(geography_path, 'district geography'),
        geography_path,
    )
    print('loading Phase C artifact: %s' % phase_c_path)
    phase_c = _validate_phase_c(
        _load_json(phase_c_path, 'Phase C artifact'),
        phase_c_path,
        geography['districts'],
    )
    print('loading Phase D artifact: %s' % phase_d_path)
    phase_d_weights = _validate_phase_d(
        _load_json(phase_d_path, 'Phase D artifact'),
        phase_d_path,
        geography['districts'],
    )
    _validate_weight_vintages(
        phase_d_weights, phase_c['latest_by_state']
    )
    print(
        'observed per-geo latest QCEW years: %s' %
        ', '.join(str(year) for year in phase_c['latest_years'])
    )

    os.makedirs(validate_root, exist_ok=True)
    sources = {}
    qcew_paths = {}
    for year in phase_c['latest_years']:
        label = 'qcew_%d' % year
        url = QCEW_URL.format(year=year)
        path = os.path.join(
            validate_root, '%d_annual_singlefile.zip' % year
        )
        sources[label] = _download_source(
            'QCEW %d annual singlefile' % year,
            url,
            path,
            force,
        )
        qcew_paths[year] = path
    bea_path = os.path.join(validate_root, 'CAEMP25N.zip')
    sources['bea'] = _download_source(
        'BEA CAEMP25N', BEA_URL, bea_path, force
    )

    qcew_by_year = {}
    qcew_audit = {}
    for year in phase_c['latest_years']:
        totals, audit = _parse_qcew(qcew_paths[year], year)
        qcew_by_year[year] = totals
        qcew_audit[str(year)] = audit
    _validate_qcew_coverage(
        qcew_audit,
        phase_c['latest_years'],
        {
            phase_c['latest_by_state'][state]
            for state in VOTING_STATES
        },
    )

    print('loading latest LODES state rows: %s' % lodes_path)
    lodes_states = _load_lodes_states(
        lodes_path, phase_c['latest_by_state']
    )
    print('loading county/CD weights: %s' % weights_path)
    weights = _load_weights(weights_path, geography['districts'])
    (
        bea_values,
        bea_unavailable,
        bea_footer_rows_skipped,
        bea_value_counts,
    ) = _parse_bea(
        bea_path,
        tuple(sorted({
            min(2022, year)
            for year in phase_c['latest_by_state'].values()
        })),
    )
    cbp_totals, cbp_value_counts = _parse_cbp_county_totals(
        cbp_county_path
    )
    allocation = _allocate_cbp_totals(
        cbp_totals, weights, geography['districts']
    )
    census = _parse_census_cd_xlsx(
        cbp_cd_path, set(geography['districts'])
    )

    state_totals, state_sectors = _build_state_bars(
        qcew_by_year,
        phase_c['latest_by_state'],
        lodes_states,
        phase_c['sector_to_naics'],
        qcew_gap,
    )
    whole_county = _build_whole_county_bar(
        qcew_by_year, phase_c['latest_by_state'], weights
    )
    bea_bar = _build_bea_bar(
        bea_values,
        bea_unavailable,
        bea_footer_rows_skipped,
        bea_value_counts,
        phase_c['latest_by_state'],
        weights,
    )
    allocation_error = _build_allocation_bar(
        phase_d_weights, allocation, weights, cbp_value_counts
    )
    census_bar = _build_census_bar(
        census,
        allocation['district_est'],
        geography['states'],
        geography['districts'],
    )

    state_verdict = (
        'PASS' if state_totals['n_fail'] == 0 else 'FAIL'
    )
    artifact = {
        'schema': 'district-industry-validation-v1',
        'generated_at': _utc_iso(),
        'sources': sources,
        'qcew_audit': qcew_audit,
        'bars': {
            'state_totals': state_totals,
            'state_sectors': state_sectors,
            'whole_county': whole_county,
            'bea': bea_bar,
            'allocation_error': allocation_error,
            'census_cd': census_bar,
        },
        'verdicts': {
            'state_totals': state_verdict,
            'state_totals_n_fail': state_totals['n_fail'],
            'others': 'REPORT',
        },
    }
    print('writing validation artifact: %s' % output_path)
    dump_atomic(artifact, output_path, separators=(',', ':'))
    print(
        'wrote district_industry_validation.json: '
        'state-totals P/F=%d/%d (bar %.2f), '
        'whole-county n=%d median %s, '
        'BEA higher share %s, census-CD median diff %s' %
        (
            state_totals['n_pass'],
            state_totals['n_fail'],
            qcew_gap,
            whole_county['n'],
            _headline_metric(whole_county['median_gap']),
            _headline_metric(bea_bar['share_bea_higher']),
            _headline_metric(census_bar['median_abs_pct_diff']),
        )
    )


if __name__ == '__main__':
    main()

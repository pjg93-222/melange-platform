# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT  # centralized root (arch review 2026-07-02)
"""CBP FOUR-DIGIT COLLECTOR/BUILDER — district industry contradiction screen.

Collector-cadence Phase D of the Melange demographic expansion.  This standalone script is
deliberately not wired into run_pipeline.py.  It mirrors Census County Business Patterns
county, US, and congressional-district files; derives county-to-district weights from Phase
B's latest raw block-level LODES WAC files and Phase A's block crosswalk; and builds the
district-industry-4digit-v1 artifact.

The CBP congressional-district XLSX is cached best-effort for Phase E
(fec/district/validate_district_industry.py) and is not parsed here.

Universe caveats:
  CNS20: CBP has no NAICS 92 (public administration) — cbp_lq_est is always null
  CNS01: CBP NAICS 11 covers only support activities/logging, not farm production
  CNS03: government-owned utilities are outside the CBP universe

Inputs:
  DEMO_DATA_ROOT/geo/cd119_block_assign.csv.gz
  DEMO_DATA_ROOT/lodes/raw/{st}_wac_S000_JT01_{year}.csv.gz
  MB_ROOT/district_industry.json

Raw/cache outputs:
  DEMO_DATA_ROOT/cbp/cbp{yy}co.zip
  DEMO_DATA_ROOT/cbp/cbp{yy}us.zip
  DEMO_DATA_ROOT/cbp/cbp{yy}cd.xlsx
  DEMO_DATA_ROOT/geo/ct_2022_block_crosswalk.csv
  DEMO_DATA_ROOT/geo/county_cd_weights.csv.gz

Repo-plane output:
  MB_ROOT/district_industry_4digit.json, unless CBP_OUT overrides the path.

Dials:
  DEMO_DATA_ROOT — out-of-OneDrive raw-data root; defaults to
                   %LOCALAPPDATA%/Melange/demographics
  CBP_YEAR       — CBP vintage; defaults to "2023"
  LQ_HI          — high-LQ contradiction threshold; defaults to "1.25"
  LQ_LO          — low-LQ contradiction threshold; defaults to "0.9"
  CBP_FORCE      — set to "1" to re-download CBP sources; defaults to "0"
  CBP_OUT        — optional output-path override
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
import urllib.error
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

URL_TEMPLATES = {
    'co': (
        'https://www2.census.gov/programs-surveys/cbp/datasets/'
        '{year}/cbp{yy}co.zip'
    ),
    'us': (
        'https://www2.census.gov/programs-surveys/cbp/datasets/'
        '{year}/cbp{yy}us.zip'
    ),
    'cd_xlsx': (
        'https://www2.census.gov/programs-surveys/cbp/datasets/'
        '{year}/cbp{yy}cd.xlsx'
    ),
}
CT_BRIDGE_URL = (
    'https://raw.githubusercontent.com/CT-Data-Collaborative/'
    '2022-block-crosswalk/master/2022blockcrosswalk.csv'
)
CT_BRIDGE_FALLBACK_URL = CT_BRIDGE_URL.replace(
    '/master/', '/main/'
)

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
VOTING_FIPS = frozenset(USPS_TO_FIPS[state] for state in VOTING_STATES)
LODES_JURISDICTIONS = (
    'ak', 'al', 'ar', 'az', 'ca', 'co', 'ct', 'dc', 'de', 'fl',
    'ga', 'hi', 'ia', 'id', 'il', 'in', 'ks', 'ky', 'la', 'ma',
    'md', 'me', 'mi', 'mn', 'mo', 'ms', 'mt', 'nc', 'nd', 'ne',
    'nh', 'nj', 'nm', 'nv', 'ny', 'oh', 'ok', 'or', 'pa', 'ri',
    'sc', 'sd', 'tn', 'tx', 'ut', 'va', 'vt', 'wa', 'wi', 'wv',
    'wy',
)

RANGE_NAICS = frozenset(('31-33', '44-45', '48-49'))
RANGE_NAICS_ALIASES = {
    '31-33': '31',
    '44-45': '44',
    '48-49': '48',
}
CBP_CNS_ALIASES = {
    '31': 'CNS05',
    '44': 'CNS07',
    '48': 'CNS08',
}
SMALL_BUSINESS_NAICS = (
    ('23', '23'),
    ('44-45', '44'),
    ('72', '72'),
    ('81', '81'),
    ('54', '54'),
    ('62', '62'),
)
CU_NAICS = ('522130', '522110')
TOTAL_NAICS = '------'
UNKNOWN = object()
CT_REGION_CODES = frozenset(
    ('09110', '09120', '09130', '09140', '09150',
     '09160', '09170', '09180', '09190')
)
CNS_SECTORS = tuple('CNS%02d' % index for index in range(1, 21))
UNIVERSE_NOTES = {
    'CNS20': (
        'CBP has no NAICS 92 (public administration) — '
        'cbp_lq_est is always null'
    ),
    'CNS01': (
        'CBP NAICS 11 covers only support activities/logging, '
        'not farm production'
    ),
    'CNS03': 'government-owned utilities are outside the CBP universe',
}

NAICS_2_RE = re.compile(r'^[0-9]{2}----$')
NAICS_3_RE = re.compile(r'^[0-9]{3}///$')
NAICS_4_RE = re.compile(r'^[0-9]{4}//$')
NAICS_5_RE = re.compile(r'^[0-9]{5}/$')
NAICS_6_RE = re.compile(r'^[0-9]{6}$')
RAW_WAC_RE_TEMPLATE = (
    r'^{state}_wac_S000_JT01_([0-9]{{4}})\.csv\.gz$'
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


def _parse_year(value):
    text = (value or '').strip()
    if re.fullmatch(r'[0-9]{4}', text) is None:
        raise SystemExit(
            'invalid CBP_YEAR %r; expected a four-digit year' % value
        )
    return text


def _parse_float_dial(name, default):
    text = os.environ.get(name, str(default))
    try:
        value = float(text)
    except (TypeError, ValueError) as exc:
        raise SystemExit(
            'invalid %s %r; expected a finite number' % (name, text)
        ) from exc
    if not math.isfinite(value):
        raise SystemExit(
            'invalid %s %r; expected a finite number' % (name, text)
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


def _validate_ct_bridge_header(path, label):
    try:
        with open(
            path, 'r', encoding='utf-8-sig', errors='strict', newline=''
        ) as source:
            reader = csv.reader(source)
            header = next(reader)
    except (
        OSError, UnicodeError, csv.Error, StopIteration
    ) as exc:
        raise OSError(
            '%s CSV header integrity check failed: %s' %
            (label, str(exc))
        ) from exc
    normalized = {
        (value or '').strip().lower() for value in header
    }
    missing = []
    if not any(
        column in normalized
        for column in ('block_fips_2020', 'block_fips_20')
    ):
        missing.append('block_fips_2020/block_fips_20')
    if not any(
        column in normalized
        for column in ('ce_fips_2022', 'ce_fips_22')
    ):
        missing.append('ce_fips_2022/ce_fips_22')
    if missing:
        raise OSError(
            '%s CSV header integrity check failed; missing %s; '
            'headers=%r' % (label, ', '.join(missing), header)
        )


def _validate_source(path, label, integrity):
    if integrity is None:
        return
    if integrity == 'zip':
        _validate_zip(path, label)
        return
    if integrity == 'ct_bridge':
        _validate_ct_bridge_header(path, label)
        return
    raise OSError(
        'internal error: unknown integrity gate %r for %s' %
        (integrity, label)
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
    print(
        '!! preserved invalid cached %s as %s' %
        (label, corrupt_path)
    )


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


def _download_source(
    label, url, path, force, integrity=None, fallback_404_url=None
):
    if os.path.exists(path) and not force:
        try:
            _validate_source(path, label, integrity)
        except Exception as exc:
            if integrity != 'zip':
                raise SystemExit(
                    'invalid cached %s %s: %s' %
                    (label, path, str(exc))
                ) from exc
            _preserve_corrupt_cache(path, label)
        else:
            print('cached %s: %s' % (label, path))
            cached_url = url
            if fallback_404_url is not None:
                # The bridge may have been fetched from the fallback URL;
                # report the URL recorded at download time.
                try:
                    with open(
                        path + '.source_url', 'r', encoding='utf-8'
                    ) as sidecar:
                        cached_url = sidecar.read().strip() or url
                except OSError:
                    cached_url = url
            return {
                'url': cached_url,
                'sha256': _sha256(path),
                'retrieved': _utc_iso(os.path.getmtime(path)),
            }

    part_path = path + '.part'
    failure = None
    active_url = url
    for attempt in range(DOWNLOAD_ATTEMPTS):
        try:
            print(
                'downloading %s (attempt %d/%d): %s' %
                (label, attempt + 1, DOWNLOAD_ATTEMPTS, active_url)
            )
            try:
                _download_to_part(active_url, part_path)
            except urllib.error.HTTPError as exc:
                if (
                    exc.code == 404 and
                    fallback_404_url is not None and
                    active_url == url
                ):
                    active_url = fallback_404_url
                    print(
                        '!! %s master URL returned 404; trying main: %s' %
                        (label, active_url)
                    )
                    _download_to_part(active_url, part_path)
                else:
                    raise
            _validate_source(part_path, label, integrity)
            os.replace(part_path, path)
            print('downloaded %s: %s' % (label, path))
            if fallback_404_url is not None:
                try:
                    with open(
                        path + '.source_url', 'w', encoding='utf-8'
                    ) as sidecar:
                        sidecar.write(active_url)
                except OSError as exc:
                    print(
                        '!! could not record source URL for %s: %s' %
                        (label, str(exc))
                    )
            return {
                'url': active_url,
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


def _load_crosswalk(path):
    if not os.path.exists(path):
        raise SystemExit('block crosswalk does not exist: %s' % path)

    assignments = {}
    print('loading block crosswalk: %s' % path)
    with gzip.open(path, 'rb') as compressed:
        with io.TextIOWrapper(
            compressed,
            encoding='utf-8-sig',
            errors='strict',
            newline=''
        ) as source:
            reader = csv.DictReader(source)
            if reader.fieldnames != ['block_geoid', 'cd']:
                raise SystemExit(
                    'invalid crosswalk header in %s: expected '
                    'block_geoid,cd; found %r' % (path, reader.fieldnames)
                )
            for row in reader:
                if None in row:
                    raise SystemExit(
                        '%s row %d has surplus field(s): %r' %
                        (path, reader.line_num, row[None])
                    )
                block_geoid = (row.get('block_geoid') or '').strip()
                district = (row.get('cd') or '').strip()
                if len(block_geoid) != 15 or not block_geoid.isdigit():
                    raise SystemExit(
                        '%s row %d has invalid block_geoid %r' %
                        (path, reader.line_num, block_geoid)
                    )
                if (
                    len(district) != 2 or
                    not (district.isdigit() or district == 'ZZ')
                ):
                    raise SystemExit(
                        '%s row %d has invalid cd %r' %
                        (path, reader.line_num, district)
                    )
                state_fips = block_geoid[:2]
                if state_fips not in FIPS_TO_USPS:
                    raise SystemExit(
                        '%s row %d has unmapped state FIPS %r' %
                        (path, reader.line_num, state_fips)
                    )
                if block_geoid in assignments:
                    raise SystemExit(
                        '%s row %d duplicates block_geoid %s' %
                        (path, reader.line_num, block_geoid)
                    )
                assignments[block_geoid] = district
    print('loaded block crosswalk: %d assignments' % len(assignments))
    return assignments


def _load_ct_bridge(path):
    if not os.path.exists(path):
        raise SystemExit('CT planning-region bridge does not exist: %s' % path)

    bridge = {}
    region_codes = set()
    print('loading CT planning-region bridge: %s' % path)
    with open(
        path, 'r', encoding='utf-8-sig', errors='strict', newline=''
    ) as source:
        reader = csv.DictReader(source)
        fieldnames = reader.fieldnames or []
        headers = {
            (header or '').strip().lower(): header
            for header in fieldnames
        }
        missing = []
        if not any(
            column in headers
            for column in ('block_fips_2020', 'block_fips_20')
        ):
            missing.append('block_fips_2020/block_fips_20')
        if not any(
            column in headers
            for column in ('ce_fips_2022', 'ce_fips_22')
        ):
            missing.append('ce_fips_2022/ce_fips_22')
        if missing:
            raise SystemExit(
                'CT bridge %s is missing required column(s) %s; '
                'headers=%r' % (path, ', '.join(missing), fieldnames)
            )
        block_header = (
            headers.get('block_fips_2020') or
            headers['block_fips_20']
        )
        region_header = (
            headers.get('ce_fips_2022') or
            headers['ce_fips_22']
        )
        for row in reader:
            if None in row:
                raise SystemExit(
                    '%s row %d has surplus field(s): %r' %
                    (path, reader.line_num, row[None])
                )
            block_geoid = (row.get(block_header) or '').strip()
            if len(block_geoid) != 15 or not block_geoid.isdigit():
                raise SystemExit(
                    '%s row %d has invalid block_fips_20 %r' %
                    (path, reader.line_num, block_geoid)
                )
            region_text = (row.get(region_header) or '').strip()
            if not region_text.isdigit() or len(region_text) > 5:
                raise SystemExit(
                    '%s row %d has invalid ce_fips_22 %r' %
                    (path, reader.line_num, region_text)
                )
            region_code = region_text.zfill(5)
            if region_code not in CT_REGION_CODES:
                raise SystemExit(
                    '%s row %d has unexpected CT planning-region code %s' %
                    (path, reader.line_num, region_code)
                )
            if block_geoid in bridge:
                raise SystemExit(
                    '%s row %d duplicates block_fips_20 %s' %
                    (path, reader.line_num, block_geoid)
                )
            bridge[block_geoid] = region_code
            region_codes.add(region_code)

    missing_regions = sorted(CT_REGION_CODES - region_codes)
    if missing_regions:
        raise SystemExit(
            'CT bridge %s is missing planning-region code(s): %s' %
            (path, ', '.join(missing_regions))
        )
    print(
        'loaded CT planning-region bridge: %d blocks, 9 regions' %
        len(bridge)
    )
    return bridge


def _latest_wac_files(raw_root):
    if not os.path.isdir(raw_root):
        raise SystemExit('LODES raw directory does not exist: %s' % raw_root)

    names = os.listdir(raw_root)
    selected = {}
    per_state_source_year = {}
    for state in LODES_JURISDICTIONS:
        pattern = re.compile(
            RAW_WAC_RE_TEMPLATE.format(state=re.escape(state)),
            re.IGNORECASE,
        )
        candidates = []
        for name in names:
            match = pattern.fullmatch(name)
            if match is not None:
                candidates.append((int(match.group(1)), name))
        if not candidates:
            raise SystemExit(
                'no raw S000/JT01 WAC file found for %s in %s' %
                (state.upper(), raw_root)
            )
        year, name = max(candidates)
        selected[state] = os.path.join(raw_root, name)
        per_state_source_year[state.upper()] = year

    print(
        'selected latest raw WAC files: %d jurisdictions' %
        len(selected)
    )
    return selected, per_state_source_year


def _parse_nonnegative_int(text, label):
    value_text = (text or '').strip()
    try:
        value = int(value_text)
    except ValueError as exc:
        raise SystemExit(
            '%s has invalid integer count %r' % (label, value_text)
        ) from exc
    if value < 0:
        raise SystemExit(
            '%s has negative count %d' % (label, value)
        )
    return value


def _stream_wac_file(
    path, state, assignments, ct_bridge, county_total, district_emp
):
    xwalk_misses = 0
    bridged_blocks = 0
    rows = 0
    with gzip.open(path, 'rb') as compressed:
        with io.TextIOWrapper(
            compressed,
            encoding='utf-8-sig',
            errors='strict',
            newline=''
        ) as source:
            reader = csv.DictReader(source)
            fieldnames = reader.fieldnames or []
            missing = [
                column for column in ('w_geocode', 'C000')
                if column not in fieldnames
            ]
            if missing:
                raise SystemExit(
                    '%s is missing required WAC column(s): %s; headers=%r' %
                    (path, ', '.join(missing), fieldnames)
                )
            for row in reader:
                rows += 1
                block_geoid = (row.get('w_geocode') or '').strip()
                if len(block_geoid) != 15 or not block_geoid.isdigit():
                    raise SystemExit(
                        '%s row %d has invalid w_geocode %r' %
                        (path, reader.line_num, block_geoid)
                    )
                expected_state = USPS_TO_FIPS[state.upper()]
                if block_geoid[:2] != expected_state:
                    raise SystemExit(
                        '%s row %d has state FIPS %s; expected %s for %s' %
                        (
                            path,
                            reader.line_num,
                            block_geoid[:2],
                            expected_state,
                            state.upper(),
                        )
                    )
                count = _parse_nonnegative_int(
                    row.get('C000'),
                    '%s row %d C000' % (path, reader.line_num),
                )
                if block_geoid[:2] == '09':
                    county_fips = ct_bridge.get(block_geoid)
                    if county_fips is None:
                        raise SystemExit(
                            'CT WAC block is absent from planning-region '
                            'bridge: %s' % block_geoid
                        )
                    bridged_blocks += 1
                else:
                    county_fips = block_geoid[:5]
                county_total[county_fips] += count
                district = assignments.get(block_geoid)
                if district is None:
                    xwalk_misses += 1
                elif district not in ('98', 'ZZ'):
                    cd_geo = '%s-%s' % (state.upper(), district)
                    district_emp[(county_fips, cd_geo)] += count

    print(
        'weights source %s %s: %d rows, %d crosswalk misses' %
        (state.upper(), os.path.basename(path), rows, xwalk_misses)
    )
    return xwalk_misses, bridged_blocks


def _write_weight_cache(path, county_total, district_emp):
    rows = []
    county_weights = {}
    for (county_fips, cd_geo), employment in sorted(
        district_emp.items()
    ):
        total = county_total.get(county_fips, 0)
        if total <= 0 or employment <= 0:
            continue
        if employment > total:
            raise SystemExit(
                'weight numerator exceeds county total for %s/%s: %d > %d' %
                (county_fips, cd_geo, employment, total)
            )
        weight = employment / total
        rows.append((county_fips, cd_geo, weight, employment))
        county_weights.setdefault(county_fips, []).append(
            (cd_geo, weight)
        )

    for county_fips, weights in county_weights.items():
        weight_sum = sum(weight for _geo, weight in weights)
        if weight_sum > 1.0000000001:
            raise SystemExit(
                'county %s district weights sum above 1.0: %.12f' %
                (county_fips, weight_sum)
            )

    part_path = path + '.part'
    with gzip.open(
        part_path, 'wt', encoding='utf-8', errors='strict', newline=''
    ) as target:
        writer = csv.writer(target, lineterminator='\n')
        writer.writerow(['county_fips', 'cd', 'weight', 'employment'])
        for county_fips, cd_geo, weight, employment in rows:
            writer.writerow([
                county_fips,
                cd_geo,
                '%.6f' % weight,
                employment,
            ])
    try:
        with gzip.open(part_path, 'rb') as validation:
            while validation.read(CHUNK_SIZE):
                pass
    except (gzip.BadGzipFile, EOFError, OSError) as exc:
        _discard_part(part_path)
        raise SystemExit(
            'weight-cache gzip integrity check failed for %s: %s' %
            (path, str(exc))
        ) from exc
    os.replace(part_path, path)
    print(
        'wrote county/CD weight cache: %d rows, %d counties at %s' %
        (len(rows), len(county_weights), path)
    )
    return county_weights


def _build_weights(
    raw_root, crosswalk_path, ct_bridge_path, weight_path
):
    assignments = _load_crosswalk(crosswalk_path)
    ct_bridge = _load_ct_bridge(ct_bridge_path)
    selected, per_state_source_year = _latest_wac_files(raw_root)
    county_total = defaultdict(int)
    district_emp = defaultdict(int)
    xwalk_misses = 0
    bridged_blocks = 0

    for state in LODES_JURISDICTIONS:
        file_misses, file_bridged_blocks = _stream_wac_file(
            selected[state],
            state,
            assignments,
            ct_bridge,
            county_total,
            district_emp,
        )
        xwalk_misses += file_misses
        bridged_blocks += file_bridged_blocks

    print('bridged CT WAC blocks to planning regions: %d' % bridged_blocks)

    county_weights = _write_weight_cache(
        weight_path, county_total, district_emp
    )
    zero_employment_counties = sorted(
        county_fips
        for county_fips, employment in county_total.items()
        if employment == 0
    )
    print(
        '!! zero-employment counties in latest WAC sources: %d' %
        len(zero_employment_counties)
    )
    print(
        '!! latest WAC crosswalk misses retained in county totals: %d' %
        xwalk_misses
    )
    return {
        'county_weights': county_weights,
        'per_state_source_year': per_state_source_year,
        'n_counties': len(county_weights),
        'zero_employment_counties': zero_employment_counties,
        'xwalk_misses': xwalk_misses,
    }


def _single_archive_member(archive, label):
    members = [
        member for member in archive.namelist()
        if not member.endswith('/')
    ]
    if len(members) != 1:
        listing = ', '.join(archive.namelist()) or '<empty archive>'
        raise SystemExit(
            '%s archive has %d non-directory members; members: %s' %
            (label, len(members), listing)
        )
    return members[0]


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


def _smallest_size_header(headers, label, fieldnames):
    if 'n1_4' in headers:
        return headers['n1_4']
    if 'n<5' in headers:
        return headers['n<5']
    raise SystemExit(
        '%s is missing smallest size-class column n1_4 or n<5; '
        'headers=%r' % (label, fieldnames)
    )


def _suppression_header(headers, label, fieldnames):
    if 'empflag' in headers:
        return headers['empflag']
    if 'emp_nf' in headers:
        return headers['emp_nf']
    raise SystemExit(
        '%s is missing required suppression-flag column empflag or '
        'emp_nf; headers=%r' % (label, fieldnames)
    )


def _row_cell(row, header, label, line_num):
    value = row.get(header)
    if value is None:
        raise SystemExit(
            '%s row %d is missing column %r' %
            (label, line_num, header)
        )
    return value


def _classify_naics(value):
    text = (value or '').strip()
    normalized_range = RANGE_NAICS_ALIASES.get(text)
    if normalized_range is not None:
        return '2digit', normalized_range
    if text == TOTAL_NAICS:
        return 'total', TOTAL_NAICS
    if NAICS_2_RE.fullmatch(text):
        return '2digit', text[:2]
    if text in RANGE_NAICS:
        return '2digit', text
    if text.endswith('-') and text[:-1] in RANGE_NAICS:
        return '2digit', text[:-1]
    if NAICS_3_RE.fullmatch(text):
        return '3digit', text[:3]
    if NAICS_4_RE.fullmatch(text):
        return '4digit', text[:4]
    if NAICS_5_RE.fullmatch(text):
        return '5digit', text[:5]
    if NAICS_6_RE.fullmatch(text):
        return '6digit', text
    return None, text


def _normalize_fips(value, width, label):
    text = (value or '').strip()
    if not text.isdigit() or len(text) > width:
        raise SystemExit(
            '%s has invalid FIPS component %r' % (label, text)
        )
    return text.zfill(width)


def _parse_count_cell(row, header, label, line_num):
    text = _row_cell(row, header, label, line_num).strip()
    if not text or text.upper() == 'N':
        return UNKNOWN
    return _parse_nonnegative_int(
        text, '%s row %d column %s' % (label, line_num, header)
    )


def _parse_cbp_archive(path, label, geography):
    if geography not in ('county', 'us'):
        raise SystemExit(
            'internal error: unknown CBP geography %r' % geography
        )

    records = {} if geography == 'us' else {}
    stats = {
        'data_rows': 0,
        'lfo_skipped': 0,
        'skipped_3digit': 0,
        'skipped_5digit': 0,
        'n_unclassified_rows': 0,
        'retained_rows': 0,
        'n_4digit_rows': 0,
        'n_emp_suppressed': 0,
        'n_est_suppressed': 0,
        'n_sizeclass_unknown_rows': 0,
    }
    unknown_naics = set()

    with zipfile.ZipFile(path, 'r') as archive:
        member = _single_archive_member(archive, label)
        print('parsing %s member: %s' % (label, member))
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
            emp_header = _required_header(
                headers, 'emp', label, fieldnames
            )
            est_header = _required_header(
                headers, 'est', label, fieldnames
            )
            suppression_header = _suppression_header(
                headers, label, fieldnames
            )
            lfo_header = headers.get('lfo')

            if geography == 'county':
                state_header = _required_header(
                    headers, 'fipstate', label, fieldnames
                )
                county_header = _required_header(
                    headers, 'fipscty', label, fieldnames
                )
                size_1_4_header = _smallest_size_header(
                    headers, label, fieldnames
                )
                size_5_9_header = _required_header(
                    headers, 'n5_9', label, fieldnames
                )
                size_10_19_header = _required_header(
                    headers, 'n10_19', label, fieldnames
                )

            for row in reader:
                stats['data_rows'] += 1
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
                        stats['lfo_skipped'] += 1
                        continue

                raw_naics = _row_cell(
                    row, naics_header, label, reader.line_num
                )
                level, naics = _classify_naics(raw_naics)
                if level is None:
                    unknown_naics.add(naics)
                    continue
                if level == '2digit' and naics == '99':
                    stats['n_unclassified_rows'] += 1
                    continue
                if level == '3digit':
                    stats['skipped_3digit'] += 1
                    continue
                if level == '5digit':
                    stats['skipped_5digit'] += 1
                    continue
                if level == '6digit' and naics not in CU_NAICS:
                    continue

                est_value = _parse_count_cell(
                    row, est_header, label, reader.line_num
                )
                if level == 'total' and est_value is UNKNOWN:
                    raise SystemExit(
                        '%s row %d has UNKNOWN total establishment count' %
                        (label, reader.line_num)
                    )
                est = None if est_value is UNKNOWN else est_value
                emp_value = _parse_count_cell(
                    row, emp_header, label, reader.line_num
                )
                # 'N' = not available/not comparable (emp cell is 0)
                flag_suppressed = (
                    _row_cell(
                        row, suppression_header, label, reader.line_num
                    ).strip().upper() in ('D', 'S', 'N')
                )
                suppressed = (
                    flag_suppressed or emp_value is UNKNOWN
                )
                emp = None if suppressed else emp_value

                record = {
                    'level': level,
                    'est': est,
                    'emp': emp,
                }
                if est is None:
                    stats['n_est_suppressed'] += 1
                if geography == 'county':
                    state_fips = _normalize_fips(
                        _row_cell(
                            row, state_header, label, reader.line_num
                        ),
                        2,
                        '%s row %d fipstate' %
                        (label, reader.line_num),
                    )
                    county_fips = _normalize_fips(
                        _row_cell(
                            row, county_header, label, reader.line_num
                        ),
                        3,
                        '%s row %d fipscty' %
                        (label, reader.line_num),
                    )
                    county_key = state_fips + county_fips
                    size_1_4 = _parse_count_cell(
                        row, size_1_4_header, label, reader.line_num
                    )
                    size_5_9 = _parse_count_cell(
                        row, size_5_9_header, label, reader.line_num
                    )
                    size_10_19 = _parse_count_cell(
                        row, size_10_19_header, label, reader.line_num
                    )
                    sizeclass_unknown = any(
                        value is UNKNOWN
                        for value in (size_1_4, size_5_9, size_10_19)
                    )
                    record['n1_4'] = (
                        0 if size_1_4 is UNKNOWN else size_1_4
                    )
                    record['n5_9'] = (
                        0 if size_5_9 is UNKNOWN else size_5_9
                    )
                    record['n10_19'] = (
                        0 if size_10_19 is UNKNOWN else size_10_19
                    )
                    record['sizeclass_complete'] = not sizeclass_unknown
                    by_naics = records.setdefault(county_key, {})
                    if naics in by_naics:
                        raise SystemExit(
                            '%s row %d duplicates county/NAICS %s/%s' %
                            (
                                label,
                                reader.line_num,
                                county_key,
                                naics,
                            )
                        )
                    by_naics[naics] = record
                    if sizeclass_unknown:
                        stats['n_sizeclass_unknown_rows'] += 1
                    if level == '4digit':
                        stats['n_4digit_rows'] += 1
                        if suppressed:
                            stats['n_emp_suppressed'] += 1
                else:
                    if naics in records:
                        raise SystemExit(
                            '%s row %d duplicates US NAICS %s' %
                            (label, reader.line_num, naics)
                        )
                    records[naics] = record
                stats['retained_rows'] += 1

    if unknown_naics:
        raise SystemExit(
            '%s has unrecognized NAICS value(s): %s' %
            (
                label,
                ', '.join(repr(value) for value in sorted(unknown_naics)),
            )
        )
    if not records:
        raise SystemExit('%s has no retained rows' % label)

    print(
        'parsed %s: %d retained, skipped 3-digit/5-digit = %d/%d, '
        'LFO skipped = %d, EST suppressed = %d, '
        'size-class unknown rows = %d, unclassified rows = %d' %
        (
            label,
            stats['retained_rows'],
            stats['skipped_3digit'],
            stats['skipped_5digit'],
            stats['lfo_skipped'],
            stats['n_est_suppressed'],
            stats['n_sizeclass_unknown_rows'],
            stats['n_unclassified_rows'],
        )
    )
    return records, stats


def _validate_phase_c(artifact, path):
    if artifact.get('schema') != 'district-industry-v1':
        raise SystemExit(
            'invalid Phase C schema in %s: %r' %
            (path, artifact.get('schema'))
        )
    sector_meta = artifact.get('sector_meta')
    geos = artifact.get('geos')
    if not isinstance(sector_meta, dict):
        raise SystemExit(
            'Phase C artifact %s has no sector_meta object' % path
        )
    if not isinstance(geos, dict):
        raise SystemExit(
            'Phase C artifact %s has no geos object' % path
        )
    expected_sectors = set(CNS_SECTORS)
    actual_sectors = set(sector_meta)
    if actual_sectors != expected_sectors:
        raise SystemExit(
            'Phase C artifact %s sector_meta keys mismatch: '
            'missing=%r; unexpected=%r' %
            (
                path,
                sorted(expected_sectors - actual_sectors),
                sorted(
                    actual_sectors - expected_sectors, key=repr
                ),
            )
        )

    naics_to_sector = {}
    for sector, detail in sector_meta.items():
        if (
            not isinstance(sector, str) or
            re.fullmatch(r'CNS[0-9]{2}', sector) is None or
            not isinstance(detail, dict)
        ):
            raise SystemExit(
                'Phase C artifact %s has invalid sector_meta entry %r' %
                (path, sector)
            )
        naics = detail.get('naics')
        if not isinstance(naics, str) or not naics:
            raise SystemExit(
                'Phase C artifact %s sector %s has invalid naics %r' %
                (path, sector, naics)
            )
        if naics in naics_to_sector:
            raise SystemExit(
                'Phase C artifact %s duplicates sector NAICS %s' %
                (path, naics)
            )
        naics_to_sector[naics] = sector

    sector_to_cbp_naics = {
        sector: detail['naics']
        for sector, detail in sector_meta.items()
    }
    for cbp_naics, sector in CBP_CNS_ALIASES.items():
        if sector not in sector_meta:
            raise SystemExit(
                'Phase C artifact %s is missing aliased sector %s' %
                (path, sector)
            )
        if (
            cbp_naics in naics_to_sector and
            naics_to_sector[cbp_naics] != sector
        ):
            raise SystemExit(
                'Phase C artifact %s conflicts with CBP alias %s->%s' %
                (path, cbp_naics, sector)
            )
        naics_to_sector[cbp_naics] = sector
        sector_to_cbp_naics[sector] = cbp_naics

    districts = {}
    for geo, detail in geos.items():
        if not isinstance(detail, dict) or detail.get('kind') != 'district':
            continue
        if re.fullmatch(r'[A-Z]{2}-[0-9]{2}', geo) is None:
            raise SystemExit(
                'Phase C artifact %s has invalid district geo %r' %
                (path, geo)
            )
        lq_national = detail.get('lq_national')
        if not isinstance(lq_national, dict):
            raise SystemExit(
                'Phase C artifact %s district %s has no lq_national object' %
                (path, geo)
            )
        missing_sectors = [
            sector for sector in CNS_SECTORS
            if sector not in lq_national
        ]
        if missing_sectors:
            raise SystemExit(
                'Phase C artifact %s district %s is missing LQ sector(s): %s' %
                (path, geo, ', '.join(sorted(missing_sectors)))
            )
        for sector in CNS_SECTORS:
            value = lq_national[sector]
            if (
                value is not None and
                (
                    isinstance(value, bool) or
                    not isinstance(value, (int, float)) or
                    not math.isfinite(value) or
                    value < 0
                )
            ):
                raise SystemExit(
                    'Phase C artifact %s district %s sector %s has '
                    'invalid lq_national %r; expected null or a finite, '
                    'non-negative, non-boolean number' %
                    (path, geo, sector, value)
                )
        districts[geo] = detail

    if len(districts) != 435:
        raise SystemExit(
            'Phase C artifact %s has %d district geos; expected 435' %
            (path, len(districts))
        )
    print('loaded Phase C district universe: 435 districts')
    return {
        'sector_meta': sector_meta,
        'naics_to_sector': naics_to_sector,
        'sector_to_cbp_naics': sector_to_cbp_naics,
        'districts': districts,
    }


def _new_district_accumulator():
    return {
        'est': defaultdict(float),
        'emp': defaultdict(float),
        'emp_suppressed': set(),
        'sizeclass_incomplete': set(),
        'n1_4': defaultdict(float),
        'n5_9': defaultdict(float),
        'n10_19': defaultdict(float),
    }


def _allocate_counties(county_records, county_weights, districts):
    allocated = {
        geo: _new_district_accumulator() for geo in sorted(districts)
    }
    for county_fips, by_naics in county_records.items():
        weights = county_weights.get(county_fips, ())
        if weights and TOTAL_NAICS not in by_naics:
            raise SystemExit(
                'CBP county %s has district weights but no total row' %
                county_fips
            )
        for geo, weight in weights:
            if geo not in allocated:
                raise SystemExit(
                    'county/CD weight targets non-Phase-C district %s '
                    'for county %s' % (geo, county_fips)
                )
            target = allocated[geo]
            for naics, record in by_naics.items():
                if record['est'] is not None:
                    target['est'][naics] += record['est'] * weight
                target['n1_4'][naics] += record['n1_4'] * weight
                target['n5_9'][naics] += record['n5_9'] * weight
                target['n10_19'][naics] += (
                    record['n10_19'] * weight
                )
                if not record['sizeclass_complete']:
                    target['sizeclass_incomplete'].add(naics)
                if record['emp'] is None:
                    target['emp_suppressed'].add(naics)
                else:
                    target['emp'][naics] += record['emp'] * weight
    return allocated


def _allocation_coverage(county_records, county_weights):
    total_est = 0.0
    allocated_est = 0.0
    total_emp = 0.0
    allocated_emp = 0.0
    n_counties_total_emp_suppressed = 0
    for county_fips, by_naics in county_records.items():
        if county_fips[:2] not in VOTING_FIPS:
            continue
        total_row = by_naics.get(TOTAL_NAICS)
        if total_row is None:
            raise SystemExit(
                'voting-state CBP county %s has no total row' % county_fips
            )
        if total_row['est'] is None:
            raise SystemExit(
                'voting-state CBP county %s has UNKNOWN total '
                'establishment count' % county_fips
            )
        weight_sum = sum(
            weight for _geo, weight in county_weights.get(
                county_fips, ()
            )
        )
        total_est += total_row['est']
        allocated_est += total_row['est'] * weight_sum
        if total_row['emp'] is None:
            n_counties_total_emp_suppressed += 1
            continue
        total_emp += total_row['emp']
        allocated_emp += total_row['emp'] * weight_sum

    if total_est <= 0 or total_emp <= 0:
        raise SystemExit(
            'voting-state county totals are zero: est=%r emp=%r' %
            (total_est, total_emp)
        )
    est_allocated_share = allocated_est / total_est
    emp_allocated_share = allocated_emp / total_emp
    if est_allocated_share > 1.0000000001:
        raise SystemExit(
            'allocated EST share exceeds 1.0: %.12f' %
            est_allocated_share
        )
    if emp_allocated_share > 1.0000000001:
        raise SystemExit(
            'allocated EMP share exceeds 1.0: %.12f' %
            emp_allocated_share
        )
    unallocated_est_share = max(0.0, 1.0 - est_allocated_share)
    unallocated_emp_share = max(0.0, 1.0 - emp_allocated_share)
    print(
        '!! county-total EST allocated/unallocated share = %.4f/%.4f' %
        (est_allocated_share, unallocated_est_share)
    )
    print(
        '!! county-total EMP allocated/unallocated share = %.4f/%.4f' %
        (emp_allocated_share, unallocated_emp_share)
    )
    print(
        '!! voting-state counties with suppressed total EMP excluded '
        'from EMP share: %d' % n_counties_total_emp_suppressed
    )
    print(
        '!! DC/PR counties are by-design unallocated and excluded from '
        'the allocation-share denominators'
    )
    return {
        'unallocated_est_share': round(unallocated_est_share, 4),
        'unallocated_emp_share': round(unallocated_emp_share, 4),
        'n_counties_total_emp_suppressed':
            n_counties_total_emp_suppressed,
    }


def _district_emp_value(accumulator, naics):
    if naics in accumulator['emp_suppressed']:
        return None
    return accumulator['emp'].get(naics, 0.0)


def _location_quotient(local_value, local_total, us_value, us_total):
    if (
        local_value is None or
        local_total is None or
        us_value is None or
        us_total is None or
        local_total <= 0 or
        us_total <= 0 or
        us_value <= 0
    ):
        return None
    return (local_value / local_total) / (us_value / us_total)


def _round_lq(value):
    if value is None:
        return None
    return round(value, 3)


def _build_districts(
    allocated, phase_c, us_records, lq_hi, lq_lo
):
    us_total = us_records.get(TOTAL_NAICS)
    if us_total is None:
        raise SystemExit('CBP US file has no total NAICS row ------')
    if us_total['est'] is None:
        raise SystemExit(
            'CBP US total NAICS row has UNKNOWN establishment count'
        )
    us_four_codes = sorted(
        naics for naics, record in us_records.items()
        if record['level'] == '4digit'
    )
    if not us_four_codes:
        raise SystemExit('CBP US file has no retained 4-digit rows')
    us_four_set = set(us_four_codes)

    districts = {}
    contradiction_high = 0
    contradiction_low = 0
    contradiction_high_out_of_universe = 0
    contradiction_low_out_of_universe = 0
    emitted_four_codes = set()
    for geo in sorted(phase_c['districts']):
        accumulator = allocated[geo]
        district_total_est = accumulator['est'].get(TOTAL_NAICS, 0.0)
        district_total_emp = _district_emp_value(
            accumulator, TOTAL_NAICS
        )

        four_digit = {}
        local_four_codes = sorted(
            naics
            for naics, est in accumulator['est'].items()
            if len(naics) == 4 and naics.isdigit() and est > 0
        )
        missing_us_codes = [
            naics for naics in local_four_codes
            if naics not in us_four_set
        ]
        if missing_us_codes:
            raise SystemExit(
                'district %s has allocated 4-digit code(s) absent from '
                'the US table: %s' %
                (geo, ', '.join(missing_us_codes))
            )
        for naics in local_four_codes:
            local_est = accumulator['est'][naics]
            local_emp = _district_emp_value(accumulator, naics)
            us_record = us_records[naics]
            four_digit[naics] = {
                'emp': (
                    None if local_emp is None else round(local_emp, 1)
                ),
                'est': round(local_est, 1),
                'lq_emp': _round_lq(
                    _location_quotient(
                        local_emp,
                        district_total_emp,
                        us_record['emp'],
                        us_total['emp'],
                    )
                ),
                'lq_est': _round_lq(
                    _location_quotient(
                        local_est,
                        district_total_est,
                        us_record['est'],
                        us_total['est'],
                    )
                ),
            }
            emitted_four_codes.add(naics)

        sector_agreement = {}
        phase_c_geo = phase_c['districts'][geo]
        for sector in sorted(phase_c['sector_meta']):
            naics = phase_c['sector_to_cbp_naics'][sector]
            lodes_lq = phase_c_geo['lq_national'][sector]
            if (
                lodes_lq is not None and
                not isinstance(lodes_lq, (int, float))
            ):
                raise SystemExit(
                    'Phase C district %s sector %s has invalid '
                    'lq_national %r' % (geo, sector, lodes_lq)
                )
            us_sector = us_records.get(naics)
            if us_sector is None:
                cbp_lq_est_raw = None
            else:
                cbp_lq_est_raw = _location_quotient(
                    accumulator['est'].get(naics, 0.0),
                    district_total_est,
                    us_sector['est'],
                    us_total['est'],
                )
            contradicted_high = bool(
                lodes_lq is not None and
                lodes_lq >= lq_hi and
                cbp_lq_est_raw is not None and
                cbp_lq_est_raw < lq_lo
            )
            contradicted_low = bool(
                lodes_lq is not None and
                lodes_lq < lq_lo and
                cbp_lq_est_raw is not None and
                cbp_lq_est_raw >= lq_hi
            )
            # Out-of-universe sectors (CNS01/CNS03/CNS20) are counted
            # apart so they do not inflate the headline counters; the
            # per-district flags below are unchanged.
            out_of_universe = sector in UNIVERSE_NOTES
            if contradicted_high:
                if out_of_universe:
                    contradiction_high_out_of_universe += 1
                else:
                    contradiction_high += 1
            if contradicted_low:
                if out_of_universe:
                    contradiction_low_out_of_universe += 1
                else:
                    contradiction_low += 1
            sector_agreement[sector] = {
                'lodes_lq': lodes_lq,
                'cbp_lq_est': _round_lq(cbp_lq_est_raw),
                'contradicted_high': contradicted_high,
                'contradicted_low': contradicted_low,
            }

        small_business = {}
        for display_naics, source_naics in SMALL_BUSINESS_NAICS:
            est_lt20 = (
                accumulator['n1_4'].get(source_naics, 0.0) +
                accumulator['n5_9'].get(source_naics, 0.0) +
                accumulator['n10_19'].get(source_naics, 0.0)
            )
            small_business[display_naics] = {
                'est_lt20': round(est_lt20, 1),
                'est_total': round(
                    accumulator['est'].get(source_naics, 0.0), 1
                ),
                'lt20_complete':
                    source_naics not in
                    accumulator['sizeclass_incomplete'],
            }

        cu_benchmark = {}
        for naics in CU_NAICS:
            local_emp = _district_emp_value(accumulator, naics)
            cu_benchmark['est_' + naics] = round(
                accumulator['est'].get(naics, 0.0), 1
            )
            cu_benchmark['emp_' + naics] = (
                None if local_emp is None else round(local_emp, 1)
            )

        districts[geo] = {
            'four_digit': four_digit,
            'sector_agreement': sector_agreement,
            'small_business': small_business,
            'cu_benchmark': cu_benchmark,
        }

    if len(districts) != 435:
        raise SystemExit(
            'internal district output count is %d; expected 435' %
            len(districts)
        )
    return {
        'districts': districts,
        'n_naics4': len(emitted_four_codes),
        'contradiction_high': contradiction_high,
        'contradiction_low': contradiction_low,
        'contradiction_high_out_of_universe':
            contradiction_high_out_of_universe,
        'contradiction_low_out_of_universe':
            contradiction_low_out_of_universe,
    }


def main():
    data_root = os.environ.get('DEMO_DATA_ROOT')
    if not data_root:
        base = (
            os.environ.get('LOCALAPPDATA') or
            os.path.expanduser(r'~\AppData\Local')
        )
        data_root = os.path.join(base, 'Melange', 'demographics')

    year = _parse_year(os.environ.get('CBP_YEAR', '2023'))
    yy = year[-2:]
    lq_hi = _parse_float_dial('LQ_HI', 1.25)
    lq_lo = _parse_float_dial('LQ_LO', 0.9)
    if lq_hi <= lq_lo:
        raise SystemExit(
            'invalid LQ thresholds: LQ_HI %r must be greater than '
            'LQ_LO %r' % (lq_hi, lq_lo)
        )
    force = os.environ.get('CBP_FORCE', '0') == '1'
    output_path = os.environ.get('CBP_OUT')
    if not output_path:
        output_path = os.path.join(
            MB_ROOT, 'data', 'lobbying', 'district_industry_4digit.json'
        )

    cbp_root = os.path.join(data_root, 'cbp')
    geo_root = os.path.join(data_root, 'geo')
    raw_root = os.path.join(data_root, 'lodes', 'raw')
    os.makedirs(cbp_root, exist_ok=True)
    os.makedirs(geo_root, exist_ok=True)

    urls = {
        label: template.format(year=year, yy=yy)
        for label, template in URL_TEMPLATES.items()
    }
    paths = {
        'co': os.path.join(cbp_root, 'cbp%sco.zip' % yy),
        'us': os.path.join(cbp_root, 'cbp%sus.zip' % yy),
        'cd_xlsx': os.path.join(cbp_root, 'cbp%scd.xlsx' % yy),
        'ct_bridge': os.path.join(
            geo_root, 'ct_2022_block_crosswalk.csv'
        ),
    }
    print('CBP Phase D year %s; data root %s' % (year, data_root))
    sources = {
        'co': _download_source(
            'CBP county', urls['co'], paths['co'], force, 'zip'
        ),
        'us': _download_source(
            'CBP US', urls['us'], paths['us'], force, 'zip'
        ),
        'ct_bridge': _download_source(
            'CT planning-region bridge',
            CT_BRIDGE_URL,
            paths['ct_bridge'],
            force,
            'ct_bridge',
            CT_BRIDGE_FALLBACK_URL,
        ),
    }
    # Best-effort: the XLSX is only cached for Phase E, never parsed here.
    try:
        sources['cd_xlsx_cached'] = _download_source(
            'CBP CD XLSX',
            urls['cd_xlsx'],
            paths['cd_xlsx'],
            force,
            'zip',
        )
    except SystemExit as exc:
        print(
            '!! CBP CD XLSX unavailable; Phase E '
            'validate_district_industry.py requires it: %s' % exc
        )
        sources['cd_xlsx_cached'] = None

    weight_path = os.path.join(
        geo_root, 'county_cd_weights.csv.gz'
    )
    crosswalk_path = os.path.join(
        geo_root, 'cd119_block_assign.csv.gz'
    )
    print('building county/CD weights from latest raw WAC files')
    weight_result = _build_weights(
        raw_root, crosswalk_path, paths['ct_bridge'], weight_path
    )

    county_records, county_stats = _parse_cbp_archive(
        paths['co'], 'CBP county', 'county'
    )
    us_records, _us_stats = _parse_cbp_archive(
        paths['us'], 'CBP US', 'us'
    )

    phase_c_path = os.path.join(MB_ROOT, 'data', 'lobbying', 'district_industry.json')
    print('loading Phase C artifact: %s' % phase_c_path)
    phase_c = _validate_phase_c(
        _load_json(phase_c_path, 'Phase C artifact'),
        phase_c_path,
    )

    print('allocating county CBP metrics to districts')
    allocated = _allocate_counties(
        county_records,
        weight_result['county_weights'],
        phase_c['districts'],
    )
    coverage = _allocation_coverage(
        county_records, weight_result['county_weights']
    )

    print('computing district LQs, contradiction screen, and benchmarks')
    district_result = _build_districts(
        allocated, phase_c, us_records, lq_hi, lq_lo
    )

    n_4digit_rows = county_stats['n_4digit_rows']
    n_emp_suppressed = county_stats['n_emp_suppressed']
    if n_4digit_rows:
        suppression_share = round(
            n_emp_suppressed / n_4digit_rows, 4
        )
    else:
        suppression_share = 0.0

    artifact = {
        'schema': 'district-industry-4digit-v1',
        'generated_at': _utc_iso(),
        'cbp_year': int(year),
        'universe_notes': UNIVERSE_NOTES,
        'sources': sources,
        'weights': {
            'method': 'lodes-block-employment',
            'per_state_source_year':
                weight_result['per_state_source_year'],
            'n_counties': weight_result['n_counties'],
            'zero_employment_counties':
                weight_result['zero_employment_counties'],
            'unallocated_est_share':
                coverage['unallocated_est_share'],
            'unallocated_emp_share':
                coverage['unallocated_emp_share'],
            'n_counties_total_emp_suppressed':
                coverage['n_counties_total_emp_suppressed'],
            'unallocated_scope_note': (
                'DC/PR counties are by-design unallocated and excluded '
                'from allocation-share denominators'
            ),
        },
        'thresholds': {
            'lq_hi': lq_hi,
            'lq_lo': lq_lo,
        },
        'districts': district_result['districts'],
        'suppression': {
            'n_4digit_rows': n_4digit_rows,
            'n_emp_suppressed': n_emp_suppressed,
            'share_emp_suppressed': suppression_share,
            'n_est_suppressed':
                county_stats['n_est_suppressed'],
            'n_sizeclass_unknown_rows':
                county_stats['n_sizeclass_unknown_rows'],
        },
    }

    print('writing Phase D artifact: %s' % output_path)
    dump_atomic(artifact, output_path, separators=(',', ':'))
    print(
        'wrote district_industry_4digit.json: 435 districts, '
        '%d naics4 codes, contradictions high/low = %d/%d '
        '(in-universe sectors only); out-of-CBP-universe sectors '
        '(CNS01/CNS03/CNS20) high/low = %d/%d; '
        'emp suppression share %.4f' %
        (
            district_result['n_naics4'],
            district_result['contradiction_high'],
            district_result['contradiction_low'],
            district_result['contradiction_high_out_of_universe'],
            district_result['contradiction_low_out_of_universe'],
            suppression_share,
        )
    )


if __name__ == '__main__':
    main()

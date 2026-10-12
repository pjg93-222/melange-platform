# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT  # centralized root (arch review 2026-07-02)
"""DISTRICT EMPLOYERS COLLECTOR/BUILDER — federal recipients and CBP scale.

Standalone Phase G collector/builder.  This script is deliberately not
chained into run_pipeline.py.  It collects USAspending top federal recipients
by place-of-performance congressional district, allocates cached County
Business Patterns large-establishment counts through Phase D's county/CD
weights, and writes the district-employers-v1 artifact plus a queue for
state UI-derived employer lists that remain manual or heavier collections.

Inputs:
  DEMO_DATA_ROOT/cbp/cbp23co.zip
  DEMO_DATA_ROOT/geo/county_cd_weights.csv.gz
  MB_ROOT/district_industry.json

Raw/cache outputs:
  DEMO_DATA_ROOT/employers/usaspending/{geo}.json

Repo-plane outputs:
  MB_ROOT/district_employers.json, unless EMP_OUT overrides the path
  MB_ROOT/employer_collection_queue.csv

Dials:
  DEMO_DATA_ROOT — out-of-OneDrive raw-data root; defaults to
                   %LOCALAPPDATA%/Melange/demographics
  EMP_FY        — federal fiscal year; defaults to "2025"
  EMP_FORCE     — set to "1" to refresh USAspending district caches
  EMP_OUT       — optional district-employers output-path override
"""
import concurrent.futures
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
import threading
import time
import urllib.request
import zipfile
from collections import defaultdict

from moneyball_core import dump_atomic, write_text_atomic


USER_AGENT = 'MelangeDemographics/1.0 (research; contact: repo owner)'
REQUEST_TIMEOUT = 120
DOWNLOAD_ATTEMPTS = 4
RETRY_BASE_SECONDS = 3
MAX_WORKERS = 4
REQUEST_START_INTERVAL = 0.25
MAX_USASPENDING_FAILURES = 20

USASPENDING_ENDPOINT = (
    'https://api.usaspending.gov/api/v2/search/'
    'spending_by_category/recipient/'
)
CBP_YEAR = '2023'
CBP_TOTAL_NAICS = '------'
REDACTED_RECIPIENT = 'REDACTED DUE TO PII'
WEIGHT_SUM_TOLERANCE = 1e-4

CNS_SECTORS = tuple('CNS%02d' % index for index in range(1, 21))
CBP_NAICS_ALIASES = {
    '31': 'CNS05',
    '44': 'CNS07',
    '48': 'CNS08',
}
RANGE_NAICS_ALIASES = {
    '31-33': '31',
    '44-45': '44',
    '48-49': '48',
}
NAICS_2_RE = re.compile(r'^[0-9]{2}----$')
NAICS_3_RE = re.compile(r'^[0-9]{3}///$')
NAICS_4_RE = re.compile(r'^[0-9]{4}//$')
NAICS_5_RE = re.compile(r'^[0-9]{5}/$')
NAICS_6_RE = re.compile(r'^[0-9]{6}$')
DISTRICT_RE = re.compile(r'^[A-Z]{2}-[0-9]{2}$')
UTC_ISO_RE = re.compile(
    r'^[0-9]{4}-[0-9]{2}-[0-9]{2}T'
    r'[0-9]{2}:[0-9]{2}:[0-9]{2}Z$'
)
UNKNOWN = object()

VOTING_STATES = (
    'AK', 'AL', 'AR', 'AZ', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA',
    'HI', 'IA', 'ID', 'IL', 'IN', 'KS', 'KY', 'LA', 'MA', 'MD',
    'ME', 'MI', 'MN', 'MO', 'MS', 'MT', 'NC', 'ND', 'NE', 'NH',
    'NJ', 'NM', 'NV', 'NY', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC',
    'SD', 'TN', 'TX', 'UT', 'VA', 'VT', 'WA', 'WI', 'WV', 'WY',
)
QUEUE_JURISDICTIONS = tuple(sorted(VOTING_STATES + ('DC', 'PR')))
# Same 50-state set as fec/collect/fetch_cbp_4digit.py's VOTING_FIPS (there
# derived from a FIPS_TO_USPS table filtered to VOTING_STATES). Listed
# directly here since this module has no FIPS->USPS table of its own.
VOTING_FIPS = frozenset((
    '01', '02', '04', '05', '06', '08', '09', '10', '12', '13',
    '15', '16', '17', '18', '19', '20', '21', '22', '23', '24',
    '25', '26', '27', '28', '29', '30', '31', '32', '33', '34',
    '35', '36', '37', '38', '39', '40', '41', '42', '44', '45',
    '46', '47', '48', '49', '50', '51', '53', '54', '55', '56',
))

_REQUEST_START_LOCK = threading.Lock()
_LAST_REQUEST_START = 0.0


def _configure_stdout():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')


def _utc_iso():
    value = datetime.datetime.now(datetime.timezone.utc)
    return value.replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def _parse_year(value):
    text = (value or '').strip()
    if re.fullmatch(r'[0-9]{4}', text) is None:
        raise SystemExit(
            'invalid EMP_FY %r; expected a four-digit federal fiscal year' %
            value
        )
    return text


def _fiscal_period(fy):
    year = int(fy)
    return {
        'start_date': '%04d-10-01' % (year - 1),
        'end_date': '%04d-09-30' % year,
    }


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


def _finite_nonnegative(value, label):
    if (
        isinstance(value, bool) or
        not isinstance(value, (int, float)) or
        not math.isfinite(value) or
        value < 0
    ):
        raise SystemExit(
            '%s has invalid nonnegative numeric value %r' % (label, value)
        )
    return value


def _validate_phase_c(artifact, path):
    if artifact.get('schema') != 'district-industry-v1':
        raise SystemExit(
            'invalid Phase C schema in %s: %r' %
            (path, artifact.get('schema'))
        )
    geos = artifact.get('geos')
    sector_meta = artifact.get('sector_meta')
    if not isinstance(geos, dict):
        raise SystemExit(
            'Phase C artifact %s has no geos object' % path
        )
    if not isinstance(sector_meta, dict):
        raise SystemExit(
            'Phase C artifact %s has no sector_meta object' % path
        )
    if set(sector_meta) != set(CNS_SECTORS):
        raise SystemExit(
            'Phase C artifact %s sector keys mismatch: '
            'missing=%r; unexpected=%r' %
            (
                path,
                sorted(set(CNS_SECTORS) - set(sector_meta)),
                sorted(set(sector_meta) - set(CNS_SECTORS), key=repr),
            )
        )

    naics_to_cns = {}
    for sector in CNS_SECTORS:
        detail = sector_meta.get(sector)
        if not isinstance(detail, dict):
            raise SystemExit(
                'Phase C artifact %s sector %s is not an object' %
                (path, sector)
            )
        raw_naics = detail.get('naics')
        if not isinstance(raw_naics, str) or not raw_naics:
            raise SystemExit(
                'Phase C artifact %s sector %s has invalid naics %r' %
                (path, sector, raw_naics)
            )
        naics = RANGE_NAICS_ALIASES.get(raw_naics, raw_naics)
        if re.fullmatch(r'[0-9]{2}', naics) is None:
            raise SystemExit(
                'Phase C artifact %s sector %s has unsupported naics %r' %
                (path, sector, raw_naics)
            )
        prior = naics_to_cns.get(naics)
        if prior is not None:
            raise SystemExit(
                'Phase C artifact %s maps NAICS %s to both %s and %s' %
                (path, naics, prior, sector)
            )
        naics_to_cns[naics] = sector

    for naics, sector in CBP_NAICS_ALIASES.items():
        if naics_to_cns.get(naics) != sector:
            raise SystemExit(
                'Phase C artifact %s conflicts with CBP alias %s->%s' %
                (path, naics, sector)
            )

    districts = {}
    for geo, detail in geos.items():
        if not isinstance(detail, dict) or detail.get('kind') != 'district':
            continue
        if DISTRICT_RE.fullmatch(geo) is None:
            raise SystemExit(
                'Phase C artifact %s has invalid district geo %r' %
                (path, geo)
            )
        state, cd = geo.split('-')
        if state not in VOTING_STATES:
            raise SystemExit(
                'Phase C artifact %s has non-voting district %s' %
                (path, geo)
            )
        if detail.get('state') != state or detail.get('cd') != cd:
            raise SystemExit(
                'Phase C artifact %s district %s state/CD mismatch' %
                (path, geo)
            )
        employment = detail.get('employment')
        makeup = detail.get('makeup_pct')
        if not isinstance(employment, dict):
            raise SystemExit(
                'Phase C artifact %s district %s has no employment object' %
                (path, geo)
            )
        total_employment = _finite_nonnegative(
            employment.get('C000'),
            'Phase C artifact %s district %s employment.C000' % (path, geo),
        )
        if not isinstance(makeup, dict) or set(makeup) != set(CNS_SECTORS):
            raise SystemExit(
                'Phase C artifact %s district %s has invalid makeup_pct '
                'sector keys' % (path, geo)
            )
        for sector in CNS_SECTORS:
            _finite_nonnegative(
                makeup.get(sector),
                'Phase C artifact %s district %s makeup_pct.%s' %
                (path, geo, sector),
            )
        largest_sector = max(
            CNS_SECTORS, key=lambda sector: makeup[sector]
        )
        districts[geo] = {
            'total_employment': total_employment,
            'largest_sector': largest_sector,
        }

    if len(districts) != 435:
        raise SystemExit(
            'Phase C artifact %s has %d voting districts; expected 435' %
            (path, len(districts))
        )
    print('loaded Phase C LODES context: 435 districts')
    return districts, naics_to_cns


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


def _large_size_headers(headers, label, fieldnames):
    size_500_999 = headers.get('n500_999')
    if size_500_999 is None:
        raise SystemExit(
            '%s is missing large size-class column n500_999; headers=%r' %
            (label, fieldnames)
        )
    if 'n1000' in headers:
        size_1000 = (headers['n1000'],)
    else:
        family_names = tuple('n1000_%d' % index for index in range(1, 5))
        missing = [name for name in family_names if name not in headers]
        if missing:
            raise SystemExit(
                '%s is missing 1000+ size-class total n1000 or complete '
                'n1000_1..n1000_4 family; missing=%r; headers=%r' %
                (label, missing, fieldnames)
            )
        size_1000 = tuple(headers[name] for name in family_names)
    return size_500_999, size_1000


def _row_cell(row, header, label, line_num):
    value = row.get(header)
    if value is None:
        raise SystemExit(
            '%s row %d is missing column %r' %
            (label, line_num, header)
        )
    return value


def _normalize_fips(value, width, label):
    text = (value or '').strip()
    if not text.isdigit() or len(text) > width:
        raise SystemExit('%s has invalid FIPS component %r' % (label, text))
    return text.zfill(width)


def _parse_nonnegative_int(value, label):
    text = (value or '').strip()
    try:
        number = int(text)
    except ValueError as exc:
        raise SystemExit(
            '%s has invalid integer count %r' % (label, text)
        ) from exc
    if number < 0:
        raise SystemExit('%s has negative count %d' % (label, number))
    return number


def _parse_count_cell(row, header, label, line_num):
    text = _row_cell(row, header, label, line_num).strip()
    if not text or text.upper() == 'N':
        return UNKNOWN
    return _parse_nonnegative_int(
        text, '%s row %d column %s' % (label, line_num, header)
    )


def _classify_naics(value):
    text = (value or '').strip()
    if text == CBP_TOTAL_NAICS:
        return 'total', CBP_TOTAL_NAICS
    normalized_range = RANGE_NAICS_ALIASES.get(text)
    if normalized_range is not None:
        return '2digit', normalized_range
    if text.endswith('-'):
        normalized_range = RANGE_NAICS_ALIASES.get(text[:-1])
        if normalized_range is not None:
            return '2digit', normalized_range
    if NAICS_2_RE.fullmatch(text):
        return '2digit', text[:2]
    if NAICS_3_RE.fullmatch(text):
        return 'other', text[:3]
    if NAICS_4_RE.fullmatch(text):
        return 'other', text[:4]
    if NAICS_5_RE.fullmatch(text):
        return 'other', text[:5]
    if NAICS_6_RE.fullmatch(text):
        return 'other', text
    return None, text


def _single_archive_member(archive, label):
    members = [
        member for member in archive.namelist()
        if member and not member.endswith('/')
    ]
    if len(members) != 1:
        raise SystemExit(
            '%s archive has %d non-directory members; members=%r' %
            (label, len(members), archive.namelist())
        )
    return members[0]


def _parse_cbp_county_archive(path, naics_to_cns):
    label = 'cached CBP county %s' % CBP_YEAR
    if not os.path.exists(path):
        raise SystemExit('%s archive does not exist: %s' % (label, path))
    records = {}
    unknown_naics = set()
    stats = {
        'data_rows': 0,
        'retained_rows': 0,
        'lfo_skipped': 0,
        'n_sizeclass_unknown_rows': 0,
        'n_sizeclass_unknown_cells': 0,
    }
    try:
        archive = zipfile.ZipFile(path, 'r')
    except (OSError, zipfile.BadZipFile) as exc:
        raise SystemExit(
            'failed to open %s archive %s: %s' % (label, path, str(exc))
        ) from exc

    with archive:
        member = _single_archive_member(archive, label)
        print('parsing %s member: %s' % (label, member))
        with io.TextIOWrapper(
            archive.open(member, 'r'),
            encoding='utf-8-sig',
            errors='strict',
            newline='',
        ) as source:
            reader = csv.DictReader(source)
            fieldnames = reader.fieldnames or []
            headers = _header_map(fieldnames, label)
            state_header = _required_header(
                headers, 'fipstate', label, fieldnames
            )
            county_header = _required_header(
                headers, 'fipscty', label, fieldnames
            )
            naics_header = _required_header(
                headers, 'naics', label, fieldnames
            )
            size_500_header, size_1000_headers = _large_size_headers(
                headers, label, fieldnames
            )
            lfo_header = headers.get('lfo')

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
                if level == 'other':
                    continue
                if level == '2digit' and naics == '99':
                    continue
                if level == '2digit' and naics not in naics_to_cns:
                    raise SystemExit(
                        '%s row %d has unmapped 2-digit NAICS %r' %
                        (label, reader.line_num, raw_naics)
                    )

                state_fips = _normalize_fips(
                    _row_cell(
                        row, state_header, label, reader.line_num
                    ),
                    2,
                    '%s row %d fipstate' % (label, reader.line_num),
                )
                county_fips = _normalize_fips(
                    _row_cell(
                        row, county_header, label, reader.line_num
                    ),
                    3,
                    '%s row %d fipscty' % (label, reader.line_num),
                )
                county = state_fips + county_fips
                size_500_999 = _parse_count_cell(
                    row, size_500_header, label, reader.line_num
                )
                size_1000_values = [
                    _parse_count_cell(
                        row, header, label, reader.line_num
                    )
                    for header in size_1000_headers
                ]
                values = [size_500_999] + size_1000_values
                unknown_cells = sum(
                    1 for value in values if value is UNKNOWN
                )
                if unknown_cells:
                    stats['n_sizeclass_unknown_rows'] += 1
                    stats['n_sizeclass_unknown_cells'] += unknown_cells
                known_500_999 = (
                    0 if size_500_999 is UNKNOWN else size_500_999
                )
                known_1000 = sum(
                    0 if value is UNKNOWN else value
                    for value in size_1000_values
                )
                record = {
                    'est_500plus': known_500_999 + known_1000,
                    'est_1000plus': known_1000,
                    'complete': unknown_cells == 0,
                }
                key = (
                    'total' if level == 'total' else naics_to_cns[naics]
                )
                by_key = records.setdefault(county, {})
                if key in by_key:
                    raise SystemExit(
                        '%s row %d duplicates county/sector %s/%s' %
                        (label, reader.line_num, county, key)
                    )
                by_key[key] = record
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
        raise SystemExit('%s has no retained county rows' % label)
    print(
        'parsed %s: %d retained, LFO skipped = %d, '
        'unknown size-class rows = %d' %
        (
            label,
            stats['retained_rows'],
            stats['lfo_skipped'],
            stats['n_sizeclass_unknown_rows'],
        )
    )
    return records, stats


def _load_county_weights(path, districts):
    if not os.path.exists(path):
        raise SystemExit('county/CD weights do not exist: %s' % path)
    weights = {}
    seen = set()
    print('loading county/CD weights: %s' % path)
    try:
        compressed = gzip.open(path, 'rb')
        with compressed:
            with io.TextIOWrapper(
                compressed,
                encoding='utf-8-sig',
                errors='strict',
                newline='',
            ) as source:
                reader = csv.DictReader(source)
                expected = ['county_fips', 'cd', 'weight', 'employment']
                if reader.fieldnames != expected:
                    raise SystemExit(
                        'invalid county/CD weight header in %s: '
                        'expected %r; found %r' %
                        (path, expected, reader.fieldnames)
                    )
                for row in reader:
                    if None in row:
                        raise SystemExit(
                            '%s row %d has surplus field(s): %r' %
                            (path, reader.line_num, row[None])
                        )
                    county = (row.get('county_fips') or '').strip()
                    if len(county) != 5 or not county.isdigit():
                        raise SystemExit(
                            '%s row %d has invalid county_fips %r' %
                            (path, reader.line_num, county)
                        )
                    district = (row.get('cd') or '').strip()
                    if district not in districts:
                        raise SystemExit(
                            '%s row %d has unknown district %r' %
                            (path, reader.line_num, district)
                        )
                    weight_text = (row.get('weight') or '').strip()
                    try:
                        weight = float(weight_text)
                    except ValueError as exc:
                        raise SystemExit(
                            '%s row %d has invalid weight %r' %
                            (path, reader.line_num, weight_text)
                        ) from exc
                    if (
                        not math.isfinite(weight) or
                        weight <= 0 or
                        weight > 1
                    ):
                        raise SystemExit(
                            '%s row %d has invalid weight %r' %
                            (path, reader.line_num, weight_text)
                        )
                    key = (county, district)
                    if key in seen:
                        raise SystemExit(
                            '%s row %d duplicates county/CD %s/%s' %
                            (path, reader.line_num, county, district)
                        )
                    seen.add(key)
                    weights.setdefault(county, []).append(
                        (district, weight)
                    )
    except (OSError, EOFError, gzip.BadGzipFile, UnicodeError) as exc:
        raise SystemExit(
            'failed to load county/CD weights %s: %s' %
            (path, str(exc))
        ) from exc

    for county, county_weights in weights.items():
        total = sum(weight for _district, weight in county_weights)
        if total > 1.0 + WEIGHT_SUM_TOLERANCE:
            raise SystemExit(
                'county/CD weights for %s sum above 1.0 + 1e-4: %.12f' %
                (county, total)
            )
    if not weights:
        raise SystemExit('county/CD weights are empty: %s' % path)
    print(
        'loaded county/CD weights: %d rows, %d counties' %
        (len(seen), len(weights))
    )
    return weights


def _round_metric(value):
    rounded = round(value, 1)
    return 0.0 if rounded == 0 else rounded


def _allocate_cbp(cbp_records, county_weights, districts):
    allocated = {
        geo: {
            'by_sector': defaultdict(
                lambda: {
                    'est_500plus': 0.0,
                    'est_1000plus': 0.0,
                    'complete': True,
                }
            ),
            'total': {
                'est_500plus': 0.0,
                'est_1000plus': 0.0,
                'complete': True,
            },
        }
        for geo in districts
    }
    counties_allocated = 0
    counties_unmatched = 0
    unallocated_est_500plus = 0.0
    total_est_500plus = 0.0
    for county, by_key in cbp_records.items():
        if county[:2] not in VOTING_FIPS:
            # DC/PR (and any other non-voting jurisdiction) counties have
            # no congressional-district crosswalk row by design -- the
            # weight table is built only for VOTING_STATES -- so they are
            # not a coverage gap and must not be booked as unallocated
            # loss. Mirrors fetch_cbp_4digit.py:1314.
            continue
        total_row = by_key.get('total')
        if total_row is None:
            raise SystemExit(
                'voting-state CBP county %s has no total row' % county
            )
        county_est = float(total_row.get('est_500plus') or 0.0)
        total_est_500plus += county_est
        weights = county_weights.get(county)
        if not weights:
            # A CBP county with no crosswalk row contributes nothing to any
            # district. That is a coverage GAP and must be measured, not
            # silently absorbed as a zero.
            counties_unmatched += 1
            unallocated_est_500plus += county_est
            continue
        weight_sum = sum(weight for _geo, weight in weights)
        unallocated_est_500plus += county_est * max(0.0, 1.0 - weight_sum)
        counties_allocated += 1
        for geo, weight in weights:
            target = allocated[geo]
            for key, record in by_key.items():
                if key == 'total':
                    metric_target = target['total']
                else:
                    metric_target = target['by_sector'][key]
                metric_target['est_500plus'] += (
                    record['est_500plus'] * weight
                )
                metric_target['est_1000plus'] += (
                    record['est_1000plus'] * weight
                )
                if not record['complete']:
                    metric_target['complete'] = False

    result = {}
    nonzero_districts = 0
    n_district_totals_incomplete = 0
    n_district_sector_allocations_incomplete = 0
    n_output_sector_entries_incomplete = 0
    for geo in sorted(districts):
        raw = allocated[geo]
        total = {
            metric: _round_metric(raw['total'][metric])
            for metric in ('est_500plus', 'est_1000plus')
        }
        total['complete'] = raw['total']['complete']
        if not total['complete']:
            n_district_totals_incomplete += 1
        by_sector = {}
        for sector in CNS_SECTORS:
            raw_sector = raw['by_sector'].get(sector)
            if raw_sector is None:
                continue
            if not raw_sector['complete']:
                n_district_sector_allocations_incomplete += 1
            sector_metrics = {
                metric: _round_metric(raw_sector[metric])
                for metric in ('est_500plus', 'est_1000plus')
            }
            if any(value != 0.0 for value in sector_metrics.values()):
                sector_metrics['complete'] = raw_sector['complete']
                by_sector[sector] = sector_metrics
                if not raw_sector['complete']:
                    n_output_sector_entries_incomplete += 1
        result[geo] = {
            'by_sector': by_sector,
            'total': total,
        }
        if (
            total['est_500plus'] != 0.0 or
            total['est_1000plus'] != 0.0
        ):
            nonzero_districts += 1

    unallocated_est_share = (
        round(unallocated_est_500plus / total_est_500plus, 4)
        if total_est_500plus > 0 else None
    )
    print(
        'allocated CBP large establishments: %d counties, '
        '%d districts nonzero' %
        (counties_allocated, nonzero_districts)
    )
    print(
        '!! CBP counties with no county/CD weight row: %d; '
        'unallocated est_500plus share = %r' %
        (counties_unmatched, unallocated_est_share)
    )
    allocation_stats = {
        'n_cbp_counties_unmatched_in_weights': counties_unmatched,
        'unallocated_est_500plus_share': unallocated_est_share,
        'unallocated_scope_note': (
            'DC/PR counties are by-design unallocated and excluded '
            'from allocation-share denominators'
        ),
        'n_district_totals_incomplete':
            n_district_totals_incomplete,
        'n_district_sector_allocations_incomplete':
            n_district_sector_allocations_incomplete,
        'n_output_sector_entries_incomplete':
            n_output_sector_entries_incomplete,
    }
    return result, nonzero_districts, allocation_stats


def _wait_for_request_start():
    global _LAST_REQUEST_START
    with _REQUEST_START_LOCK:
        now = time.monotonic()
        delay = (
            _LAST_REQUEST_START + REQUEST_START_INTERVAL - now
        )
        if delay > 0:
            time.sleep(delay)
        _LAST_REQUEST_START = time.monotonic()


def _canonical_request_body(payload):
    return json.dumps(
        payload,
        separators=(',', ':'),
        sort_keys=True,
        ensure_ascii=True,
    ).encode('utf-8')


def _request_usaspending(body, label):
    failure = None
    for attempt in range(DOWNLOAD_ATTEMPTS):
        try:
            request = urllib.request.Request(
                USASPENDING_ENDPOINT,
                data=body,
                headers={
                    'User-Agent': USER_AGENT,
                    'Content-Type': 'application/json',
                },
                method='POST',
            )
            _wait_for_request_start()
            with urllib.request.urlopen(
                request, timeout=REQUEST_TIMEOUT
            ) as response:
                response_body = response.read()
            value = json.loads(
                response_body.decode('utf-8-sig', errors='strict')
            )
            if not isinstance(value, dict):
                raise ValueError('response is not a JSON object')
            return value
        except Exception as exc:
            failure = exc
            if attempt == DOWNLOAD_ATTEMPTS - 1:
                break
            delay = RETRY_BASE_SECONDS * (attempt + 1)
            print(
                '!! request retry for %s in %d seconds: %s' %
                (label, delay, str(exc))
            )
            time.sleep(delay)
    raise OSError(
        'request failed for %s after %d attempts: %s' %
        (label, DOWNLOAD_ATTEMPTS, str(failure))
    ) from failure


def _recipient_amount(value, label):
    if isinstance(value, bool):
        raise ValueError('%s amount is boolean %r' % (label, value))
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            '%s has invalid amount %r' % (label, value)
        ) from exc
    if not math.isfinite(number) or number < 0:
        raise ValueError(
            '%s has invalid nonnegative amount %r' % (label, value)
        )
    return number


def _recipient_identity(item):
    for field in ('recipient_id', 'uei', 'id', 'code'):
        value = item.get(field)
        if (
            value is None or
            isinstance(value, bool) or
            not str(value).strip()
        ):
            continue
        return str(value).strip()
    return None


def _extract_recipients(payload, label):
    results = payload.get('results')
    if not isinstance(results, list):
        raise ValueError(
            '%s response has no results array; top-level keys=%r' %
            (label, sorted(payload))
        )
    recipients = []
    redacted = 0
    for index, item in enumerate(results[:10], 1):
        item_label = '%s result %d' % (label, index)
        if not isinstance(item, dict):
            raise ValueError('%s is not an object' % item_label)
        name = item.get('name')
        if not isinstance(name, str) or not name.strip():
            raise ValueError('%s has invalid name %r' % (item_label, name))
        name = name.strip()
        if name == REDACTED_RECIPIENT:
            redacted += 1
            continue
        amount = _recipient_amount(item.get('amount'), item_label)
        recipient_id = _recipient_identity(item)
        recipients.append({
            'name': name,
            'amount': amount,
            'recipient_id': recipient_id,
        })
    return recipients, redacted


def _validate_cache_timestamp(value, geo):
    if not isinstance(value, str) or UTC_ISO_RE.fullmatch(value) is None:
        raise ValueError(
            'USAspending cache for %s has invalid retrieved_at %r' %
            (geo, value)
        )
    try:
        datetime.datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ')
    except ValueError as exc:
        raise ValueError(
            'USAspending cache for %s has invalid retrieved_at %r' %
            (geo, value)
        ) from exc
    return value


def _load_usaspending_cache(path, geo, request_digest):
    try:
        value = _load_json(path, 'USAspending cache for %s' % geo)
    except SystemExit as exc:
        raise ValueError(str(exc)) from exc
    if value.get('cache_schema') != 'usaspending-district-v1':
        raise ValueError(
            'invalid cache_schema %r' % value.get('cache_schema')
        )
    if value.get('district') != geo:
        raise ValueError(
            'district mismatch: expected %s, found %r' %
            (geo, value.get('district'))
        )
    if value.get('request_sha256') != request_digest:
        raise ValueError(
            'request digest mismatch: expected %s, found %r' %
            (request_digest, value.get('request_sha256'))
        )
    retrieved_at = _validate_cache_timestamp(
        value.get('retrieved_at'), geo
    )
    payload = value.get('response')
    if not isinstance(payload, dict):
        raise ValueError('cache has no response object')
    try:
        recipients, redacted = _extract_recipients(
            payload, 'cached USAspending %s' % geo
        )
    except ValueError as exc:
        raise ValueError('invalid cached response: %s' % str(exc)) from exc
    return recipients, redacted, retrieved_at


def _collect_usaspending_district(
    geo, fy, period, cache_root, force
):
    state, district = geo.split('-')
    payload = {
        'category': 'recipient',
        'filters': {
            'time_period': [dict(period)],
            'place_of_performance_locations': [{
                'country': 'USA',
                'state': state,
                'district_current': district,
            }],
        },
        'limit': 10,
        'page': 1,
    }
    request_body = _canonical_request_body(payload)
    request_digest = hashlib.sha256(request_body).hexdigest()
    cache_path = os.path.join(cache_root, '%s.json' % geo)
    if os.path.exists(cache_path) and not force:
        try:
            recipients, redacted, retrieved_at = (
                _load_usaspending_cache(
                    cache_path, geo, request_digest
                )
            )
        except ValueError as exc:
            print(
                '!! invalid USAspending cache for %s; re-fetching: %s' %
                (geo, str(exc))
            )
        else:
            return {
                'geo': geo,
                'recipients': recipients,
                'redacted': redacted,
                'cached': True,
                'retrieved_at': retrieved_at,
            }

    response = _request_usaspending(
        request_body, 'USAspending %s' % geo
    )
    retrieved_at = _utc_iso()
    recipients, redacted = _extract_recipients(
        response, 'USAspending %s' % geo
    )
    cache_value = {
        'cache_schema': 'usaspending-district-v1',
        'district': geo,
        'fy': int(fy),
        'retrieved_range': dict(period),
        'request_sha256': request_digest,
        'retrieved_at': retrieved_at,
        'response': response,
    }
    dump_atomic(
        cache_value, cache_path, separators=(',', ':'), sort_keys=True
    )
    return {
        'geo': geo,
        'recipients': recipients,
        'redacted': redacted,
        'cached': False,
        'retrieved_at': retrieved_at,
    }


def _collect_usaspending(districts, fy, period, cache_root, force):
    os.makedirs(cache_root, exist_ok=True)
    geos = sorted(districts)
    top_federal = {
        geo: {
            'status': 'failed',
            'error': 'USAspending collection did not complete',
        }
        for geo in geos
    }
    stats = {
        'ok': 0,
        'fail': 0,
        'empty': 0,
        'redacted': 0,
        'cached': 0,
        'retrieved_min': None,
        'retrieved_max': None,
    }
    retrieved_times = []
    print(
        'collecting USAspending FY%s top recipients: %d districts, '
        '%d workers, %.2fs request-start interval' %
        (fy, len(geos), MAX_WORKERS, REQUEST_START_INTERVAL)
    )
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:
        future_to_geo = {
            executor.submit(
                _collect_usaspending_district,
                geo,
                fy,
                period,
                cache_root,
                force,
            ): geo
            for geo in geos
        }
        completed = 0
        try:
            for future in concurrent.futures.as_completed(future_to_geo):
                geo = future_to_geo[future]
                completed += 1
                try:
                    result = future.result()
                except Exception as exc:
                    stats['fail'] += 1
                    error = str(exc) or exc.__class__.__name__
                    top_federal[geo] = {
                        'status': 'failed',
                        'error': error,
                    }
                    print('!! %s USAspending failed: %s' % (geo, error))
                else:
                    recipients = result['recipients']
                    top_federal[geo] = recipients
                    stats['ok'] += 1
                    stats['redacted'] += result['redacted']
                    stats['cached'] += int(result['cached'])
                    retrieved_times.append(result['retrieved_at'])
                    if not recipients:
                        stats['empty'] += 1
                        print(
                            '!! %s USAspending returned no recipients' % geo
                        )
                if completed % 25 == 0 or completed == len(geos):
                    print(
                        'USAspending progress: %d/%d, ok/fail %d/%d' %
                        (
                            completed,
                            len(geos),
                            stats['ok'],
                            stats['fail'],
                        )
                    )
        except BaseException:
            for future in future_to_geo:
                future.cancel()
            raise
    print(
        'USAspending complete: ok/fail %d/%d, cached %d, '
        'empty %d, redacted skipped %d' %
        (
            stats['ok'],
            stats['fail'],
            stats['cached'],
            stats['empty'],
            stats['redacted'],
        )
    )
    if retrieved_times:
        stats['retrieved_min'] = min(retrieved_times)
        stats['retrieved_max'] = max(retrieved_times)
    return top_federal, stats


def _queue_rows():
    rows = []
    for state in QUEUE_JURISDICTIONS:
        if state == 'UT':
            row = [
                state,
                'Utah Department of Workforce Services major employers',
                (
                    'https://jobs.utah.gov/wi/data/library/firm/'
                    'majoremployers.html'
                ),
                'interactive dashboard',
                'pending',
                'interactive dashboard only',
            ]
        elif state == 'CA':
            row = [
                state,
                'California Employment Development Department',
                'https://labormarketinfo.edd.ca.gov/',
                'web UI',
                'pending',
                'TLS misconfiguration on labormarketinfo.edd.ca.gov',
            ]
        elif state == 'MD':
            row = [
                state,
                'Maryland Department of Labor employer lists',
                'https://labor.maryland.gov/lmi/emplists/',
                'HTML',
                'pending',
                'county HTML lists',
            ]
        elif state in ('DC', 'PR'):
            row = [
                state,
                '',
                '',
                '',
                'pending',
                'state UI-derived list not yet sourced; non-voting placeholder',
            ]
        else:
            row = [
                state,
                '',
                '',
                '',
                'pending',
                'state UI-derived list not yet sourced',
            ]
        rows.append(row)
    if len(rows) != 52:
        raise SystemExit(
            'internal error: employer queue has %d data rows; expected 52' %
            len(rows)
        )
    return rows


def _write_queue(path):
    header = [
        'state', 'source_name', 'url', 'format', 'status', 'notes'
    ]
    rows = _queue_rows()
    stream = io.StringIO(newline='')
    writer = csv.writer(stream, lineterminator='\n')
    writer.writerow(header)
    writer.writerows(rows)
    write_text_atomic(stream.getvalue(), path, encoding='utf-8')
    n_pending = sum(1 for row in rows if row[4] == 'pending')
    print(
        'wrote employer collection queue: %d rows, %d pending at %s' %
        (len(rows), n_pending, path)
    )
    return n_pending


def main():
    _configure_stdout()
    data_root = os.environ.get('DEMO_DATA_ROOT')
    if not data_root:
        base = (
            os.environ.get('LOCALAPPDATA') or
            os.path.expanduser(r'~\AppData\Local')
        )
        data_root = os.path.join(base, 'Melange', 'demographics')

    fy = _parse_year(os.environ.get('EMP_FY', '2025'))
    period = _fiscal_period(fy)
    force = os.environ.get('EMP_FORCE', '0') == '1'
    output_path = os.environ.get('EMP_OUT')
    if not output_path:
        output_path = os.path.join(MB_ROOT, 'data', 'district', 'district_employers.json')

    cbp_path = os.path.join(data_root, 'cbp', 'cbp23co.zip')
    weight_path = os.path.join(
        data_root, 'geo', 'county_cd_weights.csv.gz'
    )
    cache_root = os.path.join(
        data_root, 'employers', 'usaspending'
    )
    phase_c_path = os.path.join(MB_ROOT, 'data', 'lobbying', 'district_industry.json')
    queue_path = os.path.join(
        MB_ROOT, 'data', 'district', 'employer_collection_queue.csv'
    )

    print(
        'employer Phase G: USAspending FY%s, CBP %s; data root %s' %
        (fy, CBP_YEAR, data_root)
    )
    print('loading Phase C artifact: %s' % phase_c_path)
    districts, naics_to_cns = _validate_phase_c(
        _load_json(phase_c_path, 'Phase C artifact'),
        phase_c_path,
    )

    print('loading cached CBP county archive')
    cbp_records, cbp_parse_stats = _parse_cbp_county_archive(
        cbp_path, naics_to_cns
    )
    county_weights = _load_county_weights(weight_path, districts)
    (
        large_establishments,
        cbp_nonzero,
        cbp_allocation_stats,
    ) = _allocate_cbp(cbp_records, county_weights, districts)

    top_federal, usaspending_stats = _collect_usaspending(
        districts, fy, period, cache_root, force
    )

    n_pending = _write_queue(queue_path)
    artifact_districts = {}
    for geo in sorted(districts):
        artifact_districts[geo] = {
            'top_federal': top_federal[geo],
            'large_establishments': large_establishments[geo],
            'lodes_context': dict(districts[geo]),
        }

    generated_at = _utc_iso()
    artifact = {
        'schema': 'district-employers-v1',
        'generated_at': generated_at,
        'sources': {
            'usaspending': {
                'fy': int(fy),
                'endpoint': USASPENDING_ENDPOINT,
                'retrieved_range': dict(period),
                'retrieved_min': usaspending_stats['retrieved_min'],
                'retrieved_max': usaspending_stats['retrieved_max'],
            },
            'cbp': {
                'year': int(CBP_YEAR),
                'from_cache': True,
            },
        },
        'method_notes': {
            'usaspending': (
                'place-of-performance top-10 federal recipients FY%s; '
                'booking/HQ caveats apply' % fy
            ),
            'cbp_large': (
                'county establishment size classes allocated by LODES '
                'employment weights; unknown size-class cells contribute '
                'zero as lower bounds and set complete=false'
            ),
            'almis': (
                'employer figures vs LODES context is a display-side '
                'comparison'
            ),
        },
        'districts': artifact_districts,
        'queue_summary': {
            'n_pending': n_pending,
            'automated_layers': 2,
        },
        'stats': {
            'usaspending': {
                'ok': usaspending_stats['ok'],
                'fail': usaspending_stats['fail'],
                'empty': usaspending_stats['empty'],
                'cached': usaspending_stats['cached'],
                'redacted_skipped': usaspending_stats['redacted'],
            },
            'cbp_suppression': {
                'n_sizeclass_unknown_rows':
                    cbp_parse_stats['n_sizeclass_unknown_rows'],
                'n_sizeclass_unknown_cells':
                    cbp_parse_stats['n_sizeclass_unknown_cells'],
                'n_district_totals_incomplete':
                    cbp_allocation_stats[
                        'n_district_totals_incomplete'
                    ],
                'n_district_sector_allocations_incomplete':
                    cbp_allocation_stats[
                        'n_district_sector_allocations_incomplete'
                    ],
                'n_output_sector_entries_incomplete':
                    cbp_allocation_stats[
                        'n_output_sector_entries_incomplete'
                    ],
            },
        },
    }
    print('writing Phase G artifact: %s' % output_path)
    dump_atomic(artifact, output_path, separators=(',', ':'))
    coverage_failed = (
        usaspending_stats['fail'] > MAX_USASPENDING_FAILURES
    )
    if coverage_failed:
        print(
            '!! USAspending coverage floor failed: %d failures exceeds %d' %
            (
                usaspending_stats['fail'],
                MAX_USASPENDING_FAILURES,
            )
        )
    print(
        'wrote district_employers.json: 435 districts, '
        'usaspending ok/fail %d/%d, cbp large-est %d districts nonzero, '
        'queue %d pending' %
        (
            usaspending_stats['ok'],
            usaspending_stats['fail'],
            cbp_nonzero,
            n_pending,
        )
    )
    if coverage_failed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()

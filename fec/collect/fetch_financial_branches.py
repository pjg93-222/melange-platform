# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT  # centralized root (arch review 2026-07-02)
"""FINANCIAL BRANCH COLLECTOR/BUILDER — FDIC and NCUA branches by CD119.

Standalone Phase H1 collector/builder.  This script is deliberately not
chained into run_pipeline.py.  It mirrors FDIC Summary of Deposits and NCUA
call-report data, batch-geocodes branch addresses to Census blocks, assigns
blocks through the Phase A CD119 crosswalk, and builds district-financial-v1.

Inputs:
  DEMO_DATA_ROOT/geo/cd119_block_assign.csv.gz
  DEMO_DATA_ROOT/geo/county_cd_weights.csv.gz
  DEMO_DATA_ROOT/validate/CAEMP25N.zip

Raw/cache outputs:
  DEMO_DATA_ROOT/financial/fdic_sod_{year}.jsonl.gz
  DEMO_DATA_ROOT/financial/call-report-data-2026-03.zip
  DEMO_DATA_ROOT/financial/geocode_cache/

Repo-plane output:
  MB_ROOT/district_financial.json, unless FIN_OUT overrides the path.

Dials:
  DEMO_DATA_ROOT — out-of-OneDrive raw-data root; defaults to
                   %LOCALAPPDATA%/Melange/demographics
  SOD_YEAR       — FDIC SOD vintage; defaults to "2025"
  FIN_FORCE      — set to "1" to refresh sources and geocoder batches
  FIN_OUT        — optional output-path override
"""
import csv
import concurrent.futures
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
import zlib
from collections import defaultdict

from moneyball_core import dump_atomic


USER_AGENT = 'MelangeDemographics/1.0 (research; contact: repo owner)'
CHUNK_SIZE = 1024 * 1024
REQUEST_TIMEOUT = 120
# Census batch geocoder takes minutes per 5000-row POST; 120 s re-POSTs it.
GEOCODER_TIMEOUT = 600
DOWNLOAD_ATTEMPTS = 4
RETRY_BASE_SECONDS = 3
GEOCODE_BATCH_SIZE = 5000

FDIC_FIELDS = (
    'CERT', 'NAMEFULL', 'ADDRESBR', 'CITYBR', 'STALPBR', 'ZIPBR',
    'STCNTYBR', 'DEPSUMBR', 'BRNUM', 'UNINUMBR',
)
FDIC_REQUIRED_FIELDS = frozenset(FDIC_FIELDS)
FDIC_URL_TEMPLATE = (
    'https://api.fdic.gov/banks/sod?filters=YEAR:{year}'
    '&fields=' + ','.join(FDIC_FIELDS) +
    '&limit=10000&offset={offset}&format=json'
    # FDIC BankFind API sort_by/sort_order: stable order for offset paging
    '&sort_by=UNINUMBR&sort_order=ASC'
)
NCUA_URL = (
    'https://ncua.gov/files/publications/analysis/'
    'call-report-data-2026-03.zip'
)
NCUA_QUARTER = '2026-Q1'
GEOCODER_URL = (
    'https://geocoding.geo.census.gov/geocoder/'
    'geographies/addressbatch'
)
GEOCODER_BENCHMARK = 'Public_AR_Current'
GEOCODER_VINTAGE = 'Census2020_Current'

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
VOTING_STATES = frozenset((
    'AK', 'AL', 'AR', 'AZ', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA',
    'HI', 'IA', 'ID', 'IL', 'IN', 'KS', 'KY', 'LA', 'MA', 'MD',
    'ME', 'MI', 'MN', 'MO', 'MS', 'MT', 'NC', 'ND', 'NE', 'NH',
    'NJ', 'NM', 'NV', 'NY', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC',
    'SD', 'TN', 'TX', 'UT', 'VA', 'VT', 'WA', 'WI', 'WV', 'WY',
))
PREFILTER_STATES = frozenset((
    'AA', 'AE', 'AP', 'AS', 'FM', 'GU', 'MH', 'MP', 'PR', 'PW', 'VI',
))
UNKNOWN = object()


def _configure_stdout():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')


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


def _sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def _parse_year(value):
    text = (value or '').strip()
    if re.fullmatch(r'[0-9]{4}', text) is None:
        raise SystemExit(
            'invalid SOD_YEAR %r; expected a four-digit year' % value
        )
    return text


def _parse_nonnegative_number(value, label):
    text = str(value if value is not None else '').strip()
    if not text:
        return UNKNOWN
    text = text.replace(',', '').replace('$', '')
    try:
        number = float(text)
    except (TypeError, ValueError) as exc:
        raise SystemExit(
            '%s has invalid numeric value %r' % (label, value)
        ) from exc
    if not math.isfinite(number) or number < 0:
        raise SystemExit(
            '%s has invalid nonnegative value %r' % (label, value)
        )
    return number


def _normalize_digits(value, width, label, allow_blank=False):
    text = str(value if value is not None else '').strip()
    if not text and allow_blank:
        return None
    if len(text) != width or not text.isdigit():
        raise SystemExit(
            '%s has invalid %d-digit value %r' % (label, width, value)
        )
    return text


def _normalize_cu_number(value, label):
    text = str(value if value is not None else '').strip()
    if not text or not text.isdigit():
        raise SystemExit('%s has invalid CU number %r' % (label, value))
    return text


def _zip5(value):
    text = str(value if value is not None else '').strip()
    match = re.match(r'^([0-9]{5})', text)
    return match.group(1) if match else ''


def _discard_part(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def _validate_zip(path, label):
    try:
        with zipfile.ZipFile(path, 'r') as archive:
            bad_member = archive.testzip()
    except (OSError, zipfile.BadZipFile, RuntimeError, zlib.error) as exc:
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


def _request_bytes(request, label, timeout=REQUEST_TIMEOUT):
    failure = None
    for attempt in range(DOWNLOAD_ATTEMPTS):
        try:
            with urllib.request.urlopen(
                request, timeout=timeout
            ) as response:
                return response.read()
        except Exception as exc:
            if (
                isinstance(exc, urllib.error.HTTPError) and
                400 <= exc.code < 500 and exc.code != 429
            ):
                # permanent client error: retrying cannot change it
                try:
                    snippet = exc.read()[:200]
                except Exception:
                    snippet = b''
                raise SystemExit(
                    'request failed for %s: HTTP %d: %s' %
                    (label, exc.code, snippet.decode('utf-8', 'replace'))
                ) from exc
            failure = exc
            if attempt == DOWNLOAD_ATTEMPTS - 1:
                break
            delay = RETRY_BASE_SECONDS * (attempt + 1)
            print(
                '!! request retry for %s in %d seconds: %s' %
                (label, delay, str(exc))
            )
            time.sleep(delay)
    raise SystemExit(
        'request failed for %s after %d attempts: %s' %
        (label, DOWNLOAD_ATTEMPTS, str(failure))
    ) from failure


def _download_file(label, url, path, force, integrity=None):
    if os.path.exists(path) and not force:
        try:
            if integrity == 'zip':
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
    request = urllib.request.Request(
        url, headers={'User-Agent': USER_AGENT}
    )
    try:
        print('downloading %s: %s' % (label, url))
        body = _request_bytes(request, label)
        with open(part_path, 'wb') as target:
            target.write(body)
        if integrity == 'zip':
            _validate_zip(part_path, label)
        os.replace(part_path, path)
    except BaseException:
        _discard_part(part_path)
        raise
    print('downloaded %s: %s' % (label, path))
    return {
        'url': url,
        'sha256': _sha256(path),
        'retrieved': _utc_iso(),
    }


def _extract_fdic_rows(payload, label):
    if not isinstance(payload, dict):
        raise SystemExit('%s response is not a JSON object' % label)
    rows = payload.get('data')
    if not isinstance(rows, list):
        raise SystemExit(
            '%s response data is not an array; observed top-level keys=%r' %
            (label, sorted(payload))
        )
    normalized = []
    for index, wrapper in enumerate(rows, 1):
        if not isinstance(wrapper, dict):
            raise SystemExit(
                '%s data row %d is not an object' % (label, index)
            )
        if 'data' in wrapper:
            row = wrapper.get('data')
            if not isinstance(row, dict):
                raise SystemExit(
                    '%s data row %d nested data is not an object; '
                    'observed keys=%r' % (label, index, sorted(wrapper))
                )
        else:
            row = wrapper
        missing = sorted(FDIC_REQUIRED_FIELDS - set(row))
        if missing:
            raise SystemExit(
                '%s data row %d missing required field(s) %s; '
                'observed keys=%r' %
                (label, index, ', '.join(missing), sorted(row))
            )
        normalized.append({
            field: row.get(field) for field in FDIC_FIELDS
        })
    return normalized


def _extract_fdic_total(payload, label):
    meta = payload.get('meta')
    if not isinstance(meta, dict) or 'total' not in meta:
        raise SystemExit(
            '%s response is missing meta.total; observed top-level keys=%r; '
            'meta=%r' % (label, sorted(payload), meta)
        )
    text = str(meta.get('total')).strip().replace(',', '')
    if not text.isdigit():
        raise SystemExit('%s has invalid meta.total %r' % (label, meta))
    return int(text)


def _get_fdic_page(year, offset):
    url = FDIC_URL_TEMPLATE.format(year=year, offset=offset)
    request = urllib.request.Request(
        url, headers={'User-Agent': USER_AGENT}
    )
    body = _request_bytes(
        request, 'FDIC SOD %s offset %d' % (year, offset)
    )
    try:
        payload = json.loads(body.decode('utf-8-sig', errors='strict'))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise SystemExit(
            'FDIC SOD %s offset %d returned invalid JSON: %s' %
            (year, offset, str(exc))
        ) from exc
    rows = _extract_fdic_rows(
        payload, 'FDIC SOD %s offset %d' % (year, offset)
    )
    total = _extract_fdic_total(
        payload, 'FDIC SOD %s offset %d' % (year, offset)
    )
    return rows, total


def _load_fdic_records(path):
    records = []
    try:
        with gzip.open(
            path, 'rt', encoding='utf-8', errors='strict', newline=''
        ) as source:
            for line_num, line in enumerate(source, 1):
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise SystemExit(
                        'invalid FDIC JSONL %s line %d: %s' %
                        (path, line_num, str(exc))
                    ) from exc
                if not isinstance(row, dict):
                    raise SystemExit(
                        'FDIC JSONL %s line %d is not an object' %
                        (path, line_num)
                    )
                missing = sorted(FDIC_REQUIRED_FIELDS - set(row))
                if missing:
                    raise SystemExit(
                        'FDIC JSONL %s line %d missing %s; '
                        'observed keys=%r' %
                        (path, line_num, ', '.join(missing), sorted(row))
                    )
                records.append(row)
    except (OSError, EOFError, gzip.BadGzipFile, UnicodeError) as exc:
        raise SystemExit(
            'failed to load FDIC SOD cache %s: %s' % (path, str(exc))
        ) from exc
    if not records:
        raise SystemExit('FDIC SOD cache has no records: %s' % path)
    seen_uninumbr = set()
    duplicate_uninumbr = set()
    n_blank_uninumbr = 0
    for row in records:
        raw_uninumbr = row.get('UNINUMBR')
        uninumbr = str(
            '' if raw_uninumbr is None else raw_uninumbr
        ).strip()
        if not uninumbr:
            # blank is missing data, not a duplicate key
            n_blank_uninumbr += 1
        elif uninumbr in seen_uninumbr:
            duplicate_uninumbr.add(uninumbr)
        else:
            seen_uninumbr.add(uninumbr)
    if n_blank_uninumbr:
        print('!! FDIC SOD rows with blank UNINUMBR: %d' % n_blank_uninumbr)
    if duplicate_uninumbr:
        raise SystemExit(
            'FDIC SOD has duplicate UNINUMBR value(s): %s' %
            ', '.join(repr(value) for value in sorted(
                duplicate_uninumbr
            ))
        )
    return records


def _collect_fdic(year, path, force):
    if os.path.exists(path) and not force:
        print('cached FDIC SOD %s: %s' % (year, path))
        records = _load_fdic_records(path)
        return records, {
            'url': FDIC_URL_TEMPLATE.format(year=year, offset=0),
            'sha256': _sha256(path),
            'retrieved': _utc_iso(os.path.getmtime(path)),
            'year': int(year),
            'total_count': len(records),
        }

    part_path = path + '.part'
    accumulated = 0
    expected_total = None
    try:
        with gzip.open(
            part_path, 'wt', encoding='utf-8', errors='strict', newline=''
        ) as target:
            while expected_total is None or accumulated < expected_total:
                print(
                    'downloading FDIC SOD %s offset %d' %
                    (year, accumulated)
                )
                rows, page_total = _get_fdic_page(year, accumulated)
                if expected_total is None:
                    expected_total = page_total
                elif page_total != expected_total:
                    raise SystemExit(
                        'FDIC SOD total changed during pagination: '
                        '%d then %d at offset %d' %
                        (expected_total, page_total, accumulated)
                    )
                if not rows and accumulated < expected_total:
                    raise SystemExit(
                        'FDIC SOD returned an empty page at offset %d '
                        'before total %d' % (accumulated, expected_total)
                    )
                for row in rows:
                    target.write(json.dumps(
                        row, ensure_ascii=False, separators=(',', ':')
                    ))
                    target.write('\n')
                accumulated += len(rows)
                if accumulated > expected_total:
                    raise SystemExit(
                        'FDIC SOD pagination exceeded total: %d > %d' %
                        (accumulated, expected_total)
                    )
        if expected_total is None or accumulated != expected_total:
            raise SystemExit(
                'FDIC SOD incomplete pagination: accumulated %d, total %r' %
                (accumulated, expected_total)
            )
        with gzip.open(part_path, 'rb') as validation:
            while validation.read(CHUNK_SIZE):
                pass
        os.replace(part_path, path)
    except BaseException:
        _discard_part(part_path)
        raise

    records = _load_fdic_records(path)
    if len(records) != expected_total:
        raise SystemExit(
            'FDIC SOD cache count %d differs from API total %d' %
            (len(records), expected_total)
        )
    print(
        'downloaded FDIC SOD %s: %d/%d records complete' %
        (year, len(records), expected_total)
    )
    return records, {
        'url': FDIC_URL_TEMPLATE.format(year=year, offset=0),
        'sha256': _sha256(path),
        'retrieved': _utc_iso(),
        'year': int(year),
        'total_count': expected_total,
    }


def _decode_archive_member(archive, member, label):
    try:
        raw = archive.read(member)
    except (KeyError, OSError, RuntimeError) as exc:
        raise SystemExit(
            'failed to read %s member %s: %s' % (label, member, str(exc))
        ) from exc
    try:
        return raw.decode('utf-8-sig', errors='strict')
    except UnicodeError:
        try:
            return raw.decode('cp1252', errors='strict')
        except UnicodeError as exc:
            raise SystemExit(
                '%s member %s is neither UTF-8 nor cp1252' %
                (label, member)
            ) from exc


def _read_archive_table(archive, member, label):
    text = _decode_archive_member(archive, member, label)
    source = io.StringIO(text, newline='')
    reader = csv.DictReader(
        source,
        delimiter=',',
        quotechar='"',
        doublequote=True,
    )
    headers = reader.fieldnames or []
    if not headers:
        raise SystemExit('%s member %s has no header' % (label, member))
    rows = []
    try:
        for row in reader:
            if None in row:
                raise SystemExit(
                    '%s member %s row %d has surplus field(s): %r' %
                    (label, member, reader.line_num, row[None])
                )
            rows.append(row)
    except csv.Error as exc:
        raise SystemExit(
            'failed parsing %s member %s at row %d: %s' %
            (label, member, reader.line_num, str(exc))
        ) from exc
    if not rows:
        raise SystemExit('%s member %s has no data rows' % (label, member))
    return headers, rows


def _header_label(header):
    return (header or '').strip().lower()


def _compact_header(header):
    return re.sub(r'[\s_]+', '', _header_label(header))


def _select_family_header(
    headers, family, pattern, label, preferred_exact=None,
    preferred_prefix=None, warn_on_fallback=False
):
    candidates = [
        header for header in headers
        if re.search(pattern, _compact_header(header), re.IGNORECASE)
    ]
    if preferred_exact is not None:
        preferred = [
            header for header in candidates
            if _header_label(header) == preferred_exact.lower()
        ]
        if len(preferred) == 1:
            return preferred[0]
        if len(preferred) > 1:
            candidates = preferred
    if preferred_prefix is not None:
        preferred = [
            header for header in candidates
            if _compact_header(header).startswith(preferred_prefix)
        ]
        if preferred:
            candidates = preferred
    if len(candidates) != 1:
        reason = 'missing' if not candidates else 'ambiguous'
        raise SystemExit(
            '%s %s %s column; candidates=%r; headers=%r' %
            (label, reason, family, candidates, headers)
        )
    selected = candidates[0]
    if warn_on_fallback and preferred_exact is not None:
        print(
            '!! %s exact %s column %r absent; '
            'using regex fallback %r' %
            (label, family, preferred_exact, selected)
        )
    return selected


def _select_exact_header(headers, expected, label):
    candidates = [
        header for header in headers
        if _header_label(header) == expected.lower()
    ]
    if len(candidates) != 1:
        reason = 'missing' if not candidates else 'ambiguous'
        raise SystemExit(
            '%s %s %s column; headers=%r' %
            (label, reason, expected, headers)
        )
    return candidates[0]


def _discover_ncua_members(archive):
    members = [
        name for name in archive.namelist()
        if name and not name.endswith('/')
    ]
    branch = [name for name in members if re.search(r'branch', name, re.I)]
    if len(branch) != 1:
        raise SystemExit(
            'NCUA archive requires exactly one branch member; '
            'candidates=%r; members=%r' % (branch, members)
        )
    fs220 = [
        name for name in members
        if os.path.basename(name).lower() == 'fs220.txt'
    ]
    if len(fs220) != 1:
        raise SystemExit(
            'NCUA archive requires exactly one FS220 member; '
            'candidates=%r; members=%r' % (fs220, members)
        )
    foicu = [
        name for name in members
        if os.path.basename(name).lower() == 'foicu.txt'
    ]
    if len(foicu) != 1:
        raise SystemExit(
            'NCUA archive requires exactly one FOICU.txt member; '
            'candidates=%r; members=%r' % (foicu, members)
        )
    return {
        'branch': branch[0],
        'fs220': fs220[0],
        'foicu': foicu[0],
        'foicu_candidates': foicu,
    }


def _parse_ncua_names(archive, member):
    headers, rows = _read_archive_table(
        archive, member, 'NCUA FOICU'
    )
    cu_header = _select_family_header(
        headers, 'CU number', r'(cu.?number|cunum|join.?number)',
        'NCUA FOICU', preferred_exact='cu_number'
    )
    name_header = _select_family_header(
        headers, 'CU name', r'(cu.?name|^name$)', 'NCUA FOICU',
        preferred_exact='cu_name'
    )
    names = {}
    for line_num, row in enumerate(rows, 2):
        cu_number = _normalize_cu_number(
            row.get(cu_header), 'NCUA FOICU row %d' % line_num
        )
        name = str(row.get(name_header) or '').strip()
        if not name:
            raise SystemExit(
                'NCUA FOICU row %d has blank CU name for CU %s' %
                (line_num, cu_number)
            )
        if cu_number in names and names[cu_number] != name:
            raise SystemExit(
                'NCUA FOICU duplicates CU %s with conflicting names' %
                cu_number
            )
        names[cu_number] = name
    return names


def _parse_ncua_archive(path):
    try:
        archive = zipfile.ZipFile(path, 'r')
    except (OSError, zipfile.BadZipFile) as exc:
        raise SystemExit('failed to open NCUA archive %s: %s' % (
            path, str(exc)
        )) from exc
    with archive:
        members = _discover_ncua_members(archive)
        print('parsing NCUA branch member: %s' % members['branch'])
        branch_headers, branch_rows = _read_archive_table(
            archive, members['branch'], 'NCUA branches'
        )
        cu_header = _select_family_header(
            branch_headers, 'CU number',
            r'(cu.?number|cunum|join.?number)', 'NCUA branches',
            preferred_exact='cu_number'
        )
        branch_name_family_headers = [
            header for header in branch_headers
            if _compact_header(header) != 'cuname'
        ]
        branch_name_header = _select_family_header(
            branch_name_family_headers, 'branch name',
            r'(site.?name|branch.?name)', 'NCUA branches'
        )
        address_header = _select_family_header(
            branch_headers, 'address',
            r'(address.*line.?1|address1|street|^address$)',
            'NCUA branches',
            preferred_exact='PhysicalAddressLine1',
            preferred_prefix='physical',
            warn_on_fallback=True,
        )
        city_header = _select_family_header(
            branch_headers, 'city', r'city', 'NCUA branches',
            preferred_exact='PhysicalAddressCity',
            preferred_prefix='physical',
            warn_on_fallback=True,
        )
        state_header = _select_family_header(
            branch_headers, 'state', r'state', 'NCUA branches',
            preferred_exact='PhysicalAddressStateCode',
            preferred_prefix='physical',
            warn_on_fallback=True,
        )
        zip_header = _select_family_header(
            branch_headers, 'ZIP', r'(zip|postal)', 'NCUA branches',
            preferred_exact='PhysicalAddressPostalCode',
            preferred_prefix='physical',
            warn_on_fallback=True,
        )
        county_name_header = _select_exact_header(
            branch_headers,
            'PhysicalAddressCountyName2',
            'NCUA branches',
        )

        for sample_index, row in enumerate(branch_rows[:100], 2):
            state = str(row.get(state_header) or '').strip().upper()
            if (
                len(state) != 2 or
                re.fullmatch(r'[A-Z]{2}', state) is None
            ):
                raise SystemExit(
                    'NCUA branches state sanity check failed at row %d: %r; '
                    'headers=%r' % (
                        sample_index, row.get(state_header), branch_headers
                    )
                )

        branches = []
        for line_num, row in enumerate(branch_rows, 2):
            cu_number = _normalize_cu_number(
                row.get(cu_header), 'NCUA branches row %d' % line_num
            )
            branches.append({
                'source': 'ncua',
                'source_id': 'ncua-%09d' % (line_num - 1),
                'institution_id': cu_number,
                'branch_name':
                    str(row.get(branch_name_header) or '').strip(),
                'street': str(row.get(address_header) or '').strip(),
                'city': str(row.get(city_header) or '').strip(),
                'state': str(row.get(state_header) or '').strip().upper(),
                'zip': _zip5(row.get(zip_header)),
                'county_name':
                    str(row.get(county_name_header) or '').strip(),
                'county_fips': None,
                'county_from_name': False,
            })

        print('parsing NCUA FS220 member: %s' % members['fs220'])
        fs_headers, fs_rows = _read_archive_table(
            archive, members['fs220'], 'NCUA FS220'
        )
        fs_cu_header = _select_exact_header(
            fs_headers, 'CU_NUMBER', 'NCUA FS220'
        )
        shares_header = _select_exact_header(
            fs_headers, 'ACCT_018', 'NCUA FS220'
        )
        shares_kusd = {}
        for line_num, row in enumerate(fs_rows, 2):
            cu_number = _normalize_cu_number(
                row.get(fs_cu_header), 'NCUA FS220 row %d' % line_num
            )
            shares = _parse_nonnegative_number(
                row.get(shares_header),
                'NCUA FS220 row %d ACCT_018' % line_num,
            )
            if shares is UNKNOWN:
                raise SystemExit(
                    'NCUA FS220 row %d has UNKNOWN ACCT_018' % line_num
                )
            if cu_number in shares_kusd:
                raise SystemExit(
                    'NCUA FS220 duplicates CU_NUMBER %s' % cu_number
                )
            shares_kusd[cu_number] = shares / 1000.0

        print('parsing NCUA FOICU member: %s' % members['foicu'])
        names = _parse_ncua_names(archive, members['foicu'])
        name_status = 'loaded'

    if not branches:
        raise SystemExit('NCUA branch member produced no branches')
    return {
        'branches': branches,
        'shares_kusd': shares_kusd,
        'names': names,
        'members': {
            'branch': members['branch'],
            'fs220': members['fs220'],
            'foicu': members['foicu'],
        },
        'name_file_status': name_status,
        'name_file_candidates': members['foicu_candidates'],
    }


def _normalize_fdic_records(rows):
    records = []
    for line_num, row in enumerate(rows, 1):
        cert = str(row.get('CERT') or '').strip()
        if not cert or not cert.isdigit():
            raise SystemExit(
                'FDIC SOD row %d has invalid CERT %r' %
                (line_num, row.get('CERT'))
            )
        name = str(row.get('NAMEFULL') or '').strip()
        if not name:
            raise SystemExit(
                'FDIC SOD row %d has blank NAMEFULL' % line_num
            )
        state = str(row.get('STALPBR') or '').strip().upper()
        if (
            state and
            (len(state) != 2 or re.fullmatch(r'[A-Z]{2}', state) is None)
        ):
            raise SystemExit(
                'FDIC SOD row %d has invalid STALPBR %r' %
                (line_num, row.get('STALPBR'))
            )
        county_text = str(row.get('STCNTYBR') or '').strip()
        if (
            not county_text.isdigit() or
            len(county_text) > 5
        ):
            raise SystemExit(
                'FDIC SOD row %d has invalid STCNTYBR %r' %
                (line_num, row.get('STCNTYBR'))
            )
        county_fips = county_text.zfill(5)
        zip_text = str(row.get('ZIPBR') or '').strip()
        if not zip_text.isdigit() or len(zip_text) > 5:
            raise SystemExit(
                'FDIC SOD row %d has invalid ZIPBR %r' %
                (line_num, row.get('ZIPBR'))
            )
        zip_code = zip_text.zfill(5)
        deposits = _parse_nonnegative_number(
            row.get('DEPSUMBR'),
            'FDIC SOD row %d DEPSUMBR' % line_num,
        )
        if deposits is UNKNOWN:
            raise SystemExit(
                'FDIC SOD row %d has UNKNOWN DEPSUMBR' % line_num
            )
        records.append({
            'source': 'fdic',
            'source_id': 'fdic-%09d' % line_num,
            'institution_id': cert,
            'institution_name': name,
            'street': str(row.get('ADDRESBR') or '').strip(),
            'city': str(row.get('CITYBR') or '').strip(),
            'state': state,
            'zip': zip_code,
            'county_fips': county_fips,
            'deposits_kusd': deposits,
        })
    if not records:
        raise SystemExit('FDIC SOD produced no normalized branches')
    return records


def _load_block_crosswalk(path):
    if not os.path.exists(path):
        raise SystemExit('block crosswalk does not exist: %s' % path)
    assignments = {}
    districts = set()
    print('loading block crosswalk: %s' % path)
    try:
        with gzip.open(path, 'rb') as compressed:
            with io.TextIOWrapper(
                compressed, encoding='utf-8-sig', errors='strict',
                newline=''
            ) as source:
                reader = csv.DictReader(source)
                if reader.fieldnames != ['block_geoid', 'cd']:
                    raise SystemExit(
                        'invalid crosswalk header in %s: expected '
                        'block_geoid,cd; found %r' %
                        (path, reader.fieldnames)
                    )
                for row in reader:
                    if None in row:
                        raise SystemExit(
                            '%s row %d has surplus field(s): %r' %
                            (path, reader.line_num, row[None])
                        )
                    block = (row.get('block_geoid') or '').strip()
                    cd = (row.get('cd') or '').strip()
                    if len(block) != 15 or not block.isdigit():
                        raise SystemExit(
                            '%s row %d has invalid block_geoid %r' %
                            (path, reader.line_num, block)
                        )
                    if (
                        len(cd) != 2 or
                        not (cd.isdigit() or cd == 'ZZ')
                    ):
                        raise SystemExit(
                            '%s row %d has invalid cd %r' %
                            (path, reader.line_num, cd)
                        )
                    state_fips = block[:2]
                    state = FIPS_TO_USPS.get(state_fips)
                    if state is None:
                        raise SystemExit(
                            '%s row %d has unmapped state FIPS %r' %
                            (path, reader.line_num, state_fips)
                        )
                    if block in assignments:
                        raise SystemExit(
                            '%s row %d duplicates block_geoid %s' %
                            (path, reader.line_num, block)
                        )
                    assignments[block] = cd
                    if state in VOTING_STATES and cd not in ('98', 'ZZ'):
                        districts.add('%s-%s' % (state, cd))
    except (OSError, EOFError, gzip.BadGzipFile, UnicodeError) as exc:
        raise SystemExit(
            'failed to load block crosswalk %s: %s' % (path, str(exc))
        ) from exc
    if len(districts) != 435:
        raise SystemExit(
            'block crosswalk yielded %d voting districts; expected 435; '
            'districts=%r' % (len(districts), sorted(districts))
        )
    print(
        'loaded block crosswalk: %d assignments, 435 districts' %
        len(assignments)
    )
    return assignments, districts


def _load_county_weights(path, districts):
    if not os.path.exists(path):
        raise SystemExit('county/CD weights do not exist: %s' % path)
    weights = {}
    seen = set()
    print('loading county/CD weights: %s' % path)
    try:
        with gzip.open(path, 'rb') as compressed:
            with io.TextIOWrapper(
                compressed, encoding='utf-8-sig', errors='strict',
                newline=''
            ) as source:
                reader = csv.DictReader(source)
                expected = ['county_fips', 'cd', 'weight', 'employment']
                if reader.fieldnames != expected:
                    raise SystemExit(
                        'invalid county/CD weight header in %s: expected %r; '
                        'found %r' % (path, expected, reader.fieldnames)
                    )
                for row in reader:
                    if None in row:
                        raise SystemExit(
                            '%s row %d has surplus field(s): %r' %
                            (path, reader.line_num, row[None])
                        )
                    county = _normalize_digits(
                        row.get('county_fips'), 5,
                        '%s row %d county_fips' % (path, reader.line_num),
                    )
                    district = str(row.get('cd') or '').strip()
                    if district not in districts:
                        raise SystemExit(
                            '%s row %d has unknown district %r' %
                            (path, reader.line_num, district)
                        )
                    weight = _parse_nonnegative_number(
                        row.get('weight'),
                        '%s row %d weight' % (path, reader.line_num),
                    )
                    if (
                        weight is UNKNOWN or weight <= 0 or weight > 1
                    ):
                        raise SystemExit(
                            '%s row %d has invalid weight %r' %
                            (path, reader.line_num, row.get('weight'))
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
        if total > 1.0 + 1e-4:
            raise SystemExit(
                'county/CD weights for %s sum above 1.0: %.12f' %
                (county, total)
            )
    if not weights:
        raise SystemExit('county/CD weights are empty: %s' % path)
    print(
        'loaded county/CD weights: %d rows, %d counties' %
        (len(seen), len(weights))
    )
    return weights


def _normalize_county_name(value):
    text = str(value if value is not None else '').strip()
    text = text.strip('"\'*').strip().casefold()
    if text.endswith(' county'):
        text = text[:-len(' county')].rstrip()
    return text


def _load_bea_county_name_map(path):
    if not os.path.exists(path):
        raise SystemExit(
            'cached BEA CAEMP25N archive does not exist: %s' % path
        )
    try:
        _validate_zip(path, 'BEA CAEMP25N')
    except OSError as exc:
        raise SystemExit(
            'invalid cached BEA CAEMP25N archive %s: %s' %
            (path, str(exc))
        ) from exc

    county_map = {}
    ambiguous = set()
    with zipfile.ZipFile(path, 'r') as archive:
        members = [
            member for member in archive.namelist()
            if member and not member.endswith('/')
        ]
        matches = [
            member for member in members
            if (
                'all_areas' in member.lower() and
                member.lower().endswith('.csv')
            )
        ]
        if len(matches) != 1:
            raise SystemExit(
                'BEA CAEMP25N archive requires exactly one ALL_AREAS '
                'CSV member; candidates=%r; members=%r' %
                (matches, members)
            )
        member = matches[0]
        print('loading BEA county names: %s' % member)
        with io.TextIOWrapper(
            archive.open(member, 'r'),
            encoding='latin-1',
            errors='strict',
            newline='',
        ) as source:
            reader = csv.DictReader(source)
            headers = reader.fieldnames or []
            geo_header = _select_exact_header(
                headers, 'GeoFIPS', 'BEA CAEMP25N ALL_AREAS'
            )
            name_header = _select_exact_header(
                headers, 'GeoName', 'BEA CAEMP25N ALL_AREAS'
            )
            for row in reader:
                geo_fips = str(
                    row.get(geo_header) or ''
                ).strip().strip('"').strip()
                if re.fullmatch(r'[0-9]{5}', geo_fips) is None:
                    continue
                if None in row:
                    raise SystemExit(
                        'BEA CAEMP25N ALL_AREAS row %d has surplus '
                        'field(s): %r' % (reader.line_num, row[None])
                    )
                geo_name = str(
                    row.get(name_header) or ''
                ).strip().strip('"\'*').strip()
                if ',' not in geo_name:
                    continue
                county_text, state_text = geo_name.rsplit(',', 1)
                state = state_text.strip().strip('"\'*').strip().upper()
                expected_state = FIPS_TO_USPS.get(geo_fips[:2])
                if state != expected_state:
                    continue
                raw_normalized = (
                    county_text.strip().strip('"\'*').strip().casefold()
                )
                if raw_normalized.endswith(' city'):
                    continue
                county_name = _normalize_county_name(county_text)
                if not county_name:
                    continue
                key = (state, county_name)
                prior = county_map.get(key)
                if prior is None:
                    if key not in ambiguous:
                        county_map[key] = geo_fips
                elif prior != geo_fips:
                    ambiguous.add(key)
                    del county_map[key]
    if not county_map:
        raise SystemExit(
            'BEA CAEMP25N ALL_AREAS produced no county name/FIPS rows'
        )
    print(
        'loaded BEA county names: %d unique, %d ambiguous excluded' %
        (len(county_map), len(ambiguous))
    )
    return county_map


def _resolve_ncua_counties(records, county_name_map):
    resolved = 0
    for record in records:
        county_name = _normalize_county_name(
            record.get('county_name')
        )
        county_fips = county_name_map.get(
            (record['state'], county_name)
        )
        if county_fips is not None:
            record['county_fips'] = county_fips
            record['county_from_name'] = True
            resolved += 1
    print(
        'resolved NCUA counties from physical county/state names: '
        '%d/%d' % (resolved, len(records))
    )
    return resolved


def _assign_whole_counties(records, county_weights, source_name):
    remaining = []
    n_whole_county = 0
    for record in records:
        weights = county_weights.get(record.get('county_fips'), ())
        if len(weights) == 1 and weights[0][1] >= 0.9999:
            district = weights[0][0]
            record['allocations'] = [
                {'district': district, 'weight': 1.0, 'exact': True}
            ]
            record['allocation_method'] = 'whole-county'
            n_whole_county += 1
        else:
            remaining.append(record)
    print(
        'whole-county fast path %s: %d direct, %d require geocoding' %
        (source_name, n_whole_county, len(remaining))
    )
    return remaining, n_whole_county


def _prepare_geocode_records(records):
    eligible = []
    seen_ids = set()
    for record in records:
        source_id = record['source_id']
        if source_id in seen_ids:
            raise SystemExit('duplicate geocoder source id %s' % source_id)
        seen_ids.add(source_id)
        prefiltered = (
            not record['street'].strip() or
            record['state'].upper() in PREFILTER_STATES
        )
        record['geocode_eligible'] = not prefiltered
        if not prefiltered:
            eligible.append(record)
    return eligible


def _address_batch_bytes(records):
    target = io.StringIO(newline='')
    writer = csv.writer(target, lineterminator='\n')
    for record in records:
        writer.writerow([
            record['source_id'],
            record['street'],
            record['city'],
            record['state'],
            record['zip'],
        ])
    return target.getvalue().encode('utf-8', errors='strict')


def _file_token(value, label):
    text = str(value)
    if re.fullmatch(r'[A-Za-z0-9.-]+', text) is None:
        raise SystemExit('invalid %s file token %r' % (label, value))
    return text


def _geocode_batches(records, source_name, vintage, cache_root):
    source_token = _file_token(source_name, 'geocoder source')
    vintage_token = _file_token(vintage, 'geocoder vintage')
    batches = []
    for offset in range(0, len(records), GEOCODE_BATCH_SIZE):
        number = offset // GEOCODE_BATCH_SIZE + 1
        batch_records = records[offset:offset + GEOCODE_BATCH_SIZE]
        input_bytes = _address_batch_bytes(batch_records)
        filename = 'batch_%s_%s_%04d.csv' % (
            source_token, vintage_token, number
        )
        batches.append({
            'number': number,
            'filename': filename,
            'path': os.path.join(cache_root, filename),
            'records': batch_records,
            'input_bytes': input_bytes,
            'input_row_count': len(batch_records),
            'input_sha256': _sha256_bytes(input_bytes),
        })
    return batches


def _manifest_core(source_name, vintage, batches):
    return {
        'schema': 'financial-geocode-cache-v1',
        'source': source_name,
        'vintage': vintage,
        'benchmark': GEOCODER_BENCHMARK,
        'geocoder_vintage': GEOCODER_VINTAGE,
        'batches': [
            {
                'file': batch['filename'],
                'input_row_count': batch['input_row_count'],
                'input_sha256': batch['input_sha256'],
            }
            for batch in batches
        ],
    }


def _load_geocode_manifest(path):
    try:
        with open(
            path, 'r', encoding='utf-8-sig', errors='strict'
        ) as source:
            value = json.load(source)
    except (
        OSError, UnicodeError, json.JSONDecodeError
    ) as exc:
        raise SystemExit(
            'invalid geocode manifest %s; rerun with FIN_FORCE=1: %s' %
            (path, str(exc))
        ) from exc
    if not isinstance(value, dict):
        raise SystemExit(
            'invalid geocode manifest %s; expected object; '
            'rerun with FIN_FORCE=1' % path
        )
    return value


def _validate_geocode_manifest(path, expected):
    actual = _load_geocode_manifest(path)
    observed = {
        key: actual.get(key) for key in (
            'schema', 'source', 'vintage', 'benchmark',
            'geocoder_vintage', 'batches',
        )
    }
    if observed != expected:
        raise SystemExit(
            'geocode manifest/input mismatch at %s; '
            'rerun with FIN_FORCE=1' % path
        )


def _write_geocode_manifest(path, expected):
    value = dict(expected)
    value['generated_at'] = _utc_iso()
    dump_atomic(value, path, separators=(',', ':'))


def _multipart_body(input_bytes):
    boundary = (
        '----MelangeFinancial' + _sha256_bytes(input_bytes)[:24]
    )
    chunks = []

    def add_field(name, value):
        chunks.append(('--%s\r\n' % boundary).encode('ascii'))
        chunks.append((
            'Content-Disposition: form-data; name="%s"\r\n\r\n' % name
        ).encode('ascii'))
        chunks.append(str(value).encode('ascii'))
        chunks.append(b'\r\n')

    chunks.append(('--%s\r\n' % boundary).encode('ascii'))
    chunks.append(
        b'Content-Disposition: form-data; name="addressFile"; '
        b'filename="addresses.csv"\r\n'
    )
    chunks.append(b'Content-Type: text/csv; charset=utf-8\r\n\r\n')
    chunks.append(input_bytes)
    chunks.append(b'\r\n')
    add_field('benchmark', GEOCODER_BENCHMARK)
    add_field('vintage', GEOCODER_VINTAGE)
    chunks.append(('--%s--\r\n' % boundary).encode('ascii'))
    return b''.join(chunks), boundary


def _response_header_index(headers, aliases, label):
    candidates = [
        index for index, header in enumerate(headers)
        if re.sub(r'[\s_]+', '', header.strip().lower()) in aliases
    ]
    if len(candidates) != 1:
        reason = 'missing' if not candidates else 'ambiguous'
        raise SystemExit(
            'Census geocoder response %s %s column; headers=%r' %
            (reason, label, headers)
        )
    return candidates[0]


def _response_layout(rows, expected_ids, path):
    first = rows[0]
    if first and first[0].strip() in expected_ids:
        matched_row = next(
            (
                row for row in rows
                if len(row) >= 3 and row[2].strip().lower() == 'match'
            ),
            None,
        )
        # an all-non-match batch is valid; only a matched row must carry
        # STATE+COUNTY+TRACT+BLOCK
        if matched_row is not None and len(matched_row) < 12:
            raise SystemExit(
                'Census geocoder response %s matched row lacks '
                'STATE+COUNTY+TRACT+BLOCK columns; first row=%r' %
                (path, first)
            )
        return {
            'header': False,
            'id': 0,
            'match': 2,
            'state': 8,
            'county': 9,
            'tract': 10,
            'block': 11,
        }, rows

    normalized = [
        re.sub(r'[\s_]+', '', value.strip().lower()) for value in first
    ]
    geography_aliases = (
        {'state', 'statefp', 'statecode'},
        {'county', 'countyfp', 'countycode'},
        {'tract', 'tractce'},
        {'block', 'blockce'},
    )
    if not all(any(value in aliases for value in normalized)
               for aliases in geography_aliases):
        raise SystemExit(
            'Census geocoder response %s lacks expected '
            'STATE+COUNTY+TRACT+BLOCK columns; observed first row=%r' %
            (path, first)
        )
    layout = {
        'header': True,
        'id': _response_header_index(
            first, {'id', 'uniqueid', 'inputid'}, 'ID'
        ),
        'match': _response_header_index(
            first, {'match', 'matchstatus'}, 'match'
        ),
        'state': _response_header_index(
            first, {'state', 'statefp', 'statecode'}, 'STATE'
        ),
        'county': _response_header_index(
            first, {'county', 'countyfp', 'countycode'}, 'COUNTY'
        ),
        'tract': _response_header_index(
            first, {'tract', 'tractce'}, 'TRACT'
        ),
        'block': _response_header_index(
            first, {'block', 'blockce'}, 'BLOCK'
        ),
    }
    return layout, rows[1:]


def _parse_geocode_response(path, expected_ids):
    try:
        with open(
            path, 'r', encoding='utf-8-sig', errors='strict', newline=''
        ) as source:
            rows = list(csv.reader(source))
    except (OSError, UnicodeError, csv.Error) as exc:
        raise SystemExit(
            'failed to parse Census geocoder response %s: %s' %
            (path, str(exc))
        ) from exc
    if not rows:
        raise SystemExit('empty Census geocoder response: %s' % path)
    layout, data_rows = _response_layout(rows, expected_ids, path)
    max_index = max(
        layout['id'], layout['match'], layout['state'],
        layout['county'], layout['tract'], layout['block']
    )
    results = {}
    for row_num, row in enumerate(
        data_rows, 2 if layout['header'] else 1
    ):
        if len(row) <= max(layout['id'], layout['match']):
            raise SystemExit(
                'Census geocoder response %s row %d is too short: %r' %
                (path, row_num, row)
            )
        source_id = row[layout['id']].strip()
        if source_id not in expected_ids:
            raise SystemExit(
                'Census geocoder response %s row %d has unexpected id %r' %
                (path, row_num, source_id)
            )
        if source_id in results:
            raise SystemExit(
                'Census geocoder response %s duplicates id %s' %
                (path, source_id)
            )
        matched = row[layout['match']].strip().lower() == 'match'
        if not matched:
            results[source_id] = None
            continue
        if len(row) <= max_index:
            raise SystemExit(
                'Census geocoder response %s matched row %d lacks '
                'STATE+COUNTY+TRACT+BLOCK: %r' % (path, row_num, row)
            )
        state = row[layout['state']].strip()
        county = row[layout['county']].strip()
        tract = row[layout['tract']].strip()
        block = row[layout['block']].strip()
        pieces = (
            ('STATE', state, 2), ('COUNTY', county, 3),
            ('TRACT', tract, 6), ('BLOCK', block, 4),
        )
        invalid = [
            '%s=%r' % (name, value)
            for name, value, width in pieces
            if len(value) != width or not value.isdigit()
        ]
        if invalid:
            raise SystemExit(
                'Census geocoder response %s row %d has invalid '
                'geography pieces %s' %
                (path, row_num, ', '.join(invalid))
            )
        results[source_id] = state + county + tract + block
    missing = sorted(expected_ids - set(results))
    if missing:
        raise SystemExit(
            'Census geocoder response %s is missing %d input id(s); '
            'first=%r' % (path, len(missing), missing[:10])
        )
    return results


def _fetch_geocode_batch(batch):
    body, boundary = _multipart_body(batch['input_bytes'])
    request = urllib.request.Request(
        GEOCODER_URL,
        data=body,
        headers={
            'User-Agent': USER_AGENT,
            'Content-Type': 'multipart/form-data; boundary=%s' % boundary,
        },
        method='POST',
    )
    return _request_bytes(
        request, 'Census geocoder batch %s' % batch['filename'],
        timeout=GEOCODER_TIMEOUT,
    )


def _process_geocode_batch(batch, source_name, vintage, force):
    expected_ids = frozenset(
        record['source_id'] for record in batch['records']
    )
    if os.path.exists(batch['path']) and not force:
        print('cached Census geocoder batch: %s' % batch['path'])
        batch_results = _parse_geocode_response(
            batch['path'], expected_ids
        )
    else:
        print(
            'geocoding %s %s batch %d: %d rows' %
            (
                source_name, vintage, batch['number'],
                batch['input_row_count'],
            )
        )
        response = _fetch_geocode_batch(batch)
        part_path = batch['path'] + '.part'
        try:
            with open(part_path, 'wb') as target:
                target.write(response)
            batch_results = _parse_geocode_response(
                part_path, expected_ids
            )
            os.replace(part_path, batch['path'])
        except BaseException:
            _discard_part(part_path)
            raise
    return batch['number'], batch_results


def _geocode_records(
    records, source_name, vintage, cache_root, force
):
    eligible = _prepare_geocode_records(records)
    os.makedirs(cache_root, exist_ok=True)
    batches = _geocode_batches(
        eligible, source_name, vintage, cache_root
    )
    source_dir = os.path.join(
        cache_root, '%s_%s' % (
            _file_token(source_name, 'geocoder source'),
            _file_token(vintage, 'geocoder vintage'),
        )
    )
    os.makedirs(source_dir, exist_ok=True)
    manifest_path = os.path.join(source_dir, 'geocode_manifest.json')
    expected_manifest = _manifest_core(source_name, vintage, batches)

    write_manifest_after = force
    if not force and os.path.exists(manifest_path):
        _validate_geocode_manifest(manifest_path, expected_manifest)
    elif not force:
        existing_batches = [
            batch['path'] for batch in batches
            if os.path.exists(batch['path'])
        ]
        if existing_batches:
            raise SystemExit(
                'geocode batch cache exists without matching manifest: %r; '
                'rerun with FIN_FORCE=1' % existing_batches
            )
        _write_geocode_manifest(manifest_path, expected_manifest)

    results = {}
    matched = 0
    by_batch = {}
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=4
    ) as executor:
        future_to_batch = {
            executor.submit(
                _process_geocode_batch,
                batch,
                source_name,
                vintage,
                force,
            ): batch
            for batch in batches
        }
        try:
            for future in concurrent.futures.as_completed(
                future_to_batch
            ):
                batch_number, batch_results = future.result()
                by_batch[batch_number] = batch_results
        except BaseException:
            for future in future_to_batch:
                future.cancel()
            raise

    for batch in batches:
        batch_results = by_batch[batch['number']]
        results.update(batch_results)
        matched += sum(
            1 for block in batch_results.values() if block is not None
        )

    if write_manifest_after:
        _write_geocode_manifest(manifest_path, expected_manifest)

    submitted = len(eligible)
    match_rate = matched / submitted if submitted else 0.0
    print(
        'geocoded %s: %d/%d matched (%.1f%%), %d prefiltered' %
        (
            source_name, matched, submitted, match_rate * 100,
            len(records) - submitted,
        )
    )
    if match_rate < 0.80:
        print(
            '!! %s geocode match rate %.1f%% is below 80.0%%' %
            (source_name, match_rate * 100)
        )
    return results, {
        'n_records': len(records),
        'n_geocoder_submitted': submitted,
        'n_geocoder_matched': matched,
        'n_prefiltered': len(records) - submitted,
        'match_rate': round(match_rate, 4),
        'manifest': manifest_path,
    }


def _assign_records(
    records, geocodes, geocode_stats, crosswalk, districts,
    county_weights, source_name
):
    stats = dict(geocode_stats)
    stats['n_records'] = len(records)
    stats.update({
        'n_whole_county': sum(
            1 for record in records
            if record.get('allocation_method') == 'whole-county'
        ),
        'n_county_from_name': sum(
            1 for record in records
            if record.get('county_from_name')
        ),
        'n_geocoded': 0,
        'n_county_fallback': 0,
        'n_non_district': 0,
        'n_unallocated': 0,
        # diagnostic only (not a path counter): geocoded block absent from
        # the CD119 crosswalk, falls through to county fallback
        'n_block_not_in_crosswalk': 0,
        'fallback_allocated_branch_equivalents': 0.0,
        'fallback_unallocated_branch_equivalents': 0.0,
    })

    for record in records:
        if record.get('allocation_method') == 'whole-county':
            continue
        source_id = record['source_id']
        block = (
            geocodes.get(source_id)
            if record.get('geocode_eligible') else None
        )
        if block is not None and (
            len(block) != 15 or not block.isdigit()
        ):
            raise SystemExit(
                '%s has invalid geocoded block %r' % (source_id, block)
            )

        if block is not None and block in crosswalk:
            cd = crosswalk[block]
            state = FIPS_TO_USPS.get(block[:2])
            if cd in ('98', 'ZZ') or state not in VOTING_STATES:
                record['allocations'] = []
                record['allocation_method'] = 'non-district'
                stats['n_non_district'] += 1
                continue
            district = '%s-%s' % (state, cd)
            if district not in districts:
                raise SystemExit(
                    '%s exact block %s resolves to unknown district %s' %
                    (source_id, block, district)
                )
            record['allocations'] = [
                {'district': district, 'weight': 1.0, 'exact': True}
            ]
            record['allocation_method'] = 'exact-geocode'
            stats['n_geocoded'] += 1
            continue

        if block is not None:
            stats['n_block_not_in_crosswalk'] += 1
        county = record.get('county_fips')
        if county is None and block is not None:
            county = block[:5]
        fallback = county_weights.get(county, ()) if county else ()
        if fallback:
            record['allocations'] = [
                {
                    'district': district,
                    'weight': weight,
                    'exact': False,
                }
                for district, weight in fallback
            ]
            record['allocation_method'] = 'county-fallback'
            stats['n_county_fallback'] += 1
            allocated_weight = sum(weight for _district, weight in fallback)
            stats['fallback_allocated_branch_equivalents'] += (
                allocated_weight
            )
            stats['fallback_unallocated_branch_equivalents'] += max(
                0.0, 1.0 - allocated_weight
            )
        else:
            record['allocations'] = []
            record['allocation_method'] = 'unallocated'
            stats['n_unallocated'] += 1

    path_total = (
        stats['n_whole_county'] +
        stats['n_geocoded'] +
        stats['n_county_fallback'] +
        stats['n_non_district'] +
        stats['n_unallocated']
    )
    if path_total != len(records):
        raise SystemExit(
            '%s assignment paths total %d; expected %d' %
            (source_name, path_total, len(records))
        )
    stats['fallback_allocated_branch_equivalents'] = round(
        stats['fallback_allocated_branch_equivalents'], 4
    )
    stats['fallback_unallocated_branch_equivalents'] = round(
        stats['fallback_unallocated_branch_equivalents'], 4
    )
    print(
        'assigned %s: whole county %d, exact geocode %d, '
        'county fallback %d, '
        'non-district %d, unallocated %d' %
        (
            source_name,
            stats['n_whole_county'],
            stats['n_geocoded'],
            stats['n_county_fallback'],
            stats['n_non_district'],
            stats['n_unallocated'],
        )
    )
    print(
        '!! %s unallocated branches: %d; non-district exact matches: %d' %
        (
            source_name,
            stats['n_unallocated'],
            stats['n_non_district'],
        )
    )
    return stats


def _top_institutions(values, names, value_key):
    ranked = sorted(
        values.items(),
        key=lambda item: (
            -item[1],
            names.get(item[0], item[0]).casefold(),
            item[0],
        ),
    )[:5]
    return [
        {
            'name': names.get(institution, institution),
            value_key: round(value, 1),
        }
        for institution, value in ranked
    ]


def _build_metrics(
    districts, fdic_records, ncua_records, ncua_shares_kusd,
    ncua_names, fdic_stats, ncua_stats
):
    bank_branch = {district: 0.0 for district in districts}
    bank_deposits = {district: 0.0 for district in districts}
    bank_by_institution = {
        district: defaultdict(float) for district in districts
    }
    bank_hhi_by_institution = {
        district: defaultdict(float) for district in districts
    }
    bank_hhi_total = {district: 0.0 for district in districts}
    bank_names = {}
    national_deposits = 0.0
    hhi_excluded_fallback_deposits = 0.0

    for record in fdic_records:
        institution = record['institution_id']
        bank_names[institution] = record['institution_name']
        deposits = record['deposits_kusd']
        national_deposits += deposits
        for allocation in record['allocations']:
            district = allocation['district']
            weight = allocation['weight']
            allocated_deposits = deposits * weight
            bank_branch[district] += weight
            bank_deposits[district] += allocated_deposits
            bank_by_institution[district][institution] += (
                allocated_deposits
            )
            if allocation['exact']:
                bank_hhi_total[district] += allocated_deposits
                bank_hhi_by_institution[district][institution] += (
                    allocated_deposits
                )
            else:
                hhi_excluded_fallback_deposits += allocated_deposits

    if national_deposits <= 0:
        raise SystemExit('FDIC national DEPSUMBR total is not positive')
    fdic_stats['hhi_excluded_fallback_deposits_kusd'] = round(
        hhi_excluded_fallback_deposits, 1
    )

    eligible_by_cu = defaultdict(int)
    for record in ncua_records:
        if record['allocations']:
            eligible_by_cu[record['institution_id']] += 1
    missing_shares = sorted(
        cu_number for cu_number in eligible_by_cu
        if cu_number not in ncua_shares_kusd
    )
    if missing_shares:
        raise SystemExit(
            'NCUA FS220 is missing ACCT_018 for %d allocation-eligible '
            'CU(s); first=%r' % (len(missing_shares), missing_shares[:20])
        )

    branch_cus = {
        record['institution_id'] for record in ncua_records
    }
    fallback_name_cus = sorted(branch_cus - set(ncua_names))
    resolved_cu_names = {
        cu_number: ncua_names.get(cu_number, 'CU %s' % cu_number)
        for cu_number in branch_cus
    }
    cu_branch = {district: 0.0 for district in districts}
    cu_shares = {district: 0.0 for district in districts}
    cu_by_institution = {
        district: defaultdict(float) for district in districts
    }
    for record in ncua_records:
        institution = record['institution_id']
        denominator = eligible_by_cu.get(institution, 0)
        if not record['allocations']:
            continue
        if denominator <= 0:
            raise SystemExit(
                'internal error: eligible NCUA branch has zero denominator '
                'for CU %s' % institution
            )
        per_branch_shares = (
            ncua_shares_kusd[institution] / denominator
        )
        for allocation in record['allocations']:
            district = allocation['district']
            weight = allocation['weight']
            allocated_shares = per_branch_shares * weight
            cu_branch[district] += weight
            cu_shares[district] += allocated_shares
            cu_by_institution[district][institution] += allocated_shares

    result = {}
    hhi_max = 0
    for district in sorted(districts):
        exact_total = bank_hhi_total[district]
        if exact_total > 0:
            hhi = int(round(sum(
                (deposits / exact_total) ** 2
                for deposits
                in bank_hhi_by_institution[district].values()
            ) * 10000))
        else:
            hhi = 0
        hhi_max = max(hhi_max, hhi)
        bank_count = bank_branch[district]
        cu_count = cu_branch[district]
        combined_count = bank_count + cu_count
        result[district] = {
            'banks': {
                'n_branches': round(bank_count, 1),
                'deposits_kusd': round(bank_deposits[district], 1),
                'deposit_share_national': round(
                    bank_deposits[district] / national_deposits, 4
                ),
                'deposit_hhi': hhi,
                'top5': _top_institutions(
                    bank_by_institution[district],
                    bank_names,
                    'deposits_kusd',
                ),
            },
            'cus': {
                'n_branches': round(cu_count, 1),
                'est_shares_kusd': round(cu_shares[district], 1),
                'method': (
                    'equal-split-across-allocation-eligible-branches'
                ),
                'top5': _top_institutions(
                    cu_by_institution[district],
                    resolved_cu_names,
                    'est_shares_kusd',
                ),
            },
            'ratio': {
                'cu_branch_share': round(
                    cu_count / combined_count, 4
                ) if combined_count else 0.0,
            },
        }

    return {
        'districts': result,
        'national_bank_deposits_kusd': national_deposits,
        'allocated_bank_deposits_kusd': sum(bank_deposits.values()),
        'hhi_max': hhi_max,
        'n_cu_allocation_eligible_branches': sum(
            eligible_by_cu.values()
        ),
        'n_cu_ineligible_branches':
            len(ncua_records) - sum(eligible_by_cu.values()),
        'n_cu_fallback_name_cus': len(fallback_name_cus),
        'cu_fallback_name_cus': fallback_name_cus,
    }


def _local_source(path, logical_file):
    return {
        'file': logical_file,
        'sha256': _sha256(path),
        'retrieved': _utc_iso(os.path.getmtime(path)),
    }


def main():
    _configure_stdout()
    data_root = os.environ.get('DEMO_DATA_ROOT')
    if not data_root:
        base = (
            os.environ.get('LOCALAPPDATA') or
            os.path.expanduser(r'~\AppData\Local')
        )
        data_root = os.path.join(base, 'Melange', 'demographics')

    year = _parse_year(os.environ.get('SOD_YEAR', '2025'))
    force = os.environ.get('FIN_FORCE', '0') == '1'
    output_path = os.environ.get('FIN_OUT')
    if not output_path:
        output_path = os.path.join(MB_ROOT, 'data', 'district', 'district_financial.json')

    financial_root = os.path.join(data_root, 'financial')
    geocode_cache_root = os.path.join(
        financial_root, 'geocode_cache'
    )
    geo_root = os.path.join(data_root, 'geo')
    os.makedirs(financial_root, exist_ok=True)
    os.makedirs(geocode_cache_root, exist_ok=True)
    fdic_path = os.path.join(
        financial_root, 'fdic_sod_%s.jsonl.gz' % year
    )
    ncua_path = os.path.join(
        financial_root, 'call-report-data-2026-03.zip'
    )
    crosswalk_path = os.path.join(
        geo_root, 'cd119_block_assign.csv.gz'
    )
    county_weight_path = os.path.join(
        geo_root, 'county_cd_weights.csv.gz'
    )
    bea_county_path = os.path.join(
        data_root, 'validate', 'CAEMP25N.zip'
    )

    print(
        'financial Phase H1: FDIC SOD %s, NCUA %s; data root %s' %
        (year, NCUA_QUARTER, data_root)
    )
    print('collecting FDIC Summary of Deposits')
    fdic_rows, fdic_source = _collect_fdic(year, fdic_path, force)
    fdic_records = _normalize_fdic_records(fdic_rows)

    print('collecting NCUA call-report archive')
    ncua_source = _download_file(
        'NCUA call report %s' % NCUA_QUARTER,
        NCUA_URL,
        ncua_path,
        force,
        integrity='zip',
    )
    ncua_data = _parse_ncua_archive(ncua_path)
    ncua_records = ncua_data['branches']
    ncua_source.update({
        'quarter': NCUA_QUARTER,
        'members': ncua_data['members'],
        'name_file_status': ncua_data['name_file_status'],
    })

    print('loading district assignment inputs')
    crosswalk, districts = _load_block_crosswalk(crosswalk_path)
    county_weights = _load_county_weights(
        county_weight_path, districts
    )
    bea_county_names = _load_bea_county_name_map(bea_county_path)
    _resolve_ncua_counties(ncua_records, bea_county_names)

    print('applying whole-county branch fast path')
    fdic_geocode_records, _fdic_whole_county = (
        _assign_whole_counties(
            fdic_records, county_weights, 'FDIC'
        )
    )
    ncua_geocode_records, _ncua_whole_county = (
        _assign_whole_counties(
            ncua_records, county_weights, 'NCUA'
        )
    )

    print('geocoding FDIC branches')
    fdic_geocodes, fdic_geocode_stats = _geocode_records(
        fdic_geocode_records, 'fdic', year,
        geocode_cache_root, force
    )
    print('geocoding NCUA branches')
    ncua_geocodes, ncua_geocode_stats = _geocode_records(
        ncua_geocode_records, 'ncua', NCUA_QUARTER,
        geocode_cache_root, force
    )

    print('assigning branches to CD119 districts')
    fdic_match_stats = _assign_records(
        fdic_records,
        fdic_geocodes,
        fdic_geocode_stats,
        crosswalk,
        districts,
        county_weights,
        'FDIC',
    )
    ncua_match_stats = _assign_records(
        ncua_records,
        ncua_geocodes,
        ncua_geocode_stats,
        crosswalk,
        districts,
        county_weights,
        'NCUA',
    )

    print('computing district financial metrics')
    metrics = _build_metrics(
        districts,
        fdic_records,
        ncua_records,
        ncua_data['shares_kusd'],
        ncua_data['names'],
        fdic_match_stats,
        ncua_match_stats,
    )
    national_deposits = metrics['national_bank_deposits_kusd']
    allocated_deposits = metrics['allocated_bank_deposits_kusd']
    national_gap = national_deposits - allocated_deposits

    artifact = {
        'schema': 'district-financial-v1',
        'generated_at': _utc_iso(),
        'vintages': {
            'fdic_sod_year': int(year),
            'ncua_quarter': NCUA_QUARTER,
        },
        'sources': {
            'fdic_sod': fdic_source,
            'ncua_call_report': ncua_source,
            'census_batch_geocoder': {
                'url': GEOCODER_URL,
                'benchmark': GEOCODER_BENCHMARK,
                'vintage': GEOCODER_VINTAGE,
            },
            'block_crosswalk': _local_source(
                crosswalk_path,
                'DEMO_DATA_ROOT/geo/cd119_block_assign.csv.gz',
            ),
            'county_cd_weights': _local_source(
                county_weight_path,
                'DEMO_DATA_ROOT/geo/county_cd_weights.csv.gz',
            ),
            'bea_county_names': _local_source(
                bea_county_path,
                'DEMO_DATA_ROOT/validate/CAEMP25N.zip',
            ),
        },
        'method_notes': {
            'cu_shares': 'equal-split-across-branches',
            'cu_shares_denominator':
                'allocation-eligible-branches-only',
            'hhi': 'exact-geocoded-and-whole-county-only',
            'sod_hq_deposits': (
                'SOD attributes some institutional deposits to '
                'head-office branches; money-center districts '
                '(e.g. NY-12) carry HQ-booked deposits and elevated '
                'HHI — interpret deposit levels as booking location, '
                'not local retail deposits'
            ),
        },
        'match_stats': {
            'fdic': fdic_match_stats,
            'ncua': ncua_match_stats,
        },
        'districts': metrics['districts'],
        'coverage_notes': {
            'bank_deposit_share_denominator': (
                'all FDIC SOD rows for the year, including rows outside '
                'the 435-district allocation universe'
            ),
            'district_deposit_shares_may_sum_below_one': True,
            'national_bank_deposits_kusd': round(
                national_deposits, 1
            ),
            'allocated_bank_deposits_kusd': round(
                allocated_deposits, 1
            ),
            'national_bank_deposit_gap_kusd': round(national_gap, 1),
            'hhi_fallback_deposits_excluded_kusd':
                fdic_match_stats[
                    'hhi_excluded_fallback_deposits_kusd'
                ],
            'ncua_acct_018_units': (
                'source dollars converted to thousands of dollars'
            ),
            'cu_share_denominator_note': (
                'branches without an exact-district or county-fallback '
                'allocation are excluded from each CU denominator'
            ),
            'n_cu_allocation_eligible_branches':
                metrics['n_cu_allocation_eligible_branches'],
            'n_cu_ineligible_branches':
                metrics['n_cu_ineligible_branches'],
            'ncua_name_file_status':
                ncua_data['name_file_status'],
            'ncua_name_file_candidates':
                ncua_data['name_file_candidates'],
            'n_cu_fallback_name_cus':
                metrics['n_cu_fallback_name_cus'],
            'cu_fallback_name_note': (
                'missing CU names are emitted as CU {number}'
            ),
            'geocoder_prefilter_note': (
                'blank-street and US-territory/military addresses bypass '
                'the geocoder and enter the permitted fallback path'
            ),
            'whole_county_fast_path_note': (
                'branches with a single county/CD weight of at least '
                '0.9999 are assigned directly at weight 1.0'
            ),
            'ncua_county_resolution_note': (
                'physical county/state names are matched to cached BEA '
                'ALL_AREAS county FIPS; independent-city, state-mismatch, '
                'and ambiguous names remain geocoder candidates'
            ),
            'county_fallback_note': (
                'county weights may sum below one; the residual remains '
                'outside the district metrics'
            ),
        },
    }

    # Below-threshold geocode match: park the artifact beside the live file
    # as .candidate and stop, so a bad geocoder run cannot overwrite it.
    # Zero submitted (all whole-county) has no match rate to judge.
    low_match = [
        (name, stats['n_geocoder_matched'] / stats['n_geocoder_submitted'])
        for name, stats in (
            ('fdic', fdic_match_stats), ('ncua', ncua_match_stats)
        )
        if stats['n_geocoder_submitted'] > 0
    ]
    low_match = [(name, rate) for name, rate in low_match if rate < 0.80]
    if low_match:
        candidate_path = output_path + '.candidate'
        dump_atomic(artifact, candidate_path, separators=(',', ':'))
        print(
            '!! geocode match rate below 80%% for %s; wrote %s, '
            'live artifact untouched' %
            (
                ', '.join(
                    '%s %.1f%%' % (name, rate * 100)
                    for name, rate in low_match
                ),
                candidate_path,
            )
        )
        raise SystemExit(1)

    print('writing district financial artifact: %s' % output_path)
    dump_atomic(artifact, output_path, separators=(',', ':'))
    print(
        'wrote district_financial.json: 435 districts, '
        'banks %d branches / $%.1f bn, CUs %d branches, '
        'geocode match %.1f/%.1f pct, hhi max %d' %
        (
            len(fdic_records),
            national_deposits / 1000000.0,
            len(ncua_records),
            fdic_match_stats['match_rate'] * 100,
            ncua_match_stats['match_rate'] * 100,
            metrics['hhi_max'],
        )
    )


if __name__ == '__main__':
    main()

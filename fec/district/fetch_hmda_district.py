# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT  # centralized root (arch review 2026-07-02)
"""HMDA DISTRICT COLLECTOR/BUILDER — mortgage originations by CD119.

Standalone Phase H2 collector/builder.  This script is deliberately not
chained into run_pipeline.py.  It streams state HMDA LAR extracts into small
aggregate caches, builds tract-to-district employment weights from the latest
local LODES WAC files, and writes district-hmda-v1.

Inputs:
  DEMO_DATA_ROOT/lodes/raw/{st}_wac_S000_JT01_{year}.csv.gz
  DEMO_DATA_ROOT/geo/cd119_block_assign.csv.gz
  DEMO_DATA_ROOT/geo/ct_2022_block_crosswalk.csv

Raw/cache outputs:
  DEMO_DATA_ROOT/geo/tract_cd_weights.csv.gz
  DEMO_DATA_ROOT/hmda/{year}/{USPS}_agg.json

Repo-plane output:
  MB_ROOT/district_hmda.json, unless HMDA_OUT overrides the path.

Dials:
  DEMO_DATA_ROOT — out-of-OneDrive raw-data root; defaults to
                   %LOCALAPPDATA%/Melange/demographics
  HMDA_YEAR      — HMDA LAR year; defaults to "2024"
  HMDA_OUT       — optional output-path override
  HMDA_FORCE     — set to "1" to refresh per-state aggregate caches
  CURL_BIN       — optional curl executable override
  CANARY_INTERVAL — seconds between CFPB access probes; defaults to "60"
  CANARY_MAX_WAIT — shared whole-run canary budget in seconds; defaults
                    to "21600"

Raw LAR rows are never written to disk.  State responses are consumed through
curl stdout, parsed by csv.DictReader, and reduced directly to
tract/purpose/LMI counts.  curl is the transport documented in the CFPB Data
Browser API examples; Python urllib is rejected by the upstream CDN with HTTP
403 even when the same public query succeeds through curl.
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
import shutil
import subprocess
import sys
import threading
import time
from collections import defaultdict

from moneyball_core import dump_atomic


LAR_URL_TEMPLATE = (
    'https://ffiec.cfpb.gov/v2/data-browser-api/view/csv'
    '?states={state}&years={year}'
)
LODES_URL_TEMPLATE = (
    'https://lehd.ces.census.gov/data/lodes/LODES8/'
    '{st}/wac/{st}_wac_S000_JT01_{year}.csv.gz'
)
RAW_WAC_RE_TEMPLATE = (
    r'{state}_wac_S000_JT01_(\d{{4}})\.csv\.gz'
)
USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/126.0 Safari/537.36'
)
LAR_ACCEPT = 'text/csv,*/*;q=0.8'
LAR_REFERER = 'https://ffiec.cfpb.gov/data-browser/'
CHUNK_SIZE = 1024 * 1024
REQUEST_TIMEOUT = 120
REQUEST_START_SPACING = 3.0
COLLECTION_WORKERS = 3
STATE_MAX_ATTEMPTS = 4
STATE_BACKOFF_SECONDS = (60, 120, 240)
FLAG_RETURN_STREAK = 3
MAX_CANARY_REENTRIES = 3
CANARY_STATE = 'WY'
CANARY_READ_BYTES = 2048
CANARY_INTERVAL_DEFAULT = 60
CANARY_MAX_WAIT_DEFAULT = 21600
CURL_CONNECT_TIMEOUT = 30
CURL_META_PREFIX = '__HMDA_CURL_META__'
LAR_REQUIRED_COLUMNS = (
    'census_tract',
    'action_taken',
    'loan_purpose',
    'tract_to_msa_income_percentage',
)
HMDA_STATES = (
    'AK', 'AL', 'AR', 'AZ', 'CA', 'CO', 'CT', 'DC', 'DE', 'FL',
    'GA', 'HI', 'IA', 'ID', 'IL', 'IN', 'KS', 'KY', 'LA', 'MA',
    'MD', 'ME', 'MI', 'MN', 'MO', 'MS', 'MT', 'NC', 'ND', 'NE',
    'NH', 'NJ', 'NM', 'NV', 'NY', 'OH', 'OK', 'OR', 'PA', 'RI',
    'SC', 'SD', 'TN', 'TX', 'UT', 'VA', 'VT', 'WA', 'WI', 'WV',
    'WY',
)
VOTING_STATES = frozenset((
    'AK', 'AL', 'AR', 'AZ', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA',
    'HI', 'IA', 'ID', 'IL', 'IN', 'KS', 'KY', 'LA', 'MA', 'MD',
    'ME', 'MI', 'MN', 'MO', 'MS', 'MT', 'NC', 'ND', 'NE', 'NH',
    'NJ', 'NM', 'NV', 'NY', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC',
    'SD', 'TN', 'TX', 'UT', 'VA', 'VT', 'WA', 'WI', 'WV', 'WY',
))
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
USPS_TO_FIPS = {
    usps: fips for fips, usps in FIPS_TO_USPS.items()
    if usps in HMDA_STATES
}


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


def _parse_year(value):
    text = str(value or '').strip()
    if len(text) != 4 or not text.isdigit():
        raise SystemExit(
            'invalid HMDA_YEAR %r; expected a four-digit year' % value
        )
    year = int(text)
    if year < 2018:
        raise SystemExit(
            'invalid HMDA_YEAR %r; expected a year at or after 2018' % value
        )
    return text


def _parse_positive_seconds(value, name):
    text = str(value or '').strip()
    try:
        seconds = float(text)
    except ValueError as exc:
        raise SystemExit(
            'invalid %s %r; expected positive seconds' % (name, value)
        ) from exc
    if not math.isfinite(seconds) or seconds <= 0:
        raise SystemExit(
            'invalid %s %r; expected positive seconds' % (name, value)
        )
    return seconds


def _header_map(fieldnames, label):
    headers = list(fieldnames or [])
    mapped = {}
    for header in headers:
        compact = str(header or '').strip().casefold()
        if not compact:
            raise SystemExit(
                '%s has a blank CSV header; headers=%r' % (label, headers)
            )
        if compact in mapped:
            raise SystemExit(
                '%s has duplicate case-insensitive CSV header %r; '
                'headers=%r' % (label, compact, headers)
            )
        mapped[compact] = header
    return headers, mapped


def _required_headers(fieldnames, required, label):
    headers, mapped = _header_map(fieldnames, label)
    missing = [name for name in required if name.casefold() not in mapped]
    if missing:
        raise SystemExit(
            '%s is missing required column(s): %s; headers=%r' %
            (label, ', '.join(missing), headers)
        )
    return {name: mapped[name.casefold()] for name in required}


def _local_source(path, logical_file):
    return {
        'file': logical_file,
        'sha256': _sha256(path),
        'retrieved': _utc_iso(os.path.getmtime(path)),
    }


def _load_block_crosswalk(path):
    if not os.path.exists(path):
        raise SystemExit('block crosswalk does not exist: %s' % path)

    assignments = {}
    districts = set()
    print('loading block crosswalk: %s' % path)
    try:
        with gzip.open(path, 'rb') as compressed:
            with io.TextIOWrapper(
                compressed,
                encoding='utf-8-sig',
                errors='strict',
                newline='',
            ) as source:
                reader = csv.DictReader(source)
                expected = ['block_geoid', 'cd']
                if reader.fieldnames != expected:
                    raise SystemExit(
                        'invalid block crosswalk header in %s: expected %r; '
                        'found %r' % (path, expected, reader.fieldnames)
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
                    state = FIPS_TO_USPS.get(block[:2])
                    if state is None:
                        raise SystemExit(
                            '%s row %d has unknown state FIPS %s' %
                            (path, reader.line_num, block[:2])
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


def _load_ct_tract_recode(path):
    if not os.path.exists(path):
        raise SystemExit('CT tract-recode bridge does not exist: %s' % path)

    recode = {}
    print('loading CT 2020-to-2022 tract recode: %s' % path)
    try:
        with open(
            path, 'r', encoding='utf-8-sig', errors='strict', newline=''
        ) as source:
            reader = csv.DictReader(source)
            selected = _required_headers(
                reader.fieldnames,
                ('tract_fips_2020', 'tract_fips_2022'),
                path,
            )
            for row in reader:
                if None in row:
                    raise SystemExit(
                        '%s row %d has surplus field(s): %r' %
                        (path, reader.line_num, row[None])
                    )
                tract_2020 = (
                    row.get(selected['tract_fips_2020']) or ''
                ).strip()
                tract_2022 = (
                    row.get(selected['tract_fips_2022']) or ''
                ).strip()
                for label, tract in (
                    ('tract_fips_2020', tract_2020),
                    ('tract_fips_2022', tract_2022),
                ):
                    if len(tract) != 11 or not tract.isdigit():
                        raise SystemExit(
                            '%s row %d has invalid %s %r' %
                            (path, reader.line_num, label, tract)
                        )
                    if not tract.startswith('09'):
                        raise SystemExit(
                            '%s row %d has non-CT %s %s' %
                            (path, reader.line_num, label, tract)
                        )
                existing = recode.get(tract_2020)
                if existing is not None and existing != tract_2022:
                    raise SystemExit(
                        '%s maps 2020 tract %s to multiple 2022 tracts: '
                        '%s and %s' %
                        (path, tract_2020, existing, tract_2022)
                    )
                recode[tract_2020] = tract_2022
    except (OSError, UnicodeError, csv.Error) as exc:
        raise SystemExit(
            'failed to load CT tract-recode bridge %s: %s' %
            (path, str(exc))
        ) from exc

    if not recode:
        raise SystemExit('CT tract-recode bridge is empty: %s' % path)
    print('loaded CT tract recode: %d 2020 tracts' % len(recode))
    return recode


def _latest_wac_files(raw_root):
    if not os.path.isdir(raw_root):
        raise SystemExit('LODES raw directory does not exist: %s' % raw_root)

    names = os.listdir(raw_root)
    selected = {}
    source_years = {}
    for state in HMDA_STATES:
        state_lower = state.lower()
        pattern = re.compile(
            RAW_WAC_RE_TEMPLATE.format(
                state=re.escape(state_lower)
            ),
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
                (state, raw_root)
            )
        year, name = max(candidates)
        selected[state] = os.path.join(raw_root, name)
        source_years[state] = year

    print(
        'selected latest raw WAC files: %d jurisdictions' %
        len(selected)
    )
    return selected, source_years


def _parse_nonnegative_int(text, label):
    value_text = (text or '').strip()
    try:
        value = int(value_text)
    except ValueError as exc:
        raise SystemExit(
            '%s has invalid integer count %r' % (label, value_text)
        ) from exc
    if value < 0:
        raise SystemExit('%s has negative count %d' % (label, value))
    return value


def _stream_wac_file(
    path, state, assignments, ct_recode, tract_total, district_emp
):
    rows = 0
    xwalk_misses = 0
    xwalk_miss_employment = 0
    excluded_employment = 0
    ct_recoded_blocks = 0
    expected_fips = USPS_TO_FIPS[state]

    try:
        with gzip.open(path, 'rb') as compressed:
            with io.TextIOWrapper(
                compressed,
                encoding='utf-8-sig',
                errors='strict',
                newline='',
            ) as source:
                reader = csv.DictReader(source)
                selected = _required_headers(
                    reader.fieldnames, ('w_geocode', 'C000'), path
                )
                for row in reader:
                    rows += 1
                    block = (
                        row.get(selected['w_geocode']) or ''
                    ).strip()
                    if len(block) != 15 or not block.isdigit():
                        raise SystemExit(
                            '%s row %d has invalid w_geocode %r' %
                            (path, reader.line_num, block)
                        )
                    if block[:2] != expected_fips:
                        raise SystemExit(
                            '%s row %d has state FIPS %s; expected %s for '
                            '%s' %
                            (
                                path, reader.line_num, block[:2],
                                expected_fips, state,
                            )
                        )
                    employment = _parse_nonnegative_int(
                        row.get(selected['C000']),
                        '%s row %d C000' % (path, reader.line_num),
                    )
                    tract11 = block[:11]
                    if state == 'CT':
                        recoded = ct_recode.get(tract11)
                        if recoded is None:
                            raise SystemExit(
                                'CT WAC tract is absent from the 2020-to-'
                                '2022 tract bridge: %s' % tract11
                            )
                        tract11 = recoded
                        ct_recoded_blocks += 1

                    tract_total[tract11] += employment
                    cd = assignments.get(block)
                    if cd is None:
                        xwalk_misses += 1
                        xwalk_miss_employment += employment
                    elif cd in ('98', 'ZZ'):
                        excluded_employment += employment
                    else:
                        district = '%s-%s' % (state, cd)
                        district_emp[(tract11, district)] += employment
    except (OSError, EOFError, gzip.BadGzipFile, UnicodeError) as exc:
        raise SystemExit(
            'failed to stream WAC file %s: %s' % (path, str(exc))
        ) from exc

    print(
        'weights source %s %s: %d rows, %d crosswalk misses' %
        (state, os.path.basename(path), rows, xwalk_misses)
    )
    return {
        'rows': rows,
        'xwalk_misses': xwalk_misses,
        'xwalk_miss_employment': xwalk_miss_employment,
        'excluded_employment': excluded_employment,
        'ct_recoded_blocks': ct_recoded_blocks,
    }


def _write_tract_weights(path, tract_total, district_emp):
    weights = {tract11: [] for tract11 in tract_total}
    rows = []
    for (tract11, district), employment in sorted(
        district_emp.items()
    ):
        total = tract_total.get(tract11, 0)
        if total <= 0 or employment <= 0:
            continue
        if employment > total:
            raise SystemExit(
                'weight numerator exceeds tract total for %s/%s: %d > %d' %
                (tract11, district, employment, total)
            )
        weight = employment / total
        rows.append((tract11, district, weight, employment))
        weights[tract11].append((district, weight))

    for tract11, tract_weights in weights.items():
        weight_sum = sum(weight for _district, weight in tract_weights)
        if weight_sum > 1.0000000001:
            raise SystemExit(
                'tract %s district weights sum above 1.0: %.12f' %
                (tract11, weight_sum)
            )

    part_path = path + '.part'
    try:
        with gzip.open(
            part_path,
            'wt',
            encoding='utf-8',
            errors='strict',
            newline='',
        ) as target:
            writer = csv.writer(target, lineterminator='\n')
            writer.writerow(['tract11', 'cd', 'weight', 'employment'])
            for tract11, district, weight, employment in rows:
                writer.writerow([
                    tract11,
                    district,
                    '%.6f' % weight,
                    employment,
                ])
        with gzip.open(part_path, 'rb') as validation:
            while validation.read(CHUNK_SIZE):
                pass
        os.replace(part_path, path)
    except (OSError, EOFError, gzip.BadGzipFile) as exc:
        raise SystemExit(
            'failed to write tract/CD weight cache %s: %s' %
            (path, str(exc))
        ) from exc

    print(
        'wrote tract/CD weight cache: %d rows, %d tracts at %s' %
        (len(rows), len(weights), path)
    )
    return weights, len(rows)


def _build_tract_weights(
    raw_root, crosswalk_path, ct_bridge_path, weight_path
):
    assignments, districts = _load_block_crosswalk(crosswalk_path)
    ct_recode = _load_ct_tract_recode(ct_bridge_path)
    selected, source_years = _latest_wac_files(raw_root)
    tract_total = defaultdict(int)
    district_emp = defaultdict(int)
    state_stats = {}

    for state in HMDA_STATES:
        state_stats[state] = _stream_wac_file(
            selected[state],
            state,
            assignments,
            ct_recode,
            tract_total,
            district_emp,
        )

    weights, n_rows = _write_tract_weights(
        weight_path, tract_total, district_emp
    )
    zero_employment = sum(
        1 for employment in tract_total.values() if employment == 0
    )
    print(
        '!! zero-employment tracts in latest WAC sources: %d' %
        zero_employment
    )
    print(
        '!! latest WAC crosswalk misses retained in tract totals: %d' %
        sum(stats['xwalk_misses'] for stats in state_stats.values())
    )
    return {
        'weights': weights,
        'districts': districts,
        'selected': selected,
        'source_years': source_years,
        'state_stats': state_stats,
        'n_rows': n_rows,
        'n_tracts': len(weights),
        'n_zero_employment_tracts': zero_employment,
    }


class _CollectionCancelled(Exception):
    pass


def _resolve_curl():
    configured = str(os.environ.get('CURL_BIN') or '').strip()
    candidates = (configured,) if configured else ('curl.exe', 'curl')
    for candidate in candidates:
        resolved = shutil.which(candidate)
        if resolved:
            return os.path.abspath(resolved)
        if configured and os.path.isfile(candidate):
            return os.path.abspath(candidate)
    detail = (
        'CURL_BIN=%r does not resolve to an executable' % configured
        if configured else
        'curl was not found on PATH'
    )
    raise SystemExit(
        '%s; CFPB HMDA collection requires curl because the upstream CDN '
        'rejects Python urllib for this public endpoint' % detail
    )


def _curl_command(
    curl_bin, url, byte_range=None, max_time=None, max_filesize=None
):
    command = [
        curl_bin,
        '--silent',
        '--show-error',
        '--fail',
        '--location',
        '--proto',
        '=https',
        '--proto-redir',
        '=https',
        '--connect-timeout',
        str(CURL_CONNECT_TIMEOUT),
        '--speed-limit',
        '1',
        '--speed-time',
        str(REQUEST_TIMEOUT),
        '--user-agent',
        USER_AGENT,
        '--header',
        'Accept: %s' % LAR_ACCEPT,
        '--header',
        'Referer: %s' % LAR_REFERER,
    ]
    if byte_range is not None:
        command.extend(('--range', str(byte_range)))
    if max_time is not None:
        command.extend(('--max-time', '%.3f' % max_time))
    if max_filesize is not None:
        command.extend(('--max-filesize', str(max_filesize)))
    command.extend((
        '--write-out',
        '\n%%{stderr}%s%%{http_code}\t%%{url_effective}\n' %
        CURL_META_PREFIX,
        url,
    ))
    return command


def _decode_curl_stderr(payload):
    return bytes(payload or b'').decode('utf-8', errors='replace')


def _curl_metadata(stderr_text):
    status = None
    final_url = None
    for line in reversed(str(stderr_text or '').splitlines()):
        if not line.startswith(CURL_META_PREFIX):
            continue
        fields = line[len(CURL_META_PREFIX):].split('\t', 1)
        if fields and fields[0].isdigit():
            status = int(fields[0])
        if len(fields) == 2 and fields[1].strip():
            final_url = fields[1].strip()
        break
    return status, final_url


def _curl_error_summary(stderr_text):
    lines = [
        line.strip()
        for line in str(stderr_text or '').splitlines()
        if line.strip() and not line.startswith(CURL_META_PREFIX)
    ]
    if not lines:
        return '<no curl diagnostic>'
    return lines[-1][-500:]


def _terminate_curl(process):
    try:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    except (OSError, subprocess.SubprocessError):
        pass


def _wait_for_request_start(throttle, cancel_event=None):
    with throttle['lock']:
        if cancel_event is not None and cancel_event.is_set():
            return False
        previous = throttle.get('last_request')
        if previous is not None:
            remaining = (
                REQUEST_START_SPACING -
                (time.monotonic() - previous)
            )
            if remaining > 0:
                if cancel_event is None:
                    time.sleep(remaining)
                elif cancel_event.wait(remaining):
                    return False
        if cancel_event is not None and cancel_event.is_set():
            return False
        throttle['last_request'] = time.monotonic()
        return True


def _canary_payload_is_csv(payload):
    try:
        text = payload.decode('utf-8-sig', errors='strict')
    except UnicodeError:
        return False
    first_line = text.split('\n', 1)[0].strip().casefold()
    return (
        ',' in first_line and
        'census_tract' in first_line and
        'action_taken' in first_line
    )


def _probe_canary(year, throttle, deadline, curl_bin):
    url = LAR_URL_TEMPLATE.format(state=CANARY_STATE, year=year)
    _wait_for_request_start(throttle)
    remaining_budget = deadline - time.monotonic()
    if remaining_budget <= 0:
        return False, 'shared canary budget exhausted'
    timeout = max(1.0, min(REQUEST_TIMEOUT, remaining_budget))
    try:
        completed = subprocess.run(
            _curl_command(
                curl_bin,
                url,
                byte_range='0-%d' % (CANARY_READ_BYTES - 1),
                max_time=timeout,
                max_filesize=CANARY_READ_BYTES,
            ),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout + 5,
            check=False,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, '%s: %s' % (type(exc).__name__, str(exc))

    stderr_text = _decode_curl_stderr(completed.stderr)
    status, _final_url = _curl_metadata(stderr_text)
    if (
        completed.returncode == 0 and
        status in (200, 206) and
        _canary_payload_is_csv(completed.stdout)
    ):
        return True, 'HTTP %d CSV via curl' % status
    if status is not None:
        return False, 'HTTP %d via curl' % status
    return False, 'curl exit %d: %s' % (
        completed.returncode,
        _curl_error_summary(stderr_text),
    )


def _canary_gate(
    year, interval, deadline, throttle, gate_state, curl_bin
):
    last_result = '<not probed>'
    while True:
        now = time.monotonic()
        if now >= deadline:
            elapsed = int((now - gate_state['run_started']) // 60)
            print(
                '!! CFPB canary budget exhausted after %dm; last result: '
                '%s' % (elapsed, last_result)
            )
            return False

        cleared, last_result = _probe_canary(
            year, throttle, deadline, curl_bin
        )
        gate_state['n_probes'] += 1
        if cleared:
            print(
                'CFPB access canary cleared on probe %d' %
                gate_state['n_probes']
            )
            return True

        elapsed = int(
            (time.monotonic() - gate_state['run_started']) // 60
        )
        if gate_state['n_probes'] % 10 == 0:
            print(
                'waiting on CFPB access: %dm elapsed' % elapsed
            )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            continue
        time.sleep(min(interval, remaining))


def _lmi_flag(value):
    text = str(value or '').strip()
    try:
        number = float(text)
    except ValueError:
        return False
    return math.isfinite(number) and number < 80.0


def _stream_lar_response(response, state, year, cancel_event=None):
    aggregates = defaultdict(int)
    n_rows = 0
    n_originations = 0
    label = 'HMDA LAR %s %s' % (state, year)

    with io.TextIOWrapper(
        response,
        encoding='utf-8-sig',
        errors='strict',
        newline='',
    ) as source:
        reader = csv.DictReader(source)
        selected = _required_headers(
            reader.fieldnames, LAR_REQUIRED_COLUMNS, label
        )
        for row in reader:
            if (
                cancel_event is not None and
                n_rows % 8192 == 0 and
                cancel_event.is_set()
            ):
                raise _CollectionCancelled()
            n_rows += 1
            if (
                str(row.get(selected['action_taken']) or '').strip()
                != '1'
            ):
                continue
            n_originations += 1
            tract = str(
                row.get(selected['census_tract']) or ''
            ).strip()
            if not tract:
                tract = '<blank>'
            purpose = (
                'home_purchase'
                if str(
                    row.get(selected['loan_purpose']) or ''
                ).strip() == '1'
                else 'other'
            )
            lmi = _lmi_flag(
                row.get(selected['tract_to_msa_income_percentage'])
            )
            aggregates[(tract, purpose, lmi)] += 1

    return {
        'aggregates': aggregates,
        'n_rows': n_rows,
        'n_originations': n_originations,
    }


def _stream_state_with_curl(
    curl_bin, url, state, year, cancel_event
):
    try:
        process = subprocess.Popen(
            _curl_command(curl_bin, url),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
    except OSError as exc:
        raise SystemExit(
            'failed to start curl for HMDA LAR %s: %s' %
            (state, str(exc))
        ) from exc

    if process.stdout is None or process.stderr is None:
        _terminate_curl(process)
        raise SystemExit(
            'internal error: curl pipes unavailable for HMDA LAR %s' %
            state
        )

    result = None
    parse_error = None
    try:
        result = _stream_lar_response(
            process.stdout,
            state,
            year,
            cancel_event=cancel_event,
        )
    except BaseException as exc:
        parse_error = exc
        _terminate_curl(process)

    try:
        stderr_payload = process.stderr.read()
    except OSError as exc:
        stderr_payload = (
            'failed to read curl stderr: %s' % str(exc)
        ).encode('utf-8', errors='replace')
    try:
        returncode = process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        _terminate_curl(process)
        returncode = process.wait(timeout=5)

    stderr_text = _decode_curl_stderr(stderr_payload)
    status, final_url = _curl_metadata(stderr_text)

    if isinstance(parse_error, _CollectionCancelled):
        return {'status': 'cancelled'}
    if (
        status == 403 or
        (
            returncode == 22 and
            re.search(r'\b403\b', stderr_text)
        )
    ):
        return {
            'status': 'failed_403',
            'diagnostic': _curl_error_summary(stderr_text),
        }
    if returncode != 0:
        raise SystemExit(
            'failed to stream HMDA LAR for %s from %s: curl exit %d; %s' %
            (
                state,
                url,
                returncode,
                _curl_error_summary(stderr_text),
            )
        )
    if parse_error is not None:
        raise parse_error
    if status not in (200, 206) or not final_url:
        raise SystemExit(
            'failed to stream HMDA LAR for %s from %s: invalid curl '
            'completion metadata status=%r final_url=%r' %
            (state, url, status, final_url)
        )
    if result is None:
        raise SystemExit(
            'internal error: curl completed without an HMDA result for %s' %
            state
        )
    return {
        'status': 'ok',
        'result': result,
        'final_url': final_url,
    }


def _collect_state(
    state, year, throttle, cancel_event, curl_bin
):
    url = LAR_URL_TEMPLATE.format(state=state, year=year)
    final_url = None
    result = None
    for attempt in range(1, STATE_MAX_ATTEMPTS + 1):
        if cancel_event.is_set():
            return {'status': 'cancelled'}
        if not _wait_for_request_start(throttle, cancel_event):
            return {'status': 'cancelled'}
        print(
            'streaming HMDA LAR: %s %s (attempt %d/%d)' %
            (state, year, attempt, STATE_MAX_ATTEMPTS)
        )
        transport = _stream_state_with_curl(
            curl_bin,
            url,
            state,
            year,
            cancel_event,
        )
        if transport['status'] == 'ok':
            final_url = transport['final_url']
            result = transport['result']
            break
        if transport['status'] == 'cancelled':
            return {'status': 'cancelled'}
        if transport['status'] == 'failed_403':
            if attempt >= STATE_MAX_ATTEMPTS:
                print(
                    '!! %s HMDA LAR HTTP 403 exhausted %d attempts; '
                    'recording state failed' %
                    (state, STATE_MAX_ATTEMPTS)
                )
                return {'status': 'failed_403'}
            wait_seconds = STATE_BACKOFF_SECONDS[attempt - 1]
            print(
                '!! %s HMDA LAR HTTP 403 on attempt %d/%d; '
                'backing off %d seconds' %
                (
                    state,
                    attempt,
                    STATE_MAX_ATTEMPTS,
                    wait_seconds,
                )
            )
            if cancel_event.wait(wait_seconds):
                return {'status': 'cancelled'}
            continue
        raise SystemExit(
            'internal error: HMDA curl transport for %s returned %r' %
            (state, transport)
        )

    if result is None or final_url is None:
        raise SystemExit(
            'internal error: HMDA LAR retry loop ended without a result '
            'for %s' % state
        )

    cache = {
        'schema': 'hmda-state-agg-v1',
        'year': int(year),
        'state': state,
        'sources': {
            'lar': {
                'url': final_url,
                'year': int(year),
                'retrieved': _utc_iso(),
                'transport': 'curl',
            },
        },
        'n_lar_rows': result['n_rows'],
        'n_originations': result['n_originations'],
        'aggregates': [
            {
                'tract': key[0],
                'purpose_class': key[1],
                'lmi': key[2],
                'count': count,
            }
            for key, count in sorted(result['aggregates'].items())
        ],
    }
    print(
        'aggregated HMDA %s: %d LAR rows, %d originations, %d cells' %
        (
            state,
            result['n_rows'],
            result['n_originations'],
            len(cache['aggregates']),
        )
    )
    return {
        'status': 'ok',
        'cache': cache,
    }


def _load_state_cache(path, state, year):
    try:
        with open(path, 'r', encoding='utf-8') as source:
            cache = json.load(source)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SystemExit(
            'invalid HMDA aggregate cache %s; rerun with HMDA_FORCE=1: %s' %
            (path, str(exc))
        ) from exc

    if (
        not isinstance(cache, dict) or
        cache.get('schema') != 'hmda-state-agg-v1' or
        cache.get('year') != int(year) or
        cache.get('state') != state
    ):
        raise SystemExit(
            'HMDA aggregate cache identity mismatch in %s; rerun with '
            'HMDA_FORCE=1' % path
        )
    sources = cache.get('sources')
    if not isinstance(sources, dict):
        raise SystemExit(
            'HMDA aggregate cache lacks sources in %s; rerun with '
            'HMDA_FORCE=1' % path
        )
    lar_source = sources.get('lar')
    if (
        not isinstance(lar_source, dict) or
        not isinstance(lar_source.get('url'), str) or
        not isinstance(lar_source.get('retrieved'), str)
    ):
        raise SystemExit(
            'HMDA aggregate cache has invalid LAR source in %s; rerun '
            'with HMDA_FORCE=1' % path
        )

    for field in ('n_lar_rows', 'n_originations'):
        value = cache.get(field)
        if not isinstance(value, int) or value < 0:
            raise SystemExit(
                'HMDA aggregate cache has invalid %s in %s; rerun with '
                'HMDA_FORCE=1' % (field, path)
            )

    aggregates = cache.get('aggregates')
    if not isinstance(aggregates, list):
        raise SystemExit(
            'HMDA aggregate cache has invalid aggregates in %s; rerun '
            'with HMDA_FORCE=1' % path
        )
    aggregate_total = 0
    for index, cell in enumerate(aggregates):
        if not isinstance(cell, dict):
            raise SystemExit(
                '%s aggregate %d is not an object; rerun with '
                'HMDA_FORCE=1' % (path, index)
            )
        if (
            not isinstance(cell.get('tract'), str) or
            cell.get('purpose_class') not in (
                'home_purchase', 'other'
            ) or
            not isinstance(cell.get('lmi'), bool) or
            not isinstance(cell.get('count'), int) or
            cell.get('count') <= 0
        ):
            raise SystemExit(
                '%s aggregate %d is invalid: %r; rerun with '
                'HMDA_FORCE=1' % (path, index, cell)
            )
        aggregate_total += cell['count']
    if aggregate_total != cache['n_originations']:
        raise SystemExit(
            '%s aggregate counts total %d but n_originations is %d; rerun '
            'with HMDA_FORCE=1' %
            (path, aggregate_total, cache['n_originations'])
        )
    return cache


def _accept_state_result(state, result, path, caches):
    if not isinstance(result, dict):
        raise SystemExit(
            'internal error: HMDA worker for %s returned %r' %
            (state, result)
        )
    status = result.get('status')
    if status == 'ok':
        cache = result.get('cache')
        if not isinstance(cache, dict):
            raise SystemExit(
                'internal error: HMDA worker for %s omitted its cache' %
                state
            )
        dump_atomic(cache, path, separators=(',', ':'))
        caches[state] = cache
    elif status not in ('failed_403', 'cancelled'):
        raise SystemExit(
            'internal error: HMDA worker for %s returned status %r' %
            (state, status)
        )
    return status


def _collect_state_round(
    states, paths, year, throttle, caches, curl_bin
):
    queue = list(states)
    state_order = {
        state: index for index, state in enumerate(HMDA_STATES)
    }
    next_index = 0
    cancel_event = threading.Event()
    executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=COLLECTION_WORKERS
    )
    futures = {}
    failed_states = []
    consecutive_403_failures = 0
    flag_returned = False

    def submit_one():
        nonlocal next_index
        if next_index >= len(queue) or cancel_event.is_set():
            return False
        state = queue[next_index]
        next_index += 1
        future = executor.submit(
            _collect_state,
            state,
            year,
            throttle,
            cancel_event,
            curl_bin,
        )
        futures[future] = state
        return True

    for _unused in range(min(COLLECTION_WORKERS, len(queue))):
        submit_one()

    try:
        while futures and not flag_returned:
            done, _not_done = concurrent.futures.wait(
                tuple(futures),
                return_when=concurrent.futures.FIRST_COMPLETED,
            )
            ordered_done = sorted(
                done,
                key=lambda future: state_order[futures[future]],
            )
            for future in ordered_done:
                state = futures.pop(future)
                result = future.result()
                status = _accept_state_result(
                    state, result, paths[state], caches
                )
                if status == 'failed_403':
                    failed_states.append(state)
                    consecutive_403_failures += 1
                else:
                    consecutive_403_failures = 0

                if consecutive_403_failures >= FLAG_RETURN_STREAK:
                    flag_returned = True
                    cancel_event.set()
                    break
                submit_one()

        if flag_returned:
            for future in futures:
                future.cancel()
    except BaseException:
        cancel_event.set()
        for future in futures:
            future.cancel()
        executor.shutdown(wait=True)
        raise

    executor.shutdown(wait=True)
    if flag_returned:
        for future, state in list(futures.items()):
            if future.cancelled():
                continue
            result = future.result()
            _accept_state_result(
                state, result, paths[state], caches
            )
        failed_states = []

    return {
        'flag_returned': flag_returned,
        'failed_states': failed_states,
    }


def _collect_or_load_states(
    cache_root, year, force, canary_interval,
    canary_deadline, gate_state, curl_bin
):
    os.makedirs(cache_root, exist_ok=True)
    paths = {
        state: os.path.join(cache_root, '%s_agg.json' % state)
        for state in HMDA_STATES
    }
    caches = {}
    target_states = []
    for state in HMDA_STATES:
        path = paths[state]
        if os.path.exists(path) and not force:
            print('using cached HMDA aggregate: %s' % path)
            caches[state] = _load_state_cache(path, state, year)
        else:
            target_states.append(state)

    if not target_states:
        return caches, [], False

    throttle = {
        'lock': threading.Lock(),
        'last_request': None,
    }
    if not _canary_gate(
        year,
        canary_interval,
        canary_deadline,
        throttle,
        gate_state,
        curl_bin,
    ):
        return caches, list(target_states), True

    reentries = 0
    while True:
        pending_states = [
            state for state in target_states if state not in caches
        ]
        if not pending_states:
            return caches, [], False

        round_result = _collect_state_round(
            pending_states,
            paths,
            year,
            throttle,
            caches,
            curl_bin,
        )
        if not round_result['flag_returned']:
            failed_states = sorted(
                round_result['failed_states'],
                key=HMDA_STATES.index,
            )
            return caches, failed_states, False

        pending_states = [
            state for state in target_states if state not in caches
        ]
        if reentries >= MAX_CANARY_REENTRIES:
            print(
                '!! CFPB flag-return canary re-entry cap reached; '
                'stopping collection'
            )
            return caches, pending_states, True
        reentries += 1
        print(
            '!! CFPB flag-return detected after %d consecutive state '
            'HTTP 403 failures; re-entering canary gate %d/%d' %
            (
                FLAG_RETURN_STREAK,
                reentries,
                MAX_CANARY_REENTRIES,
            )
        )
        if not _canary_gate(
            year,
            canary_interval,
            canary_deadline,
            throttle,
            gate_state,
            curl_bin,
        ):
            return caches, pending_states, True


def _allocate(caches, weights, districts):
    district_values = {
        district: {
            'n_originations': 0.0,
            'home_purchase': 0.0,
            'lmi': 0.0,
        }
        for district in districts
    }
    unmatched_tracts = set()
    unmatched_originations = 0
    residual_originations = 0.0
    total_originations = 0
    lar_sources = {}

    for state in HMDA_STATES:
        cache = caches.get(state)
        if cache is None:
            continue
        total_originations += cache['n_originations']
        lar_sources[state] = cache['sources']['lar']

        for cell in cache['aggregates']:
            tract = cell['tract']
            count = cell['count']
            tract_weights = weights.get(tract)
            if tract_weights is None:
                unmatched_tracts.add((state, tract))
                unmatched_originations += count
                continue

            weight_sum = sum(
                weight for _district, weight in tract_weights
            )
            if weight_sum > 1.0000000001:
                raise SystemExit(
                    'tract %s weights sum above 1.0 during allocation: '
                    '%.12f' % (tract, weight_sum)
                )
            residual_originations += count * max(0.0, 1.0 - weight_sum)
            for district, weight in tract_weights:
                if district not in district_values:
                    raise SystemExit(
                        'tract %s references unknown district %s' %
                        (tract, district)
                    )
                allocated = count * weight
                values = district_values[district]
                values['n_originations'] += allocated
                if cell['purpose_class'] == 'home_purchase':
                    values['home_purchase'] += allocated
                if cell['lmi']:
                    values['lmi'] += allocated

    allocated_total = sum(
        values['n_originations']
        for values in district_values.values()
    )
    allocated_lmi = sum(
        values['lmi'] for values in district_values.values()
    )
    district_result = {}
    for district in sorted(districts):
        values = district_values[district]
        total = values['n_originations']
        district_result[district] = {
            'n_originations': round(total, 1),
            'home_purchase_share': (
                round(values['home_purchase'] / total, 4)
                if total else 0.0
            ),
            'lmi_tract_share': (
                round(values['lmi'] / total, 4)
                if total else 0.0
            ),
        }

    if len(district_result) != 435:
        raise SystemExit(
            'HMDA metrics yielded %d districts; expected 435' %
            len(district_result)
        )
    tract_match = (
        (total_originations - unmatched_originations) /
        total_originations
        if total_originations else 0.0
    )
    return {
        'districts': district_result,
        'total_originations': total_originations,
        'allocated_originations': allocated_total,
        'allocated_lmi_originations': allocated_lmi,
        'tract_match': tract_match,
        'unmatched_tracts': unmatched_tracts,
        'unmatched_originations': unmatched_originations,
        'residual_originations': residual_originations,
        'lar_sources': lar_sources,
    }


def _lodes_sources(weight_result):
    files = {}
    for state in HMDA_STATES:
        path = weight_result['selected'][state]
        year = weight_result['source_years'][state]
        files[state] = {
            'url': LODES_URL_TEMPLATE.format(
                st=state.lower(), year=year
            ),
            'year': year,
            'file': os.path.basename(path),
            'retrieved': _utc_iso(os.path.getmtime(path)),
            'sha256': _sha256(path),
        }
    return files


def main():
    _configure_stdout()
    run_started = time.monotonic()
    data_root = os.environ.get('DEMO_DATA_ROOT')
    if not data_root:
        base = (
            os.environ.get('LOCALAPPDATA') or
            os.path.expanduser(r'~\AppData\Local')
        )
        data_root = os.path.join(base, 'Melange', 'demographics')

    year = _parse_year(os.environ.get('HMDA_YEAR', '2024'))
    force = os.environ.get('HMDA_FORCE', '0') == '1'
    canary_interval = _parse_positive_seconds(
        os.environ.get(
            'CANARY_INTERVAL', str(CANARY_INTERVAL_DEFAULT)
        ),
        'CANARY_INTERVAL',
    )
    canary_max_wait = _parse_positive_seconds(
        os.environ.get(
            'CANARY_MAX_WAIT', str(CANARY_MAX_WAIT_DEFAULT)
        ),
        'CANARY_MAX_WAIT',
    )
    canary_deadline = run_started + canary_max_wait
    gate_state = {
        'run_started': run_started,
        'n_probes': 0,
    }
    output_path = os.environ.get('HMDA_OUT')
    if not output_path:
        output_path = os.path.join(MB_ROOT, 'data', 'district', 'district_hmda.json')
    curl_bin = _resolve_curl()

    geo_root = os.path.join(data_root, 'geo')
    raw_root = os.path.join(data_root, 'lodes', 'raw')
    cache_root = os.path.join(data_root, 'hmda', year)
    crosswalk_path = os.path.join(
        geo_root, 'cd119_block_assign.csv.gz'
    )
    ct_bridge_path = os.path.join(
        geo_root, 'ct_2022_block_crosswalk.csv'
    )
    weight_path = os.path.join(
        geo_root, 'tract_cd_weights.csv.gz'
    )

    print('HMDA Phase H2 year %s; data root %s' % (year, data_root))
    print('CFPB HMDA transport: %s' % curl_bin)
    print('building tract/CD weights from latest raw WAC files')
    weight_result = _build_tract_weights(
        raw_root, crosswalk_path, ct_bridge_path, weight_path
    )

    print('collecting or loading state HMDA aggregates')
    caches, failed_states, access_blocked = (
        _collect_or_load_states(
            cache_root,
            year,
            force,
            canary_interval,
            canary_deadline,
            gate_state,
            curl_bin,
        )
    )
    if access_blocked:
        print(
            '!! CFPB access blocked: states ok/failed %d/%d; '
            'artifact not written' %
            (len(caches), len(failed_states))
        )
        raise SystemExit(1)
    if len(failed_states) > 5:
        print(
            '!! HMDA collection failure gate: states ok/failed %d/%d; '
            'artifact not written' %
            (len(caches), len(failed_states))
        )
        raise SystemExit(1)

    print('allocating HMDA aggregates to CD119 districts')
    allocation = _allocate(
        caches,
        weight_result['weights'],
        weight_result['districts'],
    )
    print(
        '!! HMDA tracts absent from tract/CD weights: %d tracts, '
        '%d originations' %
        (
            len(allocation['unmatched_tracts']),
            allocation['unmatched_originations'],
        )
    )
    total_originations = allocation['total_originations']
    allocated_originations = allocation['allocated_originations']
    weight_residual_share = (
        allocation['residual_originations'] / total_originations
        if total_originations else 0.0
    )
    national_lmi_share = (
        allocation['allocated_lmi_originations'] /
        allocated_originations
        if allocated_originations else 0.0
    )

    artifact = {
        'schema': 'district-hmda-v1',
        'generated_at': _utc_iso(),
        'year': int(year),
        'sources': {
            'hmda_lar': {
                'url_template': LAR_URL_TEMPLATE,
                'year': int(year),
                'transport': 'curl',
                'urls': {
                    state: allocation['lar_sources'][state]['url']
                    for state in sorted(allocation['lar_sources'])
                },
                'retrieved': {
                    state:
                        allocation['lar_sources'][state]['retrieved']
                    for state in sorted(allocation['lar_sources'])
                },
            },
            'lodes_wac': {
                'url_template': LODES_URL_TEMPLATE,
                'files': _lodes_sources(weight_result),
            },
            'block_crosswalk': _local_source(
                crosswalk_path,
                'DEMO_DATA_ROOT/geo/cd119_block_assign.csv.gz',
            ),
            'ct_tract_recode': _local_source(
                ct_bridge_path,
                'DEMO_DATA_ROOT/geo/ct_2022_block_crosswalk.csv',
            ),
            'tract_cd_weights': _local_source(
                weight_path,
                'DEMO_DATA_ROOT/geo/tract_cd_weights.csv.gz',
            ),
        },
        'method_notes': {
            'originations': 'action_taken == 1',
            'home_purchase': 'loan_purpose == 1',
            'lmi_tract': (
                'tract_to_msa_income_percentage is numeric and below 80; '
                'this is a tract-basis CRA framing, not a borrower-income '
                'classification'
            ),
            'lender_split_deferred': (
                'HMDA panel (agency codes) unavailable via automation as '
                'of 2026-07-25 — all public panel routes 403/auth-walled; '
                'CU-vs-bank mortgage split deferred'
            ),
            'tract_allocation': (
                'HMDA tract aggregates are allocated to CD119 districts '
                'using latest-available LODES workplace employment shares'
            ),
            'employment_weight_allocation_caveat': (
                'DECLARED ERROR TERM: workplace employment weights are a '
                'proxy for allocating residential mortgage activity; '
                'commuting patterns and within-tract housing/employment '
                'differences can bias district estimates'
            ),
            'ct_tract_recode': (
                'Connecticut LODES 2020 tract prefixes are recoded to '
                '2022 planning-region-based tract FIPS before matching '
                'HMDA %s tracts' % year
            ),
            'weight_residual_share_denominator': (
                'all HMDA originations; the numerator is the employment-'
                'weight residual for tracts present in the WAC universe, '
                'including 98/ZZ and block-crosswalk-miss shortfall'
            ),
            'national_lmi_share_denominator':
                'total allocated originations',
        },
        'districts': allocation['districts'],
        'coverage': {
            'n_states_ok': len(caches),
            'n_states_expected': len(HMDA_STATES),
            'n_states_failed': len(failed_states),
            'states_failed': failed_states,
            'n_originations': total_originations,
            'n_originations_allocated': round(
                allocated_originations, 1
            ),
            'tract_unmatched': {
                'n_tracts': len(allocation['unmatched_tracts']),
                'n_originations': allocation['unmatched_originations'],
            },
            'weight_residual_share': round(
                weight_residual_share, 4
            ),
            'tract_weight_cache': {
                'n_tracts': weight_result['n_tracts'],
                'n_rows': weight_result['n_rows'],
                'n_zero_employment_tracts':
                    weight_result['n_zero_employment_tracts'],
                'n_wac_crosswalk_miss_blocks': sum(
                    stats['xwalk_misses']
                    for stats in weight_result['state_stats'].values()
                ),
                'n_ct_recoded_blocks': sum(
                    stats['ct_recoded_blocks']
                    for stats in weight_result['state_stats'].values()
                ),
            },
        },
    }

    print('writing district HMDA artifact: %s' % output_path)
    dump_atomic(artifact, output_path, separators=(',', ':'))
    print(
        'wrote district_hmda.json: 435 districts, %.1f originations '
        'allocated, tract match %.1f pct, lmi share national %.1f pct, '
        'states ok/failed %d/%d' %
        (
            allocated_originations,
            allocation['tract_match'] * 100.0,
            national_lmi_share * 100.0,
            len(caches),
            len(failed_states),
        )
    )


if __name__ == '__main__':
    main()

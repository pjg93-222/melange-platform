# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT  # centralized root (arch review 2026-07-02)
"""LODES WAC COLLECTOR — workplace employment by congressional district.

Collector-cadence Phase B of the Melange demographic expansion.  This script is deliberately
not wired into run_pipeline.py.  It mirrors Census LEHD LODES8 WAC S000/JT01 files for the
50 states and DC, then streams them through Phase A's block-to-congressional-district cache
to build a long employment panel at district, state, and national levels.

The Census FIPS-to-USPS table is intentionally duplicated from fetch_cd_geography.py rather
than imported so that collector scripts remain standalone.  LODES_XWALK is the swappable
boundary parameter.

Source template:
  https://lehd.ces.census.gov/data/lodes/LODES8/{st}/wac/{st}_wac_S000_JT01_{year}.csv.gz

Dials:
  DEMO_DATA_ROOT — out-of-OneDrive raw-data root; defaults to
                   %LOCALAPPDATA%/Melange/demographics
  LODES_YEARS    — inclusive start-end range; defaults to "2002-2023"
  LODES_STATES   — comma-separated USPS subset; defaults to all 50 states plus DC
  LODES_FORCE    — set to "1" to re-download raw files; defaults to "0"
  LODES_XWALK    — block assignment gzip; defaults to
                   DEMO_DATA_ROOT/geo/cd119_block_assign.csv.gz
  LODES_MODE     — "fetch", "build", or "both"; defaults to "both"

Raw/cache outputs under DEMO_DATA_ROOT/lodes:
  raw/{st}_wac_S000_JT01_{year}.csv.gz
  lodes_cd_panel.csv.gz
  lodes_panel_manifest.json

Panel schema:
  geo,year,C000,CNS01,...,CNS20

Manifest schema:
  lodes-panel-v1, with LODES coverage/provenance, row checks, and panel metadata.
  In fetch-only mode xwalk and panel are null and row-check counters are zero.
"""
import csv,datetime,gzip,hashlib,io,json,os,sys,time,urllib.request
from moneyball_core import dump_atomic
sys.stdout.reconfigure(encoding='utf-8',errors='replace')

URL_TEMPLATE = (
    'https://lehd.ces.census.gov/data/lodes/LODES8/'
    '{st}/wac/{st}_wac_S000_JT01_{year}.csv.gz'
)
USER_AGENT = 'MelangeDemographics/1.0 (research; contact: repo owner)'
CHUNK_SIZE = 1024 * 1024
REQUEST_DELAY = 0.3
REQUEST_TIMEOUT = 120
COUNT_COLUMNS = (
    'C000',
    'CNS01', 'CNS02', 'CNS03', 'CNS04', 'CNS05',
    'CNS06', 'CNS07', 'CNS08', 'CNS09', 'CNS10',
    'CNS11', 'CNS12', 'CNS13', 'CNS14', 'CNS15',
    'CNS16', 'CNS17', 'CNS18', 'CNS19', 'CNS20',
)
PANEL_COLUMNS = ('geo', 'year') + COUNT_COLUMNS
ALL_STATES = (
    'ak', 'al', 'ar', 'az', 'ca', 'co', 'ct', 'dc', 'de', 'fl',
    'ga', 'hi', 'ia', 'id', 'il', 'in', 'ks', 'ky', 'la', 'ma',
    'md', 'me', 'mi', 'mn', 'mo', 'ms', 'mt', 'nc', 'nd', 'ne',
    'nh', 'nj', 'nm', 'nv', 'ny', 'oh', 'ok', 'or', 'pa', 'ri',
    'sc', 'sd', 'tn', 'tx', 'ut', 'va', 'vt', 'wa', 'wi', 'wv',
    'wy',
)

# Stable Census state/territory FIPS law table, duplicated from Phase A.
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


def _utc_iso(timestamp=None):
    if timestamp is None:
        value = datetime.datetime.now(datetime.timezone.utc)
    else:
        value = datetime.datetime.fromtimestamp(timestamp, datetime.timezone.utc)
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


def _parse_years(value):
    text = value.strip()
    pieces = text.split('-')
    if (len(pieces) != 2 or not pieces[0].isdigit() or
            not pieces[1].isdigit()):
        raise SystemExit(
            'invalid LODES_YEARS %r; expected inclusive start-end' % value
        )
    start = int(pieces[0])
    end = int(pieces[1])
    if start > end:
        raise SystemExit(
            'invalid LODES_YEARS %r; start exceeds end' % value
        )
    return list(range(start, end + 1))


def _parse_states(value):
    if not value or not value.strip():
        return list(ALL_STATES)

    requested = []
    unknown = []
    for piece in value.split(','):
        state = piece.strip().lower()
        if not state:
            continue
        if state not in ALL_STATES:
            unknown.append(state)
        elif state not in requested:
            requested.append(state)

    if unknown:
        raise SystemExit(
            'unknown LODES_STATES code(s) %s; valid codes: %s' %
            (', '.join(sorted(set(unknown))), ', '.join(ALL_STATES))
        )
    if not requested:
        raise SystemExit(
            'invalid LODES_STATES %r; expected comma-separated USPS codes' %
            value
        )
    requested_set = set(requested)
    return [state for state in ALL_STATES if state in requested_set]


def _parse_mode(value):
    mode = value.strip().lower()
    if mode not in ('fetch', 'build', 'both'):
        raise SystemExit(
            'invalid LODES_MODE %r; expected fetch, build, or both' % value
        )
    return mode


def _raw_name(state, year):
    return '%s_wac_S000_JT01_%d.csv.gz' % (state, year)


def _file_entry(status, path=None):
    if path is None:
        return {'status': status, 'bytes': None, 'retrieved': None}
    return {
        'status': status,
        'bytes': os.path.getsize(path),
        'retrieved': _utc_iso(os.path.getmtime(path)),
    }


def _wait_for_request(throttle):
    last_request = throttle.get('last_request')
    if last_request is not None:
        remaining = REQUEST_DELAY - (time.monotonic() - last_request)
        if remaining > 0:
            time.sleep(remaining)
    throttle['last_request'] = time.monotonic()


def _download_file(state, year, path, force, throttle):
    label = '%s_%d' % (state, year)
    url = URL_TEMPLATE.format(st=state, year=year)
    cached = os.path.exists(path)
    if cached and not force:
        print('cached %s: %s' % (label, path))
        return _file_entry('ok', path)

    part_path = path + '.part'
    for attempt in range(4):
        try:
            print('downloading %s (attempt %d/4): %s' %
                  (label, attempt + 1, url))
            request = urllib.request.Request(
                url, headers={'User-Agent': USER_AGENT}
            )
            _wait_for_request(throttle)
            with urllib.request.urlopen(
                request, timeout=REQUEST_TIMEOUT
            ) as response:
                with open(part_path, 'wb') as target:
                    while True:
                        chunk = response.read(CHUNK_SIZE)
                        if not chunk:
                            break
                        target.write(chunk)
            try:
                with gzip.open(part_path, 'rb') as validation:
                    while True:
                        chunk = validation.read(CHUNK_SIZE)
                        if not chunk:
                            break
            except (gzip.BadGzipFile, EOFError, OSError) as exc:
                failure = exc
                try:
                    os.remove(part_path)
                except FileNotFoundError:
                    pass
            else:
                os.replace(part_path, path)
                print('downloaded %s: %s' % (label, path))
                return _file_entry('ok', path)
        except urllib.error.HTTPError as exc:
            if exc.code in (404, 410):
                if cached:
                    print('!! permanently missing %s (HTTP %d); '
                          'kept cached file: %s' %
                          (label, exc.code, path))
                    return _file_entry('error_kept_cached', path)
                print('!! permanently missing %s (HTTP %d): %s' %
                      (label, exc.code, url))
                return _file_entry('missing')
            failure = exc
        except Exception as exc:
            failure = exc

        if attempt == 3:
            if cached:
                print('!! re-download failed for %s after 4 attempts; '
                      'kept cached file: %s (%s)' %
                      (label, path, str(failure)))
                return _file_entry('error_kept_cached', path)
            print('!! download failed for %s after 4 attempts: %s' %
                  (label, str(failure)))
            return _file_entry('error')

        delay = 3 * (attempt + 1)
        print('!! download retry for %s in %d seconds: %s' %
              (label, delay, str(failure)))
        time.sleep(delay)


def _fetch_files(raw_root, states, years, force):
    os.makedirs(raw_root, exist_ok=True)
    files = {}
    throttle = {'last_request': None}
    for state in states:
        for year in years:
            key = '%s_%d' % (state, year)
            path = os.path.join(raw_root, _raw_name(state, year))
            files[key] = _download_file(
                state, year, path, force, throttle
            )
    return files


def _local_files(raw_root, states, years):
    files = {}
    for state in states:
        for year in years:
            key = '%s_%d' % (state, year)
            path = os.path.join(raw_root, _raw_name(state, year))
            if os.path.exists(path):
                files[key] = _file_entry('ok', path)
            else:
                files[key] = _file_entry('absent_local')
    return files


def _load_crosswalk(path):
    if not os.path.exists(path):
        raise SystemExit('LODES_XWALK does not exist: %s' % path)

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
                missing_fields = [
                    field for field in ('block_geoid', 'cd')
                    if row.get(field) is None
                ]
                if missing_fields:
                    raise SystemExit(
                        '%s row %d has missing expected field(s): %s' %
                        (path, reader.line_num, ', '.join(missing_fields))
                    )
                block_geoid = (row.get('block_geoid') or '').strip()
                district = (row.get('cd') or '').strip()
                if len(block_geoid) != 15 or not block_geoid.isdigit():
                    raise SystemExit(
                        '%s row %d has invalid block_geoid %r' %
                        (path, reader.line_num, block_geoid)
                    )
                if (len(district) != 2 or
                        not (district.isdigit() or district == 'ZZ')):
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


def _add_counts(aggregates, key, counts):
    current = aggregates.get(key)
    if current is None:
        aggregates[key] = list(counts)
        return
    for index, value in enumerate(counts):
        current[index] += value


def _stream_raw_file(path, label, year, assignments, aggregates):
    mismatch_rows = 0
    xwalk_misses = 0
    required = ('w_geocode',) + COUNT_COLUMNS

    with gzip.open(path, 'rb') as compressed:
        with io.TextIOWrapper(
            compressed,
            encoding='utf-8-sig',
            errors='strict',
            newline=''
        ) as source:
            reader = csv.DictReader(source)
            fieldnames = reader.fieldnames or []
            missing_columns = [
                column for column in required if column not in fieldnames
            ]
            if missing_columns:
                raise SystemExit(
                    '%s is missing required WAC column(s): %s' %
                    (path, ', '.join(missing_columns))
                )

            for row in reader:
                block_geoid = (row.get('w_geocode') or '').strip()
                if len(block_geoid) != 15 or not block_geoid.isdigit():
                    raise SystemExit(
                        '%s row %d has invalid w_geocode %r' %
                        (path, reader.line_num, block_geoid)
                    )
                state_fips = block_geoid[:2]
                if state_fips not in FIPS_TO_USPS:
                    raise SystemExit(
                        '%s row %d has unmapped state FIPS %r' %
                        (path, reader.line_num, state_fips)
                    )

                counts = []
                for column in COUNT_COLUMNS:
                    text = (row.get(column) or '').strip()
                    try:
                        counts.append(int(text))
                    except ValueError as exc:
                        raise SystemExit(
                            '%s row %d has invalid %s count %r' %
                            (path, reader.line_num, column, text)
                        ) from exc

                if sum(counts[1:]) != counts[0]:
                    mismatch_rows += 1

                state = FIPS_TO_USPS[state_fips]
                district = assignments.get(block_geoid)
                if district is None:
                    xwalk_misses += 1
                elif district not in ('98', 'ZZ'):
                    _add_counts(
                        aggregates, ('%s-%s' % (state, district), year),
                        counts
                    )
                _add_counts(aggregates, (state, year), counts)
                _add_counts(aggregates, ('US', year), counts)

    if mismatch_rows:
        print('!! %s has %d rows where sum(CNS01..CNS20) != C000' %
              (label, mismatch_rows))
    if xwalk_misses:
        print('!! %s has %d blocks missing from the crosswalk' %
              (label, xwalk_misses))
    return mismatch_rows, xwalk_misses


def _write_panel(path, aggregates):
    part_path = path + '.part'
    n_rows = 0
    geos = set()
    with gzip.open(
        part_path, 'wt', encoding='utf-8', errors='strict', newline=''
    ) as target:
        writer = csv.writer(target, lineterminator='\n')
        writer.writerow(PANEL_COLUMNS)
        for (geo, year), counts in sorted(aggregates.items()):
            if counts[0] <= 0 and not any(value > 0 for value in counts[1:]):
                continue
            writer.writerow([geo, year] + counts)
            n_rows += 1
            geos.add(geo)
    os.replace(part_path, path)
    return {
        'path': path,
        'n_rows': n_rows,
        'n_geos': len(geos),
    }


def _build_panel(raw_root, panel_path, xwalk_path, states, years, files):
    assignments = _load_crosswalk(xwalk_path)
    aggregates = {}
    mismatch_rows = 0
    xwalk_misses = 0

    for state in states:
        for year in years:
            key = '%s_%d' % (state, year)
            path = os.path.join(raw_root, _raw_name(state, year))
            if not os.path.exists(path):
                if files[key]['status'] in ('ok', 'error_kept_cached'):
                    raise SystemExit(
                        'raw file disappeared before build for %s: %s' %
                        (key, path)
                    )
                continue
            file_mismatches, file_misses = _stream_raw_file(
                path, key, year, assignments, aggregates
            )
            mismatch_rows += file_mismatches
            xwalk_misses += file_misses

    panel = _write_panel(panel_path, aggregates)
    return {
        'xwalk': {'path': xwalk_path, 'sha256': _sha256(xwalk_path)},
        'row_checks': {
            'cns_mismatch_rows': mismatch_rows,
            'xwalk_miss_blocks': xwalk_misses,
        },
        'panel': panel,
    }


def _coverage(files):
    n_ok = 0
    n_missing = 0
    n_error = 0
    missing_state_years = []
    for key, detail in files.items():
        status = detail['status']
        if status == 'ok':
            n_ok += 1
        elif status in ('missing', 'absent_local'):
            n_missing += 1
            missing_state_years.append(key)
        elif status == 'error':
            n_error += 1
            missing_state_years.append(key)
        elif status == 'error_kept_cached':
            n_error += 1
        else:
            raise SystemExit(
                'internal error: unknown file status %r for %s' %
                (status, key)
            )
    return {
        'n_ok': n_ok,
        'n_missing': n_missing,
        'n_error': n_error,
        'missing_state_years': missing_state_years,
    }


def _manifest(years, states, files, coverage, build_result=None):
    if build_result is None:
        xwalk = None
        row_checks = {
            'cns_mismatch_rows': 0,
            'xwalk_miss_blocks': 0,
        }
        panel = None
    else:
        xwalk = build_result['xwalk']
        row_checks = build_result['row_checks']
        panel = build_result['panel']

    return {
        'schema': 'lodes-panel-v1',
        'generated_at': _utc_iso(),
        'lodes_version': 'LODES8',
        'job_type': 'JT01',
        'segment': 'S000',
        'years': years,
        'states': states,
        'xwalk': xwalk,
        'files': files,
        'coverage': coverage,
        'row_checks': row_checks,
        'panel': panel,
    }


def main():
    data_root = os.environ.get('DEMO_DATA_ROOT')  # out-of-OneDrive demographic raw store
    if not data_root:
        base = os.environ.get('LOCALAPPDATA') or os.path.expanduser(r'~\AppData\Local')
        data_root = os.path.join(base, 'Melange', 'demographics')

    years = _parse_years(os.environ.get('LODES_YEARS', '2002-2023'))
    states = _parse_states(os.environ.get('LODES_STATES'))
    force = os.environ.get('LODES_FORCE', '0') == '1'
    mode = _parse_mode(os.environ.get('LODES_MODE', 'both'))
    xwalk_path = os.environ.get('LODES_XWALK')
    if not xwalk_path:
        xwalk_path = os.path.join(
            data_root, 'geo', 'cd119_block_assign.csv.gz'
        )

    lodes_root = os.path.join(data_root, 'lodes')
    raw_root = os.path.join(lodes_root, 'raw')
    panel_path = os.path.join(lodes_root, 'lodes_cd_panel.csv.gz')
    manifest_path = os.path.join(
        lodes_root, 'lodes_panel_manifest.json'
    )
    os.makedirs(lodes_root, exist_ok=True)

    if mode in ('fetch', 'both'):
        files = _fetch_files(raw_root, states, years, force)
    else:
        files = _local_files(raw_root, states, years)

    build_result = None
    if mode in ('build', 'both'):
        build_result = _build_panel(
            raw_root, panel_path, xwalk_path, states, years, files
        )

    coverage = _coverage(files)
    dump_atomic(
        _manifest(years, states, files, coverage, build_result),
        manifest_path,
        indent=2
    )

    if mode == 'fetch':
        print('mirrored LODES8 JT01: files ok/missing/error = %d/%d/%d '
              '(%d years x %d states)' %
              (coverage['n_ok'], coverage['n_missing'],
               coverage['n_error'], len(years), len(states)))
    else:
        panel = build_result['panel']
        checks = build_result['row_checks']
        print('wrote lodes_cd_panel.csv.gz: %d geos x %d years, %d rows; '
              'files ok/missing/error = %d/%d/%d; xwalk_miss=%d' %
              (panel['n_geos'], len(years), panel['n_rows'],
               coverage['n_ok'], coverage['n_missing'],
               coverage['n_error'], checks['xwalk_miss_blocks']))


if __name__ == '__main__':
    main()

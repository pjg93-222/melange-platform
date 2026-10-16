# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT  # centralized root (arch review 2026-07-02)
"""GEOGRAPHY BACKBONE COLLECTOR — Census block-to-congressional-district assignments.

Collector-cadence Phase A of the Melange demographic expansion.  This script is deliberately
not wired into run_pipeline.py.  It downloads and caches the Census Bureau's national Block
Equivalency Files, normalizes them to compact two-column gzip caches, and derives the current
district-geography data-plane artifact.

Sources:
  cd119: https://www2.census.gov/programs-surveys/decennial/rdo/mapping-files/2025/119-congressional-district-befs/cd119.zip
  cd118: https://www2.census.gov/programs-surveys/decennial/rdo/mapping-files/2023/118-congressional-district-bef/cd118.zip
         as published (incorporates Census's 2022-12-16 AR corrections)

Dials:
  DEMO_DATA_ROOT — out-of-OneDrive raw-data root; defaults to
                   %LOCALAPPDATA%/Melange/demographics
  GEO_FORCE      — set to "1" to re-download source archives; defaults to "0"

Raw/cache outputs under DEMO_DATA_ROOT/geo:
  cd119.zip, cd118.zip, cd119_block_assign.csv.gz, cd118_block_assign.csv.gz

Repo-plane output schema:
  district_geography.json {
    "schema": "district-geography-v1",
    "generated_at": <UTC ISO>,
    "congress": 119,
    "sources": {
      "cd119": {"url": <str>, "sha256": <str>, "retrieved": <UTC ISO>},
      "cd118": {"url": <str>, "sha256": <str>, "retrieved": <UTC ISO>}
    },
    "states": {
      "<USPS>": {
        "fips": "<2-char>",
        "n_voting_districts": <int>,
        "districts": ["<2-char>", ...],
        "redrawn_since_118": ["<2-char>", ...]
      }, ... exactly 50 states
    },
    "non_voting": {"<USPS>": "98", ...},
    "unassigned_zz_states": ["<USPS>", ...],
    "notes": {"co_split_block": <str>}
  }
"""
import csv,datetime,gzip,hashlib,io,json,os,sys,time,urllib.error,urllib.request,zipfile,zlib
from moneyball_core import dump_atomic
sys.stdout.reconfigure(encoding='utf-8',errors='replace')

URLS = {
    'cd119': 'https://www2.census.gov/programs-surveys/decennial/rdo/mapping-files/2025/119-congressional-district-befs/cd119.zip',
    'cd118': 'https://www2.census.gov/programs-surveys/decennial/rdo/mapping-files/2023/118-congressional-district-bef/cd118.zip',
}
USER_AGENT = 'MelangeDemographics/1.0 (research; contact: repo owner)'
CO_SPLIT_BLOCK = '080010096072000'
CHUNK_SIZE = 1024 * 1024

# Stable Census state/territory FIPS law table: 50 states, DC, PR, and Island Areas.
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
VOTING_STATES = (
    'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA',
    'HI', 'ID', 'IL', 'IN', 'IA', 'KS', 'KY', 'LA', 'ME', 'MD',
    'MA', 'MI', 'MN', 'MS', 'MO', 'MT', 'NE', 'NV', 'NH', 'NJ',
    'NM', 'NY', 'NC', 'ND', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC',
    'SD', 'TN', 'TX', 'UT', 'VT', 'VA', 'WA', 'WV', 'WI', 'WY',
)
USPS_TO_FIPS = {usps: fips for fips, usps in FIPS_TO_USPS.items()}


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
    print(
        '!! preserved invalid cached %s as %s' %
        (label, corrupt_path)
    )


def _download_source(label, url, path, force):
    if os.path.exists(path) and not force:
        try:
            _validate_zip(path, label)
        except OSError as exc:
            print('!! cached %s failed validation: %s' % (label, str(exc)))
            _preserve_corrupt_cache(path, label)
        else:
            print('cached %s: %s' % (label, path))
            retrieved = _utc_iso(os.path.getmtime(path))
            return {'url': url, 'sha256': _sha256(path), 'retrieved': retrieved}

    part_path = path + '.part'
    for attempt in range(4):
        try:
            print('downloading %s (attempt %d/4): %s' % (label, attempt + 1, url))
            request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
            with urllib.request.urlopen(request, timeout=120) as response:
                with open(part_path, 'wb') as target:
                    while True:
                        chunk = response.read(CHUNK_SIZE)
                        if not chunk:
                            break
                        target.write(chunk)
            _validate_zip(part_path, label)
            os.replace(part_path, path)
            retrieved = _utc_iso()
            print('downloaded %s: %s' % (label, path))
            return {'url': url, 'sha256': _sha256(path), 'retrieved': retrieved}
        except Exception as exc:
            _discard_part(part_path)
            if (isinstance(exc, urllib.error.HTTPError)
                    and 400 <= exc.code < 500 and exc.code != 429):
                raise SystemExit('download failed for %s: HTTP %d: %s' %
                                 (label, exc.code, str(exc))) from exc
            if attempt == 3:
                raise SystemExit('download failed for %s after 4 attempts: %s' %
                                 (label, str(exc))) from exc
            delay = 3 * (attempt + 1)
            print('!! download retry for %s in %d seconds: %s' %
                  (label, delay, str(exc)))
            time.sleep(delay)


def _find_national_member(archive, label):
    members = archive.namelist()
    matches = [
        member for member in members
        if not member.endswith('/') and 'national' in member.lower()
    ]
    if len(matches) != 1:
        listing = ', '.join(members) if members else '<empty archive>'
        raise SystemExit(
            '%s archive has %d NATIONAL member matches; all members: %s' %
            (label, len(matches), listing)
        )
    return matches[0]


def _parse_and_normalize(label, archive_path, cache_path):
    voting_sets = {}
    non_voting = set()
    unassigned_zz = set()
    special_codes = []
    rows_seen = 0
    rows_written = 0
    part_path = cache_path + '.part'

    with zipfile.ZipFile(archive_path, 'r') as archive:
        member = _find_national_member(archive, label)
        print('parsing %s NATIONAL member: %s' % (label, member))

        with io.TextIOWrapper(
            archive.open(member, 'r'), encoding='utf-8-sig', errors='strict', newline=''
        ) as sample_source:
            sample = sample_source.read(1024)
        try:
            dialect = csv.Sniffer().sniff(sample)
        except csv.Error:
            dialect = csv.excel

        with gzip.open(
            part_path, 'wt', encoding='utf-8', errors='strict', newline=''
        ) as normalized:
            writer = csv.writer(normalized, lineterminator='\n')
            writer.writerow(['block_geoid', 'cd'])

            def record_assignment(block_geoid, district):
                writer.writerow([block_geoid, district])
                state = FIPS_TO_USPS[block_geoid[:2]]
                if district == '98':
                    non_voting.add(state)
                elif district == 'ZZ':
                    unassigned_zz.add(state)
                else:
                    by_district = voting_sets.setdefault(state, {})
                    by_district.setdefault(district, set()).add(block_geoid)

            with io.TextIOWrapper(
                archive.open(member, 'r'),
                encoding='utf-8-sig',
                errors='strict',
                newline=''
            ) as source:
                reader = csv.reader(source, dialect=dialect)
                first_record = True
                for row in reader:
                    if not row or not any(field.strip() for field in row):
                        continue
                    if first_record:
                        first_record = False
                        if not row[0].strip().isdigit():
                            continue
                    if len(row) < 2:
                        raise SystemExit(
                            '%s %s row %d has fewer than two columns' %
                            (label, member, reader.line_num)
                        )
                    block_geoid = row[0].strip()
                    district = row[1].strip()
                    if len(block_geoid) != 15 or not block_geoid.isdigit():
                        raise SystemExit(
                            '%s %s row %d has invalid block GEOID %r' %
                            (label, member, reader.line_num, block_geoid)
                        )
                    if (len(district) != 2 or
                            not (district.isdigit() or district == 'ZZ')):
                        raise SystemExit(
                            '%s %s row %d has invalid district code %r' %
                            (label, member, reader.line_num, district)
                        )
                    state_fips = block_geoid[:2]
                    if state_fips not in FIPS_TO_USPS:
                        raise SystemExit(
                            '%s %s row %d has unmapped state FIPS %r' %
                            (label, member, reader.line_num, state_fips)
                        )

                    rows_seen += 1
                    if block_geoid == CO_SPLIT_BLOCK:
                        special_codes.append(district)
                        continue
                    record_assignment(block_geoid, district)
                    rows_written += 1

            collapsed = 0
            if special_codes:
                if len(special_codes) == 1:
                    chosen_district = special_codes[0]
                else:
                    if '08' not in special_codes:
                        raise SystemExit(
                            '%s split block %s appears %d times without CD 08: %s' %
                            (label, CO_SPLIT_BLOCK, len(special_codes),
                             ', '.join(special_codes))
                        )
                    chosen_district = '08'
                    collapsed = len(special_codes) - 1
                record_assignment(CO_SPLIT_BLOCK, chosen_district)
                rows_written += 1

    os.replace(part_path, cache_path)
    print('normalized %s: %d source rows -> %d cache rows at %s '
          '(collapsed %d duplicate rows)' %
          (label, rows_seen, rows_written, cache_path, collapsed))
    return {
        'voting_sets': voting_sets,
        'non_voting': non_voting,
        'unassigned_zz': unassigned_zz,
        'collapsed': collapsed,
    }


def _build_artifact(cd119, cd118, sources):
    states = {}
    for state in sorted(VOTING_STATES):
        current = cd119['voting_sets'].get(state, {})
        prior = cd118['voting_sets'].get(state, {})
        districts = sorted(current)
        redrawn = [
            district for district in districts
            if current[district] != prior.get(district, set())
        ]
        states[state] = {
            'fips': USPS_TO_FIPS[state],
            'n_voting_districts': len(districts),
            'districts': districts,
            'redrawn_since_118': redrawn,
        }

    total_districts = sum(
        detail['n_voting_districts'] for detail in states.values()
    )
    if total_districts != 435:
        print('!! voting-district invariant failed: expected 435, found %d' %
              total_districts)
        print('state  n_voting_districts  districts')
        for state, detail in states.items():
            print('%-5s  %-18d  %s' %
                  (state, detail['n_voting_districts'],
                   ','.join(detail['districts'])))
        raise SystemExit(1)

    collapsed = cd119['collapsed'] + cd118['collapsed']
    return {
        'schema': 'district-geography-v1',
        'generated_at': _utc_iso(),
        'congress': 119,
        'sources': sources,
        'states': states,
        'non_voting': {
            state: '98' for state in sorted(cd119['non_voting'])
        },
        'unassigned_zz_states': sorted(cd119['unassigned_zz']),
        'notes': {
            'co_split_block':
                '%s tabulated to CD 08 per Census; collapsed %d duplicate rows' %
                (CO_SPLIT_BLOCK, collapsed)
        },
    }


def _crosscheck_legislators(states):
    path = os.path.join(MB_ROOT, 'fec', 'legislators.csv')
    if not os.path.exists(path):
        print('SKIP legislators cross-check (no legislators.csv in data plane)')
        return

    counts = {state: 0 for state in states}
    try:
        with open(path, 'r', encoding='utf-8-sig', errors='strict', newline='') as source:
            for row in csv.DictReader(source):
                if (row.get('type') or '').strip() != 'rep':
                    continue
                state = (row.get('state') or '').strip().upper()
                if state in counts:
                    # A "0" or blank district is still one at-large representative row.
                    counts[state] += 1
    except Exception as exc:
        print('!! WARNING legislators cross-check failed: %s' % str(exc))
        return

    for state in sorted(states):
        expected = states[state]['n_voting_districts']
        actual = counts[state]
        if actual != expected:
            print('!! WARNING legislators.csv %s: %d rep rows vs %d voting districts' %
                  (state, actual, expected))


def main():
    data_root = os.environ.get('DEMO_DATA_ROOT')  # out-of-OneDrive demographic raw store
    if not data_root:
        base = os.environ.get('LOCALAPPDATA') or os.path.expanduser(r'~\AppData\Local')
        data_root = os.path.join(base, 'Melange', 'demographics')
    force = os.environ.get('GEO_FORCE', '0') == '1'  # "1" forces re-download
    geo_root = os.path.join(data_root, 'geo')
    os.makedirs(geo_root, exist_ok=True)

    archive_paths = {
        'cd119': os.path.join(geo_root, 'cd119.zip'),
        'cd118': os.path.join(geo_root, 'cd118.zip'),
    }
    cache_paths = {
        'cd119': os.path.join(geo_root, 'cd119_block_assign.csv.gz'),
        'cd118': os.path.join(geo_root, 'cd118_block_assign.csv.gz'),
    }

    sources = {}
    for label in ('cd119', 'cd118'):
        sources[label] = _download_source(
            label, URLS[label], archive_paths[label], force
        )

    parsed = {}
    for label in ('cd119', 'cd118'):
        parsed[label] = _parse_and_normalize(
            label, archive_paths[label], cache_paths[label]
        )

    artifact = _build_artifact(parsed['cd119'], parsed['cd118'], sources)
    _crosscheck_legislators(artifact['states'])
    dump_atomic(
        artifact,
        os.path.join(MB_ROOT, 'data', 'district', 'district_geography.json'),
        indent=2
    )

    redrawn_states = [
        state for state, detail in artifact['states'].items()
        if detail['redrawn_since_118']
    ]
    redrawn_count = sum(
        len(detail['redrawn_since_118'])
        for detail in artifact['states'].values()
    )
    state_text = ', '.join(redrawn_states) if redrawn_states else 'none'
    print('wrote district_geography.json: 50 states, 435 voting districts, '
          '%d districts redrawn vs 118th (states: %s)' %
          (redrawn_count, state_text))


if __name__ == '__main__':
    main()

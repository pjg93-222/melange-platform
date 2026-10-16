# -*- coding: utf-8 -*-
import os as _o, sys as _s
_s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))
from paths import ROOT as MB_ROOT

"""ACS COMMUNITY-CONTEXT COLLECTOR — congressional districts and states.

This collector mirrors a small, documented set of official ACS one-year
table-based summary files.  It does not scrape data.census.gov and it does not
require a Census API key.  The default 2024 vintage is tabulated to the 119th
Congress and therefore joins directly to Melange's current district keys.

The selected detailed tables are the reproducible counterparts of the DP05
(demographic), DP03 (economic), and DP04 (housing) profile concepts:

  B01003  total population
  B01002  median age
  B19013  median household income
  B23025  civilian labor force, employed, and unemployed
  B17001  poverty universe and population below poverty
  B25003  occupied housing tenure
  B25077  median owner-occupied home value
  B25064  median gross rent

Census Gazetteer land area supplies the denominator for population density.
Both estimates and published 90% margins of error are retained.

Dials:
  DEMO_DATA_ROOT — out-of-OneDrive cache root; defaults to
                   %LOCALAPPDATA%/Melange/demographics
  ACS_YEAR       — ACS one-year vintage; defaults to "2024"
  ACS_CONGRESS   — congressional session; defaults to "119"
  ACS_MODE       — "fetch", "build", or "both"; defaults to "both"
  ACS_FORCE      — "1" re-downloads cached sources; defaults to "0"
  ACS_OUT        — repo-plane JSON output override

Output:
  MB_ROOT/district_community.json (schema district-community-acs-v1)
"""
import csv
import datetime
import hashlib
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.request
import zipfile

from moneyball_core import dump_atomic

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

USER_AGENT = 'MelangeDemographics/1.0 (research; contact: repo owner)'
CHUNK_SIZE = 1024 * 1024
REQUEST_TIMEOUT = 180
DOWNLOAD_ATTEMPTS = 4
RETRY_BASE_SECONDS = 3
SQ_METERS_PER_SQ_MILE = 2589988.110336
SCHEMA = 'district-community-acs-v1'
# Published Census jam values. The '<= -555555000' test alone silently let
# -222222222 (too few sample observations) and -333333333 (median falls in an
# open-ended interval) through as real negative estimates, where they became
# nonsense derived ratios on a record still flagged complete.
ACS_JAM_VALUES = frozenset((
    -111111111.0, -222222222.0, -333333333.0, -444444444.0,
    -555555555.0, -666666666.0, -777777777.0, -888888888.0,
    -999999999.0,
))

DATA_BASE = (
    'https://www2.census.gov/programs-surveys/acs/summary_file/'
    '{year}/table-based-SF/data/1YRData/'
)
DOC_BASE = (
    'https://www2.census.gov/programs-surveys/acs/summary_file/'
    '{year}/table-based-SF/documentation/'
)
GAZ_BASE = (
    'https://www2.census.gov/geo/docs/maps-data/data/gazetteer/'
    '{year}_Gazetteer/'
)

TABLES = {
    'B01003': {
        'filename': 'acsdt1y{year}-b01003.dat',
        'variables': {
            'population': ('B01003_E001', 'B01003_M001', 'B01003_001E'),
        },
        'profile_family': 'DP05',
    },
    'B01002': {
        'filename': 'acsdt1y{year}-b01002.dat',
        'variables': {
            'median_age': ('B01002_E001', 'B01002_M001', 'B01002_001E'),
        },
        'profile_family': 'DP05',
    },
    'B19013': {
        'filename': 'acsdt1y{year}-b19013.dat',
        'variables': {
            'median_household_income': (
                'B19013_E001', 'B19013_M001', 'B19013_001E'
            ),
        },
        'profile_family': 'DP03',
    },
    'B23025': {
        'filename': 'acsdt1y{year}-b23025.dat',
        'variables': {
            'population_16plus': (
                'B23025_E001', 'B23025_M001', 'B23025_001E'
            ),
            'civilian_labor_force': (
                'B23025_E003', 'B23025_M003', 'B23025_003E'
            ),
            'employed': ('B23025_E004', 'B23025_M004', 'B23025_004E'),
            'unemployed': ('B23025_E005', 'B23025_M005', 'B23025_005E'),
        },
        'profile_family': 'DP03',
    },
    'B17001': {
        'filename': 'acsdt1y{year}-b17001.dat',
        'variables': {
            'poverty_universe': (
                'B17001_E001', 'B17001_M001', 'B17001_001E'
            ),
            'below_poverty': (
                'B17001_E002', 'B17001_M002', 'B17001_002E'
            ),
        },
        'profile_family': 'DP03',
    },
    'B25003': {
        'filename': 'acsdt1y{year}-b25003.dat',
        'variables': {
            'occupied_housing_units': (
                'B25003_E001', 'B25003_M001', 'B25003_001E'
            ),
            'owner_occupied_units': (
                'B25003_E002', 'B25003_M002', 'B25003_002E'
            ),
            'renter_occupied_units': (
                'B25003_E003', 'B25003_M003', 'B25003_003E'
            ),
        },
        'profile_family': 'DP04',
    },
    'B25077': {
        'filename': 'acsdt1y{year}-b25077.dat',
        'variables': {
            'median_home_value': (
                'B25077_E001', 'B25077_M001', 'B25077_001E'
            ),
        },
        'profile_family': 'DP04',
    },
    'B25064': {
        'filename': 'acsdt1y{year}-b25064.dat',
        'variables': {
            'median_gross_rent': (
                'B25064_E001', 'B25064_M001', 'B25064_001E'
            ),
        },
        'profile_family': 'DP04',
    },
}

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

REGIONS = {
    'Northeast': frozenset(
        ('CT', 'ME', 'MA', 'NH', 'RI', 'VT', 'NJ', 'NY', 'PA')
    ),
    'Midwest': frozenset(
        ('IN', 'IL', 'MI', 'OH', 'WI', 'IA', 'KS', 'MN', 'MO',
         'NE', 'ND', 'SD')
    ),
    'South': frozenset(
        ('DE', 'DC', 'FL', 'GA', 'MD', 'NC', 'SC', 'VA', 'WV',
         'AL', 'KY', 'MS', 'TN', 'AR', 'LA', 'OK', 'TX')
    ),
    'West': frozenset(
        ('AZ', 'CO', 'ID', 'MT', 'NV', 'NM', 'UT', 'WY',
         'AK', 'CA', 'HI', 'OR', 'WA')
    ),
}


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


def _number(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    if (not math.isfinite(number) or number <= -555555000 or
            number in ACS_JAM_VALUES):
        return None
    return int(number) if number.is_integer() else number


def _ratio(numerator, denominator, multiplier=1.0):
    numerator = _number(numerator)
    denominator = _number(denominator)
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return numerator / denominator * multiplier


def _round(value, digits=6):
    number = _number(value)
    return None if number is None else round(number, digits)


def _region(usps):
    for label, states in REGIONS.items():
        if usps in states:
            return label
    return 'Other'


def _download(url, path, force):
    if os.path.exists(path) and not force:
        print('cached: %s' % path)
        return 'cached'
    os.makedirs(os.path.dirname(path), exist_ok=True)
    part = path + '.part'
    failure = None
    for attempt in range(DOWNLOAD_ATTEMPTS):
        try:
            request = urllib.request.Request(
                url, headers={'User-Agent': USER_AGENT}
            )
            print('downloading (attempt %d/%d): %s' % (
                attempt + 1, DOWNLOAD_ATTEMPTS, url
            ))
            with urllib.request.urlopen(
                    request, timeout=REQUEST_TIMEOUT) as response:
                with open(part, 'wb') as target:
                    while True:
                        chunk = response.read(CHUNK_SIZE)
                        if not chunk:
                            break
                        target.write(chunk)
            if os.path.getsize(part) <= 0:
                raise OSError('empty response')
            os.replace(part, path)
            return 'downloaded'
        except urllib.error.HTTPError as exc:
            try:
                os.remove(part)
            except FileNotFoundError:
                pass
            if 400 <= exc.code < 500 and exc.code not in (408, 429):
                raise SystemExit('download failed for %s: %s' % (url, exc))
            failure = exc
            if attempt + 1 < DOWNLOAD_ATTEMPTS:
                delay = RETRY_BASE_SECONDS * (attempt + 1)
                print('!! retry in %d seconds: %s' % (delay, exc))
                time.sleep(delay)
        except Exception as exc:
            failure = exc
            try:
                os.remove(part)
            except FileNotFoundError:
                pass
            if attempt + 1 < DOWNLOAD_ATTEMPTS:
                delay = RETRY_BASE_SECONDS * (attempt + 1)
                print('!! retry in %d seconds: %s' % (delay, exc))
                time.sleep(delay)
    raise SystemExit('download failed for %s: %s' % (url, failure))


def _source_specs(year, congress):
    year = str(year)
    yy = year[-2:]
    specs = {}
    for table, detail in TABLES.items():
        filename = detail['filename'].format(year=year)
        specs[table] = {
            'filename': filename,
            'url': DATA_BASE.format(year=year) + filename,
            'kind': 'table',
        }
    geo_name = 'Geos%s1YR.txt' % year
    specs['geography'] = {
        'filename': geo_name,
        'url': DOC_BASE.format(year=year) + geo_name,
        'kind': 'geography',
    }
    cd_gaz = '%s_Gaz_%sCDs_national.zip' % (year, congress)
    specs['district_gazetteer'] = {
        'filename': cd_gaz,
        'url': GAZ_BASE.format(year=year) + cd_gaz,
        'kind': 'gazetteer',
    }
    state_gaz = '%s_Gaz_state_national.zip' % year
    specs['state_gazetteer'] = {
        'filename': state_gaz,
        'url': GAZ_BASE.format(year=year) + state_gaz,
        'kind': 'gazetteer',
    }
    return specs


def fetch_sources(cache_root, year, congress, force=False):
    specs = _source_specs(year, congress)
    statuses = {}
    for key, spec in specs.items():
        path = os.path.join(cache_root, spec['filename'])
        statuses[key] = _download(spec['url'], path, force)
    return statuses


def _load_geographies(path, congress):
    districts = {}
    states = {}
    by_geo_id = {}
    with open(path, encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle, delimiter='|')
        required = frozenset((
            'SUMLEVEL', 'COMPONENT', 'STATE', 'CDCURR',
            'GEO_ID', 'NAME', 'STUSAB',
        ))
        if not required.issubset(reader.fieldnames or ()):
            raise SystemExit(
                'ACS geography file lacks required fields: %s' % path
            )
        for row in reader:
            level = str(row.get('SUMLEVEL') or '').strip()
            component = str(row.get('COMPONENT') or '').strip()
            if component != '00':
                continue
            state_fips = str(row.get('STATE') or '').zfill(2)
            usps = (
                str(row.get('STUSAB') or '').strip().upper() or
                FIPS_TO_USPS.get(state_fips)
            )
            geo_id = str(row.get('GEO_ID') or '').strip()
            if not geo_id or not usps:
                continue
            if level == '040' and re.fullmatch(r'[0-9]{2}', state_fips):
                key = usps
                record = {
                    'geo': key,
                    'kind': 'state',
                    'state': usps,
                    'state_fips': state_fips,
                    'district': None,
                    'geo_id': geo_id,
                    'name': str(row.get('NAME') or key).strip(),
                    'congress': None,
                    'region': _region(usps),
                }
                states[key] = record
                by_geo_id[geo_id] = record
            elif level == '500':
                district = str(row.get('CDCURR') or '').strip().zfill(2)
                if re.fullmatch(r'[0-9]{2}', district) is None:
                    continue
                key = '%s-%s' % (usps, district)
                record = {
                    'geo': key,
                    'kind': 'district',
                    'state': usps,
                    'state_fips': state_fips,
                    'district': district,
                    'geo_id': geo_id,
                    'name': str(row.get('NAME') or key).strip(),
                    'congress': int(congress),
                    'region': _region(usps),
                }
                districts[key] = record
                by_geo_id[geo_id] = record
    if not districts or not states:
        raise SystemExit(
            'ACS geography file yielded %d districts and %d states' %
            (len(districts), len(states))
        )
    return districts, states, by_geo_id


def _load_gazetteer(path, kind):
    rows = {}
    with zipfile.ZipFile(path) as archive:
        members = [
            name for name in archive.namelist()
            if name.lower().endswith('.txt')
        ]
        if len(members) != 1:
            raise SystemExit(
                'expected one text member in Gazetteer %s; found %r' %
                (path, members)
            )
        with archive.open(members[0]) as raw:
            lines = (
                line.decode('utf-8-sig', errors='replace')
                for line in raw
            )
            reader = csv.DictReader(lines, delimiter='\t')
            reader.fieldnames = [
                str(name or '').strip() for name in (reader.fieldnames or [])
            ]
            for row in reader:
                usps = str(row.get('USPS') or '').strip().upper()
                geoid = str(row.get('GEOID') or '').strip()
                if kind == 'district':
                    if len(geoid) < 4:
                        continue
                    key = '%s-%s' % (usps, geoid[-2:])
                else:
                    key = usps
                aland = _number(row.get('ALAND'))
                land_sq_miles = _number(row.get('ALAND_SQMI'))
                if land_sq_miles is None and aland is not None:
                    land_sq_miles = aland / SQ_METERS_PER_SQ_MILE
                rows[key] = {
                    'aland_sqm': aland,
                    'land_sq_miles': _round(land_sq_miles, 3),
                    'internal_latitude': _number(row.get('INTPTLAT')),
                    'internal_longitude': _number(
                        row.get('INTPTLONG')
                    ),
                }
    return rows


def _load_table(path, detail, target_geo_ids):
    values = {}
    required_columns = {'GEO_ID'}
    for estimate_col, moe_col, _api_variable in detail[
            'variables'].values():
        required_columns.update((estimate_col, moe_col))
    with open(path, encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle, delimiter='|')
        if not required_columns.issubset(reader.fieldnames or ()):
            missing = sorted(required_columns - set(reader.fieldnames or ()))
            raise SystemExit(
                '%s lacks required columns %s' %
                (path, ', '.join(missing))
            )
        for row in reader:
            geo_id = str(row.get('GEO_ID') or '').strip()
            if geo_id not in target_geo_ids:
                continue
            if geo_id in values:
                raise SystemExit(
                    '%s contains duplicate GEO_ID %s' % (path, geo_id)
                )
            record = {}
            for metric, (
                    estimate_col, moe_col, _api_variable
                    ) in detail['variables'].items():
                record[metric] = {
                    'estimate': _number(row.get(estimate_col)),
                    'moe_90': _number(row.get(moe_col)),
                }
            values[geo_id] = record
    return values


def _direct_values(record):
    estimates = {}
    margins = {}
    for table_values in record.pop('_table_values', {}).values():
        for metric, pair in table_values.items():
            estimates[metric] = pair.get('estimate')
            margins[metric] = pair.get('moe_90')
    record['estimates'] = estimates
    record['moe_90'] = margins


def _derived_values(record, area):
    values = record.get('estimates') or {}
    land_sq_miles = _number((area or {}).get('land_sq_miles'))
    population = _number(values.get('population'))
    income = _number(values.get('median_household_income'))
    rent = _number(values.get('median_gross_rent'))
    home_value = _number(values.get('median_home_value'))
    derived = {
        'population_density_sq_mile': _round(
            _ratio(population, land_sq_miles), 3
        ),
        'unemployment_rate': _round(
            _ratio(
                values.get('unemployed'),
                values.get('civilian_labor_force'),
                100.0,
            ),
            3,
        ),
        'employment_population_ratio': _round(
            _ratio(values.get('employed'), values.get('population_16plus'),
                   100.0),
            3,
        ),
        'poverty_rate': _round(
            _ratio(
                values.get('below_poverty'),
                values.get('poverty_universe'),
                100.0,
            ),
            3,
        ),
        'owner_occupied_share': _round(
            _ratio(
                values.get('owner_occupied_units'),
                values.get('occupied_housing_units'),
                100.0,
            ),
            3,
        ),
        'renter_occupied_share': _round(
            _ratio(
                values.get('renter_occupied_units'),
                values.get('occupied_housing_units'),
                100.0,
            ),
            3,
        ),
        # Ratio of published medians: useful structural proxy, not the median
        # household's observed housing-cost burden.
        'annual_rent_income_ratio': _round(
            _ratio(12.0 * rent if rent is not None else None, income), 6
        ),
        'home_value_income_ratio': _round(
            _ratio(home_value, income), 6
        ),
    }
    record['area'] = dict(area or {})
    record['derived'] = derived
    required = (
        'population', 'median_age', 'median_household_income',
        'civilian_labor_force', 'unemployed', 'poverty_universe',
        'below_poverty', 'occupied_housing_units',
        'owner_occupied_units', 'renter_occupied_units',
        'median_home_value', 'median_gross_rent',
    )
    record['complete'] = bool(
        land_sq_miles is not None and
        all(values.get(key) is not None for key in required)
    )


def build_artifact(cache_root, year='2024', congress='119'):
    specs = _source_specs(year, congress)
    paths = {
        key: os.path.join(cache_root, spec['filename'])
        for key, spec in specs.items()
    }
    missing = [path for path in paths.values() if not os.path.exists(path)]
    if missing:
        raise SystemExit(
            'ACS build sources missing; run ACS_MODE=fetch first: %s' %
            ', '.join(missing)
        )
    districts, states, by_geo_id = _load_geographies(
        paths['geography'], congress
    )
    district_area = _load_gazetteer(
        paths['district_gazetteer'], 'district'
    )
    state_area = _load_gazetteer(paths['state_gazetteer'], 'state')

    for record in by_geo_id.values():
        record['_table_values'] = {}
    for table, detail in TABLES.items():
        rows = _load_table(paths[table], detail, frozenset(by_geo_id))
        for geo_id, values in rows.items():
            by_geo_id[geo_id]['_table_values'][table] = values

    for key, record in districts.items():
        _direct_values(record)
        _derived_values(record, district_area.get(key))
    for key, record in states.items():
        _direct_values(record)
        _derived_values(record, state_area.get(key))

    source_files = {}
    for key, spec in specs.items():
        path = paths[key]
        source_files[key] = {
            'url': spec['url'],
            'filename': spec['filename'],
            'bytes': os.path.getsize(path),
            'sha256': _sha256(path),
            'retrieved': _utc_iso(os.path.getmtime(path)),
        }
    variable_catalog = {}
    for table, detail in TABLES.items():
        for metric, (
                _estimate_col, _moe_col, api_variable
                ) in detail['variables'].items():
            variable_catalog[metric] = {
                'table': table,
                'variable': api_variable,
                'profile_family': detail['profile_family'],
            }
    complete_districts = sum(
        1 for row in districts.values() if row.get('complete')
    )
    complete_states = sum(
        1 for row in states.values() if row.get('complete')
    )
    return {
        'schema': SCHEMA,
        'generated_at': _utc_iso(),
        'vintage': int(year),
        'survey': 'ACS 1-year',
        'congress': int(congress),
        'geography_summary_level': {
            'district': '500',
            'state': '040',
        },
        'profile_families': {
            'DP05': 'ACS Demographic and Housing Estimates',
            'DP03': 'Selected Economic Characteristics',
            'DP04': 'Selected Housing Characteristics',
        },
        'variable_catalog': variable_catalog,
        'sources': source_files,
        'method': (
            f'Official {year} ACS 1-year table-based summary files joined through '
            'the published geography index; density uses Census Gazetteer '
            'land area. Derived rates are ratios of ACS estimates. Rent/'
            'income and home-value/income are ratios of published medians, '
            'not household-level burden measures.'
        ),
        'coverage': {
            'districts': len(districts),
            'complete_districts': complete_districts,
            'states': len(states),
            'complete_states': complete_states,
        },
        'districts': dict(sorted(districts.items())),
        'states': dict(sorted(states.items())),
    }


def _parse_mode(value):
    mode = str(value or '').strip().lower()
    if mode not in ('fetch', 'build', 'both'):
        raise SystemExit(
            'invalid ACS_MODE %r; expected fetch, build, or both' % value
        )
    return mode


def main():
    year = str(os.environ.get('ACS_YEAR', '2024')).strip()
    congress = str(os.environ.get('ACS_CONGRESS', '119')).strip()
    if re.fullmatch(r'[0-9]{4}', year) is None:
        raise SystemExit('invalid ACS_YEAR %r' % year)
    if re.fullmatch(r'[0-9]{3}', congress) is None:
        raise SystemExit('invalid ACS_CONGRESS %r' % congress)
    mode = _parse_mode(os.environ.get('ACS_MODE', 'both'))
    force = os.environ.get('ACS_FORCE', '0').strip() == '1'
    data_root = os.environ.get('DEMO_DATA_ROOT')
    if not data_root:
        data_root = os.path.join(
            os.environ.get('LOCALAPPDATA') or
            os.path.expanduser(r'~\AppData\Local'),
            'Melange', 'demographics',
        )
    cache_root = os.path.join(data_root, 'acs', year, '1yr')
    output_path = os.path.abspath(
        os.environ.get(
            'ACS_OUT', os.path.join(MB_ROOT, 'data', 'district', 'district_community.json')
        )
    )

    statuses = {}
    if mode in ('fetch', 'both'):
        statuses = fetch_sources(
            cache_root, year, congress, force=force
        )
    if mode == 'fetch':
        print(
            'ACS fetch complete: %d source files in %s' %
            (len(statuses), cache_root)
        )
        return 0

    artifact = build_artifact(cache_root, year, congress)
    dump_atomic(artifact, output_path, ensure_ascii=False, separators=(',', ':'))
    coverage = artifact['coverage']
    print(
        'ACS community: %d/%d districts + %d/%d states complete -> %s' %
        (
            coverage['complete_districts'],
            coverage['districts'],
            coverage['complete_states'],
            coverage['states'],
            output_path,
        )
    )
    return 0


if __name__ == '__main__':
    sys.exit(main())

# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT  # centralized root (arch review 2026-07-02)
"""DISTRICT INDUSTRY BUILDER — LQ, growth quotient, and shift-share metrics.

Phase C of the Melange demographic expansion.  This is a chain step in run_pipeline.py's
CHAIN list (build_district_industry.py, no CLI args).  It reads Phase B's district/state/
national LODES panel, its vintage manifest, and Phase A's district-geography artifact, then
writes the compact district-industry-v1 data-plane artifact.

Inputs:
  DEMO_DATA_ROOT/lodes/lodes_cd_panel.csv.gz
  DEMO_DATA_ROOT/lodes/lodes_panel_manifest.json
  MB_ROOT/data/district/district_geography.json

Output:
  MB_ROOT/data/lobbying/district_industry.json, unless INDUSTRY_OUT overrides the path.

Dials:
  DEMO_DATA_ROOT  — out-of-OneDrive raw-data root; defaults to
                    %LOCALAPPDATA%/Melange/demographics
  GROWTH_WINDOW   — target lookback in years; defaults to "10"
  SMALL_SECTOR_MIN — display/reliability threshold; defaults to "500"
  INDUSTRY_OUT    — optional output-path override

National methodology:
  Single-year location quotients use the published US row for that year.  Growth and
  shift-share terms use a balanced aggregate of the 50 state rows plus DC that are present
  at both endpoints of each geography's selected window.

Display groups are session-defined rollups, not Census or NAICS supersectors:
  office         = CNS09..CNS13
  retail         = CNS07
  industrial     = CNS01..CNS05
  services_other = CNS06, CNS08, CNS14..CNS20

Output schema:
  district-industry-v1, containing full LODES/xwalk vintage provenance, sector metadata,
  per-geography employment and metrics, flags, state display equivalents, and coverage notes.
"""
import csv,datetime,gzip,io,json,os,re,sys
from moneyball_core import dump_atomic
sys.stdout.reconfigure(encoding='utf-8',errors='replace')

COUNT_COLUMNS = (
    'C000',
    'CNS01', 'CNS02', 'CNS03', 'CNS04', 'CNS05',
    'CNS06', 'CNS07', 'CNS08', 'CNS09', 'CNS10',
    'CNS11', 'CNS12', 'CNS13', 'CNS14', 'CNS15',
    'CNS16', 'CNS17', 'CNS18', 'CNS19', 'CNS20',
)
SECTOR_COLUMNS = COUNT_COLUMNS[1:]
PANEL_COLUMNS = ('geo', 'year') + COUNT_COLUMNS
VOTING_STATES = (
    'AK', 'AL', 'AR', 'AZ', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA',
    'HI', 'IA', 'ID', 'IL', 'IN', 'KS', 'KY', 'LA', 'MA', 'MD',
    'ME', 'MI', 'MN', 'MO', 'MS', 'MT', 'NC', 'ND', 'NE', 'NH',
    'NJ', 'NM', 'NV', 'NY', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC',
    'SD', 'TN', 'TX', 'UT', 'VA', 'VT', 'WA', 'WI', 'WV', 'WY',
)
STATE_GEOS = VOTING_STATES + ('DC',)

GROUPS = {
    'office': ['CNS09', 'CNS10', 'CNS11', 'CNS12', 'CNS13'],
    'retail': ['CNS07'],
    'industrial': ['CNS01', 'CNS02', 'CNS03', 'CNS04', 'CNS05'],
    'services_other': [
        'CNS06', 'CNS08', 'CNS14', 'CNS15', 'CNS16',
        'CNS17', 'CNS18', 'CNS19', 'CNS20',
    ],
}

SECTOR_META = {
    'CNS01': {
        'naics': '11',
        'name': 'Agriculture, Forestry, Fishing and Hunting',
        'group': 'industrial',
    },
    'CNS02': {
        'naics': '21',
        'name': 'Mining, Quarrying, and Oil and Gas Extraction',
        'group': 'industrial',
    },
    'CNS03': {
        'naics': '22',
        'name': 'Utilities',
        'group': 'industrial',
    },
    'CNS04': {
        'naics': '23',
        'name': 'Construction',
        'group': 'industrial',
    },
    'CNS05': {
        'naics': '31-33',
        'name': 'Manufacturing',
        'group': 'industrial',
    },
    'CNS06': {
        'naics': '42',
        'name': 'Wholesale Trade',
        'group': 'services_other',
    },
    'CNS07': {
        'naics': '44-45',
        'name': 'Retail Trade',
        'group': 'retail',
    },
    'CNS08': {
        'naics': '48-49',
        'name': 'Transportation and Warehousing',
        'group': 'services_other',
    },
    'CNS09': {
        'naics': '51',
        'name': 'Information',
        'group': 'office',
    },
    'CNS10': {
        'naics': '52',
        'name': 'Finance and Insurance',
        'group': 'office',
    },
    'CNS11': {
        'naics': '53',
        'name': 'Real Estate and Rental and Leasing',
        'group': 'office',
    },
    'CNS12': {
        'naics': '54',
        'name': 'Professional, Scientific, and Technical Services',
        'group': 'office',
    },
    'CNS13': {
        'naics': '55',
        'name': 'Management of Companies and Enterprises',
        'group': 'office',
    },
    'CNS14': {
        'naics': '56',
        'name': (
            'Administrative and Support and Waste Management and '
            'Remediation Services'
        ),
        'group': 'services_other',
    },
    'CNS15': {
        'naics': '61',
        'name': 'Educational Services',
        'group': 'services_other',
    },
    'CNS16': {
        'naics': '62',
        'name': 'Health Care and Social Assistance',
        'group': 'services_other',
    },
    'CNS17': {
        'naics': '71',
        'name': 'Arts, Entertainment, and Recreation',
        'group': 'services_other',
    },
    'CNS18': {
        'naics': '72',
        'name': 'Accommodation and Food Services',
        'group': 'services_other',
    },
    'CNS19': {
        'naics': '81',
        'name': 'Other Services (except Public Administration)',
        'group': 'services_other',
    },
    'CNS20': {
        'naics': '92',
        'name': 'Public Administration',
        'group': 'services_other',
    },
}


def _utc_iso():
    value = datetime.datetime.now(datetime.timezone.utc)
    return value.replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def _parse_int_dial(name, default):
    text = os.environ.get(name, str(default))
    try:
        return int(text)
    except (TypeError, ValueError) as exc:
        raise SystemExit(
            'invalid %s %r; expected an integer' % (name, text)
        ) from exc


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


def _load_panel(path):
    if not os.path.exists(path):
        raise SystemExit('LODES panel does not exist: %s' % path)

    panel = {}
    with gzip.open(path, 'rb') as compressed:
        with io.TextIOWrapper(
            compressed,
            encoding='utf-8-sig',
            errors='strict',
            newline=''
        ) as source:
            reader = csv.DictReader(source)
            expected = list(PANEL_COLUMNS)
            if reader.fieldnames != expected:
                raise SystemExit(
                    'invalid panel header in %s: expected %s; found %r' %
                    (path, ','.join(expected), reader.fieldnames)
                )

            for row in reader:
                if None in row:
                    raise SystemExit(
                        '%s row %d has surplus field(s): %r' %
                        (path, reader.line_num, row[None])
                    )
                missing_fields = [
                    field for field in PANEL_COLUMNS
                    if row.get(field) is None
                ]
                if missing_fields:
                    raise SystemExit(
                        '%s row %d has missing expected field(s): %s' %
                        (path, reader.line_num, ', '.join(missing_fields))
                    )

                geo = (row.get('geo') or '').strip()
                if not geo:
                    raise SystemExit(
                        '%s row %d has a blank geo' %
                        (path, reader.line_num)
                    )
                year_text = (row.get('year') or '').strip()
                try:
                    year = int(year_text)
                except ValueError as exc:
                    raise SystemExit(
                        '%s row %d has invalid year %r' %
                        (path, reader.line_num, year_text)
                    ) from exc

                counts = []
                for column in COUNT_COLUMNS:
                    text = (row.get(column) or '').strip()
                    try:
                        value = int(text)
                    except ValueError as exc:
                        raise SystemExit(
                            '%s row %d has invalid %s count %r' %
                            (path, reader.line_num, column, text)
                        ) from exc
                    if value < 0:
                        raise SystemExit(
                            '%s row %d has negative %s count %d' %
                            (path, reader.line_num, column, value)
                        )
                    counts.append(value)

                sector_sum = sum(counts[1:])
                if counts[0] != sector_sum:
                    raise SystemExit(
                        '%s row %d geo %s year %d has C000 %d '
                        'but sum(CNS01..CNS20) %d' %
                        (
                            path,
                            reader.line_num,
                            geo,
                            year,
                            counts[0],
                            sector_sum,
                        )
                    )

                key = (geo, year)
                if key in panel:
                    raise SystemExit(
                        '%s row %d duplicates geo/year %s/%d' %
                        (path, reader.line_num, geo, year)
                    )
                panel[key] = counts

    if not panel:
        raise SystemExit('LODES panel has no data rows: %s' % path)
    print('loaded LODES panel: %d rows' % len(panel))
    return panel


def _validate_manifest(manifest, path):
    if manifest.get('schema') != 'lodes-panel-v1':
        raise SystemExit(
            'invalid LODES manifest schema in %s: %r' %
            (path, manifest.get('schema'))
        )
    required = (
        'generated_at', 'lodes_version', 'job_type', 'segment',
        'years', 'states', 'xwalk', 'coverage',
    )
    missing = [field for field in required if field not in manifest]
    if missing:
        raise SystemExit(
            'LODES manifest %s is missing field(s): %s' %
            (path, ', '.join(missing))
        )
    for field in (
        'lodes_version', 'job_type', 'segment', 'generated_at'
    ):
        value = manifest.get(field)
        if not isinstance(value, str) or not value.strip():
            raise SystemExit(
                'LODES manifest %s has invalid %s %r' %
                (path, field, value)
            )
    years = manifest.get('years')
    if (not isinstance(years, list) or
            any(type(year) is not int for year in years)):
        raise SystemExit(
            'LODES manifest %s has invalid years list' % path
        )
    states = manifest.get('states')
    if (not isinstance(states, list) or
            any(
                not isinstance(state, str) or not state.strip()
                for state in states
            )):
        raise SystemExit(
            'LODES manifest %s has invalid states list' % path
        )
    xwalk = manifest.get('xwalk')
    sha256 = xwalk.get('sha256') if isinstance(xwalk, dict) else None
    if (not isinstance(sha256, str) or
            re.fullmatch(r'[0-9a-f]{64}', sha256) is None):
        raise SystemExit(
            'LODES manifest %s has invalid xwalk.sha256 %r' %
            (path, sha256)
        )
    coverage = manifest.get('coverage')
    missing_state_years = (
        coverage.get('missing_state_years')
        if isinstance(coverage, dict) else None
    )
    if (not isinstance(missing_state_years, list) or
            any(
                not isinstance(value, str)
                for value in missing_state_years
            )):
        raise SystemExit(
            'LODES manifest %s has invalid '
            'coverage.missing_state_years' % path
        )


def _validate_coverage(panel, manifest, path):
    declared_years = manifest['years']
    declared_states = [
        state.strip().lower() for state in manifest['states']
    ]
    present_state_years = {
        (geo.lower(), year)
        for geo, year in panel
        if geo in STATE_GEOS
    }
    derived_missing = {
        '%s_%d' % (state, year)
        for state in declared_states
        for year in declared_years
        if (state, year) not in present_state_years
    }
    manifest_missing = set(
        manifest['coverage']['missing_state_years']
    )
    if derived_missing != manifest_missing:
        raise SystemExit(
            'LODES coverage mismatch for %s: '
            'derived missing state-years=%r; '
            'manifest missing state-years=%r' %
            (
                path,
                sorted(derived_missing),
                sorted(manifest_missing),
            )
        )


def _validate_geography(geography, path):
    if geography.get('schema') != 'district-geography-v1':
        raise SystemExit(
            'invalid district geography schema in %s: %r' %
            (path, geography.get('schema'))
        )
    states = geography.get('states')
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
    total_districts = 0
    for state in VOTING_STATES:
        detail = states[state]
        if not isinstance(detail, dict):
            raise SystemExit(
                'district geography %s has invalid %s detail' %
                (path, state)
            )
        n_districts = detail.get('n_voting_districts')
        if type(n_districts) is not int or n_districts <= 0:
            raise SystemExit(
                'district geography %s has invalid %s '
                'n_voting_districts %r' %
                (path, state, n_districts)
            )
        districts = detail.get('districts')
        if not isinstance(districts, list):
            raise SystemExit(
                'district geography %s has invalid %s districts' %
                (path, state)
            )
        invalid_districts = [
            district for district in districts
            if (
                not isinstance(district, str) or
                len(district) != 2 or
                not district.isdigit()
            )
        ]
        if invalid_districts:
            raise SystemExit(
                'district geography %s has invalid %s district '
                'code(s): %r' %
                (path, state, invalid_districts)
            )
        if len(set(districts)) != len(districts):
            raise SystemExit(
                'district geography %s has duplicate %s '
                'district entries: %r' %
                (path, state, districts)
            )
        if n_districts != len(districts):
            raise SystemExit(
                'district geography %s state %s has '
                'n_voting_districts %d but %d district entries' %
                (path, state, n_districts, len(districts))
            )
        total_districts += n_districts
        if not isinstance(detail.get('redrawn_since_118'), list):
            raise SystemExit(
                'district geography %s has invalid %s '
                'redrawn_since_118' % (path, state)
            )
    if total_districts != 435:
        raise SystemExit(
            'district geography %s has %d voting districts; '
            'expected 435' % (path, total_districts)
        )


def _classify_geo(geo, geography):
    states = geography['states']
    if '-' in geo:
        pieces = geo.split('-')
        if (len(pieces) != 2 or len(pieces[0]) != 2 or
                len(pieces[1]) != 2):
            raise SystemExit('invalid district geo in panel: %r' % geo)
        state, cd = pieces
        if state not in states:
            raise SystemExit(
                'district geo %s has no geography state %s' %
                (geo, state)
            )
        detail = states[state]
        if cd not in detail['districts']:
            raise SystemExit(
                'district geo %s is absent from district geography' % geo
            )
        return {
            'kind': 'district',
            'state': state,
            'cd': cd,
            'n_voting_districts': detail['n_voting_districts'],
            'redrawn_since_118': cd in detail['redrawn_since_118'],
        }

    if len(geo) != 2:
        raise SystemExit('invalid state geo in panel: %r' % geo)
    if geo == 'DC':
        n_districts = None
    elif geo in states:
        n_districts = states[geo]['n_voting_districts']
    else:
        raise SystemExit('unknown state geo in panel: %r' % geo)
    return {
        'kind': 'state',
        'state': geo,
        'cd': None,
        'n_voting_districts': n_districts,
        'redrawn_since_118': None,
    }


def _select_window(years, growth_window):
    y1 = max(years)
    eligible = [
        year for year in years if year <= y1 - growth_window
    ]
    if eligible:
        y0 = max(eligible)
    else:
        y0 = min(years)
    return y0, y1


def _balanced_pair(panel, y0, y1, cache):
    key = (y0, y1)
    cached = cache.get(key)
    if cached is not None:
        return cached

    balanced0 = [0] * len(COUNT_COLUMNS)
    balanced1 = [0] * len(COUNT_COLUMNS)
    included = []
    for state in STATE_GEOS:
        state0 = panel.get((state, y0))
        state1 = panel.get((state, y1))
        if state0 is None or state1 is None:
            continue
        included.append(state)
        for index in range(len(COUNT_COLUMNS)):
            balanced0[index] += state0[index]
            balanced1[index] += state1[index]

    if not included:
        raise SystemExit(
            'balanced national pair %d/%d has no state rows present '
            'at both endpoints' % (y0, y1)
        )
    if balanced0[0] == 0:
        raise SystemExit(
            'balanced national pair %d/%d has zero base C000' %
            (y0, y1)
        )

    result = (balanced0, balanced1)
    cache[key] = result
    print(
        'balanced national %d/%d: %d state rows' %
        (y0, y1, len(included))
    )
    return result


def _print_reconciliation_failure(
    geo, y0, y1, decomposition, totals, delta, error
):
    print(
        '!! shift-share reconciliation failed for %s: '
        'window %d/%d, error %r' % (geo, y0, y1, error)
    )
    print(
        'sector  e0  e1  national_growth  industry_mix  '
        'competitive_shift'
    )
    for detail in decomposition:
        print(
            '%s  %d  %d  %r  %r  %r' %
            (
                detail['sector'],
                detail['e0'],
                detail['e1'],
                detail['national_growth'],
                detail['industry_mix'],
                detail['competitive_shift'],
            )
        )
    print(
        'totals  delta=%d  national_growth=%r  industry_mix=%r  '
        'competitive_shift=%r  component_sum=%r  '
        'reconciliation_abs_err=%r' %
        (
            delta,
            totals['national_growth'],
            totals['industry_mix'],
            totals['competitive_shift'],
            (
                totals['national_growth'] +
                totals['industry_mix'] +
                totals['competitive_shift']
            ),
            error,
        )
    )


def _build_geo_entry(
    geo, y0, y1, panel, geography, balanced_cache,
    small_sector_min
):
    metadata = _classify_geo(geo, geography)
    current = panel[(geo, y1)]
    base = panel[(geo, y0)]
    us_current = panel.get(('US', y1))
    if us_current is None:
        raise SystemExit(
            'panel has no US row for %s latest year %d' % (geo, y1)
        )

    current_total = current[0]
    us_total = us_current[0]
    makeup_pct = {}
    lq_national = {}
    for index, sector in enumerate(SECTOR_COLUMNS, 1):
        current_value = current[index]
        us_value = us_current[index]
        if current_total == 0:
            makeup_pct[sector] = None
        else:
            makeup_pct[sector] = round(
                current_value / current_total, 4
            )
        if current_total == 0 or us_total == 0 or us_value == 0:
            lq_national[sector] = None
        else:
            lq_national[sector] = round(
                (current_value / current_total) /
                (us_value / us_total),
                3
            )

    if metadata['kind'] == 'district':
        state_current = panel.get((metadata['state'], y1))
        if state_current is None:
            raise SystemExit(
                'panel has no %s state row for district %s year %d' %
                (metadata['state'], geo, y1)
            )
        state_total = state_current[0]
        lq_state = {}
        for index, sector in enumerate(SECTOR_COLUMNS, 1):
            state_value = state_current[index]
            if (current_total == 0 or state_total == 0 or
                    state_value == 0):
                lq_state[sector] = None
            else:
                lq_state[sector] = round(
                    (current[index] / current_total) /
                    (state_value / state_total),
                    3
                )
    else:
        lq_state = None

    small_sectors = [
        sector
        for index, sector in enumerate(SECTOR_COLUMNS, 1)
        if 0 < current[index] < small_sector_min
    ]
    small_base_sectors = [
        sector
        for index, sector in enumerate(SECTOR_COLUMNS, 1)
        if 0 < base[index] < small_sector_min
    ]
    zero_base_sectors = [
        sector
        for index, sector in enumerate(SECTOR_COLUMNS, 1)
        if base[index] == 0
    ]

    reconciliation_error = None
    if y0 == y1:
        window = None
        growth_quotient = None
        shift_share = None
        shift_share_total = None
    else:
        window = [y0, y1]
        balanced0, balanced1 = _balanced_pair(
            panel, y0, y1, balanced_cache
        )
        growth_quotient = {}
        shift_share = {}
        decomposition = []
        total_national_growth = 0.0
        total_industry_mix = 0.0
        total_competitive_shift = 0.0
        national_rate = balanced1[0] / balanced0[0] - 1

        for index, sector in enumerate(SECTOR_COLUMNS, 1):
            base_value = base[index]
            current_value = current[index]
            national_base = balanced0[index]
            national_current = balanced1[index]

            if (base_value == 0 or national_base == 0 or
                    national_current == 0):
                growth_quotient[sector] = None
            else:
                growth_quotient[sector] = round(
                    (current_value / base_value) /
                    (national_current / national_base),
                    3
                )

            if national_base == 0 and base_value > 0:
                raise SystemExit(
                    'inconsistent balanced national base for geo %s '
                    'sector %s window %d/%d: geo base %d but '
                    'balanced national base is zero' %
                    (geo, sector, y0, y1, base_value)
                )
            if national_base == 0:
                industry_national_rate = 0.0
            else:
                industry_national_rate = (
                    national_current / national_base - 1
                )

            if base_value > 0:
                district_industry_rate = (
                    current_value / base_value - 1
                )
                national_growth = base_value * national_rate
                industry_mix = base_value * (
                    industry_national_rate - national_rate
                )
                competitive_shift = base_value * (
                    district_industry_rate -
                    industry_national_rate
                )
            else:
                national_growth = 0.0
                industry_mix = 0.0
                competitive_shift = float(current_value)

            total_national_growth += national_growth
            total_industry_mix += industry_mix
            total_competitive_shift += competitive_shift
            shift_share[sector] = {
                'national_growth': round(national_growth, 1),
                'industry_mix': round(industry_mix, 1),
                'competitive_shift': round(competitive_shift, 1),
            }
            decomposition.append({
                'sector': sector,
                'e0': base_value,
                'e1': current_value,
                'national_growth': national_growth,
                'industry_mix': industry_mix,
                'competitive_shift': competitive_shift,
            })

        delta = current[0] - base[0]
        unrounded_sum = (
            total_national_growth +
            total_industry_mix +
            total_competitive_shift
        )
        reconciliation_error = abs(unrounded_sum - delta)
        unrounded_totals = {
            'national_growth': total_national_growth,
            'industry_mix': total_industry_mix,
            'competitive_shift': total_competitive_shift,
        }
        if reconciliation_error > 0.5:
            _print_reconciliation_failure(
                geo, y0, y1, decomposition, unrounded_totals,
                delta, reconciliation_error
            )
            raise SystemExit(1)

        shift_share_total = {
            'delta': delta,
            'national_growth': round(total_national_growth, 1),
            'industry_mix': round(total_industry_mix, 1),
            'competitive_shift': round(total_competitive_shift, 1),
            'reconciliation_abs_err': reconciliation_error,
        }

    if metadata['kind'] == 'state' and geo != 'DC':
        n_districts = metadata['n_voting_districts']
        per_district_equiv = {
            'C000': round(current[0] / n_districts, 1),
            'by_sector': {
                sector: round(current[index] / n_districts, 1)
                for index, sector in enumerate(SECTOR_COLUMNS, 1)
            },
        }
    else:
        per_district_equiv = None

    entry = {
        'kind': metadata['kind'],
        'state': metadata['state'],
        'cd': metadata['cd'],
        'n_voting_districts': metadata['n_voting_districts'],
        'redrawn_since_118': metadata['redrawn_since_118'],
        'latest_year': y1,
        'window': window,
        'employment': {
            'C000': current[0],
            'by_sector': {
                sector: current[index]
                for index, sector in enumerate(SECTOR_COLUMNS, 1)
            },
        },
        'makeup_pct': makeup_pct,
        'lq_national': lq_national,
        'lq_state': lq_state,
        'growth_quotient': growth_quotient,
        'shift_share': shift_share,
        'shift_share_total': shift_share_total,
        'per_district_equiv': per_district_equiv,
        'flags': {
            'small_sectors': small_sectors,
            'small_base_sectors': small_base_sectors,
            'zero_base_sectors': zero_base_sectors,
        },
    }
    return entry, reconciliation_error


def _build_artifact(
    panel, manifest, geography, growth_window, small_sector_min
):
    years_by_geo = {}
    for geo, year in panel:
        if geo == 'US':
            continue
        years_by_geo.setdefault(geo, []).append(year)

    expected_geos = set(STATE_GEOS)
    for state in VOTING_STATES:
        for district in geography['states'][state]['districts']:
            expected_geos.add('%s-%s' % (state, district))
    actual_geos = set(years_by_geo)
    if actual_geos != expected_geos:
        raise SystemExit(
            'LODES panel geo universe mismatch: missing=%r; '
            'unexpected=%r' %
            (
                sorted(expected_geos - actual_geos),
                sorted(actual_geos - expected_geos),
            )
        )

    global_latest = max(max(years) for years in years_by_geo.values())
    geos = {}
    latest_year_exceptions = {}
    no_window_geos = []
    balanced_cache = {}
    max_reconciliation_error = 0.0

    for geo in sorted(years_by_geo):
        y0, y1 = _select_window(
            years_by_geo[geo], growth_window
        )
        if y1 != global_latest:
            latest_year_exceptions[geo] = y1
        if y0 == y1:
            no_window_geos.append(geo)
        entry, reconciliation_error = _build_geo_entry(
            geo,
            y0,
            y1,
            panel,
            geography,
            balanced_cache,
            small_sector_min,
        )
        geos[geo] = entry
        if (reconciliation_error is not None and
                reconciliation_error > max_reconciliation_error):
            max_reconciliation_error = reconciliation_error

    artifact = {
        'schema': 'district-industry-v1',
        'generated_at': _utc_iso(),
        'lodes': {
            'version': manifest['lodes_version'],
            'job_type': manifest['job_type'],
            'segment': manifest['segment'],
            'panel_generated_at': manifest['generated_at'],
            'xwalk_sha256': manifest['xwalk']['sha256'],
        },
        'growth_window_years': growth_window,
        'small_sector_min': small_sector_min,
        'sector_meta': SECTOR_META,
        'groups': GROUPS,
        'geos': geos,
        'coverage_notes': {
            'missing_state_years':
                manifest['coverage']['missing_state_years'],
            'latest_year_exceptions': latest_year_exceptions,
            'no_window_geos': no_window_geos,
            # Carried forward verbatim from the LODES panel manifest:
            # xwalk_miss_blocks counts blocks whose jobs reached the state and
            # national rows but NO district, and cns_mismatch_rows counts raw
            # rows where sum(CNS01..CNS20) != C000. Both are district-coverage
            # gaps; without them here no consumer of this artifact can see them.
            'lodes_row_checks': dict(manifest.get('row_checks') or {}),
        },
    }
    return {
        'artifact': artifact,
        'global_latest': global_latest,
        'latest_year_exceptions': latest_year_exceptions,
        'max_reconciliation_error': max_reconciliation_error,
    }


def _warn_latest_year_exceptions(exceptions, global_latest):
    by_state = {}
    for geo, latest_year in exceptions.items():
        state = geo.split('-', 1)[0] if '-' in geo else geo
        detail = by_state.setdefault(
            state, {'years': set(), 'n_geos': 0}
        )
        detail['years'].add(latest_year)
        detail['n_geos'] += 1

    for state in sorted(by_state):
        detail = by_state[state]
        years = ','.join(str(year) for year in sorted(detail['years']))
        print(
            '!! latest-year exception state %s: year(s) %s vs '
            'global %d (%d geos)' %
            (state, years, global_latest, detail['n_geos'])
        )


def main():
    data_root = os.environ.get('DEMO_DATA_ROOT')
    if not data_root:
        base = (
            os.environ.get('LOCALAPPDATA') or
            os.path.expanduser(r'~\AppData\Local')
        )
        data_root = os.path.join(base, 'Melange', 'demographics')

    growth_window = _parse_int_dial('GROWTH_WINDOW', 10)
    small_sector_min = _parse_int_dial('SMALL_SECTOR_MIN', 500)
    panel_path = os.path.join(
        data_root, 'lodes', 'lodes_cd_panel.csv.gz'
    )
    manifest_path = os.path.join(
        data_root, 'lodes', 'lodes_panel_manifest.json'
    )
    geography_path = os.path.join(
        MB_ROOT, 'data', 'district', 'district_geography.json'
    )
    output_path = os.environ.get('INDUSTRY_OUT')
    if not output_path:
        output_path = os.path.join(
            MB_ROOT, 'data', 'lobbying', 'district_industry.json'
        )

    print('loading LODES panel: %s' % panel_path)
    panel = _load_panel(panel_path)
    print('loading LODES manifest: %s' % manifest_path)
    manifest = _load_json(manifest_path, 'LODES manifest')
    _validate_manifest(manifest, manifest_path)
    _validate_coverage(panel, manifest, manifest_path)
    print('loading district geography: %s' % geography_path)
    geography = _load_json(
        geography_path, 'district geography'
    )
    _validate_geography(geography, geography_path)

    print(
        'building district industry metrics: growth window %d, '
        'small-sector minimum %d' %
        (growth_window, small_sector_min)
    )
    result = _build_artifact(
        panel,
        manifest,
        geography,
        growth_window,
        small_sector_min,
    )
    _warn_latest_year_exceptions(
        result['latest_year_exceptions'],
        result['global_latest'],
    )

    print('writing district industry artifact: %s' % output_path)
    dump_atomic(
        result['artifact'],
        output_path,
        separators=(',', ':')
    )
    print(
        'wrote district_industry.json: %d geos, global latest %d '
        '(%d latest-year exceptions), growth window %d, '
        'max reconciliation err %.6f' %
        (
            len(result['artifact']['geos']),
            result['global_latest'],
            len(result['latest_year_exceptions']),
            growth_window,
            result['max_reconciliation_error'],
        )
    )


if __name__ == '__main__':
    main()

# -*- coding: utf-8 -*-
"""Deterministic Demographic Potential qualification for the member desk.

The signal describes a district's constituency-side economic opportunity.  It
does not use credit-union employment as an advocacy proxy.  A sector qualifies
when its current concentration and competitive shift are both favorable, the
LODES sector is not small, and the CBP four-digit layer does not contradict the
high LQ.  NAICS 92 is outside the CBP universe and is therefore reported as
uncovered rather than silently treated as corroborated.
"""
import math
import os
import re

import numpy as np

import district.district_cu_metrics as DCM


LABEL = 'Demographic Potential'
LQ_MIN = 1.25
CBP_CONTRADICTION_FLOOR = 0.90

# Dial convention matches tier_util.py PRIMACY_BOARD / build_rank_honors.py /
# make_member_desk.py DESK_SPLIT (design-d2-d5.md D3): default = new
# behaviour ("footprint", ruling R7 -- "The footprint should be the
# potential"); non-default ("growth_room") restores today's whitespace
# scoring byte-for-byte. Read once at import, like the cited dials.
DEMO_POTENTIAL_MODES = ('footprint', 'growth_room')
DEMO_POTENTIAL = (
    os.environ.get('DEMO_POTENTIAL') or 'footprint'
).strip().lower()
# R7: the footprint qualification gate. Unrounded employment_lq, not the
# vacuous branches>0-or-lq>0 proposal footprint_v2.py measured.
FOOTPRINT_EMPLOYMENT_LQ_MIN = 1.0

SCORE_VERSION_GROWTH_ROOM = '2.1'
SCORE_VERSION_FOOTPRINT = '3.0'
SCORE_VERSION = (
    SCORE_VERSION_FOOTPRINT if DEMO_POTENTIAL == 'footprint'
    else SCORE_VERSION_GROWTH_ROOM
)
WEIGHT_BASIS = (
    'Judgmental policy weights selected for this dashboard; not copied from '
    'a published study, statistically estimated, or fitted to credit-union '
    'growth outcomes.'
)
WEIGHT_RATIONALE_GROWTH_ROOM = (
    'The allocation reserves 55% for direct economic alignment (LQ, '
    'competitive shift, and sector breadth), 20% for current credit-union '
    'whitespace, and 25% for lending/employer/small-business reach. The '
    'deposit proxy receives only 5% because NCUA shares and FDIC deposits '
    'come from different systems and district CU shares must be estimated.'
)
WEIGHT_RATIONALE_FOOTPRINT = (
    'Ruling R7 ("the footprint should be the potential") reserves 50% for '
    'credit-union presence intensity relative to the national CU workforce '
    '(employment and establishment location quotients) and 50% for '
    'observed CU market share (branch and deposit share). Higher current '
    'credit-union footprint scores higher here -- the opposite direction '
    'from the retired expansion-gap measure.'
)
WEIGHT_RATIONALE = (
    WEIGHT_RATIONALE_FOOTPRINT if DEMO_POTENTIAL == 'footprint'
    else WEIGHT_RATIONALE_GROWTH_ROOM
)
OPTIMIZATION_REQUIREMENT = (
    'Empirical optimization requires a preregistered target such as later '
    'district-level credit-union membership, share, loan, or branch growth, '
    'plus an out-of-sample validation design. Those outcomes are not present '
    'in the current evidence bundle.'
)
SENSITIVITY_VERSION = 'equal-plus-leave-one-out-v1'
STRUCTURAL_ADJUSTMENT_VERSION = 'reach-residual-v1'
STRUCTURAL_TARGETS = (
    'large_employer_reach',
    'small_business_reach',
)
STRUCTURAL_NUMERIC_CONTROLS = (
    'log_total_employment',
    'log_population',
    'log_population_density',
    'log_median_household_income',
    'median_age',
    'annual_rent_income_ratio',
    'lodes_latest_year',
)
STRUCTURAL_CONTROL_DEFINITIONS = {
    'log_total_employment': {
        'label': 'Total employment',
        'formula': 'ln(1 + latest LODES workplace jobs)',
        'source': 'lodes',
    },
    'log_population': {
        'label': 'Population',
        'formula': 'ln(1 + ACS total population)',
        'source': 'acs',
        'variable': 'B01003_001E',
    },
    'log_population_density': {
        'label': 'Population density',
        'formula': (
            'ln(1 + ACS total population / Census Gazetteer land sq. mi.)'
        ),
        'source': 'acs',
        'variable': 'B01003_001E + Gazetteer ALAND',
    },
    'log_median_household_income': {
        'label': 'Median household income',
        'formula': 'ln(ACS median household income)',
        'source': 'acs',
        'variable': 'B19013_001E',
    },
    'median_age': {
        'label': 'Median age',
        'formula': 'ACS median age',
        'source': 'acs',
        'variable': 'B01002_001E',
    },
    'annual_rent_income_ratio': {
        'label': 'Rent-to-income structure',
        'formula': (
            '12 × ACS median gross rent / ACS median household income'
        ),
        'source': 'acs',
        'variable': 'B25064_001E / B19013_001E',
    },
    'region_fixed_effects': {
        'label': 'Census region',
        'formula': (
            'Northeast, Midwest, South, and West fixed effects '
            '(one omitted reference category)'
        ),
        'source': 'acs',
    },
    'lodes_latest_year': {
        'label': 'LODES vintage',
        'formula': 'latest LODES source year',
        'source': 'lodes',
    },
}

# The score ranks expansion opportunity only after the binary evidence gate
# below has been passed.  Under growth_room, economic alignment carries 55%
# of the score and the remaining 45% measures addressable credit-union and
# lending reach.  Under footprint (R7, default) the score instead measures
# existing credit-union presence directly -- see WEIGHT_RATIONALE_FOOTPRINT.
SCORE_WEIGHTS_GROWTH_ROOM = {
    'lq_strength': 0.20,
    'competitive_shift': 0.20,
    'sector_breadth': 0.15,
    'cu_branch_whitespace': 0.15,
    'cu_deposit_whitespace': 0.05,
    'lmi_lending_opportunity': 0.10,
    'large_employer_reach': 0.08,
    'small_business_reach': 0.07,
}
# footprint_v2.py:323-328 WEIGHTS_A. Six of eight growth_room components are
# dropped; the two share components are kept, un-inverted, and reweighted up.
SCORE_WEIGHTS_FOOTPRINT = {
    'employment_lq': 0.30,
    'establishment_lq': 0.20,
    'cu_branch_share': 0.25,
    'cu_deposit_share': 0.25,
}
SCORE_WEIGHTS = (
    SCORE_WEIGHTS_FOOTPRINT if DEMO_POTENTIAL == 'footprint'
    else SCORE_WEIGHTS_GROWTH_ROOM
)
COMPONENT_LABELS_GROWTH_ROOM = {
    'lq_strength': 'LQ strength',
    'competitive_shift': 'Competitive shift',
    'sector_breadth': 'Aligned sector breadth',
    'cu_branch_whitespace': 'CU branch whitespace',
    'cu_deposit_whitespace': 'CU deposit whitespace',
    'lmi_lending_opportunity': 'LMI lending opportunity',
    'large_employer_reach': 'Large-employer reach',
    'small_business_reach': 'Small-business reach',
}
COMPONENT_LABELS_FOOTPRINT = {
    'employment_lq': 'CU employment concentration (LQ)',
    'establishment_lq': 'CU establishment concentration (LQ)',
    'cu_branch_share': 'CU branch share',
    'cu_deposit_share': 'CU deposit share',
}
COMPONENT_LABELS = (
    COMPONENT_LABELS_FOOTPRINT if DEMO_POTENTIAL == 'footprint'
    else COMPONENT_LABELS_GROWTH_ROOM
)
SOURCE_CATALOG = {
    'lodes': {
        'agency': 'U.S. Census Bureau, LEHD/LODES',
        'artifact': 'data/lobbying/district_industry.json',
        'official_url': (
            'https://lehd.ces.census.gov/data/lodes/LODES8/'
            'LODESTechDoc8.3.pdf'
        ),
    },
    'cbp': {
        'agency': 'U.S. Census Bureau, County Business Patterns',
        'artifact': (
            'district_industry_4digit.json; district_employers.json'
        ),
        'official_url': (
            'https://www.census.gov/programs-surveys/cbp/'
            'technical-documentation/methodology.html'
        ),
    },
    'acs': {
        'agency': 'U.S. Census Bureau, 2024 ACS 1-Year',
        'artifact': 'data/district/district_community.json',
        'official_url': (
            'https://api.census.gov/data/2024/acs/acs1/profile.html'
        ),
    },
    'ncua': {
        'agency': 'National Credit Union Administration',
        'artifact': 'data/district/district_financial.json',
        'official_url': (
            'https://ncua.gov/analysis/'
            'credit-union-corporate-call-report-data/quarterly-data'
        ),
    },
    'fdic': {
        'agency': 'Federal Deposit Insurance Corporation',
        'artifact': 'data/district/district_financial.json',
        'official_url': (
            'https://banks.data.fdic.gov/bankfind-suite/SOD'
        ),
    },
    'hmda': {
        'agency': 'FFIEC/Consumer Financial Protection Bureau',
        'artifact': 'data/district/district_hmda.json',
        'official_url': (
            'https://ffiec.cfpb.gov/data-publication/2024'
        ),
    },
    'usaspending': {
        'agency': 'USAspending.gov',
        'artifact': 'data/district/district_employers.json',
        'official_url': (
            'https://api.usaspending.gov/docs/endpoints'
        ),
    },
}
COMPONENT_DEFINITIONS_GROWTH_ROOM = {
    'lq_strength': {
        'raw_formula': (
            'max_s ln(LQ_s / 1.25), where LQ_s = '
            '(local sector jobs / local all jobs) / '
            '(U.S. sector jobs / U.S. all jobs), across qualifying sectors'
        ),
        'normalization': (
            'Tie-aware percentile within qualifying markets of the same '
            'geography type; higher is favorable'
        ),
        'sources': ('lodes', 'cbp'),
        'caveat': (
            'The logarithm is monotonic, so it does not change percentile '
            'order versus max LQ. LQ is concentration, not absolute scale; '
            'the 500-job gate and CBP contradiction screen guard against '
            'small or conflicting sectors.'
        ),
    },
    'competitive_shift': {
        'raw_formula': (
            'max_s competitive shift_s / base jobs_s; competitive shift_s = '
            'base jobs_s * (local sector growth rate_s - '
            'U.S. sector growth rate_s)'
        ),
        'normalization': (
            'Tie-aware percentile within qualifying markets of the same '
            'geography type; higher is favorable'
        ),
        'sources': ('lodes',),
        'caveat': (
            'This is descriptive shift-share, not a causal effect. The max '
            'operator rewards the strongest qualifying sector.'
        ),
    },
    'sector_breadth': {
        'raw_formula': (
            'count of sectors passing LQ >= 1.25, competitive shift > 0, '
            'current and base jobs > 500, and the CBP contradiction screen'
        ),
        'normalization': (
            'Tie-aware percentile within qualifying markets of the same '
            'geography type; higher is favorable'
        ),
        'sources': ('lodes', 'cbp'),
        'caveat': (
            'A count treats every eligible sector equally and does not '
            'measure diversification inside a sector.'
        ),
    },
    'cu_branch_whitespace': {
        'raw_formula': (
            'CU branches / (CU branches + FDIC-insured bank branches)'
        ),
        'normalization': (
            '1 - tie-aware penetration percentile within qualifying markets '
            'of the same geography type; lower current CU share is favorable'
        ),
        'sources': ('ncua', 'fdic'),
        'caveat': (
            'Branch locations are geocoded/allocated to districts; unmatched '
            'and county-fallback records create estimation error.'
        ),
    },
    'cu_deposit_whitespace': {
        'raw_formula': (
            'estimated CU shares / '
            '(FDIC bank deposits + estimated CU shares)'
        ),
        'normalization': (
            '1 - tie-aware penetration percentile within qualifying markets '
            'of the same geography type; lower current CU share is favorable'
        ),
        'sources': ('ncua', 'fdic'),
        'caveat': (
            'NCUA institution shares are divided equally across allocation-'
            'eligible CU branches, while FDIC reports branch deposits. This '
            'is a cross-system proxy, not a directly observed market share.'
        ),
    },
    'lmi_lending_opportunity': {
        'raw_formula': (
            'originated HMDA loans in tracts with '
            'tract_to_msa_income_percentage < 80 / all originated HMDA loans'
        ),
        'normalization': (
            'Tie-aware percentile within qualifying markets of the same '
            'geography type; higher LMI-tract exposure is favorable'
        ),
        'sources': ('hmda', 'lodes'),
        'caveat': (
            'This measures current origination exposure in LMI tracts, not '
            'unmet demand, denial gaps, borrower income, or a CU-only split. '
            'Tracts are allocated to districts with workplace-employment '
            'weights.'
        ),
    },
    'large_employer_reach': {
        'raw_formula': (
            'CBP count of establishments with 500 or more employees'
        ),
        'normalization': (
            'Tie-aware percentile within qualifying markets of the same '
            'geography type; higher is favorable; incomplete CBP size-class '
            'evidence is treated as missing/neutral'
        ),
        'sources': ('cbp',),
        'caveat': (
            'This is an establishment count allocated from counties, not a '
            'named-employer ranking or employee count. Suppressed/unknown '
            'size cells are lower bounds and are not scored as complete.'
        ),
    },
    'small_business_reach': {
        'raw_formula': (
            'sum of CBP establishments with 1-19 employees in NAICS 23, '
            '44-45, 54, 62, 72, and 81'
        ),
        'normalization': (
            'Tie-aware percentile within qualifying markets of the same '
            'geography type; higher is favorable; incomplete CBP size-class '
            'evidence is treated as missing/neutral'
        ),
        'sources': ('cbp',),
        'caveat': (
            'County counts are allocated to districts with employment '
            'weights. Suppressed/unknown size cells are not scored as zero.'
        ),
    },
}
# footprint's two share components are the growth_room whitespace pair with
# the normalization direction corrected (design-d2-d5.md D3.a: the old
# 'normalization'/'caveat' text states the opposite of un-inverted scoring
# and must be rewritten, not reused, under the dial); the two LQ components
# are new. Sourced from the SAME catalog entries as their growth_room
# counterparts ('cbp' already carries district_industry_4digit.json; 'ncua'
# and 'fdic' already carry district_financial.json) -- no new agency exists
# to catalog, so SOURCE_CATALOG itself is unchanged and these definitions
# cite the existing keys.
COMPONENT_DEFINITIONS_FOOTPRINT = {
    'employment_lq': {
        'raw_formula': (
            'CU-sector (NAICS 522130) employment location quotient: '
            '(local CU employment / local all-industry employment) / '
            '(U.S. CU employment / U.S. all-industry employment)'
        ),
        'normalization': (
            'Tie-aware percentile within qualifying markets of the same '
            'geography type, at full (unrounded) precision; higher '
            'concentration is favorable'
        ),
        'sources': ('cbp',),
        'caveat': (
            'LQ is concentration relative to the national CU workforce '
            'share, not absolute employment scale or growth.'
        ),
    },
    'establishment_lq': {
        'raw_formula': (
            'CU-sector (NAICS 522130) establishment location quotient: '
            '(local CU establishments / local all-industry establishments) '
            '/ (U.S. CU establishments / U.S. all-industry establishments)'
        ),
        'normalization': (
            'Tie-aware percentile within qualifying markets of the same '
            'geography type, at full (unrounded) precision; higher '
            'concentration is favorable'
        ),
        'sources': ('cbp',),
        'caveat': (
            'LQ is concentration of establishment counts, not employment '
            'or deposit scale.'
        ),
    },
    'cu_branch_share': {
        'raw_formula': (
            'CU branches / (CU branches + FDIC-insured bank branches)'
        ),
        'normalization': (
            'Tie-aware percentile within qualifying markets of the same '
            'geography type; higher current CU branch share is favorable'
        ),
        'sources': ('ncua', 'fdic'),
        'caveat': (
            'Branch locations are geocoded/allocated to districts; unmatched '
            'and county-fallback records create estimation error.'
        ),
    },
    'cu_deposit_share': {
        'raw_formula': (
            'estimated CU shares / '
            '(FDIC bank deposits + estimated CU shares)'
        ),
        'normalization': (
            'Tie-aware percentile within qualifying markets of the same '
            'geography type; higher current CU deposit share is favorable'
        ),
        'sources': ('ncua', 'fdic'),
        'caveat': (
            'NCUA institution shares are divided equally across allocation-'
            'eligible CU branches, while FDIC reports branch deposits. This '
            'is a cross-system proxy, not a directly observed market share.'
        ),
    },
}
COMPONENT_DEFINITIONS = (
    COMPONENT_DEFINITIONS_FOOTPRINT if DEMO_POTENTIAL == 'footprint'
    else COMPONENT_DEFINITIONS_GROWTH_ROOM
)
SMALL_BUSINESS_NAICS = ('23', '44-45', '54', '62', '72', '81')
TIER_THRESHOLDS = (
    (0.90, 'Exceptional'),
    (0.70, 'Very High'),
    (0.40, 'High'),
    (0.00, 'Qualified'),
)

# Constituency-side and small-business sectors identified in the demographic
# expansion outline.  Sector 52 is intentionally absent: CU employment is a
# noisy benchmark, not the district opportunity story.
ELIGIBLE_SECTORS = (
    'CNS01',  # agriculture / rural field of membership
    'CNS04',  # construction
    'CNS05',  # manufacturing
    'CNS07',  # retail / auto lending
    'CNS11',  # real estate / mortgage demand
    'CNS12',  # professional and technical / small business
    'CNS15',  # educational services
    'CNS16',  # health care
    'CNS18',  # accommodation and food / small business
    'CNS19',  # other services / small business
    'CNS20',  # public administration (outside CBP)
)


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _thresholds(industry_4digit):
    source = ((industry_4digit or {}).get('thresholds') or {})
    lq_min = _number(source.get('lq_hi'))
    contradiction_floor = _number(source.get('lq_lo'))
    return (
        lq_min if lq_min is not None else LQ_MIN,
        contradiction_floor if contradiction_floor is not None
        else CBP_CONTRADICTION_FLOOR,
    )


def _district_total_establishments(row):
    total = 0.0
    found = False
    for value in ((row or {}).get('four_digit') or {}).values():
        if not isinstance(value, dict):
            continue
        establishments = _number(value.get('est'))
        if establishments is not None and establishments >= 0:
            total += establishments
            found = True
    return total if found and total > 0 else None


def _normalized_agreement(rows):
    out = {}
    for code, value in (rows or {}).items():
        if not isinstance(value, dict):
            continue
        out[code] = {
            'cbp_lq_est': _number(value.get('cbp_lq_est')),
            'contradicted_high': value.get('contradicted_high') is True,
            'contradicted_low': value.get('contradicted_low') is True,
        }
    return out


def agreement_for_geo(geo, industry_row, industry_4digit):
    """Return district or establishment-weighted state CBP agreement rows."""
    districts = ((industry_4digit or {}).get('districts') or {})
    exact = districts.get(geo)
    if isinstance(exact, dict):
        return _normalized_agreement(exact.get('sector_agreement'))

    if not re.fullmatch(r'[A-Z]{2}', str(geo or '')):
        return {}

    accumulators = {}
    for district_geo, row in districts.items():
        if not str(district_geo).startswith(str(geo) + '-'):
            continue
        weight = _district_total_establishments(row)
        if weight is None:
            continue
        for code, agreement in _normalized_agreement(
                row.get('sector_agreement')).items():
            cbp_lq = agreement.get('cbp_lq_est')
            if cbp_lq is None:
                continue
            weighted_sum, weight_sum = accumulators.get(code, (0.0, 0.0))
            accumulators[code] = (
                weighted_sum + cbp_lq * weight,
                weight_sum + weight,
            )

    lq_min, contradiction_floor = _thresholds(industry_4digit)
    lodes_lq = (industry_row or {}).get('lq_national') or {}
    out = {}
    for code in set(lodes_lq) | set(accumulators):
        weighted_sum, weight_sum = accumulators.get(code, (0.0, 0.0))
        cbp_lq = weighted_sum / weight_sum if weight_sum > 0 else None
        sector_lq = _number(lodes_lq.get(code))
        out[code] = {
            'cbp_lq_est': cbp_lq,
            'contradicted_high': bool(
                sector_lq is not None and sector_lq >= lq_min and
                cbp_lq is not None and cbp_lq < contradiction_floor
            ),
            'contradicted_low': bool(
                sector_lq is not None and sector_lq < contradiction_floor and
                cbp_lq is not None and cbp_lq >= lq_min
            ),
        }
    return out


def small_business_for_geo(geo, industry_4digit):
    """Return summed <20-employee establishments for a district or state."""
    value, _complete = small_business_metric_for_geo(
        geo, industry_4digit
    )
    return value


def small_business_metric_for_geo(geo, industry_4digit):
    """Return (value, complete) for tracked <20-employee establishments."""
    districts = ((industry_4digit or {}).get('districts') or {})
    exact = districts.get(geo)
    rows = [exact] if isinstance(exact, dict) else []
    if not rows and re.fullmatch(r'[A-Z]{2}', str(geo or '')):
        rows = [
            row for district_geo, row in districts.items()
            if str(district_geo).startswith(str(geo) + '-') and
            isinstance(row, dict)
        ]
    total = 0.0
    found = False
    complete = bool(rows)
    for row in rows:
        sectors = ((row or {}).get('small_business') or {})
        for code in SMALL_BUSINESS_NAICS:
            sector = sectors.get(code)
            if not isinstance(sector, dict):
                complete = False
                continue
            establishments = _number(sector.get('est_lt20'))
            if establishments is not None and establishments >= 0:
                total += establishments
                found = True
            else:
                complete = False
            if sector.get('lt20_complete') is not True:
                complete = False
    return (total if found else None, bool(found and complete))


def _percentile_ranks(values):
    """Tie-aware 0..1 midranks for a {key: numeric_value} mapping."""
    ordered = sorted(
        ((key, _number(value)) for key, value in (values or {}).items()
         if _number(value) is not None),
        key=lambda item: (item[1], str(item[0])),
    )
    size = len(ordered)
    if size == 0:
        return {}
    if size == 1:
        return {ordered[0][0]: 0.5}
    result = {}
    start = 0
    while start < size:
        end = start + 1
        while end < size and ordered[end][1] == ordered[start][1]:
            end += 1
        percentile = ((start + end - 1) / 2.0) / (size - 1)
        for index in range(start, end):
            result[ordered[index][0]] = percentile
        start = end
    return result


def _deposit_share(financial):
    banks = (financial or {}).get('banks') or {}
    cus = (financial or {}).get('cus') or {}
    bank_deposits = _number(banks.get('deposits_kusd'))
    cu_shares = _number(cus.get('est_shares_kusd'))
    if (bank_deposits is None or cu_shares is None or
            bank_deposits < 0 or cu_shares < 0):
        return None
    denominator = bank_deposits + cu_shares
    return cu_shares / denominator if denominator > 0 else None


# CreditUnionMetrics.for_geo() rounds both LQs to 3dp for display, which
# creates ties the footprint percentiles must not have (design-d2-d5.md
# D3.a: "the prototype ranked full precision (Davis 2.720625, not 2.721)").
# _cu_metrics_for caches one CreditUnionMetrics instance per industry_4digit
# artifact (score_members and evaluate() each receive that same artifact
# reference once per run); _unrounded_cu_lq re-derives the two LQs at full
# precision from the class's own building blocks instead of duplicating the
# formula by hand the way the read-only footprint_v2.py prototype had to.
_CU_METRICS_CACHE = {}


def _cu_metrics_for(industry_4digit):
    if not isinstance(industry_4digit, dict):
        return None
    key = id(industry_4digit)
    cached = _CU_METRICS_CACHE.get(key)
    if cached is None or cached[0] is not industry_4digit:
        metrics = DCM.CreditUnionMetrics(industry_4digit)
        _CU_METRICS_CACHE.clear()
        _CU_METRICS_CACHE[key] = (industry_4digit, metrics)
        return metrics
    return cached[1]


def _unrounded_cu_lq(metrics, geo):
    """Full-precision (employment_lq, establishment_lq) for one geo."""
    if metrics is None or not geo:
        return None, None
    rows, _scope = metrics._rows_for_geo(str(geo))
    if not rows:
        return None, None
    local_cu_emp = DCM._sum_values(rows, 'cu_benchmark', 'emp_' + DCM.CU_NAICS)
    local_cu_est = DCM._sum_values(rows, 'cu_benchmark', 'est_' + DCM.CU_NAICS)
    local_all_emp = DCM._sum_four_digit(rows, 'emp')
    local_all_est = DCM._sum_four_digit(rows, 'est')
    employment_lq = DCM._ratio_lq(
        local_cu_emp, local_all_emp,
        metrics.national.get('cu_emp'), metrics.national.get('all_emp'))
    establishment_lq = DCM._ratio_lq(
        local_cu_est, local_all_est,
        metrics.national.get('cu_est'), metrics.national.get('all_est'))
    return employment_lq, establishment_lq


def _score_raw(member, industry_4digit):
    if DEMO_POTENTIAL == 'footprint':
        return _score_raw_footprint(member, industry_4digit)
    return _score_raw_growth_room(member, industry_4digit)


def _score_raw_footprint(member, industry_4digit):
    financial = (member or {}).get('financial') or {}
    industry = (member or {}).get('industry') or {}
    metrics = _cu_metrics_for(industry_4digit)
    employment_lq, establishment_lq = _unrounded_cu_lq(
        metrics, industry.get('geo'))
    return {
        'employment_lq': employment_lq,
        'establishment_lq': establishment_lq,
        'cu_branch_share': _number(financial.get('cu_branch_share')),
        'cu_deposit_share': _deposit_share(financial),
    }


def _score_raw_growth_room(member, industry_4digit):
    potential = (member or {}).get('demographic_potential') or {}
    signals = potential.get('signals') or []
    # Use the SAME threshold evaluate() used to admit these signals; the
    # artifact may declare its own lq_hi, and a hardcoded 1.25 here silently
    # dropped qualifying sectors from lq_strength.
    lq_min, _contradiction_floor = _thresholds(industry_4digit)
    lq_values = [
        math.log(_number(row.get('lq')) / lq_min)
        for row in signals
        if _number(row.get('lq')) is not None and
        _number(row.get('lq')) >= lq_min
    ]
    shift_rates = [
        _number(row.get('competitive_shift_rate')) for row in signals
        if _number(row.get('competitive_shift_rate')) is not None
    ]
    financial = (member or {}).get('financial') or {}
    employers = (member or {}).get('employers') or {}
    large = employers.get('large') or {}
    industry = (member or {}).get('industry') or {}
    hmda = (member or {}).get('hmda') or {}
    small_business, small_business_complete = (
        small_business_metric_for_geo(
            industry.get('geo'), industry_4digit
        )
    )
    return {
        'lq_strength': max(lq_values) if lq_values else None,
        'competitive_shift': max(shift_rates) if shift_rates else None,
        'sector_breadth': float(len(signals)) if signals else None,
        'cu_branch_whitespace': _number(
            financial.get('cu_branch_share')),
        'cu_deposit_whitespace': _deposit_share(financial),
        'lmi_lending_opportunity': _number(
            hmda.get('lmi_tract_share')),
        'large_employer_reach': (
            _number(large.get('est_500plus'))
            if large.get('complete') is True else None
        ),
        'small_business_reach': (
            small_business if small_business_complete else None
        ),
    }


def _structural_controls(member):
    """Return complete structural controls for reach residualization."""
    industry = (member or {}).get('industry') or {}
    community = (member or {}).get('community') or {}
    estimates = community.get('estimates') or {}
    derived = community.get('derived') or {}
    employment = _number(
        (industry.get('employment') or {}).get('total')
    )
    population = _number(estimates.get('population'))
    density = _number(derived.get('population_density_sq_mile'))
    income = _number(estimates.get('median_household_income'))
    age = _number(estimates.get('median_age'))
    rent_income = _number(derived.get('annual_rent_income_ratio'))
    latest_year = _number(industry.get('latest_year'))
    region = str(community.get('region') or '').strip()
    if (
        employment is None or employment < 0 or
        population is None or population <= 0 or
        density is None or density < 0 or
        income is None or income <= 0 or
        age is None or rent_income is None or
        latest_year is None or not region
    ):
        return None
    return {
        'log_total_employment': math.log1p(employment),
        'log_population': math.log1p(population),
        'log_population_density': math.log1p(density),
        'log_median_household_income': math.log(income),
        'median_age': age,
        'annual_rent_income_ratio': rent_income,
        'lodes_latest_year': latest_year,
        'region': region,
    }


def _structural_design(markets, controls_by_market):
    """Return an OLS design with standardized numeric controls."""
    regions = sorted({
        controls_by_market[market]['region'] for market in markets
    })
    reference_region = (
        'Northeast' if 'Northeast' in regions
        else (regions[0] if regions else None)
    )
    region_columns = [
        region for region in regions if region != reference_region
    ]
    numeric = np.asarray([
        [
            float(controls_by_market[market][key])
            for key in STRUCTURAL_NUMERIC_CONTROLS
        ]
        for market in markets
    ], dtype=float)
    means = numeric.mean(axis=0)
    scales = numeric.std(axis=0)
    scales = np.where(scales > 0, scales, 1.0)
    standardized = (numeric - means) / scales
    rows = []
    for index, market in enumerate(markets):
        region = controls_by_market[market]['region']
        rows.append(
            [1.0] +
            standardized[index].tolist() +
            [1.0 if region == label else 0.0
             for label in region_columns]
        )
    predictor_names = (
        ['intercept'] +
        list(STRUCTURAL_NUMERIC_CONTROLS) +
        ['region_' + label for label in region_columns]
    )
    return (
        np.asarray(rows, dtype=float),
        predictor_names,
        reference_region,
        means,
        scales,
    )


def _fit_reach_residuals(markets, target, raw_by_market,
                         controls_by_market):
    """Fit log-count OLS and return residual ranks plus diagnostics."""
    markets = sorted(markets, key=str)
    if not markets:
        return None, {
            'status': 'no_complete_rows',
            'n': 0,
        }
    (
        design, predictor_names, reference_region,
        means, scales,
    ) = _structural_design(markets, controls_by_market)
    parameter_count = design.shape[1]
    if len(markets) < parameter_count + 3:
        return None, {
            'status': 'insufficient_sample',
            'n': len(markets),
            'parameter_count': int(parameter_count),
        }
    outcome = np.asarray([
        math.log1p(float(raw_by_market[market][target]))
        for market in markets
    ], dtype=float)
    coefficients, _residual_sum, rank, _singular = np.linalg.lstsq(
        design, outcome, rcond=None
    )
    expected = design.dot(coefficients)
    residual = outcome - expected
    residual_by_market = {
        market: float(residual[index])
        for index, market in enumerate(markets)
    }
    percentile_by_market = _percentile_ranks(residual_by_market)
    total_sum = float(((outcome - outcome.mean()) ** 2).sum())
    residual_sum = float((residual ** 2).sum())
    r_squared = (
        1.0 - residual_sum / total_sum if total_sum > 0 else 1.0
    )
    per_market = {
        market: {
            'available': True,
            'raw_count': round(float(raw_by_market[market][target]), 6),
            'observed_log_count': round(float(outcome[index]), 6),
            'expected_log_count': round(float(expected[index]), 6),
            'residual': round(float(residual[index]), 6),
            'adjusted_percentile': round(
                float(percentile_by_market[market]), 6
            ),
        }
        for index, market in enumerate(markets)
    }
    return per_market, {
        'status': 'fit',
        'n': len(markets),
        'parameter_count': int(parameter_count),
        'design_rank': int(rank),
        'r_squared': round(r_squared, 4),
        'dependent_variable': 'ln(1 + %s raw count)' % target,
        'predictors': predictor_names,
        'reference_region': reference_region,
        'coefficients': {
            predictor_names[index]: round(float(value), 10)
            for index, value in enumerate(coefficients)
        },
        'standardization': {
            key: {
                'mean': round(float(means[index]), 10),
                'scale': round(float(scales[index]), 10),
            }
            for index, key in enumerate(STRUCTURAL_NUMERIC_CONTROLS)
        },
    }


def _structural_adjustment(market_members, raw_by_market,
                           component_by_market, baseline_scores):
    """Build a descriptive companion score with adjusted reach counts."""
    if not set(STRUCTURAL_TARGETS).issubset(SCORE_WEIGHTS):
        # footprint scores neither large_employer_reach nor
        # small_business_reach (SCORE_WEIGHTS_FOOTPRINT has no such keys, so
        # raw_by_market/component_by_market never carry them either) --
        # the reach-residual companion has nothing to adjust under this mode.
        return {
            'version': STRUCTURAL_ADJUSTMENT_VERSION,
            'status': 'not_applicable_for_mode',
            'description': (
                'The reach-residual companion only applies when '
                'large_employer_reach and small_business_reach are scored '
                'components (DEMO_POTENTIAL=growth_room).'
            ),
            'targets': list(STRUCTURAL_TARGETS),
            'markets_with_complete_controls': 0,
            'adjusted_markets': 0,
            'diagnostics': {},
            'comparison_with_baseline': {
                'spearman_rho': None,
                'top_n': 0,
                'top_overlap_count': 0,
                'top_overlap_rate': None,
            },
        }, {}
    controls_by_market = {}
    for market, group in market_members.items():
        controls = _structural_controls(group[0])
        if controls is not None:
            controls_by_market[market] = controls

    adjusted_components = {
        market: dict(components)
        for market, components in component_by_market.items()
    }
    per_market = {
        market: {
            'version': STRUCTURAL_ADJUSTMENT_VERSION,
            'available': False,
            'controls_complete': market in controls_by_market,
            'components': {},
        }
        for market in market_members
    }
    diagnostics = {}
    fitted_kinds = set()
    for kind in ('district', 'state'):
        diagnostics[kind] = {}
        kind_markets = [
            market for market in market_members
            if market[0] == kind
        ]
        for target in STRUCTURAL_TARGETS:
            fit_markets = [
                market for market in kind_markets
                if market in controls_by_market and
                raw_by_market[market][target] is not None
            ]
            fitted, diagnostic = _fit_reach_residuals(
                fit_markets, target, raw_by_market, controls_by_market
            )
            diagnostics[kind][target] = diagnostic
            if fitted is None:
                continue
            fitted_kinds.add(kind)
            for market, detail in fitted.items():
                adjusted_components[market][target] = detail[
                    'adjusted_percentile'
                ]
                per_market[market]['components'][target] = detail

    eligible = [
        market for market in market_members
        if market in controls_by_market and market[0] in fitted_kinds
    ]
    adjusted_scores = _weighted_scores(
        {
            market: adjusted_components[market]
            for market in eligible
        },
        SCORE_WEIGHTS,
    )
    adjusted_ranks = _competition_ranks(adjusted_scores)
    adjusted_percentiles = _percentile_ranks(adjusted_scores)
    comparable_baseline_scores = {
        market: baseline_scores[market] for market in adjusted_scores
    }
    comparable_baseline_ranks = _competition_ranks(
        comparable_baseline_scores
    )
    for market in eligible:
        score = adjusted_scores[market]
        percentile = adjusted_percentiles[market]
        detail = per_market[market]
        detail.update({
            'available': True,
            'score': score,
            'rank': adjusted_ranks[market],
            'market_count': len(adjusted_scores),
            'percentile': round(percentile, 3),
            'tier': _tier(percentile),
            'score_delta': round(
                score - float(baseline_scores[market]), 1
            ),
            'rank_delta': (
                comparable_baseline_ranks[market] -
                adjusted_ranks[market]
            ),
            'baseline_rank_comparable_universe': (
                comparable_baseline_ranks[market]
            ),
            'controls': {
                key: (
                    round(value, 6) if isinstance(value, (int, float))
                    else value
                )
                for key, value in controls_by_market[market].items()
            },
        })
        for target in STRUCTURAL_TARGETS:
            detail['components'].setdefault(target, {
                'available': False,
                'reason': (
                    'reach_evidence_missing'
                    if raw_by_market[market][target] is None
                    else 'model_not_fit'
                ),
                'adjusted_percentile': round(
                    adjusted_components[market][target], 6
                ),
            })

    top_n = min(20, len(adjusted_scores))
    overlap = (
        len(
            _top_markets(comparable_baseline_scores, top_n) &
            _top_markets(adjusted_scores, top_n)
        )
        if top_n else 0
    )
    comparison = {
        'spearman_rho': (
            round(_rank_correlation(
                comparable_baseline_scores,
                adjusted_scores,
            ), 4)
            if adjusted_scores else None
        ),
        'top_n': top_n,
        'top_overlap_count': overlap,
        'top_overlap_rate': (
            round(overlap / top_n, 4) if top_n else None
        ),
    }
    return {
        'version': STRUCTURAL_ADJUSTMENT_VERSION,
        'status': 'descriptive_companion_not_causal',
        'description': (
            'For each geography type, regress ln(1 + employer-reach count) '
            'on standardized structural controls and Census-region fixed '
            'effects. Replace only the two reach-component percentiles with '
            'within-type residual percentiles; all other score components '
            'and policy weights remain unchanged.'
        ),
        'targets': list(STRUCTURAL_TARGETS),
        'controls': [
            dict({'key': key}, **value)
            for key, value in STRUCTURAL_CONTROL_DEFINITIONS.items()
        ],
        'excluded_need_indicators': [
            'ACS unemployment rate',
            'ACS poverty rate',
            'LQ and competitive shift',
            'CU branch and deposit whitespace',
            'LMI lending exposure',
        ],
        'excluded_reason': (
            'These are displayed or scored as opportunity/need signals; '
            'including them as controls would partially remove the target '
            'construct.'
        ),
        'markets_with_complete_controls': len(controls_by_market),
        'adjusted_markets': len(adjusted_scores),
        'diagnostics': diagnostics,
        'comparison_with_baseline': comparison,
    }, per_market


def _tier(percentile):
    for threshold, label in TIER_THRESHOLDS:
        if percentile >= threshold:
            return label
    return TIER_THRESHOLDS[-1][1]


def _weighted_scores(component_by_market, weights):
    return {
        market: round(100.0 * sum(
            float(weights[key]) * float(components[key])
            for key in weights
        ), 1)
        for market, components in component_by_market.items()
    }


def _competition_ranks(scores):
    return {
        market: 1 + sum(
            1 for other in scores.values() if other > score
        )
        for market, score in scores.items()
    }


def _rank_correlation(left, right):
    """Pearson correlation of tie-aware rank percentiles (Spearman rho)."""
    keys = sorted(set(left) & set(right), key=str)
    if len(keys) < 2:
        return 1.0
    left_ranks = _percentile_ranks({
        key: left[key] for key in keys
    })
    right_ranks = _percentile_ranks({
        key: right[key] for key in keys
    })
    left_mean = sum(left_ranks[key] for key in keys) / len(keys)
    right_mean = sum(right_ranks[key] for key in keys) / len(keys)
    numerator = sum(
        (left_ranks[key] - left_mean) *
        (right_ranks[key] - right_mean)
        for key in keys
    )
    left_sum = sum(
        (left_ranks[key] - left_mean) ** 2 for key in keys
    )
    right_sum = sum(
        (right_ranks[key] - right_mean) ** 2 for key in keys
    )
    denominator = math.sqrt(left_sum * right_sum)
    if denominator == 0:
        return 1.0 if all(
            left_ranks[key] == right_ranks[key] for key in keys
        ) else 0.0
    return numerator / denominator


def _top_markets(scores, count):
    return set(sorted(
        scores,
        key=lambda market: (
            -float(scores[market]), str(market[0]), str(market[1])
        ),
    )[:count])


def _sensitivity_audit(component_by_market, baseline_scores):
    """Compare the chosen weights with equal and leave-one-out scenarios."""
    market_count = len(baseline_scores)
    top_n = min(20, market_count)
    if not market_count:
        return {
            'version': SENSITIVITY_VERSION,
            'market_count': 0,
            'scenario_count': 0,
            'top_n': 0,
            'equal_weights': {},
            'leave_one_out': [],
        }, {}

    baseline_top = _top_markets(baseline_scores, top_n)
    equal_weights = {
        key: 1.0 / len(SCORE_WEIGHTS) for key in SCORE_WEIGHTS
    }
    scenario_scores = {
        'equal_weights': _weighted_scores(
            component_by_market, equal_weights
        ),
    }
    for omitted, omitted_weight in SCORE_WEIGHTS.items():
        remaining = 1.0 - omitted_weight
        scenario_scores['without_' + omitted] = _weighted_scores(
            component_by_market,
            {
                key: weight / remaining
                for key, weight in SCORE_WEIGHTS.items()
                if key != omitted
            },
        )

    def comparison(scores):
        overlap = len(baseline_top & _top_markets(scores, top_n))
        return {
            'spearman_rho': round(
                _rank_correlation(baseline_scores, scores), 4
            ),
            'top_overlap_count': overlap,
            'top_overlap_rate': round(
                overlap / top_n if top_n else 1.0, 4
            ),
        }

    equal_comparison = comparison(
        scenario_scores['equal_weights']
    )
    leave_one_out = []
    for component in SCORE_WEIGHTS:
        metrics = comparison(
            scenario_scores['without_' + component]
        )
        metrics['omitted_component'] = component
        leave_one_out.append(metrics)

    all_rankings = {
        'baseline': _competition_ranks(baseline_scores),
    }
    all_rankings.update({
        name: _competition_ranks(scores)
        for name, scores in scenario_scores.items()
    })
    all_top_sets = [
        _top_markets(baseline_scores, top_n),
    ] + [
        _top_markets(scores, top_n)
        for scores in scenario_scores.values()
    ]
    per_market = {}
    for market in baseline_scores:
        ranks = [
            ranking[market] for ranking in all_rankings.values()
        ]
        per_market[market] = {
            'rank_min': min(ranks),
            'rank_max': max(ranks),
            'rank_span': max(ranks) - min(ranks),
            'robust_top_n': all(
                market in top_set for top_set in all_top_sets
            ),
            'top_n': top_n,
        }

    min_rho = min(
        leave_one_out, key=lambda row: row['spearman_rho']
    )
    min_overlap = min(
        leave_one_out, key=lambda row: (
            row['top_overlap_rate'], row['spearman_rho']
        )
    )
    return {
        'version': SENSITIVITY_VERSION,
        'description': (
            'Diagnostic only: compare baseline ranks with equal weights and '
            'with each component omitted while the remaining chosen weights '
            'are proportionally rescaled.'
        ),
        'market_count': market_count,
        'scenario_count': len(scenario_scores),
        'top_n': top_n,
        'equal_weights': equal_comparison,
        'leave_one_out': leave_one_out,
        'minimum_leave_one_out_spearman': {
            'component': min_rho['omitted_component'],
            'spearman_rho': min_rho['spearman_rho'],
        },
        'minimum_leave_one_out_top_overlap': {
            'component': min_overlap['omitted_component'],
            'top_overlap_count': min_overlap['top_overlap_count'],
            'top_overlap_rate': min_overlap['top_overlap_rate'],
        },
    }, per_market


def score_members(members, industry_4digit):
    """Attach a deterministic opportunity score, market rank, and tier.

    Raw features are converted to tie-aware percentiles separately for
    district and state markets.  Lower CU branch/deposit shares are inverted
    because they represent expansion whitespace.  Missing features receive a
    neutral 0.5 component and reduce evidence_coverage rather than being
    silently treated as either favorable or unfavorable.
    """
    members = members or []
    market_members = {}
    for member in members:
        potential = member.get('demographic_potential') or {}
        potential.update({
            'score_version': SCORE_VERSION,
            'score': None,
            'rank': None,
            'market_count': None,
            'percentile': None,
            'tier': None,
            'components': {},
            'raw': {},
            'available': {},
            'evidence_coverage': 0.0,
            'sensitivity': {},
            'structural_adjustment': {
                'version': STRUCTURAL_ADJUSTMENT_VERSION,
                'available': False,
            },
        })
        industry = member.get('industry') or {}
        if not potential.get('qualified'):
            continue
        kind = str(industry.get('kind') or '')
        geo = str(industry.get('geo') or '')
        if kind not in ('district', 'state') or not geo:
            continue
        market_members.setdefault((kind, geo), []).append(member)

    raw_by_market = {
        market: _score_raw(group[0], industry_4digit)
        for market, group in market_members.items()
    }
    component_by_market = {market: {} for market in market_members}
    for kind in ('district', 'state'):
        kind_markets = [
            market for market in market_members if market[0] == kind
        ]
        for component in SCORE_WEIGHTS:
            values = {
                market: raw_by_market[market][component]
                for market in kind_markets
                if raw_by_market[market][component] is not None
            }
            ranks = _percentile_ranks(values)
            for market in kind_markets:
                value = ranks.get(market, 0.5)
                if component in (
                        'cu_branch_whitespace', 'cu_deposit_whitespace'):
                    value = 1.0 - value
                component_by_market[market][component] = value

    score_by_market = _weighted_scores(
        component_by_market, SCORE_WEIGHTS
    )
    score_percentiles = _percentile_ranks(score_by_market)
    rank_by_market = _competition_ranks(score_by_market)
    sensitivity, sensitivity_by_market = _sensitivity_audit(
        component_by_market, score_by_market
    )
    structural_adjustment, structural_by_market = _structural_adjustment(
        market_members, raw_by_market, component_by_market, score_by_market
    )
    market_count = len(score_by_market)

    for market, group in market_members.items():
        raw = raw_by_market[market]
        components = component_by_market[market]
        coverage = sum(
            SCORE_WEIGHTS[key] for key, value in raw.items()
            if value is not None
        )
        percentile = score_percentiles[market]
        update = {
            'score': score_by_market[market],
            'rank': rank_by_market[market],
            'market_count': market_count,
            'percentile': round(percentile, 3),
            'tier': _tier(percentile),
            'components': {
                key: round(components[key], 6) for key in SCORE_WEIGHTS
            },
            'raw': {
                key: None if raw[key] is None else round(raw[key], 6)
                for key in SCORE_WEIGHTS
            },
            'available': {
                key: raw[key] is not None for key in SCORE_WEIGHTS
            },
            'evidence_coverage': round(coverage, 3),
            'sensitivity': dict(
                sensitivity_by_market.get(market) or {}
            ),
            'structural_adjustment': dict(
                structural_by_market.get(market) or {
                    'version': STRUCTURAL_ADJUSTMENT_VERSION,
                    'available': False,
                }
            ),
        }
        for member in group:
            member['demographic_potential'].update(update)

    return {
        'qualified_members': sum(
            1 for member in members
            if (member.get('demographic_potential') or {}).get('qualified')
        ),
        'qualified_markets': market_count,
        'sensitivity_audit': sensitivity,
        'structural_adjustment': structural_adjustment,
    }


def evaluate(geo, industry_row, industry_4digit, sector_meta=None):
    """Evaluate one district/state and return the compact display contract."""
    if DEMO_POTENTIAL == 'footprint':
        return _evaluate_footprint(geo, industry_row, industry_4digit)
    return _evaluate_growth_room(
        geo, industry_row, industry_4digit, sector_meta=sector_meta)


def _evaluate_footprint(geo, industry_row, industry_4digit):
    """R7 gate: qualify on unrounded employment_lq >= 1.0, not a sector list.

    design-d2-d5.md D3.b: "Under footprint the gate becomes employment_lq
    >= 1.0 on the same unrounded LQ, with reason =
    'no_cu_employment_concentration'." No sector concept applies, so
    'signals' stays empty under this mode -- growth_room's _score_raw is the
    only reader of it and footprint never calls that path.
    """
    result = {
        'qualified': False,
        'label': LABEL,
        'signals': [],
    }
    if not isinstance(industry_row, dict):
        result['reason'] = 'industry_unavailable'
        return result
    if not isinstance(industry_4digit, dict):
        result['reason'] = 'four_digit_crosscheck_unavailable'
        return result
    metrics = _cu_metrics_for(industry_4digit)
    employment_lq, _establishment_lq = _unrounded_cu_lq(metrics, geo)
    result['employment_lq'] = (
        None if employment_lq is None else round(employment_lq, 6))
    result['qualified'] = (
        employment_lq is not None
        and employment_lq >= FOOTPRINT_EMPLOYMENT_LQ_MIN
    )
    if not result['qualified']:
        result['reason'] = 'no_cu_employment_concentration'
    return result


def _evaluate_growth_room(geo, industry_row, industry_4digit,
                          sector_meta=None):
    """Evaluate one district/state and return the compact display contract."""
    lq_min, contradiction_floor = _thresholds(industry_4digit)
    result = {
        'qualified': False,
        'label': LABEL,
        'signals': [],
    }
    if not isinstance(industry_row, dict):
        result['reason'] = 'industry_unavailable'
        return result
    if not isinstance(industry_4digit, dict):
        result['reason'] = 'four_digit_crosscheck_unavailable'
        return result

    agreement = agreement_for_geo(geo, industry_row, industry_4digit)
    lq_values = industry_row.get('lq_national') or {}
    shift_share = industry_row.get('shift_share') or {}
    employment = (industry_row.get('employment') or {}).get('by_sector') or {}
    flags = industry_row.get('flags') or {}
    small = set(flags.get('small_sectors') or [])
    small.update(flags.get('small_base_sectors') or [])
    sector_meta = sector_meta or {}

    signals = []
    for code in ELIGIBLE_SECTORS:
        lq = _number(lq_values.get(code))
        shift = _number((shift_share.get(code) or {}).get(
            'competitive_shift'))
        if (code in small or lq is None or lq < lq_min or
                shift is None or shift <= 0):
            continue
        agreement_row = agreement.get(code) or {}
        cbp_lq = _number(agreement_row.get('cbp_lq_est'))
        # CBP does not contain NAICS 92.  Other sectors require a usable
        # cross-check so missing evidence never turns into a positive sticker.
        if code != 'CNS20' and cbp_lq is None:
            continue
        if agreement_row.get('contradicted_high') is True:
            continue
        meta = sector_meta.get(code) or {}
        shift_row = shift_share.get(code) or {}
        current_jobs = _number(employment.get(code))
        national_growth = _number(shift_row.get('national_growth'))
        industry_mix = _number(shift_row.get('industry_mix'))
        baseline_jobs = None
        competitive_shift_rate = None
        if (current_jobs is not None and national_growth is not None and
                industry_mix is not None):
            baseline_jobs = (
                current_jobs - national_growth - industry_mix - shift)
            if baseline_jobs > 0:
                competitive_shift_rate = shift / baseline_jobs
        signals.append({
            'sector': code,
            'naics': str(meta.get('naics') or ''),
            'name': str(meta.get('name') or code),
            'lq': round(lq, 3),
            'competitive_shift': round(shift, 1),
            'current_jobs': (
                None if current_jobs is None else round(current_jobs, 1)),
            'baseline_jobs': (
                None if baseline_jobs is None else round(baseline_jobs, 1)),
            'competitive_shift_rate': (
                None if competitive_shift_rate is None
                else round(competitive_shift_rate, 6)),
            'cbp_lq_est': None if cbp_lq is None else round(cbp_lq, 3),
            'crosscheck': (
                'not_covered' if code == 'CNS20'
                else 'passed'
            ),
        })

    signals.sort(key=lambda row: (
        -row['lq'], -row['competitive_shift'], row['sector']))
    result['signals'] = signals
    result['qualified'] = bool(signals)
    if not signals:
        result['reason'] = 'no_aligned_sector'
    return result


def metadata(industry_4digit, sector_meta=None, qualified_members=0,
             qualified_markets=0, small_sector_min=500,
             sensitivity_audit=None, structural_adjustment=None):
    lq_min, contradiction_floor = _thresholds(industry_4digit)
    sector_meta = sector_meta or {}
    if DEMO_POTENTIAL == 'footprint':
        score_formula = (
            '100 x sum(component percentile x chosen policy weight). '
            'Percentiles are tie-aware and calculated separately within '
            'district and state markets, using full-precision employment '
            'and establishment location quotients and un-inverted CU '
            'branch/deposit shares -- higher current credit-union '
            'footprint scores higher. Missing evidence is neutral at 0.50 '
            'and lowers the displayed evidence-coverage rate.'
        )
        method = (
            'Qualifies when the district or state credit-union employment '
            'location quotient (full precision, not the 3dp display '
            'rounding) is >= %.2f -- credit-union employment there is at '
            'least as concentrated as it is nationally.'
            % FOOTPRINT_EMPLOYMENT_LQ_MIN
        )
    else:
        score_formula = (
            '100 x sum(component percentile x chosen policy weight). '
            'Percentiles are tie-aware and calculated separately within '
            'district and state markets. CU branch and deposit-share '
            'percentiles are inverted so lower current CU penetration means '
            'more expansion whitespace. Missing evidence is neutral at 0.50 '
            'and lowers the displayed evidence-coverage rate.'
        )
        method = (
            'At least one credit-union-relevant constituency sector has '
            'LQ >= %.2f and positive competitive shift; sectors at or below '
            '%d jobs and CBP four-digit contradictions are excluded. '
            'NAICS 92 is outside the CBP universe and is labeled not covered.'
            % (lq_min, int(small_sector_min))
        )
    metadata_out = {
        'label': LABEL,
        'qualified_members': int(qualified_members),
        'qualified_markets': int(qualified_markets),
        'lq_min': lq_min,
        'competitive_shift_min_exclusive': 0,
        'cbp_contradiction_floor': contradiction_floor,
        'small_sector_min_jobs': int(small_sector_min),
        'eligible_sectors': [
            {
                'sector': code,
                'naics': str((sector_meta.get(code) or {}).get('naics') or ''),
                'name': str((sector_meta.get(code) or {}).get('name') or code),
            }
            for code in ELIGIBLE_SECTORS
        ],
        'score_version': SCORE_VERSION,
        'score_weights': SCORE_WEIGHTS,
        'weight_basis': WEIGHT_BASIS,
        'weight_rationale': WEIGHT_RATIONALE,
        'not_empirically_optimized': True,
        'optimization_requirement': OPTIMIZATION_REQUIREMENT,
        'component_labels': COMPONENT_LABELS,
        'component_definitions': [
            dict(
                {
                    'key': key,
                    'label': COMPONENT_LABELS[key],
                    'weight': SCORE_WEIGHTS[key],
                },
                **{
                    field: (
                        list(value) if isinstance(value, tuple)
                        else value
                    )
                    for field, value
                    in COMPONENT_DEFINITIONS[key].items()
                }
            )
            for key in SCORE_WEIGHTS
        ],
        'source_catalog': SOURCE_CATALOG,
        'sensitivity_audit': sensitivity_audit or {},
        'structural_adjustment': structural_adjustment or {
            'version': STRUCTURAL_ADJUSTMENT_VERSION,
            'status': 'unavailable',
        },
        'tier_thresholds': [
            {'percentile_min': threshold, 'label': label}
            for threshold, label in TIER_THRESHOLDS
        ],
        'score_formula': score_formula,
        'method': method,
    }
    if DEMO_POTENTIAL == 'footprint':
        # New key, footprint-only: growth_room's output must stay
        # byte-identical to before this dial existed (design-d2-d5.md D3.e
        # test_growth_room_dial_is_byte_identical).
        metadata_out['footprint_employment_lq_min'] = (
            FOOTPRINT_EMPLOYMENT_LQ_MIN
        )
    return metadata_out

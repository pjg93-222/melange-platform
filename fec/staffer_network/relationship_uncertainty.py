"""Explicit scenario costs for unknown present access, not signed-link predictions.

Employment is exposure evidence, not a positive social edge. Neither paper's
signed/content-interaction inputs are present in the employment route index.
Costs below are assumptions; they must not be displayed as probabilities.
"""
import math
from datetime import date

VERSION = 'staffer-access-uncertainty-v2-overlap-runs'
UNKNOWN_USABILITY_COST = .35
CONCENTRATION_COST = .25
STALE_HALF_LIFE_YEARS = 5.
DAYS_PER_YEAR = 365.2425


def uncertainty(support, as_of):
    """support: positive exposure, last *joint* covered day, context group.

    Same-context records are combined before concentration is calculated.
    Age is exposure weighted: a token recent posting cannot refresh a large
    historical relationship. Distinct workplaces indicate different contexts,
    never independent observations of sentiment or willingness.
    """
    as_of = as_of if isinstance(as_of, int) else date.fromisoformat(str(as_of)[:10]).toordinal()
    rows = [row for row in support if row['effective_exposure_days'] > 0]
    total = sum(row['effective_exposure_days'] for row in rows)
    if not total:
        return {'model': VERSION, 'cost': 0., 'factor': 1., 'status': 'no_supported_connection'}
    groups, age = {}, 0.
    for row in rows:
        share = row['effective_exposure_days'] / total
        group = row.get('context_group') or row['employer_id']
        groups[group] = groups.get(group, 0.) + share
        age += share * max(0, as_of - row.get('exposure_weighted_last_overlap_day', row['last_overlap_day'])) / DAYS_PER_YEAR
    concentration = sum(share * share for share in groups.values())
    components = {'unverified_usability': UNKNOWN_USABILITY_COST,
                  'staleness': math.log(2) * age / STALE_HALF_LIFE_YEARS,
                  'workplace_concentration': CONCENTRATION_COST * concentration}
    cost = sum(components.values())
    return {'model': VERSION, 'cost': cost, 'factor': math.exp(-cost),
            'components': components, 'weighted_years_since_overlap': age,
            'context_concentration': concentration, 'context_groups': len(groups),
            'as_of': date.fromordinal(as_of).isoformat(),
            'status': 'unverified_present_access', 'relationship_sign': 'unknown',
            'calibrated_probability': False}


def specification(as_of):
    return {'model': VERSION, 'as_of': str(as_of),
            'unknown_usability_cost': UNKNOWN_USABILITY_COST,
            'concentration_cost': CONCENTRATION_COST,
            'stale_half_life_years': STALE_HALF_LIFE_YEARS,
            'calibration': 'Exploratory log-cost scenarios; not fitted success or friendship probabilities',
            'formula': 'adjusted = structural * exp(-weight * uncertainty_cost)',
            'missing_sentiment': 'unknown; never treated as observed positive or negative',
            'scope': 'dated employment evidence; no signed/content-interaction classifier'}

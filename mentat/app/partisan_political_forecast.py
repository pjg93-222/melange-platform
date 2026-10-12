"""Political-feature D/R transfers on explicit, matching-scope PAC dollar ranges.

This runtime fits nothing and never reads the older published giving models.
The caller supplies source-qualified baseline/remaining ranges and a frozen
model. Uncertainty ranges describe source classification, not prediction or
coefficient confidence. Historical revised ideology is reconstructed evidence.
"""
from __future__ import annotations

import copy
from datetime import date
import hashlib
import json
import math
from pathlib import Path

MODEL_ID = 'pac-partisan-political-transfer-v1'
ACCOUNTING_BASIS = 'canonical_cycle_office_nominal_pac_D_R'
FEATURE_DEFINITION = 'lagged_prior_cycle_bill_attention_top25'
CURRENT_CHAMPION_DEFINITION = 'actual_current_signed_champions_v2_board_not_training_proxy'
CURRENT_SCENARIO_DEFINITION = 'current_champion_board_substitution_scenario'
VARIANT_FEATURES = {
    'baseline': [],
    'lean_only': ['target_r_share'],
    'both': ['target_r_share', 'champion_mean_dw'],
}


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(name + ' must be finite')
    return float(value)


def _ranges(value, name):
    if not isinstance(value, dict) or set(value) != {'D', 'R'}:
        raise ValueError(name + ' must include exactly D and R')
    result = {}
    for party in ('D', 'R'):
        bounds = value[party]
        if not isinstance(bounds, dict) or set(bounds) != {'lo_cents', 'hi_cents'}:
            raise ValueError(name + ' must use lo_cents and hi_cents')
        lo, hi = (_number(bounds[key], name + '.' + party + '.' + key)
                  for key in ('lo_cents', 'hi_cents'))
        if lo > hi:
            raise ValueError(name + ' has reversed bounds')
        result[party] = {'lo_cents': lo, 'hi_cents': hi}
    return result


def _model(model, variant):
    if variant not in VARIANT_FEATURES:
        raise ValueError('Unknown political feature variant')
    if not isinstance(model, dict) or model.get('features') != VARIANT_FEATURES[variant]:
        raise ValueError('Model features do not match the explicit variant')
    names = model['features']
    insufficient = model.get('status') == 'insufficient_earlier_elections'
    arrays = {}
    for key in ('means', 'scales', 'coefficients'):
        values = model.get(key)
        if not isinstance(values, list) or len(values) != (0 if insufficient else len(names)):
            raise ValueError('Model ' + key + ' shape mismatch')
        arrays[key] = [_number(value, key) for value in values]
    if any(scale <= 0 for scale in arrays['scales']):
        raise ValueError('Model scales must be positive')
    if model.get('feature_definition') != FEATURE_DEFINITION or model.get('ridge_lambda') != 1:
        raise ValueError('Unsupported feature definition or ridge penalty')
    cycles = model.get('training_cycles')
    if (not isinstance(cycles, list) or cycles != sorted(set(cycles))
            or any(type(cycle) is not int or cycle < 2012 or cycle % 2 for cycle in cycles)):
        raise ValueError('Invalid model training cycles')
    return arrays


def project(row, model, variant='both'):
    """Return a zero-sum Republican transfer; inputs are never mutated.

    Contributions are BEFORE the common transfer clamp. A positive delta adds
    to Republican dollars and subtracts the identical amount from Democrats.
    Missing features, incompatible definitions, or negative remaining lower
    bounds retain the complete baseline and report the reason.
    """
    return _project(row, model, variant, FEATURE_DEFINITION)


def _project(row, model, variant, input_definition):
    """Shared arithmetic; the fitted model and input definitions stay distinct."""
    arrays = _model(model, variant)
    if not isinstance(row, dict):
        raise ValueError('Forecast row must be an object')
    sector, chamber, target = row.get('sector'), row.get('chamber'), row.get('target')
    if not isinstance(sector, str) or not sector.strip() or chamber not in ('House', 'Senate'):
        raise ValueError('Explicit sector and House/Senate chamber are required')
    if type(target) is not int or target < 2018 or target % 2:
        raise ValueError('Target must be an even election year from 2018')
    as_of = row.get('as_of')
    if not isinstance(as_of, str) or date.fromisoformat(as_of).isoformat() != as_of:
        raise ValueError('as_of must be an ISO date')
    if any(cycle >= target for cycle in model['training_cycles']):
        raise ValueError('Training includes the target or a future cycle')
    baseline, remaining = _ranges(row.get('baseline'), 'baseline'), _ranges(row.get('remaining'), 'remaining')
    scale = _number(row.get('prior_scale_cents'), 'prior_scale_cents')
    if scale <= 0:
        raise ValueError('Prior-cycle scale must be positive')
    features, provenance = row.get('features', {}), row.get('feature_provenance', {})
    if not isinstance(features, dict) or not isinstance(provenance, dict):
        raise ValueError('Features and their provenance must be objects')
    result = {
        'sector': sector, 'chamber': chamber, 'target': target, 'as_of': as_of,
        'variant': variant, 'baseline': baseline, 'adjusted': copy.deepcopy(baseline),
        'remaining': remaining, 'prior_scale_cents': scale,
        'features': copy.deepcopy(features), 'feature_provenance': copy.deepcopy(provenance),
        'raw_delta_cents': 0.0, 'delta_cents': 0.0, 'clamped': False,
        'contributions': [], 'status': 'baseline' if variant == 'baseline' else 'adjusted',
        'issues': [], 'range_kind': 'source_classification_bounds_not_prediction_intervals',
    }
    for key in ('label', 'horizon', 'baseline_family', 'scope', 'mode', 'cut', 'issue'):
        if key in row:
            result[key] = copy.deepcopy(row[key])
    if variant == 'baseline':
        return result
    if model.get('status') == 'insufficient_earlier_elections' or len(model['training_cycles']) < 2:
        result['status'] = 'baseline_fallback'
        result['issues'].append('Fewer than two earlier election origins; political features are not fitted.')
        return result
    if provenance.get('feature_definition') != input_definition:
        result['issues'].append('Feature definition is missing or differs from the fitted model.')
    for name in model['features']:
        if features.get(name) is None:
            result['issues'].append('Missing feature: ' + name)
        else:
            value = _number(features[name], name)
            low, high = (0, 1) if name == 'target_r_share' else (-1, 1)
            if not low <= value <= high:
                raise ValueError(name + ' outside its measurement range')
    if any(bounds['lo_cents'] < 0 for bounds in remaining.values()):
        result['issues'].append('Negative remaining lower bound; observed giving cannot safely support a transfer.')
    if any(bounds['lo_cents'] < 0 for bounds in baseline.values()):
        result['issues'].append('Negative signed baseline lower bound; retaining the source range without a political transfer.')
    if result['issues']:
        result['status'] = 'baseline_fallback'
        return result
    for index, name in enumerate(model['features']):
        value = float(features[name])
        standardized = (value - arrays['means'][index]) / arrays['scales'][index]
        contribution = scale * arrays['coefficients'][index] * standardized
        _number(contribution, 'feature contribution')
        result['contributions'].append({
            'feature': name, 'value': value, 'standardized_value': standardized,
            'coefficient': arrays['coefficients'][index], 'raw_delta_cents': contribution,
        })
    raw = _number(sum(term['raw_delta_cents'] for term in result['contributions']), 'raw transfer')
    lower, upper = -remaining['R']['lo_cents'], remaining['D']['lo_cents']
    delta = min(upper, max(lower, raw))
    result.update(raw_delta_cents=raw, delta_cents=delta, clamped=delta != raw)
    for party, sign in (('R', 1), ('D', -1)):
        for endpoint in ('lo_cents', 'hi_cents'):
            result['adjusted'][party][endpoint] = _number(baseline[party][endpoint] + sign * delta, 'adjusted bound')
        if result['adjusted'][party]['lo_cents'] < 0:
            raise ValueError('Baseline and remaining are inconsistent: transfer would make giving negative')
    return result


def project_current_champions(row, model, profile, context_date='2026-10-06'):
    """Unvalidated sensitivity: substitute only the actual current board mean.

    The lagged target lean, trained coefficients, base dollars and clamp are
    unchanged. This deliberately transports the fitted proxy coefficient to a
    different input definition. It inherits no historical accuracy claim.
    """
    if not isinstance(context_date, str) or date.fromisoformat(context_date).isoformat() != context_date:
        raise ValueError('Context date must be an ISO date')
    if not isinstance(row, dict):
        raise ValueError('Forecast row must be an object')
    scenario = copy.deepcopy(row)
    original_features = copy.deepcopy(row.get('features', {}))
    original_provenance = copy.deepcopy(row.get('feature_provenance', {}))
    if not isinstance(original_features, dict) or not isinstance(original_provenance, dict):
        raise ValueError('Features and their provenance must be objects')
    reason, actual_mean = None, None
    if profile is None:
        reason = 'Actual current champion board is unavailable; no mean has been imputed.'
    elif not isinstance(profile, dict):
        raise ValueError('Current champion profile must be an object or null')
    else:
        if (profile.get('industry', profile.get('sector')) != row.get('sector')
                or profile.get('chamber') != row.get('chamber')
                or str(profile.get('cycle')) != str(row.get('target'))):
            raise ValueError('Current champion profile population/chamber/cycle mismatch')
        profile_date = profile.get('as_of_date')
        if (not isinstance(profile_date, str) or date.fromisoformat(profile_date).isoformat() != profile_date
                or profile_date > context_date):
            raise ValueError('Current champion profile is undated or later than the scenario context')
        value = profile.get('actual_current_champion_mean_dw_nominate_dim1')
        if (profile.get('actual_champion_status') != 'available_current_board'
                or profile.get('actual_champion_definition') != CURRENT_CHAMPION_DEFINITION
                or value is None or value == ''):
            reason = 'Actual current champion board is unavailable; no mean has been imputed.'
        else:
            # CSV profiles may carry numeric strings; empty strings above are missing.
            if isinstance(value, str):
                value = float(value)
            actual_mean = _number(value, 'actual current champion mean')
            if not -1 <= actual_mean <= 1:
                raise ValueError('Actual champion ideology outside its measurement range')
            counts = [profile.get(key) for key in ('actual_current_champion_n', 'actual_current_champion_score_n')]
            counts = [int(value) if (type(value) is int or isinstance(value, str) and value.isdigit()) else 0
                      for value in counts]
            if counts[0] <= 0 or counts[1] != counts[0]:
                reason = 'A complete scored current champion board is unavailable; no partial mean is substituted.'
                actual_mean = None
    if original_provenance.get('feature_definition') != FEATURE_DEFINITION:
        reason = 'The original lagged political feature definition is unavailable; no substitution is applied.'
        actual_mean = None
    scenario['features'] = {**original_features, 'champion_mean_dw': actual_mean}
    scenario['feature_provenance'] = {
        'feature_definition': CURRENT_SCENARIO_DEFINITION,
        'trained_feature_definition': FEATURE_DEFINITION,
        'current_champion_definition': CURRENT_CHAMPION_DEFINITION,
        'input_substitution': True,
        'context_as_of': context_date,
        'board_profile_as_of': profile.get('as_of_date') if profile else None,
        'dollar_base_issue': row.get('issue'),
        'original_lagged_provenance': original_provenance,
        'current_board_profile': copy.deepcopy(profile),
    }
    result = _project(scenario, model, 'both', CURRENT_SCENARIO_DEFINITION)
    result.update(variant='current_champions', mode='unvalidated_current_champion_sensitivity',
                  historical_validation_applicable=False, calibrated_forecast=False,
                  original_trained_features=original_features, context_as_of=context_date)
    if result['status'] == 'adjusted':
        result['status'] = 'scenario_adjusted'
    if reason:
        result['issues'].append(reason)
    result['scenario_warning'] = 'Unvalidated current-champion substitution; current context is applied to the dated dollar base.'
    return result


def catalog(bundle):
    """Three fitted comparisons plus an optional, unvalidated substitution."""
    if (not isinstance(bundle, dict) or bundle.get('schema_version') != 1
            or bundle.get('model_id') != MODEL_ID or bundle.get('currency') != 'USD'
            or bundle.get('accounting_basis') != ACCOUNTING_BASIS
            or bundle.get('active_variant') != 'both'):
        raise ValueError('Unsupported political forecast bundle')
    models = bundle.get('models')
    if not isinstance(models, dict) or set(models) != set(VARIANT_FEATURES):
        raise ValueError('Bundle must retain baseline, lean_only and both models')
    for variant, model in models.items():
        _model(model, variant)
    rows = bundle.get('current_rows')
    if not isinstance(rows, list):
        raise ValueError('current_rows must be an explicit list; missing is not zero')
    result = {key: copy.deepcopy(bundle.get(key)) for key in (
        'schema_version', 'model_id', 'currency', 'accounting_basis', 'active_variant',
        'built_at', 'limitations', 'provenance', 'status', 'current_profiles', 'validation_summary',
    ) if key in bundle}
    result['models'] = copy.deepcopy(models)
    result['rows'] = []
    profiles = bundle.get('current_profiles', [])
    if not isinstance(profiles, list):
        raise ValueError('current_profiles must be a list')
    profile_lookup = {}
    for profile in profiles:
        if not isinstance(profile, dict):
            raise ValueError('Current champion profile must be an object')
        key = (profile.get('industry', profile.get('sector')), profile.get('chamber'))
        if key in profile_lookup:
            raise ValueError('Duplicate current champion profile')
        profile_lookup[key] = profile
    seen = set()
    for row in rows:
        variants = {name: project(row, model, name) for name, model in models.items()}
        active = copy.deepcopy(variants['both'])
        variants['current_champions'] = project_current_champions(
            row, models['both'], profile_lookup.get((row.get('sector'), row.get('chamber'))),
            context_date=bundle.get('current_champion_context_date', '2026-10-06'))
        identity = (active['sector'], active['chamber'], active['target'], active['as_of'], active.get('horizon'))
        if identity in seen:
            raise ValueError('Duplicate current forecast row')
        seen.add(identity)
        active['variants'] = variants
        result['rows'].append(active)
    result['replaces_published_pac_total'] = False
    return result


def load_catalog(path, expected_sha256):
    """Read a separately pinned bundle; no implicit live pointer or fallback."""
    raw = Path(path).read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if not isinstance(expected_sha256, str) or digest != expected_sha256.lower():
        raise ValueError('Political bundle SHA-256 mismatch')
    result = catalog(json.loads(raw))
    result['bundle_sha256'] = digest
    return result

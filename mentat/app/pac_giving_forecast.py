"""Selected R12 PAC-only point forecast; no collection, fitting, or scoring.

Amounts are nominal cents for five explicitly pinned cohorts.  This module
does not consume the legacy general-giving anchors, CPI, slopes, or intervals.
The bundle producer pins and audits source availability and DUE selection;
the runtime validates the published frame and never reads raw giving files.
"""
from datetime import date, datetime, timezone
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import re
from runtime_paths import app_data_root

MODEL_ID = 'pac-r12-origin-half-r8-nowcast-v1'
ACCOUNTING_BASIS = 'nominal_pac_to_house_senate_net'
PRODUCTS = ('ORIGIN', 'm12', 'm15', 'm18', 'm21', 'L')
COHORTS = {
    'commercial_banks': (78, '356351b975a06cccf88d3c677988668b9e0812a4b3db0f3584c2664ae5660fd8'),
    'credit_unions': (16, 'd0e1b16b3bfb54f6e827dbae865c5d1935adb472ac3ea2651de959791077902c'),
    'property_casualty': (8, 'edd4282ea79757a677f7b277c23ccc72bc66b0ca8a11fff1dce5f6589767cd9e'),
    'credit_arms': (6, '2780dadf7b65033319f538c2530b2c609c285764a8741a6c59efce21249c1077'),
    'telecom': (8, 'd555b708f2b781afd84a9cbaff59b0bbf82980227326ab05de2d05b32d95afe6'),
}
LABELS = {'commercial_banks': 'Commercial banks', 'credit_unions': 'Credit unions',
          'property_casualty': 'Property & casualty', 'credit_arms': 'Credit arms',
          'telecom': 'Telecom'}
_FRAME_KEYS = {'sector', 'target', 'product', 'issue', 'cut', 'information_as_of',
               'data_available_at', 'units', 'accounting_basis', 'history_cycles',
               'totals', 'historical_observed', 'observed', 'annual', 'log_growth',
               'cohort', 'provenance'}


def _finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, ValueError):
        return False


def _positive(value):
    return _finite(value) and value > 0


def _date(value, name):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError(name + ' must be an ISO calendar date')
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(name + ' is not a valid calendar date') from error


def schedule(target, product):
    """Return the inherited information date and transaction cut."""
    if type(target) is not int or target < 2018 or target % 2:
        raise ValueError('target must be an even election year at least 2018')
    if product not in PRODUCTS:
        raise ValueError('unknown product')
    if product == 'ORIGIN':
        return date(target-1, 1, 31), None
    dates = {'m12': (1, 31, target-1, 12, 31), 'm15': (4, 20, target, 3, 31),
             'm18': (7, 20, target, 6, 30), 'm21': (10, 20, target, 9, 30),
             'L': (9, 26, target, 9, 26)}
    m, d, cy, cm, cd = dates[product]
    return date(target, m, d), date(cy, cm, cd)


def _cohort(value, sector):
    count, sha = COHORTS[sector]
    if (not isinstance(value, dict) or value.get('id') != sector or
            type(value.get('count')) is not int or value['count'] != count or
            value.get('ids_sha256') != sha):
        raise ValueError('cohort differs from the selected fixed roster')


def _years(value, expected, name, money=True, allow_missing=False):
    if value is None and allow_missing:
        return None
    keys = {str(y) for y in expected}
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError(name + ' must have exactly the required JSON year keys')
    for v in value.values():
        if v is None:
            continue
        if money and type(v) is not int:
            raise ValueError(name + ' must contain integer cents or null')
        if not money and (isinstance(v, bool) or not isinstance(v, (int, float))):
            raise ValueError(name + ' must contain numeric log factors or null')
    return {int(y): v for y, v in value.items()}


def calculate(frame, *, today=None):
    """Pure selected formula. Invalid contract raises; missing domain is unavailable.

    `information_as_of` controls the producer's DUE filtering. `issue` is the
    actual publication date and may be later, never silently backdated. No
    completed target outcome is accepted in this closed frame contract.
    """
    today = date.today() if today is None else today
    if not isinstance(frame, dict) or set(frame) - _FRAME_KEYS:
        raise ValueError('unknown frame field; target truth is not a forecast input')
    required = _FRAME_KEYS - {'provenance'}
    if not required.issubset(frame):
        raise ValueError('frame is missing required contract fields')
    sector, target, product = frame['sector'], frame['target'], frame['product']
    if sector not in COHORTS:
        raise ValueError('unknown sector; community banking is a distinct scope')
    expected_information, expected_cut = schedule(target, product)
    issue = _date(frame['issue'], 'issue')
    information = _date(frame['information_as_of'], 'information_as_of')
    available = _date(frame['data_available_at'], 'data_available_at')
    cut = None if frame['cut'] is None else _date(frame['cut'], 'cut')
    if information != expected_information or cut != expected_cut:
        raise ValueError('information date or transaction cut differs from the selected product')
    if issue < information or issue > today or available > issue:
        raise ValueError('future data, premature issue, or future publication is not allowed')
    if frame['units'] != 'cents' or frame['accounting_basis'] != ACCOUNTING_BASIS:
        raise ValueError('only nominal PAC-to-House/Senate integer cents are supported')
    _cohort(frame['cohort'], sector)
    window = [target-6, target-4, target-2]
    if (frame['history_cycles'] != window or
            any(type(y) is not int for y in frame['history_cycles'])):
        raise ValueError('history must be exactly the three immediately prior cycles')
    totals = _years(frame['totals'], window, 'totals')
    observed = frame['observed']
    if observed is not None and type(observed) is not int:
        raise ValueError('observed must be integer cents or null')
    if product == 'ORIGIN':
        if observed is not None or frame['historical_observed'] not in (None, {}):
            raise ValueError('ORIGIN must not contain pace observations')
        history = None
        annual = _years(frame['annual'], (target-3, target-2), 'annual', allow_missing=True)
        growth = _years(frame['log_growth'], (target-3, target-2, target-1),
                        'log_growth', money=False, allow_missing=True)
    else:
        history = _years(frame['historical_observed'], window, 'historical_observed')
        if frame['annual'] is not None or frame['log_growth'] is not None:
            raise ValueError('selected nowcasts do not consume IBIS or annual budgets')
        annual, growth = None, None
    trace = {'model_id': MODEL_ID, 'sector': sector, 'target': target, 'product': product,
             'window': window, 'B': None, 'U': None, 'completion_share': None,
             'remaining_cents': None, 'history': [], 'issues': [],
             'formula': '0.5*B+0.5*U' if product == 'ORIGIN' else 'O+(1-s)*B'}

    def result(point=None, reason=None):
        if reason:
            trace['issues'].append(reason)
        status = 'AVAILABLE' if point is not None else 'UNAVAILABLE'
        trace['status'] = status
        return {'status': status, 'prediction_cents': point,
                'trace': trace, 'issues': list(trace['issues'])}

    b = totals[target-2]
    if not _positive(b):
        return result(reason='PRIOR_CYCLE_TOTAL_MISSING_NONPOSITIVE_OR_NONFINITE')
    trace['B'] = float(b)
    try:
        if product == 'ORIGIN':
            if annual is None or any(v is None for v in annual.values()):
                return result(reason='ANNUAL_PRIOR_CYCLE_PIECES_MISSING')
            if any(not _finite(v) or v < 0 for v in annual.values()):
                return result(reason='ANNUAL_SECTOR_PIECES_MUST_BE_NONNEGATIVE_FINITE')
            if sum(annual.values()) != b:
                raise ValueError('annual prior-cycle pieces must sum exactly to B')
            if growth is None or any(not _finite(v) for v in growth.values()):
                return result(reason='ALL_THREE_FINITE_GROWTH_LOG_FACTORS_REQUIRED')
            units = [math.exp(growth[y]) for y in (target-3, target-2, target-1)]
            odd = math.exp(math.fsum((growth[target-3], growth[target-2])))
            even = math.exp(math.fsum((growth[target-2], growth[target-1])))
            if not all(_positive(v) for v in units + [odd, even]):
                return result(reason='GROWTH_FACTOR_NUMERIC_FAILURE')
            grown_odd, grown_even = annual[target-3]*odd, annual[target-2]*even
            u = math.fsum((grown_odd, grown_even))
            point = 0.5*b + 0.5*u
            if not all(_positive(v) for v in (u, point)):
                return result(reason='RESULT_NUMERIC_FAILURE')
            trace.update(U=u, annual=annual, log_growth=growth,
                         growth_factors={str(target-1): odd, str(target): even},
                         grown_annual_cents={str(target-1): grown_odd, str(target): grown_even},
                         remaining_cents=point)
            return result(float(point))
        if not _positive(observed):
            return result(reason='CURRENT_OBSERVATION_MISSING_NONPOSITIVE_OR_NONFINITE')
        shares, logs = [], []
        for cycle in window:
            total, partial = totals[cycle], history[cycle]
            if not _positive(total) or not _positive(partial):
                return result(reason='ALL_THREE_HISTORY_TOTALS_AND_OBSERVATIONS_MUST_BE_POSITIVE_FINITE')
            share = Fraction(partial, total)
            log_share = math.log(partial) - math.log(total)
            trace['history'].append({'cycle': cycle, 'total_cents': total,
                                     'observed_cents': partial, 'exact_share': str(share),
                                     'log_share': log_share})
            shares.append(share)
            logs.append(log_share)
        mean_log = math.fsum(logs) / 3
        product_share = shares[0]*shares[1]*shares[2]
        if product_share >= 1:
            s = 1.0
        elif shares[0] == shares[1] == shares[2]:
            s = float(shares[0])
        else:
            s = math.exp(mean_log)
        if not _finite(s) or not 0 < s <= 1:
            return result(reason='COMPLETION_SHARE_NUMERIC_FAILURE')
        remaining = (1.0-s)*b
        point = observed + remaining
        if not all(_finite(v) for v in (remaining, point)):
            return result(reason='RESULT_NUMERIC_FAILURE')
        trace.update(completion_share=s, mean_log_share=mean_log,
                     share_capped_at_one=product_share > 1, remaining_cents=remaining)
        return result(float(point))
    except (OverflowError, ZeroDivisionError):
        return result(reason='RESULT_NUMERIC_FAILURE')


def _object(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError('duplicate JSON key')
        out[key] = value
    return out


def _json(data):
    def reject(value):
        raise ValueError('nonfinite JSON value: ' + value)
    return json.loads(data.decode('utf-8'), object_pairs_hook=_object, parse_constant=reject)


def _timestamp(value):
    if not isinstance(value, str):
        raise ValueError('built_at must be an ISO timestamp with timezone')
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as error:
        raise ValueError('built_at must be an ISO timestamp with timezone') from error
    if stamp.tzinfo is None or stamp > datetime.now(timezone.utc):
        raise ValueError('future or timezone-free build timestamp')
    return stamp


def _load_bundle():
    pointer_path = Path(os.environ.get('MENTAT_PAC_GIVING_POINTER') or
                        app_data_root() / 'pac-giving-current.json')
    pointer_path = pointer_path.resolve(strict=True)
    pointer = _json(pointer_path.read_bytes())
    if (not isinstance(pointer, dict) or type(pointer.get('schema_version')) is not int or
            pointer['schema_version'] != 1 or pointer.get('model_id') != MODEL_ID):
        raise ValueError('unsupported PAC pointer schema or model identity')
    sha = pointer.get('sha256')
    active = pointer.get('active')
    if (not isinstance(sha, str) or not re.fullmatch(r'[0-9a-f]{64}', sha) or
            active != 'pac-versions/' + sha + '/pac-giving-v1.json'):
        raise ValueError('invalid content-addressed PAC pointer')
    candidate = pointer_path.parent / active
    resolved = candidate.resolve(strict=True)
    if resolved != candidate:
        raise ValueError('PAC bundle paths must not redirect')
    raw = resolved.read_bytes()
    if hashlib.sha256(raw).hexdigest() != sha:
        raise ValueError('PAC bundle hash mismatch')
    bundle = _json(raw)
    if (not isinstance(bundle, dict) or type(bundle.get('schema_version')) is not int or
            bundle['schema_version'] != 1 or bundle.get('model_id') != MODEL_ID or
            bundle.get('currency') != 'USD' or bundle.get('accounting_basis') != ACCOUNTING_BASIS):
        raise ValueError('unsupported PAC bundle contract')
    _timestamp(bundle.get('built_at'))
    return bundle, sha


def catalog():
    """Read the atomic pointer afresh; return all five dedicated PAC forecasts."""
    bundle, sha = _load_bundle()
    sectors = bundle.get('sectors')
    if (not isinstance(sectors, list) or len(sectors) != len(COHORTS) or
            any(not isinstance(row, dict) for row in sectors) or
            {row.get('sector') for row in sectors} != set(COHORTS)):
        raise ValueError('bundle must contain exactly the five selected PAC cohorts')
    output = []
    for item in sectors:
        sector = item['sector']
        _cohort(item.get('cohort'), sector)
        frames = item.get('frames')
        if not isinstance(frames, list) or not frames:
            raise ValueError('each sector requires an explicit current frame')
        forecasts, seen = [], set()
        for frame in frames:
            if (not isinstance(frame, dict) or frame.get('sector') != sector or
                    frame.get('cohort') != item['cohort']):
                raise ValueError('frame sector/cohort identity mismatch')
            identity = (frame.get('target'), frame.get('product'), frame.get('issue'))
            if identity in seen:
                raise ValueError('duplicate forecast identity')
            seen.add(identity)
            calculated = calculate(frame)
            forecasts.append(dict(calculated,
                                  calculation_status=calculated['status'],
                                  status='ready' if calculated['status'] == 'AVAILABLE' else 'unavailable',
                                  target=frame['target'], product=frame['product'],
                                  issue=frame['issue'], information_as_of=frame['information_as_of'],
                                  data_available_at=frame['data_available_at'], cut=frame['cut'],
                                  observed_cents=frame['observed'],
                                  remaining_cents=calculated['trace']['remaining_cents']))
        forecasts.sort(key=lambda x: (x['issue'], x['target'], PRODUCTS.index(x['product'])))
        output.append({'sector': sector, 'label': LABELS[sector],
                       'cohort': dict(item['cohort'], n_ids=item['cohort']['count']),
                       'forecasts': forecasts, 'current': forecasts[-1],
                       'origin_reference': item.get('origin_reference'),
                       'pending_origin': item.get('pending_origin')})
    output.sort(key=lambda x: tuple(COHORTS).index(x['sector']))
    return {'schema_version': 1, 'model_id': MODEL_ID, 'built_at': bundle['built_at'],
            'bundle_sha256': sha, 'currency': 'USD', 'accounting_basis': ACCOUNTING_BASIS,
            'owner_selected': True, 'prospectively_certified': False, 'sectors': output}

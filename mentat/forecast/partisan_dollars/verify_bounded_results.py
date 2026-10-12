"""Independent arithmetic audit of frozen interval feature frames and results.

No evaluator imports. This verifies saved feature-to-forecast arithmetic and
metrics, not the underlying official candidate-source classifications.
"""
import argparse
import hashlib
import itertools
import json
import math
from pathlib import Path


SECTORS = ('commercial_banks', 'credit_unions', 'property_casualty', 'credit_arms', 'telecom')
CHAMBERS = ('House', 'Senate')
PARTIES = ('D', 'R')
YEARS = (2018, 2020, 2022, 2024)
HORIZONS = ('m12', 'm15', 'm18', 'm21')
FAMILIES = ('prior_party', 'party_R12_remaining', 'historical_remainder')


def pair(v):
    return v['lo_cents'], v['hi_cents']


def midpoint_order_statistic(values):
    return sorted(values)[1]


def completion(numerators, denominators):
    # Product/cube-root is deliberately independent of the evaluator's log mean.
    return min(1.0, math.prod(a / b for a, b in zip(numerators, denominators)) ** (1 / 3))


def prediction(frame, family, party):
    f = [pair(h['final'][party]) for h in frame['history']]
    o = [pair(h['observed'][party]) for h in frame['history']]
    b = f[-1]
    now = pair(frame['observed'][party])
    fallback = False
    if family == 'prior_party':
        return b, fallback
    if family == 'party_R12_remaining':
        if b[0] < 0 or now[0] < 0 or any(v[0] <= 0 for v in f) or any(v[0] < 0 for v in o):
            return b, True
        low_s = completion([v[0] for v in o], [v[1] for v in f])
        high_s = completion([v[1] for v in o], [v[0] for v in f])
        return (now[0] + (1 - high_s) * b[0], now[1] + (1 - low_s) * b[1]), False
    rest = [pair(h['remaining'][party]) for h in frame['history']]
    p = tuple(now[i] + midpoint_order_statistic([r[i] for r in rest]) for i in (0, 1))
    return (b, True) if p[0] < 0 else (p, False)


def verify(result):
    comparisons, max_difference = 0, 0.0

    def check(a, b, label):
        nonlocal comparisons, max_difference
        comparisons += 1
        if isinstance(a, bool) or isinstance(b, bool) or a is None or b is None:
            if a != b:
                raise AssertionError(f'{label}: {a!r} != {b!r}')
        elif isinstance(a, (int, float)) and isinstance(b, (int, float)):
            max_difference = max(max_difference, abs(a - b))
            if not math.isclose(a, b, rel_tol=1e-11, abs_tol=1e-5):
                raise AssertionError(f'{label}: {a!r} != {b!r}')
        elif a != b:
            raise AssertionError(f'{label}: {a!r} != {b!r}')

    frames = {(r['sector'], r['chamber'], r['target'], r['horizon']): r for r in result['feature_frames']}
    truth = {(r['sector'], r['chamber'], r['target'], r['party']): pair(r) for r in result['truth_intervals']}
    check(set(frames), set(itertools.product(SECTORS, CHAMBERS, YEARS, HORIZONS)), 'feature grid')
    check(len(result['feature_frames']), 160, 'frame count')
    check(set(truth), set(itertools.product(SECTORS, CHAMBERS, YEARS, PARTIES)), 'truth grid')
    rows = result['predictions']
    keys = {(r['sector'], r['chamber'], r['target'], r['horizon'], r['family'], r['party']) for r in rows}
    check(keys, set(itertools.product(SECTORS, CHAMBERS, YEARS, HORIZONS, FAMILIES, PARTIES)), 'prediction grid')
    check(len(rows), 960, 'prediction count')
    groups = {}
    largest_prediction_gap = 0.0
    for row in rows:
        s, c, y, h, p, family = (row[k] for k in ('sector', 'chamber', 'target', 'horizon', 'party', 'family'))
        frame = frames[s, c, y, h]
        check([v['cycle'] for v in frame['history']], [y - 6, y - 4, y - 2], 'past-only cycles')
        (plo, phi), fallback = prediction(frame, family, p)
        ylo, yhi = truth[s, c, y, p]
        for expected, field in ((plo, 'prediction_lo_cents'), (phi, 'prediction_hi_cents'),
                                (phi - plo, 'prediction_width_cents'), (ylo, 'truth_lo_cents'),
                                (yhi, 'truth_hi_cents'), (yhi - ylo, 'truth_width_cents')):
            check(expected, row[field], field)
        largest_prediction_gap = max(largest_prediction_gap, abs(plo - row['prediction_lo_cents']), abs(phi - row['prediction_hi_cents']))
        check(fallback, row['uncertainty_rule_fallback'] is not None, 'fallback')
        check(ylo > 0, row['outcome_positive_over_full_interval'], 'positive truth')
        errors = [abs(a - b) for a, b in itertools.product((plo, phi), (ylo, yhi))]
        check(max(errors), row['absolute_error_upper_cents'], 'dollar error upper bound')
        ape = max(abs(a - b) / b for a, b in itertools.product((plo, phi), (ylo, yhi))) if ylo > 0 else None
        check(ape, row['ape_upper_bound'], 'APE upper bound')
        for band in (10, 20):
            # Independently certify via all four corner ratios rather than the
            # evaluator's two rearranged inequalities.
            certified = ylo > 0 and all((100 - band) / 100 <= a / b <= (100 + band) / 100
                                       for a, b in itertools.product((plo, phi), (ylo, yhi)))
            check(certified, row[f'certified_within{band}'], f'{band}% certification')
        for horizon, sector, chamber, party, target in itertools.product((h, 'ALL'), (s, 'ALL'), (c, 'ALL'), (p, 'ALL'), (str(y), 'ALL')):
            groups.setdefault((horizon, family, sector, chamber, party, target), []).append(row)
    metric_keys = set()
    for metric in result['metrics']:
        key = tuple(metric[k] for k in ('horizon', 'family', 'sector', 'chamber', 'party', 'target'))
        if key in metric_keys:
            raise AssertionError('Duplicate metric group')
        metric_keys.add(key)
        group = groups[key]
        n = len(group)
        positive = [r for r in group if r['truth_lo_cents'] > 0]
        expected = {'n': n, 'positive_truth_interval_n': len(positive), 'nonpositive_truth_lower_n': n - len(positive),
                    'uncertainty_rule_fallback_count': sum(r['uncertainty_rule_fallback'] is not None for r in group),
                    'exact_prediction_interval_count': sum(r['prediction_lo_cents'] == r['prediction_hi_cents'] for r in group),
                    'exact_truth_interval_count': sum(r['truth_lo_cents'] == r['truth_hi_cents'] for r in group),
                    'mean_prediction_width_dollars': sum(r['prediction_width_cents'] for r in group) / n / 100,
                    'max_prediction_width_dollars': max(r['prediction_width_cents'] for r in group) / 100,
                    'mean_truth_width_dollars': sum(r['truth_width_cents'] for r in group) / n / 100,
                    'maximum_ape_upper_bound': max((r['ape_upper_bound'] for r in positive), default=None),
                    'mean_absolute_error_upper_dollars': sum(r['absolute_error_upper_cents'] for r in group) / n / 100}
        for band in (10, 20):
            hits = sum(r[f'certified_within{band}'] for r in group)
            expected.update({f'certified_within{band}_count': hits, f'certified_within{band}_fraction_all_cells': hits / n,
                             f'not_certified_within{band}_count': n - hits})
        for field, value in expected.items():
            check(value, metric[field], field)
    check(metric_keys, set(groups), 'complete metric grid')
    check(result['live_activation'], False, 'offline only')
    check(result['midpoint'], False, 'no fabricated point truth')
    return {'status': 'PASS', 'comparisons': comparisons, 'prediction_rows': len(rows), 'metric_groups': len(groups),
            'feature_frames': len(frames), 'maximum_prediction_difference_dollars': largest_prediction_gap / 100,
            'scope': 'Independent arithmetic from saved feature intervals; source classifications require separate data review.',
            'independence': 'No evaluator imports. Product/cube-root completion, sorted order-statistic remainder, and corner-ratio certification.',
            'live_activation': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evaluation', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Output exists; refusing overwrite')
    result = json.loads(args.evaluation.read_text(encoding='utf-8'))
    for filename, expected in result['input_sha256'].items():
        if hashlib.sha256(Path(filename).read_bytes()).hexdigest() != expected:
            raise AssertionError('Input hash mismatch: ' + filename)
    receipt = verify(result)
    receipt['evaluation_sha256'] = hashlib.sha256(args.evaluation.read_bytes()).hexdigest()
    receipt['verifier_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(receipt, stream, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps(receipt, indent=2))


if __name__ == '__main__':
    main()

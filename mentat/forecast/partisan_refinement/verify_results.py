"""Independent stdlib arithmetic audit of the offline share experiment.

Does not import the evaluator or production predictor, fit the incumbent, choose
a family, alter a model, or open the separate 2024 confirmation input.
"""
import argparse
import csv
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path
from statistics import mean


def verify(evaluation, bundle, frozen_rows):
    checks = []

    def check(name, passed, **details):
        checks.append(dict(check=name, passed=bool(passed), **details))

    histories = {c: bundle['share_models']['control_' + c]['history'] for c in ('House', 'Senate')}
    groups = defaultdict(list)
    max_error = 0.0
    for row in evaluation['predictions']:
        if row['stage'] not in ('outer_candidate', 'outer_policy'):
            raise ValueError('primary prediction file must contain chronological rows only')
        c, t, s = row['chamber'], row['target'], row['industry']
        history = {(r['industry'], r['cycle']): r for r in histories[c]}
        check('strictly earlier training', all(y < t for y in row['training_years']), chamber=c, target=t, industry=s)
        truth = history[s, t]['share']
        error = 100 * (row['prediction'] - truth)
        max_error = max(max_error, abs(error - row['error_pp']), abs(abs(error) - row['absolute_error_pp']))
        for industry in (s, '__ALL__'):
            groups[row['stage'], c, row['family'], industry].append(error)
        family = row.get('selected_family', row['family']) if row['family'] == 'nested_policy' else row['family']
        if family == 'incumbent_R0':
            continue
        previous = history[s, t - 2]
        expected = previous['share']
        if family == 'persistence':
            pass
        elif family == 'same_phase':
            expected = history[s, t - 4]['share']
        elif family == 'equal_blend':
            expected = (previous['share'] + history[s, t - 4]['share']) / 2
        elif family.startswith('ridge_control_'):
            pairs = [(r, history[r['industry'], r['cycle'] - 2]) for r in histories[c]
                     if r['cycle'] < t and (r['industry'], r['cycle'] - 2) in history]
            xs = [a['forecast'] - b['forecast'] for a, b in pairs]
            ys = [a['share'] - b['share'] for a, b in pairs]
            xx = sum(x * x for x in xs)
            xy = sum(x * y for x, y in zip(xs, ys))
            slope = 0.0 if xx == 0 else xy / (xx * (1 + int(family.rsplit('_', 1)[1])))
            expected += slope * (history[s, t]['forecast'] - previous['forecast'])
        else:
            raise ValueError('unknown candidate ' + family)
        check('independent candidate prediction', abs(row['raw_prediction'] - expected) < 1e-12
              and abs(row['prediction'] - min(1, max(0, expected))) < 1e-12,
              chamber=c, target=t, industry=s, family=family)
    check('saved error arithmetic', max_error < 1e-10, maximum_difference_pp=max_error)
    metric_index = {}
    for m in evaluation['metrics']:
        key = m['stage'], m['chamber'], m['family'], m['industry']
        errors = groups[key]
        mae, rmse = mean(abs(x) for x in errors), math.sqrt(mean(x * x for x in errors))
        check('independent metrics', abs(mae - m['mae_pp']) < 1e-10 and abs(rmse - m['rmse_pp']) < 1e-10
              and len(errors) == m['n'], group=list(key))
        metric_index[key] = mae
    parity = []
    for chamber in ('House', 'Senate'):
        for family, rule in (('incumbent_R0', 'R0'), ('persistence', 'naive_prev')):
            matches = [r for r in frozen_rows if r['term'] == 'control_' + chamber and r['base'] == 'selected'
                       and r['design'] == 'chrono' and r['path'] == 'prevchg' and r['rule'] == rule]
            if len(matches) != 1:
                raise ValueError('frozen comparator must be unique')
            reference = matches[0]
            for industry in ['__ALL__'] + sorted({r['industry'] for r in histories[chamber]}):
                actual = metric_index['outer_candidate', chamber, family, industry]
                expected = float(reference['MAE' if industry == '__ALL__' else 'MAE_' + industry])
                delta = abs(actual - expected)
                parity.append(dict(chamber=chamber, family=family, industry=industry,
                                   reproduced_mae_pp=actual, published_mae_pp=expected, difference_pp=delta))
                check('published chronological parity', delta <= 1e-8, **parity[-1])
    decisions = []
    for chamber in ('House', 'Senate'):
        get = lambda f, s='__ALL__', stage='outer_candidate': metric_index[stage, chamber, f, s]
        selected = get('nested_policy', stage='outer_policy')
        old, naive = get('incumbent_R0'), get('persistence')
        best_simple = min(get(f) for f in ('persistence', 'same_phase', 'equal_blend'))
        inds = sorted({r['industry'] for r in histories[chamber]})
        regressions = {s: get('nested_policy', s, 'outer_policy') - get('incumbent_R0', s) for s in inds}
        selected_rows = [r for r in evaluation['predictions'] if r['stage'] == 'outer_policy' and r['chamber'] == chamber]
        raw_bounds = sum(r['chamber'] == chamber for r in evaluation['selected_policy_promotion_blockers'])
        complete = {(r['industry'], r['target']) for r in selected_rows} == {(s, y) for s in inds for y in (2018, 2020, 2022)}
        eligible = (all(c['passed'] for c in checks) and complete and old - selected >= 0.25 and selected <= old * 0.95 and selected <= naive + 1e-12
                    and max(regressions.values()) <= 0.5 and raw_bounds == 0 and evaluation['failure_count'] == 0)
        decisions.append(dict(chamber=chamber, nested_mae_pp=selected, incumbent_mae_pp=old, persistence_mae_pp=naive,
                              best_fixed_baseline_mae_pp=best_simple, improvement_pp=old-selected,
                              improvement_percent=100*(old-selected)/old, industry_regression_pp=regressions,
                              complete=complete, raw_bounds_violations=raw_bounds, development_staging_eligible=eligible,
                              g6_diagnostic_failures=[r for r in evaluation['loeo_diagnostic_failures'] if r['chamber'] == chamber],
                              live_publication_authorized=False))
    return dict(passed=all(c['passed'] for c in checks), checks=checks, parity=parity, decisions=decisions,
                independent_implementation='stdlib; no evaluator import; ridge verified as sum(x*y)/((1+penalty)*sum(x*x))')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evaluation', type=Path, required=True)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--published-backtests', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError('verification output exists')
    result = verify(json.loads(args.evaluation.read_text()), json.loads(args.bundle.read_text(encoding='utf-8-sig')),
                    list(csv.DictReader(args.published_backtests.open(encoding='utf-8-sig', newline=''))))
    result['inputs'] = {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                       for p in (args.evaluation, args.bundle, args.published_backtests, Path(__file__))}
    with args.output.open('x', encoding='utf-8') as handle:
        json.dump(result, handle, indent=2, allow_nan=False)
        handle.write('\n')
    print(json.dumps(dict(passed=result['passed'], checks=len(result['checks']), decisions=result['decisions']), indent=2))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())

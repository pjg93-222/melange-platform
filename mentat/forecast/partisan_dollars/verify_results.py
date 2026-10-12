"""Independent, stdlib-only audit of frozen whole-industry dollar run outputs.

Does not import the evaluator. Growth is recomputed from ratios and roots rather
than log differences; all target values come directly from the original CSV.
Writes a new verification receipt, never edits a result or chooses a new model.
"""
import argparse
from collections import defaultdict
import csv
from datetime import datetime, timezone
import hashlib
import itertools
import json
import math
from pathlib import Path

SECTORS = ('auto_light_duty_manufacturing', 'commercial_banks', 'credit_unions', 'property_casualty')
CHAMBERS = ('House', 'Senate')
PARTIES = ('D', 'R')
YEARS = tuple(range(2012, 2025, 2))
ORIGINS = (2018, 2020, 2022, 2024)
INNER = (2016, 2018, 2020, 2022, 2024)
METHODS = ('prior_party', 'same_phase_party', 'equal_blend_party', 'direct_party_half_growth',
           'joint_total_half_growth_prior_share', 'joint_total_half_growth_blended_share')
CODES = dict(zip(SECTORS, ('T2100', 'F03', 'F05', 'F09')))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require(condition, label):
    if not condition:
        raise AssertionError(label)


class Checks:
    def __init__(self):
        self.comparisons = 0
        self.max_prediction_difference_usd = 0.0

    def equal(self, actual, expected, label):
        self.comparisons += 1
        if isinstance(expected, bool) or expected is None:
            require(actual is expected, label)
        elif isinstance(expected, (float, int)):
            require(isinstance(actual, (int, float)) and not isinstance(actual, bool), label)
            require(math.isfinite(actual), label + ' nonfinite')
            # Sub-microdollar absolute tolerance; tighter relative comparison.
            tolerance = 1e-6 if label.endswith('_usd') else 1e-12
            require(math.isclose(actual, expected, rel_tol=1e-12, abs_tol=tolerance),
                    f'{label}: saved={actual!r}, independent={expected!r}')
        else:
            require(actual == expected, label)


def input_panel(path):
    with Path(path).open(encoding='utf-8-sig', newline='') as handle:
        raw = list(csv.DictReader(handle))
    lookup = {}
    for row in raw:
        key = row['chamber'], row['industry'], int(row['cycle']), row['party']
        require(key not in lookup, 'duplicate source key')
        require(row['opensecrets_code'] == CODES[row['industry']], 'industry code mismatch')
        amount = float(row['actual_nominal_usd'])
        require(math.isfinite(amount) and amount >= 0, 'invalid original dollar outcome')
        lookup[key] = amount
    require(set(lookup) == set(itertools.product(CHAMBERS, SECTORS, YEARS, PARTIES)), '112 source cells')
    for row in raw:
        c, s, y = row['chamber'], row['industry'], int(row['cycle'])
        require(math.isclose(lookup[c, s, y, 'D'] + lookup[c, s, y, 'R'],
                             float(row['two_party_nominal_usd']), rel_tol=1e-12, abs_tol=1e-6),
                'source D+R conservation')
    return lookup


def growth(series):
    ends = [y for y in sorted(series) if y - 2 in series][-3:]
    require(bool(ends), 'missing growth transitions')
    pairs = [(series[y - 2], series[y]) for y in ends]
    require(all(a > 0 and b > 0 for a, b in pairs), 'growth nonpositive')
    ratios = sorted(b / a for a, b in pairs)
    if len(ratios) % 2:
        factor = math.sqrt(ratios[len(ratios) // 2])
    else:
        factor = math.sqrt(math.sqrt(ratios[0]) * math.sqrt(ratios[1]))
    require(math.isfinite(factor) and factor > 0, 'invalid growth factor')
    return factor


def forecast(panel, chamber, sector, target, method):
    # This independent function exposes only earlier values to the formulas.
    history = {(y, p): value for (c, s, y, p), value in panel.items()
               if c == chamber and s == sector and y < target}
    prior = [history[target - 2, p] for p in PARTIES]
    if method == 'prior_party':
        pred = prior
    elif method == 'same_phase_party':
        pred = [history[target - 4, p] for p in PARTIES]
    elif method == 'equal_blend_party':
        pred = [(prior[i] + history[target - 4, p]) / 2 for i, p in enumerate(PARTIES)]
    elif method == 'direct_party_half_growth':
        pred = [prior[i] * growth({y: v for (y, p), v in history.items() if p == party})
                for i, party in enumerate(PARTIES)]
    else:
        require(method in METHODS[-2:], 'unknown method')
        totals = {y: history[y, 'D'] + history[y, 'R'] for y in YEARS if y < target}
        r_fraction = prior[1] / sum(prior)
        if method == METHODS[-1]:
            r_fraction = (r_fraction + history[target - 4, 'R'] / totals[target - 4]) / 2
        projected = sum(prior) * growth(totals)
        pred = [projected * (1 - r_fraction), projected * r_fraction]
    require(all(math.isfinite(v) and v >= 0 for v in pred), 'invalid independent forecast')
    return dict(zip(PARTIES, pred))


def cell(panel, chamber, sector, party, target, method):
    pred = forecast(panel, chamber, sector, target, method)
    actual = panel[chamber, sector, target, party]
    value = pred[party]
    error = value - actual
    ape = abs(error) / actual if actual else None
    return dict(chamber=chamber, industry=sector, party=party, target=target,
                prediction_nominal_usd=value, actual_nominal_usd=actual,
                projected_two_party_nominal_usd=sum(pred.values()),
                error_nominal_usd=error, absolute_error_nominal_usd=abs(error), ape=ape,
                within_10pct=None if ape is None else ape <= .1 + 1e-12,
                within_20pct=None if ape is None else ape <= .2 + 1e-12,
                zero_actual=actual == 0, exact_zero_correct=(value == 0) if actual == 0 else None,
                zero_actual_false_positive_usd=value if actual == 0 else 0.0)


def statistics_for(rows):
    errors = [r['prediction_nominal_usd'] - r['actual_nominal_usd'] for r in rows]
    absolute = [abs(e) for e in errors]
    actuals = [r['actual_nominal_usd'] for r in rows]
    positive = [(r, abs(e) / a) for r, e, a in zip(rows, errors, actuals) if a > 0]
    total = math.fsum(actuals)
    n = len(rows)
    hits = [sum(ape <= threshold + 1e-12 for _, ape in positive) for threshold in (.1, .2)]
    zero = [r for r in rows if r['actual_nominal_usd'] == 0]
    return dict(n=n, mae_nominal_usd=math.fsum(absolute) / n,
                rmse_nominal_usd=math.sqrt(math.fsum(e * e for e in errors) / n),
                total_actual_nominal_usd=total,
                total_predicted_nominal_usd=math.fsum(r['prediction_nominal_usd'] for r in rows),
                wape=math.fsum(absolute) / total if total else None,
                signed_bias=math.fsum(errors) / total if total else None,
                mean_error_nominal_usd=math.fsum(errors) / n,
                mape_positive_actual=math.fsum(a for _, a in positive) / len(positive) if positive else None,
                max_ape_positive_actual=max((a for _, a in positive), default=None),
                max_absolute_error_nominal_usd=max(absolute), positive_actual_count=len(positive),
                within_10pct_count=hits[0], within_20pct_count=hits[1],
                within_10pct_rate=hits[0] / len(positive) if positive else None,
                within_20pct_rate=hits[1] / len(positive) if positive else None,
                zero_actual_count=len(zero), zero_actual_exact_count=sum(r['prediction_nominal_usd'] == 0 for r in zero),
                zero_actual_false_positive_count=sum(r['prediction_nominal_usd'] > 0 for r in zero),
                zero_actual_false_positive_usd=math.fsum(r['prediction_nominal_usd'] for r in zero))


def macro(rows):
    strata = {s + ':' + p: statistics_for([r for r in rows if r['industry'] == s and r['party'] == p])['wape']
              for s in SECTORS for p in PARTIES}
    require(all(v is not None for v in strata.values()), 'undefined macro stratum')
    return math.fsum(strata.values()) / 8, strata


def independent_selection(panel, chamber, before):
    origins = [y for y in INNER if y < before]
    rows, scores, strata = {}, {}, {}
    for method in METHODS:
        rows[method] = [cell(panel, chamber, s, p, y, method)
                        for y in origins for s in SECTORS for p in PARTIES]
        scores[method], strata[method] = macro(rows[method])
    enough = len(origins) >= 2
    choice = next(m for m in METHODS if scores[m] <= min(scores.values()) + 1e-12) if enough else METHODS[0]
    return origins, choice, scores, strata, rows


def audit_cell(checks, saved, expected, method):
    for name, value in expected.items():
        checks.equal(saved[name], value, name)
    checks.max_prediction_difference_usd = max(checks.max_prediction_difference_usd,
        abs(saved['prediction_nominal_usd'] - expected['prediction_nominal_usd']))
    checks.equal(saved['training_years'], [y for y in YEARS if y < saved['target']], 'training_years')
    checks.equal(saved['unit'], 'nominal_usd', 'unit')
    checks.equal(saved['clipped'], False, 'clipped')
    for transition in saved['trace'].get('transitions', []):
        require(transition['start_cycle'] < transition['end_cycle'] < saved['target'], 'future trace')
    require(method in METHODS, 'unknown saved family')


def audit_csv(path, json_rows):
    with path.open(encoding='utf-8-sig', newline='') as handle:
        saved = list(csv.DictReader(handle))
    require(len(saved) == len(json_rows), path.name + ' row count')
    for flat, original in zip(saved, json_rows):
        require(set(flat) == set(original), path.name + ' schema')
        for key, value in original.items():
            text = '' if value is None else json.dumps(value, sort_keys=True, allow_nan=False) if isinstance(value, (dict, list)) else str(value)
            require(flat[key] == text, path.name + ' field ' + key)


def audit(input_path, run, protocol_path, evaluator_path):
    panel = input_panel(input_path)
    result = json.loads((run / 'evaluation.json').read_text(encoding='utf-8'))
    policy = json.loads((run / 'policy_manifest.json').read_text(encoding='utf-8'))
    checks = Checks()
    require(digest(run / 'policy_manifest.json') == (run / 'policy_manifest.sha256').read_text().strip(), 'manifest digest')
    for obj in (result, policy):
        require(obj['input_sha256'] == digest(input_path), 'source hash')
        require(obj['evaluator_sha256'] == digest(evaluator_path), 'evaluator hash')
        require(obj['protocol_file_sha256'] == digest(protocol_path), 'external protocol hash')
        require(obj['live_activation'] is False and obj['untouched_test_claimed'] is False, 'unsupported activation/test claim')
    require(result['outer_failure_count'] == result['inner_failure_count'] == 0 and result['failures'] == [], 'failure-free audit population')
    require(result['protocol'] == policy['protocol'], 'protocol parity')
    require(policy['protocol_sha256'] == hashlib.sha256((json.dumps(policy['protocol'], sort_keys=True, indent=2, allow_nan=False) + '\n').encode()).hexdigest(), 'protocol content hash')
    inner_count, choices = 0, {}
    for chamber in CHAMBERS:
        for before in ORIGINS + (2026,):
            origins, choice, scores, strata, independent = independent_selection(panel, chamber, before)
            choices[chamber, before] = choice
            saved = result['final_2026_selection'][chamber] if before == 2026 else result['nested_selection'][chamber][str(before)]
            for key, val in dict(inner_origins=origins, selected_family=choice, selection_before=before,
                                 fallback=len(origins) < 2, selection_eligible=len(origins) >= 2,
                                 ineligible_families=[], failures=[]).items():
                checks.equal(saved[key], val, key)
            for method in METHODS:
                checks.equal(saved['macro_wape_by_family'][method], scores[method], 'inner macro WAPE')
                for key in strata[method]:
                    checks.equal(saved['stratum_wape_by_family'][method][key], strata[method][key], 'inner stratum WAPE')
            expected_map = {(m, r['industry'], r['party'], r['target']): r for m, values in independent.items() for r in values}
            require(len(saved['predictions']) == len(expected_map), 'inner cell count')
            seen = set()
            for row in saved['predictions']:
                key = row['family'], row['industry'], row['party'], row['target']
                require(key not in seen, 'duplicate inner prediction')
                seen.add(key)
                audit_cell(checks, row, expected_map[key], row['family'])
                checks.equal(row['selected_family'], choice, 'inner selected family')
                checks.equal(row['stage'], 'inner_candidate', 'inner stage')
            require(seen == set(expected_map), 'inner full grid')
            inner_count += len(seen)
            if before == 2026:
                require(policy['selections'][chamber] == {k: v for k, v in saved.items() if k != 'predictions'}, 'manifest selected policy')
    expected_outer = {(c, y, s, p, 'outer_candidate', m) for c in CHAMBERS for y in ORIGINS for s in SECTORS for p in PARTIES for m in METHODS}
    expected_outer |= {(c, y, s, p, 'outer_policy', 'nested_policy') for c in CHAMBERS for y in ORIGINS for s in SECTORS for p in PARTIES}
    outer, seen = [], set()
    for row in result['predictions']:
        key = row['chamber'], row['target'], row['industry'], row['party'], row['stage'], row['family']
        require(key in expected_outer and key not in seen, 'outer identity/population')
        seen.add(key)
        choice = choices[row['chamber'], row['target']]
        checks.equal(row['selected_family'], choice, 'outer selected family')
        method = choice if row['stage'] == 'outer_policy' else row['family']
        expected = cell(panel, row['chamber'], row['industry'], row['party'], row['target'], method)
        audit_cell(checks, row, expected, method)
        outer.append(dict(expected, stage=row['stage'], family=row['family']))
    require(seen == expected_outer, '448 complete outer cells')
    groups = defaultdict(list)
    for row in outer:
        for s, p, y in itertools.product((row['industry'], '__ALL__'), (row['party'], '__ALL__'), (str(row['target']), '__ALL__')):
            groups[row['stage'], row['chamber'], row['family'], s, p, y].append(row)
    fields = ('stage', 'chamber', 'family', 'industry', 'party', 'target')
    require(len(result['metrics']) == len(groups), 'metric group count')
    metric_seen = set()
    for row in result['metrics']:
        key = tuple(row[f] for f in fields)
        require(key not in metric_seen, 'duplicate metric group')
        metric_seen.add(key)
        for field, value in statistics_for(groups[key]).items():
            checks.equal(row[field], value, field)
    require(metric_seen == set(groups), 'complete metric groups')
    summary = []
    require(len(result['macro_metrics']) == 14, '14 macro summaries')
    macro_seen = set()
    for row in result['macro_metrics']:
        key = row['chamber'], row['stage'], row['family']
        require(key not in macro_seen, 'duplicate macro summary')
        macro_seen.add(key)
        subset = [r for r in outer if (r['chamber'], r['stage'], r['family']) == key]
        require(len(subset) == 32, 'macro full population')
        val, strata = macro(subset)
        checks.equal(row['macro_wape'], val, 'outer macro WAPE')
        checks.equal(row['complete'], True, 'macro complete')
        for stratum in strata:
            checks.equal(row['stratum_wape'][stratum], strata[stratum], 'outer stratum WAPE')
        summary.append(dict(chamber=key[0], stage=key[1], family=key[2], macro_wape=val, **statistics_for(subset)))
    for filename, key in (('predictions.csv', 'predictions'), ('metrics.csv', 'metrics'), ('macro_metrics.csv', 'macro_metrics')):
        audit_csv(run / filename, result[key])
    return dict(status='PASS', verified_at_utc=datetime.now(timezone.utc).isoformat(),
                independence='No evaluator import. Dollar formulas independently recomputed from raw CSV; growth uses ratio roots, not evaluator log differences.',
                input_sha256=digest(input_path), evaluator_sha256=digest(evaluator_path),
                protocol_file_sha256=digest(protocol_path), verifier_sha256=digest(__file__),
                result_artifact_sha256={p.name: digest(p) for p in sorted(run.iterdir()) if p.is_file()},
                input_cells=len(panel), outer_predictions_checked=len(outer), retained_inner_predictions_checked=inner_count,
                metric_groups_checked=len(groups), numeric_or_identity_comparisons=checks.comparisons,
                maximum_prediction_difference_nominal_usd=checks.max_prediction_difference_usd,
                nested_choices={c: {str(y): choices[c, y] for y in ORIGINS} for c in CHAMBERS},
                final_2026_families={c: choices[c, 2026] for c in CHAMBERS}, summary=summary,
                target_status='No accuracy or promotion claim; literal positive-actual hit rates retained. All cycles already examined; retrospective evaluation.',
                live_activation=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--run', required=True, type=Path)
    parser.add_argument('--protocol', required=True, type=Path)
    parser.add_argument('--evaluator', required=True, type=Path)
    parser.add_argument('--receipt-name', default='independent_verification.json')
    args = parser.parse_args()
    require(Path(args.receipt_name).name == args.receipt_name, 'receipt filename must be local')
    receipt = args.run / args.receipt_name
    require(not receipt.exists(), 'refusing receipt overwrite')
    result = audit(args.input, args.run, args.protocol, args.evaluator)
    with receipt.open('x', encoding='utf-8') as handle:
        json.dump(result, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write('\n')
    print(json.dumps({k: v for k, v in result.items() if k not in ('summary', 'result_artifact_sha256')}, indent=2))


if __name__ == '__main__':
    main()

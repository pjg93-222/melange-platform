"""Standalone offline PAC-dollar interval predictor. No training, truth or selection.

Call predict(frame, family) with one explicit frozen family. Frames use signed
nominal cents, reviewed canonical cycle-office scope, and exactly three earlier
cycles. Input provenance remains the caller's responsibility; intervals are not
probabilities, confidence intervals or a guarantee about future outcomes.
"""
from __future__ import annotations
import argparse
from datetime import date, datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from statistics import median

SECTORS = ('commercial_banks', 'credit_unions', 'property_casualty', 'credit_arms', 'telecom')
CHAMBERS = ('House', 'Senate')
PARTIES = ('D', 'R')
HORIZONS = ('m12', 'm15', 'm18', 'm21')
FAMILIES = ('prior_party', 'party_R12_remaining', 'historical_remainder')
FRAME_KEYS = {'sector', 'chamber', 'target', 'horizon', 'issue', 'cut', 'history', 'observed'}
INTERVAL_KEYS = {'lo_cents', 'hi_cents', 'known_cents', 'known_rows', 'unknown_negative_cents',
    'unknown_positive_cents', 'unknown_net_cents', 'unknown_rows', 'unknown_pooled_over_both_chambers',
    'width_cents', 'source_issue_filter', 'source_transaction_cut', 'removed_observed_issue',
    'removed_observed_cut', 'direct_remaining_rows'}
LIMITATIONS = [
    'Offline research only; live activation and activation recommendation are false.',
    'All three methods remain explicit options; no independently validated winner is selected.',
    'Canonical cycle-office PAC cohort dollars differ from the legacy H/S population and whole-industry giving.',
    'Bounds cover declared classification uncertainty conditional on source labels and coverage; they do not bound future forecast error.',
    'Marginal party/chamber bounds cannot be summed as jointly attainable allocations.',
    'Latest amendments, later fixed cohorts/source snapshots and reconstructed DUE are not original-vintage availability.',
    'Historical outcomes were previously examined; arithmetic parity is not new accuracy evidence.',
    'Feature frames must be independently prepared with same-record F-minus-O remainder bounds and correct date filters.',
    'Supporting a future scheduled issue does not assert its inputs exist now; callers must supply genuinely issue-eligible data. No actual current 2026 forecast is included.',
    'Strict source admission remains HOLD; this callable package does not classify transactions or certify input provenance.',
]


class PredictionError(ValueError):
    pass


def canonical(value):
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n').encode('utf-8')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def schedule(target, horizon):
    if type(target) is not int or target % 2 or target < 2018 or horizon not in HORIZONS:
        raise PredictionError('target must be an even election year >=2018 and horizon must be supported')
    return cycle_schedule(target, horizon)


def cycle_schedule(year, horizon):
    if horizon == 'm12':
        pair = f'{year}-01-31', f'{year-1}-12-31'
    else:
        issue, cut = {'m15': ('04-20', '03-31'), 'm18': ('07-20', '06-30'), 'm21': ('10-20', '09-30')}[horizon]
        pair = f'{year}-{issue}', f'{year}-{cut}'
    try:
        if any(date.fromisoformat(v).isoformat() != v for v in pair):
            raise ValueError()
    except ValueError as error:
        raise PredictionError('canonical calendar dates required') from error
    return pair


def finite(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def endpoints(value, expected_metadata=None):
    if not isinstance(value, dict) or set(value) - INTERVAL_KEYS or not {'lo_cents', 'hi_cents'} <= set(value):
        raise PredictionError('closed interval schema required; no target truth fields')
    lo, hi = value['lo_cents'], value['hi_cents']
    if not finite(lo) or not finite(hi) or lo > hi:
        raise PredictionError('finite ordered interval required')
    for key, expected in (expected_metadata or {}).items():
        if key in value and value[key] != expected:
            raise PredictionError('feature availability metadata mismatch: ' + key)
    return lo, hi


def validate_frame(frame, family):
    if family not in FAMILIES or not isinstance(frame, dict) or set(frame) != FRAME_KEYS:
        raise PredictionError('explicit frozen family and closed feature-frame schema required')
    if frame['sector'] not in SECTORS or frame['chamber'] not in CHAMBERS:
        raise PredictionError('unsupported cohort or chamber')
    if (frame['issue'], frame['cut']) != schedule(frame['target'], frame['horizon']):
        raise PredictionError('issue and cut must match the declared horizon')
    years = [frame['target'] - 6, frame['target'] - 4, frame['target'] - 2]
    if not isinstance(frame['history'], list) or len(frame['history']) != 3:
        raise PredictionError('exactly three earlier cycles required')
    for expected_year, old in zip(years, frame['history']):
        if not isinstance(old, dict) or set(old) != {'cycle', 'final', 'observed', 'remaining'} or type(old['cycle']) is not int or old['cycle'] != expected_year:
            raise PredictionError('history must be t-6, t-4, t-2 with no truth or extra fields')
        old_issue, old_cut = cycle_schedule(expected_year, frame['horizon'])
        metadata = {
            'final': {'source_issue_filter': frame['issue'], 'source_transaction_cut': None},
            'observed': {'source_issue_filter': old_issue, 'source_transaction_cut': old_cut},
            'remaining': {'source_issue_filter': frame['issue'], 'removed_observed_issue': old_issue,
                          'removed_observed_cut': old_cut, 'direct_remaining_rows': True}}
        for key in ('final', 'observed', 'remaining'):
            if not isinstance(old[key], dict) or set(old[key]) != set(PARTIES):
                raise PredictionError('both historical party intervals required')
            for value in old[key].values():
                endpoints(value, metadata[key])
    if not isinstance(frame['observed'], dict) or set(frame['observed']) != set(PARTIES):
        raise PredictionError('both current party intervals required')
    for value in frame['observed'].values():
        endpoints(value, {'source_issue_filter': frame['issue'], 'source_transaction_cut': frame['cut']})
    return years


def completion(ratios):
    if any(not finite(v) or v < 0 for v in ratios):
        raise PredictionError('nonfinite completion arithmetic')
    if any(v == 0 for v in ratios):
        return 0.0
    return math.exp(min(0.0, math.fsum(math.log(v) for v in ratios) / 3))


def predict(frame, family):
    """Pure function: returns D/R marginal intervals, never mutates its inputs."""
    years = validate_frame(frame, family)
    results = []
    for party in PARTIES:
        finals = [endpoints(old['final'][party]) for old in frame['history']]
        observations = [endpoints(old['observed'][party]) for old in frame['history']]
        blo, bhi = finals[-1]
        olo, ohi = endpoints(frame['observed'][party])
        lo, hi, reason, trace = blo, bhi, None, {}
        if family == 'party_R12_remaining':
            if blo < 0 or olo < 0 or any(f[0] <= 0 for f in finals) or any(o[0] < 0 for o in observations):
                reason = 'input box does not certify the nonnegative R12 domain; use full prior interval'
            else:
                lower = [o[0] / f[1] for f, o in zip(finals, observations)]
                upper = [o[1] / f[0] for f, o in zip(finals, observations)]
                slo, shi = completion(lower), completion(upper)
                lo, hi = olo + (1 - shi) * blo, ohi + (1 - slo) * bhi
                trace = dict(completion_lo=slo, completion_hi=shi, historical_ratios_lo=lower, historical_ratios_hi=upper)
        elif family == 'historical_remainder':
            remaining = [endpoints(old['remaining'][party]) for old in frame['history']]
            rlo, rhi = median(v[0] for v in remaining), median(v[1] for v in remaining)
            candidate_lo, candidate_hi = olo + rlo, ohi + rhi
            trace = dict(remainder_lo_cents=rlo, remainder_hi_cents=rhi,
                         formula_lo_cents=candidate_lo, formula_hi_cents=candidate_hi)
            if candidate_lo < 0:
                reason = 'remainder box includes a negative prediction; use full prior interval'
            else:
                lo, hi = candidate_lo, candidate_hi
        endpoints({'lo_cents': lo, 'hi_cents': hi})
        results.append(dict(sector=frame['sector'], chamber=frame['chamber'], target=frame['target'],
            horizon=frame['horizon'], issue=frame['issue'], cut=frame['cut'], family=family, party=party,
            prediction_lo_cents=lo, prediction_hi_cents=hi, prediction_width_cents=hi-lo,
            prior_lo_cents=blo, prior_hi_cents=bhi, observed_lo_cents=olo, observed_hi_cents=ohi,
            training_years=years, uncertainty_rule_fallback=reason, trace=trace,
            conditional_target='canonical cycle-office research; conditional on reviewed source labels and coverage',
            chamber_basis='Source-resolved canonical office; unresolved party/office pooled across both chambers',
            strict_classification_admission='HOLD'))
    return results


def predict_request(request):
    if not isinstance(request, dict) or set(request) != {'family', 'frame'}:
        raise PredictionError('request requires only an explicit family and feature frame')
    return dict(schema_version=1, kind='offline_pac_dollar_interval_prediction',
                family=request['family'], unit='signed_nominal_cents', predictions=predict(request['frame'], request['family']),
                live_activation=False, activation_recommended=False, selection_performed=False,
                limitations=list(LIMITATIONS))


def verify_package(manifest_path, expected_hash):
    manifest_path = Path(manifest_path)
    raw = manifest_path.read_bytes()
    if sha(raw) != expected_hash:
        raise PredictionError('package manifest identity mismatch')
    manifest = json.loads(raw)
    if manifest.get('kind') != 'offline_pac_interval_predictor_package' or manifest.get('families') != list(FAMILIES) or any(manifest.get(k) is not False for k in ('live_activation', 'activation_recommended', 'selection_performed')):
        raise PredictionError('unsupported package contract')
    for name, expected in manifest['artifact_sha256'].items():
        if Path(name).name != name or sha((manifest_path.parent / name).read_bytes()) != expected:
            raise PredictionError('package artifact integrity failure: ' + name)
    return manifest


def synthetic_example():
    def bound(value):
        return {'lo_cents': value, 'hi_cents': value}
    frame = dict(sector='commercial_banks', chamber='House', target=2026, horizon='m18',
        issue='2026-07-20', cut='2026-06-30', history=[dict(cycle=y,
        final={'D': bound(10000), 'R': bound(20000)}, observed={'D': bound(5000), 'R': bound(10000)},
        remaining={'D': bound(5000), 'R': bound(10000)}) for y in (2020, 2022, 2024)],
        observed={'D': bound(6000), 'R': bound(9000)})
    return {'family': 'historical_remainder', 'frame': frame}


def stage_package(destination, evaluation_path, protocol_path, evaluator_path, tests_path):
    destination = Path(destination)
    if destination.exists():
        raise PredictionError('package output exists; overwrite refused')
    evaluation_path, protocol_path, evaluator_path, tests_path = map(Path, (evaluation_path, protocol_path, evaluator_path, tests_path))
    saved = json.loads(evaluation_path.read_bytes())
    # Check frozen input identities without importing or rerunning the evaluator.
    for path, expected in saved['input_sha256'].items():
        if sha(Path(path).read_bytes()) != expected:
            raise PredictionError('frozen source identity changed: ' + path)
    frozen_hashes = saved['input_sha256']
    for path in (protocol_path, evaluator_path):
        matches = [v for p, v in frozen_hashes.items() if Path(p).resolve() == path.resolve()]
        if matches != [sha(path.read_bytes())]:
            raise PredictionError('requested protocol/evaluator differs from frozen run')
    expected = {}
    for row in saved['predictions']:
        key = tuple(row[k] for k in ('sector', 'chamber', 'target', 'horizon', 'family', 'party'))
        if key in expected:
            raise PredictionError('duplicate saved prediction identity')
        expected[key] = row
    checked, largest, seen = 0, 0.0, set()
    for frame in saved['feature_frames']:
        for family in FAMILIES:
            for prediction in predict(frame, family):
                key = tuple(prediction[k] for k in ('sector', 'chamber', 'target', 'horizon', 'family', 'party'))
                if key in seen or key not in expected:
                    raise PredictionError('parity population mismatch')
                seen.add(key)
                for field, value in prediction.items():
                    if expected[key][field] != value:
                        raise PredictionError('saved forecast parity failure: ' + str(key) + '/' + field)
                for field in ('prediction_lo_cents', 'prediction_hi_cents'):
                    largest = max(largest, abs(prediction[field] - expected[key][field]))
                checked += 1
    if seen != set(expected) or checked != 960 or len(saved['feature_frames']) != 160:
        raise PredictionError('exact 960-prediction / 160-frame frozen parity required')
    parity = dict(status='PASS', prediction_rows_checked=checked, frames_checked=160,
        maximum_endpoint_difference_cents=largest, all_prediction_fields_equal=True,
        evaluation_sha256=sha(evaluation_path.read_bytes()), evaluator_sha256=sha(evaluator_path.read_bytes()),
        predictor_sha256=sha(Path(__file__).read_bytes()), outcome_scoring_performed=False,
        model_selection_performed=False, interpretation='Arithmetic replay of saved features and forecast fields only; not new accuracy evidence.')
    request = synthetic_example()
    readme = '''# Offline PAC dollar interval predictor

All three frozen methods are callable: `prior_party`, `party_R12_remaining`, and `historical_remainder`. Every request names its method explicitly. This package does not select a winner, score outcomes, classify source records, or activate the application.

Use Python's standard library only. Import `predict_interval` and call `predict(frame, family)` for two D/R interval rows, or `predict_request({"family": family, "frame": frame})` for the result envelope. Even target years from 2018 onward, including 2026, are supported when valid dated inputs exist.

The frame contains sector, chamber, target, horizon, issue, cut, current observed D/R intervals, and three history entries (t-6, t-4, t-2) with final, observed and direct remaining-row D/R intervals. Every interval needs finite ordered `lo_cents` and `hi_cents`. Additional audited availability metadata, when supplied, is checked. Missing values are errors; no zero or midpoint is substituted. Amounts are signed nominal cents.

Historical final inputs must reflect rows available by the target issue. Earlier observations use their own same-horizon issue and transaction cut. Remaining intervals must be formed from exact F-minus-O row sets. Preparing trustworthy source intervals is a separate task; a valid JSON frame does not establish source coverage or historical availability.

The synthetic 2026 example is illustrative data, not a real forecast. Supporting a future scheduled issue does not assert its inputs exist now: for example, an October 20, 2026 issue follows the study's October 5 information basis. No source-current 2026 forecast is included. Run the example with `python predict_interval.py predict --request example-synthetic-2026.json --output example-copy.json --package-manifest package_manifest.json --expected-package-sha256 HASH`, replacing HASH with the reviewed package_manifest.sha256 value. Existing output paths are refused. The manifest identity must be pinned independently when transferring the package.

The manifest binds the frozen protocol, evaluator, classified data and evaluation identities. runtime_parity.json records exact parity for all 960 saved predictions. It is an implementation check, not a fresh backtest. The four historical issue horizons share election outcomes.

Strict classification admission remains HOLD. Conditional bounds cover declared source-classification uncertainty, not future forecast errors, missing filings or all possible source mistakes. Canonical cycle-office scope differs from legacy H/S, transaction-date office and whole-industry giving. Later amendments, fixed cohorts, later snapshots and reconstructed DUE limit historical availability claims. Domain uncertainty invokes the explicit full-prior-interval fallback. Marginal bounds across parties/chambers are not jointly additive. No prospective accuracy rate, publication recommendation, or live activation is claimed.
'''
    artifacts = {'predict_interval.py': Path(__file__).read_bytes(), 'test_predict_interval.py': tests_path.read_bytes(),
        'BOUNDED_PAC_PROTOCOL.md': protocol_path.read_bytes(), 'runtime_parity.json': canonical(parity),
        'example-synthetic-2026.json': canonical(request), 'example-synthetic-2026-output.json': canonical(predict_request(request)),
        'README.md': readme.encode('utf-8')}
    manifest = dict(schema_version=1, kind='offline_pac_interval_predictor_package',
        created_at_utc=datetime.now(timezone.utc).isoformat(), families=list(FAMILIES), default_family=None,
        live_activation=False, activation_recommended=False, selection_performed=False,
        strict_classification_admission='HOLD', target_scope='canonical cycle-office PAC cohort dollars',
        source_hashes=dict(frozen_hashes), evaluation_sha256=parity['evaluation_sha256'],
        protocol_sha256=sha(protocol_path.read_bytes()), evaluator_sha256=sha(evaluator_path.read_bytes()),
        predictor_sha256=parity['predictor_sha256'], limitations=list(LIMITATIONS),
        artifact_sha256={name: sha(raw) for name, raw in artifacts.items()})
    manifest_raw = canonical(manifest)
    destination.mkdir(parents=True, exist_ok=False)
    for name, raw in dict(artifacts, **{'package_manifest.json': manifest_raw,
            'package_manifest.sha256': (sha(manifest_raw) + '\n').encode()}).items():
        with (destination / name).open('xb') as handle:
            handle.write(raw)
    verify_package(destination / 'package_manifest.json', sha(manifest_raw))
    return dict(output=str(destination), package_sha256=sha(manifest_raw), **parity)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    run = commands.add_parser('predict')
    run.add_argument('--request', type=Path, required=True)
    run.add_argument('--output', type=Path, required=True)
    run.add_argument('--package-manifest', type=Path)
    run.add_argument('--expected-package-sha256')
    stage = commands.add_parser('stage')
    for name in ('output', 'evaluation', 'protocol', 'evaluator', 'tests'):
        stage.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'stage':
        result = stage_package(args.output, args.evaluation, args.protocol, args.evaluator, args.tests)
        print(json.dumps(result, indent=2))
    else:
        if args.output.exists():
            raise PredictionError('prediction output exists; overwrite refused')
        if bool(args.package_manifest) != bool(args.expected_package_sha256):
            raise PredictionError('package manifest and expected identity must be supplied together')
        if args.package_manifest:
            verify_package(args.package_manifest, args.expected_package_sha256)
        result = predict_request(json.loads(args.request.read_bytes()))
        with args.output.open('xb') as handle:
            handle.write(canonical(result))
        print(json.dumps({'output': str(args.output), 'live_activation': False}))


if __name__ == '__main__':
    main()

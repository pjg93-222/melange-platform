"""Conditional PAC-dollar interval research; strict classification admission is HOLD.

Three fixed uncertainty-aware rules, no selection, midpoint, or learned parameter.
Unresolved party or office rows are pooled over both chambers. The final adapter
requires a hash-pinned reviewed canonical-office manifest. Independent interval boxes
can include infeasible allocations; lack of certification is not an actual miss.
"""
from __future__ import annotations
import argparse
import csv
from datetime import date
import hashlib
import io
import json
import math
from pathlib import Path
import re
from statistics import median

SECTORS = ('commercial_banks', 'credit_unions', 'property_casualty', 'credit_arms', 'telecom')
CHAMBERS = ('House', 'Senate')
PARTIES = ('D', 'R')
YEARS = tuple(range(2012, 2025, 2))
TARGETS = (2018, 2020, 2022, 2024)
HORIZONS = ('m12', 'm15', 'm18', 'm21')
FAMILIES = ('prior_party', 'party_R12_remaining', 'historical_remainder')
PROTOCOL = dict(id='conditional-pac-interval-three-family-v1', families=FAMILIES,
    target_cycles=TARGETS, horizons=HORIZONS, unit='signed_nominal_cents', selection=False,
    midpoint=False, publication=False, strict_classification_admission='HOLD',
    target='Canonical cycle-office candidate population, not unchanged legacy H/S and not transaction-date office.',
    uncertainty='All unresolved potentially-H/S office rows plus Unknown-party canonical-H/S rows pooled across both chambers; classified labels remain source assumptions.',
    classification_schema='pac-canonical-v3; reviewed manifest required; retain original fields and scan all counted rows before canonical routing.',
    bounds='known party/chamber amount plus [sum unknown negative cents, sum unknown positive cents]',
    availability='Historical final features filtered by target issue DUE; historical observations by their own horizon DUE and transaction cut; latest editions, not original vintage.',
    fallback='New uncertainty-aware rule: if formula domain is not certified over the whole input box, return the full prior interval. This does not enclose every branch of the old point predictor.',
    certification='Ylo>0 and Plo>=(1-tau)*Yhi and Phi<=(1+tau)*Ylo; otherwise not certified, not an asserted miss.',
    dependence='Marginal boxes lose coupling and can include infeasible allocations. Historical remainders bound the direct F-minus-O row set, so same-record uncertainty cancels.',
    negative_or_zero_truth='No percentage certification when outcome lower bound is nonpositive; retain dollar bounds and count.',
    source_status='All historical outcomes previously examined. Conditional retrospective research, no untouched holdout.')


class BoundsError(ValueError):
    pass


def canonical(value):
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def schedule(year, horizon):
    if type(year) is not int or year % 2 or horizon not in HORIZONS:
        raise BoundsError('unsupported cycle or horizon')
    if horizon == 'm12':
        return f'{year}-01-31', f'{year-1}-12-31'
    issue, cut = {'m15': ('04-20', '03-31'), 'm18': ('07-20', '06-30'), 'm21': ('10-20', '09-30')}[horizon]
    return f'{year}-{issue}', f'{year}-{cut}'


def canonical_date(value):
    try:
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError()
    except (TypeError, ValueError) as error:
        raise BoundsError('noncanonical date') from error
    return value


def interval(low, high):
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in (low, high)) or low > high:
        raise BoundsError('finite ordered interval required')
    return {'lo_cents': low, 'hi_cents': high}


def endpoints(value):
    if not isinstance(value, dict):
        raise BoundsError('interval object required')
    try:
        valid = interval(value['lo_cents'], value['hi_cents'])
    except KeyError as error:
        raise BoundsError('missing interval endpoint') from error
    return valid['lo_cents'], valid['hi_cents']


def read_csv(path):
    with Path(path).open(encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise BoundsError('missing or duplicate CSV columns')
        rows = list(reader)
    if any(None in r for r in rows):
        raise BoundsError('extra unnamed CSV values')
    return rows


def validate_manifest(manifest, ledger_hash, presence_hash):
    if not isinstance(manifest, dict) or manifest.get('schema_version') != 'pac-canonical-v3':
        raise BoundsError('final reviewed V3 classification manifest required')
    if manifest.get('status') not in ('HOLD', 'ADMITTED') or any(manifest.get(k) is not True for k in (
            'final_ledger_written', 'counting_equal', 'all_original_fields_equal')):
        raise BoundsError('inventory or unverified counting bridge is not an approved ledger')
    if manifest.get('fitting_or_scoring_performed') is not False:
        raise BoundsError('classification receipt must precede scoring')
    if manifest.get('ledger_sha256') != ledger_hash or manifest.get('presence_sha256') != presence_hash:
        raise BoundsError('classified ledger or presence hash mismatch')
    if type(manifest.get('original_rows')) is not int or manifest['original_rows'] != manifest.get('output_rows'):
        raise BoundsError('classification changed counted row population')
    for key in ('approved_rule_sha256', 'evidence_sha256', 'source_manifest_sha256'):
        if not re.fullmatch('[0-9a-fA-F]{64}', str(manifest.get(key, ''))):
            raise BoundsError('classification provenance hash required: ' + key)
    return manifest


def verify_provenance_files(manifest):
    for prefix in ('approved_rule', 'evidence', 'source_manifest'):
        path = manifest.get(prefix + '_path')
        if not isinstance(path, str) or not Path(path).is_file() or digest(path) != manifest[prefix + '_sha256']:
            raise BoundsError('classification provenance file missing or hash mismatch: ' + prefix)


def v3_route(row):
    """Return effective chamber, party and uncertainty without altering originals."""
    office = row.get('canonical_chamber')
    party = row.get('canonical_party')
    status = row.get('canonical_classification_status')
    office_status = row.get('canonical_office_resolution_status')
    eligible = row.get('canonical_hs_eligibility')
    original_flag = row.get('original_office_discrepancy')
    if office not in (*CHAMBERS, 'President', 'Other', 'Unknown') or party not in (*PARTIES, 'Other', 'Unknown'):
        raise BoundsError('invalid canonical office or party enum')
    if status not in ('resolved', 'unresolved') or office_status not in ('unresolved', 'source_resolved', 'no_discrepancy'):
        raise BoundsError('invalid canonical resolution enum')
    if original_flag not in ('True', 'False') or eligible not in ('include_hs', 'exclude_hs', 'unresolved_potential_hs'):
        raise BoundsError('invalid canonical discrepancy flag or H/S eligibility')
    if status == 'resolved' and (party == 'Unknown' or office_status == 'unresolved'):
        raise BoundsError('resolved classification contradicts unknown party or office')
    if office_status == 'source_resolved':
        evidence = row.get('canonical_office_evidence_key', '')
        pieces = evidence.split(':')
        if len(pieces) != 3 or pieces[0] != str(row['cycle']) or not pieces[2]:
            raise BoundsError('resolved office requires exact cycle/candidate/recipient evidence key')
        if pieces[1] != (row.get('candidate_id') or ''):
            raise BoundsError('office evidence key does not identify original candidate')
        effective_office = office
    elif office_status == 'no_discrepancy':
        # Original ledgers spell this office Presidential; V3 uses President.
        # Normalize only that representation, retaining the raw bridge label.
        original_office = 'President' if row.get('chamber') == 'Presidential' else row.get('chamber')
        if original_flag != 'False' or office != original_office:
            raise BoundsError('no-discrepancy office cannot silently change original chamber')
        effective_office = original_office
    else:
        effective_office = 'Unknown'
    if eligible == 'unresolved_potential_hs':
        if office_status != 'unresolved':
            raise BoundsError('potential-H/S flag disagrees with resolved office')
        return 'Unknown', 'Unknown', True
    if office_status == 'unresolved':
        raise BoundsError('unresolved office cannot be excluded or assigned to a chamber')
    if eligible == 'exclude_hs':
        if effective_office not in ('President', 'Other'):
            raise BoundsError('H/S exclusion needs verified non-H/S office')
        return effective_office, party, False
    if effective_office not in CHAMBERS:
        raise BoundsError('H/S inclusion requires verified House or Senate office')
    if party == 'Unknown' or status == 'unresolved':
        return effective_office, 'Unknown', True
    if not row.get('canonical_candidate_id') or not row.get('canonical_evidence_key'):
        raise BoundsError('resolved candidate/party requires source identity and evidence key')
    return effective_office, party, False


class Panel:
    """Query known labels plus a common Unknown pool, keeping every signed dollar."""
    def __init__(self, rows, presence, classification_manifest=None, ledger_hash=None, presence_hash=None):
        self.known = {}
        self.unknown = {}
        self.presence = {}
        self.row_count = 0
        self.unknown_row_count = 0
        self.known_office_discrepancy_row_count = 0
        self.pooled_uncertainty_row_count = 0
        self.classification_manifest = validate_manifest(classification_manifest, ledger_hash, presence_hash) if classification_manifest is not None else None
        self.routing_bridge = {}
        self.original_counted_rows = 0
        self.original_counted_cents = 0
        self.excluded_canonical_rows = 0
        self.excluded_canonical_cents = 0
        for row in presence:
            key = row['sector'], int(row['cycle']), row['chamber']
            if key in self.presence:
                raise BoundsError('duplicate presence cell')
            if str(row['ledger_reconciled']).lower() != 'true' or int(row['sector_raw_rows']) <= 0:
                raise BoundsError('unreconciled or absent source population')
            first = row['first_due_any_raw']
            self.presence[key] = canonical_date(first) if first else None
        expected = {(s, y, c) for s in SECTORS for y in YEARS for c in CHAMBERS}
        if set(self.presence) != expected:
            raise BoundsError('complete 70-cell source-presence grid required')
        seen = set()
        for original in rows:
            row = dict(original, cycle=int(original['cycle']), cents=int(original['cents']))
            self.original_counted_rows += 1
            self.original_counted_cents += row['cents']
            identity = row['sector'], row['cycle'], int(row['source_record_order'])
            if identity in seen:
                raise BoundsError('duplicate counted source row')
            seen.add(identity)
            if row['sector'] not in SECTORS or row['cycle'] not in YEARS:
                raise BoundsError('unsupported source identity')
            for field in ('date', 'due_available'):
                canonical_date(row[field])
            if not f"{row['cycle']-1}-01-01" <= row['date'] <= f"{row['cycle']}-12-31":
                raise BoundsError('transaction outside cycle')
            if self.classification_manifest is not None:
                chamber, party, uncertain = v3_route(row)
                office_uncertain = row['canonical_office_resolution_status'] == 'unresolved'
                excluded = chamber in ('President', 'Other') and not uncertain
            else:
                # Synthetic/v2 compatibility; production CLI always requires V3 manifest.
                chamber, party = row['chamber'], row['party']
                if party not in ('D', 'R', 'Other', 'Unknown'):
                    raise BoundsError('unsupported source party')
                office_uncertain = str(row.get('party_office_discrepancy', '')).lower() in ('true', '1')
                uncertain = party == 'Unknown' or office_uncertain
                excluded = int(row['hs']) != 1 and not office_uncertain
            route = 'excluded' if excluded else 'uncertain' if uncertain else 'known'
            bridge_key = row['chamber'], chamber, route, int(row['hs'])
            bridge = self.routing_bridge.setdefault(bridge_key, {'rows': 0, 'cents': 0})
            bridge['rows'] += 1
            bridge['cents'] += row['cents']
            if excluded:
                self.excluded_canonical_rows += 1
                self.excluded_canonical_cents += row['cents']
                continue
            self.row_count += 1
            if uncertain:
                self.unknown.setdefault((row['sector'], row['cycle']), []).append(row)
                self.pooled_uncertainty_row_count += 1
                self.unknown_row_count += party == 'Unknown'
                self.known_office_discrepancy_row_count += office_uncertain and row.get('party') != 'Unknown'
            else:
                if chamber not in CHAMBERS:
                    raise BoundsError('known label lacks a House/Senate chamber')
                self.known.setdefault((row['sector'], row['cycle'], chamber, party), []).append(row)
        if self.classification_manifest is not None and self.original_counted_rows != self.classification_manifest['output_rows']:
            raise BoundsError('classified ledger count differs from admission receipt')

    def _admit_query(self, sector, year, chamber, party, issue=None, cut=None):
        if (sector, year, chamber) not in self.presence or party not in PARTIES:
            raise BoundsError('unsupported bound query')
        if issue is not None:
            canonical_date(issue)
        if cut is not None:
            canonical_date(cut)
        first = self.presence[sector, year, chamber]
        if first is None or (issue is not None and first > issue):
            raise BoundsError('source presence unavailable at issue; missing is not zero')

    def _bound_rows(self, sector, year, chamber, party, included):
        known = [r for r in self.known.get((sector, year, chamber, party), []) if included(r)]
        unknown = [r for r in self.unknown.get((sector, year), []) if included(r)]
        total = sum(r['cents'] for r in known)
        negative = sum(min(0, r['cents']) for r in unknown)
        positive = sum(max(0, r['cents']) for r in unknown)
        return dict(interval(total + negative, total + positive), known_cents=total, known_rows=len(known),
                    unknown_negative_cents=negative, unknown_positive_cents=positive,
                    unknown_net_cents=negative + positive, unknown_rows=len(unknown),
                    unknown_pooled_over_both_chambers=True, width_cents=positive - negative)

    def amount(self, sector, year, chamber, party, issue=None, cut=None):
        self._admit_query(sector, year, chamber, party, issue, cut)
        def included(row):
            return (issue is None or row['due_available'] <= issue) and (cut is None or row['date'] <= cut)
        return dict(self._bound_rows(sector, year, chamber, party, included),
                    source_issue_filter=issue, source_transaction_cut=cut)

    def remaining(self, sector, year, chamber, party, target_issue, historical_issue, historical_cut):
        self._admit_query(sector, year, chamber, party, target_issue)
        self._admit_query(sector, year, chamber, party, historical_issue, historical_cut)
        if historical_issue >= target_issue:
            raise BoundsError('historical observed set must precede target issue')
        # Historical O is a subset of F. Cancel those exact rows before bounding.
        def included(row):
            return row['due_available'] <= target_issue and not (
                row['due_available'] <= historical_issue and row['date'] <= historical_cut)
        return dict(self._bound_rows(sector, year, chamber, party, included),
                    source_issue_filter=target_issue, removed_observed_issue=historical_issue,
                    removed_observed_cut=historical_cut, direct_remaining_rows=True)

    def frame(self, sector, chamber, year, horizon):
        issue, cut = schedule(year, horizon)
        history = []
        for old in (year - 6, year - 4, year - 2):
            old_issue, old_cut = schedule(old, horizon)
            history.append(dict(cycle=old,
                final={p: self.amount(sector, old, chamber, p, issue=issue) for p in PARTIES},
                observed={p: self.amount(sector, old, chamber, p, issue=old_issue, cut=old_cut) for p in PARTIES},
                remaining={p: self.remaining(sector, old, chamber, p, issue, old_issue, old_cut) for p in PARTIES}))
        return dict(sector=sector, chamber=chamber, target=year, horizon=horizon, issue=issue, cut=cut,
                    history=history, observed={p: self.amount(sector, year, chamber, p, issue=issue, cut=cut) for p in PARTIES})


def capped_geometric(ratios):
    if any(not math.isfinite(r) or r < 0 for r in ratios) or len(ratios) != 3:
        raise BoundsError('three nonnegative finite completion bounds required')
    if any(r == 0 for r in ratios):
        return 0.0
    # Cap in log space to prevent unnecessary exponent overflow.
    return math.exp(min(0.0, math.fsum(math.log(r) for r in ratios) / 3))


def predict(frame, family):
    if family not in FAMILIES or set(frame) != {'sector', 'chamber', 'target', 'horizon', 'issue', 'cut', 'history', 'observed'}:
        raise BoundsError('invalid family or feature-frame contract')
    if frame['sector'] not in SECTORS or frame['chamber'] not in CHAMBERS or frame['target'] not in TARGETS:
        raise BoundsError('unsupported forecast identity')
    if (frame['issue'], frame['cut']) != schedule(frame['target'], frame['horizon']):
        raise BoundsError('issue/cut mismatch')
    years = [frame['target'] - 6, frame['target'] - 4, frame['target'] - 2]
    if [h['cycle'] for h in frame['history']] != years:
        raise BoundsError('exact three earlier cycles required')
    for block in [frame['observed']] + [h[key] for h in frame['history'] for key in ('final', 'observed', 'remaining')]:
        if set(block) != set(PARTIES):
            raise BoundsError('both party intervals required')
        for val in block.values():
            endpoints(val)
    output = []
    for party in PARTIES:
        final = [endpoints(h['final'][party]) for h in frame['history']]
        observed = [endpoints(h['observed'][party]) for h in frame['history']]
        blo, bhi = final[-1]
        olo, ohi = endpoints(frame['observed'][party])
        low, high, fallback, trace = blo, bhi, None, {}
        if family == 'party_R12_remaining':
            if blo < 0 or olo < 0 or any(flo <= 0 for flo, _ in final) or any(lo < 0 for lo, _ in observed):
                fallback = 'input box does not certify the nonnegative R12 domain; use full prior interval'
            else:
                ratios_lo = [o[0] / f[1] for f, o in zip(final, observed)]
                ratios_hi = [o[1] / f[0] for f, o in zip(final, observed)]
                slo = capped_geometric(ratios_lo)
                shi = capped_geometric(ratios_hi)
                low, high = olo + (1 - shi) * blo, ohi + (1 - slo) * bhi
                trace = dict(completion_lo=slo, completion_hi=shi, historical_ratios_lo=ratios_lo, historical_ratios_hi=ratios_hi)
        elif family == 'historical_remainder':
            remaining = [endpoints(h['remaining'][party]) for h in frame['history']]
            remainder_low = median(r[0] for r in remaining)
            remainder_high = median(r[1] for r in remaining)
            candidate_low, candidate_high = olo + remainder_low, ohi + remainder_high
            trace = dict(remainder_lo_cents=remainder_low, remainder_hi_cents=remainder_high,
                         formula_lo_cents=candidate_low, formula_hi_cents=candidate_high)
            if candidate_low < 0:
                fallback = 'remainder box includes a negative prediction; use full prior interval'
            else:
                low, high = candidate_low, candidate_high
        valid = interval(low, high)
        output.append(dict(sector=frame['sector'], chamber=frame['chamber'], target=frame['target'],
            horizon=frame['horizon'], issue=frame['issue'], cut=frame['cut'], family=family, party=party,
            prediction_lo_cents=valid['lo_cents'], prediction_hi_cents=valid['hi_cents'],
            prediction_width_cents=high - low, prior_lo_cents=blo, prior_hi_cents=bhi,
            observed_lo_cents=olo, observed_hi_cents=ohi, training_years=years,
            uncertainty_rule_fallback=fallback, trace=trace,
            conditional_target='canonical cycle-office research; conditional on reviewed source labels and coverage',
            chamber_basis='Source-resolved canonical office; unresolved party/office pooled across both chambers',
            strict_classification_admission='HOLD'))
    return output


def certify(prediction, truth):
    plo, phi = endpoints(interval(prediction['prediction_lo_cents'], prediction['prediction_hi_cents']))
    ylo, yhi = endpoints(truth)
    positive = ylo > 0
    dollar_upper = max(abs(plo - yhi), abs(phi - ylo))
    ape_upper = max(abs(p - y) / y for p in (plo, phi) for y in (ylo, yhi)) if positive else None
    return dict(prediction, truth_lo_cents=ylo, truth_hi_cents=yhi, truth_width_cents=yhi - ylo,
                outcome_positive_over_full_interval=positive,
                certified_within10=positive and plo >= .9 * yhi and phi <= 1.1 * ylo,
                certified_within20=positive and plo >= .8 * yhi and phi <= 1.2 * ylo,
                absolute_error_upper_cents=dollar_upper, ape_upper_bound=ape_upper,
                certification_interpretation='guaranteed over conservative interval box; not certified does not mean an actual miss')


def metrics(rows):
    count = len(rows)
    if not count:
        raise BoundsError('empty metric group')
    eligible = [r for r in rows if r['outcome_positive_over_full_interval']]
    widths = [r['prediction_width_cents'] for r in rows]
    return dict(n=count, positive_truth_interval_n=len(eligible), nonpositive_truth_lower_n=count - len(eligible),
                certified_within10_count=sum(r['certified_within10'] for r in rows),
                certified_within20_count=sum(r['certified_within20'] for r in rows),
                certified_within10_fraction_all_cells=sum(r['certified_within10'] for r in rows) / count,
                certified_within20_fraction_all_cells=sum(r['certified_within20'] for r in rows) / count,
                not_certified_within10_count=sum(not r['certified_within10'] for r in rows),
                not_certified_within20_count=sum(not r['certified_within20'] for r in rows),
                uncertainty_rule_fallback_count=sum(r['uncertainty_rule_fallback'] is not None for r in rows),
                exact_prediction_interval_count=sum(w == 0 for w in widths),
                exact_truth_interval_count=sum(r['truth_width_cents'] == 0 for r in rows),
                mean_prediction_width_dollars=math.fsum(widths) / count / 100,
                max_prediction_width_dollars=max(widths) / 100,
                mean_truth_width_dollars=math.fsum(r['truth_width_cents'] for r in rows) / count / 100,
                maximum_ape_upper_bound=max((r['ape_upper_bound'] for r in eligible), default=None),
                mean_absolute_error_upper_dollars=math.fsum(r['absolute_error_upper_cents'] for r in rows) / count / 100)


def evaluate(panel):
    # Construct every feature/truth cell first; refuse missing data, never drop it.
    frames = {(s, c, y, h): panel.frame(s, c, y, h) for s in SECTORS for c in CHAMBERS for y in TARGETS for h in HORIZONS}
    unscored = []
    for key, frame in frames.items():
        for family in FAMILIES:
            unscored.extend(predict(frame, family))
    # Only after every forecast is fixed do we read closed-cycle target bounds.
    truth = {(s, c, y, p): panel.amount(s, y, c, p) for s in SECTORS for c in CHAMBERS for y in TARGETS for p in PARTIES}
    predictions = [certify(r, truth[r['sector'], r['chamber'], r['target'], r['party']]) for r in unscored]
    expected = {(s, c, y, h, f, p) for s in SECTORS for c in CHAMBERS for y in TARGETS for h in HORIZONS for f in FAMILIES for p in PARTIES}
    actual = {(r['sector'], r['chamber'], r['target'], r['horizon'], r['family'], r['party']) for r in predictions}
    if actual != expected or len(predictions) != len(expected):
        raise BoundsError('complete 960-cell output grid required')
    groups = {}
    for row in predictions:
        for horizon in (row['horizon'], 'ALL'):
            for sector in (row['sector'], 'ALL'):
                for chamber in (row['chamber'], 'ALL'):
                    for party in (row['party'], 'ALL'):
                        for target in (str(row['target']), 'ALL'):
                            key = horizon, row['family'], sector, chamber, party, target
                            groups.setdefault(key, []).append(row)
    names = ('horizon', 'family', 'sector', 'chamber', 'party', 'target')
    return dict(protocol=PROTOCOL, predictions=predictions,
                feature_frames=list(frames.values()), truth_intervals=[dict(sector=k[0], chamber=k[1], target=k[2], party=k[3], **v) for k, v in truth.items()],
                metrics=[dict(zip(names, key), **metrics(rows)) for key, rows in sorted(groups.items())],
                source_hs_rows=panel.row_count, source_unknown_hs_rows=panel.unknown_row_count,
                source_flagged_known_office_rows_pooled=panel.known_office_discrepancy_row_count,
                source_pooled_uncertainty_hs_rows=panel.pooled_uncertainty_row_count,
                original_counted_rows=panel.original_counted_rows, original_counted_cents=panel.original_counted_cents,
                excluded_canonical_rows=panel.excluded_canonical_rows, excluded_canonical_cents=panel.excluded_canonical_cents,
                routing_bridge=[dict(original_chamber=k[0], effective_chamber=k[1], route=k[2], original_hs=k[3], **v) for k, v in sorted(panel.routing_bridge.items())],
                full_grid_summary_repeats_election_outcomes_across_four_issue_horizons=True,
                strict_classification_admission='HOLD', live_activation=False, midpoint=False,
                no_actual_miss_claim=True, no_original_vintage_claim=True)


def csv_bytes(rows):
    stream = io.StringIO(newline='')
    columns = list(dict.fromkeys(key for row in rows for key in row))
    writer = csv.DictWriter(stream, fieldnames=columns)
    writer.writeheader()
    for row in rows:
        writer.writerow({k: json.dumps(v, sort_keys=True, allow_nan=False) if isinstance(v, (dict, list, tuple)) else v for k, v in row.items()})
    return stream.getvalue().encode('utf-8-sig')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ('ledger', 'presence', 'output', 'protocol', 'classification-manifest'):
        parser.add_argument('--' + field, required=True, type=Path)
    parser.add_argument('--protocol-sha256', required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise BoundsError('output exists; overwrite refused')
    if digest(args.protocol) != args.protocol_sha256:
        raise BoundsError('external frozen protocol hash mismatch')
    manifest = json.loads(args.classification_manifest.read_text(encoding='utf-8-sig'))
    validate_manifest(manifest, digest(args.ledger), digest(args.presence))
    verify_provenance_files(manifest)
    result = evaluate(Panel(read_csv(args.ledger), read_csv(args.presence), manifest, digest(args.ledger), digest(args.presence)))
    result['classification_receipt'] = manifest
    result['input_sha256'] = {str(p): digest(p) for p in (args.ledger, args.presence, args.protocol, args.classification_manifest, Path(__file__))}
    payloads = {'evaluation.json': canonical(result), 'predictions.csv': csv_bytes(result['predictions']),
                'metrics.csv': csv_bytes(result['metrics']), 'truth_intervals.csv': csv_bytes(result['truth_intervals']),
                'routing_bridge.csv': csv_bytes(result['routing_bridge'])}
    args.output.mkdir(parents=True, exist_ok=False)
    for name, content in payloads.items():
        with (args.output / name).open('xb') as out:
            out.write(content)
    print(json.dumps(dict(predictions=len(result['predictions']), strict_classification_admission='HOLD', live_activation=False)))


if __name__ == '__main__':
    main()

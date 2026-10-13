"""Build an immutable honors candidate; --publish atomically selects it.

No analytic stance, reporting rank, primacy factor or model output is written.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import date
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import sys

FEC = Path(__file__).resolve().parents[1]
if str(FEC) not in sys.path:
    sys.path.insert(0, str(FEC))

from paths import today
from stance.rank_honors_policy import (LADDER_G, POLICY, SCHEMA, SCHEMA_LADDER_G,
                                       classify_field, ladder_of, literal, number)
from stance.overall_stance_policy import OVERALL_POLICY, classify_overall
from stance.build_field_action_evidence import Snapshot, build_evidence, canonical_measure
from stance.honors_views import POINTER_SCHEMA, load_generation, validate_honors


def serialized(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n').encode('utf-8')


def digest(value):
    return hashlib.sha256(serialized(value)).hexdigest()


SERVICE_SPINE = ('data/staffer_network/reference/congress-legislators'
                 '/750c0608efb6ef1fc3257ba72c99af3771d35088/legislators-current.yaml',
                 'data/staffer_network/reference/congress-legislators'
                 '/750c0608efb6ef1fc3257ba72c99af3771d35088/legislators-historical.yaml')


def service_years(snapshot, roster, as_of, policy):
    """Rule G denominator: dated service in the window, clipped and clamped.

    Scalar ID/date fields only, read from the frozen congress-legislators spine
    through the snapshot so every byte is hash-bound like any other input. No
    YAML tag is interpreted and a name is never used as an identity.
    """
    window_start = date.fromisoformat(policy['score_window_start'])
    window_end = date.fromisoformat(str(as_of))
    floor_, cap = number(policy['years_min']), number(policy['years_cap'])
    days = defaultdict(int)
    wanted = {row['member_id'] for row in roster.values()}
    for path in SERVICE_SPINE:
        text = snapshot.read(path).decode('utf-8')
        for block in re.split(r'(?m)^- id:\s*$', text)[1:]:
            found = re.search(r"(?m)^    bioguide: ['\"]?([A-Z]\d{6})", block)
            if not found or found[1] not in wanted:
                continue
            terms = block.split('  terms:\n', 1)
            if len(terms) < 2:
                continue
            for term in re.split(r'(?m)^  - type: ', terms[1])[1:]:
                if term.splitlines()[0].strip() not in ('rep', 'sen'):
                    continue
                values = dict(re.findall(r'(?m)^    (start|end): (.+)$', term))
                if len(values) != 2:
                    raise ValueError('Undated service term: ' + found[1])
                start, end = (date.fromisoformat(values[key].strip('\'"')) for key in ('start', 'end'))
                if end <= start:
                    raise ValueError('Nonpositive service term: ' + found[1])
                overlap = (min(end, window_end) - max(start, window_start)).days
                if overlap > 0:
                    days[found[1]] += overlap
    years, fallbacks = {}, []
    for member_id in sorted(wanted):
        if member_id not in days:
            fallbacks.append(member_id)
        span = number(Decimal(days.get(member_id, 0)) / Decimal('365.25'))
        years[member_id] = literal(min(max(span, floor_), cap))
    return years, fallbacks


def load_board(snapshot, roster):
    # tier_util is a read-only library. Capture every source before it reads and
    # compare every byte again before accepting its result and publishing.
    import tier_util as TU
    # THE BOARD BASIS IS tier_util's, NOT A LOCAL DEFAULT (D-030/D-031). Reading
    # the dial here with its own fallback is what published generation
    # 4f2b24fec711a3920df0a6e7: the dial was unset, tier_util seated the desk and
    # the dashboard on 'per_area_v1' while this line seated overall_board on the
    # 'factor' REVERT SWITCH, so the crown followed a board no surface displayed
    # (Crapo/Rounds/Cramer crowned at Tier 2; Huizenga/Kim/Meuser Tier 1 uncrowned).
    # TU.PRIMACY_BOARD is already stripped and lowercased and carries the ruled
    # default, so an unset dial now means the same board everywhere. An explicitly
    # set dial still wins for both, and tier_util.py's own bytes are hashed below,
    # which binds the default this generation was seated under.
    mode = (os.environ.get('PRIMACY_BOARD') or TU.PRIMACY_BOARD).strip().lower()
    sources = ['ranks_python.json', 'data/engine/primacy_factors.json',
               'grievance_ledger.ndjson', 'delivery_ledger.ndjson',
               str(Path(TU._OVERRIDE_PATH)), str(Path(TU.__file__))]
    if mode == 'per_area_v1' and (snapshot.root / TU.PER_AREA_GRADE_PATH).is_file():
        # The per-area grade is half the seating score under this basis, so it
        # belongs in the generation's manifest like every other board input. Left
        # absent, tier_util raises its own actionable error a few lines below.
        sources.append(TU.PER_AREA_GRADE_PATH)
    for path in sources:
        snapshot.read(path)
    if mode == 'realized_results_v1':
        path = os.environ.get('EFFECTIVENESS_COMPARISON_PATH')
        if not path:
            raise ValueError('Active results board needs its comparison generation')
        snapshot.read(path)
    tiers, ranks = TU.legislative_press_tiers(list(roster), root=str(snapshot.root), mode=mode)
    if set(tiers) != set(roster) or any(type(value) is not int or value not in (0, 1, 2, 3) for value in tiers.values()):
        raise ValueError('Active overall board has an incomplete or invalid priority tier map')
    order = sorted((name for name in roster if ranks.get(name, 0) > 0), key=lambda name: ranks[name])
    if [ranks[n] for n in order] != list(range(1, len(order) + 1)):
        raise ValueError('Active board has noncontiguous or duplicate ranks')
    snapshot.verify()
    measure_primacy = {}
    for tag in snapshot.ndjson('bill_beneficiary_tags.ndjson'):
        key = canonical_measure(tag['bill'], tag['congress'])
        value = '-1' if tag.get('acu_position') == 'OPPOSE' else str(tag['act_weight'])
        if key in measure_primacy and measure_primacy[key] != value:
            raise ValueError('Conflicting beneficiary weights for ' + key)
        measure_primacy[key] = value
    return order, {'mode': mode, 'generation': digest({'mode': mode, 'order': order, 'priority_tiers': tiers,
        'factors_sha256': snapshot.digest('data/engine/primacy_factors.json'),
        'policy_sha256': snapshot.digest(TU._OVERRIDE_PATH),
        'minimum_cu_primary_credit': TU.PRIMACY_MIN_CUPRIM})[:24],
        'top13': order[:13], 'order': order, 'priority_tiers': tiers,
        'minimum_cu_primary_credit': TU.PRIMACY_MIN_CUPRIM, 'measure_primacy': measure_primacy}


def build_honors(roster, cats, actions, tensor, board, as_of, *, policy=None, board_metadata=None,
                 years=None, measure_titles=None):
    """Pure producer used by both fixtures and the snapshot-backed CLI."""
    policy = deepcopy(policy or POLICY)
    ladder = ladder_of(policy)
    members = {name: {'member_id': row['member_id'], 'chamber': row['chamber'], 'fields': {}}
               for name, row in sorted(roster.items())}
    by_cat = defaultdict(list)
    for action in actions:
        if action['category_id'] not in cats:
            raise ValueError('Accepted action has unknown category')
        by_cat[action['category_id']].append(action)
    for cat in cats:
        fields = classify_field([r['member_id'] for r in roster.values()], by_cat[cat], tensor,
                                policy=policy, years=years, category=cat)
        for name, member in members.items():
            member['fields'][cat] = {**fields[member['member_id']], 'category_id': cat}
    metadata = deepcopy(board_metadata) if board_metadata else {
        'mode': 'fixture', 'generation': digest(board)[:24], 'top13': board[:13], 'order': board}
    if 'priority_tiers' not in metadata:
        if metadata['mode'] != 'fixture' and metadata.get('generation') != 'fixture':
            raise ValueError('Production board metadata requires the authoritative priority tier map')
        # Fixtures can emulate fixed buckets. Live builds always capture TU's
        # exact tier map, including conduct ceilings and untiered members.
        metadata['priority_tiers'] = {name: (1 if index < 13 else 2 if index < 37 else 3 if index < 85 else 0)
                                      for index, name in enumerate(board)}
        metadata['priority_tiers'].update({name: 0 for name in roster if name not in board})
    overall = classify_overall(roster, actions, tensor, board, metadata['priority_tiers'],
                               measure_primacy=metadata.get('measure_primacy'))
    for name, member in members.items():
        member['overall'] = overall[name]
    doc = {'schema': SCHEMA_LADDER_G if ladder == 'g' else SCHEMA,
           'policy': policy, 'as_of': str(as_of), 'cats': list(cats),
           'members': members, 'overall_board': metadata, 'overall_board_mode': metadata['mode'],
           'overall_policy': deepcopy(OVERALL_POLICY)}
    if ladder == 'g':
        # Published so any consumer can re-derive the per-year rate and the
        # itemized ledger without reading the producer's private state.
        doc['years_served'] = {row['member_id']: years[row['member_id']] for row in roster.values()}
        doc['measure_titles'] = dict(sorted((measure_titles or {}).items()))
    return doc


def transition_report(doc, evidence, years=None):
    old = evidence['analytic']
    actions_by_member_field = defaultdict(list)
    for action in evidence['actions']:
        actions_by_member_field[(action['member_id'], action['category_id'])].append(action)
    old_ranks = {}
    for cat in doc['cats']:
        # Capture the old display's separate all-member mixed-score rank for
        # review only. It is never consulted by the new classifier.
        order = sorted(old['scores'], key=lambda name: -old['scores'][name].get(cat, 0))
        old_ranks[cat] = {name: i + 1 for i, name in enumerate(order)}
    transitions, fields, boundaries = [], {}, []
    for cat in doc['cats']:
        counts, chambers = Counter(), defaultdict(Counter)
        seen_ties = set()
        for name, member in doc['members'].items():
            row = member['fields'][cat]
            if row['action_count']:
                counts['eligible'] += 1
                chambers[member['chamber']]['eligible'] += 1
            label = row['recognition'] or 'No earned recognition'
            counts[label] += 1
            chambers[member['chamber']][label] += 1
            acts = actions_by_member_field[(member['member_id'], cat)]
            vote_only = bool(acts) and all(a['kind'] == 'vote' for a in acts)
            if vote_only and row['recognition']:
                counts['vote_only_recipients'] += 1
            transition = {'member': name, 'member_id': member['member_id'], 'category': cat,
                'old_label': old['matrix'].get(name, {}).get(cat), 'new_label': row['recognition'],
                'old_display_rank': old_ranks[cat].get(name),
                'rank_start': row['rank_start'], 'rank_end': row['rank_end'], 'cohort_size': row['cohort_size'],
                'cutoffs': row['cutoffs'], 'reason': row['reason'], 'raw_field_score': row['raw_field_score'],
                'action_count': row['action_count'], 'vote_only': vote_only, 'boundary_tie': row.get('boundary_tie', False)}
            transitions.append(transition)
            tie = (row['rank_start'], row['rank_end'])
            if row.get('boundary_tie') and tie not in seen_ties:
                seen_ties.add(tie)
                group = [m['fields'][cat] for m in doc['members'].values()
                         if (m['fields'][cat]['rank_start'], m['fields'][cat]['rank_end']) == tie]
                boundaries.append({'category': cat, 'rank_start': tie[0], 'rank_end': tie[1],
                    'member_ids': sorted(r['member_id'] for r in group),
                    'tensor_status': row['tensor_status'], 'cutoffs': row['cutoffs'],
                    'missing_tensor_measures': sorted({key for r in group for key in r['missing_tensor_measures']})})
        fields[cat] = {'counts': dict(counts), 'chambers': {k: dict(v) for k, v in chambers.items()}}
    sensitivity = {}
    by_cat = defaultdict(list)
    for a in evidence['actions']:
        by_cat[a['category_id']].append(a)
    ladder = ladder_of(doc['policy'])
    for weight in ('0.5', '1', '2'):
        changes, vote_only_names = [], []
        for cat in doc['cats']:
            # Rule G prices votes from its own dial, so the sweep moves the dial
            # rather than the stored source weight the ledger must keep.
            policy = {**doc['policy'], 'vote_weight': weight} if ladder == 'g' else None
            actions = (by_cat[cat] if ladder == 'g' else
                       [{**a, 'weight': weight if a['kind'] == 'vote' else a['weight']} for a in by_cat[cat]])
            classified = classify_field([r['member_id'] for r in evidence['roster'].values()], actions,
                                        evidence['tensor'], policy=policy, years=years, category=cat)
            for name, member in doc['members'].items():
                row = classified[member['member_id']]
                original = member['fields'][cat]
                if row['recognition'] != original['recognition']:
                    changes.append({'member': name, 'category': cat, 'default_label': original['recognition'], 'label': row['recognition']})
                acts = [a for a in actions if a['member_id'] == member['member_id']]
                if row['recognition'] and acts and all(a['kind'] == 'vote' for a in acts):
                    vote_only_names.append({'member': name, 'category': cat, 'label': row['recognition']})
        sensitivity[weight] = {'changed_label_count_from_weight_1': len(changes),
                               'changes': changes, 'vote_only_recipients': vote_only_names}
    return {'schema': 'rank-honors-transition-v1', 'policy': doc['policy'],
            'fields': fields, 'boundary_ties': boundaries, 'transitions': transitions,
            'vote_weight_sensitivity': sensitivity, 'evidence_counts': evidence['counts'],
            'overall_board': doc['overall_board'], 'overall_policy': doc['overall_policy'],
            'overall_stance_counts': dict(Counter(member['overall']['stance'] for member in doc['members'].values())),
            'overall_priority_tier_counts': dict(Counter(str(member['overall']['priority_tier']) for member in doc['members'].values())),
            'overall_record_pattern_counts': dict(Counter(member['overall']['record_pattern'] for member in doc['members'].values())),
            'overall_rows': [{'member': name, 'member_id': member['member_id'], **member['overall']}
                             for name, member in doc['members'].items()]}


def _write_once(path, raw):
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError('Refusing to overwrite an immutable honors generation: ' + str(path))
        return
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_bytes(raw)
    os.replace(temporary, path)


def build(root, as_of, *, publish=False, output_dir=None):
    if publish and os.environ.get('HONORS_MODE', 'active').strip().lower() == 'legacy':
        return {'published': False, 'skipped': 'HONORS_MODE=legacy preserves the active generation'}
    snapshot = Snapshot(root)
    ladder = os.environ.get('HONORS_LADDER', 'g').strip().lower()
    if ladder not in ('g', 'legacy'):
        raise ValueError('Unknown HONORS_LADDER: ' + ladder)
    policy = {**POLICY, **LADDER_G} if ladder == 'g' else deepcopy(POLICY)
    evidence = build_evidence(snapshot, as_of)
    board, board_metadata = load_board(snapshot, evidence['roster'])
    years, year_fallbacks = (service_years(snapshot, evidence['roster'], as_of, policy)
                             if ladder == 'g' else (None, []))
    for path in ('stance/rank_honors_policy.py', 'stance/overall_stance_policy.py', 'stance/build_field_action_evidence.py',
                 'stance/build_rank_honors.py', 'stance/honors_views.py', 'bill_rules.py'):
        snapshot.read(FEC / path)
    doc = build_honors(evidence['roster'], evidence['cats'], evidence['actions'], evidence['tensor'],
                       board, as_of, policy=policy, board_metadata=board_metadata, years=years,
                       measure_titles=evidence.get('measure_titles'))
    doc['evidence_window'] = evidence['evidence_window']
    doc['inputs_sha256'] = snapshot.manifest()
    doc['coverage'] = dict(evidence['counts'], **({'service_year_fallbacks': len(year_fallbacks)}
                                                  if ladder == 'g' else {}))
    generation = digest({'honors': doc, 'actions': evidence['actions'], 'tensor': evidence['tensor']})[:24]
    doc['generation_id'] = generation
    evidence_doc = {'schema': 'field-action-evidence-v1', 'generation_id': generation,
                    'actions': evidence['actions'], 'tensor': evidence['tensor'],
                    'inputs_sha256': doc['inputs_sha256'], 'evidence_window': doc['evidence_window']}
    validate_honors(doc, evidence_doc)
    report = transition_report(doc, evidence, years)
    report['generation_id'] = generation
    base = Path(output_dir) if output_dir else snapshot.root / 'data/stance/rank_honors'
    directory = base / generation
    if publish and output_dir is not None and base.resolve() != (snapshot.root / 'data/stance/rank_honors').resolve():
        raise ValueError('Publishing requires the canonical generation directory')
    snapshot.verify()
    directory.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for name, value in (('rank_honors.json', doc), ('field_action_evidence.json', evidence_doc), ('transition_report.json', report)):
        raw = serialized(value)
        _write_once(directory / name, raw)
        hashes[name] = hashlib.sha256(raw).hexdigest()
    load_generation(directory, generation, hashes)
    snapshot.verify()
    if publish:
        pointer = {'schema': POINTER_SCHEMA, 'generation_id': generation, 'policy_id': POLICY['id'],
                   'overall_policy_id': OVERALL_POLICY['id'], 'as_of': str(as_of), 'files_sha256': hashes}
        temporary = base / 'active.json.tmp'
        temporary.write_bytes(serialized(pointer))
        os.replace(temporary, base / 'active.json')
    return {'generation_id': generation, 'directory': str(directory.resolve()), 'published': publish,
            'as_of': str(as_of), 'overall_champions': board[:13], 'ladder': ladder,
            'service_year_fallbacks': year_fallbacks,
            'field_counts': report['fields'], 'evidence_counts': evidence['counts'],
            'overall_stance_counts': report['overall_stance_counts'],
            'overall_priority_tier_counts': report['overall_priority_tier_counts'],
            'unresolved_boundary_groups': len(report['boundary_ties']), 'files_sha256': hashes}


def main():
    from paths import ROOT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(ROOT))
    parser.add_argument('--as-of', type=date.fromisoformat, default=today())
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--publish', action='store_true')
    args = parser.parse_args()
    result = build(args.root, str(args.as_of), publish=args.publish, output_dir=args.output_dir)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()

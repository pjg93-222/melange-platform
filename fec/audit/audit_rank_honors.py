"""Independently reconstruct recognition ordering from normalized action rows."""
from collections import defaultdict, Counter
from decimal import Decimal, ROUND_HALF_EVEN
from pathlib import Path
import argparse
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from paths import ROOT


def audit(root=ROOT, surfaces=False, generation=None):
    from stance.honors_views import load_honors, load_generation
    if generation:
        import re
        if surfaces or not re.fullmatch(r'[a-f0-9]{24}', generation):
            raise ValueError('Candidate audit requires a valid generation and no live surfaces')
        doc = load_generation(Path(root) / 'data/stance/rank_honors' / generation, generation)
    else:
        doc = load_honors(root)
    if doc is None:
        return {'status': 'SKIPPED_LEGACY'}
    directory = Path(root) / 'data/stance/rank_honors' / doc['generation_id']
    evidence = json.loads((directory / 'field_action_evidence.json').read_text(encoding='utf-8'))
    result = audit_document(doc, evidence)
    if surfaces:
        from surfaces.honors_publication import validate_pages
        validate_pages(root, ('Moneyball Dashboard.html', 'cu-member-desk.html', 'Moneyball.html', 'Moneyball_Email.html'))
    result['surface_generation_markers_verified'] = surfaces
    return result


def _observed_alignment(events):
    positive = sum(event['alignment'] > 0 for event in events)
    negative = sum(event['alignment'] < 0 for event in events)
    return ('No recorded action' if not events else 'Mixed actions' if positive and negative else
            'Supportive actions' if positive else 'Opposing actions' if negative else 'Neutral actions')


def _audit_overall_standing(doc, evidence, events_by_id, ids):
    """Independent overall arithmetic, both cohorts, and priority separation."""
    assert doc['overall_policy']['id'] == 'overall_primacy_standing_v2', 'Unknown overall standing policy'
    scores, tensors, missing, status = {}, {}, {}, {}
    quantum = Decimal('0.000000000001')
    def dec(value):
        return Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_EVEN)
    for mid, events in events_by_id.items():
        if not events:
            continue
        scores[mid] = sum((dec(e['weight']) * dec(e['decay']) * e['alignment'] for e in events), Decimal(0))
        missing[mid] = sorted({e['measure_id'] for e in events if e['measure_id'] not in evidence['tensor']
            or e.get('position_on_measure') not in (-1, 1) or not e.get('tensor_position_scoped', True)})
        tensors[mid] = None if missing[mid] else sum((dec(e['weight']) * dec(e['decay'])
            * e['position_on_measure'] * dec(evidence['tensor'][e['measure_id']]) for e in events), Decimal(0))
        status[mid] = 'incomplete' if missing[mid] else 'complete'
    def intervals(mids, descending):
        primary = defaultdict(list)
        for mid in mids:
            primary[scores[mid]].append(mid)
        result, rank = {}, 1
        for score in sorted(primary, reverse=descending):
            block = primary[score]
            if all(tensors[mid] is not None for mid in block):
                groups = defaultdict(list)
                for mid in block:
                    groups[tensors[mid]].append(mid)
                groups = [groups[t] for t in sorted(groups, reverse=descending)]
            else:
                groups = [block]
                if len(block) > 1:
                    for mid in block:
                        status[mid] = 'tensor_unavailable_for_tie'
            for group in groups:
                end = rank + len(group) - 1
                for mid in group:
                    result[mid] = (rank, end)
                rank = end + 1
        return result
    ranks = {name: i+1 for i,name in enumerate(doc['overall_board']['order'])}
    observed = {mid: (ranks[name], ranks[name]) for mid,name in ids.items() if mid in scores and name in ranks}
    negative = {mid: None for mid,events in events_by_id.items() if any(e['alignment']<0 for e in events)}
    positive_cuts = {key: len(ranks)*pct//100 for key,pct in
        (('ally',15),('highly_persuadable',20),('persuadable',50),('lean_persuadable',75))}
    negative_cuts = {'opponent':None,'highly_skeptical':None,'skeptic':None}
    counts, priority_counts, outside_counts = Counter(), Counter(), Counter()
    for mid, name in ids.items():
        cell = doc['members'][name]['overall']
        events = events_by_id[mid]
        rank = cell['rank']
        expected_tier = 1 if rank and rank <= 13 else 2 if rank and rank <= 37 else 3 if rank and rank <= 85 else 0
        if doc['overall_board']['mode'] in ('fixture', 'factor', 'legxfactor'):
            assert doc['overall_board']['priority_tiers'][name] == expected_tier, 'Board priority metadata mismatch'
        else:
            expected_tier = doc['overall_board']['priority_tiers'][name]
        assert cell['priority_tier'] == expected_tier, 'Overall priority membership mismatch'
        assert cell['stance_cohort_size'] == len(ranks), 'Overall observed denominator mismatch'
        assert cell['opposition_cohort_size'] == len(negative), 'Overall negative denominator mismatch'
        assert cell['stance_cutoffs'] == positive_cuts, 'Overall positive cutoffs mismatch'
        assert cell['opposition_cutoffs'] == negative_cuts, 'Overall opposition cutoffs mismatch'
        assert (cell['stance_rank_start'], cell['stance_rank_end']) == observed.get(mid, (None, None)), 'Overall action rank mismatch'
        assert (cell['opposition_rank_start'], cell['opposition_rank_end']) == (None, None), 'Overall opposition rank mismatch'
        pos = sum(e['alignment'] > 0 for e in events)
        neg = sum(e['alignment'] < 0 for e in events)
        assert cell['neutral_action_count'] == sum(e['alignment'] == 0 for e in events), 'Overall neutral count mismatch'
        assert cell['mixed_record'] is bool(pos and neg), 'Overall mixed flag mismatch'
        expected, pattern, boundary = 'Insufficient evidence', 'no_recorded_action', False
        if events:
            score = scores[mid]
            pattern = 'balanced' if score == 0 and pos and neg else 'mixed' if pos and neg else 'supportive' if pos else 'opposing' if neg else 'neutral'
            adverse = [e for e in events if e['alignment']<0]
            primacy = doc['overall_board'].get('measure_primacy')
            axes = {**(primacy or {}), **evidence['tensor']}
            leadership_acts = [e for e in adverse if e['kind'] in ('legislative', 'letter')]
            core = leadership_acts if primacy is None else [e for e in leadership_acts if e['measure_id'] in axes
                and e['position_on_measure'] * Decimal(str(axes[e['measure_id']])) < 0]
            core_measures = sorted({e['measure_id'] for e in core})
            leaders = [e for e in core if e['kind']=='legislative' and e['role'] in ('sponsor','original-cosponsor')]
            measures = sorted({e['measure_id'] for e in adverse})
            leadership = sorted({e['measure_id'] for e in leaders})
            congresses = sorted({e['congress'] for e in leaders})
            expected = 'Uncommitted'
            for key,label in (('ally','Ally'),('highly_persuadable','Highly Persuadable'),('persuadable','Persuadable'),('lean_persuadable','Lean Persuadable')):
                if pos and rank is not None and rank<=positive_cuts[key]:
                    expected=label
                    break
            if not pos and not neg:
                expected='True Neutral'
            elif len(leadership)>=2 and len(congresses)>=2:
                expected='Opponent'
            elif len(leadership)>=2:
                expected='Highly Skeptical'
            elif any(e['role']=='sponsor' for e in leaders) or len(core_measures)>=2:
                expected='Skeptic'
            elif neg and (score<=0 or rank is None or rank>positive_cuts['persuadable']):
                expected='Lean Skeptic'
            if rank is not None and rank<=13:
                assert not any(e['role']=='sponsor' for e in leaders) and len(core_measures)<2, 'Champion conflicts with explicit opposition'
                expected='Champion'
            proof=cell['opposition_evidence']
            assert proof['measures']==measures and proof['lead_measures']==leadership and proof['congresses']==congresses
            assert proof['core_measures']==core_measures
            assert proof['evidence_ids']==sorted(e['evidence_id'] for e in adverse)
            assert dec(cell['raw_overall_score']) == score.quantize(quantum, rounding=ROUND_HALF_EVEN), 'Overall raw score mismatch'
            assert cell['missing_tensor_measures'] == missing[mid], 'Overall missing tensor references mismatch'
            assert cell['tensor_status'] == status[mid], 'Overall tensor coverage mismatch'
            if tensors[mid] is None:
                assert cell['tensor_tiebreak'] is None, 'Fabricated overall tensor score'
            else:
                assert dec(cell['tensor_tiebreak']) == tensors[mid].quantize(quantum, rounding=ROUND_HALF_EVEN), 'Overall tensor score mismatch'
        else:
            assert cell['raw_overall_score'] is None and cell['tensor_tiebreak'] is None
            assert cell['tensor_status'] == 'not_applicable' and cell['missing_tensor_measures'] == []
        assert cell['stance'] == expected, 'Overall behavioral standing mismatch: ' + name
        assert cell['record_pattern'] == pattern, 'Overall action pattern mismatch'
        assert cell['stance_boundary_tie'] is boundary, 'Overall boundary tie mismatch'
        counts[expected] += 1
        priority_counts[str(expected_tier)] += 1
        if expected_tier == 0:
            outside_counts[expected] += 1
    return {'standing_counts': dict(counts), 'priority_tier_counts': dict(priority_counts),
        'outside_priority_tiers': dict(outside_counts), 'observed_cohort_size': len(observed),
        'negative_cohort_size': len(negative), 'positive_cutoffs': positive_cuts, 'negative_cutoffs': negative_cuts}


def audit_document(doc, evidence):
    """Reconstruct semantic claims without importing production rank helpers.

    Kept callable independently of the production loader so negative-control
    tests prove that these comparisons themselves detect corrupted outputs.
    """
    assert doc['generation_id'] == evidence['generation_id'], 'Evidence generation mismatch'
    actions = evidence['actions']
    ids = {row['member_id']: name for name, row in doc['members'].items()}
    by_field = defaultdict(lambda: defaultdict(list))
    by_overall_event = defaultdict(lambda: defaultdict(list))
    seen_evidence = set()
    for event in actions:
        assert event['member_id'] in ids and event['category_id'] in doc['cats'], 'Unresolved event identity'
        assert event['evidence_id'] not in seen_evidence, 'Duplicate normalized evidence identity'
        seen_evidence.add(event['evidence_id'])
        assert event['congress'] in doc['policy']['congresses'], 'Out-of-window action'
        if event['kind'] == 'vote':
            assert event['cast_code'] in (1, 2, 3, 4, 5, 6), 'Vote is not an actual recorded Yea/Nay'
            assert event['position_on_measure'] == (1 if event['cast_code'] in (1, 2, 3) else -1), 'Cast code contradicts recorded position'
        by_field[event['category_id']][event['member_id']].append(event)
        by_overall_event[event['member_id']][event['overall_event_id']].append(event)
    counts, compared, tensor_blocks = {}, 0, 0
    quantum = Decimal('0.000000000001')
    ladder = doc['policy'].get('ladder', 'legacy')
    years = doc.get('years_served') or {}

    def priced(event):
        """Weight actually scored: rule G prices votes on its own published dial."""
        source = doc['policy']['vote_weight'] if (ladder == 'g' and event['kind'] == 'vote') else event['weight']
        return Decimal(source).quantize(quantum, rounding=ROUND_HALF_EVEN)

    def term(event, mid, factor):
        value = priced(event) * factor
        if ladder != 'g':
            return value * Decimal(event['decay'])
        return (value / Decimal(years[mid]).quantize(quantum, rounding=ROUND_HALF_EVEN)).quantize(
            quantum, rounding=ROUND_HALF_EVEN)

    for cat in doc['cats']:
        cohort = by_field[cat]
        size = len(cohort)
        scores, tensors, complete, missing, tensor_status = {}, {}, {}, {}, {}
        for mid, events in cohort.items():
            scores[mid] = sum(term(e, mid, Decimal(str(e['alignment']))) for e in events)
            missing[mid] = sorted({e['measure_id'] for e in events
                if not e.get('tensor_position_scoped', True) or e['measure_id'] not in evidence['tensor']
                or e.get('position_on_measure') not in (-1, 1)})
            complete[mid] = not missing[mid]
            tensor_status[mid] = 'complete' if complete[mid] else 'incomplete'
            if complete[mid]:
                tensors[mid] = sum(term(e, mid, Decimal(str(e['position_on_measure'])) * Decimal(evidence['tensor'][e['measure_id']]))
                                   for e in events)
        sides = {'positive': [], 'negative': [], 'zero': []}
        for mid in cohort:
            sides['positive' if scores[mid] > 0 else 'negative' if scores[mid] < 0 else 'zero'].append(mid)
        blocks = ((('positive', True), ('negative', False)) if ladder == 'g' else (('all', True),))
        expected_intervals, sub_cohort = {}, {}
        for side, descending in blocks:
            members = sides[side] if ladder == 'g' else list(cohort)
            for mid in members:
                sub_cohort[mid] = side
            score_groups = defaultdict(list)
            for mid in members:
                score_groups[scores[mid]].append(mid)
            next_rank = 1
            for score in sorted(score_groups, reverse=descending):
                mids = score_groups[score]
                if all(complete[m] for m in mids):
                    groups = defaultdict(list)
                    for mid in mids:
                        groups[tensors[mid]].append(mid)
                    ordered_groups = [groups[t] for t in sorted(groups, reverse=descending)]
                    tensor_blocks += len(mids) > 1
                else:
                    ordered_groups = [mids]
                    if len(mids) > 1:
                        for mid in mids:
                            tensor_status[mid] = 'tensor_unavailable_for_tie'
                for group in ordered_groups:
                    end = next_rank + len(group) - 1
                    for mid in group:
                        expected_intervals[mid] = (next_rank, end)
                    next_rank = end + 1
        for mid in sides['zero'] if ladder == 'g' else ():
            sub_cohort[mid] = 'zero'
            expected_intervals[mid] = (None, None)
        expected_cuts = {key: len(sides['positive']) * doc['policy'][key + '_pct'] // 100
                         for key in ('champion', 'ally', 'highly_persuadable', 'persuadable', 'lean_persuadable')} if ladder == 'g' else {
                         key: size * doc['policy'][key + '_pct'] // 100
                         for key in ('champion', 'ally', 'highly_persuadable')}
        if ladder == 'g':
            expected_cuts = dict(expected_cuts, positive=dict(expected_cuts), negative={
                key: len(sides['negative']) * doc['policy'][key + '_pct'] // 100
                for key in ('opponent', 'highly_skeptical', 'skeptic', 'lean_skeptic')})
        labels = Counter()
        for mid, name in ids.items():
            cell = doc['members'][name]['fields'][cat]
            events = cohort.get(mid, [])
            assert cell['evidence_ids'] == sorted(e['evidence_id'] for e in events), 'Field evidence reference mismatch'
            assert cell['action_count'] == len(events), 'Field action count mismatch'
            assert cell['positive_action_count'] == sum(e['alignment'] > 0 for e in events), 'Field positive count mismatch'
            assert cell['negative_action_count'] == sum(e['alignment'] < 0 for e in events), 'Field negative count mismatch'
            assert cell['alignment'] == _observed_alignment(events), 'Field alignment mismatch'
            assert cell['evidence_status'] == ('observed' if events else 'no_recorded_action'), 'Field evidence status mismatch'
            assert cell['cohort_size'] == size, 'Denominator mismatch'
            assert cell['cutoffs'] == expected_cuts, 'Published cutoff count mismatch'
            if ladder == 'g' and mid in cohort:
                assert cell['sub_cohort'] == sub_cohort[mid], 'Signed sub-cohort mismatch'
                assert cell['sub_cohort_size'] == len(sides[sub_cohort[mid]]), 'Signed sub-cohort denominator mismatch'
                assert cell['years_served'] == doc['years_served'][mid], 'Published service years mismatch'
                assert len(cell['field_actions']) == len(cohort[mid]), 'Itemized ledger row count mismatch'
                assert sum(Decimal(row['v']) for row in cell['field_actions']) == scores[mid], 'Itemized ledger does not total the score'
            if mid not in cohort:
                assert cell['recognition'] is None and cell['rank_start'] is None and cell['rank_end'] is None
                assert cell['raw_field_score'] is None and cell['tensor_tiebreak'] is None
                assert cell['tensor_status'] == 'not_applicable' and cell['missing_tensor_measures'] == []
                continue
            assert cell['tensor_status'] == tensor_status[mid], 'Tensor coverage/tie-block status mismatch'
            assert cell['missing_tensor_measures'] == missing[mid], 'Missing tensor measure references mismatch'
            if complete[mid]:
                expected_tensor = tensors[mid].quantize(Decimal('0.000000000001'), rounding=ROUND_HALF_EVEN)
                assert cell['tensor_tiebreak'] is not None and Decimal(cell['tensor_tiebreak']) == expected_tensor, 'Tensor tiebreak value mismatch'
            else:
                assert cell['tensor_tiebreak'] is None, 'Incomplete tensor has a fabricated tiebreak'
            start, end = expected_intervals[mid]
            assert (cell['rank_start'], cell['rank_end']) == (start, end), 'Rank order/tie mismatch: %s / %s' % (name, cat)
            assert abs(Decimal(cell['raw_field_score']) - scores[mid]) <= Decimal('0.000000000001'), 'Raw score mismatch'
            expected = None
            if ladder == 'g':
                defending = cat in doc['policy']['defensive_fields']
                if not scores[mid]:
                    expected = doc['policy']['zero_record_label']
                elif scores[mid] > 0:
                    expected = 'Lean Persuadable'
                    for key, label in (('champion', doc['policy']['defensive_top_band_label'] if defending else 'Champion'),
                                       ('ally', 'Ally'), ('highly_persuadable', 'Highly Persuadable'),
                                       ('persuadable', 'Persuadable')):
                        if 100 * end <= doc['policy'][key + '_pct'] * len(sides['positive']):
                            expected = label
                            break
                else:
                    expected = 'Lean Skeptic'
                    for key, label in (('opponent', 'Opponent'), ('highly_skeptical', 'Highly Skeptical'),
                                       ('skeptic', 'Skeptic')):
                        if 100 * end <= doc['policy'][key + '_pct'] * len(sides['negative']):
                            expected = label
                            break
            elif scores[mid] > 0 and any(e['alignment'] > 0 for e in cohort[mid]):
                for key, label in (('champion', 'Champion'), ('ally', 'Ally'), ('highly_persuadable', 'Highly Persuadable')):
                    if 100 * end <= doc['policy'][key + '_pct'] * size:
                        expected = label
                        break
            assert cell['recognition'] == expected, 'Honor cutoff mismatch'
            if expected:
                labels[expected] += 1
            compared += 1
        counts[cat] = {'eligible': size, 'awards': dict(labels)}
    crowns = [name for name, row in doc['members'].items() if row['overall']['recognition'] == 'Champion']
    assert len(crowns) == 13 and set(crowns) == set(doc['overall_board']['order'][:13])
    board_ranks = {name: index + 1 for index, name in enumerate(doc['overall_board']['order'])}
    overall_events_compared = 0
    events_by_id = {}
    for mid, name in ids.items():
        representatives = []
        for event_id, copies in by_overall_event[mid].items():
            signs = {e['alignment'] for e in copies if e['alignment']}
            assert len(signs) <= 1, 'Conflicting signed overall action directions: ' + event_id
            for key in ('member_id', 'measure_id', 'kind', 'role', 'weight', 'decay', 'congress',
                        'position_on_measure', 'tensor_position_scoped'):
                values = {Decimal(str(e[key])).quantize(Decimal('0.000000000001'), rounding=ROUND_HALF_EVEN)
                          if key in ('weight', 'decay') else e.get(key) for e in copies}
                assert len(values) == 1, 'Conflicting overall event metadata: ' + key
            # Prefer the signed reference, then the lexically first evidence ID.
            signed = [e for e in copies if e['alignment']]
            representatives.append(min(signed or copies, key=lambda e: e['evidence_id']))
        events_by_id[mid] = representatives
        cell = doc['members'][name]['overall']
        assert cell['action_count'] == len(representatives), 'Overall deduplicated action count mismatch'
        assert cell['positive_action_count'] == sum(e['alignment'] > 0 for e in representatives), 'Overall positive count mismatch'
        assert cell['negative_action_count'] == sum(e['alignment'] < 0 for e in representatives), 'Overall negative count mismatch'
        assert cell['evidence_ids'] == sorted(e['evidence_id'] for e in representatives), 'Overall evidence reference mismatch'
        assert cell['alignment'] == _observed_alignment(representatives), 'Overall observed alignment mismatch'
        assert cell['evidence_status'] == ('observed' if representatives else 'no_recorded_action'), 'Overall evidence status mismatch'
        assert cell['rank'] == board_ranks.get(name), 'Overall board rank mismatch'
        assert cell['recognition'] == ('Champion' if name in crowns else None), 'Unexpected overall recognition'
        if name in crowns:
            assert representatives, 'Overall Champion without demonstrated action'
        overall_events_compared += len(representatives)
    overall_audit = _audit_overall_standing(doc, evidence, events_by_id, ids)
    return {'schema': 'rank-honors-independent-audit-v2', 'status': 'PASS',
            'generation_id': doc['generation_id'], 'members': len(ids), 'fields': len(doc['cats']),
            'observed_cells_reconstructed': compared, 'normalized_actions': len(actions),
            'tensor_resolved_primary_blocks': tensor_blocks, 'overall_champions': crowns,
            'field_counts': counts, 'deduplicated_overall_actions_reconstructed': overall_events_compared,
            'tensor_values_and_statuses_verified': True, 'field_and_overall_evidence_summaries_verified': True,
            'overall_standing': overall_audit, 'surface_generation_markers_verified': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default=ROOT)
    parser.add_argument('--surfaces', action='store_true')
    parser.add_argument('--generation', help='Audit an immutable candidate without publishing it')
    parser.add_argument('--output')
    args = parser.parse_args()
    result = audit(args.root, args.surfaces, args.generation)
    if args.output:
        Path(args.output).write_text(json.dumps(result, indent=2), encoding='utf-8', newline='\r\n')
    print(json.dumps(result, indent=2))

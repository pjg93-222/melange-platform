"""Overall standing boundaries, deduplication, evidence status and isolation."""
from collections import Counter
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from stance.overall_stance_policy import OVERALL_POLICY, classify_overall, deduplicate_overall_actions
from stance.build_rank_honors import build_honors
from stance.honors_views import validate_honors


def roster(size):
    return {f'Member {i:03}': {'member_id': f'M{i:06d}', 'chamber': 'House'} for i in range(size)}


def action(i, score=1, *, alignment=1, measure=None, category='Field'):
    measure = measure or f'119:hr:{i + 1}'
    event = f'legislative|M{i:06d}|{measure}'
    return {'member_id': f'M{i:06d}', 'measure_id': measure, 'overall_event_id': event,
            'evidence_id': event + '|' + category, 'category_id': category,
            'congress': 119, 'kind': 'legislative', 'role': 'sponsor',
            'weight': str(score), 'decay': '1', 'alignment': alignment,
            'position_on_measure': 1, 'tensor_position_scoped': True}


def classify(actions, *, size=None, tensor=None, tiers=None, board=None, measure_primacy=None):
    people = roster(size or len({a['member_id'] for a in actions}))
    board = board or list(people)
    tiers = tiers or {name: 1 if i < 13 else 2 if i < 37 else 3 if i < 85 else 0
                      for i, name in enumerate(board)}
    return classify_overall(people, actions, tensor or {}, board, tiers, measure_primacy=measure_primacy)


class OverallStancePolicyTests(unittest.TestCase):
    def test_shared_benefit_category_disagreement_is_not_committed_cu_opposition(self):
        acts=[action(i) for i in range(100)]
        for term in (118,119):
            e=action(90,alignment=-1,measure=f'{term}:s:700')
            e['congress']=term
            acts.append(e)
        shared={'118:s:700':'0.5','119:s:700':'0.5'}
        row=classify(acts,measure_primacy=shared)['Member 090']
        self.assertEqual(row['stance'],'Lean Skeptic')
        self.assertEqual(row['opposition_evidence']['core_measures'],[])
        adverse={key:'-1' for key in shared}
        self.assertEqual(classify(acts,measure_primacy=adverse)['Member 090']['stance'],'Opponent')
        self.assertEqual(classify(acts,measure_primacy=shared,tensor=adverse)['Member 090']['stance'],'Opponent')

    def test_labels_follow_board_not_action_volume(self):
        acts = [action(i, i+1) for i in range(100)]
        result = classify(acts)
        self.assertEqual(Counter(r['stance'] for r in result.values()),
            {'Champion':13,'Ally':2,'Highly Persuadable':5,'Persuadable':30,'Lean Persuadable':25,'Uncommitted':25})
        self.assertEqual(result['Member 099']['stance'], 'Uncommitted')
        self.assertEqual(result['Member 099']['stance_rank_start'],100)
        self.assertEqual(result['Member 000']['stance'], 'Champion')

    def test_positive_volume_cannot_erase_persistent_opposing_leadership(self):
        acts = [action(i) for i in range(20)]
        old=action(19,alignment=-1,measure='118:s:1')
        old['congress']=118
        acts += [old,action(19,alignment=-1,measure='119:s:2'),action(19,10000,measure='119:hr:900')]
        row=classify(acts)['Member 019']
        self.assertGreater(float(row['raw_overall_score']),0)
        self.assertEqual(row['stance'],'Opponent')
        self.assertEqual(row['opposition_evidence']['congresses'],[118,119])
        self.assertEqual(len(row['opposition_evidence']['lead_measures']),2)

    def test_single_negative_member_can_receive_full_opposition_label(self):
        acts=[action(i) for i in range(20)]
        for term in (118,119):
            e=action(19,alignment=-1,measure=f'{term}:s:700')
            e['congress']=term
            acts.append(e)
        self.assertEqual(classify(acts)['Member 019']['stance'],'Opponent')

    def test_recent_repeated_leadership_is_highly_skeptical(self):
        acts=[action(i) for i in range(20)]
        acts += [action(19,alignment=-1,measure='119:s:700'),action(19,alignment=-1,measure='119:s:701')]
        self.assertEqual(classify(acts)['Member 019']['stance'],'Highly Skeptical')

    def test_one_original_cosponsorship_does_not_erase_strong_primacy(self):
        acts=[action(i) for i in range(20)]
        adverse=action(0,alignment=-1,measure='119:s:700')
        adverse['role']='original-cosponsor'
        acts.append(adverse)
        row=classify(acts)['Member 000']
        self.assertEqual(row['stance'],'Champion')
        self.assertTrue(row['mixed_record'])

    def test_single_opposing_vote_in_tail_is_lean_skeptic(self):
        acts=[action(i) for i in range(20)]
        adverse=action(19,alignment=-1,measure='119:s:700')
        adverse.update(kind='vote',role='vote')
        acts.append(adverse)
        self.assertEqual(classify(acts)['Member 019']['stance'],'Lean Skeptic')

    def test_absent_and_neutral_are_not_low_positive_or_opposition(self):
        acts=[action(i) for i in range(13)]+[action(13,alignment=0)]
        result=classify(acts,size=16)
        self.assertEqual(result['Member 013']['stance'],'True Neutral')
        self.assertEqual(result['Member 015']['stance'],'Insufficient evidence')
        self.assertIsNone(result['Member 015']['stance_rank_start'])

    def test_tensor_diagnostic_never_reorders_authoritative_board(self):
        acts=[action(i) for i in range(20)]
        tensors={a['measure_id']:str(i) for i,a in enumerate(acts)}
        a=classify(acts,tensor=tensors)
        b=classify(acts,tensor={})
        self.assertEqual([v['stance'] for v in a.values()],[v['stance'] for v in b.values()])
        self.assertEqual([v['rank'] for v in a.values()],list(range(1,21)))

    def test_multicategory_event_counts_once_and_selects_deterministic_signed_reference(self):
        a = action(0, category='Z', alignment=0)
        b = {**a, 'category_id': 'Y', 'evidence_id': a['overall_event_id'] + '|Y', 'alignment': 1}
        c = {**b, 'category_id': 'A', 'evidence_id': a['overall_event_id'] + '|A'}
        expected = deduplicate_overall_actions([a, b, c])
        self.assertEqual(len(expected), 1)
        self.assertEqual(expected[0]['evidence_id'], c['evidence_id'])
        self.assertEqual(expected, deduplicate_overall_actions([c, b, a]))
        self.assertEqual(deduplicate_overall_actions([a, {**b, 'alignment': 0}])[0]['evidence_id'], b['evidence_id'])

    def test_conflicting_metadata_and_nonzero_directions_fail(self):
        a = action(0)
        b = {**a, 'evidence_id': a['evidence_id'] + '|Other', 'category_id': 'Other'}
        changes = {'member_id': 'M999999', 'measure_id': '119:hr:900', 'weight': '2', 'decay': '0.8',
                   'congress': 118, 'kind': 'letter', 'role': 'cosponsor', 'position_on_measure': -1,
                   'tensor_position_scoped': False, 'alignment': -1}
        for key, value in changes.items():
            with self.assertRaises(ValueError, msg=key):
                deduplicate_overall_actions([a, {**b, key: value}])
        with self.assertRaises(ValueError):
            deduplicate_overall_actions([{k: v for k, v in a.items() if k != 'role'}])

    def test_priority_map_is_preserved_exactly_and_not_derived_from_stance(self):
        people = roster(20)
        tiers = dict.fromkeys(people, 0)
        tiers['Member 019'] = 3
        result = classify([action(i, 20 - i) for i in range(20)], tiers=tiers)
        self.assertEqual(result['Member 019']['priority_tier'], 3)
        self.assertEqual(result['Member 000']['priority_tier'], 0)
        self.assertEqual(result['Member 000']['recognition'], 'Champion')

    def test_reordered_inputs_leave_records_identical(self):
        acts = [action(i, 20 - i) for i in range(20)]
        self.assertEqual(classify(acts), classify(list(reversed(acts))))

    def test_modified_policy_is_not_accepted_under_same_version(self):
        acts = [action(i) for i in range(20)]
        people = roster(20)
        with self.assertRaises(ValueError):
            classify_overall(people, acts, {}, list(people), dict.fromkeys(people, 0), policy={**OVERALL_POLICY, 'ally_pct': 50})


class OverallStanceArtifactTests(unittest.TestCase):
    def fixture(self):
        people = roster(20)
        acts = [action(i, 20 - i) for i in range(20)]
        doc = build_honors(people, ['Field'], acts, {}, list(people), '2026-09-12')
        doc['generation_id'] = 'b' * 24
        evidence = {'schema': 'field-action-evidence-v1', 'generation_id': doc['generation_id'], 'actions': acts, 'tensor': {}}
        return doc, evidence

    def test_new_overall_standing_cannot_change_any_field_record(self):
        doc, evidence = self.fixture()
        people = roster(20)
        metadata = deepcopy(doc['overall_board'])
        metadata['priority_tiers'] = dict.fromkeys(people, 0)
        changed = build_honors(people, ['Field'], evidence['actions'], {}, list(reversed(people)), '2026-09-12',
                               board_metadata={**metadata, 'order': list(reversed(people)), 'top13': list(reversed(people))[:13]})
        self.assertEqual({n: r['fields'] for n, r in doc['members'].items()}, {n: r['fields'] for n, r in changed['members'].items()})

    def test_validator_rejects_stance_rank_tier_pattern_and_tensor_corruption(self):
        doc, evidence = self.fixture()
        validate_honors(doc, evidence)
        for key, value in (('stance', 'Ally'), ('stance', 'Opponent'), ('stance_rank_start', 20),
                           ('priority_tier', 3), ('mixed_record', True), ('record_pattern', 'opposing'),
                           ('tensor_tiebreak', '99'), ('raw_overall_score', '99'), ('opposition_rank_start', 1)):
            changed = deepcopy(doc)
            changed['members']['Member 000']['overall'][key] = value
            with self.assertRaises(ValueError, msg=key):
                validate_honors(changed, evidence)

    def test_fixed_board_bucket_contradiction_rejected_even_when_records_agree(self):
        doc, evidence = self.fixture()
        doc['overall_board']['priority_tiers']['Member 000'] = 3
        doc['members']['Member 000']['overall']['priority_tier'] = 3
        with self.assertRaisesRegex(ValueError, 'fixed active board buckets'):
            validate_honors(doc, evidence)


if __name__ == '__main__':
    unittest.main()

"""Negative controls exercise the independent audit without the core loader."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from audit.audit_rank_honors import audit_document
from stance.build_rank_honors import build_honors


def fixture(*, tied=False, missing_tensor=False, multicategory=False):
    roster = {f'Member {i:02}': {'member_id': f'M{i:06d}', 'chamber': 'House'} for i in range(20)}
    actions = []
    for i in range(20):
        event = f'legislative|M{i:06d}|119:hr:{i + 1}'
        actions.append({'member_id': f'M{i:06d}', 'category_id': 'Field', 'measure_id': f'119:hr:{i + 1}',
                        'evidence_id': event + '|Field', 'overall_event_id': event, 'congress': 119,
                        'kind': 'legislative', 'alignment': 1, 'weight': '1' if tied else str(20 - i),
                        'decay': '1', 'position_on_measure': 1, 'tensor_position_scoped': True, 'role': 'sponsor'})
    cats = ['Field']
    if multicategory:
        cats.append('Other')
        actions.append({**actions[0], 'category_id': 'Other', 'alignment': 0,
                        'evidence_id': actions[0]['overall_event_id'] + '|Other'})
    tensor = {a['measure_id']: '0.5' for a in actions}
    if missing_tensor:
        tensor.pop(actions[-1]['measure_id'])
    doc = build_honors(roster, cats, actions, tensor, list(roster), '2026-09-12')
    doc['generation_id'] = 'f' * 24
    evidence = {'generation_id': doc['generation_id'], 'actions': actions, 'tensor': tensor}
    return doc, evidence


class IndependentHonorsAuditTests(unittest.TestCase):
    def test_reconstructs_tensor_and_deduplicated_overall_summaries(self):
        doc, evidence = fixture(multicategory=True)
        result = audit_document(doc, evidence)
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['normalized_actions'], 21)
        self.assertEqual(result['deduplicated_overall_actions_reconstructed'], 20)
        self.assertTrue(result['tensor_values_and_statuses_verified'])
        self.assertFalse(result['surface_generation_markers_verified'])
        self.assertNotIn('surfaces_verified', result)

    def test_equivalent_decimal_metadata_does_not_create_an_overall_conflict(self):
        doc, evidence = fixture(multicategory=True)
        evidence['actions'][-1]['weight'] = '20.0'
        evidence['actions'][-1]['decay'] = '1.000'
        self.assertEqual(audit_document(doc, evidence)['status'], 'PASS')

    def test_rejects_wrong_field_counts_alignment_and_reference_list(self):
        doc, evidence = fixture()
        for key, value in (('action_count', 99), ('positive_action_count', 0), ('negative_action_count', 1),
                           ('alignment', 'Opposing actions'), ('evidence_ids', []), ('evidence_status', 'no_recorded_action')):
            changed = deepcopy(doc)
            changed['members']['Member 00']['fields']['Field'][key] = value
            with self.assertRaises(AssertionError, msg=key):
                audit_document(changed, evidence)

    def test_rejects_fabricated_tensor_values_and_coverage(self):
        doc, evidence = fixture()
        for key, value in (('tensor_tiebreak', '999'), ('tensor_status', 'incomplete'),
                           ('missing_tensor_measures', ['119:hr:999'])):
            changed = deepcopy(doc)
            changed['members']['Member 00']['fields']['Field'][key] = value
            with self.assertRaises(AssertionError, msg=key):
                audit_document(changed, evidence)

    def test_incomplete_member_disables_entire_primary_tie_block(self):
        doc, evidence = fixture(tied=True, missing_tensor=True)
        self.assertEqual(audit_document(doc, evidence)['status'], 'PASS')
        changed = deepcopy(doc)
        changed['members']['Member 00']['fields']['Field']['tensor_status'] = 'complete'
        with self.assertRaisesRegex(AssertionError, 'Tensor coverage'):
            audit_document(changed, evidence)

    def test_rejects_wrong_overall_counts_alignment_and_reference_list(self):
        doc, evidence = fixture(multicategory=True)
        for key, value in (('action_count', 2), ('positive_action_count', 0), ('negative_action_count', 1),
                           ('alignment', 'Opposing actions'), ('evidence_ids', []), ('rank', 20)):
            changed = deepcopy(doc)
            changed['members']['Member 00']['overall'][key] = value
            with self.assertRaises(AssertionError, msg=key):
                audit_document(changed, evidence)

    def test_conflicting_nonzero_overall_directions_rejected(self):
        doc, evidence = fixture(multicategory=True)
        evidence['actions'][-1]['alignment'] = -1
        # Update the field summary so the independent overall check is reached;
        # it must reject conflicting directions rather than selecting a name.
        other = doc['members']['Member 00']['fields']['Other']
        other.update(raw_field_score='-20', negative_action_count=1, alignment='Opposing actions')
        with self.assertRaisesRegex(AssertionError, 'Conflicting signed overall'):
            audit_document(doc, evidence)

    def test_rejects_corrupt_overall_standing_and_priority_claims(self):
        doc, evidence = fixture()
        changes = {'stance': 'Ally', 'priority_tier': 3, 'stance_rank_end': 20,
            'stance_cohort_size': 999, 'opposition_cohort_size': 999, 'raw_overall_score': '0',
            'tensor_tiebreak': '999', 'tensor_status': 'incomplete', 'mixed_record': True,
            'record_pattern': 'balanced', 'neutral_action_count': 1, 'stance_boundary_tie': True,
            'stance_cutoffs': {'ally': 99, 'highly_persuadable': 99, 'persuadable': 99},
            'opposition_cutoffs': {'opponent': 99, 'highly_skeptical': 99, 'skeptic': 99},
            'opposition_rank_start': 1}
        for key, value in changes.items():
            with self.subTest(key=key):
                changed = deepcopy(doc)
                changed['members']['Member 00']['overall'][key] = value
                with self.assertRaises(AssertionError):
                    audit_document(changed, evidence)

    def test_independent_overall_mixed_neutral_balanced_absent_and_negative_cohort(self):
        source, evidence = fixture()
        actions = evidence['actions'][:-1]  # Member19 has no evidence, outside crowns.
        actions[16]['alignment'] = -1
        actions[18]['alignment'] = 0
        for index, weight in ((0, '1'), (17, '3')):
            old = actions[index]
            actions.append({**old, 'evidence_id': old['evidence_id'] + '-negative',
                'overall_event_id': old['overall_event_id'] + '-negative', 'alignment': -1, 'weight': weight, 'role':'cosponsor'})
        doc = build_honors(source['members'], source['cats'], actions, evidence['tensor'],
                           source['overall_board']['order'], '2026-09-12')
        doc['generation_id'] = evidence['generation_id']
        evidence['actions'] = actions
        report = audit_document(doc, evidence)['overall_standing']
        self.assertEqual(report['negative_cohort_size'], 3)
        self.assertEqual(report['negative_cutoffs'], dict(opponent=None, highly_skeptical=None, skeptic=None))
        self.assertEqual(doc['members']['Member 16']['overall']['stance'], 'Skeptic')
        self.assertEqual(doc['members']['Member 17']['overall']['record_pattern'], 'balanced')
        self.assertEqual(doc['members']['Member 18']['overall']['record_pattern'], 'neutral')
        self.assertEqual(doc['members']['Member 19']['overall']['stance'], 'Insufficient evidence')
        self.assertTrue(doc['members']['Member 00']['overall']['mixed_record'])


if __name__ == '__main__':
    unittest.main()

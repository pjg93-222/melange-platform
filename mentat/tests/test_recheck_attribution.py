import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from recheck_attribution import recheck_attribution


class RecheckTests(unittest.TestCase):
    def row(self, **kwargs):
        return dict(industry='Fixture', company='Acme', contributor_employer='ACME INC',
                    entity_type='IND', company_match_status='review',
                    company_match_reason='old prefix hold', **kwargs)

    def run_bridge(self, rows, responses):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            def respond(*args, **kwargs):
                self.assertIsInstance(args[0], list)
                self.assertTrue(kwargs['check'])
                (root / 'attribution-results.json').write_text(json.dumps(responses), encoding='utf-8')
            with patch('recheck_attribution.Path.is_file', return_value=True), \
                    patch('recheck_attribution.subprocess.run', side_effect=respond):
                return recheck_attribution(rows, root/'source.db', root)

    def test_exact_recheck_preserves_previous_decision_and_records_its_evidence(self):
        rows = [self.row(sub_id='one'), self.row(sub_id='two')]
        result = self.run_bridge(rows, [{'request_index': 0, 'status': 'accepted', 'reason': 'squashed_form_exact'}])
        self.assertEqual(result['unique_inputs'], 1)
        self.assertEqual(result['resolved_records'], 2)
        self.assertEqual(rows[0]['attribution_resolution']['previous_reason'], 'old prefix hold')
        self.assertEqual(rows[0]['attribution_resolution']['evidence_artifact'], 'attribution-results.json')
        self.assertEqual(rows[0]['company_match_status'], 'accepted')

    def test_prefix_acceptance_is_not_enough_to_release_historical_hold(self):
        for status, reason in [('accepted', 'corporate_qualifier_prefix'),
                               ('accepted', 'squashed_form_corporate_qualifier'),
                               ('rejected', 'persistent_override:reject'),
                               ('review', 'ambiguous_brand_outside_home_state')]:
            rows = [self.row()]
            result = self.run_bridge(rows, [{'request_index': 0, 'status': status, 'reason': reason}])
            self.assertEqual(result['resolved_records'], 0)
            self.assertEqual(rows[0]['company_match_status'], 'review')

    def test_existing_rejection_exclusion_conflict_and_nonindividual_are_not_reopened(self):
        for update in [{'company_match_status':'rejected','company_match_reason':'persistent_override:reject'},
                       {'company_match_status':'excluded','company_match_reason':'adjudicated:unrelated'},
                       {'excluded_reason':'manual policy'}, {'source_conflicts':['mismatch']},
                       {'entity_type':'ORG'}]:
            row = {**self.row(), **update}
            with patch('recheck_attribution.subprocess.run', side_effect=AssertionError('Must not call R')):
                result = recheck_attribution([row], 'unused.db', 'unused')
            self.assertEqual(result['eligible_records'], 0)
            self.assertNotIn('attribution_resolution', row)

    def test_heuristic_rejection_can_be_resolved_by_an_exact_approved_alias(self):
        row={**self.row(),'company_match_status':'rejected','company_match_reason':'employer_prefix_not_verified'}
        result=self.run_bridge([row],[{'request_index':0,'status':'accepted','reason':'verified_employer_alias'}])
        self.assertEqual(result['resolved_records'],1)
        self.assertEqual(row['attribution_resolution']['previous_status'],'rejected')

    def test_result_id_validation_precedes_any_attribution_mutation(self):
        rows = [self.row(), self.row(contributor_name='Another')]
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            self.run_bridge(rows, [{'request_index': 0, 'status':'accepted', 'reason':'verified_roster_alias'},
                                   {'request_index': 0, 'status':'accepted', 'reason':'verified_roster_alias'}])
        self.assertTrue(all(row['company_match_status'] == 'review' for row in rows))

    def test_exact_response_with_different_employer_cannot_release_stored_hold(self):
        row = self.row(sub_id='1')
        with patch('recheck_attribution.subprocess.run', side_effect=AssertionError('Must not call R')):
            result = recheck_attribution([row], 'unused.db', 'unused', {
                '1': {**row, 'contributor_employer': 'UNRELATED EMPLOYER'}})
        self.assertEqual(result['conflicting_match_inputs'], 1)
        self.assertEqual(result['resolved_records'], 0)
        self.assertEqual(row['company_match_status'], 'review')

    def test_verified_alias_source_is_preserved_in_resolution_trace(self):
        row = self.row()
        result = self.run_bridge([row], [{'request_index': 0, 'status': 'accepted',
            'reason': 'verified_employer_alias', 'alias_evidence': {
                'alias': 'Acme Inc', 'source_url': 'https://example.test/acme',
                'evidence_note': 'Official company relationship.'}}])
        self.assertEqual(result['resolved_records'], 1)
        self.assertEqual(row['attribution_resolution']['alias_evidence']['source_url'],
                         'https://example.test/acme')


if __name__ == '__main__':
    unittest.main()

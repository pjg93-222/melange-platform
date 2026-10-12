"""Fail-closed subtraction of stale claims during a profile evidence refresh."""
import unittest
from fec.members.regenerate_member_profiles import omit_unsupported_cross_party, omit_unsupported_defense
from fec.members.revise_member_narratives_v2 import headed_text, mechanical_issues


class ProfileEvidenceRefreshTests(unittest.TestCase):
    def test_current_gate_retains_all_bytes(self):
        row = {'p1': 'Public record.', 'p2': 'She defends her position repeatedly.  Next observation.', 'p3': 'Advice.'}
        result, omitted = omit_unsupported_defense(row, {'speech_profile': {'repeated_defensive_reactions': {'count': 3}}})
        self.assertEqual(result, row)
        self.assertFalse(omitted)

    def test_closed_gate_removes_claim_and_dependent_advice(self):
        row = {'p1': 'Public record.', 'p2': 'Brief answers. She pushes back repeatedly. Next observation.',
               'p3': 'Given the floor defense pattern, lead with facts. Retained advice.'}
        result, omitted = omit_unsupported_defense(row, {'speech_profile': {}})
        self.assertEqual(result, {'p1': row['p1'], 'p2': 'Brief answers. Next observation.', 'p3': 'Retained advice.'})
        self.assertEqual(len(omitted), 2)
        self.assertIn('pushes back', omitted[0]['text'])
        self.assertIn('pushes back', row['p2'])
        self.assertNotIn('defensive language appears without the repeated-defensive evidence gate',
                         mechanical_issues(headed_text(result), 'Member', {'speech_profile': {}}))

    def test_unaffected_wording_and_spacing_are_unchanged(self):
        row = {'p1': 'Original.  Spacing.', 'p2': 'Original wording.', 'p3': 'Original advice.'}
        self.assertEqual(omit_unsupported_defense(row, {}), (row, []))

    def test_no_punctuation_claim_cannot_escape(self):
        with self.assertRaisesRegex(ValueError, 'empty the paragraph'):
            omit_unsupported_defense({'p1': 'A.', 'p2': 'Repeated pushback', 'p3': 'B.'}, {})

    def test_unrelated_invalid_claim_still_fails(self):
        row = {'p1': 'Public record.', 'p2': 'She is emotionally stable. She defends her position. Other observation.', 'p3': 'Advice.'}
        result, _ = omit_unsupported_defense(row, {})
        self.assertTrue(any('private-state' in issue for issue in mechanical_issues(headed_text(result), 'Member', {})))


    def test_cross_party_claims_drop_only_when_the_measure_is_absent(self):
        row = {'p1': 'Visible. He works across the aisle often. The coalition-building is real.', 'p2': 'Floor.', 'p3': 'Bring a frame for the other party. Lead with people.'}
        covered = {'behavior': {'cross_party_cooperation': {'label': 'above'}}}
        self.assertEqual(omit_unsupported_cross_party(row, covered), (row, []))
        result, omitted = omit_unsupported_cross_party(row, {})
        self.assertEqual(result, {'p1': 'Visible.', 'p2': 'Floor.', 'p3': 'Lead with people.'})
        self.assertEqual(len(omitted), 3)
        self.assertNotIn('cross-party subject appears although the measure is absent', mechanical_issues(headed_text(result), 'Member', {}))


if __name__ == '__main__':
    unittest.main()

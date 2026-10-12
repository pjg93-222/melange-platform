"""Temporal, semantic and missingness controls for proposed political inputs."""
import copy
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('political_features', Path(__file__).with_name('political_features.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def relation(member='A', party='D', kind='bill_alignment'):
    return dict(member_id=member, party=party, relation=kind, sector='commercial_banks', chamber='House',
                scope_id='broad_bank_context', effective_from='2017-01-01', effective_to='',
                source_available_on='2017-02-01', source_ref='evidence:' + member + ':' + kind)


def score(member='A', party='D', value=-.5):
    return dict(member_id=member, party=party, chamber='House', dw_nominate_dim1=value,
                effective_from='2017-01-01', effective_to='', source_available_on='2017-12-01',
                votes_through='2017-11-30', vintage_basis='archived_as_of', source_ref='archive:' + member,
                score_vintage_id='synthetic-release-20171201', score_congress=115)


def build(relations, scores, **overrides):
    args = dict(sector='commercial_banks', chamber='House', issue='2018-01-31',
                target_relation='bill_alignment', scope_id='broad_bank_context',
                source_universe_complete={'target_relations': True, 'champions': True, 'ideology_reference': True})
    args.update(overrides)
    return m.build_features(relations, scores, **args)


class PoliticalFeatureTests(unittest.TestCase):
    def test_equal_member_lean_and_bimodal_ideology(self):
        rels = [relation(a, p, k) for a, p in [('A', 'D'), ('B', 'R')] for k in ('bill_alignment', 'champion')]
        out = build(rels, [score(), score('B', 'R', .5)])
        self.assertEqual(out['target_composition']['republican_share_of_known_two_party'], .5)
        self.assertEqual(out['champion_dw_nominate_mean'], 0)
        self.assertEqual(out['champion_dw_nominate_dispersion'], .5)
        self.assertEqual(out['champion_within_party_ideology_mean'], 0)
        self.assertTrue(out['complete_primary_features'])

    def test_repeated_bills_do_not_multiply_member_weight(self):
        out = build([relation()] * 20 + [relation('B', 'R')], [])
        self.assertEqual(out['target_composition']['member_count'], 2)
        self.assertEqual(out['target_composition']['republican_share_of_known_two_party'], .5)

    def test_future_labels_and_publication_do_not_enter(self):
        rels = [relation(), dict(relation('B', 'R'), source_available_on='2020-01-01'),
                dict(relation('C', 'R'), effective_from='2019-01-01')]
        out = build(rels, [])
        self.assertEqual(out['target_composition']['member_count'], 1)
        self.assertEqual(out['target_composition']['republican_share_of_known_two_party'], 0)

    def test_current_revision_and_future_votes_are_not_backdated(self):
        for replacement in ({'vintage_basis': 'current_revised_snapshot'}, {'votes_through': '2024-12-31'},
                            {'source_available_on': '2026-10-06'}):
            out = build([relation(kind='champion')], [dict(score(), **replacement)])
            self.assertIsNone(out['champion_dw_nominate_mean'])
            self.assertFalse(out['complete_primary_features'])

    def test_missing_is_not_neutral(self):
        out = build([], [])
        self.assertIsNone(out['champion_dw_nominate_mean'])
        self.assertIsNone(out['target_composition']['republican_share_of_known_two_party'])
        self.assertFalse(out['complete_primary_features'])

    def test_contact_and_bill_relation_are_not_interchanged(self):
        out = build([relation()], [], target_relation='direct_contact')
        self.assertEqual(out['target_composition']['member_count'], 0)

    def test_source_coverage_not_inferred_from_present_rows(self):
        out = build([relation(), relation(kind='champion')], [score()],
                    source_universe_complete={'target_relations': True, 'champions': False, 'ideology_reference': True})
        self.assertIsNotNone(out['champion_dw_nominate_mean'])
        self.assertFalse(out['complete_primary_features'])

    def test_conflicting_party_or_same_vintage_scores_refused(self):
        with self.assertRaises(m.FeatureError):
            build([relation(), relation(party='R')], [])
        with self.assertRaises(m.FeatureError):
            build([], [score(), score(value=.7)])

    def test_exact_scope_and_chamber_do_not_leak(self):
        out = build([dict(relation(), scope_id='credit_union'), dict(relation(), chamber='Senate')], [])
        self.assertEqual(out['target_composition']['member_count'], 0)

    def test_unknown_party_and_source_availability_stay_explicit(self):
        out = build([relation(party='Unknown'), dict(relation('B'), source_available_on='')], [])
        self.assertEqual(out['target_composition']['party_counts']['Unknown'], 1)
        self.assertIsNone(out['target_composition']['republican_share_of_known_two_party'])
        self.assertEqual(out['exclusions']['target_relations']['unknown_source_availability'], 1)

    def test_no_input_mutation(self):
        rels, scores = [relation(), relation(kind='champion')], [score()]
        expected = copy.deepcopy((rels, scores))
        build(rels, scores)
        self.assertEqual((rels, scores), expected)

    def test_mixed_ideological_scales_cannot_supply_party_reference(self):
        with self.assertRaises(m.FeatureError):
            build([], [score(), dict(score('B', 'R', .5), score_vintage_id='different-release')])
        with self.assertRaises(m.FeatureError):
            build([], [score(), dict(score('B', 'R', .5), score_congress=114)])

    def test_unknown_eligibility_blocks_readiness_despite_complete_subset(self):
        base = [relation(), relation(kind='champion')]
        out = build(base, [score()])
        self.assertTrue(out['complete_primary_features'])
        self.assertIsNone(out['champion_dw_nominate_dispersion'])
        for extra in (dict(relation('B', 'R'), source_available_on=''),
                      dict(relation('B', 'R', 'champion'), source_ref='')):
            out = build(base + [extra], [score()])
            self.assertIsNotNone(out['champion_dw_nominate_mean'])
            self.assertFalse(out['complete_primary_features'])

    def test_same_member_vintage_conflict_and_impossible_congress_refused(self):
        with self.assertRaises(m.FeatureError):
            build([], [score(), dict(score(), score_vintage_id='different-release')])
        for congress in (0, 119):
            with self.assertRaises(m.FeatureError):
                build([], [dict(score(), score_congress=congress)])


if __name__ == '__main__':
    unittest.main()

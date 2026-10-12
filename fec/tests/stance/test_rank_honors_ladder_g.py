"""Rule G: the full Champion-to-Opponent field gauntlet, and its legacy rollback.

Mechanics run against fixtures built with build_honors. The named roster anchors
(Bean, Davis, Kamlager-Dove, the Credit Card Competition Act sponsors) assert
against whichever generation is published, and skip while the published one
still uses the legacy ladder, so they become live the moment the chain
republishes under HONORS_LADDER=g.
"""
from __future__ import annotations

from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys
import unittest

FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))
from paths import ROOT
from stance.rank_honors_policy import (LADDER_G, POLICY, SCHEMA, SCHEMA_LADDER_G, classify_field,
                                       field_score, rank_cutoffs)
from stance.build_rank_honors import build_honors, serialized
from stance.honors_views import load_generation, validate_honors

G_POLICY = {**POLICY, **LADDER_G}
FIELD = 'Member Business Lending'
DEFEND = 'Credit Card Mandates'


def action(index, weight='1', *, alignment=1, kind='legislative', role='sponsor',
           category=FIELD, measure=None, congress=119, position=1):
    bio = 'M%06d' % index
    measure = measure or '119:hr:%d' % (index + 1)
    base = '%s|%s|%s' % (kind, bio, measure)
    return {'member_id': bio, 'category_id': category, 'measure_id': measure,
            'evidence_id': base + '|' + category, 'overall_event_id': base,
            'weight': weight, 'decay': '1', 'alignment': alignment,
            'position_on_measure': position, 'tensor_position_scoped': True,
            'kind': kind, 'congress': congress, 'role': role}


def classify(actions, ids=None, *, policy=G_POLICY, years=None, category=FIELD):
    ids = ids or sorted({a['member_id'] for a in actions})
    years = years if years is not None else {mid: '1' for mid in ids}
    return classify_field(ids, actions, {}, policy=policy, years=years, category=category)


_PUBLISHED = []


def published():
    """The live generation, validated once per run (full re-derivation is slow)."""
    if not _PUBLISHED:
        base = Path(ROOT) / 'data/stance/rank_honors'
        pointer = json.loads((base / 'active.json').read_text(encoding='utf-8'))
        generation = pointer['generation_id']
        _PUBLISHED.append(load_generation(base / generation, generation, pointer['files_sha256']))
    return _PUBLISHED[0]


def rule_g_generation():
    doc = published()
    return doc if doc['policy'].get('ladder') == 'g' else None


class LadderMechanicsTests(unittest.TestCase):
    def test_positive_and_negative_records_rank_in_their_own_cohort(self):
        actions = [action(i, str(20 - i)) for i in range(20)]
        actions += [action(100 + i, str(i + 1), alignment=-1) for i in range(10)]
        rows = classify(actions)
        positive = [r for r in rows.values() if r['sub_cohort'] == 'positive']
        negative = [r for r in rows.values() if r['sub_cohort'] == 'negative']
        self.assertEqual((len(positive), len(negative)), (20, 10))
        self.assertTrue(all(r['sub_cohort_size'] == 20 for r in positive))
        self.assertTrue(all(r['sub_cohort_size'] == 10 for r in negative))
        self.assertEqual(sorted(r['rank_start'] for r in positive), list(range(1, 21)))
        self.assertEqual(sorted(r['rank_start'] for r in negative), list(range(1, 11)))
        self.assertTrue(all(r['cohort_size'] == 30 for r in rows.values()))
        # extremity first on both sides
        self.assertEqual(rows['M000000']['rank_start'], 1)
        self.assertEqual(rows['M000109']['rank_start'], 1)

    def test_every_band_of_the_gauntlet_is_reachable(self):
        actions = [action(i, str(1000 - i)) for i in range(100)]
        actions += [action(200 + i, str(i + 1), alignment=-1) for i in range(100)]
        rows = classify(actions)
        labels = {row['recognition'] for row in rows.values()}
        self.assertEqual(labels, {'Champion', 'Ally', 'Highly Persuadable', 'Persuadable',
                                  'Lean Persuadable', 'Lean Skeptic', 'Skeptic',
                                  'Highly Skeptical', 'Opponent'})
        counts = {label: sum(r['recognition'] == label for r in rows.values()) for label in labels}
        self.assertEqual(counts['Champion'], 5)
        self.assertEqual(counts['Ally'], 10)
        self.assertEqual(counts['Highly Persuadable'], 5)
        self.assertEqual(counts['Persuadable'], 30)
        self.assertEqual(counts['Lean Persuadable'], 50)
        self.assertEqual(counts['Opponent'], 5)
        self.assertEqual(counts['Highly Skeptical'], 10)
        self.assertEqual(counts['Skeptic'], 15)
        self.assertEqual(counts['Lean Skeptic'], 70)

    def test_zero_net_with_evidence_is_true_neutral_and_unranked(self):
        rows = classify([action(1), action(1, alignment=-1, measure='119:hr:99')], ['M000001'])
        row = rows['M000001']
        self.assertEqual(row['recognition'], 'True Neutral')
        self.assertEqual(row['reason'], 'zero_net_with_evidence')
        self.assertEqual((row['rank_start'], row['rank_end']), (None, None))
        self.assertEqual(row['sub_cohort'], 'zero')
        self.assertEqual(row['evidence_status'], 'observed')

    def test_no_label_only_without_evidence(self):
        rows = classify([action(1)], ['M000001', 'M000002'])
        # a one-member positive cohort has a floor-division cutoff of 0, so the
        # catch-all band takes it: labelled, but nowhere near the top.
        self.assertEqual(rows['M000001']['recognition'], 'Lean Persuadable')
        self.assertIsNone(rows['M000002']['recognition'])
        self.assertEqual(rows['M000002']['reason'], 'no_recorded_action')
        unlabelled = [r for r in rows.values() if r['recognition'] is None]
        no_evidence = [r for r in rows.values() if r['action_count'] == 0]
        self.assertEqual(len(unlabelled), len(no_evidence))

    def test_defensive_field_renames_only_its_top_band(self):
        actions = [action(i, str(100 - i), category=DEFEND) for i in range(40)]
        defend = classify(actions, category=DEFEND)
        plain = classify([{**a, 'category_id': FIELD,
                           'evidence_id': a['evidence_id'].replace(DEFEND, FIELD)} for a in actions],
                         category=FIELD)
        self.assertEqual(defend['M000000']['recognition'], 'Defender')
        self.assertEqual(plain['M000000']['recognition'], 'Champion')
        # only the top band is renamed; every lower band is identical
        self.assertEqual(defend['M000005']['recognition'], plain['M000005']['recognition'])
        self.assertEqual(defend['M000005']['recognition'], 'Ally')
        self.assertNotIn('Champion', {r['recognition'] for r in defend.values()})

    def test_score_is_a_signed_per_service_year_rate_without_decay(self):
        actions = [action(1, '3'), action(1, '1', measure='118:hr:7', congress=118),
                   action(1, '1', kind='vote', role='vote', measure='119:hr:9')]
        actions = [dict(a, decay='0.64') for a in actions]
        rows = classify(actions, ['M000001'], years={'M000001': '2'})
        # 3 + 1 + 0.4 (the vote dial) over two years of service; decay ignored.
        self.assertEqual(rows['M000001']['raw_field_score'], '2.2')
        self.assertEqual(rows['M000001']['years_served'], '2')
        legacy = classify_field(['M000001'], actions, {}, policy=POLICY)
        self.assertEqual(legacy['M000001']['raw_field_score'], '3.2')
        self.assertNotIn('years_served', legacy['M000001'])

    def test_service_years_are_required_and_clamped(self):
        with self.assertRaisesRegex(ValueError, 'service years'):
            classify_field(['M000001'], [action(1)], {}, policy=G_POLICY, years=None)
        with self.assertRaisesRegex(ValueError, 'clamp'):
            classify([action(1)], ['M000001'], years={'M000001': '0.25'})
        with self.assertRaisesRegex(ValueError, 'clamp'):
            classify([action(1)], ['M000001'], years={'M000001': '6.5'})

    def test_itemized_ledger_totals_the_published_score(self):
        actions = [action(1, '3'), action(1, '2', measure='118:hr:4', congress=118),
                   action(1, '1', kind='vote', role='vote', measure='117:hr:5',
                          congress=117, alignment=-1, position=-1)]
        row = classify(actions, ['M000001'], years={'M000001': '3'})['M000001']
        self.assertEqual(len(row['field_actions']), 3)
        self.assertEqual(sum(Decimal(a['v']) for a in row['field_actions']),
                         Decimal(row['raw_field_score']))
        self.assertEqual([a['c'] for a in row['field_actions']], [119, 118, 117])
        vote = row['field_actions'][-1]
        self.assertEqual((vote['k'], vote['r'], vote['d']), ('vote', 'Nay', -1))
        self.assertEqual(row['field_actions'][0]['r'], 'sponsor')

    def test_cutoff_bands_must_stay_strictly_increasing(self):
        self.assertEqual(rank_cutoffs(100, G_POLICY, side='positive'),
                         {'champion': 5, 'ally': 15, 'highly_persuadable': 20,
                          'persuadable': 50, 'lean_persuadable': 100})
        self.assertEqual(rank_cutoffs(100, G_POLICY, side='negative'),
                         {'opponent': 5, 'highly_skeptical': 15, 'skeptic': 30, 'lean_skeptic': 100})
        self.assertEqual(rank_cutoffs(100, POLICY), {'champion': 5, 'ally': 15, 'highly_persuadable': 20})
        for bad in ({'persuadable_pct': 15}, {'skeptic_pct': 15}, {'lean_persuadable_pct': 101}):
            with self.assertRaises(ValueError):
                rank_cutoffs(100, {**G_POLICY, **bad}, side='positive' if 'skeptic_pct' not in bad else 'negative')


class GenerationShapeTests(unittest.TestCase):
    def fixture(self, policy):
        roster = {'Member %02d' % i: {'member_id': 'M%06d' % i, 'chamber': 'House' if i % 2 else 'Senate'}
                  for i in range(20)}
        actions = [action(i, str(20 - i)) for i in range(20)]
        years = {'M%06d' % i: '2' for i in range(20)}
        titles = {a['measure_id']: 'Measure ' + a['measure_id'] for a in actions}
        return build_honors(roster, [FIELD], actions, {}, list(roster), '2026-09-16',
                            policy=policy, years=years, measure_titles=titles,
                            board_metadata={'mode': 'factor', 'generation': 'fixture',
                                            'top13': list(roster)[:13], 'order': list(roster)})

    def test_rule_g_document_publishes_its_denominators_and_titles(self):
        doc = self.fixture(G_POLICY)
        self.assertEqual(doc['schema'], SCHEMA_LADDER_G)
        self.assertEqual(len(doc['years_served']), 20)
        self.assertEqual(len(doc['measure_titles']), 20)
        doc['generation_id'] = 'a' * 24
        evidence = {'schema': 'field-action-evidence-v1', 'generation_id': doc['generation_id'],
                    'actions': [action(i, str(20 - i)) for i in range(20)], 'tensor': {}}
        validate_honors(doc, evidence)

    def test_legacy_dial_keeps_todays_document_shape(self):
        doc = self.fixture(POLICY)
        self.assertEqual(doc['schema'], SCHEMA)
        self.assertNotIn('years_served', doc)
        self.assertNotIn('measure_titles', doc)
        self.assertEqual(doc['policy'], POLICY)
        cell = doc['members']['Member 00']['fields'][FIELD]
        for key in ('field_actions', 'sub_cohort', 'sub_cohort_size', 'years_served'):
            self.assertNotIn(key, cell)
        self.assertEqual(set(cell['cutoffs']), {'champion', 'ally', 'highly_persuadable'})
        self.assertEqual(serialized(doc), serialized(self.fixture(POLICY)))

    def test_legacy_ladder_still_reproduces_the_published_generation(self):
        """The strongest legacy pin available: the live generation was produced
        by the pre-rule-G code, and validate_honors re-derives every field and
        overall record from its evidence with today's code."""
        doc = published()
        self.assertIsNotNone(doc)
        if doc['policy'].get('ladder') == 'g':
            self.skipTest('the published generation already uses rule G')
        self.assertEqual(doc['schema'], SCHEMA)
        self.assertEqual(doc['policy'], POLICY)


class PublishedRosterAnchorTests(unittest.TestCase):
    """Named anchors from the ruling, asserted once rule G is the published ladder."""

    def setUp(self):
        self.doc = rule_g_generation()
        if self.doc is None:
            self.skipTest('published generation still uses the legacy ladder')
        self.by_id = {row['member_id']: name for name, row in self.doc['members'].items()}

    def cell(self, bioguide, category):
        return self.doc['members'][self.by_id[bioguide]]['fields'][category]

    def test_bean_member_business_lending_is_persuadable(self):
        row = self.cell('B001314', 'Member Business Lending')
        self.assertEqual(row['recognition'], 'Persuadable')
        self.assertEqual(row['sub_cohort'], 'positive')
        self.assertEqual(sum(Decimal(a['v']) for a in row['field_actions']),
                         Decimal(row['raw_field_score']))

    def test_davis_has_no_champion_cell(self):
        member = self.doc['members'][self.by_id['D000230']]
        self.assertNotIn(member['overall']['recognition'], ('Champion',))
        for category in self.doc['cats']:
            self.assertNotIn(member['fields'][category]['recognition'], ('Champion', 'Defender'))

    def test_kamlager_dove_is_labelled_wherever_she_has_evidence(self):
        member = self.doc['members'][self.by_id['K000400']]
        labelled = [c for c in self.doc['cats'] if member['fields'][c]['recognition']]
        evidenced = [c for c in self.doc['cats'] if member['fields'][c]['action_count']]
        self.assertEqual(sorted(labelled), sorted(evidenced))
        negative = [c for c in self.doc['cats'] if member['fields'][c]['negative_action_count']]
        self.assertTrue(negative, 'D7 gives her signed Nay evidence')

    def test_no_label_count_equals_no_evidence_count(self):
        unlabelled = sum(1 for m in self.doc['members'].values() for c in self.doc['cats']
                         if m['fields'][c]['recognition'] is None)
        no_evidence = sum(1 for m in self.doc['members'].values() for c in self.doc['cats']
                          if m['fields'][c]['action_count'] == 0)
        self.assertEqual(unlabelled, no_evidence)
        everywhere = sum(1 for m in self.doc['members'].values()
                         if all(m['fields'][c]['recognition'] is None for c in self.doc['cats']))
        self.assertEqual(everywhere, 0)

    def test_credit_card_competition_act_sponsors_are_negative(self):
        for bioguide in ('G000589', 'D000563'):
            row = self.cell(bioguide, 'Interchange')
            self.assertLess(Decimal(row['raw_field_score']), 0)
            self.assertIn(row['recognition'], ('Skeptic', 'Highly Skeptical', 'Opponent', 'Lean Skeptic'))
            self.assertEqual(row['sub_cohort'], 'negative')

    def test_defender_appears_only_in_the_defend_fields(self):
        defending = set(self.doc['policy']['defensive_fields'])
        for member in self.doc['members'].values():
            for category in self.doc['cats']:
                if member['fields'][category]['recognition'] == 'Defender':
                    self.assertIn(category, defending)
                if category in defending:
                    self.assertNotEqual(member['fields'][category]['recognition'], 'Champion')


if __name__ == '__main__':
    unittest.main()

"""D7: the honors lane admits the analytic producer's own Yea/Nay cast codes.

Before this, the honors gate admitted cast codes (1, 4) while the accepted
roll-call CSV writes 6 for Nay and never writes 4, so every Nay was dropped and
no vote action in any published generation carried a negative alignment.
"""
from __future__ import annotations

from collections import Counter
from datetime import date
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))
from paths import ROOT
from stance.build_field_action_evidence import (LEGACY_CAST_CODES, vote_actions, vote_cast_codes,
                                                vote_direction)
from stance.honors_views import load_generation

PRODUCER = FEC / 'legislation/build_vote_category.py'


class Source:
    """Only the reads vote_actions makes; the producer source is the real file."""

    def __init__(self, rows):
        self._rows = rows

    def json(self, path):
        return {'schema': 'vote-category-refresh-v1', 'status': 'PASS',
                'outputs_sha256': {'cu_rollcall_votes_clean.csv': 'ok', 'vote_category_lean.json': 'ok'}}

    def digest(self, path):
        return 'ok'

    def rows(self, path):
        return self._rows

    def read(self, path):
        return PRODUCER.read_bytes()


def rollcall(index, code, vote, *, congress='119', roll=None):
    return {'congress': congress, 'chamber': 'House', 'rollnumber': str(roll or index + 1),
            'bioguide': 'M%06d' % index, 'date': '2026-01-01', 'vote_question': 'On Passage',
            'bill_number': 'H.R.1', 'canonical_key': '119:hr:1', 'cast_code': str(code), 'vote': vote}


def run(rows, mapping, dial='honors'):
    roster = {str(i): {'member_id': 'M%06d' % i} for i in range(12)}
    counts = Counter()
    with patch.dict(os.environ, {'VOTE_DIRECTION': dial}):
        with patch('stance.build_field_action_evidence._vote_mapping', return_value=mapping):
            actions = vote_actions(Source(rows), roster, ['CFPB', 'Interchange'], {}, date(2026, 9, 12), counts)
    return actions, counts


class CastCodeAdmissionTests(unittest.TestCase):
    def test_cast_codes_are_lifted_from_the_analytic_producer(self):
        yea, nay = vote_cast_codes(Source([]))
        self.assertEqual((sorted(yea), sorted(nay)), ([1, 2, 3], [4, 5, 6]))
        source = PRODUCER.read_text(encoding='utf-8-sig')
        self.assertIn("vote = 'Yea' if code in (1, 2, 3) else 'Nay' if code in (4, 5, 6)", source)

    def test_legacy_dial_restores_the_historical_gate(self):
        with patch.dict(os.environ, {'VOTE_DIRECTION': 'legacy'}):
            self.assertEqual(vote_cast_codes(None), LEGACY_CAST_CODES)
            self.assertEqual(vote_cast_codes(None), ({1}, {4}))

    def test_unknown_dial_fails_closed(self):
        with patch.dict(os.environ, {'VOTE_DIRECTION': 'whatever'}):
            with self.assertRaisesRegex(ValueError, 'Unknown VOTE_DIRECTION'):
                vote_cast_codes(Source([]))

    def test_nay_cast_code_6_is_admitted_and_signed(self):
        rows = [rollcall(0, 1, 'Yea'), rollcall(1, 6, 'Nay')]
        actions, counts = run(rows, {('119:hr:1', 'CFPB'): 1})
        self.assertEqual(len(actions), 2)
        by_member = {a['member_id']: a for a in actions}
        self.assertEqual(by_member['M000000']['position_on_measure'], 1)
        self.assertEqual(by_member['M000001']['position_on_measure'], -1)
        self.assertEqual(by_member['M000000']['alignment'], 1)
        self.assertEqual(by_member['M000001']['alignment'], -1)
        self.assertEqual(counts['vote_cast_position:nay'], 1)

    def test_the_same_nay_is_silently_dropped_under_the_legacy_dial(self):
        rows = [rollcall(0, 1, 'Yea'), rollcall(1, 6, 'Nay')]
        actions, counts = run(rows, {('119:hr:1', 'CFPB'): 1}, dial='legacy')
        self.assertEqual([a['member_id'] for a in actions], ['M000000'])
        self.assertEqual(counts['vote_non_actual_cast:6'], 1)
        self.assertEqual(sum(a['alignment'] < 0 for a in actions), 0)

    def test_negative_vote_actions_exist_under_honors_and_never_under_legacy(self):
        rows = [rollcall(i, 6, 'Nay') for i in range(5)]
        honors, _ = run(rows, {('119:hr:1', 'CFPB'): 1})
        legacy, _ = run(rows, {('119:hr:1', 'CFPB'): 1}, dial='legacy')
        self.assertEqual(sum(a['alignment'] < 0 for a in honors), 5)
        self.assertEqual(sum(a['alignment'] < 0 for a in legacy), 0)
        self.assertEqual(len(legacy), 0)

    def test_yea_on_an_anti_category_is_negative(self):
        rows = [rollcall(0, 1, 'Yea')]
        actions, _ = run(rows, {('119:hr:1', 'Interchange'): -1})
        self.assertEqual(actions[0]['alignment'], -1)
        self.assertEqual(actions[0]['position_on_measure'], 1)

    def test_cast_code_contradicting_its_vote_text_is_fatal(self):
        with self.assertRaisesRegex(ValueError, 'Cast code contradicts vote text'):
            run([rollcall(0, 6, 'Yea')], {('119:hr:1', 'CFPB'): 1})

    def test_present_and_not_voting_are_still_excluded(self):
        rows = [rollcall(0, 7, 'Present'), rollcall(1, 9, 'NotVoting')]
        actions, counts = run(rows, {('119:hr:1', 'CFPB'): 1})
        self.assertEqual(actions, [])
        self.assertEqual(counts['vote_non_actual_cast:7'], 1)
        self.assertEqual(counts['vote_non_actual_cast:9'], 1)


class DirectionAgreementTests(unittest.TestCase):
    def test_honors_and_analytic_lanes_agree_on_every_direction_branch(self):
        anti = {4, 14}
        admitted = {('119:hr:1', 4): 1, ('119:hr:2', 13): 0, ('119:hr:3', 5): -1}
        cfpb = {'119:hr:4': 1, '119:hr:5': -1, '119:hr:6': 0}
        keys = ['119:hr:%d' % i for i in range(1, 8)]
        for key in keys:
            for category in (1, 4, 5, 13, 14, 16):
                honors = vote_direction(key, category, admitted, cfpb, anti)
                # the analytic producer's own expression, build_vote_category.py:201
                analytic = admitted.get((key, category),
                                        cfpb.get(key, 0) if category == 13 else -1 if category in anti else 1)
                if honors is None:
                    self.assertFalse(analytic, (key, category))
                else:
                    self.assertEqual(honors, analytic, (key, category))


class PublishedGenerationTests(unittest.TestCase):
    def test_published_generation_carries_signed_vote_evidence(self):
        base = Path(ROOT) / 'data/stance/rank_honors'
        pointer = json.loads((base / 'active.json').read_text(encoding='utf-8'))
        generation = pointer['generation_id']
        doc = load_generation(base / generation, generation, pointer['files_sha256'])
        evidence = json.loads((base / generation / 'field_action_evidence.json').read_text(encoding='utf-8'))
        votes = [a for a in evidence['actions'] if a['kind'] == 'vote']
        negative = sum(a['alignment'] < 0 for a in votes)
        if doc['policy'].get('ladder') != 'g':
            self.assertEqual(negative, 0, 'the pre-D7 generation dropped every Nay')
            self.skipTest('published generation predates the unified vote direction')
        self.assertTrue(votes)
        self.assertGreater(negative, 0)
        self.assertTrue({a['cast_code'] for a in votes} - set(LEGACY_CAST_CODES[0] | LEGACY_CAST_CODES[1]))


if __name__ == '__main__':
    unittest.main()

"""Eligibility and conduct must hold simultaneously in every published seat."""
import itertools
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))
import tier_util as TU
from engine import effectiveness_integration as EI


def breach(severity):
    return {'effective_severity': severity, 'events': []}


class JointSeating(unittest.TestCase):
    def test_second_constraint_cannot_admit_evidence_disqualified_member(self):
        order = list('ABCD')
        floors = TU.evidence_slot_floors(
            order, {'A': 1, 'B': 1, 'C': 0, 'D': 1}, buckets=(1, 1, 0))
        result, detail = TU.apply_conduct_ladder(
            order, {'A': breach(6)}, buckets=(1, 1, 0), minimum_slots=floors)
        self.assertEqual(result, list('BDAC'))
        self.assertEqual(detail['A']['tier_ceiling'], 3)
        self.assertGreaterEqual(result.index('C'), 2)

    def test_stricter_evidence_floor_survives_conduct_and_delivery(self):
        for delivered in (0, 3, 10):
            with self.subTest(delivered=delivered):
                result, _ = TU.apply_conduct_ladder(
                    list('ABCD'), {'A': breach(6)},
                    {'A': {'delivered': delivered, 'events': []}},
                    buckets=(1, 1, 1), minimum_slots={'A': 3})
                self.assertEqual(result, list('BCDA'))

    def test_conduct_floor_survives_weaker_evidence_floor(self):
        result, _ = TU.apply_conduct_ladder(
            list('ABCD'), {'A': breach(9)}, buckets=(1, 1, 1),
            minimum_slots={'A': 1})
        self.assertEqual(result, list('BCDA'))

    def test_impossible_compact_board_refuses_instead_of_appending(self):
        with self.assertRaisesRegex(ValueError, r'Infeasible tier seating at rank 2 of 3'):
            TU._reseat_with_floors(list('ABC'), {'B': 2, 'C': 2})

    def test_short_evidence_roster_is_not_silently_tiered(self):
        with self.assertRaisesRegex(ValueError, r'Infeasible tier seating at rank 2 of 2'):
            TU.apply_evidence_floor(list('AB'), {'A': 1, 'B': 0}, buckets=(1, 1, 1))

    def test_exhaustive_small_boards_match_independent_permutation_oracle(self):
        order = tuple('ABCD')
        permutations = list(itertools.permutations(order))
        # 625 floor assignments, including floors beyond the final index.
        for values in itertools.product(range(5), repeat=4):
            floors = dict(zip(order, values))
            feasible = next((candidate for candidate in permutations
                             if all(index >= floors[name]
                                    for index, name in enumerate(candidate))), None)
            with self.subTest(floors=floors):
                if feasible is None:
                    with self.assertRaises(ValueError):
                        TU._reseat_with_floors(order, floors)
                else:
                    self.assertEqual(tuple(TU._reseat_with_floors(order, floors)), feasible)

    def test_no_floor_returns_independent_copy(self):
        order = list('ABC')
        result = TU._reseat_with_floors(order, {})
        self.assertEqual(result, order)
        self.assertIsNot(result, order)

    def test_duplicate_members_cannot_fill_two_slots(self):
        with self.assertRaisesRegex(ValueError, 'duplicate members'):
            TU._reseat_with_floors(list('AAB'), {})

    def test_slot_floors_are_nonnegative_integers(self):
        for value in (-1, .5, True, float('nan'), float('inf'), '2'):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'minimum slot'):
                TU.apply_conduct_ladder(list('ABCD'), {'A': breach(9)},
                                        buckets=(1, 1, 1), minimum_slots={'A': value})

    def test_invalid_active_evidence_cannot_evade_gate(self):
        for value in (float('nan'), float('inf'), float('-inf'), True, None):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'CU-primary credit'):
                TU.evidence_slot_floors(list('AB'), {'A': value, 'B': 1})

    def test_invalid_minimum_cannot_disable_gate(self):
        for value in (float('nan'), float('inf'), True):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'minimum CU-primary'):
                TU.evidence_slot_floors(list('AB'), {'A': 0, 'B': 1}, minimum=value)

    def test_invalid_conduct_or_delivery_cannot_remove_ceiling(self):
        for value in (float('nan'), float('inf'), True, None, -1):
            with self.subTest(value=value, source='conduct'), self.assertRaises(ValueError):
                TU.apply_conduct_ladder(list('ABCD'), {'A': breach(value)}, buckets=(1, 1, 1))
            with self.subTest(value=value, source='delivery'), self.assertRaises(ValueError):
                TU.apply_conduct_ladder(list('ABCD'), {'A': breach(9)},
                    {'A': {'delivered': value, 'events': []}}, buckets=(1, 1, 1))

    def test_disabled_or_absent_evidence_gate_retains_existing_contract(self):
        self.assertEqual(TU.evidence_slot_floors(list('AB'), {}), {})
        self.assertEqual(TU.evidence_slot_floors(list('AB'), {'A': 0}, minimum=0), {})
        self.assertEqual(TU.evidence_slot_floors(list('AB'), {'A': 0}, minimum=-1), {})


class BoardEntrypoints(unittest.TestCase):
    def board(self, mode, *, unqualified='M84', overrides=None, positions=None,
              conduct_member='M00', severity=9):
        names = ['M%02d' % index for index in range(90)]
        factors = {name: (100-index)/100 for index, name in enumerate(names)}
        credit = {name: 0 if name == unqualified else 1 for name in names}
        breaches = {conduct_member: breach(severity)} if conduct_member else {}
        with tempfile.TemporaryDirectory() as root:
            Path(root, 'ranks_python.json').write_text(json.dumps({
                'pcts': {name: {'leg': .5} for name in names}}), encoding='utf-8')
            with patch.object(TU, 'load_primacy_ranked', return_value=factors), \
                 patch.object(TU, 'load_primacy_factors', return_value=factors), \
                 patch.object(TU, 'load_primacy_cu_credit', return_value=credit), \
                 patch.object(TU, 'load_primacy_affirmative_evidence', return_value={name:(credit[name],factors[name]) for name in names}), \
                 patch.object(TU, 'load_per_area_grade', return_value={name: ('House', .5) for name in names}), \
                 patch.object(TU, 'load_primacy_baseline', return_value=.5), \
                 patch.object(TU, 'load_open_breaches', return_value=breaches), \
                 patch.object(TU, 'load_delivery_credit', return_value={}), \
                 patch.object(TU, 'load_tier_overrides', return_value=overrides or {}), \
                 patch.object(TU, 'load_tier_positions', return_value=positions or {}):
                return TU.legislative_press_tiers(names, root=root, with_detail=True, mode=mode)

    def test_factor_and_per_area_both_compose_constraints(self):
        for mode in ('factor', 'per_area_v1'):
            with self.subTest(mode=mode):
                tiers, ranks, _ = self.board(mode)
                self.assertEqual(ranks['M00'], 86)
                self.assertEqual(ranks['M84'], 87)
                self.assertEqual(tiers['M00'], 0)
                self.assertEqual(tiers['M84'], 0)
                self.assertEqual(sum(tier != 0 for tier in tiers.values()), 85)
                self.assertEqual(set(ranks.values()), set(range(1, 91)))

    def test_legacy_product_keeps_evidence_gate_disabled(self):
        tiers, ranks, _ = self.board('legxfactor')
        self.assertEqual(tiers['M84'], 3)
        self.assertEqual(ranks['M84'], 84)
        self.assertEqual(tiers['M00'], 0)

    @patch.object(TU, 'PRIMACY_T1_MIN_AFFIRMATIVE', 0)
    def test_explicit_pin_exempts_legacy_evidence_but_never_conduct(self):
        for mode in ('factor', 'per_area_v1'):
            options = {'unqualified': 'M89', 'overrides': {'M89': 1}, 'positions': {'M89': 1}}
            with self.subTest(mode=mode):
                tiers, ranks, _ = self.board(mode, **options, conduct_member=None)
                self.assertEqual((tiers['M89'], ranks['M89']), (1, 1))
                tiers, ranks, _ = self.board(mode, **options, conduct_member='M89', severity=6)
                self.assertEqual((tiers['M89'], ranks['M89']), (3, 38))

    def results_board(self, names):
        doc = {'schema': EI.SCHEMA, 'status': 'shadow_candidate',
               'results_evidence_gate': {'id': EI.RESULTS_GATE, 'minimum_distinct_signed_measures': 1},
               'members': {name: {'realized_results': 10-index, 'distinct_signed_measures': 1}
                           for index, name in enumerate(names)}}
        with patch.object(TU, 'load_open_breaches', return_value={'A': breach(9)}), \
             patch.object(TU, 'load_delivery_credit', return_value={}), \
             patch.object(TU, 'load_tier_overrides', return_value={}), \
             patch.object(TU, 'load_tier_positions', return_value={}):
            return TU.realized_results_tiers(names, doc, root='unused', buckets=(1, 1, 1))

    def test_results_board_refuses_impossible_short_conduct_layout(self):
        with self.assertRaisesRegex(ValueError, r'Infeasible tier seating at rank 3 of 3'):
            self.results_board(list('ABC'))

    def test_feasible_results_board_tiers_and_ranks_agree(self):
        tiers, ranks = self.results_board(list('ABCD'))
        self.assertEqual(ranks, {'A': 4, 'B': 1, 'C': 2, 'D': 3})
        self.assertEqual(tiers, {'A': 0, 'B': 1, 'C': 2, 'D': 3})


if __name__ == '__main__':
    unittest.main()

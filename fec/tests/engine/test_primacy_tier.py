# -*- coding: utf-8 -*-
"""Regression tests for beneficiary primacy, conduct, and UTF-8 identities."""
import csv
import json
import os
import re
import shutil
import sys
import tempfile
import unittest

FEC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, FEC)

import legislation_measure_key as MK
from engine import primacy_factors as PF
import tier_util as TU
import moneyball_core as MC
from legislation import build_primacy_act_memberships as PAM
from legislation import legislation_culling_status as LCS


def bundle(*members):
    return {'members': list(members)}


def member(name, *acts):
    return {
        'name': name,
        'bills': [
            {'bill': bill, 'congress': congress, 'role': role}
            for bill, congress, role in acts
        ],
    }


def tags(**values):
    return {
        key: {'class': value}
        for key, value in values.items()
    }


class PrimacyFactors(unittest.TestCase):
    def test_role_mass_is_sponsor_original_later_four_two_one(self):
        self.assertEqual(
            PF.ROLE_W,
            {'sponsor': 1.0, 'original': 0.5, 'cosponsor': 0.25},
        )

    def test_ratified_beneficiary_weights(self):
        self.assertEqual(PF.ACT_W['cu_primary'], 1.0)
        self.assertEqual(PF.ACT_W['shared'], 0.5)
        self.assertEqual(PF.ACT_W['oppose'], -1.0)
        self.assertEqual(PF.ACT_W['crypto_primary'], 0.15)
        self.assertEqual(PF.ACT_W['telecom_primary'], 0.15)

    def test_default_board_basis_is_the_per_area_board(self):
        # RE-PINNED 2026-09-16 (D-031, Paul 2026-09-15): the shipped default is
        # 'per_area_v1'. 'factor' (the 2026-07-29 factor-only directive) and
        # 'legxfactor' stay as revert switches, so this pins the SOURCE default
        # and then checks the env-resolved value is a recognised mode -- a
        # deliberate PRIMACY_BOARD=factor run stays green.
        source = open(
            os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))), 'tier_util.py'),
            encoding='utf-8').read()
        self.assertIn("(os.environ.get('PRIMACY_BOARD') or 'per_area_v1')", source)
        self.assertIn(TU.PRIMACY_BOARD, TU.PRIMACY_BOARD_MODES)
        for mode in ('factor', 'legxfactor', 'per_area_v1'):
            self.assertIn(mode, TU.PRIMACY_BOARD_MODES)

    def test_score_is_the_factor_alone(self):
        # 2026-07-29 user directive: the Legislative percentile no longer
        # multiplies the board score. 0.8 x 0.5 = 0.4 would be the OLD answer.
        # Mode pinned 2026-09-16: this asserts the FACTOR rule, which is now the
        # revert switch, so it must name the mode rather than follow the dial.
        scores = TU.primacy_scores(
            {'A': {'leg': 0.8, 'press': 1.0}}, {'A': 0.5}, mode='factor'
        )
        self.assertAlmostEqual(scores['A'], 0.5)

    def test_per_area_score_moves_the_last_fifth_on_the_grade(self):
        # D-031 ratified form (R1): factor_ranked * (0.80 + 0.20 * grade%).
        # Since RE-1 this is the 'whole' revert form, so it names the form.
        scores = TU.primacy_scores(
            {'A': {'leg': 0.8}, 'B': {'leg': 0.2}}, {'A': 0.5, 'B': 0.5},
            mode='per_area_v1', grades={'A': 1.0, 'B': 0.0}, grade_form='whole',
        )
        self.assertAlmostEqual(scores['A'], 0.5)
        self.assertAlmostEqual(scores['B'], 0.4)
        self.assertEqual(TU.PER_AREA_PURITY_SHARE, 0.80)

    def test_re1_default_grade_form_is_deviation(self):
        # RE-1 (2026-10-11): the source default is G1; 'whole' is the revert.
        source = open(
            os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))), 'tier_util.py'),
            encoding='utf-8').read()
        self.assertIn(
            "(os.environ.get('PRIMACY_GRADE_FORM') or 'deviation')", source)
        self.assertIn(TU.PRIMACY_GRADE_FORM, TU.PRIMACY_GRADE_FORMS)

    def test_re1_grade_never_lifts_a_below_baseline_record(self):
        # G1: B + (P - B) * (0.80 + 0.20 * grade%) for P >= B, P itself below
        # B. Under the old whole form a higher grade LIFTED a below-baseline
        # factor (0.32 -> 0.40); under G1 the grade cannot move it at all.
        pcts = {'Lo': {'leg': .5}, 'Hi': {'leg': .5}}
        below = TU.primacy_scores(pcts, {'Lo': 0.4, 'Hi': 0.4}, mode='per_area_v1',
                                  grades={'Lo': 0.0, 'Hi': 1.0}, baseline=0.5)
        self.assertAlmostEqual(below['Lo'], 0.40)
        self.assertAlmostEqual(below['Hi'], 0.40)
        above = TU.primacy_scores(pcts, {'Lo': 0.6, 'Hi': 0.6}, mode='per_area_v1',
                                  grades={'Lo': 0.0, 'Hi': 1.0}, baseline=0.5)
        self.assertAlmostEqual(above['Lo'], 0.58)
        self.assertAlmostEqual(above['Hi'], 0.60)
        # A below-baseline record can never outscore the baseline via grade.
        self.assertLess(max(below.values()), 0.5)
        # Parity gate 2: at grade 1 the deviation form is the factor itself.
        self.assertAlmostEqual(
            TU.primacy_scores({'A': {'leg': .5}}, {'A': 0.37}, mode='per_area_v1',
                              grades={'A': 1.0}, baseline=0.5)['A'], 0.37)
        with self.assertRaisesRegex(ValueError, 'roster baseline'):
            TU.primacy_scores(pcts, {'Lo': 0.4, 'Hi': 0.4}, mode='per_area_v1',
                              grades={'Lo': 0.0, 'Hi': 1.0},
                              grade_form='deviation')

    def test_re1_score_is_non_decreasing_in_grade(self):
        # Coordinator ruling 2026-10-11: a member's score must never rise as
        # their grade falls, on either side of the baseline (and at it).
        grades = [index / 20 for index in range(21)]
        for factor in (0.0, 0.2, 0.4999, 0.5, 0.5001, 0.8, 1.0):
            scores = [TU.primacy_scores({'A': {'leg': .5}}, {'A': factor},
                                        mode='per_area_v1', grades={'A': grade},
                                        grade_form='deviation', baseline=0.5)['A']
                      for grade in grades]
            self.assertEqual(scores, sorted(scores), factor)

    def test_per_area_missing_grade_fails_loud(self):
        with self.assertRaisesRegex(ValueError, 'per-area grade'):
            TU.primacy_scores(
                {'A': {'leg': 0.8}}, {'A': 0.5}, mode='per_area_v1', grades={},
                baseline=0.5,
            )

    def test_revert_switch_restores_legislative_times_factor(self):
        scores = TU.primacy_scores(
            {'A': {'leg': 0.8, 'press': 1.0}}, {'A': 0.5},
            mode='legxfactor',
        )
        self.assertAlmostEqual(scores['A'], 0.4)

    def test_rankable_roster_is_identical_under_both_modes(self):
        pcts = {'A': {'leg': 0.8}, 'B': {'leg': 0.2}, 'C': {'press': 1.0}}
        factors = {'A': 0.5, 'B': 0.9, 'C': 0.7}
        self.assertEqual(
            set(TU.primacy_scores(pcts, factors, mode='factor')),
            set(TU.primacy_scores(pcts, factors, mode='legxfactor')),
        )
        self.assertEqual(
            set(TU.primacy_scores(pcts, factors, mode='per_area_v1',
                                  grades={'A': 0.5, 'B': 0.5, 'C': 0.5},
                                  baseline=0.5)),
            set(TU.primacy_scores(pcts, factors, mode='legxfactor')),
        )

    def test_missing_factor_fails_loud_under_factor_only(self):
        # A missing factor used to fall open to a neutral 1.0. With the
        # percentile gone, 1.0 is the MAXIMUM score -- that member would take
        # slot 1, and an absent primacy_factors.json would order the whole
        # board alphabetically. It must fail loud instead.
        with self.assertRaises(ValueError):
            TU.primacy_scores({'A': {'leg': 0.8}}, {}, mode='factor')

    def test_missing_factor_stays_neutral_under_revert(self):
        self.assertAlmostEqual(
            TU.primacy_scores(
                {'A': {'leg': 0.8}}, {}, mode='legxfactor'
            )['A'],
            0.8,
        )

    def test_unknown_board_mode_fails_loud(self):
        with self.assertRaises(ValueError):
            TU.primacy_scores(
                {'A': {'leg': 0.8}}, {'A': 0.5}, mode='bogus'
            )


class EvidenceFloor(unittest.TestCase):
    """Purity ranks, evidence qualifies (user-ratified 2026-07-29)."""

    def test_default_floor_is_three_quarters_credit(self):
        self.assertEqual(TU.PRIMACY_MIN_CUPRIM, 0.75)

    def test_sub_floor_member_reseats_below_the_tiers(self):
        # 3 seats (1/1/1); A is the top factor but holds no cu_primary work.
        out = TU.apply_evidence_floor(
            ['A', 'B', 'C', 'D'],
            {'A': 0.0, 'B': 2.0, 'C': 2.0, 'D': 2.0},
            minimum=0.75, buckets=(1, 1, 1),
        )
        self.assertEqual(out, ['B', 'C', 'D', 'A'])

    def test_qualified_members_keep_pure_factor_order(self):
        out = TU.apply_evidence_floor(
            ['A', 'B', 'C'],
            {'A': 1.0, 'B': 1.0, 'C': 1.0},
            minimum=0.75, buckets=(1, 1, 1),
        )
        self.assertEqual(out, ['A', 'B', 'C'])

    def test_zero_minimum_disables_the_gate(self):
        out = TU.apply_evidence_floor(
            ['A', 'B'], {'A': 0.0, 'B': 5.0}, minimum=0,
        )
        self.assertEqual(out, ['A', 'B'])

    def test_absent_evidence_map_stands_the_gate_down(self):
        # An absent primacy_factors.json loads as {}. The gate must stand
        # down rather than silently untier the whole roster.
        out = TU.apply_evidence_floor(['A', 'B'], {}, minimum=0.75)
        self.assertEqual(out, ['A', 'B'])

    def test_exempt_placement_is_never_displaced(self):
        out = TU.apply_evidence_floor(
            ['A', 'B', 'C'],
            {'A': 0.0, 'B': 2.0, 'C': 2.0},
            minimum=0.75, buckets=(1, 1, 1), exempt={'A'},
        )
        self.assertEqual(out[0], 'A')

    def test_member_exactly_at_the_floor_qualifies(self):
        out = TU.apply_evidence_floor(
            ['A', 'B'], {'A': 0.75, 'B': 2.0},
            minimum=0.75, buckets=(1, 0, 0),
        )
        self.assertEqual(out, ['A', 'B'])

    def test_cu_credit_loader_absent_root_fails_open(self):
        self.assertEqual(
            TU.load_primacy_cu_credit('/nonexistent-root'), {}
        )


class UncertaintyPenalty(unittest.TestCase):
    """2026-09-20 adoption removes the extra dock; legacy math stays covered."""

    def test_ratified_penalty_strength(self):
        from engine.primacy_method import DIAGNOSTIC
        self.assertEqual(PF.UNCERT_K, 0.0 if DIAGNOSTIC else 0.25)

    def test_full_evidence_member_is_untouched(self):
        # lambda=1 -> factor_ranked == factor_shrunk exactly.
        bundle = {'members': [{
            'name': 'A',
            'bills': [{'bill': 'H.R.%d' % n, 'congress': '119',
                       'role': 'sponsor'} for n in range(1, 17)],
        }]}
        tags = {'H.R.%d|119' % n: {'class': 'cu_primary'}
                for n in range(1, 17)}
        rows, _ = PF.compute(bundle, tags)
        self.assertEqual(rows['A']['lambda'], 1.0)
        self.assertAlmostEqual(
            rows['A']['factor_ranked'], rows['A']['factor_shrunk']
        )

    def test_thin_evidence_estimate_respects_ratified_dock(self):
        # One sponsor role: lambda = 1/15; the ranked value must sit below
        # the shrunk value by K*(1-lambda)*|raw - baseline|.
        bundle = {'members': [
            {'name': 'Thin', 'bills': [{'bill': 'H.R.1', 'congress': '119',
                                        'role': 'sponsor'}]},
            {'name': 'Bulk', 'bills': [{'bill': 'H.R.%d' % n,
                                        'congress': '119',
                                        'role': 'sponsor'}
                                       for n in range(2, 32)]},
        ]}
        tags = {'H.R.1|119': {'class': 'cu_primary'}}
        tags.update({'H.R.%d|119' % n: {'class': 'shared'}
                     for n in range(2, 32)})
        rows, baseline = PF.compute(bundle, tags)
        thin = rows['Thin']
        expected = (
            thin['factor_shrunk']
            - PF.UNCERT_K * (1.0 - thin['lambda'])
            * abs(thin['factor_raw'] - baseline)
        )
        self.assertAlmostEqual(thin['factor_ranked'], expected)
        if PF.UNCERT_K:
            self.assertLess(thin['factor_ranked'], thin['factor_shrunk'])
        else:
            self.assertEqual(thin['factor_ranked'], thin['factor_shrunk'])

    def test_ranked_loader_prefers_factor_ranked(self):
        root = tempfile.mkdtemp()
        try:
            os.makedirs(os.path.join(root, 'data', 'engine'), exist_ok=True)
            with open(os.path.join(root, 'data', 'engine', 'primacy_factors.json'), 'w',
                      encoding='utf-8') as handle:
                json.dump({'members': {
                    'A': {'factor_shrunk': 0.9, 'factor_ranked': 0.6},
                    'B': {'factor_shrunk': 0.7},
                }}, handle)
            ranked = TU.load_primacy_ranked(root)
            self.assertAlmostEqual(ranked['A'], 0.6)
            # Pre-penalty rows fall back to factor_shrunk (the K=0 board);
            # the audit pins the artifact constant so this cannot pass
            # silently in production.
            self.assertAlmostEqual(ranked['B'], 0.7)
            self.assertAlmostEqual(
                TU.load_primacy_factors(root)['A'], 0.9
            )
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_ranked_loader_absent_root_fails_open(self):
        self.assertEqual(
            TU.load_primacy_ranked('/nonexistent-root'), {}
        )


class AmendmentMeasureWeight(unittest.TestCase):
    """Amendments count half (user-ratified 2026-07-29, '0.5 applies broad')."""

    def test_ratified_amendment_weight(self):
        self.assertEqual(PF.AMENDMENT_W, 0.5)
        self.assertEqual(MK.AMENDMENT_MEASURE_WEIGHT, 0.5)

    def test_display_ids_map_to_the_right_measure_kind(self):
        for value in ('H.Amdt.103', 'S.Amdt.2231', 'h.amdt.9', 'HAmdt7'):
            self.assertTrue(
                MK.is_display_amendment(value), value
            )
        for value in ('H.R.3928', 'H.Res.5', 'S.1582',
                      'LTR-119-CUTAX-CISCOMANI', '', None):
            self.assertFalse(
                MK.is_display_amendment(value), repr(value)
            )

    def test_unparseable_id_fails_open_to_bill_weight(self):
        # An unrecognized id must keep the weight it has always carried
        # rather than being silently halved.
        self.assertEqual(MK.display_measure_weight('???'), 1.0)

    def test_amendment_is_half_an_act_in_both_terms(self):
        # Two sponsor acts, both cu_primary: one bill, one amendment.
        # Denominator must be 1.0 + 0.5, not 2.0 -- an amendment is half an
        # ACT, not a full act worth half.
        bundle = {'members': [{'name': 'A', 'bills': [
            {'bill': 'H.R.1', 'congress': '119', 'role': 'sponsor'},
            {'bill': 'H.Amdt.1', 'congress': '119', 'role': 'sponsor'},
        ]}]}
        tags = {
            'H.R.1|119': {'class': 'cu_primary'},
            'H.Amdt.1|119': {'class': 'cu_primary'},
        }
        rows, _ = PF.compute(bundle, tags)
        self.assertAlmostEqual(rows['A']['agenda_credit'], 1.5)
        self.assertAlmostEqual(rows['A']['amendment_credit'], 0.5)
        # Both acts are cu_primary, so the ratio stays 1.0 either way; the
        # weight shows up as CREDIT, which is what shrinkage consumes.
        self.assertAlmostEqual(rows['A']['factor_raw'], 1.0)

    def test_amendment_weight_moves_the_ratio_when_classes_differ(self):
        # cu_primary bill + offtopic amendment. At full weight the ratio is
        # 1/2 = 0.5; at half weight it is 1/1.5 = 0.667.
        bundle = {'members': [{'name': 'A', 'bills': [
            {'bill': 'H.R.1', 'congress': '119', 'role': 'sponsor'},
            {'bill': 'H.Amdt.1', 'congress': '119', 'role': 'sponsor'},
        ]}]}
        tags = {
            'H.R.1|119': {'class': 'cu_primary'},
            'H.Amdt.1|119': {'class': 'offtopic'},
        }
        rows, _ = PF.compute(bundle, tags)
        self.assertAlmostEqual(rows['A']['factor_raw'], 1.0 / 1.5)

    def test_unclassified_act_fails_loud(self):
        bundle = {
            'members': [{
                'name': 'A',
                'bills': [{
                    'bill': 'H.R.9',
                    'congress': '119',
                    'role': 'sponsor',
                }],
            }],
        }
        with self.assertRaises(KeyError):
            PF.compute(
                bundle, {'H.R.1|119': {'class': 'cu_primary'}}
            )

    def test_duplicate_tag_fails_loud(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, 'tags.ndjson')
            with open(path, 'w', encoding='utf-8') as handle:
                row = {'bill': 'H.R.1', 'congress': 119, 'class': 'shared'}
                handle.write(json.dumps(row) + '\n')
                handle.write(json.dumps(row) + '\n')
            with self.assertRaises(ValueError):
                PF.load_tags(path)


class PrimacyEnginePolicy(unittest.TestCase):
    def test_discrete_engine_levels_are_exact_and_monotone(self):
        self.assertEqual(MC.primacy_engine_value(0), 0.0)
        self.assertEqual(MC.primacy_engine_value(1), 1.0)
        self.assertAlmostEqual(MC.primacy_engine_value(2), 2.0 / 3.0)
        self.assertAlmostEqual(MC.primacy_engine_value(3), 1.0 / 3.0)
        self.assertGreater(
            MC.primacy_engine_value(1),
            MC.primacy_engine_value(2),
        )
        self.assertGreater(
            MC.primacy_engine_value(2),
            MC.primacy_engine_value(3),
        )
        self.assertGreater(
            MC.primacy_engine_value(3),
            MC.primacy_engine_value(0),
        )

    def test_invalid_engine_tiers_fail_loud(self):
        for value in (-1, 4, True, None, 'bad'):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    MC.primacy_engine_value(value)

    def test_missing_orientation_is_exactly_neutral(self):
        self.assertAlmostEqual(
            MC.blended_engine_priority(2, {}, 'CFPB', 'flip'),
            MC.primacy_engine_value(2),
        )

    def test_current_topic_cannot_leak_into_its_priority(self):
        positive = MC.blended_engine_priority(
            3, {'CFPB': 1.0}, 'CFPB', 'flip'
        )
        negative = MC.blended_engine_priority(
            3, {'CFPB': -1.0}, 'CFPB', 'flip'
        )
        self.assertAlmostEqual(positive, negative)
        self.assertAlmostEqual(positive, MC.primacy_engine_value(3))

    def test_other_topic_orientation_adjusts_without_upper_clipping(self):
        scores = {category: 1.0 for category in MC.IMP}
        value = MC.blended_engine_priority(
            1, scores, 'CFPB', 'flip'
        )
        self.assertAlmostEqual(value, 1.15)
        self.assertGreater(value, 1.0)

    def test_coalition_orientation_is_stronger_than_flip(self):
        scores = {category: 1.0 for category in MC.IMP}
        flip = MC.blended_engine_priority(
            2, scores, 'CFPB', 'flip'
        )
        coalition = MC.blended_engine_priority(
            2, scores, 'CFPB', 'coalition'
        )
        self.assertGreater(coalition, flip)

    def test_nonnegative_floor_is_after_engagement_and_priority(self):
        self.assertEqual(
            MC.priority_base_signal(0.0, -0.25, 0.15), 0.0
        )
        self.assertAlmostEqual(
            MC.priority_base_signal(0.1, -0.25, 0.15), 0.0625
        )

    def test_invalid_orientation_score_fails_loud(self):
        with self.assertRaises(ValueError):
            MC.engine_orientation({'CFPB': 1.01})

    def test_pipeline_builds_primacy_before_allocation_engines(self):
        with open(
            os.path.join(FEC, 'run_pipeline.py'),
            encoding='utf-8',
        ) as handle:
            source = handle.read()
        primacy = source.index(
            "('build_primacy_surfaces.py',['--engine-inputs-only'])"
        )
        flip = source.index("('per_topic_flip.py',[])")
        optimizer = source.index("('optimize.py',[])")
        self.assertLess(primacy, flip)
        self.assertLess(primacy, optimizer)

    def test_industry_tiers_precedes_lobby_network_family_build(self):
        with open(
            os.path.join(FEC, 'run_pipeline.py'),
            encoding='utf-8',
        ) as handle:
            source = handle.read()
        chain = re.search(
            r'^CHAIN\s*=\s*\[(.*?)^[ \t]*\][ \t]*$',
            source,
            re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(chain)
        gap = r'(?:\s|#[^\n]*(?:\n|$))*'
        scripts = re.findall(
            r"^[ \t]*\(" + gap
            + r"['\"]([^'\"]+\.py)['\"]" + gap
            + r"," + gap + r"\[[^\]]*\]" + gap + r"\)",
            chain.group(1),
            re.MULTILINE,
        )
        self.assertEqual(scripts.count('industry_tiers.py'), 1)
        self.assertLess(
            scripts.index('industry_tiers.py'),
            scripts.index('make_lobby_network_html.py'),
        )

    def test_responsiveness_follows_primacy_and_precedes_member_desk(self):
        with open(
            os.path.join(FEC, 'run_pipeline.py'),
            encoding='utf-8',
        ) as handle:
            source = handle.read()
        chain = re.search(
            r'^CHAIN\s*=\s*\[(.*?)^[ \t]*\][ \t]*$',
            source,
            re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(chain)
        gap = r'(?:\s|#[^\n]*(?:\n|$))*'
        scripts = re.findall(
            r"^[ \t]*\(" + gap
            + r"['\"]([^'\"]+\.py)['\"]" + gap
            + r"," + gap + r"\[[^\]]*\]" + gap + r"\)",
            chain.group(1),
            re.MULTILINE,
        )
        self.assertEqual(scripts.count('build_primacy_surfaces.py'), 1)
        self.assertEqual(scripts.count('responsiveness_layers.py'), 1)
        self.assertEqual(scripts.count('make_lobby_network_html.py'), 1)
        self.assertEqual(scripts.count('make_member_desk.py'), 1)
        self.assertLess(
            scripts.index('build_primacy_surfaces.py'),
            scripts.index('responsiveness_layers.py'),
        )
        self.assertLess(
            scripts.index('responsiveness_layers.py'),
            scripts.index('make_lobby_network_html.py'),
        )
        self.assertLess(
            scripts.index('responsiveness_layers.py'),
            scripts.index('make_member_desk.py'),
        )


class ConductLadder(unittest.TestCase):
    def test_thresholds(self):
        self.assertEqual(TU.BUCKETS, (13, 24, 48))
        self.assertEqual(TU.tier_ceiling(2.99), 1)
        self.assertEqual(TU.tier_ceiling(3.0), 2)
        self.assertEqual(TU.tier_ceiling(6.0), 3)
        self.assertEqual(TU.tier_ceiling(9.0), 0)

    def test_ceiling_never_promotes(self):
        order = ['M%02d' % index for index in range(90)]
        breaches = {
            'M01': {
                'effective_severity': 7.25,
                'events': [('G', 3, 1.75)],
            }
        }
        final, _ = TU.apply_conduct_ladder(order, breaches)
        self.assertEqual(final.index('M01'), 37)
        for member in order:
            if member != 'M01':
                self.assertLessEqual(
                    final.index(member), order.index(member)
                )

    def test_delivery_offsets_but_never_overpromotes(self):
        order = ['M%02d' % index for index in range(90)]
        breaches = {
            'M01': {
                'effective_severity': 8.0,
                'events': [('G', 3, 2.0)],
            }
        }
        delivery = {
            'M01': {
                'delivered': 3.0,
                'events': [('D', 'H.R.1', 'cu_primary', 3.0)],
            }
        }
        final, detail = TU.apply_conduct_ladder(
            order, breaches, delivery
        )
        self.assertEqual(final.index('M01'), 13)
        self.assertEqual(detail['M01']['net_severity'], 5.0)
        self.assertGreaterEqual(final.index('M01'), order.index('M01'))

    def test_live_ledgers_reproduce_known_cases(self):
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
        breaches = TU.load_open_breaches(root)
        delivery = TU.load_delivery_credit(root)
        if not breaches:
            self.skipTest('production conduct ledgers not installed yet')
        self.assertAlmostEqual(
            breaches['Andy Barr']['effective_severity'], 7.25
        )
        self.assertAlmostEqual(
            breaches['French Hill']['effective_severity'], 8.0
        )
        self.assertAlmostEqual(delivery['French Hill']['delivered'], 3.0)


class IdentityBoundary(unittest.TestCase):
    def test_category_scorer_declares_utf8_for_identity_ledgers(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            'stance',
            'per_category_stance.py',
        )
        with open(path, encoding='utf-8') as handle:
            source = handle.read()
        self.assertIn(
            "open(os.path.join(MB_ROOT, 'archive_leg.json'),encoding='utf-8')",
            source,
        )
        self.assertIn(
            "open(os.path.join(MB_ROOT, 'vote_category_lean.json'),encoding='utf-8')",
            source,
        )
        self.assertIn("unicodedata.normalize('NFC'", source)


class PrimacyMembershipBoundary(unittest.TestCase):
    def test_only_missing_sponsor_amendments_are_materialized(self):
        with tempfile.TemporaryDirectory() as root:
            source_path = os.path.join(root, 'source.json')
            current_path = os.path.join(root, 'current.json')
            tag_path = os.path.join(root, 'tags.ndjson')
            source = {
                'members': [{
                    'name': 'A',
                    'bills': [
                        {
                            'bill': 'H.R.1',
                            'congress': '119',
                            'role': 'sponsor',
                        },
                        {
                            'bill': 'S.Amdt.2',
                            'congress': '119',
                            'role': 'sponsor',
                            'areas': ['CFPB'],
                        },
                    ],
                }],
            }
            current = {
                'members': [{
                    'name': 'A',
                    'bills': [{
                        'bill': 'H.R.1',
                        'congress': '119',
                        'role': 'sponsor',
                    }],
                }],
            }
            with open(source_path, 'w', encoding='utf-8') as handle:
                json.dump(source, handle)
            with open(current_path, 'w', encoding='utf-8') as handle:
                json.dump(current, handle)
            with open(tag_path, 'w', encoding='utf-8') as handle:
                for bill in ('H.R.1', 'S.Amdt.2'):
                    handle.write(json.dumps({
                        'bill': bill,
                        'congress': '119',
                    }) + '\n')
            rows = PAM.build(source_path, current_path, tag_path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['bill'], 'S.Amdt.2')
            self.assertEqual(rows[0]['member'], 'A')
            self.assertEqual(rows[0]['role'], 'sponsor')


class CullingSurfaceStatus(unittest.TestCase):
    def test_matching_promotion_supersedes_staged_gate_for_surfaces(self):
        with tempfile.TemporaryDirectory() as root:
            staged_path = os.path.join(root, LCS.STAGED_NAME)
            rank_path = os.path.join(root, LCS.RANK_NAME)
            promotion_dir = os.path.join(
                root, 'audits', 'legfix5', 'production_promotion', 'run'
            )
            os.makedirs(promotion_dir)
            with open(staged_path, 'w', encoding='utf-8') as handle:
                json.dump({
                    'status': (
                        'PASS_STAGED_INTEGRATION_WITH_HELD_NONSCORING'
                    ),
                    'counts': {'scoring_ready_candidates': 60},
                }, handle)
            with open(rank_path, 'w', encoding='utf-8') as handle:
                json.dump({'rank': {'A': 1}}, handle)
            promotion_path = os.path.join(
                promotion_dir, 'promotion_receipt.json'
            )
            with open(promotion_path, 'w', encoding='utf-8') as handle:
                json.dump({
                    'status': 'PASS_PRODUCTION_INPUTS_PROMOTED',
                    'promoted_at': '2026-07-29T00:00:00+00:00',
                    'authorization': 'GREENLIGHT_INTEGRATION',
                    'installed': {
                        LCS.STAGED_NAME: {
                            'sha256': LCS.sha256(
                                __import__('pathlib').Path(staged_path)
                            ),
                        },
                        LCS.RANK_NAME: {
                            'sha256': LCS.sha256(
                                __import__('pathlib').Path(rank_path)
                            ),
                        },
                    },
                    'counts': {'scoring_ready_candidates': 60},
                }, handle)
            status = LCS.build_surface_status(root)
            self.assertEqual(
                status['status'], 'PASS_PRODUCTION_CULLING_INTEGRATED'
            )
            self.assertEqual(status['phase'], 'production')
            self.assertTrue(status['promotion_performed'])
            self.assertEqual(
                status['staged_receipt']['status'],
                'PASS_STAGED_INTEGRATION_WITH_HELD_NONSCORING',
            )


class RatifiedActContract(unittest.TestCase):
    def test_complete_weight_table(self):
        self.assertEqual(PF.ACT_W['offtopic'], 0.0)
        for name in ('bank_primary', 'crypto_primary', 'fintech', 'telecom_primary'):
            self.assertEqual(PF.ACT_W[name], 0.15)
        # The beneficiary table that feeds the diagnostic must agree with ACT_W.
        from engine.primacy_method import BENEFICIARY_W
        self.assertEqual(BENEFICIARY_W['telecom_primary'], PF.ACT_W['telecom_primary'])
        self.assertTrue(PF._is_synthetic_cell_class('telecom_cell_3'))

    def test_oppose_is_the_only_negative_class(self):
        self.assertEqual(
            [name for name, value in PF.ACT_W.items() if value < 0],
            ['oppose'],
        )

    def test_letters_use_letter_weight(self):
        row = {'class': 'shared'}
        self.assertEqual(
            PF.act_weight(row, 'LTR-119-X|119'),
            PF.LETTER_W,
        )

    def test_ratified_letter_override_uses_full_primary_weight(self):
        row = {
            'class': 'cu_primary',
            'letter_weight_override': 1.0,
        }
        self.assertEqual(
            PF.act_weight(row, 'LTR-119-X|119'),
            PF.ACT_W['cu_primary'],
        )

    def test_letter_override_outside_weight_range_raises(self):
        row = {
            'class': 'cu_primary',
            'letter_weight_override': 1.01,
        }
        with self.assertRaises(ValueError):
            PF.act_weight(row, 'LTR-119-X|119')

    def test_unknown_class_raises(self):
        with self.assertRaises(ValueError):
            PF.act_weight({'class': 'nonsense'}, 'H.R.1|119')


class ACURelevanceAxis(unittest.TestCase):
    def setUp(self):
        self.original_acu_relevance_active = PF.ACU_RELEVANCE_ACTIVE

    def tearDown(self):
        PF.ACU_RELEVANCE_ACTIVE = self.original_acu_relevance_active

    def test_defaults_are_pinned(self):
        self.assertTrue(PF.ACU_RELEVANCE_ACTIVE)
        self.assertEqual(
            PF.ACU_RELEVANCE,
            {'SUPPORT': 0.5, 'OPPOSE': -1.0},
        )

    def test_support_floors_low_class_weight(self):
        PF.ACU_RELEVANCE_ACTIVE = True
        self.assertEqual(
            PF.act_weight(
                {'class': 'bank_primary', 'acu_position': 'SUPPORT'},
                'H.R.1|119',
            ),
            0.5,
        )

    def test_oppose_forces_negative_one(self):
        PF.ACU_RELEVANCE_ACTIVE = True
        self.assertEqual(
            PF.act_weight(
                {'class': 'bank_primary', 'acu_position': 'OPPOSE'},
                'H.R.2|119',
            ),
            -1.0,
        )

    def test_disabled_flag_leaves_class_weight_untouched(self):
        PF.ACU_RELEVANCE_ACTIVE = False
        self.assertEqual(
            PF.act_weight(
                {'class': 'bank_primary', 'acu_position': 'SUPPORT'},
                'H.R.3|119',
            ),
            0.15,
        )

    def test_monitor_and_empty_positions_are_noops(self):
        PF.ACU_RELEVANCE_ACTIVE = True
        for position in ('MONITOR', ''):
            with self.subTest(acu_position=position):
                self.assertEqual(
                    PF.act_weight(
                        {
                            'class': 'bank_primary',
                            'acu_position': position,
                        },
                        'H.R.4|119',
                    ),
                    0.15,
                )


class FactorKernelContract(unittest.TestCase):
    def test_pure_cu_author_scores_one(self):
        values, _baseline = PF.compute(
            bundle(member('A', ('H.R.1', '119', 'sponsor'))),
            tags(**{'H.R.1|119': 'cu_primary'}),
        )
        self.assertAlmostEqual(values['A']['factor_raw'], 1.0)

    def test_oppose_offsets_cu_primary(self):
        values, _baseline = PF.compute(
            bundle(member(
                'A',
                ('H.R.1', '119', 'sponsor'),
                ('H.R.2', '119', 'sponsor'),
            )),
            tags(**{
                'H.R.1|119': 'cu_primary',
                'H.R.2|119': 'oppose',
            }),
        )
        self.assertAlmostEqual(values['A']['factor_raw'], 0.0)

    def test_role_weights_are_applied(self):
        values, _baseline = PF.compute(
            bundle(member(
                'A',
                ('H.R.1', '119', 'sponsor'),
                ('H.R.2', '119', 'cosponsor'),
            )),
            tags(**{
                'H.R.1|119': 'cu_primary',
                'H.R.2|119': 'offtopic',
            }),
        )
        self.assertAlmostEqual(values['A']['factor_raw'], 1.0 / 1.25)

    def test_shrinkage_is_contained_between_raw_and_baseline(self):
        values, baseline = PF.compute(
            bundle(
                member('A', ('H.R.1', '119', 'sponsor')),
                member('B', ('H.R.2', '119', 'sponsor')),
            ),
            tags(**{
                'H.R.1|119': 'cu_primary',
                'H.R.2|119': 'oppose',
            }),
        )
        for row in values.values():
            low = min(row['factor_raw'], baseline) - 1e-9
            high = max(row['factor_raw'], baseline) + 1e-9
            self.assertTrue(low <= row['factor_shrunk'] <= high)

    def test_member_with_no_acts_uses_baseline(self):
        values, baseline = PF.compute(
            bundle(
                member('Empty'),
                member('A', ('H.R.1', '119', 'sponsor')),
            ),
            tags(**{'H.R.1|119': 'shared'}),
        )
        self.assertEqual(values['Empty']['agenda_credit'], 0)
        self.assertAlmostEqual(
            values['Empty']['factor_shrunk'], baseline
        )

    def test_producer_is_deterministic(self):
        act_tags = tags(**{
            'H.R.1|119': 'cu_primary',
            'H.R.2|119': 'shared',
        })
        act_bundle = bundle(
            member('A', ('H.R.1', '119', 'sponsor')),
            member('B', ('H.R.2', '119', 'original')),
        )
        self.assertEqual(
            PF.compute(act_bundle, act_tags),
            PF.compute(act_bundle, act_tags),
        )

    def test_adjudicated_credit_uses_the_same_factor_math(self):
        act_bundle = bundle(
            member('A', ('H.R.1', '119', 'sponsor')),
            member('B', ('H.R.1', '119', 'sponsor')),
        )
        act_tags = tags(**{'H.R.1|119': 'shared'})
        credits = {
            'A': {
                'denominator_delta': 1.0,
                'numerator_delta': 1.0,
                'mix': {'cu_primary': 1.0},
                'events': ['PAC-TEST'],
            },
        }
        values, _baseline = PF.compute(
            act_bundle, act_tags, credits
        )
        self.assertAlmostEqual(values['A']['factor_raw'], 0.75)
        self.assertAlmostEqual(values['B']['factor_raw'], 0.50)
        self.assertEqual(values['A']['adjudicated_credit'], 1.0)
        self.assertEqual(
            values['A']['adjudicated_events'], ['PAC-TEST']
        )

    def test_adjudication_unknown_member_fails_loud(self):
        with self.assertRaises(ValueError):
            PF.compute(
                bundle(member('A', ('H.R.1', '119', 'sponsor'))),
                tags(**{'H.R.1|119': 'shared'}),
                {
                    'Not In Roster': {
                        'denominator_delta': 1.0,
                        'numerator_delta': 1.0,
                    },
                },
            )


class EvidenceLedgerContract(unittest.TestCase):
    def test_missing_artifacts_fail_open(self):
        self.assertEqual(
            TU.load_primacy_factors('/nonexistent-root'), {}
        )
        self.assertEqual(
            TU.load_open_breaches('/nonexistent-root'), {}
        )
        self.assertEqual(
            TU.load_delivery_credit('/nonexistent-root'), {}
        )

    def test_malformed_primacy_artifact_fails_loud(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, 'data', 'engine', 'primacy_factors.json')
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w', encoding='utf-8') as handle:
                handle.write('{not json')
            with self.assertRaises(ValueError):
                TU.load_primacy_factors(root)

    def test_grievance_weighting_and_explanations(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, 'grievance_ledger.ndjson')
            rows = [
                {
                    'member': 'A',
                    'severity': 3,
                    'leverage_mult': 1.75,
                    'status': 'open',
                    'event_id': 'G1',
                    'type': 'G1_refused_ask_on_vehicle',
                    'vehicle': 'H.R.1',
                    'leverage': 'chair',
                    'cure': 'Report the CU bill',
                },
                {
                    'member': 'A',
                    'severity': 2,
                    'status': 'open',
                    'event_id': 'G2',
                    'type': 'G4_credit_appropriation',
                    'vehicle': 'H.R.2',
                    'cure': 'Correct the statement',
                },
                {
                    'member': 'Cured',
                    'severity': 3,
                    'status': 'cured',
                    'event_id': 'G3',
                },
            ]
            with open(path, 'w', encoding='utf-8') as handle:
                for row in rows:
                    handle.write(json.dumps(row) + '\n')
            loaded = TU.load_open_breaches(root)
            self.assertAlmostEqual(
                loaded['A']['effective_severity'], 7.25
            )
            self.assertNotIn('Cured', loaded)
            self.assertTrue(all(
                row['reason'] and row['cure']
                for row in loaded['A']['event_detail']
            ))

    def test_severity_two_events_are_not_free(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, 'grievance_ledger.ndjson')
            with open(path, 'w', encoding='utf-8') as handle:
                for index in range(2):
                    handle.write(json.dumps({
                        'member': 'A',
                        'severity': 2,
                        'status': 'open',
                        'event_id': 'G%d' % index,
                    }) + '\n')
            severity = (
                TU.load_open_breaches(root)['A']
                ['effective_severity']
            )
            self.assertEqual(TU.tier_ceiling(severity), 2)

    def test_delivery_is_class_priced(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, 'delivery_ledger.ndjson')
            rows = [
                {
                    'member': 'A',
                    'event_id': 'D1',
                    'bill': 'H.R.1',
                    'beneficiary_class': 'cu_primary',
                    'instrument': 'floor_managed',
                    'delivered_credit': 0.75,
                    'status': 'open',
                },
                {
                    'member': 'A',
                    'event_id': 'D2',
                    'bill': 'H.R.2',
                    'beneficiary_class': 'crypto_primary',
                    'instrument': 'floor_managed',
                    'delivered_credit': 0.1125,
                    'status': 'open',
                },
                {
                    'member': 'B',
                    'event_id': 'D3',
                    'delivered_credit': 0.0,
                    'status': 'open',
                },
            ]
            with open(path, 'w', encoding='utf-8') as handle:
                for row in rows:
                    handle.write(json.dumps(row) + '\n')
            loaded = TU.load_delivery_credit(root)
            self.assertAlmostEqual(loaded['A']['delivered'], 0.8625)
            self.assertNotIn('B', loaded)
            self.assertTrue(all(
                row['bill'] and row['instrument']
                for row in loaded['A']['event_detail']
            ))

    def test_closed_delivery_is_excluded(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, 'delivery_ledger.ndjson')
            with open(path, 'w', encoding='utf-8') as handle:
                handle.write(json.dumps({
                    'member': 'A',
                    'event_id': 'D1',
                    'delivered_credit': 0.75,
                    'status': 'superseded',
                }) + '\n')
            self.assertEqual(TU.load_delivery_credit(root), {})

    def test_adjudication_ledger_is_unique_and_class_priced(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, 'adjudication.ndjson')
            rows = [
                {
                    'event_id': 'PAC-1',
                    'member': 'A',
                    'status': 'active',
                    'beneficiary_class': 'cu_primary',
                    'role_weight': 1.0,
                },
                {
                    'event_id': 'PAC-2',
                    'member': 'A',
                    'status': 'active',
                    'beneficiary_class': 'shared',
                    'role_weight': 0.5,
                },
            ]
            with open(path, 'w', encoding='utf-8') as handle:
                for row in rows:
                    handle.write(json.dumps(row) + '\n')
            loaded = PF.load_adjudicated_credits(path)
            self.assertAlmostEqual(
                loaded['A']['denominator_delta'], 1.5
            )
            self.assertAlmostEqual(
                loaded['A']['numerator_delta'], 1.25
            )
            self.assertEqual(
                loaded['A']['events'], ['PAC-1', 'PAC-2']
            )


class LadderBoundaryContract(unittest.TestCase):
    def test_buckets_are_sizes_not_cutoffs(self):
        order = ['M%02d' % index for index in range(90)]
        final, _detail = TU.apply_conduct_ladder(
            order,
            {'M01': {
                'effective_severity': 3.0,
                'events': [('E', 3, 1.0)],
            }},
        )
        self.assertEqual(final.index('M01'), 13)

    def test_ceiling_is_monotone(self):
        rank = {1: 0, 2: 1, 3: 2, 0: 3}
        previous = 1
        for quarter in range(60):
            ceiling = TU.tier_ceiling(quarter / 4.0)
            self.assertGreaterEqual(rank[ceiling], rank[previous])
            previous = ceiling

    def test_member_already_below_floor_does_not_move(self):
        order = ['M%02d' % index for index in range(100)]
        final, detail = TU.apply_conduct_ladder(
            order,
            {'M80': {
                'effective_severity': 6.0,
                'events': [('E', 3, 2.0)],
            }},
        )
        self.assertEqual(detail['M80']['tier_ceiling'], 3)
        self.assertEqual(final.index('M80'), 80)

    def test_untiered_rung_moves_member_below_tier_three(self):
        order = ['M%02d' % index for index in range(100)]
        final, detail = TU.apply_conduct_ladder(
            order,
            {'M73': {
                'effective_severity': 10.0,
                'events': [('E', 5, 2.0)],
            }},
        )
        self.assertEqual(detail['M73']['tier_ceiling'], 0)
        self.assertGreaterEqual(final.index('M73'), 85)

    def test_delivery_without_grievance_never_promotes(self):
        order = ['M%02d' % index for index in range(90)]
        final, detail = TU.apply_conduct_ladder(
            order,
            {},
            {'M50': {
                'delivered': 99.0,
                'events': [('D', 'H.R.1', 'cu_primary', 99.0)],
            }},
        )
        self.assertEqual(final, order)
        self.assertEqual(detail['M50']['tier_ceiling'], 1)
        self.assertEqual(detail['M50']['gross_severity'], 0.0)

    def test_more_delivery_is_never_worse(self):
        order = ['M%02d' % index for index in range(90)]
        breaches = {
            'M01': {
                'effective_severity': 8.0,
                'events': [('G', 3, 2.0)],
            },
        }
        prior_position = None
        for credit in (0.0, 1.0, 2.5, 5.0, 20.0):
            final, _detail = TU.apply_conduct_ladder(
                order,
                breaches,
                {'M01': {'delivered': credit, 'events': []}},
            )
            position = final.index('M01')
            if prior_position is not None:
                self.assertLessEqual(position, prior_position)
            prior_position = position

    def test_empty_ladder_is_identity(self):
        final, detail = TU.apply_conduct_ladder(
            ['A', 'B', 'C'], {}, None
        )
        self.assertEqual(final, ['A', 'B', 'C'])
        self.assertEqual(detail, {})


class ProductionPrimacyClosure(unittest.TestCase):
    def test_registry_reconciliation_closes_all_55_anchors(self):
        root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        )
        with open(
            os.path.join(root, 'data/legislation/registry_anchor_reconciliation.json'),
            encoding='utf-8',
        ) as handle:
            receipt = json.load(handle)
        self.assertEqual(
            receipt['counts']['declared_registry_anchors'], 55
        )
        self.assertEqual(receipt['counts']['recovered_anchors'], 6)
        self.assertEqual(receipt['counts']['missing_after'], 0)
        self.assertTrue(all(receipt['gates'].values()))

    def test_frozen_board_fixture_replays_its_own_533_evidence_inputs(self):
        fixture_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            'fixtures',
            'FIXTURE_expected_board.csv',
        )
        with open(
            fixture_path, encoding='utf-8', newline=''
        ) as handle:
            fixture = list(csv.DictReader(handle))
        self.assertEqual(len(fixture), 533)
        # Historical fixtures test the policy on frozen evidence. Comparing
        # today's live sources to old positions would prohibit self-updates.
        order=sorted(fixture,key=lambda row:(-float(row['score']),row['member']))
        order=TU.apply_evidence_floor([r['member'] for r in order],
            {r['member']:float(r['cu_primary_credit']) for r in fixture})
        breaches={r['member']:{'effective_severity':float(r['gross_severity']), 'events':[]}
                  for r in fixture if r['gross_severity']}
        deliveries={r['member']:{'delivered':float(r['delivered_credit']), 'events':[]}
                    for r in fixture if r['delivered_credit']}
        actual,_=TU.apply_conduct_ladder(order,breaches,deliveries)
        self.assertEqual(actual, [row['member'] for row in fixture])

    def test_ratified_members_qualify_by_score_without_positions(self):
        root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        )
        with open(
            os.path.join(root, 'member_desk_bundle.json'),
            encoding='utf-8',
        ) as handle:
            desk = json.load(handle)
        members = [row['name'] for row in desk['members']]
        # Mode pinned 2026-09-16 (D-031): the expectations below are the FACTOR
        # board's. The per_area_v1 board that now ships reseats three of them and
        # is pinned separately in PerAreaRatifiedMembers.
        tier, rank = TU.legislative_press_tiers(members, mode='factor')
        # RE-PINNED 2026-07-29 twice: the factor-only directive re-seated
        # Huizenga and Kim in Tier 2; the same-day uncertainty penalty
        # (K=0.25, "certainty outranks estimation") re-seated Kim in Tier 1
        # at 13 on her full-evidence measurement — no ledger re-size — and
        # left Huizenga at 16 (his gap is structural bank/crypto ballast,
        # not measurement noise). Vargas T1 is a ratified constraint of the
        # K choice ("Vargas needs to be in"). Asserted exactly, not relaxed.
        # 2026-08-12 (Paul-ratified): exact-rank pins retired here as they
        # already were in audit_full on 2026-08-10 - tier membership plus the
        # outer boundary is the contract, so lawful data changes no longer
        # fire this check while a tier eviction still does. Huizenga moves to
        # Tier 2 with the crypto-cell repricing; Cortez Masto's Tier 2 seat is
        # accepted, her dilution traced to the 2026-08-10 bank-anchor universe
        # expansion, not to this change.
        self.assertEqual(TU.load_tier_overrides(), {})
        self.assertEqual(TU.load_tier_positions(), {})
        self.assertEqual(tier['Brad Sherman'], 1)
        self.assertLessEqual(rank['Brad Sherman'], 13)
        self.assertEqual(tier['Tim Scott'], 1)
        self.assertLessEqual(rank['Tim Scott'], 13)
        self.assertEqual(tier['Mike Crapo'], 1)
        self.assertLessEqual(rank['Mike Crapo'], 13)
        self.assertEqual(tier['Juan Vargas'], 1)
        self.assertLessEqual(rank['Juan Vargas'], 13)
        self.assertEqual(tier['Catherine Cortez Masto'], 2)
        self.assertLessEqual(rank['Catherine Cortez Masto'], 37)
        self.assertEqual(tier['Bill Huizenga'], 2)
        self.assertLessEqual(rank['Bill Huizenga'], 37)
        self.assertEqual(tier['Young Kim'], 2)
        self.assertLessEqual(rank['Young Kim'], 37)
        # All five keep their adjudication credit in the FACTOR math itself —
        # the directives changed the board basis, not the ledger.
        factors = TU.load_primacy_factors(root)
        self.assertTrue(all(
            member in factors
            for member in (
                'Brad Sherman', 'Bill Huizenga', 'Young Kim',
                'Catherine Cortez Masto', 'Tim Scott',
            )
        ))


class PerAreaRatifiedMembers(unittest.TestCase):
    """The same five adjudicated members, under the board that now SHIPS.

    ADDED 2026-09-16 (D-031, Paul 2026-09-15). The class above pins the FACTOR
    board explicitly; without this one, flipping the default would have silently
    stopped testing the board production actually seats.

    FIX-UP 2026-09-17. This class used to pin the ruled sweep's 13 Tier-1 names
    in order. Every input of that board -- data/engine/primacy_factors.json,
    data/engine/per_area_grade.json, ranks_python.json -- is rewritten by every
    chain run, so the pin asserted a chain snapshot rather than the design: the
    2026-09-17 input refresh moved the 13th seat from Pete Sessions to Daniel
    Meuser and turned the test red with no shipped code change. That is the same
    defect commit 4dc148fc1 retired from test_per_area_board.py; the full account
    is in audits/members_section_20260914/evidence/board-tests-and-order.md.

    What is asserted here now is what the ruling actually fixed: 13 seats, and
    the BOARD OF RECORD -- the tier/tierrank member_desk_bundle.json published --
    equal to what tier_util seats from the live inputs today. The exact ratified
    name list stays reproducible on the FROZEN plane of test_per_area_board.py
    (PrototypeFixture.test_tier1_order_is_the_ruled_sweep under
    PER_AREA_FIXTURE=1), which reads the 2026-09-15 sweep CSV and no live
    artifact; it is deliberately not duplicated here, where only live data is
    available and a flag would freeze nothing.

    Tier membership plus the outer boundary is the contract (2026-08-12 ruling);
    exact ranks stay in test_per_area_board.py.
    """

    @classmethod
    def setUpClass(cls):
        root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        )
        bundle = os.path.join(root, 'member_desk_bundle.json')
        grades = os.path.join(root, 'data', 'engine', 'per_area_grade.json')
        if not (os.path.exists(bundle) and os.path.exists(grades)):
            raise unittest.SkipTest('per-area board inputs are not in this tree')
        with open(bundle, encoding='utf-8') as handle:
            desk = json.load(handle)
        cls.desk = desk['members']
        cls.members = [row['name'] for row in cls.desk]
        cls.tier, cls.rank = TU.legislative_press_tiers(
            cls.members, mode='per_area_v1')

    def test_the_board_of_record_is_what_tier_util_seats_today(self):
        """13 seats, and the published board equals the live recomputation.

        member_desk_bundle.json's per-member ``tier``/``tierrank`` IS the board
        of record -- the chain wrote it. Checking it against tier_util's
        per_area_v1 seating recomputed from the live inputs is a real
        cross-check (two producers, one answer) that survives a refresh; a name
        list is not.
        """
        seated = [name for name in self.members if self.tier[name] == 1]
        self.assertEqual(len(seated), 13)
        self.assertEqual(sorted(self.rank[name] for name in seated),
                         list(range(1, 14)))
        disagreed = [row['name'] for row in self.desk
                     if (row.get('tier'), row.get('tierrank'))
                     != (self.tier[row['name']], self.rank[row['name']])]
        self.assertEqual(disagreed, [],
                         'board of record disagrees with tier_util per_area_v1')
        print('board-of-record receipt: Tier 1 in rank order = %s'
              % ', '.join(sorted(seated, key=lambda name: self.rank[name])))

    def test_the_per_area_board_promotes_the_adjudicated_pair(self):
        """The 2026-07-29 factor-only directive pushed Huizenga and Kim OUT of
        Tier 1 and accepted that as a cost (tier_util.py:39-40). The per-area
        board seats them back on measured per-area activity, with no pin and no
        override, and the Senate members it displaces pay for it.

        RETIRED 2026-09-17: the exact tiers (Huizenga/Kim 1, Crapo 2, Cortez
        Masto 3) were chain numbers on a board every refresh rebuilds. The
        DIRECTION is the design claim and is what is asserted; the frozen
        counterpart is test_per_area_board.PrototypeFixture's
        test_ungated_board_reproduces_the_published_sweep_row, which names
        Cramer/Crapo/Rounds out and Huizenga/Sessions/Kim in from the 2026-09-15
        CSV.
        """
        self.assertEqual(TU.load_tier_overrides(), {})
        self.assertEqual(TU.load_tier_positions(), {})
        factor_tier, factor_rank = TU.legislative_press_tiers(
            self.members, mode='factor')
        for member in ('Bill Huizenga', 'Young Kim'):
            with self.subTest(promoted=member):
                self.assertLess(self.rank[member], factor_rank[member])
        for member in ('Mike Crapo', 'Mike Rounds', 'Kevin Cramer'):
            with self.subTest(displaced=member):
                self.assertGreater(self.rank[member], factor_rank[member])
        for member in ('Brad Sherman', 'Tim Scott', 'Juan Vargas',
                       'Bill Huizenga', 'Young Kim'):
            with self.subTest(seated=member):
                self.assertIn(self.tier[member], (1, 2, 3))
        print('adjudicated receipt: %s' % '; '.join(
            '%s per_area t%s r%s / factor t%s r%s'
            % (member, self.tier[member], self.rank[member],
               factor_tier[member], factor_rank[member])
            for member in ('Bill Huizenga', 'Young Kim', 'Mike Crapo',
                           'Mike Rounds', 'Catherine Cortez Masto')))

    def test_the_ledger_still_credits_all_five(self):
        root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        )
        factors = TU.load_primacy_factors(root)
        self.assertTrue(all(
            member in factors
            for member in (
                'Brad Sherman', 'Bill Huizenga', 'Young Kim',
                'Catherine Cortez Masto', 'Tim Scott',
            )
        ))

    def test_the_gate_and_buckets_are_unchanged_under_the_new_board(self):
        # R4: PRIMACY_MIN_CUPRIM is KEPT under per_area_v1.
        self.assertEqual(TU.BUCKETS, (13, 24, 48))
        self.assertEqual(TU.PRIMACY_MIN_CUPRIM, 0.75)
        self.assertEqual(sum(1 for r in self.members if self.tier[r] == 1), 13)
        self.assertEqual(sum(1 for r in self.members if self.tier[r] == 2), 24)
        self.assertEqual(sum(1 for r in self.members if self.tier[r] == 3), 48)

    def test_tier_is_exactly_the_fixed_bucket_of_the_seated_rank(self):
        """The invariant honors_views.py:187 now RUNS under this mode.

        That guard rebuilds priority_tiers from the position in board['order']
        as 13/37/85 and rejects any generation that disagrees. It was skipping
        per_area_v1 silently until 2026-09-16; this proves admitting the mode
        does not turn the guard red.
        """
        for name in self.members:
            seat = self.rank.get(name)
            expected = 0
            if seat:
                expected = (1 if seat <= 13 else 2 if seat <= 37
                            else 3 if seat <= 85 else 0)
            self.assertEqual(self.tier[name], expected, name)


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromModule(
            __import__(__name__)
        )
    )
    failed = len(result.failures) + len(result.errors)
    print('%d passed, %d failed' % (result.testsRun - failed, failed))
    raise SystemExit(1 if failed else 0)

# -*- coding: utf-8 -*-
"""Tests for DEMO_POTENTIAL (design-d2-d5.md D3): the R7 footprint dial on
fec/district/demographic_potential.py. Registered beside test_demographic_potential
(fec/tests/run_tests.py, _LANE_MODULES).

The Davis/gate-census/reversal tests read the real LIVE data artifacts
(district_industry_4digit.json, member_desk_bundle.json) read-only, the same
way run_tests.py's own kernel already depends on the real 533-member roster
(MC.members()) rather than a synthetic fixture. Those tests skip cleanly if
the LIVE tree's data files are not present in this checkout (e.g. a bare
worktree).

The headline footprint numbers pinned here are Davis 71.3 / rank 42 /
market_count 198 / tier 'Very High' (test_davis_footprint_score), measured
under the spec-faithful population: R7's employment_lq >= 1.0 gate applied,
districts and states sharing one percentile population exactly as growth_room
already does. 173 of 435 districts pass that gate.

design-d2-d5.md D3.e and evidence/footprint-potential.md instead report 87.1 /
rank 36 / Exceptional. Those came from the read-only footprint_v2.py
prototype, which applies no gate and models no state markets; they are pinned
here only as a formula check, inside
test_formula_matches_prototype_population, which reproduces that prototype's
own population deliberately. The coordinator ruled the 71.3 figures correct
and the 87.1 figures prototype-only -- see evidence/d3-implementation.md.

TWO PLANES (FIX-UP 2026-09-17). DEMO_POTENTIAL then SHIPPED at 'footprint'
(demographic_potential.py:30) and the chain republished member_desk_bundle.json
under it: the bundle now stores score_version 3.0 records over 198 qualified
markets. Three tests here read that bundle as if it still held growth_room's
output and went red with no shipped code change --
test_growth_room_dial_is_byte_identical compared a growth_room re-score against
footprint records (47.1 != 67.2 at FL-04), and both reversal tests correlated
footprint against itself (rho 1.0 and 0.98). Same defect as the board pins
commit 4dc148fc1 retired; see evidence/board-tests-and-order.md.

LIVE plane (always on) -- self-consistency only: the SHIPPED dial reproduces
every stored demographic_potential field of every qualified market, the gate
census equals evaluate()'s own verdict over the same districts, growth_room is
still a real and distinct alternative, and the footprint composite reverses
growth_room's rank order on one common population.

FIXTURE plane -- the exact ruled numbers (Davis 71.3 / 42 / 198 / Very High,
gate 173 of 435, prototype 87.1 / 36 / 431 / Exceptional). They are functions of
district_industry_4digit.json and the bundle, both chain-regenerated, so they
run only under ``DEMO_FOOTPRINT_FIXTURE=1``: the ruling stays reproducible on
demand while a refresh can never turn the kernel red. Every one of them is also
printed as a receipt by the live plane.

RETIRED OUTRIGHT: rho -0.2345 and -0.460 "against today". Both were measured
against the stored growth_room board over ITS own 327-market population, which
no longer exists on disk -- the same class as the two board pins 4dc148fc1
retired outright. They are replaced by recomputing BOTH sides from today's
inputs over one shared population.
"""
import copy
import importlib
import json
import os
import sys
import unittest

FEC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, FEC)

import district.demographic_potential as dp

LIVE = r"C:\Users\pgoff\OneDrive\Desktop\Melange Systems"
INDUSTRY_4DIGIT_PATH = os.path.join(
    LIVE, 'data', 'lobbying', 'district_industry_4digit.json')
BUNDLE_PATH = os.path.join(LIVE, 'member_desk_bundle.json')
_LIVE_DATA_AVAILABLE = (
    os.path.isfile(INDUSTRY_4DIGIT_PATH) and os.path.isfile(BUNDLE_PATH))

# The exact ruled numbers are functions of chain-regenerated artifacts, so they
# are opt-in. Mirrors PER_AREA_FIXTURE in fec/tests/engine/test_per_area_board.py.
FIXTURE_ENABLED = os.environ.get('DEMO_FOOTPRINT_FIXTURE') == '1'
FIXTURE_OFF = (
    'DEMO_FOOTPRINT_FIXTURE=1 not set: the exact 2026-09-16 footprint numbers '
    '(Davis 71.3 / 42 / 198 / Very High, gate 173 of 435, prototype 87.1 / 36 / '
    '431 / Exceptional) are pinned off by default so a chain refresh cannot '
    'turn the kernel red. The live plane recomputes and prints all of them.')


def _reload(mode):
    """Reload dp with DEMO_POTENTIAL pinned to `mode`, mirroring how
    test_primacy_tier-style dial tests must re-import a module-level-constant
    dial (design-d2-d5.md D2.f note on PRIMACY_BOARD applies identically to
    DEMO_POTENTIAL: it is read once at import, not re-read per call)."""
    os.environ['DEMO_POTENTIAL'] = mode
    importlib.reload(dp)
    return dp


_industry_4digit_cache = None


def _load_industry_4digit():
    global _industry_4digit_cache
    if _industry_4digit_cache is None:
        with open(INDUSTRY_4DIGIT_PATH, encoding='utf-8') as handle:
            _industry_4digit_cache = json.load(handle)
    return _industry_4digit_cache


_district_members_cache = None


def _load_district_state_members():
    """Read-only extraction of every district/state member's
    demographic_potential inputs from the real LIVE bundle: industry,
    financial, hmda, employers, community (for the structural companion),
    and the existing demographic_potential sub-object (today's growth_room
    output, already computed by production). Only these fields are kept,
    not the full ~75MB member record."""
    global _district_members_cache
    if _district_members_cache is None:
        with open(BUNDLE_PATH, encoding='utf-8') as handle:
            bundle = json.load(handle)
        kept = []
        for member in bundle['members']:
            industry = member.get('industry') or {}
            if (industry.get('kind') not in ('district', 'state')
                    or not industry.get('geo')):
                continue
            kept.append({
                'name': member.get('name'),
                'industry': industry,
                'financial': member.get('financial'),
                'hmda': member.get('hmda'),
                'employers': member.get('employers'),
                'community': member.get('community'),
                'demographic_potential': copy.deepcopy(
                    member.get('demographic_potential') or {}),
            })
        _district_members_cache = kept
    return _district_members_cache


def _two_district_artifact(target_geo, target_cu_emp, other_cu_emp,
                            all_emp=1000.0, other_geo='ZZ-99'):
    """A minimal 2-district CBP artifact where employment_lq(target_geo) is
    fully controlled by the cu_emp split, since both rows share all_emp:
    LQ = 2*target_cu_emp / (target_cu_emp + other_cu_emp)."""
    def _row(cu_emp):
        return {
            'cu_benchmark': {'emp_522130': cu_emp, 'est_522130': cu_emp},
            'four_digit': {'999999': {'emp': all_emp, 'est': all_emp}},
        }
    return {
        'districts': {
            target_geo: _row(target_cu_emp),
            other_geo: _row(other_cu_emp),
        },
    }


class FootprintFixtureTests(unittest.TestCase):
    """Synthetic-fixture tests: no LIVE data required, always run."""

    def test_weights_sum_and_uninverted(self):
        _reload('footprint')
        self.assertEqual(
            set(dp.SCORE_WEIGHTS),
            {'employment_lq', 'establishment_lq',
             'cu_branch_share', 'cu_deposit_share'})
        self.assertAlmostEqual(sum(dp.SCORE_WEIGHTS.values()), 1.0)

        # Un-inversion: a district with MORE current CU branch/deposit share
        # must score HIGHER on that component, the opposite of growth_room's
        # "1 - value" whitespace direction.
        members = [
            {'name': 'HighShare',
             'industry': {'geo': 'ZZ-01', 'kind': 'district'},
             'demographic_potential': {'qualified': True},
             'financial': {
                 'cu_branch_share': 0.9,
                 'banks': {'deposits_kusd': 100.0},
                 'cus': {'est_shares_kusd': 900.0}}},
            {'name': 'LowShare',
             'industry': {'geo': 'ZZ-02', 'kind': 'district'},
             'demographic_potential': {'qualified': True},
             'financial': {
                 'cu_branch_share': 0.1,
                 'banks': {'deposits_kusd': 900.0},
                 'cus': {'est_shares_kusd': 100.0}}},
        ]
        dp.score_members(members, {'districts': {}})
        high = members[0]['demographic_potential']['components']
        low = members[1]['demographic_potential']['components']
        self.assertEqual(high['cu_branch_share'], 1.0)
        self.assertEqual(low['cu_branch_share'], 0.0)
        self.assertEqual(high['cu_deposit_share'], 1.0)
        self.assertEqual(low['cu_deposit_share'], 0.0)
        self.assertGreater(
            members[0]['demographic_potential']['score'],
            members[1]['demographic_potential']['score'])

    def test_gate_is_employment_lq(self):
        _reload('footprint')
        unqualified_artifact = _two_district_artifact('TEST', 99.0, 101.0)
        result = dp.evaluate('TEST', {}, unqualified_artifact)
        self.assertAlmostEqual(result['employment_lq'], 0.99)
        self.assertFalse(result['qualified'])
        self.assertEqual(result['reason'], 'no_cu_employment_concentration')

        qualified_artifact = _two_district_artifact('TEST', 101.0, 99.0)
        result = dp.evaluate('TEST', {}, qualified_artifact)
        self.assertAlmostEqual(result['employment_lq'], 1.01)
        self.assertTrue(result['qualified'])
        self.assertNotIn('reason', result)

    def test_labels_do_not_say_whitespace(self):
        _reload('footprint')
        for key, label in dp.COMPONENT_LABELS.items():
            self.assertNotIn('whitespace', label.lower(), key)
            self.assertNotIn('inverted', label.lower(), key)
        for key, definition in dp.COMPONENT_DEFINITIONS.items():
            for field, value in definition.items():
                if isinstance(value, str):
                    self.assertNotIn(
                        'whitespace', value.lower(), (key, field))
                    self.assertNotIn(
                        'inverted', value.lower(), (key, field))
        self.assertNotIn('whitespace', dp.WEIGHT_RATIONALE.lower())
        self.assertNotIn('inverted', dp.WEIGHT_RATIONALE.lower())

    def test_growth_room_is_default_off(self):
        """The literal growth_room dict is untouched by the dial existing at
        all (design-d2-d5.md D3.a: "Keep both dicts... so growth_room is the
        untouched literal above")."""
        _reload('growth_room')
        self.assertEqual(dp.SCORE_WEIGHTS, {
            'lq_strength': 0.20,
            'competitive_shift': 0.20,
            'sector_breadth': 0.15,
            'cu_branch_whitespace': 0.15,
            'cu_deposit_whitespace': 0.05,
            'lmi_lending_opportunity': 0.10,
            'large_employer_reach': 0.08,
            'small_business_reach': 0.07,
        })
        self.assertEqual(dp.SCORE_VERSION, '2.1')
        _reload('footprint')
        self.assertEqual(dp.SCORE_VERSION, '3.0')


@unittest.skipUnless(
    _LIVE_DATA_AVAILABLE,
    'LIVE data artifacts (district_industry_4digit.json / '
    'member_desk_bundle.json) are not present in this checkout')
class FootprintRealDataTests(unittest.TestCase):
    """Tests against the real LIVE artifacts (read-only), pinned to the
    numbers design-d2-d5.md D3.e and evidence/footprint-potential.md
    measured with footprint_v2.py against this same data."""

    @classmethod
    def setUpClass(cls):
        cls.industry_4digit = _load_industry_4digit()

    def _footprint_members(self):
        """Fresh deep copy, footprint-qualified via evaluate(), then scored."""
        _reload('footprint')
        members = copy.deepcopy(_load_district_state_members())
        for member in members:
            geo = member['industry']['geo']
            member['demographic_potential'] = dp.evaluate(
                geo, member['industry'], self.industry_4digit)
        dp.score_members(members, self.industry_4digit)
        return {m['industry']['geo']: m for m in members}

    def test_the_shipped_dial_reproduces_the_published_record(self):
        """The bundle's demographic_potential IS the shipped dial's output.

        Replaces test_growth_room_dial_is_byte_identical, which asserted the
        bundle still held growth_room's numbers. It does not: DEMO_POTENTIAL
        ships at 'footprint' and the chain republished under it. The revert
        check that still means something is this one -- re-run evaluate() +
        score_members() under the SHIPPED dial from the same inputs and every
        stored field of every qualified market must come back identical.
        """
        by_geo = self._footprint_members()
        originals = {m['industry']['geo']: m['demographic_potential']
                     for m in _load_district_state_members()}
        compared = 0
        for geo, member in by_geo.items():
            base = originals[geo]
            if not base.get('qualified'):
                continue
            compared += 1
            for field in ('score', 'rank', 'market_count', 'percentile',
                          'tier', 'components', 'raw', 'available',
                          'evidence_coverage', 'sensitivity',
                          'structural_adjustment'):
                self.assertEqual(
                    member['demographic_potential'].get(field),
                    base.get(field), 'geo=%s field=%s' % (geo, field))
        self.assertGreater(compared, 0)
        print('shipped-dial receipt: %d qualified markets reproduced field for '
              'field under DEMO_POTENTIAL=%s' % (compared, dp.DEMO_POTENTIAL))

    def test_growth_room_is_a_real_alternative_dial(self):
        """Negative control for the test above: the reproduction is the dial
        agreeing with itself, not the two dials being the same code path."""
        _reload('growth_room')
        self.assertEqual(dp.SCORE_VERSION, '2.1')
        growth = copy.deepcopy(_load_district_state_members())
        dp.score_members(growth, self.industry_4digit)
        growth_scores = {m['industry']['geo']:
                         m['demographic_potential'].get('score')
                         for m in growth}
        _reload('footprint')
        self.assertEqual(dp.SCORE_VERSION, '3.0')
        moved = [geo for geo, member in self._footprint_members().items()
                 if member['demographic_potential'].get('score')
                 != growth_scores.get(geo)]
        self.assertGreater(len(moved), 0)

    def test_davis_score_is_the_published_record_recomputed(self):
        """Davis's score/rank/tier recomputed from today's district inputs must
        equal the bundle's current record, and his raw LQ must equal what
        _unrounded_cu_lq derives from the same artifact. The 71.3 / 42 / 198 /
        Very High figures are receipts here and pins on the fixture plane."""
        by_geo = self._footprint_members()
        davis = by_geo['NC-01']['demographic_potential']
        published = {m['industry']['geo']: m['demographic_potential']
                     for m in _load_district_state_members()}['NC-01']
        for field in ('score', 'rank', 'market_count', 'tier', 'percentile',
                      'components', 'raw'):
            self.assertEqual(davis.get(field), published.get(field), field)
        metrics = dp._cu_metrics_for(self.industry_4digit)
        employment_lq, establishment_lq = dp._unrounded_cu_lq(metrics, 'NC-01')
        self.assertAlmostEqual(davis['raw']['employment_lq'], employment_lq,
                               places=6)
        self.assertAlmostEqual(davis['raw']['establishment_lq'],
                               establishment_lq, places=6)
        self.assertGreaterEqual(employment_lq, dp.FOOTPRINT_EMPLOYMENT_LQ_MIN)
        print('Davis receipt: score %s rank %s of %s markets, tier %s, '
              'employment_lq %.6f'
              % (davis['score'], davis['rank'], davis['market_count'],
                 davis['tier'], employment_lq))

    def test_the_gate_census_is_evaluate_own_verdict(self):
        """design-d2-d5.md D3.b: 'How many districts pass employment_lq >= 1.0
        is NOT IN EVIDENCE... Measure it before APPLY'. Measured here two ways
        over the same artifact -- the raw LQ comparison and evaluate()'s own
        qualified flag -- which must agree, and the gate must be neither vacuous
        nor total. The 173-of-435 count is a receipt here and a fixture pin."""
        _reload('footprint')
        districts = (self.industry_4digit.get('districts') or {})
        metrics = dp._cu_metrics_for(self.industry_4digit)
        by_lq = sum(
            1 for geo in districts
            if (dp._unrounded_cu_lq(metrics, geo)[0] or 0.0)
            >= dp.FOOTPRINT_EMPLOYMENT_LQ_MIN)
        by_evaluate = sum(
            1 for geo in districts
            if dp.evaluate(geo, {}, self.industry_4digit).get('qualified'))
        self.assertEqual(by_lq, by_evaluate)
        self.assertGreater(by_evaluate, 0)
        self.assertLess(by_evaluate, len(districts))
        print('gate receipt: %d of %d districts pass employment_lq >= %s'
              % (by_evaluate, len(districts), dp.FOOTPRINT_EMPLOYMENT_LQ_MIN))

    def test_the_footprint_composite_reverses_growth_room(self):
        """R7's whole point, recomputed on ONE shared population.

        Replaces test_reversal_against_today, which correlated the footprint
        board against a 'today' that IS the footprint board (rho 1.0). Both
        sides are now built from today's inputs over the markets the shipped
        board qualifies, so the comparison is a property of the two formulas
        rather than of whichever dial last wrote the bundle.
        """
        footprint_scores = {
            geo: member['demographic_potential']['score']
            for geo, member in self._footprint_members().items()
            if member['demographic_potential'].get('score') is not None}
        qualified = set(footprint_scores)
        _reload('growth_room')
        growth = copy.deepcopy([m for m in _load_district_state_members()
                                if m['industry']['geo'] in qualified])
        for member in growth:
            member['demographic_potential'] = {'qualified': True}
        dp.score_members(growth, self.industry_4digit)
        growth_scores = {m['industry']['geo']:
                         m['demographic_potential']['score']
                         for m in growth
                         if m['demographic_potential'].get('score') is not None}
        self.assertEqual(set(growth_scores), qualified)
        rho = dp._rank_correlation(footprint_scores, growth_scores)
        _reload('footprint')
        self.assertLess(rho, -0.15)
        print('reversal receipt: rho %.4f over %d shared markets'
              % (rho, len(qualified)))

    def test_davis_footprint_score_fixture(self):
        """MEASURED, not the design brief's target.

        design-d2-d5.md D3.e / evidence/footprint-potential.md pin Davis to
        score 87.1, rank 36, market_count 435, tier Exceptional. Those
        numbers come from footprint_v2.py's read-only prototype, which (a)
        never applies any qualification gate to its composite -- it scores
        literally every one of the 435 raw district keys in
        district_industry_4digit.json / district_financial.json -- and (b)
        never models state markets at all (its own 'all_geos' is districts
        only). evaluate()+score_members() cannot reproduce that: R7's own
        gate (employment_lq >= 1.0) legitimately restricts market_members
        to qualifying markets -- exactly as it already does for growth_room,
        where design-d2-d5.md D3.b itself says today's gate "passes 298 of
        435 districts (+29 states = the 327 market_count Davis shows)" --
        and states already share one percentile population with districts
        in that same existing architecture. Applying both correctly (not
        optional: R7 names the gate explicitly, and score_members's
        district/state mixing is unchanged code, not something this dial
        may selectively disable) measures Davis at 71.3 / rank 42 /
        market_count 198 / tier 'Very High', not Exceptional.

        The formula itself is verified byte-correct, independent of that
        population question: Davis's raw employment_lq/establishment_lq
        here reproduce the documented 2.720625/1.177051 to 10 significant
        figures (see test_formula_matches_prototype_population, which
        reproduces the prototype's own districts-only, gate-free population
        and gets score 86.9 [not 87.1 -- 4 districts in the raw artifacts
        have no bundle member at all, per footprint-potential.md's own
        "CA-14, FL-20, GA-13, TX-23 are NOT FOUND" -- and this test's 431
        bundle members are short exactly those 4] and rho -0.45999... ==
        -0.460 to 3dp against today's Potential, an exact match). This is a
        genuine conflict between R7's gate text and the design brief's
        test-table numbers for Paul to rule on, not an implementation bug;
        see L\\evidence\\d3-implementation.md.
        """
        if not FIXTURE_ENABLED:
            self.skipTest(FIXTURE_OFF)
        by_geo = self._footprint_members()
        davis = by_geo['NC-01']['demographic_potential']
        self.assertAlmostEqual(davis['raw']['employment_lq'], 2.720625, places=6)
        self.assertEqual(davis['score'], 71.3)
        self.assertEqual(davis['rank'], 42)
        self.assertEqual(davis['market_count'], 198)
        self.assertEqual(davis['tier'], 'Very High')

    def test_formula_matches_prototype_population(self):
        """Confirms the scoring formula itself (percentiles, un-inversion,
        weights) is correct by reproducing footprint_v2.py's own population
        exactly: gate-free, districts only. This isolates "is the formula
        right" from "does the real gate + state-inclusive market population
        reproduce the brief's target numbers" (it does not -- see
        test_davis_footprint_score_fixture).

        FIX-UP 2026-09-17: the composite identity and the reversal are asserted
        from today's inputs; the prototype's 87.1 / 36 / Exceptional and its
        rho -0.460 against the RETIRED growth_room board are fixture pins and
        receipts. `today_scores` used to be read off the bundle, which now holds
        footprint output -- that is what made this rho 0.98."""
        _reload('footprint')
        members = copy.deepcopy(_load_district_state_members())
        today_scores = {}
        district_members = []
        for member in members:
            if member['industry']['kind'] != 'district':
                continue
            today = member['demographic_potential']
            geo = member['industry']['geo']
            if today.get('qualified') and today.get('score') is not None:
                today_scores[geo] = today['score']
            member['demographic_potential'] = {'qualified': True}
            district_members.append(member)
        dp.score_members(district_members, self.industry_4digit)
        by_geo = {m['industry']['geo']: m for m in district_members}
        davis = by_geo['NC-01']['demographic_potential']
        footprint_scores = {
            geo: m['demographic_potential']['score']
            for geo, m in by_geo.items()
        }
        # The formula itself: the published score is the weighted composite of
        # the published components, recomputed here rather than pinned.
        self.assertEqual(davis['market_count'], len(district_members))
        self.assertAlmostEqual(
            davis['score'],
            round(100.0 * sum(weight * davis['components'][key]
                              for key, weight in dp.SCORE_WEIGHTS.items()), 1),
            places=6)
        self.assertEqual(
            davis['rank'],
            1 + sum(1 for score in footprint_scores.values()
                    if score > davis['score']))
        # The reversal, both sides recomputed on this same gate-free population.
        _reload('growth_room')
        growth_members = copy.deepcopy(
            [m for m in _load_district_state_members()
             if m['industry']['kind'] == 'district'])
        for member in growth_members:
            member['demographic_potential'] = {'qualified': True}
        dp.score_members(growth_members, self.industry_4digit)
        growth_scores = {m['industry']['geo']:
                         m['demographic_potential']['score']
                         for m in growth_members}
        rho = dp._rank_correlation(footprint_scores, growth_scores)
        _reload('footprint')
        self.assertLess(rho, -0.15)
        print('prototype receipt: Davis %s rank %s of %s, tier %s; rho %.4f '
              '(brief: 87.1 / 36 / 435 / Exceptional, rho -0.460)'
              % (davis['score'], davis['rank'], davis['market_count'],
                 davis['tier'], rho))
        if FIXTURE_ENABLED:
            self.assertEqual(davis['market_count'], 431)
            self.assertAlmostEqual(davis['score'], 87.1, delta=0.5)
            self.assertLessEqual(abs(davis['rank'] - 36), 2)
            self.assertEqual(davis['tier'], 'Exceptional')
        # `today_scores` is the board the bundle publishes -- now footprint
        # output, not growth_room's. Every market it names must exist in the
        # gate-free population this test scored.
        self.assertTrue(set(today_scores) <= set(footprint_scores))
        self.assertTrue(today_scores)

    @unittest.skipUnless(FIXTURE_ENABLED, FIXTURE_OFF)
    def test_footprint_gate_census(self):
        """RETIRED 2026-09-17 to the fixture plane: 173 and 435 are counts over
        a chain-regenerated artifact, and
        test_the_gate_census_is_evaluate_own_verdict is the live replacement.

        design-d2-d5.md D3.b: 'How many districts pass employment_lq >=
        1.0 is NOT IN EVIDENCE... Measure it before APPLY'. Measured here
        over all 435 raw district keys in district_industry_4digit.json
        (the same universe footprint_v2.py calls 435 districts) -- 173
        pass. This is far below the vacuous 435/435 the brief's OTHER,
        weaker 'branches>0 or employment_lq>0' proposal gate measured
        (design-d2-d5.md D3.b cites that figure); the two gates are not
        the same and must not be conflated."""
        _reload('footprint')
        districts = (self.industry_4digit.get('districts') or {})
        metrics = dp._cu_metrics_for(self.industry_4digit)
        passing = 0
        total = 0
        for geo in districts:
            total += 1
            employment_lq, _est_lq = dp._unrounded_cu_lq(metrics, geo)
            if employment_lq is not None and employment_lq >= 1.0:
                passing += 1
        self.assertEqual(total, 435)
        self.assertEqual(passing, 173)

    # RETIRED OUTRIGHT 2026-09-17, not moved to the fixture plane:
    # test_reversal_against_today and test_growth_room_dial_is_byte_identical.
    # Both compared against member_desk_bundle.json's stored
    # demographic_potential as a GROWTH_ROOM board. DEMO_POTENTIAL now ships at
    # 'footprint' and the chain republished the bundle under it, so 'today' IS
    # the footprint board: the reversal read rho 1.0 and the byte-identity read
    # 47.1 != 67.2 at FL-04. There is no frozen counterpart to park them behind
    # a flag -- the population they measured no longer exists on disk, exactly
    # like the two board pins commit 4dc148fc1 retired outright. Replaced by
    # test_the_shipped_dial_reproduces_the_published_record (the revert check
    # that still has meaning), test_growth_room_is_a_real_alternative_dial (its
    # negative control) and test_the_footprint_composite_reverses_growth_room
    # (both sides recomputed on one shared population).

    def test_metadata_growth_room_byte_identical_shape(self):
        _reload('growth_room')
        meta = dp.metadata(self.industry_4digit)
        self.assertNotIn('footprint_employment_lq_min', meta)
        self.assertEqual(meta['score_version'], '2.1')
        _reload('footprint')
        meta = dp.metadata(self.industry_4digit)
        self.assertIn('footprint_employment_lq_min', meta)
        self.assertEqual(meta['score_version'], '3.0')


if __name__ == '__main__':
    unittest.main()

# -*- coding: utf-8 -*-
"""D-034: the desk postflight follows DEMO_POTENTIAL.

fec/audit/dashboard_integrity.py asserted the growth-room sticker shape
unconditionally, so the first chain run under the new default dial died inside
build_primacy_surfaces.py after 3831.7 s with

    demographic-potential sensitivity audit is missing;
    demographic-potential structural adjustment is missing;
    qualified demographic potential has no signals for Aaron Bean;
    incomplete CBP large-employer evidence was scored as available for ...

(audits/members_section_20260914/phase2/chain_run_20260917e.log).  All four are
statements about growth_room's eight-component score, not about a defect.

These tests drive dashboard_integrity.check_demographic_potential with a small
synthetic bundle in BOTH modes.  The stickers and the metadata block are built
by the REAL producer -- demographic_potential.score_members / .metadata with
_score_raw monkeypatched to supply raw feature values -- so the percentile,
score, rank, tier, sensitivity and structural machinery under test is the same
code make_member_desk runs; only the CBP/LODES/NCUA inputs are synthetic.
"""
import copy
import importlib
import os
import sys
import unittest

FEC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, FEC)

import district.demographic_potential as dp
import audit.dashboard_integrity as di


# DEMO_POTENTIAL is read once at import (demographic_potential.py:30), so a dial
# test must reload the module -- the same pattern test_demographic_potential_
# footprint.py uses.  reload() mutates the module object in place, so
# dashboard_integrity's own ``DP`` alias re-parameterises with it; the identity
# assertion below is what makes that guarantee explicit rather than assumed.
def _reload(mode):
    os.environ['DEMO_POTENTIAL'] = mode
    importlib.reload(dp)
    assert di.DP is dp, 'dashboard_integrity lost its demographic_potential alias'
    return dp


COMMUNITY_META = {
    'schema': 'district-community-acs-v1',
    'vintage': 2024,
    'survey': 'ACS 1-year',
    'profile_families': {'DP03': 'DP03', 'DP04': 'DP04', 'DP05': 'DP05'},
    'official_urls': {'acs': 'https://api.census.gov/data/2024/acs/acs1'},
}

# Five district markets and two state markets: enough population for tie-aware
# percentiles to spread, and CA-04 deliberately carries two members so the
# cross-member score/rank agreement branch is exercised.
FOOTPRINT_RAW = {
    ('district', 'CA-04'): {
        'employment_lq': 2.720625, 'establishment_lq': 1.91,
        'cu_branch_share': 0.312, 'cu_deposit_share': 0.244},
    ('district', 'TX-07'): {
        'employment_lq': 1.402, 'establishment_lq': 1.20,
        'cu_branch_share': 0.180, 'cu_deposit_share': 0.101},
    ('district', 'OH-09'): {
        'employment_lq': 1.051, 'establishment_lq': 1.02,
        'cu_branch_share': 0.090, 'cu_deposit_share': 0.061},
    ('district', 'WA-02'): {
        'employment_lq': 2.010, 'establishment_lq': 1.55,
        'cu_branch_share': 0.271, 'cu_deposit_share': None},
    ('district', 'FL-15'): {
        'employment_lq': 1.220, 'establishment_lq': 1.11,
        'cu_branch_share': 0.140, 'cu_deposit_share': 0.088},
    ('state', 'UT'): {
        'employment_lq': 3.100, 'establishment_lq': 2.40,
        'cu_branch_share': 0.402, 'cu_deposit_share': 0.351},
    ('state', 'NY'): {
        'employment_lq': 1.010, 'establishment_lq': 0.98,
        'cu_branch_share': 0.070, 'cu_deposit_share': 0.052},
}

GROWTH_ROOM_RAW = {
    ('district', 'CA-04'): {
        'lq_strength': 0.61, 'competitive_shift': 0.044, 'sector_breadth': 3.0,
        'cu_branch_whitespace': 0.312, 'cu_deposit_whitespace': 0.244,
        'lmi_lending_opportunity': 0.38,
        # employers.large.complete is False for CA-04 below, so the reach
        # feature MUST be withheld -- that is the rule the fourth failing
        # assertion enforces, and it stays live under growth_room.
        'large_employer_reach': None, 'small_business_reach': 812.0},
    ('district', 'TX-07'): {
        'lq_strength': 0.33, 'competitive_shift': 0.021, 'sector_breadth': 2.0,
        'cu_branch_whitespace': 0.180, 'cu_deposit_whitespace': 0.101,
        'lmi_lending_opportunity': 0.22,
        'large_employer_reach': 44.0, 'small_business_reach': 640.0},
    ('district', 'OH-09'): {
        'lq_strength': 0.18, 'competitive_shift': 0.009, 'sector_breadth': 1.0,
        'cu_branch_whitespace': 0.090, 'cu_deposit_whitespace': 0.061,
        'lmi_lending_opportunity': 0.51,
        'large_employer_reach': 19.0, 'small_business_reach': 401.0},
    ('district', 'WA-02'): {
        'lq_strength': 0.47, 'competitive_shift': 0.033, 'sector_breadth': 2.0,
        'cu_branch_whitespace': 0.271, 'cu_deposit_whitespace': None,
        'lmi_lending_opportunity': 0.29,
        'large_employer_reach': 31.0, 'small_business_reach': 705.0},
    ('district', 'FL-15'): {
        'lq_strength': 0.25, 'competitive_shift': 0.015, 'sector_breadth': 2.0,
        'cu_branch_whitespace': 0.140, 'cu_deposit_whitespace': 0.088,
        'lmi_lending_opportunity': 0.34,
        'large_employer_reach': 27.0, 'small_business_reach': 522.0},
    ('state', 'UT'): {
        'lq_strength': 0.72, 'competitive_shift': 0.058, 'sector_breadth': 4.0,
        'cu_branch_whitespace': 0.402, 'cu_deposit_whitespace': 0.351,
        'lmi_lending_opportunity': 0.41,
        'large_employer_reach': 96.0, 'small_business_reach': 1204.0},
    ('state', 'NY'): {
        'lq_strength': 0.11, 'competitive_shift': 0.006, 'sector_breadth': 1.0,
        'cu_branch_whitespace': 0.070, 'cu_deposit_whitespace': 0.052,
        'lmi_lending_opportunity': 0.19,
        'large_employer_reach': 210.0, 'small_business_reach': 2410.0},
}

# CA-04 is the shared market: two members, and it is the one reporting
# incomplete CBP large-employer evidence.
MEMBER_MARKETS = (
    ('Aaron Bean', 'district', 'CA-04'),
    ('Adam Gray', 'district', 'CA-04'),
    ('Bernice Carver', 'district', 'TX-07'),
    ('Carla Doyle', 'district', 'OH-09'),
    ('Dana Ellis', 'district', 'WA-02'),
    ('Evan Fox', 'district', 'FL-15'),
    ('Fiona Grant', 'state', 'UT'),
    ('Gil Hayes', 'state', 'NY'),
)

GROWTH_ROOM_SIGNALS = [
    {'sector': 'CNS05', 'name': 'Manufacturing', 'lq': 2.10,
     'competitive_shift': 1.4, 'competitive_shift_rate': 0.031,
     'crosscheck': 'passed'},
    {'sector': 'CNS16', 'name': 'Health care', 'lq': 1.63,
     'competitive_shift': 0.9, 'competitive_shift_rate': 0.018,
     'crosscheck': 'not_covered'},
]


def _build(mode):
    """Return (bundle, members) produced by the real scorer under `mode`."""
    _reload(mode)
    raw_by_market = (
        FOOTPRINT_RAW if mode == 'footprint' else GROWTH_ROOM_RAW)
    members = []
    for name, kind, geo in MEMBER_MARKETS:
        potential = {
            'qualified': True,
            'label': dp.LABEL,
            'signals': (
                [] if mode == 'footprint'
                else copy.deepcopy(GROWTH_ROOM_SIGNALS)),
        }
        if mode == 'footprint':
            potential['employment_lq'] = raw_by_market[
                (kind, geo)]['employment_lq']
        members.append({
            'name': name,
            'industry': {'kind': kind, 'geo': geo},
            'demographic_potential': potential,
            'employers': {'large': {'complete': geo != 'CA-04'}},
        })
    members.append({
        'name': 'Unqualified Member',
        'industry': {'kind': 'district', 'geo': 'ZZ-01'},
        'demographic_potential': {
            'qualified': False,
            'label': dp.LABEL,
            'signals': [],
            'employment_lq': 0.41,
            'reason': (
                'no_cu_employment_concentration' if mode == 'footprint'
                else 'no_aligned_sector'),
        },
        'employers': {'large': {'complete': True}},
    })

    original = dp._score_raw
    dp._score_raw = lambda member, industry_4digit: dict(
        raw_by_market[(member['industry']['kind'], member['industry']['geo'])])
    try:
        stats = dp.score_members(members, {})
    finally:
        dp._score_raw = original

    bundle = {
        'community_meta': copy.deepcopy(COMMUNITY_META),
        'demographic_potential_meta': dp.metadata(
            {}, {},
            qualified_members=stats['qualified_members'],
            qualified_markets=stats['qualified_markets'],
            sensitivity_audit=stats['sensitivity_audit'],
            structural_adjustment=stats['structural_adjustment'],
        ),
    }
    return bundle, members


def _errors(bundle, members):
    errors = []
    counts = di.check_demographic_potential(bundle, members, errors)
    return errors, counts


def _sticker(members, name='Aaron Bean'):
    for member in members:
        if member['name'] == name:
            return member['demographic_potential']
    raise AssertionError('no member named ' + name)


# The four assertion families that killed chain_run_20260917e.
REGRESSION_FRAGMENTS = (
    'sensitivity audit is missing',
    'structural adjustment is missing',
    'has no signals for',
    'was scored as available for',
)


class FootprintContract(unittest.TestCase):

    def test_footprint_bundle_passes(self):
        bundle, members = _build('footprint')
        errors, counts = _errors(bundle, members)
        self.assertEqual(errors, [])
        self.assertEqual(counts[0], 8)
        self.assertEqual(sorted(counts[1]), [
            ('district', 'CA-04'), ('district', 'FL-15'),
            ('district', 'OH-09'), ('district', 'TX-07'),
            ('district', 'WA-02'), ('state', 'NY'), ('state', 'UT')])

    def test_footprint_clears_the_chain_run_assertions(self):
        """The literal symptom: none of the four messages may fire."""
        bundle, members = _build('footprint')
        errors, _counts = _errors(bundle, members)
        for fragment in REGRESSION_FRAGMENTS:
            self.assertFalse(
                [e for e in errors if fragment in e], fragment)

    def test_the_retired_growth_room_predicates_would_still_fail(self):
        """Negative control for the test above.

        Those four messages could also vanish because the checks were DELETED.
        These are the literal predicates dashboard_integrity.py evaluated before
        D-034; every one of them is false against a genuine footprint bundle,
        which is why the chain died -- and why they had to be replaced by a
        footprint contract rather than merely dropped.
        """
        bundle, members = _build('footprint')
        meta = bundle['demographic_potential_meta']
        self.assertNotEqual(meta['sensitivity_audit'].get('scenario_count'), 9)
        self.assertNotEqual(
            meta['structural_adjustment'].get('status'),
            'descriptive_companion_not_causal')
        sticker = _sticker(members)
        self.assertEqual(sticker.get('signals'), [])
        self.assertIsNone(
            sticker['available'].get('large_employer_reach'))
        self.assertIs(
            _sticker(members, 'Aaron Bean')['available'].get(
                'large_employer_reach') is False, False)

    def test_footprint_metadata_is_the_footprint_contract(self):
        bundle, _members = _build('footprint')
        meta = bundle['demographic_potential_meta']
        self.assertEqual(meta['score_version'], '3.0')
        self.assertEqual(
            set(meta['score_weights']), set(dp.SCORE_WEIGHTS_FOOTPRINT))
        self.assertEqual(meta['sensitivity_audit']['scenario_count'], 5)
        self.assertEqual(
            meta['structural_adjustment']['status'],
            'not_applicable_for_mode')

    def test_footprint_components_are_four_keys_in_unit_interval(self):
        _bundle, members = _build('footprint')
        for name, _kind, _geo in MEMBER_MARKETS:
            components = _sticker(members, name)['components']
            self.assertEqual(
                set(components), set(dp.SCORE_WEIGHTS_FOOTPRINT), name)
            self.assertTrue(
                all(0.0 <= value <= 1.0 for value in components.values()),
                (name, components))

    def test_footprint_rejects_a_growth_room_shaped_record(self):
        bundle, members = _build('footprint')
        sticker = _sticker(members)
        sticker['signals'] = copy.deepcopy(GROWTH_ROOM_SIGNALS)
        sticker['components'] = {
            key: 0.5 for key in dp.SCORE_WEIGHTS_GROWTH_ROOM}
        sticker['available'] = {
            key: True for key in dp.SCORE_WEIGHTS_GROWTH_ROOM}
        errors, _counts = _errors(bundle, members)
        self.assertTrue(
            [e for e in errors if 'carries sector signals for' in e], errors)
        self.assertTrue(
            [e for e in errors if 'scores a reach component for' in e], errors)
        self.assertTrue(
            [e for e in errors
             if 'components do not match weights for' in e], errors)

    def test_footprint_rejects_growth_room_metadata(self):
        bundle, members = _build('footprint')
        meta = bundle['demographic_potential_meta']
        meta['score_weights'] = dict(dp.SCORE_WEIGHTS_GROWTH_ROOM)
        meta['score_version'] = dp.SCORE_VERSION_GROWTH_ROOM
        meta['structural_adjustment'] = {
            'version': dp.STRUCTURAL_ADJUSTMENT_VERSION,
            'status': 'descriptive_companion_not_causal',
            'adjusted_markets': 3,
        }
        errors, _counts = _errors(bundle, members)
        self.assertTrue(
            [e for e in errors if 'weights differ from the pinned formula' in e],
            errors)
        self.assertTrue(
            [e for e in errors if 'is not the footprint contract' in e], errors)
        self.assertTrue(
            [e for e in errors
             if 'not-applicable-for-mode companion' in e], errors)

    def test_footprint_enforces_the_unqualified_stub(self):
        bundle, members = _build('footprint')
        _sticker(members, 'Unqualified Member')['tier'] = 'High'
        errors, _counts = _errors(bundle, members)
        self.assertTrue(
            [e for e in errors
             if 'does not carry the documented footprint stub' in e], errors)

    def test_footprint_unqualified_stub_needs_a_reason(self):
        bundle, members = _build('footprint')
        _sticker(members, 'Unqualified Member').pop('reason')
        errors, _counts = _errors(bundle, members)
        self.assertTrue(
            [e for e in errors
             if 'does not carry the documented footprint stub' in e], errors)

    def test_footprint_tier_must_follow_the_percentile_cuts(self):
        bundle, members = _build('footprint')
        sticker = _sticker(members, 'Gil Hayes')
        self.assertEqual(sticker['tier'], 'Qualified')
        sticker['tier'] = 'Exceptional'
        errors, _counts = _errors(bundle, members)
        self.assertTrue(
            [e for e in errors
             if 'tier does not follow the percentile cuts' in e], errors)

    def test_footprint_score_must_reconstruct(self):
        bundle, members = _build('footprint')
        for name in ('Aaron Bean', 'Adam Gray'):
            sticker = _sticker(members, name)
            sticker['score'] = round(sticker['score'] + 5.0, 1)
        errors, _counts = _errors(bundle, members)
        self.assertTrue(
            [e for e in errors if 'score does not reconstruct for' in e],
            errors)


class GrowthRoomUnchanged(unittest.TestCase):

    def test_growth_room_bundle_passes(self):
        bundle, members = _build('growth_room')
        errors, counts = _errors(bundle, members)
        self.assertEqual(errors, [])
        self.assertEqual(counts[0], 8)
        self.assertEqual(len(counts[1]), 7)

    def test_growth_room_metadata_is_unchanged(self):
        bundle, _members = _build('growth_room')
        meta = bundle['demographic_potential_meta']
        self.assertEqual(meta['score_version'], '2.1')
        self.assertNotIn('footprint_employment_lq_min', meta)
        self.assertEqual(meta['sensitivity_audit']['scenario_count'], 9)
        self.assertEqual(
            meta['structural_adjustment']['status'],
            'descriptive_companion_not_causal')

    def test_growth_room_still_requires_signals(self):
        bundle, members = _build('growth_room')
        for name in ('Aaron Bean', 'Adam Gray'):
            _sticker(members, name)['signals'] = []
        errors, _counts = _errors(bundle, members)
        self.assertTrue(
            [e for e in errors
             if 'qualified demographic potential has no signals for' in e],
            errors)

    def test_growth_room_still_guards_incomplete_cbp_large_employers(self):
        bundle, members = _build('growth_room')
        for name in ('Aaron Bean', 'Adam Gray'):
            _sticker(members, name)['available']['large_employer_reach'] = True
        errors, _counts = _errors(bundle, members)
        self.assertTrue(
            [e for e in errors
             if 'large-employer evidence was scored as available for' in e],
            errors)

    def test_growth_room_still_requires_the_structural_companion(self):
        bundle, members = _build('growth_room')
        bundle['demographic_potential_meta']['structural_adjustment'] = {
            'version': dp.STRUCTURAL_ADJUSTMENT_VERSION,
            'status': 'not_applicable_for_mode',
            'adjusted_markets': 0,
        }
        errors, _counts = _errors(bundle, members)
        self.assertTrue(
            [e for e in errors
             if 'structural adjustment is missing' in e], errors)

    def test_growth_room_still_requires_nine_sensitivity_scenarios(self):
        bundle, members = _build('growth_room')
        bundle['demographic_potential_meta'][
            'sensitivity_audit']['scenario_count'] = 5
        errors, _counts = _errors(bundle, members)
        self.assertTrue(
            [e for e in errors if 'sensitivity audit is missing' in e], errors)

    def test_growth_room_rejects_a_footprint_shaped_record(self):
        bundle, members = _build('growth_room')
        sticker = _sticker(members)
        sticker['signals'] = []
        sticker['components'] = {
            key: 0.5 for key in dp.SCORE_WEIGHTS_FOOTPRINT}
        sticker['available'] = {
            key: True for key in dp.SCORE_WEIGHTS_FOOTPRINT}
        errors, _counts = _errors(bundle, members)
        self.assertTrue(
            [e for e in errors
             if 'qualified demographic potential has no signals for' in e],
            errors)
        self.assertTrue(
            [e for e in errors
             if 'components do not match weights for' in e], errors)


if __name__ == '__main__':
    unittest.main()

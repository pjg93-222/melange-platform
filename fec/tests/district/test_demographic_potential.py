import copy
import importlib
import os
import sys
import unittest

FEC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, FEC)
# The sibling footprint suite lives next to this file and owns the read-only
# LIVE-artifact loaders reused by FootprintDefaultRealDataTests below.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import district.demographic_potential as dp

# DEMO_POTENTIAL (design-d2-d5.md D3) is read once at import
# (demographic_potential.py:30) and SCORE_WEIGHTS / SCORE_VERSION /
# COMPONENT_LABELS / COMPONENT_DEFINITIONS are all derived from it at module
# level. Assigning dp.DEMO_POTENTIAL therefore does NOT switch modes -- the
# module has to be reloaded, exactly as test_demographic_potential_footprint
# does it.
_AMBIENT_DEMO_POTENTIAL = os.environ.get('DEMO_POTENTIAL')


def _reload(mode):
    """Reload dp with DEMO_POTENTIAL pinned to `mode`."""
    os.environ['DEMO_POTENTIAL'] = mode
    importlib.reload(dp)
    return dp


def _restore_ambient_dial():
    """Undo _reload so no test leaks a dial setting into the next one."""
    if _AMBIENT_DEMO_POTENTIAL is None:
        os.environ.pop('DEMO_POTENTIAL', None)
    else:
        os.environ['DEMO_POTENTIAL'] = _AMBIENT_DEMO_POTENTIAL
    importlib.reload(dp)


try:
    import test_demographic_potential_footprint as _footprint_suite
except ImportError:  # pragma: no cover - sibling suite not in this checkout
    _footprint_suite = None

_FOOTPRINT_REAL_DATA = bool(
    _footprint_suite is not None
    and getattr(_footprint_suite, '_LIVE_DATA_AVAILABLE', False))


def industry_row(code='CNS16', lq=1.5, shift=200.0, small=False):
    return {
        'lq_national': {code: lq},
        'employment': {'by_sector': {code: 1000.0}},
        'shift_share': {code: {
            'national_growth': 50.0,
            'industry_mix': 20.0,
            'competitive_shift': shift,
        }},
        'flags': {
            'small_sectors': [code] if small else [],
            'small_base_sectors': [],
        },
    }


def four_digit(code='CNS16', cbp_lq=1.1, contradicted=False):
    return {
        'schema': 'district-industry-4digit-v1',
        'thresholds': {'lq_hi': 1.25, 'lq_lo': 0.9},
        'districts': {
            'CA-01': {
                'four_digit': {'6211': {'est': 100}},
                'small_business': {
                    '62': {'est_lt20': 80, 'est_total': 100},
                },
                'sector_agreement': {
                    code: {
                        'cbp_lq_est': cbp_lq,
                        'contradicted_high': contradicted,
                        'contradicted_low': False,
                    },
                },
            },
        },
    }


class DemographicPotentialTests(unittest.TestCase):
    """Growth-room semantics.

    Every assertion in this class predates the DEMO_POTENTIAL dial and pins
    growth_room's gate, weights and component key names. The dial's default
    is now `footprint`, so the mode is stated explicitly here instead of
    riding the default -- without this, 10 of these 17 tests assert
    footprint output against growth_room literals and go red on the dial
    flip alone (design-d2-d5.md D2.f flags the identical situation for
    PRIMACY_BOARD). No assertion was weakened, parameterised away or
    dropped; only the mode is now named. The footprint default is covered
    by FootprintDefaultTests / FootprintDefaultRealDataTests below.
    """

    def setUp(self):
        self.addCleanup(_restore_ambient_dial)
        _reload('growth_room')

    def test_aligned_relevant_sector_qualifies(self):
        result = dp.evaluate(
            'CA-01', industry_row(), four_digit(),
            {'CNS16': {'naics': '62', 'name': 'Health Care'}})
        self.assertTrue(result['qualified'])
        self.assertEqual(result['signals'][0]['sector'], 'CNS16')
        self.assertEqual(result['signals'][0]['crosscheck'], 'passed')
        self.assertAlmostEqual(
            result['signals'][0]['competitive_shift_rate'],
            200.0 / 730.0,
            places=6,
        )

    def test_four_digit_contradiction_vetoes_high_lq(self):
        result = dp.evaluate(
            'CA-01', industry_row(), four_digit(cbp_lq=0.7,
                                                contradicted=True))
        self.assertFalse(result['qualified'])
        self.assertEqual(result['reason'], 'no_aligned_sector')

    def test_small_or_nonpositive_sector_does_not_qualify(self):
        self.assertFalse(dp.evaluate(
            'CA-01', industry_row(small=True), four_digit())['qualified'])
        self.assertFalse(dp.evaluate(
            'CA-01', industry_row(shift=0), four_digit())['qualified'])

    def test_finance_sector_is_not_an_opportunity_proxy(self):
        result = dp.evaluate(
            'CA-01', industry_row(code='CNS10'),
            four_digit(code='CNS10'))
        self.assertFalse(result['qualified'])

    def test_missing_crosscheck_fails_closed_except_public_admin(self):
        source = industry_row()
        source_4digit = four_digit()
        source_4digit['districts']['CA-01']['sector_agreement'] = {}
        self.assertFalse(dp.evaluate(
            'CA-01', source, source_4digit)['qualified'])

        public_admin = industry_row(code='CNS20')
        result = dp.evaluate('CA-01', public_admin, source_4digit)
        self.assertTrue(result['qualified'])
        self.assertEqual(result['signals'][0]['crosscheck'], 'not_covered')

    def test_state_crosscheck_is_establishment_weighted(self):
        source_4digit = four_digit()
        source_4digit['districts'] = {
            'CA-01': {
                'four_digit': {'6211': {'est': 100}},
                'sector_agreement': {
                    'CNS16': {'cbp_lq_est': 0.5},
                },
            },
            'CA-02': {
                'four_digit': {'6211': {'est': 300}},
                'sector_agreement': {
                    'CNS16': {'cbp_lq_est': 1.0},
                },
            },
        }
        agreement = dp.agreement_for_geo(
            'CA', industry_row(), source_4digit)
        self.assertAlmostEqual(agreement['CNS16']['cbp_lq_est'], 0.875)
        self.assertTrue(agreement['CNS16']['contradicted_high'])
        self.assertFalse(dp.evaluate(
            'CA', industry_row(), source_4digit)['qualified'])

    def test_missing_four_digit_artifact_disables_sticker(self):
        result = dp.evaluate('CA-01', industry_row(), None)
        self.assertFalse(result['qualified'])
        self.assertEqual(
            result['reason'], 'four_digit_crosscheck_unavailable')

    def test_small_business_reach_supports_district_and_state(self):
        source = four_digit()
        source['districts']['CA-02'] = {
            'four_digit': {'6211': {'est': 50}},
            'small_business': {
                '62': {'est_lt20': 20},
                '54': {'est_lt20': 30},
            },
            'sector_agreement': {},
        }
        self.assertEqual(
            dp.small_business_for_geo('CA-01', source), 80)
        self.assertEqual(
            dp.small_business_for_geo('CA', source), 130)

    def test_incomplete_cbp_size_classes_are_not_scored_as_available(self):
        member = self._scored_member(
            'Incomplete', 'CA-01', 'district',
            0.2, 0.2, 0.2, 0.2, 2)
        member['employers']['large']['complete'] = False
        source = {
            'districts': {
                'CA-01': {
                    'small_business': {
                        code: {
                            'est_lt20': 10,
                            'lt20_complete': code != '62',
                        }
                        for code in dp.SMALL_BUSINESS_NAICS
                    },
                },
            },
        }
        dp.score_members([member], source)
        potential = member['demographic_potential']
        self.assertFalse(
            potential['available']['large_employer_reach'])
        self.assertFalse(
            potential['available']['small_business_reach'])
        self.assertEqual(
            potential['components']['large_employer_reach'], 0.5)
        self.assertEqual(
            potential['components']['small_business_reach'], 0.5)
        self.assertAlmostEqual(potential['evidence_coverage'], 0.85)

    def test_percentile_ranks_are_tie_aware(self):
        ranks = dp._percentile_ranks({
            'low': 1, 'tie-a': 2, 'tie-b': 2, 'high': 4,
        })
        self.assertEqual(ranks['low'], 0.0)
        self.assertAlmostEqual(ranks['tie-a'], 0.5)
        self.assertAlmostEqual(ranks['tie-b'], 0.5)
        self.assertEqual(ranks['high'], 1.0)

    def test_score_formula_weights_are_pinned(self):
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
        self.assertAlmostEqual(sum(dp.SCORE_WEIGHTS.values()), 1.0)

    @staticmethod
    def _scored_member(name, geo, kind, strength, cu_share,
                       branch_share, lmi, large):
        signal = {
            'sector': 'CNS16',
            'lq': 1.25 + strength,
            'competitive_shift_rate': strength,
        }
        return {
            'name': name,
            'industry': {'geo': geo, 'kind': kind},
            'demographic_potential': {
                'qualified': True,
                'signals': [signal],
            },
            'financial': {
                'cu_branch_share': branch_share,
                'banks': {'deposits_kusd': 1000 * (1 - cu_share)},
                'cus': {'est_shares_kusd': 1000 * cu_share},
            },
            'hmda': {'lmi_tract_share': lmi},
            'employers': {
                'large': {
                    'est_500plus': large,
                    'complete': True,
                },
            },
        }

    def test_score_orders_opportunity_and_inverts_cu_penetration(self):
        members = [
            self._scored_member(
                'Low', 'CA-01', 'district', 0.1, 0.40, 0.40, 0.1, 1),
            self._scored_member(
                'Middle', 'CA-02', 'district', 0.2, 0.20, 0.20, 0.2, 2),
            self._scored_member(
                'High', 'CA-03', 'district', 0.3, 0.05, 0.05, 0.3, 3),
        ]
        source = four_digit()
        source['districts'] = {
            member['industry']['geo']: {
                'small_business': {
                    '62': {'est_lt20': index * 100},
                },
            }
            for index, member in enumerate(members, 1)
        }
        stats = dp.score_members(members, source)
        scores = {
            member['name']: member['demographic_potential']['score']
            for member in members
        }
        self.assertGreater(scores['High'], scores['Middle'])
        self.assertGreater(scores['Middle'], scores['Low'])
        self.assertEqual(
            members[2]['demographic_potential']['components']
            ['cu_branch_whitespace'],
            1.0,
        )
        self.assertEqual(stats['qualified_members'], 3)
        self.assertEqual(stats['qualified_markets'], 3)
        self.assertEqual(members[2]['demographic_potential']['rank'], 1)
        self.assertEqual(
            stats['sensitivity_audit']['scenario_count'], 9)
        self.assertIn(
            'rank_min',
            members[2]['demographic_potential']['sensitivity'],
        )

    def test_same_state_members_share_one_market_rank(self):
        members = [
            self._scored_member(
                'Senator A', 'CA', 'state', 0.3, 0.05, 0.05, 0.3, 3),
            self._scored_member(
                'Senator B', 'CA', 'state', 0.3, 0.05, 0.05, 0.3, 3),
            self._scored_member(
                'Senator C', 'TX', 'state', 0.1, 0.40, 0.40, 0.1, 1),
        ]
        source = {
            'districts': {
                'CA-01': {'small_business': {'62': {'est_lt20': 300}}},
                'TX-01': {'small_business': {'62': {'est_lt20': 100}}},
            },
        }
        stats = dp.score_members(members, source)
        first = members[0]['demographic_potential']
        second = members[1]['demographic_potential']
        self.assertEqual(first['score'], second['score'])
        self.assertEqual(first['rank'], second['rank'])
        self.assertEqual(first['rank'], 1)
        self.assertEqual(first['market_count'], 2)
        self.assertEqual(stats['qualified_members'], 3)
        self.assertEqual(stats['qualified_markets'], 2)

    def test_competition_rank_skips_after_a_tie(self):
        members = [
            self._scored_member(
                'High', 'CA-01', 'district', 0.4, 0.05, 0.05, 0.4, 4),
            self._scored_member(
                'Tie A', 'CA-02', 'district', 0.2, 0.20, 0.20, 0.2, 2),
            self._scored_member(
                'Tie B', 'CA-03', 'district', 0.2, 0.20, 0.20, 0.2, 2),
            self._scored_member(
                'Low', 'CA-04', 'district', 0.1, 0.40, 0.40, 0.1, 1),
        ]
        source = {
            'districts': {
                'CA-01': {'small_business': {'62': {'est_lt20': 400}}},
                'CA-02': {'small_business': {'62': {'est_lt20': 200}}},
                'CA-03': {'small_business': {'62': {'est_lt20': 200}}},
                'CA-04': {'small_business': {'62': {'est_lt20': 100}}},
            },
        }
        dp.score_members(members, source)
        ranks = {
            member['name']: member['demographic_potential']['rank']
            for member in members
        }
        self.assertEqual(ranks, {
            'High': 1, 'Tie A': 2, 'Tie B': 2, 'Low': 4,
        })

    def test_missing_evidence_is_neutral_and_reduces_coverage(self):
        member = self._scored_member(
            'Missing', 'CA-01', 'district', 0.2, 0.2, 0.2, 0.2, 2)
        member.pop('financial')
        member.pop('hmda')
        member.pop('employers')
        dp.score_members([member], {'districts': {}})
        potential = member['demographic_potential']
        self.assertEqual(
            potential['components']['cu_branch_whitespace'], 0.5)
        self.assertEqual(
            potential['components']['small_business_reach'], 0.5)
        self.assertLess(potential['evidence_coverage'], 1.0)

    def test_structural_companion_residualizes_reach_counts(self):
        members = []
        districts = {}
        regions = ('Northeast', 'Midwest', 'South', 'West')
        for index in range(20):
            geo = 'CA-%02d' % (index + 1)
            member = self._scored_member(
                'Market %02d' % index,
                geo,
                'district',
                0.1 + index / 100.0,
                0.2,
                0.2,
                0.2,
                5 + index,
            )
            member['industry'].update({
                'employment': {'total': 100000 + index * 5000},
                'latest_year': 2023 if index else 2021,
            })
            member['community'] = {
                'region': regions[index % len(regions)],
                'estimates': {
                    'population': 600000 + index * 10000,
                    'median_household_income': 70000 + index * 1000,
                    'median_age': 35 + index / 5.0,
                },
                'derived': {
                    'population_density_sq_mile': 100 + index * 50,
                    'annual_rent_income_ratio': 0.15 + index / 1000.0,
                },
            }
            if index == 19:
                member['employers']['large']['est_500plus'] = 200
            members.append(member)
            districts[geo] = {
                'small_business': {
                    code: {
                        'est_lt20': (
                            100 + index * 10 +
                            (1000 if index == 19 else 0)
                        ),
                        'lt20_complete': True,
                    }
                    for code in dp.SMALL_BUSINESS_NAICS
                },
            }

        stats = dp.score_members(
            members, {'districts': districts})
        structural_meta = stats['structural_adjustment']
        self.assertEqual(
            structural_meta['version'],
            dp.STRUCTURAL_ADJUSTMENT_VERSION,
        )
        self.assertEqual(structural_meta['adjusted_markets'], 20)
        self.assertEqual(
            structural_meta['diagnostics']['district']
            ['large_employer_reach']['status'],
            'fit',
        )
        outlier = members[-1]['demographic_potential']
        structural = outlier['structural_adjustment']
        self.assertTrue(structural['available'])
        self.assertEqual(
            set(structural['components']), set(dp.STRUCTURAL_TARGETS))
        self.assertGreater(
            structural['components']['large_employer_reach']['residual'],
            0,
        )
        adjusted = dict(outlier['components'])
        for target in dp.STRUCTURAL_TARGETS:
            adjusted[target] = structural['components'][target][
                'adjusted_percentile']
        reconstructed = round(100 * sum(
            dp.SCORE_WEIGHTS[key] * adjusted[key]
            for key in dp.SCORE_WEIGHTS
        ), 1)
        self.assertEqual(reconstructed, structural['score'])

    def test_metadata_carries_formula_and_market_count(self):
        meta = dp.metadata(
            four_digit(), qualified_members=3, qualified_markets=2)
        self.assertEqual(meta['score_version'], dp.SCORE_VERSION)
        self.assertEqual(meta['score_weights'], dp.SCORE_WEIGHTS)
        self.assertEqual(meta['qualified_markets'], 2)
        self.assertIn('tie-aware', meta['score_formula'])
        self.assertTrue(meta['not_empirically_optimized'])
        self.assertIn('not copied', meta['weight_basis'])
        self.assertEqual(
            [row['key'] for row in meta['component_definitions']],
            list(dp.SCORE_WEIGHTS),
        )
        self.assertTrue(all(
            row['official_url'].startswith('https://')
            for row in meta['source_catalog'].values()
        ))


class FootprintDefaultTests(unittest.TestCase):
    """The footprint default's contract, on synthetic fixtures only (no LIVE
    artifacts required, so this runs in any checkout)."""

    def setUp(self):
        self.addCleanup(_restore_ambient_dial)
        _reload('footprint')

    def test_footprint_is_the_default_mode(self):
        """R7: "the footprint should be the potential". With no dial set at
        all, the module must come up in footprint."""
        os.environ.pop('DEMO_POTENTIAL', None)
        importlib.reload(dp)
        self.assertEqual(dp.DEMO_POTENTIAL, 'footprint')
        self.assertEqual(dp.SCORE_VERSION, '3.0')
        self.assertEqual(dp.SCORE_VERSION_GROWTH_ROOM, '2.1')

    def test_footprint_score_formula_weights_are_pinned(self):
        """The footprint counterpart of
        test_score_formula_weights_are_pinned, which pins the growth_room
        dict under the growth_room dial."""
        self.assertEqual(dp.SCORE_WEIGHTS, {
            'employment_lq': 0.30,
            'establishment_lq': 0.20,
            'cu_branch_share': 0.25,
            'cu_deposit_share': 0.25,
        })
        self.assertAlmostEqual(sum(dp.SCORE_WEIGHTS.values()), 1.0)

    def test_footprint_gate_constant_is_employment_lq(self):
        self.assertEqual(dp.FOOTPRINT_EMPLOYMENT_LQ_MIN, 1.0)


@unittest.skipUnless(
    _FOOTPRINT_REAL_DATA,
    'sibling test_demographic_potential_footprint or the LIVE data '
    'artifacts it reads are not available in this checkout')
class FootprintDefaultRealDataTests(unittest.TestCase):
    """The measured footprint defaults, against the real LIVE artifacts.

    These are the numbers the coordinator ruled on: the spec-faithful
    population (R7's employment_lq >= 1.0 gate applied, districts and states
    sharing one percentile population exactly as growth_room already does).
    The earlier 87.1 / rank 36 / Exceptional figures came from
    footprint_v2.py's ungated, districts-only prototype and are pinned
    separately, as a formula check, in
    test_demographic_potential_footprint.test_formula_matches_prototype_population.

    The read-only bundle/artifact loaders are reused from the sibling suite
    rather than duplicated here.
    """

    def setUp(self):
        self.addCleanup(_restore_ambient_dial)
        _reload('footprint')
        self.industry_4digit = _footprint_suite._load_industry_4digit()

    def test_davis_footprint_default_score(self):
        members = copy.deepcopy(
            _footprint_suite._load_district_state_members())
        for member in members:
            member['demographic_potential'] = dp.evaluate(
                member['industry']['geo'],
                member['industry'],
                self.industry_4digit,
            )
        dp.score_members(members, self.industry_4digit)
        by_geo = {m['industry']['geo']: m for m in members}
        davis = by_geo['NC-01']['demographic_potential']
        self.assertEqual(davis['score'], 71.3)
        self.assertEqual(davis['rank'], 42)
        self.assertEqual(davis['tier'], 'Very High')
        self.assertEqual(davis['market_count'], 198)

    def test_footprint_gate_passes_173_of_435_districts(self):
        districts = (self.industry_4digit.get('districts') or {})
        metrics = dp._cu_metrics_for(self.industry_4digit)
        passing = 0
        for geo in districts:
            employment_lq, _establishment_lq = dp._unrounded_cu_lq(
                metrics, geo)
            if (employment_lq is not None
                    and employment_lq >= dp.FOOTPRINT_EMPLOYMENT_LQ_MIN):
                passing += 1
        self.assertEqual(len(districts), 435)
        self.assertEqual(passing, 173)


if __name__ == '__main__':
    unittest.main()

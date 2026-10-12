"""Schema-2 (Mentat-anchored) Giving Forecast calculator, asserted against the shared contract fixtures and ADDENDUM section F.

The bundle and the anchor snapshot come from mentat/forecast/contracts (the same example files the R writer and the anchor builder are
asserted against, brain DE-021). The anchors example is real builder output for banks and credit unions, so the numbers below are the
section F numbers of the phase-2 design, reproduced by the console's own calculator."""
import copy
from datetime import date
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
sys.path.insert(0, str(ROOT / 'forecast' / 'contracts'))
import contract  # noqa: E402
import giving_forecast as gf  # noqa: E402

GOLDEN = Path(__file__).resolve().parent / 'fixtures' / 'giving_forecast_v2_render.json'
BANKS, CUS = 'commercial_banks', 'credit_unions'


class Today(date):
    @classmethod
    def today(cls):
        return cls(2026, 9, 30)


# The shape of mentat/forecast/refit/registry/gate_waivers.json: the quiet banks-lobbying waiver and the three House-share waivers the card shows, each with the
# error of the calculator the refit was compared with. The contract example's own House-share errors are 1.544 (banks), 1.477 (credit unions) and 0.852 (auto),
# so these comparators sit below them: the model in the example is worse than the calculator it replaced, which is what a waiver is for.
AUTO = 'auto_light_duty_manufacturing'
COMPARATORS = {BANKS: 1.053, CUS: 1.022, AUTO: 0.512}
RULING = 'Owner explicitly authorized the three House-share LOEO degradations measured by pod-final-20260930: "Waive these three cells and publish".'


def waiver_entry(term, industry, show_warning, comparator=None, gate='G6', date='2026-09-30'):
    entry = dict(gate=gate, cell=term + ' / ' + industry, term=term, industry=industry, reason=RULING if term == 'control_House' else 'owner ruling: keep log refit',
                 owner='Paul Goff', date=date, show_warning=show_warning)
    if comparator is not None:
        entry['measured'] = dict(metric='Leave-one-election-out mean absolute error of the House share change', unit='percentage points',
                                 comparator='the previously published calculator (pooled slope, same-phase anchor) recomputed on the same folds',
                                 comparator_value=comparator, model_value_at_ruling=comparator + 0.5, source='test registry')
    return entry


def waiver_registry(comparators=None):
    comparators = COMPARATORS if comparators is None else comparators
    return dict(schema=1, note='test registry', waivers=[waiver_entry('lobby', BANKS, False)] + [waiver_entry('control_House', industry, True, comparators[industry])
                                                                                               for industry in (BANKS, CUS, AUTO)])


def keys_of(node):
    """Every dict key anywhere in a nested response."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from keys_of(value)
    elif isinstance(node, (list, tuple)):
        for value in node:
            yield from keys_of(value)


class World:
    """Bundle + anchors + waiver registry installed from the contract examples (optionally mutated) into a temp folder, with the published-workbook check patched.
    `waivers`: a registry object, the verbatim text of a file, or False for no registry file at all (default: waiver_registry())."""

    def __init__(self, tmp, bundle=None, anchors=None, published=None, waivers=None):
        self.dir = Path(tmp)
        self.bundle = bundle or contract.example('models')
        self.anchors = anchors or contract.example('anchors')
        (self.dir / 'models.json').write_text(json.dumps(self.bundle))
        (self.dir / 'anchors.json').write_text(json.dumps(self.anchors))
        if waivers is not False:
            (self.dir / 'waivers.json').write_text(waivers if isinstance(waivers, str) else json.dumps(waiver_registry() if waivers is None else waivers))
        shas = {i['mentat']['workbook']: i['mentat'].get('workbook_sha256') for i in self.anchors['industries'].values()}
        self.published = published if published is not None else shas
        self.patches = [patch.dict(os.environ, {'MENTAT_GIVING_MODELS': str(self.dir / 'models.json'), 'MENTAT_GIVING_ANCHORS': str(self.dir / 'anchors.json'),
                                                'MENTAT_GIVING_WAIVERS': str(self.dir / 'waivers.json'), 'MENTAT_REPORT_DIRECTORY': str(self.dir)}),          # the report directory is this folder: no machine workbook leaks in
                        patch.object(gf, 'date', Today), patch.object(gf, '_published_workbook_sha256', lambda name: self.published.get(name))]

    def __enter__(self):
        gf._load.cache_clear()
        gf._load_anchors.cache_clear()
        gf._load_waivers.cache_clear()
        for p in self.patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self.patches):
            p.stop()
        gf._load.cache_clear()
        gf._load_anchors.cache_clear()
        gf._load_waivers.cache_clear()


def scenario(sector=BANKS, growth='mean', scores=None, dg=(0, 0, 0), as_of='2026-09-30', control=(1, 1)):
    """The owner scenario of ADDENDUM section F: 2026 growth = the industry's mean growth; the Polymarket January-June 2026 scores."""
    b = contract.example('models')
    industry = b['industries'][sector]
    outlook = b['saved_outlook']['control']
    scores = scores or {'House': outlook['House']['value'], 'Senate': outlook['Senate']['value']}
    changes = {'2026': dg[0], '2027': dg[1], '2028': dg[2]}
    request = dict(industry=sector, as_of=as_of, horizons={})
    for cycle in (2026, 2028):
        g = industry['growth_mean'] if growth == 'mean' else growth
        request['horizons'][str(cycle)] = dict(growth_pct=g, current_control={'House': control[0], 'Senate': control[1]}, control_scores=dict(scores),
                                               annual_forecast_changes=changes)
    return request


def run(request, **world):
    with tempfile.TemporaryDirectory() as tmp, World(tmp, **world):
        return gf.project(request)


SAME_PHASE_SIGMA2 = 4.0e-4        # distinct from the previous-cycle residual variance, so an interval that read the wrong block would show


def same_phase_bundle(chamber='House', sector=BANKS):
    """The contract example with one industry x chamber flipped to the same-phase anchor, consistently: the backtest MAEs make same_phase win by
    more than min_gain in both designs, the gains follow from them, and prediction.industries repeats the same-phase block (owner ruling 2026-09-30).
    The example's own same_phase numbers are interim placeholders, so the flip is built here rather than read from the example."""
    bundle = copy.deepcopy(contract.example('models'))
    m = bundle['share_models']['control_' + chamber]
    entry = m['anchors'][sector]
    back = entry['backtest']
    back['same_phase'].update(loeo_mae=back['previous_cycle']['loeo_mae'] * 0.8, chrono_mae=back['previous_cycle']['chrono_mae'] * 0.7)
    back['same_phase_gain'] = {d: 1 - back['same_phase'][d + '_mae'] / back['previous_cycle'][d + '_mae'] for d in ('loeo', 'chrono')}
    entry['prediction']['same_phase']['sigma2'] = SAME_PHASE_SIGMA2
    entry.update(anchor='same_phase', reason='same_phase chosen: its MAE is 20% (LOEO) and 30% (chronological) below previous_cycle (test fixture)')
    m['prediction']['industries'][sector] = copy.deepcopy(entry['prediction']['same_phase'])
    assert contract.validate_model(bundle) == [], contract.validate_model(bundle)
    return bundle


def near(test, actual, expected, places=0):
    test.assertAlmostEqual(actual, expected, places=places, msg=f'{actual} != {expected}')


class SectionFTests(unittest.TestCase):
    """ADDENDUM F.2 (PAC), F.3 (share) and F.4 (lobbying), banks and credit unions."""

    @classmethod
    def setUpClass(cls):
        cls.out = {}
        with tempfile.TemporaryDirectory() as tmp, World(tmp):
            for sector in (BANKS, CUS):
                cls.out[sector] = gf.project(scenario(sector))
                cls.out[sector + ':eq'] = gf.project(scenario(sector, scores={'House': 19.03485027624309, 'Senate': 56.39529613}))
                cls.out[sector + ':lob'] = {dg: gf.project(scenario(sector, dg=(dg, 0, 0))) for dg in (-1, 0, 1)}

    def measure(self, sector, mid, horizon=0):
        return next(m for m in self.out[sector]['horizons'][horizon]['pac']['measures'] if m['id'] == mid)

    # --- F.2 ---------------------------------------------------------------------------------------------------------------------
    def test_pac_relative_change_and_intervals_use_each_industrys_own_slope(self):
        expected = {BANKS: (13.207, (-16.679, 53.814), (-38.076, 106.961)), CUS: (12.593, (-12.864, 45.488), (-23.381, 65.459))}
        for sector, (change, coef, pred) in expected.items():
            pac = self.out[sector]['horizons'][0]['pac']
            with self.subTest(sector=sector):
                self.assertEqual(pac['slope']['label'], 'industry-specific slope')
                near(self, pac['change_pct'], change, 2)
                near(self, pac['coefficient_interval_pct'][0], coef[0], 2)
                near(self, pac['coefficient_interval_pct'][1], coef[1], 2)
                near(self, pac['prediction_interval_pct'][0], pred[0], 2)
                near(self, pac['prediction_interval_pct'][1], pred[1], 2)

    def test_pac_table_reproduces_section_f2_to_the_dollar(self):
        # (anchor nominal, anchor 2025 USD, projection, coef low, coef high, pred low, pred high, observed, remainder)
        table = {
            (BANKS, 'pac_to_candidates'): (5692500, 5926312, 6709026, 4937854, 9115504, 3669835, 12265136, 4673000, 2036026),
            (BANKS, 'general_giving'): (26107204, 27179525, 30769242, 22646213, 41805943, 16830766, 56250931, 11566805, 19202438),
            (CUS, 'pac_to_candidates'): (80800, 84119, 94712, 73298, 122382, 64451, 139182, 54500, 40212),
            (CUS, 'general_giving'): (779088, 811088, 913231, 706752, 1180033, 621446, 1342017, 272141, 641090),
        }
        for (sector, mid), expected in table.items():
            m = self.measure(sector, mid)
            actual = (m['anchor_cycle_nominal'], m['anchor_cycle_real_2025'], m['projected_cycle_real_2025'], m['cycle_coefficient_low'], m['cycle_coefficient_high'],
                      m['cycle_prediction_low'], m['cycle_prediction_high'], m['observed']['amount_nominal'], m['projected_remaining'])
            with self.subTest(sector=sector, measure=mid):
                for a, e in zip(actual, expected):
                    self.assertLessEqual(abs(round(a) - e), 1, f'{a} vs {e} in {actual}')
                self.assertEqual(m['remainder_status'], 'ready')

    def test_the_two_pac_measures_are_side_by_side_never_summed(self):
        pac = self.out[BANKS]['horizons'][0]['pac']
        self.assertEqual([m['id'] for m in pac['measures']], ['pac_to_candidates', 'general_giving'])
        self.assertNotIn('total', pac)
        self.assertNotEqual(pac['measures'][0]['projected_cycle_real_2025'], pac['measures'][1]['projected_cycle_real_2025'])

    def test_remainder_starts_the_day_after_the_coverage_end(self):
        self.assertEqual(self.out[BANKS]['horizons'][0]['starts'], '2026-07-01')
        self.assertEqual(self.out[CUS]['horizons'][0]['starts'], '2026-06-01')           # credit unions are cut back to 2026-05-31
        self.assertEqual(self.measure(CUS, 'general_giving')['observed']['through'], '2026-05-31')
        self.assertEqual(self.out[BANKS]['anchors']['coverage_end'], '2026-06-30')

    def test_cpi_is_missing_for_2026_so_observed_amounts_are_face_value_and_say_so(self):
        self.assertTrue(self.measure(BANKS, 'general_giving')['observed']['face_value'])
        self.assertTrue(self.out[BANKS]['anchors']['face_value_current_cycle'])
        self.assertAlmostEqual(self.out[BANKS]['anchors']['cpi_multiplier'], 1.0410737386804656, places=12)

    def test_credit_union_pac_channel_carries_the_single_pac_thin_base_flag(self):
        self.assertIn('single_pac_thin_base', self.measure(CUS, 'pac_to_candidates')['flags'])
        self.assertNotIn('single_pac_thin_base', self.measure(BANKS, 'pac_to_candidates')['flags'])

    def test_next_cycle_is_chained_on_the_anchor_with_widened_prediction_variance(self):
        for sector in (BANKS, CUS):
            pac0, pac1 = (self.out[sector]['horizons'][i]['pac'] for i in (0, 1))
            with self.subTest(sector=sector):
                self.assertEqual((pac0['steps'], pac0['chained']), (1, False))
                self.assertEqual((pac1['steps'], pac1['chained']), (2, True))
                self.assertEqual(pac1['anchor_cycle'], 2024)
                self.assertNotIn('projected_remaining', pac1['measures'][0])            # no observed amounts for the next cycle
                # same growth, so the same point; the prediction interval is wider by the second step's residual variance
                near(self, pac1['change_pct'], pac0['change_pct'], 9)
                w0 = pac0['prediction_interval_pct'][1] - pac0['prediction_interval_pct'][0]
                w1 = pac1['prediction_interval_pct'][1] - pac1['prediction_interval_pct'][0]
                self.assertGreater(w1, w0)
                self.assertEqual(pac1['coefficient_interval_pct'], pac0['coefficient_interval_pct'])

    # --- F.3 ---------------------------------------------------------------------------------------------------------------------
    def chamber(self, key, chamber, horizon=0):
        return self.out[key]['horizons'][horizon]['shares'][chamber]

    def basis(self, key, chamber, bid):
        return next(b for b in self.chamber(key, chamber)['bases'] if b['id'] == bid)

    def test_share_pp_change_on_polymarket_to_polymarket_inputs(self):
        # Polymarket-only sensitivity column of F.3, the owner's primary: pp change [coefficient 95%]
        expected = {(BANKS, 'House'): (-0.939, -3.004, 1.126), (CUS, 'House'): (-1.251, -3.147, 0.645),
                    (BANKS, 'Senate'): (11.632, 2.965, 20.300), (CUS, 'Senate'): (10.251, 2.370, 18.131)}
        for (sector, chamber), (change, low, high) in expected.items():
            s = self.chamber(sector, chamber)
            b = self.basis(sector, chamber, 'employee_candidate')
            with self.subTest(sector=sector, chamber=chamber):
                near(self, s['change_pp'], change, 2)
                near(self, 100 * (b['cycle_coefficient_low'] - b['anchor_share']), low, 2)
                near(self, 100 * (b['cycle_coefficient_high'] - b['anchor_share']), high, 2)

    def test_share_pp_change_on_the_equal_weight_sensitivity_matches_the_f3_table(self):
        expected = {(BANKS, 'House'): (-0.892, -2.949, 1.165), (CUS, 'House'): (-1.204, -3.090, 0.681),
                    (BANKS, 'Senate'): (11.343, 2.723, 19.963), (CUS, 'Senate'): (9.961, 2.131, 17.791)}
        for (sector, chamber), (change, low, high) in expected.items():
            s = self.chamber(sector + ':eq', chamber)
            b = self.basis(sector + ':eq', chamber, 'employee_candidate')
            with self.subTest(sector=sector, chamber=chamber):
                near(self, s['change_pp'], change, 2)
                near(self, 100 * (b['cycle_coefficient_low'] - b['anchor_share']), low, 2)
                near(self, 100 * (b['cycle_coefficient_high'] - b['anchor_share']), high, 2)

    def test_share_anchors_on_both_bases_side_by_side_match_f3(self):
        # (employee anchor -> projected, PAC anchor -> projected) on the equal-weight inputs of the F.3 table
        expected = {(BANKS, 'House'): ((0.3435, 0.3345), (0.5436, 0.5347)), (BANKS, 'Senate'): ((0.5144, 0.6278), (0.6450, 0.7584)),
                    (CUS, 'House'): ((0.4407, 0.4286), (0.4063, 0.3943)), (CUS, 'Senate'): ((0.0868, 0.1864), (0.0000, 0.0996))}
        for (sector, chamber), (employee, pac) in expected.items():
            for bid, (anchor, projected) in zip(('employee_candidate', 'pac_candidate'), (employee, pac)):
                b = self.basis(sector + ':eq', chamber, bid)
                with self.subTest(sector=sector, chamber=chamber, basis=bid):
                    near(self, b['anchor_share'], anchor, 4)
                    near(self, b['projected_cycle_R_share'], projected, 4)
        self.assertEqual([b['id'] for b in self.chamber(BANKS, 'House')['bases']], ['employee_candidate', 'pac_candidate'])

    def test_thin_base_is_flagged_per_basis_and_chamber_below_100k(self):
        thin = {(CUS, 'House', 'employee_candidate'): True, (CUS, 'House', 'pac_candidate'): True, (CUS, 'Senate', 'employee_candidate'): False,
                (CUS, 'Senate', 'pac_candidate'): True, (BANKS, 'House', 'employee_candidate'): False, (BANKS, 'Senate', 'pac_candidate'): False}
        for (sector, chamber, bid), expected in thin.items():
            b = self.basis(sector, chamber, bid)
            with self.subTest(sector=sector, chamber=chamber, basis=bid):
                self.assertIs(b['thin_base'], expected)
                self.assertEqual('thin_base' in b['flags'], expected)
        self.assertAlmostEqual(self.basis(CUS, 'House', 'employee_candidate')['anchor_total_nominal'], 55123.94, places=2)
        self.assertAlmostEqual(self.basis(CUS, 'Senate', 'pac_candidate')['anchor_total_nominal'], 5000.0, places=2)

    def test_the_2024_house_window_is_flagged_131_of_182_days(self):
        house, senate = self.chamber(BANKS, 'House')['anchor_input'], self.chamber(BANKS, 'Senate')['anchor_input']
        self.assertEqual((house['days'], house['expected_days'], house['partial_window'], house['market']), (131, 182, True, 'polymarket'))
        self.assertEqual(house['first_day'], '2024-02-21')
        self.assertEqual((senate['days'], senate['expected_days'], senate['partial_window']), (182, 182, False))

    def test_no_response_carries_a_combined_reference(self):
        # Owner ruling 2026-10-01 (D7): employee giving and roster PAC candidate giving are separate accounts. The earlier design's combined reference
        # (ADDENDUM F.3, "open point S1") pooled them into one base and one remainder; no response and no chamber carries it any more.
        found = [str(key) for response in self.out.values() for key in keys_of(response) if 'combined' in str(key)]
        self.assertEqual(found, [])
        for key in (BANKS, CUS, BANKS + ':eq', CUS + ':eq'):
            for chamber in ('House', 'Senate'):
                with self.subTest(key=key, chamber=chamber):
                    s = self.chamber(key, chamber)
                    self.assertNotIn('combined_reference', s)
                    self.assertEqual([b['id'] for b in s['bases']], ['employee_candidate', 'pac_candidate'])       # the two accounts stay side by side

    def test_each_basis_carries_its_own_remainder(self):
        for sector in (BANKS, CUS):
            for chamber in ('House', 'Senate'):
                for b in self.chamber(sector, chamber)['bases']:
                    with self.subTest(sector=sector, chamber=chamber, basis=b['id']):
                        self.assertIn(b['remainder_status'], ('ready', 'budget_inconsistent'))
                        self.assertIn('projected_total_real_2025', b)
                        self.assertEqual(b['observed']['through'], '2026-06-30' if sector == BANKS else '2026-05-31')
        ready = self.basis(BANKS, 'Senate', 'employee_candidate')
        self.assertEqual(ready['remainder_status'], 'ready')
        self.assertAlmostEqual(ready['projected_remaining_R_share'], ready['projected_remaining_R_real_2025'] / (ready['projected_remaining_R_real_2025'] + ready['projected_remaining_D_real_2025']))

    # --- F.4: the lobbying nowcast and its empirical bands (owner rulings 2026-10-08) --------------------------------------------
    def lobby(self, sector, dg=0, year=2026):
        rows = self.out[sector + ':lob'][dg]['horizons'][0 if year == 2026 else 1]['lobbying']['rows']
        return {s['id']: s for s in next(r for r in rows if r['year'] == year)['series']}

    def test_lobbying_nowcast_table_reproduces_the_study(self):
        # (2025 level, nowcast, 80% low, 80% high, ~95% low, ~95% high, observed Q1+Q2, Q3+Q4 remainder) at no growth change: the 2026 Q2-cut table of the blind-test study
        table = {(BANKS, 'income_total'): (23824000, 25655818, 23235661, 28328051, 21357900, 30818619, 13367000, 12288818),
                 (BANKS, 'expenses_total'): (65696931, 71862522, 65083609, 79347506, 59823957, 86323645, 38975000, 32887522),
                 (CUS, 'income_total'): (2054500, 2372976, 2149129, 2620138, 1975450, 2850498, 1311000, 1061976),
                 (CUS, 'expenses_total'): (6233000, 6337214, 5739414, 6997280, 5275591, 7612472, 3424000, 2913214)}
        for (sector, sid), (level, point, lo80, hi80, lo95, hi95, observed, remainder) in table.items():
            with self.subTest(sector=sector, series=sid):
                s = self.lobby(sector, 0)[sid]
                for actual, e in ((s['base_level'], level), (s['projected'], point), (s['band80_low'], lo80), (s['band80_high'], hi80), (s['band95_low'], lo95), (s['band95_high'], hi95),
                                  (s['observed']['amount_nominal'], observed), (s['projected_remaining'], remainder)):
                    self.assertLessEqual(abs(round(actual) - e), 1, f'{actual} vs {e}')
                self.assertEqual((s['nowcast']['through_quarter'], s['nowcast']['share_years']), ('Q2', [2023, 2024, 2025]))
                self.assertEqual(s['remainder_status'], 'ready')

    def test_the_bands_are_the_blind_test_errors_of_the_cut_not_a_model_interval(self):
        errors = contract.example('anchors')['lda_nowcast_errors']['by_cut']['2']
        self.assertEqual(errors['n'], 32)
        for sector in (BANKS, CUS):
            for sid, s in self.lobby(sector, 0).items():
                with self.subTest(sector=sector, series=sid):
                    self.assertAlmostEqual(math.log(s['band80_high'] / s['projected']), errors['q80'], places=12)
                    self.assertAlmostEqual(math.log(s['projected'] / s['band80_low']), errors['q80'], places=12)
                    self.assertAlmostEqual(math.log(s['band95_high'] / s['projected']), errors['max'], places=12)
                    self.assertAlmostEqual(math.log(s['projected'] / s['band95_low']), errors['max'], places=12)
                    self.assertGreater(s['band95_high'], s['band80_high'])
        bands = self.out[BANKS + ':lob'][0]['horizons'][0]['lobbying']['bands']
        self.assertEqual(bands['blind_tests'], 32)
        self.assertIn('80% band', bands['label_80'])
        self.assertIn('largest blind miss', bands['label_95'])
        self.assertIn('n=32 blind tests', bands['label_95'])
        self.assertNotIn('persistence', bands['label_95'])                                  # the 2026 block has no later year
        later = self.out[BANKS + ':lob'][0]['horizons'][1]['lobbying']['bands']
        self.assertIn('added in quadrature to the pooled 95th percentile of the LobbyView persistence error', later['label_95'])        # 2027-28
        self.assertIn('largest blind miss (n=32 blind tests)', later['label_95'])
        for key in ('coefficient_low', 'coefficient_high', 'prediction_low', 'prediction_high', 'cycle_given_observed_median'):          # the old year-effect interval is gone
            self.assertNotIn(key, self.lobby(BANKS, 0)['income_total'])

    def test_a_growth_change_moves_the_nowcast_by_the_pooled_slope_and_leaves_the_bands(self):
        beta = contract.example('models')['lobby_model']['log_refit']['beta']
        for sid in ('income_total', 'expenses_total'):
            lo, mid, hi = (self.lobby(BANKS, dg)[sid] for dg in (-1, 0, 1))
            with self.subTest(series=sid):
                self.assertAlmostEqual(lo['projected'] / mid['projected'], math.exp(-beta), places=12)
                self.assertAlmostEqual(hi['projected'] / mid['projected'], math.exp(beta), places=12)
                for s in (lo, hi):
                    self.assertEqual((s['half_width_80'], s['half_width_95']), (mid['half_width_80'], mid['half_width_95']))

    def test_later_years_carry_the_nowcast_and_add_the_persistence_error_in_quadrature(self):
        errors = contract.example('anchors')['lda_nowcast_errors']['by_cut']['2']
        persistence = contract.example('models')['lobby_model']['log_refit']['persistence']
        for sector in (BANKS, CUS):
            now = self.lobby(sector, 0)
            for year, h in ((2027, 1), (2028, 2)):
                for sid, s in self.lobby(sector, 0, year).items():
                    with self.subTest(sector=sector, year=year, series=sid):
                        self.assertAlmostEqual(s['projected'], now[sid]['projected'], places=6)           # no growth change: carried forward unchanged
                        self.assertEqual(s['years_after_nowcast'], h)
                        self.assertAlmostEqual(s['half_width_80'], math.hypot(errors['q80'], persistence['industries'][sector][str(h)]['q80']), places=12)
                        self.assertAlmostEqual(s['half_width_95'], math.hypot(errors['max'], persistence['pooled'][str(h)]['q95']), places=12)
                        self.assertNotIn('observed', s)

    def test_both_lda_series_are_side_by_side_and_never_summed(self):
        block = self.out[BANKS + ':lob'][0]['horizons'][0]['lobbying']
        row = block['rows'][0]
        self.assertEqual([s['id'] for s in row['series']], ['income_total', 'expenses_total'])
        self.assertEqual(block['sector'], 'bank')
        self.assertNotIn('total', row)
        self.assertEqual(block['base_year'], 2025)
        near(self, row['slope_change_pct'], 0, 9)

    def test_lobbying_growth_changes_use_the_log_refit_slope_with_its_ordinary_p(self):
        block = self.out[BANKS + ':lob'][-1]['horizons'][0]['lobbying']
        self.assertAlmostEqual(block['ordinary_p'], 0.029125763521828413, places=12)
        self.assertEqual(block['slope']['label'], 'pooled slope')
        self.assertAlmostEqual(block['rows'][0]['slope_change_pct'], 100 * math.expm1(-0.00814763405915353 * -1), places=6)


class ContractAndRefusalTests(unittest.TestCase):
    def test_a_stale_snapshot_is_refused_with_anchor_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            anchors = contract.example('anchors')
            shas = {i['mentat']['workbook']: i['mentat']['workbook_sha256'] for i in anchors['industries'].values()}
            shas['Banking Industry Spending Overview.xlsx'] = 'f' * 64                # the workbook was republished
            with World(tmp, published=shas):
                out = gf.project(scenario(BANKS))
                self.assertEqual(out['status'], 'anchor_stale')
                self.assertEqual(out['horizons'], [])
                self.assertIn('Rebuild the anchors', out['reason'])
                self.assertEqual(gf.project(scenario(CUS))['status'], 'ready')          # one industry's republish does not block another
                catalog = gf.catalog('2026-09-30')
                self.assertEqual(catalog['anchors']['industries'][BANKS]['status'], 'anchor_stale')
                self.assertEqual(catalog['anchors']['industries'][CUS]['status'], 'ready')

    def test_an_unreadable_published_workbook_is_refused_not_trusted(self):
        with tempfile.TemporaryDirectory() as tmp, World(tmp, published={}):
            self.assertEqual(gf.project(scenario(BANKS))['status'], 'anchor_unavailable')

    def test_a_missing_or_invalid_snapshot_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp, World(tmp) as w:
            (w.dir / 'anchors.json').unlink()
            gf._load_anchors.cache_clear()
            out = gf.project(scenario(BANKS))
            self.assertEqual(out['status'], 'anchor_unavailable')
            self.assertIn('build_giving_anchors', out['reason'])
            self.assertEqual(gf.catalog('2026-09-30')['anchors']['status'], 'anchor_unavailable')
            (w.dir / 'anchors.json').write_text(json.dumps(contract.apply_mutation(contract.example('anchors'), {'op': 'delete', 'path': ['cpi']})))
            gf._load_anchors.cache_clear()
            self.assertEqual(gf.project(scenario(BANKS))['status'], 'anchor_unavailable')

    def test_every_anchor_negative_control_makes_the_console_refuse(self):
        for control in (c for c in contract.negative_controls() if c['target'] == 'anchors'):
            path = ['commercial_banks' if step == '<industry>' else step for step in control['path']]
            mutated = contract.apply_mutation(contract.example('anchors'), {**control, 'path': path})
            if control['op'] == 'set' and isinstance(control.get('value'), dict) and '$nonfinite' in control['value']:
                continue                                                                 # JSON on disk cannot carry NaN; the validator rejects it in memory
            with self.subTest(control=control['id']), tempfile.TemporaryDirectory() as tmp, World(tmp, anchors=mutated):
                self.assertEqual(gf.project(scenario(BANKS))['status'], 'anchor_unavailable')

    def test_an_invalid_bundle_is_refused_by_the_loader(self):
        for control in (c for c in contract.negative_controls() if c['target'] == 'models'):
            if isinstance(control.get('value'), dict) and '$nonfinite' in control['value'] or control['path'] == ['schema_version']:
                continue                                   # NaN cannot be written to JSON; schema_version 1 is a legitimate v1 package to the loader
            mutated = contract.apply_mutation(contract.example('models'), control)
            with self.subTest(control=control['id']), tempfile.TemporaryDirectory() as tmp:
                with World(tmp, bundle=mutated), self.assertRaises(ValueError):
                    gf.models()

    def test_a_bundle_whose_anchor_contract_the_console_does_not_implement_is_refused(self):
        bundle = contract.example('models')
        bundle['anchor_contract']['pac']['measures'][0]['id'] = 'general_giving'
        with tempfile.TemporaryDirectory() as tmp, World(tmp, bundle=bundle), self.assertRaises(ValueError):
            gf.models()

    def test_seats_are_not_a_selectable_signal_and_slopes_are_not_selectable(self):
        with tempfile.TemporaryDirectory() as tmp, World(tmp):
            with self.assertRaisesRegex(ValueError, 'evidence only'):
                gf.project(scenario(BANKS) | {'signal': 'seats'})
            with self.assertRaisesRegex(ValueError, 'not selectable'):
                gf.project(scenario(BANKS) | {'industry_slopes': True})
            self.assertEqual(gf.project(scenario(BANKS) | {'signal': 'control', 'industry_slopes': False})['status'], 'ready')
            catalog = gf.catalog('2026-09-30')
            self.assertEqual(set(catalog['share_models']), {'control_House', 'control_Senate'})
            self.assertTrue(all(m['calculator'] is False for m in catalog['evidence_models'].values()))
            self.assertIn('seats_House', catalog['evidence_models'])

    def test_auto_has_no_mentat_data_so_it_is_not_offered_but_stays_in_estimation(self):
        with tempfile.TemporaryDirectory() as tmp, World(tmp):
            catalog = gf.catalog('2026-09-30')
            self.assertNotIn('auto_light_duty_manufacturing', [i['id'] for i in catalog['industries']])
            self.assertEqual([n['id'] for n in catalog['not_offered']], ['auto_light_duty_manufacturing'])
            self.assertIn('auto_light_duty_manufacturing', catalog['pac_model']['slopes'])            # still in the estimation
            with self.assertRaises(ValueError):
                gf.project(scenario('auto_light_duty_manufacturing'))

    def test_community_banks_are_separate_and_not_a_forecast_industry(self):
        with tempfile.TemporaryDirectory() as tmp, World(tmp) as w:
            self.assertEqual(w.anchors['industries']['community_banks']['forecast_industry'], None)
            with self.assertRaises(ValueError):
                gf.project({'industry': 'community_banks', 'as_of': '2026-09-30'})
            banks = gf.project(scenario(BANKS))['anchors']
            self.assertEqual(banks['dataset'], 'Banking')
            self.assertNotEqual(w.anchors['industries']['community_banks']['general_giving']['cycles']['2024'], w.anchors['industries'][BANKS]['general_giving']['cycles']['2024'])

    def test_property_casualty_lobbying_is_hidden_and_flagged_and_its_other_models_withheld(self):
        with tempfile.TemporaryDirectory() as tmp, World(tmp):
            out = gf.project(scenario('property_casualty'))
            self.assertEqual(out['status'], 'ready')
            h = out['horizons'][0]
            self.assertEqual(h['lobbying']['status'], 'unavailable')
            self.assertIn('No Melange-LDA sector', h['lobbying']['reason'])
            self.assertEqual(h['lobbying']['rows'], [])
            self.assertEqual(h['pac']['status'], 'withheld')
            self.assertIn('F3400', h['pac']['reason'])
            self.assertEqual(h['shares']['House']['status'], 'withheld')

    def test_v2_outputs_are_strict_json_and_the_request_is_not_mutated(self):
        request = scenario(BANKS)
        before = copy.deepcopy(request)
        out = run(request)
        self.assertEqual(request, before)
        json.dumps(out, allow_nan=False)
        self.assertEqual(out['inputs'], request)
        self.assertEqual(len(out['model_sha256']), 64)

    def test_non_finite_and_invalid_numbers_are_rejected(self):
        for bad in (float('nan'), float('inf'), True, {}, 501, 10 ** 400):
            r = scenario(BANKS)
            r['horizons']['2026']['growth_pct'] = bad
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as tmp, World(tmp), self.assertRaises(ValueError):
                gf.project(r)

    def test_dates_are_validated_like_v1(self):
        with tempfile.TemporaryDirectory() as tmp, World(tmp):
            for extra in ({'as_of': '2026-10-01'}, {'as_of': '2026-09-26'}, {'as_of': 'bad'}, {'assumption_date': '2026-10-01'}):
                with self.subTest(extra=extra), self.assertRaises(ValueError):
                    gf.project(scenario(BANKS) | extra)

    def test_inputs_left_blank_are_unknown_not_zero(self):
        with tempfile.TemporaryDirectory() as tmp, World(tmp):
            out = gf.project({'industry': BANKS, 'as_of': '2026-09-30'})
            h = out['horizons'][0]
            self.assertEqual(h['pac']['status'], 'needs_inputs')
            self.assertEqual(h['shares']['House']['status'], 'needs_inputs')
            self.assertEqual((h['lobbying']['rows'][0]['status'], h['lobbying']['rows'][0]['cumulative_change_pp']), ('ready', 0))       # the nowcast needs no input: blank is no change
            self.assertEqual([r['cycle'] for r in out['horizons']], [2026, 2028])

    def test_an_earlier_as_of_date_uses_the_latest_observed_cut_on_or_before_it(self):
        bundle = contract.example('models')
        bundle['built_on'] = '2025-01-01'                # the vintage guard would otherwise refuse an as-of date before the model package existed
        with tempfile.TemporaryDirectory() as tmp, World(tmp, bundle=bundle):
            out = gf.project(scenario(BANKS, as_of='2026-03-31'))
            g = next(m for m in out['horizons'][0]['pac']['measures'] if m['id'] == 'general_giving')
            self.assertEqual(g['observed']['through'], '2026-03-31')
            self.assertAlmostEqual(g['observed']['amount_nominal'], 9416929.24, places=2)
            self.assertEqual(out['horizons'][0]['starts'], '2026-04-01')
            self.assertEqual(g['remainder_starts'], '2026-04-01')
            # as-of in the middle of a month: the previous month-end
            mid = gf.project(scenario(BANKS, as_of='2026-04-20'))
            self.assertEqual(next(m for m in mid['horizons'][0]['pac']['measures'] if m['id'] == 'general_giving')['observed']['through'], '2026-03-31')

    def test_an_as_of_date_before_the_first_observed_month_end_has_no_remainder(self):
        bundle = contract.example('models')
        bundle['built_on'] = '2025-01-01'
        with tempfile.TemporaryDirectory() as tmp, World(tmp, bundle=bundle):
            out = gf.project(scenario(BANKS, as_of='2025-01-15'))
            g = next(m for m in out['horizons'][0]['pac']['measures'] if m['id'] == 'general_giving')
            self.assertEqual(g['remainder_status'], 'no_observed')
            self.assertNotIn('projected_remaining', g)

    def test_observed_above_the_projection_is_a_conflict_not_a_zero_forecast(self):
        with tempfile.TemporaryDirectory() as tmp, World(tmp):
            out = gf.project(scenario(BANKS, growth=400))          # extreme growth: a strongly negative log change shrinks the projection below the observed
            g = next(m for m in out['horizons'][0]['pac']['measures'] if m['id'] == 'general_giving')
            self.assertEqual(g['remainder_status'], 'baseline_exceeded')
            self.assertIsNone(g['projected_remaining'])
            self.assertGreater(g['excess_observed'], 0)
            self.assertTrue(out['horizons'][0]['pac']['extrapolation'])

    def test_share_outside_zero_one_is_flagged_and_never_clipped(self):
        with tempfile.TemporaryDirectory() as tmp, World(tmp):
            out = gf.project(scenario(CUS, scores={'House': 100, 'Senate': 100}))
            senate = out['horizons'][0]['shares']['Senate']
            pac = next(b for b in senate['bases'] if b['id'] == 'pac_candidate')       # anchor share 0.0000 on a $5,000 base: the negative effect takes it below zero
            self.assertEqual(pac['status'], 'outside_share_range')
            self.assertLess(pac['raw_projected_cycle_R_share'], 0)
            self.assertNotIn('projected_cycle_R_share', pac)
            self.assertTrue(pac['interval_outside_share_range'])
            self.assertIn('thin_base', pac['flags'])

    def test_offset_application_with_the_pooled_slope_equals_the_pooled_application(self):
        bundle = contract.example('models')
        m = bundle['share_models']['control_House']
        j = m['terms'].index('Forecast')
        keep = [i for i in range(len(m['terms'])) if i != j]
        m['application'] = 'offset'
        m['offset'] = {'terms': [m['terms'][i] for i in keep], 'beta': [m['beta'][i] for i in keep], 'covariance': [[m['covariance'][a][c] for c in keep] for a in keep]}
        with tempfile.TemporaryDirectory() as tmp, World(tmp, bundle=bundle):
            offset = gf.project(scenario(BANKS))['horizons'][0]['shares']['House']
        pooled = run(scenario(BANKS))['horizons'][0]['shares']['House']
        self.assertAlmostEqual(offset['change_pp'], pooled['change_pp'], places=9)
        self.assertAlmostEqual(offset['political_change_pp'], pooled['political_change_pp'], places=9)

    def test_an_industry_specific_focal_slope_changes_only_the_political_term(self):
        bundle = contract.example('models')
        m = bundle['share_models']['control_House']
        j = m['terms'].index('Forecast')
        keep = [i for i in range(len(m['terms'])) if i != j]
        m['application'] = 'offset'
        m['offset'] = {'terms': [m['terms'][i] for i in keep], 'beta': [m['beta'][i] for i in keep], 'covariance': [[m['covariance'][a][c] for c in keep] for a in keep]}
        m['slopes'][BANKS]['applied'].update(rule='R1', beta=m['beta'][j] * 2)
        with tempfile.TemporaryDirectory() as tmp, World(tmp, bundle=bundle):
            doubled = gf.project(scenario(BANKS))['horizons'][0]['shares']['House']
        base = run(scenario(BANKS))['horizons'][0]['shares']['House']
        self.assertEqual(doubled['slope_label'], 'industry-specific slope')
        self.assertAlmostEqual(doubled['political_change_pp'], 2 * base['political_change_pp'], places=9)
        self.assertNotAlmostEqual(doubled['change_pp'], base['change_pp'], places=3)

    def test_the_slope_rule_the_bundle_records_is_the_one_applied(self):
        bundle = contract.example('models')
        row = bundle['pac_model']['slopes'][BANKS]
        row['applied'].update(rule='R0', beta=-0.168, se=0.035, df=11, critical_95=2.2, ordinary_p=0.0006)
        with tempfile.TemporaryDirectory() as tmp, World(tmp, bundle=bundle):
            pac = gf.project(scenario(BANKS))['horizons'][0]['pac']
        self.assertEqual(pac['slope']['label'], 'pooled slope')
        self.assertEqual(pac['slope']['beta'], -0.168)
        self.assertEqual(pac['ordinary_p'], 0.0006)

    def test_chained_lobbying_treats_a_blank_change_as_no_change(self):
        with tempfile.TemporaryDirectory() as tmp, World(tmp):
            r = scenario(BANKS)
            r['horizons']['2026']['annual_forecast_changes'] = {'2027': 1, '2028': 1}
            r['horizons']['2028']['annual_forecast_changes'] = {'2027': 1, '2028': 1}
            out = gf.project(r)
            rows = out['horizons'][1]['lobbying']['rows']
            self.assertEqual([x['year'] for x in rows], [2027, 2028])
            self.assertEqual([(x['status'], x['cumulative_change_pp']) for x in rows], [('ready', 1), ('ready', 2)])         # 2026 is blank: no change
            r['horizons']['2028']['annual_forecast_changes'] = {'2026': 1, '2027': 1, '2028': 1}
            rows = gf.project(r)['horizons'][1]['lobbying']['rows']
            self.assertEqual([(x['steps'], x['chained'], x['cumulative_change_pp']) for x in rows], [(2, True, 2), (3, True, 3)])
            near(self, rows[1]['slope_change_pct'], 100 * math.expm1(-0.00814763405915353 * 3), 6)

    def test_lobbying_remainder_needs_the_as_of_date_to_reach_the_ldas_last_quarter(self):
        bundle = contract.example('models')
        bundle['built_on'] = '2025-01-01'
        with tempfile.TemporaryDirectory() as tmp, World(tmp, bundle=bundle):
            out = gf.project(scenario(BANKS, as_of='2026-06-01'))
            s = out['horizons'][0]['lobbying']['rows'][0]['series'][0]
            self.assertEqual(s['remainder_status'], 'no_observed')
            self.assertNotIn('projected_remaining', s)

    def test_catalog_reads_the_bundle_not_hard_coded_text(self):
        with tempfile.TemporaryDirectory() as tmp, World(tmp):
            catalog = gf.catalog('2026-09-30')
            self.assertEqual(catalog['schema_version'], 2)
            self.assertEqual(catalog['pac_model']['n'], 21)
            self.assertEqual(catalog['lobby_model']['n'], 71)
            self.assertEqual({i['id'] for i in catalog['industries']}, {BANKS, CUS, 'property_casualty'})
            self.assertEqual(catalog['anchors']['industries'][BANKS]['coverage_end'], '2026-06-30')
            self.assertIn('prediction', catalog['interval_labels']['prediction'])
            json.dumps(catalog, allow_nan=False)


class ReviewFixTests(unittest.TestCase):
    def test_a_missing_anchor_cycle_cpi_multiplier_refuses_instead_of_reading_as_1_0(self):
        anchors = contract.example('anchors')
        del anchors['cpi']['multipliers']['2024']
        with tempfile.TemporaryDirectory() as tmp, World(tmp, anchors=anchors):
            out = gf.project(scenario(BANKS))
            self.assertEqual(out['status'], 'anchor_unavailable')
            self.assertEqual(out['horizons'], [])
        # defence in depth: the calculator's own guard, for a snapshot that somehow reached it
        ok = contract.example('anchors')
        with tempfile.TemporaryDirectory() as tmp, World(tmp, anchors=ok):
            snapshot = gf.anchors()
            broken = copy.deepcopy(snapshot)
            del broken['cpi']['multipliers']['2024']
            with patch.object(gf, 'anchors', lambda: broken), self.assertRaisesRegex(gf.AnchorProblem, 'no CPI multiplier'):
                gf._anchor_industry(BANKS)

    def test_once_the_open_cycle_has_a_cpi_the_observed_amounts_are_deflated_and_no_longer_face_value(self):
        anchors = contract.example('anchors')
        anchors['cpi']['multipliers']['2026'] = 1.02
        with tempfile.TemporaryDirectory() as tmp, World(tmp, anchors=anchors):
            out = gf.project(scenario(BANKS))
        base = run(scenario(BANKS))
        g = next(m for m in out['horizons'][0]['pac']['measures'] if m['id'] == 'general_giving')
        g0 = next(m for m in base['horizons'][0]['pac']['measures'] if m['id'] == 'general_giving')
        self.assertIs(g['observed']['face_value'], False)
        self.assertIs(g0['observed']['face_value'], True)
        self.assertAlmostEqual(g['observed']['amount_real_2025'], g['observed']['amount_nominal'] * 1.02, places=6)
        self.assertAlmostEqual(g['projected_remaining'], g['projected_cycle_real_2025'] - g['observed']['amount_nominal'] * 1.02, places=4)
        self.assertAlmostEqual(g0['observed']['amount_real_2025'], g0['observed']['amount_nominal'], places=6)
        self.assertFalse(out['anchors']['face_value_current_cycle'])
        bank = out['horizons'][0]['shares']['Senate']['bases'][0]
        self.assertAlmostEqual(bank['observed']['R_real_2025'], bank['observed']['R'] * 1.02, places=6)

    def test_the_horizon_start_follows_whichever_measure_is_ready_and_the_reason_names_the_real_gap(self):
        anchors = contract.example('anchors')
        i = anchors['industries'][BANKS]
        i['general_giving'].update(status='unavailable', reason='test', cycles={}, by_funding_source={}, observed=[])
        i['status']['general_giving'] = 'unavailable'
        with tempfile.TemporaryDirectory() as tmp, World(tmp, anchors=anchors):
            out = gf.project(scenario(BANKS))
        h = out['horizons'][0]
        self.assertEqual((h['starts'], h['coverage_through']), ('2026-07-01', '2026-06-30'))
        employee = next(b for b in h['shares']['House']['bases'] if b['id'] == 'employee_candidate')
        self.assertEqual(employee['remainder_status'], 'needs_inputs')
        self.assertNotIn('Enter cycle revenue growth', employee['remainder_reason'])
        self.assertIn('needs a ready projection', employee['remainder_reason'])

    def test_a_share_model_without_a_forecast_term_is_refused_by_the_loader(self):
        bundle = contract.example('models')
        bundle['share_models']['control_House']['terms'] = [t for t in bundle['share_models']['control_House']['terms'] if t != 'Forecast']
        with tempfile.TemporaryDirectory() as tmp, World(tmp, bundle=bundle), self.assertRaisesRegex(ValueError, 'no Forecast term'):
            gf.models()


class UnusualButValidAnchorTests(unittest.TestCase):
    """Schema-valid snapshots with missing pieces answer with a status or a reason, never a crash and never a zero."""

    def with_anchors(self, edit):
        anchors = contract.example('anchors')
        edit(anchors['industries'][BANKS])
        self.assertEqual(contract.validate_kind(anchors, 'anchors') or contract.check_anchor_invariants(anchors), [])     # the variant is a legal snapshot
        with tempfile.TemporaryDirectory() as tmp, World(tmp, anchors=anchors):
            out = gf.project(scenario(BANKS))
            json.dumps(out, allow_nan=False)
            return out['horizons'][0]

    def test_a_missing_anchor_cycle_of_candidate_receipts_makes_the_share_bases_unavailable(self):
        def edit(i):
            i['candidate_receipts']['cycles'].pop('2024')
            i['candidate_receipts']['thin_base'].pop('2024')
        h = self.with_anchors(edit)
        basis = next(b for b in h['shares']['House']['bases'] if b['id'] == 'employee_candidate')
        self.assertEqual(basis['status'], 'unavailable')
        self.assertIn('anchor cycle', basis['reason'])
        self.assertEqual(next(b for b in h['shares']['House']['bases'] if b['id'] == 'pac_candidate')['status'], 'ready')
        self.assertNotIn('combined_reference', h['shares']['House'])

    def test_an_unavailable_pac_block_leaves_general_giving_and_the_employee_basis(self):
        def edit(i):
            i['pac_to_candidates'].update(status='unavailable', reason='roster not resolved', committee_count=0, single_pac=False, committees=[], inputs=[],
                                          cycles={}, file_through={}, observed=[], thin_base={})
            i['status']['pac_to_candidates'] = 'unavailable'
        h = self.with_anchors(edit)
        measures = {m['id']: m for m in h['pac']['measures']}
        self.assertEqual(measures['pac_to_candidates']['status'], 'unavailable')
        self.assertEqual(measures['pac_to_candidates']['reason'], 'roster not resolved')
        self.assertEqual(measures['general_giving']['status'], 'ready')
        bases = {b['id']: b for b in h['shares']['Senate']['bases']}
        self.assertEqual(bases['pac_candidate']['status'], 'unavailable')
        self.assertIn(bases['employee_candidate']['remainder_status'], ('ready', 'budget_inconsistent'))

    def test_a_partial_year_absent_from_the_lda_means_no_observed_quarters_and_no_remainder(self):
        def edit(i):
            for s in i['lda']['series'].values():
                s['partial'] = None
        s = self.with_anchors(edit)['lobbying']['rows'][0]['series'][0]
        self.assertNotIn('observed', s)
        self.assertNotIn('projected_remaining', s)
        self.assertGreater(s['projected'], 0)

    def test_a_two_party_base_of_zero_has_no_share_to_carry_over(self):
        def edit(i):
            for chamber in ('House', 'Senate'):
                i['candidate_receipts']['cycles']['2024'][chamber].update(D=0.0, R=0.0, other=0.0)
            i['candidate_receipts']['thin_base']['2024'] = {'House': True, 'Senate': True}
        h = self.with_anchors(edit)
        basis = next(b for b in h['shares']['House']['bases'] if b['id'] == 'employee_candidate')
        self.assertEqual(basis['status'], 'unavailable')

    def test_the_last_day_of_the_cycle_has_no_remainder_window(self):
        class YearEnd(date):
            @classmethod
            def today(cls):
                return cls(2026, 12, 31)
        with tempfile.TemporaryDirectory() as tmp, World(tmp), patch.object(gf, 'date', YearEnd):
            out = gf.project(scenario(BANKS, as_of='2026-12-31'))
        h = out['horizons'][0]
        self.assertIsNone(h['starts'])
        self.assertEqual(h['pac']['measures'][0]['remainder_status'], 'cycle_closed')
        self.assertEqual(h['shares']['House']['bases'][0]['remainder_status'], 'cycle_closed')
        self.assertEqual(h['lobbying']['rows'][0]['series'][0]['remainder_status'], 'ready')       # Melange LDA's Q3 + Q4 are not filed yet: that remainder stands


def pooled_change(m, target, anchor):
    """(effect, variance) of a pooled share model for the change of inputs from `anchor` to `target`, computed from beta and the covariance."""
    delta = [0. if t == 'Intercept' or t.startswith('industry:') else target[t] - anchor[t] for t in m['terms']]
    effect = sum(d * b for d, b in zip(delta, m['beta']))
    variance = sum(delta[i] * m['covariance'][i][j] * delta[j] for i in range(len(delta)) for j in range(len(delta)))
    return effect, variance


class SharePerChamberAnchorTests(unittest.TestCase):
    """Owner ruling 2026-09-30: each industry x chamber carries its anchor (previous_cycle or same_phase) in the bundle; the share uses it."""

    @classmethod
    def setUpClass(cls):
        cls.bundle = same_phase_bundle('House', BANKS)
        cls.request = scenario(BANKS)
        cls.flipped = run(cls.request, bundle=cls.bundle)
        cls.base = run(cls.request)

    def share(self, out, horizon, chamber='House'):
        return out['horizons'][horizon]['shares'][chamber]

    def basis(self, out, horizon, bid, chamber='House'):
        return next(b for b in self.share(out, horizon, chamber)['bases'] if b['id'] == bid)

    def inputs(self, horizon_cycle):
        """The House model's inputs for a target horizon of the scenario and for its same-phase anchor cycle, built here from the bundle."""
        b, m = self.bundle, self.bundle['share_models']['control_House']
        ind = b['industries'][BANKS]
        mu, sd = ind['growth_mean'], ind['growth_sd']
        h = self.request['horizons'][str(horizon_cycle)]
        prior = next((r['growth_pct'] for r in ind['history'] if r['cycle'] == horizon_cycle - 2), None)
        prior = self.request['horizons'][str(horizon_cycle - 2)]['growth_pct'] if prior is None else prior
        target = {'RMajNow': h['current_control']['House'], 'Forecast': h['control_scores']['House'] / 100, 'G_prior': (prior - mu) / sd,
                  'G_current': (h['growth_pct'] - mu) / sd, 'Pres': float(horizon_cycle % 4 == 0)}
        a = horizon_cycle - 4
        if str(a) in b['political_inputs']['anchor_cycles']:
            p = b['political_inputs']['anchor_cycles'][str(a)]
            g = next(r for r in ind['history'] if r['cycle'] == a)
            anchor = {'RMajNow': p['current_control']['House'], 'Forecast': p['House']['p_rep']}
        else:
            g = next(r for r in m['history'] if r['industry'] == BANKS and r['cycle'] == a)
            anchor = {'RMajNow': g['current_control'], 'Forecast': g['forecast']}
        anchor.update(G_prior=(g['prior_growth_pct'] - mu) / sd, G_current=(g['growth_pct'] - mu) / sd, Pres=float(a % 4 == 0))
        return target, anchor

    def test_the_flipped_bundle_is_consistent_and_accepted_by_the_loader(self):
        self.assertEqual(contract.validate_model(self.bundle), [])
        self.assertEqual(self.flipped['status'], 'ready')

    def test_same_phase_anchors_2026_on_2022_and_2028_on_2024_one_step_each(self):
        h0, h1 = self.share(self.flipped, 0), self.share(self.flipped, 1)
        self.assertEqual((h0['anchor'], h0['anchor_cycle'], h0['steps']), ('same_phase', 2022, 1))
        self.assertEqual((h1['anchor'], h1['anchor_cycle'], h1['steps']), ('same_phase', 2024, 1))
        self.assertEqual(h0['anchor_label'], 'Mentat 2021–2022 (same phase)')
        self.assertEqual(h1['anchor_label'], 'Mentat 2023–2024 (same phase)')
        entry = self.bundle['share_models']['control_House']['anchors'][BANKS]
        self.assertEqual(h0['anchor_reason'], entry['reason'])
        self.assertEqual(h0['anchor_backtest'], entry['backtest'])

    def test_the_anchor_input_block_reports_the_control_input_actually_used(self):
        h0, h1 = self.share(self.flipped, 0)['anchor_input'], self.share(self.flipped, 1)['anchor_input']
        row = next(r for r in self.bundle['share_models']['control_House']['history'] if r['industry'] == BANKS and r['cycle'] == 2022)
        self.assertEqual((h0['cycle'], h0['source'], h0['p_rep'], h0['current_control'], h0['market']), (2022, 'model_history', row['forecast'], row['current_control'], None))
        self.assertFalse(h0['partial_window'])
        p2024 = self.bundle['political_inputs']['anchor_cycles']['2024']
        self.assertEqual((h1['cycle'], h1['source'], h1['p_rep'], h1['current_control']), (2024, 'political_inputs', p2024['House']['p_rep'], p2024['current_control']['House']))
        self.assertEqual(self.share(self.flipped, 0)['baseline_forecast'], row['forecast'])

    def test_anchor_shares_come_from_the_snapshot_cycle_of_the_same_phase(self):
        snap = contract.example('anchors')['industries'][BANKS]
        for horizon, cycle in ((0, '2022'), (1, '2024')):
            for bid, block in (('employee_candidate', 'candidate_receipts'), ('pac_candidate', 'pac_to_candidates')):
                parts = snap[block]['cycles'][cycle]['House']
                b = self.basis(self.flipped, horizon, bid)
                with self.subTest(horizon=horizon, basis=bid):
                    self.assertAlmostEqual(b['anchor_share'], parts['R'] / (parts['D'] + parts['R']), places=12)
                    self.assertAlmostEqual(b['anchor_total_nominal'], parts['D'] + parts['R'], places=6)

    def test_projected_share_is_the_anchor_share_plus_the_pooled_effect_of_the_input_deltas(self):
        m = self.bundle['share_models']['control_House']
        for horizon, cycle in ((0, 2026), (1, 2028)):
            target, anchor = self.inputs(cycle)
            effect, variance = pooled_change(m, target, anchor)
            s = self.share(self.flipped, horizon)
            with self.subTest(cycle=cycle):
                self.assertAlmostEqual(s['change_pp'], effect * 100, places=9)
                for bid in ('employee_candidate', 'pac_candidate'):
                    b = self.basis(self.flipped, horizon, bid)
                    self.assertAlmostEqual(b['raw_projected_cycle_R_share'], b['anchor_share'] + effect, places=12)
                    coef = m['critical_95'] * math.sqrt(variance)
                    self.assertAlmostEqual(b['cycle_coefficient_high'] - b['raw_projected_cycle_R_share'], coef, places=12)

    def test_the_prediction_interval_uses_the_same_phase_residual_block_for_one_step(self):
        m = self.bundle['share_models']['control_House']
        block = m['anchors'][BANKS]['prediction']['same_phase']
        self.assertEqual(block['sigma2'], SAME_PHASE_SIGMA2)
        for horizon, cycle in ((0, 2026), (1, 2028)):
            _effect, variance = pooled_change(m, *self.inputs(cycle))
            half = block['critical_95'] * math.sqrt(variance + 1 * block['sigma2'])
            b = self.basis(self.flipped, horizon, 'employee_candidate')
            with self.subTest(cycle=cycle):
                self.assertAlmostEqual(b['cycle_prediction_high'] - b['raw_projected_cycle_R_share'], half, places=12)
                self.assertAlmostEqual(b['raw_projected_cycle_R_share'] - b['cycle_prediction_low'], half, places=12)

    def test_2028_has_the_previous_cycle_point_but_not_its_chained_interval(self):
        # previous_cycle chains 2028 through the open cycle from the same 2024 anchor: same point, two steps of the previous-cycle variance
        flipped, base = self.basis(self.flipped, 1, 'employee_candidate'), self.basis(self.base, 1, 'employee_candidate')
        self.assertEqual((self.share(self.base, 1)['anchor_cycle'], self.share(self.base, 1)['steps']), (2024, 2))
        self.assertAlmostEqual(flipped['raw_projected_cycle_R_share'], base['raw_projected_cycle_R_share'], places=12)
        self.assertNotAlmostEqual(flipped['cycle_prediction_high'], base['cycle_prediction_high'], places=6)

    def test_the_senate_of_the_same_industry_and_the_other_industries_stay_on_previous_cycle(self):
        for horizon in (0, 1):
            senate = self.share(self.flipped, horizon, 'Senate')
            with self.subTest(horizon=horizon):
                self.assertEqual((senate['anchor'], senate['anchor_cycle'], senate['steps']), ('previous_cycle', 2024, horizon + 1))
                self.assertEqual(senate, self.share(self.base, horizon, 'Senate'))
                self.assertEqual(self.flipped['horizons'][horizon]['pac'], self.base['horizons'][horizon]['pac'])        # PAC always uses the previous cycle
        cus_flipped, cus_base = run(scenario(CUS), bundle=self.bundle), run(scenario(CUS))
        self.assertEqual(cus_flipped['horizons'][0]['shares'], cus_base['horizons'][0]['shares'])

    def test_the_remainder_total_stays_on_the_previous_cycle_ratio_while_the_share_follows_the_anchor(self):
        for bid in ('employee_candidate', 'pac_candidate'):
            flipped, base = self.basis(self.flipped, 0, bid), self.basis(self.base, 0, bid)
            with self.subTest(basis=bid):
                self.assertAlmostEqual(flipped['projected_total_real_2025'], base['projected_total_real_2025'], places=6)
                self.assertEqual(flipped['observed'], base['observed'])
                if flipped['remainder_status'] == 'ready':
                    total, share = flipped['projected_total_real_2025'], flipped['projected_cycle_R_share']
                    self.assertAlmostEqual(flipped['projected_remaining_R_real_2025'], share * total - flipped['observed']['R_real_2025'], places=4)

    def test_an_unavailable_snapshot_cycle_makes_the_basis_unavailable_and_names_the_cycle(self):
        anchors = contract.example('anchors')
        i = anchors['industries'][BANKS]
        i['candidate_receipts']['cycles'].pop('2022')
        i['candidate_receipts']['thin_base'].pop('2022', None)
        self.assertEqual(contract.validate_kind(anchors, 'anchors') or contract.check_anchor_invariants(anchors), [])
        out = run(self.request, bundle=self.bundle, anchors=anchors)
        employee, pac = self.basis(out, 0, 'employee_candidate'), self.basis(out, 0, 'pac_candidate')
        self.assertEqual(employee['status'], 'unavailable')
        self.assertIn('2021–2022 anchor cycle', employee['reason'])
        self.assertEqual(pac['status'], 'ready')
        self.assertEqual(self.basis(out, 1, 'employee_candidate')['status'], 'ready')                  # 2028 anchors on 2024
        self.assertEqual(self.share(out, 0, 'Senate'), self.share(self.base, 0, 'Senate'))               # previous_cycle never reads 2022
        i['pac_to_candidates']['cycles'].pop('2022')
        i['pac_to_candidates']['thin_base'].pop('2022', None)
        self.assertEqual(contract.validate_kind(anchors, 'anchors') or contract.check_anchor_invariants(anchors), [])
        both = run(self.request, bundle=self.bundle, anchors=anchors)
        house = self.share(both, 0)
        self.assertEqual(house['status'], 'unavailable')
        self.assertIn('2021–2022 cycle', house['reason'])
        json.dumps(both, allow_nan=False)

    def test_same_phase_never_anchors_on_the_open_cycle(self):
        class Later(date):
            @classmethod
            def today(cls):
                return cls(2027, 3, 1)
        mean = self.bundle['industries'][BANKS]['growth_mean']
        horizons = {'2028': dict(self.request['horizons']['2026'], prior_growth_pct=mean), '2030': self.request['horizons']['2028']}
        with tempfile.TemporaryDirectory() as tmp, World(tmp, bundle=self.bundle), patch.object(gf, 'date', Later):
            out = gf.project(scenario(BANKS, as_of='2027-03-01') | {'horizons': horizons})
        h0, h1 = self.share(out, 0), self.share(out, 1)
        self.assertEqual((h0['anchor_cycle'], h0['status']), (2024, 'ready'))
        self.assertEqual((h1['anchor_cycle'], h1['status']), (2026, 'unavailable'))
        self.assertIn('2025–2026 cycle is still open', h1['reason'])

    def test_previous_cycle_reads_only_its_own_residual_block(self):
        bundle = copy.deepcopy(contract.example('models'))
        bundle['share_models']['control_House']['anchors'][BANKS]['prediction']['same_phase']['sigma2'] = 0.5     # never read while previous_cycle is chosen
        self.assertEqual(contract.validate_model(bundle), [])
        out = run(self.request, bundle=bundle)
        for horizon in (0, 1):
            self.assertEqual(self.share(out, horizon), self.share(self.base, horizon))

    def test_the_example_keeps_previous_cycle_everywhere_with_its_recorded_choice(self):
        b = contract.example('models')
        for sector in (BANKS, CUS):
            out = run(scenario(sector))
            for chamber in ('House', 'Senate'):
                entry = b['share_models']['control_' + chamber]['anchors'][sector]
                for horizon in (0, 1):
                    s = self.share(out, horizon, chamber)
                    with self.subTest(sector=sector, chamber=chamber, horizon=horizon):
                        self.assertEqual((s['anchor'], s['anchor_cycle'], s['steps']), ('previous_cycle', 2024, horizon + 1))
                        self.assertEqual(s['anchor_label'], 'Mentat 2023–2024 (previous cycle)')
                        self.assertEqual((s['anchor_reason'], s['anchor_backtest']), (entry['reason'], entry['backtest']))
                        self.assertEqual(s['anchor_input']['source'], 'political_inputs')

    def test_the_catalog_exposes_the_anchor_choices_and_options(self):
        with tempfile.TemporaryDirectory() as tmp, World(tmp, bundle=self.bundle):
            catalog = gf.catalog('2026-09-30')
        self.assertEqual(catalog['share_models']['control_House']['anchors'][BANKS]['anchor'], 'same_phase')
        self.assertEqual(catalog['share_models']['control_Senate']['anchors'][BANKS]['anchor'], 'previous_cycle')
        self.assertEqual({o['id'] for o in catalog['anchor_contract']['share']['anchor_options']}, {'previous_cycle', 'same_phase'})


class AnchorContractRefusalTests(unittest.TestCase):
    """The loader validates the bundle with contract.validate_model (schema + cross-field invariants) and the anchors this console implements."""

    def refused(self, bundle, pattern):
        with tempfile.TemporaryDirectory() as tmp, World(tmp, bundle=bundle), self.assertRaisesRegex(ValueError, pattern):
            gf.models()

    def test_a_flip_without_the_matching_residual_block_or_backtest_is_refused(self):
        bundle = copy.deepcopy(contract.example('models'))
        bundle['share_models']['control_House']['anchors'][BANKS]['anchor'] = 'same_phase'
        self.assertEqual(contract.validate_kind(bundle, 'models'), [])            # schema-valid: only the invariants catch it
        self.assertNotEqual(contract.check_model_invariants(bundle), [])
        self.refused(bundle, r'does not satisfy its contract \(share_models\.control_House\.anchors\.commercial_banks: .*same_phase')

    def test_a_consistent_choice_whose_prediction_block_is_the_other_anchors_is_refused(self):
        bundle = same_phase_bundle()
        m = bundle['share_models']['control_House']
        m['prediction']['industries'][BANKS] = copy.deepcopy(m['anchors'][BANKS]['prediction']['previous_cycle'])
        self.refused(bundle, r'does not satisfy its contract .*is not the same_phase block')

    def test_a_gain_that_does_not_follow_from_the_maes_is_refused(self):
        bundle = same_phase_bundle()
        bundle['share_models']['control_House']['anchors'][BANKS]['backtest']['same_phase_gain']['loeo'] = 0.9
        self.refused(bundle, r'does not satisfy its contract .*same_phase_gain\.loeo does not follow')

    def test_an_unknown_anchor_option_is_refused(self):
        bundle = copy.deepcopy(contract.example('models'))
        bundle['anchor_contract']['share']['anchor_options'][1]['id'] = 'two_cycles_back'
        self.refused(bundle, r'does not satisfy its contract')

    def test_anchor_options_the_console_does_not_implement_are_refused_even_when_schema_valid(self):
        duplicate = copy.deepcopy(contract.example('models'))
        duplicate['anchor_contract']['share']['anchor_options'][1] = copy.deepcopy(duplicate['anchor_contract']['share']['anchor_options'][0])
        offset = copy.deepcopy(contract.example('models'))
        next(o for o in offset['anchor_contract']['share']['anchor_options'] if o['id'] == 'same_phase')['cycle_offset'] = 6
        for name, bundle, pattern in (('duplicate', duplicate, 'share anchor options'), ('offset', offset, 'share anchor offsets')):
            with self.subTest(name):
                self.assertEqual(contract.validate_model(bundle), [])            # the contract accepts it; the console does not implement it
                self.refused(bundle, 'does not satisfy its contract .*' + pattern)


class WaiverTests(unittest.TestCase):
    """Owner waivers of the refit's backtest gate (mentat/forecast/refit/registry/gate_waivers.json): the ones the registry marks to be shown reach the
    catalog, beside the error of the model in force. Display only: nothing here changes a gate or a projection."""

    @staticmethod
    def shown(**world):
        with tempfile.TemporaryDirectory() as tmp, World(tmp, **world):
            return gf.catalog('2026-09-30')['waivers']

    @staticmethod
    def example_error(industry=BANKS, chamber='House'):
        """The contract example's recorded error of the anchor it uses (previous cycle) for one share model x industry."""
        return contract.example('models')['share_models']['control_' + chamber]['anchors'][industry]['backtest']['previous_cycle']['loeo_mae']

    def test_the_catalog_carries_the_visible_house_share_waivers_of_the_industries_it_offers(self):
        waivers = self.shown()
        # banks lobbying is quiet (show_warning false) and auto is not offered by the console: two cards, in the registry's order
        self.assertEqual([(w['industry'], w['term'], w['chamber'], w['cell']) for w in waivers],
                         [(BANKS, 'control_House', 'House', 'control_House / commercial_banks'), (CUS, 'control_House', 'House', 'control_House / credit_unions')])
        banks = waivers[0]
        self.assertEqual((banks['gate'], banks['owner'], banks['date'], banks['show_warning'], banks['industry_label'], banks['model_id']),
                         ('G6', 'Paul Goff', '2026-09-30', True, 'Commercial banks', 'control_House'))
        self.assertEqual(banks['reason'], RULING)
        self.assertEqual((banks['metric'], banks['unit']), ('Leave-one-election-out mean absolute error of the House share change', 'percentage points'))
        self.assertEqual((banks['comparator_value'], banks['published_value']), (1.053, self.example_error(BANKS)))
        self.assertEqual(banks['comparator_label'], 'the previously published calculator (pooled slope, same-phase anchor) recomputed on the same folds')
        self.assertNotIn('comparator', banks)                       # a reader that takes `comparator` before `comparator_value` as the figure must not find the label there
        self.assertEqual(banks['display_text'], 'House share gate waived by owner on 2026-09-30: backtest error 1.053 -> 1.544 pp')
        self.assertEqual(waivers[1]['display_text'], 'House share gate waived by owner on 2026-09-30: backtest error 1.022 -> 1.477 pp')
        json.dumps(waivers, allow_nan=False)

    def test_the_published_figure_is_the_error_of_the_anchor_the_model_in_force_uses(self):
        bundle = same_phase_bundle('House', BANKS)                  # banks House on the same-phase anchor: its error is 20% below the previous cycle's
        mae = bundle['share_models']['control_House']['anchors'][BANKS]['backtest']['same_phase']['loeo_mae']
        banks = self.shown(bundle=bundle)[0]
        self.assertAlmostEqual(mae, 0.8 * self.example_error(BANKS), places=9)
        self.assertEqual(banks['published_value'], mae)
        self.assertEqual(banks['display_text'], 'House share gate waived by owner on 2026-09-30: backtest error 1.053 -> %.3f pp' % mae)

    def test_a_waiver_is_not_shown_once_the_model_in_force_is_no_worse_than_the_calculator_it_was_compared_with(self):
        error = self.example_error(BANKS)
        for label, comparator, expected in (('comparator above the error', error + 0.1, [CUS]), ('equal', error, [CUS]), ('inside the 1e-9 slack', error - 5e-10, [CUS]),
                                            ('just outside the slack', error - 2e-9, [BANKS, CUS])):
            with self.subTest(label):
                self.assertEqual([w['industry'] for w in self.shown(waivers=waiver_registry({**COMPARATORS, BANKS: comparator}))], expected)

    def test_only_a_g6_cell_marked_show_warning_of_an_offered_industry_is_shown(self):
        registry = waiver_registry()
        for entry in registry['waivers']:                           # the banks cell is the control: it stays shown while each variant below is not
            entry['show_warning'] = entry['show_warning'] and entry['industry'] == BANKS and entry['term'] == 'control_House'
        registry['waivers'] += [waiver_entry('control_Senate', CUS, True, 1.0, gate='G7'), waiver_entry('control_Senate', 'steel_mills', True, 1.0),
                                waiver_entry('control_Senate', CUS, 'true', 1.0), waiver_entry('control_Senate', CUS, False, 1.0)]
        self.assertEqual([w['cell'] for w in self.shown(waivers=registry)], ['control_House / commercial_banks'])

    def test_a_shown_waiver_without_figures_still_names_its_cell_and_ruling(self):
        registry = waiver_registry()
        registry['waivers'] = [waiver_entry('pac', BANKS, True)]
        (waiver,) = self.shown(waivers=registry)
        self.assertEqual((waiver['term'], waiver['chamber'], waiver['model_id'], waiver['metric'], waiver['comparator_value'], waiver['published_value']),
                         ('pac', None, None, None, None, None))
        self.assertEqual((waiver['cell'], waiver['owner'], waiver['date']), ('pac / commercial_banks', 'Paul Goff', '2026-09-30'))
        self.assertEqual(waiver['display_text'], 'PAC giving gate waived by owner on 2026-09-30')

    def test_a_registry_that_is_missing_unreadable_or_malformed_leaves_the_catalog_whole_with_no_waivers(self):
        banks = waiver_entry('control_House', BANKS, True, COMPARATORS[BANKS])
        nested = '{"waivers": ' + '[' * 50000 + ']' * 50000 + '}'                    # far deeper than the JSON parser's recursion limit: unreadable like any other file
        bad = dict(missing=False, not_json='{"waivers": [', not_an_object='[]', no_list=json.dumps({'waivers': {}}), not_objects=json.dumps({'waivers': ['x', 3, None]}),
                   no_cell=json.dumps({'waivers': [{'term': 'control_House', 'industry': BANKS, 'show_warning': True}]}),
                   industry_a_list={'waivers': [dict(banks, industry=[BANKS])]}, industry_an_object={'waivers': [dict(banks, industry={'id': BANKS})]},     # not hashable
                   industry_a_number={'waivers': [dict(banks, industry=7)]}, nested_too_deeply=nested)
        for label, waivers in bad.items():
            with self.subTest(label), tempfile.TemporaryDirectory() as tmp, World(tmp, waivers=waivers):
                catalog = gf.catalog('2026-09-30')
                self.assertEqual(catalog['waivers'], [])
                self.assertEqual([i['id'] for i in catalog['industries']], [BANKS, CUS, 'property_casualty'])
                json.dumps(catalog, allow_nan=False)                                   # and the catalog is still the strict JSON a browser parses

    def test_registry_fields_that_are_not_text_or_not_a_finite_figure_never_reach_the_response(self):
        # Python's json reads NaN and Infinity, a browser's JSON.parse rejects them: the response passes through strings and finite figures only.
        banks = waiver_entry('control_House', BANKS, True, COMPARATORS[BANKS])
        measured, nan = banks['measured'], float('nan')
        cases = dict(metric_nan=dict(banks, measured=dict(measured, metric=nan)), owner_nan=dict(banks, owner=nan), reason_an_object=dict(banks, reason={'text': 'x'}),
                     unit_a_number=dict(banks, measured=dict(measured, unit=7)), source_a_list=dict(banks, measured=dict(measured, source=['a'])),
                     comparator_label_true=dict(banks, measured=dict(measured, comparator=True)))
        for label, entry in cases.items():
            with self.subTest(label), tempfile.TemporaryDirectory() as tmp, World(tmp, waivers={'schema': 1, 'waivers': [entry]}):
                catalog = gf.catalog('2026-09-30')
                (waiver,) = catalog['waivers']                                           # still shown: the registry says so, and the figures are good
                self.assertEqual(waiver['display_text'], 'House share gate waived by owner on 2026-09-30: backtest error 1.053 -> 1.544 pp')
                for field in ('metric', 'owner', 'reason', 'unit', 'comparator_label', 'source'):
                    self.assertTrue(waiver[field] is None or isinstance(waiver[field], str), (label, field))
                if label in ('metric_nan', 'owner_nan'):
                    self.assertIsNone(waiver[label.split('_')[0]])                       # the NaN is gone, not carried
                json.dumps(catalog, allow_nan=False)
        # the figure itself: NaN, an infinity, or an integer too large for a float is no figure (the entry is shown, without numbers)
        for label, figure in (('nan', nan), ('infinity', float('inf')), ('integer beyond the float range', 10 ** 400), ('negative integer beyond the float range', -10 ** 400)):
            with self.subTest(figure=label), tempfile.TemporaryDirectory() as tmp, World(tmp, waivers={'schema': 1, 'waivers': [dict(banks, measured=dict(measured, comparator_value=figure))]}):
                catalog = gf.catalog('2026-09-30')
                (waiver,) = catalog['waivers']
                self.assertEqual((waiver['comparator_value'], waiver['display_text']), (None, 'House share gate waived by owner on 2026-09-30'))
                json.dumps(catalog, allow_nan=False)

    def test_waivers_are_display_only_no_projection_and_no_other_catalog_field_depends_on_the_registry(self):
        self.assertEqual(run(scenario(BANKS)), run(scenario(BANKS), waivers=False))
        with tempfile.TemporaryDirectory() as tmp, World(tmp):
            with_registry = gf.catalog('2026-09-30')
        with tempfile.TemporaryDirectory() as tmp, World(tmp, waivers=False):
            without = gf.catalog('2026-09-30')
        self.assertEqual(len(with_registry.pop('waivers')), 2)
        self.assertEqual(without.pop('waivers'), [])
        self.assertEqual(with_registry, without)

    def test_the_checkouts_own_registry_lists_the_three_shown_house_waivers_with_the_figures_of_the_build_notes(self):
        with patch.dict(os.environ, {'MENTAT_GIVING_WAIVERS': ''}):
            path = gf.waiver_path()
        self.assertEqual(path, gf.WAIVER_FILE)
        stat = path.stat()
        entries = {w['cell']: w for w in gf._load_waivers(str(path), stat.st_mtime_ns, stat.st_size)}
        self.assertEqual(sorted(entries), ['control_House / auto_light_duty_manufacturing', 'control_House / commercial_banks', 'control_House / credit_unions',
                                           'control_Senate / auto_light_duty_manufacturing', 'control_Senate / commercial_banks', 'control_Senate / credit_unions',
                                           'control_Senate / property_casualty', 'lobby / commercial_banks', 'pac / auto_light_duty_manufacturing'])
        self.assertIs(entries['lobby / commercial_banks']['show_warning'], False)
        # 2026-10-07 (owner: "2. Keep senate in", "1. Yes"): the Senate share model keeps the presidential-cycle term, the PAC model is the 10-01 rebuild's phase specification;
        # the Senate waivers of banks, credit unions and P&C are shown, the auto cells (not offered on the card) are quiet
        for cell, shown in (('control_Senate / commercial_banks', True), ('control_Senate / credit_unions', True), ('control_Senate / property_casualty', True),
                            ('control_Senate / auto_light_duty_manufacturing', False), ('pac / auto_light_duty_manufacturing', False)):
            with self.subTest(cell=cell):
                self.assertEqual((entries[cell]['gate'], entries[cell]['owner'], entries[cell]['date'], entries[cell]['show_warning']), ('G6', 'Paul Goff', '2026-10-07', shown))
        self.assertIn('2. Keep senate in', entries['control_Senate / commercial_banks']['reason'])
        self.assertIn('1. Yes', entries['pac / auto_light_duty_manufacturing']['reason'])
        # forecast/BUILD_NOTES.md: House-share LOEO MAE 1.053 to 1.550 (banks), 1.522 to 1.836 (credit unions), 1.338 to 1.766 (auto)
        figures = {'control_House / commercial_banks': (1.053, 1.550), 'control_House / credit_unions': (1.522, 1.836), 'control_House / auto_light_duty_manufacturing': (1.338, 1.766)}
        for cell, (before, after) in figures.items():
            with self.subTest(cell=cell):
                w = entries[cell]
                self.assertIs(w['show_warning'], True)
                self.assertEqual((w['gate'], w['owner'], w['date']), ('G6', 'Paul Goff', '2026-09-30'))
                self.assertEqual((round(w['measured']['comparator_value'], 3), round(w['measured']['model_value_at_ruling'], 3)), (before, after))


# Telecom (owner ruling 2026-10-08): offered for lobbying only. The refit writes it as an industry with a Mentat key and the lobbying capability and no OpenSecrets capture; the anchors
# builder reads its workbook and the Melange LDA sector `telecom`. The series below are the real LDA telecom levels with the nowcast the builder computes from them.
TELECOM = 'telecom'
TELECOM_REASON = 'Telecom is offered for lobbying only (owner ruling 2026-10-08): there is no OpenSecrets capture and no roster PAC for this industry, so no giving forecast is made.'
TELECOM_PERSISTENCE = {'1': dict(n=10, q80=0.12977393486852762), '2': dict(n=10, q80=0.18041710178986747), '3': dict(n=10, q80=0.2550267185327719)}
TELECOM_SERIES = {
    'income_total': {'label': 'Outside-firm fees (LDA income_total)', 'annual': {'2021': 35530250.0, '2022': 37020000.0, '2023': 36448000.0, '2024': 36967000.0, '2025': 37000500.0},
                     'partial': {'year': 2026, 'through_quarter': 'Q2', 'quarters': {'Q1': 9108000.0, 'Q2': 8480500.0}, 'total': 17588500.0,
                                 'nowcast': {'year': 2026, 'through_quarter': 'Q2', 'observed': 17588500.0, 'prior_total': 37000500.0, 'share': 0.4989584775218408,
                                             'share_years': [2023, 2024, 2025], 'shares': [0.4962823748902546, 0.5040711986366219, 0.49656085728571236], 'point': 36127286.85245313}}},
    'expenses_total': {'label': 'In-house expenses (LDA expenses_total)', 'annual': {'2021': 95333890.0, '2022': 98212019.0, '2023': 100554138.0, '2024': 101098642.0, '2025': 102385884.55},
                       'partial': {'year': 2026, 'through_quarter': 'Q2', 'quarters': {'Q1': 28287640.0, 'Q2': 23778530.0}, 'total': 52066170.0,
                                   'nowcast': {'year': 2026, 'through_quarter': 'Q2', 'observed': 52066170.0, 'prior_total': 102385884.55, 'share': 0.4928507304003711,
                                               'share_years': [2023, 2024, 2025], 'shares': [0.4879265336648801, 0.49633794289739325, 0.4943269648198786], 'point': 103991096.56684443}}}}


def telecom_files():
    """The contract examples with telecom added as the refit and the anchors builder produce it: a lobbying-only industry with its own LDA sector."""
    bundle, anchors = copy.deepcopy(contract.example('models')), copy.deepcopy(contract.example('anchors'))
    row = copy.deepcopy(bundle['industries']['property_casualty'])
    row.update(id=TELECOM, label='Telecommunications', mentat_key=TELECOM, ibisworld_code='51721+51711a', lobbyview_sector=TELECOM, history=[], scope='no OpenSecrets capture; lobbying only', lobbying_only=True,
               withheld=dict(pac=TELECOM_REASON, control=TELECOM_REASON), lobbying_history=[])
    row.pop('caveats', None)
    bundle['industries'][TELECOM] = row
    log = bundle['lobby_model']['log_refit']
    log['slopes'][TELECOM] = copy.deepcopy(log['slopes'][BANKS])
    log['prediction']['industries'][TELECOM] = copy.deepcopy(log['prediction']['industries'][BANKS])
    log['persistence']['industries'][TELECOM] = copy.deepcopy(TELECOM_PERSISTENCE)
    entry = copy.deepcopy(anchors['industries']['property_casualty'])
    entry.update(id=TELECOM, label='Telecommunications (Mentat Telecom)', forecast_industry=TELECOM)
    entry['mentat'].update(dataset='Telecom', workbook='Telecom Industry Spending Overview.xlsx')
    entry['status']['lda'] = 'ready'
    entry['lda'] = dict(entry['lda'], status='ready', reason=None, sector=TELECOM, label='Melange LDA telecom sector', source_file='sector_spend.csv', source_sha256='0' * 64,
                        through='2026-06-30', series=copy.deepcopy(TELECOM_SERIES))
    anchors['industries'][TELECOM] = entry
    assert contract.validate_model(bundle) == [] and contract.validate_kind(anchors, 'anchors') == [] and contract.check_anchor_invariants(anchors) == []
    return bundle, anchors


class TelecomLobbyingOnlyTests(unittest.TestCase):
    def project(self, bundle=None, anchors=None, request=None):
        files = telecom_files()
        with tempfile.TemporaryDirectory() as tmp, World(tmp, bundle=bundle or files[0], anchors=anchors or files[1]):
            return gf.project(request or {'industry': TELECOM, 'as_of': '2026-09-30'})

    def test_telecom_is_listed_with_the_lobbying_capability_only(self):
        bundle, anchors = telecom_files()
        with tempfile.TemporaryDirectory() as tmp, World(tmp, bundle=bundle, anchors=anchors):
            catalog = gf.catalog('2026-09-30')
        row = next(i for i in catalog['industries'] if i['id'] == TELECOM)
        self.assertEqual(row['capabilities'], dict(pac=False, control=False, lobbying=True))
        self.assertEqual((row['withheld']['pac'], row['withheld']['control']), (TELECOM_REASON, TELECOM_REASON))
        self.assertEqual(catalog['anchors']['industries'][TELECOM]['status'], 'ready')
        self.assertEqual({i['id'] for i in catalog['industries']}, {BANKS, CUS, 'property_casualty', TELECOM})

    def test_giving_is_unavailable_with_the_reason_and_lobbying_is_the_nowcast_with_bands(self):
        out = self.project()
        self.assertEqual(out['status'], 'ready')
        errors = contract.example('anchors')['lda_nowcast_errors']['by_cut']['2']
        for h in out['horizons']:
            self.assertEqual((h['pac']['status'], h['pac']['reason']), ('withheld', TELECOM_REASON))
            for chamber in ('House', 'Senate'):
                self.assertEqual((h['shares'][chamber]['status'], h['shares'][chamber]['reason']), ('withheld', TELECOM_REASON))
            self.assertEqual(h['lobbying']['status'], 'ready')
        fees, expenses = out['horizons'][0]['lobbying']['rows'][0]['series']
        self.assertLessEqual(abs(fees['projected'] - 36127287), 1)
        self.assertLessEqual(abs(expenses['projected'] - 103991097), 1)
        self.assertAlmostEqual(fees['band80_high'] / fees['projected'], math.exp(errors['q80']), places=12)
        self.assertAlmostEqual(fees['band95_low'] / fees['projected'], math.exp(-errors['max']), places=12)
        self.assertEqual(fees['remainder_status'], 'ready')
        self.assertLessEqual(abs(fees['projected_remaining'] - (36127287 - 17588500)), 1)
        rows = out['horizons'][1]['lobbying']['rows']
        self.assertEqual([r['year'] for r in rows], [2027, 2028])
        for r, h in zip(rows, ('1', '2')):                                      # the telecom persistence error of 1 and 2 years, in quadrature with the nowcast's blind error
            self.assertAlmostEqual(r['series'][0]['half_width_80'], math.hypot(errors['q80'], TELECOM_PERSISTENCE[h]['q80']), places=12)

    def test_a_bundle_without_the_persistence_errors_gives_the_nowcast_year_but_no_later_year(self):
        bundle, anchors = telecom_files()
        del bundle['lobby_model']['log_refit']['persistence']
        out = self.project(bundle, anchors)
        self.assertEqual(out['horizons'][0]['lobbying']['rows'][0]['status'], 'ready')
        later = out['horizons'][1]['lobbying']['rows']
        self.assertEqual([r['status'] for r in later], ['unavailable', 'unavailable'])
        self.assertIn('persistence', later[0]['reason'])

    def test_a_snapshot_that_predates_the_nowcast_is_not_made_up(self):
        bundle, anchors = telecom_files()
        for ind in anchors['industries'].values():
            for series in ind['lda']['series'].values():
                if series['partial']:
                    series['partial'].pop('nowcast')
        del anchors['lda_nowcast_errors']
        row = self.project(bundle, anchors)['horizons'][0]['lobbying']['rows'][0]
        self.assertEqual(row['status'], 'unavailable')
        self.assertIn('Rebuild the anchors', row['reason'])

    def test_a_missing_blind_error_table_leaves_the_bands_unavailable_not_invented(self):
        bundle, anchors = telecom_files()
        del anchors['lda_nowcast_errors']
        row = self.project(bundle, anchors)['horizons'][0]['lobbying']['rows'][0]
        self.assertEqual(row['status'], 'unavailable')
        self.assertIn('no blind-test errors', row['reason'])


class RenderFixtureTests(unittest.TestCase):
    """The golden payload the browser harness (test_giving_forecast_v2.cjs) renders: pinned to the calculator so the two cannot drift."""

    @staticmethod
    def payload():
        runs = {}
        with tempfile.TemporaryDirectory() as tmp, World(tmp):
            catalog = gf.catalog('2026-09-30')
            for name, request in (('banks', scenario(BANKS)), ('credit_unions', scenario(CUS)), ('banks_equal_weight', scenario(BANKS, scores={'House': 19.03485027624309, 'Senate': 56.39529613})),
                                  ('banks_blank', {'industry': BANKS, 'as_of': '2026-09-30'}), ('property_casualty', scenario('property_casualty'))):
                runs[name] = gf.project(request)
        stale = contract.example('anchors')
        shas = {i['mentat']['workbook']: i['mentat']['workbook_sha256'] for i in stale['industries'].values()}
        shas['Banking Industry Spending Overview.xlsx'] = 'e' * 64
        with tempfile.TemporaryDirectory() as tmp, World(tmp, published=shas):
            runs['banks_stale'] = gf.project(scenario(BANKS))
        with tempfile.TemporaryDirectory() as tmp, World(tmp, bundle=same_phase_bundle('House', BANKS)):
            runs['banks_same_phase_house'] = gf.project(scenario(BANKS))          # House on the same-phase anchor, Senate on the previous cycle
        return {'catalog': catalog, 'runs': runs}

    def test_the_payload_and_its_golden_carry_no_combined_reference_and_the_two_shown_house_waivers(self):
        payload = self.payload()
        self.assertEqual([str(key) for key in keys_of(payload) if 'combined' in str(key)], [])
        self.assertNotIn('combined_reference', GOLDEN.read_text(encoding='utf-8'))
        self.assertEqual([w['cell'] for w in payload['catalog']['waivers']], ['control_House / commercial_banks', 'control_House / credit_unions'])

    def test_the_render_payload_is_the_calculators_current_output(self):
        payload = json.loads(json.dumps(self.payload()))
        if os.environ.get('UPDATE_GIVING_FORECAST_GOLDEN'):
            GOLDEN.parent.mkdir(exist_ok=True)
            GOLDEN.write_text(json.dumps(payload, indent=1, sort_keys=True) + '\n', encoding='utf-8', newline='\n')
        # Platform libm implementations can differ in their final floating-point bits.
        # Preserve the authored fixture, all structure, and every nonnumeric value exactly.
        def compare(actual, expected, path='$'):
            if isinstance(expected, dict):
                self.assertIsInstance(actual, dict, path)
                self.assertEqual(actual.keys(), expected.keys(), path)
                for key in expected:
                    compare(actual[key], expected[key], path + '.' + key)
            elif isinstance(expected, list):
                self.assertIsInstance(actual, list, path)
                self.assertEqual(len(actual), len(expected), path)
                for index, (value, reference) in enumerate(zip(actual, expected)):
                    compare(value, reference, path + '[' + str(index) + ']')
            elif type(expected) in (int, float) and type(actual) in (int, float):
                self.assertTrue(math.isclose(actual, expected, rel_tol=1e-14, abs_tol=1e-12),
                                f'{path}: {actual!r} != {expected!r}')
            else:
                self.assertEqual(type(actual), type(expected), path)
                self.assertEqual(actual, expected, path)
        compare(payload, json.loads(GOLDEN.read_text(encoding='utf-8')))


if __name__ == '__main__':
    unittest.main()

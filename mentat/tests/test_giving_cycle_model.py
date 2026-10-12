"""Cycle-aware 50/50 "Giving by cycle" calculator (bundle `cycle_model`; owner rulings of 2026-10-07).

The bundle is the contract's cycle example (real parameters: phi, drift, step variances, IBISWorld paths, pace series of the 10-01 rebuild) and the anchors are the real
snapshot rebuilt with the odd-year split (tests/fixtures/giving_anchors_cycle.json). The expected numbers are the spec architect's INDEPENDENT re-computation
(modelspec/targets.py: numpy/scipy re-fit of the rebuild panel; tests/fixtures/giving_cycle_targets.json, grid 400 and the reference grid 1000), not this calculator's output.

Company PACs left that specification on 2026-10-08 (owner ruling D-116, "use the panel's nowcast for company PACs, drop the 50/50"): no budget change on either horizon and,
in the open cycle, the published PAC panel's R8_REMAINING nowcast O + (1 - s) B. Their expected numbers are worked by hand in PanelNowcastTests (the SPEC's PAC targets, which
carry the 50/50 and the D-099 blend, no longer apply); general giving keeps the SPEC targets and the numbers it had before the ruling.

The credit unions' company PACs left the anchors' single committee (SchoolsFirst) on 2026-10-08 (owner ruling D-117, "use the 16 credit union PACs"): the measure is the published PAC
panel's 16-PAC cohort, taken from the active PAC bundle (app/data/pac-giving-current.json) and checked here against the bundle file read directly (PanelCohortTests). Banks keep
the chart's 19 committees; general giving is untouched. The party split's company-PAC candidate basis divides the same 16-PAC cohort (Paul, 2026-10-08, "fix the credit union party split"):
its anchor share, observed D/R and R8 remainder are the cohort's, from the partisan-dollar lane's ledgers pinned in forecast/anchors/panel_cohort_split.json (worked by hand in PanelCohortTests).

The presidential-cycle factors (owner rulings D-118, D-119 and D-121, 2026-10-08): in place of the fitted cycle effect phi the company PACs take a fixed +9% on the step into the presidential
cycle (2027-28 = nowcast x 1.09 x exp(d_2)); general giving takes u = 12.56 / 6.74 = 1.8635 (OpenSecrets total cost of elections, presidential average over midterm average) on BOTH steps,
-log u at 2025-26 and +log u at 2027-28. Worked by hand in PresidentialFactorTests; the general-giving reference numbers (tests/fixtures/giving_cycle_targets.json) were recomputed
independently with numpy/scipy (FORECAST_OVERNIGHT.../d121/targets_d121.py, which first reproduces the original fixture rows to the bit with the fitted phi).
"""
import copy
import csv
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

FIXTURES = Path(__file__).resolve().parent / 'fixtures'
TARGETS = json.loads((FIXTURES / 'giving_cycle_targets.json').read_text(encoding='utf-8'))
ANCHORS = json.loads((FIXTURES / 'giving_anchors_cycle.json').read_text(encoding='utf-8'))
BANKS, CUS, PC = 'commercial_banks', 'credit_unions', 'property_casualty'
PAC, GEN = 'pac_to_candidates', 'general_giving'
OLD_VERSION = ROOT / 'app' / 'data' / 'versions' / '7bc7bcf175f7a3c291cc67300f981d610ef7b52059eefb7cb21e2dc4e020e0bb' / 'giving-models-v2.json'


PANEL_POINTER = ROOT / 'app' / 'data' / 'pac-giving-current.json'

TELECOM = 'telecom'
TELECOM_FIXTURE = json.loads((FIXTURES / 'giving_telecom_cycle.json').read_text(encoding='utf-8'))


def telecom_files():
    """The contract's cycle example and the fixture anchors with Telecom added as the refit and the anchors builder produce it (owner ruling 2026-10-08: OpenSecrets B09, IBISWorld
    wireless + cable, scored on the pooled phase and drift): the industry row, its two step blocks, its IBISWorld path, its lobbying terms and its anchor block, all recorded from the real files."""
    bundle, anchors, fx = contract.example('models_cycle'), copy.deepcopy(ANCHORS), copy.deepcopy(TELECOM_FIXTURE)
    bundle['industries'][TELECOM] = fx['industry']
    cm = bundle['cycle_model']
    for measure, step in fx['cycle_model']['step'].items():
        cm['measures'][measure]['step'][TELECOM] = step
    cm['budget_rule']['growth'][TELECOM] = fx['cycle_model']['growth']
    cm['g_prior'][TELECOM] = fx['cycle_model']['g_prior']
    log = bundle['lobby_model']['log_refit']
    log['slopes'][TELECOM], log['prediction']['industries'][TELECOM], log['persistence']['industries'][TELECOM] = fx['lobby']['slopes'], fx['lobby']['prediction'], fx['lobby']['persistence']
    anchors['industries'][TELECOM] = fx['anchor']
    return bundle, anchors


# D-118 / D-119 / D-121: the company PACs' fixed +9%; general giving's u is Paul's two averages of OpenSecrets' total cost of elections (inflation-adjusted $bn, presidential 2012-2024 over midterm
# 2010-2022), and the eight cycle totals they come from.
PAC_UPLIFT = 1.09
GENERAL_COST_BN = {'2010': 5.24, '2012': 8.59, '2014': 5.09, '2016': 8.51, '2018': 7.15, '2020': 18.34, '2022': 9.50, '2024': 14.80}
GENERAL_UPLIFT = 12.56 / 6.74          # 1.8635


def panel_by_hand(sector):
    """The active PAC bundle's current-cycle numbers of one sector read straight from the bundle file (integer cents -> dollars), with the panel's formula worked out here (not by
    pac_giving_forecast, not by the calculator): s is the CUBE ROOT of the product of the three shares, nowcast = O + (1 - s) B."""
    pointer = json.loads(PANEL_POINTER.read_text(encoding='utf-8'))
    item = next(s for s in json.loads((PANEL_POINTER.parent / pointer['active']).read_text(encoding='utf-8'))['sectors'] if s['sector'] == sector)
    frame = next(f for f in item['frames'] if f['product'] != 'ORIGIN' and f['target'] == 2026)
    origin = next(f for f in item['frames'] if f['product'] == 'ORIGIN' and f['target'] == 2026)
    years = ('2020', '2022', '2024')
    totals, seen = [frame['totals'][y] / 100 for y in years], [frame['historical_observed'][y] / 100 for y in years]
    shares = [o / f for o, f in zip(seen, totals)]
    s = (shares[0] * shares[1] * shares[2]) ** (1 / 3)
    o, b = frame['observed'] / 100, totals[2]
    each = [o + (1 - x) * b for x in shares]
    return dict(pointer=pointer, item=item, frame=frame, O=o, B=b, shares=shares, s=s, nominal=o + (1 - s) * b, low=min(each), high=max(each), odd=origin['annual']['2023'] / 100)


def full_request(ident, bundle):
    """The default request plus the control inputs the party split needs (the saved outlook, current control Republican), so the split is calculated."""
    req = request(ident)
    outlook = bundle['saved_outlook']['control']
    for h in req['horizons'].values():
        h.update(current_control={'House': 1, 'Senate': 1}, control_scores={'House': outlook['House']['value'], 'Senate': outlook['Senate']['value']})
    return req


class CycleToday(date):
    @classmethod
    def today(cls):
        return cls(2026, 10, 7)


class CycleWorld:
    """Bundle + anchors installed into a temp folder, the published-workbook check patched, today = 2026-10-07 (the spec's as-of date)."""

    def __init__(self, tmp, bundle=None, anchors=None, models_path=None):
        self.dir = Path(tmp)
        self.bundle = bundle if bundle is not None else contract.example('models_cycle')
        self.anchors = anchors if anchors is not None else copy.deepcopy(ANCHORS)
        (self.dir / 'models.json').write_text(json.dumps(self.bundle))
        (self.dir / 'anchors.json').write_text(json.dumps(self.anchors))
        shas = {i['mentat']['workbook']: i['mentat']['workbook_sha256'] for i in self.anchors['industries'].values()}
        self.patches = [patch.dict(os.environ, {'MENTAT_GIVING_MODELS': str(models_path or self.dir / 'models.json'), 'MENTAT_GIVING_ANCHORS': str(self.dir / 'anchors.json'),
                                                'MENTAT_GIVING_WAIVERS': str(self.dir / 'no-waivers.json'),
                                                'MENTAT_REPORT_DIRECTORY': str(self.dir)}),      # no published workbook in the world: the catalog's display-only own_cycle_ratio (read from the report workbooks, not from the bundle) is left out, as in the golden
                        patch.object(gf, 'date', CycleToday), patch.object(gf, '_published_workbook_sha256', lambda name: shas.get(name))]

    def clear(self):
        gf._load.cache_clear()
        gf._load_anchors.cache_clear()
        gf._load_waivers.cache_clear()

    def __enter__(self):
        self.clear()
        for p in self.patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self.patches):
            p.stop()
        self.clear()


def request(industry, growth=None, shift=None, as_of='2026-10-07'):
    """Blank inputs (every default) unless `growth` is {cycle: percent}; `shift` {cycle: pp} is added to the catalog default of that cycle.
    A request that sets a growth carries the page's `growth_basis` marker, as the current page does."""
    horizons = {'2026': {}, '2028': {}}
    for cycle in ('2026', '2028'):
        if growth and cycle in growth:
            horizons[cycle]['growth_pct'] = growth[cycle]
        if shift and cycle in shift:
            default = gf.catalog(as_of)['cycle_model_view']['growth'][industry][cycle]['growth_default']
            horizons[cycle]['growth_pct'] = default + shift[cycle]
    out = dict(industry=industry, as_of=as_of, horizons=horizons)
    if growth or shift:
        out['growth_basis'] = gf.GROWTH_BASIS
    return out


def measures(out):
    """{(cycle, measure id): measure} of a ready response."""
    return {(h['cycle'], m['id']): m for h in out['horizons'] for m in h['pac']['measures']}


def run(industry, world=None, **kw):
    if world is not None:
        return gf.project(request(industry, **kw))
    with tempfile.TemporaryDirectory() as tmp, CycleWorld(tmp):
        return gf.project(request(industry, **kw))


class TargetTests(unittest.TestCase):
    """The spec's target numbers, every industry and measure, at the default inputs (blank growth)."""

    @classmethod
    def setUpClass(cls):
        cls.out = {ident: run(ident) for ident in (BANKS, CUS, PC)}

    def test_every_industry_projects(self):
        for ident, out in self.out.items():
            self.assertEqual(out['status'], 'ready', ident)

    def check(self, grid, rel26, rel28):
        for ident, by_measure in TARGETS['grids'][grid]['rows'].items():
            got = measures(self.out[ident])
            for mid, want in by_measure.items():
                with self.subTest(grid=grid, industry=ident, measure=mid):
                    m26, m28 = got[(2026, mid)], got[(2028, mid)]
                    if mid == PAC:        # D-116: off the SPEC's 50/50 and blend targets; hand-checked in PanelNowcastTests
                        self.assertEqual((m26['model']['budget_change'], m28['model']['budget_change']), (0., 0.))
                        continue
                    self.assertAlmostEqual(m26['model']['budget_change'], want['x2026'], delta=1e-6)
                    self.assertAlmostEqual(m28['model']['budget_change'], want['x2028'], delta=1e-6)
                    for key, ref in (('projected_cycle_real_2025', 'M2026'), ('cycle_prediction_low', 'M2026_lo'), ('cycle_prediction_high', 'M2026_hi'),
                                     ('cycle_given_observed_median', 'layer_med'), ('cycle_given_observed_low', 'layer_lo'), ('cycle_given_observed_high', 'layer_hi')):
                        self.assertAlmostEqual(m26[key] / want[ref], 1., delta=rel26, msg=key)
                    self.assertEqual(m26['layer'], want['layer'])
                    self.assertAlmostEqual(m26['observed']['amount_real_2025'] / want['O_real'], 1., delta=1e-6)
                    self.assertAlmostEqual(m26['prob_full_below_observed'], want['p_full_below_obs'], delta=1e-4)
                    self.assertAlmostEqual(m26['remaining_given_observed_median'], want['remainder_med'], delta=1e-6 * want['O_real'] + 1.)
                    for key, ref in (('projected_cycle_real_2025', 'M2028_med'), ('cycle_prediction_low', 'M2028_lo'), ('cycle_prediction_high', 'M2028_hi')):
                        self.assertAlmostEqual(m28[key] / want[ref], 1., delta=rel28, msg=key)

    def test_the_calculator_reproduces_the_independent_reference_on_the_same_grid(self):
        self.check('400', rel26=1e-5, rel28=2e-4)

    def test_the_calculator_agrees_with_the_reference_grid_of_the_spec(self):
        self.check('1000', rel26=1e-4, rel28=3e-3)          # SPEC 4.3: 2026 to four digits, the chained 2028 tails differ <= 0.25% by grid construction

    def test_budget_default_values_of_the_spec(self):
        banks = measures(self.out[BANKS])
        self.assertEqual((banks[(2026, PAC)]['model']['budget_change'], banks[(2028, PAC)]['model']['budget_change']), (0., 0.))        # D-116: no 50/50 for company PACs (the SPEC's 0.236638 / -0.041669)
        self.assertEqual((banks[(2026, PAC)]['model']['budget_factor'], banks[(2026, PAC)]['model']['growth_applies']), (1., False))
        self.assertTrue(banks[(2026, GEN)]['model']['growth_applies'])
        # SPEC 4.2 prints 12.89% for the general-giving default; its target script (and this formula: s_odd 0.247293) gives 0.128898
        self.assertAlmostEqual(banks[(2026, GEN)]['model']['budget_change'], 0.128898, delta=1e-6)
        self.assertEqual(banks[(2026, PAC)]['model']['budget_growth_source'], 'default')

    def test_hand_check_of_the_banks_roster_pac_model(self):
        bundle, anchors = contract.example('models_cycle'), ANCHORS['industries'][BANKS]
        p = bundle['cycle_model']['measures'][PAC]
        base = anchors['pac_to_candidates']['cycles']['2024']['to_house_senate'] * ANCHORS['cpi']['multipliers']['2024']
        s_odd = anchors['pac_to_candidates']['odd_year']['2024']['to_house_senate'] / anchors['pac_to_candidates']['cycles']['2024']['to_house_senate']
        g = bundle['cycle_model']['budget_rule']['growth'][BANKS]['annual']
        go, ge = (1 + g['2023']) * (1 + g['2024']), (1 + g['2024']) * (1 + g['2025'])
        x = s_odd * go + (1 - s_odd) * ge - 1
        want = base * math.exp(-p['phi'] + p['drift']['d']['1'])             # D-116: the model's own point without the budget factor (x = 0)
        got = measures(self.out[BANKS])[(2026, PAC)]['projected_cycle_real_2025']
        self.assertAlmostEqual(got / want, 1., delta=1e-12)
        self.assertAlmostEqual(got / 1e6, 5.7825, delta=5e-5)                 # the SPEC's 4.2 hand check was $6.4667M with the 50/50 (x = 0.2366, factor 1.1183): 6.4667 / 1.1183
        self.assertAlmostEqual(x, 0.236638, delta=1e-6)                       # what the IBIS path would still imply, now not applied

    def test_the_response_exposes_each_measures_budget_change_and_the_input_it_came_from(self):
        out = self.out[BANKS]
        for h in out['horizons']:
            info = h['pac']['growth_input']
            self.assertEqual((info['source'], info['shift_pp']), ('default', 0.))
            self.assertEqual(info['window'], [h['cycle'] - 3, h['cycle'] - 1])
            for m in h['pac']['measures']:
                self.assertEqual(set(m['model']) >= {'budget_change', 'budget_growth_pct', 'budget_factor', 'growth_applies', 's_odd', 'G_odd', 'G_even', 'B_real_2025', 'dP', 'phi', 'drift', 'h'}, True)
                self.assertAlmostEqual(m['model']['budget_growth_pct'], 100 * m['model']['budget_change'], delta=1e-12)
        self.assertEqual(sorted(m['id'] for m in out['horizons'][0]['pac']['measures']), [GEN, PAC])
        self.assertEqual([m['status'] for m in self.out[PC]['horizons'][0]['pac']['measures']], ['unavailable', 'ready'])       # no Mentat roster PAC for P&C


class GrowthInputTests(unittest.TestCase):
    def test_a_blank_or_missing_growth_is_the_default_and_the_catalog_value_is_the_default(self):
        with tempfile.TemporaryDirectory() as tmp, CycleWorld(tmp):
            base = gf.project(request(BANKS))
            view = gf.catalog('2026-10-07')['cycle_model_view']['growth'][BANKS]
            blank = gf.project(dict(request(BANKS), horizons={'2026': {'growth_pct': ''}, '2028': {'growth_pct': None}}))
            shown = gf.project(request(BANKS, growth={'2026': view['2026']['growth_default'], '2028': view['2028']['growth_default']}))
        for other in (blank, shown):
            for key, m in measures(base).items():
                for field in ('projected_cycle_real_2025', 'cycle_prediction_low', 'cycle_given_observed_median'):
                    self.assertEqual(measures(other)[key].get(field), m.get(field), (key, field))

    def test_more_growth_means_more_money_in_2026_and_in_2028_for_general_giving(self):
        for ident in (BANKS, CUS, PC):
            with tempfile.TemporaryDirectory() as tmp, CycleWorld(tmp) as world:
                base, up, down = (measures(gf.project(request(ident, shift=s))) for s in (None, {'2026': 2, '2028': 2}, {'2026': -2, '2028': -2}))
            for key in base:
                if base[key]['status'] != 'ready' or key[1] != GEN:        # company PACs: growth does not move them (D-116), see test_growth_plus_or_minus_2_pp_leaves_company_pacs_unchanged
                    continue
                with self.subTest(industry=ident, cycle=key[0], measure=key[1]):
                    for field in ('projected_cycle_real_2025', 'cycle_prediction_low', 'cycle_prediction_high'):
                        self.assertGreater(up[key][field], base[key][field], field)
                        self.assertLess(down[key][field], base[key][field], field)
                    self.assertGreater(up[key]['model']['budget_change'], base[key]['model']['budget_change'])
                    if key[0] == 2026:
                        self.assertGreater(up[key]['cycle_given_observed_median'], base[key]['cycle_given_observed_median'])
                        self.assertLess(down[key]['cycle_given_observed_median'], base[key]['cycle_given_observed_median'])

    def test_growth_plus_or_minus_2_pp_leaves_company_pacs_unchanged(self):
        """D-116: no 50/50 for company PACs on either horizon. Every number of the PAC measure, both horizons, is identical at the default and at +/- 2 pp (either horizon alone too)."""
        fields = ('projected_cycle_real_2025', 'cycle_prediction_low', 'cycle_prediction_high', 'cycle_coefficient_low', 'cycle_coefficient_high', 'cycle_given_observed_low',
                  'cycle_given_observed_median', 'cycle_given_observed_high', 'remaining_given_observed_low', 'remaining_given_observed_median', 'remaining_given_observed_high',
                  'projected_remaining', 'interval_kind')
        for ident in (BANKS, CUS):
            with tempfile.TemporaryDirectory() as tmp, CycleWorld(tmp):
                base, *others = (measures(gf.project(request(ident, shift=s))) for s in (None, {'2026': 2, '2028': 2}, {'2026': -2, '2028': -2}, {'2026': 2}, {'2028': -2}))
            for other in others:
                for cycle in (2026, 2028):
                    with self.subTest(industry=ident, cycle=cycle):
                        for field in fields:
                            self.assertEqual(other[(cycle, PAC)].get(field), base[(cycle, PAC)].get(field), field)
                        self.assertEqual((other[(cycle, PAC)]['model']['budget_change'], other[(cycle, PAC)]['model']['budget_factor']), (0., 1.))
            with tempfile.TemporaryDirectory() as tmp, CycleWorld(tmp):
                up = measures(gf.project(request(ident, shift={'2026': 2, '2028': 2})))
            self.assertGreater(up[(2026, GEN)]['projected_cycle_real_2025'], base[(2026, GEN)]['projected_cycle_real_2025'])      # general giving still moves

    def test_general_giving_with_the_fitted_phi_is_what_it_was_before_the_presidential_factor(self):
        """The numbers general giving had before D-116 (the 50/50 and P1; recorded from the calculator of 2026-10-07 on this bundle and these anchors). D-121 replaced phi (0.738) by log 1.8635:
        with the factor patched back to exp(fitted phi) every one of them is reproduced to 1e-12, so the 50/50, P1, the drift and the intervals' spread are untouched and only phi moved."""
        before = {(BANKS, 2026): (11095557.125895955, 13864766.106186062, 5335297.309006488, 23074888.01536829),
                  (BANKS, 2028): (22865893.294932492, None, 10390935.390010012, 60375316.81140448),
                  (CUS, 2026): (276394.94754315936, 418845.45859615697, 57908.34639247795, 1319225.4966083628),
                  (CUS, 2028): (738662.0992695044, None, 136216.56619387527, 5897797.890439039),
                  (PC, 2026): (3220404.8128052936, 4485061.159272751, 1219499.4006501413, 8504315.092578553),
                  (PC, 2028): (7761877.474120953, None, 2734050.8709921152, 28123419.644775055)}
        for (ident, cycle), (point, given, low, high) in before.items():
            with tempfile.TemporaryDirectory() as tmp, CycleWorld(tmp) as world:
                with patch.object(gf, 'GENERAL_UPLIFT_FACTOR', math.exp(world.bundle['cycle_model']['measures'][GEN]['phi'])):
                    m = measures(gf.project(request(ident)))[(cycle, GEN)]
            with self.subTest(industry=ident, cycle=cycle):
                self.assertAlmostEqual(m['projected_cycle_real_2025'] / point, 1., delta=1e-12)
                self.assertAlmostEqual(m['cycle_prediction_low'] / low, 1., delta=1e-12)
                self.assertAlmostEqual(m['cycle_prediction_high'] / high, 1., delta=1e-12)
                if given is not None:
                    self.assertAlmostEqual(m['cycle_given_observed_median'] / given, 1., delta=1e-12)
                self.assertEqual(m['layer'] if cycle == 2026 else m['chained_from']['layer'], 'P1')
                self.assertTrue(m['model']['growth_applies'])
                self.assertNotEqual(m['model']['budget_change'], 0.)

    def test_the_shift_moves_every_annual_growth_of_the_window_by_the_same_amount(self):
        with tempfile.TemporaryDirectory() as tmp, CycleWorld(tmp):
            base = gf.project(request(BANKS))['horizons'][0]['pac']
            moved = gf.project(request(BANKS, shift={'2026': 3}))['horizons'][0]['pac']
        self.assertAlmostEqual(moved['growth_input']['shift_pp'], 3., delta=1e-3)       # the catalog shows the default to three decimals
        for year, g in base['measures'][0]['model']['growth_by_year'].items():
            self.assertAlmostEqual(moved['measures'][0]['model']['growth_by_year'][year] - g, moved['growth_input']['shift_pp'] / 100, delta=1e-12)
        self.assertEqual(moved['growth_input']['source'], 'user')

    def test_2028_chains_from_2026(self):
        with tempfile.TemporaryDirectory() as tmp, CycleWorld(tmp):
            base = measures(gf.project(request(BANKS)))
            only26 = measures(gf.project(request(BANKS, shift={'2026': 2})))
            only28 = measures(gf.project(request(BANKS, shift={'2028': 2})))
        for mid in (PAC, GEN):
            # a higher 2026 input raises the 2026 projection and, through the chain, the 2028 one although the 2028 input and its budget change stay put
            # (company PACs, D-116: growth moves neither horizon, so the same inputs leave both exactly where they were)
            check = self.assertGreater if mid == GEN else self.assertEqual
            check(only26[(2028, mid)]['projected_cycle_real_2025'], base[(2028, mid)]['projected_cycle_real_2025'])
            self.assertEqual(only26[(2028, mid)]['model']['budget_change'], base[(2028, mid)]['model']['budget_change'])
            self.assertEqual(only26[(2028, mid)]['chained_from']['cycle'], 2026)
            # the 2028 input leaves the 2026 projection exactly where it was
            self.assertEqual(only28[(2026, mid)]['projected_cycle_real_2025'], base[(2026, mid)]['projected_cycle_real_2025'])
            self.assertEqual(only28[(2026, mid)]['cycle_given_observed_median'], base[(2026, mid)]['cycle_given_observed_median'])
            check(only28[(2028, mid)]['projected_cycle_real_2025'], base[(2028, mid)]['projected_cycle_real_2025'])
        self.assertEqual(base[(2028, PAC)]['chained_from'], dict(layer='R8', cycle=2026))
        self.assertEqual(base[(2028, GEN)]['chained_from'], dict(layer='P1', cycle=2026))
        self.assertIsNone(base[(2028, PAC)]['cycle_coefficient_low'])

    def test_a_zero_growth_path_gives_the_model_without_the_budget_change(self):
        bundle = contract.example('models_cycle')
        for entry in bundle['cycle_model']['budget_rule']['growth'].values():
            entry['annual'] = {year: 0. for year in entry['annual']}
        with tempfile.TemporaryDirectory() as tmp, CycleWorld(tmp, bundle=bundle):
            out = measures(gf.project(request(BANKS)))
        p, g = bundle['cycle_model']['measures'][PAC], bundle['cycle_model']['measures'][GEN]
        base = ANCHORS['industries'][BANKS]['pac_to_candidates']['cycles']['2024']['to_house_senate'] * ANCHORS['cpi']['multipliers']['2024']
        self.assertAlmostEqual(out[(2026, PAC)]['projected_cycle_real_2025'] / (base * math.exp(-p['phi'] + p['drift']['d']['1'])), 1., delta=1e-12)
        self.assertAlmostEqual(out[(2026, PAC)]['projected_cycle_real_2025'] / 5782539.48, 1., delta=1e-6)       # the 10-01 rebuild's banks roster-PAC point (x = 0)
        base_g = ANCHORS['industries'][BANKS]['general_giving']['cycles']['2024'] * ANCHORS['cpi']['multipliers']['2024']
        self.assertAlmostEqual(out[(2026, GEN)]['projected_cycle_real_2025'] / (base_g * math.exp(-math.log(GENERAL_UPLIFT) + g['drift']['d']['1'])), 1., delta=1e-12)          # D-121: -log u
        self.assertAlmostEqual(out[(2026, GEN)]['projected_cycle_real_2025'] / 11701134.94, 1., delta=1e-6)      # ... and the general-giving point (was 10423757.31 with the fitted phi)

    def test_unusable_growth_is_refused_not_ignored(self):
        with tempfile.TemporaryDirectory() as tmp, CycleWorld(tmp):
            for bad in ('abc', -99.95, 501, True, float('inf')):
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    gf.project(dict(request(BANKS), growth_basis=gf.GROWTH_BASIS, horizons={'2026': {'growth_pct': bad}}))
            with self.assertRaisesRegex(ValueError, 'outside its supported range'):          # a shift that takes an annual growth to -100% or below
                gf.project(dict(request(BANKS), growth_basis=gf.GROWTH_BASIS, horizons={'2026': {'growth_pct': -99.9}}))


class GrowthBasisGuardTests(unittest.TestCase):
    """`growth_pct` changed meaning under the cycle model (cycle revenue growth -> average annual IBIS-window growth); a stale page sends the old one without the marker."""

    STALE = 'This page is out of date. Reload the page to use the current growth inputs.'

    def test_an_unmarked_growth_is_refused_with_the_reload_text(self):
        with tempfile.TemporaryDirectory() as tmp, CycleWorld(tmp):
            for horizons in ({'2026': {'growth_pct': '-1.55'}, '2028': {'growth_pct': -1.302}}, {'2028': {'growth_pct': -1.302}}, {'2026': {'growth_pct': 0}}):
                with self.subTest(horizons=horizons), self.assertRaisesRegex(ValueError, '^' + self.STALE + '$'):
                    gf.project(dict(industry=BANKS, as_of='2026-10-07', horizons=horizons))
            with self.assertRaisesRegex(ValueError, '^' + self.STALE + '$'):          # only the exact marker counts
                gf.project(dict(industry=BANKS, as_of='2026-10-07', growth_basis='cycle', horizons={'2026': {'growth_pct': '-1.55'}}))

    def test_a_marked_growth_computes_as_the_ibis_window_average(self):
        with tempfile.TemporaryDirectory() as tmp, CycleWorld(tmp):
            base = gf.project(request(BANKS))
            out = gf.project(dict(industry=BANKS, as_of='2026-10-07', growth_basis='ibis_window_avg', horizons={'2026': {'growth_pct': '-1.55'}, '2028': {'growth_pct': -1.302}}))
        self.assertEqual(out['status'], 'ready')
        pac = out['horizons'][0]['pac']
        self.assertEqual((pac['growth_input']['source'], pac['growth_input']['used_pct']), ('user', -1.55))
        self.assertLess(measures(out)[(2026, GEN)]['projected_cycle_real_2025'], measures(base)[(2026, GEN)]['projected_cycle_real_2025'])
        self.assertEqual(measures(out)[(2026, PAC)]['projected_cycle_real_2025'], measures(base)[(2026, PAC)]['projected_cycle_real_2025'])        # company PACs do not read it (D-116)

    def test_a_blank_growth_needs_no_marker(self):
        with tempfile.TemporaryDirectory() as tmp, CycleWorld(tmp):
            base = gf.project(request(BANKS))
            blank = gf.project(dict(industry=BANKS, as_of='2026-10-07', horizons={'2026': {'growth_pct': ''}, '2028': {'growth_pct': None}}))
            absent = gf.project(dict(industry=BANKS, as_of='2026-10-07', horizons={'2026': {}, '2028': {}}))
        for other in (blank, absent):
            self.assertEqual(other['status'], 'ready')
            for key, m in measures(base).items():
                self.assertEqual(measures(other)[key].get('projected_cycle_real_2025'), m.get('projected_cycle_real_2025'), key)

    def test_a_bundle_without_a_cycle_model_ignores_the_marker(self):
        horizons = {'2026': {'growth_pct': 7.0}, '2028': {'growth_pct': 7.0}}
        with tempfile.TemporaryDirectory() as tmp, CycleWorld(tmp, bundle=contract.example('models')):
            plain = gf.project(dict(industry=BANKS, as_of='2026-10-07', horizons=horizons))
            marked = gf.project(dict(industry=BANKS, as_of='2026-10-07', growth_basis='ibis_window_avg', horizons=horizons))
        self.assertEqual(plain['status'], 'ready')
        self.assertEqual({k: v for k, v in plain.items() if k != 'inputs'}, {k: v for k, v in marked.items() if k != 'inputs'})


class LayerTests(unittest.TestCase):
    def test_no_layer_quantile_and_no_remainder_falls_below_the_giving_already_observed(self):
        for ident in (BANKS, CUS, PC):
            for shift in (None, {'2026': -25}, {'2026': 25}):
                with tempfile.TemporaryDirectory() as tmp, CycleWorld(tmp):
                    got = measures(gf.project(request(ident, shift=shift)))
                for (cycle, mid), m in got.items():
                    if cycle != 2026 or m['status'] != 'ready':
                        continue
                    with self.subTest(industry=ident, measure=mid, shift=shift):
                        seen = m['observed']['amount_real_2025']
                        for key in ('cycle_given_observed_low', 'cycle_given_observed_median', 'cycle_given_observed_high'):
                            self.assertGreaterEqual(m[key], seen)
                        for key in ('remaining_given_observed_low', 'remaining_given_observed_median', 'remaining_given_observed_high'):
                            self.assertGreaterEqual(m[key], 0.)
                        self.assertAlmostEqual(m['remaining_given_observed_median'], m['cycle_given_observed_median'] - seen, delta=1e-6)

    def test_r8_uses_the_latest_pace_month_on_or_before_the_cut_and_never_falls_below_all_giving_observed(self):
        with tempfile.TemporaryDirectory() as tmp, CycleWorld(tmp):
            banks, cus = measures(gf.project(request(BANKS)))[(2026, PAC)], measures(gf.project(request(CUS)))[(2026, PAC)]
        self.assertEqual((banks['nowcast']['pace_month'], banks['observed']['through']), (18, '2026-06-30'))
        # D-117: the credit unions are the panel's 16-PAC cohort, which has its own cut (2026-09-26) and its own history on that date, so there is no pace month and nothing to floor
        self.assertEqual((cus['nowcast']['pace_month'], cus['nowcast']['panel_cut'], cus['observed']['through']), (None, '2026-09-26', '2026-09-26'))
        self.assertGreaterEqual(cus['cycle_given_observed_low'], cus['observed']['amount_real_2025'])
        self.assertFalse(cus['nowcast']['floored_at_observed'])
        self.assertNotIn('nowcast_floored_at_observed', cus['flags'])
        self.assertFalse(banks['nowcast']['floored_at_observed'])
        self.assertEqual((banks['layer'], cus['layer']), ('R8', 'R8'))
        for m in (banks, cus):
            self.assertNotIn('prob_full_below_observed', m)         # the panel publishes a point, no distribution to condition

    def test_without_a_pace_month_the_layer_is_p1_with_the_reason(self):
        anchors = copy.deepcopy(ANCHORS)
        entry = anchors['industries'][BANKS]
        entry['coverage'].update(coverage_end='2026-02-28')
        for key in ('general_giving', 'candidate_receipts', 'pac_to_candidates'):
            entry[key]['observed'] = [c for c in entry[key]['observed'] if c['through'] <= '2026-02-28']
        self.assertEqual(contract.check_anchor_invariants(anchors), [])
        with tempfile.TemporaryDirectory() as tmp, CycleWorld(tmp, anchors=anchors):
            got = measures(gf.project(request(BANKS)))
        m = got[(2026, PAC)]
        self.assertEqual(m['layer'], 'P1')
        self.assertIn('nowcast_unavailable_pace', m['flags'])
        self.assertIn('first pace month', m['nowcast_reason'])
        self.assertNotIn('nowcast', m)
        self.assertEqual(m['model']['budget_change'], 0.)          # still no 50/50 for the company PACs
        self.assertEqual(got[(2028, PAC)]['chained_from'], dict(layer='P1', cycle=2026))

    def test_a_pace_series_built_for_another_roster_is_not_used(self):
        bundle = contract.example('models_cycle')
        bundle['cycle_model']['pace']['series'][BANKS]['cycles']['2024']['F'] += 1000.
        with tempfile.TemporaryDirectory() as tmp, CycleWorld(tmp, bundle=bundle):
            m = measures(gf.project(request(BANKS)))[(2026, PAC)]
        self.assertEqual(m['layer'], 'P1')
        self.assertIn('nowcast_unavailable_pace', m['flags'])
        self.assertIn('different roster', m['nowcast_reason'])

    def test_a_pace_series_without_one_of_the_three_prior_cycles_is_not_used(self):
        bundle = contract.example('models_cycle')
        del bundle['cycle_model']['pace']['series'][BANKS]['cycles']['2020']
        with tempfile.TemporaryDirectory() as tmp, CycleWorld(tmp, bundle=bundle):
            m = measures(gf.project(request(BANKS)))[(2026, PAC)]
        self.assertEqual(m['layer'], 'P1')
        self.assertIn('three prior cycles (2020, 2022, 2024)', m['nowcast_reason'])

    def test_the_layers_and_the_growth_flags_the_page_reads(self):
        with tempfile.TemporaryDirectory() as tmp, CycleWorld(tmp):
            view = gf.catalog('2026-10-07')['cycle_model_view']
            out = gf.project(request(BANKS))
        self.assertEqual(view['layers'], {PAC: 'R8', GEN: 'P1'})
        self.assertEqual(view['growth_applies'], {PAC: False, GEN: True})
        self.assertEqual(out['cycle_model']['layers'], {PAC: 'R8', GEN: 'P1'})
        self.assertEqual(out['cycle_model']['growth_applies'], {PAC: False, GEN: True})
        self.assertEqual(view['growth'][BANKS]['2026']['budget_defaults'][PAC]['budget_growth_pct'], 0.)
        self.assertFalse(view['growth'][BANKS]['2026']['budget_defaults'][PAC]['growth_applies'])
        self.assertTrue(any('not a 95% interval' in note and 'does not affect company PACs' in note for note in view['measure_notes'][PAC]))
        self.assertTrue(any('two complete cycles' in note for note in view['measure_notes'][GEN]))


class PanelNowcastTests(unittest.TestCase):
    """Company PACs, open cycle 2025-26 (D-116): O + (1 - s) B worked BY HAND from the bundle's pace series and the anchors, never from the calculator's own arithmetic."""

    BUNDLE = contract.example('models_cycle')
    DEFLATOR = 1 / (((1 + 0.025726436) ** 0 + (1 + 0.025726436) ** 1) / 2)          # the open cycle's expected-inflation deflator to 2025 USD (no 2026 CPI yet)

    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as tmp, CycleWorld(tmp):
            cls.banks, cls.cus = (measures(gf.project(request(i))) for i in (BANKS, CUS))

    def by_hand(self, ident, p, cut):
        """(O, B, [s_2020, s_2022, s_2024], s, nominal nowcast, nominal range) of the panel's formula: s is the CUBE ROOT of the product of the three shares."""
        series = self.BUNDLE['cycle_model']['pace']['series'][ident]['cycles']
        block = ANCHORS['industries'][ident]['pac_to_candidates']
        b = block['cycles']['2024']['to_house_senate']
        o = [c['to_house_senate'] for c in block['observed'] if c['through'] <= cut][-1]
        shares = [series[str(y)]['O%d' % p] / series[str(y)]['F'] for y in (2020, 2022, 2024)]
        s = (shares[0] * shares[1] * shares[2]) ** (1 / 3)
        each = [o + (1 - x) * b for x in shares]
        return o, b, shares, s, o + (1 - s) * b, (min(each), max(each))

    def test_banks_2025_26_by_hand(self):
        m = self.banks[(2026, PAC)]
        o, b, shares, s, nominal, (low, high) = self.by_hand(BANKS, 18, '2026-06-30')
        # the hand arithmetic itself (cut 2026-06-30, the panel's m18): O $4,673,000, B $5,692,500, shares 0.72969 / 0.71300 / 0.79007, s = 0.743533
        self.assertEqual((o, b), (4673000., 5692500.))
        self.assertAlmostEqual(s, 0.7435327273, delta=1e-10)
        self.assertAlmostEqual(nominal, 6132939.95, delta=0.01)          # 4,673,000 + 0.2564673 x 5,692,500
        self.assertAlmostEqual(low, 5868000.00, delta=0.01)              # s = 0.79007 (2024)
        self.assertAlmostEqual(high, 6306723.79, delta=0.01)             # s = 0.71300 (2022)
        # converted ONCE to 2025 USD at the open cycle's deflator (the one the observed amount on the chart uses), not deflated twice
        self.assertAlmostEqual(m['observed']['amount_real_2025'], o * self.DEFLATOR, delta=1e-6)
        self.assertAlmostEqual(m['cycle_given_observed_median'], nominal * self.DEFLATOR, delta=1e-6)
        self.assertAlmostEqual(m['cycle_given_observed_median'] / 1e6, 6.05505, delta=5e-6)
        self.assertAlmostEqual(m['cycle_given_observed_low'], low * self.DEFLATOR, delta=1e-6)
        self.assertAlmostEqual(m['cycle_given_observed_high'], high * self.DEFLATOR, delta=1e-6)
        self.assertAlmostEqual(m['remaining_given_observed_median'], (1 - s) * b * self.DEFLATOR, delta=1e-6)        # the remainder is the nominal remainder, deflated once
        self.assertAlmostEqual(m['remaining_given_observed_median'] + m['observed']['amount_real_2025'], m['cycle_given_observed_median'], delta=1e-6)
        self.assertLess(m['cycle_given_observed_low'], m['cycle_given_observed_median'])
        self.assertLess(m['cycle_given_observed_median'], m['cycle_given_observed_high'])
        # the info the page and the Methods read
        info = m['nowcast']
        self.assertEqual((info['pace_month'], info['cycles']), (18, [2020, 2022, 2024]))
        self.assertAlmostEqual(info['completion_share'], s, delta=1e-12)
        self.assertAlmostEqual(info['nowcast_nominal'], nominal, delta=1e-6)
        self.assertEqual(info['range_nominal'], [low, high])
        self.assertEqual(m['interval_kind'], m['given_observed_kind'])
        self.assertIn("Range of the three prior cycles' pace (2020, 2022, 2024), not a 95% interval", m['interval_kind'])

    def test_credit_unions_2025_26_by_hand(self):
        """D-117: the panel's 16 credit-union PACs, read from the bundle file: O $2,078,390 at the panel's cut 2026-09-26, B $2,976,160 (the cohort's 2023-24 total), s = 0.830729."""
        m, hand = self.cus[(2026, PAC)], panel_by_hand(CUS)
        self.assertEqual((hand['O'], hand['B'], hand['frame']['cut']), (2078390., 2976160., '2026-09-26'))
        self.assertAlmostEqual(hand['s'], 0.8307293818, delta=1e-10)
        self.assertAlmostEqual(hand['nominal'], 2582166.44, delta=0.01)
        self.assertAlmostEqual(m['cycle_given_observed_median'], hand['nominal'] * self.DEFLATOR, delta=1e-6)
        self.assertAlmostEqual(m['cycle_given_observed_low'], hand['low'] * self.DEFLATOR, delta=1e-6)
        self.assertAlmostEqual(m['cycle_given_observed_high'], hand['high'] * self.DEFLATOR, delta=1e-6)
        self.assertLess(m['cycle_given_observed_low'], m['cycle_given_observed_median'])
        self.assertLess(m['cycle_given_observed_median'], m['cycle_given_observed_high'])
        self.assertAlmostEqual(m['cycle_given_observed_median'] / 1e6, 2.54937, delta=5e-6)          # 2025 USD, converted once

    def test_the_arithmetic_is_the_panels_own_pac_giving_forecast(self):
        """The mirror agrees with pac_giving_forecast.calculate (R8_REMAINING, lines 189-217) on the same numbers, in integer cents as the panel takes them."""
        sys.path.insert(0, str(ROOT / 'app'))
        import pac_giving_forecast as panel
        series = self.BUNDLE['cycle_model']['pace']['series'][BANKS]['cycles']
        o, b, shares, s, nominal, (low, high) = self.by_hand(BANKS, 18, '2026-06-30')
        cents = lambda dollars: int(round(dollars * 100))
        count, sha = panel.COHORTS['commercial_banks']
        frame = dict(sector='commercial_banks', target=2026, product='m18', issue='2026-07-20', cut='2026-06-30', information_as_of='2026-07-20', data_available_at='2026-07-20',
                     units='cents', accounting_basis=panel.ACCOUNTING_BASIS, history_cycles=[2020, 2022, 2024], cohort=dict(id='commercial_banks', count=count, ids_sha256=sha),
                     totals={str(y): cents(series[str(y)]['F']) for y in (2020, 2022, 2024)}, historical_observed={str(y): cents(series[str(y)]['O18']) for y in (2020, 2022, 2024)},
                     observed=cents(o), annual=None, log_growth=None)
        got = panel.calculate(frame, today=date(2026, 10, 8))
        self.assertEqual(got['status'], 'AVAILABLE')
        self.assertAlmostEqual(got['prediction_cents'] / 100, nominal, delta=0.01)
        self.assertAlmostEqual(got['trace']['completion_share'], s, delta=1e-12)
        with tempfile.TemporaryDirectory() as tmp, CycleWorld(tmp):
            m = measures(gf.project(request(BANKS)))[(2026, PAC)]
        self.assertAlmostEqual(m['nowcast']['nowcast_nominal'], got['prediction_cents'] / 100, delta=0.01)

    def test_the_2028_chain_starts_from_the_nowcast_with_no_budget_change(self):
        for ident, by in ((BANKS, self.banks), (CUS, self.cus)):
            m26, m28 = by[(2026, PAC)], by[(2028, PAC)]
            p = self.BUNDLE['cycle_model']['measures'][PAC]
            want = m26['cycle_given_observed_median'] * 1.09 * math.exp(p['drift']['d']['2'])           # dP = 1 (2028 is a presidential cycle), budget factor 1; D-118/D-119: the fixed +9% in place of the fitted phi
            with self.subTest(industry=ident):
                self.assertAlmostEqual(m28['projected_cycle_real_2025'] / want, 1., delta=1e-6)
                self.assertEqual(m28['chained_from'], dict(layer='R8', cycle=2026))
                self.assertEqual((m28['model']['budget_change'], m28['model']['budget_factor'], m28['model']['growth_applies']), (0., 1., False))
                self.assertLess(m28['cycle_prediction_low'], m28['projected_cycle_real_2025'])
                self.assertGreater(m28['cycle_prediction_high'], m28['projected_cycle_real_2025'])
                self.assertIn('95% prediction interval of the phase-and-drift step chained on the panel nowcast', m28['interval_kind'])
        self.assertAlmostEqual(self.banks[(2028, PAC)]['projected_cycle_real_2025'] / 1e6, 6.08140, delta=5e-6)          # was 5.44149 with the fitted phi (before D-118/D-119)

    def test_property_casualty_has_no_roster_pac(self):
        with tempfile.TemporaryDirectory() as tmp, CycleWorld(tmp):
            got = measures(gf.project(request(PC)))
        self.assertEqual([got[(2026, PAC)]['status'], got[(2028, PAC)]['status']], ['unavailable', 'unavailable'])

    def test_a_bundle_that_still_says_blend_is_overruled_for_company_pacs(self):
        bundle = contract.example('models_cycle')
        self.assertEqual(bundle['cycle_model']['measures'][PAC]['open_cycle_layer'], 'BLEND')          # the producer still writes it; the ruling lives in the app
        with tempfile.TemporaryDirectory() as tmp, CycleWorld(tmp, bundle=bundle):
            m = measures(gf.project(request(BANKS)))[(2026, PAC)]
        self.assertEqual(m['layer'], 'R8')
        self.assertNotIn('blend', m)


class PanelCohortTests(unittest.TestCase):
    """Owner ruling D-117 (Paul, 2026-10-08: "use the 16 credit union PACs"): the credit unions' company PACs are the published PAC panel's cohort, not the anchors' SchoolsFirst."""

    DEFLATOR = PanelNowcastTests.DEFLATOR

    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as tmp, CycleWorld(tmp) as world:
            cls.bundle = world.bundle
            cls.cus, cls.banks = (measures(gf.project(request(i))) for i in (CUS, BANKS))
            cls.out = gf.project(request(CUS))
            cls.view = gf.catalog('2026-10-07')['cycle_model_view']
            cls.full, cls.full_banks = (gf.project(full_request(i, world.bundle)) for i in (CUS, BANKS))          # with the control inputs, so the party split is calculated
        cls.hand = panel_by_hand(CUS)

    def test_the_population_is_named_and_the_cohort_is_the_panels(self):
        h = self.hand
        for cycle in (2026, 2028):
            pop = self.cus[(cycle, PAC)]['population']
            self.assertEqual(pop, dict(kind='panel_cohort', count=16, label='Credit unions: 16 PACs (published panel cohort)', observed_to='2026-09-26',
                                       bundle_sha256=h['pointer']['sha256'], cohort_ids_sha256=h['item']['cohort']['ids_sha256']))
            self.assertEqual(self.banks[(cycle, PAC)]['population'], dict(kind='chart_roster', count=19, label='Banks: 19 bank companies'))
            self.assertNotIn('population', self.cus[(cycle, GEN)])
            self.assertEqual(self.cus[(cycle, PAC)]['label'], 'Panel cohort PACs: own giving to House and Senate candidates')          # not "roster companies"
            self.assertEqual(self.banks[(cycle, PAC)]['label'], "Roster companies' PACs: own giving to House and Senate candidates")
        self.assertEqual(h['item']['cohort']['count'], 16)
        self.assertEqual(self.view['company_pacs'][CUS], dict(kind='panel_cohort', count=16, label='Credit unions: 16 PACs (published panel cohort)', observed_to='2026-09-26', status='ready',
                                                              bundle_sha256=h['pointer']['sha256'], cohort_ids_sha256=h['item']['cohort']['ids_sha256']))          # no split note: the split is the cohort's too
        self.assertEqual(self.view['company_pacs'][BANKS], dict(kind='chart_roster', count=19, label='Banks: 19 bank companies', observed_to='2026-06-30', status='ready'))
        self.assertIsNone(self.view['company_pacs'][PC])          # no roster PAC, nothing to name

    def test_the_anchor_bar_the_observed_amount_and_the_shares_are_the_panels_by_hand(self):
        h, m = self.hand, self.cus[(2026, PAC)]
        multiplier = ANCHORS['cpi']['multipliers']['2024']
        self.assertEqual(m['anchor_cycle_nominal'], h['B'])          # the 2023-24 bar is the cohort's 2024 total, not SchoolsFirst's $80,800
        self.assertAlmostEqual(m['anchor_cycle_real_2025'], h['B'] * multiplier, delta=1e-6)
        self.assertEqual((m['observed']['through'], m['observed']['amount_nominal']), (h['frame']['cut'], h['O']))
        self.assertAlmostEqual(m['observed']['amount_real_2025'], h['O'] * self.DEFLATOR, delta=1e-6)
        info = m['nowcast']
        for got, want in zip(info['shares'], h['shares']):
            self.assertAlmostEqual(got, want, delta=1e-15)
        self.assertEqual((info['cycles'], info['observed_nominal'], info['anchor_total_nominal']), ([2020, 2022, 2024], h['O'], h['B']))
        self.assertAlmostEqual(info['completion_share'], h['s'], delta=1e-12)
        self.assertAlmostEqual(info['nowcast_nominal'], h['nominal'], delta=1e-6)
        self.assertAlmostEqual(info['range_nominal'][0], h['low'], delta=1e-6)
        self.assertAlmostEqual(info['range_nominal'][1], h['high'], delta=1e-6)
        self.assertAlmostEqual(m['remaining_given_observed_median'], (1 - h['s']) * h['B'] * self.DEFLATOR, delta=1e-6)
        self.assertEqual((m['layer'], m['flags']), ('R8', []))          # no thin-base flag: this is no longer one PAC
        self.assertNotIn('flag_text', m)

    def test_the_nowcast_equals_the_panels_own_published_credit_union_nowcast_to_the_cent(self):
        import pac_giving_forecast as panel
        published = next(s for s in panel.catalog()['sectors'] if s['sector'] == CUS)['current']
        self.assertEqual((published['product'], published['cut']), ('L', '2026-09-26'))
        m = self.cus[(2026, PAC)]
        cents = m['nowcast']['nowcast_nominal'] * 100
        self.assertEqual(round(cents), round(published['prediction_cents']))
        self.assertAlmostEqual(cents, published['prediction_cents'], delta=1e-3)
        self.assertEqual(m['nowcast']['completion_share'], published['trace']['completion_share'])          # the same exact fractions, the same s, bit for bit
        self.assertEqual(round(m['observed']['amount_nominal'] * 100), published['observed_cents'])
        self.assertEqual(round(m['nowcast']['anchor_total_nominal'] * 100), published['trace']['B'])
        self.assertAlmostEqual(m['cycle_given_observed_median'], published['prediction_cents'] / 100 * self.DEFLATOR, delta=1e-6)          # converted to 2025 USD once, at the open cycle's deflator
        self.assertAlmostEqual(self.hand['nominal'] * 100, published['prediction_cents'], delta=1e-3)          # and the by-hand reading of the file agrees with the panel

    def test_2027_28_chains_from_the_new_nowcast_exactly_as_for_banks(self):
        m26, m28 = self.cus[(2026, PAC)], self.cus[(2028, PAC)]
        p = self.bundle['cycle_model']['measures'][PAC]
        self.assertAlmostEqual(m28['projected_cycle_real_2025'] / (m26['cycle_given_observed_median'] * 1.09 * math.exp(p['drift']['d']['2'])), 1., delta=1e-6)          # D-118/D-119: +9% in place of phi
        self.assertEqual((m28['chained_from'], m28['model']['budget_change'], m28['model']['growth_applies']), (dict(layer='R8', cycle=2026), 0., False))
        self.assertAlmostEqual(m28['projected_cycle_real_2025'] / 1e6, 2.56047, delta=5e-6)          # was 2.29104 with the fitted phi (before D-118/D-119)

    def test_the_two_observed_to_dates_stay_apart(self):
        pac, gen = self.cus[(2026, PAC)], self.cus[(2026, GEN)]
        self.assertEqual((pac['observed']['through'], gen['observed']['through']), ('2026-09-26', '2026-05-31'))
        self.assertEqual((pac['remainder_starts'], gen['remainder_starts']), ('2026-09-27', '2026-06-01'))
        self.assertEqual((self.banks[(2026, PAC)]['observed']['through'], self.banks[(2026, GEN)]['observed']['through']), ('2026-06-30', '2026-06-30'))
        self.assertEqual(self.out['anchors']['coverage_end'], '2026-05-31')          # the anchors' own date is still general giving's
        notice = next(n for n in self.out['notices'] if n.startswith('Company PACs:'))
        self.assertIn('Credit unions: 16 PACs (published panel cohort), observed to 2026-09-26', notice)
        self.assertIn('observed to 2026-05-31', notice)
        self.assertIn('never mixed in one figure', notice)

    def test_banks_company_pacs_are_unchanged_by_the_credit_union_ruling(self):
        """The numbers banks had before D-117 (recorded from the calculator of 2026-10-08 on this bundle and these anchors). 2025-26 is literally unchanged; the 2027-28 figures moved only by the
        D-118/D-119 uplift: the old literal values times 1.09 / exp(fitted phi), the interval ratios to the median exactly as they were."""
        m26, m28 = self.banks[(2026, PAC)], self.banks[(2028, PAC)]
        for key, want in dict(anchor_cycle_nominal=5692500.0, anchor_cycle_real_2025=5926312.257438551, projected_cycle_real_2025=5782539.48044099, cycle_prediction_low=4395580.776305029,
                              cycle_prediction_high=7607131.92284158, cycle_given_observed_low=5793477.239292936, cycle_given_observed_median=6055052.489636696,
                              cycle_given_observed_high=6226629.302478375, remaining_given_observed_median=1441398.921263163, projected_remaining=1168885.912067457).items():
            self.assertAlmostEqual(m26[key] / want, 1., delta=1e-12, msg=key)
        self.assertEqual((m26['observed']['amount_nominal'], m26['observed']['through']), (4673000.0, '2026-06-30'))
        self.assertAlmostEqual(m26['nowcast']['nowcast_nominal'], 6132939.949812336, delta=1e-6)
        self.assertEqual(m26['nowcast']['pace_month'], 18)
        self.assertNotIn('panel_cut', m26['nowcast'])
        moved = PAC_UPLIFT / math.exp(self.bundle['cycle_model']['measures'][PAC]['phi'])          # +9% against the fitted phi the old figures carried
        old = dict(projected_cycle_real_2025=5441490.834186593, cycle_prediction_low=4157733.921166177, cycle_prediction_high=7121625.159271819)
        for key, want in old.items():
            self.assertAlmostEqual(m28[key] / (want * moved), 1., delta=1e-12, msg=key)
        for key in ('cycle_prediction_low', 'cycle_prediction_high'):          # the step's prediction interval is unchanged: the same distance from the median
            self.assertAlmostEqual((m28[key] / m28['projected_cycle_real_2025']) / (old[key] / old['projected_cycle_real_2025']), 1., delta=1e-12, msg=key)

    # ---- the party split's company-PAC basis is the same 16 PACs (Paul, 2026-10-08: "fix the credit union party split") ------------------------------------------------------------
    # Cents, worked by hand from the partisan-dollar lane's canonical ledgers (candidate, office and party of the target cycle) for the rows the panel counts (hs == 1):
    # the 2023-24 cycle (ledger pac_party_chamber_v3) and 2025-26 to the panel's cut 2026-09-26 (ledger current2026). `pool` is the lane's rows of unresolved office, pooled over both chambers.
    SPLIT = dict(anchor=dict(House=dict(D=128256000, R=127060000, Other=0, Unknown=500000), Senate=dict(D=21550000, R=17200000, Other=1200000, Unknown=250000), pool=1600000),
                 observed=dict(House=dict(D=94689000, R=88175000, Other=0, Unknown=500000), Senate=dict(D=6200000, R=17075000, Other=0, Unknown=750000), pool=450000))
    LANE = Path(os.environ.get('LOCALAPPDATA', '')) / 'Mentat' / 'forecast'
    HISTORY_LEDGER = LANE / 'partisan-refinement-round2-20261005' / 'inputs' / 'pac_party_chamber_v3' / 'ledger.csv'
    CURRENT_LEDGER = LANE / 'political-model-20261006' / 'inputs' / 'current2026' / 'ledger.csv'

    @staticmethod
    def basis(out, i, chamber, bid='pac_candidate'):
        return next(b for b in out['horizons'][i]['shares'][chamber]['bases'] if b['id'] == bid)

    @staticmethod
    def ledger_dr(path, cycle, cut=None):
        """{(chamber, party): cents} straight from a lane ledger, no code of the app: credit-union rows the panel counts (hs == 1) that the lane resolves to a House / Senate candidate with a D or R."""
        out = {}
        with open(path, encoding='utf-8-sig', newline='') as stream:
            for r in csv.DictReader(stream):
                if (r['sector'], r['cycle'], r['hs'], r['canonical_hs_eligibility']) == (CUS, str(cycle), '1', 'include_hs') and (cut is None or r['date'] <= cut) and r['canonical_party'] in ('D', 'R'):
                    key = r['canonical_chamber'], r['canonical_party']
                    out[key] = out.get(key, 0) + int(r['cents'])
        return out

    def test_the_hand_numbers_add_up_to_the_panels_own_total_and_observed_amount_to_the_cent(self):
        h = self.hand
        for name, want in (('anchor', h['B']), ('observed', h['O'])):          # D + R + other + unknown recipients + the unresolved-office pool == the panel's 2023-24 total / observed amount
            part = self.SPLIT[name]
            self.assertEqual(sum(part[c][k] for c in gf.CHAMBERS for k in part[c]) + part['pool'], round(want * 100), name)
        data = json.loads(gf.SPLIT_FILE.read_text(encoding='utf-8'))          # and the pinned file the calculator reads is these numbers
        self.assertEqual((data['schema'], data['basis'], data['cohort_ids_sha256']), (gf.SPLIT_SCHEMA, 'canonical_cycle_office_nominal_pac_D_R', h['item']['cohort']['ids_sha256']))
        for name, part in (('anchor', data['cycles']['2024']), ('observed', data['observed'])):
            self.assertEqual({c: part[c] for c in gf.CHAMBERS}, {c: self.SPLIT[name][c] for c in gf.CHAMBERS}, name)
            self.assertEqual(part['unresolved_office_pool'], self.SPLIT[name]['pool'], name)
        self.assertEqual(data['observed']['through'], h['frame']['cut'])

    @unittest.skipUnless(HISTORY_LEDGER.is_file() and CURRENT_LEDGER.is_file(), 'the partisan-dollar lane ledgers are not on this machine')
    def test_the_hand_numbers_are_the_lanes_ledgers_read_directly(self):
        got = {'anchor': self.ledger_dr(self.HISTORY_LEDGER, 2024), 'observed': self.ledger_dr(self.CURRENT_LEDGER, 2026, cut=self.hand['frame']['cut'])}
        for name in ('anchor', 'observed'):
            for chamber in gf.CHAMBERS:
                for party in 'DR':
                    self.assertEqual(got[name][(chamber, party)], self.SPLIT[name][chamber][party], (name, chamber, party))

    def test_the_company_pac_anchor_share_is_the_cohorts_2024_republican_share_by_hand(self):
        want = {'House': 127060000 / (127060000 + 128256000), 'Senate': 17200000 / (17200000 + 21550000)}          # R / (R + D), 49.77% and 44.39%
        for i in (0, 1):
            for chamber in gf.CHAMBERS:
                d, r = self.SPLIT['anchor'][chamber]['D'] / 100, self.SPLIT['anchor'][chamber]['R'] / 100
                b = self.basis(self.full, i, chamber)
                self.assertEqual((b['status'], b['anchor_D'], b['anchor_R'], b['anchor_total_nominal']), ('ready', d, r, d + r))
                self.assertAlmostEqual(b['anchor_share'], want[chamber], delta=1e-15)
                self.assertFalse(b['thin_base'])          # $2.55M (House) and $387,500 (Senate) of two-party giving, no longer one PAC's $80,800
        self.assertAlmostEqual(want['House'], .4976578, delta=5e-8)
        self.assertAlmostEqual(want['Senate'], .4438710, delta=5e-8)

    def test_the_observed_dr_is_the_cohorts_to_the_panels_cut(self):
        for chamber in gf.CHAMBERS:
            d, r = self.SPLIT['observed'][chamber]['D'] / 100, self.SPLIT['observed'][chamber]['R'] / 100
            b = self.basis(self.full, 0, chamber)
            seen = b['observed']
            self.assertEqual((seen['through'], seen['D'], seen['R']), (self.hand['frame']['cut'], d, r))          # 2026-09-26, the cut of the company-PAC dollars
            self.assertAlmostEqual(seen['D_real_2025'], d * self.DEFLATOR, delta=1e-6)
            self.assertAlmostEqual(seen['R_real_2025'], r * self.DEFLATOR, delta=1e-6)
            self.assertEqual(b['remainder_starts'], '2026-09-27')
            self.assertEqual(self.basis(self.full, 0, chamber, 'employee_candidate')['observed']['through'], '2026-05-31')          # the employees' basis keeps general giving's own date

    def test_the_remainder_budget_is_the_cohorts_r8_remainder_and_the_share_change_is_the_models_own(self):
        m = self.cus[(2026, PAC)]
        for chamber in gf.CHAMBERS:
            s, b = self.full['horizons'][0]['shares'][chamber], self.basis(self.full, 0, chamber)
            two_party = (self.SPLIT['anchor'][chamber]['D'] + self.SPLIT['anchor'][chamber]['R']) / 100
            self.assertAlmostEqual(b['raw_projected_cycle_R_share'], b['anchor_share'] + s['change_pp'] / 100, delta=1e-15)          # anchor share + the share model's predicted change, as for every industry
            self.assertEqual(b['raw_projected_cycle_R_share'], b['projected_cycle_R_share'])
            a = b['remaining_allocation']
            self.assertAlmostEqual(a['remaining_total_real_2025'], m['remaining_given_observed_median'] * two_party / self.hand['B'], delta=1e-6)          # the cohort's R8 remainder x its two-party part
            seen = self.SPLIT['observed'][chamber]
            self.assertAlmostEqual(a['implied_cycle_total_real_2025'], (seen['D'] + seen['R']) / 100 * self.DEFLATOR + a['remaining_total_real_2025'], delta=1e-6)
            self.assertAlmostEqual(a['remaining_R_real_2025'], b['projected_cycle_R_share'] * a['remaining_total_real_2025'], delta=1e-6)
            self.assertAlmostEqual(a['implied_cycle_R_share'], (seen['R'] / 100 * self.DEFLATOR + a['remaining_R_real_2025']) / a['implied_cycle_total_real_2025'], delta=1e-12)
            self.assertEqual(b['remainder_status'], 'ready')

    def test_the_old_single_pac_note_is_gone_and_the_row_names_its_population(self):
        label = 'Credit unions: 16 PACs (published panel cohort)'
        self.assertFalse(hasattr(gf, 'SPLIT_NOTE'))
        for i in (0, 1):
            for chamber in gf.CHAMBERS:
                self.assertEqual(self.basis(self.full, i, chamber)['population'], self.cus[(2026, PAC)]['population'])          # the very population of the dollars
                self.assertEqual(self.basis(self.full, i, chamber)['population']['label'], label)
                self.assertNotIn('population', self.basis(self.full, i, chamber, 'employee_candidate'))
                self.assertFalse(any('roster_note' in b for b in self.full['horizons'][i]['shares'][chamber]['bases']))
        self.assertNotIn('single-PAC', json.dumps(self.full['notices']) + json.dumps(self.view))
        notice = next(n for n in self.full['notices'] if n.startswith('Company PACs:'))
        self.assertIn('and so is their party split', notice)

    def test_an_anchor_share_outside_the_fitted_range_keeps_its_flag_and_the_intervals_are_as_returned(self):
        for chamber in gf.CHAMBERS:
            s, b = self.full['horizons'][0]['shares'][chamber], self.basis(self.full, 0, chamber)
            lo, hi = s['fitted_share_range']
            outside = not lo - 1e-9 <= b['anchor_share'] <= hi + 1e-9
            self.assertEqual(b['anchor_share_outside_fitted_range'], outside)
            self.assertEqual('anchor_share_outside_fitted_range' in b['flags'], outside)
            self.assertEqual(b['prediction_outside_share_range'], b['cycle_prediction_low'] < 0 or b['cycle_prediction_high'] > 1)          # the flag is the existing one; nothing is clipped
            self.assertAlmostEqual(b['cycle_prediction_high'] - b['cycle_prediction_low'], 2 * (b['cycle_prediction_high'] - b['raw_projected_cycle_R_share']), delta=1e-12)

    def test_observed_giving_beyond_the_projection_is_budget_inconsistent_and_not_clipped_as_for_the_other_industries(self):
        data = json.loads(gf.SPLIT_FILE.read_text(encoding='utf-8'))
        data['observed']['House']['R'] += 80000000          # $800,000 moved from D to R: the sums still equal the panel's observed amount
        data['observed']['House']['D'] -= 80000000
        with tempfile.TemporaryDirectory() as tmp, CycleWorld(tmp) as world:
            path = Path(tmp) / 'split.json'
            path.write_text(json.dumps(data))
            with patch.object(gf, 'SPLIT_FILE', path):
                out = gf.project(full_request(CUS, world.bundle))
        b = self.basis(out, 0, 'House')
        self.assertEqual((b['status'], b['remainder_status']), ('ready', 'budget_inconsistent'))
        self.assertLess(b['projected_remaining_R_real_2025'], 0)          # drawn as returned
        self.assertIsNone(b['projected_remaining_R_share'])
        self.assertIn('no clipping', b['remainder_reason'])

    def test_a_split_that_does_not_match_the_panel_is_withheld_for_the_company_pacs_only(self):
        good = json.loads(gf.SPLIT_FILE.read_text(encoding='utf-8'))

        def edited(edit):
            data = copy.deepcopy(good)
            edit(data)
            return data
        cases = {'a different cut': edited(lambda d: d['observed'].update(through='2026-06-30')),
                 'a total that is off by a cent': edited(lambda d: d['cycles']['2024']['House'].update(D=d['cycles']['2024']['House']['D'] + 1)),
                 'another cohort': edited(lambda d: d.update(cohort_ids_sha256='0' * 64)),
                 'another schema': edited(lambda d: d.update(schema='panel-cohort-party-split-v0')),
                 'a missing field': edited(lambda d: d['observed'].pop('unresolved_office_pool')),
                 'no file': None}
        for name, data in cases.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp, CycleWorld(tmp) as world:
                path = Path(tmp) / 'split.json'
                if data is not None:
                    path.write_text(json.dumps(data))
                with patch.object(gf, 'SPLIT_FILE', path):
                    out = gf.project(full_request(CUS, world.bundle))
                for i in (0, 1):
                    for chamber in gf.CHAMBERS:
                        b = self.basis(out, i, chamber)
                        self.assertEqual(b['status'], 'unavailable')          # not the single PAC Paul turned down
                        self.assertIn('panel_cohort_split.json', b['reason'])
                        self.assertEqual(self.basis(out, i, chamber, 'employee_candidate'), self.basis(self.full, i, chamber, 'employee_candidate'))
                got = measures(out)
                self.assertEqual([got[(c, PAC)]['status'] for c in (2026, 2028)], ['ready', 'ready'])          # the dollars do not need the split
                self.assertEqual(got[(2026, PAC)]['cycle_given_observed_median'], self.cus[(2026, PAC)]['cycle_given_observed_median'])

    def test_an_unreadable_panel_withholds_the_company_pac_split_too_not_the_single_pac(self):
        import pac_giving_forecast as panel
        with tempfile.TemporaryDirectory() as tmp, CycleWorld(tmp) as world:
            with patch.object(panel, 'catalog', side_effect=ValueError('bundle hash mismatch')):
                out = gf.project(full_request(CUS, world.bundle))
        for chamber in gf.CHAMBERS:
            self.assertEqual(self.basis(out, 0, chamber)['status'], 'unavailable')
            self.assertIn('published PAC panel could not be read', self.basis(out, 0, chamber)['reason'])
            self.assertEqual(self.basis(out, 0, chamber, 'employee_candidate')['status'], 'ready')          # the employees' split does not need the panel

    def test_banks_and_the_employees_splits_are_unchanged_by_the_credit_union_fix(self):
        anchors = ANCHORS['industries']
        for chamber in gf.CHAMBERS:
            for i in (0, 1):
                b = self.basis(self.full_banks, i, chamber)          # banks: the chart's 19 committees, by hand from the anchors snapshot
                part = anchors[BANKS]['pac_to_candidates']['cycles']['2024'][chamber]
                self.assertEqual((b['anchor_D'], b['anchor_R'], b['anchor_total_nominal']), (part['D'], part['R'], part['D'] + part['R']))
                self.assertAlmostEqual(b['anchor_share'], part['R'] / (part['D'] + part['R']), delta=1e-15)
                self.assertNotIn('population', b)
                self.assertNotIn('roster_note', b)
                e = self.basis(self.full, i, chamber, 'employee_candidate')          # credit-union employees: general giving's receipts, not the cohort
                part = anchors[CUS]['candidate_receipts']['cycles']['2024'][chamber]
                self.assertEqual((e['anchor_D'], e['anchor_R']), (part['D'], part['R']))
            seen, cut = self.basis(self.full_banks, 0, chamber)['observed'], anchors[BANKS]['pac_to_candidates']['observed'][-1]
            self.assertEqual((seen['through'], seen['D'], seen['R']), (cut['through'], cut[chamber]['D'], cut[chamber]['R']))          # banks stay on their own cut, 2026-06-30
            self.assertEqual(seen['through'], '2026-06-30')
            self.assertEqual(self.basis(self.full, 0, chamber, 'employee_candidate')['observed']['through'], '2026-05-31')
        self.assertFalse(any(n.startswith('Company PACs:') for n in self.full_banks['notices']))

    def test_the_schema_2_rollback_split_is_still_the_anchors_own_roster(self):
        bundle = contract.example('models')
        with tempfile.TemporaryDirectory() as tmp, CycleWorld(tmp, bundle=bundle):
            outlook = bundle['saved_outlook']['control']
            horizons = {c: dict(growth_pct=7.0, current_control={'House': 1, 'Senate': 1}, control_scores={'House': outlook['House']['value'], 'Senate': outlook['Senate']['value']}) for c in ('2026', '2028')}
            with patch.object(gf, '_panel_block', side_effect=AssertionError('the rollback path read the panel')), patch.object(gf, '_cohort_split', side_effect=AssertionError('the rollback path read the split')):
                out = gf.project(dict(industry=CUS, as_of='2026-10-07', horizons=horizons))
        for chamber in gf.CHAMBERS:
            b = self.basis(out, 0, chamber)
            part = ANCHORS['industries'][CUS]['pac_to_candidates']['cycles']['2024'][chamber]
            self.assertEqual((b['status'], b['anchor_D'], b['anchor_R']), ('ready', part['D'], part['R']))          # SchoolsFirst, as before the cycle model
            self.assertNotIn('population', b)
            self.assertNotIn('roster_note', b)

    def test_an_unreadable_panel_withholds_the_credit_union_company_pacs_and_nothing_else(self):
        import pac_giving_forecast as panel
        with tempfile.TemporaryDirectory() as tmp, CycleWorld(tmp):
            with patch.object(panel, 'catalog', side_effect=ValueError('bundle hash mismatch')):
                out, cat, banks = gf.project(request(CUS)), gf.catalog('2026-10-07'), gf.project(request(BANKS))
        got = measures(out)
        for cycle in (2026, 2028):
            self.assertEqual(got[(cycle, PAC)]['status'], 'unavailable')          # not the single PAC Paul turned down
            self.assertIn('published PAC panel could not be read', got[(cycle, PAC)]['reason'])
            self.assertEqual(got[(cycle, GEN)]['status'], 'ready')
        self.assertEqual(cat['cycle_model_view']['company_pacs'][CUS]['status'], 'unavailable')
        self.assertEqual(cat['cycle_model_view']['company_pacs'][BANKS]['status'], 'ready')
        self.assertEqual(measures(banks)[(2026, PAC)]['status'], 'ready')

    def test_a_bundle_without_a_cycle_model_never_reads_the_panel(self):
        with tempfile.TemporaryDirectory() as tmp, CycleWorld(tmp, bundle=contract.example('models')):
            with patch.object(gf, '_panel_block', side_effect=AssertionError('the rollback path read the panel')):
                cat = gf.catalog('2026-10-07')
                out = gf.project(dict(industry=CUS, as_of='2026-10-07', horizons={'2026': {'growth_pct': 7.0}, '2028': {'growth_pct': 7.0}}))
        self.assertNotIn('cycle_model_view', cat)
        self.assertEqual(out['status'], 'ready')
        self.assertNotIn('cycle_model', out)
        self.assertFalse(any('population' in m for h in out['horizons'] for m in h['pac'].get('measures', [])))
        self.assertFalse(any(n.startswith('Company PACs:') for n in out['notices']))
        self.assertFalse(any('roster_note' in b for h in out['horizons'] for c in gf.CHAMBERS for b in h['shares'][c].get('bases', [])))


class TelecomTests(unittest.TestCase):
    """Telecom as a giving industry (owner ruling 2026-10-08: OpenSecrets B09 and IBISWorld wireless + cable for the general-giving model; coordinator call, consistent with D-117: the company
    PACs are the published PAC panel's 8 telecom PACs). Telecom is scored on the pooled phase and drift (never estimated in them), so nothing of another industry may move. The telecom parts
    of the bundle and of the anchors are the refit's and the anchors builder's real output (tests/fixtures/giving_telecom_cycle.json); every expected number below is worked by hand from them,
    from the panel bundle file or from the pinned inputs, never from the calculator's own arithmetic."""

    DEFLATOR = PanelNowcastTests.DEFLATOR
    CSV = ROOT / 'forecast' / 'refit' / 'inputs' / 'cycle_model' / 'ibis_telecom_2024_p87_p89_p45_p46.csv'
    # Cents, worked by hand from the partisan-dollar lane's canonical ledgers (candidate, office and party of the target cycle) for the rows the panel counts (hs == 1) of the 7 telecom PACs
    # that gave (the 8th id of the cohort, C00377044, has no row in either ledger): the 2023-24 cycle and 2025-26 to the panel's cut 2026-09-26.
    SPLIT = dict(anchor=dict(House=dict(D=328475000, R=355980000, Other=0, Unknown=350000), Senate=dict(D=42960000, R=50250000, Other=4500000, Unknown=300000), pool=600000),
                 observed=dict(House=dict(D=194100000, R=182150000, Other=100000, Unknown=0), Senate=dict(D=16900000, R=46350000, Other=0, Unknown=0), pool=3150000))
    LANE = PanelCohortTests.LANE
    HISTORY_LEDGER, CURRENT_LEDGER = PanelCohortTests.HISTORY_LEDGER, PanelCohortTests.CURRENT_LEDGER

    @classmethod
    def setUpClass(cls):
        cls.bundle, cls.anchors = telecom_files()
        cls.fx = TELECOM_FIXTURE
        with tempfile.TemporaryDirectory() as tmp, CycleWorld(tmp, bundle=cls.bundle, anchors=cls.anchors) as world:
            cls.out = gf.project(request(TELECOM))
            cls.by = measures(cls.out)
            cls.cat = gf.catalog('2026-10-07')
            cls.other = {i: gf.project(request(i)) for i in (BANKS, CUS, PC)}
            cls.full = gf.project(full_request(TELECOM, world.bundle))
        with tempfile.TemporaryDirectory() as tmp, CycleWorld(tmp):          # the same requests on the world WITHOUT telecom
            cls.before = {i: gf.project(request(i)) for i in (BANKS, CUS, PC)}
            cls.before_cat = gf.catalog('2026-10-07')
        cls.hand = panel_by_hand(TELECOM)

    # ---- offered, with its defaults ----------------------------------------------------------------------------------------------------------------------------------------------
    def test_telecom_is_offered_for_general_giving_company_pacs_and_lobbying_and_is_no_longer_lobbying_only(self):
        row = next(i for i in self.cat['industries'] if i['id'] == TELECOM)
        self.assertEqual(row['capabilities'], dict(pac=True, control=True, lobbying=True))          # the chamber split is scored on the pooled share models (owner ruling 2026-10-09, option A)
        self.assertFalse(row['lobbying_only'])
        self.assertEqual((row['code'], row['opensecrets_codes'], row['ibisworld_code']), ('B09', ['B09'], '51721+51711a'))
        self.assertEqual((row['withheld'], row['share_scoring']), ({}, 'pooled'))          # nothing is withheld any more
        self.assertTrue(any('B08 Telephone Utilities left out' in c for c in row['caveats']))
        self.assertEqual(self.out['status'], 'ready')
        for h in self.out['horizons']:
            self.assertEqual(h['pac']['status'], 'ready')
            self.assertEqual(sorted(m['id'] for m in h['pac']['measures']), [GEN, PAC])
            self.assertTrue(all(m['status'] == 'ready' for m in h['pac']['measures']))
            self.assertEqual(h['lobbying']['status'], 'ready')          # lobbying is as it was

    def test_the_ibis_defaults_are_the_wireless_plus_cable_revenue_weighted_window_averages(self):
        with open(self.CSV, encoding='utf-8', newline='') as stream:
            level = {(r['code'], int(r['year'])): float(r['revenue_million']) for r in csv.DictReader(stream)}
        total = lambda y: level[('51721', y)] + level[('51711a', y)]          # both constant 2024 USD: the growth of the summed revenue = the previous-year-revenue-weighted average
        annual = {y: total(y) / total(y - 1) - 1 for y in range(2023, 2028)}
        for y, g in annual.items():
            self.assertAlmostEqual(self.fx['cycle_model']['growth']['annual'][str(y)], g, delta=1e-12)
        view = self.cat['cycle_model_view']['growth'][TELECOM]
        for cycle in (2026, 2028):
            window = [annual[y] for y in (cycle - 3, cycle - 2, cycle - 1)]
            want = 100 * (math.prod(1 + g for g in window) ** (1 / 3) - 1)
            self.assertEqual(view[str(cycle)]['growth_default'], round(want, 3))
            self.assertEqual(view[str(cycle)]['growth_window'], [cycle - 3, cycle - 1])
            self.assertEqual(view[str(cycle)]['edition'], '2024-10 (51721) + 2024-12 (51711a)')
        self.assertEqual((view['2026']['growth_default'], view['2028']['growth_default']), (2.049, 4.241))
        self.assertAlmostEqual(self.cat['cycle_model_view']['g_prior_defaults'][TELECOM]['2028'], 100 * ((total(2025) + total(2026)) / (total(2023) + total(2024)) - 1), delta=1e-9)          # 2028's prior-cycle growth
        # the weights are revenue, not equal: an equal-weight average of the two components' growth would differ by more than a point
        equal = (level[('51721', 2025)] / level[('51721', 2024)] + level[('51711a', 2025)] / level[('51711a', 2024)]) / 2 - 1
        self.assertGreater(abs(equal - annual[2025]), .0005)
        for h in self.out['horizons']:
            info = h['pac']['growth_input']
            self.assertEqual((info['source'], info['shift_pp']), ('default', 0.))
        # the default budget change of the general giving is the 50/50 rule's, company PACs take none (D-116)
        self.assertTrue(self.by[(2026, GEN)]['model']['growth_applies'])
        self.assertEqual((self.by[(2026, PAC)]['model']['budget_change'], self.by[(2026, PAC)]['model']['growth_applies']), (0., False))

    def test_telecoms_step_variances_are_the_pooled_models_residual_variance_on_its_own_history_recomputed_here(self):
        """Telecom is SCORED on the pooled phase and drift, not estimated in them: its anchored-step residual variance is sum(e^2) / (pairs - 1) over its own two-cycle pairs, with
        e = (y[t] - y[t-2]) - (phi (Pres[t] - Pres[t-2]) + d_1), y the log of its OpenSecrets B09 amount in 2025 dollars, phi and d_1 the pooled parameters; the pooled company-PAC measure drops 2022."""
        h = {r['cycle']: r for r in self.fx['industry']['history']}
        self.assertEqual(sorted(h), [2012, 2014, 2016, 2018, 2020, 2022, 2024])
        for measure, key, skip_2022, pairs in ((GEN, 'individual_real_2025', False, 6), (PAC, 'pac_real_2025', True, 4)):
            p = self.bundle['cycle_model']['measures'][measure]
            errors = [(math.log(h[c][key]) - math.log(h[c - 2][key])) - (p['phi'] * ((c % 4 == 0) - ((c - 2) % 4 == 0)) + p['drift']['d']['1'])
                      for c in h if c - 2 in h and not (skip_2022 and 2022 in (c, c - 2))]
            step = self.fx['cycle_model']['step'][measure]
            with self.subTest(measure=measure):
                self.assertEqual((len(errors), step['pairs'], step['df']), (pairs, pairs, pairs - 1))
                self.assertAlmostEqual(math.fsum(e * e for e in errors) / (pairs - 1), step['sigma2'], delta=1e-12)
        # and no other industry's parameters moved: the pooled phi and drift are the contract example's, which the fixture's variances reproduce
        self.assertEqual(self.bundle['cycle_model']['measures'][GEN]['phi'], contract.example('models_cycle')['cycle_model']['measures'][GEN]['phi'])

    # ---- general giving ----------------------------------------------------------------------------------------------------------------------------------------------------------
    def test_general_giving_2025_26_is_the_formula_by_hand(self):
        """M = B exp(-log u + d_1)(1 + x/2), B the 2023-24 level in 2025 dollars, u = 12.56 / 6.74, x the 50/50 budget change of the lag window 2023-25 (the anchor cycle's odd-year share
        weighting the two years); the interval is exp(+-half) around it, half = t crit x sqrt(phi_se^2 + sigma2 of Telecom's own step)."""
        g = self.bundle['cycle_model']['measures'][GEN]
        block = self.fx['anchor']['general_giving']
        base = block['cycles']['2024'] * ANCHORS['cpi']['multipliers']['2024']
        s_odd = block['odd_year']['2024'] / block['cycles']['2024']
        a = self.fx['cycle_model']['growth']['annual']
        go, ge = (1 + a['2023']) * (1 + a['2024']), (1 + a['2024']) * (1 + a['2025'])
        x = s_odd * go + (1 - s_odd) * ge - 1
        point = base * math.exp(-math.log(GENERAL_UPLIFT) + g['drift']['d']['1']) * (1 + x / 2)
        step = self.fx['cycle_model']['step'][GEN]
        half = step['critical_95'] * math.sqrt(g['phi_se'] ** 2 + step['sigma2'])
        m = self.by[(2026, GEN)]
        self.assertAlmostEqual(m['model']['B_real_2025'] / base, 1., delta=1e-12)
        self.assertAlmostEqual(m['model']['budget_change'], x, delta=1e-12)
        self.assertAlmostEqual(m['projected_cycle_real_2025'] / point, 1., delta=1e-12)
        self.assertAlmostEqual(m['cycle_prediction_low'] / (point * math.exp(-half)), 1., delta=1e-12)
        self.assertAlmostEqual(m['cycle_prediction_high'] / (point * math.exp(half)), 1., delta=1e-12)
        self.assertEqual((step['pairs'], step['df']), (6, 5))          # Telecom's own six two-cycle pairs; the t critical value is its own, not the pool's
        self.assertAlmostEqual(step['critical_95'], 2.570581835636314, delta=1e-12)
        self.assertEqual(m['layer'], 'P1')          # conditioned on the giving already observed, as for every industry
        self.assertEqual((m['observed']['through'], m['observed']['amount_nominal']), ('2026-06-30', block['observed'][-1]['amount']))
        self.assertGreaterEqual(m['cycle_given_observed_low'], m['observed']['amount_real_2025'] - 1.)
        self.assertEqual(m['model']['presidential_uplift']['factor'], GENERAL_UPLIFT)

    def test_general_giving_2027_28_chains_from_the_conditioned_open_cycle_with_the_same_factor(self):
        m26, m28 = self.by[(2026, GEN)], self.by[(2028, GEN)]
        self.assertEqual(m28['chained_from'], dict(layer='P1', cycle=2026))
        self.assertAlmostEqual(m28['model']['phi'], math.log(GENERAL_UPLIFT), delta=1e-15)          # +log u on the presidential step
        self.assertGreater(m28['projected_cycle_real_2025'], m26['cycle_given_observed_median'])
        self.assertLess(m28['cycle_prediction_low'], m28['projected_cycle_real_2025'])
        self.assertGreater(m28['cycle_prediction_high'], m28['projected_cycle_real_2025'])

    # ---- company PACs: the panel's 8 telecom PACs ---------------------------------------------------------------------------------------------------------------------------
    def test_company_pacs_are_the_panels_eight_telecom_pacs_by_hand_and_equal_its_published_nowcast_to_the_cent(self):
        import pac_giving_forecast as panel
        h, m = self.hand, self.by[(2026, PAC)]
        self.assertEqual((h['item']['cohort']['count'], h['frame']['cut'], h['O'], h['B']), (8, '2026-09-26', 4427500., 7834150.))
        self.assertAlmostEqual(h['s'], .8218662742, delta=1e-10)          # the cube root of 732,724,900/903,204,900 x 610,900,000/783,160,000 x 687,260,000/783,415,000
        self.assertAlmostEqual(h['nominal'], 5823026.33, delta=.01)       # 4,427,500 + (1 - s) 7,834,150
        published = next(s for s in panel.catalog()['sectors'] if s['sector'] == TELECOM)['current']
        self.assertEqual((published['product'], published['cut']), ('L', '2026-09-26'))
        cents = m['nowcast']['nowcast_nominal'] * 100
        self.assertEqual(round(cents), round(published['prediction_cents']))          # the chart's nowcast IS the panel's published Telecom nowcast, to the cent (nominal)
        self.assertAlmostEqual(cents, published['prediction_cents'], delta=1e-3)
        self.assertEqual(m['nowcast']['completion_share'], published['trace']['completion_share'])          # the same exact fractions, the same s, bit for bit
        self.assertEqual(round(m['observed']['amount_nominal'] * 100), published['observed_cents'])
        self.assertEqual(round(m['nowcast']['anchor_total_nominal'] * 100), published['trace']['B'])
        self.assertAlmostEqual(m['cycle_given_observed_median'], h['nominal'] * self.DEFLATOR, delta=1e-6)          # converted to 2025 USD once, at the open cycle's deflator
        self.assertAlmostEqual(m['cycle_given_observed_low'], h['low'] * self.DEFLATOR, delta=1e-6)
        self.assertAlmostEqual(m['cycle_given_observed_high'], h['high'] * self.DEFLATOR, delta=1e-6)
        self.assertAlmostEqual(m['remaining_given_observed_median'], (1 - h['s']) * h['B'] * self.DEFLATOR, delta=1e-6)
        self.assertAlmostEqual(m['cycle_given_observed_median'] / 1e6, 5.74907, delta=5e-6)
        self.assertEqual((m['layer'], m['flags']), ('R8', []))
        self.assertEqual(m['anchor_cycle_nominal'], h['B'])          # the 2023-24 bar is the cohort's total
        self.assertEqual(m['nominal']['anchor_cycle'], h['B'])          # and its nominal bar is the same number
        self.assertAlmostEqual(m['nominal']['cycle_given_observed_median'], h['nominal'], delta=.01)          # nominal dollars: the panel's own published nowcast, to the cent (no conversion at all)
        self.assertEqual(m['nominal']['observed'], h['O'])
        self.assertEqual((m['observed']['through'], self.by[(2026, GEN)]['observed']['through']), ('2026-09-26', '2026-06-30'))          # the two dates stay apart

    def test_the_population_is_named_and_2027_28_is_the_nowcast_times_1_09_times_the_drift(self):
        h = self.hand
        label = 'Telecom: 8 PACs (published panel cohort)'
        for cycle in (2026, 2028):
            self.assertEqual(self.by[(cycle, PAC)]['population'], dict(kind='panel_cohort', count=8, label=label, observed_to='2026-09-26', bundle_sha256=h['pointer']['sha256'],
                                                                         cohort_ids_sha256=h['item']['cohort']['ids_sha256']))
            self.assertEqual(self.by[(cycle, PAC)]['label'], 'Panel cohort PACs: own giving to House and Senate candidates')
            self.assertNotIn('population', self.by[(cycle, GEN)])
        self.assertEqual(self.cat['cycle_model_view']['company_pacs'][TELECOM], dict(kind='panel_cohort', count=8, label=label, observed_to='2026-09-26', status='ready',
                                                                                    bundle_sha256=h['pointer']['sha256'], cohort_ids_sha256=h['item']['cohort']['ids_sha256']))
        self.assertEqual(h['item']['cohort']['ids_sha256'], 'd555b708f2b781afd84a9cbaff59b0bbf82980227326ab05de2d05b32d95afe6')
        p = self.bundle['cycle_model']['measures'][PAC]
        m26, m28 = self.by[(2026, PAC)], self.by[(2028, PAC)]
        self.assertAlmostEqual(m28['projected_cycle_real_2025'] / (m26['cycle_given_observed_median'] * 1.09 * math.exp(p['drift']['d']['2'])), 1., delta=1e-9)          # D-121: the fixed +9%
        self.assertEqual((m28['chained_from'], m28['model']['budget_change'], m28['model']['growth_applies']), (dict(layer='R8', cycle=2026), 0., False))
        self.assertAlmostEqual(m28['model']['phi'], math.log(1.09), delta=1e-15)
        self.assertAlmostEqual(m28['projected_cycle_real_2025'] / 1e6, 5.77409, delta=5e-6)
        self.assertGreater(m28['projected_cycle_real_2025'], m26['cycle_given_observed_median'])          # a presidential cycle always projects higher (D-118)
        step = self.fx['cycle_model']['step'][PAC]
        self.assertEqual((step['pairs'], step['df']), (4, 3))          # Telecom's own four pairs (2022 dropped, as the pooled PAC measure handles it)

    def test_growth_does_not_move_telecoms_company_pacs_but_moves_its_general_giving(self):
        with tempfile.TemporaryDirectory() as tmp, CycleWorld(tmp, bundle=self.bundle, anchors=self.anchors):
            up = measures(gf.project(request(TELECOM, shift={'2026': 2., '2028': 2.})))
        for cycle in (2026, 2028):
            self.assertEqual(up[(cycle, PAC)]['projected_cycle_real_2025'], self.by[(cycle, PAC)]['projected_cycle_real_2025'])
            self.assertGreater(up[(cycle, GEN)]['projected_cycle_real_2025'], self.by[(cycle, GEN)]['projected_cycle_real_2025'])

    # ---- the party split ---------------------------------------------------------------------------------------------------------------------------------------------------------
    def test_the_cohorts_party_split_adds_up_to_the_panels_own_total_and_observed_amount_to_the_cent(self):
        h = self.hand
        for name, want in (('anchor', h['B']), ('observed', h['O'])):          # D + R + other + unknown recipients + the lane's unresolved-office pool == the panel's 2023-24 total / observed amount
            part = self.SPLIT[name]
            self.assertEqual(sum(part[c][k] for c in gf.CHAMBERS for k in part[c]) + part['pool'], round(want * 100), name)
        data = json.loads(gf.SPLIT_FILES[TELECOM].read_text(encoding='utf-8'))          # and the pinned file the calculator reads is these numbers
        self.assertEqual((data['schema'], data['sector'], data['basis'], data['cohort_ids_sha256'], data['count']),
                         (gf.SPLIT_SCHEMA, TELECOM, 'canonical_cycle_office_nominal_pac_D_R', h['item']['cohort']['ids_sha256'], 8))
        for name, part in (('anchor', data['cycles']['2024']), ('observed', data['observed'])):
            self.assertEqual({c: part[c] for c in gf.CHAMBERS}, {c: self.SPLIT[name][c] for c in gf.CHAMBERS}, name)
            self.assertEqual(part['unresolved_office_pool'], self.SPLIT[name]['pool'], name)
            self.assertEqual(part['sum_cents'], round((h['B'] if name == 'anchor' else h['O']) * 100), name)
        self.assertEqual((data['observed']['through'], data['silent_committees']), (h['frame']['cut'], ['C00377044']))          # one cohort id has no row in either ledger
        credit_unions = json.loads(gf.SPLIT_FILE.read_text(encoding='utf-8'))
        self.assertNotIn('silent_committees', credit_unions)          # the credit unions' file is as it was

    @unittest.skipUnless(PanelCohortTests.HISTORY_LEDGER.is_file() and PanelCohortTests.CURRENT_LEDGER.is_file(), 'the partisan-dollar lane ledgers are not on this machine')
    def test_the_hand_numbers_are_the_lanes_ledgers_read_directly(self):
        def dr(path, cycle, cut=None):
            out = {}
            with open(path, encoding='utf-8-sig', newline='') as stream:
                for r in csv.DictReader(stream):
                    if (r['sector'], r['cycle'], r['hs'], r['canonical_hs_eligibility']) == (TELECOM, str(cycle), '1', 'include_hs') and (cut is None or r['date'] <= cut) and r['canonical_party'] in ('D', 'R'):
                        key = r['canonical_chamber'], r['canonical_party']
                        out[key] = out.get(key, 0) + int(r['cents'])
            return out
        got = {'anchor': dr(self.HISTORY_LEDGER, 2024), 'observed': dr(self.CURRENT_LEDGER, 2026, cut=self.hand['frame']['cut'])}
        for name in ('anchor', 'observed'):
            for chamber in gf.CHAMBERS:
                for party in 'DR':
                    self.assertEqual(got[name][(chamber, party)], self.SPLIT[name][chamber][party], (name, chamber, party))

    @unittest.skipUnless(PanelCohortTests.HISTORY_LEDGER.is_file() and PanelCohortTests.CURRENT_LEDGER.is_file(), 'the partisan-dollar lane ledgers are not on this machine')
    def test_the_builder_rederives_both_pinned_files_and_reconciles_them_to_the_cent(self):
        sys.path.insert(0, str(ROOT / 'forecast' / 'anchors'))
        import build_panel_cohort_split as builder
        import pac_giving_forecast as panel
        items = {s['sector']: s for s in panel.catalog()['sectors']}
        for sector, (out, ids_path) in builder.SECTORS.items():
            if not Path(ids_path).is_file():
                self.skipTest('the cohort ids file is not on this machine')
            fresh = builder.derive(items[sector], builder.rows(builder.HISTORY, sector), builder.rows(builder.CURRENT, sector), ids_path)
            self.assertEqual(out.read_text(encoding='utf-8'), json.dumps(fresh, indent=2, sort_keys=True) + '\n', sector)          # the file in the tree IS the fresh derivation (credit unions byte for byte as before)
        # a ledger that is one cent off the panel is refused, not rounded
        item = items[TELECOM]
        history, current = builder.rows(builder.HISTORY, TELECOM), builder.rows(builder.CURRENT, TELECOM)
        bumped = [dict(r) for r in history]
        next(r for r in bumped if r['cycle'] == '2024' and r['hs'] == '1')['cents'] = str(int(next(r for r in bumped if r['cycle'] == '2024' and r['hs'] == '1')['cents']) + 1)
        with self.assertRaisesRegex(ValueError, 'cents, the panel has'):
            builder.derive(item, bumped, current, builder.SECTORS[TELECOM][1])

    def test_the_panel_block_carries_the_cohorts_dr_by_chamber_when_the_file_matches_the_panel(self):
        block, why = gf._panel_block(TELECOM, 2026)
        self.assertIsNone(why)
        self.assertIsNone(block['split_reason'])
        self.assertEqual((block['committee_count'], block['single_pac']), (8, False))
        for chamber in gf.CHAMBERS:
            self.assertEqual(block['cycles']['2024'][chamber], {c: self.SPLIT['anchor'][chamber][c] / 100. for c in 'DR'})
            self.assertEqual({k: block['observed'][0][chamber][k] for k in 'DR'}, {c: self.SPLIT['observed'][chamber][c] / 100. for c in 'DR'})
        self.assertEqual(block['observed'][0]['through'], '2026-09-26')

    def test_a_split_that_does_not_match_the_panel_is_withheld_with_the_files_name_and_the_dollars_stay(self):
        good = json.loads(gf.SPLIT_FILES[TELECOM].read_text(encoding='utf-8'))

        def edited(edit):
            data = copy.deepcopy(good)
            edit(data)
            return data
        cases = {'a different cut': edited(lambda d: d['observed'].update(through='2026-06-30')),
                 'a total that is off by a cent': edited(lambda d: d['cycles']['2024']['House'].update(D=d['cycles']['2024']['House']['D'] + 1)),
                 'another cohort': edited(lambda d: d.update(cohort_ids_sha256='0' * 64)),
                 'the credit unions\' file': json.loads(gf.SPLIT_FILE.read_text(encoding='utf-8')),
                 'no file': None}
        for name, data in cases.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / 'split.json'
                if data is not None:
                    path.write_text(json.dumps(data))
                with patch.dict(gf.SPLIT_FILES, {TELECOM: path}):
                    block, _why = gf._panel_block(TELECOM, 2026)
                self.assertIn('panel_cohort_split_telecom.json', block['split_reason'])
                self.assertEqual(block['status'], 'ready')          # the dollars do not need the split
                self.assertNotIn('D', block['cycles']['2024'].get('House', {}))

    # ---- the party split: scored on the pooled share models (owner ruling 2026-10-09, option A) -----------------------------------------------------------------------------
    def test_the_chamber_split_is_computed_not_withheld_and_says_it_is_scored_on_the_pooled_model(self):
        for h in self.full['horizons']:
            for chamber in gf.CHAMBERS:
                s = h['shares'][chamber]
                self.assertEqual((s['status'], s.get('reason'), s['model_id'], s['share_scoring']), ('ready', None, 'control_' + chamber, 'pooled'))
                self.assertEqual(s['share_scoring_note'], gf.POOLED_SHARE_NOTE)
                self.assertEqual([b['id'] for b in s['bases']], ['employee_candidate', 'pac_candidate'])
                self.assertTrue(all(b['status'] == 'ready' for b in s['bases']))
        self.assertNotIn('share_scoring', self.other[BANKS]['horizons'][0]['shares']['House'])          # an industry with share-model rows of its own is scored as before

    def test_a_telecom_share_is_the_mentat_anchor_share_plus_the_pooled_models_change_by_hand(self):
        """House, employees, 2025-26: raw share = R/(D+R) of Telecom's 2023-24 employee giving + sum(beta x (target - anchor input)) over the pooled model's non-sector terms; the sector terms
        (Intercept, industry:*) are 0 in the change, so Telecom, which has none of its own, takes the reference level and the pooled coefficients are all that is used. Coefficients, covariance and
        the pooled step are read straight from the bundle, the inputs from the anchors, the fixture's growth history and the saved outlook, not from the calculator."""
        model, h = self.bundle['share_models']['control_House'], self.full['horizons'][0]
        terms, cov = model['terms'], model['covariance']
        beta = dict(zip(terms, model['beta']))
        row = self.fx['anchor']['candidate_receipts']['cycles']['2024']['House']
        share_anchor = row['R'] / (row['D'] + row['R'])
        ind = self.fx['industry']
        hist = {r['cycle']: r for r in ind['history']}
        z = lambda pct: (pct - ind['growth_mean']) / ind['growth_sd']
        outlook = self.bundle['saved_outlook']['control']['House']
        political = self.bundle['political_inputs']['anchor_cycles']['2024']
        target = dict(RMajNow=1., Forecast=outlook['value'] / 100, G_prior=z(hist[2024]['growth_pct']), Pres=0.)          # 2026: the 2024 growth is the prior cycle's; a midterm
        anchor = dict(RMajNow=float(political['current_control']['House']), Forecast=political['House']['p_rep'], G_prior=z(hist[2024]['prior_growth_pct']), Pres=1.)          # 2024
        delta = [0. if t == 'Intercept' or t.startswith('industry:') else target[t] - anchor[t] for t in terms]
        effect = sum(beta[t] * d for t, d in zip(terms, delta))
        var = sum(delta[i] * cov[i][j] * delta[j] for i in range(len(terms)) for j in range(len(terms)))
        step = next(iter(model['anchors'].values()))['prediction']['previous_cycle']          # the pooled residual: the bundle records the same one for every industry
        self.assertEqual({json.dumps(e['prediction']['previous_cycle'], sort_keys=True) for e in model['anchors'].values()}, {json.dumps(step, sort_keys=True)})
        half = step['critical_95'] * math.sqrt(var + 1 * step['sigma2'])
        got = next(b for b in h['shares']['House']['bases'] if b['id'] == 'employee_candidate')
        self.assertAlmostEqual(got['anchor_share'], share_anchor, delta=1e-15)
        self.assertAlmostEqual(got['raw_projected_cycle_R_share'], share_anchor + effect, delta=1e-12)
        self.assertAlmostEqual(got['projected_cycle_R_share'], share_anchor + effect, delta=1e-12)
        self.assertAlmostEqual(got['cycle_prediction_low'], share_anchor + effect - half, delta=1e-12)
        self.assertAlmostEqual(got['cycle_prediction_high'], share_anchor + effect + half, delta=1e-12)
        self.assertAlmostEqual(h['shares']['House']['change_pp'], 100 * effect, delta=1e-10)
        self.assertAlmostEqual(got['anchor_total_nominal'], row['D'] + row['R'], delta=1e-9)
        by_term = h['shares']['House']['change_by_term_pp']
        self.assertEqual(sorted(by_term), ['Forecast', 'G_prior', 'Pres', 'RMajNow'])          # no Intercept and no industry term: the sector level does not move the share
        self.assertEqual(by_term['RMajNow'], 0.)          # House control is Republican before both elections

    def test_the_sector_coefficients_do_not_move_telecoms_split_it_takes_the_reference_level(self):
        """The refit codes the sector as treatment dummies against the first sector alphabetically (no dummy for it): the pooled models are applied as a CHANGE from the anchor cycle, in which
        Intercept and industry:* are 0. Shift every sector coefficient of both models by a lot: Telecom's whole split is exactly what it was."""
        bundle, anchors = telecom_files()
        for model in (bundle['share_models']['control_House'], bundle['share_models']['control_Senate']):
            self.assertEqual([t for t in model['terms'] if t.startswith('industry:')], ['industry:commercial_banks', 'industry:credit_unions'])          # no dummy for the reference sector
            model['beta'] = [x + 0.4 if t == 'Intercept' or t.startswith('industry:') else x for t, x in zip(model['terms'], model['beta'])]
        with tempfile.TemporaryDirectory() as tmp, CycleWorld(tmp, bundle=bundle, anchors=anchors) as world:
            shifted = gf.project(full_request(TELECOM, world.bundle))
        split = lambda out: json.dumps({c: [h['shares'][c] for h in out['horizons']] for c in gf.CHAMBERS}, sort_keys=True)
        self.assertEqual(split(shifted), split(self.full))

    def test_the_pooled_scoring_is_for_the_named_industry_only_and_only_for_pooled_share_models(self):
        bundle, _anchors = telecom_files()
        row = bundle['industries'][TELECOM]
        self.assertEqual(gf._pooled_scored(bundle, row)['capabilities']['control'], True)
        self.assertEqual(row['capabilities']['control'], False)          # the bundle's own row is not modified
        for edit, why in ((lambda b: b['share_models']['control_House'].update(application='offset'), 'a share model that is not pooled'),
                          (lambda b: b['industries'][TELECOM].update(lobbying_only=True), 'a lobbying-only industry'),
                          (lambda b: b['industries'][TELECOM].update(id='crypto'), 'an industry that is not named')):
            doc = copy.deepcopy(bundle)
            edit(doc)
            with self.subTest(why):
                got = gf._pooled_scored(doc, doc['industries'][TELECOM])
                self.assertIs(got, doc['industries'][TELECOM])
                self.assertEqual(got['capabilities']['control'], False)
                self.assertIn('control', got['withheld'])
        done = copy.deepcopy(bundle)          # a bundle that already gives the industry a control capability is served as it is
        done['industries'][TELECOM]['capabilities']['control'] = True
        self.assertIs(gf._pooled_scored(done, done['industries'][TELECOM]), done['industries'][TELECOM])

    def test_telecoms_fitted_range_is_the_pooled_rows_in_its_own_growth_units(self):
        """With no rows of its own, Telecom's growth is checked against the pooled rows' standardised growth put back on its mean and sd (the model sees z-scores)."""
        model, ind = self.bundle['share_models']['control_House'], self.fx['industry']
        got = gf._fitted_growth(self.bundle, model, ind, 'prior_growth_pct')
        self.assertEqual(len(got), len(model['history']))
        for r, x in zip(model['history'], got):
            other = self.bundle['industries'][r['industry']]
            self.assertAlmostEqual((x - ind['growth_mean']) / ind['growth_sd'], (r['prior_growth_pct'] - other['growth_mean']) / other['growth_sd'], delta=1e-12)
        own = gf._fitted_growth(self.bundle, model, self.bundle['industries'][BANKS], 'prior_growth_pct')          # an industry with rows keeps its own raw values
        self.assertEqual(own, [r['prior_growth_pct'] for r in model['history'] if r['industry'] == BANKS])

    def test_telecoms_company_pac_basis_divides_the_panel_cohort_and_its_employee_basis_the_mentat_receipts(self):
        h = self.full['horizons'][0]
        for chamber in gf.CHAMBERS:
            employees, pacs = h['shares'][chamber]['bases']
            row = self.fx['anchor']['candidate_receipts']['cycles']['2024'][chamber]
            self.assertAlmostEqual(employees['anchor_share'], row['R'] / (row['D'] + row['R']), delta=1e-15)
            d, r = self.SPLIT['anchor'][chamber]['D'] / 100., self.SPLIT['anchor'][chamber]['R'] / 100.
            self.assertAlmostEqual(pacs['anchor_share'], r / (d + r), delta=1e-15)          # the 8-PAC cohort's own D / R, in cents -> dollars
            self.assertEqual(pacs['population']['label'], 'Telecom: 8 PACs (published panel cohort)')

    # ---- nothing else moves ------------------------------------------------------------------------------------------------------------------------------------------------------
    def test_banks_credit_unions_and_property_casualty_are_unchanged_by_adding_telecom(self):
        def without_hash(value):          # the bundle's own sha256 is the one thing that differs: it is the hash of a bundle that now holds one more industry
            if isinstance(value, dict):
                return {k: without_hash(v) for k, v in value.items() if k != 'model_sha256'}
            return [without_hash(v) for v in value] if isinstance(value, list) else value
        for ident in (BANKS, CUS, PC):
            with self.subTest(industry=ident):
                self.assertEqual(json.dumps(without_hash(self.other[ident]), sort_keys=True), json.dumps(without_hash(self.before[ident]), sort_keys=True))
        old, new = self.before_cat['cycle_model_view'], self.cat['cycle_model_view']
        for key in ('growth', 'company_pacs'):
            for ident in (BANKS, CUS, PC):
                self.assertEqual(old[key][ident], new[key][ident], (key, ident))
        self.assertEqual({i['id'] for i in self.cat['industries']} - {i['id'] for i in self.before_cat['industries']}, {TELECOM})
        for row in self.before_cat['industries']:
            self.assertEqual(row, next(i for i in self.cat['industries'] if i['id'] == row['id']), row['id'])

    def test_the_lobbying_only_mechanism_still_exempts_an_industry_that_is_flagged(self):
        """The flag is kept for future industries (contract.py); only an explicit lobbying_only industry is exempt from the giving checks."""
        bundle, anchors = telecom_files()
        bundle['industries'][TELECOM].update(lobbying_only=True, capabilities=dict(pac=False, control=False, lobbying=True), withheld=dict(pac='lobbying only', control='lobbying only'))
        self.assertEqual(contract.check_cycle_model(bundle), [])          # still has its step blocks: nothing is required of a flagged industry
        for measure in bundle['cycle_model']['measures'].values():
            measure['step'].pop(TELECOM)
        bundle['cycle_model']['budget_rule']['growth'].pop(TELECOM)
        bundle['cycle_model']['g_prior'].pop(TELECOM)
        self.assertEqual(contract.check_cycle_model(bundle), [])
        bundle['industries'][TELECOM]['lobbying_only'] = False
        self.assertTrue(any('no entry for telecom' in p for p in contract.check_cycle_model(bundle)))


class PresidentialFactorTests(unittest.TestCase):
    """Owner rulings D-118, D-119 and D-121 (Paul, 2026-10-08): "use +9% and restart 8806", then "Different for both PAC and General" with OpenSecrets data.
    Company PACs: a fixed +9% in place of the fitted phi on the step into the presidential cycle (2027-28 = nowcast x 1.09 x exp(d_2)); the midterm step keeps the fitted phi.
    General giving: u = 12.56 / 6.74 = 1.8635 in place of phi on BOTH steps, symmetrically (-log u at 2025-26, +log u at 2027-28), the drift, the 50/50, P1 and the spread kept."""

    DEFLATOR = PanelNowcastTests.DEFLATOR
    PAC_RATIOS = {'2016/2014': 0.9969291514830991, '2020/2018': 0.9639711510629424, '2024/2022': 0.9829388610358335}          # national FEC all-PAC presidential / preceding midterm, real 2025 USD
    PAC_SOURCE = 'owner rule D-118/D-121: +9% (PAC spending less cyclical; national FEC all-PAC ratio 0.98)'
    GENERAL_SOURCE = 'owner rule D-118/D-121: OpenSecrets total cost of elections, presidential avg 2012-2024 vs midterm avg 2010-2022'
    BANKS_OLD_2028 = dict(projected_cycle_real_2025=5441490.834186593, cycle_prediction_low=4157733.921166177, cycle_prediction_high=7121625.159271819)          # company PACs, the fitted phi (before the rulings)

    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as tmp, CycleWorld(tmp) as world:
            cls.bundle = world.bundle
            cls.by = {i: measures(gf.project(request(i))) for i in (BANKS, CUS, PC)}
            cls.view = gf.catalog('2026-10-07')['cycle_model_view']
        cls.p, cls.g = cls.bundle['cycle_model']['measures'][PAC], cls.bundle['cycle_model']['measures'][GEN]

    # ---- company PACs: x1.09 on the presidential step ---------------------------------------------------------------------------------------------------------------------------
    def test_company_pacs_2027_28_is_the_nowcast_times_1_09_times_the_drift_by_hand_and_above_2025_26(self):
        d2 = self.p['drift']['d']['2']
        nominal = {BANKS: PanelNowcastTests('test_banks_2025_26_by_hand').by_hand(BANKS, 18, '2026-06-30')[4], CUS: panel_by_hand(CUS)['nominal']}
        for ident, want_nominal in nominal.items():
            m26, m28 = self.by[ident][(2026, PAC)], self.by[ident][(2028, PAC)]
            nowcast = want_nominal * self.DEFLATOR
            with self.subTest(industry=ident):
                self.assertAlmostEqual(m26['cycle_given_observed_median'], nowcast, delta=1e-6)          # 2025-26 is the nowcast, unchanged
                self.assertAlmostEqual(m28['projected_cycle_real_2025'] / (nowcast * 1.09 * math.exp(d2)), 1., delta=1e-9)
                self.assertGreater(m28['projected_cycle_real_2025'], m26['cycle_given_observed_median'])
                self.assertLess(m28['cycle_prediction_low'], m28['projected_cycle_real_2025'])
                self.assertGreater(m28['cycle_prediction_high'], m28['projected_cycle_real_2025'])
        # the dollars the ruling's text works out: nowcast x exp(d_2) = $5,579,268 (banks) and $2,349,052 (credit unions), times 1.09
        for ident, (stepped, uplifted) in {BANKS: (5579268., 6081402.), CUS: (2349052., 2560467.)}.items():
            m26, m28 = self.by[ident][(2026, PAC)], self.by[ident][(2028, PAC)]
            self.assertAlmostEqual(m26['cycle_given_observed_median'] * math.exp(d2), stepped, delta=1.)
            self.assertAlmostEqual(m28['projected_cycle_real_2025'], uplifted, delta=1.)

    def test_company_pacs_model_block_says_what_was_applied(self):
        uplift = dict(factor=1.09, source=self.PAC_SOURCE, ratio_by_pair=self.PAC_RATIOS)
        self.assertAlmostEqual(math.exp(sum(math.log(r) for r in self.PAC_RATIOS.values()) / 3), 0.981, delta=5e-4)          # the national data the source line quotes
        self.assertEqual(self.view['presidential_uplift'][PAC], uplift)
        for ident in (BANKS, CUS):
            m26, m28 = self.by[ident][(2026, PAC)]['model'], self.by[ident][(2028, PAC)]['model']
            self.assertEqual(m28['presidential_uplift'], uplift)
            self.assertAlmostEqual(m28['phi'], math.log(1.09), delta=1e-15)          # the effect actually applied
            self.assertEqual((m28['phi_fitted'], m28['dP'], m28['drift']), (self.p['phi'], 1., self.p['drift']['d']['2']))          # the fitted one is kept beside it; the D-092 drift is the fitted one
            self.assertNotIn('presidential_uplift', m26)          # the company PACs' midterm step keeps the fitted phi (it is not a presidential step)
            self.assertEqual((m26['phi'], m26['dP'], m26['drift']), (self.p['phi'], -1., self.p['drift']['d']['1']))
            self.assertNotIn('phi_fitted', m26)
            m26m = self.by[ident][(2026, PAC)]
            self.assertAlmostEqual(m26m['projected_cycle_real_2025'] / (m26['B_real_2025'] * math.exp(-self.p['phi'] + self.p['drift']['d']['1'])), 1., delta=1e-12)          # the unconditioned point, fitted phi
            self.assertNotIn('observed_exceeds_unconditional_projection', m26m['flags'])
        self.assertEqual([self.by[PC][(c, PAC)]['status'] for c in (2026, 2028)], ['unavailable', 'unavailable'])          # no company PACs there

    def test_company_pac_factor_only_moves_the_location_of_the_step(self):
        """Patched to exp(fitted phi) the step is the pre-ruling one to the last digit (the interval and the drift are the fitted ones); another size flows straight through."""
        with tempfile.TemporaryDirectory() as tmp, CycleWorld(tmp):
            with patch.object(gf, 'PAC_UPLIFT_FACTOR', math.exp(self.p['phi'])):
                same = measures(gf.project(request(BANKS)))[(2028, PAC)]
            with patch.object(gf, 'PAC_UPLIFT_FACTOR', 1.2):
                bigger = measures(gf.project(request(BANKS)))[(2028, PAC)]
        for key, want in self.BANKS_OLD_2028.items():
            self.assertAlmostEqual(same[key] / want, 1., delta=1e-12, msg=key)
            self.assertAlmostEqual(bigger[key] / self.by[BANKS][(2028, PAC)][key], 1.2 / 1.09, delta=1e-12, msg=key)
        self.assertEqual(bigger['model']['presidential_uplift']['factor'], 1.2)

    # ---- general giving: u = 12.56 / 6.74 on both steps -------------------------------------------------------------------------------------------------------------------------
    def test_general_giving_factor_is_pauls_two_averages_and_the_eight_totals_are_exposed(self):
        totals = GENERAL_COST_BN
        self.assertAlmostEqual(GENERAL_UPLIFT, 1.8635, delta=5e-5)          # "roughly 86% higher"
        self.assertAlmostEqual(math.fsum(totals[y] for y in ('2012', '2016', '2020', '2024')) / 4, 12.56, delta=1e-12)          # the presidential average is the four totals' mean
        self.assertAlmostEqual(math.fsum(totals[y] for y in ('2010', '2014', '2018', '2022')) / 4, 6.74, delta=.006)          # the midterm average Paul gave (6.74) is the totals' mean (6.745) to the cent
        uplift = dict(factor=GENERAL_UPLIFT, source=self.GENERAL_SOURCE, ratio_by_pair=totals)
        self.assertEqual(list(totals), ['2010', '2012', '2014', '2016', '2018', '2020', '2022', '2024'])
        self.assertEqual(self.view['presidential_uplift'][GEN], uplift)
        for ident in (BANKS, CUS, PC):
            for cycle, d_p in ((2026, -1.), (2028, 1.)):          # BOTH steps carry it
                model = self.by[ident][(cycle, GEN)]['model']
                with self.subTest(industry=ident, cycle=cycle):
                    self.assertEqual(model['presidential_uplift'], uplift)
                    self.assertAlmostEqual(model['phi'], math.log(GENERAL_UPLIFT), delta=1e-15)
                    self.assertEqual((model['phi_fitted'], model['dP']), (self.g['phi'], d_p))
                    self.assertEqual(model['drift'], self.g['drift']['d'][str(1 if cycle == 2026 else 2)])          # the drift is the fitted one on both steps
                    self.assertTrue(model['growth_applies'])
                    self.assertNotEqual(model['budget_change'], 0.)          # the 50/50 is untouched

    def test_general_giving_2025_26_point_and_interval_by_hand(self):
        """M = B exp(-log u + d_1)(1 + x/2); the interval is exp(+-half) around it, half = t crit x sqrt(se^2 + sigma^2): the fitted spread, not scaled by the factor."""
        for ident in (BANKS, CUS, PC):
            m = self.by[ident][(2026, GEN)]
            step, anchors = self.g['step'][ident], ANCHORS['industries'][ident]['general_giving']
            base = anchors['cycles']['2024'] * ANCHORS['cpi']['multipliers']['2024']
            half = step['critical_95'] * math.sqrt(self.g['phi_se'] ** 2 + step['sigma2'])
            point = base * math.exp(-math.log(GENERAL_UPLIFT) + self.g['drift']['d']['1']) * m['model']['budget_factor']
            with self.subTest(industry=ident):
                self.assertAlmostEqual(m['model']['B_real_2025'] / base, 1., delta=1e-12)
                self.assertAlmostEqual(m['projected_cycle_real_2025'] / point, 1., delta=1e-12)
                self.assertAlmostEqual(m['cycle_prediction_low'] / (point * math.exp(-half)), 1., delta=1e-12)
                self.assertAlmostEqual(m['cycle_prediction_high'] / (point * math.exp(half)), 1., delta=1e-12)
                self.assertEqual(m['layer'], 'P1')          # still conditioned on the giving already observed
                self.assertGreater(m['cycle_given_observed_low'], m['observed']['amount_real_2025'] - 1.)

    def test_general_giving_model_points_differ_by_exactly_u_squared_times_the_drift_ratio(self):
        """The unconditioned point of a step is B exp(phi dP + d_h)(1 + x/2): phi = log u on both, dP = -1 then +1, so the 2027-28 point over the 2025-26 point from the same base is
        u^2 exp(d_2 - d_1) (1 + x_28/2) / (1 + x_26/2). With the IBIS path at zero (no budget change) that is exactly u^2 exp(d_2 - d_1)."""
        bundle = contract.example('models_cycle')
        for entry in bundle['cycle_model']['budget_rule']['growth'].values():
            entry['annual'] = {year: 0. for year in entry['annual']}
        with tempfile.TemporaryDirectory() as tmp, CycleWorld(tmp, bundle=bundle):
            flat = {i: measures(gf.project(request(i))) for i in (BANKS, CUS, PC)}
        d1, d2 = self.g['drift']['d']['1'], self.g['drift']['d']['2']
        for label, source in (('default budget change', self.by), ('zero growth path', flat)):
            for ident in (BANKS, CUS, PC):
                m26, m28 = source[ident][(2026, GEN)], source[ident][(2028, GEN)]
                point = lambda m: m['model']['B_real_2025'] * math.exp(m['model']['phi'] * m['model']['dP'] + m['model']['drift']) * m['model']['budget_factor']
                with self.subTest(case=label, industry=ident):
                    self.assertAlmostEqual(point(m26) / m26['projected_cycle_real_2025'], 1., delta=1e-12)          # the 2025-26 response IS the formula's point
                    self.assertAlmostEqual(point(m28) / point(m26), GENERAL_UPLIFT ** 2 * math.exp(d2 - d1) * m28['model']['budget_factor'] / m26['model']['budget_factor'], delta=1e-12)
                    if label == 'zero growth path':
                        self.assertEqual((m26['model']['budget_factor'], m28['model']['budget_factor']), (1., 1.))
                        self.assertAlmostEqual(point(m28) / point(m26), GENERAL_UPLIFT ** 2 * math.exp(d2 - d1), delta=1e-12)          # exactly 1.8635^2 x the drift ratio
        self.assertAlmostEqual(GENERAL_UPLIFT ** 2 * math.exp(d2 - d1), 3.31117, delta=5e-6)

    def test_general_giving_2027_28_chains_from_the_conditioned_layer_with_plus_log_u(self):
        """The chained median against the independent scipy reference (giving_cycle_targets.json, recomputed for D-121) and the direction of the move against the fitted-phi figures."""
        for ident in (BANKS, CUS, PC):
            m28 = self.by[ident][(2028, GEN)]
            want = TARGETS['grids']['400']['rows'][ident][GEN]
            with self.subTest(industry=ident):
                self.assertAlmostEqual(m28['projected_cycle_real_2025'] / want['M2028_med'], 1., delta=2e-4)
                self.assertEqual(m28['chained_from'], dict(layer='P1', cycle=2026))
        # banks: $14.47M open cycle (was $13.86M), $21.15M next cycle (was $22.87M)
        self.assertAlmostEqual(self.by[BANKS][(2026, GEN)]['cycle_given_observed_median'] / 1e6, 14.47106, delta=1e-5)
        self.assertAlmostEqual(self.by[BANKS][(2028, GEN)]['projected_cycle_real_2025'] / 1e6, 21.15099, delta=1e-5)

    def test_general_giving_factor_flows_through_and_the_midterm_step_is_symmetric(self):
        with tempfile.TemporaryDirectory() as tmp, CycleWorld(tmp):
            with patch.object(gf, 'GENERAL_UPLIFT_FACTOR', 2.0):
                two = measures(gf.project(request(BANKS)))
        base26, base28 = self.by[BANKS][(2026, GEN)], self.by[BANKS][(2028, GEN)]
        # the unconditioned 2025-26 point scales by u_old / u_new (the midterm step divides by the factor)
        self.assertAlmostEqual(two[(2026, GEN)]['projected_cycle_real_2025'] / base26['projected_cycle_real_2025'], GENERAL_UPLIFT / 2.0, delta=1e-12)
        self.assertEqual(two[(2026, GEN)]['model']['presidential_uplift']['factor'], 2.0)
        self.assertEqual(two[(2028, GEN)]['model']['presidential_uplift']['factor'], 2.0)
        self.assertAlmostEqual(two[(2028, GEN)]['model']['phi'], math.log(2.0), delta=1e-15)

    def test_the_rollback_bundle_has_no_factor_at_all(self):
        with tempfile.TemporaryDirectory() as tmp, CycleWorld(tmp, bundle=contract.example('models')):
            cat = gf.catalog('2026-10-07')
            out = gf.project(dict(industry=BANKS, as_of='2026-10-07', horizons={'2026': {'growth_pct': 7.0}, '2028': {'growth_pct': 7.0}}))
        self.assertNotIn('cycle_model_view', cat)
        self.assertNotIn('presidential_uplift', json.dumps(out))


class NominalDollarTests(unittest.TestCase):
    """D-092 (Paul, 2026-10-09, "Confirm"): projections are shown in nominal dollars by default. Every cycle-model measure carries an explicit nominal twin of its 2025-dollar amounts
    (`nominal`), so the page does no inflation arithmetic. The factor is the inverse of the multiplier the model deflates the cycle with: the expected inflation of 2.5726436% a year at the
    cycle mid-point (2025-26 x1.01286, 2027-28 x1.06565), or the snapshot's CPI of the cycle once it has one. The anchor cycle's level and the observed amount are Mentat's own nominal values."""

    RATE = 0.025726436

    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as tmp, CycleWorld(tmp) as world:
            cls.bundle = world.bundle
            cls.out = {i: gf.project(request(i)) for i in (BANKS, CUS, PC)}
        cls.by = {i: measures(out) for i, out in cls.out.items()}

    @staticmethod
    def factor(cycle, rate=RATE):
        """Mean price level of the cycle's two years against 2025 (= 1), worked here from the rate."""
        level = lambda y: (1 + rate) ** max(y - 2025, 0)
        return (level(cycle - 1) + level(cycle)) / 2

    def test_the_factors_are_the_mid_cycle_expected_inflation_of_d092(self):
        self.assertEqual(self.bundle['cycle_model']['inflation']['annual_rate'], self.RATE)
        self.assertAlmostEqual(self.factor(2026), 1.01286, places=5)
        self.assertAlmostEqual(self.factor(2028), 1.06565, places=5)
        for ident, by in self.by.items():
            for (cycle, mid), m in by.items():
                if m['status'] == 'ready':
                    with self.subTest(ident=ident, cycle=cycle, measure=mid):
                        self.assertAlmostEqual(m['nominal']['factor'], self.factor(cycle), delta=1e-12)

    def test_nominal_is_the_2025_dollar_amount_times_the_factor_for_every_amount_of_every_measure_and_horizon(self):
        seen = set()
        for ident, by in self.by.items():
            for (cycle, mid), m in by.items():
                if m['status'] != 'ready':
                    continue
                for real, nominal in gf.NOMINAL_FIELDS:
                    if m.get(real) is None:
                        self.assertNotIn(nominal, m['nominal'], (ident, cycle, mid, real))
                        continue
                    seen.add((cycle, nominal))
                    if nominal == 'anchor_cycle':
                        continue          # the anchors' own nominal level, checked below
                    self.assertAlmostEqual(m['nominal'][nominal] / (m[real] * self.factor(cycle)), 1., delta=1e-12, msg=(ident, cycle, mid, real))
        for want in ((2026, 'projected_cycle'), (2026, 'cycle_given_observed_median'), (2026, 'remaining_given_observed_median'), (2028, 'projected_cycle'), (2028, 'cycle_prediction_low'), (2028, 'cycle_prediction_high')):
            self.assertIn(want, seen)
        # one value per horizon, by hand: banks' general giving
        m26, m28 = self.by[BANKS][(2026, GEN)], self.by[BANKS][(2028, GEN)]
        self.assertAlmostEqual(m26['nominal']['cycle_given_observed_median'], m26['cycle_given_observed_median'] * 1.012863218, delta=1e-6)
        self.assertAlmostEqual(m28['nominal']['projected_cycle'], m28['projected_cycle_real_2025'] * 1.0656483025330452, delta=1e-6)
        self.assertAlmostEqual(m28['nominal']['projected_cycle'] / m28['projected_cycle_real_2025'], 1.06565, places=5)

    def test_the_2023_24_bar_and_the_observed_amount_are_the_anchors_own_nominal_values_and_the_stack_adds_up_in_nominal_dollars(self):
        for ident, by in self.by.items():
            for (cycle, mid), m in by.items():
                if m['status'] != 'ready':
                    continue
                n = m['nominal']
                if mid == GEN:
                    self.assertEqual(n['anchor_cycle'], ANCHORS['industries'][ident]['general_giving']['cycles']['2024'], (ident, cycle))          # the anchors' number, not real / CPI
                self.assertEqual(n['anchor_cycle'], m['anchor_cycle_nominal'])
                if cycle == 2026:
                    self.assertEqual(n['observed'], m['observed']['amount_nominal'], (ident, mid))          # not the 2025-dollar amount converted back
                    self.assertAlmostEqual(n['observed'] + n['remaining_given_observed_median'], n['cycle_given_observed_median'], delta=1e-6, msg=(ident, mid))
                else:
                    self.assertNotIn('observed', n)

    def test_a_cpi_for_the_open_cycle_replaces_the_expected_inflation_factor_for_that_cycle_only(self):
        anchors = copy.deepcopy(ANCHORS)
        anchors['cpi']['multipliers']['2026'] = 0.97
        with tempfile.TemporaryDirectory() as tmp, CycleWorld(tmp, anchors=anchors):
            by = measures(gf.project(request(BANKS)))
        self.assertAlmostEqual(by[(2026, GEN)]['nominal']['factor'], 1 / 0.97, delta=1e-12)
        self.assertAlmostEqual(by[(2028, GEN)]['nominal']['factor'], self.factor(2028), delta=1e-12)
        self.assertEqual(by[(2026, GEN)]['nominal']['observed'], by[(2026, GEN)]['observed']['amount_nominal'])

    def test_the_nominal_block_says_where_the_factor_comes_from(self):
        text = self.by[BANKS][(2028, GEN)]['nominal']['source']
        for part in ('x1.06565', '2.5726436%', 'D-092', 'cycle mid-point'):
            self.assertIn(part, text)

    def test_an_unavailable_measure_has_no_nominal_block_and_a_bundle_without_a_cycle_model_has_none_at_all(self):
        self.assertEqual(self.by[PC][(2026, PAC)]['status'], 'unavailable')
        self.assertNotIn('nominal', self.by[PC][(2026, PAC)])
        with tempfile.TemporaryDirectory() as tmp, CycleWorld(tmp, bundle=contract.example('models')):
            out = gf.project(dict(industry=BANKS, as_of='2026-10-07', horizons={'2026': {'growth_pct': 7.0}, '2028': {'growth_pct': 7.0}}))
        self.assertTrue(all('nominal' not in m for h in out['horizons'] for m in h['pac']['measures']))


class AnchorAndDisplayTests(unittest.TestCase):
    def test_a_snapshot_without_the_odd_year_split_makes_the_measure_unavailable_with_a_reason(self):
        anchors = copy.deepcopy(ANCHORS)
        del anchors['industries'][BANKS]['general_giving']['odd_year']
        with tempfile.TemporaryDirectory() as tmp, CycleWorld(tmp, anchors=anchors):
            got = measures(gf.project(request(BANKS)))
            view = gf.catalog('2026-10-07')['cycle_model_view']['growth'][BANKS]['2026']['budget_defaults']
        self.assertEqual(got[(2026, GEN)]['status'], 'unavailable')
        self.assertIn('odd-year split', got[(2026, GEN)]['reason'])
        self.assertEqual(got[(2026, PAC)]['status'], 'ready')
        self.assertEqual(view[GEN]['status'], 'unavailable')

    def test_open_cycle_amounts_are_deflated_at_the_expected_inflation_not_at_face_value(self):
        with tempfile.TemporaryDirectory() as tmp, CycleWorld(tmp) as world:
            out = gf.project(request(BANKS))
        rate = world.bundle['cycle_model']['inflation']['annual_rate']
        factor = lambda c: ((1 + rate) ** max(c - 1 - 2025, 0) + (1 + rate) ** max(c - 2025, 0)) / 2      # mean price level of the cycle's two years, 2025 = 1
        self.assertAlmostEqual(factor(2026), 1.0128632180, delta=1e-9)
        self.assertAlmostEqual(factor(2028), 1.0656483025, delta=1e-9)       # SPEC 1.2 prints 1.0656548; the formula (and the spec's own target script) gives 1.0656483
        a = out['anchors']
        self.assertAlmostEqual(a['cpi_multiplier_current_cycle'], 1 / factor(2026), delta=1e-12)
        self.assertFalse(a['face_value_current_cycle'])
        self.assertTrue(all(not m['observed']['face_value'] for m in measures(out).values() if m.get('observed')))
        text = ' '.join(a['flags'])
        self.assertNotIn('face value', text)
        self.assertIn('deflated at the expected 2.5726436%/yr', text)
        seen = measures(out)[(2026, GEN)]['observed']
        self.assertAlmostEqual(seen['amount_real_2025'], seen['amount_nominal'] / factor(2026), delta=1e-6)

    def test_an_actual_cpi_for_the_open_cycle_is_used_when_the_snapshot_has_one(self):
        anchors = copy.deepcopy(ANCHORS)
        anchors['cpi']['multipliers']['2026'] = 0.97
        with tempfile.TemporaryDirectory() as tmp, CycleWorld(tmp, anchors=anchors):
            out = gf.project(request(BANKS))
        self.assertEqual(out['anchors']['cpi_multiplier_current_cycle'], 0.97)

    def test_the_senate_share_model_must_carry_the_presidential_cycle_term(self):
        bundle = contract.example('models_cycle')
        senate = bundle['share_models']['control_Senate']
        keep = [i for i, t in enumerate(senate['terms']) if t != 'Pres']
        senate['terms'] = [senate['terms'][i] for i in keep]
        senate['beta'] = [senate['beta'][i] for i in keep]
        senate['covariance'] = [[senate['covariance'][i][j] for j in keep] for i in keep]
        with tempfile.TemporaryDirectory() as tmp, CycleWorld(tmp, bundle=bundle):
            with self.assertRaisesRegex(ValueError, 'contract'):
                gf.models()

    def test_the_catalog_carries_the_defaults_the_window_the_source_and_the_methods_notes(self):
        with tempfile.TemporaryDirectory() as tmp, CycleWorld(tmp) as world:
            cat = gf.catalog('2026-10-07')
        view = cat['cycle_model_view']
        banks = view['growth'][BANKS]
        self.assertEqual((banks['2026']['growth_window'], banks['2028']['growth_window']), ([2023, 2025], [2025, 2027]))
        g = world.bundle['cycle_model']['budget_rule']['growth'][BANKS]['annual']
        want = 100 * (((1 + g['2023']) * (1 + g['2024']) * (1 + g['2025'])) ** (1 / 3) - 1)
        self.assertEqual(banks['2026']['growth_default'], round(want, 3))
        self.assertEqual((banks['2026']['report_id'], banks['2026']['edition']), ('52211', '2026-08'))
        self.assertIn('IBISWorld 52211 2026-08', banks['2026']['growth_source'])
        self.assertEqual(view['growth'][PC]['2026']['edition'], '2024-11')
        self.assertEqual(banks['2026']['budget_defaults'][PAC]['budget_growth_pct'], 0.)          # D-116: the SPEC's 23.6637 is not applied to company PACs
        self.assertAlmostEqual(banks['2026']['budget_defaults'][GEN]['budget_growth_pct'], 12.8898, delta=1e-3)
        self.assertEqual(sorted(view['g_prior_defaults']), [BANKS, CUS, PC])
        notes = ' '.join(view['notes'])
        for needle in ('No backtest', 'inferred as constant dollars', '50/50 rule is untested on general giving', 'phi_gen = 0.738'):
            self.assertIn(needle, notes)
        self.assertNotIn('0.748', notes)

    def test_the_share_models_take_the_ibis_cycle_growth_as_the_prior_growth_of_2028(self):
        with tempfile.TemporaryDirectory() as tmp, CycleWorld(tmp) as world:
            outlook = world.bundle['saved_outlook']['control']
            req = request(BANKS)
            for h in req['horizons'].values():
                h.update(current_control={'House': 1, 'Senate': 1}, control_scores={'House': outlook['House']['value'], 'Senate': outlook['Senate']['value']})
            out = gf.project(req)
        history = next(r for r in world.bundle['industries'][BANKS]['history'] if r['cycle'] == 2024)
        self.assertEqual(out['horizons'][0]['prior_growth_pct'], history['growth_pct'])
        self.assertEqual(out['horizons'][1]['prior_growth_pct'], world.bundle['cycle_model']['g_prior'][BANKS]['2028'])
        self.assertEqual(out['horizons'][1]['shares']['Senate']['status'], 'ready')
        self.assertEqual(out['cycle_model']['version'], 1)


class RollbackTests(unittest.TestCase):
    """The new calculator serves the previously published bundle (no cycle_model) exactly as the calculator it replaces did: a pointer rollback needs no code rollback."""

    @unittest.skipUnless(OLD_VERSION.is_file(), 'the previously published version (7bc7bcf1...) is not in this tree')
    def test_the_previous_published_bundle_loads_and_every_response_leaf_is_unchanged(self):
        golden = json.loads((FIXTURES / 'giving_forecast_rollback_golden.json').read_text(encoding='utf-8'))
        with tempfile.TemporaryDirectory() as tmp, CycleWorld(tmp, models_path=OLD_VERSION):
            bundle = gf.models()
            self.assertEqual((bundle['model_version'], bundle['sha256']), (golden['model_version'], golden['bundle_sha256']))
            self.assertNotIn('cycle_model', bundle)
            outlook = bundle['saved_outlook']['control']
            runs = {'catalog': gf.catalog('2026-10-07')}
            for ident, (g26, g28) in {BANKS: (-1.550, -1.302), CUS: (10.294, 1.252), PC: (7.035, 7.562)}.items():
                horizons = {str(c): dict(growth_pct=g, current_control={'House': outlook['House']['current_control'], 'Senate': outlook['Senate']['current_control']},
                                         control_scores={'House': outlook['House']['value'], 'Senate': outlook['Senate']['value']},
                                         annual_forecast_changes={'2026': 0, '2027': 0, '2028': 0}) for c, g in ((2026, g26), (2028, g28))}
                runs[ident] = gf.project(dict(industry=ident, as_of='2026-10-07', horizons=horizons))
        runs = json.loads(json.dumps(runs))

        def compare(actual, expected, path='$'):
            if isinstance(expected, dict):
                self.assertEqual(actual.keys(), expected.keys(), path)
                for key in expected:
                    compare(actual[key], expected[key], path + '.' + key)
            elif isinstance(expected, list):
                self.assertEqual(len(actual), len(expected), path)
                for index, (value, reference) in enumerate(zip(actual, expected)):
                    compare(value, reference, path + '[' + str(index) + ']')
            elif type(expected) in (int, float) and type(actual) in (int, float):
                self.assertTrue(math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-9), f'{path}: {actual!r} != {expected!r}')
            else:
                self.assertEqual(actual, expected, path)
        compare(runs, golden['runs'])
        for run_ in runs.values():
            self.assertNotIn('cycle_model', run_)
            self.assertNotIn('cycle_model_view', run_)

    def test_the_contract_example_without_a_cycle_model_takes_the_old_path_and_adds_no_key(self):
        with tempfile.TemporaryDirectory() as tmp, CycleWorld(tmp, bundle=contract.example('models')):
            cat = gf.catalog('2026-10-07')
            out = gf.project(dict(industry=BANKS, as_of='2026-10-07', horizons={'2026': {'growth_pct': 7.0}, '2028': {'growth_pct': 7.0}}))
        self.assertNotIn('cycle_model_view', cat)
        self.assertNotIn('cycle_model', out)
        self.assertNotIn('cpi_multiplier_current_cycle', out['anchors'])
        self.assertTrue(out['anchors']['face_value_current_cycle'])
        self.assertEqual(out['horizons'][0]['pac']['slope']['rule'] in gf.SLOPE_LABELS, True)


class StdlibNumericsTests(unittest.TestCase):
    def test_the_fast_t_quantile_agrees_with_the_bisection_one(self):
        for df in (1, 3, 5, 15, 23, 200):
            for u in (1e-6, 1e-3, .0125, .1, .5, .8, .975, .9999):
                with self.subTest(df=df, u=u):
                    self.assertAlmostEqual(gf._t_quantile_upper(u, df), gf._t_upper_inverse(u, df), delta=1e-8 * max(1., abs(gf._t_upper_inverse(u, df))))

    def test_the_midpoint_grid_is_symmetric_and_matches_the_quantiles(self):
        grid = gf._t_grid(5, 400)
        self.assertEqual(len(grid), 400)
        self.assertEqual([round(a + b, 12) for a, b in zip(grid, reversed(grid))], [0.] * 400)
        self.assertAlmostEqual(grid[0], -gf._t_upper_inverse(.5 / 400, 5), delta=1e-8)
        self.assertEqual(sorted(grid), list(grid))

    def test_linear_interpolation_quantile(self):
        self.assertEqual(gf._quantile([1., 2., 3., 4.], .5), 2.5)
        self.assertEqual(gf._quantile([1., 2., 3., 4.], 0.), 1.)
        self.assertAlmostEqual(gf._quantile([1., 2., 3., 4.], .975), 3.925, delta=1e-12)

    def test_a_chain_with_no_noise_recovers_the_conditioned_layer_quantiles(self):
        layer = gf._layer_dist(math.log(100.), .2, 5, 90.)
        low, median, high = gf._chain(layer, 0., 1e-9, 5)
        reference = gf._given_observed(100., gf._t_crit(5) * .2, gf._t_crit(5), 5, 90.)
        self.assertAlmostEqual(median / reference['cycle_given_observed_median'], 1., delta=2e-3)
        self.assertGreater(low, 90.)


if __name__ == '__main__':
    unittest.main()

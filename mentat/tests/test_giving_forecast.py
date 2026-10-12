"""Numerical/accounting invariants for dated research-industry scenarios."""
import copy
from datetime import date
import json
import math
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import giving_forecast as gf


class Today(date):
    @classmethod
    def today(cls):
        return cls(2026, 9, 27)


def scenario(sector='credit_unions', signal='control', varying=False):
    b = gf.models(); industry = b['industries'][sector]
    request = dict(industry=sector, as_of='2026-09-27', signal=signal, industry_slopes=varying, horizons={})
    for cycle in (2026, 2028):
        m = b['share_models'][signal + '_House' + ('_industry' if varying else '')]
        reference = gf._reference(m['history'], cycle, sector)
        a = dict(growth_pct=reference['growth_pct'], prior_growth_pct=reference['prior_growth_pct'],
                 current_control={}, control_scores={}, seat_gains={})
        for chamber in gf.CHAMBERS:
            m = b['share_models'][signal + '_' + chamber + ('_industry' if varying else '')]
            r = gf._reference(m['history'], cycle, sector)
            a['current_control'][chamber] = r['current_control']
            a['control_scores' if signal == 'control' else 'seat_gains'][chamber] = r['forecast'] * (100 if signal == 'control' else 1)
        request['horizons'][str(cycle)] = a
    return request


class GivingForecastTests(unittest.TestCase):
    def setUp(self):
        self.clock = patch.object(gf, 'date', Today); self.clock.start(); self.addCleanup(self.clock.stop)
        # These are the schema-1 invariants: pin the v1 bundle so they keep running once a schema-2 bundle is published beside it.
        self.pin = patch.dict(os.environ, {'MENTAT_GIVING_MODELS': str(gf.MODEL_FILE)}); self.pin.start(); self.addCleanup(self.pin.stop)

    def test_frozen_models_reproduce_reviewed_ordinary_coefficients(self):
        b = gf.models()
        self.assertAlmostEqual(b['pac_model']['beta'], -.16835651109143, places=12)
        self.assertAlmostEqual(b['pac_model']['ordinary_p'], .000594628455641254, places=12)
        self.assertAlmostEqual(b['lobby_model']['beta_million'], -.5367134383271494, places=12)
        m = b['share_models']['control_Senate']; j = m['terms'].index('Forecast')
        self.assertAlmostEqual(m['beta'][j] * 10, -4.2498279134, places=8)
        self.assertAlmostEqual(m['ordinary_p']['Forecast'], .000721705, places=8)

    def test_missing_is_not_zero_and_both_horizons_are_always_returned(self):
        out = gf.project({'industry':'credit_unions'})
        self.assertEqual([r['cycle'] for r in out['horizons']], [2026, 2028])
        self.assertEqual(out['horizons'][0]['starts'], '2026-09-28')
        self.assertEqual(out['horizons'][1]['starts'], '2027-01-01')
        self.assertTrue(all(r['pac']['projected_cycle_real_2025'] is None for r in out['horizons']))
        self.assertEqual(len(out['horizons'][1]['lobbying']), 2)
        self.assertIsNone(out['horizons'][0]['lobbying'][0]['amount_real_2025'])

    def test_reference_inputs_reproduce_baseline_share_in_every_specification(self):
        for sector in gf.models()['industries']:
            for signal in ('control', 'seats'):
                for varying in (False, True):
                    out = gf.project(scenario(sector, signal, varying))
                    for h in out['horizons']:
                        for s in h['shares'].values():
                            self.assertEqual(s['status'], 'ready')
                            self.assertAlmostEqual(s['projected_cycle_R_share'], s['baseline_share'])
                            self.assertAlmostEqual(s['cycle_coefficient_low'], s['baseline_share'])
                            self.assertAlmostEqual(s['political_change_pp'], 0)

    def test_one_sd_less_growth_increases_pac_18_percent(self):
        for sector, industry in gf.models()['industries'].items():
            r = scenario(sector); ref = gf._reference(industry['history'], 2026)
            r['horizons']['2026']['growth_pct'] = ref['growth_pct'] - industry['growth_sd']
            pac = gf.project(r)['horizons'][0]['pac']
            self.assertAlmostEqual(pac['change_pct'], 18.33584156, places=5)
            self.assertGreater(pac['cycle_coefficient_low'], pac['baseline_real_2025'])
            self.assertIsNone(pac['projected_remaining_real_2025'])

    def test_senate_negative_political_effect_is_not_relabelled_as_power_following(self):
        r = scenario(); a = r['horizons']['2026']; a['control_scores']['Senate'] += 10
        s = gf.project(r)['horizons'][0]['shares']['Senate']
        self.assertAlmostEqual(s['political_change_pp'], -4.2498279134, places=8)
        self.assertLess(s['political_high_pp'], 0)
        self.assertEqual([t['threshold_pp'] for t in s['materiality']], [1,3,5])
        self.assertEqual(s['expected_advantaged_party'], 'R')
        self.assertTrue(all(not t['point_meets'] for t in s['materiality']))

    def test_same_raw_growth_shock_adjusts_pac_differently_for_each_industry(self):
        # Independently refitted raw revenue/spending panel, not bundle-derived expectations.
        expected = {'commercial_banks': .84632458676881,
                    'credit_unions': 1.128354614983653,
                    'auto_light_duty_manufacturing': 1.221834005589938}
        for sector, change in expected.items():
            r = scenario(sector)
            ref = gf._reference(gf.models()['industries'][sector]['history'], 2026)
            r['horizons']['2026']['growth_pct'] = ref['growth_pct'] - 1
            self.assertAlmostEqual(gf.project(r)['horizons'][0]['pac']['change_pct'], change, places=10)

    def test_prior_growth_defaults_use_own_industry_then_current_scenario(self):
        actual_priors = []
        for sector in gf.models()['industries']:
            r = scenario(sector)
            for a in r['horizons'].values(): a.pop('prior_growth_pct')
            r['horizons']['2026']['growth_pct'] = 7.5
            out = gf.project(r)
            prior = next(x['growth_pct'] for x in gf.models()['industries'][sector]['history'] if x['cycle']==2024)
            self.assertEqual(out['horizons'][0]['prior_growth_pct'], prior)
            self.assertEqual(out['horizons'][1]['prior_growth_pct'], 7.5)
            actual_priors.append(prior)
            r['horizons']['2028']['prior_growth_pct'] = 4.25
            self.assertEqual(gf.project(r)['horizons'][1]['prior_growth_pct'], 4.25)
        self.assertEqual(len(set(actual_priors)), 3)

    def test_industry_slopes_and_ordinary_p_match_reviewed_industry_results(self):
        b = gf.models()
        for row in b['industry_slopes']:
            r = scenario(row['sector'], row['signal'], True)
            a = r['horizons']['2026']; delta = -10 if row['signal']=='control' or row['chamber']=='House' else -1
            key = 'control_scores' if row['signal']=='control' else 'seat_gains'
            a[key][row['chamber']] += delta
            s = gf.project(r)['horizons'][0]['shares'][row['chamber']]
            self.assertAlmostEqual(s['political_change_pp'], -row['effect_pp'], places=10)
            self.assertEqual(s['ordinary_p'], row['ordinary_p'])

    def test_observed_pac_is_subtracted_only_with_same_cutoff_and_source(self):
        r = scenario(); a = r['horizons']['2026']
        for observed in ({'amount_real_2025':0}, {'amount_real_2025':0,'source':'fixture','through':'2026-09-26'}):
            a['observed_pac'] = observed
            self.assertIsNone(gf.project(r)['horizons'][0]['pac']['projected_remaining_real_2025'])
        a['observed_pac'] = {'amount_real_2025':1000,'source':'fixture','through':'2026-09-27'}
        p = gf.project(r)['horizons'][0]['pac']
        self.assertEqual(p['projected_remaining_real_2025'], p['projected_cycle_real_2025']-1000)

    def test_overrun_is_a_conflict_not_a_zero_forecast(self):
        r=scenario();r['horizons']['2026']['observed_pac']={'amount_real_2025':1e10,'source':'fixture','through':'2026-09-27'}
        p=gf.project(r)['horizons'][0]['pac']
        self.assertEqual(p['remainder_status'],'baseline_exceeded')
        self.assertIsNone(p['projected_remaining_real_2025'])

    def test_candidate_allocations_reconcile_and_never_use_pac_totals(self):
        r=scenario();a=r['horizons']['2026'];a['candidate_budgets']={'House':dict(cycle_total_real_2025=1e7,observed_R_real_2025=1e5,observed_D_real_2025=2e5,source='fixture',through='2026-09-27')}
        h=gf.project(r)['horizons'][0];s=h['shares']['House']
        self.assertAlmostEqual(s['projected_cycle_R_real_2025']+s['projected_cycle_D_real_2025'],1e7)
        self.assertAlmostEqual(s['projected_remaining_R_real_2025']+s['projected_remaining_D_real_2025'],9.7e6)
        self.assertAlmostEqual(s['projected_remaining_R_share'],s['projected_remaining_R_real_2025']/9.7e6)
        self.assertNotIn('projected_cycle_R_real_2025',h['shares']['Senate'])
        a['observed_pac']={'amount_real_2025':123,'through':'2026-09-27','source':'fixture'}
        self.assertEqual(gf.project(r)['horizons'][0]['shares']['House'],s)

    def test_incompatible_candidate_budget_has_no_remaining_allocation(self):
        r=scenario();r['horizons']['2026']['candidate_budgets']={'House':dict(cycle_total_real_2025=1e3,observed_R_real_2025=1e6,observed_D_real_2025=0,source='fixture',through='2026-09-27')}
        s=gf.project(r)['horizons'][0]['shares']['House']
        self.assertEqual(s['remainder_status'],'budget_inconsistent')
        self.assertIsNone(s['projected_remaining_R_share'])

    def test_lobbying_is_annual_sensitivity_and_zero_is_valid(self):
        r=scenario();r['horizons']['2026']['annual_forecast_changes']={'2026':-1}
        r['horizons']['2028']['annual_forecast_changes']={'2027':0,'2028':1}
        out=gf.project(r)
        self.assertAlmostEqual(out['horizons'][0]['lobbying'][0]['amount_real_2025'],536713.4383271494)
        self.assertEqual(out['horizons'][1]['lobbying'][0]['amount_real_2025'],0)
        self.assertLess(out['horizons'][1]['lobbying'][1]['amount_real_2025'],0)
        self.assertNotIn('projected_remaining_real_2025',out['horizons'][0]['lobbying'][0])

    def test_historical_and_future_information_dates_are_rejected(self):
        for extra in ({'as_of':'2026-09-26'},{'as_of':'2026-09-28'},{'as_of':'bad'},{'assumption_date':'2026-09-28'}):
            with self.subTest(extra=extra),self.assertRaises(ValueError):gf.project(scenario()|extra)

    def test_report_scopes_and_invalid_numbers_are_not_silently_accepted(self):
        for sector in ('Banking','Community Banks',None,{}):
            with self.subTest(sector=sector),self.assertRaises(ValueError):gf.project({'industry':sector})
        for invalid in (float('nan'),float('inf'),True,{},501):
            r=scenario();r['horizons']['2026']['growth_pct']=invalid
            with self.subTest(invalid=invalid),self.assertRaises(ValueError):gf.project(r)

    def test_seat_scores_keep_original_units_and_model_choice(self):
        r=scenario(signal='seats');a=r['horizons']['2026'];a['seat_gains']['Senate']+=1
        s=gf.project(r)['horizons'][0]['shares']['Senate']
        self.assertAlmostEqual(s['political_change_pp'],.523724,places=5)
        self.assertIn('not retained',s['specification'])

    def test_inputs_are_not_mutated_and_outputs_are_strict_json(self):
        r=scenario();before=copy.deepcopy(r);out=gf.project(r)
        self.assertEqual(r,before);json.dumps(out,allow_nan=False)
        self.assertEqual(len(out['model_sha256']),64)

    def test_year_end_cutoff_has_no_reversed_remaining_date_range(self):
        class YearEnd(date):
            @classmethod
            def today(cls):return cls(2026,12,31)
        r=scenario();r['as_of']='2026-12-31'
        with patch.object(gf,'date',YearEnd):out=gf.project(r)
        h=out['horizons'][0]
        self.assertIsNone(h['starts'])
        self.assertEqual(h['pac']['remainder_status'],'cycle_closed')
        self.assertEqual(h['shares']['House']['remainder_status'],'cycle_closed')

    def test_share_flags_extrapolation_and_intervals_outside_0_1_without_clipping(self):
        r=scenario('credit_unions','seats');a=r['horizons']['2026']
        a.update(growth_pct=5,current_control={'House':1,'Senate':1},seat_gains={'House':-435,'Senate':-100});a.pop('prior_growth_pct')
        house,senate=[gf.project(r)['horizons'][0]['shares'][c] for c in gf.CHAMBERS]
        self.assertEqual((house['status'],senate['status']),('ready','ready'))
        self.assertAlmostEqual(house['projected_cycle_R_share'],.469,places=3)
        self.assertAlmostEqual(house['cycle_coefficient_low'],-.028,places=3)
        self.assertAlmostEqual(house['cycle_coefficient_high'],.966,places=3)
        self.assertAlmostEqual(senate['projected_cycle_R_share'],.018,places=3)
        self.assertAlmostEqual(senate['cycle_coefficient_low'],-1.98,places=2)
        self.assertAlmostEqual(senate['cycle_coefficient_high'],2.02,places=2)
        for s in (house,senate):
            self.assertIs(s['extrapolation'],True);self.assertIs(s['interval_outside_share_range'],True)

    def test_share_flags_are_off_for_reference_inputs_in_every_specification(self):
        for sector in gf.models()['industries']:
            for signal in ('control','seats'):
                for varying in (False,True):
                    for h in gf.project(scenario(sector,signal,varying))['horizons']:
                        for s in h['shares'].values():
                            with self.subTest(sector=sector,signal=signal,varying=varying,chamber=s['chamber']):
                                self.assertIs(s['extrapolation'],False);self.assertIs(s['interval_outside_share_range'],False)

    def test_share_extrapolation_follows_each_model_input_and_its_training_range(self):
        b=gf.models()
        def flag(signal,varying,chamber,**change):
            r=scenario('credit_unions',signal,varying);a=r['horizons']['2026']
            for k,v in change.items():
                if k=='political':a['control_scores' if signal=='control' else 'seat_gains'][chamber]=v
                else:a[k]=v
            return gf.project(r)['horizons'][0]['shares'][chamber]['extrapolation']
        # Political input: the training range of the model's own expectation term, inclusive at both ends.
        seats=[x['forecast'] for x in b['share_models']['seats_Senate']['history']]
        self.assertFalse(flag('seats',False,'Senate',political=max(seats)));self.assertTrue(flag('seats',False,'Senate',political=max(seats)+1))
        self.assertFalse(flag('seats',False,'Senate',political=min(seats)));self.assertTrue(flag('seats',False,'Senate',political=min(seats)-1))
        # Control scores are entered on 0-100 and modelled on 0-1: a re-entered training score is not extrapolation.
        control=[x['forecast'] for x in b['share_models']['control_Senate']['history']]
        self.assertFalse(flag('control',False,'Senate',political=min(control)*100));self.assertTrue(flag('control',False,'Senate',political=min(control)*100-1))
        self.assertFalse(flag('control',False,'Senate',political=max(control)*100));self.assertTrue(flag('control',False,'Senate',political=max(control)*100+1))
        # Prior-cycle growth: this industry's training rows in the model.
        prior=[x['prior_growth_pct'] for x in b['share_models']['control_House']['history'] if x['industry']=='credit_unions']
        self.assertFalse(flag('control',False,'House',prior_growth_pct=max(prior)));self.assertTrue(flag('control',False,'House',prior_growth_pct=max(prior)+.01))
        self.assertFalse(flag('control',False,'House',prior_growth_pct=min(prior)));self.assertTrue(flag('control',False,'House',prior_growth_pct=min(prior)-.01))
        # Current-cycle growth only matters to models that contain it.
        self.assertFalse(flag('control',False,'House',growth_pct=500))
        self.assertTrue(flag('control',True,'House',growth_pct=500));self.assertTrue(flag('seats',False,'House',growth_pct=500))
        self.assertFalse(flag('seats',False,'House',growth_pct=5))

    def test_huge_integers_are_invalid_numbers_not_crashes(self):
        edits=(lambda a:a.update(growth_pct=10**400),lambda a:a.update(growth_pct=-10**400),lambda a:a.update(prior_growth_pct=10**400),
               lambda a:a['control_scores'].update(House=10**400),lambda a:a.update(annual_forecast_changes={'2026':10**400}),
               lambda a:a.update(observed_pac={'amount_real_2025':10**400,'source':'fixture','through':'2026-09-27'}))
        for index,edit in enumerate(edits):
            r=scenario();edit(r['horizons']['2026'])
            with self.subTest(edit=index),self.assertRaises(ValueError):gf.project(r)
        with self.assertRaises(ValueError):gf._number(10**400,'Value')

    def test_non_finite_numbers_anywhere_in_the_request_are_rejected_not_echoed(self):
        for bad in (float('nan'),float('inf'),-float('inf')):
            for place in ('top','horizon','nested'):
                r=scenario()
                if place=='top':r['note']=bad
                elif place=='horizon':r['horizons']['2026']['note']=bad
                else:r['horizons']['2026']['candidate_budgets']={'House':{'extra':[1,{'x':bad}]}}
                with self.subTest(bad=bad,place=place),self.assertRaises(ValueError):gf.project(r)
        r=scenario();out=gf.project(r)
        self.assertEqual(out['inputs'],r);json.dumps(out,allow_nan=False)

    def test_zero_lobbying_change_is_positive_zero_not_negative_zero(self):
        for value in (0,-0.0,'0'):
            r=scenario();r['horizons']['2026']['annual_forecast_changes']={'2026':value}
            row=gf.project(r)['horizons'][0]['lobbying'][0]
            for key in ('amount_real_2025','coefficient_low','coefficient_high'):
                with self.subTest(value=value,key=key):
                    self.assertEqual(row[key],0);self.assertEqual(math.copysign(1,row[key]),1)

    def test_prior_growth_is_echoed_as_the_validated_number_in_both_horizons(self):
        r=scenario();a26,a28=r['horizons']['2026'],r['horizons']['2028']
        a26.pop('prior_growth_pct');a28.pop('prior_growth_pct');a26['growth_pct']='7.5'   # the console sends form values as text
        out=gf.project(r)
        for h,expected in zip(out['horizons'],(gf.models()['industries']['credit_unions']['history'][-1]['growth_pct'],7.5)):
            self.assertEqual(h['prior_growth_pct'],expected);self.assertIsInstance(h['prior_growth_pct'],float)
        a28['prior_growth_pct']='4.25'
        self.assertEqual(gf.project(r)['horizons'][1]['prior_growth_pct'],4.25);self.assertIsInstance(gf.project(r)['horizons'][1]['prior_growth_pct'],float)
        a26['growth_pct']='';a28['prior_growth_pct']=''
        self.assertIsNone(gf.project(r)['horizons'][1]['prior_growth_pct'])


if __name__=='__main__':
    unittest.main()

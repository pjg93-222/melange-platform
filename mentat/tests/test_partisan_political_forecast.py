"""Political transfers: conservation, observed floor, missingness and pins."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import partisan_political_forecast as p


def model(variant='both'):
    features = p.VARIANT_FEATURES[variant]
    return dict(features=list(features), means=[.5, 0][:len(features)],
                scales=[.25, .5][:len(features)], coefficients=[.1, -.05][:len(features)],
                ridge_lambda=1, training_cycles=[2018, 2020, 2022, 2024],
                feature_definition=p.FEATURE_DEFINITION)


def row():
    return dict(sector='commercial_banks', chamber='House', target=2026, as_of='2026-10-06',
                baseline={'D': {'lo_cents': 400, 'hi_cents': 500}, 'R': {'lo_cents': 600, 'hi_cents': 750}},
                remaining={'D': {'lo_cents': 100, 'hi_cents': 150}, 'R': {'lo_cents': 200, 'hi_cents': 300}},
                prior_scale_cents=1000, features={'target_r_share': .75, 'champion_mean_dw': .2},
                feature_provenance={'feature_definition': p.FEATURE_DEFINITION, 'vintage': 'reconstructed_revised'})


def bundle():
    return dict(schema_version=1, model_id=p.MODEL_ID, currency='USD', accounting_basis=p.ACCOUNTING_BASIS,
                active_variant='both', models={v: model(v) for v in p.VARIANT_FEATURES}, current_rows=[row()])


def profile():
    return dict(industry='commercial_banks', chamber='House', cycle='2026', as_of_date='2026-10-06',
                actual_current_champion_mean_dw_nominate_dim1='.6', actual_current_champion_n='25',
                actual_current_champion_score_n='25', actual_champion_status='available_current_board',
                actual_champion_definition=p.CURRENT_CHAMPION_DEFINITION, attention_republican_share=.01)


class PoliticalTransferTest(unittest.TestCase):
    def test_manual_arithmetic_conserves_both_endpoints_and_input(self):
        r, m = row(), model()
        before = copy.deepcopy((r, m))
        out = p.project(r, m)
        self.assertAlmostEqual(out['raw_delta_cents'], 80)
        self.assertAlmostEqual(out['delta_cents'], 80)
        self.assertEqual(out['adjusted']['D'], {'lo_cents': 320, 'hi_cents': 420})
        self.assertEqual(out['adjusted']['R'], {'lo_cents': 680, 'hi_cents': 830})
        for endpoint in ('lo_cents', 'hi_cents'):
            self.assertEqual(sum(out['adjusted'][party][endpoint] for party in ('D', 'R')),
                             sum(r['baseline'][party][endpoint] for party in ('D', 'R')))
        self.assertEqual((r, m), before)

    def test_positive_and_negative_clamps_preserve_observed_floor(self):
        for coefficient, expected in ((2, 100), (-2, -200)):
            m = model(); m['coefficients'] = [coefficient, 0]
            out = p.project(row(), m)
            self.assertEqual(out['delta_cents'], expected)
            self.assertTrue(out['clamped'])
            for party in ('D', 'R'):
                observed_floor = row()['baseline'][party]['lo_cents'] - row()['remaining'][party]['lo_cents']
                self.assertGreaterEqual(out['adjusted'][party]['lo_cents'], observed_floor)

    def test_missing_is_baseline_not_zero(self):
        r = row(); r['features'].pop('champion_mean_dw')
        out = p.project(r, model())
        self.assertEqual(out['status'], 'baseline_fallback')
        self.assertEqual(out['adjusted'], r['baseline'])
        self.assertIn('Missing feature: champion_mean_dw', out['issues'])

    def test_current_board_definition_cannot_replace_fitted_proxy(self):
        r = row(); r['feature_provenance']['feature_definition'] = 'current_champion_board'
        out = p.project(r, model())
        self.assertEqual(out['status'], 'baseline_fallback')
        self.assertEqual(out['delta_cents'], 0)

    def test_negative_remaining_keeps_signed_baseline(self):
        r = row(); r['remaining']['D']['lo_cents'] = -10
        out = p.project(r, model())
        self.assertEqual(out['adjusted'], r['baseline'])
        self.assertEqual(out['status'], 'baseline_fallback')

    def test_zero_remaining_permits_no_positive_transfer(self):
        r = row(); r['remaining']['D']['lo_cents'] = 0
        out = p.project(r, model())
        self.assertEqual(out['delta_cents'], 0)
        self.assertTrue(out['clamped'])

    def test_negative_signed_baseline_is_preserved_not_clamped(self):
        r = row(); r['baseline']['D']['lo_cents'] = -10
        out = p.project(r, model())
        self.assertEqual(out['adjusted'], r['baseline'])
        self.assertEqual(out['status'], 'baseline_fallback')
        self.assertEqual(p.project(r, model('baseline'), 'baseline')['adjusted'], r['baseline'])

    def test_one_cycle_coefficients_cannot_bypass_fitting_minimum(self):
        m = model(); m['training_cycles'] = [2024]
        self.assertEqual(p.project(row(), m)['status'], 'baseline_fallback')

    def test_baseline_and_lean_only_are_explicit(self):
        self.assertEqual(p.project(row(), model('baseline'), 'baseline')['status'], 'baseline')
        r = row(); r['features'].pop('champion_mean_dw')
        out = p.project(r, model('lean_only'), 'lean_only')
        self.assertEqual(out['delta_cents'], 100)
        with self.assertRaises(ValueError): p.project(row(), model(), 'lean_only')

    def test_insufficient_model_has_no_fake_coefficients(self):
        m = model(); m.update(status='insufficient_earlier_elections', means=[], scales=[], coefficients=[], training_cycles=[2024])
        out = p.project(row(), m)
        self.assertEqual(out['status'], 'baseline_fallback')
        self.assertEqual(out['adjusted'], row()['baseline'])

    def test_reject_nonfinite_or_out_of_range_measurements(self):
        for value in (float('nan'), float('inf'), True, 1.1, -.1):
            r = row(); r['features']['target_r_share'] = value
            with self.assertRaises(ValueError): p.project(r, model())
        m = model(); m['coefficients'][0] = float('inf')
        with self.assertRaises(ValueError): p.project(row(), m)

    def test_reject_invalid_dates_training_and_bounds(self):
        for field, value in (('chamber', 'President'), ('target', 2025), ('as_of', '2026-02-30')):
            r = row(); r[field] = value
            with self.assertRaises(ValueError): p.project(r, model())
        m = model(); m['training_cycles'].append(2026)
        with self.assertRaises(ValueError): p.project(row(), m)
        r = row(); r['baseline']['R']['hi_cents'] = 1
        with self.assertRaises(ValueError): p.project(r, model())

    def test_catalog_matches_callable_all_variants(self):
        b = bundle(); before = copy.deepcopy(b)
        out = p.catalog(b)
        for variant, m in b['models'].items():
            self.assertEqual(out['rows'][0]['variants'][variant], p.project(b['current_rows'][0], m, variant))
        self.assertEqual(out['rows'][0]['adjusted'], p.project(row(), model())['adjusted'])
        self.assertFalse(out['replaces_published_pac_total'])
        self.assertEqual(b, before)

    def test_bundle_scope_duplicate_and_identity_rejected(self):
        for key, value in (('model_id', 'old_model'), ('accounting_basis', 'nominal_pac_to_house_senate_net'), ('active_variant', 'baseline')):
            b = bundle(); b[key] = value
            with self.assertRaises(ValueError): p.catalog(b)
        b = bundle(); b['current_rows'].append(row())
        with self.assertRaises(ValueError): p.catalog(b)

    def test_pin_and_json_roundtrip(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'bundle.json'
            raw = json.dumps(bundle(), allow_nan=False).encode(); path.write_bytes(raw)
            digest = hashlib.sha256(raw).hexdigest()
            actual = p.load_catalog(path, digest)
            self.assertEqual(actual['rows'], p.catalog(bundle())['rows'])
            self.assertEqual(actual, json.loads(json.dumps(actual, allow_nan=False)))
            with self.assertRaises(ValueError): p.load_catalog(path, '0' * 64)

    def test_current_champions_only_replaces_mean_without_mutation(self):
        r, m, board = row(), model(), profile()
        original = copy.deepcopy((r, m, board))
        out = p.project_current_champions(r, m, board)
        self.assertEqual(out['features']['champion_mean_dw'], .6)
        self.assertEqual(out['features']['target_r_share'], .75)
        self.assertEqual(out['original_trained_features'], r['features'])
        self.assertAlmostEqual(out['delta_cents'], 40)
        self.assertEqual(out['status'], 'scenario_adjusted')
        self.assertEqual(out['feature_provenance']['feature_definition'], p.CURRENT_SCENARIO_DEFINITION)
        self.assertEqual(out['feature_provenance']['trained_feature_definition'], p.FEATURE_DEFINITION)
        self.assertEqual((r, m, board), original)

    def test_current_champions_missing_is_not_zero_and_zero_is_valid(self):
        for board in (None, {**profile(), 'actual_current_champion_mean_dw_nominate_dim1': ''},
                      {**profile(), 'actual_current_champion_score_n': '24'}):
            out = p.project_current_champions(row(), model(), board)
            self.assertEqual(out['status'], 'baseline_fallback')
            self.assertIsNone(out['features']['champion_mean_dw'])
            self.assertEqual(out['adjusted'], row()['baseline'])
        board = profile(); board['actual_current_champion_mean_dw_nominate_dim1'] = '0'
        out = p.project_current_champions(row(), model(), board)
        self.assertEqual(out['status'], 'scenario_adjusted')
        self.assertEqual(out['delta_cents'], 100)

    def test_current_champions_reject_identity_future_and_nonfinite(self):
        for key, value in (('industry', 'credit_unions'), ('chamber', 'Senate'), ('cycle', 2024),
                           ('as_of_date', '2026-10-07'), ('actual_current_champion_mean_dw_nominate_dim1', 'nan')):
            board = profile(); board[key] = value
            with self.assertRaises(ValueError): p.project_current_champions(row(), model(), board)

    def test_current_champions_uses_same_clamp_and_conservation(self):
        m = model(); m['coefficients'] = [2, 1]
        out = p.project_current_champions(row(), m, profile())
        self.assertEqual(out['delta_cents'], 100)
        self.assertTrue(out['clamped'])
        for endpoint in ('lo_cents', 'hi_cents'):
            self.assertEqual(sum(out['adjusted'][party][endpoint] for party in ('D', 'R')),
                             sum(row()['baseline'][party][endpoint] for party in ('D', 'R')))

    def test_current_champions_does_not_inherit_accuracy_or_model_identity(self):
        b = bundle(); b['current_profiles'] = [profile()]
        b['validation_summary'] = {'metrics': [{'variant': 'both', 'n': 320}]}
        out = p.catalog(b)
        self.assertEqual(set(out['models']), {'baseline', 'lean_only', 'both'})
        scenario = out['rows'][0]['variants']['current_champions']
        self.assertEqual(scenario['variant'], 'current_champions')
        self.assertFalse(scenario['historical_validation_applicable'])
        self.assertFalse(scenario['calibrated_forecast'])
        self.assertNotIn('validation_summary', scenario)
        self.assertNotIn('metrics', scenario)
        self.assertEqual(out['rows'][0]['variants']['both'], p.project(row(), model()))

    def test_current_champions_cannot_relabel_missing_original_definition(self):
        r = row(); r['feature_provenance'] = {}
        out = p.project_current_champions(r, model(), profile())
        self.assertEqual(out['status'], 'baseline_fallback')
        self.assertEqual(out['delta_cents'], 0)


if __name__ == '__main__':
    unittest.main()

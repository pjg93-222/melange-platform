"""Synthetic standalone runtime tests; no frozen evaluation imported."""
import copy
import importlib.util
import json
import math
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('interval_runtime', Path(__file__).with_name('predict_interval.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def frame():
    return m.synthetic_example()['frame']


class RuntimeTests(unittest.TestCase):
    def test_all_three_explicit_families_accept_2026_and_match_hand_values(self):
        values = {'prior_party': (10000, 20000), 'party_R12_remaining': (11000, 19000), 'historical_remainder': (11000, 19000)}
        for family in m.FAMILIES:
            rows = m.predict(frame(), family)
            self.assertEqual(tuple(r['prediction_lo_cents'] for r in rows), values[family])
            self.assertEqual(tuple(r['prediction_hi_cents'] for r in rows), values[family])
            self.assertEqual(rows[0]['training_years'], [2020, 2022, 2024])

    def test_no_mutation_or_hidden_default(self):
        feature = frame(); original = copy.deepcopy(feature)
        for family in m.FAMILIES:
            m.predict(feature, family)
        self.assertEqual(feature, original)
        with self.assertRaises(m.PredictionError): m.predict(feature, 'auto')
        with self.assertRaises(m.PredictionError): m.predict_request({'frame': feature})

    def test_closed_frame_rejects_target_truth_anywhere(self):
        f = frame(); f['truth'] = 12
        with self.assertRaises(m.PredictionError): m.predict(f, 'prior_party')
        f = frame(); f['history'][0]['actual'] = 12
        with self.assertRaises(m.PredictionError): m.predict(f, 'prior_party')
        f = frame(); f['observed']['D']['actual_cents'] = 12
        with self.assertRaises(m.PredictionError): m.predict(f, 'prior_party')

    def test_dates_and_three_prior_cycles_required(self):
        for change in ({'target': 2025}, {'target': 2016}, {'target': True}, {'issue': '2026-07-21'}, {'cut': '2026-06-29'}):
            f = frame(); f.update(change)
            with self.assertRaises(m.PredictionError): m.predict(f, 'prior_party')
        f = frame(); f['history'][0]['cycle'] = 2026
        with self.assertRaises(m.PredictionError): m.predict(f, 'prior_party')
        f = frame(); f['history'].pop()
        with self.assertRaises(m.PredictionError): m.predict(f, 'prior_party')

    def test_each_horizon_schedule_and_later_even_target(self):
        for horizon in m.HORIZONS:
            f = frame(); f.update(target=2028, horizon=horizon)
            f['issue'], f['cut'] = m.schedule(2028, horizon)
            for old, year in zip(f['history'], (2022, 2024, 2026)): old['cycle'] = year
            self.assertEqual(len(m.predict(f, 'prior_party')), 2)

    def test_nonfinite_reversed_and_missing_bounds_refused(self):
        for lo, hi in ((math.nan, 1), (0, math.inf), (True, 1), (2, 1)):
            f = frame(); f['observed']['D'] = {'lo_cents': lo, 'hi_cents': hi}
            with self.assertRaises(m.PredictionError): m.predict(f, 'prior_party')
        f = frame(); del f['observed']['D']
        with self.assertRaises(m.PredictionError): m.predict(f, 'prior_party')

    def test_supplied_source_date_metadata_cannot_admit_future_information(self):
        f = frame(); f['history'][0]['final']['D']['source_issue_filter'] = '2027-01-31'
        with self.assertRaises(m.PredictionError): m.predict(f, 'prior_party')
        f = frame(); f['history'][0]['remaining']['D']['direct_remaining_rows'] = False
        with self.assertRaises(m.PredictionError): m.predict(f, 'historical_remainder')

    def test_r12_interval_and_explicit_domain_fallback(self):
        f = frame(); f['history'][-1]['final']['D'] = {'lo_cents': 8000, 'hi_cents': 12000}
        f['history'][0]['final']['D']['lo_cents'] = 0
        row = m.predict(f, 'party_R12_remaining')[0]
        self.assertEqual((row['prediction_lo_cents'], row['prediction_hi_cents']), (8000, 12000))
        self.assertIsNotNone(row['uncertainty_rule_fallback'])

    def test_zero_completion_and_cap_remain_defined(self):
        f = frame()
        for old in f['history']:
            old['observed']['D'] = {'lo_cents': 0, 'hi_cents': 0}
            old['observed']['R'] = {'lo_cents': 24000, 'hi_cents': 24000}
        rows = m.predict(f, 'party_R12_remaining')
        self.assertEqual(rows[0]['prediction_lo_cents'], 16000)
        self.assertEqual(rows[1]['prediction_hi_cents'], 9000)

    def test_negative_remainder_fallback_retains_negative_prior_without_floor(self):
        f = frame(); f['history'][-1]['final']['D'] = {'lo_cents': -100, 'hi_cents': 100}
        for old in f['history']:
            old['remaining']['D'] = {'lo_cents': -7000, 'hi_cents': 100}
        row = m.predict(f, 'historical_remainder')[0]
        self.assertEqual((row['prediction_lo_cents'], row['prediction_hi_cents']), (-100, 100))
        self.assertIsNotNone(row['uncertainty_rule_fallback'])

    def test_no_accuracy_selection_or_activation_claim_in_request_result(self):
        result = m.predict_request(m.synthetic_example())
        for field in ('live_activation', 'activation_recommended', 'selection_performed'):
            self.assertIs(result[field], False)
        self.assertFalse(any('certified_within20' in r for r in result['predictions']))

    def test_package_identity_and_runtime_tamper_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); runtime = b'fixed runtime'
            (root / 'runtime.py').write_bytes(runtime)
            manifest = dict(kind='offline_pac_interval_predictor_package', families=list(m.FAMILIES),
                live_activation=False, activation_recommended=False, selection_performed=False,
                artifact_sha256={'runtime.py': m.sha(runtime)})
            raw = m.canonical(manifest); path = root / 'package_manifest.json'; path.write_bytes(raw)
            self.assertEqual(m.verify_package(path, m.sha(raw))['families'], list(m.FAMILIES))
            with self.assertRaises(m.PredictionError): m.verify_package(path, '0'*64)
            (root / 'runtime.py').write_bytes(b'changed runtime')
            with self.assertRaises(m.PredictionError): m.verify_package(path, m.sha(raw))

    def test_package_export_refuses_existing_destination_before_reading_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(m.PredictionError):
                m.stage_package(directory, 'absent', 'absent', 'absent', 'absent')


if __name__ == '__main__':
    unittest.main()

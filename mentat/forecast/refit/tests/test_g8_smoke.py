"""The G8 consumer smoke (tools/g8_consumer_smoke.py) against the contract example bundle (must PASS) and against a bundle the calculator refuses (must FAIL).
Runs locally only: it needs mentat/app (a pod snapshot carries mentat/forecast). usage: python -m unittest mentat/forecast/refit/tests/test_g8_smoke.py"""
from datetime import date, datetime, time, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
SMOKE = HERE.parent / 'tools' / 'g8_consumer_smoke.py'
CONTRACTS = HERE.parents[1] / 'contracts'
sys.path.insert(0, str(CONTRACTS))
import contract  # noqa: E402


def smoke(bundle):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'giving-models-v2.json'
        path.write_text(json.dumps(bundle), encoding='utf-8')
        done = subprocess.run([sys.executable, str(SMOKE), str(path)], capture_output=True, text=True, timeout=120)
    return done.returncode, done.stdout + done.stderr


class G8SmokeTests(unittest.TestCase):
    def test_contract_example_bundle_passes_and_projects_banks_and_credit_unions(self):
        code, out = smoke(contract.example('models'))
        self.assertEqual(code, 0, out)
        self.assertIn('G8 PASS', out)
        self.assertIn('commercial_banks', out)
        self.assertIn('credit_unions', out)

    def test_a_cycle_model_bundle_passes_with_the_ibis_defaults_and_still_projects_every_offered_industry(self):
        code, out = smoke(contract.example('models_cycle'))
        self.assertEqual(code, 0, out)
        self.assertIn('G8 PASS', out)
        self.assertIn('commercial_banks', out)
        self.assertIn('projected 2 horizon(s)', out)

    def test_a_bundle_built_at_2330_eastern_passes_on_that_same_local_day(self):
        # 23:30 Eastern is 03:30Z/04:30Z on the next UTC day, so the refit stamps built_on one day ahead of the console's calendar until Eastern midnight (2026-10-09)
        built = datetime.combine(date.today(), time(23, 30), tzinfo=ZoneInfo('America/New_York')).astimezone(timezone.utc)
        bundle = contract.example('models')
        bundle.update(built_at_utc=built.strftime('%Y-%m-%dT%H:%M:%SZ'), built_on=built.date().isoformat())
        self.assertGreater(bundle['built_on'], date.today().isoformat())
        code, out = smoke(bundle)
        self.assertEqual(code, 0, out)
        self.assertIn('G8 PASS', out)
        self.assertNotIn('historical cutoff', out)

    def test_a_bundle_the_calculator_refuses_fails(self):
        bad = contract.example('models')
        bad['industries']['commercial_banks']['withheld'] = []       # the empty-map-written-as-list defect the contract rejects
        code, out = smoke(bad)
        self.assertEqual(code, 1, out)
        self.assertIn('G8 FAIL', out)

    def test_a_schema_1_bundle_fails(self):
        code, out = smoke(json.loads((HERE.parents[1].parent / 'app' / 'data' / 'giving-models-v1.json').read_text(encoding='utf-8')))
        self.assertEqual(code, 1, out)


if __name__ == '__main__':
    unittest.main()

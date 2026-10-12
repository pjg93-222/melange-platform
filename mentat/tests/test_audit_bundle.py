import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class AuditBundleTests(unittest.TestCase):
    def test_empty_csv_replaces_stale_rows_and_bundle_binds_unchanged_inputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = root / 'payload.json'
            workbook = root / 'report.xlsx'
            audit = root / 'report.audit'
            audit.mkdir()
            (audit / 'classification_review.csv').write_text('stale\nrow\n')
            fields = ('transfer_paths', 'allocation_estimates', 'allocation_coverage',
                'attribution_summary', 'funding_cohorts', 'source_freshness', 'classification_review', 'quality_checks',
                'downstream_reach', 'downstream_measurement_ledger', 'downstream_sources')
            payload.write_text(json.dumps({name: [] for name in fields}), encoding='utf-8')
            workbook.write_bytes(b'fixture workbook bytes')
            original = payload.read_bytes()
            subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1] /
                'tools/enrich_downstream.py'), str(payload), str(root), '--audit-only',
                '--audit-directory', str(audit), '--workbook', str(workbook)],
                check=True, capture_output=True)
            manifest = json.loads((audit / 'audit-manifest.json').read_text())
            self.assertEqual(payload.read_bytes(), original)
            self.assertEqual(manifest['payload_sha256'], hashlib.sha256(original).hexdigest())
            self.assertEqual(manifest['workbook_sha256'], hashlib.sha256(workbook.read_bytes()).hexdigest())
            self.assertEqual((audit / 'classification_review.csv').read_text(encoding='utf-8-sig'), 'status\n')
            self.assertEqual(set(manifest['csv_sha256']), {name + '.csv' for name in fields})
            for name, expected in manifest['csv_sha256'].items():
                self.assertEqual(expected, hashlib.sha256((audit / name).read_bytes()).hexdigest())


if __name__ == '__main__':
    unittest.main()

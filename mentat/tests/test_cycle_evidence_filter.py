import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class CycleEvidenceTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node required')
    def test_selected_cycle_filters_funding_paths_and_allocation_audits_together(self):
        arrays = ['raw_rows', 'employees', 'committee_map', 'downstream_activity', 'beneficiary_bridge', 'beneficiary_summary',
                  'candidate_master', 'candidate_committee_links', 'transfer_edges', 'transfer_paths', 'funding_cohorts',
                  'allocation_estimates', 'allocation_coverage', 'attribution_summary', 'source_freshness', 'classification_review']
        payload = {name:[{'cycle':2024, 'amount':1}, {'cycle':2026, 'amount':2}] for name in arrays}
        payload['cycles'] = [{'cycle':2024,'label':'2023-2024'}, {'cycle':2026,'label':'2025-2026'}]
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory)/'input.json', Path(directory)/'output.json'
            source.write_text(json.dumps(payload))
            script = Path(__file__).resolve().parents[1]/'tools/filter_industry_cycle_data.mjs'
            subprocess.run([shutil.which('node'),str(script),str(source),str(target),'2025-2026'],check=True,capture_output=True)
            result = json.loads(target.read_text())
            for name in arrays:
                self.assertEqual(result[name], [{'cycle':2026,'amount':2}], name)
            self.assertEqual(json.loads(source.read_text()), payload)


if __name__ == '__main__':
    unittest.main()

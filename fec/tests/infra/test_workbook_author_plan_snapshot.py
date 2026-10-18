"""Synthetic workbooks only; no live artifacts, scoring, or pipeline."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

SOURCE = Path(__file__).resolve().parents[2] / 'xlsx_openpyxl_authors.py'
SPEC = importlib.util.spec_from_file_location('workbook_snapshot_fixture', SOURCE)
A = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(A)


class PlanSnapshot(unittest.TestCase):
    def test_all_author_receipts_bind_consumed_bytes_during_replacement(self):
        for kind in ('cells', 'honors', 'sentinel'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory(prefix='fec-plan-snapshot-') as directory:
                base = Path(directory)
                output = base / 'workbook.xlsx'
                if kind == 'cells':
                    payload = {'output': str(output), 'changes': [{'sheet': 'S', 'cell': 'A1', 'value': 'original'}]}
                    hash_key = 'plan_sha256'
                elif kind == 'honors':
                    payload = {'output': str(output), 'generation_id': 'fixture', 'views': [
                        {'name': 'Field Recognition', 'values': [['original'] + [None] * 7,
                         ['note'] + [None] * 7, ['generation'] + [None] * 7,
                         ['header'] + [None] * 7, ['value'] + [None] * 7]}]}
                    hash_key = 'plan_sha256'
                else:
                    payload = {'schema': 'sentinel-bill-workbook-plan-v1', 'ledger_sheet': 'Ledger',
                               'output_workbook': str(output), 'ledger_rows': [['original', 'Title'], ['B1', 'Fixture']],
                               'accepted_interactions': []}
                    hash_key = 'input_plan_sha256'
                plan = base / 'plan.json'
                original = json.dumps(payload, indent=2).replace('\n', '\r\n').encode('utf-8')
                plan.write_bytes(original)
                save = A.openpyxl.Workbook.save
                def replace_plan_then_save(workbook, filename):
                    plan.write_text('{"replacement": true}', encoding='utf-8')
                    return save(workbook, filename)
                with mock.patch.object(A.openpyxl.Workbook, 'save', replace_plan_then_save):
                    if kind == 'cells':
                        receipt = A.author_cell_sync(plan)
                    elif kind == 'honors':
                        receipt = A.author_honors_views(plan)
                    else:
                        receipt = A.author_sentinel(plan, base / 'report.json')
                self.assertEqual(receipt[hash_key], hashlib.sha256(original).hexdigest())
                self.assertNotEqual(receipt[hash_key], hashlib.sha256(plan.read_bytes()).hexdigest())
                self.assertEqual(receipt['output_sha256'], hashlib.sha256(output.read_bytes()).hexdigest())
                workbook = A.openpyxl.load_workbook(output, read_only=True)
                try:
                    self.assertEqual(workbook.worksheets[0]['A1'].value, 'original')
                finally:
                    workbook.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)

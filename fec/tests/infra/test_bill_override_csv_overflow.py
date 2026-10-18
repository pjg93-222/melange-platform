"""CSV parsing fixtures only; no bill data, model, or publication."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[2] / 'bill_rules.py'
SPEC = importlib.util.spec_from_file_location('bill_override_fixture', SOURCE)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class CsvOverflow(unittest.TestCase):
    def read_rows(self, content):
        with tempfile.TemporaryDirectory(prefix='fec-override-csv-') as temporary:
            path = Path(temporary) / 'fixture.csv'
            path.write_text(content, encoding='utf-8')
            return MODULE.load_overrides(path)

    def test_extra_cells_do_not_destroy_named_override(self):
        rows = self.read_rows('Bill,Congress,Include,Reason\nH.R.7,119,0,fixture,extra,another\n')
        self.assertEqual(rows, {(('H', 7), '119'): {
            'Bill': 'H.R.7', 'Congress': '119', 'Include': '0', 'Reason': 'fixture'}})
        self.assertFalse(MODULE.override_included(rows[(('H', 7), '119')]))

    def test_valid_and_missing_cells_remain_unchanged(self):
        rows = self.read_rows('Bill,Congress,Include,Reason\nH.R.7,119,0, fixture \nS.8,118,1\n')
        self.assertEqual(rows[(('H', 7), '119')]['Reason'], 'fixture')
        self.assertEqual(rows[(('S', 8), '118')]['Reason'], '')
        self.assertTrue(MODULE.override_included(rows[(('S', 8), '118')]))

    def test_missing_file_is_still_empty(self):
        with tempfile.TemporaryDirectory(prefix='fec-override-missing-') as temporary:
            self.assertEqual(MODULE.load_overrides(Path(temporary) / 'missing.csv'), {})


if __name__ == '__main__':
    unittest.main(verbosity=2)

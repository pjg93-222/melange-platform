"""Controls proving the independent result audit rejects corrupted artifacts."""
import copy
import importlib.util
from pathlib import Path
import unittest


def load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


b = load('bounded_pac')
v = load('verify_bounded_results')


def fixture():
    presence = [dict(sector=s, cycle=y, chamber=c, ledger_reconciled='True', sector_raw_rows='1',
                     first_due_any_raw=f'{y-1}-02-20')
                for s in b.SECTORS for y in b.YEARS for c in b.CHAMBERS]
    rows = []
    for s in b.SECTORS:
        for y in b.YEARS:
            for ci, c in enumerate(b.CHAMBERS):
                for pi, p in enumerate(b.PARTIES):
                    for i, (day, amount) in enumerate((('03-01', 3000), ('09-01', 9000))):
                        rows.append(dict(sector=s, cycle=y, chamber=c, party=p, cents=amount,
                                         source_record_order=ci * 100 + pi * 10 + i,
                                         hs=1, date=f'{y}-{day}', due_available=f'{y}-{day}'))
    return b.evaluate(b.Panel(rows, presence))


class IndependentAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = fixture()

    def test_full_independent_recomputation(self):
        receipt = v.verify(self.result)
        self.assertEqual(receipt['status'], 'PASS')
        self.assertEqual(receipt['prediction_rows'], 960)

    def test_prediction_certification_and_metric_corruptions_rejected(self):
        for target, field, replacement in [('predictions', 'prediction_hi_cents', 1),
                                             ('predictions', 'certified_within20', False),
                                             ('metrics', 'n', 1)]:
            bad = copy.deepcopy(self.result)
            if target == 'predictions' and field == 'certified_within20':
                replacement = not bad[target][0][field]
            bad[target][0][field] = replacement
            with self.assertRaises(AssertionError):
                v.verify(bad)


if __name__ == '__main__':
    unittest.main()

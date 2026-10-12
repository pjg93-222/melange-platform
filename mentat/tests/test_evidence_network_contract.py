"""contracts/evidence-network.json v1: /api/evidence-network's per-committee coverage
and summary reconciliation (F4, Paul approved 2026-09-25), asserted against the producer.

PAC evidence showed one coverage window per network and checked only the pane's
own committee against its FEC summary. downstream.snapshot_view now lists every
committee in the network (`committees`), each with its last itemized transaction
and its summary's coverage end, and its itemized independent expenditures and
lines 22-23 transfers against its own summary ("no summary saved" where there is
none). The contract's example is this file's scenario, produced by the real
producer; its negative controls must be refused for their stated reasons.
"""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import downstream  # noqa: E402

CONTRACT = (Path(__file__).resolve().parents[2] / 'audits' / 'mentat_failings_20260915'
            / 'contracts' / 'evidence-network.json')


def spend(amount, day, sub_id):
    return {'sub_id': sub_id, 'candidate_id': 'H6CA12000', 'candidate_name': 'HOUSE, HOPE',
            'support_oppose_indicator': 'S', 'expenditure_amount': amount, 'expenditure_date': day,
            'memo_code': '', 'memoed_subtotal': False}


def transfer(destination, amount, day, sub_id, kind=None):
    row = {'sub_id': sub_id, 'line_number': '22', 'recipient_committee_id': destination,
           'recipient_name': destination, 'disbursement_amount': amount, 'disbursement_date': day,
           'memo_code': '', 'memoed_subtotal': False}
    if kind:
        row['disbursement_type'] = kind
    return row


def units():
    """A FAIRSHAKE-shaped 2026 network. The root's summary reports 65,000 on line 22
    and its rows itemize 53,000; Defend American Jobs' summary runs to 7/31 while
    its itemized spending stops on 6/29, and its itemized independent expenditures
    fall 3,000 short of its summary; Protect Progress reconciles; the root's
    untyped line-22 row reaches a committee with no saved snapshot."""
    return [
        {'committee_id': 'C00835959', 'cycle': 2026,
         'receipts': [{'sub_id': 'r1', 'contribution_receipt_amount': 100000, 'contribution_receipt_date': '2026-06-30',
                       'contributor_name': 'COINBASE', 'memo_code': '', 'memoed_subtotal': False}],
         'disbursements': [transfer('C00836221', 30000, '2026-05-01', 'b1', '24G'),
                           transfer('C00848440', 21000, '2026-05-02', 'b2', '24G'),
                           transfer('C00571703', 2000, '2026-06-29', 'b3')],
         'expenditures': [],
         'totals': {'coverage_start_date': '2025-01-01T00:00:00', 'coverage_end_date': '2026-07-31T00:00:00',
                    'disbursements': 65000.0, 'transfers_to_affiliated_committee': 65000.0,
                    'independent_expenditures': 0.0}},
        {'committee_id': 'C00836221', 'cycle': 2026, 'receipts': [], 'disbursements': [],
         'expenditures': [spend(37000, '2026-06-29', 'e1')],
         'totals': {'coverage_start_date': '2025-01-01T00:00:00', 'coverage_end_date': '2026-07-31T00:00:00',
                    'disbursements': 37000.0, 'independent_expenditures': 40000.0}},
        {'committee_id': 'C00848440', 'cycle': 2026, 'receipts': [], 'disbursements': [],
         'expenditures': [spend(21000, '2026-06-25', 'e2')],
         'totals': {'coverage_start_date': '2025-01-01T00:00:00', 'coverage_end_date': '2026-06-30T00:00:00',
                    'disbursements': 21000.0, 'independent_expenditures': 21000.0,
                    'transfers_to_affiliated_committee': 0.0, 'fed_candidate_committee_contributions': 0.0}},
    ]


def cut(value, like):
    """value cut to the shape of like: a dict keeps only the keys like names
    (recursively), a list is cut item by item against like's items. An added
    field is then not a difference; a renamed, missing or changed one still is."""
    if isinstance(like, dict) and isinstance(value, dict):
        return {key: cut(item, like[key]) for key, item in value.items() if key in like}
    if isinstance(like, list) and isinstance(value, list):
        return [cut(item, like[index]) if index < len(like) else item for index, item in enumerate(value)]
    return value


def network_answer():
    """snapshot_view's answer for the scenario; the temp snapshot path is named by its folder."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / 'fairshake-fixture').mkdir()
        (root / 'fairshake-fixture' / 'snapshots.json').write_text(json.dumps(units()), encoding='utf-8')
        answer = downstream.snapshot_view('fairshake-fixture', 'C00835959', '2026', root)
    for row in answer['sources']:
        row['source'] = '/'.join(Path(row['source']).parts[-2:])
    return answer


class EvidenceNetworkContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = json.loads(CONTRACT.read_text(encoding='utf-8'))
        cls.answer = network_answer()

    def test_the_producer_answer_holds_and_is_the_example(self):
        # Field by field: the producer may add fields (F12 dates the walk), but
        # every field the example names must be there with the example's value.
        self.assertEqual(downstream.network_problems(self.answer), [])
        example = self.contract['examples'][0]['payload']
        self.assertEqual(cut(self.answer['committees'], example['committees']), example['committees'])
        self.assertEqual(cut(self.answer['missing'], example['missing']), example['missing'])

    def test_negative_control_the_example_check_still_sees_a_renamed_or_changed_field(self):
        example = self.contract['examples'][0]['payload']['committees']
        added = copy.deepcopy(example)
        added[0]['date_unverified'] = True
        self.assertEqual(cut(added, example), example)
        renamed = copy.deepcopy(example)
        renamed[0]['last_itemized_on'] = renamed[0].pop('last_itemized_date')
        self.assertNotEqual(cut(renamed, example), example)
        changed = copy.deepcopy(example)
        changed[0]['reconciliation']['transfers']['difference'] = 0.0
        self.assertNotEqual(cut(changed, example), example)
        dropped = copy.deepcopy(example)[:-1]
        self.assertNotEqual(cut(dropped, example), example)

    def test_each_committee_carries_its_own_coverage_and_checks(self):
        rows = {row['committee']: row for row in self.answer['committees']}
        self.assertEqual(list(rows), ['C00835959', 'C00836221', 'C00848440', 'C00571703'])
        jobs = rows['C00836221']
        self.assertEqual((jobs['last_itemized_date'], jobs['coverage_end']), ('2026-06-29', '2026-07-31'))
        self.assertEqual(jobs['reconciliation']['independent_expenditures'],
                         {'status': 'differs', 'summary': 40000.0, 'itemized': 37000.0, 'difference': -3000.0})
        self.assertEqual(jobs['reconciliation']['transfers']['status'], 'no_summary')
        root = rows['C00835959']
        self.assertEqual(root['last_itemized'], {'receipts': '2026-06-30', 'disbursements': '2026-06-29',
                                                 'expenditures': None})
        self.assertEqual(root['reconciliation']['transfers'],
                         {'status': 'differs', 'summary': 65000.0, 'itemized': 53000.0, 'difference': -12000.0})
        self.assertEqual(root['reconciliation']['independent_expenditures']['status'], 'reconciled')
        self.assertEqual({name: check['status'] for name, check in rows['C00848440']['reconciliation'].items()},
                         {'independent_expenditures': 'reconciled', 'transfers': 'reconciled'})
        unsaved = rows['C00571703']
        self.assertFalse(unsaved['saved'])
        self.assertEqual({check['status'] for check in unsaved['reconciliation'].values()}, {'no_summary'})

    def test_the_transfer_check_is_the_map_s_not_itemized_gap(self):
        # One rule: evidence_geo's F6 line is this check's shortfall, never negative.
        import evidence_geo
        for unit in units():
            gap, stated, itemized = evidence_geo.transfers_not_itemized(unit)
            check = downstream.summary_reconciliation(unit)['transfers']
            if stated is None:
                self.assertEqual(check['status'], 'no_summary')
            else:
                self.assertEqual(float(gap), max(0.0, -check['difference']))

    def test_every_example_validates(self):
        self.assertTrue(self.contract['examples'])
        for example in self.contract['examples']:
            self.assertEqual(downstream.network_problems(example['payload']), [], example['name'])

    def test_every_negative_control_is_refused_for_its_stated_reason(self):
        self.assertGreaterEqual(len(self.contract['negative_controls']), 5)
        for control in self.contract['negative_controls']:
            problems = downstream.network_problems(copy.deepcopy(control['payload']))
            self.assertTrue(any(control['expect'] in problem for problem in problems),
                            f"{control['name']}: {problems}")


if __name__ == '__main__':
    unittest.main()

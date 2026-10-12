"""Lane B's loose end 5: the cash model names a transfer and counts a reload the
way the collector, the network and the map do (tools/transfer_rule.py, F8 and F11).

Each case is one the old line 22/23 rule and the old every-row count got wrong;
each is also run against the other outcome, so a regression back fails here.
"""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from pac_cash_model import estimate_snapshots  # noqa: E402


def pair(transfer):
    """C00000001 sends 50 to C00000002, which spends it; `transfer` is the outgoing row."""
    first = {'committee_id': 'C00000001', 'cycle': 2024,
             'totals': {'coverage_start_date': '2023-01-01', 'coverage_end_date': '2024-12-31',
                        'cash_on_hand_beginning_period': 0, 'last_cash_on_hand_end_period': 50,
                        'receipts': 100, 'disbursements': 50, 'independent_expenditures': 0},
             'receipts': [{'sub_id': 'a1', 'filing_form': 'F3X', 'line_number': '11AI', 'contributor_name': 'Acme Inc',
                           'contribution_receipt_date': '2024-01-01', 'contribution_receipt_amount': 100}],
             'disbursements': [{'sub_id': 'b1', 'filing_form': 'F3X', 'recipient_committee_id': 'C00000002',
                                'disbursement_amount': 50, 'disbursement_date': '2024-01-10', **transfer}],
             'expenditures': []}
    second = {'committee_id': 'C00000002', 'cycle': 2024,
              'totals': {'coverage_start_date': '2023-01-01', 'coverage_end_date': '2024-12-31',
                         'cash_on_hand_beginning_period': 0, 'last_cash_on_hand_end_period': 0,
                         'receipts': 50, 'disbursements': 50, 'independent_expenditures': 50},
              'receipts': [{'sub_id': 'a2', 'filing_form': 'F3X', 'line_number': '12', 'contributor_name': 'First PAC',
                            'contributor_id': 'C00000001', 'contribution_receipt_date': '2024-01-11',
                            'contribution_receipt_amount': 50}],
              'disbursements': [],
              'expenditures': [{'sub_id': 'e2', 'filing_form': 'F3X', 'line_number': '24', 'is_notice': False,
                                'candidate_id': 'H00000001', 'candidate_name': 'Candidate', 'support_oppose_indicator': 'O',
                                'disbursement_dt': '2024-02-01', 'expenditure_date': '2024-01-29',
                                'expenditure_amount': 50}]}
    return [first, second]


def result(units):
    return estimate_snapshots(units, 'Acme', ['Acme Inc'])['cycles'][0]


def checks(value):
    return {issue['check'] for issue in value['issues']}


class TransferByType(unittest.TestCase):
    def test_a_24k_filed_on_line_21b_is_a_transfer_and_pairs_with_its_receipt(self):
        # The old rule read line 21B as operating spending and left the receipt unpaired.
        value = result(pair({'line_number': '21B', 'disbursement_type': '24K'}))
        self.assertEqual(value['status'], 'estimated', value['issues'])
        self.assertEqual(value['allocations'][0]['estimated_funding']['Acme'], 50)

    def test_a_24z_filed_on_line_23_is_not_a_cash_transfer(self):
        # The old rule paired an in-kind 24Z as if cash had moved between the committees.
        value = result(pair({'line_number': '23', 'disbursement_type': '24Z'}))
        self.assertEqual(value['status'], 'withheld')
        self.assertIn('disbursement_role', checks(value))
        self.assertIn('transfer_pairing', checks(value))

    def test_negative_control_an_untyped_line_22_row_is_still_a_transfer(self):
        # transfer_rule falls back to the old line rule only when the row records no type.
        value = result(pair({'line_number': '22'}))
        self.assertEqual(value['status'], 'estimated', value['issues'])


class ReloadsCountOnce(unittest.TestCase):
    def reloaded(self, **change):
        units = pair({'line_number': '22', 'disbursement_type': '24G', 'report_type': 'M2',
                      'transaction_id': 'SB22.1', 'file_number': '1001', 'recipient_name': 'Second PAC'})
        again = dict(copy.deepcopy(units[0]['disbursements'][0]), sub_id='b1-reload', **change)
        units[0]['disbursements'].append(again)
        return units

    def test_a_reloaded_transfer_counts_once(self):
        # Counted twice, the ledger spent 100 against a summary of 50 and paired one row of two.
        value = result(self.reloaded())
        self.assertEqual(value['status'], 'estimated', value['issues'])
        self.assertNotIn('disbursements_gap', checks(value))

    def test_negative_control_a_distinct_transaction_id_stays_counted(self):
        value = result(self.reloaded(transaction_id='SB22.2'))
        self.assertEqual(value['status'], 'withheld')
        self.assertIn('disbursements_gap', checks(value))


if __name__ == '__main__':
    unittest.main()

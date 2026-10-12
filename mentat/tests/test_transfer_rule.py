"""F8: one rule for what a saved Schedule B row is, with the transfer index's own set.

Paul, 2026-09-25: "one rule, by transaction type, everywhere". The collector,
the downstream network and the map all ask tools/transfer_rule.py; these tests
pin the rule itself. The consumers are pinned in test_pac_evidence.py,
test_downstream_network.py and test_evidence_geo.py.

Run from inside mentat/tests: python -m unittest test_transfer_rule
"""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import fec_transfer_graph  # noqa: E402
import transfer_rule  # noqa: E402

leg = transfer_rule.schedule_b_leg


class TransferRuleTests(unittest.TestCase):
    def test_the_set_is_the_transfer_index_s_own(self):
        self.assertEqual(transfer_rule.TRANSFER_TYPES, frozenset(fec_transfer_graph.CASH_CODES))
        self.assertEqual(transfer_rule.TRANSFER_TYPES, {'24G', '24K'})

    def test_the_transaction_type_decides_whatever_the_line(self):
        # The Senate Leadership Fund case: a 24K filed on line 21B is a transfer.
        self.assertEqual(leg({'line_number': '21B', 'disbursement_type': '24K',
                              'recipient_committee_id': 'C00571703'}), ('transfer', 'transaction_type'))
        self.assertEqual(leg({'line_number': '29', 'disbursement_type': ' 24g ',
                              'recipient_committee_id': 'C00000001'}), ('transfer', 'transaction_type'))
        # A line-23 in-kind gift to a committee is not a cash transfer.
        self.assertEqual(leg({'line_number': '23', 'disbursement_type': '24Z',
                              'recipient_committee_id': 'C00000001'}), ('', 'transaction_type'))
        # A transfer type that names no committee has nowhere to go.
        self.assertEqual(leg({'line_number': '22', 'disbursement_type': '24G'}), ('', 'transaction_type'))

    def test_a_row_without_a_type_falls_back_to_the_old_line_rule_and_says_so(self):
        for missing in ({}, {'disbursement_type': None}, {'disbursement_type': ''}):
            with self.subTest(missing=missing):
                self.assertEqual(leg({'line_number': '22', 'recipient_committee_id': 'C00000001', **missing}),
                                 ('transfer', 'line_fallback'))
                self.assertEqual(leg({'line_number': '23', 'recipient_committee_id': 'C00000001', **missing}),
                                 ('transfer', 'line_fallback'))
                self.assertEqual(leg({'line_number': '21B', 'recipient_committee_id': 'C00000001', **missing}),
                                 ('', 'line_fallback'))
                self.assertEqual(leg({'line_number': '22', **missing}), ('', 'line_fallback'))

    def test_a_memo_line_is_never_a_leg(self):
        base = {'line_number': '22', 'disbursement_type': '24G', 'recipient_committee_id': 'C00000001'}
        for flags in ({'memo_code': 'X'}, {'memo_code': ' x '}, {'memoed_subtotal': True},
                      {'memoed_subtotal': 'true'}, {'is_memo_leg': '1'}):
            with self.subTest(flags=flags):
                self.assertEqual(leg({**base, **flags}), ('', 'memo'))
        for flags in ({'memoed_subtotal': False}, {'memoed_subtotal': 'false'}, {'memo_code': ''}):
            with self.subTest(flags=flags):
                self.assertEqual(leg({**base, **flags}), ('transfer', 'transaction_type'))

    def test_a_row_naming_a_candidate_stays_terminal_candidate_activity(self):
        # A 24K to the candidate's committee is a candidate payment on any line.
        self.assertEqual(leg({'line_number': '21B', 'disbursement_type': '24K', 'recipient_committee_id': 'C1',
                              'candidate_id': 'H0CA00001'}), ('candidate', 'transaction_type'))
        # Line 22/23 gifts keep their old reading, whatever their type (24C, 24Z).
        self.assertEqual(leg({'line_number': '23', 'disbursement_type': '24C', 'candidate_id': 'H0CA00001'}),
                         ('candidate', 'transaction_type'))
        self.assertEqual(leg({'line_number': '23', 'disbursement_type': '24Z', 'recipient_committee_id': 'C1',
                              'candidate_id': 'H0CA00001'}), ('candidate', 'transaction_type'))
        self.assertEqual(leg({'line_number': '23', 'candidate_id': 'H0CA00001'}), ('candidate', 'line_fallback'))
        # Neither a transfer type nor a transfer line: not a candidate payment.
        self.assertEqual(leg({'line_number': '21B', 'disbursement_type': '24Z', 'candidate_id': 'H0CA00001'}),
                         ('', 'transaction_type'))

    def test_the_line_is_read_without_its_punctuation(self):
        self.assertEqual(transfer_rule.line_code({'line_number': ' 21b '}), '21B')
        self.assertEqual(transfer_rule.line_code({'line_number': '(22)'}), '22')
        self.assertEqual(leg({'line_number': ' 22 ', 'recipient_committee_id': 'C00000001'}),
                         ('transfer', 'line_fallback'))


class ReloadRuleTests(unittest.TestCase):
    """F11 (Paul, 2026-09-25): a row counts once as a reload ONLY when it shares
    committee | report type | transaction id | file number | counterparty |
    amount | date | type with another row and differs only in SUB_ID."""

    def row(self, sub, **fields):
        return {'committee_id': 'C00000001', 'report_type': 'M6', 'transaction_id': 'SB22.1',
                'file_number': 1985775, 'recipient_committee_id': 'C00000002', 'recipient_name': 'PAC TWO',
                'disbursement_amount': 9000000, 'disbursement_date': '2026-05-04T00:00:00',
                'disbursement_type': '24G', 'sub_id': sub, **fields}

    def test_a_reload_that_differs_only_in_sub_id_counts_once(self):
        census = transfer_rule.new_reload_census()
        kept = list(transfer_rule.count_reloads_once([self.row('1'), self.row('2'), self.row('3')],
                                                     'disbursements', 'C00000001', census))
        self.assertEqual([row['sub_id'] for row in kept], ['1'])
        self.assertEqual(census['reload_rows'], 2)
        self.assertEqual(census['reload_amount'], 18000000)

    def test_genuine_repeats_stay_counted(self):
        rows = [self.row('1'), self.row('2', transaction_id='SB22.2'), self.row('3', file_number=1985776),
                self.row('4', disbursement_amount=9000001), self.row('5', disbursement_date='2026-05-05'),
                self.row('6', disbursement_type='24K'), self.row('7', recipient_name='PAC THREE'),
                self.row('8', report_type='M7'), self.row('9', committee_id='C00000009'),
                self.row('1')]     # the same SUB_ID again is not a reload either
        self.assertEqual(len(list(transfer_rule.count_reloads_once(rows, 'disbursements'))), 10)

    def test_a_row_without_a_transaction_id_or_file_number_is_never_a_reload(self):
        rows = [self.row('1', transaction_id=''), self.row('2', transaction_id=''),
                self.row('3', file_number=None), self.row('4', file_number=None)]
        census = transfer_rule.new_reload_census()
        self.assertEqual(len(list(transfer_rule.count_reloads_once(rows, 'disbursements', census=census))), 4)
        self.assertEqual(census['rows_without_transaction_id_or_file_number'], 4)
        self.assertEqual(census['reload_rows'], 0)
        self.assertIsNone(transfer_rule.reload_key(rows[0], 'disbursements'))

    def test_the_key_has_the_same_eight_parts_on_every_schedule(self):
        self.assertEqual(transfer_rule.RELOAD_KEY,
                         'committee|report type|transaction id|file number|counterparty|amount|date|type')
        self.assertEqual(transfer_rule.reload_key(self.row('1'), 'disbursements'),
                         ('C00000001', 'M6', 'SB22.1', '1985775', 'C00000002|PAC TWO', '9000000.00',
                          '2026-05-04', '24G'))
        receipt = {'report_type': 'q1', 'transaction_id': 'SA11AI.1', 'file_number': '10',
                   'contributor_name': 'Doe, Jane', 'contribution_receipt_amount': '5',
                   'contribution_receipt_date': '2024-01-02', 'receipt_type': '15'}
        self.assertEqual(transfer_rule.reload_key(receipt, 'receipts', 'c00000001'),
                         ('C00000001', 'Q1', 'SA11AI.1', '10', '|DOE JANE', '5.00', '2024-01-02', '15'))
        spend = {'report_type': 'M10', 'transaction_id': 'SE.1', 'file_number': 7, 'candidate_id': 'H0KY06104',
                 'payee_name': 'Media Co', 'expenditure_amount': 10, 'expenditure_date': '2024-09-01',
                 'support_oppose_indicator': 'O'}
        self.assertEqual(transfer_rule.reload_key(spend, 'expenditures'),
                         ('', 'M10', 'SE.1', '7', 'H0KY06104|MEDIA CO', '10.00', '2024-09-01', '24A'))

    def test_a_memo_reload_is_skipped_but_is_not_counted_as_dollars(self):
        census = transfer_rule.new_reload_census()
        rows = [self.row('1', memo_code='X'), self.row('2', memo_code='X')]
        self.assertEqual(len(list(transfer_rule.count_reloads_once(rows, 'disbursements', census=census))), 1)
        self.assertEqual((census['reload_rows'], census['reload_amount']), (0, 0))


if __name__ == '__main__':
    unittest.main()

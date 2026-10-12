"""Cash timing, dilution, transfer conservation and unavailable-data boundaries."""
import copy
import json
from pathlib import Path
import random
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from pac_cash_model import OTHER, estimate_snapshots, run_pool, write_estimates


def event(identifier, kind, value, stamp='2024-01-01', committee='A', **extra):
    return dict(id=identifier, kind=kind, amount=value, date=stamp, committee=committee, **extra)


class CashPoolTests(unittest.TestCase):
    def test_company_share_is_diluted_and_transferred_once(self):
        rows = [event('company', 'receipt', 100, funding_source='Acme'),
                event('other', 'receipt', 300),
                event('move', 'transfer', 200, '2024-01-02', destination='B', received_date='2024-01-03'),
                event('operating', 'operating', 100, '2024-01-03'),
                event('support', 'candidate', 150, '2024-01-04', committee='B', direction='Support')]
        result = run_pool({'A': {}, 'B': {}}, rows, {'A': 100, 'B': 50})
        allocated = {r['id']: r['estimated_funding']['Acme'] for r in result['allocations']}
        self.assertEqual(allocated, {'operating': 25, 'support': 37.5})
        self.assertEqual(result['closing_funding']['A']['Acme'], 25)
        self.assertEqual(result['closing_funding']['B']['Acme'], 12.5)
        self.assertEqual(result['source_funding']['Acme'], 100)
        self.assertEqual(sum(allocated.values()) + 25 + 12.5, 100)

    def test_late_money_never_funds_earlier_candidate(self):
        rows = [event('early', 'candidate', 50, direction='Oppose'),
                event('late', 'receipt', 100, '2024-12-20', funding_source='Acme')]
        result = run_pool({'A': {OTHER: 100}}, rows, {'A': 150})
        self.assertNotIn('Acme', result['allocations'][0]['estimated_funding'])
        self.assertEqual(result['closing_funding']['A']['Acme'], 100)
        self.assertEqual(result['allocations'][0]['direction'], 'Oppose')

    def test_payment_cannot_spend_cash_still_in_transit(self):
        rows = [event('send', 'transfer', 100, destination='B', received_date='2024-01-03'),
                event('ad', 'candidate', 50, '2024-01-02', committee='B')]
        with self.assertRaisesRegex(ValueError, 'Insufficient dated cash'):
            run_pool({'A': {'Acme': 100}, 'B': {}}, rows, {'A': 0, 'B': 50})

    def test_same_day_transfer_chain_is_permutation_invariant(self):
        rows = [event('first', 'transfer', 80, destination='B', received_date='2024-01-01'),
                event('second', 'transfer', 40, committee='B', destination='C', received_date='2024-01-01'),
                event('ad', 'candidate', 20, committee='C')]
        expected = None
        for seed in range(8):
            shuffled = rows[:]; random.Random(seed).shuffle(shuffled)
            result = run_pool({'A': {'Acme': 50, OTHER: 50}, 'B': {}, 'C': {}}, shuffled, {'A': 20, 'B': 40, 'C': 20})
            self.assertEqual(result['allocations'][0]['estimated_funding']['Acme'], 10)
            if expected: self.assertEqual(result, expected)
            expected = result

    def test_cycle_and_bad_totals_are_refused(self):
        rows = [event('one', 'transfer', 10, destination='B', received_date='2024-01-01'),
                event('two', 'transfer', 10, committee='B', destination='A', received_date='2024-01-01')]
        with self.assertRaisesRegex(ValueError, 'circular'):
            run_pool({'A': {'Acme': 50}, 'B': {OTHER: 50}}, rows, {'A': 50, 'B': 50})
        with self.assertRaisesRegex(ValueError, 'Closing cash'):
            run_pool({'A': {'Acme': 50}}, [], {'A': 49})

    def test_duplicate_source_ids_and_invalid_amounts_are_refused(self):
        receipt = event('one', 'receipt', 1)
        with self.assertRaisesRegex(ValueError, 'unique'):
            run_pool({'A': {}}, [receipt, receipt], {'A': 2})
        for value in (None, -1, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                run_pool({'A': {}}, [event('bad', 'receipt', value)], {'A': 0})

    def test_identified_refund_does_not_get_charged_to_other_donors(self):
        result = run_pool({'A': {'Acme': 100, OTHER: 100}},
                          [event('refund', 'refund', 80, funding_source='Acme'),
                           event('ad', 'candidate', 60, '2024-01-02')], {'A': 60})
        self.assertEqual(result['allocations'][1]['estimated_funding']['Acme'], 10)
        self.assertEqual(result['closing_funding']['A']['Acme'], 10)


def snapshot():
    return {'committee_id': 'C00000001', 'cycle': 2024,
            'totals': {'coverage_start_date': '2023-01-01', 'coverage_end_date': '2024-12-31',
                       'cash_on_hand_beginning_period': 0, 'last_cash_on_hand_end_period': 50,
                       'receipts': 100, 'disbursements': 50, 'independent_expenditures': 50},
            'receipts': [{'sub_id': 'a1', 'filing_form': 'F3X', 'line_number': '11AI', 'contributor_name': 'Acme Inc',
                          'contribution_receipt_date': '2024-01-01', 'contribution_receipt_amount': 100}],
            'disbursements': [],
            'expenditures': [{'sub_id': 'e1', 'filing_form': 'F3X', 'line_number': '24', 'is_notice': False,
                              'candidate_id': 'H00000001', 'candidate_name': 'Candidate', 'support_oppose_indicator': 'O',
                              'disbursement_dt': '2024-02-01', 'expenditure_date': '2024-01-29', 'expenditure_amount': 50}]}


class SnapshotAdapterTests(unittest.TestCase):
    def test_all_unverified_receipt_roles_withhold_company_estimates(self):
        for change in ({'line_number':'17','receipt_type_full':'OTHER FEDERAL RECEIPTS'},
                       {'receipt_type_full':'IMPERMISSIBLE CONTRIBUTION'},
                       {'line_number':'UNKNOWN'}):
            with self.subTest(change=change):
                value=snapshot();value['receipts'][0].update(change)
                result=estimate_snapshots([value],'Acme',['Acme Inc'])['cycles'][0]
                self.assertEqual(result['status'],'withheld')
                self.assertEqual(result['allocations'],[])
                self.assertIn('receipt_role',{i['check'] for i in result['issues']})

    def test_verified_special_account_and_interest_roles_remain_usable(self):
        for description,expected in [('NON CONTRIBUTION ACCOUNT',50),('BANK INTEREST',0)]:
            with self.subTest(description=description):
                value=snapshot();value['receipts'][0].update(line_number='17',receipt_type_full=description)
                result=estimate_snapshots([value],'Acme',['Acme Inc'])['cycles'][0]
                self.assertEqual(result['status'],'estimated',result['issues'])
                self.assertEqual(result['allocations'][0]['estimated_funding'].get('Acme',0),expected)

    def test_balanced_cash_snapshot_produces_estimate_and_uses_payment_date(self):
        value = estimate_snapshots([snapshot()], 'Acme', ['Acme Inc'])['cycles'][0]
        self.assertEqual(value['status'], 'estimated')
        self.assertEqual(value['allocations'][0]['date'], '2024-02-01')
        self.assertEqual(value['allocations'][0]['direction'], 'Oppose')
        self.assertEqual(value['allocations'][0]['estimated_funding']['Acme'], 50)

    def test_gaps_assets_and_missing_payment_dates_are_unavailable_not_zero(self):
        changes = [('gap', lambda s: s['totals'].update(receipts=101, last_cash_on_hand_end_period=51)),
                   ('asset', lambda s: s['receipts'][0].update(receipt_type_full='IN-KIND NOT LIQUIDATED')),
                   ('date', lambda s: s['expenditures'][0].update(disbursement_dt=None))]
        for label, change in changes:
            with self.subTest(label=label):
                s = snapshot(); change(s)
                result = estimate_snapshots([s], 'Acme', ['Acme Inc'])['cycles'][0]
                self.assertEqual(result['status'], 'withheld')
                self.assertEqual(result['allocations'], [])
                self.assertTrue(result['issues'])

    def test_reciprocal_transfer_is_one_source_of_cash(self):
        first = snapshot(); first['expenditures'] = []
        first['totals']['independent_expenditures'] = 0
        first['disbursements'] = [{'sub_id': 'b1', 'filing_form': 'F3X', 'line_number': '22',
                                   'recipient_committee_id': 'C00000002', 'disbursement_amount': 50,
                                   'disbursement_date': '2024-01-10'}]
        second = snapshot(); second['committee_id'] = 'C00000002'
        second['totals'].update(receipts=50, last_cash_on_hand_end_period=0)
        second['receipts'][0].update(sub_id='a2', contribution_receipt_amount=50, contributor_name='First PAC',
                                    contributor_id='C00000001', line_number='12', contribution_receipt_date='2024-01-11')
        second['expenditures'][0]['sub_id'] = 'e2'
        result = estimate_snapshots([first, second], 'Acme', ['Acme Inc'])['cycles'][0]
        self.assertEqual(result['status'], 'estimated', result['issues'])
        self.assertEqual(result['source_funding']['Acme'], 100)
        self.assertEqual(result['allocations'][0]['estimated_funding']['Acme'], 50)
        second['receipts'][0]['contributor_id'] = 'C00000003'
        self.assertEqual(estimate_snapshots([first, second], 'Acme', ['Acme Inc'])['cycles'][0]['status'], 'withheld')

    def test_offsetting_category_error_cannot_hide_ie_summary_mismatch(self):
        value = snapshot(); value['totals']['independent_expenditures'] = 49
        result = estimate_snapshots([value], 'Acme', ['Acme Inc'])['cycles'][0]
        self.assertEqual(result['status'], 'withheld')
        self.assertIn('independent_expenditures_gap', {i['check'] for i in result['issues']})

    def test_sidecar_is_source_hash_bound_and_does_not_edit_observed_evidence(self):
        import hashlib
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = json.dumps([snapshot()]).encode()
            evidence = json.dumps({'company': 'Acme', 'aliases': ['Acme Inc'],
                                   'source_snapshot_sha256': hashlib.sha256(source).hexdigest()}).encode()
            (root / 'snapshots.json').write_bytes(source); (root / 'evidence.json').write_bytes(evidence)
            result = write_estimates(root)
            self.assertEqual(result['cycles'][0]['status'], 'estimated')
            self.assertEqual((root / 'evidence.json').read_bytes(), evidence)
            (root / 'snapshots.json').write_bytes(b'[]')
            with self.assertRaisesRegex(ValueError, 'hash'):
                write_estimates(root)


if __name__ == '__main__':
    unittest.main()

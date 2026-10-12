import sys
from pathlib import Path
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from pac_evidence import Client, collect_network, receipt_role, summarize, temporal_routes


class EvidenceTests(unittest.TestCase):
    def test_sale_and_memo_are_not_new_donations_even_with_contribution_code(self):
        sale = {'contributor_name': 'EXAMPLE EXCHANGE', 'receipt_type': '10', 'filing_form': 'F3X', 'line_number': '17',
                'receipt_type_full': '25,000,000 USDC SOLD VIA EXCHANGE - PURCHASER UNKNOWN'}
        self.assertEqual(receipt_role(sale), 'asset_liquidation')
        self.assertEqual(receipt_role({**sale, 'memo_code': 'X', 'receipt_type_full': 'CONTRIBUTION PREVIOUSLY DISCLOSED'}), 'unverified_disclosure_memo')
        self.assertEqual(receipt_role({'filing_form':'F3X','line_number':'11AI','memo_code':'X','memo_text':'PREVIOUSLY DISCLOSED'}), 'unverified_disclosure_memo')
        self.assertEqual(receipt_role({'filing_form':'F3X', 'line_number':'11AI', 'receipt_type_full':'IN-KIND - BITCOIN NOT LIQUIDATED'}), 'in_kind_contribution')

    def test_cash_contribution_and_other_receipt_are_distinct(self):
        self.assertEqual(receipt_role({'filing_form':'F3X', 'line_number':'11AI'}), 'contribution')
        self.assertEqual(receipt_role({'filing_form':'F3X', 'line_number':'17'}), 'unverified_other_receipt')
        self.assertEqual(receipt_role({'contributor_name':'Acme'}), 'unverified_receipt_role')
        self.assertEqual(receipt_role({'filing_form':'F3','line_number':'12'}), 'transfer_receipt')
        self.assertEqual(receipt_role({'filing_form':'F3P','line_number':'17'}), 'contribution')

    def test_other_federal_receipts_must_not_be_blanket_excluded(self):
        cases = {
            'NON-CONTRIBUTION ACCOUNT':'contribution',
            'HEADQUARTERS ACCOUNT CONTRIBUTION':'contribution',
            'LEGAL PROCEEDINGS ACCOUNT':'contribution',
            'NON-CONTRIBUTION':'other_receipt',
            'OTHER FEDERAL RECEIPTS (DIVIDENDS, INTEREST, ETC.)':'unverified_other_receipt',
            'INTEREST':'other_receipt',
            'RETURN OF ERRONEOUS DEBIT MADE BY PROCESSOR':'other_receipt',
            'NON-CONTRIBUTION ACCOUNT; 1500000 USDC LIQUIDATED VIA EXCHANGE - PURCHASER UNKNOWN':'asset_liquidation',
            'NON-CONTRIBUTION ACCOUNT; REFUND FOR PROCESSING FEES':'unverified_other_receipt',
            'LEGAL PROCEEDINGS ACCOUNT; IMPERMISSIBLE CONTRIBUTION; SEE NEXT REPORT FOR REFUND':'unverified_refund_adjustment',
        }
        for description, expected in cases.items():
            with self.subTest(description=description):
                self.assertEqual(receipt_role({'filing_form':'F3X','line_number':'17','receipt_type_full':description}), expected)

    def test_explicit_interest_income_survives_account_labels(self):
        for description in ('NON-CONTRIBUTION: INTEREST','NONCONTRIBUTION ACCOUNT - INTEREST',
                'HEADQUARTERS INTEREST INCOME','RECOUNT INTEREST INCOME','PAC BANK INTEREST',
                'CD INTEREST INCOME','MONTHLY INTEREST','INVESTMENT ACCOUNT INTEREST',
                'INTEREST/DIVIDENDS','INVESTMENT INCOME'):
            with self.subTest(description=description):
                self.assertEqual(receipt_role({'filing_form':'F3X','line_number':'17',
                    'receipt_type_full':description}), 'other_receipt')
        for account in ('NON-CONTRIBUTION ACCOUNT','NONCONTRIBUTION ACCOUNT','HEADQUARTERS ACCOUNT',
                        'LEGAL PROCEEDINGS ACCOUNT','RECOUNT ACCOUNT','CONVENTION ACCOUNT','NON-CONTRIBUTION'):
            self.assertEqual(receipt_role({'filing_form':'F3X','line_number':'17',
                'receipt_type_full':'INTEREST INCOME','memo_text':account}), 'other_receipt')

    def test_income_rule_preserves_ambiguous_captions_and_actual_gift_conflicts(self):
        for description in ('OTHER FEDERAL RECEIPTS (DIVIDENDS, INTEREST, ETC.)',
                'INVESTMENT OPPORTUNITY','INTEREST IN PUBLIC POLICY'):
            self.assertEqual(receipt_role({'filing_form':'F3X','line_number':'17',
                'receipt_type_full':description}), 'unverified_other_receipt')
        for gift in ('CONTRIBUTION','HEADQUARTERS ACCOUNT CONTRIBUTION','NON-CONTRIBUTION ACCOUNT DONATION'):
            for fields in ({'receipt_type_full':gift,'memo_text':'INTEREST INCOME'},
                           {'receipt_type_full':'INTEREST INCOME','memo_text':gift}):
                self.assertEqual(receipt_role({'filing_form':'F3X','line_number':'17',**fields}),
                                 'unverified_contradicted_receipt')
        self.assertEqual(receipt_role({'filing_form':'F3P','line_number':'17',
            'receipt_type_full':'INTEREST INCOME'}), 'contribution')

    def test_explicit_financial_events_and_refunds_are_other_receipts(self):
        descriptions = ('INTEREST RECEIVED','INVESTMENT CHANGE IN VALUE',
            'PAC INVESTMENT CHANGE IN VALUE','INVESTMENT LOSS','BANK INTEREST LOST',
            'GAIN ON SALE OF STOCK','LOSS ON SALE OF BOND','REINVESTED DIVIDEND',
            'UNREALIZED INVESTMENT GAINS','NET INVESTMENT EARNINGS',
            'INTEREST/DIVIDENDS INCOME PLUS UNREALIZED LOSS ON INVESTMENTS',
            'INTEREST, DIVIDENDS, CAPITAL GAIN (LOSS)','SALE OF INVESTMENT',
            'NET INVESTMENT EARNINGS/LOSS','SERVICE FEE','JANUARY INTEREST',
            'PROCEEDS FROM SALE OF 8 SHS VANGUARD REAL ESTATE',
            'CREDIT CARD REWARDS','MUTUAL FUND INVESTMENT ACTIVITY FOR PERIOD 1-1-26 THROUGH 3-31-26',
            'INTEREST FOR THE MONTH OF AUGUST 2023','NTEREST INCOME',
            'DUPLICATE BANK FEE CREDIT','ONE TIME ADJUSTMENT',
            'COMMERCIAL CARD REBATE','VENDOR REFUND',
            'RETURN OF FRAUDULENT DISBURSEMENT - SEE MEMO TEXT',
            'REFUND OF 9/18/22 FRAUDULENT DISBURSEMENT',
            'CREDIT FOR UNAUTHORIZED DISBURSEMENT ON 2/20/2024',
            'RETURNED EXAMPLE CONSULTING BILL PAYMENT',
            'REFUND OF ERRONEOUS BANK FEES FROM MAY 2023',
            'REFUND OF UNAUTHORIZED TRANSACTION THAT OCCURRED ON 07/31/2023',
            'BANK ERRONEOUSLY DEPOSITED 251 17 INTO PAC ACCOUNT THIS ERROR WILL BE REVERSED',
            'REVERSAL OF 08/27 ARKPAC FOR SEN. JOHN BOOZMAN CONTRIBUTION',
            'BANK REFUND OF STOLEN 7/26/21 CONTRIBUTION TO EXAMPLE FOR CONGRESS',
            'CREDIT FOR UNAUTHORIZED ACTIVITY','RECOVERED FUNDS FROM BANK FRAUD',
            'NON-CONTRIBUTION AMOUNT','REFUND OF BANK FEES ERRONEOUSLY CHARGED',
            'REFUND OF MERCHANT FEES','REFUND OF CHECK ORDER FEE',
            'BANK STOP PAYMENT FEE REFUND','BANK FEE REVERSAL','BANK FEE REFUND',
            'INTEREST DISTRIBUTION FUND','INTEREST (DISTRIBUTION FUND)',
            'INTEREST EARNE','INTEREST EARNER','JULY INTEREST EARNED',
            'OCTOBER MONTHLY INTEREST','ACCOUNT INTEREST','INTEREST FROM BANK ACCT',
            'INTEREST ON DEPOSIT ACCT','INTEREST ON BANK ACCOUNT','CASH BACK REWARD',
            'JPM TEST DEPOSIT','NON-CONTRIBUTORY ACCOUNT RECEIPT',
            'BANK ERROR DEPOSIT. HOPE TO HAVE THE BANK REVERSE THE ERROR IN APRIL 2022.',
            'BANK DEPOSIT ERROR - REFUNDED 08/10/2022','INTEREST ($0.32)',
            'TEST DEPOSIT')
        for description in descriptions:
            with self.subTest(description=description):
                self.assertEqual(receipt_role({'filing_form':'F3X','line_number':'17',
                    'receipt_type_full':description}), 'other_receipt')
        self.assertEqual(receipt_role({'filing_form':'F3X','line_number':'17',
            'receipt_type_full':'JFC TRANSFER: EXAMPLE JOINT FUNDRAISING COMMITTEE'}),
            'transfer_receipt')
        self.assertEqual(receipt_role({'filing_form':'F3X','line_number':'17',
            'receipt_type_full':'TRANSFER','memo_text':'TRANSFER FROM EXAMPLE JFC'}),
            'transfer_receipt')
        self.assertEqual(receipt_role({'filing_form':'F3X','line_number':'17',
            'receipt_type_full':'REVERSAL OF EXAMPLE CONTRIBUTION','memo_text':'CONTRIBUTION'}),
            'unverified_contradicted_receipt')
        self.assertEqual(receipt_role({'filing_form':'F3X','line_number':'17',
            'receipt_type_full':'CONTRIBUTION','memo_text':'BANK FEE REFUND'}),
            'unverified_contradicted_receipt')


    def test_ambiguity_counts_and_only_contradiction_refuses(self):
        """2026-09-17 ruling, mirrored from mentat/R/collect.R."""
        role = receipt_role
        # exact non-giving captions stay excluded
        for description in ('NON-CONTRIBUTION', 'INTEREST', 'SERVICE FEE'):
            self.assertEqual(role({'filing_form':'F3X','line_number':'17',
                'receipt_type_full':description}), 'other_receipt')
        # redesignations, bank artifacts and fair-market-value sales
        for description in ('CONTRIBUTION REDESIGNATION FROM GENERAL',
                'REDESIGNATION TO RECOUNT', 'NON CONFORMING IMAGE CHECK REDEPOSIT',
                'NON CONFIRMING CHECK REDEPOSIT', 'ACH FAILURE TO SETTLE RETURN',
                'DUPLICATE TRANSACTION SENT IN ERROR RETURNED ON 3/13/25',
                'STALE CHECK', 'MISDIRECTED WIRE REFUND', 'CREDIT',
                'OVERPAYMENT OF PAYROLL TO BE CORRECTED IN AUGUST',
                'REFUND FOR WORKERS COMPENSATION INSURANCE',
                'VOTER FILE PURCHASE AT FAIR MARKET VALUE'):
            with self.subTest(description=description):
                self.assertEqual(role({'filing_form':'F3X','line_number':'17',
                    'receipt_type_full':description}), 'other_receipt')
        # a redesignation split across the two fields is one claim, not a conflict
        self.assertEqual(role({'filing_form':'F3','line_number':'15',
            'receipt_type_full':'CONTRIBUTION','memo_text':'REDESIGNATION FROM GENERAL'}),
            'other_receipt')
        # special accounts the v5 ruleset now names
        for description in ('RECOUNT LEGAL FUND CONTRIBUTION', 'LEGAL FUND CONTRIBUTION',
                'HEADQUARTERS BUILDING FUND CONTRIBUTION', 'IE ONLY ACCOUNT CONTRIBUTION'):
            with self.subTest(description=description):
                self.assertEqual(role({'filing_form':'F3X','line_number':'17',
                    'receipt_type_full':description}), 'contribution')
        self.assertEqual(role({'filing_form':'F3X','line_number':'17',
            'receipt_type_full':'NON-FEDERAL CONTRIBUTION TRANSFERRED FROM STATE ACCOUNT'}),
            'transfer_receipt')
        # F13 inaugural donations count
        self.assertEqual(role({'filing_form':'F13','line_number':'F132'}), 'contribution')
        # a blank line number is recovered from the transaction id prefix
        self.assertEqual(role({'filing_form':'F3X','line_number':'',
            'transaction_id':'SA17.621125'}), 'unverified_other_receipt')
        self.assertEqual(role({'filing_form':'F3X','line_number':'',
            'transaction_id':'SA11AI.42'}), 'contribution')
        self.assertEqual(role({'filing_form':'F3X','line_number':'',
            'transaction_id':'E966384FC6024738878E'}), 'unverified_receipt_role')
        # a blank description on an other-receipts line stays unresolved (counted
        # and flagged by the R gate), it is never a contradiction
        for form, line in (('F3X','17'), ('F3','15'), ('F3P','21'), ('F4','19A')):
            with self.subTest(form=form):
                self.assertEqual(role({'filing_form':form,'line_number':line,
                    'receipt_type_full':'','memo_text':''}), 'unverified_other_receipt')
        # a single row can never prove a repeat, so the twin reports the unpaired
        # verdict that collect.R uses when the payload holds no counterpart
        self.assertEqual(role({'filing_form':'F3X','line_number':'17','memo_code':'X',
            'receipt_type_full':'24,999,995 USDC CONTRIBUTION PREVIOUSLY DISCLOSED'}),
            'unverified_disclosure_memo')
        # the sale wording added in v5
        self.assertEqual(role({'filing_form':'F3X','line_number':'17',
            'receipt_type_full':'5,000 ETH CONVERTED BY EXCHANGE - UNKNOWN PURCHASER'}),
            'asset_liquidation')

    def test_count_cursor_integrity_and_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []
            def transport(endpoint, params):
                calls.append(params)
                i = len(calls)
                return {'results':[{'sub_id':str(i)}], 'pagination':{'count':2, 'is_count_exact':True, 'last_indexes':{'last_index':str(i)}}}
            client = Client(directory, key='fixture-secret', transport=transport)
            self.assertEqual(len(client.rows('schedules/schedule_a/', {})), 2)
            self.assertEqual(calls[1]['last_index'], '1')
            self.assertEqual(len(client.rows('schedules/schedule_a/', {})), 2)
            self.assertEqual(len(calls), 2)
            self.assertNotIn('fixture-secret', ''.join(p.read_text() for p in Path(directory).glob('*.json')))

    def test_incomplete_pagination_does_not_publish_success(self):
        with tempfile.TemporaryDirectory() as directory:
            client = Client(directory, key='fixture', transport=lambda *_: {'results':[{'sub_id':'1'}], 'pagination':{'count':2,'is_count_exact':True,'last_indexes':{}}})
            with self.assertRaisesRegex(ValueError, 'Incomplete'):
                client.rows('schedules/schedule_a/', {})

    def test_company_funding_personal_money_and_committee_activity_stay_separate(self):
        snapshot = {'committee_id':'C00000001','cycle':2024,'totals':{'independent_expenditures':100},
                    'receipts':[{'sub_id':'1','contributor_name':'Acme', 'filing_form':'F3X','line_number':'11AI','contribution_receipt_amount':50},
                                {'sub_id':'2','contributor_name':'CEO, JANE', 'filing_form':'F3X','line_number':'11AI','contribution_receipt_amount':100}],
                    'disbursements':[], 'expenditures':[{'sub_id':'3','expenditure_amount':100,'candidate_id':'H00000001','candidate_name':'DOE, JANE','support_oppose_indicator':'O',
                                                        'expenditure_date':'2024-10-01','image_number':'202410019999999999','payee_name':'EXAMPLE MEDIA'}]}
        result = summarize([snapshot], 'Acme', [])
        self.assertEqual(len(result['receipts']), 1)
        self.assertEqual(result['candidate_activity'][0]['direction'], 'Oppose')
        self.assertIsNone(result['candidate_activity'][0]['company_attributed_amount'])
        filing = result['candidate_activity'][0]['filings'][0]
        self.assertEqual(filing['amount'], 100)
        self.assertEqual(filing['payee'], 'EXAMPLE MEDIA')
        self.assertEqual(filing['source_url'], 'https://docquery.fec.gov/cgi-bin/fecimg/?202410019999999999')
        self.assertTrue(result['committee_finances'][0]['regular_ie_reconciles'])

    def test_unknown_receipt_role_retains_amount_and_missing_summary_is_not_zero(self):
        snapshot={'committee_id':'C00000001','cycle':2024,'totals':{},
                  'receipts':[{'sub_id':'1','contributor_name':'Acme','contribution_receipt_amount':100}],
                  'disbursements':[], 'expenditures':[{'sub_id':'2','expenditure_amount':20,'memoed_subtotal':'false'}]}
        result=summarize([snapshot],'Acme',[])
        self.assertTrue(result['receipts'][0]['included_company_contribution'])
        self.assertTrue(result['receipts'][0]['receipt_role_unverified'])
        self.assertEqual(result['receipts'][0]['amount'],100)
        self.assertEqual(result['committee_finances'][0]['regular_ie_total'],20)
        self.assertIsNone(result['committee_finances'][0]['regular_ie_reconciles'])
        snapshot['expenditures'][0]['expenditure_amount']=None
        with self.assertRaises(ValueError):
            summarize([snapshot],'Acme',[])

    def test_dates_block_retroactive_spending_and_unliquidated_assets(self):
        receipt = {'sub_id':'late', 'company':'Acme', 'committee_id':'C1', 'cycle':2024,
                   'included_company_contribution':True, 'date':'2024-12-20', 'amount':23000000, 'role':'contribution'}
        activity = {'cycle':2024,'committee_id':'C2','candidate_id_as_filed':'H00000001','candidate_name_as_filed':'DOE, JANE',
                    'direction':'Support','committee_spending':100,'first_date':'2024-10-01','last_date':'2024-10-31'}
        evidence = {'receipts':[receipt], 'transfers':[{'cycle':2024,'source':'C1','destination':'C2','date':'2024-09-01','amount':100}],
                    'candidate_activity':[activity]}
        self.assertEqual(temporal_routes(evidence)[0]['status'], 'no_dated_route_in_observed_scope')
        receipt['date'] = '2024-08-01'
        self.assertEqual(temporal_routes(evidence)[0]['path'], ['C1','C2'])
        self.assertIsNone(temporal_routes(evidence)[0]['company_attributed_amount'])
        receipt['role'] = 'in_kind_contribution'
        self.assertEqual(temporal_routes(evidence)[0]['status'], 'asset_liquidation_link_required')


def disbursement(sub, line, amount, code=None, recipient='', **fields):
    return {'sub_id': sub, 'line_number': line, 'disbursement_type': code,
            'recipient_committee_id': recipient, 'disbursement_amount': amount,
            'disbursement_date': '2024-05-0' + sub[-1], **fields}


class TransferRuleTests(unittest.TestCase):
    """F8 (Paul, 2026-09-25): "one rule, by transaction type, everywhere"."""

    ROWS = {'C00000001': [
        disbursement('1', '21B', 3000, '24K', 'C00000002'),   # a 24K filed on line 21B
        disbursement('2', '23', 500, '24Z', 'C00000003'),     # in-kind on line 23: no transfer
        disbursement('3', '22', 700, None, 'C00000004'),      # no type: the old line rule decides
        disbursement('4', '21B', 80, None, '', recipient_name='VENDOR'),
        disbursement('5', '23', 40, '24C', 'C00000005', candidate_id='H0CA00001', candidate_name='ONE')]}

    def test_the_collector_follows_transfers_by_transaction_type(self):
        rows = self.ROWS

        class Client:
            def rows(self, endpoint, params):
                return list(rows.get(params['committee_id'], [])) if 'schedule_b' in endpoint else []

            def page(self, endpoint, params):
                return {'results': [{'disbursements': 0}]}

        snapshots, missing = collect_network(Client(), [('C00000001', 2024)], max_units=10)
        self.assertEqual(sorted(unit['committee_id'] for unit in snapshots),
                         ['C00000001', 'C00000002', 'C00000004'])
        self.assertEqual(missing, [])

    def test_each_leg_is_named_by_type_and_the_fallback_is_counted(self):
        snapshot = {'committee_id': 'C00000001', 'cycle': 2024, 'totals': {}, 'receipts': [],
                    'expenditures': [], 'disbursements': self.ROWS['C00000001']}
        result = summarize([snapshot], 'Acme', [])
        legs = {leg['destination']: leg.get('transfer_basis') for leg in result['transfers']}
        self.assertEqual(legs, {'C00000002': 'transaction_type', 'C00000004': 'line_fallback',
                                'C00000005': 'transaction_type'})
        self.assertEqual([row['candidate_id_as_filed'] for row in result['candidate_activity']], ['H0CA00001'])
        rule = result['transfer_rule']
        self.assertEqual(rule['transaction_types'], ['24G', '24K'])
        self.assertEqual(rule['rows_without_transaction_type'], 2)
        self.assertEqual(rule['legs_by_line_fallback'], 1)
        self.assertEqual(rule['amount_by_line_fallback'], 700)
        self.assertEqual(rule['by_committee'], [{'committee_id': 'C00000001', 'cycle': 2024,
                                                 'rows_without_transaction_type': 2,
                                                 'legs_by_line_fallback': 1, 'amount_by_line_fallback': 700.0}])


class ReloadRuleTests(unittest.TestCase):
    """F11 (Paul, 2026-09-25): a reload differing only in SUB_ID counts once."""

    def snapshot(self):
        row = disbursement('1', '22', 700, '24G', 'C00000004', report_type='M6', transaction_id='SB22.1',
                           file_number='9')
        gift = {'sub_id': 'e1', 'expenditure_amount': 50, 'candidate_id': 'H00000001', 'candidate_name': 'DOE, JANE',
                'support_oppose_indicator': 'S', 'expenditure_date': '2024-10-01', 'report_type': 'M10',
                'transaction_id': 'SE.1', 'file_number': '9', 'payee_name': 'MEDIA', 'is_notice': False}
        return {'committee_id': 'C00000001', 'cycle': 2024, 'totals': {'independent_expenditures': 50},
                'receipts': [],
                'expenditures': [gift, {**gift, 'sub_id': 'e2'}],
                'disbursements': [row, {**row, 'sub_id': '2'}, {**row, 'sub_id': '3', 'transaction_id': 'SB22.2'},
                                  {**row, 'sub_id': '4', 'transaction_id': ''}, {**row, 'sub_id': '5', 'transaction_id': ''}]}

    def test_the_collector_counts_a_reload_once_and_a_distinct_transaction_twice(self):
        result = summarize([self.snapshot()], 'Acme', [])
        self.assertEqual(sorted(leg['sub_id'] for leg in result['transfers']), ['1', '3', '4', '5'])
        self.assertEqual([row['committee_spending'] for row in result['candidate_activity']], [50])
        self.assertTrue(result['committee_finances'][0]['regular_ie_reconciles'])
        reload = result['reload_rule']
        self.assertEqual(reload['key'], 'committee|report type|transaction id|file number|counterparty|amount|date|type')
        self.assertEqual((reload['reload_rows'], reload['reload_amount'],
                          reload['rows_without_transaction_id_or_file_number']), (2, 750.0, 2))


if __name__ == '__main__':
    unittest.main()

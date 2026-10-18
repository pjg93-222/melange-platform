"""Candidate campaign spending (FEC Schedule B): the source adapter, the classifier, the money rules and the route.

Every figure is worked by hand. Scenario rows come from Andy Barr's 2023-24 committee (ANDY BARR FOR CONGRESS,
INC.) as Paul named them: WinRed CC TRANSACTION FEES, The Stanton Group FUNDRAISING CONSULTING, Payroll Solutions
PAYROLL TAXES / SEE MEMOS, American Express SEE MEMOS, Delta TRAVEL EXPENSE, Accurate Word PRINTING, Professional Data
Services COMPLIANCE CONSULTING, Republican Party of Fayette County EVENT SPONSOR and a VOID of -$1,500, refunds to
donors, and IN-KIND: FOOD, BEVERAGE & EVENT SPACE.

The source is the crawler's reader (tools/committee_activity_reader.py, not in this checkout); every test feeds the
adapter results in the reader's exact shape through MENTAT_CANDIDATE_SPENDING_FIXTURE or by calling combine() directly.
"""
import http.client
import importlib.util
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / 'app'))
sys.path.insert(0, str(HERE / 'tools'))
import candidate_spending as cs  # noqa: E402

FIXTURES = HERE / 'tests' / 'fixtures' / 'candidate_spending'
CRAWLER = FIXTURES / 'crawler'
CRAWLER_FIXTURE = CRAWLER / 'read_campaign_spending_fixture.json'      # copied unchanged from the crawler lane (see the README beside it)
CRAWLER_SCHEMA = CRAWLER / 'read_campaign_spending_fixture.schema.json'
CRAWLER_SHA256 = {CRAWLER_FIXTURE.name: 'c6a51fe6018691372f2cc427d0a37523003f22263fc91202990fc3eae49523f7',
                  CRAWLER_SCHEMA.name: 'def754dc60bd42a0323cf0ab56699db5fe76e52345854ef958caf6a8e969cc05'}
BARR = 'BARR, GARLAND ANDY'
COMMITTEE = 'C00467571'


def row(name, text, amount, tid='', line='17', purpose='OTHER', memo=None, back=None, entity='ORG', committee=None, date='2024-03-01', sub=None):
    key = abs(hash((name, text, amount, date)))
    return {'committee_id': COMMITTEE, 'transaction_id': tid or f'T{key % 10**8}', 'sub_id': sub or tid or f'S{key % 10**8}',
            'disbursement_date': date, 'disbursement_amount': amount, 'disbursement_description': text,
            'disbursement_purpose_category': purpose, 'memo_code': memo, 'memo_text': None,
            'back_reference_transaction_id': back, 'entity_type': entity, 'recipient_committee_id': committee,
            'line_number': line, 'recipient_name': name, 'recipient_state': 'KY',
            'image_number': '202501319748746337', 'pdf_url': 'https://docquery.fec.gov/cgi-bin/fecimg/?202501319748746337'}


def totals(disbursements, committee=COMMITTEE, cycle=2024, loans=0.0, transfers=0.0, end='2024-12-31T00:00:00'):
    return {'committee_id': committee, 'cycle': cycle, 'disbursements': disbursements, 'loan_repayments': loans,
            'transfers_to_other_authorized_committee': transfers, 'coverage_end_date': end}


def committee(rows, disbursements='sum', committee_id=COMMITTEE, status='complete', name='ANDY BARR FOR CONGRESS, INC.', **extra):
    if disbursements == 'sum':
        disbursements = sum(r['disbursement_amount'] for r in rows if r['memo_code'] != 'X')
    return {'committee_id': committee_id, 'committee_name': name, 'cycle': 2024, 'coverage_status': status,
            'endpoint_status': {'B': 'complete', 'totals': 'complete'} if status == 'complete' else {'B': 'pending', 'totals': 'pending'},
            'retrieved_at': {'B_latest': '2026-10-15T12:00:00Z', 'totals': '2026-10-15T12:01:00Z'},
            'totals': [] if disbursements is None else [totals(disbursements, committee_id, **extra)], 'rows': rows,
            'row_occurrences': len(rows), 'disclosure_boundaries': 0}


def result(*committees, candidate='H0KY06104'):
    ids = [item['committee_id'] for item in committees]
    return {'query': {'cycle': 2024, 'candidate_id': candidate, 'committee_id': None}, 'snapshot': {'batch': 3, 'sha256': 'ab' * 32},
            'mapping': {'candidate_id': candidate, 'source': 'bulk_ccl', 'vintage_or_retrieved_at': '2026-07-22', 'committee_ids': ids},
            'committees': list(committees)}


def campaign(rows, **kwargs):
    return cs.combine(result(committee(rows, **kwargs)), 2024)


def numbers(rows, hint=BARR, detail=False, **kwargs):
    return cs.breakdown(campaign(rows, **kwargs), hint, detail=detail)


def amounts(body):
    return {item['key']: item['amount'] for item in body['categories']}


class Classifier(unittest.TestCase):
    TABLE = [
        # (payee, description, expected, extra row fields)
        ('WINRED TECHNICAL SERVICES, LLC', 'CC TRANSACTION FEES', 'fundraising_fees', {}),
        ('THE STANTON GROUP, LLC', 'FUNDRAISING CONSULTING', 'consulting', {}),
        ('PAYROLL SOLUTIONS', 'PAYROLL TAXES / SEE MEMOS', 'payroll', {}),
        ('PAYROLL SOLUTIONS', 'PAYROLL EXPENSE', 'payroll', {}),
        ('AMERICAN EXPRESS', 'SEE MEMOS', 'other', {}),
        ('DELTA AIRLINES', 'TRAVEL EXPENSE', 'travel', {}),
        ('ACCURATE WORD LLC', 'PRINTING', 'printing_mail', {}),
        ('PROFESSIONAL DATA SERVICES', 'COMPLIANCE CONSULTING', 'legal_compliance', {}),     # not consulting
        ('REPUBLICAN PARTY OF FAYETTE COUNTY', 'EVENT SPONSOR', 'contributions_transfers', {}),  # not events
        ('NRCC', 'CONTRIBUTION', 'contributions_transfers', {}),
        ('DONOR, SAMPLE A', 'REFUND', 'refunds', {'line': '20A', 'entity': 'IND'}),
        ('SUPPORTER, SAMPLE A', 'IN-KIND:FOOD, BEVERAGE, & EVENT SPACE', 'events', {}),
        ('SUPPORTER, SAMPLE A', 'IN-KIND: FOOD, BEVERAGE & EVENT SPACE', 'events', {}),
        ('LEXINGTON MINI STORAGE', 'RENT', 'office', {}),
        ('AMERICAN TRUST', 'BANK FEE', 'office', {}),                       # a bank fee is not a processing fee
        ('UPONE INSIGHTS', 'POLLING EXPENSE', 'polling_data', {}),
        ('SOMETHING ELSE STRATEGIES, LLC', 'MEDIA PRODUCTION', 'advertising', {}),
        ('SEYMOUR & PERRY, LLC', 'TAX PREPARATION EXPENSE', 'legal_compliance', {'purpose': 'MATERIALS'}),
        ('LILLY & COMPANY', 'GRAPHIC DESIGN', 'advertising', {}),
        ('BARR, GARLAND ANDY', 'CHILD CARE REIMBURSEMENT', 'personal_reimb', {'entity': 'IND'}),
        ('BARR, GARLAND ANDY', 'TRAVEL EXPENSE REIMBURSEMENT', 'personal_reimb', {'entity': 'IND'}),
        ('ANDY BARR VICTORY COMMITTEE', 'TRANSFER', 'contributions_transfers', {'committee': 'C00123456'}),
        ('ACTBLUE', 'ACTBLUE PROCESSING FEES', 'fundraising_fees', {'committee': 'C00401224'}),   # a fee before the committee signal
        ('ARISTOTLE INTERNATIONAL', 'DATABASE SERVICES', 'polling_data', {}),
        ('THE MAIL HAUS', 'POSTAGE', 'printing_mail', {}),
        ('WHATEVER LLC', 'MYSTERY ITEM', 'other', {}),
        ('WHATEVER LLC', 'MYSTERY ITEM', 'travel', {'purpose': 'TRAVEL'}),     # FEC's purpose category is the fallback
        ('WHATEVER LLC', '', 'other', {}),
        # Review fix S3: each of these used to land in the wrong category
        ('SAMPLE VENDOR', 'STAFF TRAVEL REIMBURSEMENT', 'travel', {}),                # STAFF is a weak payroll word
        ('SAMPLE VENDOR', 'STAFF MILEAGE', 'travel', {}),
        ('SAMPLE VENDOR', 'MEALS - STAFF', 'events', {}),
        ('SAMPLE VENDOR', 'EVENT STAFFING', 'events', {}),
        ('SAMPLE VENDOR', 'STAFFING AGENCY', 'payroll', {}),                          # STAFF alone is still payroll
        ('SAMPLE VENDOR', 'VAN RENTAL', 'travel', {}),                                # not the voter file
        ('NGP VAN', 'VAN', 'polling_data', {}),                                       # a bare VAN is
        ('SAMPLE VENDOR', 'DATA PLAN - CELL PHONE', 'office', {}),                    # not data
        ('SAMPLE VENDOR', 'CABLE/INTERNET', 'office', {}),                            # not cable ads
        ('SAMPLE VENDOR', 'CABLE TV ADS', 'advertising', {}),
        ('SAMPLE VENDOR', 'GOOGLE WORKSPACE', 'office', {}),                          # not Google ads
        ('SAMPLE VENDOR', 'GOOGLE ADS', 'advertising', {}),
        ('SAMPLE VENDOR', 'WEBSITE DESIGN', 'advertising', {}),                       # documented: design is digital advertising
        ('SAMPLE VENDOR', 'WEBSITE HOSTING', 'office', {}),                           # hosting and domains are office
        ('SAMPLE VENDOR', 'PRINTING AND DESIGN', 'printing_mail', {}),                # not design
        ('SAMPLE VENDOR', 'PRINT ADS', 'advertising', {}),
        ('SAMPLE VENDOR', 'PAYMENT FOR LEGAL FEES', 'legal_compliance', {}),          # not a processing fee
        ('SAMPLE VENDOR', 'PAYMENT PROCESSING FEES', 'fundraising_fees', {}),
        # a vendor whose name has CAMPAIGN / COMMITTEE / VICTORY is a committee only when the filing says so
        ('CAMPAIGN PROMOTIONS INC', 'EVENT SPONSOR', 'events', {}),
        ('VICTORY ENTERPRISES', 'EVENT SPONSOR', 'events', {}),
        ('THE COMMITTEE GROUP', 'EVENT TICKETS', 'events', {}),
        ('VICTORY FUND', 'EVENT SPONSOR', 'contributions_transfers', {'entity': 'COM'}),
        ('CAMPAIGN PROMOTIONS INC', 'EVENT SPONSOR', 'contributions_transfers', {'committee': 'C00123456'}),
    ]

    def test_paul_rows_and_the_ordering_rules(self):
        for payee, description, expected, extra in self.TABLE:
            with self.subTest(payee=payee, description=description):
                self.assertEqual(cs.classify(row(payee, description, 100, **extra), BARR), expected)

    NAMES = [
        # (payee, entity_type, description, candidate as filed, expected)
        ('BARR, GARLAND ANDY', 'IND', 'CHILD CARE REIMBURSEMENT', BARR, 'personal_reimb'),
        ('BARR, ANDY', 'IND', 'MILEAGE', BARR, 'personal_reimb'),                       # the nickname the filed name carries
        ('BARR, GARLAND', 'IND', 'TRAVEL REIMBURSEMENT', BARR, 'personal_reimb'),
        ('BARR, ANDY JR', 'IND', 'CHILD CARE REIMBURSEMENT', BARR, 'personal_reimb'),   # JR is the only difference
        ('BARR, ANDY JR', 'ORG', 'CHILD CARE REIMBURSEMENT', BARR, 'other'),            # ... and only for an individual
        ('BARR, ANDREW JR', 'IND', 'CHILD CARE REIMBURSEMENT', BARR, 'other'),          # a different given name
        ('BARR, GARLAND ANDY', 'IND', 'CHILD CARE REIMBURSEMENT', 'ANDY BARR', 'personal_reimb'),    # the committee-name hint
        ('BARR, GARLAND ANDY', 'IND', 'SALARY', BARR, 'payroll'),                       # paid for work, not reimbursed
        ('SMITH, JOHN', 'IND', 'SALARY', 'SMITH, JOHN ROBERT', 'payroll'),              # a namesake staffer on salary
        ('SMITH, JOHN', 'IND', 'MILEAGE REIMBURSEMENT', 'SMITH, JOHN ROBERT', 'personal_reimb'),   # documented: cannot be told apart
        ('SMITH, JOHN WILLIAM', 'IND', 'MILEAGE REIMBURSEMENT', 'SMITH, JOHN ROBERT', 'travel'),   # a different middle name
        ('FRIENDS OF JOHN Q SMITH', 'IND', 'REIMBURSEMENT', 'SMITH, MARY Q', 'other'),
        ('FRIENDS OF JOHN Q SMITH', 'IND', 'REIMBURSEMENT', 'SMITH, JOHN Q', 'other'),    # an organisation never is the person
        ('ANDY BARR GRILL', 'IND', 'TRAVEL REIMBURSEMENT', BARR, 'travel'),
        ('ANDY BARR GRILL', 'ORG', 'MEALS', BARR, 'events'),
        ('BARR, ANDY', 'IND', 'CHILD CARE REIMBURSEMENT', 'JONES, ANDY', 'other'),      # same first name, other surname
    ]

    def test_the_candidate_name_match_is_strict_and_needs_a_reimbursement_purpose(self):
        for payee, entity, description, candidate, expected in self.NAMES:
            with self.subTest(payee=payee, entity=entity, description=description, candidate=candidate):
                self.assertEqual(cs.classify(row(payee, description, 100, entity=entity), candidate), expected)

    def test_suffix_words_and_initials_neither_make_nor_break_a_name(self):
        self.assertEqual(cs._name_parts('BARR, GARLAND ANDY JR.'), (['BARR'], ['GARLAND', 'ANDY']))
        self.assertEqual(cs._name_parts('REP. ANDY Q BARR III'), (['BARR'], ['ANDY']))
        self.assertEqual(cs._name_parts('JOHN Q SMITH'), (['SMITH'], ['JOHN']))

    def test_a_campaign_paying_itself_is_a_transfer_not_a_reimbursement(self):
        self.assertEqual(cs.classify(row('ANDY BARR FOR CONGRESS', 'REIMBURSEMENT', 10, committee='C00467571'), BARR), 'contributions_transfers')

    def test_without_the_candidates_name_the_committees_stands_in(self):
        self.assertEqual(cs.candidate_hint('ANDY BARR FOR CONGRESS, INC. + ANDY BARR VICTORY'), 'ANDY BARR')
        body = numbers([row('BARR, GARLAND ANDY', 'CHILD CARE REIMBURSEMENT', 120.0, entity='IND')], hint=None)
        self.assertEqual(amounts(body), {'personal_reimb': 120.0})

    def test_the_table_is_deterministic(self):
        sample = row('THE STANTON GROUP, LLC', 'FUNDRAISING CONSULTING', 5)
        self.assertEqual({cs.classify(sample, BARR) for _ in range(5)}, {'consulting'})


class MemoParents(unittest.TestCase):
    def rows(self, child_total):
        parent = row('PAYROLL SOLUTIONS', 'SEE MEMOS', 1000.00, tid='P1')
        kids = [row('STAFFER, SAMPLE E', 'PAYROLL EXPENSE', 600.00, tid='K1', memo='X', back='P1', entity='IND'),
                row('PAYROLL SOLUTIONS', 'PAYROLL TAXES', child_total - 600.00, tid='K2', memo='X', back='P1')]
        return [parent, *kids, row('DELTA AIRLINES', 'TRAVEL EXPENSE', 50.00)]

    def test_children_that_sum_to_the_parent_are_classified_and_the_parent_is_not_counted_twice(self):
        body = numbers(self.rows(1000.00))
        self.assertEqual(amounts(body), {'payroll': 1000.0, 'travel': 50.0})
        self.assertEqual(body['total_itemized'], 1050.0)            # the parent once, never parent plus children
        self.assertEqual(body['memo_handling'], {'parents': 1, 'resolved': 1, 'unresolved': 0, 'children_used': 2,
                                                  'unresolved_amount': 0.0, 'memo_rows': 2, 'memo_rows_not_counted': 0})

    def test_within_a_dollar_resolves_and_the_difference_stays_in_the_total(self):
        body = numbers(self.rows(999.20))                           # $0.80 short: within $1.00
        self.assertEqual(body['memo_handling']['resolved'], 1)
        self.assertEqual(body['total_itemized'], 1050.0)
        self.assertAlmostEqual(sum(amounts(body).values()), 1050.0, places=2)   # categories still add up to the parent

    def test_more_than_a_dollar_off_stays_one_unitemized_parent_in_other(self):
        body = numbers(self.rows(998.50))                           # $1.50 short
        self.assertEqual(amounts(body), {'other': 1000.0, 'travel': 50.0})
        self.assertEqual(body['memo_handling']['unresolved'], 1)
        self.assertEqual(body['memo_handling']['unresolved_amount'], 1000.0)
        self.assertEqual(body['memo_handling']['memo_rows_not_counted'], 2)
        self.assertEqual(body['other_top_descriptions'][0]['description'], cs.UNITEMIZED_PARENT)
        self.assertEqual(body['total_itemized'], 1050.0)

    def test_memo_rows_alone_are_never_totals(self):
        orphan = row('SOMEONE', 'TRAVEL EXPENSE', 77.00, memo='X', back='NOPE')
        body = numbers([orphan, row('DELTA AIRLINES', 'TRAVEL EXPENSE', 50.00)])
        self.assertEqual(body['total_itemized'], 50.0)

    def test_a_parent_that_is_not_a_see_memos_row_keeps_its_own_words(self):
        parent = row('U.S. DEPARTMENT OF THE TREASURY', 'DISGORGED FUNDS', 100.0, tid='D1')
        kid = row('DONOR, ONE', 'REFUND', 100.0, tid='D2', memo='X', back='D1')
        body = numbers([parent, kid])
        self.assertEqual(amounts(body), {'other': 100.0})
        self.assertEqual(body['memo_handling']['parents'], 0)


    def test_two_parents_sharing_a_transaction_id_do_not_claim_each_others_children(self):
        def parent(name, amount, **extra):
            return dict(row(name, 'SEE MEMOS', amount, tid='SHARED', sub=f'P-{name}-{amount}'), **extra)

        def child(name, text, amount, **extra):
            return dict(row(name, text, amount, tid=f'K-{name}-{amount}', memo='X', back='SHARED', sub=f'K-{name}-{amount}'), **extra)
        # two committees of one campaign
        a = committee([parent('AMERICAN EXPRESS', 1000.00), child('DELTA AIRLINES', 'TRAVEL EXPENSE', 1000.00)],
                      committee_id='C90000101', name='ZED FOR CONGRESS')
        b = committee([parent('PAYROLL SOLUTIONS', 500.00, committee_id='C90000102'),
                       child('PAYROLL SOLUTIONS', 'PAYROLL TAXES', 500.00, committee_id='C90000102')],
                      committee_id='C90000102', name='ZED FOR CONGRESS II')
        for item in (a, b):
            for each in item['rows']:
                each['committee_id'] = item['committee_id']
        body = cs.breakdown(cs.combine(result(a, b), 2024), 'ZED, ZACHARY')
        self.assertEqual(body['memo_handling']['resolved'], 2)
        self.assertEqual(body['memo_handling']['unresolved'], 0)
        self.assertEqual(amounts(body), {'travel': 1000.0, 'payroll': 500.0})
        # one committee, two filings
        rows = [parent('AMERICAN EXPRESS', 1000.00, file_number='1001'), child('DELTA AIRLINES', 'TRAVEL EXPENSE', 1000.00, file_number='1001'),
                parent('PAYROLL SOLUTIONS', 500.00, file_number='1002'), child('PAYROLL SOLUTIONS', 'PAYROLL TAXES', 500.00, file_number='1002')]
        body = numbers(rows)
        self.assertEqual((body['memo_handling']['resolved'], body['memo_handling']['unresolved']), (2, 0))
        self.assertEqual(amounts(body), {'travel': 1000.0, 'payroll': 500.0})
        # and the report (year and type) tells filings apart when there is no file number
        rows = [parent('AMERICAN EXPRESS', 1000.00, report_year=2024, report_type='Q1'), child('DELTA AIRLINES', 'TRAVEL EXPENSE', 1000.00, report_year=2024, report_type='Q1'),
                parent('PAYROLL SOLUTIONS', 500.00, report_year=2024, report_type='Q2'), child('PAYROLL SOLUTIONS', 'PAYROLL TAXES', 500.00, report_year=2024, report_type='Q2')]
        self.assertEqual(numbers(rows)['memo_handling']['resolved'], 2)


class Netting(unittest.TestCase):
    def test_a_void_nets_inside_the_category_of_the_row_it_undoes(self):
        rows = [row('REPUBLICAN PARTY OF FAYETTE COUNTY', 'EVENT SPONSOR', 1500.00, date='2024-02-01'),
                row('REPUBLICAN PARTY OF FAYETTE COUNTY', 'VOID OF PREVIOUS - REISSUED FOR CORRECTED AMOUNT', -1500.00, date='2024-03-01'),
                row('REPUBLICAN PARTY OF FAYETTE COUNTY', 'EVENT SPONSOR', 2500.00, date='2024-03-02')]
        body = numbers(rows)
        self.assertEqual(amounts(body), {'contributions_transfers': 2500.0})
        self.assertEqual(body['categories'][0]['payments'], 2)           # the void is not a payment
        self.assertEqual(body['total_itemized'], 2500.0)

    def test_a_void_with_no_original_in_the_data_falls_back_to_its_own_words(self):
        rows = [row('ELECTORAL STRATEGIES INC.', 'VOID OF PREVIOUS CK 10442 LOST', -2146.91),
                row('THE STANTON GROUP, LLC', 'FUNDRAISING CONSULTING', 5000.00)]
        self.assertEqual(numbers(rows)['total_itemized'], 2853.09)

    def test_a_category_that_nets_to_zero_or_less_is_dropped_and_listed(self):
        rows = [row('DELTA AIRLINES', 'TRAVEL EXPENSE', 300.00),
                row('DELTA AIRLINES', 'TRAVEL EXPENSE REFUNDED', -300.00),
                row('DELTA AIRLINES', 'TRAVEL EXPENSE', -50.00),
                row('THE STANTON GROUP, LLC', 'FUNDRAISING CONSULTING', 1000.00)]
        body = numbers(rows)
        self.assertEqual(amounts(body), {'consulting': 1000.0})
        self.assertEqual(body['dropped_negative'], [{'key': 'travel', 'label': 'Travel', 'amount': -50.0, 'payments': 1}])
        self.assertEqual(body['categories'][0]['share'], 1.0)           # shares are over what the ring draws
        self.assertEqual(body['total_itemized'], 950.0)                  # the net of everything itemized


    def test_a_category_that_nets_to_exactly_zero_is_dropped_too(self):
        rows = [row('DELTA AIRLINES', 'TRAVEL EXPENSE', 300.00),
                row('DELTA AIRLINES', 'TRAVEL EXPENSE REFUNDED', -300.00),
                row('THE STANTON GROUP, LLC', 'FUNDRAISING CONSULTING', 1000.00)]
        body = numbers(rows)
        self.assertEqual(amounts(body), {'consulting': 1000.0})                        # no zero-dollar slice
        self.assertEqual(body['dropped_negative'], [{'key': 'travel', 'label': 'Travel', 'amount': 0.0, 'payments': 1}])
        self.assertEqual(body['categories'][0]['share'], 1.0)


class Coverage(unittest.TestCase):
    def test_coverage_is_itemized_over_reported_and_never_scaled_up(self):
        rows = [row('THE STANTON GROUP, LLC', 'FUNDRAISING CONSULTING', 600.00), row('DELTA AIRLINES', 'TRAVEL EXPENSE', 200.00)]
        body = numbers(rows, disbursements=1000.00)
        self.assertEqual((body['total_itemized'], body['reported_total'], body['coverage']), (800.0, 1000.0, 0.8))
        self.assertEqual(sum(item['amount'] for item in body['categories']), 800.0)    # nothing was scaled to 1,000
        self.assertFalse(body['coverage_unknown'])

    def test_loan_repayments_and_transfers_to_authorized_committees_leave_both_sides(self):
        rows = [row('THE STANTON GROUP, LLC', 'FUNDRAISING CONSULTING', 600.00),
                row('ANDY BARR VICTORY COMMITTEE', 'TRANSFER', 300.00, line='18', committee='C00123456'),
                row('BARR, GARLAND ANDY', 'LOAN REPAYMENT', 100.00, line='19A', entity='IND')]
        body = numbers(rows, disbursements=1500.00, loans=100.00, transfers=300.00)
        self.assertEqual((body['total_itemized'], body['reported_total'], body['coverage']), (600.0, 1100.0, 0.5454))      # 0.54545 floored
        self.assertEqual(body['excluded']['transfers_to_authorized'], {'itemized': 300.0, 'reported': 300.0})
        self.assertEqual(body['excluded']['loan_repayments'], {'itemized': 100.0, 'reported': 100.0})
        self.assertEqual(amounts(body), {'consulting': 600.0})

    def test_no_totals_means_unknown_coverage_not_a_made_up_one(self):
        body = numbers([row('DELTA AIRLINES', 'TRAVEL EXPENSE', 5.00)], disbursements=None, status='partial')
        self.assertIsNone(body['coverage'])
        self.assertIsNone(body['reported_total'])
        self.assertTrue(body['coverage_unknown'])
        self.assertEqual(body['coverage_status'], 'partial')
        self.assertEqual(body['total_itemized'], 5.0)

    def test_coverage_is_floored_never_rounded_up(self):
        for itemized, expected in ((899.96, 0.8999), (999.96, 0.9999), (896.0, 0.896), (999.99, 0.9999), (1000.0, 1.0)):
            with self.subTest(itemized=itemized):
                body = numbers([row('DELTA AIRLINES', 'TRAVEL EXPENSE', itemized)], disbursements=1000.00)
                self.assertEqual(body['coverage'], expected)

    def test_coverage_is_never_clamped_to_one(self):
        body = numbers([row('DELTA AIRLINES', 'TRAVEL EXPENSE', 1000.00)], disbursements=800.00)       # more itemized than reported
        self.assertEqual((body['total_itemized'], body['reported_total'], body['coverage']), (1000.0, 800.0, 1.25))
        self.assertFalse(body['coverage_unknown'])

    def test_a_totals_field_that_is_missing_is_unknown_not_zero(self):
        rows = [row('DELTA AIRLINES', 'TRAVEL EXPENSE', 100.00)]
        for gone in cs.TOTALS_FIELDS:
            for how in ('absent', 'null', 'text'):
                with self.subTest(field=gone, how=how):
                    item = committee(rows, disbursements=1000.00)
                    if how == 'absent':
                        del item['totals'][0][gone]
                    else:
                        item['totals'][0][gone] = None if how == 'null' else 'n/a'
                    made = cs.combine(result(item), 2024)
                    body = cs.breakdown(made, BARR)
                    self.assertIsNone(body['reported_total'])
                    self.assertIsNone(body['coverage'])
                    self.assertTrue(body['coverage_unknown'])
                    if gone == 'disbursements' and how != 'text':                         # no totals object at all for the cycle
                        self.assertIsNone(made['totals'])
                        self.assertEqual(body['coverage_status'], 'partial')
                        continue
                    self.assertIsNone(made['totals'][gone])
                    self.assertEqual(body['coverage_status'], 'complete')                  # the reader's word stands
                    self.assertEqual(body['coverage_reasons'], [f'Totals lack {gone}, so what was reported is unknown'])
        whole = numbers(rows, disbursements=1000.00)                                         # all three present: known
        self.assertEqual((whole['reported_total'], whole['coverage'], whole['coverage_reasons']), (1000.0, 0.1, []))

    def test_a_presidential_campaign_draws_from_its_rows_and_never_claims_coverage(self):
        rows = [row('DELTA AIRLINES', 'TRAVEL EXPENSE', 1000.00, line='23'),
                row('PRESIDENT SECOND COMMITTEE', 'TRANSFER', 400.00, line='24'),                # no committee id: the line says it
                row('LENDER, SAMPLE A', 'LOAN REPAYMENT', 300.00, line='27A', entity='IND'),
                row('DONOR, SAMPLE A', 'CONTRIBUTION RETURN', 50.00, line='28A', entity='IND')]            # refunds by the line alone
        item = committee(rows, disbursements=1750.00, name='PRESIDENT FOR AMERICA')
        item['totals'][0].update(loan_repayments_made=300.0, transfers_to_affiliated=400.0)      # Form 3P names: never read
        del item['totals'][0]['loan_repayments']
        body = cs.breakdown(cs.combine(result(item, candidate='P80000722'), 2024), 'PRESIDENT, SAMPLE')
        self.assertEqual(amounts(body), {'travel': 1000.0, 'refunds': 50.0})
        self.assertEqual(body['total_itemized'], 1050.0)
        self.assertIsNone(body['coverage'])
        self.assertIsNone(body['reported_total'])
        self.assertTrue(body['coverage_unknown'])
        self.assertIn(cs.PRESIDENTIAL_NOTE, body['coverage_reasons'])
        self.assertEqual(body['excluded'], {'loan_repayments': {'itemized': 300.0, 'reported': None},
                                            'transfers_to_authorized': {'itemized': 400.0, 'reported': None}})
        # the same rows for a House candidate: lines 24, 27 and 28 mean nothing there, and coverage is worked out
        house = cs.breakdown(cs.combine(result(committee(rows, disbursements=1750.00), candidate='H0KY06104'), 2024), BARR)
        self.assertEqual(house['excluded']['loan_repayments']['itemized'], 0.0)
        self.assertNotIn('refunds', amounts(house))
        self.assertNotIn(cs.PRESIDENTIAL_NOTE, house['coverage_reasons'])
        self.assertEqual(house['coverage'], 1.0)

    def test_the_office_comes_from_the_candidate_id_when_the_reader_gives_no_mapping(self):
        with tempfile.TemporaryDirectory() as directory:
            unmapped = result(committee([row('DELTA AIRLINES', 'TRAVEL EXPENSE', 5.0)]), candidate='P80000722')
            unmapped['mapping'] = None
            unmapped['query']['candidate_id'] = None
            (Path(directory) / 'P80000722-2024.json').write_text(json.dumps(unmapped), encoding='utf-8')
            with patch.dict(os.environ, {cs.FIXTURE_ENV: directory}):
                self.assertEqual(cs.load_campaign_spending('P80000722', 2024)['office'], 'P')

    def test_totals_are_the_object_for_the_cycle_and_the_latest_when_there_are_several(self):
        item = committee([row('DELTA AIRLINES', 'TRAVEL EXPENSE', 10.0)])
        item['totals'] = [totals(999.0, cycle=2022), totals(100.0, end='2024-06-30T00:00:00'), totals(250.0, end='2024-12-31T00:00:00')]
        self.assertEqual(cs.combine(result(item), 2024)['totals']['disbursements'], 25000)       # cents
        item['totals'] = [totals(999.0, cycle=2022)]
        self.assertIsNone(cs.combine(result(item), 2024)['totals'])


class Source(unittest.TestCase):
    def test_rows_are_deduplicated_by_sub_id_before_anything_else_and_the_count_is_kept(self):
        first = row('THE STANTON GROUP, LLC', 'FUNDRAISING CONSULTING', 600.00, sub='S1')
        rows = [first, dict(first), row('DELTA AIRLINES', 'TRAVEL EXPENSE', 200.00, sub='S2')]
        body = numbers(rows, disbursements=800.00)
        self.assertEqual(body['total_itemized'], 800.0)
        self.assertEqual((body['source']['row_occurrences'], body['source']['duplicate_rows_dropped'], body['source']['rows']), (3, 1, 2))
        self.assertEqual(body['coverage'], 1.0)

    def test_two_committees_are_combined_and_transfers_between_them_are_counted_once_and_reported(self):
        a = committee([row('PEAK', 'FUNDRAISING CONSULTING', 1000.00, sub='A1'),
                       row('ZED FOR CONGRESS II', 'TRANSFER', 400.00, line='18', committee='C90000102', sub='A2')],
                      disbursements=1400.00, committee_id='C90000101', name='ZED FOR CONGRESS', transfers=400.00)
        b = committee([row('KENTUCKY TV', 'TV ADS', 300.00, sub='B1'),
                       row('ZED FOR CONGRESS', 'REIMBURSEMENT', 50.00, line='21', committee='C90000101', sub='B2')],
                      disbursements=350.00, committee_id='C90000102', name='ZED FOR CONGRESS II')
        body = cs.breakdown(cs.combine(result(a, b), 2024), 'ZED, ZACHARY')
        self.assertEqual(amounts(body), {'consulting': 1000.0, 'advertising': 300.0})
        self.assertEqual(body['own_transfers'], {'amount': 450.0, 'rows': 2})
        self.assertEqual(body['committee_name'], 'ZED FOR CONGRESS + ZED FOR CONGRESS II')
        self.assertEqual(body['committee_ids'], ['C90000101', 'C90000102'])
        self.assertEqual((body['total_itemized'], body['reported_total'], body['coverage']), (1300.0, 1350.0, 0.9629))
        self.assertEqual(body['coverage_status'], 'complete')

    def test_complete_needs_every_committee_complete(self):
        a = committee([row('PEAK', 'FUNDRAISING CONSULTING', 1000.00, sub='A1')], committee_id='C90000101', name='ZED FOR CONGRESS')
        b = committee([], disbursements=None, committee_id='C90000102', name='ZED FOR CONGRESS II', status='partial')
        both = cs.combine(result(a, b), 2024)
        self.assertEqual(both['coverage_status'], 'partial')
        self.assertIsNone(both['totals'])                       # one committee's totals are missing: the sum is unknown
        self.assertIn('ZED FOR CONGRESS II: partial (Schedule B pending, totals pending)', both['reasons'])
        only = cs.combine(result(a), 2024)
        self.assertEqual(only['coverage_status'], 'complete')

    def test_complete_needs_totals(self):
        made = cs.combine(result(committee([row('PEAK', 'FUNDRAISING CONSULTING', 5.00)], disbursements=None)), 2024)    # B and totals "complete", no totals object
        self.assertEqual(made['coverage_status'], 'partial')
        self.assertIsNone(made['totals'])
        self.assertEqual(made['reasons'], ['Totals were not retrieved, so what was reported is unknown'])
        self.assertEqual(cs.combine(result(committee([row('PEAK', 'FUNDRAISING CONSULTING', 5.00)])), 2024)['coverage_status'], 'complete')

    def test_a_mapped_committee_the_reader_did_not_return_makes_the_campaign_partial(self):
        a = committee([row('PEAK', 'FUNDRAISING CONSULTING', 1000.00, sub='A1')], committee_id='C90000101', name='ZED FOR CONGRESS')
        made = result(a)
        made['mapping']['committee_ids'] = ['C90000101', 'C90000102']
        both = cs.combine(made, 2024)
        self.assertEqual(both['coverage_status'], 'partial')
        self.assertEqual(both['reasons'], ['1 of 2 campaign committees not collected'])
        self.assertEqual(cs.breakdown(both, 'ZED, ZACHARY')['coverage_reasons'], ['1 of 2 campaign committees not collected'])
        made['mapping']['committee_ids'] = ['C90000101']
        self.assertEqual(cs.combine(made, 2024)['coverage_status'], 'complete')

    def test_pending_not_seeded_and_unmapped_never_imply_zero(self):
        for status, words in (('pending', 'Spending not collected for 2023–24'), ('not_seeded', 'Spending not collected for 2023–24'),
                              ('mapping_unknown', 'Campaign committee not identified for 2023–24')):
            with self.subTest(status=status):
                made = cs.combine(result(committee([], disbursements=None, status=status)), 2024)
                self.assertEqual(made['message'], words)
                self.assertNotEqual(made['status'], 'ok')
        self.assertEqual(cs.combine({'committees': []}, 2024)['status'], 'mapping_unknown')

    def test_a_partial_campaign_is_drawn_with_its_reason(self):
        item = committee([row('PEAK', 'FUNDRAISING CONSULTING', 1000.00)], disbursements=None, status='partial')
        body = cs.breakdown(cs.combine(result(item), 2024), BARR)
        self.assertEqual((body['status'], body['coverage_status'], body['coverage']), ('ok', 'partial', None))
        self.assertEqual(body['coverage_reasons'], ['ANDY BARR FOR CONGRESS, INC.: partial (Schedule B pending, totals pending)'])

    def test_without_the_reader_the_answer_is_source_unavailable_not_a_crash(self):
        with patch.dict(os.environ, {cs.FIXTURE_ENV: ''}), patch.dict(sys.modules, {cs.READER: None}):
            body, status = cs.respond({'candidate': ['H0KY06104'], 'cycle': ['2024']})
        self.assertEqual((status, body['status'], body['error']), (503, 'source_unavailable', 'Spending source not installed'))

    def test_the_reader_is_called_with_its_documented_keywords(self):
        calls = []

        class Reader:
            @staticmethod
            def read_campaign_spending(**kwargs):
                calls.append(kwargs)
                return result(committee([row('DELTA AIRLINES', 'TRAVEL EXPENSE', 5.0)]))
        with patch.dict(os.environ, {cs.FIXTURE_ENV: ''}), patch.dict(sys.modules, {cs.READER: Reader}):
            cs.load_campaign_spending('H0KY06104', 2024)
            cs.load_campaign_spending('C00467571', 2024)
        self.assertEqual(calls, [{'cycle': 2024, 'candidate_id': 'H0KY06104', 'committee_id': None},
                                 {'cycle': 2024, 'candidate_id': None, 'committee_id': 'C00467571'}])

    def test_the_crawlers_contract_fixture_is_the_copy_it_was_given(self):
        import hashlib
        for name, digest in CRAWLER_SHA256.items():
            self.assertEqual(hashlib.sha256((CRAWLER / name).read_bytes()).hexdigest(), digest, name)

    def test_every_fixture_here_validates_against_the_crawlers_schema(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest('jsonschema is not installed')
        schema = json.loads(CRAWLER_SCHEMA.read_text(encoding='utf-8'))
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.validate(json.loads(CRAWLER_FIXTURE.read_text(encoding='utf-8')), schema)            # the container
        one = jsonschema.Draft202012Validator({'$ref': '#/$defs/Result', '$defs': schema['$defs']})
        for path in sorted(FIXTURES.glob('*-20*.json')):
            with self.subTest(fixture=path.name):
                one.validate(json.loads(path.read_text(encoding='utf-8')))

    def test_the_crawlers_fixture_goes_through_the_adapter_unchanged_and_each_state_reads_right(self):
        value = json.loads(CRAWLER_FIXTURE.read_text(encoding='utf-8'))
        # coverage_status -> what the UI is told (candidate_spending.combine + breakdown)
        expected = {'complete': 'ok', 'partial': 'ok', 'not_seeded': 'not_collected', 'pending': 'not_collected', 'mapping_unknown': 'mapping_unknown'}
        self.assertEqual({item['name'] for item in value['examples']}, set(expected))
        words = {'not_collected': 'Spending not collected for 2023–24', 'mapping_unknown': 'Campaign committee not identified for 2023–24'}
        for item in value['examples']:
            call = item['call']
            who = call.get('candidate_id') or call['committee_id']
            with self.subTest(example=item['name']), patch.dict(os.environ, {cs.FIXTURE_ENV: str(CRAWLER_FIXTURE)}):
                made = cs.load_campaign_spending(who, call['cycle'])
                self.assertEqual(made['status'], expected[item['name']])
                if made['status'] != 'ok':
                    self.assertEqual(made['message'], words[made['status']])
                    if call.get('candidate_id'):
                        body, status = cs.respond({'candidate': [who], 'cycle': [str(call['cycle'])]})
                        self.assertEqual((status, body['error']), (404, words[made['status']]))
                    continue
                shown = cs.breakdown(made, detail=True)
                self.assertEqual(shown['coverage_status'], item['name'])
                rows = [row for committee in item['result']['committees'] for row in committee['rows']]
                self.assertEqual(shown['source']['row_occurrences'], len(rows))
                self.assertEqual(shown['source']['rows'] + shown['source']['duplicate_rows_dropped'] + shown['own_transfers']['rows'], len(rows))
                self.assertEqual(shown['committee_name'], item['result']['committees'][0]['committee_name'])
                if item['name'] == 'complete':
                    # the crawler's totals object has no loan_repayments: a missing field is unknown, not $0
                    self.assertTrue(shown['coverage_unknown'])
                    self.assertIsNone(shown['reported_total'])
                    self.assertEqual(shown['coverage_reasons'], ['Totals lack loan_repayments, so what was reported is unknown'])
                else:                                                          # partial: drawn, never read as zero or as complete
                    self.assertTrue(shown['coverage_unknown'])
                    self.assertIsNone(shown['reported_total'])
                    self.assertIn('partial (Schedule B pending, totals unsupported)', shown['coverage_reasons'][0])


class Fixtures(unittest.TestCase):
    def test_no_committed_fixture_names_a_real_individual(self):
        # Individuals in a committed fixture are the candidate and synthetic placeholders only; vendors and committees may be real.
        import re
        allowed = {'BARR, GARLAND ANDY', 'ZED, ZACHARY', 'DOE, JANE', 'ZED STAFFER, ONE'}       # the candidates and obvious placeholders
        for path in sorted(FIXTURES.glob('*-20*.json')):
            value = json.loads(path.read_text(encoding='utf-8'))
            names = {item['recipient_name'] for each in value['committees'] for item in each['rows'] if item.get('entity_type') == 'IND'}
            self.assertTrue(names, path.name)
            for name in names:
                with self.subTest(fixture=path.name, payee=name):
                    self.assertTrue(name in allowed or re.fullmatch(r'(?:DONOR|STAFFER|SUPPORTER), SAMPLE [A-Z]', name), name)


class Detail(unittest.TestCase):
    def test_top_payees_use_non_memo_rows_and_resolved_memo_children(self):
        parent = row('AMERICAN EXPRESS', 'SEE MEMOS', 700.00, tid='A1')
        kids = [row('DELTA AIRLINES', 'TRAVEL EXPENSE', 400.00, tid='A2', memo='X', back='A1'),
                row('MARRIOTT', 'TRAVEL EXPENSE', 300.00, tid='A3', memo='X', back='A1')]
        plain = [row('DELTA AIRLINES', 'TRAVEL EXPENSE', 250.00), row('GOLD SHIELD TRANSPORTATION', 'TRANSPORTATION', 500.00),
                 row('UNION CARD', 'SEE MEMOS', 90.00, tid='U1'),
                 row('HERTZ', 'TRAVEL EXPENSE', 20.00, tid='U2', memo='X', back='U1')]           # does not sum: parent stays Other
        body = numbers([parent, *kids, *plain], detail=True)
        self.assertEqual(body['payees']['travel'], [
            {'payee': 'DELTA AIRLINES', 'amount': 650.0, 'payments': 2},      # a card memo child plus a direct payment
            {'payee': 'GOLD SHIELD TRANSPORTATION', 'amount': 500.0, 'payments': 1},
            {'payee': 'MARRIOTT', 'amount': 300.0, 'payments': 1}])
        self.assertEqual([p['payee'] for p in body['payees']['other']], ['UNION CARD'])
        self.assertNotIn('AMERICAN EXPRESS', [p['payee'] for p in body['payees']['travel']])    # the parent is not also counted
        self.assertEqual(body['payments']['travel']['total'], 4)
        self.assertEqual(sum(item['amount'] for item in body['payments']['travel']['rows']), 1450.0)
        self.assertTrue(all(item['url'].startswith('https://docquery.fec.gov/') for item in body['payments']['travel']['rows']))

    def test_top_five_only_and_the_first_hundred_payments_with_the_count_of_the_rest(self):
        rows = [row(f'VENDOR {n}', 'TRAVEL EXPENSE', 10.0 + n, date=f'2024-01-{n % 28 + 1:02d}', sub=f'V{n}') for n in range(150)]
        body = numbers(rows, detail=True)
        self.assertEqual(len(body['payees']['travel']), 5)
        self.assertEqual(body['payees']['travel'][0]['payee'], 'VENDOR 149')
        self.assertEqual((len(body['payments']['travel']['rows']), body['payments']['travel']['total']), (100, 150))

    def test_a_payment_without_a_pdf_url_links_by_its_image_number(self):
        item = row('DELTA AIRLINES', 'TRAVEL EXPENSE', 10.0)
        item['pdf_url'] = None
        body = numbers([item], detail=True)
        self.assertEqual(body['payments']['travel']['rows'][0]['url'], 'https://docquery.fec.gov/cgi-bin/fecimg/?202501319748746337')


class Route(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        patcher = patch.dict(os.environ, {cs.FIXTURE_ENV: self.directory.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def save(self, candidate, rows=None, cycle=2024, status='complete', committee_id=None, **kwargs):
        rows = rows or [row('THE STANTON GROUP, LLC', 'FUNDRAISING CONSULTING', 600.00, sub=f'{candidate}1'),
                        row('DELTA AIRLINES', 'TRAVEL EXPENSE', 400.00, sub=f'{candidate}2')]
        item = committee(rows, committee_id=committee_id or 'C0' + candidate[-8:], status=status, **kwargs)
        (Path(self.directory.name) / f'{candidate}-{cycle}.json').write_text(json.dumps(result(item, candidate=candidate)), encoding='utf-8')

    def ask(self, **query):
        return cs.respond({key: [value] for key, value in query.items()})

    def test_not_collected_is_a_404_with_a_json_body_and_never_a_fetch(self):
        body, status = self.ask(candidate='H0KY06104', cycle='2024')
        self.assertEqual(status, 404)
        self.assertEqual(body['status'], 'mapping_unknown')                 # nothing in the source for it
        self.save('H0KY06104', [], status='pending', disbursements=None)
        body, status = self.ask(candidate='H0KY06104', cycle='2024')
        self.assertEqual((status, body['status'], body['error']), (404, 'not_collected', 'Spending not collected for 2023–24'))
        json.dumps(body)

    def test_a_collected_campaign_is_served_by_id_or_by_node_key(self):
        self.save('H0KY06104', committee_id=COMMITTEE)
        for key in ('H0KY06104', 'candidate:H0KY06104:support'):
            body, status = self.ask(candidate=key, cycle='2024')
            self.assertEqual(status, 200)
            self.assertEqual((body['committee_id'], body['committee_name'], body['cycle']), ('C00467571', 'ANDY BARR FOR CONGRESS, INC.', 2024))
            self.assertEqual((body['total_itemized'], body['reported_total'], body['coverage']), (1000.0, 1000.0, 1.0))
            self.assertEqual([(c['key'], c['amount'], c['share'], c['payments']) for c in body['categories']],
                             [('consulting', 600.0, 0.6, 1), ('travel', 400.0, 0.4, 1)])
            self.assertNotIn('payees', body)                         # the hover card stays light
        self.assertIn('payees', self.ask(candidate='H0KY06104', cycle='2024', detail='1')[0])

    def test_the_name_the_chart_sends_finds_the_candidates_own_reimbursements(self):
        self.save('H0KY06104', [row('BARR, GARLAND ANDY', 'CHILD CARE REIMBURSEMENT', 50.0, entity='IND', sub='R1'), row('DELTA AIRLINES', 'TRAVEL EXPENSE', 50.0, sub='R2')])
        with_name = self.ask(candidate='H0KY06104', cycle='2024', name='BARR, GARLAND ANDY')[0]
        self.assertIn('personal_reimb', amounts(with_name))

    def test_bad_requests_are_refused_not_guessed(self):
        for query in ({'candidate': 'nope', 'cycle': '2024'}, {'candidate': 'H0KY06104', 'cycle': '2023'}, {'candidate': 'H0KY06104'}):
            with self.assertRaises(ValueError):
                cs.respond({key: [value] for key, value in query.items()})

    def test_the_comparison_needs_five_complete_peers_of_the_same_office_and_cycle_among_those_offered(self):
        self.save('H0KY06104', committee_id=COMMITTEE)
        names = ['H0XX00000', 'H0XX00001', 'H0XX00002', 'H0XX00003']
        for number, candidate in enumerate(names):
            self.save(candidate, [row('X', 'TRAVEL EXPENSE', 10.0, sub=f'P{number}')])

        def peers(*offered):
            return self.ask(candidate='H0KY06104', cycle='2024', detail='1', peers=','.join(offered))[0]['peers']
        found = peers(*names)
        self.assertEqual((found['n'], found['too_few'], found['note']), (4, True, 'Too few collected campaigns to compare'))
        self.assertNotIn('shares', found)
        self.save('S0XX00009', [row('X', 'TRAVEL EXPENSE', 10.0)], committee_id='C00000099')          # a Senate campaign is no House peer
        self.assertEqual(peers(*names, 'S0XX00009', 'H0KY06104')['n'], 4)                                # nor is the campaign itself
        self.save('H0XX00004', [row('X', 'FUNDRAISING CONSULTING', 30.0, sub='P4'), row('X', 'TRAVEL EXPENSE', 10.0, sub='P5')])
        self.save('H0XX00005', [row('X', 'TRAVEL EXPENSE', 10.0, sub='P6')], status='partial', disbursements=None)   # partial: never a peer
        found = peers(*names, 'H0XX00004', 'H0XX00005')
        self.assertEqual((found['n'], found['too_few']), (5, False))
        self.assertEqual(found['label'], 'Median of 5 collected House campaigns, 2023–24')
        self.assertAlmostEqual(sum(found['shares'].values()), 1.0, places=5)
        self.assertEqual(found['shares']['travel'], 1.0)            # the median share of four all-travel campaigns and one 25% one
        self.assertEqual(self.ask(candidate='H0KY06104', cycle='2024', detail='1')[0]['peers']['n'], 0)   # none offered: none read

    def test_a_comparison_where_every_median_is_zero_is_not_drawn(self):
        self.save('H0KY06104', committee_id=COMMITTEE)
        kinds = ['TRAVEL EXPENSE', 'FUNDRAISING CONSULTING', 'TV ADS', 'POLLING EXPENSE', 'RENT']          # five peers, five different categories
        names = [f'H0XX0000{n}' for n in range(5)]
        for number, (candidate, kind) in enumerate(zip(names, kinds)):
            self.save(candidate, [row('X', kind, 10.0, sub=f'M{number}')])
        found = self.ask(candidate='H0KY06104', cycle='2024', detail='1', peers=','.join(names))[0]['peers']
        self.assertEqual(found['n'], 5)
        self.assertTrue(found['too_few'])                                          # no category is spent in by most of them
        self.assertNotIn('shares', found)
        self.assertIn('no spending category', found['note'])

    def test_a_peers_shares_are_worked_once_per_snapshot_and_the_cache_is_bounded(self):
        cs._SHARES.clear()
        self.addCleanup(cs._SHARES.clear)
        names = [f'H0XX0000{n}' for n in range(4)]
        for number, candidate in enumerate(names):
            self.save(candidate, [row('X', 'TRAVEL EXPENSE', 10.0 + number, sub=f'C{number}')])
        with patch.object(cs, 'breakdown', wraps=cs.breakdown) as spy:
            cs.peers(2024, 'H', [], names)
            self.assertEqual(spy.call_count, 4)
            cs.peers(2024, 'H', [], names)
            self.assertEqual(spy.call_count, 4, 'the second comparison is served from memory')
        # without a snapshot sha the rows themselves are the key: unchanged rows hit, changed rows do not
        for number, candidate in enumerate(names):
            path = Path(self.directory.name) / f'{candidate}-2024.json'
            value = json.loads(path.read_text(encoding='utf-8'))
            value['snapshot'] = None
            path.write_text(json.dumps(value), encoding='utf-8')
        cs._SHARES.clear()
        with patch.object(cs, 'breakdown', wraps=cs.breakdown) as spy:
            cs.peers(2024, 'H', [], names)
            cs.peers(2024, 'H', [], names)
            self.assertEqual(spy.call_count, 4)
            self.save(names[0], [row('X', 'TRAVEL EXPENSE', 99.0, sub='C0')])
            path = Path(self.directory.name) / f'{names[0]}-2024.json'
            value = json.loads(path.read_text(encoding='utf-8'))
            value['snapshot'] = None
            path.write_text(json.dumps(value), encoding='utf-8')
            cs.peers(2024, 'H', [], names)
            self.assertEqual(spy.call_count, 5, 'a changed input is worked again')
        with patch.object(cs, 'SHARES_CAP', 2):
            cs._SHARES.clear()
            cs.peers(2024, 'H', [], names)
            self.assertEqual(len(cs._SHARES), 2, 'least recently used go first')

    def test_a_reader_that_raises_is_a_503_source_error_with_its_message_and_a_log_line(self):
        class Reader:
            @staticmethod
            def read_campaign_spending(**kwargs):
                raise RuntimeError('snapshot store is locked')
        with patch.dict(os.environ, {cs.FIXTURE_ENV: ''}), patch.dict(sys.modules, {cs.READER: Reader}):
            with self.assertLogs('mentat.candidate_spending', level='ERROR') as logged:
                body, status = cs.respond({'candidate': ['H0KY06104'], 'cycle': ['2024']})
        self.assertEqual((status, body['status']), (503, 'source_error'))
        self.assertIn('snapshot store is locked', body['error'])
        self.assertEqual((body['candidate_id'], body['cycle']), ('H0KY06104', 2024))
        self.assertTrue(any('H0KY06104' in line for line in logged.output))
        json.dumps(body)

    def test_a_peer_the_reader_cannot_read_is_left_out_not_a_failed_answer(self):
        names = [f'H0XX0000{n}' for n in range(6)]
        for number, candidate in enumerate(names):
            self.save(candidate, [row('X', 'TRAVEL EXPENSE', 10.0, sub=f'E{number}')])
        real = cs.read_source

        def flaky(**kwargs):
            if kwargs.get('candidate_id') == names[0]:
                raise OSError('disk gone')
            return real(**kwargs)
        self.save('H0KY06104', committee_id=COMMITTEE)
        with patch.object(cs, 'read_source', flaky), self.assertLogs('mentat.candidate_spending', level='WARNING'):
            found = self.ask(candidate='H0KY06104', cycle='2024', detail='1', peers=','.join(names))
        self.assertEqual(found[1], 200)
        self.assertEqual(found[0]['peers']['n'], 5)

    def test_the_committed_fixtures_answer_through_the_route(self):
        with patch.dict(os.environ, {cs.FIXTURE_ENV: str(FIXTURES)}):
            barr, status = self.ask(candidate='H0KY06104', cycle='2024')
            self.assertEqual((status, barr['coverage_status'], barr['coverage'], barr['reported_total']), (200, 'partial', None, None))
            self.assertTrue(barr['coverage_unknown'])
            synthetic, _ = self.ask(candidate='H8ZZ00001', cycle='2024', name='ZED, ZACHARY', detail='1')
            self.assertEqual((synthetic['total_itemized'], synthetic['reported_total'], synthetic['coverage']), (71770.0, 153000.0, 0.469))
            self.assertEqual(synthetic['own_transfers'], {'amount': 6000.0, 'rows': 2})
            self.assertEqual(synthetic['source']['duplicate_rows_dropped'], 1)
            self.assertEqual(synthetic['dropped_negative'][0]['key'], 'travel')
            self.assertEqual(synthetic['memo_handling']['resolved'], 1)
            self.assertEqual(synthetic['memo_handling']['unresolved'], 1)
            self.assertIn('personal_reimb', amounts(synthetic))
            self.assertEqual(self.ask(candidate='H0KY06104', cycle='2022')[1], 404)


class Server(unittest.TestCase):
    """The route as the console serves it: wired in server.py, refused when hosted, and never a dropped connection."""

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('candidate_spending_server', HERE / 'app' / 'server.py')
        cls.console = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.console)

    def get(self, path, enforced):
        sent = []
        handler = SimpleNamespace(path=path, access_cfg=SimpleNamespace(enforced=enforced), sent=sent, valid_host=lambda: True,
                                  server=SimpleNamespace(app=SimpleNamespace(read_only=True, report_directory=Path('.'), token='local'), server_port=0),
                                  session_token=lambda: 'hosted-read-only' if enforced else 'local',
                                  send=lambda value, status=200, **kwargs: sent.append((value, status)))
        with patch.object(self.console, 'build_status', return_value={'git_sha': 'x'}):
            self.console.Handler.do_GET(handler)
        return sent[0]

    def test_the_hosted_console_refuses_the_route_and_tells_the_page_it_is_hosted(self):
        self.assertNotIn('/api/candidate-spending', self.console.HOSTED_GET_ROUTES)
        value, status = self.get('/api/candidate-spending?candidate=H0KY06104&cycle=2024', True)
        self.assertEqual((status, value), (403, {'error': self.console.HOSTED_REFUSED}))
        self.assertIs(self.get('/api/session?light=1', True)[0]['hosted'], True)       # the page switches the card and panel off on this
        self.assertIs(self.get('/api/session?light=1', False)[0]['hosted'], False)

    def test_a_local_console_serves_it(self):
        with tempfile.TemporaryDirectory() as directory:
            item = committee([row('DELTA AIRLINES', 'TRAVEL EXPENSE', 5.0)])
            (Path(directory) / 'H0KY06104-2024.json').write_text(json.dumps(result(item)), encoding='utf-8')
            with patch.dict(os.environ, {cs.FIXTURE_ENV: directory}):
                value, status = self.get('/api/candidate-spending?candidate=H0KY06104&cycle=2024', False)
        self.assertEqual((status, value['status'], value['total_itemized']), (200, 'ok', 5.0))

    def test_a_reader_that_raises_still_answers_over_the_wire(self):
        class Reader:
            @staticmethod
            def read_campaign_spending(**kwargs):
                raise RuntimeError('reader exploded')
        server = self.console.ThreadingHTTPServer(('127.0.0.1', 0), self.console.Handler)
        server.app = SimpleNamespace(token='t', read_only=True, report_directory=Path('.'), configured_root='.', root=Path('.'))
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(worker.join, 5)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with patch.dict(os.environ, {cs.FIXTURE_ENV: ''}), patch.dict(sys.modules, {cs.READER: Reader}):
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=15)
            with self.assertLogs('mentat.candidate_spending', level='ERROR'):
                connection.request('GET', '/api/candidate-spending?candidate=H0KY06104&cycle=2024')
                response = connection.getresponse()
                body = json.loads(response.read())
            connection.close()
        self.assertEqual(response.status, 503)
        self.assertEqual(body['status'], 'source_error')
        self.assertIn('reader exploded', body['error'])


if __name__ == '__main__':
    unittest.main()

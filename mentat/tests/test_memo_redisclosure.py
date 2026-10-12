"""F13, the memo re-disclosure rule (Paul approved 2026-09-26, "Approve F13").

Every case in tests/fixtures/memo_redisclosure_cases.json, which
tests/test_memo_redisclosure.R runs against schema.R as well: the Python twin
(pac_evidence.memo_redisclosure_parents), the PAC evidence receipts built on it
(pac_evidence.summarize) and the cross-verification engine
(cross_reference_engine.analyze).

Run from inside mentat/tests: python -m unittest test_memo_redisclosure
"""
import copy
import json
from pathlib import Path
import re
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import pac_evidence  # noqa: E402
from pac_evidence import REDISCLOSURE_REASON, memo_redisclosure_parents, name_key, receipt_role, receipt_rules  # noqa: E402
from cross_reference_engine import analyze  # noqa: E402

FIXTURE = json.loads((Path(__file__).resolve().parent / 'fixtures' / 'memo_redisclosure_cases.json').read_text(encoding='utf-8'))
NON_GIVING = ('other_receipt', 'asset_liquidation')


def rows_of(case):
    return [{**FIXTURE['defaults'], **row} for row in case['rows']]


def verdicts(rows):
    """What stays in the total, the recorded parent, and the disclosure_memo_unmatched flag."""
    parents = memo_redisclosure_parents(rows)
    found = {}
    for row in rows:
        sid, role = str(row['sub_id']), receipt_role(row)
        parent = parents.get(sid, '')
        found[sid] = {'counted': role not in NON_GIVING and not parent, 'duplicate_of': parent,
                      'flagged': role == 'unverified_disclosure_memo' and not parent}
    return found


def problems_in(case, expectations=None):
    expectations = case['expect'] if expectations is None else expectations
    found = verdicts(rows_of(case))
    problems = [f"{case['name']}/{sid}: expected {want}, found {found.get(sid)}"
                for sid, want in expectations.items() if found.get(sid) != want]
    if set(found) != set(expectations):
        problems.append(f"{case['name']}: every row needs an expectation")
    return problems


def case(name):
    return next(item for item in FIXTURE['cases'] if item['name'] == name)


class SharedFixture(unittest.TestCase):
    def test_every_case(self):
        self.assertGreaterEqual(len(FIXTURE['cases']), 20)
        for item in FIXTURE['cases']:
            with self.subTest(case=item['name']):
                self.assertEqual(problems_in(item), [])

    def test_a_wrong_expectation_is_caught(self):
        # Negative control on the harness: flip the $15.5M memo to counted.
        item = case('fairshake_15_5m_disclosedon_typo')
        wrong = copy.deepcopy(item['expect'])
        wrong['4021420241824800143'] = {'counted': True, 'duplicate_of': '', 'flagged': False}
        self.assertTrue(problems_in(item, wrong))

    def test_statements(self):
        rules = receipt_rules()
        self.assertEqual(rules['version'], 6)
        for statement in FIXTURE['statements']:
            receipt, memo_text = statement['receipt_type_full'], statement['memo_text']
            text = (name_key(receipt) + ' ' + name_key(memo_text)).strip()
            with self.subTest(text=text):
                self.assertEqual(bool(re.search(rules['disclosure_pattern'], text)), statement['states'])
                self.assertEqual(bool(re.search(rules['redisclosure_unitemized_pattern'], text)), statement['unitemized'])
                role = receipt_role({'filing_form': 'F3X', 'line_number': '17', 'memo_code': 'X',
                                     'receipt_type_full': receipt, 'memo_text': memo_text})
                self.assertEqual(role == 'unverified_disclosure_memo', statement['states'])

    def test_the_rule_needs_the_statement_not_just_equal_amounts(self):
        # Negative control: strip the statement from every fixture memo and no memo is a duplicate.
        for item in FIXTURE['cases']:
            rows = rows_of(item)
            for row in rows:
                if pac_evidence.is_memo(row):
                    row['receipt_type_full'], row['memo_text'] = 'MEMO', ''
            self.assertEqual(memo_redisclosure_parents(rows), {}, item['name'])


def fec_receipt(row, committee='C00835959'):
    """A fixture row as openFEC's schedule_a returns it."""
    return {'sub_id': row['sub_id'], 'committee_id': committee, 'contributor_name': row['contributor_name'],
            'contribution_receipt_amount': row['contribution_receipt_amount'],
            'contribution_receipt_date': row['contribution_receipt_date'] + 'T00:00:00',
            'filing_form': row.get('filing_form', 'F3X'), 'line_number': row['line_number'],
            'receipt_type_full': row['receipt_type_full'] or None, 'memo_text': row['memo_text'] or None,
            'memo_code': row['memo_code'] or None, 'memoed_subtotal': row['memo_code'] == 'X',
            'transaction_id': row.get('transaction_id', ''), 'pdf_url': ''}


class PacEvidenceReceipts(unittest.TestCase):
    """summarize(): the receipts the PAC evidence page lists and temporal_routes follows."""

    def setUp(self):
        names = ('fairshake_15_5m_disclosedon_typo', 'fairshake_four_bitcoin_memos',
                 'fairshake_usdc_previously_disclosed', 'unitemized_memo_without_parent', 'd040c_blank_rows_untouched')
        rows = {row['sub_id']: row for name in names for row in rows_of(case(name))}
        snapshot = {'committee_id': 'C00835959', 'cycle': 2024, 'totals': {},
                    'receipts': [fec_receipt(row) for row in rows.values()], 'disbursements': [], 'expenditures': []}
        self.result = pac_evidence.summarize([snapshot], 'Coinbase', ['COINBASE COMMERCE (EXCHANGE)'])
        self.receipts = {row['sub_id']: row for row in self.result['receipts']}

    def test_restated_memos_leave_the_company_total_with_their_parent(self):
        for memo, parent in (('4021420241824800143', '4021420241824800115'),
                             ('4030420261386079659', '4020420251141540904'), ('4030420261386079661', '4020420251141540904'),
                             ('4081520251218411849', '4020420251141540904'), ('4081520251218411851', '4020420251141540904'),
                             ('4081220242011729506', '4062620241962823148')):
            with self.subTest(memo=memo):
                receipt = self.receipts[memo]
                self.assertFalse(receipt['included_company_contribution'])
                self.assertEqual((receipt['duplicate_of_sub_id'], receipt['duplicate_reason']), (parent, REDISCLOSURE_REASON))
                self.assertTrue(self.receipts[parent]['included_company_contribution'])

    def test_unmatched_memo_and_d040c_rows_stay_included(self):
        memo = self.receipts['4081220242011729505']
        self.assertTrue(memo['included_company_contribution'])
        self.assertEqual(memo['receipt_role_reason'], 'disclosure_memo_unmatched')
        self.assertNotIn('duplicate_of_sub_id', memo)
        for sid in ('4030420261386079660', '4030420261386079658', '4030420261386079642'):
            with self.subTest(sid=sid):
                self.assertTrue(self.receipts[sid]['included_company_contribution'])
                self.assertNotIn('duplicate_of_sub_id', self.receipts[sid])
                self.assertNotIn('receipt_role_reason', self.receipts[sid])

    def test_included_total_counts_each_gift_once(self):
        included = sum(row['amount'] for row in self.result['receipts'] if row['included_company_contribution'])
        # 15.5M + 23M + 24,999,995 gifts, the $5 unitemized memo, the three D-040(c) rows.
        self.assertEqual(round(included, 2), 15500000 + 23000000 + 24999995 + 5 + 5175000 + 6000000 + 8092475)
        routed = {route['receipt_sub_id'] for route in pac_evidence.temporal_routes(self.result)}
        self.assertNotIn('4030420261386079659', routed)
        self.assertIn('4020420251141540904', routed)


def engine_row(row):
    return {**row, 'industry': 'Crypto', 'cycle': 2024, 'entity_type': 'ORG', 'contributor_zip': '94105',
            'current_receipt_cents': int(round(row['contribution_receipt_amount'] * 100)), 'file_num': 1}


class CrossReferenceEngine(unittest.TestCase):
    """analyze(): the Contribution Studio's canonical decisions apply the same rule."""

    def test_restated_memos_are_excluded_against_their_parent(self):
        rows = [engine_row(row) for name in ('fairshake_four_bitcoin_memos', 'unitemized_memo_without_parent')
                for row in rows_of(case(name))]
        result = analyze(rows)
        decisions = {d['source_id']: d for d in result['decisions']}
        for memo in ('4030420261386079659', '4030420261386079661', '4081520251218411849', '4081520251218411851'):
            with self.subTest(memo=memo):
                self.assertEqual(decisions[memo]['status'], 'excluded_with_evidence')
                self.assertEqual(decisions[memo]['counted_cents'], 0)
                self.assertIn(REDISCLOSURE_REASON, decisions[memo]['reasons'])
        links = {(link['source_id'], link['retained_source_id']) for link in result['links'] if link['kind'] == 'memo'}
        self.assertEqual(links, {(memo, '4020420251141540904') for memo in (
            '4030420261386079659', '4030420261386079661', '4081520251218411849', '4081520251218411851')})
        self.assertEqual(decisions['4081220242011729505']['counted_cents'], 500)
        self.assertEqual(result['totals']['provisional_original_giving_cents'], 2300000000 + 500)
        # An excluded memo leaves the review queue: its question is answered.
        excluded = {memo for memo, _ in links}
        self.assertFalse(any(excluded & set(item['source_ids']) for item in result['review_queue']))

    def test_no_statement_or_review_status_is_not_excluded(self):
        # Negative controls: the equal-amount clue without a statement, and a memo under attribution review.
        for name, memo in (('no_statement_is_untouched', 'F13-M7'), ('memo_under_attribution_review', 'F13-M17')):
            with self.subTest(case=name):
                result = analyze([engine_row(row) for row in rows_of(case(name))])
                decision = next(d for d in result['decisions'] if d['source_id'] == memo)
                self.assertNotEqual(decision['status'], 'excluded_with_evidence')
                self.assertNotIn(REDISCLOSURE_REASON, decision['reasons'])
                self.assertFalse(result['links'])


if __name__ == '__main__':
    unittest.main()

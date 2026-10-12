from pathlib import Path
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing, contextmanager

@contextmanager
def db(path):
    with closing(sqlite3.connect(path)) as connection, connection:
        yield connection

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from hydrate_receipt_metadata import hydrate, apply_evidence
from report_source_guard import sha


class HydrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.db = self.root / 'original.db'
        self.evidence = dict(sub_id='123', contributor_name='ACME', committee_id='C1',
            contribution_receipt_date='2024-01-01', contribution_receipt_amount=190000,
            filing_form='F3X', line_number='17', memo_text='Return of erroneous debit', memo_code='', memoed_subtotal=False)
        with db(self.db) as c:
            for table in ('contributions', 'corporate_giving'):
                c.execute(f'CREATE TABLE {table}(sub_id TEXT, company TEXT, company_match_status TEXT, committee_id TEXT, contributor_name TEXT, contribution_receipt_date TEXT, contribution_receipt_amount REAL, cycle INTEGER, two_year_transaction_period INTEGER, is_memo_leg INTEGER)')
            c.execute("INSERT INTO corporate_giving VALUES('123','Acme','accepted','C1','ACME','2024-01-01',190000,2024,2024,0)")
            c.execute('CREATE TABLE api_page_checkpoints(payload_json TEXT)')

    def test_cached_evidence_restores_metadata_without_changing_money_or_source(self):
        with db(self.db) as c:
            c.execute('INSERT INTO api_page_checkpoints VALUES(?)', (json.dumps({'rows': [self.evidence]}),))
        original = sha(self.db)
        result = hydrate(self.db, self.root / 'copy.db')
        self.assertEqual(result['status'], 'complete'); self.assertEqual(sha(self.db), original)
        with db(result['output']) as c:
            self.assertEqual(c.execute('SELECT filing_form,line_number,contribution_receipt_amount FROM corporate_giving').fetchone(), ('F3X','17',190000))
            self.assertEqual(c.execute('SELECT count(*) FROM receipt_metadata_history').fetchone()[0], 1)

    def test_exact_fec_response_is_validated(self):
        class Client:
            def page(inner, endpoint, params):
                self.assertEqual(endpoint, 'schedules/schedule_a/123/')
                return {'results': [{**self.evidence, 'contribution_receipt_amount': 1234}]}
        with self.assertRaisesRegex(ValueError, 'amount conflict'):
            hydrate(self.db, self.root/'copy.db', fetch=True, client=Client())

    def test_unresolved_rows_are_kept_and_requests_are_bounded(self):
        result = hydrate(self.db, self.root/'copy.db', fetch=True, max_requests=0, client=object())
        self.assertEqual(result['unresolved_count'], 1)
        with db(result['output']) as c:
            self.assertEqual(c.execute('SELECT contribution_receipt_amount FROM corporate_giving').fetchone()[0],190000)

    def test_source_cannot_be_overwritten(self):
        with self.assertRaisesRegex(ValueError,'private copy'):
            hydrate(self.db,self.db)

    def test_memo_flag_is_repaired_and_logged_even_when_metadata_matches(self):
        result = hydrate(self.db, self.root/'copy.db')
        with db(result['output']) as c:
            c.row_factory = sqlite3.Row
            evidence = {**self.evidence, 'memoed_subtotal': 'true'}
            stored = dict(c.execute('SELECT * FROM corporate_giving').fetchone())
            apply_evidence(c, 'corporate_giving', stored, evidence, 'fixture')
            c.execute('UPDATE corporate_giving SET is_memo_leg=0')
            stored = dict(c.execute('SELECT * FROM corporate_giving').fetchone())
            self.assertTrue(apply_evidence(c, 'corporate_giving', stored, evidence, 'fixture'))
            self.assertEqual(c.execute('SELECT is_memo_leg FROM corporate_giving').fetchone()[0], 1)
            change = json.loads(c.execute('SELECT changes_json FROM receipt_metadata_history ORDER BY id DESC').fetchone()[0])
            self.assertEqual(set(change), {'is_memo_leg'})


if __name__ == '__main__':
    unittest.main()

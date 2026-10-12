import json, sqlite3, tempfile, unittest
from contextlib import closing
from pathlib import Path
from test_validate_industry_coverage import create_complete_database, gate


class QueryCoverageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.db=Path(self.temp.name)/'sample.db';create_complete_database(self.db)

    def inspect(self, c):
        return gate.api_query_coverage(c,('Alpha','Beta'),(2022,2024,2026),'Fintech')

    def test_zero_results_are_complete_only_for_the_whole_roster(self):
        with closing(sqlite3.connect(self.db)) as c:
            self.assertTrue(self.inspect(c)['complete'])
            c.execute("INSERT INTO companies VALUES('Fintech','Alpha','Alpha.Pay')")
            result=self.inspect(c)
            self.assertFalse(result['complete'])
            self.assertEqual(len(result['missing_queries']),6)
            self.assertEqual({r['query'] for r in result['missing_queries']},{'ALPHA.PAY'})

    def test_normalized_spelling_is_not_literal_query_coverage(self):
        with closing(sqlite3.connect(self.db)) as c:
            c.execute("INSERT INTO companies VALUES('Fintech','Alpha','Alpha.Pay')")
            uid='employer:Alpha:2024'
            contract=json.loads(c.execute('SELECT query_contract FROM api_work_progress WHERE unit_id=?',(uid,)).fetchone()[0])
            contract['request']['params']['contributor_employer']=['Alpha','Alpha Pay']
            c.execute('UPDATE api_work_progress SET query_contract=? WHERE unit_id=?',(json.dumps(contract),uid))
            self.assertTrue(any(r['cycle']==2024 and r['kind']=='employer' for r in self.inspect(c)['missing_queries']))

    def test_old_changed_or_incomplete_contract_cannot_certify(self):
        with closing(sqlite3.connect(self.db)) as c:
            uid='employer:Alpha:2024'
            saved=c.execute('SELECT query_contract FROM api_work_progress WHERE unit_id=?',(uid,)).fetchone()[0]
            subset=json.loads(saved);subset['coverage_verified']=False
            for value in (None,'{}','invalid',saved.replace('"version": 2','"version": 1'),json.dumps(subset)):
                c.execute('UPDATE api_work_progress SET query_contract=? WHERE unit_id=?',(value,uid))
                self.assertIn(uid,self.inspect(c)['invalid_units'])
            c.execute('UPDATE api_work_progress SET query_contract=?,rows_seen=1 WHERE unit_id=?',(saved,uid))
            self.assertIn(uid,self.inspect(c)['invalid_units'])

if __name__=='__main__':unittest.main()

from contextlib import closing
import sqlite3, tempfile, unittest
from pathlib import Path
from test_fec_filing_headers import fixture,fetch_header
from recover_filing_forms import recover
from report_source_guard import sha

class RecoveryTests(unittest.TestCase):
    def test_replay_preserves_source_money_and_ledger(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source=root/'source.db';output=root/'copy.db';cache=root/'headers'
            with closing(sqlite3.connect(source)) as c,c:
                for table in ('contributions','corporate_giving'):
                    c.execute(f'CREATE TABLE {table}(sub_id TEXT PRIMARY KEY,committee_id TEXT,file_num INTEGER,company_match_status TEXT,filing_form TEXT,line_number TEXT,contribution_receipt_amount REAL)')
                c.execute("INSERT INTO contributions VALUES('1','C00000001',100,'accepted',NULL,'11AI',125)")
            digest=sha(source);fetch_header(100,cache,lambda _:fixture())
            first=recover(source,output,cache)
            self.assertEqual(first['forms_filled'],1);self.assertTrue(first['complete'])
            self.assertEqual(recover(source,output,cache)['forms_filled'],0)
            self.assertEqual(sha(source),digest)
            with closing(sqlite3.connect(output)) as c:
                self.assertEqual(c.execute('SELECT contribution_receipt_amount FROM contributions').fetchone()[0],125)
                self.assertEqual(c.execute('SELECT COUNT(*) FROM receipt_metadata_history').fetchone()[0],1)
            with self.assertRaisesRegex(ValueError,'private copy'):recover(source,source,cache)

if __name__=='__main__':unittest.main()

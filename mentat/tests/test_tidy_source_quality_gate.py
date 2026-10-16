"""Stored source guards must survive the tidy writer's publication boundary."""
from contextlib import closing, redirect_stdout, redirect_stderr
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import build_tidy_workbook as writer


class StoredSourceQualityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='mentat-tidy-source-quality-')
        self.folder = Path(self.temporary.name)
        self.store = self.folder / 'synthetic.sqlite'
        self.out = self.folder / 'synthetic.xlsx'
        with closing(sqlite3.connect(self.store)) as database, database:
            database.execute('CREATE TABLE metadata(key TEXT,value TEXT)')
            database.executemany('INSERT INTO metadata VALUES (?,?)',
                [('industry','Synthetic'),('database_sha256','a'*64),
                 ('expected_cycle_totals',json.dumps({'2025-2026':-12.5}))])
            database.execute('CREATE TABLE contributions(source_id TEXT,cycle_label TEXT,company TEXT,'
                             'person_key TEXT,amount REAL,analysis_amount REAL,include_in_analysis INTEGER)')
            database.executemany('INSERT INTO contributions VALUES (?,?,?,?,?,?,?)',
                [('4021620231727827649','2025-2026','Synthetic','P1',-12.5,-12.5,1),
                 ('4021620231727827650','2025-2026','Synthetic','P2',0,0,1)])
            database.execute('CREATE TABLE data_quality(check_id TEXT,source TEXT,severity TEXT,'
                             'passed,detail TEXT,metric TEXT)')

    def tearDown(self):
        self.temporary.cleanup()

    def source_check(self, severity, passed, check_id='saved-source-check'):
        with closing(sqlite3.connect(self.store)) as database, database:
            database.execute('INSERT INTO data_quality VALUES (?,?,?,?,?,?)',
                             (check_id,'r_guard',severity,passed,'Synthetic stored source guard','2'))

    def run_writer(self):
        before = hashlib.sha256(self.store.read_bytes()).hexdigest()
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            status = writer.main(['--staging',str(self.store),'--out',str(self.out)])
        self.assertEqual(hashlib.sha256(self.store.read_bytes()).hexdigest(), before)
        return status, json.loads(Path(str(self.out)+'.receipt.json').read_text())

    def assert_refused(self, status, receipt, original, expected_status):
        self.assertEqual(status, 2)
        self.assertFalse(receipt['ok'])
        self.assertIsNone(receipt['sha256'])
        self.assertEqual(self.out.read_bytes(), original)
        self.assertFalse(list(self.folder.glob('*.pending-*')))
        stored = [row for row in receipt['checks'] if row['source']=='r_guard']
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]['passed'], expected_status)
        self.assertEqual(stored[0]['severity'], 'fail')

    def test_failed_source_guard_refuses_and_preserves_existing_output(self):
        self.source_check('fail', 0)
        original = b'Existing synthetic output must survive refusal.'
        self.out.write_bytes(original)
        status, receipt = self.run_writer()
        self.assert_refused(status, receipt, original, 0)

    def test_null_and_unknown_source_guard_refuse_with_original_diagnostics(self):
        for passed in (None, 'unknown'):
            with self.subTest(passed=passed):
                with closing(sqlite3.connect(self.store)) as database, database:
                    database.execute('DELETE FROM data_quality')
                self.source_check('fail', passed)
                original = b'Uncertain critical guard cannot publish.'
                self.out.write_bytes(original)
                status, receipt = self.run_writer()
                self.assert_refused(status, receipt, original, passed)

    def test_confirmed_pass_retains_signed_amounts_zero_and_text_ids(self):
        self.source_check('fail', 1)
        status, receipt = self.run_writer()
        self.assertEqual(status, 0)
        self.assertTrue(receipt['ok'])
        self.assertEqual(len([r for r in receipt['checks'] if r['source']=='r_guard']), 1)
        book = openpyxl.load_workbook(self.out, read_only=True, data_only=True)
        try:
            rows = list(book['Contributions'].values)
            columns = {value:index for index,value in enumerate(rows[0])}
            self.assertEqual([r[columns['Amount']] for r in rows[1:]], [-12.5, 0])
            self.assertEqual([r[columns['Source ID']] for r in rows[1:]],
                             ['4021620231727827649','4021620231727827650'])
        finally:
            book.close()

    def test_warn_and_info_remain_visible_without_new_fatal_semantics(self):
        self.source_check('warn', 0, 'warning')
        self.source_check('info', None, 'information')
        status, receipt = self.run_writer()
        self.assertEqual(status, 0)
        self.assertTrue(receipt['ok'])
        stored = [r for r in receipt['checks'] if r['source']=='r_guard']
        self.assertEqual({(r['severity'],r['passed']) for r in stored}, {('warn',0),('info',None)})
        book = openpyxl.load_workbook(self.out, read_only=True, data_only=True)
        try:
            rows = list(book['Data Quality'].values)
            self.assertEqual(sum(row[1]=='r_guard' for row in rows[1:]), 2)
        finally:
            book.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)

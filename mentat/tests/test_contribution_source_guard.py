"""Recovered metadata databases must preserve the full accepted financial universe."""
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import contribution_source_guard as guard
from report_source_guard import source_facts


class RecoveredSourceGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.database=self.root/'recovered.db'
        self.accepted=self.root/'accepted';self.directory=self.accepted/'example/2022-2024-2026'
        self.directory.mkdir(parents=True);self.snapshot=self.directory/'snapshot.db';self.pointer=self.directory/'current.json'
        with closing(sqlite3.connect(self.database)) as db, db:
            for table in ('contributions','corporate_giving'):
                db.execute('CREATE TABLE '+table+' (sub_id TEXT,committee_id TEXT,contributor_name TEXT,contribution_receipt_date TEXT,contribution_receipt_amount REAL,industry TEXT,company_match_status TEXT,contributor_zip TEXT)')
            db.executemany('INSERT INTO contributions VALUES (?,?,?,?,?,?,?,?)',[
                ('one','C00000001','DONOR ONE','2024-01-01',100.25,'Example','accepted','10001'),
                ('two','C00000002','DONOR TWO','2024-02-01',200.50,'Example','rejected','10002')])
        with closing(sqlite3.connect(self.snapshot)) as db, db:
            db.execute('CREATE TABLE facts (id TEXT PRIMARY KEY,raw TEXT,analysis TEXT)')
            db.executemany('INSERT INTO facts VALUES (?,?,NULL)',source_facts(self.database,'Example',[2022,2024,2026]))
        self.receipt={'status':'verified','industry':'Example','cycles':[2022,2024,2026],
                      'database_sha256':'original-hash','snapshot':str(self.snapshot),
                      'snapshot_sha256':guard._hash(guard._stamp(self.snapshot))}
        self.save_pointer()
        self.source={'industry':'Example','hydration_manifest':self.root/'repair.json',
                     'database':self.database,'database_sha256':guard._hash(guard._stamp(self.database)),
                     'original_database_sha256':'original-hash'}
        self.addCleanup(patch.stopall)
        patch.object(guard,'ACCEPTED_ROOT',self.accepted).start();patch.object(guard,'CACHE_ROOT',self.root/'proofs').start()
        guard._verify_cached.cache_clear()

    def save_pointer(self):self.pointer.write_text(json.dumps(self.receipt),encoding='utf-8')

    def change(self,sql,params=()):
        with closing(sqlite3.connect(self.database)) as db, db:db.execute(sql,params)
        self.source['database_sha256']=guard._hash(guard._stamp(self.database))

    def test_metadata_edits_preserve_all_financial_facts_including_rejected(self):
        self.change("UPDATE contributions SET contributor_zip='90210'")
        result=guard.verify(self.source)
        self.assertTrue(result['full_financial_universe_preserved'])
        self.assertEqual(result['counts']['unchanged'],2)

    def test_added_source_only_record_is_refused_even_if_report_receipts_unchanged(self):
        self.change("INSERT INTO corporate_giving SELECT 'three',committee_id,contributor_name,contribution_receipt_date,0,industry,'rejected',contributor_zip FROM contributions WHERE sub_id='one'")
        with self.assertRaisesRegex(ValueError,'1 added'):guard.verify(self.source)

    def test_missing_rejected_source_record_is_refused(self):
        self.change("DELETE FROM contributions WHERE sub_id='two'")
        with self.assertRaisesRegex(ValueError,'1 missing'):guard.verify(self.source)

    def test_balanced_amount_changes_are_refused_despite_unchanged_total(self):
        self.change("UPDATE contributions SET contribution_receipt_amount=CASE sub_id WHEN 'one' THEN 101.25 ELSE 199.50 END")
        with self.assertRaisesRegex(ValueError,'2 changed'):guard.verify(self.source)

    def test_same_source_query_copy_is_allowed_but_financial_conflict_is_refused(self):
        self.change('INSERT INTO corporate_giving SELECT * FROM contributions')
        self.assertEqual(guard.verify(self.source)['counts']['repeated_source_observations'],2)
        self.change("UPDATE corporate_giving SET contribution_receipt_amount=999 WHERE sub_id='one'")
        with self.assertRaisesRegex(ValueError,'1 conflicting_source_copies'):guard.verify(self.source)

    def test_original_database_industry_and_cycles_must_match_snapshot(self):
        for key,value in [('database_sha256','different'),('industry','Elsewhere'),('cycles',[2024])]:
            with self.subTest(key=key):
                original=self.receipt[key];self.receipt[key]=value;self.save_pointer()
                with self.assertRaisesRegex(ValueError,'original published source scope'):guard.verify(self.source)
                self.receipt[key]=original;self.save_pointer()

    def test_changed_snapshot_is_refused_by_hash(self):
        with closing(sqlite3.connect(self.snapshot)) as db, db:db.execute("DELETE FROM facts WHERE id='two'")
        with self.assertRaisesRegex(ValueError,'snapshot failed its fingerprint'):guard.verify(self.source)

    def test_invalid_calendar_dates_are_refused(self):
        self.change("UPDATE contributions SET contribution_receipt_date='2024-02-30' WHERE sub_id='one'")
        with self.assertRaisesRegex(ValueError,'invalid contribution date'):guard.verify(self.source)

    def test_durable_proof_avoids_rescanning_but_metadata_edit_gets_new_proof(self):
        first=guard.verify(self.source);guard._verify_cached.cache_clear()
        with patch.object(guard,'_compare',side_effect=AssertionError('unexpected rescan')):
            self.assertEqual(guard.verify(self.source)['proof_token'],first['proof_token'])
        self.change("UPDATE contributions SET contributor_zip='02101'")
        second=guard.verify(self.source)
        self.assertNotEqual(first['proof_token'],second['proof_token'])

    def test_active_wal_is_refused_even_after_memory_cache_hit(self):
        guard.verify(self.source)
        wal=Path(str(self.database)+'-wal');wal.write_bytes(b'active')
        with self.assertRaisesRegex(ValueError,'active uncheckpointed'):guard.verify(self.source)
        wal.unlink()

    def test_unstable_source_cannot_publish_a_cached_proof(self):
        compare=guard._compare
        def changing(*args):
            counts=compare(*args)
            self.change("UPDATE contributions SET contribution_receipt_amount=999 WHERE sub_id='one'")
            return counts
        with patch.object(guard,'_compare',side_effect=changing):
            with self.assertRaisesRegex(ValueError,'changed during verification'):guard.verify(self.source)
        self.assertFalse(list(guard.CACHE_ROOT.glob('*.json')))

    def test_old_loaded_algorithm_cannot_claim_a_new_file_fingerprint(self):
        with patch.object(guard,'LOADED_ALGORITHM_STAMP',('old-code',0,0,0)):
            with self.assertRaisesRegex(ValueError,'Restart the app'):guard.verify(self.source)

    def test_proof_key_names_the_code_by_content_not_line_endings(self):
        def token(body,newline):
            copy=self.root/('algorithm-%d.py'%len(list(self.root.glob('algorithm-*.py'))))
            copy.write_bytes(body.replace('\n',newline).encode());guard._verify_cached.cache_clear()
            with patch.object(guard,'__file__',str(copy)),patch.object(guard,'LOADED_ALGORITHM_STAMP',guard._stamp(copy)):
                return guard.verify(self.source)['proof_token']
        lf=token('A=1\nB=2\n','\n')
        self.assertEqual(token('A=1\nB=2\n','\r\n'),lf)
        self.assertNotEqual(token('A=1\nB=3\n','\n'),lf)

    def test_original_database_fingerprint_is_checked_on_every_source_epoch(self):
        original={**self.source,'hydration_manifest':None,'original_database_sha256':self.source['database_sha256']}
        self.assertTrue(guard.verify(original)['full_financial_universe_preserved'])
        self.change("UPDATE contributions SET contribution_receipt_amount=999 WHERE sub_id='one'")
        with self.assertRaisesRegex(ValueError,'published fingerprint check'):guard.verify(original)

    def test_original_source_wal_cannot_hide_behind_cached_analytical_index(self):
        original={**self.source,'hydration_manifest':None,'original_database_sha256':self.source['database_sha256']}
        guard.verify(original)
        wal=Path(str(self.database)+'-wal');wal.write_bytes(b'active')
        try:
            with self.assertRaisesRegex(ValueError,'active uncheckpointed'):guard.verify(original)
        finally:wal.unlink()


if __name__=='__main__':unittest.main()

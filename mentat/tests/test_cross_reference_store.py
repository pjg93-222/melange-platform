import hashlib
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from cross_reference_store import (EvidenceStore, canonical, digest, fetch_exact_evidence,
    fetch_filing_header_evidence, filing_number)
from fec_filing_headers import fetch_header


def row(**changes):
    value = dict(sub_id='400000000000000001', committee_id='C00000001', contributor_name='ACME',
        contribution_receipt_amount=123.45, contribution_receipt_date='2024-01-02',
        cycle=2024, file_num=0, transaction_id='T1')
    value.update(changes)
    return value


def receipt(**changes):
    value = row(); value.pop('file_num'); value.update(file_number=123,
        filing_form='F3X', line_number='11C', entity_type='ORG')
    value.update(changes)
    return value


def prefix():
    report=['']*30;report[:3]=['F3XA','C00000001','TEST COMMITTEE']
    report[9]='Q1';report[13]='20240101';report[14]='20240331'
    return ('\x1c'.join(['HDR','FEC','8.5','Fixture','1','FEC-99','1',''])+'\r\n'+
            '\x1c'.join(report)+'\r\n').encode()


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.store=EvidenceStore(self.root/'evidence.db')

    def tearDown(self):
        self.store.close();self.temp.cleanup()

    def test_non_json_errors_preserved_and_provenance_retained(self):
        source=sqlite3.connect(':memory:')
        source.executescript('''CREATE TABLE fec_response_archive(archive_sha256 TEXT,payload_json TEXT,payload_kind TEXT,created_at TEXT);
          CREATE TABLE fec_response_observations(archive_sha256 TEXT,query_json TEXT,query_sha256 TEXT,http_status INTEGER,observed_at TEXT,last_observed_at TEXT);''')
        query=canonical({'request':{'url':'https://api.open.fec.gov/v1/schedules/schedule_a/',
            'params':{'contributor_employer':'ACME','two_year_transaction_period':'2024'}}})
        for body,status in [('<html>temporarily unavailable</html>',503),
                            (canonical({'results':[receipt()]}),200)]:
            sha=digest(body)
            source.execute('INSERT INTO fec_response_archive VALUES(?,?,?,?)',(sha,body,'fec_api_response_body','2026-09-01'))
            source.execute('INSERT INTO fec_response_observations VALUES(?,?,?,?,?,?)',
                (sha,query,digest(query),status,'2026-09-01','2026-09-10'))
        self.assertEqual(self.store.import_database(source),2)
        self.assertEqual(self.store.import_database(source),2)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM records').fetchone()[0],1)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM documents').fetchone()[0],2)
        observations=self.store.db.execute('SELECT source_url,last_observed_at,http_status FROM document_observations').fetchall()
        self.assertEqual(len(observations),2)
        self.assertTrue(all('contributor_employer=ACME' in o[0] for o in observations))
        self.assertTrue(all(o[1]=='2026-09-10' for o in observations))
        self.assertTrue(any('temporarily unavailable' in r[0] for r in self.store.db.execute('SELECT payload_json FROM documents')))
        self.assertEqual(self.store.resolve([row()])[0][row()['sub_id']]['_observed_at'],'2026-09-10')
        source.close()

    def test_zero_filing_is_unknown_and_merged_fields_cite_all_sources(self):
        for value in (0,'0',0.0,'0.0','',None):self.assertIsNone(filing_number(value))
        old=self.store.archive({'results':[receipt(contributor_id='C00000123')]},
            'https://api.open.fec.gov/v1/schedules/schedule_a/','2026-09-01')
        newer=receipt();newer.pop('filing_form')
        fresh=self.store.archive({'results':[newer]},'https://api.open.fec.gov/v1/schedules/schedule_a/','2026-09-10')
        known,headers,conflicts=self.store.resolve([row(file_num='0')])
        self.assertFalse(conflicts);self.assertFalse(headers)
        evidence=known[row()['sub_id']]
        self.assertEqual(evidence['filing_form'],'F3X')
        self.assertEqual(set(evidence['_evidence_ids']),{old,fresh})
        self.assertEqual(evidence['_field_evidence_ids']['filing_form'],[old])
        self.assertEqual(evidence['_evidence_id'],fresh)

    def test_document_and_index_corruption_are_rejected_even_after_cached_verification(self):
        source='https://api.open.fec.gov/v1/schedules/schedule_a/'
        sha=self.store.archive({'results':[receipt()]},source)
        self.store.resolve([row()])
        self.store.db.execute("UPDATE documents SET payload_json='{}' WHERE sha256=?",(sha,))
        with self.assertRaisesRegex(ValueError,'document hash mismatch'):
            self.store.resolve([row()])

    def test_record_payload_hash_and_membership_are_verified(self):
        sha=self.store.archive({'results':[receipt()]},'https://api.open.fec.gov/v1/schedules/schedule_a/')
        self.store.db.execute("UPDATE records SET payload_json='{}'")
        with self.assertRaisesRegex(ValueError,'record hash mismatch'):
            self.store.resolve([row()])
        altered=canonical(receipt(contribution_receipt_amount=999))
        self.store.db.execute('UPDATE records SET payload_json=?,row_sha256=?',(altered,digest(altered)))
        with self.assertRaisesRegex(ValueError,'absent from its referenced document'):
            self.store.resolve([row()])

    def test_accounting_sensitive_metadata_conflicts_are_not_silently_merged(self):
        for field,a,b in [('memo_code','','X'),('memoed_subtotal',False,True),
            ('memo_text','','refund'),('receipt_type_full','CONTRIBUTION','INTEREST'),
            ('entity_type','ORG','IND'),('back_reference_schedule_name','SA','SB')]:
            with self.subTest(field=field):
                sid='4'+str(len(field))
                self.store.archive({'results':[receipt(sub_id=sid,**{field:a})]},
                    'https://api.open.fec.gov/v1/schedules/schedule_a/','2026-09-01')
                self.store.archive({'results':[receipt(sub_id=sid,**{field:b})]},
                    'https://api.open.fec.gov/v1/schedules/schedule_a/','2026-09-10')
                known,_,conflicts=self.store.resolve([row(sub_id=sid)])
                self.assertNotIn(sid,known)
                self.assertTrue(any(field in c.get('fields',[]) for c in conflicts))

    def test_partial_known_evidence_is_fetched_once_and_original_query_cycle_used(self):
        incomplete=receipt(filing_form='',line_number='',entity_type='')
        self.store.archive({'results':[incomplete]},'https://api.open.fec.gov/v1/schedules/schedule_a/','2025-01-01')
        calls=[]
        def transport(spec,key):
            calls.append(spec)
            return {'status':200,'headers':{},'body':canonical({'results':[incomplete]})}
        item=row(source_query_cycle=2026)
        first=fetch_exact_evidence(self.store,[item],1,self.root/'exact',self.root/'budget.db',transport=transport)
        self.assertEqual(first['requested'],1);self.assertEqual(first['still_partial'],1)
        self.assertEqual(calls[0]['params']['two_year_transaction_period'],2026)
        second=fetch_exact_evidence(self.store,[item],1,self.root/'exact',self.root/'budget.db',transport=transport)
        self.assertEqual(second['requested'],0);self.assertEqual(len(calls),1)

    def test_exhausted_api_budget_defers_instead_of_waiting_an_hour(self):
        budget=self.root/'budget.db'
        with closing(sqlite3.connect(budget)) as db, db:
            db.execute('CREATE TABLE api_request_budget(budget_id TEXT PRIMARY KEY,window_started_epoch REAL,requests_used INTEGER)')
            key_id='openfec:sha256:'+hashlib.sha256(b'test-transport').hexdigest()[:12]
            db.execute('INSERT INTO api_request_budget VALUES(?,?,1000)',(key_id,time.time()))
        started=time.monotonic()
        result=fetch_exact_evidence(self.store,[row()],1,self.root/'exact',budget,
            transport=lambda *_:self.fail('Budget exhaustion must not issue HTTP'),max_budget_wait_seconds=.05,max_seconds=1)
        self.assertLess(time.monotonic()-started,2)
        self.assertEqual(result['requested'],0);self.assertIn('budget unavailable',result['deferred_reason'])

    def test_headers_use_cache_then_bounded_public_requests(self):
        cached=self.root/'headers';fetch_header(123,cached,transport=lambda _:prefix())
        calls=[]
        def transport(url):calls.append(url);return prefix()
        rows=[row(file_num=123),row(sub_id='2',file_num=124),row(sub_id='3',file_num=125)]
        result=fetch_filing_header_evidence(self.store,rows,1,cached,self.root/'budget.db',transport=transport)
        self.assertEqual((result['cached'],result['requested'],result['recovered']),(1,1,2))
        self.assertEqual(len(calls),1)
        _,headers,_=self.store.resolve(rows)
        self.assertEqual(set(headers),{'123','124'})
        self.assertEqual(headers['123']['original_file_num'],99)
        result=fetch_filing_header_evidence(self.store,rows,0,cached,self.root/'budget.db',transport=transport)
        self.assertEqual(result['requested'],0)

    def test_exhausted_public_header_budget_defers_quickly(self):
        budget=self.root/'budget.db'
        with closing(sqlite3.connect(budget)) as db, db:
            db.execute('CREATE TABLE api_request_budget(budget_id TEXT PRIMARY KEY,window_started_epoch REAL,requests_used INTEGER)')
            db.execute("INSERT INTO api_request_budget VALUES('docquery:filing-headers:v1',?,1000)",(time.time(),))
        started=time.monotonic()
        result=fetch_filing_header_evidence(self.store,[row(file_num=123)],1,self.root/'headers',budget,
            transport=lambda _:self.fail('No public request is budgeted'),max_budget_wait_seconds=.05,max_seconds=1)
        self.assertLess(time.monotonic()-started,2);self.assertEqual(result['requested'],0)
        self.assertIn('budget unavailable',result['failures'][0]['reason'])


if __name__=='__main__':unittest.main()

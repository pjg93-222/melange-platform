"""Projection fidelity, source drift and concurrent query-cache regressions."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import hashlib
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
import contribution_data as data
import contribution_query as query
import contribution_query_store as store
import test_contribution_data as fixtures

class QueryStoreTests(unittest.TestCase):
    def setUp(self):
        fixtures.ContributionQueryTests.setUp(self)
        self.enterContext(patch.object(store,'ROOT',Path(self.tmp.name)/'query-cache'))
        self.enterContext(patch.object(store,'_memory',store.OrderedDict()))
        self.enterContext(patch.object(store,'_memory_bytes',0))
        self.enterContext(patch.object(store,'_verified',{}))

    def result(self,request,reference=False):
        result=(data._query_ready if reference else query._query_ready)(self.path,request,self.source,*data._validate(request))
        result['provenance'].pop('query_engine_sha256',None);result['provenance'].pop('query_projection_sha256',None)
        result['provenance'].pop('field_semantics_sha256',None)
        for column in result['columns']:
            for key in ['label','data_type','table','discrete','is_time','geographic_role','description','aggregation']:column.pop(key,None)
            if column['role']=='dimension':column['type']='category';column.pop('unit',None)
        if result['basis']=='original_supported':
            result['basis_label']='Supported original giving (gross subset)'
            for column in result['columns']:
                if column['id']=='amount':column['name']='Supported gross amount'
        return result

    def test_projection_preserves_all_detail_dimensions_and_donor_null_semantics(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE facts SET contributor_key=NULL,recipient='Committee A',employer='Example LLC',city='Boston' WHERE source_id='c'")
            db.execute("UPDATE facts SET contributor_key='',recipient='Committee B',employer='example llc',city='Cambridge' WHERE source_id='d'")
            db.commit()
        original_sha=data._hash(self.path)
        for basis in data.BASES:
            for dimension in store.DETAIL:
                request={'basis':basis,'dimensions':[dimension,'zip5'],'measures':list(data.MEASURES)}
                with self.subTest(basis=basis,dimension=dimension):self.assertEqual(self.result(request),self.result(request,True))
        for op,value in [('eq','Example LLC'),('ne','Example LLC'),('gte','E'),('contains','LLC'),('in',['Example LLC','example llc']),('eq',None)]:
            request={'dimensions':['city'],'filters':[{'field':'employer','op':op,'value':value}]}
            with self.subTest(op=op,value=value):self.assertEqual(self.result(request),self.result(request,True))
        self.assertEqual(data._hash(self.path),original_sha)

    def test_repeated_and_simultaneous_requests_compute_once_and_return_copies(self):
        calls=[]
        def compute():calls.append(1);time.sleep(.03);return {'amount':10000,'rows':[{'zip':'01234'}]}
        with ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(lambda _:store.cached('test',{'query':1},compute),range(16)))
        self.assertEqual(len(calls),1)
        results[0]['rows'][0]['zip']='mutated'
        self.assertEqual(store.cached('test',{'query':1},compute)['rows'][0]['zip'],'01234')
        store._memory.clear();store._memory_bytes=0
        self.assertEqual(store.cached('test',{'query':1},compute)['amount'],10000)
        self.assertEqual(len(calls),1)

    def test_cross_process_requests_share_one_completed_result(self):
        script=Path(self.tmp.name)/'worker.py';marker=Path(self.tmp.name)/'computations.txt'
        script.write_text("import sys,time\nfrom pathlib import Path\nsys.path.insert(0,"+repr(str(Path(store.__file__).parent))+" )\nimport contribution_query_store as s\ns.ROOT=Path(sys.argv[1])\ndef work():\n p=Path(sys.argv[2]);p.write_text(p.read_text()+'x' if p.exists() else 'x');time.sleep(.1);return {'amount':12345}\nassert s.cached('process',{'same':True},work)=={'amount':12345}\n")
        children=[subprocess.Popen([sys.executable,str(script),str(store.ROOT),str(marker)],stdout=subprocess.PIPE,stderr=subprocess.PIPE) for _ in range(3)]
        for child in children:
            out,err=child.communicate(timeout=20);self.assertEqual(child.returncode,0,err.decode())
        self.assertEqual(marker.read_text(),'x')

    def test_changed_index_cannot_reuse_previous_result_or_projection(self):
        request={'dimensions':['zip5']};before=self.result(request)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE facts SET zip5='54321' WHERE source_id='a'");db.commit()
        after=self.result(request)
        self.assertNotEqual(before['rows'],after['rows'])
        self.assertEqual(after,self.result(request,True))

    def test_source_drift_during_projection_never_publishes_a_view(self):
        with patch.object(store,'source_stable',side_effect=[True,True,False]):
            with self.assertRaisesRegex(ValueError,'changed'):store.view(self.path)
        self.assertFalse(list((store.ROOT/'views').glob('*.sqlite')))

    def test_source_drift_and_active_writes_reject_cached_or_new_results(self):
        calls=[]
        def work():calls.append(1);return {'amount':10000}
        with self.assertRaisesRegex(ValueError,'changed'):
            store.cached('unstable',{},work,stable=lambda:not calls)
        self.assertFalse(list((store.ROOT/'responses'/'unstable').glob('*.json')))
        self.result({'dimensions':['cycle']})
        Path(str(self.path)+'-journal').write_bytes(b'active write')
        with self.assertRaisesRegex(ValueError,'changed|active writes'):self.result({'dimensions':['cycle']})

    def test_corrupted_cached_result_and_modified_sidecar_are_rebuilt(self):
        calls=[]
        def work():calls.append(1);return {'amount':10000}
        store.cached('corrupt',{},work);store._memory.clear();store._memory_bytes=0
        response=next((store.ROOT/'responses'/'corrupt').glob('*.json'));response.write_text('{truncated')
        self.assertEqual(store.cached('corrupt',{},work),{'amount':10000});self.assertEqual(len(calls),2)
        projected=store.view(self.path)
        with closing(sqlite3.connect(projected)) as db:db.execute('UPDATE facts SET receipt_cents=999999');db.commit()
        projected=store.view(self.path)
        with closing(store._readonly(projected)) as db:self.assertEqual(db.execute('SELECT SUM(receipt_cents) FROM facts').fetchone()[0],25000)

    def test_same_stat_sqlite_edits_invalidate_verified_and_cached_hashes(self):
        # Windows can report identical size/mtime/creation-time across a quick
        # SQLite commit. Force that observed collision on every platform, and
        # prime the separately memoized digest so clearing _verified alone is
        # insufficient to pass this regression.
        projected=store.view(self.path)
        original_stamp=data._stamp(projected);read_stamp=data._stamp
        original_sha=data._hash(projected)
        def same_projection_stamp(path):
            return original_stamp if Path(path).resolve()==projected.resolve() else read_stamp(path)
        for clear_verification in (False,True):
            with self.subTest(clear_verification=clear_verification),patch.object(data,'_stamp',side_effect=same_projection_stamp):
                with closing(sqlite3.connect(projected)) as db:
                    db.execute('UPDATE facts SET receipt_cents=999999');db.commit()
                self.assertEqual(data._stamp(projected),original_stamp)
                self.assertNotEqual(hashlib.sha256(projected.read_bytes()).hexdigest(),original_sha)
                self.assertEqual(data._hash(projected),original_sha)
                if clear_verification:store._verified.clear()
                repaired=store.view(self.path)
                with closing(store._readonly(repaired)) as db:
                    self.assertEqual(db.execute('SELECT SUM(receipt_cents) FROM facts').fetchone()[0],25000)

    def test_query_code_fingerprint_separates_completed_cache_entries(self):
        calls=[]
        def work():calls.append(1);return len(calls)
        self.assertEqual(store.cached('version',{},work),1)
        with patch.object(store,'LOADED_SHA','different code epoch'):self.assertEqual(store.cached('version',{},work),2)

    def test_validated_projection_with_new_wal_is_rejected(self):
        self.result({'dimensions':['cycle']})
        projected=store.view(self.path)
        writer=sqlite3.connect(projected);self.addCleanup(writer.close)
        writer.execute('PRAGMA journal_mode=WAL')
        # Validate this unchanged, checkpointed main-file epoch first, then
        # commit new money exclusively to WAL without changing main stamps.
        writer.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        proof_path=projected.with_suffix('.json');proof=json.loads(proof_path.read_text())
        proof['sha256']=data._hash(projected);proof_path.write_text(json.dumps(proof))
        store._verified.clear();self.assertEqual(store.view(self.path),projected)
        stamp=data._stamp(projected)
        writer.execute('UPDATE facts SET receipt_cents=999999');writer.commit()
        self.assertEqual(data._stamp(projected),stamp)
        with self.assertRaisesRegex(ValueError,'active writes'):
            self.result({'dimensions':['company']})

    def test_projection_change_during_sql_never_publishes_a_result(self):
        projected=store.view(self.path);reader=store._readonly
        def connection(path):
            db=reader(path)
            original_execute=db.execute
            class Proxy:
                def __getattr__(self,name):return getattr(db,name)
                def execute(self,sql,args=()):
                    result=original_execute(sql,args)
                    if sql.startswith('WITH grouped'):
                        Path(str(projected)+'-journal').write_bytes(b'concurrent projection write')
                    return result
            return Proxy()
        with patch.object(store,'_readonly',side_effect=connection):
            with self.assertRaisesRegex(ValueError,'projection changed'):
                self.result({'dimensions':['company']})
        self.assertFalse(list((store.ROOT/'responses'/'results').glob('*.json')))

    def test_wrong_shaped_json_cache_envelopes_and_proofs_are_misses(self):
        for invalid in (None,[],123,'text',{'payload':None}):
            with self.subTest(invalid=invalid):
                result=store.cached('shape',{},lambda:{'amount':10000})
                path=next((store.ROOT/'responses'/'shape').glob('*.json'))
                path.write_text(json.dumps(invalid));store._memory.clear();store._memory_bytes=0
                self.assertEqual(store.cached('shape',{},lambda:{'amount':10000}),result)
                projected=store.view(self.path)
                projected.with_suffix('.json').write_text(json.dumps(invalid))
                self.assertEqual(store.view(self.path),projected)


if __name__=='__main__':unittest.main()

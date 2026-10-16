from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json,os,sqlite3,subprocess,sys,tempfile,unittest
from pathlib import Path

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT/'tools'))
import api_request_budget as budget
from api_request_pool import RequestPool
KEY='fixture-budget-a'
R=Path('C:/Program Files/R/R-4.6.0/bin/Rscript.exe')

def ledger(path,rows):
    with closing(sqlite3.connect(path)) as db,db:
        budget.ensure_schema(db)
        db.executemany('INSERT INTO api_request_budget VALUES (?,?,?)',rows)

def rcall(*args):
    environment=os.environ.copy()
    for name in ('FEC_API_KEY','FEC_API_KEYS','MENTAT_API_BUDGET_ID','MENTAT_API_BUDGET_DB',
                 'MENTAT_API_BUDGET_IMPORTS','MENTAT_REQUEST_POOL_URL','MENTAT_REQUEST_POOL_TOKEN'):
        environment.pop(name,None)
    result=subprocess.run([str(R),str(PROJECT/'tests/budget_interop.R'),str(PROJECT),*map(str,args)],
        env=environment,capture_output=True,text=True,timeout=30)
    if result.returncode:
        raise AssertionError(result.stderr+result.stdout)
    return json.loads(result.stdout)

class MigrationTests(unittest.TestCase):
    def test_r_python_identity_aliases_and_shared_path_are_identical(self):
        identity=rcall('identity')
        self.assertEqual(identity['id'],budget.budget_id(KEY))
        self.assertEqual(identity['aliases'],list(budget.budget_aliases(KEY)))
        with tempfile.TemporaryDirectory() as tmp:
            host=Path(tmp)/'host';host.mkdir()
            first=rcall('path',host,Path(tmp)/'data-a','')
            second=rcall('path',host,Path(tmp)/'different-project-data','')
            expected=budget.budget_path(PROJECT,environment={'LOCALAPPDATA':str(host)})
            self.assertEqual(Path(first),expected);self.assertEqual(first,second)
            explicit=Path(tmp)/'explicit.sqlite'
            self.assertEqual(Path(rcall('path',host,Path(tmp)/'data',explicit)),explicit)
            self.assertEqual(budget.budget_path(PROJECT,environment={'MENTAT_API_BUDGET_DB':str(explicit)}),explicit)

    def test_overlapping_alias_usage_is_preserved_and_refusal_commits_imports(self):
        with tempfile.TemporaryDirectory() as tmp:
            shared=Path(tmp)/'budget.sqlite';identity=budget.budget_id(KEY)
            ledger(shared,[(identity,95,3),(budget.legacy_budget_id(KEY),90,2)])
            result=budget.reserve(shared,identity,aliases=budget.budget_aliases(KEY),now=100,cap=5)
            self.assertFalse(result['admitted']);self.assertEqual(result['requests_used'],5)
            self.assertEqual(result['window_started_epoch'],95)
            result=rcall('reserve',shared,1,5,101,'')
            self.assertEqual(result['error'],'mentat_api_budget_exhausted')
            with closing(sqlite3.connect(shared)) as db:
                self.assertEqual(db.execute('SELECT requests_used FROM api_request_budget WHERE budget_id=?',(identity,)).fetchone()[0],5)
                self.assertEqual(db.execute('SELECT requests_used FROM api_request_budget WHERE budget_id=?',(budget.legacy_budget_id(KEY),)).fetchone()[0],2)

    def test_external_increases_are_charged_once_and_decreases_do_not_refund(self):
        with tempfile.TemporaryDirectory() as tmp:
            shared=Path(tmp)/'budget.sqlite';legacy=Path(tmp)/'old.sqlite';identity=budget.budget_id(KEY)
            ledger(legacy,[(budget.legacy_budget_id(KEY),90,2)])
            first=rcall('reserve',shared,1,10,100,legacy)
            self.assertEqual(first['requests_used'],3)
            result=budget.reserve(shared,identity,aliases=budget.budget_aliases(KEY),legacy_paths=[legacy],now=101,cap=10)
            self.assertEqual(result['requests_used'],4)
            with closing(sqlite3.connect(legacy)) as db,db:db.execute('UPDATE api_request_budget SET requests_used=4')
            self.assertEqual(rcall('reserve',shared,1,10,102,legacy)['requests_used'],7)
            with closing(sqlite3.connect(legacy)) as db,db:db.execute('UPDATE api_request_budget SET requests_used=1')
            result=budget.reserve(shared,identity,0,aliases=budget.budget_aliases(KEY),legacy_paths=[legacy],now=103,cap=10)
            self.assertEqual(result['requests_used'],7)
            with closing(sqlite3.connect(shared)) as db:
                self.assertEqual(db.execute('SELECT MAX(adopted_requests) FROM api_request_budget_imports').fetchone()[0],4)

    def test_expired_evidence_expires_naturally_and_unkeyed_usage_is_conservative(self):
        with tempfile.TemporaryDirectory() as tmp:
            shared=Path(tmp)/'budget.sqlite';identity=budget.budget_id(KEY)
            ledger(shared,[('openfec',90,2)])
            first=budget.reserve(shared,identity,aliases=budget.budget_aliases(KEY),now=100,cap=3)
            self.assertEqual(first['requests_used'],3)
            second=budget.reserve(shared,budget.budget_id('other-fixture'),aliases=budget.budget_aliases('other-fixture'),now=101,cap=3)
            self.assertEqual(second['requests_used'],3)
            self.assertEqual(rcall('reserve',shared,1,3,3690,'')['requests_used'],1)

    def test_invalid_legacy_counters_fail_closed_in_both_implementations(self):
        for bad in (None,-1,1.5,'invalid'):
            with self.subTest(bad=bad),tempfile.TemporaryDirectory() as tmp:
                shared=Path(tmp)/'budget.sqlite';legacy=Path(tmp)/'old.sqlite'
                # Deliberately nullable legacy schema to exercise strict evidence checks.
                with closing(sqlite3.connect(legacy)) as db,db:
                    db.execute('CREATE TABLE api_request_budget(budget_id TEXT,window_started_epoch REAL,requests_used)')
                    db.execute('INSERT INTO api_request_budget VALUES (?,?,?)',(budget.legacy_budget_id(KEY),90,bad))
                with self.assertRaisesRegex(ValueError,'invalid usage evidence'):
                    budget.reserve(shared,budget.budget_id(KEY),aliases=budget.budget_aliases(KEY),legacy_paths=[legacy],now=100)
                self.assertIn('invalid usage evidence',rcall('reserve',shared,1,10,100,legacy)['message'])

    def test_validation_preserves_typed_inputs_and_integral_real_legacy_counts(self):
        for field,bad in (('requests',True),('requests',-1),('requests',1.5),
                          ('cap',True),('cap',0),('cap',1.5)):
            values={'requests':1,'cap':10};values[field]=bad
            with self.subTest(field=field,bad=bad),tempfile.TemporaryDirectory() as tmp:
                with self.assertRaisesRegex(ValueError,'Invalid API budget reservation'):
                    budget.reserve(Path(tmp)/'shared.sqlite',budget.budget_id(KEY),now=100,**values)
                self.assertIn('Invalid API budget reservation',rcall('validate',json.dumps(values))['message'])
        # R's normal numeric literal is double; preserve integral request/cap semantics.
        self.assertEqual(rcall('validate','{"requests":1.0,"cap":3.0}')['requests_used'],1)
        with tempfile.TemporaryDirectory() as tmp:
            shared=Path(tmp)/'shared.sqlite';legacy=Path(tmp)/'old.sqlite'
            with closing(sqlite3.connect(legacy)) as db,db:
                db.execute('CREATE TABLE api_request_budget(budget_id TEXT,window_started_epoch REAL,requests_used REAL)')
                db.execute('INSERT INTO api_request_budget VALUES (?,?,?)',(budget.legacy_budget_id(KEY),90,2.0))
            self.assertEqual(rcall('reserve',shared,1,10,100,legacy)['requests_used'],3)
            result=budget.reserve(shared,budget.budget_id(KEY),aliases=budget.budget_aliases(KEY),legacy_paths=[legacy],now=101,cap=10)
            self.assertEqual(result['requests_used'],4)

    def test_pool_and_r_cannot_spend_separate_capacity(self):
        with tempfile.TemporaryDirectory() as tmp:
            shared=Path(tmp)/'budget.sqlite'
            first=rcall('reserve',shared,2,3,100,'')
            self.assertEqual(first['requests_used'],2)
            pool=RequestPool([KEY],shared,1,cap=3,transport=lambda *_:None)
            try:
                self.assertTrue(pool.reserve(0,101));self.assertFalse(pool.reserve(0,102))
            finally:pool.close()
            self.assertEqual(rcall('reserve',shared,1,3,103,'')['error'],'mentat_api_budget_exhausted')

    def test_concurrent_reservations_remain_atomic(self):
        with tempfile.TemporaryDirectory() as tmp:
            shared=Path(tmp)/'budget.sqlite';identity=budget.budget_id(KEY)
            with ThreadPoolExecutor(12) as pool:
                results=list(pool.map(lambda _:budget.reserve(shared,identity,now=100,cap=5),range(20)))
            self.assertEqual(sum(result['admitted'] for result in results),5)
            with closing(sqlite3.connect(shared)) as db:
                self.assertEqual(db.execute('SELECT requests_used FROM api_request_budget').fetchone()[0],5)

    def test_inert_http_bodies_archive_store_and_merge_without_losing_integer_ids(self):
        import contextlib,io
        import merge_company_shards
        with tempfile.TemporaryDirectory() as tmp:
            environment=os.environ.copy()
            environment.pop('FEC_API_KEY',None)
            result=subprocess.run([str(R),str(PROJECT/'tests/test_api_budget_producer.R'),str(PROJECT),tmp],
                env=environment,capture_output=True,text=True,timeout=30,cwd=PROJECT)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertIn('test_api_budget_producer: PASS',result.stdout)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(merge_company_shards.main([str(Path(tmp)/'target.sqlite'),
                    '--company-order-json','["Fixture"]',str(Path(tmp)/'shard.sqlite')+'::Fixture']),0)
            with closing(sqlite3.connect(Path(tmp)/'target.sqlite')) as db:
                self.assertEqual(db.execute('SELECT sub_id,typeof(sub_id) FROM contributions ORDER BY sub_id').fetchall(),
                    [('400000000000000001','text'),('400000000000000003','text')])
                self.assertAlmostEqual(db.execute('SELECT SUM(contribution_receipt_amount) FROM contributions').fetchone()[0],103.43)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM fec_response_archive').fetchone()[0],2)

if __name__=='__main__':unittest.main()

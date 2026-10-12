from contextlib import closing
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from api_request_pool import RequestPool, ENDPOINT
import unittest
import tempfile
import threading
import time
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import json
import os
import subprocess


def specs(n):return [{'id':str(i),'url':ENDPOINT,'params':{'contributor_employer':'Fictional Company','test':str(i)}} for i in range(n)]

INVALID_KEY_BODY='{"error":{"code":"API_KEY_INVALID","message":"The API key supplied is not valid."}}'


class PoolTests(unittest.TestCase):
    def test_r_crash_resumes_durable_page_through_local_http_pool(self):
        binary=Path('C:/Program Files/R/R-4.6.0/bin/Rscript.exe')
        if not binary.exists():self.skipTest('Rscript unavailable')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);calls=[]
            def fetch(spec,key):
                cursor=spec['params'].get('last_index')
                calls.append((spec['id'],cursor))
                number='2' if cursor else '1'
                next_cursor={} if cursor else {'last_index':'1','last_contribution_receipt_date':'2026-01-01'}
                return {'status':200,'headers':{},'body':json.dumps({'results':[{'sub_id':number}],
                    'pagination':{'count':2,'is_count_exact':True,'last_indexes':next_cursor}})}
            pool=RequestPool(['fixture-a','fixture-b'],root/'budget.db',2,transport=fetch)
            try:
                url=pool.start();project=(Path(__file__).resolve().parents[1]/'R').as_posix()
                script=root/'checkpoint.R'
                script.write_text(f'''args<-commandArgs(trailingOnly=TRUE)
for(f in c("paths.R","schema.R","ingest.R","collect.R","fec_api.R"))source(file.path("{project}",f))
con<-DBI::dbConnect(RSQLite::SQLite(),args[[1]])
DBI::dbExecute(con,"PRAGMA busy_timeout=60000")
init_schema(con)
unit<-list(id="fixture-unit",company="Fixture",cycle=2026L,kind="employer",phase="P1_employer",
  variants="FIXTURE",company_variants="FIXTURE",exclusions=character(),query_field="contributor_employer",query_filters=list())
prior<-fec_api_load_progress(con,list(unit))
result<-fec_api_collect_work_units(list(unit),api_key="unused",concurrency=2L,
  transport=fec_api_default_transport,initial_states=prior,sleep=function(...)NULL,jitter=function(...)0,
  page_callback=function(state,page){{
    fec_api_checkpoint_page(con,state,page)
    if(args[[2]]=="crash")quit(save="no",status=17L,runLast=FALSE)
  }})
stopifnot(result$units[[unit$id]]$rows_seen==2L,result$units[[unit$id]]$complete)
DBI::dbDisconnect(con)
''',encoding='utf-8')
                env=os.environ.copy();env.update(MENTAT_REQUEST_POOL_URL=url,
                    MENTAT_REQUEST_POOL_TOKEN=pool.token,MENTAT_REQUEST_POOL_LANE='fixture-lane')
                database=root/'lane.db'
                first=subprocess.run([str(binary),str(script),str(database),'crash'],env=env,
                    capture_output=True,text=True,timeout=30)
                self.assertEqual(first.returncode,17,first.stderr+first.stdout)
                with closing(sqlite3.connect(database)) as db:
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM api_page_checkpoints').fetchone()[0],1)
                    self.assertEqual(db.execute('SELECT last_page,rows_seen FROM api_work_progress').fetchone(),(0,1))
                second=subprocess.run([str(binary),str(script),str(database),'resume'],env=env,
                    capture_output=True,text=True,timeout=30)
                self.assertEqual(second.returncode,0,second.stderr+second.stdout)
                self.assertEqual(calls, [('fixture-unit',None),('fixture-unit','1')])
                with closing(sqlite3.connect(database)) as db:
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM api_page_checkpoints').fetchone()[0],2)
                    self.assertEqual(db.execute('SELECT last_page,rows_seen FROM api_work_progress').fetchone(),(1,2))
                    self.assertEqual(db.execute('PRAGMA quick_check').fetchone()[0],'ok')
            finally:pool.close()

    def test_r_transport_uses_shared_pool_and_preserves_batch_results(self):
        binary=Path('C:/Program Files/R/R-4.6.0/bin/Rscript.exe')
        if not binary.exists():self.skipTest('Rscript unavailable')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);barrier=threading.Barrier(3)
            def fetch(spec,key):
                barrier.wait(5)
                return {'status':200,'headers':{},'body':json.dumps({'id':spec['id']})}
            pool=RequestPool(['a','b','c'],root/'budget.db',3,transport=fetch)
            try:
                url=pool.start();env=os.environ.copy()
                env.update(MENTAT_REQUEST_POOL_URL=url,MENTAT_REQUEST_POOL_TOKEN=pool.token)
                project=(Path(__file__).resolve().parents[1]/'R').as_posix()
                script=root/'transport.R'
                script.write_text(f'''for(f in c("paths.R","schema.R","ingest.R","collect.R","fec_api.R"))source(file.path("{project}",f))
specs<-lapply(1:6,function(i)list(id=as.character(i),url=FEC_API_ENDPOINT,params=list(per_page="100",contributor_employer="FICTIONAL")))
result<-fec_api_fetch_with_retry(specs,api_key="unused",concurrency=6L)
stopifnot(length(result$responses)==6L,all(vapply(result$responses,function(r)r$status,integer(1))==200L))
stopifnot(identical(vapply(result$responses,function(r)jsonlite::fromJSON(r$body)$id,character(1)),as.character(1:6)))
''',encoding='utf-8')
                run=subprocess.run([str(binary),str(script)],env=env,capture_output=True,text=True,timeout=30)
                self.assertEqual(run.returncode,0,run.stderr)
                self.assertEqual(pool.snapshot()['peak_active_workers'],3)
            finally:pool.close()

    def test_six_workers_help_one_lane_without_key_collisions_and_keep_response_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            barrier=threading.Barrier(6);busy=set();lock=threading.Lock();used=[]
            def fetch(spec,key):
                with lock:
                    self.assertNotIn(key,busy);busy.add(key);used.append(key)
                barrier.wait(3)
                with lock:busy.remove(key)
                return {'status':200,'body':spec['id'],'headers':{}}
            pool=RequestPool([f'test-secret-{i}' for i in range(6)],Path(tmp)/'budget.db',6,transport=fetch)
            try:
                result=pool.batch(specs(12),'last-unfinished-lane')
                self.assertEqual([r['body'] for r in result],[str(i) for i in range(12)])
                self.assertEqual(pool.snapshot()['peak_active_workers'],6)
                self.assertEqual(len(set(used)),6)
                self.assertNotIn('test-secret',json.dumps(pool.snapshot()))
                with closing(sqlite3.connect(pool.budget_path)) as db:self.assertEqual(db.execute('SELECT SUM(requests_used) FROM api_request_budget').fetchone()[0],12)
            finally:pool.close()

    def test_exhausted_keys_wait_and_stop_releases_waiters(self):
        with tempfile.TemporaryDirectory() as tmp:
            pool=RequestPool(['a','b'],Path(tmp)/'budget.db',2,cap=1,transport=lambda *_:{'status':200,'body':'','headers':{}})
            try:
                pool.batch(specs(2),'first')
                with ThreadPoolExecutor(1) as ex:
                    waiting=ex.submit(pool.batch,specs(2),'second')
                    time.sleep(.05);self.assertFalse(waiting.done());pool.stop()
                    self.assertTrue(all(r.get('pool_stopped') for r in waiting.result(timeout=2)))
            finally:pool.close()

    def test_live_resize_and_http_auth_and_endpoint_restrictions(self):
        with tempfile.TemporaryDirectory() as tmp:
            pool=RequestPool(['a','b','c'],Path(tmp)/'budget.db',1,transport=lambda s,k:{'status':200,'body':s['id'],'headers':{}})
            try:
                url=pool.start();pool.resize(3);self.assertEqual(pool.snapshot()['requested_workers'],3)
                payload=json.dumps({'specs':specs(2),'lane':'one'}).encode()
                with self.assertRaises(HTTPError):urlopen(Request(url,data=payload))
                with urlopen(Request(url,data=payload,headers={'Authorization':'Bearer '+pool.token})) as response:
                    self.assertEqual(len(json.load(response)['responses']),2)
                with self.assertRaises(ValueError):pool.batch([{'url':'https://example.com','params':{}}],'one')
                with self.assertRaises(ValueError):pool.batch([{'url':ENDPOINT,'params':{'api_key':'secret'}}],'one')
            finally:pool.close()


class QuarantineTests(unittest.TestCase):
    """One dead key in the shared pool must cost one request, not a whole task."""

    def pool_with_one_bad_key(self, tmp, *, keys=4, bad=1, quarantine=True):
        used=[];lock=threading.Lock()
        def fetch(spec,key):
            with lock:used.append(key)
            if key==f'key-{bad}':return {'status':403,'headers':{},'body':INVALID_KEY_BODY}
            return {'status':200,'headers':{},'body':'{"ok":1}'}
        pool=RequestPool([f'key-{i}' for i in range(keys)],Path(tmp)/'budget.db',keys,
                         transport=fetch,quarantine=quarantine)
        return pool,used

    def batch_within(self, pool, count, seconds=5, lane='lane-1'):
        """Run a batch on a daemon thread so a regression fails instead of hanging."""
        box={}
        def run():
            try:box['result']=pool.batch(specs(count),lane)
            except BaseException as error:box['error']=error
        thread=threading.Thread(target=run,daemon=True);thread.start();thread.join(seconds)
        if thread.is_alive():
            pool.stop();thread.join(seconds)
            self.fail(f'pool.batch did not return within {seconds}s')
        if 'error' in box:raise box['error']
        return box['result']

    def test_invalid_key_is_quarantined_and_request_reissued(self):
        with tempfile.TemporaryDirectory() as tmp:
            pool,used=self.pool_with_one_bad_key(tmp)
            try:
                results=self.batch_within(pool,8)
                self.assertEqual([r['status'] for r in results],[200]*8)
                state=pool.snapshot()
                self.assertEqual(state['quarantined_keys'],[pool.ids[1][-12:]])
                self.assertGreaterEqual(state['reissued_requests'],1)
                self.assertEqual(state['healthy_keys'],3)
                # The fixture really did hand out the bad key, and never again after.
                self.assertEqual(used.count('key-1'),1)
                self.assertEqual(state['completed_requests'],len(used))
            finally:pool.close()

    def test_quarantine_disabled_surfaces_the_403_negative_control(self):
        # Negative control for the test above: with quarantine off the identical
        # fixture must fail a request, so the passing case cannot be vacuous.
        with tempfile.TemporaryDirectory() as tmp:
            pool,used=self.pool_with_one_bad_key(tmp,quarantine=False)
            try:
                results=self.batch_within(pool,8)
                self.assertIn(403,[r['status'] for r in results])
                self.assertIn('key-1',used)
                state=pool.snapshot()
                self.assertEqual(state['quarantined_keys'],[])
                self.assertEqual(state['reissued_requests'],0)
                self.assertEqual(state['healthy_keys'],4)
            finally:pool.close()

    def test_all_keys_invalid_returns_403_without_looping(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls=[];lock=threading.Lock()
            def fetch(spec,key):
                with lock:calls.append(key)
                return {'status':403,'headers':{},'body':INVALID_KEY_BODY}
            pool=RequestPool([f'key-{i}' for i in range(4)],Path(tmp)/'budget.db',4,transport=fetch)
            try:
                started=time.monotonic()
                results=self.batch_within(pool,6)
                self.assertLess(time.monotonic()-started,5)
                self.assertEqual([r['status'] for r in results],[403]*6)
                self.assertEqual(pool.snapshot()['healthy_keys'],0)
                self.assertLessEqual(len(calls),2*4*6)
                self.assertEqual(sorted(set(calls)),[f'key-{i}' for i in range(4)])
            finally:pool.close()

    def test_ratelimit_remaining_zero_cools_the_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            used=[];lock=threading.Lock()
            def fetch(spec,key):
                with lock:used.append(key)
                if key=='key-0':
                    return {'status':200,'headers':{'X-RateLimit-Remaining':'0','Retry-After':'30'},'body':'{"ok":1}'}
                return {'status':200,'headers':{},'body':'{"ok":1}'}
            pool=RequestPool([f'key-{i}' for i in range(3)],Path(tmp)/'budget.db',3,transport=fetch)
            try:
                self.batch_within(pool,1)
                self.assertEqual(used,['key-0'])
                second=self.batch_within(pool,2)
                self.assertEqual([r['status'] for r in second],[200,200])
                self.assertNotIn('key-0',used[1:])
                state=pool.snapshot()
                self.assertEqual(state['healthy_keys'],3)
                self.assertEqual(state['quarantined_keys'],[])
            finally:pool.close()

    def test_resize_clamps_to_healthy_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            pool,used=self.pool_with_one_bad_key(tmp,keys=3,bad=1)
            try:
                self.batch_within(pool,3)
                self.assertEqual(pool.snapshot()['healthy_keys'],2)
                pool.resize(10)
                self.assertEqual(pool.snapshot()['requested_workers'],2)
            finally:pool.close()


if __name__=='__main__':unittest.main()

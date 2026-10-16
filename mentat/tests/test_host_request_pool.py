from pathlib import Path
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
import hashlib,json,os,sqlite3,subprocess,sys,tempfile,threading,time,unittest
from unittest.mock import patch
from urllib.request import Request,build_opener,ProxyHandler
from urllib.error import HTTPError
PROJECT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(PROJECT/'tools'))
import api_request_budget as budget
from api_request_pool import RequestPool,RemoteRequestPool,ENDPOINT
import api_host_pool as host
KEY='fixture-host-pool-a'
BODY='{"results":[{"sub_id":400000000000000001,"contributor_name":"SYNTHETIC FIXTURE","contributor_employer":"Fixture","committee_id":"C00000001","contribution_receipt_amount":-1.25,"contribution_receipt_date":"2026-01-01","entity_type":"IND","two_year_transaction_period":2026,"filing_form":"F3X","line_number":"11AI"}],"pagination":{"count":1,"is_count_exact":true,"last_indexes":null}}'

def wait_ready(path,thread):
    end=time.monotonic()+5
    while not path.is_file() and thread.is_alive() and time.monotonic()<end:time.sleep(.02)
    if not path.is_file():raise AssertionError('inert host broker did not become ready')

class HostPoolTests(unittest.TestCase):
    def test_descriptor_strict_key_order_scope_endpoint_and_nonsecret_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);pool=RequestPool([KEY,'fixture-host-pool-b'],root/'quota.sqlite',2,transport=lambda *_:None)
            try:
                value=json.loads(host.descriptor(pool,'http://127.0.0.1:12345/batch'))
                self.assertNotIn(KEY,json.dumps(value));path=root/'descriptor.json'
                path.write_text(json.dumps(value));remote=RemoteRequestPool(path,pool.keys)
                self.assertEqual(remote.url,'http://host.docker.internal:12345/batch')
                self.assertIsNone(remote.snapshot()['active_workers']);self.assertFalse(remote.snapshot()['counters_available'])
                for changed in ({'key_ids':list(reversed(value['key_ids']))},{'url':'http://0.0.0.0:12345/batch'},
                    {'scope':'arbitrary'},{'endpoint':'https://example.invalid/'},{'width':True},{'token':'short'}):
                    path.write_text(json.dumps(value|changed))
                    with self.assertRaisesRegex(ValueError,'descriptor or key-slot'):RemoteRequestPool(path,pool.keys)
            finally:pool.close()

    def test_exact_host_source_mapping_refuses_traversal_and_missing_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'job.sqlite').touch()
            self.assertEqual(host.host_job_database('/collect/job.sqlite',PROJECT,{'COLLECT_HOST':str(root)}),root/'job.sqlite')
            for value in ('/collect/../outside.sqlite','/unknown/job.sqlite','/collect/missing.sqlite'):
                with self.assertRaises(ValueError):host.host_job_database(value,PROJECT,{'COLLECT_HOST':str(root)})

    def test_owner_creation_watch_and_stopfile_close_only_owned_broker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);secret=root/'secrets';secret.mkdir();(secret/'fec_api_keys').write_text(KEY)
            source=root/'source.sqlite';source.touch();seen=[]
            def alive(pid,started):seen.append((pid,started));return False
            with patch.dict(os.environ,{'MENTAT_API_BUDGET_DB':str(root/'budget.sqlite'),'MENTAT_API_BUDGET_IMPORTS':'[]'}):
                host.serve(secret,123,source,PROJECT,key_loader=lambda:[KEY],owner_started=lambda _:777,owner_alive=alive,transport=lambda *_:None)
            self.assertEqual(seen,[(123,777)]);self.assertFalse((secret/'request-pool.json').exists())
            self.assertEqual((secret/'fec_api_keys').read_text(),KEY)

    def test_windows_parallel_reserve_preserves_legacy_usage_and_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'old.sqlite';shared=root/'shared.sqlite'
            with closing(sqlite3.connect(source)) as db,db:
                budget.ensure_schema(db);db.execute('INSERT INTO api_request_budget VALUES (?,?,?)',(budget.legacy_budget_id(KEY),90,1))
            original=hashlib.sha256(source.read_bytes()).hexdigest()
            with ThreadPoolExecutor(12) as workers:
                values=list(workers.map(lambda _:budget.reserve(shared,budget.budget_id(KEY),aliases=budget.budget_aliases(KEY),legacy_paths=[source],now=100,cap=3),range(20)))
            self.assertEqual(sum(row['admitted'] for row in values),2)
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),original)

    def test_compose_mounts_only_descriptor_and_replay_cannot_receive_host_pool(self):
        import yaml
        class Loader(yaml.SafeLoader):pass
        Loader.add_constructor('!reset',lambda loader,node:None)
        base=yaml.safe_load((PROJECT/'compute.compose.yaml').read_text());replay=yaml.load((PROJECT/'compute.replay.yaml').read_text(),Loader)
        job=base['services']['collect-fanout']
        self.assertIn('fec_request_pool',job['secrets']);self.assertEqual(job['environment']['MENTAT_REQUEST_POOL_CONFIG'],'/run/secrets/fec_request_pool')
        self.assertFalse(any('budget' in row['target'] for row in job['volumes']))
        self.assertEqual(replay['services']['collect-fanout']['environment']['MENTAT_REQUEST_POOL_CONFIG'],'')
        self.assertEqual(replay['services']['collect-fanout']['network_mode'],'none')
        self.assertIn('REPLAY_KEYS_FILE',replay['secrets']['fec_request_pool']['file'])
        self.assertIn("'--budget-database', str(join(work, '_replay', 'budget.sqlite'))",(PROJECT/'tools/replay_parity.py').read_text())
        wrapper=(PROJECT/'tools/run_job.ps1').read_text()
        self.assertLess(wrapper.index("'owner.stop'"),wrapper.index("$wiped = Invoke-Native"))
        self.assertIn('$hostPool.WaitForExit(150000)',wrapper)

    def test_powershell_ast_and_plan_only_preserve_env_and_owned_lifecycle_order(self):
        import shutil
        shell=shutil.which('powershell') or shutil.which('pwsh')
        if not shell:self.skipTest('PowerShell unavailable')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            environment=os.environ.copy()
            for name in list(environment):
                if name.startswith(('FEC_API_KEY','MENTAT_REQUEST_POOL_')):environment.pop(name,None)
            environment.update(LOCALAPPDATA=str(root),COLLECT_HOST=str(root),PYTHONDONTWRITEBYTECODE='1')
            result=subprocess.run([shell,'-NoProfile','-ExecutionPolicy','Bypass','-File',str(PROJECT/'tests/host_pool_wrapper_plan.ps1'),
                '-ProjectRoot',str(PROJECT),'-PythonPath',sys.executable],env=environment,capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            value=json.loads(result.stdout)
            self.assertEqual(value['ast_parse_errors'],0);self.assertTrue(value['plan_only'])
            self.assertEqual(value['collect_host'],str(root).replace('\\','/'))
            self.assertTrue(value['broker_ready_wait_finite']);self.assertTrue(value['broker_stop_wait_finite'])
            self.assertEqual(list(root.iterdir()),[])

    @unittest.skipUnless(os.environ.get('MENTAT_TEST_BUDGET_DOCKER')=='1','explicit inert container bridge opt-in')
    def test_inert_r_container_uses_host_quota_and_preserves_original_archive(self):
        lock=json.loads((PROJECT/'compute.images.lock').read_text(encoding='utf-8-sig'));image=lock['image']+'@'+lock['digest']
        with tempfile.TemporaryDirectory(prefix='mentat-host-broker-') as tmp:
            root=Path(tmp);secret=root/'secret';secret.mkdir();(secret/'fec_api_keys').write_text(KEY)
            target=root/'out';target.mkdir();old=root/'source.sqlite';shared=root/'quota.sqlite'
            with closing(sqlite3.connect(old)) as db,db:
                budget.ensure_schema(db);db.execute('INSERT INTO api_request_budget VALUES (?,?,?)',(budget.legacy_budget_id(KEY),time.time(),1))
            original=hashlib.sha256(old.read_bytes()).hexdigest();calls=[];errors=[]
            def inert(spec,key):calls.append(1);return dict(status=200,headers={},body=BODY)
            def serve():
                try:host.serve(secret,123,old,PROJECT,key_loader=lambda:[KEY],owner_started=lambda _:777,owner_alive=lambda *_:True,transport=inert,cap=3)
                except Exception as error:errors.append(type(error).__name__)
            with patch.dict(os.environ,{'MENTAT_API_BUDGET_DB':str(shared),'MENTAT_API_BUDGET_IMPORTS':'[]'}):
                thread=threading.Thread(target=serve,daemon=True);thread.start();wait_ready(secret/'request-pool.json',thread)
                try:
                    worker="""import sys,os,json,subprocess
sys.path.insert(0,'/code/tools')
from api_request_pool import RemoteRequestPool
from urllib.request import Request,build_opener,ProxyHandler
from urllib.error import HTTPError
remote=RemoteRequestPool('/run/secrets/fec_request_pool',['fixture-host-pool-a'])
try:
 build_opener(ProxyHandler({})).open(Request(remote.url,data=b'{}',headers={'Authorization':'Bearer invalid-inert-token'}),timeout=3)
 raise AssertionError('invalid auth admitted')
except HTTPError as error:assert error.code==403;error.close()
env=os.environ.copy();env.update(MENTAT_REQUEST_POOL_REMOTE='1',MENTAT_REQUEST_POOL_URL=remote.url,MENTAT_REQUEST_POOL_TOKEN=remote.token,MENTAT_API_BUDGET_DB='/forbidden/local-quota.sqlite')
result=subprocess.run(['/usr/local/bin/Rscript','/code/tests/host_pool_producer.R','/code','/out'],env=env,capture_output=True,text=True,timeout=15)
assert result.returncode==0,result.stdout+result.stderr
assert 'host_pool_producer: PASS' in result.stdout
print('inert_container_R_remote_archive: PASS')
"""
                    command=['docker','run','--rm','--pull=never','--network','bridge','--read-only','--user','10001:10001',
                        '--cap-drop','ALL','--security-opt','no-new-privileges','--pids-limit','96','--memory','512m','--cpus','1',
                        '--tmpfs','/tmp:size=64m,mode=1777','--workdir','/code','--env','PYTHONDONTWRITEBYTECODE=1',
                        '--mount','type=bind,source='+str(PROJECT)+',target=/code,readonly',
                        '--mount','type=bind,source='+str(secret/'request-pool.json')+',target=/run/secrets/fec_request_pool,readonly',
                        '--mount','type=bind,source='+str(target)+',target=/out','--entrypoint','python',image,'-c',worker]
                    result=subprocess.run(command,capture_output=True,text=True,timeout=25)
                    self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                    self.assertIn('inert_container_R_remote_archive: PASS',result.stdout)
                    with closing(sqlite3.connect(target/'shard.sqlite')) as db:
                        row=db.execute('SELECT payload_json,archive_sha256 FROM fec_response_archive').fetchone()
                        self.assertEqual(row,(BODY,hashlib.sha256(BODY.encode()).hexdigest()))
                        self.assertEqual(db.execute('SELECT sub_id,typeof(sub_id),contribution_receipt_amount FROM contributions').fetchone(),('400000000000000001','text',-1.25))
                    config=json.loads((secret/'request-pool.json').read_text())
                    request=Request(config['url'],data=json.dumps({'specs':[{'url':ENDPOINT,'params':{'per_page':'1'}}]}).encode(),headers={'Authorization':'Bearer '+config['token'],'Content-Type':'application/json'})
                    with build_opener(ProxyHandler({})).open(request,timeout=3) as response:self.assertEqual(response.status,200)
                    self.assertEqual(len(calls),2)
                    self.assertFalse(budget.reserve(shared,budget.budget_id(KEY),aliases=budget.budget_aliases(KEY),legacy_paths=[old],now=time.time(),cap=3)['admitted'])
                    self.assertEqual(hashlib.sha256(old.read_bytes()).hexdigest(),original)
                finally:
                    (secret/'owner.stop').touch();thread.join(timeout=5)
                self.assertFalse(thread.is_alive());self.assertEqual(errors,[]);self.assertFalse((secret/'request-pool.json').exists())
                import compute_secrets
                self.assertTrue(compute_secrets.wipe(secret.name,base=root));self.assertFalse(secret.exists())

if __name__=='__main__':unittest.main()

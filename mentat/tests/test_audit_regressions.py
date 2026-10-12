"""Offline counterexamples for the September system audit; no real termination."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT/'tools'))
spec=importlib.util.spec_from_file_location('audit_regression_server',PROJECT/'app/server.py')
app=importlib.util.module_from_spec(spec);spec.loader.exec_module(app)
from run_cross_reference import coalesce_source_observations
from cross_reference_engine import analyze


class ConsoleIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        with patch.object(app,'report_directory',return_value=self.root),patch.object(app,'environment_keys',return_value=[]):
            self.console=app.Console(self.root,rscript=sys.executable)
        self.identifier='a'*32;self.directory=self.root/self.identifier;self.directory.mkdir()
        self.identity={'pid':123456,'created':123,'executable':'fixture.exe'}
        self.manifest={'id':self.identifier,'name':'Synthetic','state':'running','created_at':0,'pid':123456,'process_identity':self.identity}
        self.save()

    def save(self):app.atomic_json(self.directory/'upload.json',self.manifest)

    def test_terminal_runs_never_terminate_or_write_stop(self):
        for state in ('completed','failed','stopped','ready'):
            self.manifest['state']=state;self.save()
            with patch.object(app.subprocess,'run') as kill,self.assertRaisesRegex(ValueError,'not active'):
                self.console.stop({'id':self.identifier,'force':True})
            kill.assert_not_called()
            self.assertFalse((self.directory/'control.json').exists())

    def test_missing_or_reused_identity_only_requests_graceful_stop(self):
        @contextmanager
        def reused(pid):yield {**self.identity,'created':999}
        for expected in (None,self.identity):
            self.manifest['process_identity']=expected;self.save()
            with patch.object(app,'process_identity_handle',reused),patch.object(app.subprocess,'run') as kill:
                result=self.console.stop({'id':self.identifier,'force':True})
            kill.assert_not_called();self.assertEqual(result['mode'],'graceful')
            self.assertTrue(app.read_json(self.directory/'control.json')['stop'])

    def test_matching_identity_is_held_until_mocked_tree_termination_finishes(self):
        held=[]
        @contextmanager
        def verified(pid):
            held.append(pid)
            try:yield self.identity
            finally:held.pop()
        def kill(command,**kwargs):
            self.assertEqual(held,[123456]);self.assertEqual(command,['taskkill','/PID','123456','/T','/F'])
            return subprocess.CompletedProcess(command,0,b'',b'')
        with patch.object(app,'process_identity_handle',verified),patch.object(app.subprocess,'run',side_effect=kill):
            self.assertTrue(self.console.stop({'id':self.identifier,'force':True})['ok'])
        self.assertEqual(held,[]);self.assertEqual(app.read_json(self.directory/'upload.json')['state'],'stopped')

    @unittest.skipUnless(os.name=='nt','Windows process identity')
    def test_real_current_process_identity_is_stable_and_pid_bound(self):
        first=app.process_identity(os.getpid())
        self.assertIsNotNone(first);self.assertEqual(first['pid'],os.getpid())
        self.assertGreater(first['created'],0)
        self.assertEqual(first,app.process_identity(os.getpid()))
        self.assertEqual(Path(first['executable']).name.lower(),Path(sys.executable).name.lower())

    def test_separate_console_instances_claim_same_upload_once(self):
        self.manifest['state']='ready';self.manifest.pop('pid');self.save()
        with patch.object(app,'report_directory',return_value=self.root),patch.object(app,'environment_keys',return_value=[]):
            other=app.Console(self.root,rscript=sys.executable)
        gate=threading.Barrier(2)
        def launch(console):
            gate.wait()
            try:return console.launch({'id':self.identifier,'cycles':[2024]})
            except ValueError:return None
        with patch.object(app,'load_keys',side_effect=lambda:(time.sleep(.06) or ['fixture'])),patch.object(app.Console,'prepare_launch') as prepare:
            with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(launch,[self.console,other]))
        self.assertEqual(sum(r is not None for r in results),1)
        self.assertEqual(prepare.call_count,1)

    def test_claim_is_exclusive_across_two_python_processes(self):
        self.manifest['state']='ready';self.manifest.pop('pid');self.save()
        worker=self.root/'worker.py'
        worker.write_text('''import importlib.util,json,os,sys,time
from pathlib import Path
spec=importlib.util.spec_from_file_location('fixture_server',sys.argv[1]);app=importlib.util.module_from_spec(spec);spec.loader.exec_module(app)
root=Path(sys.argv[2]);tag=sys.argv[3]
app.environment_keys=lambda:[];app.load_keys=lambda:['fixture']
console=app.Console(root,rscript=sys.executable)
console.prepare_launch=lambda *a,**k:None
(root/(tag+'.ready')).write_text('ready')
# Checked after the ready file: a worker that dies first reads as a startup timeout.
pinned=os.environ.get('MENTAT_REPORT_DIRECTORY','')
if not pinned or console.report_directory!=Path(pinned).resolve():raise SystemExit('report directory not pinned, resolved '+str(console.report_directory))
while not (root/'go').exists():time.sleep(.01)
try:console.launch({'id':'a'*32,'cycles':[2024]});result='claimed'
except ValueError:result='refused'
(root/(tag+'.result')).write_text(result)
''')
        # A real Console resolves the configured report folder unless one is pinned.
        reports=self.root/'reports';reports.mkdir()
        environment={**os.environ,'MENTAT_REPORT_DIRECTORY':str(reports)}
        children=[subprocess.Popen([sys.executable,str(worker),str(PROJECT/'app/server.py'),str(self.root),tag],stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=environment) for tag in ('one','two')]
        try:
            deadline=time.monotonic()+15
            while not all((self.root/(tag+'.ready')).exists() for tag in ('one','two')):
                if time.monotonic()>deadline:self.fail('Fixture process startup timed out')
                time.sleep(.02)
            (self.root/'go').write_text('go')
            for child in children:
                out,err=child.communicate(timeout=15);self.assertEqual(child.returncode,0,err.decode(errors='replace'))
            self.assertEqual(sorted((self.root/(tag+'.result')).read_text() for tag in ('one','two')),['claimed','refused'])
        finally:
            for child in children:
                if child.poll() is None:child.terminate();child.wait(timeout=5)


class MemoConflictTests(unittest.TestCase):
    def source(self,sid,**changes):
        row=dict(sub_id=sid,source_table='contributions',company='Acme',industry='Fixture',cycle=2024,
            contribution_receipt_amount=100,current_receipt_cents=10000,contributor_name='PERSON, ONE',
            contribution_receipt_date='2024-01-01',committee_id='C00000001',transaction_id=sid,
            file_num=10,filing_form='F3X',line_number='11AI',entity_type='IND',company_match_status='accepted',
            memo_code='',attribution_claims=[],source_conflicts=[])
        return dict(row,**changes)

    def test_explicit_nonmemo_and_memo_disagreement_cannot_silently_exclude(self):
        parent=self.source('parent',transaction_id='TX')
        child=self.source('child',back_reference_transaction_id='TX')
        memo=dict(child,source_table='corporate_giving',memo_code='X')
        for observations in ([parent,child,memo],[memo,child,parent]):
            merged=coalesce_source_observations(observations)
            row=next(r for r in merged if r['sub_id']=='child')
            self.assertTrue(any(c['field']=='memo_code' for c in row['source_conflicts']))
            result=analyze(merged);decision=next(d for d in result['decisions'] if d['source_id']=='child')
            self.assertNotEqual(decision['status'],'excluded_with_evidence')
            self.assertTrue(any('child' in r['source_ids'] for r in result['review_queue']))

    def test_absent_memo_metadata_can_still_be_recovered(self):
        child=self.source('child');child.pop('memo_code')
        merged=coalesce_source_observations([child,dict(child,source_table='corporate_giving',memo_code='X')])[0]
        self.assertEqual(merged['memo_code'],'X');self.assertEqual(merged['source_conflicts'],[])


if __name__=='__main__':unittest.main()

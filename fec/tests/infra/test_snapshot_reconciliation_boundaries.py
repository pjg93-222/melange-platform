"""Synthetic AST-only checks. Never import a project runner or inspect live data."""
import ast
import json
import os
from pathlib import Path
import tempfile
import types
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]

def lift(path, names, values):
    tree = ast.parse((ROOT / path).read_text(encoding='utf-8-sig'))
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names]
    assert len(nodes) == len(names)
    ns = {'__builtins__': __builtins__, **values}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), path, 'exec'), ns)
    return ns

class SnapshotRefusal(unittest.TestCase):
    def setUp(self):
        self.ns = lift('fec/refresh_state.py', ['key_for', 'snapshot'], {'os': os, 'EXTERNAL_PREFIX':'ext:'})
        self.root = Path(tempfile.mkdtemp(prefix='fec-reconcile42-snapshot-'))
        self.file = self.root/'sample.txt'
        self.file.write_text('synthetic')

    def test_walk_denied_refuses(self):
        def denied(root, **kwargs):
            if kwargs.get('onerror'):
                kwargs['onerror'](PermissionError('synthetic denied subtree'))
            return iter(())
        with mock.patch.object(os, 'walk', side_effect=denied):
            with self.assertRaises(PermissionError):
                self.ns['snapshot'](self.root, recursive=[self.root])

    def test_top_level_denied_refuses(self):
        with mock.patch.object(os, 'scandir', side_effect=PermissionError('synthetic denied root')):
            with self.assertRaises(PermissionError):
                self.ns['snapshot'](self.root, top_level=[self.root])

    def test_file_stat_denied_refuses(self):
        with mock.patch.object(os, 'stat', side_effect=PermissionError('synthetic denied stat')):
            with self.assertRaises(PermissionError):
                self.ns['snapshot'](self.root, extra_files=[self.file])

    def test_missing_file_and_optional_tree_are_absent(self):
        self.assertEqual({}, self.ns['snapshot'](self.root, recursive=[self.root/'not-created'],
                         top_level=[self.root/'not-created'], extra_files=[self.root/'not-created.txt']))

    def test_valid_file_and_exclusion(self):
        self.assertEqual({'sample.txt'}, set(self.ns['snapshot'](self.root, recursive=[self.root])))
        self.assertEqual({}, self.ns['snapshot'](self.root, recursive=[self.root], exclude=lambda k:True))

class DependencyOrder(unittest.TestCase):
    def validate(self, steps):
        ns=lift('fec/refresh_table.py',['validate'], {'TABLE':(), 'slug':lambda s:s.lower(),
                'COLLECT_KINDS':('source','press'), 'COMPUTE_KINDS':('compute',)})
        return ns['validate'](steps)

    def row(self, name, requires=()):
        return types.SimpleNamespace(name=name,requires=requires,kind='compute',timeout_s=1,
                                     argv=('synthetic.py',),evidence=False,partial_codes=())

    def test_self_dependency_is_named_refusal(self):
        self.assertTrue(self.validate([self.row('self',('self',))]))

    def test_prior_dependency_valid_later_and_unknown_refused(self):
        self.assertEqual([],self.validate([self.row('a'),self.row('b',('a',))]))
        self.assertTrue(self.validate([self.row('a',('b',)),self.row('b')]))
        self.assertTrue(self.validate([self.row('a',('missing',))]))

class CompletionTimestamp(unittest.TestCase):
    def test_stamp_follows_final_assessment(self):
        tree=ast.parse((ROOT/'fec/research/cosponsor_universe/universe_models.py').read_text())
        run=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='run')
        start=next(i for i,n in enumerate(run.body) if isinstance(n,ast.Assign)
                   and any(isinstance(x,ast.Name) and x.id=='results' for x in n.targets))
        clock=[0]
        class Array:
            def __getitem__(self,k):return self
            def __invert__(self):return self
            def reset_index(self,**kw):return self
        def metrics(*args):
            clock[0]+=1
            return {'precision_at_8':0.0}
        written=[]
        ns={'stamp':lambda:clock[0], 'digest':lambda p:'synthetic', 'selection_path':Path('selection'),
            'selection':{'chosen':'fake'}, 'models':{'fake':{'source_qualification':'synthetic'}},
            'OUT':Path('synthetic'), 'np':types.SimpleNamespace(load=lambda p:Array()),
            'me':Array(),'m':Array(),'early':Array(),'metrics':metrics,'log':lambda x:None,
            'gc':types.SimpleNamespace(collect=lambda:None),'write_json':lambda p,r:written.append(r.copy())}
        exec(compile(ast.Module(body=run.body[start:],type_ignores=[]),'synthetic-universe-tail','exec'),ns)
        self.assertEqual(2,clock[0])
        self.assertEqual(clock[0],written[0]['completed_utc'])

class SnapshotLifecycle(unittest.TestCase):
    def runner(self):
        tree=ast.parse((ROOT/'fec/refresh_tick.py').read_text(encoding='utf-8-sig'))
        original=next(n for n in tree.body if isinstance(n,ast.ClassDef) and any(
            isinstance(m,ast.FunctionDef) and m.name=='run_step' for m in n.body))
        methods=[n for n in original.body if isinstance(n,ast.FunctionDef)
                 and n.name in {'run_step','reconcile','eligibility','verify_outputs'}]
        klass=ast.ClassDef(name='Runner',bases=[],keywords=[],body=methods,decorator_list=[])
        ast.fix_missing_locations(klass)
        store=types.SimpleNamespace(hours_since=lambda *a:0,load_snapshot=lambda p:{'a':(1,1)},
              diff=lambda a,b:sorted(k for k,v in b.items() if a.get(k)!=v),
              removed=lambda a,b:sorted(set(a)-set(b)),describe_file=lambda *a,**k:{'sha256':'bound'},TORN='TORN')
        ns={'time':time,'state_store':store,'table_module':types.SimpleNamespace(evidence_steps=lambda rows:rows),
            'RUNNING':'RUNNING','FAILED':'FAILED','INTERRUPTED':'INTERRUPTED','COVERAGE_OK':'OK',
            'PHASE_RECONCILE':'reconcile'}
        exec(compile(ast.Module(body=[klass],type_ignores=[]),'synthetic-refresh-lifecycle','exec'),ns)
        r=ns['Runner']()
        entry={'state':'OK','torn':False,'last_ok_utc':'prior','coverage':'OK'}
        r.state=types.SimpleNamespace(step=lambda n:entry,record=lambda n,**kw:entry.update(kw))
        r.table=[types.SimpleNamespace(name='synthetic',kind='source')]
        r.unavailable_manifests=set();r.window_days=7;r.now=lambda:0;r.due_now=set()
        r.logs=[];r.log=r.logs.append;r.emit=lambda *a,**k:None;r.set_phase=lambda *a:None
        r.commits=[];r.commit_state=lambda:r.commits.append(entry.copy())
        r.pre_snapshot_path=lambda step:'synthetic-pre';r.manifests={'synthetic':{'files':{'a':{'sha256':'bound'}}}}
        r.root='synthetic';r._fresh_snapshot=None
        def fail(step,phase):
            r._step_pre={'a':(1,1)};entry['state']='RUNNING'
            raise PermissionError('synthetic post-snapshot denied')
        r._run_step=fail
        r.snapshot=mock.Mock(side_effect=PermissionError('synthetic resnapshot denied'))
        return r,entry,store

    def test_double_snapshot_failure_exits_and_blocks_prior_success(self):
        r,entry,store=self.runner()
        with self.assertRaises(PermissionError):r.run_step(r.table[0],'collect')
        self.assertEqual('FAILED',entry['state'])
        self.assertIsNone(entry['torn'])
        self.assertFalse(r.eligibility()[0])
        self.assertTrue(r.commits)
        self.assertTrue(any('snapshot' in x.lower() for x in r.logs))

    def test_running_and_unknown_outputs_cannot_be_eligible(self):
        r,entry,store=self.runner()
        for state,torn in [('RUNNING',False),('FAILED',None)]:
            entry.update(state=state,torn=torn)
            with self.subTest(state=state):self.assertFalse(r.eligibility()[0])

    def test_complete_reconciliation_preserves_changed_outputs_as_torn(self):
        r,entry,store=self.runner();entry.update(state='FAILED',torn=None)
        r.snapshot=lambda:{'a':(2,2)}
        r.reconcile();r.verify_outputs()
        self.assertTrue(entry['torn']);self.assertEqual(['a'],entry['unverified_files'])
        self.assertFalse(r.eligibility()[0])

    def test_complete_unchanged_reconciliation_then_manifest_validation(self):
        r,entry,store=self.runner();entry.update(state='FAILED',torn=None)
        r.snapshot=lambda:{'a':(1,1)}
        r.reconcile();r.verify_outputs();self.assertTrue(r.eligibility()[0])
        store.describe_file=lambda *a,**k:{'sha256':'changed'}
        r.verify_outputs();self.assertFalse(r.eligibility()[0])

    def test_legacy_ok_record_receives_existing_defaults_and_stays_eligible(self):
        # Execute the actual migration method, without constructor, files or runner imports.
        ns=lift('fec/refresh_state.py',['blank_step','RefreshState'],
                {'NEVER':'NEVER','COVERAGE_OK':'OK'})
        state=object.__new__(ns['RefreshState'])
        state.data={'steps':{'synthetic':{'state':'OK','last_ok_utc':'prior'}}}
        entry=state.step('synthetic')
        self.assertIs(entry['torn'],False)
        r,unused,store=self.runner()
        r.state=state
        self.assertTrue(r.eligibility()[0])

    def test_failure_state_commit_error_preserves_refusal(self):
        r,entry,store=self.runner()
        r.commit_state=mock.Mock(side_effect=PermissionError('synthetic state commit denied'))
        with self.assertRaises(PermissionError):r.run_step(r.table[0],'collect')
        self.assertFalse(r.eligibility()[0])
        # The previous durable record can still be RUNNING if writing FAILED was denied.
        entry.update(state='RUNNING',torn=False)
        self.assertFalse(r.eligibility()[0])

if __name__=='__main__':unittest.main(verbosity=2)

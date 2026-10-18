"""AST-only identity and legacy review controls; no locks/audio/processes are accessed."""
import ast
from contextlib import contextmanager
from pathlib import Path
import types
import unittest

ROOT=Path(__file__).resolve().parents[3]

def lock_ns(windows=True):
    tree=ast.parse((ROOT/'fec/pipeline_lock.py').read_text())
    names={'_same_pid_space','_holder_is_live','_foreign_holder_is_live','pipeline_run_lock'}
    functions=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names]
    ns={'_windows':lambda:windows,'host_identity':lambda:'nt:LOCAL' if windows else 'posix:LOCAL',
        '_pid_alive':lambda pid:False,'contextmanager':contextmanager,'Path':Path,
        'LOCK_TOKEN_ENV':'LOCK_TOKEN','LOCK_OWNER_PID_ENV':'OWNER_PID','ROOT':'never-used',
        'uuid':types.SimpleNamespace(uuid4=lambda:types.SimpleNamespace(hex='synthetic')),
        '_stale_seconds':lambda:600,'LOCK_STALE_SECONDS_ENV':'STALE',
        '_heartbeat_path':lambda p:Path('synthetic.heartbeat.lock')}
    exec(compile(ast.Module(body=functions,type_ignores=[]),'lease-functions','exec'),ns)
    return ns

class LeaseIdentity(unittest.TestCase):
    def test_exact_normalized_local_windows_keeps_pid_rule(self):
        ns=lock_ns()
        for host in ['nt:LOCAL','NT:local',' nt:LOCAL ']:
            with self.subTest(host=host):self.assertTrue(ns['_same_pid_space'](host))
        self.assertFalse(ns['_holder_is_live']({'host':'nt:LOCAL','pid':123}))

    def test_windows_unknown_and_foreign_are_held_without_io(self):
        ns=lock_ns()
        ns['_heartbeat_path']=lambda p:(_ for _ in ()).throw(AssertionError('heartbeat read'))
        for host in [None,'','nt:OTHER','NT:OTHER','nt:RENAMED']:
            with self.subTest(host=host):
                self.assertFalse(ns['_same_pid_space'](host))
                self.assertTrue(ns['_holder_is_live']({'host':host,'pid':123,'token':'synthetic'},'synthetic'))

    def test_posix_identity_unchanged(self):
        ns=lock_ns(False)
        self.assertTrue(ns['_same_pid_space']('posix:LOCAL'))
        for host in [None,'nt:LOCAL','posix:OTHER']:
            self.assertFalse(ns['_same_pid_space'](host))
            self.assertTrue(ns['_holder_is_live']({'host':host,'pid':123}))

    def test_foreign_windows_diagnostic_requires_owner_verification(self):
        ns=lock_ns()
        payload={'pid':123,'token':'synthetic','host':'nt:OTHER','owner_kind':'fixture'}
        ns['_read_lock']=lambda p:payload
        ns['_stale_lock_may_be_removed']=lambda p,body:False
        def occupied(*a):raise FileExistsError('synthetic occupied')
        ns['os']=types.SimpleNamespace(environ={},O_CREAT=1,O_EXCL=2,O_WRONLY=4,open=occupied)
        with self.assertRaisesRegex(RuntimeError,'owner.*verif|verif.*owner') as caught:
            with ns['pipeline_run_lock'](Path('synthetic.lock')):self.fail('acquired')
        self.assertNotIn('else delete',str(caught.exception))

class LegacySpeechReview(unittest.TestCase):
    def check(self,fields):
        tree=ast.parse((ROOT/'fec/speech/extract_member_interview_long_turns.py').read_text())
        main=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='main')
        loop=next(n for n in main.body if isinstance(n,ast.For) and isinstance(n.target,ast.Name) and n.target.id=='review')
        start=next(i for i,n in enumerate(loop.body) if isinstance(n,ast.If) and 'schema' in ast.unparse(n.test))
        end=next(i for i,n in enumerate(loop.body) if isinstance(n,ast.If) and ast.unparse(n.test)=='turn_id in seen_turns')
        exec(compile(ast.Module(body=loop.body[start:end],type_ignores=[]),'legacy-review-gate','exec'),
             {'review':{'schema':'member-interview-reviewed-turn-v1',**fields},'turn_id':'synthetic','TurnRefusal':RuntimeError})

    def test_explicit_nonaccept_status_refused(self):
        for status in ['REJECT','PENDING','',None,'UNKNOWN']:
            with self.subTest(status=status),self.assertRaises(RuntimeError):
                self.check({'review_status':status,'boundary_review':'Human checked handoffs.'})

    def test_empty_or_rejected_boundary_refused(self):
        for boundary in [None,'',' ','REJECT',' reject ','PENDING','PENDING_REVIEW']:
            with self.subTest(boundary=boundary),self.assertRaises(RuntimeError):self.check({'boundary_review':boundary})
        with self.assertRaises(RuntimeError):self.check({})

    def test_legacy_and_explicit_human_controls(self):
        self.check({'boundary_review':'Both speaker handoffs were checked.'})
        self.check({'review_status':' accept ','boundary_review':'ACCEPT','reviewer_type':'HUMAN'})
        with self.assertRaises(RuntimeError):self.check({'boundary_review':'accepted','reviewer_type':'AUTOMATED'})

if __name__=='__main__':unittest.main(verbosity=2)

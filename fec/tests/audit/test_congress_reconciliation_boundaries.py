"""AST-loaded observers with synthetic temporary artifacts only; no collector import."""
import ast
import copy
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import types
import unittest

ROOT=Path(__file__).resolve().parents[3]

def audit(name):
    path=ROOT/'fec/audit'/('audit_congress_'+name+'.py')
    tree=ast.parse(path.read_bytes())
    # All imported modules are stdlib; top-level assignments are constants/Path/re.compile.
    allowed=(ast.Import,ast.ImportFrom,ast.Assign,ast.AnnAssign,ast.FunctionDef,ast.ClassDef)
    ns={'__file__':str(path),'__name__':'synthetic_observer'}
    exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n,allowed)],type_ignores=[]),str(path),'exec'),ns)
    return types.SimpleNamespace(**ns)

CONT=audit('collector_resume_continuity')
IDENT=audit('identifier_failure_resolution')
CATCH=audit('full_text_failure_catchnet')

def definitions(path,names,ns):
    tree=ast.parse((ROOT/path).read_bytes())
    nodes=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.ClassDef)) and n.name in names]
    assert len(nodes)==len(names)
    exec(compile(ast.Module(body=nodes,type_ignores=[]),str(path),'exec'),ns)
    return ns

class QueryShapes(unittest.TestCase):
    def setUp(self):
        ns=definitions('fec/tests/collect/test_audit_congress_collector_resume_continuity.py',
                       ['CollectorResumeContinuityTests'],
                       dict(audit=CONT,unittest=unittest,Path=Path,tempfile=tempfile,json=json,hashlib=hashlib,copy=copy))
        self.fixture=ns['CollectorResumeContinuityTests']();self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
    def query(self):
        f=self.fixture
        return CONT.validate_current_query(f.root,json.loads(f.current.read_text())['queries'][0])
    def test_valid_query_control(self):self.assertEqual('PASS',self.query()['status'])
    def test_records_must_be_objects(self):
        for value in [None,'scalar',4,[]]:
            with self.subTest(value=value):
                q=copy.deepcopy(self.fixture.query);q['records']=[value]
                self.fixture._json(self.fixture.root/'queries/q1.json',q)
                self.assertEqual('FAIL',self.query()['status'])
    def test_canonical_keys_must_be_nonempty_strings(self):
        for value in [[],{},'',None,12]:
            with self.subTest(value=value):
                q=copy.deepcopy(self.fixture.query);q['records'][0]['canonical_key']=value
                self.fixture._json(self.fixture.root/'queries/q1.json',q)
                self.assertEqual('FAIL',self.query()['status'])
    def test_page_root_nonobject_is_reported(self):
        for value in [[],None,'scalar']:
            with self.subTest(value=value):
                self.fixture._json(self.fixture.root/'raw/pages/q1/page_001.json',value)
                self.assertEqual('FAIL',self.query()['status'])
    def test_page_range_nonnumbers_are_reported(self):
        for value in ['one',None,{},True]:
            with self.subTest(value=value):
                q=copy.deepcopy(self.fixture.query);q['pages'][0]['result_range']['start']=value
                self.fixture._json(self.fixture.root/'queries/q1.json',q)
                self.assertEqual('FAIL',self.query()['status'])
    def test_query_and_page_descriptor_existing_controls(self):
        self.fixture._json(self.fixture.root/'queries/q1.json',[])
        self.assertEqual('FAIL',self.query()['status'])
        q=copy.deepcopy(self.fixture.query);q['pages']=[None]
        self.fixture._json(self.fixture.root/'queries/q1.json',q)
        self.assertEqual('FAIL',self.query()['status'])

class IdentifierShapes(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='reconcile42-identifier-');self.addCleanup(self.temp.cleanup)
        ns=definitions('fec/tests/collect/test_audit_congress_identifier_failure_resolution.py',
                       ['write_json','fixture_run'],dict(Path=Path,audit=IDENT,json=json,hashlib=hashlib))
        self.root,_=ns['fixture_run'](Path(self.temp.name))
        self.entry=json.loads((self.root/'manifest.json').read_text())['queries'][0]
        self.path=self.root/self.entry['query_file'];self.original=json.loads(self.path.read_text())
    def check(self):return IDENT.audit_query_artifact(self.root,self.entry)[0]
    def test_valid_identifier_control(self):self.assertTrue(self.check()['resolved'])
    def test_unhashable_or_invalid_identifier_key_is_failure(self):
        for value in [[],{},'',None,3]:
            with self.subTest(value=value):
                q=copy.deepcopy(self.original);q['records'][0]['canonical_key']=value
                self.path.write_text(json.dumps(q))
                self.assertFalse(self.check()['resolved'])
    def test_existing_nonobject_query_page_record_refusals(self):
        for field,value in [('records',[None]),('pages',[None])]:
            q=copy.deepcopy(self.original);q[field]=value;self.path.write_text(json.dumps(q))
            self.assertFalse(self.check()['resolved'])
        self.path.write_text('[]');self.assertFalse(self.check()['resolved'])

class CatchnetShapes(unittest.TestCase):
    def test_bad_validation_record_does_not_abort_later_valid_receipt(self):
        with tempfile.TemporaryDirectory(prefix='reconcile42-catchnet-') as tmp:
            root=Path(tmp);body=b'synthetic body';archive=root/'body.gz';archive.write_bytes(gzip.compress(body))
            valid={'canonical_key':'119:hr:1','status':'accepted','http_status':200,
                   'expected_url':'https://www.congress.gov/bill/119th-congress/house-bill/1/text',
                   'final_url':'https://www.congress.gov/bill/119th-congress/house-bill/1/text',
                   'cookie_values_persisted':False,'html_file':str(archive),
                   'body_sha256':hashlib.sha256(body).hexdigest(),'byte_length':len(body),
                   'validation':dict.fromkeys(CATCH.REQUIRED_VALIDATION,True)}
            for value in [['bad'],'bad',7,True]:
                folder=root/'receipts'/'a-invalid';folder.mkdir(parents=True,exist_ok=True)
                bad={**valid,'validation':value};(folder/'receipt.json').write_text(json.dumps(bad))
                good=root/'receipts'/'z-valid';good.mkdir(parents=True,exist_ok=True)
                (good/'receipt.json').write_text(json.dumps(valid))
                with self.subTest(value=value):
                    accepted,invalid=CATCH.accepted_receipts(root)
                    self.assertEqual(['119:hr:1'],list(accepted));self.assertEqual(1,len(invalid))

class MetadataScope(QueryShapes):
    # Avoid inheriting query tests: a separate loader below retains only metadata methods.
    def report(self):
        f=self.fixture
        return CONT.audit_continuity(f.baseline,f.current,f.partition_audit,expected_query_count=1)
    def mutate(self,change):
        f=self.fixture;c=json.loads(f.current.read_text());change(c);f._json(f.current,c)
    def test_known_timestamp_timeout_status_metadata_remains_nonrecord(self):
        report=self.report();self.assertEqual('PASS',report['status'])
        self.assertFalse(report['manifest_continuity']['semantic_record_drift_detected'])
        self.assertEqual('run_timestamp_timeout_receipt_rematerialization_only',report['manifest_continuity']['difference_classification'])
    def test_static_safety_policy_change_blocks_without_record_drift(self):
        for field in ['result_validation_weakened','captcha_bypass_attempted','unknown_safety_option']:
            with self.subTest(field=field):
                f=self.fixture
                baseline=json.loads(f.baseline.read_text());current=json.loads(f.current.read_text())
                baseline['automatic_verification_policy']={field:False}
                current['automatic_verification_policy']={field:True}
                f._json(f.baseline,baseline);f._json(f.current,current)
                report=self.report();self.assertEqual('FAIL',report['status'])
                self.assertFalse(report['manifest_continuity']['semantic_record_drift_detected'])
    def test_unknown_top_level_change_blocks_without_record_drift(self):
        self.mutate(lambda c:c.update(unrecognized_selection_filter='synthetic'))
        report=self.report();self.assertEqual('FAIL',report['status'])
        self.assertFalse(report['manifest_continuity']['semantic_record_drift_detected'])
    def test_honored_prior_state_is_metadata(self):
        f=self.fixture;b=json.loads(f.baseline.read_text());c=json.loads(f.current.read_text())
        b['automatic_verification_policy']={'captcha_bypass_attempted':False}
        c['automatic_verification_policy']={'captcha_bypass_attempted':False,'honored_prior_state':{
            'observed_at':'synthetic','consecutive_timeouts':2,'next_retry_not_before':'synthetic later'}}
        f._json(f.baseline,b);f._json(f.current,c)
        self.assertEqual('PASS',self.report()['status'])
    def test_unknown_or_malformed_honored_state_does_not_hide_safety_change(self):
        for value in [{'captcha_bypass_attempted':True},['not an object']]:
            with self.subTest(value=value):
                self.mutate(lambda c:c.update(automatic_verification_policy={'honored_prior_state':value}))
                report=self.report();self.assertEqual('FAIL',report['status'])
                self.assertFalse(report['manifest_continuity']['semantic_record_drift_detected'])
    def test_malformed_policy_or_manifest_refuses_controlled(self):
        for value in [[],None,7]:
            with self.subTest(value=value):
                self.mutate(lambda c:c.update(automatic_verification_policy=value))
                self.assertEqual('FAIL',self.report()['status'])
        self.fixture._json(self.fixture.current,[])
        with self.assertRaises(ValueError):self.report()

# Reuse the audited fixture without duplicating six inherited tests/counts.
MetadataScope=type('MetadataScope',(unittest.TestCase,),{
    k:v for k,v in MetadataScope.__dict__.items() if k not in {'__dict__','__weakref__'}} | {'setUp':QueryShapes.setUp})

if __name__=='__main__':unittest.main(verbosity=2)

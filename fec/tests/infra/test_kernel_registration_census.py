# -*- coding: utf-8 -*-
"""Kernel registration census (S-2; decision F-3 of 2026-10-11, audits/decisions_20261011/F_residual.md).

fec/tests/run_tests.py selects suites by explicit name, never by a discovery walk, so a suite it does
not name is never run -- indistinguishable from a deleted one.  On 2026-10-11, 199 of 402 tracked
suites were in that state.  This census freezes the gap and lets it only shrink:

  * every git-tracked fec/tests/**/test_*.py (by stem) and test_*.cjs (by file name, the node lane)
    must be named in run_tests.py or listed in BACKLOG;
  * every BACKLOG entry must still be tracked and still unregistered, so a suite that gets
    registered (or deleted) must also leave BACKLOG.

Registered names are read from run_tests.py's string constants with ast; nothing is imported.
The census reads the git index, so a new suite is caught when it is added to git.
Each BACKLOG value is a one-line reason: 'backlog-2026-10-11' means not yet run on its own;
anything else records why the suite stays out of the kernel.
"""
import ast,contextlib,io,os,re,subprocess,unittest

FEC=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT=os.path.dirname(FEC)

BACKLOG={
    'test_audit_legislation_downstream_readiness': 'operational pre-sync gate on live receipts; run at sync time (run_tests.py note)',
    'test_big5_gold_audio_tdd': 'setUp reads three live artifacts with no skip guard (run_tests.py note); not run here',
    'test_compet_reciprocity_look': 'retired CM-1 harness kept as record (3de682ba5)',
    'test_congress_advanced_pages_contract': 'reads gitignored source_data/legislation/*.json with no skip guard (run_tests.py note); not run here',
    'test_descriptive_contract_and_surfaces': 'held out pending its own check (run_tests.py note); not run here',
    'test_run_legislation_presync_audit': 'operational pre-sync gate on live receipts; run at sync time (run_tests.py note)',
    'test_staffer_live_server_concurrency': 'never register: browser-burst load test (Paul: no load or launch-loop tests)',
}


def registered():
    with open(os.path.join(FEC,'tests','run_tests.py'),encoding='utf-8') as f:
        tree=ast.parse(f.read())
    return {n.value for n in ast.walk(tree)
            if isinstance(n,ast.Constant) and isinstance(n.value,str) and re.fullmatch(r'test_\w+(\.cjs)?',n.value)}


def tracked():
    out=subprocess.run(['git','-C',ROOT,'ls-files','-z','--','fec/tests'],capture_output=True,check=True).stdout
    names=[os.path.basename(p) for p in out.decode('utf-8').split('\0')]
    return ({n[:-3] for n in names if re.fullmatch(r'test_\w+\.py',n)}|
            {n for n in names if re.fullmatch(r'test_\w+\.cjs',n)})


class KernelRegistrationCensus(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registered,cls.tracked=registered(),tracked()

    def test_census_sees_the_tree(self):
        # Negative control: an empty walk or an unparsable runner would make the two checks below vacuous.
        self.assertGreater(len(self.tracked),300)
        self.assertGreater(len(self.registered),200)
        self.assertIn('test_kernel_registration_census',self.registered)
        self.assertTrue(any(n.endswith('.cjs') for n in self.tracked),'census no longer sees the .cjs suites')

    def test_every_tracked_suite_is_registered_or_on_backlog(self):
        self.assertEqual(sorted(self.tracked-self.registered-set(BACKLOG)),[],
                         'register each new suite in fec/tests/run_tests.py')

    def test_backlog_holds_no_stale_entries(self):
        self.assertEqual(sorted(n for n in BACKLOG if n not in self.tracked or n in self.registered),[],
                         'a registered or deleted suite must leave BACKLOG')

    def test_only_suites_the_export_names_may_be_absent(self):
        # run_tests.py skips an absent registered suite only when ops/release_lib/export.py leaves it out by name
        # (a published tree); any other absent suite still fails. Its two helpers are taken by ast and run here.
        with open(os.path.join(FEC,'tests','run_tests.py'),encoding='utf-8') as f:
            tree=ast.parse(f.read())
        helpers=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in ('_export_excluded','_report_missing')]
        self.assertEqual(len(helpers),2)
        failed=[]
        ns={'os':os,'ok':lambda name,cond,detail='':failed.append(name) if not cond else None}
        exec(compile(ast.Module(helpers,[]),'run_tests.py','exec'),ns)
        excluded=ns['_export_excluded'](ROOT)
        self.assertIn('test_original_workspace',excluded)
        self.assertIn('test_cohort_output_bindings',excluded)
        self.assertLessEqual(excluded,self.registered)          # registered locally, absent from the public tree
        self.assertNotIn('test_kernel_registration_census',excluded)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            ns['_report_missing'](['test_original_workspace','test_cohort_output_bindings','test_kernel_registration_census'],excluded)
        self.assertEqual(failed,['test_kernel_registration_census suite clean'])   # the export's two are skipped, not failed
        self.assertIn('SKIP test_original_workspace',out.getvalue())
        self.assertEqual(ns['_export_excluded'](os.path.join(ROOT,'no-such-tree')),set())   # no export list: nothing excused

    def test_every_backlog_entry_has_a_reason(self):
        self.assertEqual([n for n,r in BACKLOG.items() if not (isinstance(r,str) and r.strip())],[])


if __name__=='__main__':
    unittest.main()

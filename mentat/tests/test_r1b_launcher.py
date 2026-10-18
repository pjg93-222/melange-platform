"""Inert R1b controls for the host-native `collect-downstream` job in tools\\run_job.ps1 (D075 phase 1).

Each control runs the STAGED launcher with Windows PowerShell 5.1 inside a throw-away project tree whose collector is a STUB (it writes a receipt and
exits; no provider, key or network is involved). LOCALAPPDATA is redirected so locks and logs stay in the temp tree. The unmodified launcher
(run_job.base.ps1 in the overlay) provides the parity and byte-identity controls. An argument given as a Python list is passed as a PowerShell array
(`-File` would deliver a comma list as one string, a limitation the launcher's own header documents).
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

TOOLS = Path(__file__).resolve().parents[1] / 'tools'
POWERSHELL = shutil.which('powershell.exe')
TOTALS = 'openfec-unpinned-totals-numbered-v1'
LOOKUP = 'openfec-unpinned-candidate-committees-numbered-v1'

STUB = r'''
import json, os, sys
from pathlib import Path
argv = sys.argv[1:]
Path(os.environ['STUB_ARGV']).write_text(json.dumps(argv), encoding='utf-8')
mode = os.environ.get('STUB_MODE', 'ok')
if mode == 'fail':
    sys.exit(3)
if '--live' not in argv:
    sys.exit('No implicit provider calls.')
get = lambda flag: argv[argv.index(flag) + 1]
state = Path(get('--state'))
receipt = {'schema_version': 1, 'ledger': str(state / 'frontier.sqlite'), 'physical_request_cap': int(get('--max-requests')), 'workers': int(get('--workers')),
           'http_attempts': 3, 'endpoint_attempts_recorded': 3, 'candidate_committee_lookup_http_attempts': 0, 'stop': None}
if mode == 'unaccounted':
    receipt['endpoint_attempts_recorded'] = 2
if mode == 'archive-stop':
    receipt['stop'] = {'error_type': 'ArchiveBindingFailed'}
if mode == 'cap-stop':
    receipt['stop'] = {'error_type': 'FECAdmissionRefused'}
if mode == 'over-cap':
    receipt['http_attempts'] = receipt['endpoint_attempts_recorded'] = int(get('--max-requests')) + 1
if mode == 'wrong-workers':
    receipt['workers'] = 1
path = state / 'RECEIPT-stub.json'
path.write_text(json.dumps(receipt), encoding='utf-8')
if mode != 'no-print':
    print(json.dumps({'receipt': str(path), 'http_attempts': receipt['http_attempts'], 'exhaustive_complete': False}))
'''


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def render(argument):
    if isinstance(argument, (list, tuple)):
        return ','.join(quote(item) for item in argument)
    return argument if argument.startswith('-') and not argument.startswith('--') else quote(argument)


BASE_COMMIT = '419ad729ffccf3cf08c6b6b006d2a84bfb0a8bc6'


def base_launcher():
    """The launcher as it was before R1b: run_job.base.ps1 beside run_job.ps1 when an overlay provides it, else the blob at BASE_COMMIT; None if neither is available."""
    local = TOOLS / 'run_job.base.ps1'
    if local.is_file():
        return local.read_bytes()
    git = shutil.which('git')
    if not git:
        return None
    done = subprocess.run([git, '-C', str(TOOLS), 'show', '%s:mentat/tools/run_job.ps1' % BASE_COMMIT], capture_output=True)
    return done.stdout if done.returncode == 0 and done.stdout else None


@unittest.skipUnless(os.name == 'nt' and POWERSHELL, 'Windows PowerShell 5.1 is required')
class Launcher(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='R1b-launcher-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = self.root / 'repo' / 'mentat'
        shutil.copytree(TOOLS, self.project / 'tools', ignore=shutil.ignore_patterns('__pycache__'))
        for name in ('compute.images.lock', 'compute.compose.yaml'):
            shutil.copyfile(TOOLS.parent / name, self.project / name)
        shutil.copytree(TOOLS.parent / 'app', self.project / 'app', ignore=shutil.ignore_patterns('__pycache__'))
        (self.project / 'tools' / 'collect_committee_activity.py').write_text(STUB, encoding='utf-8')
        self.state = self.root / 'state'
        self.state.mkdir()
        (self.state / 'frontier.sqlite').write_bytes(b'')
        self.seeds = self.root / 'seeds.json'
        self.seeds.write_text('{}')
        self.priority = self.root / 'priority.json'
        self.priority.write_text('[]')
        self.local = self.root / 'local'
        self.local.mkdir()
        self.argv_file = self.root / 'argv.json'

    def env(self, **extra):
        environment = dict(os.environ, LOCALAPPDATA=str(self.local), DOWNSTREAM_STATE_HOST=str(self.state), STUB_ARGV=str(self.argv_file), PYTHONDONTWRITEBYTECODE='1',
                           USERPROFILE=str(self.root))
        environment.pop('GLASSBOX_EVENTS_DIR', None)
        environment.update(extra)
        return environment

    def run_job(self, *arguments, script='run_job.ps1', env=None, timeout=240):
        text = '& %s -PythonPath %s %s; exit $LASTEXITCODE' % (quote(self.project / 'tools' / script), quote(sys.executable), ' '.join(render(item) for item in arguments))
        return subprocess.run([POWERSHELL, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command', text], capture_output=True, env=env or self.env(),
                              timeout=timeout, text=True, encoding='utf-8', errors='replace')

    def good(self, *extra):
        return ['-Job', 'collect-downstream', '-Batch', '4', '-Workers', '4', '-MaxRequests', '300', '-Seeds', str(self.seeds)] + list(extra)

    # ---------------------------------------------------------------- plan
    def test_plan_only_shows_the_exact_collector_argv_and_never_adds_live(self):
        plain = self.run_job(*self.good(), '-PlanOnly')
        self.assertEqual(plain.returncode, 0, plain.stderr)
        plan = json.loads(plain.stdout)
        self.assertEqual((plan['job'], plan['runtime'], plan['live_requested'], plan['workers'], plan['max_requests']), ('collect-downstream', 'host-native (no container)', False, 4, 300))
        self.assertNotIn('--live', plan['arguments'])
        self.assertEqual(plan['arguments'], ['-B', str(self.project / 'tools' / 'collect_committee_activity.py'), '--state', str(self.state), '--seeds', str(self.seeds),
                                             '--max-requests', '300', '--workers', '4'])
        self.assertNotIn('docker', plain.stdout.lower().replace('no container', ''))
        self.assertEqual(list(self.local.iterdir()), [], 'a plan writes no log, lock or event')
        live = json.loads(self.run_job(*self.good(), '-PlanOnly', '-JobArgs', ['--live']).stdout)
        self.assertTrue(live['live_requested'])
        self.assertEqual(live['arguments'][-1], '--live')

    def test_contracts_and_priority_are_forwarded_before_the_caller_arguments(self):
        result = self.run_job(*self.good('-EnableEndpointContract', [TOTALS, LOOKUP], '-PriorityCandidates', str(self.priority)), '-JobArgs', ['--live', '--include-later-cycle-context'], '-PlanOnly')
        arguments = json.loads(result.stdout)['arguments']
        self.assertEqual(arguments[arguments.index('--workers'):], ['--workers', '4', '--enable-endpoint-contract', TOTALS, '--enable-endpoint-contract', LOOKUP,
                                                                     '--priority-candidates', str(self.priority), '--live', '--include-later-cycle-context'])

    # ---------------------------------------------------------------- refusals (exit 2, before anything runs)
    def test_refusals_exit_2_and_run_nothing(self):
        def swap(name, value):
            copy = self.good()
            copy[copy.index(name) + 1] = value
            return copy
        cases = {'no batch': swap('-Batch', '0'), 'workers 33': swap('-Workers', '33'), 'workers 0': swap('-Workers', '0'), 'no cap': swap('-MaxRequests', '0'),
                 'missing seeds': swap('-Seeds', str(self.root / 'nope.json')), 'bad contract': self.good('-EnableEndpointContract', 'not a contract'),
                 'missing priority file': self.good('-PriorityCandidates', str(self.root / 'nope.json'))}
        for flag in ('--state', '--seeds', '--max-requests', '--workers', '--init-new-ledger', '--recover-crashed-runs', '--rebind-endpoint-contracts',
                     '--close-out-legacy-run', '--migrate-to-schema3', '--enable-endpoint-contract', '--priority-candidates', '--bogus'):
            cases['jobargs ' + flag] = self.good('-JobArgs', ['--live', flag])
        for name, arguments in cases.items():
            with self.subTest(case=name):
                done = self.run_job(*arguments)
                self.assertEqual(done.returncode, 2, (name, done.stdout, done.stderr))
                self.assertIn('REFUSED', done.stderr)
                self.assertFalse(self.argv_file.exists(), 'the collector never started')
        (self.state / 'frontier.sqlite').unlink()
        done = self.run_job(*self.good('-JobArgs', ['--live']))
        self.assertEqual(done.returncode, 2)
        self.assertIn('never creates a ledger', done.stderr)
        self.assertFalse(self.argv_file.exists())

    # ---------------------------------------------------------------- run and verification (DE-053)
    def test_a_verified_run_exits_0_and_passes_exactly_what_it_was_given(self):
        done = self.run_job(*self.good('-EnableEndpointContract', [TOTALS], '-PriorityCandidates', str(self.priority)),
                            '-JobArgs', ['--live', '--include-later-cycle-context', '--max-failed-attempts', '8', '--ccl', '2024=x.txt'])
        self.assertEqual(done.returncode, 0, (done.stdout, done.stderr))
        self.assertEqual(json.loads(self.argv_file.read_text()),
                         ['--state', str(self.state), '--seeds', str(self.seeds), '--max-requests', '300', '--workers', '4', '--enable-endpoint-contract', TOTALS,
                          '--priority-candidates', str(self.priority), '--live', '--include-later-cycle-context', '--max-failed-attempts', '8', '--ccl', '2024=x.txt'])
        summary = (self.local / 'Mentat' / 'logs' / 'compute' / 'collect-downstream.log').read_text()
        self.assertIn('START collect-downstream', summary)
        self.assertIn('END exit=0', summary)
        self.assertTrue(list((self.local / 'Mentat' / 'compute' / 'locks').glob('collect-downstream.lock')))

    def test_without_live_in_jobargs_the_collector_is_not_given_it_and_the_run_fails(self):
        done = self.run_job(*self.good())
        self.assertEqual(done.returncode, 1, (done.stdout, done.stderr))
        self.assertNotIn('--live', json.loads(self.argv_file.read_text()))

    def test_exit_0_of_the_collector_is_not_completion_unless_its_receipt_verifies(self):
        for mode in ('unaccounted', 'archive-stop', 'over-cap', 'wrong-workers', 'no-print', 'fail'):
            with self.subTest(mode=mode):
                done = self.run_job(*self.good('-JobArgs', ['--live']), env=self.env(STUB_MODE=mode))
                self.assertEqual(done.returncode, 1, (mode, done.stdout, done.stderr))
        done = self.run_job(*self.good('-JobArgs', ['--live']), env=self.env(STUB_MODE='cap-stop'))
        self.assertEqual(done.returncode, 0, 'reaching the physical cap is an allowed stop')

    # ---------------------------------------------------------------- locks and second writers (exit 75)
    def test_another_run_holding_the_lock_refuses_with_75(self):
        locks = self.local / 'Mentat' / 'compute' / 'locks'
        locks.mkdir(parents=True)
        holder = subprocess.Popen([POWERSHELL, '-NoProfile', '-Command', "$s=[IO.File]::Open('%s','OpenOrCreate','ReadWrite','None');Write-Host held;Start-Sleep 60" % (locks / 'collect-downstream.lock')],
                                  stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(holder.stdout.readline().strip(), 'held')
            done = self.run_job(*self.good('-JobArgs', ['--live']))
            self.assertEqual(done.returncode, 75, (done.stdout, done.stderr))
            self.assertFalse(self.argv_file.exists())
        finally:
            holder.kill()
            holder.wait()

    def test_a_collector_process_already_running_refuses_with_75(self):
        other = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(60)', 'collect_committee_activity.py'])
        try:
            time.sleep(1)
            done = self.run_job(*self.good('-JobArgs', ['--live']))
            self.assertEqual(done.returncode, 75, (done.stdout, done.stderr))
            self.assertFalse(self.argv_file.exists())
        finally:
            other.kill()
            other.wait()

    # ---------------------------------------------------------------- nothing else changed
    def test_collect_fanout_block_is_byte_identical_and_the_other_jobs_plan_the_same(self):
        base = base_launcher()
        if base is None:
            self.skipTest('the unmodified launcher is unavailable: no run_job.base.ps1 beside run_job.ps1, and git cannot show %s (source export without history?)' % BASE_COMMIT)
        staged = (TOOLS / 'run_job.ps1').read_bytes()
        (self.project / 'tools' / 'run_job.base.ps1').write_bytes(base)       # a temp copy next to the temp copy of run_job.ps1, so both scripts resolve the same neighbours
        end_marker = b"    $hosts['REQUEST_POOL_CONFIG_FILE'] = ConvertTo-Slash $hostPoolConfig\n  }\n"
        start = base.index(b"  if ($Job -eq 'collect-fanout') {")
        block = base[start:base.index(end_marker, start) + len(end_marker)]
        self.assertEqual((len(block.splitlines()), staged.count(block)), (12, 1))
        staged_lines, cursor = staged.split(b'\n'), 0      # the staged file is the base plus insertions only: every base line survives, in order
        for line in base.split(b'\n'):
            cursor = staged_lines.index(line, cursor) + 1
        for job in ('anchors', 'refit-detect', 'collect-fanout', 'collect-company'):
            outputs = []
            for script in ('run_job.base.ps1', 'run_job.ps1'):
                done = self.run_job('-Job', job, '-RunId', 'parity-1', '-PlanOnly', script=script)
                outputs.append((done.returncode, done.stdout, re.sub(r'\s+$', '', done.stderr)))
            self.assertEqual(outputs[0], outputs[1], job)

    def test_an_unknown_job_is_still_refused_with_the_job_list(self):
        unknown = self.run_job('-Job', 'no-such-job')
        self.assertEqual(unknown.returncode, 2, (unknown.stdout, unknown.stderr))
        self.assertIn('Unknown job', unknown.stderr + unknown.stdout)
        self.assertIn('collect-fanout', unknown.stderr + unknown.stdout)


if __name__ == '__main__':
    unittest.main()

"""The Windows wrapper tools/run_job.ps1 (Windows PowerShell 5.1), driven against a fake `docker` and a temporary LOCALAPPDATA.
Fake keys and synthetic receipts only: no Docker, no network, no real key store, no real task."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'tools' / 'run_job.ps1'
DIGEST = 'sha256:' + 'ab' * 32
FAKE_KEYS = ['fake-key-aaaaaaaaaaaa', 'fake-key-bbbbbbbbbbbb']

# What `docker` does for the four calls the wrapper makes. FAKE_MODE: ok | no-receipt | failed.
FAKE_DOCKER = r'''
import json, os, sys, time
from pathlib import Path
args = sys.argv[1:]
log = Path(os.environ['FAKE_DOCKER_LOG'])
def note(**record):
    with log.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(record) + '\n')
if args[:1] == ['info']:
    sys.exit(0)
if args[:1] == ['rm']:
    note(call='rm', args=args)
    sys.exit(0)
if args[:1] == ['run']:                                   # the clock probe: docker run --rm --network none --entrypoint date IMAGE -u +%s.%N
    note(call='clock', args=args)
    print('%.6f' % (time.time() + float(os.environ.get('FAKE_SKEW', '0'))))
    sys.exit(0)
assert args[:1] == ['compose'], args
job = args[args.index('--no-deps') + 4] if '--no-deps' in args else None
run_id = args[args.index('--run-id') + 1]
keys_path = os.environ.get('FEC_KEYS_FILE')
seen = None
if keys_path:
    seen = {'exists': Path(keys_path).is_file(), 'content': Path(keys_path).read_text(encoding='utf-8') if Path(keys_path).is_file() else None}
note(call='compose', args=args, job=job, run_id=run_id, keys=seen,
     env={k: os.environ.get(k) for k in ('DIGEST', 'MENTAT_HOST', 'LOCALAPPDATA_HOST', 'GLASSBOX_HOST', 'REPORTS_HOST', 'FEC_KEYS_FILE', 'FEC_API_KEYS_FILE')})
mode = os.environ.get('FAKE_MODE', 'ok')
receipts = Path(os.environ['LOCALAPPDATA']) / 'Mentat' / 'compute' / 'receipts'
if mode in ('ok', 'failed'):
    receipts.mkdir(parents=True, exist_ok=True)
    good = mode == 'ok'
    (receipts / (run_id + '.json')).write_text(json.dumps({
        'schema': 'mentat-compute-job-receipt-v1', 'job': job, 'run_id': run_id, 'status': 'ok' if good else 'failed', 'exit_code': 0 if good else 1,
        'outputs': [], 'check': {'ok': good, 'problems': [] if good else ['fake failure']}}), encoding='utf-8')
sys.exit(0 if mode != 'failed' else 1)
'''


@unittest.skipUnless(os.name == 'nt' and shutil.which('powershell.exe'), 'Windows PowerShell 5.1 wrapper')
class RunJobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        for name in ('localappdata', 'reports', 'gb', 'bin'):
            (self.base / name).mkdir()
        (self.base / 'bin' / 'fake_docker.py').write_text(FAKE_DOCKER, encoding='utf-8')
        (self.base / 'bin' / 'docker.cmd').write_text('@"%s" "%s" %%*\r\n' % (sys.executable, self.base / 'bin' / 'fake_docker.py'), encoding='ascii')
        (self.base / 'bin' / 'wsl.cmd').write_text('@echo resync>>"%s"\r\n' % (self.base / 'wsl.log'), encoding='ascii')
        self.lock = self.base / 'images.lock'
        self.lock.write_text(json.dumps({'image': 'ghcr.io/pjg93-222/compute-mentat', 'digest': DIGEST}), encoding='utf-8')
        self.keys = self.base / 'fake-keys.txt'
        self.keys.write_text('\n'.join(FAKE_KEYS) + '\n', encoding='utf-8')
        self.log = self.base / 'docker.jsonl'
        self.env = {**{k: v for k, v in os.environ.items() if not k.startswith(('FEC_API_KEY', 'MENTAT_', 'GLASSBOX', 'REPORTS_HOST'))},
                    'LOCALAPPDATA': str(self.base / 'localappdata'), 'MENTAT_REPORT_DIRECTORY': str(self.base / 'reports'),
                    'GLASSBOX_EVENTS_DIR': str(self.base / 'gb'), 'FAKE_DOCKER_LOG': str(self.log), 'FEC_API_KEYS_FILE': str(self.keys)}

    def wrapper(self, *arguments, env=None, lock=True, command=None):
        """-> CompletedProcess of the wrapper; the fake docker, the temp image lock and this interpreter are filled in."""
        extra = ['-DockerPath', str(self.base / 'bin' / 'docker.cmd'), '-WslPath', str(self.base / 'bin' / 'wsl.cmd'), '-PythonPath', sys.executable]
        if lock:
            extra += ['-ImageLock', str(self.lock)]
        argv = ['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass']
        argv += command if command else ['-File', str(SCRIPT), *arguments, *extra]
        return subprocess.run(argv, capture_output=True, text=True, env={**self.env, **(env or {})}, timeout=240)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text(encoding='utf-8').splitlines()] if self.log.is_file() else []

    def events(self):
        return [json.loads(p.read_text(encoding='utf-8')) for p in sorted((self.base / 'gb').glob('evt-*.json'))]

    def run_dir(self):
        return self.base / 'localappdata' / 'Melange' / 'compute' / 'run'

    def everything_written(self):
        """Every byte the wrapper and the fake left under the temp LOCALAPPDATA and Glass Box folders, as text."""
        parts = []
        for folder in (self.base / 'localappdata', self.base / 'gb'):
            for path in folder.rglob('*'):
                if path.is_file():
                    parts.append(path.read_bytes().decode('utf-8', 'replace'))
        return '\n'.join(parts)

    # --- the plan and the refusals ---------------------------------------------------------------------------------------------
    def test_plan_only_prints_the_compose_command_and_touches_nothing(self):
        done = self.wrapper('-Job', 'pac-evidence', '-RunId', 'plan-1', '-PlanOnly')
        self.assertEqual(done.returncode, 0, done.stderr)
        plan = json.loads(done.stdout)
        self.assertEqual(plan['compose_arguments'][:12], ['compose', '-f', str(ROOT / 'compute.compose.yaml'), 'run', '--rm', '--no-deps', '-T', '--name',
                                                           'mentat-compute-plan-1', 'pac-evidence', 'run', 'pac-evidence'])
        self.assertEqual(plan['compose_arguments'][-3:], ['--run-id', 'plan-1', '--'])
        self.assertEqual(plan['image'], 'ghcr.io/pjg93-222/compute-mentat@' + DIGEST)
        env = plan['environment']
        self.assertEqual((env['DIGEST'], env['MENTAT_HOST']), (DIGEST, str(ROOT.parent).replace('\\', '/')))
        self.assertEqual(env['LOCALAPPDATA_HOST'], str(self.base / 'localappdata').replace('\\', '/'))
        self.assertEqual(env['GLASSBOX_HOST'], str(self.base / 'gb').replace('\\', '/'))
        self.assertTrue(plan['fec_keys'] and plan['network'])
        self.assertEqual(plan['timeout_minutes'], 125)                               # the job's 120 plus five
        self.assertIn('/mentat=' + env['MENTAT_HOST'], plan['verify_maps'])
        self.assertEqual(len(plan['verify_maps']), 3)
        self.assertFalse(self.log.exists())                                           # no Docker call
        self.assertFalse(self.run_dir().exists())                                     # no key staged
        self.assertEqual(self.events(), [])
        for key in FAKE_KEYS:
            self.assertNotIn(key, done.stdout)

    def test_the_report_directory_comes_from_the_configuration_for_the_jobs_that_read_reports(self):
        plan = json.loads(self.wrapper('-Job', 'anchors', '-RunId', 'plan-2', '-PlanOnly').stdout)
        self.assertEqual(plan['environment']['REPORTS_HOST'], str(self.base / 'reports').replace('\\', '/'))
        self.assertIn('/reports=' + plan['environment']['REPORTS_HOST'], plan['verify_maps'])
        plan = json.loads(self.wrapper('-Job', 'pocket-build', '-RunId', 'plan-3', '-PlanOnly', env={'POCKET_OUT_HOST': 'C:\\out dir\\'}).stdout)
        self.assertNotIn('REPORTS_HOST', plan['environment'])
        self.assertEqual(plan['environment']['POCKET_OUT_HOST'], 'C:/out dir')
        self.assertIn('/work/out=C:/out dir', plan['verify_maps'])

    def test_a_scratch_output_for_the_anchors_job_is_passed_through_and_mapped_for_verification(self):
        plan = json.loads(self.wrapper('-Job', 'anchors', '-RunId', 'plan-5', '-PlanOnly', env={'ANCHORS_OUT_HOST': 'D:/scratch/anchors-B/'}).stdout)
        self.assertEqual(plan['environment']['ANCHORS_OUT_HOST'], 'D:/scratch/anchors-B')
        self.assertIn('/mentat/mentat/state/giving-anchors=D:/scratch/anchors-B', plan['verify_maps'])

    def test_job_arguments_follow_the_double_dash_as_separate_arguments(self):
        command = ['-Command', "& '%s' -Job pac-evidence -RunId plan-4 -PlanOnly -PythonPath '%s' -ImageLock '%s' -JobArgs '--output','C:\\x y\\out','--company','X'"
                   % (SCRIPT, sys.executable, self.lock)]
        plan = json.loads(self.wrapper(command=command).stdout)
        self.assertEqual(plan['compose_arguments'][-5:], ['--', '--output', 'C:\\x y\\out', '--company', 'X'])

    def test_a_missing_digest_a_tag_an_unknown_job_and_a_bad_run_id_are_refused_before_anything_runs(self):
        for text in ('{"image": "ghcr.io/pjg93-222/compute-mentat", "digest": null}', '{"image": "x", "digest": "latest"}',
                     '{"image": "x", "digest": "sha256:' + 'AB' * 32 + '"}', '{"image": "x", "digest": "sha256:abc"}'):
            self.lock.write_text(text, encoding='utf-8')
            done = self.wrapper('-Job', 'pocket-build', '-RunId', 'r-1')
            self.assertEqual(done.returncode, 2, text)
            self.assertIn('digest', done.stderr)
        self.lock.write_text(json.dumps({'image': 'x', 'digest': DIGEST}), encoding='utf-8')
        self.assertEqual(self.wrapper('-Job', 'no-such-job').returncode, 2)
        self.assertEqual(self.wrapper('-Job', 'pocket-build', '-RunId', '../escape').returncode, 2)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.events(), [])

    def test_the_shipped_image_lock_has_no_digest_until_ci_publishes_one(self):
        lock = json.loads((ROOT / 'compute.images.lock').read_text(encoding='utf-8'))
        self.assertEqual(lock['image'], 'ghcr.io/pjg93-222/compute-mentat')
        self.assertTrue(lock['digest'] is None or lock['digest'].startswith('sha256:'))
        if lock['digest'] is None:
            done = self.wrapper('-Job', 'pocket-build', lock=False)
            self.assertEqual(done.returncode, 2)

    # --- a run ------------------------------------------------------------------------------------------------------------------
    def test_a_verified_run_exits_0_and_a_collecting_job_sees_the_key_file_only_while_it_runs(self):
        done = self.wrapper('-Job', 'pac-evidence', '-RunId', 'run-ok')
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        compose = [c for c in self.calls() if c['call'] == 'compose']
        self.assertEqual(len(compose), 1)
        self.assertEqual(compose[0]['job'], 'pac-evidence')
        self.assertEqual(compose[0]['keys'], {'exists': True, 'content': '\n'.join(FAKE_KEYS) + '\n'})
        self.assertEqual(compose[0]['env']['DIGEST'], DIGEST)
        self.assertTrue(compose[0]['env']['FEC_KEYS_FILE'].replace('/', '\\').startswith(str(self.run_dir())))
        self.assertFalse(self.run_dir().joinpath('run-ok').exists())                        # wiped
        self.assertEqual(list(self.run_dir().iterdir()), [])
        self.assertTrue((self.base / 'localappdata' / 'Mentat' / 'compute' / 'receipts' / 'run-ok.json').is_file())
        logs = self.base / 'localappdata' / 'Mentat' / 'logs' / 'compute'
        self.assertTrue(any(p.name.startswith('pac-evidence-') and p.name.endswith('.out.log') for p in logs.iterdir()))
        self.assertIn('END exit=0', (logs / 'pac-evidence.log').read_text(encoding='utf-8'))
        kinds = [(e['kind'], e['status'], e['run_id'], e['source']) for e in self.events()]
        self.assertEqual(kinds, [('run.start', 'running', 'run-ok', 'mentat-compute'), ('run.finish', 'completed', 'run-ok', 'mentat-compute')])
        text = self.everything_written() + done.stdout + done.stderr
        for key in FAKE_KEYS:
            self.assertNotIn(key, text.replace(str(self.keys), ''))        # nothing the wrapper wrote carries a key value

    def test_a_job_that_does_not_collect_gets_no_key_file(self):
        self.assertEqual(self.wrapper('-Job', 'pocket-build', '-RunId', 'run-nokeys').returncode, 0)
        compose = next(c for c in self.calls() if c['call'] == 'compose')
        self.assertIsNone(compose['keys'])
        self.assertIsNone(compose['env']['FEC_KEYS_FILE'])
        self.assertFalse(self.run_dir().exists() and any(self.run_dir().iterdir()))

    def test_docker_exit_0_without_a_receipt_is_a_failure_and_the_keys_are_still_wiped(self):
        done = self.wrapper('-Job', 'pac-evidence', '-RunId', 'run-lost', env={'FAKE_MODE': 'no-receipt'})
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(list(self.run_dir().iterdir()), [])
        finish = self.events()[-1]
        self.assertEqual((finish['kind'], finish['status']), ('run.finish', 'failed'))
        self.assertIn('no receipt', finish['detail'])

    def test_a_failed_job_receipt_is_a_failure_and_the_keys_are_wiped(self):
        done = self.wrapper('-Job', 'pac-evidence', '-RunId', 'run-bad', env={'FAKE_MODE': 'failed'})
        self.assertEqual(done.returncode, 1)
        self.assertEqual(list(self.run_dir().iterdir()), [])
        self.assertIn('fake failure', self.events()[-1]['detail'])

    def test_a_vm_clock_that_stays_off_stops_the_run_after_one_resync_and_before_any_key_is_staged(self):
        done = self.wrapper('-Job', 'pac-evidence', '-RunId', 'run-skew', env={'FAKE_SKEW': '45'})
        self.assertEqual(done.returncode, 3, done.stdout + done.stderr)
        self.assertEqual((self.base / 'wsl.log').read_text().split(), ['resync'])
        self.assertEqual([c['call'] for c in self.calls()], ['clock', 'clock'])
        self.assertFalse(self.run_dir().exists() and any(self.run_dir().iterdir()))
        self.assertIn('clock', self.events()[-1]['detail'])

    def test_a_small_skew_needs_no_resync(self):
        self.assertEqual(self.wrapper('-Job', 'pocket-build', '-RunId', 'run-ok2', env={'FAKE_SKEW': '1.0'}).returncode, 0)
        self.assertFalse((self.base / 'wsl.log').exists())

    def test_a_second_run_of_the_same_job_is_refused_while_the_first_holds_the_lock(self):
        locks = self.base / 'localappdata' / 'Mentat' / 'compute' / 'locks'
        locks.mkdir(parents=True)
        ready = self.base / 'ready'
        holder = subprocess.Popen(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
                                   "$s = [IO.File]::Open('%s', 'OpenOrCreate', 'ReadWrite', 'None'); New-Item '%s' -ItemType File | Out-Null; Start-Sleep -Seconds 60"
                                   % (locks / 'pocket-build.lock', ready)])
        self.addCleanup(lambda: (holder.kill(), holder.wait()))              # wait: the file is held until the process is gone
        deadline = time.time() + 30
        while not ready.exists() and time.time() < deadline:
            time.sleep(0.2)
        self.assertTrue(ready.exists())
        done = self.wrapper('-Job', 'pocket-build', '-RunId', 'run-locked')
        self.assertEqual(done.returncode, 75, done.stdout + done.stderr)
        self.assertEqual(self.calls(), [])


@unittest.skipUnless(shutil.which('docker'), 'no docker CLI (the engine is not needed: `docker compose config` only interpolates and validates)')
class ComposeConfigTests(unittest.TestCase):
    """The compose file is valid Compose, and the variables run_job.ps1 sets are the ones it requires."""
    HOSTS = {'DIGEST': DIGEST, 'MENTAT_HOST': 'C:/m', 'LOCALAPPDATA_HOST': 'C:/l', 'GLASSBOX_HOST': 'C:/g'}

    def config(self, env):
        base = {k: v for k, v in os.environ.items() if k not in ('DIGEST', 'MENTAT_HOST', 'LOCALAPPDATA_HOST', 'GLASSBOX_HOST', 'FEC_KEYS_FILE')}
        return subprocess.run(['docker', 'compose', '-f', str(ROOT / 'compute.compose.yaml'), 'config', '--format', 'json'],
                              capture_output=True, text=True, env={**base, **env}, timeout=120)

    def test_the_variables_the_wrapper_sets_are_enough_and_each_one_is_required(self):
        done = self.config(self.HOSTS)
        self.assertEqual(done.returncode, 0, done.stderr)
        services = json.loads(done.stdout)['services']
        self.assertEqual(sorted(services), ['anchors', 'collect-fanout', 'pac-evidence', 'pocket-build', 'refit', 'refit-detect', 'refit-parity'])
        self.assertTrue(all(s['image'] == 'ghcr.io/pjg93-222/compute-mentat@' + DIGEST for s in services.values()))
        for name in self.HOSTS:
            done = self.config({k: v for k, v in self.HOSTS.items() if k != name})
            self.assertNotEqual(done.returncode, 0, name)
            self.assertIn(name, done.stderr)

    def test_the_anchors_output_mount_defaults_to_the_live_folder_and_moves_with_ANCHORS_OUT_HOST(self):
        def output_source(env):
            done = self.config({**self.HOSTS, **env})
            self.assertEqual(done.returncode, 0, done.stderr)
            mounts = json.loads(done.stdout)['services']['anchors']['volumes']
            return next(m['source'] for m in mounts if m['target'] == '/mentat/mentat/state/giving-anchors').replace(chr(92), '/')
        self.assertEqual(output_source({}), 'C:/m/mentat/state/giving-anchors')
        self.assertEqual(output_source({'ANCHORS_OUT_HOST': 'D:/scratch/B'}), 'D:/scratch/B')

    def test_the_keys_secret_is_the_staged_file_when_given(self):
        done = self.config({**self.HOSTS, 'FEC_KEYS_FILE': 'C:/run/x/fec_api_keys'})
        secrets = json.loads(done.stdout)['secrets']
        self.assertEqual(secrets['fec_api_keys']['file'].replace(chr(92), '/'), 'C:/run/x/fec_api_keys')


if __name__ == '__main__':
    unittest.main()

"""Inert D075 controls for `tools\\run_job.ps1 -Runtime container` (Windows PowerShell 5.1), driven against a FAKE docker and a throw-away project tree.

What is pinned: the opt-in (the default stays host-native), every refusal before Docker work (exit 2), the volume label and ownership steps, that the FEC keys are staged for the host broker ONLY
(never mounted, never an environment variable of the compose run) and wiped, that the broker the wrapper starts is the v2 broker for exactly this run id / cap / family set, that the
descriptor it mounts is key-free, the verification maps, the shared lock (75), the host quarantine before a recovery, and -JobArgsJson. Fake keys only: no Docker, network or real key store.
"""
from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

TOOLS = Path(__file__).resolve().parents[1] / 'tools'
sys.path.insert(0, str(TOOLS))
import downstream_volume as dv  # noqa: E402
APP = Path(__file__).resolve().parents[1] / 'app'
POWERSHELL = shutil.which('powershell.exe')
DIGEST = 'sha256:' + 'ab' * 32
FAKE_KEYS = ['fake-key-aaaaaaaaaaaa', 'fake-key-bbbbbbbbbbbb']
CID = 'ab' * 16
TOTALS = 'openfec-unpinned-totals-numbered-v1'

FAKE_DOCKER = r'''
import json, os, sys, time
from pathlib import Path
args = sys.argv[1:]
log = Path(os.environ['FAKE_DOCKER_LOG'])
def note(**record):
    with log.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(record) + '\n')
mode = os.environ.get('FAKE_MODE', 'ok')
if args[:1] == ['info']:
    sys.exit(0)
if args[:1] == ['rm']:
    note(call='rm', args=args); sys.exit(0)
if args[:2] == ['volume', 'inspect']:
    note(call='volume-inspect', args=args)
    state = os.environ.get('FAKE_VOLUME', 'labelled')
    marker = Path(os.environ['FAKE_VOLUME_MARKER'])
    if state == 'none' and not marker.exists():
        sys.exit(1)
    print(json.dumps(None if state == 'unlabelled' else {'com.mentat.keep': 'true'}))
    sys.exit(0)
if args[:2] == ['volume', 'create']:
    note(call='volume-create', args=args); Path(os.environ['FAKE_VOLUME_MARKER']).write_text('x'); print(args[-1]); sys.exit(0)
if args[:1] == ['ps']:
    note(call='ps', args=args)
    if 'label=com.docker.compose.project=mentat-compute' in args and '--format' in args:      # FAKE_RUNNING = "container:service,..." as compose labels them
        for item in filter(None, os.environ.get('FAKE_RUNNING', '').split(',')):
            container, service = item.split(':')
            # the real CLI (docker/cli formatter): `.Labels` is ONE comma-joined, sorted key=value string; `json .Labels` is that string JSON-quoted. Neither is a map (delta review D1).
            labels = ','.join(sorted(['com.docker.compose.config-hash=0f1e2d', 'com.docker.compose.container-number=1', 'com.docker.compose.oneoff=True', 'com.docker.compose.project=mentat-compute',
                                      'com.docker.compose.project.working_dir=C:/x/collect-downstream', 'com.docker.compose.service=' + service, 'com.docker.compose.version=2.29.7']))
            shape = args[args.index('--format') + 1]
            print(json.dumps(labels) if shape == '{{json .Labels}}' else labels if shape == '{{.Labels}}' else '')
    sys.exit(0)
if args[:1] == ['run']:
    entry = args[args.index('--entrypoint') + 1] if '--entrypoint' in args else ''
    note(call='chown' if entry == 'chown' else 'clock', args=args)
    if entry != 'chown':
        print('%.6f' % time.time())
    sys.exit(0)
assert args[:1] == ['compose'], args
job = args[args.index('--no-deps') + 4]
run_id = args[args.index('--run-id') + 1]
config = os.environ.get('REQUEST_POOL_CONFIG_FILE')
seen = None
if config:
    path = Path(config)
    descriptor = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else None
    keys_file = path.parent / 'fec_api_keys'
    seen = {'descriptor': descriptor, 'descriptor_has_a_key': any(k in path.read_text(encoding='utf-8') for k in ('fake-key-aaaaaaaaaaaa', 'fake-key-bbbbbbbbbbbb')) if path.is_file() else None,
            'keys_staged_beside_it': keys_file.is_file(), 'keys_content_ok': keys_file.is_file() and 'fake-key-aaaaaaaaaaaa' in keys_file.read_text(encoding='utf-8')}
state_dir = Path(os.environ['DOWNSTREAM_STATE_HOST'])
exports_dir = state_dir / 'exports'
exports_at_start = sorted(p.name for p in exports_dir.iterdir()) if exports_dir.is_dir() else None
note(call='compose', args=args, job=job, run_id=run_id, broker=seen, exports_at_start=exports_at_start,
     env={k: os.environ.get(k) for k in ('DIGEST', 'FEC_KEYS_FILE', 'REQUEST_POOL_CONFIG_FILE', 'DOWNSTREAM_STATE_HOST', 'DOWNSTREAM_EXPORT_HOST', 'DOWNSTREAM_INPUTS_HOST', 'DOWNSTREAM_IMPORT_HOST',
                                        'DOWNSTREAM_VOLUME', 'DOWNSTREAM_STORED_MAP', 'FEC_API_KEYS_FILE')},
     state_listing=sorted(p.name for p in Path(os.environ['DOWNSTREAM_STATE_HOST']).iterdir()))
if job == 'collect-downstream' and mode == 'ok':
    # the files a real run leaves on the host: the broker's journal and BROKER receipt, and the collector receipt the in-job export copied. FAKE_BROKER picks the shape.
    cid = args[args.index('--collector-run-id') + 1]
    shape = os.environ.get('FAKE_BROKER', 'ok')
    charged, journalled, in_file, counted, late = {'undelivered': (3, 3, 3, 2, 1), 'gap': (4, 3, 3, 3, 0), 'journal-short': (3, 3, 2, 3, 0), 'receipt-short': (3, 3, 3, 2, 0)}.get(shape, (3, 3, 3, 3, 0))
    (state_dir / 'broker').mkdir(exist_ok=True)
    (state_dir / 'broker' / (cid + '.attempts.ndjson')).write_text(''.join(json.dumps({'event': 'attempt', 'n': i + 1}) + '\n' for i in range(in_file)), encoding='utf-8')
    if shape != 'no-broker':
        (state_dir / 'broker' / ('BROKER-' + cid + '.json')).write_text(json.dumps({'physical_attempts_charged': charged, 'attempts_journalled': journalled, 'undelivered_attempts': list(range(late))}), encoding='utf-8')
    if shape != 'no-receipt':
        (exports_dir / cid).mkdir(parents=True)
        (exports_dir / cid / ('RECEIPT-' + cid + '.json')).write_text(json.dumps({'http_attempts': counted}), encoding='utf-8')
receipts = Path(os.environ['LOCALAPPDATA']) / 'Mentat' / 'compute' / 'receipts'
if mode in ('ok', 'failed'):
    receipts.mkdir(parents=True, exist_ok=True)
    good = mode == 'ok'
    (receipts / (run_id + '.json')).write_text(json.dumps({'schema': 'mentat-compute-job-receipt-v1', 'job': job, 'run_id': run_id, 'status': 'ok' if good else 'failed', 'exit_code': 0 if good else 1,
                                                          'outputs': [], 'check': {'ok': good, 'problems': [] if good else ['fake failure']}}), encoding='utf-8')
sys.exit(0 if mode != 'failed' else 1)
'''


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def render(argument):
    if isinstance(argument, (list, tuple)):
        return ','.join(quote(item) for item in argument)
    return argument if argument.startswith('-') and not argument.startswith('--') else quote(argument)


@unittest.skipUnless(os.name == 'nt' and POWERSHELL, 'Windows PowerShell 5.1 is required')
class Launcher(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='D075-launcher-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = self.root / 'repo' / 'mentat'
        shutil.copytree(TOOLS, self.project / 'tools', ignore=shutil.ignore_patterns('__pycache__'))
        (self.project / 'app').mkdir()
        for item in APP.glob('*.py'):
            shutil.copyfile(item, self.project / 'app' / item.name)
        for name in ('compute.images.lock', 'compute.compose.yaml'):
            shutil.copyfile(TOOLS.parent / name, self.project / name)
        self.local, self.state, self.inputs, self.imported = (self.root / name for name in ('local', 'state', 'inputs', 'import'))
        for folder in (self.local, self.state, self.inputs, self.imported, self.root / 'pstemp', self.root / 'bin'):
            folder.mkdir()
        (self.state / 'frontier.sqlite.MOVED.json').write_text('{"volume": "mentat-downstream-frontier"}')
        (self.inputs / 'seeds.json').write_text('{}')
        (self.imported / 'frontier.sqlite').write_bytes(b'snapshot')
        (self.root / 'bin' / 'fake_docker.py').write_text(FAKE_DOCKER, encoding='utf-8')
        (self.root / 'bin' / 'docker.cmd').write_text('@"%s" "%s" %%*\r\n' % (sys.executable, self.root / 'bin' / 'fake_docker.py'), encoding='ascii')
        (self.root / 'bin' / 'wsl.cmd').write_text('@echo resync>>"%s"\r\n' % (self.root / 'wsl.log'), encoding='ascii')
        self.lock = self.root / 'images.lock'
        self.lock.write_text(json.dumps({'image': 'ghcr.io/pjg93-222/compute-mentat', 'digest': DIGEST}), encoding='utf-8')
        self.keys = self.root / 'fake-keys.txt'
        self.keys.write_text('\n'.join(FAKE_KEYS) + '\n', encoding='utf-8')
        self.log = self.root / 'docker.jsonl'
        self.base_env = {**{k: v for k, v in os.environ.items() if not k.startswith(('FEC_API_KEY', 'MENTAT_', 'GLASSBOX', 'DOWNSTREAM_', 'REQUEST_POOL', 'FAKE_'))},
                         'LOCALAPPDATA': str(self.local), 'TEMP': str(self.root / 'pstemp'), 'TMP': str(self.root / 'pstemp'), 'GLASSBOX_EVENTS_DIR': str(self.root / 'gb'),
                         'FAKE_DOCKER_LOG': str(self.log), 'FAKE_VOLUME_MARKER': str(self.root / 'volume-marker'), 'FEC_API_KEYS_FILE': str(self.keys), 'PYTHONDONTWRITEBYTECODE': '1',
                         'DOWNSTREAM_STATE_HOST': str(self.state), 'DOWNSTREAM_INPUTS_HOST': str(self.inputs), 'DOWNSTREAM_IMPORT_HOST': str(self.imported), 'DOWNSTREAM_MIN_FREE_GB': '0',
                         'MENTAT_API_BUDGET_DB': str(self.local / 'budget.sqlite')}
        (self.root / 'gb').mkdir()

    def run_job(self, *arguments, env=None, timeout=240):
        extra = ['-DockerPath', str(self.root / 'bin' / 'docker.cmd'), '-WslPath', str(self.root / 'bin' / 'wsl.cmd'), '-ImageLock', str(self.lock)]
        text = '& %s -PythonPath %s %s; exit $LASTEXITCODE' % (quote(self.project / 'tools' / 'run_job.ps1'), quote(sys.executable), ' '.join(render(item) for item in [*arguments, *extra]))
        return subprocess.run([POWERSHELL, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command', text], capture_output=True, text=True, encoding='utf-8', errors='replace',
                              env={**self.base_env, **(env or {})}, timeout=timeout)

    def job_args(self, **replace):
        values = {'--batch': '4', '--collector-run-id': CID, '--expect-base-sha256': 'b' * 64, '--seeds': '/inputs/seeds.json', '--max-requests': '300', '--max-failed-attempts': '8', '--workers': '4'}
        values.update(replace)
        return [item for flag, value in values.items() if value is not None for item in (flag, value)] + ['--live']

    def container(self, *more, job='collect-downstream', args=None, run_id='run-ds-1', **kw):
        return self.run_job('-Job', job, '-Runtime', 'container', '-RunId', run_id, '-JobArgs', self.job_args() if args is None else args, *more, **kw)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text(encoding='utf-8').splitlines()] if self.log.is_file() else []

    def run_dir(self):
        return self.local / 'Melange' / 'compute' / 'run'

    # ------------------------------------------------------------------ the plan
    def test_plan_only_shows_the_container_plan_without_docker_keys_or_files(self):
        done = self.container('-PlanOnly', args=self.job_args() + ['--enable-endpoint-contract', TOTALS])
        self.assertEqual(done.returncode, 0, done.stderr)
        plan = json.loads(done.stdout)
        self.assertEqual((plan['runtime'], plan['fec_keys'], plan['fec_keys_in_container'], plan['network']), ('container', False, False, True))
        self.assertEqual(plan['compose_arguments'][:12], ['compose', '-f', str(self.project / 'compute.compose.yaml'), 'run', '--rm', '--no-deps', '-T', '--name', 'mentat-compute-run-ds-1',
                                                           'collect-downstream', 'run', 'collect-downstream'])
        self.assertEqual(plan['timeout_minutes'], 725)
        down = plan['downstream']
        self.assertEqual((down['job_plan']['families'], down['job_plan']['collector_run_id'], down['job_plan']['max_requests']), ('B,E,totals', CID, 300))
        self.assertEqual((down['broker']['scope'], down['broker']['run_id'], down['broker']['max_attempts'], down['broker']['families']), ('spending-evidence-v2', CID, 300, 'B,E,totals'))
        self.assertIn('never mounted', down['broker']['keys'])
        self.assertTrue(down['live_requested'])
        env = plan['environment']
        slash = lambda path: str(path).replace('\\', '/')
        self.assertEqual((env['DOWNSTREAM_STATE_HOST'], env['DOWNSTREAM_EXPORT_HOST'], env['DOWNSTREAM_INPUTS_HOST'], env['DOWNSTREAM_VOLUME']),
                         (slash(self.state), slash(self.state / 'exports'), slash(self.inputs), 'mentat-downstream-frontier'))
        stored = self.state.as_posix().replace('/', '\\')
        self.assertEqual(env['DOWNSTREAM_STORED_MAP'], '/hoststate=%s;/frontier=%s;/export=%s\\exports;/inputs=%s' % (stored, stored, stored, slash(self.inputs).replace('/', '\\')))
        self.assertIn('/export=' + env['DOWNSTREAM_EXPORT_HOST'], plan['verify_maps'])
        self.assertIn('/hoststate=' + env['DOWNSTREAM_STATE_HOST'], plan['verify_maps'])
        self.assertNotIn('FEC_KEYS_FILE', env)
        after = plan['compose_arguments'][plan['compose_arguments'].index('--') + 1:]
        self.assertEqual(after, self.job_args() + ['--enable-endpoint-contract', TOTALS], 'the wrapper adds nothing: --live is exactly what the caller passed')
        self.assertEqual(plan['compose_arguments'].count('--live'), 1)
        self.assertFalse(self.log.exists(), 'no Docker call')
        self.assertEqual(list(self.local.iterdir()), [], 'no log, lock, key or event')
        self.assertFalse((self.state / 'exports').exists())
        for key in FAKE_KEYS:
            self.assertNotIn(key, done.stdout)

    def test_the_default_runtime_is_untouched_host_native_still_wins_without_runtime_container(self):
        done = self.run_job('-Job', 'collect-downstream', '-Batch', '4', '-Workers', '2', '-MaxRequests', '30', '-Seeds', str(self.inputs / 'seeds.json'), '-PlanOnly')
        self.assertEqual(done.returncode, 2, 'no ledger in the state folder: the host-native block is the one that answered')
        self.assertIn('never creates a ledger', done.stderr)
        (self.state / 'frontier.sqlite').write_bytes(b'')
        done = self.run_job('-Job', 'collect-downstream', '-Batch', '4', '-Workers', '2', '-MaxRequests', '30', '-Seeds', str(self.inputs / 'seeds.json'), '-PlanOnly')
        self.assertEqual(json.loads(done.stdout)['runtime'], 'host-native (no container)')

    def test_job_args_json_is_the_same_as_job_args_and_not_both(self):
        path = self.root / 'args.json'
        path.write_text(json.dumps(self.job_args() + ['--enable-endpoint-contract', TOTALS]))
        by_array = json.loads(self.container('-PlanOnly', args=self.job_args() + ['--enable-endpoint-contract', TOTALS]).stdout)
        by_json = json.loads(self.run_job('-Job', 'collect-downstream', '-Runtime', 'container', '-RunId', 'run-ds-1', '-JobArgsJson', str(path), '-PlanOnly').stdout)
        self.assertEqual(by_json['compose_arguments'], by_array['compose_arguments'])
        self.assertEqual(by_json['downstream'], by_array['downstream'])
        both = self.run_job('-Job', 'collect-downstream', '-Runtime', 'container', '-JobArgsJson', str(path), '-JobArgs', ['--live'], '-PlanOnly')
        self.assertEqual(both.returncode, 2)
        self.assertEqual(self.run_job('-Job', 'collect-downstream', '-Runtime', 'container', '-JobArgsJson', str(self.root / 'none.json'), '-PlanOnly').returncode, 2)
        path.write_text('{"not": "an array of strings"')
        self.assertEqual(self.run_job('-Job', 'collect-downstream', '-Runtime', 'container', '-JobArgsJson', str(path), '-PlanOnly').returncode, 2)

    # ------------------------------------------------------------------ refusals before any Docker work
    def test_refusals_exit_2_and_touch_neither_docker_nor_keys(self):
        cases = {'unknown runtime': self.run_job('-Job', 'collect-downstream', '-Runtime', 'bogus', '-JobArgs', self.job_args()),
                 'recover on the host runtime': self.run_job('-Job', 'collect-downstream-recover', '-JobArgs', ['--collector-run-id', CID]),
                 'import on the host runtime': self.run_job('-Job', 'collect-downstream-import', '-JobArgs', ['--expect-sha256', 'a' * 64]),
                 'InitVolume on the host runtime': self.run_job('-Job', 'collect-downstream', '-InitVolume', '-Batch', '1', '-MaxRequests', '1', '-Seeds', str(self.inputs / 'seeds.json')),
                 'a host-native parameter': self.run_job('-Job', 'collect-downstream', '-Runtime', 'container', '-Batch', '4', '-JobArgs', self.job_args()),
                 'workers 33': self.container(args=self.job_args(**{'--workers': '33'})),
                 'no --live': self.container(args=self.job_args()[:-1]),
                 'state flag': self.container(args=self.job_args() + ['--state', 'C:\\x']),
                 'init flag': self.container(args=self.job_args() + ['--init-new-ledger']),
                 'seeds outside inputs': self.container(args=self.job_args(**{'--seeds': '/etc/seeds.json'})),
                 'bad run id': self.container(run_id='../x')}
        for name, done in cases.items():
            with self.subTest(name):
                self.assertEqual(done.returncode, 2, (name, done.stdout, done.stderr))
        self.assertFalse(self.log.exists())
        self.assertFalse(self.run_dir().exists() and any(self.run_dir().iterdir()))

    def test_the_host_state_must_say_the_cutover_happened_and_must_be_a_safe_place(self):
        (self.state / 'frontier.sqlite.MOVED.json').unlink()
        done = self.container()
        self.assertEqual(done.returncode, 2, done.stdout)
        self.assertTrue('MOVED.json' in done.stderr + done.stdout)
        (self.state / 'frontier.sqlite.MOVED.json').write_text('{}')
        (self.state / 'frontier.sqlite').write_bytes(b'a live host ledger')
        done = self.container()
        self.assertEqual(done.returncode, 2)
        self.assertIn('would fork it', done.stderr + done.stdout)
        (self.state / 'frontier.sqlite').unlink()
        done = self.container(env={'TEMP': str(self.root)})                           # the state folder is under %TEMP%
        self.assertEqual(done.returncode, 2)
        self.assertIn('OneDrive or %TEMP%', done.stderr + done.stdout)
        done = self.container(env={'DOWNSTREAM_INPUTS_HOST': str(self.root / 'nowhere')})
        self.assertEqual(done.returncode, 2)
        self.assertIn('DOWNSTREAM_INPUTS_HOST', done.stderr + done.stdout)
        done = self.container(env={'DOWNSTREAM_MIN_FREE_GB': '100000000'})
        self.assertEqual(done.returncode, 2)
        self.assertIn('free space', done.stderr + done.stdout)
        self.assertFalse([c for c in self.calls() if c['call'] == 'compose'])
        self.assertFalse(self.run_dir().exists() and any(self.run_dir().iterdir()), 'no key was staged for any of these')

    def test_a_missing_or_foreign_volume_a_running_collector_and_a_second_run_are_refused(self):
        done = self.container(env={'FAKE_VOLUME': 'none'})
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn('-InitVolume', done.stderr + done.stdout)
        done = self.container(env={'FAKE_VOLUME': 'unlabelled'})
        self.assertEqual(done.returncode, 2)
        self.assertIn('com.mentat.keep', done.stderr + done.stdout)
        done = self.container(env={'FAKE_RUNNING': 'mentat-compute-ds-b04:collect-downstream'}, run_id='ds-b05')       # review F5: the container is named for the run id, not for the job
        self.assertEqual(done.returncode, 75, done.stdout + done.stderr)
        self.assertIn('collect-downstream', done.stderr + done.stdout)
        self.assertFalse([c for c in self.calls() if c['call'] == 'compose'])
        self.assertFalse(self.run_dir().exists() and any(self.run_dir().iterdir()), 'keys are staged only after every engine-side refusal')

    # ------------------------------------------------------------------ a run
    def test_a_verified_run_stages_keys_for_the_broker_only_mounts_a_key_free_descriptor_and_wipes_everything(self):
        done = self.container(args=self.job_args() + ['--enable-endpoint-contract', TOTALS])
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        compose = [c for c in self.calls() if c['call'] == 'compose']
        self.assertEqual(len(compose), 1)
        call = compose[0]
        self.assertEqual((call['job'], call['run_id']), ('collect-downstream', 'run-ds-1'))
        self.assertEqual(call['args'][call['args'].index('--') + 1:], self.job_args() + ['--enable-endpoint-contract', TOTALS])
        self.assertIsNone(call['env']['FEC_KEYS_FILE'], 'the keys are not a compose secret for this job')
        broker = call['broker']
        descriptor = broker['descriptor']
        self.assertEqual((descriptor['schema'], descriptor['scope'], descriptor['run_id'], descriptor['max_attempts'], descriptor['reach'], descriptor['width']),
                         ('mentat-evidence-pool-v2', 'fec_spending_evidence_v2', CID, 300, 'host.docker.internal', len(FAKE_KEYS)))
        self.assertEqual([f['family'] for f in descriptor['families']], ['B', 'E', 'totals'])
        self.assertEqual(descriptor['stored_root'], str(self.state))
        self.assertEqual((broker['descriptor_has_a_key'], broker['keys_staged_beside_it'], broker['keys_content_ok']), (False, True, True), 'the broker has the keys; the descriptor does not')
        self.assertTrue(call['env']['REQUEST_POOL_CONFIG_FILE'].replace('/', '\\').startswith(str(self.run_dir())))
        self.assertEqual(call['env']['DIGEST'], DIGEST)
        self.assertEqual(call['exports_at_start'], [], 'the wrapper makes the exports folder and never the run folder inside it')
        self.assertEqual([c['call'] for c in self.calls() if c['call'] in ('volume-create', 'chown')], [])
        self.assertFalse(self.run_dir().exists() and any(self.run_dir().iterdir()), 'keys and descriptor wiped, broker stopped')
        self.assertFalse((self.run_dir() / 'run-ds-1').exists())
        summary = (self.local / 'Mentat' / 'logs' / 'compute' / 'collect-downstream.log').read_text(encoding='utf-8')
        self.assertIn('END exit=0', summary)
        for key in FAKE_KEYS:
            self.assertNotIn(key, summary + done.stdout + done.stderr)

    def test_a_job_that_fails_or_leaves_no_receipt_is_a_failure_and_the_keys_are_still_wiped(self):
        for mode in ('failed', 'no-receipt'):
            with self.subTest(mode):
                done = self.container(run_id='run-' + mode, env={'FAKE_MODE': mode})
                self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
                self.assertFalse(self.run_dir().exists() and any(self.run_dir().iterdir()))

    def test_init_volume_creates_the_labelled_volume_once_and_hands_it_to_the_job_uid(self):
        done = self.container('-InitVolume', env={'FAKE_VOLUME': 'none'})
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        made = [c for c in self.calls() if c['call'] == 'volume-create']
        self.assertEqual(len(made), 1)
        self.assertEqual(made[0]['args'], ['volume', 'create', '--label', 'com.mentat.keep=true', 'mentat-downstream-frontier'])
        owned = [c for c in self.calls() if c['call'] == 'chown']
        self.assertEqual(len(owned), 1)
        text = ' '.join(owned[0]['args'])
        for needed in ('--user 0:0', '--cap-drop ALL', '--cap-add CHOWN', '--network none', '--pull=never', '10001:10001 /frontier'):
            self.assertIn(needed, text)
        self.assertNotIn('--privileged', text)

    def test_recovery_quarantines_the_unpaired_files_on_the_host_first_and_then_runs_the_no_network_job(self):
        folder = self.state / ('responses-' + CID)
        folder.mkdir()
        (folder / 'attempt-000001-aaaaaaaaaaaa.body.bin').write_bytes(b'half a body')
        done = self.run_job('-Job', 'collect-downstream-recover', '-Runtime', 'container', '-RunId', 'run-rec-1', '-JobArgs', ['--collector-run-id', CID])
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertFalse(list(folder.iterdir()), 'the unpaired body was moved aside before the container ran')
        self.assertTrue(list((self.state / 'crash-quarantine' / CID).glob('attempt-000001-*.body.bin')))
        compose = [c for c in self.calls() if c['call'] == 'compose']
        self.assertEqual((compose[0]['job'], compose[0]['env']['REQUEST_POOL_CONFIG_FILE'], compose[0]['broker']), ('collect-downstream-recover', None, None), 'no broker, no keys, no network for a recovery')
        self.assertFalse(self.run_dir().exists() and any(self.run_dir().iterdir()))

    def test_the_three_downstream_jobs_share_one_lock_and_a_recovery_waits_for_a_collection(self):
        locks = self.local / 'Mentat' / 'compute' / 'locks'
        locks.mkdir(parents=True)
        holder = subprocess.Popen([POWERSHELL, '-NoProfile', '-Command', "$s=[IO.File]::Open('%s','OpenOrCreate','ReadWrite','None');Write-Host held;Start-Sleep 60" % (locks / 'collect-downstream.lock')],
                                  stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(holder.stdout.readline().strip(), 'held')
            done = self.run_job('-Job', 'collect-downstream-recover', '-Runtime', 'container', '-RunId', 'run-rec-2', '-JobArgs', ['--collector-run-id', CID])
            self.assertEqual(done.returncode, 75, done.stdout + done.stderr)
            done = self.container(run_id='run-ds-2')
            self.assertEqual(done.returncode, 75, 'the collection itself holds the same lock file')
            self.assertFalse([c for c in self.calls() if c['call'] == 'compose'])
        finally:
            holder.kill()
            holder.wait()

    def test_the_import_job_needs_its_snapshot_folder_and_runs_with_no_broker(self):
        (self.imported / 'frontier.sqlite').unlink()
        done = self.run_job('-Job', 'collect-downstream-import', '-Runtime', 'container', '-RunId', 'run-imp-1', '-JobArgs', ['--expect-sha256', 'a' * 64])
        self.assertEqual(done.returncode, 2)
        self.assertIn('frontier.sqlite', done.stderr + done.stdout)
        (self.imported / 'frontier.sqlite').write_bytes(b'snapshot')
        done = self.run_job('-Job', 'collect-downstream-import', '-Runtime', 'container', '-RunId', 'run-imp-2', '-JobArgs', ['--expect-sha256', 'a' * 64])
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        call = [c for c in self.calls() if c['call'] == 'compose'][0]
        self.assertEqual((call['job'], call['broker'], call['env']['DOWNSTREAM_IMPORT_HOST']), ('collect-downstream-import', None, str(self.imported).replace('\\', '/')))
        self.assertIsNone(call['env']['REQUEST_POOL_CONFIG_FILE'], 'an import has no network and no descriptor')

    # ------------------------------------------------------------------ review F3, F4, F5
    def test_a_broker_that_charged_more_than_it_journalled_or_a_receipt_that_disagrees_fails_the_run(self):
        shapes = {'ok': 0, 'undelivered': 0, 'gap': 1, 'journal-short': 1, 'receipt-short': 1, 'no-broker': 1, 'no-receipt': 1}
        summary = self.local / 'Mentat' / 'logs' / 'compute' / 'collect-downstream.log'
        for index, (shape, expected) in enumerate(shapes.items(), start=1):
            with self.subTest(shape):
                cid = '%02x' % (0x50 + index) * 16
                done = self.container(run_id='run-rec-%d' % index, args=self.job_args(**{'--collector-run-id': cid}), env={'FAKE_BROKER': shape})
                self.assertEqual(done.returncode, expected, (shape, done.stdout, done.stderr))
                last = summary.read_text(encoding='utf-8').splitlines()[-1]
                self.assertIn('END exit=%d' % expected, last)
                self.assertEqual('BROKER RECONCILIATION FAILED' in last, bool(expected), last)
                self.assertFalse(self.run_dir().exists() and any(self.run_dir().iterdir()), 'keys wiped even when the run is refused for its accounting')

    def _exports(self, ids):
        """Real exports of earlier container runs under <state>/exports and their response folders; returns the sha256 of the last ledger.
        The stand-in ledger is a tiny real SQLite file whose pages name each run's response folder, as the collector's do: the receipt restore reads it (delta review D3)."""
        volume = self.root / 'old-volume'
        volume.mkdir(exist_ok=True)
        ledger = volume / 'frontier.sqlite'
        for cid in ids:
            with closing(sqlite3.connect(ledger)) as db:
                db.execute('CREATE TABLE IF NOT EXISTS pages(committee_id TEXT NOT NULL, page_no INTEGER NOT NULL, archive_path TEXT NOT NULL, PRIMARY KEY(committee_id, page_no))')
                db.execute('INSERT INTO pages VALUES (?,?,?)', ('C' + cid[:8], 1, 'C:\\stored\\responses-' + cid + '\\page-1.body.bin'))
                db.commit()
            (volume / ('RUN-' + cid + '.START.json')).write_text(json.dumps({'run_id': cid, 'seed_receipt': {}}))
            (volume / ('RECEIPT-' + cid + '.json')).write_text(json.dumps({'ledger_sha256': dv.sha256_file(ledger), 'http_attempts': 1, 'new_pages_this_invocation': 1}))
            self.assertEqual(dv.export_run(volume, self.state / 'exports', cid)['problems'], [])
            (self.state / ('responses-' + cid)).mkdir()
        return dv.sha256_file(ledger), ledger

    def test_the_import_job_restores_the_earlier_runs_receipts_into_the_state_folder_only_when_it_succeeded(self):
        first, second = '11' * 16, '22' * 16
        expect, ledger = self._exports([first, second])
        (self.imported / 'frontier.sqlite').write_bytes(ledger.read_bytes())
        arguments = ['-Job', 'collect-downstream-import', '-Runtime', 'container', '-JobArgs', ['--expect-sha256', expect]]
        failed = self.run_job(*arguments, '-RunId', 'run-imp-fail', env={'FAKE_MODE': 'failed'})
        self.assertEqual(failed.returncode, 1, failed.stdout + failed.stderr)
        self.assertFalse(list(self.state.glob('RECEIPT-*.json')), 'a failed import restores nothing')
        done = self.run_job(*arguments, '-RunId', 'run-imp-ok')
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        for cid in (first, second):
            self.assertEqual((self.state / ('RECEIPT-' + cid + '.json')).read_bytes(), (self.state / 'exports' / cid / ('RECEIPT-' + cid + '.json')).read_bytes())
        summary = (self.local / 'Mentat' / 'logs' / 'compute' / 'collect-downstream-import.log').read_text(encoding='utf-8')
        self.assertIn('RESTORED receipts of 2 earlier run(s)', summary)
        again = self.run_job(*arguments, '-RunId', 'run-imp-again')
        self.assertEqual(again.returncode, 0, 'a second import-time restore is idempotent')

    def test_an_import_whose_receipts_cannot_be_restored_is_not_a_clean_exit(self):
        only = '33' * 16
        expect, ledger = self._exports([only])
        (self.imported / 'frontier.sqlite').write_bytes(ledger.read_bytes())
        (self.state / ('RECEIPT-' + only + '.json')).write_text('{"someone else": true}')
        done = self.run_job('-Job', 'collect-downstream-import', '-Runtime', 'container', '-RunId', 'run-imp-clash', '-JobArgs', ['--expect-sha256', expect])
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn('RECEIPT RESTORE FAILED', (self.local / 'Mentat' / 'logs' / 'compute' / 'collect-downstream-import.log').read_text(encoding='utf-8'))
        self.assertEqual((self.state / ('RECEIPT-' + only + '.json')).read_text(), '{"someone else": true}')

    def test_the_running_container_check_matches_by_compose_service_whatever_the_run_id(self):
        cases = (('mentat-compute-ds-b04:collect-downstream', 75), ('mentat-compute-ds-b04-recover:collect-downstream-recover', 75), ('mentat-compute-ds-import-b03:collect-downstream-import', 75),
                 ('mentat-compute-anchors-1:anchors', 0), ('mentat-compute-anchors-1:anchors,mentat-compute-ds-b09:collect-downstream', 75), ('', 0))
        for index, (running, expected) in enumerate(cases, start=1):
            with self.subTest(running):
                cid = '%02x' % (0x70 + index) * 16
                done = self.container(run_id='run-busy-%d' % index, args=self.job_args(**{'--collector-run-id': cid}), env={'FAKE_RUNNING': running})
                self.assertEqual(done.returncode, expected, (running, done.stdout, done.stderr))
                asked = [c for c in self.calls() if c['call'] == 'ps'][-1]['args']
                self.assertIn('label=com.docker.compose.project=mentat-compute', asked)
                self.assertEqual(asked[asked.index('--format') + 1], '{{.Labels}}', 'the real shape: one key=value string, which a JSON parse cannot read')
                self.assertNotIn('name=mentat-compute-collect-downstream', ' '.join(asked))


if __name__ == '__main__':
    unittest.main()

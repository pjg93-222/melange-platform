"""The container entry point (tools/compute_job.py), the Windows-side key handoff (tools/compute_secrets.py) and the compose file
(compute.compose.yaml). Synthetic data and fake keys only: no real job collects, no real key is read or written."""
import ast
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import compute_job as cj  # noqa: E402
import compute_secrets as secrets  # noqa: E402
from compute_world import make_layout  # noqa: E402

FAKE_KEYS = ['fake-key-aaaaaaaaaaaa', 'fake-key-bbbbbbbbbbbb']


class Temp(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)


class SecretHandoffTests(Temp):
    def run_base(self):
        return self.base / 'run'

    def test_stage_writes_the_keys_to_one_per_run_file_and_returns_only_fingerprints(self):
        info = secrets.stage('job-1', loader=lambda: list(FAKE_KEYS), base=self.run_base(), restrict=False)
        self.assertEqual(Path(info['path']).read_text(encoding='utf-8'), '\n'.join(FAKE_KEYS) + '\n')
        self.assertEqual(info['count'], 2)
        self.assertEqual(len(info['key_ids']), 2)
        for key in FAKE_KEYS:
            self.assertNotIn(key, json.dumps(info))
        self.assertEqual(Path(info['path']).parent, self.run_base() / 'job-1')

    def test_wipe_overwrites_then_removes_and_is_idempotent(self):
        info = secrets.stage('job-2', loader=lambda: list(FAKE_KEYS), base=self.run_base(), restrict=False)
        written, real_unlink = [], Path.unlink

        def spy(path, *args, **kwargs):
            written.append(path.read_bytes())
            return real_unlink(path, *args, **kwargs)
        with patch.object(Path, 'unlink', spy):
            self.assertTrue(secrets.wipe('job-2', self.run_base()))
        self.assertEqual(written, [bytes(len(('\n'.join(FAKE_KEYS) + '\n').encode()))])            # zeros over the same length, then deleted
        self.assertFalse(Path(info['path']).exists())
        self.assertFalse(secrets.wipe('job-2', self.run_base()))
        self.assertFalse((self.run_base() / 'job-2').exists())

    def test_wipe_is_never_recursive_and_run_ids_cannot_escape_the_base(self):
        secrets.stage('job-3', loader=lambda: list(FAKE_KEYS), base=self.run_base(), restrict=False)
        (self.run_base() / 'job-3' / 'nested').mkdir()
        with self.assertRaises(RuntimeError):
            secrets.wipe('job-3', self.run_base())
        for bad in ('..', '../x', 'a/b', '', '-x'):
            with self.assertRaises(ValueError):
                secrets.run_dir(bad, self.run_base())

    def test_stage_refuses_a_reused_run_id_and_an_empty_key_store_and_leaves_nothing_behind(self):
        secrets.stage('job-4', loader=lambda: list(FAKE_KEYS), base=self.run_base(), restrict=False)
        with self.assertRaises(FileExistsError):
            secrets.stage('job-4', loader=lambda: list(FAKE_KEYS), base=self.run_base(), restrict=False)
        with self.assertRaises(ValueError):
            secrets.stage('job-5', loader=lambda: [], base=self.run_base(), restrict=False)
        self.assertFalse((self.run_base() / 'job-5').exists())

    def test_sweep_removes_only_runs_that_outlived_the_limit(self):
        secrets.stage('old-run', loader=lambda: list(FAKE_KEYS), base=self.run_base(), restrict=False)
        secrets.stage('new-run', loader=lambda: list(FAKE_KEYS), base=self.run_base(), restrict=False)
        old = time.time() - 3 * 86400
        os.utime(self.run_base() / 'old-run', (old, old))
        self.assertEqual(secrets.sweep(self.run_base(), 24), ['old-run'])
        self.assertEqual(sorted(p.name for p in self.run_base().iterdir()), ['new-run'])

    @unittest.skipUnless(os.name == 'nt', 'Windows ACL')
    def test_the_secret_folder_grants_the_current_user_alone(self):
        info = secrets.stage('job-acl', loader=lambda: list(FAKE_KEYS), base=self.run_base())
        listing = subprocess.run(['icacls', str(Path(info['path']).parent)], capture_output=True, text=True).stdout
        self.assertIn(os.environ['USERNAME'], listing)
        for broad in ('Everyone', 'BUILTIN\\Users', 'Authenticated Users'):
            self.assertNotIn(broad, listing)
        self.assertTrue(secrets.wipe('job-acl', self.run_base()))


class JobTests(Temp):
    def env(self, **more):
        return {**{k: v for k, v in os.environ.items() if not k.startswith(('FEC_API_KEY', 'MENTAT_', 'RF_', 'REFIT_'))},
                'GLASSBOX_EVENTS_DIR': str(self.base / 'gb'), 'MENTAT_COMPUTE_RECEIPT_DIR': str(self.base / 'receipts'), **more}

    def run_job(self, name, run_id, extra, env=None):
        with patch.object(cj.glassbox_emit, 'EVENTS_DIR', self.base / 'gb'):
            return cj.run(name, run_id, extra, env or self.env())

    def test_the_job_table_is_the_inventory_and_only_collectors_need_keys_and_a_network(self):
        self.assertEqual(sorted(cj.JOBS), ['anchors', 'collect-downstream', 'collect-downstream-import', 'collect-downstream-recover', 'collect-fanout', 'pac-evidence', 'pocket-build', 'refit',
                                           'refit-detect', 'refit-parity'])
        # D075: collect-downstream reaches the network only to talk to the host evidence broker, which holds the keys; it holds none itself
        self.assertEqual(sorted(n for n, j in cj.JOBS.items() if j.network), ['collect-downstream', 'collect-fanout', 'pac-evidence'])
        self.assertEqual(sorted(n for n, j in cj.JOBS.items() if j.keys), ['collect-fanout', 'pac-evidence'])
        self.assertEqual(sorted(n for n, j in cj.JOBS.items() if j.broker), ['collect-downstream'])
        self.assertEqual((cj.JOBS['anchors'].minutes, cj.JOBS['refit-detect'].minutes), (45, 20))           # the two scheduled tasks' own caps

    def test_dry_run_plans_each_job_without_a_key_value_and_refuses_a_missing_requirement(self):
        secret = self.base / 'fec_api_keys'
        secret.write_text('\n'.join(FAKE_KEYS) + '\n', encoding='utf-8')
        env = self.env(FEC_API_KEYS_FILE=str(secret), MENTAT_RSCRIPT='/usr/local/bin/Rscript', MENTAT_R_LIBRARY='/rlib')
        plans = {}
        for name, extra in (('anchors', []), ('refit-detect', []), ('refit-parity', ['--no-tests']), ('refit', ['--industry', 'x']),
                            ('pocket-build', ['--data', 'in.json', '--out', 'out.html']),
                            ('collect-fanout', ['--source-database', 'db', '--industry', 'X', '--run-directory', 'run']),
                            ('pac-evidence', ['--output', 'o', '--committees', 'C1', '--company', 'X'])):
            with patch('builtins.print') as printed:
                self.assertEqual(cj.run(name, 'r-' + name, extra, env, dry_run=True), 0)
            plans[name] = json.loads(printed.call_args[0][0])
            for key in FAKE_KEYS:
                self.assertNotIn(key, json.dumps(plans[name]))
        self.assertEqual(plans['refit-detect']['argv'][:5], ['/usr/local/bin/Rscript', str(ROOT / 'forecast' / 'refit' / 'run_refit.R'), '--mode', 'detect', '--run-id'])
        self.assertEqual(plans['refit']['argv'][3:9], ['refit', '--run-id', 'r-refit', '--publish', 'never', '--industry'])
        self.assertEqual(plans['collect-fanout']['argv'][2:6], ['--rscript-path', '/usr/local/bin/Rscript', '--r-library-path', '/rlib'])
        self.assertEqual(plans['collect-fanout']['keys']['count'], 2)
        self.assertIsNone(plans['anchors']['keys'])
        self.assertEqual(plans['anchors']['cwd'], str(ROOT.parent))
        for name, extra in (('pocket-build', ['--data', 'x']), ('collect-fanout', ['--industry', 'X']), ('pac-evidence', [])):
            with self.assertRaises(cj.JobError):
                cj.run(name, 'r', extra, env, dry_run=True)
        with self.assertRaises(cj.JobError):                       # a collecting job without its secret does not start
            cj.run('pac-evidence', 'r', ['--output', 'o', '--committees', 'C', '--company', 'X'], self.env(FEC_API_KEYS_FILE=str(self.base / 'absent')), dry_run=True)

    def test_a_job_that_does_not_collect_never_sees_a_key_and_a_collecting_one_sees_them_only_in_its_environment(self):
        seen = self.base / 'seen.json'
        probe = ['-c', 'import json, os, sys; json.dump({k: v for k, v in os.environ.items() if k.startswith("FEC_API_KEY")}, open(sys.argv[1], "w"))', str(seen)]
        secret = self.base / 'fec_api_keys'
        secret.write_text('\n'.join(FAKE_KEYS) + '\n', encoding='utf-8')
        env = self.env(FEC_API_KEYS_FILE=str(secret), FEC_API_KEY='ambient-key', FEC_API_KEY_2='ambient-key-2')
        for keys, expected in ((False, {}), (True, {'FEC_API_KEYS': '|'.join(FAKE_KEYS), 'FEC_API_KEYS_FILE': str(secret)})):
            job = cj.Job('probe', 1, keys, keys, lambda ctx, extra: [sys.executable, *probe], lambda ctx, extra: [seen],
                         lambda ctx, extra, started: [])
            with patch.dict(cj.JOBS, {'probe': job}):
                self.assertEqual(self.run_job('probe', 'probe-' + str(keys), [], env), 0)
            self.assertEqual(json.loads(seen.read_text()), expected)
            receipt = (self.base / 'receipts' / ('probe-' + str(keys) + '.json')).read_text()
            for value in (*FAKE_KEYS, 'ambient-key'):
                self.assertNotIn(value, receipt)

    def test_a_timeout_kills_the_job_and_the_receipt_says_failed(self):
        job = cj.Job('sleeper', 0, False, False, lambda ctx, extra: [sys.executable, '-c', 'import time; time.sleep(60)'], lambda ctx, extra: [], lambda ctx, extra, started: [])
        with patch.dict(cj.JOBS, {'sleeper': job}):
            self.assertEqual(self.run_job('sleeper', 'sleeper-1', []), 124)
        record = json.loads((self.base / 'receipts' / 'sleeper-1.json').read_text())
        self.assertEqual((record['status'], record['exit_code']), ('failed', 124))
        self.assertTrue(cj.verify(self.base / 'receipts' / 'sleeper-1.json', 'sleeper', 'sleeper-1'))

    def test_a_clean_exit_is_not_a_completed_job_unless_its_own_check_passes(self):
        job = cj.Job('liar', 1, False, False, lambda ctx, extra: [sys.executable, '-c', 'pass'], lambda ctx, extra: [], lambda ctx, extra, started: ['no receipt written'])
        with patch.dict(cj.JOBS, {'liar': job}):
            self.assertEqual(self.run_job('liar', 'liar-1', []), 1)
        record = json.loads((self.base / 'receipts' / 'liar-1.json').read_text())
        self.assertEqual((record['status'], record['exit_code'], record['check']['problems']), ('failed', 0, ['no receipt written']))

    def test_pocket_build_runs_for_real_and_verify_catches_a_changed_or_missing_output(self):
        data = self.base / 'in' / 'pocket-data.json'
        data.parent.mkdir()
        data.write_text(json.dumps({'datasets': [{'id': 'x', 'rows': [1, 2, 3]}]}), encoding='utf-8')
        page = self.base / 'out' / 'page.html'
        self.assertEqual(self.run_job('pocket-build', 'pocket-1', ['--data', str(data), '--out', str(page)]), 0)
        receipt = self.base / 'receipts' / 'pocket-1.json'
        self.assertEqual(cj.verify(receipt, 'pocket-build', 'pocket-1'), [])
        self.assertEqual(cj.verify(receipt, 'pocket-build', 'other-id') != [], True)
        host = self.base / 'host'
        host.mkdir()
        (host / 'page.html').write_bytes(page.read_bytes())
        self.assertEqual(cj.verify(receipt, 'pocket-build', 'pocket-1', [(str(page.parent), str(host))]), [])          # a container path mapped to its host path
        page.write_bytes(page.read_bytes() + b' ')
        self.assertTrue(any('changed or missing' in p for p in cj.verify(receipt, 'pocket-build', 'pocket-1')))
        page.unlink()
        self.assertTrue(any('changed or missing' in p for p in cj.verify(receipt, 'pocket-build', 'pocket-1')))

    def test_verify_maps_a_container_path_to_its_longest_mounted_prefix_on_a_path_boundary(self):
        maps = [('/mentat', 'C:/live'), ('/mentat/mentat/state/giving-anchors', 'D:/scratch'), ('/reports', 'C:/r')]
        self.assertEqual(cj._host('/mentat/mentat/state/giving-anchors/a.json', maps), Path('D:/scratch/a.json'))
        self.assertEqual(cj._host('/mentat/mentat/state/other/a.json', maps), Path('C:/live/mentat/state/other/a.json'))
        self.assertEqual(cj._host('/mentat2/x', maps), Path('/mentat2/x'))                    # not a path boundary: unmapped
        self.assertEqual(cj._host('/reports', maps), Path('C:/r'))

    def test_pocket_build_refuses_to_rewrite_the_template_in_place(self):
        with self.assertRaises(cj.JobError):
            self.run_job('pocket-build', 'pocket-2', ['--data', 'x.json'])

    def test_anchors_run_for_real_on_a_synthetic_layout_and_the_receipt_verifies(self):
        env_extra, extra = make_layout(self.base / 'world')
        env = self.env(**env_extra)
        self.assertEqual(self.run_job('anchors', 'anchors-1', extra, env), 0)
        receipt = Path(env['MENTAT_COMPUTE_RECEIPT_DIR']) / 'anchors-1.json'
        record = json.loads(receipt.read_text())
        self.assertEqual((record['job'], record['status'], record['exit_code'], record['check']), ('anchors', 'ok', 0, {'ok': True, 'problems': []}))
        self.assertEqual([Path(o['path']).name for o in record['outputs']], ['giving-anchors-v1.json', 'giving-anchors-v1.receipt.json'])
        self.assertEqual(cj.verify(receipt, 'anchors', 'anchors-1'), [])
        self.assertIn('LOCALAPPDATA', record['locations'])
        events = sorted((self.base / 'gb').glob('evt-*.json'))
        kinds = {json.loads(p.read_text())['source']: json.loads(p.read_text())['kind'] for p in events}
        self.assertIn('mentat-compute', kinds)
        self.assertEqual(self.run_job('anchors', 'anchors-2', ['--check', *extra], env), 0)                          # --check writes nothing and needs no outputs

    def test_a_failed_anchors_build_leaves_a_failed_receipt_with_no_outputs(self):
        env_extra, extra = make_layout(self.base / 'world')
        env = self.env(**{**env_extra, 'MENTAT_REPORT_DIRECTORY': str(self.base / 'no-such-reports')})
        self.assertEqual(self.run_job('anchors', 'anchors-bad', extra, env), 1)
        record = json.loads((self.base / 'world' / 'receipts' / 'anchors-bad.json').read_text())
        self.assertEqual((record['status'], record['outputs']), ('failed', []))

    def test_the_runbook_table_matches_the_job_table(self):
        rows = {}
        for line in (ROOT / 'COMPUTE.md').read_text(encoding='utf-8').splitlines():
            cells = [c.strip() for c in line.strip().strip('|').split('|')]
            if line.startswith('| `') and len(cells) == 6:
                rows[cells[0].strip('`')] = cells
        self.assertEqual(sorted(rows), sorted(cj.JOBS))
        for name, job in cj.JOBS.items():
            _, _, network, keys, cap, service = rows[name]
            self.assertEqual((network != 'none', keys == 'yes', cap, service.strip('`')), (job.network, job.keys, str(job.minutes) + ' min', name), name)

    def test_the_event_command_writes_a_glass_box_event(self):
        with patch.object(cj.glassbox_emit, 'EVENTS_DIR', self.base / 'gb'):
            self.assertEqual(cj.main(['event', '--kind', 'run.start', '--status', 'running', '--summary', 'wrapper', '--run-id', 'r-1', '--stage', 'start']), 0)
        event = json.loads(next((self.base / 'gb').glob('evt-*.json')).read_text())
        self.assertEqual((event['run_id'], event['kind'], event['status'], event['source']), ('r-1', 'run.start', 'running', 'mentat-compute'))

    def test_the_entry_point_imports_without_a_windows_api_and_calls_none(self):
        done = subprocess.run([sys.executable, '-c', 'import sys; sys.path.insert(0, %r); import compute_job; print(sorted(compute_job.JOBS)[0])' % str(ROOT / 'tools')],
                              capture_output=True, text=True)
        self.assertEqual((done.returncode, done.stdout.strip()), (0, 'anchors'), done.stderr)
        tree = ast.parse((ROOT / 'tools' / 'compute_job.py').read_text(encoding='utf-8'))
        tree.body = tree.body[1:]                                                            # the docstring names what it avoids
        code = ast.dump(tree).lower()
        for forbidden in ('windll', 'windll', 'msvcrt', 'taskkill', 'powershell', 'ctypes'):
            self.assertNotIn(forbidden, code)

    @unittest.skipUnless(os.environ.get('MENTAT_COMPUTE_REFIT_RLIB'), 'set MENTAT_COMPUTE_REFIT_RLIB to the vendored refit library to run Rscript end to end')
    def test_refit_detect_runs_for_real_over_a_synthetic_report_directory(self):
        rscript = Path(os.environ.get('MENTAT_RSCRIPT') or 'C:/Program Files/R/R-4.6.0/bin/Rscript.exe')
        if not rscript.is_file():
            self.skipTest('no Rscript')
        reports = self.base / 'reports'
        reports.mkdir()
        (reports / 'Crypto Industry Spending Overview.xlsx').write_bytes(b'not a real workbook')
        env = self.env(RF_RUNTIME=str(self.base / 'runtime'), RF_RLIB=os.environ['MENTAT_COMPUTE_REFIT_RLIB'], RF_REPORT_DIR=str(reports),
                       RF_GLASSBOX_DIR=str(self.base / 'gb'), MENTAT_RSCRIPT=str(rscript))
        self.assertEqual(self.run_job('refit-detect', 'detect-1', [], env), 0)
        receipt = self.base / 'receipts' / 'detect-1.json'
        self.assertEqual(cj.verify(receipt, 'refit-detect', 'detect-1'), [])
        self.assertTrue((self.base / 'runtime' / 'runs' / 'detect-1' / 'detect.csv').is_file())


class ComposeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import yaml
        except ImportError:
            raise unittest.SkipTest('PyYAML not installed; compose policy not checked')
        cls.doc = yaml.safe_load((ROOT / 'compute.compose.yaml').read_text(encoding='utf-8'))
        cls.services = cls.doc['services']

    def test_one_service_per_job_started_through_the_entry_point(self):
        self.assertEqual(sorted(self.services), sorted(cj.JOBS))
        for name, service in self.services.items():
            self.assertEqual(service['entrypoint'], ['python', '/mentat/mentat/tools/compute_job.py'])
            self.assertEqual(service['command'][:2], ['run', name])
            self.assertTrue(service['image'].startswith('ghcr.io/pjg93-222/compute-mentat@${DIGEST:?'), name)

    def test_every_service_is_hardened_and_bounded(self):
        for name, service in self.services.items():
            self.assertIs(service['read_only'], True, name)
            self.assertEqual(service['user'], '10001:10001', name)
            self.assertEqual(service['cap_drop'], ['ALL'], name)
            self.assertIn('no-new-privileges:true', service['security_opt'], name)
            self.assertTrue(any(t.startswith('/tmp') for t in service['tmpfs']), name)
            for limit in ('mem_limit', 'cpus', 'pids_limit'):
                self.assertIn(limit, service, name)
            for forbidden in ('ports', 'privileged', 'cap_add', 'devices', 'pid', 'ipc', 'userns_mode', 'volumes_from'):
                self.assertNotIn(forbidden, service, name)

    def test_only_the_collectors_have_a_network_and_the_keys_secret(self):
        for name, service in self.services.items():
            if cj.JOBS[name].network:
                self.assertEqual(service['networks'], ['collect'], name)
                self.assertNotIn('network_mode', service, name)
            else:
                self.assertEqual(service['network_mode'], 'none', name)
                self.assertNotIn('networks', service, name)
            secrets = service.get('secrets', [])
            self.assertEqual('fec_api_keys' in secrets, cj.JOBS[name].keys, name)
            self.assertEqual('fec_request_pool' in secrets, cj.JOBS[name].broker or name == 'collect-fanout', name)
            self.assertEqual(set(secrets) - {'fec_api_keys', 'fec_request_pool'}, set(), name)
        self.assertEqual(self.services['collect-downstream']['secrets'], ['fec_request_pool'], 'the downstream container is mounted the key-free descriptor and never the keys')
        self.assertEqual(self.doc['secrets']['fec_api_keys']['file'], '${FEC_KEYS_FILE:-/dev/null}')
        self.assertEqual(sorted(self.doc['networks']), ['collect'])
        # the one named volume is external, so compose never creates an empty ledger by typo
        self.assertEqual(self.doc['volumes'], {'frontier': {'name': '${DOWNSTREAM_VOLUME:-mentat-downstream-frontier}', 'external': True}})

    def test_data_is_mounted_read_only_unless_the_job_writes_there_and_never_auto_created(self):
        for name, service in self.services.items():
            mounts = [v for v in service['volumes'] if isinstance(v, dict)]
            self.assertEqual(len(mounts), len(service['volumes']), name)           # long form only
            for mount in mounts:
                if mount['type'] == 'volume':                                      # D075: the live downstream ledger, never a bind (cross-OS SQLite locks are blind)
                    self.assertEqual((name in DOWNSTREAM, mount['source'], mount['target'], 'read_only' in mount), (True, 'frontier', '/frontier', False), name)
                    continue
                self.assertEqual(mount['type'], 'bind', name)
                self.assertIs(mount['bind']['create_host_path'], False, (name, mount['target']))
            writable = sorted(m['target'] for m in mounts if not m.get('read_only'))
            self.assertEqual(writable, EXPECTED_WRITABLE[name], name)
            roots = [m for m in mounts if m['target'] == '/mentat']
            self.assertEqual(len(roots), 1, name)
            self.assertIs(roots[0]['read_only'], True, name)                        # the code and data tree is never writable as a whole

    def test_nothing_of_melange_is_mounted_beyond_the_two_lda_files_the_anchors_build_reads(self):
        melange = {}
        for name, service in self.services.items():
            for mount in service['volumes']:
                if 'Melange' in mount['source'] or mount['target'].startswith('/localappdata/Melange'):
                    melange.setdefault(name, []).append((mount['target'], mount.get('read_only')))
        self.assertEqual(melange, {'anchors': [
            ('/localappdata/Melange/lda_rebuild/derived/sector_spend/sector_spend.csv', True),
            ('/localappdata/Melange/lda_rebuild/derived/fullscope_receipts.json', True)]})

    def test_the_downstream_services_see_the_host_state_read_only_and_hold_no_key_and_no_host_pool_secret_where_they_have_no_network(self):
        for name in DOWNSTREAM:
            service = self.services[name]
            mounts = {m['target']: m for m in service['volumes']}
            self.assertNotIn('fec_api_keys', service.get('secrets', []), name)
            self.assertFalse([k for k in service['environment'] if k.startswith(('FEC_API_KEY', 'FEC_KEYS')) or k in ('FEC_API_KEYS_FILE',)], name)
            for target in ('/hoststate', '/inputs', '/import', '/mentat'):
                if target in mounts:
                    self.assertIs(mounts[target].get('read_only'), True, (name, target))
            self.assertNotIn('/collect', mounts)
            self.assertEqual(service['mem_limit'], '1g' if name.endswith('import') else '4g', name)
        self.assertEqual(self.services['collect-downstream']['environment']['MENTAT_REQUEST_POOL_CONFIG'], '/run/secrets/fec_request_pool')
        for name in ('collect-downstream-recover', 'collect-downstream-import'):
            self.assertEqual((self.services[name]['network_mode'], 'secrets' in self.services[name], 'networks' in self.services[name]), ('none', False, False), name)
        self.assertIn('/export', {m['target'] for m in self.services['collect-downstream']['volumes']})
        self.assertNotIn('/export', {m['target'] for m in self.services['collect-downstream-import']['volumes']})

    def test_the_container_environment_points_every_location_at_a_mount(self):
        for name, service in self.services.items():
            env = service['environment']
            self.assertEqual(env['LOCALAPPDATA'], '/localappdata')
            self.assertEqual(env['MENTAT_REPORT_DIRECTORY'], '/reports')           # named everywhere; mounted only where a job reads reports
            self.assertEqual({env[k] for k in ('GLASSBOX_EVENTS_DIR', 'ANCHORS_GLASSBOX_DIR', 'RF_GLASSBOX_DIR')}, {'/glassbox/events'})
            self.assertEqual(env['MENTAT_COMPUTE_RECEIPT_DIR'], '/localappdata/Mentat/compute/receipts')
            self.assertEqual(env['PYTHONDONTWRITEBYTECODE'], '1')
            targets = {m['target'] for m in service['volumes']}
            for needed in ('/mentat', '/glassbox/events', '/localappdata/Mentat/compute/receipts'):
                self.assertIn(needed, targets, (name, needed))
            self.assertEqual('/reports' in targets, name in ('anchors', 'refit-detect', 'refit-parity', 'refit'), name)
        refit = self.services['refit-parity']['environment']
        self.assertEqual(refit['RF_RUNTIME'], '/localappdata/Mentat/forecast/refit')
        for alias in ('LOO', 'CHE', 'ZIPC', 'RLIBX'):
            self.assertTrue(refit['RF_ROOT_' + alias].startswith('/inputs/'), alias)
        self.assertEqual(refit['RF_ROOT_SGSD'], '/mentat/sector_growth_study/data')


DOWNSTREAM = ('collect-downstream', 'collect-downstream-recover', 'collect-downstream-import')
EXPECTED_WRITABLE = {
    'collect-downstream': ['/localappdata/Mentat/compute/receipts', '/glassbox/events', '/frontier', '/export'],
    'collect-downstream-recover': ['/localappdata/Mentat/compute/receipts', '/glassbox/events', '/frontier', '/export'],
    'collect-downstream-import': ['/localappdata/Mentat/compute/receipts', '/glassbox/events', '/frontier'],
    'anchors': ['/localappdata/Mentat/compute/receipts', '/glassbox/events', '/mentat/mentat/state/giving-anchors'],
    'refit-detect': ['/localappdata/Mentat/compute/receipts', '/glassbox/events', '/localappdata/Mentat/forecast/refit'],
    'refit-parity': ['/localappdata/Mentat/compute/receipts', '/glassbox/events', '/localappdata/Mentat/forecast/refit'],
    'refit': ['/localappdata/Mentat/compute/receipts', '/glassbox/events', '/localappdata/Mentat/forecast/refit'],
    'pocket-build': ['/localappdata/Mentat/compute/receipts', '/glassbox/events', '/work/out'],
    'collect-fanout': ['/localappdata/Mentat/compute/receipts', '/glassbox/events', '/collect', '/mentat/mentat/.cache'],
    'pac-evidence': ['/localappdata/Mentat/compute/receipts', '/glassbox/events', '/evidence'],
}
for _writable in EXPECTED_WRITABLE.values():
    _writable.sort()


if __name__ == '__main__':
    unittest.main()

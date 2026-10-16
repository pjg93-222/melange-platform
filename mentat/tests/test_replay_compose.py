"""compute.replay.yaml (D-180), the second -f file for the container side B only: no network, read-only cassette / tls / http_replay mounts that Docker never
creates, the dummy keys secret, http_replay.py three levels deep, no new writable mount and not the live .cache; and the production run_job.ps1,
compute.compose.yaml and compute_job.py never mention the replay file or any proxy variable."""
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
SERVICES = ('collect-fanout', 'pac-evidence')
REPLAY_TARGETS = ('/replay/cassette', '/replay/tls', '/melange-replay/ops/compute/http_replay.py')
WRITABLE = {'collect-fanout': {'/localappdata/Mentat/compute/receipts', '/glassbox/events', '/collect', '/mentat/mentat/.cache'},
            'pac-evidence': {'/localappdata/Mentat/compute/receipts', '/glassbox/events', '/evidence'}}
RESET = object()
HOSTS = {'DIGEST': 'sha256:' + 'a' * 64, 'MENTAT_HOST': 'C:/m', 'LOCALAPPDATA_HOST': 'C:/l', 'GLASSBOX_HOST': 'C:/g', 'REPLAY_CASSETTE_HOST': 'C:/c', 'REPLAY_TLS_HOST': 'C:/t',
         'REPLAY_HTTP_REPLAY_HOST': 'C:/h/http_replay.py', 'REPLAY_KEYS_FILE': 'C:/k/dummy_keys', 'REPLAY_CACHE_HOST': 'C:/cache', 'EVIDENCE_HOST': 'C:/ev', 'COLLECT_HOST': 'C:/co'}


class Compose(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import yaml
        except ImportError:
            raise unittest.SkipTest('PyYAML not installed; compose policy not checked')

        class Loader(yaml.SafeLoader):
            pass
        Loader.add_constructor('!reset', lambda loader, node: RESET)
        cls.base = yaml.safe_load((ROOT / 'compute.compose.yaml').read_text(encoding='utf-8'))
        cls.doc = yaml.load((ROOT / 'compute.replay.yaml').read_text(encoding='utf-8'), Loader)
        cls.services = cls.doc['services']

    def mounts(self, name):
        return {m['target']: m for m in self.services[name]['volumes']}

    def test_only_the_two_collectors_are_overlaid_and_they_have_no_network_at_all(self):
        self.assertEqual(sorted(self.services), sorted(SERVICES))
        for name in SERVICES:
            self.assertEqual(self.services[name]['network_mode'], 'none', name)
            self.assertIs(self.services[name]['networks'], RESET, name)                    # the base's `networks: [collect]` is removed, not merged
            self.assertNotIn('ports', self.services[name])

    def test_the_cassette_tls_and_http_replay_are_read_only_and_never_created_by_docker(self):
        for name in SERVICES:
            mounts = self.mounts(name)
            for target in REPLAY_TARGETS:
                self.assertEqual((mounts[target]['type'], mounts[target]['read_only'], mounts[target]['bind']['create_host_path']), ('bind', True, False), (name, target))

    def test_http_replay_is_mounted_three_levels_deep_because_it_computes_parents_2_of_its_own_path(self):
        target = PurePosixPath(self.mounts('pac-evidence')['/melange-replay/ops/compute/http_replay.py']['target'])
        self.assertEqual(target.parts[-3:], ('ops', 'compute', 'http_replay.py'))
        self.assertNotEqual(str(target.parents[2]), '/')

    def test_the_keys_secret_is_the_dummy_file_and_the_real_one_is_not_named(self):
        self.assertEqual(self.doc['secrets']['fec_api_keys']['file'].split(':?')[0], '${REPLAY_KEYS_FILE')
        self.assertNotIn('FEC_KEYS_FILE', json.dumps(self.doc, default=str))
        self.assertNotIn('secrets', self.services['pac-evidence'])                           # the base's `secrets: [fec_api_keys]` stays; nothing else is added

    def test_the_entry_point_is_replay_proxy_in_front_of_compute_job(self):
        for name in SERVICES:
            entry = self.services[name]['entrypoint']
            self.assertEqual(entry[:3], ['python', '/mentat/mentat/tools/replay_proxy.py', 'replay'])
            self.assertEqual(entry[entry.index('--'):], ['--', 'python', '/mentat/mentat/tools/compute_job.py'])
            self.assertIn('--allow-unused', entry)
            self.assertTrue((ROOT / 'tools' / 'replay_proxy.py').is_file())

    def test_no_writable_mount_is_added_and_the_live_cache_is_replaced_by_scratch(self):
        for name in SERVICES:
            effective = {m['target']: m for m in self.base['services'][name]['volumes'] if isinstance(m, dict)}
            effective.update(self.mounts(name))
            self.assertEqual({t for t, m in effective.items() if not m.get('read_only')}, WRITABLE[name], name)
        cache = self.mounts('collect-fanout')['/mentat/mentat/.cache']
        self.assertIn('REPLAY_CACHE_HOST', cache['source'])
        self.assertNotIn('MENTAT_HOST', cache['source'])
        self.assertNotIn('/mentat/mentat/.cache', self.mounts('pac-evidence'))

    def test_production_never_mentions_the_replay_file_or_a_proxy_variable(self):
        banned = ('compute.replay', 'replay_proxy', 'replay_parity', 'http_replay', 'HTTPS_PROXY', 'HTTP_PROXY', 'ALL_PROXY', 'NO_PROXY', 'SSL_CERT_FILE', 'CURL_CA_BUNDLE',
                  'CURL_SSL_BACKEND', 'REPLAY_')
        for relative in ('compute.compose.yaml', 'tools/run_job.ps1', 'tools/compute_job.py'):
            text = (ROOT / relative).read_text(encoding='utf-8')
            for word in banned:
                self.assertNotIn(word.lower(), text.lower(), (relative, word))

    @unittest.skipUnless(shutil.which('docker'), 'no docker CLI')
    def test_docker_compose_merges_the_two_files_into_what_the_policy_says(self):
        env = {**os.environ, **HOSTS}
        done = subprocess.run(['docker', 'compose', '-f', str(ROOT / 'compute.compose.yaml'), '-f', str(ROOT / 'compute.replay.yaml'), 'config', '--format', 'json'],
                              capture_output=True, text=True, env=env)
        if 'unknown command' in done.stderr or 'is not a docker command' in done.stderr:
            self.skipTest('no docker compose plugin')
        self.assertEqual(done.returncode, 0, done.stderr)
        merged = json.loads(done.stdout)
        for name in SERVICES:
            service = merged['services'][name]
            self.assertEqual(service['network_mode'], 'none')
            self.assertNotIn('networks', service)
            targets = {v['target']: v for v in service['volumes']}
            self.assertEqual({t for t, v in targets.items() if not v.get('read_only')}, WRITABLE[name])
            self.assertEqual(targets['/replay/cassette']['source'], 'C:/c')
        cache = {v['target']: v for v in merged['services']['collect-fanout']['volumes']}['/mentat/mentat/.cache']
        self.assertEqual(cache['source'], 'C:/cache')                                      # the live mentat/.cache is not in the merged file
        self.assertTrue(merged['secrets']['fec_api_keys']['file'].endswith('dummy_keys'))


if __name__ == '__main__':
    unittest.main()

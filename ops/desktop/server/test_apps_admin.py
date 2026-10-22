import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import apps_admin as apps
from apps_admin import (ActionFailed, AppsAdmin, AppsConfig, BadRequest, Busy, DEFAULT_SERVICES, Docker, DockerUnavailable,
                        NotAllowed, NotSetUp, build_argv, redact)
import test_access_store as store_tests

PROJECT = 'melange-local'
ID = {'melange-desk': 'a' * 64, 'mentat-console': 'b' * 64, 'terrier': 'c' * 64,
      'desk-relay': '1' * 64, 'mentat-relay': '2' * 64, 'terrier-relay': '3' * 64}
PORT = {'desk-relay': 18080, 'mentat-relay': 18802, 'terrier-relay': 18766}
OLD, NEW = 'reg/img@sha256:' + '1' * 64, 'reg/img@sha256:' + '2' * 64
STAMP_CLOCK = 1800000000  # 2027-01-15T08:00:00Z
FAKE_DOCKER = os.path.join(tempfile.gettempdir(), 'docker.exe')   # absolute and .exe: what find_docker accepts, never run


# Secret- and PII-shaped fixtures are assembled at run time so this file passes the release security gate
# (gitleaks + data_leak) it ships through, the way ops/tests/test_security_gate.py builds its own.
def mail(local, domain='b.org'):
    return f'{local}@{domain}'


def pem(kind, *body, sep='\n'):
    """PEM armor around `body`: the label is joined from pieces so no armored block sits in the source."""
    label = (kind + ' ' if kind else '') + 'PRIVATE ' + 'KEY'
    return sep.join([f'-----BEGIN {label}-----', *body, f'-----END {label}-----'])


class FakeRunner:
    """Stands in for subprocess.run: scripted docker answers, every call recorded. Never starts a process."""

    def __init__(self, running=('melange-desk', 'mentat-console', 'terrier'), stopped=(), outdated=('mentat-console',), fail=()):
        self.calls, self.running, self.stopped, self.outdated, self.fail = [], set(running), set(stopped), set(outdated), set(fail)
        self.gate = self.entered = None   # block `restart terrier` until gate is set
        self.invite_output = 'http://127.0.0.1:18766/?invite=TESTTOKEN\n'
        self.invite_error = ''
        self.logs_gate = self.logs_entered = None   # block `logs` until logs_gate is set
        self.partial = None   # stderr text: `docker inspect` then exits 1 while still printing what it found
        self.labels, self.images = {}, {}   # extra image labels / the image a container was created from, per service
        self.volumes = [{'Name': PROJECT + '_terrier-state', 'Size': '299.4MB'}, {'Name': 'other', 'Size': '1GB'}]
        self.timeout = set()   # verbs that run out of time (subprocess.TimeoutExpired)
        self.ps_gate = self.ps_entered = None   # block `ps` until ps_gate is set
        self.ports = {r: {f'{p}/tcp': [{'HostIp': '127.0.0.1', 'HostPort': str(p)}]} for r, p in PORT.items()}   # NetworkSettings.Ports per relay
        self.extra = []   # more containers `ps` lists: (id, inspect document)
        self.env_files = []   # env_file entries of the terrier service in `compose config`

    def ps(self):
        return '\n'.join([ID[n] for n in sorted(self.running | self.stopped)] + [ID[r] for r in PORT] + [i for i, _ in self.extra]) + '\n'

    def inspect(self, ids):
        out = [doc for i, doc in self.extra if i in ids]
        for relay in PORT:
            if ID[relay] in ids:
                out.append({'Id': ID[relay], 'State': {'Status': 'running', 'ExitCode': 0, 'StartedAt': '2027-01-15T03:00:00Z', 'FinishedAt': '0001-01-01T00:00:00Z'},
                            'Config': {'Image': NEW, 'Labels': {'com.docker.compose.project': PROJECT, 'com.docker.compose.service': relay}},
                            'NetworkSettings': {'Ports': self.ports[relay]}, 'Mounts': []})
        for service in sorted(self.running | self.stopped):
            if ID[service] not in ids:
                continue
            up = service in self.running
            out.append({'Id': ID[service], 'State': {'Status': 'running' if up else 'exited', 'ExitCode': 0 if up else 1,
                                                    'StartedAt': '2027-01-15T03:02:00.5Z', 'FinishedAt': '0001-01-01T00:00:00Z' if up else '2027-01-15T07:12:00Z',
                                                    **({'Health': {'Status': 'healthy', 'Log': [{'End': '2027-01-15T07:59:00.1Z'}]}} if up else {})},
                        'Config': {'Image': self.images.get(service) or (OLD if service in self.outdated else NEW), 'Labels': {
                            'com.docker.compose.project': PROJECT, 'com.docker.compose.service': service,
                            **({'org.opencontainers.image.version': '2026.10.17.1'} if service != 'terrier' else {}),
                            **self.labels.get(service, {})}},
                        'Mounts': [{'Type': 'volume', 'Name': PROJECT + '_terrier-state', 'Destination': '/state', 'RW': True},
                                   {'Type': 'bind', 'Destination': '/data', 'RW': False}] if service == 'terrier' else []})
        return json.dumps(out)

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        rest = argv[1:]
        sub = rest[5:] if rest[0] == 'compose' else rest
        verb = sub[0]
        ok = lambda out='', err='': SimpleNamespace(returncode=0, stdout=out.encode(), stderr=err.encode())
        if verb in self.timeout:
            raise subprocess.TimeoutExpired(argv, kwargs.get('timeout'))
        if verb in self.fail or (verb == 'exec' and 'exec' in self.fail):
            return SimpleNamespace(returncode=1, stdout=b'', stderr=b'boom: password=hunter2 failed\n')
        if verb == 'ps':
            if self.ps_gate is not None:
                self.ps_entered.set()
                self.ps_gate.wait(5)
            return ok(self.ps())
        if verb == 'inspect':
            if self.partial is not None:
                return SimpleNamespace(returncode=1, stdout=self.inspect(sub[1:]).encode(), stderr=self.partial.encode())
            return ok(self.inspect(sub[1:]))
        if verb == 'config':
            services = {n: {'image': NEW} for n in ID}
            if self.env_files:
                services['terrier']['env_file'] = self.env_files
            return ok(json.dumps({'name': PROJECT, 'services': services}))
        if verb == 'system':
            return ok(json.dumps({'Volumes': self.volumes}))
        if verb == 'image':
            return ok(json.dumps([{'Config': {'Labels': {'org.opencontainers.image.version': '2026.10.18.1'}}}]))
        if verb == 'logs':
            if self.logs_gate is not None:
                self.logs_entered.set()
                self.logs_gate.wait(5)
            return ok('2027-01-15T09:41:02.123456789Z INFO Server started on port 8080\n'
                      '2027-01-15T09:41:07.000000001Z Authorization: Bearer abcdef123456\n'
                      '2027-01-15T09:42:30.000000000Z WARN CT-01 district data is older than 24 hours\n'
                      '2027-01-15T09:44:50.000000000Z ERROR Health check timed out after 5 s\n'
                      '2027-01-15T09:44:51.000000000Z SECRET_KEY=hunter2\n')
        if verb == 'restart' and self.gate is not None and sub[-1] == 'terrier':
            self.entered.set()
            self.gate.wait(5)
        if verb == 'exec' and 'invite' in sub:
            return SimpleNamespace(returncode=1 if self.invite_error else 0, stdout=self.invite_output.encode(), stderr=self.invite_error.encode())
        return ok()


def make_config(directory):
    compose = Path(directory) / 'compose.local.yaml'
    compose.write_text('name: melange-local\n')
    return AppsConfig(PROJECT, str(compose), DEFAULT_SERVICES)


def make_apps(directory, audit, runner=None, spawn=lambda fn: fn()):
    config = make_config(directory)
    runner = runner or FakeRunner()
    docker = Docker(config, which=lambda name: FAKE_DOCKER, run=runner, environ={'PATH': 'p', 'SECRET_KEY': 'must-not-pass'})
    return AppsAdmin(config, audit, docker, clock=lambda: STAMP_CLOCK, spawn=spawn, fetch_version=lambda port: None), runner


class AppsAdminTests(unittest.TestCase):
    def setUp(self):
        store_tests.AccessStoreTests.setUp(self)
        self.apps, self.runner = make_apps(self.temp.name, self.store)

    def entries(self):
        return self.store.app_actions()

    def test_argv_builder_allows_only_templates_and_known_services(self):
        config = make_config(self.temp.name)
        c = ['compose', '-p', PROJECT, '-f', config.compose_file]
        self.assertEqual(build_argv(config, 'ps'), ['ps', '-a', '-q', '--no-trunc', '--filter', 'label=com.docker.compose.project=' + PROJECT])
        self.assertEqual(build_argv(config, 'restart', service='terrier'), c + ['restart', 'terrier'])
        self.assertEqual(build_argv(config, 'start', service='terrier'), c + ['start', 'terrier'])
        self.assertEqual(build_argv(config, 'up', service='melange-desk'), c + ['up', '-d', 'melange-desk'])
        self.assertEqual(build_argv(config, 'pull', service='mentat-console'), c + ['pull', 'mentat-console'])
        self.assertEqual(build_argv(config, 'logs', container='c' * 64), ['logs', '--tail', '500', '--timestamps', 'c' * 64])
        self.assertEqual(build_argv(config, 'df'), ['system', 'df', '-v', '--format', 'json'])
        self.assertEqual(build_argv(config, 'backup', terrier='terrier', stamp='20270115T080000Z'),
                         c + ['exec', '-T', 'terrier', 'python', 'manage.py', 'backup', '/state/backups/accounts-20270115T080000Z.sqlite3'])
        self.assertEqual(build_argv(config, 'invite', terrier='terrier', email=mail('a'), role='canvasser', origin='http://127.0.0.1:18766'),
                         c + ['exec', '-T', 'terrier', 'python', 'manage.py', 'invite', '--field-staff', '--origin', 'http://127.0.0.1:18766', '--', mail('a')])
        refused = [('run', {}), ('exec', {'service': 'terrier'}), ('ps', {'service': 'terrier'}), ('restart', {}),
                   ('ps', {'filter': 'status=exited'}), ('ps', {'project': 'other'}), ('ps', {'ids': ['a' * 64]}), ('ps', {'all': True}),
                   ('restart', {'service': 'terrier', 'extra': 'x'}), ('restart', {'service': 'other'}), ('restart', {'service': ''}),
                   ('restart', {'service': 'terrier; rm -rf /'}), ('restart', {'service': '../terrier'}), ('restart', {'service': None}),
                   ('restart', {'service': ['terrier']}), ('up', {'service': 'terrier-relay'}),
                   ('invite', {'terrier': 'melange-desk', 'email': mail('a'), 'role': 'canvasser', 'origin': 'http://127.0.0.1:1234'}),
                   ('backup', {'terrier': 'mentat-console', 'stamp': '20270115T080000Z'}),
                   ('backup', {'terrier': 'terrier', 'stamp': '../../etc'}),
                   ('invite', {'terrier': 'terrier', 'email': mail('-x'), 'role': 'canvasser', 'origin': 'http://127.0.0.1:1234'}),
                   ('invite', {'terrier': 'terrier', 'email': mail('a b'), 'role': 'canvasser', 'origin': 'http://127.0.0.1:1234'}),
                   ('invite', {'terrier': 'terrier', 'email': 'a' * 150 + '@b.org', 'role': 'canvasser', 'origin': 'http://127.0.0.1:1234'}),
                   ('invite', {'terrier': 'terrier', 'email': mail('a'), 'role': 'root', 'origin': 'http://127.0.0.1:1234'}),
                   ('invite', {'terrier': 'terrier', 'email': mail('a'), 'role': 'canvasser', 'origin': 'http://evil.example:1234'}),
                   ('logs', {'container': 'terrier'}), ('logs', {'container': '--all'}), ('inspect', {'ids': []}),
                   ('inspect', {'ids': ['c' * 64, '-q']}), ('image_inspect', {'ref': '--format={{.Id}}'}),
                   ('image_inspect', {'ref': 'reg/img;rm'})]
        for kind, params in refused:
            with self.subTest(kind=kind, params=params), self.assertRaises(NotAllowed):
                build_argv(config, kind, **params)

    def test_docker_runs_fixed_argv_without_shell_and_with_minimal_environment(self):
        self.apps.docker.run('restart', 7, service='terrier')
        argv, kwargs = self.runner.calls[-1]
        self.assertEqual(argv[:2], [FAKE_DOCKER, 'compose'])
        self.assertIs(kwargs['shell'], False)
        self.assertEqual(kwargs['timeout'], 7)
        self.assertNotIn('SECRET_KEY', kwargs['env'])
        self.assertEqual(kwargs['env']['COMPOSE_PROFILES'], 'desk,mentat,terrier')
        before = len(self.runner.calls)
        with self.assertRaises(NotAllowed):
            self.apps.docker.run('restart', 7, service='x')
        with self.assertRaises(NotAllowed):
            self.apps.docker.run('shell', 7)
        self.assertEqual(len(self.runner.calls), before)
        with mock.patch.dict(os.environ, {'LOCALAPPDATA': self.temp.name, 'ProgramFiles': self.temp.name}):
            missing = Docker(make_config(self.temp.name), which=lambda name: None, run=self.runner)
            with self.assertRaises(DockerUnavailable):
                missing.run('ps', 5)
            self.assertIsNone(missing.exe)
            missing.which = lambda name: FAKE_DOCKER       # Docker Desktop turned up later: no restart needed
            missing.run('ps', 5)
            self.assertEqual(missing.exe, FAKE_DOCKER)

    def test_docker_is_never_taken_from_the_current_directory_or_a_non_exe(self):
        name = 'docker.exe' if os.name == 'nt' else 'docker'
        with tempfile.TemporaryDirectory() as work, tempfile.TemporaryDirectory() as empty, \
                mock.patch.dict(os.environ, {'PATH': empty, 'LOCALAPPDATA': empty, 'ProgramFiles': empty}):
            (Path(work) / name).write_bytes(b'')
            os.chmod(Path(work) / name, 0o755)
            before = os.getcwd()
            os.chdir(work)
            try:
                self.assertIsNone(apps.find_docker())                                # real shutil.which, fake docker beside us
                self.assertIsNone(apps.find_docker(lambda n: name))                  # a relative answer is refused
                if os.name == 'nt':
                    self.assertIsNone(apps.find_docker(lambda n: str(Path(work) / 'docker.cmd')))   # so is a shim
                    self.assertEqual(os.environ['NoDefaultCurrentDirectoryInExePath'], '1')
            finally:
                os.chdir(before)
            if os.name == 'nt':   # the two Docker Desktop folders are the fallback when PATH has no docker
                for variable, rest in apps.DOCKER_DESKTOP:
                    with mock.patch.dict(os.environ, {variable: work}):
                        target = Path(work) / rest
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(b'')
                        self.assertEqual(apps.find_docker(lambda n: None), str(target))
                        target.unlink()

    def test_timeout_kills_the_child_and_its_grandchild(self):
        def alive(pid):
            if os.name != 'nt':
                try:
                    os.kill(pid, 0)
                    return True
                except OSError:
                    return False
            import ctypes
            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
            if not handle:
                return False
            code = ctypes.c_ulong()
            ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            ctypes.windll.kernel32.CloseHandle(handle)
            return code.value == 259   # STILL_ACTIVE

        with tempfile.TemporaryDirectory() as folder:
            pidfile = Path(folder) / 'pid'
            # The grandchild sleeps 3 s and holds no pipe of ours, so only a tree kill (not child.kill) can stop it early.
            grandchild = f'import os, pathlib, time; pathlib.Path({str(pidfile)!r}).write_text(str(os.getpid())); time.sleep(3)'
            child = ('import subprocess, sys, time; subprocess.Popen([sys.executable, "-c", ' + repr(grandchild) + '], stdin=subprocess.DEVNULL, '
                     'stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); time.sleep(3)')
            with self.assertRaises(subprocess.TimeoutExpired):
                apps.run_process([sys.executable, '-c', child], timeout=1.0, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if not pidfile.exists():
                self.skipTest('the grandchild had not started when the timeout hit')
            pid = int(pidfile.read_text())
            self.addCleanup(lambda: alive(pid) and subprocess.run(['taskkill', '/F', '/PID', str(pid)] if os.name == 'nt' else ['kill', '-9', str(pid)],
                                                                    capture_output=True))
            self.assertFalse(alive(pid))
        done = apps.run_process([sys.executable, '-c', 'print("ok")'], timeout=20, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual((done.returncode, done.stdout.strip()), (0, b'ok'))

    def test_redaction_masks_canaries_and_secret_shapes(self):
        canaries = Path(apps.OPS / 'security' / 'canaries.txt')
        if canaries.exists():
            tokens = [t.strip() for t in canaries.read_text(encoding='utf-8').splitlines() if t.strip() and not t.startswith('#')]
            self.assertTrue(tokens)
            for token in tokens:
                self.assertNotIn(token, redact(f'start {token} end'))
                self.assertNotIn(token, redact(f'k={token}'))
        cases = {'Authorization: Bearer abc.def-123': 'Authorization: Bearer [redacted]',
                 'Authorization: Token abc123def': 'Authorization: Token [redacted]',
                 'Proxy-Authorization: Digest username="u", realm="r", response="abc"': 'Proxy-Authorization: Digest [redacted]',
                 '{"Authorization": "Token abc", "n": 1}': '{"Authorization": "Token [redacted]", "n": 1}',
                 'SECRET_KEY=hunter2': 'SECRET_KEY=[redacted]',
                 'password: "correct horse"': 'password: "[redacted]"',
                 'CU_SECRET_KEY: abc123': 'CU_SECRET_KEY: [redacted]',
                 'ACCESS_AUD=abc123 ok': 'ACCESS_AUD=[redacted] ok',
                 '{"token":"abc","n":1}': '{"token":"[redacted]","n":1}',
                 '{"password": "a\\"b c", "n": 1}': '{"password": "[redacted]", "n": 1}',
                 "api_key='k k'": "api_key='[redacted]'",
                 'x-api-key: abc': 'x-api-key: [redacted]',
                 'apiKey=abc apikey=def accessToken: ghi': 'apiKey=[redacted] apikey=[redacted] accessToken: [redacted]',
                 'invite=A1 session: B2 sessionid=C3 credential=D4 credentials=E5 pwd=F6 signature=G7':
                     'invite=[redacted] session: [redacted] sessionid=[redacted] credential=[redacted] credentials=[redacted] pwd=[redacted] signature=[redacted]',
                 'monkey: banana turkey=1 donkey=2 keyboard=us Monkey: 5': 'monkey: banana turkey=1 donkey=2 keyboard=us Monkey: 5',
                 'Cookie: sessionid=abc; csrftoken=xyz': 'Cookie: sessionid=[redacted]; csrftoken=[redacted]',
                 'Cookie: csrftoken=xyz; sessionid=abc': 'Cookie: csrftoken=[redacted]; sessionid=[redacted]',
                 'Cookie: foo=bar; other=xyz': 'Cookie: foo=[redacted]; other=[redacted]',
                 'Set-Cookie: sid=abc; Path=/; HttpOnly; Secure': 'Set-Cookie: sid=[redacted]; Path=/; HttpOnly; Secure',
                 '"cookie": "a=b; c=d"': '"cookie": "[redacted]"',
                 'postgres://user:pw@db/x': 'postgres://user:[redacted]@db/x',
                 'redis://:pw@h:6379/0': 'redis://:[redacted]@h:6379/0',
                 'redis://:p/w@ss@host:6379/0': 'redis://:[redacted]@host:6379/0',
                 pem('RSA', 'MIIEabc'): '[redacted]',
                 'KEY="' + pem('', 'MIIE', sep='\\n') + '"': 'KEY="[redacted]"',
                 'GET /api/health 200 3 ms': 'GET /api/health 200 3 ms',
                 'CT-01 district data is older than 24 hours': 'CT-01 district data is older than 24 hours',
                 'ssn ' + '-'.join(['123', '45', '6789']) + ' end': 'ssn [redacted] end',
                 'call (555) 123-4567 now': 'call [redacted] now',
                 'user ' + mail('person', 'realdomain.com') + ' signed in': 'user [redacted] signed in',
                 'user person@example.org signed in': 'user person@example.org signed in'}
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(redact(raw), expected)
                self.assertEqual(redact(redact(raw)), expected)
        jwt = '.'.join(['eyJhbGciOiJIUzI1NiJ9', 'eyJzdWIiOiIxMjM0NTYifQ', 'SflKxwRJSMeKKF2QT4fwpM'])
        self.assertNotIn('eyJ', redact('got ' + jwt))

    def test_redaction_of_long_separator_lines_is_linear(self):
        uuid_like = '-'.join('0123abcd-4567-89ef-0123-456789abcdef' for _ in range(110))
        for name, line in (('dots', 'a.' * 2000), ('dashes', 'a-' * 2000), ('uuid', uuid_like[:4000]), ('urls', 'a://' * 1000),
                           ('keys', 'key=' * 1000), ('blanks', 'key' + ' ' * 3990), ('cookie', 'Cookie: ' + 'a' * 3990)):
            best = 1.0
            for _ in range(3):   # the best of three keeps a stray GC pause from failing the bound
                started = time.perf_counter()
                redact(line)
                best = min(best, time.perf_counter() - started)
            with self.subTest(line=name):
                self.assertLess(best, 0.05)

    def test_log_key_blocks_are_redacted_across_lines_and_windows(self):
        stamp = '2027-01-15T09:41:0%dZ '
        block = pem('OPENSSH', 'b3BlbnNzaC1rZXktdjEAAAAA', 'AAAAB3NzaC1yc2E').split('\n')
        whole = '\n'.join(stamp % n + body for n, body in enumerate(['INFO start'] + block + ['INFO after']))
        self.assertEqual([l['text'] for l in apps.parse_logs(whole)], ['start'] + ['[redacted]'] * 4 + ['after'])
        window = '\n'.join(stamp % n + body for n, body in enumerate(block[1:] + ['INFO after']))   # starts inside the block
        self.assertEqual([l['text'] for l in apps.parse_logs(window)], ['[redacted]'] * 3 + ['after'])
        unfinished = '\n'.join(stamp % n + body for n, body in enumerate(block[:2] + ['INFO still secret']))
        self.assertEqual([l['text'] for l in apps.parse_logs(unfinished)], ['[redacted]'] * 3)

    def test_failure_reason_drops_repeats_and_ends_on_a_word(self):
        Result = apps.Result
        self.assertEqual(apps.tail(Result(1, '', 'Error: first\nError: boom\nError: boom\nError: boom\n')), 'Error: first Error: boom')
        self.assertEqual(apps.tail(Result(1, '', '')), 'docker exited with status 1')
        long = apps.tail(Result(1, '', 'word ' * 200))
        self.assertLessEqual(len(long), 300)
        self.assertTrue(long.endswith('word…'))
        self.assertEqual(apps.tail(Result(1, '', 'x' * 400)), 'x' * 299 + '…')

    def test_redaction_patterns_match_the_security_gate(self):
        spec = importlib.util.spec_from_file_location('security_gate_for_test', apps.OPS / 'security_gate.py')
        if not spec or not spec.loader or not (apps.OPS / 'security_gate.py').exists():
            self.skipTest('security_gate.py is not beside this module')
        gate = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gate)
        for name in ('SSN_RE', 'EMAIL_RE', 'EMAIL_OK', 'PHONE_RE'):
            self.assertEqual(getattr(apps, name).pattern, getattr(gate, name).pattern, name)
            self.assertEqual(getattr(apps, name).flags, getattr(gate, name).flags, name)

    def test_overview_states_actions_versions_and_disk(self):
        self.store.audit_app_action('admin', 'terrier', 'backup', 'succeeded')
        view = self.apps.overview()
        self.assertTrue(view['docker']['ok'])
        self.assertIsNone(view['notice'])
        rows = {r['service']: r for r in view['services']}
        self.assertEqual([r['name'] for r in view['services']], ['Melange desk', 'Mentat console', 'Terrier'])
        desk, mentat, terrier = rows['melange-desk'], rows['mentat-console'], rows['terrier']
        self.assertEqual((desk['state'], desk['version'], desk['openUrl']), ('running', 'v2026.10.17.1', 'http://127.0.0.1:18080'))
        self.assertEqual(desk['actions'], ['open', 'restart'])
        self.assertEqual((mentat['state'], mentat['readyVersion']), ('update_ready', 'v2026.10.18.1'))
        self.assertEqual(mentat['actions'], ['update', 'open', 'restart'])
        self.assertEqual((terrier['state'], terrier['disk'], terrier['backup']), ('running', 299400000, 'supported'))
        self.assertEqual(terrier['lastBackup'], STAMP_CLOCK)
        self.assertEqual(desk['backup'], 'not_set_up')
        self.assertIsNone(desk['lastBackup'])
        self.assertEqual(view['activity'][0]['name'], 'Terrier')
        self.assertNotIn('c' * 64, json.dumps(view))   # container ids stay on the server

    def test_a_vanished_container_does_not_hide_the_others(self):
        partial, runner = make_apps(self.temp.name, self.store, FakeRunner(running=('melange-desk', 'terrier')))
        runner.partial = 'Error: No such object: ' + ID['mentat-console']
        runner.running.add('mentat-console')             # still in `ps`, gone by the time `inspect` runs
        original = runner.inspect
        runner.inspect = lambda ids: original([i for i in ids if i != ID['mentat-console']])
        view = partial.overview()
        self.assertTrue(view['docker']['ok'])
        self.assertEqual({r['service']: r['state'] for r in view['services']}, {'melange-desk': 'running', 'mentat-console': 'missing', 'terrier': 'running'})
        cold, cold_runner = make_apps(self.temp.name, self.store)      # no list read yet, so nothing to fall back on
        cold_runner.inspect = lambda ids: '[]'           # nothing found and the daemon is the reason: that is not "all missing"
        cold_runner.partial = 'error during connect: the docker daemon is not running'
        self.assertFalse(cold.overview()['docker']['ok'])
        cold_runner.inspect = lambda ids: 'not json'
        self.assertFalse(cold.overview()['docker']['ok'])
        runner.inspect = lambda ids: '[]'                # the same failures once a list has been read: it is kept, marked stale
        runner.partial = 'error during connect: the docker daemon is not running'
        again = partial.overview()
        self.assertEqual((again['stale'], again['docker']['ok']), (True, True))
        runner.inspect = lambda ids: 'not json'
        self.assertTrue(partial.overview()['stale'])

    def test_docker_down_still_reports_running_actions_so_the_page_keeps_polling(self):
        self.apps.busy['terrier'] = {'action': 'update', 'since': STAMP_CLOCK - 5}
        self.apps.last['mentat-console'] = {'action': 'restart', 'outcome': 'failed', 'at': STAMP_CLOCK - 60, 'message': 'boom'}
        with mock.patch.dict(os.environ, {'LOCALAPPDATA': self.temp.name, 'ProgramFiles': self.temp.name}):
            self.apps.docker.exe, self.apps.docker.which = None, lambda name: None
            view = self.apps.overview()
        self.assertFalse(view['docker']['ok'])
        rows = {r['service']: r for r in view['services']}
        self.assertEqual(rows['terrier']['busy'], {'action': 'update', 'since': STAMP_CLOCK - 5})
        self.assertEqual(rows['mentat-console']['last']['outcome'], 'failed')
        self.assertEqual({r['state'] for r in rows.values()}, {'unknown'})

    def test_update_ready_compares_digests_when_both_references_have_one(self):
        digest = '3' * 64
        same, runner = make_apps(self.temp.name, self.store, FakeRunner(outdated=()))
        runner.images = {'terrier': 'registry-1.example.com/other/spelling@sha256:' + NEW.rpartition(':')[2]}   # same digest, other spelling
        runner.images['melange-desk'] = 'reg/img@sha256:' + digest                                               # a different digest
        rows = {r['service']: r for r in same.overview()['services']}
        self.assertEqual((rows['terrier']['update'], rows['melange-desk']['update']), (False, True))
        tagged, runner = make_apps(self.temp.name, self.store, FakeRunner(outdated=()))
        runner.images = {'terrier': 'reg/img:1', 'melange-desk': NEW}   # no digest on one side: the whole strings are compared
        rows = {r['service']: r for r in tagged.overview()['services']}
        self.assertEqual((rows['terrier']['update'], rows['melange-desk']['update']), (True, False))

    def test_version_comes_from_the_version_label_then_the_revision_label_then_one_cached_probe(self):
        probes = []
        runner = FakeRunner()
        runner.labels = {'terrier': {'org.opencontainers.image.revision': '0123456789ABCDEF0123456789abcdef01234567'}}
        runner.volumes = []
        by_revision, _ = make_apps(self.temp.name, self.store, runner)
        by_revision.fetch_version = lambda port: probes.append(port)
        self.assertEqual({r['service']: r['version'] for r in by_revision.overview()['services']}['terrier'], 'build 0123456')
        self.assertEqual(probes, [])                              # a label is enough: no /version request
        now = [STAMP_CLOCK]
        no_label = FakeRunner()
        probing, _ = make_apps(self.temp.name, self.store, no_label)
        probing.clock = lambda: now[0]
        probing.fetch_version = lambda port: probes.append(port)  # 404 / timeout: None
        for _ in range(3):
            self.assertIsNone({r['service']: r for r in probing.overview()['services']}['terrier']['version'])
        self.assertEqual(probes, [18766])                         # polls do not ask again within ten minutes
        now[0] += 601
        probing.overview()
        self.assertEqual(probes, [18766, 18766])
        probing.fetch_version = lambda port: {'git_sha': 'abcdef123456'}
        now[0] += 601
        self.assertEqual({r['service']: r for r in probing.overview()['services']}['terrier']['version'], 'build abcdef1')
        probing.fetch_version = lambda port: self.fail('a found version is kept for the life of the container')
        now[0] += 100000
        self.assertEqual({r['service']: r for r in probing.overview()['services']}['terrier']['version'], 'build abcdef1')

    def test_a_volume_without_a_size_is_unknown_not_zero(self):
        runner = FakeRunner()
        runner.volumes = [{'Name': PROJECT + '_terrier-state', 'Size': 'N/A'}, {'Name': 'other', 'UsageData': {'Size': -1}}]
        unknown, _ = make_apps(self.temp.name, self.store, runner)
        row = {r['service']: r for r in unknown.overview()['services']}['terrier']
        self.assertIsNone(row['disk'])
        self.assertEqual(unknown.handle('/apps/detail', {'service': 'terrier'})['storage'][0]['bytes'], None)
        self.assertEqual(apps.volume_sizes(json.dumps({'Volumes': [{'Name': 'a'}, {'Name': 'b', 'Size': 'N/A'}, {'Name': 'c', 'Size': '0B'}]})), {'c': 0})

    def test_last_backup_is_a_direct_query_not_the_last_200_actions(self):
        self.store.audit_app_action('admin', 'terrier', 'backup', 'succeeded')
        self.store.audit_app_action('admin', 'terrier', 'backup', 'failed')
        for _ in range(210):
            self.store.audit_app_action('admin', 'melange-desk', 'restart', 'succeeded')
        self.assertEqual({r['service']: r for r in self.apps.overview()['services']}['terrier']['lastBackup'], STAMP_CLOCK)
        self.assertIsNone(self.store.last_app_action('terrier', 'backup', 'unknown'))
        self.assertIsNone(self.store.last_app_action('terrier', 'backup*', 'succeeded'))

    def test_stopped_missing_and_unavailable(self):
        apps_stopped, _ = make_apps(self.temp.name, self.store, FakeRunner(running=('melange-desk', 'mentat-console'), stopped=('terrier',), outdated=('terrier',)))
        terrier = {r['service']: r for r in apps_stopped.overview()['services']}['terrier']
        # Stopped and Update ready both; Update is offered, Start stays a plain start.
        self.assertEqual((terrier['state'], terrier['update'], terrier['actions'], terrier['since']),
                         ('stopped', True, ['update', 'start', 'logs'], STAMP_CLOCK - 48 * 60))
        self.assertEqual(terrier['readyVersion'], 'v2026.10.18.1')
        current, _ = make_apps(self.temp.name, self.store, FakeRunner(running=('melange-desk', 'mentat-console'), stopped=('terrier',), outdated=()))
        row = {r['service']: r for r in current.overview()['services']}['terrier']
        self.assertEqual((row['state'], row['update'], row['actions']), ('stopped', False, ['start', 'logs']))
        apps_none, _ = make_apps(self.temp.name, self.store, FakeRunner(running=('melange-desk',)))
        row = {r['service']: r for r in apps_none.overview()['services']}['terrier']
        self.assertEqual((row['state'], row['actions']), ('missing', []))
        with self.assertRaises(NotSetUp):
            apps_none.handle('/apps/action', {'service': 'terrier', 'action': 'restart'})
        no_file = AppsAdmin(AppsConfig(PROJECT, str(Path(self.temp.name) / 'absent.yaml'), DEFAULT_SERVICES), self.store, self.apps.docker, spawn=lambda fn: fn())
        view = no_file.overview()
        self.assertTrue(all(r['state'] == 'missing' for r in view['services']))
        self.assertIn('not found', view['notice'])
        with mock.patch.dict(os.environ, {'LOCALAPPDATA': self.temp.name, 'ProgramFiles': self.temp.name}):   # no real Docker Desktop folder
            down = AppsAdmin(self.apps.config, self.store, Docker(self.apps.config, which=lambda n: None, run=self.runner))
            self.assertEqual(down.overview()['docker']['ok'], False)

    def test_one_action_writes_exactly_one_activity_entry(self):
        self.assertEqual(self.entries(), [])
        self.assertEqual(self.apps.handle('/apps/action', {'service': 'terrier', 'action': 'restart'}), {'ok': True, 'accepted': True})
        self.assertEqual([(e['service'], e['action'], e['outcome'], e['actor']) for e in self.entries()], [('terrier', 'restart', 'succeeded', 'admin')])
        self.assertEqual(self.apps.busy, {})
        self.apps.handle('/apps/action', {'service': 'mentat-console', 'action': 'update'})
        self.assertEqual(len(self.entries()), 2)
        verbs = [c[0][1:][5:][0] for c in self.runner.calls if c[0][1] == 'compose' and c[0][6] in ('pull', 'up')]
        self.assertEqual(verbs, ['pull', 'up'])
        self.apps.handle('/apps/action', {'service': 'terrier', 'action': 'backup'})
        backup = [c[0] for c in self.runner.calls if 'backup' in c[0]][-1]
        self.assertEqual(backup[-1], '/state/backups/accounts-20270115T080000Z.sqlite3')
        self.assertEqual(len(self.entries()), 3)
        self.assertEqual(self.apps.overview()['activity'][0]['action'], 'backup')

    def test_start_only_starts_and_update_is_the_only_way_to_a_new_release(self):
        stopped, runner = make_apps(self.temp.name, self.store, FakeRunner(running=('melange-desk', 'mentat-console'), stopped=('terrier',), outdated=('terrier',)))
        verbs = lambda: [c[0][6] for c in runner.calls if c[0][1] == 'compose' and c[0][6] in ('start', 'up', 'pull', 'restart')]
        stopped.handle('/apps/action', {'service': 'terrier', 'action': 'start'})
        self.assertEqual(verbs(), ['start'])                    # no pull, no up -d (which would recreate it from the new pin)
        stopped.handle('/apps/action', {'service': 'terrier', 'action': 'update'})
        self.assertEqual(verbs(), ['start', 'pull', 'up'])
        self.assertEqual([(e['action'], e['outcome']) for e in reversed(self.entries())], [('start', 'succeeded'), ('update', 'succeeded')])

    def test_an_action_does_not_read_docker_in_the_request(self):
        # `docker ps` has taken 1.9 to 32 s on a busy machine; the page gives up on a request at 12 s. The request only checks the
        # service against the list that offered the button, and the spawned step does the work.
        stopped, runner = make_apps(self.temp.name, self.store, FakeRunner(running=('mentat-console', 'terrier'), stopped=('melange-desk',)))
        verbs = lambda: [c[0][6] if c[0][1] == 'compose' else c[0][1] for c in runner.calls]
        self.assertEqual({r['service']: r['actions'] for r in stopped.overview()['services']}['melange-desk'], ['start', 'logs'])
        runner.timeout = {'ps'}                                 # docker ps now runs out of time
        reads = verbs().count('ps')
        self.assertEqual(stopped.handle('/apps/action', {'service': 'melange-desk', 'action': 'start'}), {'ok': True, 'accepted': True})
        self.assertEqual(verbs().count('ps'), reads)           # no read in the request
        self.assertEqual((verbs()[-1], runner.calls[-1][0][-1]), ('start', 'melange-desk'))
        self.assertEqual([(e['service'], e['action'], e['outcome']) for e in self.entries()], [('melange-desk', 'start', 'succeeded')])

    def test_an_action_before_any_list_is_read_is_accepted_and_its_failure_reaches_the_log_and_the_row(self):
        for name, setup, message in (('compose start fails', lambda r: r.fail.add('start'), '[redacted]'),
                                     ('compose start runs out of time', lambda r: r.timeout.add('start'), 'Docker took too long to answer.')):
            with self.subTest(name):
                runner = FakeRunner(running=('mentat-console', 'terrier'), stopped=('melange-desk',))
                setup(runner)
                cold, _ = make_apps(self.temp.name, self.store, runner)
                self.assertIsNone(cold.kept())
                self.assertEqual(cold.handle('/apps/action', {'service': 'melange-desk', 'action': 'start'}), {'ok': True, 'accepted': True})
                self.assertFalse([c for c in runner.calls if c[0][1] == 'ps'])      # accepted without reading docker
                self.assertEqual(self.entries()[0]['outcome'], 'failed')
                self.assertEqual(cold.busy, {})
                row = {r['service']: r for r in cold.overview()['services']}['melange-desk']
                self.assertEqual((row['last']['action'], row['last']['outcome']), ('start', 'failed'))
                self.assertIn(message, row['last']['message'])
                self.assertIn('start', row['actions'])                              # the button is back, so the page can try again

    def test_docker_down_offers_to_start_docker_desktop_only_when_it_can(self):
        daemon_down = lambda argv, **kwargs: SimpleNamespace(returncode=1, stdout=b'', stderr=b'error during connect: open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified.')
        with tempfile.TemporaryDirectory() as local, tempfile.TemporaryDirectory() as machine, \
                mock.patch.dict(os.environ, {'LOCALAPPDATA': local, 'ProgramFiles': machine}):
            down, _ = make_apps(self.temp.name, self.store, daemon_down)
            view = down.overview()
            self.assertEqual((view['docker']['ok'], view['docker']['canStart']), (False, False))   # no Docker Desktop exe on this computer
            self.assertIn('Docker is not running', view['docker']['message'])
            exe = Path(machine) / 'Docker' / 'Docker' / 'Docker Desktop.exe'
            exe.parent.mkdir(parents=True)
            exe.write_bytes(b'')
            self.assertEqual(down.overview()['docker']['canStart'], True)
            slow, runner = make_apps(self.temp.name, self.store)
            runner.timeout = {'ps'}                                                                # slow is not down: nothing to start
            view = slow.overview()
            self.assertEqual((view['docker']['ok'], view['docker']['message'], view['docker']['canStart']), (False, 'Docker took too long to answer.', False))

    def test_docker_going_down_after_a_list_was_read_keeps_the_list_and_still_offers_to_start_it(self):
        down_text = 'Docker is not running. Start Docker Desktop, then check again.'
        with tempfile.TemporaryDirectory() as local, tempfile.TemporaryDirectory() as machine,                 mock.patch.dict(os.environ, {'LOCALAPPDATA': local, 'ProgramFiles': machine}):
            exe = Path(machine) / 'Docker' / 'Docker' / 'Docker Desktop.exe'
            exe.parent.mkdir(parents=True)
            exe.write_bytes(b'')
            admin, runner = make_apps(self.temp.name, self.store)
            first = admin.overview()
            self.assertEqual((first['stale'], first['docker']), (False, {'ok': True, 'message': None, 'canStart': False}))
            read = runner.inspect
            runner.inspect, runner.partial = (lambda ids: '[]'), 'error during connect: the docker daemon is not running'
            view = admin.overview()                                  # the daemon went away: the same read, now failing as "not running"
            self.assertEqual((view['stale'], view['checkedAt'], len(view['services'])), (True, first['checkedAt'], 3))   # the kept list is still served
            self.assertEqual(view['docker'], {'ok': True, 'message': down_text, 'canStart': True})
            exe.unlink()
            self.assertEqual(admin.overview()['docker'], {'ok': True, 'message': down_text, 'canStart': False})   # nothing to start on this computer
            runner.inspect, runner.partial = read, None
            self.assertEqual(admin.overview()['docker'], {'ok': True, 'message': None, 'canStart': False})        # and it clears once Docker answers
            exe.write_bytes(b'')
            runner.timeout = {'ps'}                                  # slow is not down: the old text, no button
            view = admin.overview()
            self.assertEqual((view['stale'], view['staleReason'], view['docker']), (True, 'Docker took too long to answer.', {'ok': True, 'message': None, 'canStart': False}))

    def test_logs_take_the_container_from_the_kept_list_and_read_docker_only_when_none_is_kept(self):
        runner = FakeRunner(running=('melange-desk', 'mentat-console'))          # terrier has no container
        admin, _ = make_apps(self.temp.name, self.store, runner)
        cold, _ = make_apps(self.temp.name, self.store, runner)                  # never read a list
        admin.overview()                                                         # a list is kept
        runner.timeout = {'ps'}                                                  # docker ps now runs out of time
        calls = len(runner.calls)
        self.assertTrue(admin.handle('/apps/logs', {'service': 'mentat-console'})['lines'])
        self.assertEqual([c[0][1] for c in runner.calls[calls:]], ['logs'])      # no ps in the request
        self.assertEqual(runner.calls[-1][0][-1], ID['mentat-console'])
        with self.assertRaises(NotSetUp):
            admin.handle('/apps/logs', {'service': 'terrier'})                   # the kept list has no container for it
        self.assertEqual([c[0][1] for c in runner.calls[calls:]], ['logs'])
        with self.assertRaises(DockerUnavailable):                               # with no list kept, it does read, and says so
            cold.handle('/apps/logs', {'service': 'mentat-console'})
        self.assertEqual(runner.calls[-1][0][1:3], ['ps', '-a'])

    def test_an_action_is_refused_with_the_down_message_while_docker_is_known_not_to_be_running(self):
        runner = FakeRunner(running=('mentat-console', 'terrier'), stopped=('melange-desk',))
        admin, _ = make_apps(self.temp.name, self.store, runner)
        admin.overview()
        read = runner.inspect
        runner.inspect, runner.partial = (lambda ids: '[]'), 'error during connect: the docker daemon is not running'
        self.assertTrue(admin.overview()['stale'])                               # the latest read found the daemon down
        calls = len(runner.calls)
        with self.assertRaises(DockerUnavailable) as raised:
            admin.handle('/apps/action', {'service': 'melange-desk', 'action': 'start'})
        self.assertEqual(raised.exception.message, 'Docker is not running. Start Docker Desktop, then check again.')
        self.assertEqual((len(runner.calls), self.entries(), admin.busy), (calls, [], {}))   # no docker read, nothing recorded, nothing busy
        runner.inspect, runner.partial = read, None
        runner.timeout = {'ps'}
        self.assertTrue(admin.overview()['stale'])                               # slow is not down: the action is accepted as before
        self.assertEqual(admin.handle('/apps/action', {'service': 'melange-desk', 'action': 'start'}), {'ok': True, 'accepted': True})
        self.assertEqual([(e['action'], e['outcome']) for e in self.entries()], [('start', 'succeeded')])

    def test_the_down_error_is_read_once_so_a_read_finishing_meanwhile_cannot_swap_it_out(self):
        admin, _ = make_apps(self.temp.name, self.store)
        admin.overview()                                                         # a list is kept

        class Vanishing(DockerUnavailable):
            @property
            def down(self):                                                      # a read on another thread finishes between the test and the use
                admin.failed = None
                return True
        admin.failed = gone = Vanishing('Docker is not running. Start Docker Desktop, then check again.')
        with self.assertRaises(DockerUnavailable) as raised:
            admin.handle('/apps/action', {'service': 'melange-desk', 'action': 'start'})
        self.assertIs(raised.exception, gone)                                    # the error that was tested, not None.with_traceback
        self.assertEqual(admin.busy, {})
        kept = admin.kept()
        admin.snapshot = lambda: (kept, 'Another check is still running.')
        admin.failed = gone
        self.assertEqual(admin.overview()['docker']['message'], gone.message)    # the overview says what it tested, too

    def test_start_docker_launches_only_the_fixed_exe_detached_and_audits_it(self):
        launched = []
        self.apps.docker.launch = lambda argv, **kwargs: launched.append((argv, kwargs))
        with tempfile.TemporaryDirectory() as local, tempfile.TemporaryDirectory() as machine, \
                mock.patch.dict(os.environ, {'LOCALAPPDATA': local, 'ProgramFiles': machine}):
            with self.assertRaises(NotSetUp):
                self.apps.handle('/apps/docker-start', {})                       # neither install folder holds the exe
            self.assertEqual((launched, self.entries()), ([], []))
            system = Path(machine) / 'Docker' / 'Docker' / 'Docker Desktop.exe'
            system.parent.mkdir(parents=True)
            system.write_bytes(b'')
            self.assertEqual(self.apps.handle('/apps/docker-start', {}), {'ok': True, 'accepted': True})
            self.assertEqual(launched[-1][0], [str(system)])
            self.assertEqual(launched[-1][1]['cwd'], os.path.dirname(str(system)))   # its own folder, not the dashboard's (a OneDrive repo)
            user = Path(local) / 'Programs' / 'DockerDesktop' / 'Docker Desktop.exe'
            user.parent.mkdir(parents=True)
            user.write_bytes(b'')
            self.apps.overview()                                                 # Docker answered, so another start is accepted
            self.apps.handle('/apps/docker-start', {})
            argv, kwargs = launched[-1]
            self.assertEqual(argv, [str(user)])                                  # the per-user install comes first
            self.assertIs(kwargs['shell'], False)
            self.assertEqual(kwargs['creationflags'], getattr(subprocess, 'DETACHED_PROCESS', 0) | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0))
            self.assertEqual(kwargs['cwd'], os.path.dirname(str(user)))
            self.assertEqual({kwargs[name] for name in ('stdin', 'stdout', 'stderr')}, {subprocess.DEVNULL})
            self.assertEqual([(e['service'], e['action'], e['outcome']) for e in self.entries()], [('docker-desktop', 'start', 'succeeded')] * 2)
            shown = self.apps.overview()['activity']                                 # and it shows in the Apps timeline
            self.assertEqual([(a['name'], a['action'], a['outcome']) for a in shown], [('Docker Desktop', 'start', 'succeeded')] * 2)
            for request in ({'path': str(user)}, {'exe': r'C:\evil.exe'}, {'args': ['--x']}, {'service': 'terrier'}):   # nothing from the request reaches the launch
                with self.subTest(request=request), self.assertRaises(BadRequest):
                    self.apps.handle('/apps/docker-start', request)
            self.assertEqual(len(launched), 2)

            def broken(argv, **kwargs):
                raise OSError('not allowed')
            self.apps.overview()
            self.apps.docker.launch = broken
            with self.assertRaises(ActionFailed):
                self.apps.handle('/apps/docker-start', {})
            self.assertEqual(self.entries()[0]['outcome'], 'failed')

    def test_a_second_docker_start_while_one_is_starting_is_refused_and_not_logged(self):
        launched, now = [], [1000.0]
        self.apps.docker.launch = lambda argv, **kwargs: launched.append(argv)
        self.apps.monotonic = lambda: now[0]
        with tempfile.TemporaryDirectory() as local, tempfile.TemporaryDirectory() as machine, \
                mock.patch.dict(os.environ, {'LOCALAPPDATA': local, 'ProgramFiles': machine}):
            exe = Path(machine) / 'Docker' / 'Docker' / 'Docker Desktop.exe'
            exe.parent.mkdir(parents=True)
            exe.write_bytes(b'')
            self.apps.handle('/apps/docker-start', {})
            now[0] += 170                                                        # Docker has not answered: a second click, another tab
            with self.assertRaises(Busy) as refused:
                self.apps.handle('/apps/docker-start', {})
            self.assertIn('already starting', refused.exception.message)
            self.assertEqual((len(launched), [e['outcome'] for e in self.entries()]), (1, ['succeeded']))   # one launch, one entry
            now[0] += 11                                                         # three minutes without an answer: it may be tried again
            self.apps.handle('/apps/docker-start', {})
            self.apps.overview()                                                 # Docker answers
            self.apps.handle('/apps/docker-start', {})                           # so a later start is not held back
            self.assertEqual(len(launched), 3)

            gate, entered = threading.Event(), threading.Event()

            def slow(argv, **kwargs):
                entered.set()
                gate.wait(5)
                launched.append(argv)
            self.apps.overview()
            self.apps.docker.launch = slow
            first = threading.Thread(target=self.apps.handle, args=('/apps/docker-start', {}), daemon=True)
            first.start()
            self.assertTrue(entered.wait(5))
            with self.assertRaises(Busy):                                        # two at once: one launch, not two
                self.apps.handle('/apps/docker-start', {})
            gate.set()
            first.join(5)
            self.assertEqual(len(launched), 4)

            def broken(argv, **kwargs):
                raise OSError('not allowed')
            self.apps.overview()
            self.apps.docker.launch = broken
            with self.assertRaises(ActionFailed):
                self.apps.handle('/apps/docker-start', {})
            self.apps.docker.launch = lambda argv, **kwargs: launched.append(argv)
            self.apps.handle('/apps/docker-start', {})                           # a launch that failed holds nothing back
            self.assertEqual(len(launched), 5)
            self.assertEqual([e['outcome'] for e in self.entries()].count('failed'), 1)
            self.assertEqual(len(self.entries()), 6)                             # five launches and the failed one; no refusal is logged

    def test_failed_action_logs_one_failed_entry_with_redacted_reason(self):
        failing, runner = make_apps(self.temp.name, self.store, FakeRunner(fail=('restart',)))
        failing.handle('/apps/action', {'service': 'terrier', 'action': 'restart'})
        self.assertEqual([(e['action'], e['outcome']) for e in self.entries()], [('restart', 'failed')])
        last = {r['service']: r for r in failing.overview()['services']}['terrier']['last']
        self.assertEqual(last['outcome'], 'failed')
        self.assertIn('[redacted]', last['message'])
        self.assertNotIn('hunter2', json.dumps(last))
        self.assertEqual(failing.busy, {})

    def test_second_concurrent_action_on_a_service_is_refused(self):
        runner = FakeRunner()
        runner.gate, runner.entered = threading.Event(), threading.Event()
        threads = []

        def spawn(fn):
            threads.append(threading.Thread(target=fn, daemon=True))
            threads[-1].start()
        locked, _ = make_apps(self.temp.name, self.store, runner, spawn)
        locked.handle('/apps/action', {'service': 'terrier', 'action': 'restart'})
        self.assertTrue(runner.entered.wait(3))
        for action in ('update', 'backup', 'restart', 'start'):
            with self.subTest(action=action), self.assertRaises(Busy):
                locked.handle('/apps/action', {'service': 'terrier', 'action': action})
        row = {r['service']: r for r in locked.overview()['services']}['terrier']
        self.assertEqual((row['busy']['action'], row['actions']), ('restart', []))
        with self.assertRaises(Busy):
            locked.handle('/apps/invite', {'email': mail('a'), 'role': 'canvasser'})
        locked.handle('/apps/action', {'service': 'melange-desk', 'action': 'restart'})   # another service is free
        runner.gate.set()
        for thread in threads:
            thread.join(3)
        self.assertEqual(locked.busy, {})
        self.assertEqual(len(self.entries()), 2)        # one entry per accepted action, none for the refused ones
        locked.handle('/apps/action', {'service': 'terrier', 'action': 'restart'})
        threads[-1].join(3)
        self.assertEqual(len(self.entries()), 3)

    def test_a_thread_that_cannot_start_leaves_nothing_busy(self):
        def broken(fn):
            raise RuntimeError("can't start new thread")
        stuck, _ = make_apps(self.temp.name, self.store, spawn=broken)
        with self.assertRaises(ActionFailed):
            stuck.handle('/apps/action', {'service': 'terrier', 'action': 'restart'})
        self.assertEqual((stuck.busy, self.entries()), ({}, []))
        stuck.overview()                                      # the disk-size read cannot start either
        self.assertFalse(stuck.df_running)
        stuck.spawn = lambda fn: fn()                         # once threads work again nothing is stuck
        stuck.handle('/apps/action', {'service': 'terrier', 'action': 'restart'})
        self.assertEqual([(e['action'], e['outcome']) for e in self.entries()], [('restart', 'succeeded')])

    def test_shutdown_records_each_running_action_as_interrupted_once(self):
        runner = FakeRunner()
        runner.gate, runner.entered = threading.Event(), threading.Event()
        threads = []

        def spawn(fn):
            threads.append(threading.Thread(target=fn, daemon=True))
            threads[-1].start()
        closing, _ = make_apps(self.temp.name, self.store, runner, spawn)
        closing.shutdown()
        self.assertEqual(self.entries(), [])                  # nothing running: nothing to record
        closing.handle('/apps/action', {'service': 'terrier', 'action': 'restart'})
        self.assertTrue(runner.entered.wait(3))
        closing.shutdown()
        self.assertEqual([(e['service'], e['action'], e['outcome'], e['actor']) for e in self.entries()], [('terrier', 'restart', 'interrupted', 'admin')])
        runner.gate.set()
        for thread in threads:
            thread.join(3)
        closing.shutdown()
        self.assertEqual(len(self.entries()), 1)              # the action that finished afterwards adds nothing

    def test_the_page_waits_out_the_slowest_invite(self):
        # invite() reads the containers (REFRESH_BUDGET), then runs the command (INVITE_TIMEOUT, plus 5 s to kill its tree).
        # A page that aborts first loses the link while Terrier still creates the invitation; a retry then hits 'Use reset'.
        script = (Path(apps.__file__).parent / 'admin_ui_v2' / 'app.js').read_text(encoding='utf-8')
        wait = int(re.search(r"call\('/apps/invite',\{[^}]*\},(\d+)\)", script).group(1)) / 1000
        self.assertGreater(wait, apps.REFRESH_BUDGET + apps.INVITE_TIMEOUT + 5)

    def test_invite_uses_terrier_command_and_returns_the_link_once(self):
        result = self.apps.handle('/apps/invite', {'email': 'maya@example.org', 'role': 'organizer'})
        self.assertEqual(result, {'ok': True, 'link': 'http://127.0.0.1:18766/?invite=TESTTOKEN', 'expiresInHours': 24})
        argv = self.runner.calls[-1][0]
        self.assertEqual(argv[-8:], ['manage.py', 'invite', '--field-staff', '--field-manager', '--origin', 'http://127.0.0.1:18766', '--', 'maya@example.org'])
        self.assertEqual([(e['action'], e['outcome']) for e in self.entries()], [('invite', 'succeeded')])
        self.assertNotIn('maya', json.dumps(self.entries()))
        self.assertNotIn('TESTTOKEN', json.dumps(self.apps.overview()))
        self.runner.invite_output, self.runner.invite_error = '', 'CommandError: Use reset for an existing account, invitation for a new account\n'
        with self.assertRaises(ActionFailed) as caught:
            self.apps.handle('/apps/invite', {'email': 'maya@example.org', 'role': 'canvasser'})
        self.assertIn('Use reset', caught.exception.message)
        self.assertEqual([e['outcome'] for e in self.entries()], ['failed', 'succeeded'])

    def test_invite_refuses_bad_input_and_stopped_terrier_without_running_docker_or_logging(self):
        before = len(self.runner.calls)
        for value in ({'email': mail('-x'), 'role': 'canvasser'}, {'email': mail('a'), 'role': 'root'}, {'email': 5, 'role': 'canvasser'},
                      {'email': mail('a')}, {'email': mail('a'), 'role': 'canvasser', 'extra': 1}):
            with self.subTest(value=value), self.assertRaises(BadRequest):
                self.apps.handle('/apps/invite', value)
        self.assertEqual((len(self.runner.calls), self.entries()), (before, []))
        stopped, _ = make_apps(self.temp.name, self.store, FakeRunner(running=('melange-desk', 'mentat-console'), stopped=('terrier',)))
        with self.assertRaises(NotSetUp):
            stopped.handle('/apps/invite', {'email': mail('a'), 'role': 'canvasser'})
        self.assertEqual(self.entries(), [])

    def test_logs_are_redacted_leveled_and_bounded_to_known_services(self):
        lines = self.apps.handle('/apps/logs', {'service': 'terrier'})['lines']
        self.assertEqual([l['level'] for l in lines], ['INFO', 'INFO', 'WARN', 'ERROR', 'INFO'])
        self.assertEqual(lines[0]['text'], 'Server started on port 8080')
        self.assertEqual(lines[1]['text'], 'Authorization: Bearer [redacted]')
        self.assertEqual(lines[4]['text'], 'SECRET_KEY=[redacted]')
        self.assertNotIn('hunter2', json.dumps(lines))
        self.assertEqual(lines[0]['ts'], 1800000000 + 1 * 3600 + 41 * 60 + 2)
        self.assertEqual(self.runner.calls[-1][0][1:], ['logs', '--tail', '500', '--timestamps', ID['terrier']])

    def test_a_second_logs_read_for_the_same_service_is_refused_at_once(self):
        runner = FakeRunner()
        runner.logs_gate, runner.logs_entered = threading.Event(), threading.Event()
        slow, _ = make_apps(self.temp.name, self.store, runner)
        first = threading.Thread(target=lambda: slow.handle('/apps/logs', {'service': 'terrier'}), daemon=True)
        first.start()
        self.assertTrue(runner.logs_entered.wait(3))
        calls = len(runner.calls)
        with self.assertRaises(Busy):
            slow.handle('/apps/logs', {'service': 'terrier'})
        self.assertEqual(len(runner.calls), calls)               # refused before any docker call
        runner.logs_gate.set()
        first.join(3)
        self.assertEqual(slow.logs_running, set())
        self.assertTrue(slow.handle('/apps/logs', {'service': 'terrier'})['lines'])   # free again once the first read is done

    def test_unknown_service_action_route_and_shape_are_bad_requests(self):
        before = len(self.runner.calls)
        for path, value in (('/apps/detail', {'service': 'nope'}), ('/apps/logs', {'service': 'terrier-relay'}),
                            ('/apps/action', {'service': 'nope', 'action': 'restart'}), ('/apps/action', {'service': 'terrier', 'action': 'rm'}),
                            ('/apps/action', {'service': 'melange-desk', 'action': 'backup'}), ('/apps/action', {'service': 'terrier'}),
                            ('/apps/action', {'service': ['terrier'], 'action': 'restart'}), ('/apps', {'x': 1}), ('/apps/other', {'service': 'terrier'})):
            with self.subTest(path=path, value=value), self.assertRaises(BadRequest):
                self.apps.handle(path, value)
        self.assertEqual((len(self.runner.calls), self.entries()), (before, []))

    # ---- fast list: label-filtered ps, cached config, last good list ---------------------------------------------------------

    def test_ps_is_a_label_filtered_docker_ps_not_compose_ps(self):
        config = make_config(self.temp.name)
        self.assertEqual(config.project, 'melange-local')
        compose = Path(apps.OPS / 'compose.local.yaml')
        if compose.exists():   # the label value is the compose file's own `name:`
            self.assertEqual(re.search(r'^name:\s*(\S+)', compose.read_text(encoding='utf-8'), re.M).group(1), apps.DEFAULT_PROJECT)
        with self.assertRaises(ValueError):
            AppsConfig('x --filter label=a=b', config.compose_file, DEFAULT_SERVICES)   # a project name cannot carry another filter
        self.apps.overview()
        listed = [c[0][1:] for c in self.runner.calls if c[0][1] == 'ps']
        self.assertEqual(listed, [['ps', '-a', '-q', '--no-trunc', '--filter', 'label=com.docker.compose.project=melange-local']])
        self.assertEqual([c[0] for c in self.runner.calls if c[0][1] == 'compose' and 'ps' in c[0]], [])   # no `compose ps` anywhere
        inspected = [c[0][2:] for c in self.runner.calls if c[0][1] == 'inspect']
        self.assertEqual(len(inspected), 1)                                    # one inspect for all of the containers
        self.assertEqual(set(inspected[0]), {ID[n] for n in ID})
        with self.assertRaises(NotAllowed):                                    # the id list is checked as before (64 hex with --no-trunc)
            self.apps.docker.run('inspect', 5, ids=['not-a-container'])

    def test_services_come_from_inspect_labels_and_ports_from_network_settings(self):
        runner = FakeRunner()
        runner.ports['desk-relay'] = {'18080/tcp': [{'HostIp': '192.168.1.5', 'HostPort': '9999'}, {'HostIp': '0.0.0.0', 'HostPort': '18080'},
                                                   {'HostIp': '::', 'HostPort': '18080'}], '9/tcp': None}
        runner.ports['mentat-relay'] = {'18802/tcp': [{'HostIp': '192.168.1.5', 'HostPort': '18802'}]}   # not loopback: no Open link
        runner.ports['terrier-relay'] = {}
        foreign = {'Id': 'd' * 64, 'State': {'Status': 'running'}, 'Config': {'Image': OLD, 'Labels': {
            'com.docker.compose.project': 'someone-else', 'com.docker.compose.service': 'melange-desk'}}}
        oneoff = {'Id': 'e' * 64, 'State': {'Status': 'running'}, 'Config': {'Image': OLD, 'Labels': {
            'com.docker.compose.project': PROJECT, 'com.docker.compose.service': 'terrier', 'com.docker.compose.oneoff': 'True'}}}
        runner.extra = [('d' * 64, foreign), ('e' * 64, oneoff)]
        mapped, _ = make_apps(self.temp.name, self.store, runner)
        rows = {r['service']: r for r in mapped.overview()['services']}
        self.assertEqual(rows['melange-desk']['openUrl'], 'http://127.0.0.1:18080')
        self.assertEqual((rows['mentat-console']['openUrl'], rows['mentat-console']['actions']), (None, ['update', 'restart']))
        self.assertIsNone(rows['terrier']['openUrl'])
        self.assertEqual(rows['terrier']['state'], 'running')                   # the one-off container did not take its place
        self.assertEqual(rows['melange-desk']['update'], False)                 # nor did another project's container
        self.assertEqual(apps.published_port({'NetworkSettings': {'Ports': {'1/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '0'}]}}}), None)
        self.assertEqual(apps.published_port({'NetworkSettings': {'Ports': {'80/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '18080'}]}}}), 18080)
        for odd in (None, {}, {'NetworkSettings': None}, {'NetworkSettings': {'Ports': None}}, {'NetworkSettings': {'Ports': {'1/tcp': 'x'}}},
                    {'NetworkSettings': {'Ports': {'1/tcp': [None, {'HostPort': None}, {'HostIp': '127.0.0.1', 'HostPort': 'x'}]}}}):
            self.assertIsNone(apps.published_port(odd))
        # act, logs and invite use the same lookup: the relay's port builds the invite origin, the service's Id is the one logged
        self.assertEqual(mapped.handle('/apps/logs', {'service': 'terrier'})['lines'][0]['text'], 'Server started on port 8080')
        self.assertEqual(runner.calls[-1][0][-1], ID['terrier'])
        with self.assertRaises(NotSetUp):                                       # terrier-relay publishes nothing: no origin to invite from
            mapped.handle('/apps/invite', {'email': mail('a'), 'role': 'canvasser'})

    def config_calls(self, runner):
        return [c for c in runner.calls if c[0][1] == 'compose' and c[0][6] == 'config']

    def test_compose_config_is_read_once_until_a_file_it_reads_changes(self):
        env = Path(self.temp.name) / '.env'
        secrets = Path(self.temp.name) / 'terrier.env'
        env.write_text('IMAGE_TERRIER=x\n')
        secrets.write_text('CU_SECRET_KEY=x\n')
        compose = Path(self.apps.config.compose_file)
        self.runner.env_files = [{'path': str(secrets), 'required': True}]
        for _ in range(3):
            self.assertIsNone(self.apps.overview()['notice'])
        self.assertEqual(len(self.config_calls(self.runner)), 1)                # cold, then two cache hits
        self.assertEqual(self.apps.config_cache[0], (str(compose), str(env), str(secrets)))
        self.assertEqual(self.apps.config_cache[2]['mentat-console'], NEW)
        stat = env.stat()
        os.utime(env, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))   # the same size, a later mtime: ops/.env was edited
        self.apps.overview()
        self.assertEqual(len(self.config_calls(self.runner)), 2)
        self.apps.overview()
        self.assertEqual(len(self.config_calls(self.runner)), 2)                # and cached again
        stat = secrets.stat()
        os.utime(secrets, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))   # an env_file the config lists
        self.apps.overview()
        self.assertEqual(len(self.config_calls(self.runner)), 3)
        compose.write_text('name: melange-local\n# edited\n')                  # the compose file itself (size changes)
        self.apps.overview()
        self.assertEqual(len(self.config_calls(self.runner)), 4)
        env.unlink()                                                            # a file that disappears is a change too
        self.apps.overview()
        self.assertEqual(len(self.config_calls(self.runner)), 5)

    def test_a_failed_compose_config_is_not_kept(self):
        self.runner.fail = {'config'}
        for _ in range(2):
            view = self.apps.overview()
            self.assertIn('updates cannot be detected', view['notice'])
            self.assertEqual({r['service'] for r in view['services']}, set(s.name for s in DEFAULT_SERVICES))
        self.assertEqual(len(self.config_calls(self.runner)), 2)                # asked again each time
        self.assertIsNone(self.apps.config_cache)
        self.runner.fail = set()
        self.assertIsNone(self.apps.overview()['notice'])
        self.assertEqual(len(self.config_calls(self.runner)), 3)
        self.apps.overview()
        self.assertEqual(len(self.config_calls(self.runner)), 3)                # now it is kept
        self.runner.timeout = {'config'}                                        # a timeout is a failure too
        self.apps.config_cache = None
        self.assertIn('updates cannot be detected', self.apps.overview()['notice'])
        self.assertIsNone(self.apps.config_cache)

    def test_a_timeout_or_a_running_read_answers_with_the_last_good_list(self):
        clock = [STAMP_CLOCK]
        self.apps.clock = lambda: clock[0]
        self.store.audit_app_action('admin', 'terrier', 'backup', 'succeeded')
        first = self.apps.overview()
        self.assertEqual((first['stale'], first['checkedAt']), (False, STAMP_CLOCK))
        self.assertNotIn('staleReason', first)
        clock[0] += 100
        self.runner.timeout = {'ps'}
        late = self.apps.overview()
        self.assertEqual((late['stale'], late['checkedAt'], late['docker']['ok']), (True, STAMP_CLOCK, True))   # when the list was read, not now
        self.assertEqual(late['staleReason'], 'Docker took too long to answer.')
        self.assertEqual(late['services'], first['services'])                   # the same rows, with no "Could not read" in sight
        self.runner.timeout = {'inspect'}
        self.assertTrue(self.apps.overview()['stale'])
        self.runner.timeout = set()
        self.runner.fail = {'ps'}
        down = self.apps.overview()
        self.assertEqual((down['stale'], down['staleReason']), (True, 'Docker could not read the local services.'))
        self.runner.fail = set()
        # Busy and last are the current ones even when the list is old.
        self.apps.busy['terrier'] = {'action': 'restart', 'since': clock[0]}
        self.runner.timeout = {'ps'}
        rows = {r['service']: r for r in self.apps.overview()['services']}
        self.assertEqual((rows['terrier']['busy']['action'], rows['terrier']['actions']), ('restart', []))
        self.apps.busy.clear()
        self.runner.timeout = set()
        clock[0] += 100
        fresh = self.apps.overview()
        self.assertEqual((fresh['stale'], fresh['checkedAt']), (False, STAMP_CLOCK + 200))   # and the next read that works is fresh

    def test_a_second_overview_during_a_read_returns_the_last_list_at_once(self):
        self.apps.overview()                                                    # the list to fall back on
        self.runner.ps_gate, self.runner.ps_entered = threading.Event(), threading.Event()
        answers = []
        reader = threading.Thread(target=lambda: answers.append(self.apps.overview()), daemon=True)
        reader.start()
        self.assertTrue(self.runner.ps_entered.wait(3))                         # the first read is inside docker now
        calls = len(self.runner.calls)
        started = time.perf_counter()
        second = self.apps.overview()
        self.assertLess(time.perf_counter() - started, 1.0)                     # it did not wait for the read
        self.assertEqual((second['stale'], second['staleReason']), (True, 'Another check is still running.'))
        self.assertEqual(len(self.runner.calls), calls)                         # and started no docker call of its own
        detail = self.apps.handle('/apps/detail', {'service': 'terrier'})
        self.assertEqual((detail['stale'], detail['service']['state']), (True, 'running'))
        self.assertEqual(len(self.runner.calls), calls)
        self.runner.ps_gate.set()
        reader.join(5)
        self.assertEqual(answers[0]['stale'], False)
        self.assertIsNone(self.apps.reading)

    def test_a_read_stuck_in_docker_cannot_hold_the_page_past_its_budget(self):
        # The page gives up at 12 s. A docker call that is slow to be killed must not hold a request that long: the request
        # answers at the budget (the last list, or the error when there is none) and the read goes on in the background.
        self.apps.overview()
        for cache in ('kept', 'none'):
            admin, runner = (self.apps, self.runner) if cache == 'kept' else make_apps(self.temp.name, self.store)
            runner.ps_gate, runner.ps_entered = threading.Event(), threading.Event()
            self.addCleanup(runner.ps_gate.set)
            with mock.patch.object(apps, 'REFRESH_BUDGET', 1.5):
                started = time.perf_counter()
                view = admin.overview()
                took = time.perf_counter() - started
            self.assertTrue(runner.ps_entered.wait(5))                          # the read is stuck inside `docker ps`
            self.assertLess(took, 4.0)                                          # and the request was answered long before the gate opened
            if cache == 'kept':
                self.assertEqual((view['stale'], view['staleReason'], view['docker']['ok']), (True, 'Docker took too long to answer.', True))
            else:
                self.assertEqual((view['docker']['ok'], view['docker']['message'], view['stale']), (False, 'Docker took too long to answer.', False))
            self.assertIsNotNone(admin.reading)                                 # still reading; nobody else starts another
            if cache == 'kept':
                self.assertEqual(admin.overview()['staleReason'], 'Another check is still running.')
            runner.ps_gate.set()
            for _ in range(60):
                if admin.reading is None:
                    break
                time.sleep(0.05)
            self.assertIsNone(admin.reading)
            self.assertTrue(admin.overview()['docker']['ok'])                   # and the next request is served normally

    def test_the_first_read_is_waited_for_when_there_is_no_list_yet(self):
        self.runner.ps_gate, self.runner.ps_entered = threading.Event(), threading.Event()
        answers = []
        threads = [threading.Thread(target=lambda: answers.append(self.apps.overview()), daemon=True) for _ in range(2)]
        threads[0].start()
        self.assertTrue(self.runner.ps_entered.wait(3))
        threads[1].start()                                                      # nothing kept: it can only wait for the first read
        time.sleep(0.2)
        self.assertEqual(answers, [])
        self.runner.ps_gate.set()
        for thread in threads:
            thread.join(5)
        self.assertEqual([(a['stale'], a['docker']['ok']) for a in answers], [(False, True), (False, True)])
        self.assertEqual(len([c for c in self.runner.calls if c[0][1] == 'ps']), 1)   # one read served both

    def test_no_kept_list_means_the_error_as_before(self):
        for how in ('timeout', 'fail'):
            cold, runner = make_apps(self.temp.name, self.store)
            setattr(runner, how, {'ps'})
            view = cold.overview()
            self.assertEqual((view['docker']['ok'], view['stale']), (False, False))
            self.assertTrue(view['docker']['message'])
            self.assertEqual({r['state'] for r in view['services']}, {'unknown'})
            with self.assertRaises(DockerUnavailable):
                cold.handle('/apps/detail', {'service': 'terrier'})
            setattr(runner, how, set())
            self.assertTrue(cold.overview()['docker']['ok'])                    # and the next read that works is just fine

    def test_one_read_stays_inside_its_time_budget(self):
        self.assertLessEqual(apps.CALL_CAP['ps'] + apps.CALL_CAP['inspect'], apps.REFRESH_BUDGET)   # the calls that run one after another
        self.assertLessEqual(max(apps.CALL_CAP.values()), apps.REFRESH_BUDGET)
        self.assertLess(apps.REFRESH_BUDGET, 12)                                # the page gives up on a request at 12 s
        self.apps.overview()
        used = {c[0][1] if c[0][1] != 'compose' else c[0][6]: c[1]['timeout'] for c in self.runner.calls}
        self.assertEqual(used['ps'], 6)                                         # its own cap: the whole budget is still there
        self.assertLessEqual(used['inspect'], 4)                                # its cap, or what ps left of the budget
        self.assertTrue(9 < used['config'] <= apps.REFRESH_BUDGET)              # the whole budget, less the moment since the read began
        self.assertLessEqual(used['image'], apps.REFRESH_BUDGET)   # mentat-console is out of date: its pinned image is looked up
        now = [0.0]
        runner = FakeRunner()
        slow, runner = make_apps(self.temp.name, self.store, runner)
        slow.monotonic = lambda: now[0]
        original = runner.ps
        runner.ps = lambda: (now.__setitem__(0, now[0] + 9.8), original())[1]  # `docker ps` alone used up almost all of the time
        view = slow.overview()
        self.assertFalse(view['docker']['ok'])                                  # no list to fall back on, and inspect was never started
        self.assertEqual([c[0][1] for c in runner.calls if c[0][1] == 'inspect'], [])
        now[0] = 100.0
        runner.ps = original
        self.assertTrue(slow.overview()['docker']['ok'])                        # the next read gets a whole budget again
        now[0] = 200.0
        calls = len(runner.calls)
        runner.ps = lambda: (now.__setitem__(0, now[0] + 9.8), original())[1]
        self.assertTrue(slow.overview()['stale'])                               # with a list kept, the same slowness is stale, not an error
        self.assertEqual([c[0][1] for c in runner.calls[calls:] if c[0][1] == 'inspect'], [])

    def test_the_slow_parts_of_a_read_are_bounded_by_what_is_left(self):
        deadline = apps.Deadline(10, lambda: 0.0)
        self.assertEqual(deadline.timeout(4), 4)
        late = apps.Deadline(10, iter([0.0, 7.0, 9.8, 10.5]).__next__)
        self.assertEqual(late.timeout(4), 3.0)                                  # 3 s left: less than the cap
        with self.assertRaises(DockerUnavailable):
            late.timeout(4)                                                     # 0.2 s left: not worth starting a docker call
        # a /version probe (2 s) is not started with less than that left, and is not marked as failed for 10 minutes either
        now = [0.0]
        probing, _ = make_apps(self.temp.name, self.store, FakeRunner())
        probing.monotonic = lambda: now[0]
        probes = []
        probing.fetch_version = lambda port: probes.append(port)
        info = {'label': None, 'id': 'x', 'kind': 'running', 'port': 1234}
        self.assertIsNone(probing.version(info, apps.Deadline(1.0, lambda: 0.0)))
        self.assertEqual((probes, probing.cache.get(('version', 'x'))), ([], None))

    def test_detail_reports_health_storage_and_image(self):
        detail = self.apps.handle('/apps/detail', {'service': 'terrier'})
        self.assertEqual(detail['health'], {'status': 'passing', 'checkedAt': STAMP_CLOCK - 60})
        self.assertEqual(detail['uptime'], 4 * 3600 + 58 * 60)
        self.assertEqual(detail['storage'][0], {'type': 'volume', 'destination': '/state', 'readOnly': False, 'bytes': 299400000})
        self.assertEqual(detail['storage'][1]['readOnly'], True)
        self.assertEqual(detail['accounts'], {'supported': True, 'listing': False, 'roles': ['administrator', 'organizer', 'canvasser']})
        self.assertTrue(detail['image']['pinnedByDigest'])
        self.assertEqual(self.apps.handle('/apps/detail', {'service': 'melange-desk'})['accounts']['supported'], False)


if __name__ == '__main__': unittest.main()

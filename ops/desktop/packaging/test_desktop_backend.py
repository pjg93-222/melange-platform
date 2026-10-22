import contextlib
import hashlib
import importlib.util
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.request
from urllib.error import HTTPError

spec = importlib.util.spec_from_file_location('desktop_backend', Path(__file__).with_name('desktop_backend.py'))
backend = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backend)


class BackendBoundaryTests(unittest.TestCase):
    def test_github_dataset_flag_requires_existing_terrier_owner_and_separate_roots(self):
        base = ['desktop_backend', '--app', 'terrier', '--port', '2000', '--source-root', 'src', '--data-root', 'data', '--state-root', 'state']
        with patch.dict('os.environ', {'DESKTOP_LAUNCH_TOKEN': 'x'*32}), patch.object(backend, 'run') as run, patch.object(backend, 'contain_windows_children'):
            for argv in (base + ['--github-district-data'],
                         [*base[:2], 'mentat', *base[3:], '--github-district-data'],
                         base + ['--legacy-owner', 'admin', '--owner-root', 'owner', '--github-district-data', '--state-root', 'data']):
                with patch('sys.argv', argv), self.assertRaises(SystemExit):
                    backend.main()
            with patch('sys.argv', base + ['--legacy-owner', 'admin', '--owner-root', 'owner', '--github-district-data']):
                backend.main()
        self.assertEqual(run.call_count, 1)

    def test_owner_directory_goes_with_and_only_with_the_existing_terrier_owner(self):
        base = ['desktop_backend', '--app', 'terrier', '--port', '2000', '--source-root', 'src', '--data-root', 'data', '--state-root', 'state']
        with patch.dict('os.environ', {'DESKTOP_LAUNCH_TOKEN': 'x'*32}), patch.object(backend, 'run') as run, patch.object(backend, 'contain_windows_children'):
            for extra in (['--owner-root', 'owner'], ['--legacy-owner', 'admin']):
                with patch('sys.argv', base + extra), self.assertRaises(SystemExit):
                    backend.main()
            with patch('sys.argv', base + ['--legacy-owner', 'admin', '--owner-root', 'owner']):
                backend.main()
        self.assertEqual(run.call_count, 1)

    def test_invitation_is_only_in_authenticated_health_and_no_referrer(self):
        guarded = backend.GuardedWSGI(lambda environ, start: [b'app'], 'x'*32, 'terrier', launch_path='/?invite=private')
        capture = []
        start = lambda status, headers: capture.append((status, dict(headers)))
        denied = b''.join(guarded({'PATH_INFO':backend.HEALTH_PATH, 'REQUEST_METHOD':'GET'}, start))
        self.assertNotIn(b'private', denied)
        answer = b''.join(guarded({'PATH_INFO':backend.HEALTH_PATH, 'REQUEST_METHOD':'GET', 'HTTP_X_DESKTOP_TOKEN':'x'*32}, start))
        self.assertEqual(json.loads(answer)['launchPath'], '/?invite=private')
        self.assertEqual(capture[-1][1]['Referrer-Policy'], 'no-referrer')
        self.assertEqual(capture[-1][1]['Cache-Control'], 'no-store')

    def test_mentat_activity_reports_real_busy_without_data(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), backend.guarded_handler(Handler, 'x'*32, 'mentat'))
        working = [True]
        server.app = SimpleNamespace(busy=lambda: working[0])
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = 'http://127.0.0.1:' + str(server.server_port) + backend.ACTIVITY_PATH
        try:
            with self.assertRaises(HTTPError) as denied:
                urllib.request.urlopen(url)
            self.assertEqual(denied.exception.code, 403)
            for busy in (True, False):
                working[0] = busy
                with urllib.request.urlopen(urllib.request.Request(url, headers={backend.TOKEN_HEADER: 'x'*32})) as response:
                    self.assertEqual(json.load(response), {'appId':'mentat', 'busy':busy})
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_stdlib_boundary_protects_every_verb_without_replacing_existing_routes(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'existing app')
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), backend.guarded_handler(Handler, 'x'*32, 'melange'))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = 'http://127.0.0.1:' + str(server.server_port)
        try:
            for method in ('GET', 'POST', 'PUT', 'DELETE', 'HEAD', 'OPTIONS'):
                with self.assertRaises(HTTPError) as result:
                    urllib.request.urlopen(urllib.request.Request(origin+'/', method=method))
                self.assertEqual(result.exception.code, 403)
            request = urllib.request.Request(origin+'/', headers={backend.TOKEN_HEADER:'x'*32})
            self.assertEqual(urllib.request.urlopen(request).read(), b'existing app')
            request = urllib.request.Request(origin+backend.HEALTH_PATH, headers={backend.TOKEN_HEADER:'x'*32})
            self.assertEqual(json.load(urllib.request.urlopen(request))['appId'], 'melange')
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_django_boundary_authenticates_before_existing_application(self):
        calls=[]
        def app(environ,start):
            calls.append(environ)
            start('200 OK', [])
            return [b'existing django']
        guarded=backend.GuardedWSGI(app,'x'*32,'terrier')
        status=[]
        self.assertEqual(guarded({}, lambda code, headers: status.append(code)), [b'Desktop authorization required'])
        self.assertEqual(status, ['403 Forbidden'])
        self.assertEqual(calls, [])
        self.assertEqual(guarded({'HTTP_X_DESKTOP_TOKEN':'x'*32}, lambda *args: None), [b'existing django'])


class DistrictBundleImportTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.data, self.state = Path(temp.name) / 'data', Path(temp.name) / 'state'
        self.data.mkdir()
        self.state.mkdir()
        self.imported = []
        def import_bundle(file, actor='local operator', expected_sha256=None):  # the checksum gate of Terrier bundles.import_bundle
            if hashlib.sha256(Path(file).read_bytes()).hexdigest() != expected_sha256:
                raise ValueError('Bundle checksum does not match the expected sha256')
            self.imported.append(expected_sha256)
        bundles = ModuleType('server.research.bundles')
        bundles.import_bundle = import_bundle
        modules = patch.dict(sys.modules, {'server': ModuleType('server'), 'server.research': ModuleType('server.research'),
                                           'server.research.bundles': bundles})
        modules.start()
        self.addCleanup(modules.stop)

    def pack(self, content, sidecar=None):
        (self.data / 'bundle.zip').write_bytes(content)
        (self.data / 'bundle.zip.sha256').write_text((sidecar or hashlib.sha256(content).hexdigest()) + '\n', encoding='utf-8')

    def run_import(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            backend.import_district_bundle(self.data, self.state)
        return stderr.getvalue()

    def test_marker_makes_import_once_per_sidecar_sha(self):
        self.assertEqual(self.run_import(), '')  # no pack: nothing to do
        self.assertFalse((self.state / 'bundle-imported.sha256').exists())
        self.pack(b'first')
        first = hashlib.sha256(b'first').hexdigest()
        self.assertEqual((self.run_import(), self.run_import()), ('', ''))
        self.assertEqual(self.imported, [first])
        self.assertEqual((self.state / 'bundle-imported.sha256').read_text(encoding='utf-8'), first + '\n')
        self.pack(b'second')
        self.run_import()
        self.assertEqual(self.imported, [first, hashlib.sha256(b'second').hexdigest()])

    def test_wrong_sha_only_warns_and_is_retried(self):
        self.pack(b'bundle', sidecar='0' * 64)
        warning = self.run_import()
        self.assertIn('DESKTOP_RUNTIME_WARNING', warning)
        self.assertIn('checksum', warning)
        self.assertEqual(self.imported, [])
        self.assertFalse((self.state / 'bundle-imported.sha256').exists())
        self.pack(b'bundle')
        self.assertEqual(self.run_import(), '')
        self.assertEqual(self.imported, [hashlib.sha256(b'bundle').hexdigest()])

    def test_failed_import_leaves_a_marker_that_the_next_successful_start_removes(self):
        failed = self.state / 'bundle-import-failed'
        self.assertEqual(self.run_import(), '')  # no pack: no marker either
        self.assertFalse(failed.exists())
        self.pack(b'bundle', sidecar='0' * 64)
        self.run_import()
        reason = failed.read_text(encoding='utf-8')
        self.assertTrue(reason.startswith('ValueError: ') and 'checksum' in reason and reason.count('\n') == 1, reason)
        self.assertFalse((self.state / 'bundle-imported.sha256').exists())
        self.pack(b'bundle')
        self.assertEqual(self.run_import(), '')
        self.assertFalse(failed.exists())
        self.assertEqual(self.imported, [hashlib.sha256(b'bundle').hexdigest()])

    def test_marker_is_cleared_when_the_bundle_was_already_imported_and_failures_never_raise(self):
        failed = self.state / 'bundle-import-failed'
        self.pack(b'bundle')
        self.run_import()
        failed.write_text('stale\n', encoding='utf-8')
        self.assertEqual(self.run_import(), '')  # sidecar already imported: nothing to do, but the stale failure is gone
        self.assertFalse(failed.exists())
        self.assertEqual(len(self.imported), 1)
        (self.data / 'bundle.zip.sha256').unlink()  # unverifiable pack
        warning = self.run_import()
        self.assertIn('DESKTOP_RUNTIME_WARNING', warning)
        self.assertTrue(failed.read_text(encoding='utf-8').startswith('FileNotFoundError: '))
        # a state folder that cannot take the marker still only warns
        failed.unlink()
        failed.mkdir()
        self.assertIn('DESKTOP_RUNTIME_WARNING', self.run_import())


class GitHubDistrictArchiveTests(unittest.TestCase):
    def setUp(self):
        import sqlite3
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / 'sources'
        self.source.mkdir()
        self.archive = self.root / 'district.zip'
        self.output = self.root / 'cache'
        database = self.root / 'fixture.sqlite3'
        with contextlib.closing(sqlite3.connect(database)) as connection:
            connection.execute('CREATE TABLE metadata(key TEXT, value TEXT)')
            connection.commit()
        self.content = {name + '.sqlite3': database.read_bytes() for name in ('contacts', 'roads', 'scores', 'street', 'walk')}
        self.content['density.json'] = b'{}\n'

    def pack(self, extras=()):
        import zipfile
        with zipfile.ZipFile(self.archive, 'w', compression=zipfile.ZIP_DEFLATED) as zipped:
            for name, content in self.content.items():
                zipped.writestr('co-05/data/' + name, content)
            for name, content in extras:
                if isinstance(name, str) and '\\' in name:
                    # ZipInfo's constructor normalizes OS separators on Windows;
                    # assign the raw name afterward to exercise the actual guard.
                    raw_name = name
                    name = zipfile.ZipInfo('fixture')
                    name.filename = raw_name
                zipped.writestr(name, content)

    def request(self):
        return {'archive': str(self.archive), 'destination': str(self.output), 'sourceRoot': str(self.source), 'district': 'co-05',
                'files': [{'role': name, 'path': 'co-05/data/' + name, 'bytes': len(content), 'sha256': hashlib.sha256(content).hexdigest()}
                          for name, content in self.content.items()]}

    def extract(self, request=None, check=lambda *_: None):
        return backend.extract_github_district_archive(request or self.request(), check=check)

    def test_extracts_only_six_roles_then_validates_source_metadata(self):
        self.pack([('co-05/sources/example.csv', b'invented fixture')])
        checked = []
        result = self.extract(check=lambda *args: checked.append(args))
        self.assertTrue(result['ok'])
        self.assertTrue(result['sourceMetadataAccepted'])
        self.assertEqual({path.name for path in self.output.iterdir()}, set(self.content))
        self.assertEqual(checked, [(self.source, self.output, 'co-05')])
        for name, content in self.content.items():
            self.assertEqual((self.output / name).read_bytes(), content)

    def test_wrong_checksum_removes_only_new_cache(self):
        self.pack()
        request = self.request()
        request['files'][0]['sha256'] = '0' * 64
        original = (self.root / 'accounts.sqlite3')
        original.write_bytes(b'private account fixture')
        with self.assertRaises(ValueError):
            self.extract(request)
        self.assertFalse(self.output.exists())
        self.assertEqual(original.read_bytes(), b'private account fixture')

    def test_unsafe_ignored_zip_paths_and_duplicate_names_are_rejected(self):
        for name in ('../outside', '/absolute', 'co-05/../outside', 'co-05/data\\outside', 'ct-01/data/extra', 'co-05/data/STREET.SQLITE3'):
            with self.subTest(name=name):
                self.pack([(name, b'bad')])
                with self.assertRaises(ValueError):
                    self.extract()
                self.assertFalse(self.output.exists())

    def test_symlink_entries_are_rejected_even_when_not_selected(self):
        import stat
        import zipfile
        entry = zipfile.ZipInfo('co-05/data/shortcut')
        entry.create_system = 3
        entry.external_attr = (stat.S_IFLNK | 0o777) << 16
        self.pack([(entry, b'outside')])
        with self.assertRaises(ValueError):
            self.extract()
        self.assertFalse(self.output.exists())

    def test_existing_account_directory_is_never_overwritten(self):
        self.pack()
        self.output.mkdir()
        account = self.output / 'accounts.sqlite3'
        account.write_bytes(b'private state')
        with self.assertRaises(ValueError):
            self.extract()
        self.assertEqual(account.read_bytes(), b'private state')

    def test_signed_wal_header_is_not_accepted_without_sidecars(self):
        self.content['street.sqlite3'] = self.content['street.sqlite3'][:18] + b'\x02\x02' + self.content['street.sqlite3'][20:]
        self.pack()
        with self.assertRaises(ValueError):
            self.extract()
        self.assertFalse(self.output.exists())

    def test_signed_size_mismatch_and_metadata_rejection_leave_no_cache(self):
        self.pack()
        request = self.request()
        request['files'][0]['bytes'] += 1
        with self.assertRaises(ValueError):
            self.extract(request)
        self.assertFalse(self.output.exists())
        def reject(*_):
            raise ValueError('Wrong district source metadata')
        with self.assertRaises(ValueError):
            self.extract(check=reject)
        self.assertFalse(self.output.exists())

    def test_explicit_github_owner_state_retains_existing_secret(self):
        owner_spec = importlib.util.spec_from_file_location('terrier_owner_fixture', Path(__file__).with_name('terrier_owner.py'))
        owner = importlib.util.module_from_spec(owner_spec)
        owner_spec.loader.exec_module(owner)
        state = self.root / 'owner-state'
        state.mkdir()
        (state / 'accounts.sqlite3').write_bytes(b'private existing accounts')
        (state / '.secret').write_text('existing secret')
        self.output.mkdir()
        with self.assertRaises(ValueError):
            owner.legacy_secret_path(self.output, state, 'admin')
        self.assertEqual(owner.legacy_secret_path(self.output, state, 'admin', separate_data=True), state / '.secret')
        self.assertEqual((state / '.secret').read_text(), 'existing secret')

    def test_extraction_probe_does_not_enter_server_startup(self):
        with patch('sys.argv', ['desktop_backend', '--desktop-github-district-extract']), patch.object(backend, 'github_district_extract_main', return_value=0) as probe, patch.object(backend, 'run') as run:
            self.assertEqual(backend.main(), 0)
        probe.assert_called_once()
        run.assert_not_called()

    def test_metadata_probe_does_not_enter_server_startup(self):
        with patch('sys.argv', ['desktop_backend', '--desktop-github-district-check']), patch('terrier_district.main', return_value=0) as probe, patch.object(backend, 'run') as run:
            self.assertEqual(backend.main(), 0)
        probe.assert_called_once_with(github_runtime=True)
        run.assert_not_called()


if __name__ == '__main__':
    unittest.main()

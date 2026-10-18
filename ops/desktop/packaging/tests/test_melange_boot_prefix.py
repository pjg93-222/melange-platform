"""The app-owned mlk-boot.js desktop prefix reaches a selected workspace generated before it existed,
at serve time, without writing into that workspace. Synthetic temporary workspace only."""
import gzip
import hashlib
import http.client
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

PACKAGING = Path(__file__).resolve().parents[1]
REPO = PACKAGING.parents[2]
sys.path[:0] = [str(PACKAGING), str(REPO)]
from desktop_backend import guarded_handler  # noqa: E402
import fec.surfaces.staffer_live_server as live  # noqa: E402

TOKEN = 'fixture-only-launch-token'
OLD_LOADER = b'/* mlk-boot.js: fixture loader generated before the desktop prefix */\nwindow.fixtureBoot = 1;\n'


class BootPrefixDelivery(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.approved = Path(tmp.name) / 'workspace' / 'fec' / 'surfaces' / 'staffer_live' / 'approved'
        self.approved.mkdir(parents=True)
        (self.approved / 'mlk-boot.js').write_bytes(OLD_LOADER)
        (self.approved / 'app.js').write_bytes(b'window.app = 1;\n')
        (self.approved.parent / 'secret.js').write_bytes(b'outside approved\n')
        for patcher in (mock.patch.object(live, 'ASSETS', self.approved.parent),):
            patcher.start()
            self.addCleanup(patcher.stop)
        live._approved_cache.clear()
        self.addCleanup(live._approved_cache.clear)
        self.server = live.WorkspaceServer(('127.0.0.1', 0), guarded_handler(live.Handler, TOKEN, 'melange'))
        self.server.dashboard = SimpleNamespace()
        self.addCleanup(self.server.server_close)
        worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(worker.join, 2)
        self.addCleanup(self.server.shutdown)

    def get(self, route, token=TOKEN, host=None, gzip_ok=False):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            headers = {'X-Desktop-Token': token} if token else {}
            if host:
                headers['Host'] = host
            if gzip_ok:
                headers['Accept-Encoding'] = 'gzip'
            connection.request('GET', route, headers=headers)
            response = connection.getresponse()
            return response.status, response.read(), dict(response.getheaders())
        finally:
            connection.close()

    def test_a_workspace_with_the_pre_prefix_loader_is_served_exactly_the_app_loader(self):
        app_loader = live.BOOT_PREFIX_SOURCE.read_bytes()
        body = app_loader[app_loader.index(b'/* mlk-boot.js:'):]            # the loader as published before the prefix
        self.assertNotIn(live.BOOT_PREFIX_MARKER, body)
        (self.approved / 'mlk-boot.js').write_bytes(body)
        status, served, headers = self.get('/staffer-live/approved/mlk-boot.js')
        self.assertEqual(status, 200)
        self.assertEqual(served, app_loader)                               # prefix + workspace body == app file
        self.assertEqual(headers['Content-Type'], 'application/javascript; charset=utf-8')

    def test_a_nested_workspace_approved_clean_loader_gets_no_prefix(self):
        nested = self.approved / 'clean' / 'mlk-boot.js'
        nested.parent.mkdir()
        nested.write_bytes(OLD_LOADER)
        status, served, headers = self.get('/staffer-live/approved/clean/mlk-boot.js')
        self.assertEqual((status, served), (200, OLD_LOADER))
        self.assertEqual(headers['Content-Type'], 'application/javascript; charset=utf-8')

    def test_an_old_workspace_loader_is_served_with_the_prefix_and_never_rewritten(self):
        boot = self.approved / 'mlk-boot.js'
        before = (boot.read_bytes(), boot.stat().st_mtime_ns)
        status, body, headers = self.get('/staffer-live/approved/mlk-boot.js')
        self.assertEqual(status, 200)
        self.assertEqual(body, live.boot_prefix() + OLD_LOADER)
        self.assertEqual(headers['Content-Type'], 'application/javascript; charset=utf-8')
        self.assertEqual(headers['ETag'], '"clean-' + hashlib.sha256(body).hexdigest()[:24] + '"')
        status, zipped, headers = self.get('/staffer-live/approved/mlk-boot.js', gzip_ok=True)
        self.assertEqual((status, headers.get('Content-Encoding')), (200, 'gzip'))
        self.assertEqual(gzip.decompress(zipped), body)
        self.assertEqual((boot.read_bytes(), boot.stat().st_mtime_ns), before)
        self.assertEqual(sorted(p.name for p in self.approved.iterdir()), ['app.js', 'mlk-boot.js'])

    def test_a_workspace_loader_that_already_has_a_prefix_is_served_byte_identical(self):
        own = b'/* user copy */ Object.defineProperty(window, "__melangeDesktopStorage", {value: true});\n' + OLD_LOADER
        (self.approved / 'mlk-boot.js').write_bytes(own)
        status, body, _ = self.get('/staffer-live/approved/mlk-boot.js')
        self.assertEqual((status, body), (200, own))

    def test_other_assets_are_unchanged_and_absent_app_copy_leaves_the_loader_as_is(self):
        self.assertEqual(self.get('/staffer-live/approved/app.js')[:2], (200, b'window.app = 1;\n'))
        with mock.patch.object(live, 'BOOT_PREFIX_SOURCE', self.approved.parent / 'missing.js'):
            live._approved_cache.clear()
            self.assertEqual(self.get('/staffer-live/approved/mlk-boot.js')[:2], (200, OLD_LOADER))

    def test_token_host_and_traversal_refusals_still_hold(self):
        route = '/staffer-live/approved/mlk-boot.js'
        self.assertEqual(self.get(route, token=None)[0], 403)
        self.assertEqual(self.get(route, token='wrong-token')[0], 403)
        self.assertEqual(self.get(route, host='forged.invalid')[0], 403)
        for escape in ('/staffer-live/approved/../secret.js', '/staffer-live/approved/%2e%2e/secret.js',
                       '/staffer-live/approved/..%5csecret.js'):
            self.assertEqual(self.get(escape)[0], 404, escape)


class PackagedAppCopy(unittest.TestCase):
    def test_the_runtime_manifest_ships_the_app_copy_despite_the_approved_exclusion(self):
        spec = json.loads((PACKAGING / 'runtime-manifest.json').read_text(encoding='utf-8'))['apps']['melange']
        self.assertIn('fec/surfaces/approved', spec['excludedSourcePrefixes'])
        self.assertIn('fec/surfaces/approved/clean/mlk-boot.js', spec['sourceFiles'])
        self.assertEqual(Path(live.__file__).resolve().parent / 'approved' / 'clean' / 'mlk-boot.js',
                         live.BOOT_PREFIX_SOURCE.resolve())


if __name__ == '__main__':
    unittest.main()

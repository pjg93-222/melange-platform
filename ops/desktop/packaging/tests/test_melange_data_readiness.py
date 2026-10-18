"""Fatal model startup reaches the existing UI error state without exposing data."""
import http.client
import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest

PACKAGING = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(PACKAGING), str(PACKAGING.parents[2])]
from desktop_backend import guarded_handler
from fec.surfaces.staffer_live_server import Handler, WorkspaceServer


class ModelReadinessTests(unittest.TestCase):
    def test_failure_loading_and_authorization_are_distinct(self):
        token = 'fixture-only-launch-token'
        server = WorkspaceServer(('127.0.0.1', 0), guarded_handler(Handler, token, 'melange'))
        self.addCleanup(server.server_close)
        server.dashboard = SimpleNamespace(startup_error='private evidence contents and path',
            network_index=lambda *_args, **_kwargs: (b'{"ready":false,"stage":"Loading fixture"}', None),
            groups_index=lambda *_args, **_kwargs: (b'{"ready":false,"stage":"Loading fixture"}', None))
        worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
        self.addCleanup(worker.join, 2)
        self.addCleanup(server.shutdown)

        def get(route, authenticated=True, host=None):
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
            try:
                headers = {'X-Desktop-Token': token} if authenticated else {}
                if host: headers['Host'] = host
                connection.request('GET', route, headers=headers)
                response = connection.getresponse()
                return response.status, response.read()
            finally:
                connection.close()

        for route in ('/api/staffer-network-index', '/api/staffer-groups-index'):
            with self.subTest(route=route):
                status, body = get(route)
                self.assertEqual(status, 503)
                self.assertEqual(json.loads(body)['code'], 'model_unavailable')
                self.assertNotIn(b'private evidence', body)
                self.assertEqual(get(route, authenticated=False)[0], 403)
                self.assertEqual(get(route, host='forged.invalid')[0], 403)
        server.dashboard.startup_error = None
        for route in ('/api/staffer-network-index', '/api/staffer-groups-index'):
            status, body = get(route)
            self.assertEqual(status, 200)
            self.assertFalse(json.loads(body)['ready'])
            self.assertEqual(json.loads(body)['stage'], 'Loading fixture')
        server.dashboard.network_index = lambda *_args, **_kwargs: (b'{"ready":true,"total":7}', None)
        server.dashboard.groups_index = lambda *_args, **_kwargs: (b'{"ready":true,"counts":{"offices":2}}', None)
        for route in ('/api/staffer-network-index', '/api/staffer-groups-index'):
            status, body = get(route)
            self.assertEqual(status, 200)
            self.assertTrue(json.loads(body)['ready'])
        # An ancillary failure after the model loaded (prewarm/background threads) must not hide it.
        server.dashboard.ready = True
        server.dashboard.startup_error = 'private ancillary thread failure'
        for route in ('/api/staffer-network-index', '/api/staffer-groups-index'):
            with self.subTest(route=route, ready=True):
                status, body = get(route)
                self.assertEqual(status, 200)
                self.assertTrue(json.loads(body)['ready'])
                self.assertNotIn(b'private', body)


if __name__ == '__main__': unittest.main()

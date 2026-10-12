"""Fast session and conditional delivery retain local HTTP boundaries."""
import gzip
import http.client
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from test_contribution_http import app


class ConsoleDeliveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.console = SimpleNamespace(token='local-fixture-token', read_only=True,
                                      report_directory=Path(cls.temp.name), summary=Mock(side_effect=AssertionError('No collection scan during handshake')))
        cls.server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        cls.server.app = cls.console
        cls.worker = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.worker.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.worker.join(timeout=5)
        cls.temp.cleanup()

    def request(self, path, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            connection.request('GET', path, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_light_handshake_avoids_collection_and_credentials(self):
        with patch.object(app, 'key_status', side_effect=AssertionError('No key access')):
            status, headers, body = self.request('/api/session?light=1')
        value = json.loads(body)
        self.assertEqual((status, value['token'], value['read_only']), (200, self.console.token, True))
        self.assertNotIn('runs', value)
        self.assertEqual(headers['Cache-Control'], 'no-store')
        status, _, _ = self.request('/api/session?light=1', {'Host':'untrusted.example'})
        self.assertEqual(status, 403)

    def test_assets_revalidate_and_gzip_without_caching_api_token(self):
        status, headers, compressed = self.request('/chart-studio-core.js', {'Accept-Encoding':'gzip'})
        self.assertEqual((status, headers['Content-Encoding']), (200, 'gzip'))
        self.assertEqual(headers['Cache-Control'], 'private, no-cache')
        raw = gzip.decompress(compressed)
        self.assertGreater(len(raw), len(compressed))
        status, unchanged, body = self.request('/chart-studio-core.js', {'If-None-Match':headers['ETag']})
        self.assertEqual((status, body, unchanged['ETag']), (304, b'', headers['ETag']))
        status, fresh, body = self.request('/chart-studio-core.js', {'If-None-Match':'W/"older"', 'Accept-Encoding':'gzip;q=0'})
        self.assertEqual((status, body), (200, raw))
        self.assertNotIn('Content-Encoding', fresh)

    def test_large_json_is_compressed_only_when_accepted(self):
        fixture = {'rows':[{'zip5':'02134', 'amount':-12345, 'label':'same'} for _ in range(500)]}
        with patch.object(app.contribution_geo, 'geo_catalog', return_value=fixture):
            status, headers, body = self.request('/api/contributions/geography/catalog', {'Accept-Encoding':'gzip'})
        self.assertEqual(status, 200)
        # The catalogue now echoes the request industry scope (absent here).
        self.assertEqual(json.loads(gzip.decompress(body)), {**fixture, 'industry': None})
        self.assertEqual(headers['Cache-Control'], 'no-store')

    def test_the_retired_comparison_script_is_no_longer_served(self):
        # The N charts strip was removed on 2026-09-19. A page held in a cache
        # still asks for its script, and a 404 is the answer that keeps the
        # strip gone; a silently served file would bring it back.
        status, _, _ = self.request('/chart-comparison.js')
        self.assertEqual(status, 404)

    def test_changed_asset_content_gets_new_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'asset.js'; path.write_bytes(b'first')
            def read():
                stat = path.stat()
                return app.static_asset((str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
            first = read(); path.write_bytes(b'updated content'); second = read()
            self.assertNotEqual(first[1], second[1])
            self.assertEqual(second[0], b'updated content')

    def test_inaccessible_keys_do_not_hide_reports_or_assert_zero_keys(self):
        console = app.Console.__new__(app.Console)
        console.root = self.console.report_directory
        console.report_directory = self.console.report_directory
        console.read_only = True
        console.runs = Mock(return_value=[])
        console.coverage_catalog = Mock(return_value=[])
        with patch.object(app, 'key_status', side_effect=RuntimeError('fixture encrypted store unavailable')):
            value = console.summary()
        self.assertFalse(value['key_status_available'])
        self.assertIsNone(value['active_key_count'])
        self.assertIn('Report analysis remains available', value['key_status_message'])
        self.assertEqual(value['runs'], [])


if __name__ == '__main__': unittest.main()

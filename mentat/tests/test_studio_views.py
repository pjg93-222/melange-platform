"""Saved Chart Studio views over HTTP, against a real Console on a temporary data root.

A real Console is used because the contract is partly about files: a view saved
here has to be a file on disk that another console opened on the same data root
can list. Nothing else about the console is real -- the report directory is a
temporary empty folder, patched in for the duration of construction only, and no
report, collector or key store is touched (read_only=True at construction skips
the key save; the flag is lowered afterwards for the write tests).
"""
import http.client
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1] / "app"
spec = importlib.util.spec_from_file_location("studio_views_server", APP / "server.py")
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

READ_ONLY_REFUSAL = 'Read-only preview. Save views from the main console.'


def sheet(name='Recipients'):
    return {'name': name, 'charts': [{'kind': 'bar', 'field': 'amount'}]}


class StudioViewsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.data_root = Path(cls.temp.name) / 'data'
        cls.report_root = Path(cls.temp.name) / 'reports'
        cls.data_root.mkdir()
        cls.report_root.mkdir()
        with patch.object(app, 'report_directory', return_value=cls.report_root):
            cls.console = app.Console(str(cls.data_root), read_only=True)
        cls.console.read_only = False
        cls.server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        cls.server.app = cls.console
        cls.console.port = cls.server.server_port
        cls.worker = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.worker.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.worker.join(timeout=5)
        cls.temp.cleanup()

    def setUp(self):
        self.console.read_only = False
        self.views = self.console.root / 'studio' / 'views'
        if self.views.is_dir():
            for path in self.views.glob('*.json'):
                path.unlink()

    def request(self, path, *, method='GET', data=None, raw=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        body = raw if raw is not None else (json.dumps(data).encode() if data is not None else None)
        headers = {'Host': '127.0.0.1:{}'.format(self.server.server_port)}
        if method == 'POST':
            headers.update({'X-Mentat-Token': self.console.token, 'Content-Type': 'application/json'})
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            content = response.read()
            parsed = json.loads(content) if response.getheader('Content-Type') == 'application/json' else content
            return response.status, dict(response.getheaders()), parsed
        finally:
            connection.close()

    def save(self, **overrides):
        payload = {'name': 'Ride B', 'sourceKey': 'Banking|2025-2026', 'source': {'dataset': 'Banking'},
                   'sheets': [sheet('Recipients'), sheet('Cycles')], 'active': 1}
        payload.update(overrides)
        return self.request('/api/studio/views', method='POST', data=payload)

    def test_a_saved_view_round_trips_through_a_file_on_disk(self):
        status, _, body = self.save()
        self.assertEqual(status, 200)
        self.assertRegex(body['id'], '^[a-f0-9]{32}$')
        self.assertGreater(body['saved_at'], 0)
        stored = json.loads((self.views / (body['id'] + '.json')).read_text(encoding='utf-8'))
        self.assertEqual(stored['name'], 'Ride B')
        self.assertEqual([entry['name'] for entry in stored['sheets']], ['Recipients', 'Cycles'])
        self.assertEqual(stored['active'], 1)

        status, _, listed = self.request('/api/studio/views')
        self.assertEqual(status, 200)
        self.assertEqual([view['id'] for view in listed['views']], [body['id']])
        entry = listed['views'][0]
        self.assertEqual((entry['name'], entry['sheet_count'], entry['sourceKey']),
                         ('Ride B', 2, 'Banking|2025-2026'))
        self.assertEqual(entry['source'], {'dataset': 'Banking'})
        self.assertNotIn('sheets', entry)

        status, _, one = self.request('/api/studio/views?id=' + body['id'])
        self.assertEqual(status, 200)
        self.assertEqual(one['view']['id'], body['id'])
        self.assertEqual([entry['name'] for entry in one['view']['sheets']], ['Recipients', 'Cycles'])
        self.assertEqual(one['view']['active'], 1)
        self.assertEqual(one['view']['source'], {'dataset': 'Banking'})

    def test_the_list_is_newest_first(self):
        identifiers = [self.save(name='View {}'.format(index))[2]['id'] for index in range(3)]
        # Pin the timestamps: the ordering contract must not rest on clock resolution.
        for offset, identifier in enumerate(identifiers):
            path = self.views / (identifier + '.json')
            value = json.loads(path.read_text(encoding='utf-8'))
            value['saved_at'] = 1700000000 + offset
            path.write_text(json.dumps(value), encoding='utf-8')
        _, _, listed = self.request('/api/studio/views')
        self.assertEqual([view['id'] for view in listed['views']], list(reversed(identifiers)))

    def test_duplicate_names_are_allowed(self):
        first, second = self.save()[2]['id'], self.save()[2]['id']
        self.assertNotEqual(first, second)
        _, _, listed = self.request('/api/studio/views')
        self.assertEqual([view['name'] for view in listed['views']], ['Ride B', 'Ride B'])

    def test_an_unknown_view_is_404_and_a_malformed_identifier_is_400(self):
        status, _, body = self.request('/api/studio/views?id=' + 'a' * 32)
        self.assertEqual(status, 404)
        self.assertIn('error', body)
        for bad in ('nope', 'A' * 32, 'a' * 31, 'a' * 33, '../server.py'):
            with self.subTest(identifier=bad):
                self.assertEqual(self.request('/api/studio/views?id=' + bad)[0], 400)

    def test_delete_removes_the_file_and_repeating_it_is_still_success(self):
        identifier = self.save()[2]['id']
        path = self.views / (identifier + '.json')
        self.assertTrue(path.is_file())
        status, _, body = self.request('/api/studio/views/delete', method='POST', data={'id': identifier})
        self.assertEqual((status, body), (200, {'deleted': True}))
        self.assertFalse(path.exists())
        self.assertEqual(self.request('/api/studio/views')[2], {'views': []})
        # Deleting a view that is already gone is success: the postcondition holds.
        status, _, body = self.request('/api/studio/views/delete', method='POST', data={'id': identifier})
        self.assertEqual((status, body), (200, {'deleted': True}))
        self.assertEqual(self.request('/api/studio/views/delete', method='POST', data={'id': 'nope'})[0], 400)

    def test_every_stated_limit_is_refused_and_nothing_is_stored(self):
        for label, overrides in (('empty name', {'name': '   '}),
                                 ('name over 80', {'name': 'x' * 81}),
                                 ('no sheets', {'sheets': []}),
                                 ('25 sheets', {'sheets': [sheet('S{}'.format(index)) for index in range(25)]}),
                                 ('sheet without a name', {'sheets': [{'charts': []}]}),
                                 ('sheet is not an object', {'sheets': ['Recipients']}),
                                 ('sourceKey is not a string', {'sourceKey': 7}),
                                 ('source is not an object', {'source': 'Banking'}),
                                 ('active is not a number', {'active': 'first'}),
                                 ('active is a boolean', {'active': True})):
            with self.subTest(limit=label):
                status, _, body = self.save(**overrides)
                self.assertEqual(status, 400)
                self.assertIn('error', body)
        self.assertEqual(self.request('/api/studio/views')[2], {'views': []})
        # The boundary values themselves are inside the limits, not outside them.
        self.assertEqual(self.save(name='x' * 80)[0], 200)
        self.assertEqual(self.save(sheets=[sheet('S{}'.format(index)) for index in range(24)])[0], 200)
        self.assertEqual(self.save(active=0)[0], 200)

    def test_a_payload_over_512_kb_is_refused_before_anything_is_written(self):
        payload = {'name': 'Huge', 'sourceKey': 'Banking', 'source': {}, 'active': 0,
                   'sheets': [{'name': 'Sheet', 'blob': 'x' * 600000}]}
        raw = json.dumps(payload).encode()
        self.assertGreater(len(raw), 512 * 1024)
        status, _, body = self.request('/api/studio/views', method='POST', raw=raw)
        self.assertEqual(status, 400)
        self.assertIn('error', body)
        self.assertEqual(self.request('/api/studio/views')[2], {'views': []})
        payload['sheets'][0]['blob'] = 'x' * 400000
        raw = json.dumps(payload).encode()
        self.assertLess(len(raw), 512 * 1024)
        self.assertEqual(self.request('/api/studio/views', method='POST', raw=raw)[0], 200)

    def test_a_read_only_console_refuses_the_writes_and_still_answers_the_reads(self):
        identifier = self.save()[2]['id']
        self.console.read_only = True
        for path in ('/api/studio/views', '/api/studio/views/delete'):
            with self.subTest(path=path):
                status, _, body = self.request(path, method='POST',
                                               data={'id': identifier, 'name': 'Renamed'})
                self.assertEqual(status, 403)
                self.assertEqual(body, {'error': READ_ONLY_REFUSAL})
        status, _, listed = self.request('/api/studio/views')
        self.assertEqual(status, 200)
        self.assertEqual([view['id'] for view in listed['views']], [identifier])
        self.assertEqual(self.request('/api/studio/views?id=' + identifier)[0], 200)
        self.assertTrue((self.views / (identifier + '.json')).is_file())


if __name__ == '__main__':
    unittest.main()

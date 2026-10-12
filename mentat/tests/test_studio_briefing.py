"""The briefing tray over HTTP, against a real Console on a temporary data root.

The tray is a file contract as much as an HTTP one: an ordered index.json plus
one SVG per item, shared by every console on the data root. So these run against
a real Console with a temporary root; no report, collector or key store is read.
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
spec = importlib.util.spec_from_file_location("studio_briefing_server", APP / "server.py")
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

READ_ONLY_REFUSAL = 'Read-only preview. Briefing changes are not saved.'
ITEM_FIELDS = ('id', 'title', 'scope', 'chip', 'note', 'basis', 'sheetId', 'sheetName', 'source',
               'provenance', 'width', 'height', 'added_at', 'origin')


class StudioBriefingTests(unittest.TestCase):
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
        self.tray = self.console.root / 'studio' / 'briefing'
        if self.tray.is_dir():
            for path in list(self.tray.glob('*.svg')) + list(self.tray.glob('*.json')):
                path.unlink()

    def request(self, path, *, method='GET', data=None, raw=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=20)
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

    def add(self, **overrides):
        payload = {'title': 'Published included receipts by Recipient category',
                   'scope': 'Banking - Cycle 2025-2026 - Bank of America NA - Workbook SHA256 '
                            + 'a' * 64 + ' - Groups 1-5 of 5',
                   'chip': 'Banking - Cycle 2025-2026 - Bank of America NA',
                   'note': 'Top five categories', 'basis': 'Published included receipts',
                   'sheetId': 'sheet-1', 'sheetName': 'Recipients',
                   'source': {'workbook': 'Banking.xlsx'},
                   'provenance': {'workbook_sha256': 'a' * 64}, 'width': 960, 'height': 540,
                   'origin': 'chart-studio', 'svg': '<svg width="960" height="540"><rect/></svg>'}
        payload.update(overrides)
        return self.request('/api/studio/briefing/add', method='POST', data=payload)

    def test_an_added_item_is_listed_with_every_contracted_field_and_stored_as_a_file(self):
        status, _, body = self.add()
        self.assertEqual(status, 200)
        self.assertRegex(body['id'], '^[a-f0-9]{32}$')
        self.assertGreater(body['added_at'], 0)
        self.assertTrue((self.tray / (body['id'] + '.svg')).is_file())
        index = json.loads((self.tray / 'index.json').read_text(encoding='utf-8'))
        self.assertEqual([item['id'] for item in index['items']], [body['id']])

        status, _, listed = self.request('/api/studio/briefing')
        self.assertEqual(status, 200)
        item = listed['items'][0]
        self.assertEqual(sorted(item), sorted(ITEM_FIELDS))
        self.assertEqual(item['title'], 'Published included receipts by Recipient category')
        self.assertEqual(item['sheetName'], 'Recipients')
        self.assertEqual(item['chip'], 'Banking - Cycle 2025-2026 - Bank of America NA')
        self.assertEqual(item['provenance'], {'workbook_sha256': 'a' * 64})
        self.assertEqual((item['width'], item['height']), (960, 540))
        self.assertEqual(item['origin'], 'chart-studio')

    def test_the_short_scope_chip_survives_the_round_trip(self):
        """Every reader-facing surface prints the chip first and the full scope only
        as a fallback - the drawer card, the printed sheet, the HTML bundle and the
        slide notes. A tray stored without it comes back with the whole provenance
        string where a one-line scope belongs, which is what a second console, a
        reload, or the mirror console reads."""
        identifier = self.add()[2]['id']
        item = next(entry for entry in self.request('/api/studio/briefing')[2]['items']
                    if entry['id'] == identifier)
        self.assertEqual(item['chip'], 'Banking - Cycle 2025-2026 - Bank of America NA')
        self.assertNotEqual(item['chip'], item['scope'])
        self.assertLess(len(item['chip']), len(item['scope']))
        stored = json.loads((self.tray / 'index.json').read_text(encoding='utf-8'))
        self.assertEqual([entry['chip'] for entry in stored['items']],
                         ['Banking - Cycle 2025-2026 - Bank of America NA'])

    def test_an_item_sent_without_a_chip_still_lists_one_field_rather_than_none(self):
        identifier = self.add(chip='')[2]['id']
        item = next(entry for entry in self.request('/api/studio/briefing')[2]['items']
                    if entry['id'] == identifier)
        self.assertEqual(item['chip'], '')
        self.assertEqual(sorted(item), sorted(ITEM_FIELDS))

    def test_the_stored_chart_comes_back_as_text_not_as_an_image_document(self):
        identifier = self.add()[2]['id']
        status, headers, body = self.request('/api/studio/briefing/item?id=' + identifier)
        self.assertEqual(status, 200)
        # text/plain, never image/svg+xml: an SVG served as an image type from
        # this origin would be script-capable, and this one is assembled from
        # report text. nosniff keeps the browser from upgrading it.
        self.assertEqual(headers['Content-Type'], 'text/plain; charset=utf-8')
        self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(body.decode('utf-8'), '<svg width="960" height="540"><rect/></svg>')
        self.assertEqual(self.request('/api/studio/briefing/item?id=' + 'a' * 32)[0], 404)
        self.assertEqual(self.request('/api/studio/briefing/item?id=nope')[0], 400)

    def test_the_chart_has_to_be_an_svg_string_under_six_megabytes(self):
        for label, value in (('missing', None), ('not a string', {'svg': True}),
                             ('empty', ''), ('html', '<div></div>'),
                             ('leading whitespace', ' <svg></svg>')):
            with self.subTest(chart=label):
                status, _, body = self.add(svg=value)
                self.assertEqual(status, 400)
                self.assertIn('error', body)
        oversize = '<svg' + 'x' * (6 * 1024 * 1024)
        self.assertGreater(len(oversize.encode('utf-8')), 6 * 1024 * 1024)
        status, _, body = self.add(svg=oversize)
        self.assertEqual(status, 400)
        self.assertIn('6 MB', body['error'])
        self.assertEqual(self.request('/api/studio/briefing')[2], {'items': []})

    def test_the_forty_first_item_is_a_conflict_not_a_bad_request(self):
        identifiers = [self.add(title='Item {}'.format(index))[2]['id'] for index in range(40)]
        self.assertEqual(len(set(identifiers)), 40)
        status, _, body = self.add(title='Item 41')
        self.assertEqual(status, 409)
        self.assertIn('error', body)
        self.assertEqual(len(self.request('/api/studio/briefing')[2]['items']), 40)
        # Removing one makes room again.
        self.assertEqual(self.request('/api/studio/briefing/remove', method='POST',
                                      data={'id': identifiers[0]})[0], 200)
        self.assertEqual(self.add(title='Item 41')[0], 200)
        self.assertEqual(len(self.request('/api/studio/briefing')[2]['items']), 40)

    def test_remove_drops_the_item_and_its_chart_and_repeats_harmlessly(self):
        first, second = self.add(title='First')[2]['id'], self.add(title='Second')[2]['id']
        status, _, body = self.request('/api/studio/briefing/remove', method='POST', data={'id': first})
        self.assertEqual((status, body), (200, {'removed': True}))
        self.assertFalse((self.tray / (first + '.svg')).exists())
        self.assertEqual([item['id'] for item in self.request('/api/studio/briefing')[2]['items']], [second])
        status, _, body = self.request('/api/studio/briefing/remove', method='POST', data={'id': first})
        self.assertEqual((status, body), (200, {'removed': True}))
        self.assertEqual(self.request('/api/studio/briefing/remove', method='POST', data={'id': 'nope'})[0], 400)

    def test_order_accepts_a_permutation_and_refuses_anything_else(self):
        identifiers = [self.add(title='Item {}'.format(index))[2]['id'] for index in range(3)]
        shuffled = [identifiers[2], identifiers[0], identifiers[1]]
        status, _, body = self.request('/api/studio/briefing/order', method='POST', data={'ids': shuffled})
        self.assertEqual((status, body), (200, {'ok': True}))
        self.assertEqual([item['id'] for item in self.request('/api/studio/briefing')[2]['items']], shuffled)
        self.assertEqual([item['title'] for item in self.request('/api/studio/briefing')[2]['items']],
                         ['Item 2', 'Item 0', 'Item 1'])
        for label, ids in (('one missing', identifiers[:2]),
                           ('a duplicate', [identifiers[0], identifiers[0], identifiers[1]]),
                           ('an unknown id', [identifiers[0], identifiers[1], 'b' * 32]),
                           ('one too many', identifiers + ['b' * 32]),
                           ('not a list', 'everything'),
                           ('not strings', [1, 2, 3])):
            with self.subTest(order=label):
                status, _, body = self.request('/api/studio/briefing/order', method='POST', data={'ids': ids})
                self.assertEqual(status, 400)
                self.assertIn('error', body)
        # A refused reorder left the tray exactly as the accepted one left it.
        self.assertEqual([item['id'] for item in self.request('/api/studio/briefing')[2]['items']], shuffled)

    def test_a_read_only_console_refuses_the_writes_and_still_answers_the_reads(self):
        identifier = self.add()[2]['id']
        self.console.read_only = True
        for path, payload in (('/api/studio/briefing/add', {'svg': '<svg></svg>'}),
                              ('/api/studio/briefing/remove', {'id': identifier}),
                              ('/api/studio/briefing/order', {'ids': [identifier]})):
            with self.subTest(path=path):
                status, _, body = self.request(path, method='POST', data=payload)
                self.assertEqual(status, 403)
                self.assertEqual(body, {'error': READ_ONLY_REFUSAL})
        status, _, listed = self.request('/api/studio/briefing')
        self.assertEqual(status, 200)
        self.assertEqual([item['id'] for item in listed['items']], [identifier])
        self.assertEqual(self.request('/api/studio/briefing/item?id=' + identifier)[0], 200)


if __name__ == '__main__':
    unittest.main()

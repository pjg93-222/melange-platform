"""The worked example payloads in mentat/app/studio-contract-examples.json, sent to a
real Console over HTTP.

The two defects this suite exists to stop were both invisible to the per-endpoint unit
tests, because each side was tested against its own idea of the payload. Here the one
file the browser is written against is the one file the server is asked to accept, so a
client that starts sending something else fails here rather than on a reader's screen.

Nothing real is touched: the report directory is a temporary empty folder patched in for
the duration of construction only, and the data root is a temporary directory.
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
EXAMPLES = json.loads((APP / "studio-contract-examples.json").read_text(encoding="utf-8"))
# The lane's shared contract file, read at test time exactly as the node suite
# reads it (contracts/README.md). Never copied into a literal here.
CONTRACTS = Path(__file__).resolve().parents[2] / "audits" / "mentat_failings_20260915" / "contracts"
STUDIO_API = json.loads((CONTRACTS / "studio_api.json").read_text(encoding="utf-8"))
BOARD_REFUSAL = "The saved view's board does not match its sheets."

spec = importlib.util.spec_from_file_location("studio_contract_server", APP / "server.py")
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


class StudioContractExampleTests(unittest.TestCase):
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

    def post(self, path, payload):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        headers = {'Host': '127.0.0.1:{}'.format(self.server.server_port),
                   'X-Mentat-Token': self.console.token, 'Content-Type': 'application/json'}
        try:
            connection.request('POST', path, body=json.dumps(payload).encode(), headers=headers)
            response = connection.getresponse()
            content = response.read()
            return response.status, json.loads(content) if content else None
        finally:
            connection.close()

    def get(self, path):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        try:
            connection.request('GET', path, headers={'Host': '127.0.0.1:{}'.format(self.server.server_port)})
            response = connection.getresponse()
            content = response.read()
            return response.status, json.loads(content) if content else None
        finally:
            connection.close()

    # ------------------------------------------------------------------ saved views

    def test_the_example_saved_view_is_accepted_exactly_as_written(self):
        status, body = self.post('/api/studio/views', EXAMPLES['views_save'])
        self.assertEqual(status, 200, body)
        stored = json.loads((self.console.root / 'studio' / 'views' / (body['id'] + '.json'))
                            .read_text(encoding='utf-8'))
        self.assertEqual(stored['source'], EXAMPLES['views_save']['source'])
        self.assertEqual(stored['sourceKey'], EXAMPLES['views_save']['sourceKey'])

        status, listed = self.get('/api/studio/views')
        self.assertEqual(status, 200)
        view = next(entry for entry in listed['views'] if entry['id'] == body['id'])
        # The picker prints a caption; it can only do that if the object carried one.
        # A saved view's caption is the dataset's, while an exported chart's is the
        # workbook's, so the fixture keeps the two apart and so does this.
        self.assertIsInstance(view['source'], dict)
        self.assertEqual(view['source']['label'], EXAMPLES['expected']['view_source_caption'])
        self.assertEqual(view['source']['reportName'], EXAMPLES['expected']['source_caption'])
        self.assertEqual(view['source']['sha256'], EXAMPLES['expected']['workbook_sha256'])

    def test_a_view_recovered_from_the_old_browser_store_is_accepted_too(self):
        """The migration path carries nothing but a caption, because the old
        browser-local store recorded nothing else - but it still has to travel as an
        object, or every recovered view is refused and the reader is told nothing."""
        payload = dict(EXAMPLES['views_save'])
        payload['name'] = 'Recovered - Crypto - 2 sheets'
        payload['source'] = EXAMPLES['producer']['view_recovered']['expected_source']
        status, body = self.post('/api/studio/views', payload)
        self.assertEqual(status, 200, body)
        stored = json.loads((self.console.root / 'studio' / 'views' / (body['id'] + '.json'))
                            .read_text(encoding='utf-8'))
        self.assertEqual(stored['source'],
                         EXAMPLES['producer']['view_recovered']['expected_source'])

    def test_a_saved_view_whose_source_is_a_caption_is_refused_with_the_reason(self):
        """The original defect: the browser sent viewSourceLabel(), a string, and every
        Save view answered 400 with no clue on screen about which field was wrong."""
        payload = dict(EXAMPLES['views_save'])
        payload['source'] = EXAMPLES['expected']['source_caption']
        status, body = self.post('/api/studio/views', payload)
        self.assertEqual(status, 400)
        self.assertIn('source', body['error'].lower())

    # --------------------------------------------------------------------- briefing

    def test_the_example_briefing_item_keeps_its_provenance_object(self):
        status, body = self.post('/api/studio/briefing/add', EXAMPLES['briefing_add'])
        self.assertEqual(status, 200, body)
        index = json.loads((self.console.root / 'studio' / 'briefing' / 'index.json')
                           .read_text(encoding='utf-8'))
        item = next(entry for entry in index['items'] if entry['id'] == body['id'])
        self.assertEqual(item['provenance'], EXAMPLES['briefing_add']['provenance'])
        self.assertEqual(item['source'], EXAMPLES['briefing_add']['source'])
        self.assertEqual(item['provenance']['sha'], EXAMPLES['expected']['workbook_sha256'])
        # The short scope chip travels with the item. Every export prints it before
        # it falls back to the full provenance scope, so a tray that loses it here
        # prints the digest string where a reader expects one line.
        self.assertEqual(item['chip'], EXAMPLES['briefing_add']['chip'])

    def test_a_briefing_item_sent_with_a_string_provenance_loses_it_entirely(self):
        """Why the browser must send objects: the server replaces anything that is not
        an object with an empty one, so a digest sent as a string never reaches a slide."""
        payload = dict(EXAMPLES['briefing_add'])
        payload['provenance'] = EXAMPLES['expected']['workbook_sha256']
        payload['source'] = EXAMPLES['expected']['source_caption']
        status, body = self.post('/api/studio/briefing/add', payload)
        self.assertEqual(status, 200, body)
        index = json.loads((self.console.root / 'studio' / 'briefing' / 'index.json')
                           .read_text(encoding='utf-8'))
        item = next(entry for entry in index['items'] if entry['id'] == body['id'])
        self.assertEqual(item['provenance'], {})
        self.assertEqual(item['source'], {})


    # ------------------------------------------------------- the board (v2)

    def board_controls(self):
        """The six studio_api v2 controls: a view whose board is malformed."""
        return [control for control in STUDIO_API['negative_controls']
                if isinstance(control['payload'], dict) and 'board' in control['payload']]

    def stored_view(self, identifier):
        return json.loads((self.console.root / 'studio' / 'views' / (identifier + '.json'))
                          .read_text(encoding='utf-8'))

    def test_the_contract_is_v2_and_carries_the_board_examples_and_controls(self):
        # v3 (lane M) adds marks cards; every v2 board example and control is kept.
        self.assertEqual(STUDIO_API['version'], 3)
        for name in ('views_save_board', 'views_get_board', 'views_save_board_four'):
            self.assertIn(name, STUDIO_API['examples'])
        self.assertEqual(len(self.board_controls()), 6)

    def test_a_board_is_kept_on_disk_and_read_back_exactly_as_saved(self):
        """The v1 whitelist dropped every unknown top-level key in silence, so a
        board that saved and came back empty would have looked like a client bug."""
        for name in ('views_save_board', 'views_save_board_four'):
            payload = STUDIO_API['examples'][name]
            status, body = self.post('/api/studio/views', payload)
            self.assertEqual(status, 200, body)
            self.assertEqual(self.stored_view(body['id'])['board'], payload['board'], name)
            status, got = self.get('/api/studio/views?id=' + body['id'])
            self.assertEqual(status, 200)
            self.assertEqual(got['view']['board'], payload['board'], name)

    def test_the_view_read_back_has_exactly_the_keys_views_get_board_names(self):
        payload = STUDIO_API['examples']['views_save_board']
        status, body = self.post('/api/studio/views', payload)
        self.assertEqual(status, 200, body)
        status, got = self.get('/api/studio/views?id=' + body['id'])
        self.assertEqual(status, 200)
        self.assertEqual(sorted(got['view']), sorted(STUDIO_API['examples']['views_get_board']))

    def test_a_view_saved_without_a_board_is_stored_and_returned_without_one(self):
        """Every v1 view stays byte-identical: no board key appears on disk, and
        none appears in the response, so an older reader sees what it always saw."""
        payload = STUDIO_API['examples']['views_save']
        status, body = self.post('/api/studio/views', payload)
        self.assertEqual(status, 200, body)
        self.assertNotIn('board', self.stored_view(body['id']))
        status, got = self.get('/api/studio/views?id=' + body['id'])
        self.assertEqual(status, 200)
        self.assertNotIn('board', got['view'])
        self.assertEqual(sorted(got['view']), sorted(STUDIO_API['examples']['views_get']))

    def test_every_malformed_board_is_refused_with_the_same_sentence(self):
        for control in self.board_controls():
            status, body = self.post('/api/studio/views', control['payload'])
            self.assertEqual(status, 400, control['why'])
            self.assertEqual(body['error'], BOARD_REFUSAL, control['why'])

    def test_the_listing_says_nothing_about_boards(self):
        """The picker's rows are unchanged by v2: a board is a property of the
        view's canvas, not of the row that names it."""
        payload = STUDIO_API['examples']['views_save_board']
        status, body = self.post('/api/studio/views', payload)
        self.assertEqual(status, 200, body)
        status, listed = self.get('/api/studio/views')
        self.assertEqual(status, 200)
        row = next(entry for entry in listed['views'] if entry['id'] == body['id'])
        self.assertNotIn('board', row)
        self.assertEqual(sorted(row), sorted(STUDIO_API['examples']['views_list_item']))


if __name__ == '__main__':
    unittest.main()

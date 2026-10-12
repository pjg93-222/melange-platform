"""A report-worksheet view still saves after S4 (Fable review, 2026-09-16).

The S4 review reproduced a 400 on every worksheet-mode save while the client
defaulted sheet.source.industry to ''. The save gate is right to refuse an
empty industry (contracts/studio_api.json negative control 3); the client must
simply not send the key for a worksheet. Both halves are pinned here against
the real route.
"""
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'app'))
sys.path.insert(0, str(HERE.parent / 'tools'))
import server  # noqa: E402


def worksheet_sheet():
    return {"id": "", "name": "Sheet 1",
            "source": {"run": "", "runName": "", "report": "", "reportName": "Crypto.xlsx",
                       "sheet": "Summary", "header": 0, "sha256": ""},
            "sources": [], "columns": [], "rows": [],
            "marks": {"type": "automatic", "color": None, "size": None, "label": None, "detail": [], "tooltip": []},
            "filters": [], "pages": None, "pageMember": "",
            "analytics": {"transform": "none", "period": "", "horizon": 2, "totals": False, "average": False, "median": False},
            "types": {}, "params": {"start": 0, "end": 0, "limit": 0, "subject": "", "maxGroups": 0, "splitMulti": False},
            "sort": "none", "showLabels": False, "fit": "standard"}


class WorksheetViewSaveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.app = server.Console(cls.temp.name, '', '', False)
        cls.httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        cls.httpd.app = cls.app
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.base = f'http://127.0.0.1:{cls.httpd.server_port}'
        cls.token = json.loads(urllib.request.urlopen(cls.base + '/api/contributions/session').read())['token']

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.temp.cleanup()

    def post(self, body):
        request = urllib.request.Request(self.base + '/api/studio/views', data=json.dumps(body).encode(),
                                         headers={'Content-Type': 'application/json', 'X-Mentat-Token': self.token},
                                         method='POST')
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def view(self, sheet):
        return {"name": "Worksheet view", "sourceKey": "run|report|Summary|0|abc",
                "source": {"label": "Crypto.xlsx", "reportName": "Crypto.xlsx", "sheet": "Summary"},
                "sheets": [sheet], "active": 0}

    def test_a_worksheet_sheet_without_an_industry_key_saves(self):
        status, body = self.post(self.view(worksheet_sheet()))
        self.assertEqual(status, 200, body)
        self.assertIn('id', body)

    def test_negative_control_an_empty_industry_on_a_sheet_source_is_refused(self):
        sheet = worksheet_sheet()
        sheet['source']['industry'] = ''
        status, body = self.post(self.view(sheet))
        self.assertEqual(status, 400)
        self.assertEqual(body['error'], 'Every series source in a saved view needs an industry.')


if __name__ == '__main__':
    unittest.main()

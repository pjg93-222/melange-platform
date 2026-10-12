import hashlib
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from pac_evidence import Client

ENDPOINT = 'committee/C00000001/totals/'
PARAMS = {'cycle': 2024, 'per_page': 100}
EXPECTED_URL = 'https://api.open.fec.gov/v1/committee/C00000001/totals/?cycle=2024&per_page=100'


class PacEvidenceErrorTests(unittest.TestCase):
    def setUp(self):
        patcher = patch('pac_evidence.time.sleep')
        self.sleep = patcher.start()
        self.addCleanup(patcher.stop)

    def test_url_error_retried_then_reported(self):
        calls = []

        def transport(endpoint, params):
            calls.append((endpoint, params))
            raise urllib.error.URLError('getaddrinfo failed')

        with tempfile.TemporaryDirectory() as directory:
            client = Client(directory, key='fixture', transport=transport)
            with self.assertRaises(ValueError) as ctx:
                client.page(ENDPOINT, PARAMS)
        self.assertEqual(str(ctx.exception),
                          'FEC evidence request failed: getaddrinfo failed; URL ' + EXPECTED_URL)
        self.assertEqual(len(calls), 4)
        self.assertEqual(self.sleep.call_count, 3)

    def test_timeout_reported_with_url(self):
        def transport(endpoint, params):
            raise TimeoutError()

        with tempfile.TemporaryDirectory() as directory:
            client = Client(directory, key='fixture', transport=transport)
            with self.assertRaises(ValueError) as ctx:
                client.page(ENDPOINT, PARAMS)
        self.assertIn('timed out after 60 s; URL https://api.open.fec.gov/v1/', str(ctx.exception))

    def test_http_404_not_retried(self):
        calls = []

        def transport(endpoint, params):
            calls.append((endpoint, params))
            raise urllib.error.HTTPError(EXPECTED_URL, 404, 'Not Found', {}, None)

        with tempfile.TemporaryDirectory() as directory:
            client = Client(directory, key='fixture', transport=transport)
            with self.assertRaises(ValueError) as ctx:
                client.page(ENDPOINT, PARAMS)
        self.assertEqual(str(ctx.exception),
                          'FEC evidence request failed: HTTP 404; URL ' + EXPECTED_URL)
        self.assertEqual(len(calls), 1)
        self.sleep.assert_not_called()

    def test_http_503_retried(self):
        calls = []

        def transport(endpoint, params):
            calls.append((endpoint, params))
            if len(calls) <= 3:
                raise urllib.error.HTTPError(EXPECTED_URL, 503, 'Service Unavailable', {}, None)
            return {'results': [{'name': 'X'}], 'pagination': {}}

        with tempfile.TemporaryDirectory() as directory:
            client = Client(directory, key='fixture', transport=transport)
            result = client.page(ENDPOINT, PARAMS)
        self.assertEqual(result, {'results': [{'name': 'X'}], 'pagination': {}})
        self.assertEqual(len(calls), 4)

    def test_non_json_reported(self):
        calls = []

        def transport(endpoint, params):
            calls.append((endpoint, params))
            raise json.JSONDecodeError('x', '', 0)

        with tempfile.TemporaryDirectory() as directory:
            client = Client(directory, key='fixture', transport=transport)
            with self.assertRaises(ValueError) as ctx:
                client.page(ENDPOINT, PARAMS)
        self.assertEqual(str(ctx.exception),
                          'FEC evidence request failed: response was not JSON; URL ' + EXPECTED_URL)
        self.assertEqual(len(calls), 1)
        self.sleep.assert_not_called()

    def test_cache_integrity_names_path(self):
        with tempfile.TemporaryDirectory() as directory:
            client = Client(directory, key='fixture', transport=lambda *a: {})
            path = client.directory / (hashlib.sha256(EXPECTED_URL.encode()).hexdigest() + '.json')
            path.write_text(json.dumps({'url': 'https://mismatched.example/', 'response': {}, 'sha256': 'bad'}))
            with self.assertRaises(ValueError) as ctx:
                client.page(ENDPOINT, PARAMS)
        self.assertEqual(str(ctx.exception),
                          f'FEC evidence cache integrity check failed for {path}.')

    def test_success_path_unchanged(self):
        response = {'results': [{'name': 'Y'}], 'pagination': {}}

        def transport(endpoint, params):
            return response

        with tempfile.TemporaryDirectory() as directory:
            client = Client(directory, key='fixture', transport=transport)
            result = client.page(ENDPOINT, PARAMS)
            self.assertEqual(result, response)
            path = client.directory / (hashlib.sha256(EXPECTED_URL.encode()).hexdigest() + '.json')
            saved = json.loads(path.read_text())
        self.assertEqual(set(saved.keys()), {'url', 'retrieved_at', 'sha256', 'response'})
        self.assertEqual(saved['url'], EXPECTED_URL)
        self.assertEqual(saved['response'], response)


if __name__ == '__main__':
    unittest.main()

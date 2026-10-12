"""The Graph idle prefetch is not a user access (review H4, 2026-09-24).

graph.js prefetches the likely next Graph views while the page is idle, through the same /api/graph URL a
click sends, so the answer, its ETag and the browser's cache entry are shared. Those reads carry the request
header X-Mentat-Prefetch: 1; server.py answers them exactly as before but does not call record_access, so
the RACF value ranking the warmer primes by counts what people ask for, not what the page guessed. A read
without the header is recorded as before: 'graph_view', or 'graph_drawer' for the drawer's mode=activity.

Driven the way the other route tests drive the Handler: a real ThreadingHTTPServer on an ephemeral port,
with a stand-in console whose graph() answers at once. MENTAT_PREFETCH_TEST_OVERLAY names a folder whose
modules replace the app's (a staged or a deliberately broken server.py)."""
import json
import os
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

APP = Path(__file__).resolve().parents[1] / 'app'
sys.path.insert(0, str(APP))
if os.environ.get('MENTAT_PREFETCH_TEST_OVERLAY'):
    sys.path.insert(0, os.environ['MENTAT_PREFETCH_TEST_OVERLAY'])
import server as app  # noqa: E402

PREFETCH = {'X-Mentat-Prefetch': '1'}


class Recorder:
    """Stands in for prewarm.AccessRecorder: notes each submit()."""

    def __init__(self):
        self.calls = []

    def submit(self, kind, body, cost_s=None, size=None):
        self.calls.append((kind, dict(body)))


class Console:
    """What /api/graph reads from the console. No industry is named, so no registry is built."""

    def __init__(self):
        self.asked = []

    def graph(self, identifier, label, params, registry):
        self.asked.append(dict(params))
        return {'filters': {'company': params.get('company', ''), 'cycle': params.get('cycle', '')},
                'mode': params.get('mode', 'direct'), 'rows': [{'id': index, 'amount': index * 10} for index in range(80)]}


class PrefetchAccessTest(unittest.TestCase):
    def setUp(self):
        self.console = Console()
        self.recorder = Recorder()
        self.server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        self.server.app = self.console
        self.origin = f'http://127.0.0.1:{self.server.server_port}'
        # record_access runs after the response is written, so the client can hold the answer before the
        # server has decided whether to record it: each finished do_GET releases one permit.
        self.finished = threading.Semaphore(0)
        handled = app.Handler.do_GET

        def do_get(handler):
            try:
                return handled(handler)
            finally:
                self.finished.release()
        self.enterContext(patch.object(app.Handler, 'do_GET', do_get))
        self.enterContext(patch.object(app, 'ACCESS_RECORDER', self.recorder))
        self.record_access = self.enterContext(patch.object(app, 'record_access', wraps=app.record_access))
        worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        worker.start()

        def stop():
            self.server.shutdown()
            self.server.server_close()
            worker.join(5)
        self.addCleanup(stop)

    def get(self, query, headers=None):
        """(status, headers, body) of one GET of /api/graph?query, once the server has finished with it."""
        request = Request(self.origin + '/api/graph?' + query, headers=headers or {})
        try:
            with urlopen(request, timeout=10) as response:
                result = response.status, dict(response.headers.items()), response.read()
        except HTTPError as error:
            result = error.code, dict(error.headers.items()), error.read()
        self.assertTrue(self.finished.acquire(timeout=10), 'the handler did not finish')
        return result

    def test_a_prefetch_read_is_answered_the_same_and_not_recorded(self):
        query = 'id=published-reports&company=Acme&cycle=2024'
        status, headers, body = self.get(query, PREFETCH)
        self.assertEqual(status, 200)
        self.assertEqual(self.record_access.call_count, 0, 'record_access must not be called for a prefetch')
        self.assertEqual(self.recorder.calls, [], 'and nothing reaches the access recorder')
        click_status, click_headers, click_body = self.get(query)
        self.assertEqual(click_status, 200)
        self.assertEqual(click_body, body, 'the same answer, byte for byte')
        self.assertEqual(click_headers['ETag'], headers['ETag'], 'under the same ETag')
        self.assertEqual(self.record_access.call_count, 1, 'the click is recorded')
        self.assertEqual([kind for kind, _ in self.recorder.calls], ['graph_view'])
        self.assertEqual(self.recorder.calls[0][1]['company'], 'Acme')
        self.assertEqual(len(self.console.asked), 2, 'both reads are answered by the console as before')
        self.assertEqual(json.loads(body)['filters']['company'], 'Acme')

    def test_the_drawer_hop_follows_the_same_rule(self):
        query = 'id=published-reports&company=Acme&mode=activity'
        self.get(query, PREFETCH)
        self.assertEqual((self.record_access.call_count, self.recorder.calls), (0, []))
        self.get(query)
        self.assertEqual(self.record_access.call_count, 1)
        self.assertEqual([kind for kind, _ in self.recorder.calls], ['graph_drawer'])

    def test_a_conditional_prefetch_gets_the_same_304_and_is_not_recorded(self):
        query = 'id=published-reports&company=Beta'
        _, headers, _ = self.get(query)
        self.assertEqual(self.record_access.call_count, 1)
        etag = headers['ETag']
        status, prefetched, body = self.get(query, {**PREFETCH, 'If-None-Match': etag})
        self.assertEqual((status, body, prefetched['ETag']), (304, b'', etag), 'a prefetch revalidates like a click')
        self.assertEqual(self.record_access.call_count, 1, 'and is still not recorded')
        status, clicked, _ = self.get(query, {'If-None-Match': etag})
        self.assertEqual((status, clicked['ETag']), (304, etag))
        self.assertEqual(self.record_access.call_count, 2, 'a click that revalidates is recorded, as before')
        self.assertEqual([kind for kind, _ in self.recorder.calls], ['graph_view', 'graph_view'])

    def test_the_answer_does_not_vary_on_the_prefetch_header(self):
        """The browser keeps one cache entry per URL unless the answer varies on a request header, so the
        prefetch and the later click must share it: no Vary on X-Mentat-Prefetch, same caching headers."""
        query = 'id=published-reports&company=Gamma'
        _, prefetched, _ = self.get(query, PREFETCH)
        _, clicked, _ = self.get(query)
        self.assertNotIn('x-mentat-prefetch', prefetched.get('Vary', '').lower())
        for name in ('Content-Type', 'Cache-Control', 'Vary', 'ETag', 'Content-Length'):
            self.assertEqual(prefetched.get(name), clicked.get(name), name)


if __name__ == '__main__':
    unittest.main()

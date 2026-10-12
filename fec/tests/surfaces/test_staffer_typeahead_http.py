"""The same promises through the real HTTP handler: typeahead answers while the service lock is held, and the endpoints still answer.

A local ThreadingHTTPServer on an ephemeral port serves a fixture service; nothing here touches the live sidecar.
"""
import http.client
from http.server import ThreadingHTTPServer
import json
import threading
import time

import pytest

from fec.surfaces.staffer_live_server import Handler
from fec.tests.surfaces.staffer_latency_fixtures import LockHeld, memo_service

PROMPT = 0.35  # seconds, including one HTTP round trip


@pytest.fixture
def served():
    service, provider = memo_service()
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.dashboard = service
    thread = threading.Thread(target=server.serve_forever, daemon=True, name='fixture-http')
    thread.start()
    yield service, server.server_address[1]
    server.shutdown()
    server.server_close()
    thread.join(5)


def get(port, path, timeout=10):
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=timeout)
    try:
        started = time.perf_counter()
        connection.request('GET', path)
        response = connection.getresponse()
        body = response.read()
        return response.status, json.loads(body), time.perf_counter() - started
    finally:
        connection.close()


def test_the_typeahead_endpoints_answer_over_http(served):
    service, port = served
    status, body, _ = get(port, '/api/staffer-search?q=ada&limit=20')
    assert status == 200 and body['ready'] and 'Ada Lovelace' in [n['label'] for n in body['nodes']]
    status, body, _ = get(port, '/api/staffer-search?q=zzzz&limit=5')
    assert status == 200 and body['nodes'] == []
    status, body, _ = get(port, '/api/staffer-offices?q=test&limit=5')
    assert status == 200 and body['ready'] and [n['label'] for n in body['nodes']] == ['TEST OFFICE']
    status, body, _ = get(port, '/api/staffer-live?q=Gordon&limit=100')
    assert status == 200 and body['ready'] and body['routing']['algorithm'] == 'Bidirectional Dijkstra'
    assert body['source_revision'] == service.revision
    status, body, _ = get(port, '/api/staffer-person?id=person:gordon')
    assert status == 200 and body['node']['id'] == 'person:gordon'


def test_typeahead_endpoints_answer_while_another_thread_holds_the_service_lock(served):
    service, port = served
    with LockHeld(service, 3.0):
        for path in ('/api/staffer-search?q=ada&limit=20', '/api/staffer-search?q=al&limit=20', '/api/staffer-search?q=po&limit=20',
                     '/api/staffer-offices?q=test&limit=20'):
            status, body, elapsed = get(port, path)
            assert status == 200 and body['ready'], path
            assert elapsed < PROMPT, (path, elapsed)


def test_a_slow_request_that_needs_the_lock_does_not_stop_typeahead(served):
    """The snapshot waits for a held lock (it assembles state under it); typeahead on the same server does not wait for it."""
    service, port = served
    answers = {}

    def snapshot():
        answers['snapshot'] = get(port, '/api/staffer-live?q=Gordon&limit=100', timeout=20)
    with LockHeld(service, 1.5) as held:
        slow = threading.Thread(target=snapshot, daemon=True)
        slow.start()
        time.sleep(0.2)
        assert slow.is_alive(), 'the snapshot is waiting for the held lock'
        status, body, elapsed = get(port, '/api/staffer-search?q=ada&limit=20')
        assert status == 200 and body['ready'] and elapsed < PROMPT
        held.release.set()
        slow.join(10)
    assert answers['snapshot'][0] == 200 and answers['snapshot'][1]['ready']
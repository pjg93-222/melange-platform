"""Crash test dummies: synthetic testers driven past every former people/network cap, end to end.

Real access listener on an ephemeral 127.0.0.1 port, real TLS, temporary state. Small and
sequential except for one bounded burst; the whole file runs in seconds."""
import http.client
import json
import secrets
import socket
import ssl
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest

from desktop_admin import BoundedServer, Store, handler_for
from access_store import AccessStore

ORIGIN = 'https://updates.example.com'
TLS = ssl._create_unverified_context()  # fixture server; pin validation belongs to the native-client tests


@pytest.fixture
def clock():
    """Simulated hours: the kept 100-new-requests-per-hour flood bound is a rate, not a head count."""
    offset = [0]
    with patch.object(AccessStore, 'now', lambda _self: int(time.time()) + offset[0]):
        yield offset


def listener(tmp_path, public_origin):
    publisher = Store(tmp_path / 'state', public_origin=public_origin)
    publisher.init()
    admin = AccessStore(publisher)
    admin.initialize()
    server = BoundedServer(('127.0.0.1', 0), handler_for(publisher), publisher.tls_context(), public_origin=public_origin)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    return server, worker, admin


def stop(server, worker):
    server.shutdown()
    worker.join(5)
    server.server_close()


def call(server, method, path, body=None, *, auth=None, host=None, source='127.0.0.1'):
    connection = http.client.HTTPSConnection('127.0.0.1', server.server_port, context=TLS, timeout=20,
                                             source_address=(source, 0))
    try:
        headers = {'Content-Type': 'application/json'}
        if host:
            headers['Host'] = host
        if auth:
            headers['Authorization'] = auth
        connection.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


def dummy(n):
    return dict(requestId=str(uuid.uuid4()), deviceId=str(uuid.uuid4()), appId='melange', platform='win32',
                arch='x64', displayName=f'Crash test dummy {n}', requestSecret=secrets.token_urlsafe(32))


def test_150_relayed_dummies_all_end_approved_and_active(tmp_path, clock):
    """Public-origin mode: every dummy arrives through the relay as 127.0.0.1. Formerly 100 open
    requests, 600 calls/min in total and (in LAN mode) 10 requests/hour and 60 calls/min per address."""
    server, worker, admin = listener(tmp_path, ORIGIN)
    relay = dict(host='updates.example.com')

    def enroll(n):  # one tester's first run: ask, get approved by the administrator, activate
        value = dummy(n)
        assert call(server, 'POST', '/v1/access/requests', value, **relay)[0] == 201
        key = admin.approve(value['requestId'], 'local_user', [])['activationKey']
        activation = {**{k: value[k] for k in ('requestId', 'deviceId', 'appId', 'platform', 'arch')}, 'activationKey': key}
        status, granted = call(server, 'POST', '/v1/access/activate', activation, auth='Request ' + value['requestSecret'], **relay)
        assert status == 200, granted
        return 'Bearer ' + granted['accessToken']

    def in_use(bearer):  # every tester then checks status and reports presence at the same time
        assert call(server, 'GET', '/v1/access/status', auth=bearer, **relay)[0] == 200
        presence = {'sessionId': str(uuid.uuid4()), 'state': 'active', 'version': '1.0.0', 'districtId': None}
        assert call(server, 'POST', '/v1/access/presence', presence, auth=bearer, **relay)[0] == 200

    try:
        with ThreadPoolExecutor(8) as pool:
            bearers = []
            for hour in range(2):  # 75 arrivals in each of two simulated hours
                clock[0] = hour * 3601
                bearers += pool.map(enroll, range(hour * 75, hour * 75 + 75))
            list(pool.map(in_use, bearers))
        dummies = [g for g in admin.dashboard()['grants'] if g['display_name'].startswith('Crash test dummy')]
        assert len(dummies) == 150 and all(g['online'] and not g['revoked'] for g in dummies)
        with admin.connect() as db:
            assert db.execute("SELECT COUNT(*) FROM requests WHERE state='used'").fetchone()[0] == 150
        # The one bound kept on purpose: a 101st brand-new request inside one hour waits for the window.
        for n in range(25):
            value = dummy(1000 + n)
            assert call(server, 'POST', '/v1/access/requests', value, **relay)[0] == 201
            admin.deny(value['requestId'])
        assert call(server, 'POST', '/v1/access/requests', dummy(2000), **relay) == (429, {'error': 'rate_limited'})
    finally:
        stop(server, worker)


def test_210_dummies_from_distinct_addresses_are_all_admitted(tmp_path, clock):
    """LAN mode: formerly the 201st distinct peer within a minute got 429 because the peer table was full."""
    server, worker, admin = listener(tmp_path, None)

    def admit(n):
        value, source = dummy(n), f'127.0.{1 + n // 200}.{1 + n % 200}'
        status, reply = call(server, 'POST', '/v1/access/requests', value, source=source)
        assert (status, reply['status']) == (201, 'pending'), (n, source, reply)
        admin.approve(value['requestId'], 'local_user', [])

    try:
        with ThreadPoolExecutor(8) as pool:
            for hour in range(3):  # 70 arrivals per simulated hour
                clock[0] = hour * 3601
                list(pool.map(admit, range(hour * 70, hour * 70 + 70)))
        with admin.connect() as db:
            assert db.execute('SELECT COUNT(*), COUNT(DISTINCT source_hash) FROM requests').fetchone()[:] == (210, 210)
    finally:
        stop(server, worker)


def test_connections_beyond_the_workers_wait_and_all_get_answers(tmp_path):
    """Every worker held busy, then 24 more callers at once: formerly each was closed with no reply."""
    server, worker, _ = listener(tmp_path, None)
    holders, results = [], []
    try:
        for _ in range(32):  # occupy all 32 workers with peers that have not started TLS yet
            holders.append(socket.create_connection(('127.0.0.1', server.server_port), timeout=5))
        deadline = time.monotonic() + 5
        while server.slots._value and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.slots._value == 0

        def caller():
            try:
                results.append(call(server, 'GET', '/v1/access/status')[0])
            except Exception as exc:  # a dropped connection surfaces here
                results.append(repr(exc))
        callers = [threading.Thread(target=caller) for _ in range(24)]
        for thread in callers:
            thread.start()
        time.sleep(0.5)
        assert results == []  # all 24 are waiting for a worker, none was turned away
        for sock in holders:
            sock.close()
        for thread in callers:
            thread.join(20)
        assert results == [403] * 24  # every caller got an HTTP answer (no bearer token, so 403)
    finally:
        for sock in holders:
            sock.close()
        stop(server, worker)

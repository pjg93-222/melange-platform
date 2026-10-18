"""Inert D075 controls for the evidence broker over its REAL loopback HTTP wire (the offline replay entry point is test_d075_replay_entry.py).

These need a connect to 127.0.0.1 inside the test process (and in a child process for the replay), so they run only under the loopback guard of work/run_overlay.py (D075_LOOPBACK_OK=1: a
connect to 127.0.0.1 or ::1 is allowed, everything else - DNS, any other address, the key store - is refused). Elsewhere they are skipped with that reason. The provider is a scripted fake and
the keys are test strings: nothing leaves the machine.
"""
import hashlib
import http.client
import json
import os
from pathlib import Path
import socket
import struct
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

import d075_support as s
import test_collect_committee_activity_concurrency as cc

driver, host, pool = s.driver, s.host, s.pool
LOOPBACK = os.environ.get('D075_LOOPBACK_OK') == '1'
CID = 'ab' * 16


@unittest.skipUnless(LOOPBACK, 'needs the loopback guard of work/run_overlay.py --guard loopback (D075_LOOPBACK_OK=1): a connect to 127.0.0.1 only')
class Wire(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='D075-wire-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.secret = self.root / 'secret'
        self.secret.mkdir()
        (self.secret / 'fec_api_keys').write_text('\n'.join(cc.KEYS) + '\n')
        self.alive = threading.Event()
        self.alive.set()
        self.events = []

    def serve(self, *, opener=None, families=('B', 'E'), max_attempts=100000, owner_alive=None, keys=None, per_key_concurrency=2):
        """serve_evidence in a thread; returns (thread, errors, ready) once the descriptor exists."""
        ready, errors, holder = threading.Event(), [], {}

        def on_ready(broker, config):
            holder['broker'] = broker
            ready.set()

        def run():
            try:
                with mock.patch.dict(os.environ, {'MENTAT_API_BUDGET_DB': str(self.root / 'quota.sqlite'), 'MENTAT_API_BUDGET_IMPORTS': '[]'}):
                    holder['final'] = host.serve_evidence(self.secret, 123, s.TOOLS.parent, run_id=CID, archive_root=self.root / 'host', max_attempts=max_attempts, families=list(families),
                                                           key_loader=lambda: list(keys or cc.KEYS), owner_started=lambda pid: 777, owner_alive=owner_alive or (lambda pid, started: True),
                                                           opener=opener or cc.ScriptedOpener(cc.World()), cap=100000, per_key_concurrency=per_key_concurrency, reach='loopback', tools=s.TOOLS, ready=on_ready,
                                                           emit=lambda *args, **kw: self.events.append((args, kw)), heartbeat_seconds=0.3)
            except Exception as error:      # surfaced by the test
                errors.append(repr(error))
                ready.set()
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        self.assertTrue(ready.wait(20), errors)
        self.assertEqual(errors, [])
        return thread, errors, holder

    def stop(self, thread):
        (self.secret / 'owner.stop').write_bytes(b'')
        thread.join(20)
        self.assertFalse(thread.is_alive())

    def post(self, port, path, body, token, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', port, timeout=20)
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        connection.request('POST', path, body=data, headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json', **(headers or {})})
        response = connection.getresponse()
        payload = response.read()
        connection.close()
        return response.status, payload

    def test_a_whole_collection_over_the_real_wire_matches_the_local_transport_and_leaves_a_clean_host(self):
        thread, errors, holder = self.serve()
        raw_hash = hashlib.sha256((self.secret / 'request-pool.json').read_bytes()).hexdigest()
        descriptor = json.loads((self.secret / 'request-pool.json').read_text())
        broker = holder['broker']
        self.assertEqual((descriptor['reach'], descriptor['run_id']), ('loopback', CID))
        local, _, _, local_report = cc.run_world(self.root, workers=4, adapter=True, name='local', ledger=self.root / 'local-quota.sqlite')
        frontier = driver.Frontier(self.root / 'remote-state' / 'frontier.sqlite', include_later_cycle_context=True)
        self.addCleanup(frontier.close)
        frontier.seed(cc.SEEDS)
        start = self.root / 'START.json'
        start.write_text('{}')
        transport = pool.RemoteEvidenceTransport(descriptor, run_id=CID, max_requests=100000, workers=4, tools=s.TOOLS)         # the real HTTP post
        with transport.session(start, 'c' * 64) as active:
            report = frontier.run(active, max_failed_attempts=100000, workers=4)
        self.assertEqual(cc.canonical(frontier.path), cc.canonical(local.path))
        self.assertEqual(report['attempts_recorded'], transport.http_attempts)
        # bad requests over the same wire
        port = int(descriptor['url'].rsplit(':', 1)[1].split('/')[0])
        self.assertEqual(self.post(port, '/v2/fetch', {}, 'x' * 43)[0], 403)
        self.assertEqual(self.post(port, '/v2/fetch', {}, descriptor['token'], {'Origin': 'http://evil.invalid'})[0], 403)
        self.assertEqual(self.post(port, '/v2/fetch', b'{' + b'a' * 70000, descriptor['token'])[0], 413)
        self.assertEqual(self.post(port, '/v2/fetch', b'not json', descriptor['token'])[0], 400)
        self.assertEqual(self.post(port, '/v2/elsewhere', {}, descriptor['token'])[0], 404)
        connection = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
        connection.request('GET', '/v2/fetch')
        self.assertEqual(connection.getresponse().status, 405)
        connection.close()
        self.stop(thread)
        self.assertEqual(errors, [])
        self.assertFalse((self.secret / 'request-pool.json').exists(), 'the descriptor is removed when the broker stops')
        receipt = json.loads(broker.receipt_path.read_text())
        journal = s.journal(broker)
        attempts = [line for line in journal if line['event'] == 'attempt']
        deliveries = [line for line in journal if line['event'] == 'delivery']
        self.assertEqual((receipt['attempts_journalled'], receipt['physical_attempts_charged'], len(attempts), len(deliveries)), (transport.http_attempts,) * 4)
        self.assertTrue(all(line['delivered'] for line in deliveries))
        self.assertEqual((receipt['delivered'], receipt['undelivered_attempts']), (transport.http_attempts, []))
        self.assertEqual(receipt['descriptor_sha256'], raw_hash, 'the receipt names the exact descriptor bytes the container was given')
        # nothing secret was written anywhere on the host: not a key, not the token
        for path in self.root.rglob('*'):
            if path.is_file() and path.suffix in ('.json', '.ndjson', '.bin', '.log') and path.name != 'request-pool.json':
                data = path.read_bytes()
                for key in cc.KEYS:
                    self.assertNotIn(key.encode(), data, str(path))
                self.assertNotIn(descriptor['token'].encode(), data, str(path))
        self.assertNotIn(cc.KEYS[0], json.dumps(descriptor))
        # the Glass Box rule: the broker is a process, so it emits: a start, a rate heartbeat while it works, and a stop that names its receipt
        kinds = [args[0] for args, kw in self.events]
        self.assertEqual((kinds[0], kinds[-1]), ('fec.broker.start', 'fec.broker.stop'))
        self.assertGreaterEqual(kinds.count('fec.broker.rate'), 1)
        for args, kw in self.events:
            self.assertEqual((kw['run_id'], kw['source'], kw['lane'], kw['stage']), (CID, 'mentat-evidence-broker', 'system', 'broker'))
            self.assertNotIn(descriptor['token'], json.dumps(args) + json.dumps(kw, default=str))
            for key in cc.KEYS:
                self.assertNotIn(key, json.dumps(args) + json.dumps(kw, default=str))
        stop = json.loads(self.events[-1][1]['detail'])
        self.assertEqual((stop['attempts'], stop['state'], stop['receipt']), (transport.http_attempts, 'closed', str(broker.receipt_path)))
        rates = [json.loads(kw['detail']) for args, kw in self.events if args[0] == 'fec.broker.rate']
        self.assertTrue(all({'attempts', 'attempts_last_60s', 'requests_per_hour_60s', 'rate_limited_429'} <= set(item) for item in rates))

    def test_the_server_binds_loopback_only_and_a_vanished_client_marks_its_attempt_undelivered(self):
        slow = cc.ScriptedOpener(cc.World(failures=False), delay=0.4)
        thread, errors, holder = self.serve(opener=slow)
        descriptor = json.loads((self.secret / 'request-pool.json').read_text())
        port = int(descriptor['url'].rsplit(':', 1)[1].split('/')[0])
        broker = holder['broker']
        self.assertEqual(self.post(port, '/v2/open', {'run_id': CID, 'start_sha256': '0' * 64, 'collector_sha256': '1' * 64}, descriptor['token'])[0], 200)
        body = json.dumps({'seq': 1, 'endpoint': 'schedules/schedule_b/', 'params': {'committee_id': 'C00000001', 'two_year_transaction_period': 2024, 'per_page': 100}}).encode()
        raw = socket.create_connection(('127.0.0.1', port), timeout=10)
        raw.sendall(b'POST /v2/fetch HTTP/1.0\r\nAuthorization: Bearer ' + descriptor['token'].encode() + b'\r\nContent-Type: application/json\r\nContent-Length: ' + str(len(body)).encode() + b'\r\n\r\n' + body)
        time.sleep(0.15)                                                                   # let the server read the request first; its answer takes 0.4 s
        raw.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack('ii', 1, 0))      # reset on close: the answer has nowhere to go
        raw.close()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and not any(line['event'] == 'delivery' for line in s.journal(broker)):
            time.sleep(0.1)
        self.stop(thread)
        receipt = json.loads(broker.receipt_path.read_text())
        self.assertEqual((receipt['attempts_journalled'], receipt['undelivered_attempts']), (1, [1]), 'charged and archived on the host, the client never got it: the journal is one ahead')
        self.assertEqual(len(list((self.root / 'host' / ('responses-' + CID)).glob('*.body.bin'))), 1)

    def test_the_owner_dying_stops_the_broker_and_removes_the_descriptor_without_a_receipt_for_a_run_that_never_opened(self):
        beats = []

        def owner_alive(pid, started):
            beats.append(1)
            return len(beats) < 3
        thread, errors, holder = self.serve(owner_alive=owner_alive)
        thread.join(20)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertFalse((self.secret / 'request-pool.json').exists())
        self.assertFalse(list((self.root / 'host').rglob('BROKER-*')) if (self.root / 'host').exists() else [], 'nothing opened, nothing to receipt')
        self.assertEqual((self.secret / 'fec_api_keys').read_text(), '\n'.join(cc.KEYS) + '\n', 'the broker never touches the staged keys; the wrapper wipes them')

    def test_serve_refuses_an_existing_descriptor_no_keys_and_an_unidentifiable_owner(self):
        (self.secret / 'request-pool.json').write_text('{}')
        with mock.patch.dict(os.environ, {'MENTAT_API_BUDGET_DB': str(self.root / 'q.sqlite')}):
            with self.assertRaisesRegex(ValueError, 'already exists'):
                host.serve_evidence(self.secret, 1, s.TOOLS.parent, run_id=CID, archive_root=self.root / 'host', max_attempts=5, families=['B', 'E'], key_loader=lambda: list(cc.KEYS),
                                    owner_started=lambda pid: 7, owner_alive=lambda *_: False, tools=s.TOOLS)
            (self.secret / 'request-pool.json').unlink()
            with self.assertRaisesRegex(ValueError, 'No enabled API keys'):
                host.serve_evidence(self.secret, 1, s.TOOLS.parent, run_id=CID, archive_root=self.root / 'host', max_attempts=5, families=['B', 'E'], key_loader=lambda: [],
                                    owner_started=lambda pid: 7, owner_alive=lambda *_: False, tools=s.TOOLS)
            with self.assertRaisesRegex(ValueError, 'creation identity'):
                host.serve_evidence(self.secret, 1, s.TOOLS.parent, run_id=CID, archive_root=self.root / 'host', max_attempts=5, families=['B', 'E'], key_loader=lambda: list(cc.KEYS),
                                    owner_started=lambda pid: 0, owner_alive=lambda *_: False, tools=s.TOOLS)
            with self.assertRaisesRegex(ValueError, 'secret directory'):
                host.serve_evidence(self.root / 'nowhere', 1, s.TOOLS.parent, run_id=CID, archive_root=self.root / 'host', max_attempts=5, families=['B', 'E'], key_loader=lambda: list(cc.KEYS),
                                    owner_started=lambda pid: 7, owner_alive=lambda *_: False, tools=s.TOOLS)
        self.assertFalse((self.secret / 'request-pool.json').exists())

    def test_a_stop_with_attempts_in_flight_waits_for_them_refuses_the_waiting_one_and_journals_everything(self):
        gate, entered = threading.Event(), threading.Event()
        opener = cc.ScriptedOpener(cc.World(), hook=lambda number, key, query: (entered.set(), gate.wait(30)))
        thread, errors, holder = self.serve(opener=opener, keys=cc.KEYS[:1], per_key_concurrency=1)           # one slot: the second request waits for the first
        descriptor = json.loads((self.secret / 'request-pool.json').read_text())
        port, token = int(descriptor['url'].rsplit(':', 1)[1].split('/')[0]), descriptor['token']
        page = {'committee_id': 'C00000001', 'two_year_transaction_period': 2024, 'per_page': 100}
        self.assertEqual(self.post(port, '/v2/open', {'run_id': CID, 'start_sha256': '0' * 64, 'collector_sha256': '1' * 64}, token)[0], 200)
        answered = {}
        first = threading.Thread(target=lambda: answered.update(first=self.post(port, '/v2/fetch', {'seq': 1, 'endpoint': 'schedules/schedule_b/', 'params': page}, token)))
        first.start()
        self.assertTrue(entered.wait(10))
        second = threading.Thread(target=lambda: answered.update(second=self.post(port, '/v2/fetch', {'seq': 2, 'endpoint': 'schedules/schedule_b/', 'params': dict(page, committee_id='C00000002')}, token)))
        second.start()
        time.sleep(0.5)                                                                                       # the second request is waiting for the only key slot
        (self.secret / 'owner.stop').write_bytes(b'')                                                          # what the wrapper's finally writes after a container kill or a timeout
        time.sleep(1.5)                                                                                       # the serve loop (0.2 s poll) has seen it and is waiting on the handler in flight
        self.assertTrue(thread.is_alive(), 'shutdown waits for the attempt in flight instead of closing the journal under it')
        gate.set()
        thread.join(30)
        first.join(30)
        second.join(30)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        status, payload = answered['first']
        self.assertEqual((status, json.loads(payload)['attempts']), (200, 1), 'the attempt in flight is answered, not dropped with a 500')
        status, payload = answered['second']
        self.assertEqual((status, json.loads(payload)), (200, {'attempts': 0, 'refusal': 'stopped'}), 'admission ended first: the waiting request was refused, not sent after the stop')
        self.assertEqual(len(opener.calls), 1)
        broker = holder['broker']
        receipt = json.loads(broker.receipt_path.read_text())
        attempts = [line for line in s.journal(broker) if line['event'] == 'attempt']
        self.assertEqual((receipt['physical_attempts_charged'], receipt['attempts_journalled'], len(attempts)), (1, 1, 1), 'charged == journalled == 1')

    def test_the_server_joins_its_handlers_and_times_out_a_silent_connection(self):
        broker = s.make_broker(self.root)
        server, thread, url = host.evidence_http_server(broker)
        try:
            self.assertEqual((server.daemon_threads, server.block_on_close, server.RequestHandlerClass.timeout), (False, True, 30))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(10)
            broker.shutdown()


if __name__ == '__main__':
    unittest.main()

"""Inert D075 controls for the host evidence broker v2 (scope fec_spending_evidence_v2) and its key-free client.

The broker runs the REAL D067/R1b AccountedTransport behind a scripted fake provider; the client talks to it through broker.handle() (the wire without a socket), so nothing here
needs a network, a credential or a container. Controls: descriptor and token scope, endpoint-family least privilege (each refusal has a twin that is served), the physical cap held by the
broker alone, R1 accounting equality through the broker (N=1 and N=4, equal to the local transport), archive-before-response, per-key 429 isolation without pacing, and the host
quarantine of a crashed run followed by recovery against a read-only archive root.
"""
from contextlib import closing
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
from urllib.error import HTTPError, URLError

import d075_support as s
import test_collect_committee_activity_concurrency as cc
from api_request_budget import budget_id

driver, accounted, host, pool = s.driver, s.accounted, s.host, s.pool
PAGE = {'committee_id': 'C00000001', 'two_year_transaction_period': 2024, 'per_page': 100}
OPEN = {'run_id': s.RUN, 'start_sha256': '0' * 64, 'collector_sha256': '1' * 64}
EXAMPLES = {'A': 'schedules/schedule_a/', 'B': 'schedules/schedule_b/', 'E': 'schedules/schedule_e/', 'F': 'schedules/schedule_f/', 'H4': 'schedules/schedule_h4/',
            'totals': 'committee/C00000001/totals/', 'candidate_committees': 'candidate/H0AA00001/committees/'}
ALL = ('A', 'B', 'E', 'F', 'H4', 'totals', 'candidate_committees')


class BodyReadFails(io.BytesIO):
    """A provider answer whose body read breaks AFTER the request was admitted and sent (what the adapter does not wrap in URLError)."""
    status, headers = 200, {'Content-Type': 'application/json'}

    def __init__(self, error):
        super().__init__(b'')
        self.error = error

    def read(self, *args):
        raise self.error


class FaultyOpener:
    def __init__(self, error):
        self.error, self.calls = error, 0

    def open(self, request, timeout):
        self.calls += 1
        return BodyReadFails(self.error)


class Base(cc.Base):
    def setUp(self):
        super().setUp()
        self.addCleanup(self.shutdown_brokers)
        self.brokers = []

    def shutdown_brokers(self):
        for broker in self.brokers:
            broker.shutdown()

    def broker(self, **options):
        broker = s.make_broker(self.root, **options)
        self.brokers.append(broker)
        return broker

    def opened(self, **options):
        broker = self.broker(**options)
        self.assertEqual(s.call(broker, 'open', OPEN)[0], 200)
        return broker

    def fetch(self, broker, seq, endpoint='schedules/schedule_b/', params=None, **kw):
        return s.call(broker, 'fetch', {'seq': seq, 'endpoint': endpoint, 'params': dict(params or PAGE)}, **kw)


# ---------------------------------------------------------------- the descriptor and the client's refusals
class Descriptor(Base):
    def test_the_descriptor_is_key_free_unpaced_and_names_exactly_the_run_scope(self):
        broker = self.broker(families=('B', 'E', 'totals'), max_attempts=777)
        text = host.evidence_descriptor_text(broker, 'http://127.0.0.1:4242/v2', 'host.docker.internal')
        value = json.loads(text)
        for key in cc.KEYS:
            self.assertNotIn(key, text)
        self.assertEqual((value['schema'], value['scope'], value['url'], value['reach'], value['run_id'], value['max_attempts'], value['width'], value['per_key_concurrency']),
                         ('mentat-evidence-pool-v2', 'fec_spending_evidence_v2', 'http://127.0.0.1:4242/v2', 'host.docker.internal', s.RUN, 777, len(cc.KEYS), 2))
        self.assertEqual([item['family'] for item in value['families']], ['B', 'E', 'totals'])
        self.assertEqual(value['key_ids'], [budget_id(k) for k in cc.KEYS])
        self.assertEqual(value['adapter_sha256'], hashlib.sha256((s.TOOLS / 'fec_accounted_client.py').read_bytes()).hexdigest())
        self.assertEqual(value['contracts_sha256'], hashlib.sha256((s.TOOLS / 'spending_endpoint_contracts.py').read_bytes()).hexdigest())
        for forbidden in ('pacing', 'quota_wait', 'delay', 'sleep'):          # owner ruling: no pacing; a 429 is key-scoped (R1b)
            self.assertFalse([name for name in value if forbidden in name], forbidden)
        self.assertTrue(pool.evidence_descriptor(value))
        self.assertEqual(len(broker.token), 43)

    def test_the_client_refuses_every_malformed_descriptor_and_accepts_the_unmutated_twin(self):
        value = s.descriptor_of(self.broker())
        self.assertTrue(pool.evidence_descriptor(dict(value)))
        mutations = {'schema': {'schema': 'mentat-request-pool-v1'}, 'scope': {'scope': 'schedule_a_batch_only'}, 'origin': {'origin': 'https://example.invalid/v1/'},
                     'url host': {'url': 'http://0.0.0.0:4242/v2'}, 'url path': {'url': 'http://127.0.0.1:4242/batch'}, 'url port': {'url': 'http://127.0.0.1:0/v2'},
                     'reach': {'reach': 'anywhere'}, 'short token': {'token': 'short'}, 'run id': {'run_id': 'xyz'}, 'cap': {'max_attempts': 0}, 'bool cap': {'max_attempts': True},
                     'width vs key ids': {'width': value['width'] + 1}, 'per-key': {'per_key_concurrency': 0}, 'family regex widened': {'families': [{'family': 'B', 'endpoint_regex': 'schedules/'}, value['families'][1]]},
                     'no E': {'families': value['families'][:1]}, 'unknown family': {'families': value['families'] + [{'family': 'X', 'endpoint_regex': 'x/'}]},
                     'adapter hash': {'adapter_sha256': 'nothex'}, 'no stored root': {'stored_root': ''}}
        for name, change in mutations.items():
            with self.subTest(mutation=name):
                with self.assertRaisesRegex(ValueError, 'descriptor is invalid'):
                    pool.evidence_descriptor(value | change)
        for missing in value:
            with self.subTest(missing=missing):
                with self.assertRaises(ValueError):
                    pool.evidence_descriptor({k: v for k, v in value.items() if k != missing})

    def test_the_client_refuses_another_run_a_larger_cap_more_workers_and_a_changed_tree(self):
        broker = self.broker(max_attempts=50)
        self.assertTrue(s.client(broker, max_requests=50, workers=len(cc.KEYS) * 2))
        with self.assertRaisesRegex(ValueError, 'another collector run'):
            s.client(broker, run_id='cd' * 16)
        with self.assertRaisesRegex(ValueError, 'cap this run token carries'):
            s.client(broker, max_requests=51)
        with self.assertRaisesRegex(ValueError, 'broker width'):
            s.client(broker, max_requests=50, workers=len(cc.KEYS) * 2 + 1)
        changed = self.root / 'tree'
        changed.mkdir()
        for name in ('fec_accounted_client.py', 'spending_endpoint_contracts.py'):
            shutil.copyfile(s.TOOLS / name, changed / name)
        (changed / 'spending_endpoint_contracts.py').write_bytes((s.TOOLS / 'spending_endpoint_contracts.py').read_bytes() + b'\n# edited after the broker started\n')
        with self.assertRaisesRegex(ValueError, 'differs from the one the broker imported'):
            s.client(broker, max_requests=50, tools=changed)

    def test_a_descriptor_that_would_carry_a_key_is_never_written(self):
        broker = self.broker()
        broker.stored_root = 'C:\\spelled\\' + cc.KEYS[0]
        with self.assertRaisesRegex(ValueError, 'credential content'):
            host.evidence_descriptor_text(broker, 'http://127.0.0.1:4242/v2')

    def test_families_are_canonical_b_and_e_are_always_there_and_nothing_unknown_is_admitted(self):
        self.assertEqual(host.parse_families('totals,F'), ['B', 'E', 'F', 'totals'])
        self.assertEqual(host.parse_families(''), ['B', 'E'])
        self.assertEqual(host.parse_families('candidate_committees,A,H4'), ['A', 'B', 'E', 'H4', 'candidate_committees'])
        for bad in ('X', 'B,B', 'totals,totals', 'schedule_b'):
            with self.assertRaises(ValueError):
                host.parse_families(bad)
        for families in (['B'], ['E'], ['E', 'B'], ['B', 'E', 'X']):
            with self.assertRaises(ValueError):
                s.make_broker(self.root, families=families)
        with self.assertRaises(ValueError):
            s.make_broker(self.root, run_id='nothex')
        with self.assertRaises(ValueError):
            s.make_broker(self.root, max_attempts=0)


# ---------------------------------------------------------------- token scope and least privilege
class TokenScope(Base):
    def test_a_request_without_the_run_token_the_right_method_and_a_clean_origin_is_refused(self):
        broker = self.opened()
        good = self.fetch(broker, 1)
        self.assertEqual((good[0], good[1]['attempts']), (200, 1))                             # the twin
        for name, kw in {'wrong token': dict(token='x' * 43), 'no authorization': dict(headers={}), 'basic scheme': dict(headers={'Authorization': 'Basic ' + broker.token}),
                         'browser origin': dict(headers={'Authorization': 'Bearer ' + broker.token, 'Origin': 'http://evil.invalid'})}.items():
            with self.subTest(name):
                self.assertEqual(self.fetch(broker, 2, **kw)[0], 403)
        self.assertEqual(self.fetch(broker, 2, method='GET')[0], 405)
        self.assertEqual(broker.handle('POST', '/v2/other', {'Authorization': 'Bearer ' + broker.token}, b'{}')[0], 404)
        self.assertEqual(broker.handle('POST', '/batch', {'Authorization': 'Bearer ' + broker.token}, b'{}')[0], 404)       # the v1 path does not exist here
        self.assertEqual(self.fetch(broker, 2, body=b'not json')[0], 400)
        self.assertEqual(self.fetch(broker, 2, body=b'[1]')[0], 400)
        self.assertEqual(self.fetch(broker, 2, body=b'{"x":"' + b'a' * 70000 + b'"}')[0], 413)
        self.assertEqual(self.fetch(broker, 2)[0], 200)                                         # none of the refusals spent seq 2 or a request
        self.assertEqual(broker.transport.http_attempts, 2)

    def test_open_is_bound_to_the_run_id_and_happens_once(self):
        broker = self.broker()
        self.assertEqual(s.call(broker, 'open', OPEN | {'run_id': 'cd' * 16})[0], 403)
        self.assertEqual(s.call(broker, 'open', OPEN | {'start_sha256': 'x'})[0], 400)
        self.assertEqual(s.call(broker, 'open', {'run_id': s.RUN})[0], 400)
        self.assertEqual(self.fetch(broker, 1)[0], 409)                                         # not open yet
        self.assertFalse((broker.archive_root / ('responses-' + s.RUN)).exists())
        status, answer = s.call(broker, 'open', OPEN)
        self.assertEqual((status, answer['opened'], answer['archive_directory_stored']), (200, True, str(broker.archive_root / ('responses-' + s.RUN))))
        self.assertTrue(s.folder_of(broker).is_dir())
        self.assertEqual(s.call(broker, 'open', OPEN), (409, {'error': 'already_opened'}))
        self.assertEqual(s.call(broker, 'close', {'run_id': 'cd' * 16})[0], 403)

    def test_a_run_id_is_never_reused_the_archive_folder_must_not_exist(self):
        broker = self.broker()
        (broker.archive_root / ('responses-' + s.RUN)).mkdir(parents=True)
        self.assertEqual(s.call(broker, 'open', OPEN), (409, {'error': 'archive_exists'}))

    def test_close_releases_a_request_that_is_waiting_for_a_key_and_the_closed_state_refuses_on_its_own(self):
        gate, entered = threading.Event(), threading.Event()
        opener = cc.ScriptedOpener(cc.World(failures=False), hook=lambda number, key, query: (entered.set(), gate.wait(10)))
        broker = self.opened(opener=opener, keys=cc.KEYS[:1], per_key_concurrency=1)
        results = {}
        first = threading.Thread(target=lambda: results.update(first=self.fetch(broker, 1)))
        first.start()
        self.assertTrue(entered.wait(10))
        second = threading.Thread(target=lambda: results.update(second=self.fetch(broker, 2, params=dict(PAGE, committee_id='C00000002'))))
        second.start()
        time.sleep(0.4)                                                                        # the second request is now waiting for the only key slot
        self.assertEqual(s.call(broker, 'close', {'run_id': s.RUN}), (200, {'closed': True}))
        second.join(10)
        self.assertEqual(results['second'], (200, {'attempts': 0, 'refusal': 'stopped'}), 'close stops the pool: the waiting request is refused, not sent after the first one finishes')
        gate.set()
        first.join(10)
        self.assertEqual(results['first'][1]['attempts'], 1)
        self.assertEqual(len(opener.calls), 1)
        other = self.broker(run_id='cd' * 16, name='other')
        self.assertEqual(s.call(other, 'open', OPEN | {'run_id': 'cd' * 16})[0], 200)
        with mock.patch.object(other.transport.pool, 'stop'):                                   # the closed STATE refuses by itself even when the pool were not stopped
            self.assertEqual(s.call(other, 'close', {'run_id': 'cd' * 16}), (200, {'closed': True}))
            self.assertEqual(s.call(other, 'fetch', {'seq': 1, 'endpoint': 'schedules/schedule_b/', 'params': PAGE}), (200, {'attempts': 0, 'refusal': 'stopped'}))
        self.assertEqual(other.transport.http_attempts, 0)

    def test_close_ends_admission_and_is_idempotent(self):
        broker = self.opened()
        self.assertEqual(self.fetch(broker, 1)[1]['attempts'], 1)
        self.assertEqual(s.call(broker, 'close', {'run_id': s.RUN}), (200, {'closed': True}))
        self.assertEqual(self.fetch(broker, 2), (200, {'attempts': 0, 'refusal': 'stopped'}))
        self.assertEqual(s.call(broker, 'close', {'run_id': s.RUN}), (200, {'closed': True}))
        self.assertEqual(broker.transport.http_attempts, 1)

    def test_only_the_named_families_are_admitted_and_each_named_one_is_served(self):
        plain = s.PlainOpener()
        for named in ((), ('F',), ('H4',), ('totals',), ('candidate_committees',), ('A',), ('F', 'H4', 'totals', 'candidate_committees', 'A')):
            families = host.parse_families(','.join(named))
            broker = self.broker(families=families, opener=plain, run_id=('%02x' % (len(named) * 17 + len(self.brokers))) * 16, name='host%d' % len(self.brokers))
            self.assertEqual(s.call(broker, 'open', OPEN | {'run_id': broker.run_id})[0], 200)
            for seq, family in enumerate(ALL, 1):
                params = {'committee_id': 'C00000001', 'per_page': 100} if family not in ('totals', 'candidate_committees') else {'per_page': 100}
                status, answer = s.call(broker, 'fetch', {'seq': seq, 'endpoint': EXAMPLES[family], 'params': params})
                with self.subTest(named=named, family=family):
                    if family in families:
                        self.assertEqual((status, answer['attempts']), (200, 1))                    # the twin: a named family is served
                    else:
                        self.assertEqual((status, answer), (403, {'error': 'family_not_admitted'}))
            self.assertEqual(broker.transport.http_attempts, len(families))

    def test_forms_the_adapter_allows_but_this_run_never_uses_are_refused(self):
        broker = self.opened(families=host.parse_families('A,F,H4,totals,candidate_committees'), opener=s.PlainOpener())
        for seq, endpoint in enumerate(('schedules/schedule_b/123/', 'schedules/schedule_e/9/', 'communication_costs/', 'electioneering/', 'schedules/schedule_x/', '../schedules/schedule_b/',
                                        'schedules/schedule_b', '/schedules/schedule_b/', 'SCHEDULES/schedule_b/', 'committee/C0000001/totals/', 'candidate/X0AA00001/committees/',
                                        'https://api.open.fec.gov/v1/schedules/schedule_b/'), 1):
            with self.subTest(endpoint):
                self.assertEqual(self.fetch(broker, seq, endpoint)[0], 403)
        self.assertEqual(broker.transport.http_attempts, 0)

    def test_selectors_are_validated_before_any_request_is_admitted(self):
        broker = self.opened(opener=s.PlainOpener())
        bad = {'api_key': {'api_key': 'x'}, 'token': {'x_token': 'x'}, 'auth': {'Authorization': 'x'}, 'signature': {'signature': 'x'}, 'committee id': dict(PAGE, committee_id='C1'),
               'committee id type': dict(PAGE, committee_id=7), 'per_page 0': dict(PAGE, per_page=0), 'per_page 101': dict(PAGE, per_page=101), 'per_page text': dict(PAGE, per_page='100'),
               'per_page bool': dict(PAGE, per_page=True), 'a key value inside a selector': dict(PAGE, sort=cc.KEYS[0])}
        for name, params in bad.items():
            with self.subTest(name):
                self.assertEqual(self.fetch(broker, 1, params=params)[0], 400)
        for name, payload in {'seq 0': dict(seq=0), 'seq text': dict(seq='1'), 'seq bool': dict(seq=True), 'endpoint number': dict(endpoint=5), 'params list': dict(params=[1])}.items():
            with self.subTest(name):
                self.assertEqual(s.call(broker, 'fetch', {'seq': 1, 'endpoint': 'schedules/schedule_b/', 'params': PAGE} | payload)[0], 400)
        self.assertEqual(broker.transport.http_attempts, 0)
        with closing(sqlite3.connect(self.root / 'quota.sqlite')) as db:
            self.assertEqual(db.execute('SELECT COALESCE(SUM(requests_used),0) FROM api_request_budget').fetchone()[0], 0)       # nothing was charged
        self.assertEqual(self.fetch(broker, 1)[1]['attempts'], 1)                                # the twin

    def test_a_repeated_seq_and_an_identical_request_in_flight_are_refused_never_charged_twice(self):
        gate, entered = threading.Event(), threading.Event()
        opener = cc.ScriptedOpener(cc.World(), hook=lambda number, key, query: (entered.set(), gate.wait(5)))
        broker = self.opened(opener=opener)
        results = []
        worker = threading.Thread(target=lambda: results.append(self.fetch(broker, 1)))
        worker.start()
        self.assertTrue(entered.wait(5))
        self.assertEqual(self.fetch(broker, 2), (409, {'error': 'duplicate_in_flight'}))         # same endpoint and params under another seq
        self.assertEqual(self.fetch(broker, 1, params=dict(PAGE, committee_id='C00000002'))[1], {'error': 'duplicate_seq'})
        gate.set()
        worker.join(10)
        self.assertEqual(results[0][1]['attempts'], 1)
        self.assertEqual(self.fetch(broker, 1, params=dict(PAGE, committee_id='C00000002')), (409, {'error': 'duplicate_seq'}))
        self.assertEqual(self.fetch(broker, 3)[1]['attempts'], 1)                                # the same page may be asked again once the first answer is back
        self.assertEqual(broker.transport.http_attempts, 2)

    def test_the_physical_cap_is_held_by_the_broker_even_for_a_client_that_ignores_it(self):
        opener = cc.ScriptedOpener(cc.World())
        broker = self.opened(max_attempts=3, opener=opener)
        answers = [self.fetch(broker, n, params=dict(PAGE, committee_id=cc.cid(n)))[1] for n in range(1, 7)]
        self.assertEqual([a['attempts'] for a in answers], [1, 1, 1, 0, 0, 0])
        self.assertEqual([a.get('refusal') for a in answers[3:]], ['cap'] * 3)
        self.assertEqual(len(opener.calls), 3)
        with closing(sqlite3.connect(self.root / 'quota.sqlite')) as db:
            self.assertEqual(db.execute('SELECT SUM(requests_used) FROM api_request_budget').fetchone()[0], 3)
        broker.shutdown()
        self.assertEqual(json.loads(broker.receipt_path.read_text())['refusals'], {'cap': 3})


# ---------------------------------------------------------------- R1 accounting equality through the broker
class Accounting(Base):
    def run_remote(self, *, workers, name, world=None, keys=cc.KEYS, opener=None, seeds=cc.SEEDS, max_failed=100000, **options):
        broker = self.broker(opener=opener or cc.ScriptedOpener(world or cc.World()), keys=keys, name=name + '-host', **options)
        frontier = driver.Frontier(self.root / (name + '-state') / 'frontier.sqlite', include_later_cycle_context=True)
        self.addCleanup(frontier.close)
        frontier.seed(seeds)
        start = self.root / (name + '-START.json')
        start.write_text('{}')
        transport = s.client(broker, workers=workers, max_requests=options.get('max_attempts', 100000))
        with transport.session(start, 'c' * 64) as active:
            report = frontier.run(active, max_failed_attempts=max_failed, workers=workers)
        return frontier, transport, broker, report

    def check_equal_to_local(self, workers):
        local, _, _, local_report = cc.run_world(self.root, workers=workers, adapter=True, name='local', ledger=self.root / 'local-quota.sqlite')
        remote, transport, broker, report = self.run_remote(workers=workers, name='remote')
        self.assertEqual(cc.canonical(remote.path), cc.canonical(local.path))
        for name in cc.COUNT_FIELDS:
            self.assertEqual(report[name], local_report[name], name)
        self.assertEqual(report['stop'], local_report['stop'])
        recorded = remote.db.execute('SELECT SUM(attempt_count) FROM endpoints').fetchone()[0]
        broker.shutdown()
        receipt = json.loads(broker.receipt_path.read_text())
        self.assertEqual(transport.http_attempts, report['attempts_recorded'], 'the client count equals the outcomes the writer recorded')
        self.assertEqual(transport.http_attempts, recorded, 'and the endpoint attempt_count the ledger holds')
        self.assertEqual(receipt['physical_attempts_charged'], receipt['attempts_journalled'])
        self.assertEqual(receipt['attempts_journalled'], transport.http_attempts, 'the journal equals the client: nothing undelivered, nothing unrecorded')
        self.assertEqual(sum(receipt['by_status'].values()), receipt['attempts_journalled'])
        with closing(sqlite3.connect(self.root / 'quota.sqlite')) as db:
            self.assertEqual(db.execute('SELECT SUM(requests_used) FROM api_request_budget').fetchone()[0], receipt['attempts_journalled'], 'the shared quota ledger was charged once per physical attempt')
        problems = cc.conservation(self.root / 'remote-state', folders=[s.folder_of(broker)])
        problems.pop('attempts_vs_receipts', None)           # (no collector receipt in this control: the main() controls check it)
        self.assertEqual(problems, {}, 'every archived pair is referenced exactly once')
        self.assertIsNone(report['stop'])
        return local, transport, broker

    def test_remote_equals_local_and_every_attempt_is_accounted_at_n1(self):
        _, transport, broker = self.check_equal_to_local(1)
        local_names = sorted(p.name for p in (self.root / 'archive-local').iterdir())
        self.assertEqual(sorted(p.name for p in s.folder_of(broker).iterdir()), local_names, 'N=1: the very same archive files, attempt numbers and hashes included')

    def test_remote_equals_local_and_every_attempt_is_accounted_at_n4(self):
        self.check_equal_to_local(4)

    def test_the_journal_is_ordered_complete_and_bound_to_the_archive(self):
        _, transport, broker, report = self.run_remote(workers=4, name='remote')
        broker.shutdown()
        lines = s.journal(broker)
        attempts = [line for line in lines if line['event'] == 'attempt']
        self.assertEqual((lines[0]['event'], lines[0]['start_sha256'], lines[0]['collector_sha256'], lines[-1]['event']), ('open', hashlib.sha256(b'{}').hexdigest(), 'c' * 64, 'close'))
        self.assertEqual([line['n'] for line in attempts], list(range(1, len(attempts) + 1)), 'n increases down the file')
        self.assertEqual(len(attempts), transport.http_attempts)
        self.assertEqual(len({line['seq'] for line in attempts}), len(attempts), 'one line per seq')
        folder = s.folder_of(broker)
        hashes = {hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.glob('*.body.bin')}
        archived = [line for line in attempts if line['archived']]
        self.assertTrue(archived)
        self.assertEqual({line['sha256'] for line in archived}, hashes)
        self.assertEqual(sum(1 for line in attempts if not line['archived']), 1, 'the one scripted connection failure leaves a journal line and no pair')
        for line in attempts:
            self.assertTrue(line['key_id'].startswith('openfec:sha256:'))
        receipt = json.loads(broker.receipt_path.read_text())
        self.assertEqual(sum(receipt['by_key_id'].values()), len(attempts))
        self.assertEqual(receipt['journal']['sha256'], hashlib.sha256(broker.journal_path.read_bytes()).hexdigest())
        self.assertEqual(receipt['bytes_archived'], sum(p.stat().st_size for p in folder.glob('*.body.bin')))

    def test_the_rate_snapshot_counts_attempts_by_window_and_reports_the_pool(self):
        broker = self.broker()
        self.assertEqual(broker.rate_snapshot(), {'state': 'new', 'attempts': 0, 'attempts_last_60s': 0, 'attempts_last_3600s': 0, 'requests_per_hour_60s': 0, 'rate_limited_429': 0, 'refusals': {}})
        self.assertEqual(s.call(broker, 'open', OPEN)[0], 200)
        for seq in range(1, 4):
            self.fetch(broker, seq, params=dict(PAGE, committee_id=cc.cid(seq)))
        snapshot = broker.rate_snapshot()
        self.assertEqual((snapshot['state'], snapshot['attempts'], snapshot['attempts_last_60s'], snapshot['attempts_last_3600s'], snapshot['requests_per_hour_60s']), ('open', 3, 3, 3, 180))
        self.assertEqual((snapshot['healthy'], snapshot['cooling'], snapshot['in_flight']), (len(cc.KEYS), 0, 0))
        later = time.monotonic() + 61
        with mock.patch.object(time, 'monotonic', lambda: later):
            self.assertEqual(broker.rate_snapshot()['attempts_last_60s'], 0)
            self.assertEqual(broker.rate_snapshot()['attempts_last_3600s'], 3)

    def test_original_bytes_are_durable_on_the_host_before_the_answer_leaves_the_broker(self):
        broker = self.opened()
        syncs = []
        real_fsync = os.fsync
        with mock.patch.object(os, 'fsync', lambda fd: (syncs.append(fd), real_fsync(fd))[1]):
            status, answer = self.fetch(broker, 1)
        evidence = answer['evidence']
        body = Path(evidence['path']).read_bytes()                                                      # on disk by the time the client could see anything
        self.assertEqual(hashlib.sha256(body).hexdigest(), answer['sha256'])
        self.assertEqual(json.loads(Path(evidence['receipt_path']).read_text())['sha256'], answer['sha256'])
        self.assertGreaterEqual(len(syncs), 3, 'body, metadata and journal line are each fsynced')

    def failing_body_write(self):
        real = accounted.durable_new

        def failing(path, body):
            if str(path).endswith('.body.bin'):
                raise OSError('disk full (inert)')
            return real(path, body)
        return mock.patch.object(accounted, 'durable_new', failing)

    def test_an_archive_failure_returns_no_bytes_is_journalled_and_stops_the_pool(self):
        broker = self.opened()
        with self.failing_body_write():
            status, answer = self.fetch(broker, 1)
        self.assertEqual((status, answer['attempts'], answer['refusal']), (200, 1, 'archive_failed'))
        self.assertNotIn('body_b64', answer)
        self.assertIsNone(answer['evidence'])
        self.assertFalse([line for line in s.journal(broker) if line['event'] == 'attempt'][0]['archived'])
        self.assertEqual(self.fetch(broker, 2), (200, {'attempts': 0, 'refusal': 'stopped'}), 'a failed archive is a global stop: nobody else may send')
        self.assertEqual(broker.transport.http_attempts, 1)

    def test_the_client_maps_an_archive_failure_to_the_adapter_exception_with_the_attempt_counted(self):
        broker = self.opened()
        transport = s.client(broker)
        call = {}
        with self.failing_body_write():
            with self.assertRaisesRegex(accounted.FECArchiveFailed, 'could not be stored'):
                transport.fetch('schedules/schedule_b/', PAGE, call=call)
        self.assertEqual((call['attempts'], transport.http_attempts, transport.reserved), (1, 1, 0))
        self.assertNotIn('evidence', call)
        with self.assertRaisesRegex(accounted.FECAdmissionRefused, 'pool is stopped'):
            transport.fetch('schedules/schedule_b/', dict(PAGE, committee_id='C00000002'))
        self.assertEqual((transport.http_attempts, transport.reserved), (1, 0))

    def test_bytes_that_do_not_match_the_attested_hash_are_refused_not_admitted(self):
        broker = self.opened()
        real = s.direct_post(broker)

        def tampering(operation, payload):
            status, answer = real(operation, payload)
            if operation == 'fetch' and answer.get('attempts') == 1 and answer.get('body_b64'):
                import base64
                body = bytearray(base64.b64decode(answer['body_b64']))
                body[0] ^= 1
                answer['body_b64'] = base64.b64encode(bytes(body)).decode()
            return status, answer
        transport = s.client(broker, post=tampering)
        call = {}
        with self.assertRaisesRegex(accounted.FECArchiveFailed, 'do not match the evidence it attested'):
            transport.fetch('schedules/schedule_b/', PAGE, call=call)
        self.assertEqual((call['attempts'], transport.http_attempts, transport.reserved, len(transport.records)), (1, 1, 0, 0), 'the attempt is counted, the bytes are not admitted')
        self.assertNotIn('evidence', call)

    def test_an_answer_without_a_usable_result_is_broker_unavailable_and_never_a_result(self):
        broker = self.opened()
        real = s.direct_post(broker)
        good = real('fetch', {'seq': 99, 'endpoint': 'schedules/schedule_b/', 'params': PAGE})[1]
        shapes = {'http 500': (500, {'error': 'internal'}), 'http 403': (403, {'error': 'forbidden'}), 'not a mapping': (200, ['x']), 'attempts 2': (200, dict(good, attempts=2)),
                  'attempts missing': (200, {k: v for k, v in good.items() if k != 'attempts'}), 'unknown refusal': (200, {'attempts': 0, 'refusal': 'because'}), 'no refusal': (200, {'attempts': 0})}
        for name, shape in shapes.items():
            with self.subTest(name):
                transport = s.client(broker, post=lambda operation, payload, shape=shape: shape)
                with self.assertRaises(pool.BrokerUnavailable):
                    transport.fetch('schedules/schedule_b/', PAGE)
                self.assertEqual((transport.http_attempts, transport.reserved, transport.inflight), (0, 0, 0))
        without = dict(good)
        without.pop('evidence')
        transport = s.client(broker, post=lambda operation, payload: (200, without))
        with self.assertRaises(pool.BrokerUnavailable):
            transport.fetch('schedules/schedule_b/', PAGE)
        self.assertEqual((transport.http_attempts, transport.reserved), (1, 0), 'an attempt the broker says it made is counted even when its evidence is missing')

    def test_a_stopped_client_refuses_locally_and_never_asks_the_broker(self):
        broker = self.opened()
        asked = []
        real = s.direct_post(broker)
        transport = s.client(broker, post=lambda operation, payload: (asked.append(operation), real(operation, payload))[1])
        transport.pool.stop()
        with self.assertRaisesRegex(accounted.FECAdmissionRefused, 'pool is stopped'):
            transport.fetch('schedules/schedule_b/', PAGE)
        self.assertEqual((asked, transport.http_attempts, transport.reserved, broker.transport.http_attempts), ([], 0, 0, 0))

    def test_a_body_read_that_fails_after_admission_is_a_charged_journalled_per_endpoint_failure_never_a_stop(self):
        faults = {'TimeoutError': TimeoutError('timed out'), 'ConnectionResetError': ConnectionResetError('reset by peer'), 'IncompleteRead': http.client.IncompleteRead(b'part', 100)}
        for index, (name, fault) in enumerate(faults.items(), start=1):
            with self.subTest(fault=name):
                run = '%02x' % (0x30 + index) * 16
                opener = FaultyOpener(fault)
                broker = self.broker(run_id=run, name='fault%d' % index, opener=opener)
                self.assertEqual(s.call(broker, 'open', OPEN | {'run_id': run})[0], 200)
                answers, real = [], s.direct_post(broker)

                def post(operation, payload):
                    result = real(operation, payload)
                    answers.append((operation, result))
                    return result
                transport = s.client(broker, run_id=run, post=post)
                call = {}
                with self.assertRaises(URLError) as caught:
                    transport.fetch('schedules/schedule_b/', PAGE, call=call)
                fetched = [result for operation, result in answers if operation == 'fetch']
                self.assertEqual(len(fetched), 1)
                self.assertEqual((fetched[0][0], {k: fetched[0][1].get(k) for k in ('attempts', 'status', 'error')}), (200, {'attempts': 1, 'status': 0, 'error': 'connection'}),
                                 'a charged attempt answered as a per-endpoint failure, not an HTTP 500 the client reads as a dead broker')
                self.assertEqual(driver._status(caught.exception), 'blocked', 'the collector files it as the endpoint failure the local transport gives')
                self.assertFalse(driver._is_global(caught.exception), 'a mid-body fault is never a global stop')
                self.assertEqual((call['attempts'], transport.http_attempts, transport.reserved, opener.calls), (1, 1, 0, 1))
                lines = [line for line in s.journal(broker) if line['event'] == 'attempt']
                self.assertEqual((len(lines), lines[0]['status'], lines[0]['error'], lines[0]['archived']), (1, 0, 'connection', False))
                receipt = broker.shutdown()
                self.assertEqual((receipt['physical_attempts_charged'], receipt['attempts_journalled'], broker.transport.http_attempts), (1, 1, 1), 'charged == journalled == 1')

    def test_a_fault_before_the_charge_is_an_internal_error_and_a_global_stop_never_a_phantom_attempt(self):
        # delta review D4 (probe P1): the F1 except also caught an OSError raised BEFORE the charge (the budget file), journalled it as a charged attempt and answered it as a per-endpoint failure
        run = 'ee' * 16
        opener = s.PlainOpener()
        broker = self.broker(run_id=run, name='precharge', opener=opener)
        self.assertEqual(s.call(broker, 'open', OPEN | {'run_id': run})[0], 200)
        answers, real = [], s.direct_post(broker)

        def post(operation, payload):
            result = real(operation, payload)
            answers.append((operation, result))
            return result
        transport = s.client(broker, run_id=run, post=post)
        with mock.patch.object(s.accounted, 'budget_reserve', side_effect=PermissionError('the budget file is not readable')):
            with self.assertRaises(pool.BrokerUnavailable):
                transport.fetch('schedules/schedule_b/', PAGE)
        self.assertEqual([result for operation, result in answers if operation == 'fetch'], [(500, {'error': 'internal'})], 'nothing was charged: an internal error, which the client reads as a dead broker')
        self.assertEqual([line for line in s.journal(broker) if line['event'] == 'attempt'], [], 'no phantom attempt in the journal')
        receipt = broker.shutdown()
        self.assertEqual((receipt['physical_attempts_charged'], receipt['attempts_journalled'], broker.transport.http_attempts, opener.calls), (0, 0, 0, []), 'charged == journalled == 0')

    def test_a_credential_echo_is_refused_on_the_host_and_only_a_sanitized_record_remains(self):
        body = ('{"results":[],"echo":"%s"}' % cc.KEYS[1]).encode()
        broker = self.opened(opener=s.PlainOpener(body=body))
        status, answer = self.fetch(broker, 1)
        self.assertEqual((status, answer['attempts'], answer['refusal']), (200, 1, 'secret_echo'))
        self.assertNotIn('body_b64', answer)
        record = answer['evidence']
        self.assertEqual((record['original_response_bytes'], record['raw_body_persisted'], record['refused_secret_echo']), (False, False, True))
        self.assertFalse(list(s.folder_of(broker).glob('*.body.bin')))
        self.assertNotIn(cc.KEYS[1], json.dumps(answer))
        self.assertEqual(self.fetch(broker, 2)[1]['refusal'], 'stopped')
        self.assertEqual(host.quarantine_run(broker.archive_root, s.RUN)['kept_refusal_records'], 1)

    def test_the_client_maps_a_credential_echo_to_the_adapter_exception_and_keeps_the_sanitized_record(self):
        body = ('{"results":[],"echo":"%s"}' % cc.KEYS[1]).encode()
        broker = self.opened(opener=s.PlainOpener(body=body))
        transport = s.client(broker)
        call = {}
        with self.assertRaisesRegex(accounted.FECArchiveFailed, 'echoed a credential'):
            transport.fetch('schedules/schedule_b/', PAGE, call=call)
        self.assertEqual((call['attempts'], call['evidence']['refused_secret_echo'], len(transport.records)), (1, True, 1))
        self.assertEqual(transport.http_attempts, 1)

    def test_a_non_200_answer_is_a_charged_archived_attempt_that_reaches_the_client_as_http_error(self):
        broker = self.opened(opener=s.PlainOpener(status=504, body=b'{}'))
        transport = s.client(broker)
        call = {}
        with self.assertRaises(HTTPError) as caught:
            transport.fetch('schedules/schedule_b/', PAGE, call=call)
        self.assertEqual(caught.exception.code, 504)
        self.assertEqual((call['attempts'], call['evidence']['status'], transport.http_attempts), (1, 504, 1))
        self.assertTrue(Path(call['evidence']['path']).is_file())
        self.assertEqual(len(transport.records), 1)

    def test_a_connection_failure_is_a_charged_attempt_without_an_archive_pair(self):
        class Down:
            def open(self, request, timeout):
                raise cc.URLError('scripted (inert)')
        broker = self.opened(opener=Down())
        transport = s.client(broker)
        call = {}
        with self.assertRaisesRegex(cc.URLError, 'connection attempt failed'):
            transport.fetch('schedules/schedule_b/', PAGE, call=call)
        self.assertEqual((call['attempts'], transport.http_attempts, 'evidence' in call), (1, 1, False))
        self.assertEqual(list(s.folder_of(broker).iterdir()), [])
        self.assertEqual([line['archived'] for line in s.journal(broker) if line['event'] == 'attempt'], [False])

    def test_a_lost_answer_is_a_broker_unavailable_global_stop_with_the_endpoint_left_pending(self):
        broker = self.broker()
        frontier = driver.Frontier(self.root / 'f.sqlite', include_later_cycle_context=True)
        self.addCleanup(frontier.close)
        frontier.seed(cc.SMALL_SEEDS)
        start = self.root / 'START.json'
        start.write_text('{}')
        real = s.direct_post(broker)
        counter = []

        def flaky(operation, payload):
            if operation == 'fetch':
                counter.append(1)
                if len(counter) == 3:
                    raise pool.BrokerUnavailable('answer lost (inert)')
            return real(operation, payload)
        transport = s.client(broker, post=flaky)
        with transport.session(start, 'c' * 64) as active:
            report = frontier.run(active, max_failed_attempts=100)
        self.assertEqual(report['stop']['error_type'], 'BrokerUnavailable')
        self.assertTrue(report['stop']['global_admission_archive_auth_quota_or_failure_allowance'])
        self.assertEqual(transport.http_attempts, report['attempts_recorded'])
        self.assertEqual(frontier.db.execute("SELECT COUNT(*) FROM endpoints WHERE status IN ('blocked','inconsistent','source_refused')").fetchone()[0], 0)
        self.assertEqual(frontier.db.execute("SELECT COUNT(*) FROM failures WHERE error_type='BrokerUnavailable'").fetchone()[0], 1)
        broker.shutdown()
        # the fake dropped the request before the wire, so journal and client agree; with a real lost ANSWER the journal is one ahead, which is why the observer
        # compares journal = client attempts + undelivered (next control)
        self.assertEqual(json.loads(broker.receipt_path.read_text())['attempts_journalled'], transport.http_attempts)

    def test_an_undelivered_answer_is_the_gap_between_the_journal_and_the_client(self):
        broker = self.opened()
        first = self.fetch(broker, 1)[1]
        second = self.fetch(broker, 2, params=dict(PAGE, committee_id='C00000002'))[1]
        broker.mark_delivery(first['n'], True)
        broker.mark_delivery(second['n'], False)                              # the HTTP wrapper calls this when writing the response fails
        broker.shutdown()
        receipt = json.loads(broker.receipt_path.read_text())
        self.assertEqual((receipt['attempts_journalled'], receipt['delivered'], receipt['undelivered_attempts']), (2, 1, [second['n']]))
        self.assertEqual([(line['n'], line['delivered']) for line in s.journal(broker) if line['event'] == 'delivery'], [(first['n'], True), (second['n'], False)])


# ---------------------------------------------------------------- per-key 429 isolation, no pacing
class KeyIsolation(Base):
    def run_with(self, opener, *, keys, workers=4, seeds=cc.SMALL_SEEDS, **options):
        broker = self.broker(opener=opener, keys=keys, **options)
        frontier = driver.Frontier(self.root / 'f.sqlite', include_later_cycle_context=True)
        self.addCleanup(frontier.close)
        frontier.seed(seeds)
        start = self.root / 'START.json'
        start.write_text('{}')
        transport = s.client(broker, workers=workers)
        with transport.session(start, 'c' * 64) as active:
            report = frontier.run(active, max_failed_attempts=100, workers=workers)
        return frontier, transport, broker, report

    def test_a_429_cools_only_its_own_key_and_the_other_keys_finish_the_run(self):
        keys = cc.KEYS[:3]
        opener = cc.ScriptedOpener(cc.World(failures=False), key_responses={keys[0]: (b'{}', 429, {'Retry-After': '30', 'Content-Type': 'application/json'})}, delay=0.01)
        frontier, transport, broker, report = self.run_with(opener, keys=keys)
        self.assertIsNone(report['stop'])
        self.assertGreaterEqual(report['rate_limited_responses'], 1)
        self.assertLessEqual(report['rate_limited_responses'], 4, 'a cooled key gets no more requests once its cooldown is set (a few already in flight)')
        self.assertEqual(frontier.db.execute("SELECT COUNT(*) FROM endpoints WHERE status NOT IN ('complete','unsupported')").fetchone()[0], 0, 'another key served every 429d endpoint')
        self.assertEqual(frontier.db.execute("SELECT COUNT(*) FROM endpoints WHERE retry_after IS NOT NULL").fetchone()[0], 0, 'back to pending at once, not parked')
        broker.shutdown()
        receipt = json.loads(broker.receipt_path.read_text())
        hit = {key[-12:]: sum(1 for call in opener.calls if call[1] == key) for key in keys}
        self.assertEqual(sum(hit.values()), transport.http_attempts)
        self.assertEqual(receipt['by_status']['429'], report['rate_limited_responses'])
        self.assertEqual(receipt['key_levels'], {'throttled': report['rate_limited_responses']})
        self.assertEqual(sum(1 for call in opener.calls if call[1] == keys[0]), report['rate_limited_responses'])
        self.assertEqual(self.failure_rows(frontier, 'HTTPError'), report['rate_limited_responses'], 'each 429 keeps its failure row and archive pair')
        self.assertEqual(report['attempts_recorded'], transport.http_attempts)

    def failure_rows(self, frontier, kind):
        return frontier.db.execute('SELECT COUNT(*) FROM failures WHERE error_type=? AND detail LIKE ?', (kind, '%not successful%')).fetchone()[0]

    def test_every_key_throttled_is_the_quota_stop_with_nothing_parked(self):
        keys = cc.KEYS[:2]
        opener = cc.ScriptedOpener(cc.World(failures=False), key_responses={key: (b'{}', 429, {'Retry-After': '30'}) for key in keys})
        frontier, transport, broker, report = self.run_with(opener, keys=keys, workers=2)
        self.assertEqual(report['stop']['error_type'], 'FECAdmissionRefused')
        self.assertIn('quota', report['stop']['detail'])
        self.assertEqual(frontier.db.execute("SELECT COUNT(*) FROM endpoints WHERE status IN ('blocked','inconsistent')").fetchone()[0], 0)
        self.assertLessEqual(transport.http_attempts, len(keys) * 2)

    def test_there_is_no_pacing_and_the_broker_never_sleeps_on_the_request_path(self):
        opener = cc.ScriptedOpener(cc.World(failures=False))
        started = time.monotonic()
        with mock.patch.object(time, 'sleep', side_effect=AssertionError('the broker must not sleep between requests')):
            frontier, transport, broker, report = self.run_with(opener, keys=cc.KEYS, workers=4)
        self.assertIsNone(report['stop'])
        self.assertLess(time.monotonic() - started, 30)
        self.assertGreater(transport.http_attempts, 20)
        self.assertNotIn('pacing', json.dumps(s.descriptor_of(broker)))

    def test_an_invalid_key_403_is_labelled_and_is_a_global_stop_as_in_r1b(self):
        keys = cc.KEYS[:2]
        body = b'{"error":{"code":"API_KEY_INVALID","message":"inert"}}'
        opener = cc.ScriptedOpener(cc.World(failures=False), key_responses={keys[0]: (body, 403, {'Content-Type': 'application/json'})})
        broker = self.opened(opener=opener, keys=keys)
        statuses = []
        for seq in range(1, 7):
            answer = self.fetch(broker, seq, params=dict(PAGE, committee_id=cc.cid(seq)))[1]
            statuses.append((answer['attempts'], answer.get('status'), answer.get('key_level'), answer.get('refusal')))
        first = next(item for item in statuses if item[1] == 403)
        self.assertEqual(first[:3], (1, 403, 'invalid_key'))
        self.assertEqual(statuses[statuses.index(first) + 1][3], 'stopped', 'the pool stops at the 403 so nobody waiting can send (R1b M1)')


# ---------------------------------------------------------------- a crash, the host quarantine, and recovery against a read-only archive root
class Kill(BaseException):
    """A process that dies: nothing catches it."""


class Crash(Base):
    def seeds_file(self, rows=cc.SMALL_SEEDS):
        self.write_seeds(rows)

    def start_run(self, broker, *, kill_after=None, flags=(), init=True):
        """main() in broker mode; the writer dies at its Nth commit (kill_after) so its page is archived on the host but never reaches the ledger."""
        self.seeds_file()
        counter = []
        real_commit = driver.Frontier._commit

        def commit(frontier, job, outcome):
            counter.append(1)
            if kill_after is not None and len(counter) == kill_after:
                raise Kill()
            return real_commit(frontier, job, outcome)
        patches = [mock.patch.object(driver.Frontier, '_commit', commit)] if kill_after is not None else []
        for patch in patches:
            patch.start()
        try:
            try:
                s.invoke_broker(self, broker, ['--include-later-cycle-context', '--max-requests', '100000', *(['--init-new-ledger'] if init else []), *flags], state=self.state, run_id=broker.run_id)
            except Kill:
                return 'killed'
            return 'finished'
        finally:
            for patch in patches:
                patch.stop()

    def test_a_writer_that_dies_after_the_host_archived_a_page_is_recovered_through_a_read_only_archive_root(self):
        broker = self.broker()
        self.assertEqual(self.start_run(broker, kill_after=4), 'killed')
        host_folder = s.folder_of(broker)
        crashed, legacy = driver.unresolved_runs(self.state, broker.archive_root)
        self.assertEqual((crashed, legacy), ([s.RUN], []))
        with self.assertRaises(SystemExit) as refused:                        # a new live run refuses while a run is unresolved
            self.start_run(self.broker(run_id='cd' * 16, name='other'), init=False)
        self.assertIn('Unresolved collector run', str(refused.exception))
        before = sorted(p.name for p in host_folder.iterdir())
        printed = driver.recover_crashed_runs(self.state, broker.archive_root)
        self.assertEqual(sorted(p.name for p in host_folder.iterdir()), before, 'the archive root is read-only to the recovery: nothing moved')
        self.assertTrue(printed['recovered'] and printed['replayed_pairs'] == 1, printed)
        receipt = json.loads(Path(printed['receipt']).read_text())
        self.assertTrue(receipt['recovered_after_crash'])
        self.assertEqual(receipt['stop']['error_type'], 'CrashRecovered')
        self.assertEqual(receipt['raw_archive_directory'], str(host_folder))
        self.assertEqual(receipt['http_attempts'], len(list(host_folder.glob('*.body.bin'))), 'every archived pair is an attempt the receipt now counts')
        self.assertEqual(cc.conservation(self.state, folders=[host_folder]), {}, 'every pair is referenced exactly once after the replay')
        self.assertEqual(driver.unresolved_runs(self.state, broker.archive_root), ([], []))
        self.assertEqual(driver.recover_crashed_runs(self.state, broker.archive_root), {'recovered': False, 'reason': 'No unresolved collector run.'})

    def test_a_broker_that_dies_mid_write_leaves_a_body_without_metadata_that_only_the_host_quarantine_may_move(self):
        broker = self.broker()
        real = accounted.durable_new
        seen = []

        def dying(path, body):
            seen.append(Path(path).name)
            if str(path).endswith('.json') and len([n for n in seen if n.endswith('.json')]) == 3:
                raise Kill()                                                  # the body is durable, the metadata never lands, nothing answers
            return real(path, body)
        with mock.patch.object(accounted, 'durable_new', dying):
            self.assertEqual(self.start_run(broker), 'killed')
        folder = s.folder_of(broker)
        stems = {p.name[:-9] for p in folder.glob('*.body.bin')} ^ {p.name[:-5] for p in folder.glob('attempt-*.json')}
        self.assertEqual(len(stems), 1, 'exactly one unpaired file')
        with self.assertRaises(driver.RecoveryRefused) as caught:
            driver.recover_crashed_runs(self.state, broker.archive_root)
        self.assertIn('api_host_pool.py quarantine', str(caught.exception))
        self.assertEqual(driver.unresolved_runs(self.state, broker.archive_root), ([s.RUN], []), 'nothing was changed by the refusal')
        moved = host.quarantine_run(broker.archive_root, s.RUN)
        self.assertEqual(moved['quarantined'], 1)
        manifest = json.loads(Path(moved['manifest']).read_text())
        self.assertEqual((manifest['purpose'], manifest['files'][0]['reason'], manifest['never_replayed_here']), ('host-crash-quarantine', 'body without metadata', True))
        self.assertTrue((broker.archive_root / 'crash-quarantine' / s.RUN / manifest['files'][0]['name']).is_file())
        self.assertEqual(host.quarantine_run(broker.archive_root, s.RUN)['quarantined'], 0, 'idempotent: nothing left to move')
        printed = driver.recover_crashed_runs(self.state, broker.archive_root)
        self.assertTrue(printed['recovered'], printed)
        self.assertEqual(cc.conservation(self.state, folders=[folder]), {})

    def test_the_host_quarantine_moves_only_what_is_not_a_complete_binding_pair(self):
        broker = self.opened()
        for seq in range(1, 4):
            self.fetch(broker, seq, params=dict(PAGE, committee_id=cc.cid(seq)))
        folder = s.folder_of(broker)
        pairs = sorted(p.name for p in folder.iterdir())
        (folder / 'attempt-000010-aaaaaaaaaaaa.body.bin').write_bytes(b'half')                      # body without metadata
        (folder / 'attempt-000011-bbbbbbbbbbbb.json').write_text('{"sha256":"0"}')                  # metadata that binds nothing
        (folder / 'attempt-000012-cccccccccccc.json').write_text('{"truncated": ')                  # truncated metadata
        (folder / 'attempt-000013-dddddddddddd.body.bin').write_bytes(b'x')                         # a body whose metadata names other bytes
        meta = json.loads((folder / pairs[1]).read_text())
        (folder / 'attempt-000013-dddddddddddd.json').write_text(json.dumps(meta | {'attempt': 13}))
        (folder / 'notes.tmp').write_text('stray')
        (folder / 'attempt-000014-eeeeeeeeeeee.json').write_text(json.dumps({'refused_secret_echo': True, 'raw_body_persisted': False, 'original_response_bytes': False}))   # kept
        moved = host.quarantine_run(broker.archive_root, s.RUN)
        self.assertEqual((moved['quarantined'], moved['kept_pairs'], moved['kept_refusal_records']), (6, 3, 1))
        self.assertEqual(sorted(p.name for p in folder.iterdir()), sorted(pairs + ['attempt-000014-eeeeeeeeeeee.json']))
        target = broker.archive_root / 'crash-quarantine' / s.RUN
        self.assertEqual(len([p for p in target.iterdir() if not p.name.startswith('QUARANTINE-')]), 6)
        for bad in ('xyz', '', None):
            with self.assertRaises(ValueError):
                host.quarantine_run(broker.archive_root, bad)
        with self.assertRaises(ValueError):
            host.quarantine_run(broker.archive_root, 'cd' * 16)

    def test_the_quarantine_command_line_prints_the_manifest_and_refuses_quietly(self):
        broker = self.opened()
        self.fetch(broker, 1)
        (s.folder_of(broker) / 'stray.tmp').write_text('x')
        with mock.patch.object(sys, 'argv', ['api_host_pool.py', 'quarantine', '--archive-root', str(broker.archive_root), '--run-id', s.RUN]):
            with mock.patch('builtins.print') as printed:
                self.assertEqual(host.main(), 0)
        self.assertEqual(json.loads(printed.call_args[0][0])['quarantined'], 1)
        with mock.patch.object(sys, 'argv', ['api_host_pool.py', 'quarantine', '--archive-root', str(broker.archive_root), '--run-id', 'bad']):
            with mock.patch('sys.stderr'):
                self.assertEqual(host.main(), 2)


class Cli(unittest.TestCase):
    """api_host_pool.py main(): the default scope is the v1 broker exactly as before; --scope spending-evidence-v2 is the new one; mixing their arguments is a parser error."""
    def run_main(self, *arguments):
        with mock.patch.object(sys, 'argv', ['api_host_pool.py', *arguments]), mock.patch('sys.stderr'):
            try:
                return host.main()
            except SystemExit as stop:
                return ('exit', stop.code)

    def test_the_default_scope_is_the_v1_broker_and_still_needs_its_source_database(self):
        with mock.patch.object(host, 'serve') as v1, mock.patch.object(host, 'serve_evidence') as v2, tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'job.sqlite').touch()
            with mock.patch.dict(os.environ, {'COLLECT_HOST': tmp}):
                self.assertEqual(self.run_main('--run-directory', tmp, '--watch-pid', '5', '--source-database', '/collect/job.sqlite'), 0)
            self.assertEqual((v1.call_count, v2.call_count), (1, 0))
            self.assertEqual(self.run_main('--run-directory', tmp, '--watch-pid', '5'), ('exit', 2), 'v1 without its source database is refused as before')
            self.assertEqual(self.run_main('--run-directory', tmp, '--watch-pid', '5', '--source-database', '/collect/job.sqlite', '--run-id', s.RUN), ('exit', 2))
            self.assertEqual(self.run_main('--run-directory', tmp, '--watch-pid', '5', '--source-database', '/unmapped/job.sqlite'), 2, 'an unmapped database is refused with the old generic message')
            self.assertEqual(v1.call_count, 1)

    def test_the_v2_scope_needs_its_run_and_takes_the_families(self):
        with mock.patch.object(host, 'serve') as v1, mock.patch.object(host, 'serve_evidence') as v2, tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self.run_main('--scope', 'spending-evidence-v2', '--run-directory', tmp, '--watch-pid', '5', '--run-id', s.RUN, '--archive-root', tmp, '--max-attempts', '300',
                                           '--families', 'totals,F'), 0)
            self.assertEqual((v1.call_count, v2.call_count), (0, 1))
            call = v2.call_args
            self.assertEqual((call.kwargs['run_id'], call.kwargs['max_attempts'], call.kwargs['families'], call.args[1]), (s.RUN, 300, ['B', 'E', 'F', 'totals'], 5))
            for missing in ('--run-id', '--archive-root', '--max-attempts'):
                arguments = {'--run-id': s.RUN, '--archive-root': tmp, '--max-attempts': '300'}
                arguments.pop(missing)
                flat = [item for pair in arguments.items() for item in pair]
                self.assertEqual(self.run_main('--scope', 'spending-evidence-v2', '--run-directory', tmp, '--watch-pid', '5', *flat), ('exit', 2), missing)
            self.assertEqual(self.run_main('--scope', 'spending-evidence-v2', '--run-directory', tmp, '--watch-pid', '5', '--run-id', s.RUN, '--archive-root', tmp, '--max-attempts', '3',
                                           '--source-database', '/collect/x'), ('exit', 2), 'a v1 argument beside the v2 scope')
            self.assertEqual(self.run_main('--scope', 'spending-evidence-v2', '--run-directory', tmp, '--watch-pid', '5', '--run-id', s.RUN, '--archive-root', tmp, '--max-attempts', '3',
                                           '--families', 'B,nonsense'), 2, 'an unknown family is refused with the generic message, nothing started')
            self.assertEqual(v2.call_count, 1)


if __name__ == '__main__':
    unittest.main()

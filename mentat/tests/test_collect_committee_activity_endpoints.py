"""Inert F/H4 activation, reserved-selector, pagination, retention and adapter-composition controls.

No credentials, provider, network, product state or canonical collection state: every database,
ledger and archive lives in a temporary directory, credential loading and sockets are patched to
fail, and the real accounted transport only receives a scripted fake opener.
"""
from contextlib import closing, redirect_stdout
from datetime import date, timedelta
from decimal import Decimal
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock
from urllib.parse import parse_qsl, urlsplit

TOOLS = Path(__file__).resolve().parents[1] / 'tools'
sys.path.insert(0, str(TOOLS))
import collect_committee_activity as driver
import spending_endpoint_contracts as contracts
import fec_accounted_client as accounted
import fec_key_store
from api_request_budget import budget_id

C1, C2, C3, C9 = 'C00000001', 'C00000002', 'C00000003', 'C00000099'
F_ID = contracts.binding('F')['contract_id']
H4_ID = contracts.binding('H4')['contract_id']
BOTH = [F_ID, H4_ID]
KEY = 'inert-D069-synthetic-key-not-a-provider-credential'
SCHEDULE_B, SCHEDULE_E = 'schedules/schedule_b/', 'schedules/schedule_e/'
RESERVED = ('committee_id', 'two_year_transaction_period', 'cycle', 'is_notice', 'per_page', 'page', 'sort',
            'sort_hide_null', 'sort_null_only', 'sort_nulls_last', 'min_date', 'max_date', 'min_amount',
            'max_amount', 'candidate_id', 'recipient_committee_id', 'api_key', 'contributor_id', 'image_number')


# ---------------------------------------------------------------- synthetic provider data
def sid(n):
    return str(9000000000000000000 + n)


def amount(n):
    return Decimal(('-12.50', '0', '10.25', '0.01')[n % 4])


def f_rows(count, start=0):
    return [{'sub_id': sid(start + n), 'expenditure_amount': amount(n), 'committee_id': C1,
             'subordinate_committee_id': C2, 'recipient_committee_id': C3, 'candidate_id': 'H0AA00001',
             'expenditure_date': '2024-01-02'} for n in range(count)]


def h4_rows(count, null_tail=0):
    rows = []
    for n in range(count):
        day = None if n >= count - null_tail else (date(2024, 12, 28) - timedelta(days=n)).isoformat()
        rows.append({'sub_id': sid(n), 'disbursement_amount': amount(n), 'event_purpose_date': day,
                     'committee_id': C1, 'spender_committee_name': 'INERT PARTY', 'payee_name': 'INERT VENDOR'})
    return rows


def f_response(rows, count, page, *, size=100, exact=True):
    pagination = {'is_count_exact': exact, 'page': page, 'per_page': size}
    if count is not None:
        pagination.update(count=count, pages=(count + size - 1) // size)
    return {'results': rows, 'pagination': pagination}


def h4_response(rows, count, cursor, *, size=100, exact=True):
    pagination = {'is_count_exact': exact, 'last_indexes': cursor, 'per_page': size}
    if count is not None:
        pagination.update(count=count, pages=(count + size - 1) // size)
    return {'results': rows, 'pagination': pagination}


def legacy_response(rows=(), count=None, cursor=None, exact=True):
    pagination = {'count': len(rows) if count is None else count, 'is_count_exact': exact, 'per_page': 100,
                  'pages': 1, 'last_indexes': cursor}
    return {'results': list(rows), 'pagination': pagination}


def empty_page(endpoint):
    if endpoint.endswith('schedule_f/'):
        return f_response([], 0, 1)
    if endpoint.endswith('schedule_h4/'):
        return h4_response([], 0, None)
    return legacy_response()


def f_server(rows, *, exact=True, reported=None):
    def respond(query):
        size, page = int(query['per_page']), int(query['page'])
        count = len(rows) if reported is None else reported
        return f_response(rows[(page - 1) * size:page * size], count, page, size=size, exact=exact)
    return respond


def h4_server(rows, *, exact=True, reported=None):
    def respond(query):
        size, start = int(query['per_page']), 0
        if 'last_index' in query:
            start = [row['sub_id'] for row in rows].index(str(query['last_index'])) + 1
        chunk = rows[start:start + size]
        cursor = ({'last_index': chunk[-1]['sub_id'], 'last_event_purpose_date': chunk[-1]['event_purpose_date']}
                  if chunk else None)
        return h4_response(chunk, len(rows) if reported is None else reported, cursor, size=size, exact=exact)
    return respond


def pinned_null_sort_cursor(server):
    """The pinned provider drops a NULL sort key from last_indexes and emits sort_null_only instead."""
    def respond(query):
        data = server(query)
        cursor = data['pagination']['last_indexes']
        if cursor and cursor['last_event_purpose_date'] is None:
            data['pagination']['last_indexes'] = {'last_index': cursor['last_index'], 'sort_null_only': True}
        return data
    return respond


def serve(f=None, h4=None, other=None):
    def handler(endpoint, query):
        if endpoint.endswith('schedule_f/') and f:
            return f(query)
        if endpoint.endswith('schedule_h4/') and h4:
            return h4(query)
        if other:
            value = other(endpoint, query)
            if value is not None:
                return value
        return empty_page(endpoint)
    return handler


def to_body(data):
    """JSON text with Decimal values written as bare numeric literals, like the provider."""
    text = json.dumps(data, default=lambda value: '\x00%s\x00' % value, separators=(',', ':'))
    return re.sub(r'"\\u0000([^"\\]+)\\u0000"', r'\1', text).encode()


class FECAdmissionRefused(ValueError):
    """Same class name the driver already recognizes for an inert admission refusal."""


class Transport:
    """Scripted fake with the same surface the driver uses; one archive file per attempt."""
    def __init__(self, root, handler, cap=10000):
        self.root, self.handler, self.cap = Path(root), handler, cap
        self.root.mkdir()
        self.http_attempts, self.records, self.calls = 0, [], []
        self.maximum = cap

    def fetch(self, endpoint, query, exact_decimal=False):
        assert exact_decimal
        if self.http_attempts >= self.cap:
            raise FECAdmissionRefused('inert physical request cap')
        self.http_attempts += 1
        self.calls.append((endpoint, dict(query)))
        data = self.handler(endpoint, query)
        body = json.dumps(data, default=str).encode()
        path = self.root / (str(self.http_attempts) + '.bin')
        path.write_bytes(body)
        record = {'path': str(path), 'sha256': hashlib.sha256(body).hexdigest(), 'bytes': len(body), 'status': 200,
                  'endpoint': endpoint, 'query': dict(query), 'original_response_bytes': True}
        self.records.append(record)
        return data, record

    def kind_calls(self, suffix):
        return [query for endpoint, query in self.calls if endpoint.endswith(suffix)]


class Response(io.BytesIO):
    def __init__(self, body, status=200, headers=None):
        super().__init__(body)
        self.status, self.headers = status, headers or {'Content-Type': 'application/json'}


class Opener:
    """Fake of the transport's urllib opener; overrides replace the Nth attempt's response."""
    def __init__(self, handler, overrides=None):
        self.handler, self.overrides, self.calls = handler, overrides or {}, []

    def open(self, request, timeout):
        self.calls.append((request.full_url, timeout, request.get_header('X-api-key')))
        if len(self.calls) in self.overrides:
            return Response(*self.overrides[len(self.calls)])
        parts = urlsplit(request.full_url)
        endpoint, query = parts.path.split('/v1/', 1)[1], dict(parse_qsl(parts.query))
        return Response(to_body(self.handler(endpoint, query)))

    def endpoints(self):
        return [urlsplit(url).path.split('/v1/', 1)[1] for url, _timeout, _key in self.calls]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='D069-inert-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.sequence = 0
        for guard in (mock.patch.object(fec_key_store, 'load_keys', side_effect=AssertionError('credentials forbidden')),
                      mock.patch('socket.socket.connect', side_effect=AssertionError('network forbidden'))):
            guard.start()
            self.addCleanup(guard.stop)

    def frontier(self, name='frontier.sqlite', **options):
        value = driver.Frontier(self.root / name, **options)
        self.addCleanup(value.close)
        return value

    def legacy(self, name='frontier.sqlite', units=((C1, 2026),), **options):
        """A D068-shaped state: F/H4 exist but are unsupported, nothing collected."""
        value = driver.Frontier(self.root / name, **options)
        value.seed([{'committee_id': c, 'cycle': y, 'origins': ['industryA']} for c, y in units])
        value.close()
        return self.root / name

    def transport(self, handler, cap=60):
        # Bounded on purpose: a regression that loops must fail fast, not spend a runaway allowance.
        self.sequence += 1
        return Transport(self.root / ('raw-%d' % self.sequence), handler, cap)

    def dump(self, path, table='endpoints'):
        with closing(sqlite3.connect(path)) as db:
            return [tuple(row) for row in db.execute('SELECT * FROM ' + table + ' ORDER BY 1,2,3')]

    def endpoint(self, frontier, kind):
        row = frontier.db.execute('SELECT status,coverage_issues_json,rows_stored,pages_stored,expected_count,'
                                  'request_cursor_json,attempt_count,last_error FROM endpoints WHERE kind=?', (kind,)).fetchone()
        return {'status': row[0], 'issues': set(json.loads(row[1])), 'rows': row[2], 'pages': row[3],
                'expected': row[4], 'cursor': row[5], 'attempts': row[6], 'error': row[7]}

    def count(self, frontier, sql, *args):
        return frontier.db.execute(sql, args).fetchone()[0]


# ---------------------------------------------------------------- pure selector rules
class ReservedSelectorRules(unittest.TestCase):
    def test_legacy_cursor_allow_list_rejects_every_reserved_selector_and_container(self):
        legitimate = {'B': {'last_index': '123', 'last_disbursement_date': '2024-05-01'},
                      'E_regular': {'last_index': 5, 'last_expenditure_date': '2024-05-01',
                                    'last_expenditure_amount': Decimal('1.50')},
                      'E_notice': {'last_index': '5', 'last_expenditure_date': '2024-05-01'},
                      'A': {'last_index': '9', 'last_contribution_receipt_date': '2024-05-01'}}
        for kind, cursor in legitimate.items():
            second = [key for key in cursor if key != 'last_index'][0]
            with self.subTest(kind=kind, shape='legitimate'):
                self.assertTrue(contracts.legacy_cursor_valid(kind, cursor))
                self.assertTrue(contracts.legacy_cursor_valid(kind, {}))
            for name in RESERVED:
                with self.subTest(kind=kind, reserved=name):
                    self.assertFalse(contracts.legacy_cursor_valid(kind, {'last_index': '1', name: 'x'}))
                    self.assertFalse(contracts.legacy_cursor_valid(kind, {name: 'x'}))
            for bad in ({'last_index': ['1', '2']}, {'last_index': {'a': 1}}, {'last_index': True},
                        {'last_index': '1; DROP'}, {'last_index': '1', second: {'x': 1}},
                        {'last_index': '1', second: ['x']}, {'last_index': '1', second: 'x' * 129}, 'text', [], None):
                with self.subTest(kind=kind, bad=repr(bad)[:40]):
                    self.assertFalse(contracts.legacy_cursor_valid(kind, bad))
        # A numbered-page cursor is accepted only when this driver generated or stored it.
        self.assertFalse(contracts.legacy_cursor_valid('B', {'page': 2}))
        self.assertTrue(contracts.legacy_cursor_valid('B', {'page': 2}, stored_or_generated_page=True))
        self.assertFalse(contracts.legacy_cursor_valid('B', {'page': 2, 'last_index': '1'}, stored_or_generated_page=True))
        self.assertFalse(contracts.legacy_cursor_valid('B', {'page': 0}, stored_or_generated_page=True))

    def test_fh4_query_builder_cannot_be_steered_by_a_cursor(self):
        for kind, cursor in (('F', {'page': 2, 'committee_id': C9}), ('F', {'cycle': 2022}), ('F', {'page': 2, 'per_page': 1}),
                             ('H4', {'committee_id': C9, 'last_index': '1', 'last_event_purpose_date': '2024-01-01'}),
                             ('H4', {'last_index': '1', 'last_event_purpose_date': '2024-01-01', 'cycle': 2022}),
                             ('H4', {'last_index': '1', 'last_event_purpose_date': '2024-01-01', 'sort': 'payee_name'}),
                             ('H4', {'last_index': '1', 'last_event_purpose_date': ['2024-01-01']})):
            with self.subTest(kind=kind, cursor=cursor):
                with self.assertRaises(ValueError):
                    contracts.query(kind, C1, 2024, cursor=cursor)
        accepted = contracts.query('H4', C1, 2024, cursor={'last_index': '19', 'last_event_purpose_date': '2024-01-01'})
        self.assertEqual((accepted['committee_id'], accepted['cycle'], accepted['per_page']), (C1, 2024, 100))


# ---------------------------------------------------------------- durable enablement
CHILD = textwrap.dedent('''
    import os, sys
    sys.path.insert(0, sys.argv[1])
    import collect_committee_activity as driver
    mode, path, ids = sys.argv[2], sys.argv[3], sys.argv[4:]
    if mode == 'before_link':
        os.link = lambda *a, **k: os._exit(89)
    if mode == 'before_commit':
        original = driver.bind_enablement
        def die_with_intent_published_and_promotion_uncommitted(*a, **k):
            original(*a, **k)
            os._exit(87)
        driver.bind_enablement = die_with_intent_published_and_promotion_uncommitted
    driver.Frontier(path, enabled_endpoint_contracts=ids)
    if mode == 'after_commit':
        os._exit(88)
''')


class DurableEnablement(Base):
    def marker(self, path):
        return Path(path).with_suffix('.endpoint-contracts.json')

    def child(self, mode, path):
        script = self.root / ('child-%s.py' % mode)
        script.write_text(CHILD)
        environment = {key: value for key, value in os.environ.items() if not key.startswith('FEC_API_KEY')}
        environment['PYTHONDONTWRITEBYTECODE'] = '1'
        return subprocess.run([sys.executable, '-B', str(script), str(TOOLS), mode, str(path), *BOTH],
                              capture_output=True, timeout=120, env=environment)

    def test_intent_is_published_beside_sqlite_and_only_clean_unsupported_rows_are_promoted(self):
        path = self.legacy()
        before = self.dump(path)
        frontier = self.frontier(enabled_endpoint_contracts=list(reversed(BOTH)))
        marker = self.marker(path)
        self.assertEqual(marker.name, 'frontier.endpoint-contracts.json')
        self.assertEqual(marker.parent, path.parent)
        intent = json.loads(marker.read_bytes())
        self.assertEqual(intent['enabled_contract_ids'], sorted(BOTH))
        self.assertEqual(intent['module_sha256'], hashlib.sha256(Path(contracts.__file__).read_bytes()).hexdigest())
        self.assertEqual([item['contract_id'] for item in intent['contracts']], [F_ID, H4_ID])
        self.assertTrue(all(len(item['contract_metadata_sha256']) == 64 for item in intent['contracts']))
        after = self.dump(path)
        changed = [row for row in after if row not in before]
        self.assertEqual({(row[2], row[3]) for row in changed}, {('F', 'pending'), ('H4', 'pending')})
        self.assertEqual([row for row in after if row in before and row[2] in ('F', 'H4')], [])
        self.assertEqual([row for row in before if row[2] not in ('F', 'H4')], [row for row in after if row[2] not in ('F', 'H4')])
        receipt = frontier.report()['endpoint_contract_enablement']
        self.assertEqual((receipt['activated_existing_endpoints'], receipt['blocked_existing_endpoints']), (2, 0))
        self.assertEqual(frontier.report()['supported_outgoing_endpoint_kinds'], ['B', 'E_notice', 'E_regular', 'F', 'H4'])
        self.assertTrue(frontier.report()['other_spending_families'].startswith('B/E/F/H4 are overlapping evidence.'))
        self.assertEqual(list(path.parent.glob('*.pending-*')), [])
        # Idempotent restart: same intent, no second promotion, no byte or row change.
        frontier.close()
        raw = marker.read_bytes()
        again = self.frontier(enabled_endpoint_contracts=BOTH)
        self.assertEqual(again.report()['endpoint_contract_enablement']['activated_existing_endpoints'], 0)
        self.assertEqual((marker.read_bytes(), self.dump(path)), (raw, after))

    def test_new_units_after_enablement_start_pending_while_other_families_stay_unsupported(self):
        frontier = self.frontier(enabled_endpoint_contracts=BOTH)
        frontier.seed([{'committee_id': C1, 'cycle': 2026}])
        states = dict(frontier.db.execute('SELECT kind,status FROM endpoints'))
        self.assertEqual({kind for kind, status in states.items() if status == 'pending'}, {'B', 'E_regular', 'E_notice', 'F', 'H4'})
        self.assertEqual(frontier.report()['required_unsupported_endpoints'],
                         ['C_loans', 'D_debts', 'communication_costs', 'efile_amendment_timing', 'electioneering',
                          'totals_reports_unitemized'])

    def test_dirty_unsupported_checkpoint_is_held_and_never_reset_by_activation(self):
        path = self.legacy()
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("UPDATE endpoints SET rows_stored=1,pages_stored=1 WHERE kind='F'")
        before = {row[2]: row for row in self.dump(path)}
        frontier = self.frontier(enabled_endpoint_contracts=BOTH)
        after = {row[2]: row for row in self.dump(path)}
        self.assertEqual(after['F'], before['F'])
        self.assertEqual(after['H4'][3], 'pending')
        receipt = frontier.report()['endpoint_contract_enablement']
        self.assertEqual((receipt['activated_existing_endpoints'], receipt['blocked_existing_endpoints']), (1, 1))
        self.assertIn('F', frontier.report()['required_unsupported_endpoints'])
        self.assertFalse(frontier.report()['supported_outgoing_endpoints_complete'])

    def test_crash_between_intent_write_and_db_commit_replays_the_identical_activation(self):
        path = self.legacy('crash.sqlite')
        result = self.child('before_commit', path)
        self.assertEqual(result.returncode, 87, result.stderr.decode())
        marker = self.marker(path)
        self.assertTrue(marker.exists(), 'intent must be durable before promotion')
        self.assertTrue(Path(str(path) + '-journal').exists(), 'process died with the promotion transaction open')
        # Missing required enablement after the crash refuses; it never silently resumes unsupported.
        with self.assertRaises(driver.EndpointContractRefused):
            driver.Frontier(path)
        self.assertEqual({row[3] for row in self.dump(path) if row[2] in ('F', 'H4')}, {'unsupported'})
        replay = self.frontier('crash.sqlite', enabled_endpoint_contracts=BOTH)
        control_path = self.legacy('control.sqlite')
        control = self.frontier('control.sqlite', enabled_endpoint_contracts=BOTH)
        self.assertEqual(self.dump(path), self.dump(control_path))
        self.assertEqual(marker.read_bytes(), self.marker(control_path).read_bytes())
        self.assertEqual(replay.report()['endpoint_contract_enablement']['activated_existing_endpoints'], 2)
        self.assertEqual(control.report()['endpoint_contract_enablement']['activated_existing_endpoints'], 2)
        replay.close()
        third = self.frontier('crash.sqlite', enabled_endpoint_contracts=BOTH)
        self.assertEqual(third.report()['endpoint_contract_enablement']['activated_existing_endpoints'], 0)
        self.assertEqual(self.dump(path), self.dump(control_path))

    def test_crash_after_intent_fsync_but_before_publication_leaves_no_binding_and_replays(self):
        path = self.legacy('crash.sqlite')
        result = self.child('before_link', path)
        self.assertEqual(result.returncode, 89, result.stderr.decode())
        self.assertFalse(self.marker(path).exists(), 'only a complete, fsynced intent is ever linked into place')
        stale = list(path.parent.glob('*.pending-*'))
        self.assertEqual(len(stale), 1)
        self.assertEqual({row[3] for row in self.dump(path) if row[2] in ('F', 'H4')}, {'unsupported'})
        control_path = self.legacy('control.sqlite')
        self.frontier('control.sqlite', enabled_endpoint_contracts=BOTH)
        replay = self.frontier('crash.sqlite', enabled_endpoint_contracts=BOTH)
        self.assertEqual(self.dump(path), self.dump(control_path))
        self.assertEqual(self.marker(path).read_bytes(), self.marker(control_path).read_bytes())
        self.assertEqual(replay.report()['endpoint_contract_enablement']['activated_existing_endpoints'], 2)
        self.assertEqual(stale[0].read_bytes(), self.marker(path).read_bytes(), 'the leftover temporary file is inert evidence, never a binding')

    def test_intent_is_fsynced_and_published_before_the_first_promotion_statement(self):
        path = self.legacy('order.sqlite')
        marker, events = self.marker(path), []
        real_fsync, real_link = os.fsync, os.link

        class Spy:
            def __init__(self, connection):
                self.connection = connection

            def execute(self, sql, *args):
                if sql.lstrip().upper().startswith('UPDATE'):
                    events.append(('update', marker.exists() and marker.stat().st_size > 0))
                return self.connection.execute(sql, *args)

        def fsync(descriptor):
            events.append('fsync')
            return real_fsync(descriptor)

        def link(source, destination):
            events.append('link')
            return real_link(source, destination)
        connection = sqlite3.connect(path, isolation_level=None)
        connection.row_factory = sqlite3.Row
        self.addCleanup(connection.close)
        with mock.patch.object(os, 'fsync', fsync), mock.patch.object(os, 'link', link):
            connection.execute('BEGIN IMMEDIATE')
            contracts.bind_enablement(Spy(connection), path, BOTH)
            connection.execute('COMMIT')
        names = [event if isinstance(event, str) else event[0] for event in events]
        self.assertEqual(names.count('update'), 2)
        self.assertLess(names.index('fsync'), names.index('link'))
        self.assertLess(names.index('link'), names.index('update'))
        self.assertTrue(all(event[1] for event in events if isinstance(event, tuple)))

    def test_crash_after_commit_replays_idempotently_and_matches_a_clean_activation(self):
        path = self.legacy('crash.sqlite')
        result = self.child('after_commit', path)
        self.assertEqual(result.returncode, 88, result.stderr.decode())
        control_path = self.legacy('control.sqlite')
        self.frontier('control.sqlite', enabled_endpoint_contracts=BOTH)
        self.assertEqual(self.dump(path), self.dump(control_path))
        committed = (self.dump(path), self.marker(path).read_bytes())
        for _ in range(2):
            replay = self.frontier('crash.sqlite', enabled_endpoint_contracts=BOTH)
            self.assertEqual(replay.report()['endpoint_contract_enablement']['activated_existing_endpoints'], 0)
            replay.close()
            self.assertEqual((self.dump(path), self.marker(path).read_bytes()), committed)
        with self.assertRaises(driver.EndpointContractRefused):
            driver.Frontier(path)

    def test_absent_or_mismatched_identity_refuses_without_changing_state(self):
        def tamper_marker(path):
            self.marker(path).write_bytes(self.marker(path).read_bytes() + b' ')

        def alter_module_hash(path):
            intent = json.loads(self.marker(path).read_bytes())
            intent['module_sha256'] = '0' * 64
            self.marker(path).write_bytes((json.dumps(intent, sort_keys=True, separators=(',', ':')) + '\n').encode())

        cases = {'only one contract id': (lambda path: None, [F_ID]),
                 'enablement omitted': (lambda path: None, []),
                 'marker deleted while F/H4 active': (lambda path: self.marker(path).unlink(), BOTH),
                 'marker bytes altered': (tamper_marker, BOTH),
                 'recorded module hash differs': (alter_module_hash, BOTH),
                 'marker emptied': (lambda path: self.marker(path).write_bytes(b''), BOTH)}
        for label, (damage, ids) in cases.items():
            with self.subTest(label):
                name = re.sub('[^a-z]', '', label) + '.sqlite'
                path = self.legacy(name)
                self.frontier(name, enabled_endpoint_contracts=BOTH).close()
                damage(path)
                state = (self.dump(path), self.marker(path).read_bytes() if self.marker(path).exists() else None)
                with self.assertRaises(driver.EndpointContractRefused):
                    driver.Frontier(path, enabled_endpoint_contracts=ids)
                self.assertEqual((self.dump(path), self.marker(path).read_bytes() if self.marker(path).exists() else None), state)
        with self.assertRaises(ValueError):
            driver.Frontier(self.root / 'never-created.sqlite', enabled_endpoint_contracts=['unreviewed-contract'])
        self.assertFalse((self.root / 'never-created.sqlite').exists())

    def test_marker_tampered_after_start_refuses_before_any_admission(self):
        frontier = self.frontier(enabled_endpoint_contracts=BOTH)
        frontier.seed([{'committee_id': C1, 'cycle': 2026}])
        marker = self.marker(self.root / 'frontier.sqlite')
        marker.write_bytes(marker.read_bytes() + b' ')
        transport = self.transport(serve())
        report = frontier.run(transport)
        self.assertEqual(transport.http_attempts, 0)
        self.assertEqual(report['stop']['error_type'], 'EndpointContractRefused')
        self.assertEqual(report['pages'], 0)
        self.assertEqual(self.endpoint(frontier, 'B')['status'], 'pending')
        self.assertEqual(self.endpoint(frontier, 'B')['attempts'], 0)
        self.assertEqual(self.count(frontier, "SELECT COUNT(*) FROM endpoints WHERE status NOT IN('pending','unsupported')"), 0)
        self.assertEqual(self.count(frontier, 'SELECT COUNT(*) FROM failures'), 1, 'the refusal stops the invocation at once instead of repeating')

    def test_module_bytes_changed_after_start_refuses_before_any_admission(self):
        frontier = self.frontier(enabled_endpoint_contracts=BOTH)
        frontier.seed([{'committee_id': C1, 'cycle': 2026}])
        transport = self.transport(serve())
        real = Path.read_bytes

        def changed_module(path):
            data = real(path)
            return data + b'#' if Path(path) == Path(contracts.__file__) else data
        with mock.patch.object(Path, 'read_bytes', changed_module):
            report = frontier.run(transport)
        self.assertEqual((transport.http_attempts, report['stop']['error_type']), (0, 'EndpointContractRefused'))
        self.assertEqual(self.endpoint(frontier, 'B')['status'], 'pending')

    def test_unenabled_open_never_contends_for_the_writer_lease_but_enablement_does(self):
        path = self.legacy()
        holder = self.frontier()
        with holder.transaction():
            driver.Frontier(path).close()
            with self.assertRaises(sqlite3.OperationalError):
                driver.Frontier(path, enabled_endpoint_contracts=BOTH)
        self.assertFalse(self.marker(path).exists(), 'enablement that could not own the writer lease published nothing')

    def test_pending_kind_without_this_invocations_enablement_is_refused_before_admission(self):
        path = self.legacy()
        unenabled = self.frontier()
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("UPDATE endpoints SET status='pending' WHERE kind='F'")
        transport = self.transport(serve(f=f_server(f_rows(3))))
        report = unenabled.run(transport)
        self.assertEqual(transport.kind_calls('schedule_f/'), [])
        self.assertEqual(report['stop']['error_type'], 'EndpointContractRefused')
        self.assertEqual(self.endpoint(unenabled, 'F')['status'], 'pending')
        self.assertEqual(self.endpoint(unenabled, 'F')['attempts'], 0)

    def test_cli_refuses_before_credentials_and_records_enablement_in_the_receipt(self):
        state, seeds = self.root / 'state', self.root / 'seeds.json'
        seeds.write_text(json.dumps({'schema_version': 1, 'seed_units': [{'committee_id': C1, 'cycle': 2026}]}))
        base = ['collect', '--state', str(state), '--seeds', str(seeds), '--max-requests', '5', '--live']
        started = []

        def live(archive, maximum, **options):
            started.append(maximum)
            transport = self.transport(serve())
            transport.maximum = maximum

            class Context:
                def __enter__(self_inner):
                    return transport

                def __exit__(self_inner, *exc):
                    return False
            return Context()
        output = io.StringIO()
        with mock.patch.object(accounted.AccountedTransport, 'live', side_effect=live), redirect_stdout(output):
            with mock.patch.object(sys, 'argv', base + ['--enable-endpoint-contract', F_ID, '--enable-endpoint-contract', H4_ID]):
                driver.main()
            receipt = json.loads(next(state.glob('RECEIPT-*.json')).read_text())
            self.assertEqual(receipt['endpoint_contract_enablement']['enabled_contract_ids'], sorted(BOTH))
            self.assertEqual(receipt['supported_outgoing_endpoint_kinds'], ['B', 'E_notice', 'E_regular', 'F', 'H4'])
            self.assertEqual((receipt['http_attempts'], receipt['supported_outgoing_endpoints_complete']), (5, True))
            self.assertEqual(len(started), 1)
            for flags in ([], ['--enable-endpoint-contract', F_ID]):
                with mock.patch.object(sys, 'argv', base + flags):
                    with self.assertRaises(driver.EndpointContractRefused):
                        driver.main()
            self.assertEqual(len(started), 1, 'a refused invocation must never reach credential loading or admission')
        fec_key_store.load_keys.assert_not_called()

    def test_runtime_has_no_dependence_on_authoring_workspace_paths(self):
        forbidden = ('Codex', 'mentat-audit', 'D-069', 'pre-resume', 'C:' + chr(92) + 'Users', 'C:/Users', 'outputs' + chr(92),
                     'outputs/', 'proof-', 'proof/', 'proof' + chr(92))
        frontier = self.frontier(enabled_endpoint_contracts=BOTH)
        texts = {name: Path(module.__file__).read_text(encoding='utf-8') for name, module in (('driver', driver), ('contracts', contracts))}
        texts['intent'] = self.marker(self.root / 'frontier.sqlite').read_text(encoding='utf-8')
        for name, text in texts.items():
            for word in forbidden:
                self.assertNotIn(word, text, (name, word))
        self.assertEqual(frontier.report()['endpoint_contract_enablement']['binding']['path'],
                         str(self.marker(self.root / 'frontier.sqlite')))


# ---------------------------------------------------------------- reserved selectors and cursor safety
UNSAFE_PROVIDER_CURSORS = ({'last_index': '1', 'committee_id': C9}, {'last_index': '1', 'two_year_transaction_period': 2022},
                           {'last_index': '1', 'cycle': 2022}, {'last_index': '1', 'is_notice': 'true'},
                           {'last_index': '1', 'per_page': 1}, {'last_index': '1', 'sort': 'payee_name'},
                           {'page': 2, 'last_index': '1'}, {'last_index': ['1', '2']}, {'committee_id': C9},
                           {'last_index': '1; DROP TABLE x'}, {'last_index': '1', 'last_disbursement_date': {'x': 1}})


class ReservedSelectors(Base):
    def one_row(self, cursor):
        def other(endpoint, query):
            if endpoint in (SCHEDULE_B, SCHEDULE_E):
                return legacy_response([{'sub_id': '1', 'disbursement_amount': Decimal('1.00'),
                                         'expenditure_amount': Decimal('1.00')}], count=3, cursor=cursor)
        return serve(other=other)

    def test_provider_continuation_never_overrides_reserved_selectors_and_retains_the_page(self):
        for cursor in UNSAFE_PROVIDER_CURSORS:
            with self.subTest(cursor=cursor):
                self.sequence += 1
                frontier = self.frontier('case-%d.sqlite' % self.sequence)
                frontier.seed([{'committee_id': C1, 'cycle': 2026}])
                transport = self.transport(self.one_row(cursor))
                report = frontier.run(transport)
                self.assertEqual(len(transport.calls), 3, 'no follow-up request may be built from an unsafe continuation')
                for endpoint, query in transport.calls:
                    self.assertEqual((query['committee_id'], query['per_page']), (C1, 100))
                    self.assertEqual(set(query), {'committee_id', 'per_page', 'two_year_transaction_period'} if endpoint == SCHEDULE_B
                                     else {'committee_id', 'per_page', 'cycle', 'is_notice'})
                    self.assertEqual(query['two_year_transaction_period' if endpoint == SCHEDULE_B else 'cycle'], 2026)
                for kind in ('B', 'E_regular', 'E_notice'):
                    state = self.endpoint(frontier, kind)
                    self.assertEqual((state['status'], state['rows'], state['pages'], state['cursor']), ('closed_unknown', 1, 1, '{}'), kind)
                    self.assertTrue({'unsafe_cursor_fields_require_recovery', 'cursor_recovery_required'} <= state['issues'], (kind, state))
                self.assertEqual(report['observations'], 3)
                self.assertFalse(report['supported_outgoing_endpoints_complete'])
                stored = self.count(frontier, "SELECT COUNT(*) FROM pages WHERE response_pagination_json LIKE '%last_indexes%'")
                self.assertEqual(stored, 3, 'the raw provider continuation stays in the retained page for recovery')

    def test_safe_provider_continuation_still_advances_without_touching_selectors(self):
        frontier = self.frontier()
        frontier.seed([{'committee_id': C1, 'cycle': 2026}])

        def other(endpoint, query):
            if endpoint == SCHEDULE_B and 'last_index' not in query:
                return legacy_response([{'sub_id': '1', 'disbursement_amount': Decimal('1.00')}], count=2,
                                      cursor={'last_index': '1', 'last_disbursement_date': '2024-05-01'})
            if endpoint == SCHEDULE_B:
                return legacy_response([{'sub_id': '2', 'disbursement_amount': Decimal('2.00')}], count=2)
        transport = self.transport(serve(other=other))
        frontier.run(transport)
        second = transport.kind_calls('schedule_b/')[1]
        self.assertEqual(second, {'committee_id': C1, 'per_page': 100, 'two_year_transaction_period': 2026,
                                  'last_index': '1', 'last_disbursement_date': '2024-05-01'})
        self.assertEqual(self.endpoint(frontier, 'B')['status'], 'complete')

    def stored_unsafe(self, name, kind, cursor):
        frontier = self.frontier(name, enabled_endpoint_contracts=BOTH)
        frontier.seed([{'committee_id': C1, 'cycle': 2026}])
        frontier.db.execute("UPDATE endpoints SET status='pending',request_cursor_json=?,pages_stored=1,rows_stored=1 WHERE kind=?",
                            (json.dumps(cursor), kind))
        return frontier

    def test_already_stored_unsafe_cursor_refuses_before_admission_and_other_endpoints_proceed(self):
        cases = [('B', {'committee_id': C9, 'last_index': '1'}), ('B', {'two_year_transaction_period': 2022}),
                 ('E_notice', {'is_notice': 'false', 'last_index': '1'}), ('E_regular', {'per_page': 1}),
                 ('B', {'last_index': {'nested': 'selector'}}), ('F', {'last_index': '1'}), ('F', {'page': 0}),
                 ('F', {'page': 2, 'committee_id': C9}),
                 ('H4', {'last_index': '1', 'last_event_purpose_date': '2024-01-01', 'committee_id': C9}),
                 ('H4', {'last_index': '1'})]
        for kind, cursor in cases:
            with self.subTest(kind=kind, cursor=cursor):
                self.sequence += 1
                frontier = self.stored_unsafe('stored-%d.sqlite' % self.sequence, kind, cursor)
                transport = self.transport(serve())
                report = frontier.run(transport)
                refused = self.endpoint(frontier, kind)
                self.assertEqual(refused['status'], 'inconsistent')
                self.assertEqual(refused['attempts'], 0, 'no physical attempt may be charged to an unsafe stored cursor')
                self.assertEqual(refused['rows'], 1)
                self.assertEqual(self.count(frontier, 'SELECT COUNT(*) FROM failures WHERE kind=?', kind), 1)
                suffix = {'B': 'schedule_b/', 'F': 'schedule_f/', 'H4': 'schedule_h4/'}.get(kind, 'schedule_e/')
                if kind in ('B', 'F', 'H4'):
                    self.assertEqual(transport.kind_calls(suffix), [])
                self.assertEqual(len(transport.calls), 4, 'the four other endpoints are not starved by the refused one')
                self.assertEqual(report['stop'], None)
                self.assertFalse(report['supported_outgoing_endpoints_complete'])


# ---------------------------------------------------------------- F/H4 pagination and retention
class Pagination(Base):
    def enabled(self, name='frontier.sqlite'):
        frontier = self.frontier(name, enabled_endpoint_contracts=BOTH)
        frontier.seed([{'committee_id': C1, 'cycle': 2026}])
        return frontier

    def test_f_numbered_pages_run_until_pages_are_exhausted_and_preserve_signed_text_rows(self):
        rows = f_rows(250)
        frontier = self.enabled()
        transport = self.transport(serve(f=f_server(rows)))
        report = frontier.run(transport)
        calls = transport.kind_calls('schedule_f/')
        self.assertEqual([query['page'] for query in calls], [1, 2, 3])
        for query in calls:
            self.assertEqual({key: query[key] for key in query if key != 'page'},
                             {'committee_id': C1, 'cycle': 2026, 'per_page': 100, 'sort': 'expenditure_date',
                              'sort_hide_null': 'false', 'sort_null_only': 'false'})
        state = self.endpoint(frontier, 'F')
        self.assertEqual((state['status'], state['rows'], state['pages'], state['expected'], state['cursor'], state['issues']),
                         ('complete', 250, 3, 250, '{}', set()))
        stored = frontier.db.execute("SELECT source_id,amount_text,amount_status FROM observations WHERE kind='F' ORDER BY page_no,row_ordinal").fetchall()
        self.assertEqual([row[0] for row in stored], [row['sub_id'] for row in rows])
        self.assertEqual([row[1] for row in stored[:4]], ['-12.50', '0', '10.25', '0.01'])
        self.assertEqual({row[2] for row in stored}, {'valid_decimal'})
        self.assertEqual((self.count(frontier, "SELECT COUNT(*) FROM leaf_coverage WHERE kind='F'"),
                          self.count(frontier, "SELECT COUNT(*) FROM candidate_references WHERE kind='F'")), (250, 250))
        self.assertEqual([row[0] for row in frontier.db.execute("SELECT DISTINCT role FROM candidate_references WHERE kind='F'")],
                         ['candidate_beneficiary_of_coordinated_party_spending_not_paid_counterparty'])
        self.assertEqual((report['edges'], report['units']), (0, 1), 'source, subordinate, payee and candidate fields are not payment edges')
        self.assertTrue(report['supported_outgoing_endpoints_complete'])
        self.assertFalse(report['exhaustive_disclosed_spending_complete'])

    def test_f_estimated_or_missing_count_cannot_truncate_valid_retrieval(self):
        for label, kwargs in (('estimated', {'exact': False, 'reported': 1}), ('missing', {'exact': False, 'reported': None})):
            with self.subTest(label):
                self.sequence += 1
                frontier = self.enabled('f-%d.sqlite' % self.sequence)
                rows = f_rows(215)
                if label == 'missing':
                    def respond(query, rows=rows):
                        size, page = int(query['per_page']), int(query['page'])
                        return f_response(rows[(page - 1) * size:page * size], None, page, size=size, exact=False)
                    handler = serve(f=respond)
                else:
                    handler = serve(f=f_server(rows, **kwargs))
                transport = self.transport(handler)
                report = frontier.run(transport)
                self.assertEqual([query['page'] for query in transport.kind_calls('schedule_f/')], [1, 2, 3, 4])
                state = self.endpoint(frontier, 'F')
                self.assertEqual((state['status'], state['rows'], state['pages']), ('closed_unknown', 215, 4))
                self.assertIn('count_exactness_unknown', state['issues'])
                self.assertFalse(report['supported_outgoing_endpoints_complete'])
                self.assertEqual(self.count(frontier, "SELECT COUNT(*) FROM observations WHERE kind='F'"), 215)

    def test_f_provider_that_stops_advancing_is_recovery_required_after_one_repeat(self):
        rows = f_rows(100)
        for label, marks in (('ignores_page', {'duplicate_source_id', 'wrong_numbered_page', 'cursor_recovery_required'}),
                             ('echoes_page_but_repeats_rows', {'duplicate_source_id', 'pagination_made_no_valid_progress', 'cursor_recovery_required'})):
            with self.subTest(label):
                self.sequence += 1
                frontier = self.enabled('loop-%d.sqlite' % self.sequence)

                def respond(query, label=label):
                    page = 1 if label == 'ignores_page' else int(query['page'])
                    return f_response(rows, 1000, page)
                transport = self.transport(serve(f=respond))
                frontier.run(transport)
                self.assertEqual(len(transport.kind_calls('schedule_f/')), 2, 'the allowance is not spent on a repeating provider')
                state = self.endpoint(frontier, 'F')
                self.assertEqual(state['status'], 'closed_unknown')
                self.assertTrue(marks <= state['issues'], state)
                self.assertEqual((state['rows'], state['pages']), (200, 2), 'both retrieved pages stay retained')

    def test_f_ignores_provider_last_indexes_so_reserved_selectors_cannot_be_overridden(self):
        rows, frontier = f_rows(250), self.enabled()
        serve_rows = f_server(rows)

        def respond(query):
            data = serve_rows(query)
            data['pagination']['last_indexes'] = {'committee_id': C9, 'cycle': 2022, 'per_page': 1, 'page': 99}
            return data
        transport = self.transport(serve(f=respond))
        frontier.run(transport)
        self.assertEqual([(q['committee_id'], q['cycle'], q['per_page'], q['page']) for q in transport.kind_calls('schedule_f/')],
                         [(C1, 2026, 100, 1), (C1, 2026, 100, 2), (C1, 2026, 100, 3)])
        self.assertEqual(self.endpoint(frontier, 'F')['status'], 'complete')

    def test_f_exact_count_already_exceeded_stops_instead_of_continuing(self):
        frontier = self.enabled()
        transport = self.transport(serve(f=f_server(f_rows(250), reported=150)))
        frontier.run(transport)
        self.assertEqual(len(transport.kind_calls('schedule_f/')), 2)
        state = self.endpoint(frontier, 'F')
        self.assertEqual((state['status'], state['rows']), ('closed_unknown', 200))
        self.assertTrue({'rows_exceed_count', 'pagination_made_no_valid_progress', 'cursor_recovery_required'} <= state['issues'], state)

    def test_h4_seek_pages_forward_complete_cursor_and_retain_unknown_dates(self):
        rows = h4_rows(250, null_tail=3)
        frontier = self.enabled()
        transport = self.transport(serve(h4=h4_server(rows)))
        report = frontier.run(transport)
        calls = transport.kind_calls('schedule_h4/')
        self.assertEqual(len(calls), 3)
        self.assertEqual(set(calls[0]), {'committee_id', 'cycle', 'per_page', 'sort', 'sort_hide_null', 'sort_null_only'})
        for previous, query in zip((rows[99], rows[199]), calls[1:]):
            self.assertEqual((query['last_index'], query['last_event_purpose_date']), (previous['sub_id'], previous['event_purpose_date']))
            self.assertNotIn('page', query)
            self.assertEqual((query['committee_id'], query['cycle'], query['per_page'], query['sort']), (C1, 2026, 100, '-event_purpose_date'))
            self.assertNotIn('two_year_transaction_period', query)
        state = self.endpoint(frontier, 'H4')
        self.assertEqual((state['status'], state['rows'], state['pages'], state['issues']), ('complete', 250, 3, set()))
        unknown = frontier.db.execute("SELECT COUNT(*) FROM observations WHERE kind='H4' AND raw_json LIKE '%\"event_purpose_date\":null%'").fetchone()[0]
        self.assertEqual(unknown, 3)
        self.assertEqual((report['edges'], report['units'], self.count(frontier, "SELECT COUNT(*) FROM leaf_coverage WHERE kind='H4'")), (0, 1, 250))

    def test_h4_estimated_count_with_valid_cursor_continues_until_an_empty_page(self):
        frontier = self.enabled()
        transport = self.transport(serve(h4=h4_server(h4_rows(150), exact=False, reported=1)))
        report = frontier.run(transport)
        self.assertEqual(len(transport.kind_calls('schedule_h4/')), 3)
        state = self.endpoint(frontier, 'H4')
        self.assertEqual((state['status'], state['rows'], state['pages']), ('closed_unknown', 150, 3))
        self.assertIn('count_exactness_unknown', state['issues'])
        self.assertFalse(report['supported_outgoing_endpoints_complete'])

    def test_h4_unsafe_or_incomplete_continuation_retains_the_page_as_recovery_required(self):
        good = {'last_index': sid(99), 'last_event_purpose_date': '2024-12-01'}
        for cursor in ({**good, 'committee_id': C9}, {**good, 'cycle': 2022}, {**good, 'sort': 'payee_name'},
                       {**good, 'last_event_purpose_date': ['2024-12-01']}, {'last_index': sid(99)},
                       {**good, 'last_event_purpose_date': None}, {'last_index': True, 'last_event_purpose_date': '2024-12-01'}):
            with self.subTest(cursor=cursor):
                self.sequence += 1
                frontier = self.enabled('h4-%d.sqlite' % self.sequence)
                transport = self.transport(serve(h4=lambda query, cursor=cursor: h4_response(h4_rows(100), 250, cursor)))
                frontier.run(transport)
                self.assertEqual(len(transport.kind_calls('schedule_h4/')), 1)
                state = self.endpoint(frontier, 'H4')
                self.assertEqual((state['status'], state['rows'], state['pages'], state['cursor']), ('closed_unknown', 100, 1, '{}'))
                self.assertTrue({'incomplete_or_unsupported_cursor', 'cursor_recovery_required'} <= state['issues'], state)
                self.assertEqual(self.count(frontier, "SELECT COUNT(*) FROM observations WHERE kind='H4'"), 100)

    def test_h4_stalled_or_looping_cursor_is_recovery_required(self):
        rows = h4_rows(300)
        stalled = {'last_index': rows[99]['sub_id'], 'last_event_purpose_date': rows[99]['event_purpose_date']}
        frontier = self.enabled()
        transport = self.transport(serve(h4=lambda query: h4_response(rows[:100] if 'last_index' not in query else rows[100:200], 300, stalled)))
        frontier.run(transport)
        self.assertEqual(len(transport.kind_calls('schedule_h4/')), 2)
        state = self.endpoint(frontier, 'H4')
        self.assertEqual((state['status'], state['rows'], state['pages']), ('closed_unknown', 200, 2))
        self.assertIn('cursor_recovery_required', state['issues'])

    def test_h4_exact_final_page_with_pinned_null_sort_cursor_completes(self):
        rows = h4_rows(101, null_tail=1)
        frontier = self.enabled()
        transport = self.transport(serve(h4=pinned_null_sort_cursor(h4_server(rows))))
        report = frontier.run(transport)
        self.assertEqual(len(transport.kind_calls('schedule_h4/')), 2)
        state = self.endpoint(frontier, 'H4')
        self.assertEqual((state['status'], state['rows'], state['pages'], state['cursor'], state['issues']), ('complete', 101, 2, '{}', set()))
        self.assertTrue(report['supported_outgoing_endpoints_complete'])

    def test_h4_pinned_null_sort_cursor_mid_stream_still_closes_unknown(self):
        frontier = self.enabled()
        transport = self.transport(serve(h4=pinned_null_sort_cursor(h4_server(h4_rows(250, null_tail=200)))))
        frontier.run(transport)
        self.assertEqual(len(transport.kind_calls('schedule_h4/')), 1)
        state = self.endpoint(frontier, 'H4')
        self.assertEqual((state['status'], state['rows'], state['pages'], state['cursor']), ('closed_unknown', 100, 1, '{}'))
        self.assertTrue({'incomplete_or_unsupported_cursor', 'cursor_recovery_required'} <= state['issues'], state)
        self.assertEqual(self.count(frontier, "SELECT COUNT(*) FROM observations WHERE kind='H4'"), 100)

    def test_h4_pinned_null_sort_cursor_with_estimated_count_does_not_certify(self):
        frontier = self.enabled()
        transport = self.transport(serve(h4=pinned_null_sort_cursor(h4_server(h4_rows(1, null_tail=1), exact=False))))
        report = frontier.run(transport)
        self.assertEqual(len(transport.kind_calls('schedule_h4/')), 1)
        state = self.endpoint(frontier, 'H4')
        self.assertEqual((state['status'], state['rows'], state['pages']), ('closed_unknown', 1, 1))
        self.assertTrue({'count_exactness_unknown', 'incomplete_or_unsupported_cursor', 'cursor_recovery_required'} <= state['issues'], state)
        self.assertFalse(report['supported_outgoing_endpoints_complete'])

    def test_f_h4_source_subordinate_payee_and_candidate_fields_are_not_payment_edges(self):
        frontier = self.enabled()
        poisoned_f = [{**row, 'payee_committee_id': C3, 'spender_committee_id': C9, 'recipient_committee': {'committee_id': C9}} for row in f_rows(2)]
        poisoned_h4 = [{**row, 'recipient_committee_id': C3, 'subordinate_committee_id': C2, 'candidate_id': 'H0AA00001',
                        'recipient_committee': {'committee_id': C9}} for row in h4_rows(2)]
        transport = self.transport(serve(f=f_server(poisoned_f), h4=h4_server(poisoned_h4)))
        report = frontier.run(transport)
        self.assertEqual((report['units'], report['edges'], report['counterparty_identity_conflicts']), (1, 0, 0))
        self.assertEqual(self.count(frontier, "SELECT COUNT(*) FROM leaf_coverage WHERE kind IN('F','H4')"), 4)
        self.assertEqual(self.count(frontier, "SELECT COUNT(*) FROM units"), 1)

    def test_unenabled_endpoints_stay_unsupported_and_are_never_requested(self):
        frontier = self.frontier()
        frontier.seed([{'committee_id': C1, 'cycle': 2026}])
        transport = self.transport(serve(f=f_server(f_rows(3)), h4=h4_server(h4_rows(3))))
        report = frontier.run(transport)
        self.assertEqual(transport.kind_calls('schedule_f/') + transport.kind_calls('schedule_h4/'), [])
        self.assertEqual(report['endpoint_status_counts']['F:unsupported'], 1)
        self.assertEqual(report['supported_outgoing_endpoint_kinds'], ['B', 'E_notice', 'E_regular'])
        self.assertTrue(report['other_spending_families'].startswith('B/E are overlapping evidence.'))
        self.assertIsNone(report['endpoint_contract_enablement']['binding'])

    def test_enabled_endpoints_gate_the_outgoing_completion_flag(self):
        frontier = self.enabled()
        transport = self.transport(serve(f=f_server(f_rows(150))), cap=4)
        report = frontier.run(transport)
        self.assertEqual(report['stop']['error_type'], 'FECAdmissionRefused')
        self.assertFalse(report['supported_outgoing_endpoints_complete'])
        self.assertEqual(self.endpoint(frontier, 'F')['status'], 'pending')
        report = frontier.run(self.transport(serve(f=f_server(f_rows(150)), h4=h4_server(h4_rows(10)))))
        self.assertTrue(report['supported_outgoing_endpoints_complete'])


# ---------------------------------------------------------------- composition with the real D067 adapter
class AdapterComposition(Base):
    def adapter(self, handler, *, maximum=100, cap=1000, ledger=None, overrides=None):
        self.sequence += 1
        opener = Opener(handler, overrides)
        transport = accounted.AccountedTransport([KEY], ledger or self.root / 'quota.sqlite', self.root / ('archive-%d' % self.sequence),
                                                 maximum, opener=opener, cap=cap)
        self.addCleanup(transport.close)
        return transport, opener

    def used(self, ledger=None):
        with closing(sqlite3.connect(ledger or self.root / 'quota.sqlite')) as db:
            row = db.execute('SELECT requests_used FROM api_request_budget WHERE budget_id=?', (budget_id(KEY),)).fetchone()
        return 0 if row is None else row[0]

    def enabled(self, name='frontier.sqlite'):
        frontier = self.frontier(name, enabled_endpoint_contracts=BOTH)
        frontier.seed([{'committee_id': C1, 'cycle': 2026}])
        return frontier

    def test_f_and_h4_pages_are_charged_archived_and_bound_through_the_actual_adapter(self):
        frontier = self.enabled()
        f, h4 = f_rows(150), h4_rows(120)
        transport, opener = self.adapter(serve(f=f_server(f), h4=h4_server(h4)))
        report = frontier.run(transport)
        self.assertEqual((transport.http_attempts, len(opener.calls), self.used(), len(transport.records)), (7, 7, 7, 7))
        self.assertEqual(sorted(opener.endpoints()), sorted([SCHEDULE_B, SCHEDULE_E, SCHEDULE_E] + ['schedules/schedule_f/'] * 2 + ['schedules/schedule_h4/'] * 2))
        self.assertTrue(all(key == KEY for _url, _timeout, key in opener.calls))
        self.assertFalse(any(KEY in url for url, _timeout, _key in opener.calls))
        for record in transport.records:
            body = Path(record['path']).read_bytes()
            self.assertEqual(hashlib.sha256(body).hexdigest(), record['sha256'])
            self.assertTrue(record['original_response_bytes'])
            self.assertNotIn(KEY.encode(), body)
            self.assertNotIn(KEY, Path(record['receipt_path']).read_text())
        stored = {row[0] for row in frontier.db.execute('SELECT archive_sha256 FROM pages')}
        self.assertEqual(stored, {record['sha256'] for record in transport.records})
        self.assertEqual((self.endpoint(frontier, 'F')['status'], self.endpoint(frontier, 'F')['rows']), ('complete', 150))
        self.assertEqual((self.endpoint(frontier, 'H4')['status'], self.endpoint(frontier, 'H4')['rows']), ('complete', 120))
        raw = frontier.db.execute("SELECT amount_text FROM observations WHERE kind='F' ORDER BY page_no,row_ordinal LIMIT 4").fetchall()
        self.assertEqual([row[0] for row in raw], ['-12.50', '0', '10.25', '0.01'], 'amounts parsed from original bytes as exact decimals')
        self.assertTrue(report['supported_outgoing_endpoints_complete'])
        self.assertEqual(report['edges'], 0)

    def test_original_bytes_are_fsynced_before_they_are_parsed(self):
        frontier = self.enabled()
        transport, _opener = self.adapter(serve())
        events = []
        real_fsync, real_loads = os.fsync, json.loads

        def fsync(descriptor):
            events.append('fsync')
            return real_fsync(descriptor)

        def loads(value, *args, **kwargs):
            if isinstance(value, bytes):
                events.append('parse')
            return real_loads(value, *args, **kwargs)
        with mock.patch.object(os, 'fsync', fsync), mock.patch.object(json, 'loads', loads):
            frontier.run(transport)
        self.assertEqual(events.count('parse'), 5)
        self.assertEqual(events[:3], ['fsync', 'fsync', 'parse'], 'body and receipt are durable before the first parse')
        for index, event in enumerate(events):
            if event == 'parse':
                self.assertEqual(events[index - 2:index], ['fsync', 'fsync'])

    def test_unparseable_success_response_keeps_the_original_bytes_and_charges_once(self):
        frontier = self.enabled()
        transport, opener = self.adapter(serve(), overrides={1: (b'not json at all', 200)})
        report = frontier.run(transport)
        record = transport.records[0]
        self.assertEqual(Path(record['path']).read_bytes(), b'not json at all')
        self.assertEqual(self.endpoint(frontier, 'B')['status'], 'inconsistent')
        failure = frontier.db.execute("SELECT archive_record_json FROM failures WHERE kind='B'").fetchone()[0]
        self.assertEqual(json.loads(failure)['sha256'], record['sha256'])
        self.assertEqual(self.count(frontier, "SELECT COUNT(*) FROM pages WHERE kind='B'"), 0)
        self.assertEqual((self.used(), len(opener.calls)), (5, 5), 'one charged attempt for the bad page, no retry, others proceed')
        self.assertIsNone(report['stop'])

    def test_shared_quota_exhaustion_is_nonretryable_stops_the_invocation_and_grants_no_capacity(self):
        frontier = self.enabled()
        transport, opener = self.adapter(serve(f=f_server(f_rows(150))), cap=3)
        report = frontier.run(transport)
        self.assertEqual((transport.http_attempts, len(opener.calls), self.used()), (3, 3, 3))
        self.assertEqual(report['stop']['error_type'], 'FECAdmissionRefused')
        self.assertEqual(self.endpoint(frontier, 'F')['status'], 'pending')
        self.assertFalse(report['supported_outgoing_endpoints_complete'])
        second, other = self.adapter(serve(f=f_server(f_rows(150))), cap=3)
        report = frontier.run(second)
        self.assertEqual((second.http_attempts, len(other.calls), self.used()), (0, 0, 3), 'a new transport cannot spend a fresh budget')
        self.assertEqual(report['stop']['error_type'], 'FECAdmissionRefused')

    def test_physical_request_cap_leaves_work_incomplete_without_extra_attempts(self):
        frontier = self.enabled()
        transport, opener = self.adapter(serve(f=f_server(f_rows(250)), h4=h4_server(h4_rows(250))), maximum=5)
        report = frontier.run(transport)
        self.assertEqual((transport.http_attempts, len(opener.calls), self.used()), (5, 5, 5))
        self.assertEqual(report['stop']['error_type'], 'FECAdmissionRefused')
        self.assertFalse(report['supported_outgoing_endpoints_complete'])
        self.assertEqual({self.endpoint(frontier, kind)['status'] for kind in ('F', 'H4')}, {'pending'})
        resumed, other = self.adapter(serve(f=f_server(f_rows(250)), h4=h4_server(h4_rows(250))))
        report = frontier.run(resumed)
        self.assertTrue(report['supported_outgoing_endpoints_complete'])
        self.assertEqual((resumed.http_attempts, self.used()), (4, 9), 'the same shared ledger keeps accumulating across invocations')
        self.assertEqual([dict(parse_qsl(urlsplit(url).query))['page'] for url, _t, _k in opener.calls + other.calls
                          if 'schedule_f' in url], ['1', '2', '3'], 'resume continues from the committed page, never from page 1')

    def test_archive_storage_failure_stops_the_invocation_without_retry_or_admitted_page(self):
        frontier = self.enabled()
        transport, opener = self.adapter(serve(f=f_server(f_rows(150))))
        real = accounted.durable_new

        def failing(path, body):
            if str(path).endswith('.body.bin') and transport.http_attempts == 4:
                raise OSError('inert storage failure')
            return real(path, body)
        with mock.patch.object(accounted, 'durable_new', failing):
            report = frontier.run(transport)
        self.assertEqual((transport.http_attempts, len(opener.calls), self.used()), (4, 4, 4))
        self.assertEqual(report['stop']['error_type'], 'FECArchiveFailed')
        self.assertEqual(self.count(frontier, "SELECT COUNT(*) FROM pages WHERE kind='F'"), 0)
        self.assertEqual(self.count(frontier, "SELECT COUNT(*) FROM observations WHERE kind='F'"), 0)
        self.assertEqual(self.endpoint(frontier, 'F')['status'], 'blocked')
        self.assertEqual(self.endpoint(frontier, 'F')['attempts'], 1)

    def test_credential_echo_is_refused_without_persisting_a_body_and_stops(self):
        frontier = self.enabled()
        echo = ('{"results":[],"pagination":{"count":0,"is_count_exact":true},"echo":"%s"}' % KEY).encode()
        transport, opener = self.adapter(serve(), overrides={1: (echo, 200)})
        report = frontier.run(transport)
        self.assertEqual((len(opener.calls), self.used()), (1, 1))
        self.assertEqual(report['stop']['error_type'], 'FECArchiveFailed')
        archive = transport.archive
        self.assertEqual(list(archive.glob('*.body.bin')), [])
        for item in archive.iterdir():
            self.assertNotIn(KEY, item.read_text())
        self.assertFalse(transport.records[0]['original_response_bytes'])
        self.assertEqual(self.count(frontier, 'SELECT COUNT(*) FROM pages'), 0)

    def test_provider_failure_statuses_defer_one_endpoint_or_stop_the_invocation(self):
        frontier = self.enabled()
        transport, opener = self.adapter(serve(f=f_server(f_rows(3)), h4=h4_server(h4_rows(3))), overrides={1: (b'{}', 503)})
        report = frontier.run(transport)
        self.assertEqual((self.endpoint(frontier, 'B')['status'], report['stop'], self.used(), len(opener.calls)), ('blocked', None, 5, 5))
        self.assertEqual({self.endpoint(frontier, kind)['status'] for kind in ('E_notice', 'E_regular', 'F', 'H4')}, {'complete'})
        self.assertEqual(Path(transport.records[0]['path']).read_bytes(), b'{}', 'the error body is retained as original evidence too')
        limited = self.enabled('limited.sqlite')
        transport, opener = self.adapter(serve(), overrides={4: (b'{}', 429)})
        report = limited.run(transport)
        self.assertEqual(report['stop']['error_type'], 'HTTPError')
        self.assertEqual((len(opener.calls), self.endpoint(limited, 'F')['status']), (4, 'blocked'))

    def test_stored_unsafe_cursor_is_refused_before_the_shared_ledger_is_charged(self):
        frontier = self.enabled()
        frontier.db.execute("UPDATE endpoints SET status='pending',request_cursor_json=?,pages_stored=1,rows_stored=1 WHERE kind='B'",
                            (json.dumps({'committee_id': C9, 'last_index': '1'}),))
        frontier.db.execute("UPDATE endpoints SET status='pending',request_cursor_json=?,pages_stored=1,rows_stored=1 WHERE kind='H4'",
                            (json.dumps({'last_index': '1', 'last_event_purpose_date': '2024-01-01', 'cycle': 2022}),))
        transport, opener = self.adapter(serve(f=f_server(f_rows(3))))
        frontier.run(transport)
        self.assertEqual(sorted(opener.endpoints()), sorted([SCHEDULE_E, SCHEDULE_E, 'schedules/schedule_f/']))
        self.assertEqual((self.used(), transport.http_attempts), (3, 3), 'only the three safe endpoints were admitted and charged')
        self.assertEqual({self.endpoint(frontier, kind)['status'] for kind in ('B', 'H4')}, {'inconsistent'})
        self.assertEqual({self.endpoint(frontier, kind)['attempts'] for kind in ('B', 'H4')}, {0})


if __name__ == '__main__':
    unittest.main()

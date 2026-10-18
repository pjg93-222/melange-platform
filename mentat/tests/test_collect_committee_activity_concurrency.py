"""Inert D071 controls: bounded concurrency for the downstream spending collector.

No credentials, provider, network, product state or canonical collection state: every database, ledger and archive lives in a
temporary directory, credential loading and sockets are patched to fail, and the real accounted transport only receives a
scripted, thread-safe opener. Controls that need the audit validator run it only when D071_VALIDATOR names the reviewed v5.2
file (sha256 pinned below); every control also asserts the validator's R1 equalities itself, so none depends on it.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, redirect_stdout
from decimal import Decimal
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlsplit

TOOLS = Path(__file__).resolve().parents[1] / 'tools'
sys.path.insert(0, str(TOOLS))
import collect_committee_activity as driver
import fec_accounted_client as accounted
import fec_key_store
from api_request_budget import budget_id, ensure_schema

KEY = 'inert-D071-synthetic-key-not-a-provider-credential'
KEYS = tuple('%s-%d' % (KEY, i) for i in range(8))   # in-flight HTTP is bounded by keys x per-key concurrency (2)
KEYS21 = tuple('%s-%d' % (KEY, i) for i in range(21))   # the production key count
ALL_SEEDS = [{'committee_id': 'C%08d' % n, 'cycle': cycle, 'origins': ['industry%02d-%d' % (n, cycle)]} for n in range(1, 11) for cycle in (2022, 2024, 2026)]
VALIDATOR_SHA256 = 'ce23260e11057129fe07402f8eae9fb0710625b92deb67877fdd9956630d7d66'
NC, CYCLES = 10, (2022, 2024, 2026)
SEEDS = [{'committee_id': 'C00000001', 'cycle': 2024, 'origins': ['industryA', 'industryB']},
         {'committee_id': 'C00000002', 'cycle': 2026, 'origins': ['industryD']},
         {'committee_id': 'C00000003', 'cycle': 2022, 'origins': ['industryC']}]
SMALL_SEEDS = [{'committee_id': 'C00000001', 'cycle': 2026, 'origins': ['industryA']},
               {'committee_id': 'C00000002', 'cycle': 2026, 'origins': ['industryB']}]
# Frozen from the production bytes (D073 collector 0e01b506, adapter 21586e90) by make_golden.py on the same scenario (R1b rebase: schema-3 tables).
GOLDEN = {'adapter': '74d2e7e00eb565a68469aab25171ce79ae70d57975e985b20505872da4710381', 'legacy': 'ac4f8534040bdc4dae5b6d2821c3ecc260681539505a0be93e99e56cf7b56219'}


def cid(n):
    return 'C%08d' % n


def to_body(data):
    """JSON text with Decimal values written as bare numeric literals, like the provider."""
    text = json.dumps(data, default=lambda value: '\x00%s\x00' % value, separators=(',', ':'))
    return re.sub(r'"\\u0000([^"\\]+)\\u0000"', r'\1', text).encode()


# ---------------------------------------------------------------- deterministic synthetic provider
class World:
    """Pure function of (endpoint, query); never of call order, so every worker count must reach the same ledger."""
    FAIL_503 = {(5, 2026, 'E_notice')}
    FAIL_JSON = {(6, 2024, 'B')}
    FAIL_404 = {(7, 2026, 'E_regular')}
    FAIL_URL = {(8, 2022, 'E_regular')}

    def __init__(self, big=False, failures=True, fail_all_b=False):
        self.big, self.failures, self.fail_all_b = big, failures, fail_all_b

    @staticmethod
    def parse(endpoint, query):
        kind = 'B' if endpoint.endswith('schedule_b/') else ('E_notice' if str(query.get('is_notice')) == 'true' else 'E_regular')
        return int(query['committee_id'][1:]), int(query.get('two_year_transaction_period') or query['cycle']), kind

    @staticmethod
    def sid(n, cycle, i):
        return str(9000000000000000000 + (n * 10 + (cycle - 2022) // 2) * 100000 + i)

    def rows(self, n, cycle, kind):
        if kind == 'B':
            count = 1000 if (self.big and n == 3 and cycle == 2026) else {0: 0, 1: 3, 2: 12, 3: 120}[n % 4]
        else:
            count = ({0: 5, 1: 0, 2: 2} if kind == 'E_regular' else {0: 0, 1: 2, 2: 0})[n % 3]
        rows = []
        for i in range(count):
            amount = Decimal(('-12.50', '0', '10.25', '0.01', '7.00')[i % 5])
            row = {'sub_id': self.sid(n, cycle, i)}
            if kind == 'B':
                row.update(disbursement_amount=amount, disbursement_date='2024-03-%02d' % (i % 28 + 1))
                if i % 5 != 4:
                    row['recipient_committee_id'] = cid((n + i * 3) % NC + 1)
                if i % 11 == 10:
                    row['recipient_committee'] = {'committee_id': cid((n + i * 5) % NC + 1)}
                if i % 7 == 3:
                    row['candidate_id'] = 'H0AA%05d' % (i % 90 + 1)
            else:
                row.update(expenditure_amount=amount, expenditure_date='2024-04-%02d' % (i % 28 + 1), candidate_id='H0BB%05d' % (i % 90 + 1))
            rows.append(row)
        return rows

    def data(self, endpoint, query):
        n, cycle, kind = self.parse(endpoint, query)
        rows = self.rows(n, cycle, kind)
        ids = [row['sub_id'] for row in rows]
        start = ids.index(str(query['last_index'])) + 1 if 'last_index' in query else 0
        chunk = rows[start:start + 100]
        sort = 'last_disbursement_date' if kind == 'B' else 'last_expenditure_date'
        field = 'disbursement_date' if kind == 'B' else 'expenditure_date'
        cursor = {'last_index': chunk[-1]['sub_id'], sort: chunk[-1][field]} if chunk else {}
        return {'results': chunk, 'pagination': {'count': len(rows), 'is_count_exact': True, 'per_page': 100,
                                                 'pages': max(1, (len(rows) + 99) // 100), 'last_indexes': cursor}}

    def special(self, endpoint, query):
        """(body, status), 'urlerror', or None. Used by the real-adapter path."""
        n, cycle, kind = self.parse(endpoint, query)
        if self.fail_all_b and kind == 'B':
            return b'{}', 503
        if not self.failures:
            return None
        if (n, cycle, kind) in self.FAIL_503:
            return b'{}', 503
        if (n, cycle, kind) in self.FAIL_JSON:
            return b'not json at all', 200
        if (n, cycle, kind) in self.FAIL_404:
            return b'{}', 404
        if (n, cycle, kind) in self.FAIL_URL and self.failures != 'pairs':   # 'pairs': only failures that leave an archive pair
            return 'urlerror'
        return None

    def legacy(self, endpoint, query):
        """Same failures for the fake driver-only transport, which has no HTTP layer."""
        n, cycle, kind = self.parse(endpoint, query)
        if self.fail_all_b and kind == 'B':
            raise TimeoutError('inert timeout')
        if self.failures:
            if (n, cycle, kind) in self.FAIL_503:
                raise TimeoutError('inert endpoint timeout')
            if (n, cycle, kind) in self.FAIL_JSON:
                raise ValueError('inert unparseable response')
            if (n, cycle, kind) in self.FAIL_404:
                raise HTTPError('inert', 404, 'inert refusal', {}, None)
            if (n, cycle, kind) in self.FAIL_URL:
                raise OSError('inert connection failure')
        return self.data(endpoint, query)


class Response(io.BytesIO):
    def __init__(self, body, status=200, headers=None):
        super().__init__(body)
        self.status, self.headers = status, headers or {'Content-Type': 'application/json'}


class ScriptedOpener:
    """Thread-safe fake of the transport's urllib opener with overlap bookkeeping; overrides replace the Nth call's response."""
    def __init__(self, world=None, delay=0.0, overrides=None, hook=None, key_responses=None, headers=None):
        self.world, self.delay, self.overrides, self.hook = world or World(), delay, overrides or {}, hook
        self.key_responses, self.headers = key_responses or {}, headers   # per-API-key canned response; extra headers for normal pages
        self.lock = threading.Lock()
        self.calls, self.active, self.peak, self.overlap, self.threads = [], {}, 0, [], set()

    def open(self, request, timeout):
        parts = urlsplit(request.full_url)
        endpoint, query = parts.path.split('/v1/', 1)[1], dict(parse_qsl(parts.query))
        key = (endpoint, query.get('committee_id'), query.get('two_year_transaction_period') or query.get('cycle'), query.get('is_notice'))
        with self.lock:
            self.calls.append((key, request.get_header('X-api-key')))
            number = len(self.calls)
            if key in self.active:
                self.overlap.append(key)
            self.active[key] = number
            self.peak = max(self.peak, len(self.active))
            self.threads.add(threading.get_ident())
        try:
            if self.hook:
                self.hook(number, key, query)
            if self.delay:
                time.sleep(self.delay)
            api_key = request.get_header('X-api-key')
            if api_key in self.key_responses:
                return Response(*self.key_responses[api_key])
            if number in self.overrides:
                value = self.overrides[number]
                if isinstance(value, BaseException):
                    raise value
                return Response(*value)
            special = self.world.special(endpoint, query)
            if special == 'urlerror':
                raise URLError('scripted connection failure')
            if special is not None:
                return Response(*special)
            extra = self.headers(number, api_key, query) if self.headers else {}
            return Response(to_body(self.world.data(endpoint, query)), 200, {'Content-Type': 'application/json', **extra})
        finally:
            with self.lock:
                del self.active[key]


class LegacyTransport:
    """The driver-only fake of the existing controls: no per-call reporting, serial only."""
    def __init__(self, root, world, cap=100000):
        self.root, self.world, self.cap, self.maximum = Path(root), world, cap, cap
        self.root.mkdir()
        self.http_attempts, self.records = 0, []

    def fetch(self, endpoint, query, exact_decimal=False):
        assert exact_decimal
        if self.http_attempts >= self.cap:
            raise accounted.FECAdmissionRefused('inert physical request cap')
        self.http_attempts += 1
        data = self.world.legacy(endpoint, query)
        body = to_body(data)
        path = self.root / (str(self.http_attempts) + '.bin')
        path.write_bytes(body)
        record = {'path': str(path), 'sha256': hashlib.sha256(body).hexdigest(), 'bytes': len(body), 'status': 200,
                  'endpoint': endpoint, 'query': dict(query), 'original_response_bytes': True}
        self.records.append(record)
        return json.loads(body, parse_float=Decimal), record


# ---------------------------------------------------------------- canonical ledger form and conservation
TABLES = ('units', 'origin_catalog', 'origin_seeds', 'ledger_policy', 'endpoints', 'pages', 'observations', 'edges', 'counterparty_conflicts',
          'candidate_resolutions', 'candidate_references', 'leaf_coverage', 'failures')


def stem(path):
    """Archive file identity independent of the attempt number and folder: body sha12 + suffix."""
    return re.sub(r'^attempt-[0-9]{6}-', '', Path(path).name)


def normalized_record(text):
    if text is None:
        return None
    record = json.loads(text)
    for name in ('captured_utc', 'attempt'):
        record.pop(name, None)
    for name in ('path', 'receipt_path'):
        if name in record:
            record[name] = stem(record[name])
    return json.dumps(record, sort_keys=True)


def read_only(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro&immutable=1', uri=True)


def canonical(path):
    """Every table with run-dependent columns (clocks, row ids, attempt numbers, archive folders) normalized away."""
    out = {}
    with closing(read_only(path)) as db:
        for table in TABLES:
            names = [row[1] for row in db.execute('PRAGMA table_info(%s)' % table)]
            rows = []
            for values in db.execute('SELECT * FROM ' + table):
                row = dict(zip(names, values))
                if table == 'units':
                    row.pop('created')
                elif table == 'endpoints':
                    row.pop('last_attempt_at')
                    row['retry_after'] = row['retry_after'] is not None
                elif table == 'pages':
                    row['archive_path'] = stem(row['archive_path'])
                elif table == 'failures':
                    row.pop('id')
                    row.pop('at')
                    row['archive_record_json'] = normalized_record(row['archive_record_json'])
                rows.append(json.dumps(row, sort_keys=True, default=str))
            out[table] = sorted(rows)
    return out


def digest_of(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


COUNT_FIELDS = ('units', 'origin_seed_assignments', 'origin_catalog_entries', 'origin_unit_assignments', 'pages', 'observations', 'edges', 'candidate_beneficiary_or_unverified_references',
                'counterparty_identity_conflicts', 'leaf_disclosure_boundaries', 'new_pages_this_invocation',
                'endpoint_status_counts', 'supported_outgoing_endpoints_complete')


def run_world(root, *, workers=1, adapter=True, world=None, seeds=SEEDS, later=True, keys=KEYS, cap=100000, maximum=100000,
              delay=0.0, max_failed=100000, opener=None, name='frontier', ledger=None):
    """One scenario run straight through Frontier.run. Compatible with production bytes (no workers keyword when serial)."""
    root = Path(root)
    world = world or World()
    frontier = driver.Frontier(root / (name + '.sqlite'), include_later_cycle_context=later)
    frontier.seed(seeds)
    if adapter:
        opener = opener or ScriptedOpener(world, delay)
        transport = accounted.AccountedTransport(list(keys), ledger or root / 'quota.sqlite', root / ('archive-' + name), maximum, opener=opener, cap=cap)
    else:
        opener, transport = None, LegacyTransport(root / ('raw-' + name), world)
    try:
        report = frontier.run(transport, max_failed_attempts=max_failed, **({} if workers == 1 else {'workers': workers}))
    finally:
        frontier.close()
        if adapter:
            transport.close()
    return frontier, transport, opener, report


def scenario_digest(root, workers=1, adapter=True, **options):
    frontier, transport, opener, report = run_world(root, workers=workers, adapter=adapter, ledger=Path(root) / (options.get('name', 'frontier') + '-quota.sqlite'), **options)
    tables = canonical(frontier.path)
    summary = {name: report[name] for name in COUNT_FIELDS}
    extra = {'http_attempts': transport.http_attempts}
    if adapter:
        with closing(sqlite3.connect(Path(root) / (options.get('name', 'frontier') + '-quota.sqlite'))) as db:
            extra['budget_used'] = db.execute('SELECT COALESCE(SUM(requests_used),0) FROM api_request_budget').fetchone()[0]
    return digest_of({'tables': tables, 'report': summary, **extra}), frontier, transport, opener, report


def scenario_digests(root):
    root = Path(root)
    return {'adapter': scenario_digest(root, adapter=True, name='adapter')[0], 'legacy': scenario_digest(root, adapter=False, name='legacy')[0]}


def conservation(state, folders=None):
    """The validator's R1 equalities, independent of it: every archived pair is referenced exactly once and
    SUM(attempt_count) equals the physical attempts of every receipt. Returns a dict of problems (empty when sound)."""
    state = Path(state)
    folders = [Path(p) for p in (folders if folders is not None else sorted(state.glob('responses-*')))]
    receipts = [json.loads(p.read_bytes()) for p in sorted(state.glob('RECEIPT-*.json'))]
    problems = {}
    with closing(read_only(state / 'frontier.sqlite')) as db:
        used = {}
        for (path,) in db.execute('SELECT archive_path FROM pages'):
            used[os.path.normcase(str(Path(path).resolve()))] = used.get(os.path.normcase(str(Path(path).resolve())), 0) + 1
        bodies = 0
        for (text,) in db.execute('SELECT archive_record_json FROM failures WHERE archive_record_json IS NOT NULL'):
            for path in re.findall(r'[^"]+\.body\.bin', json.dumps(json.loads(text))):
                key = os.path.normcase(str(Path(path).resolve()))
                used[key] = used.get(key, 0) + 1
                bodies += 1
        pages = db.execute('SELECT COUNT(*) FROM pages').fetchone()[0]
        attempts = db.execute('SELECT COALESCE(SUM(attempt_count),0) FROM endpoints').fetchone()[0]
    pairs = unpaired = 0
    unreferenced, multiply = [], []
    for folder in folders:
        names = sorted(p.name for p in folder.iterdir()) if folder.is_dir() else []
        body_stems = {n[:-9] for n in names if n.endswith('.body.bin')}
        meta_stems = {n[:-5] for n in names if n.endswith('.json')}
        unpaired += len(body_stems ^ meta_stems)
        for item in sorted(body_stems & meta_stems):
            pairs += 1
            count = used.get(os.path.normcase(str((folder / (item + '.body.bin')).resolve())), 0)
            if count == 0:
                unreferenced.append(item)
            elif count > 1:
                multiply.append(item)
    if unreferenced:
        problems['unreferenced_pairs'] = unreferenced
    if multiply:
        problems['multiply_referenced_pairs'] = multiply
    if pairs != pages + bodies:
        problems['pairs_vs_pages_plus_failure_bodies'] = (pairs, pages, bodies)
    if receipts and attempts != sum(r['http_attempts'] for r in receipts):
        problems['attempts_vs_receipts'] = (attempts, [r['http_attempts'] for r in receipts])
    if unpaired:
        problems['unpaired_files'] = unpaired
    return problems


_VALIDATOR = {}


def validator():
    path = os.environ.get('D071_VALIDATOR')
    if not path:
        return None
    if 'module' not in _VALIDATOR:
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == VALIDATOR_SHA256, 'validator is not the reviewed v5.2 file'
        sys.dont_write_bytecode = True
        sys.path.append(str(Path(path).parent))
        spec = importlib.util.spec_from_file_location('validate_downstream_ledger', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _VALIDATOR['module'] = module
    return _VALIDATOR['module']


def tree_listing(root):
    return sorted((str(p.relative_to(root)), p.stat().st_size, p.stat().st_mtime_ns) for p in Path(root).rglob('*') if p.is_file())


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='D071-inert-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for guard in (mock.patch.object(fec_key_store, 'load_keys', side_effect=AssertionError('credentials forbidden')),
                      mock.patch('socket.socket.connect', side_effect=AssertionError('network forbidden')),
                      mock.patch('socket.getaddrinfo', side_effect=AssertionError('DNS forbidden'))):
            guard.start()
            self.addCleanup(guard.stop)
        self.state = self.root / 'state'
        self.seeds = self.root / 'seeds.json'
        self.snapshots = 0

    def fresh(self):
        """A clean directory/ledger/state for the next subTest."""
        self.tmp.cleanup()
        self.tmp = tempfile.TemporaryDirectory(prefix='D071-inert-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state, self.seeds, self.snapshots = self.root / 'state', self.root / 'seeds.json', 0

    def write_seeds(self, rows=SEEDS):
        self.seeds.write_text(json.dumps({'schema_version': 1, 'seed_units': rows}))

    def adapter(self, opener=None, keys=KEYS, maximum=None, cap=100000, world=None):
        """AccountedTransport.live replacement: the real transport on this test's budget ledger and scripted opener."""
        def live(archive, requested, **options):
            return accounted.AccountedTransport(list(keys), self.root / 'quota.sqlite', archive, requested if maximum is None else maximum,
                                                opener=opener or ScriptedOpener(world or World()), cap=cap)
        return live

    def invoke(self, flags, live=None, state=None):
        """Run main() in-process; returns the JSON line it prints."""
        argv = ['collect', '--state', str(state or self.state)] + flags
        if '--live' in flags and not (Path(state or self.state) / 'frontier.sqlite').exists() and '--init-new-ledger' not in flags:
            argv.append('--init-new-ledger')   # R1b: a missing ledger is refused unless the caller says so
        output = io.StringIO()
        patches = [mock.patch.object(sys, 'argv', argv)]
        if live is not None:
            patches.append(mock.patch.object(accounted.AccountedTransport, 'live', side_effect=live))
        for patch in patches:
            patch.start()
        try:
            with redirect_stdout(output):
                driver.main()
        finally:
            for patch in reversed(patches):
                patch.stop()
        return json.loads(output.getvalue().strip().splitlines()[-1]) if output.getvalue().strip() else {}

    def collect(self, extra, live, later=True):
        self.write_seeds(self.seeds_rows if hasattr(self, 'seeds_rows') else SEEDS)
        flags = ['--seeds', str(self.seeds), '--live'] + (['--include-later-cycle-context'] if later else []) + extra
        printed = self.invoke(flags, live)
        receipt = Path(printed['receipt'])
        return receipt, json.loads(receipt.read_bytes())

    def snapshot(self, receipt):
        """The accepted-batch byte copy plus its proof, exactly as an observer would take it."""
        self.snapshots += 1
        target = self.state / 'accepted-batches' / ('batch-%02d' % self.snapshots)
        target.mkdir(parents=True)
        ledger, copy = self.state / 'frontier.sqlite', target / 'frontier.sqlite'
        shutil.copyfile(ledger, copy)
        digest = hashlib.sha256(copy.read_bytes()).hexdigest()
        proof = target / 'SNAPSHOT.json'
        proof.write_text(json.dumps({'schema_version': 1, 'original_ledger': str(ledger), 'snapshot': str(copy), 'source_sha256_before': digest,
                                     'snapshot_sha256': digest, 'source_sha256_after': digest, 'no_journal_wal_before_after': True,
                                     'original_collection_receipt': {'path': str(receipt), 'sha256': hashlib.sha256(receipt.read_bytes()).hexdigest()}}))
        return proof

    def validate(self, label, receipts, proofs=()):
        """Issues from the reviewed validator over the receipt chain (None when it is not supplied)."""
        module = validator()
        if module is None:
            return None
        pins = [hashlib.sha256(Path(r).read_bytes()).hexdigest() for r in receipts]
        result = module.validate(self.state / 'frontier.sqlite', self.root, [str(r) for r in receipts], None, prior_snapshots=[str(p) for p in proofs],
                                 expect_receipt_sha256=pins if len(receipts) > 1 else [])
        evidence = os.environ.get('D071_VAL_EVIDENCE')
        if evidence:
            Path(evidence).mkdir(parents=True, exist_ok=True)
            slim = {key: result[key] for key in ('validator_version', 'integrity_checks_passed', 'issues', 'ledger_counts', 'receipt_chain', 'later_context_flags')}
            (Path(evidence) / (re.sub(r'[^A-Za-z0-9_.-]', '_', self.id().split('.', 1)[-1] + '-' + label) + '.json')).write_text(json.dumps(slim, indent=2, default=str))
        return result['issues']

    def assert_sound(self, receipts, proofs=(), label='final', folders=None):
        self.assertEqual(conservation(self.state, folders), {})
        issues = self.validate(label, receipts, proofs)
        if issues is not None:
            self.assertEqual(issues, [])

    def budget_used(self):
        with closing(sqlite3.connect(self.root / 'quota.sqlite')) as db:
            return db.execute('SELECT COALESCE(SUM(requests_used),0) FROM api_request_budget').fetchone()[0]

    def rows(self, sql, *args):
        with closing(read_only(self.state / 'frontier.sqlite')) as db:
            return db.execute(sql, args).fetchall()


class Concurrency(Base):
    # ---------------------------------------------------------------- 2: N=1 parity with the production bytes
    def test_n1_is_byte_for_byte_the_production_ledger_on_the_same_inert_fixtures(self):
        """The golden digests were produced by the pre-D071 collector and adapter on this scenario (see make_golden.py)."""
        got = scenario_digests(self.root)
        self.assertEqual(got, GOLDEN, 'N=1 diverged from the production ledger/archive/receipt result')
        frontier, transport, opener, report = run_world(self.root, name='sanity')
        counts = report['endpoint_status_counts']
        for status in ('E_notice:blocked', 'B:inconsistent', 'E_regular:source_refused', 'E_regular:blocked'):
            self.assertGreaterEqual(counts.get(status, 0), 1, status + ' must be exercised by the scenario')
        self.assertGreater(report['edges'], 50)
        self.assertGreater(report['counterparty_identity_conflicts'], 0)
        self.assertGreater(report['leaf_disclosure_boundaries'], 0)

    # ---------------------------------------------------------------- 3: N=4 and N=8 reach the same ledger as sets
    def test_n4_and_n8_reach_the_n1_ledger_as_sets(self):
        for workers in (4, 8):
            with self.subTest(workers=workers):
                result, frontier, transport, opener, report = scenario_digest(self.root, workers=workers, name='n%d' % workers)
                self.assertEqual(result, GOLDEN['adapter'])
                self.assertEqual((report['workers'], report['peak_in_flight'], report['halted_unadmitted_jobs']), (workers, workers, 0))
                self.assertEqual(report['attempts_recorded'], transport.http_attempts)
                self.assertEqual(opener.overlap, [])
                self.assertGreater(len(opener.threads), 1)

    # ---------------------------------------------------------------- 4: real concurrency, never the same endpoint twice
    def test_requests_overlap_in_time_but_never_for_one_endpoint(self):
        delay = 0.2
        timings = {}
        for workers in (1, 4):
            start = time.monotonic()
            _, transport, opener, report = run_world(self.root, workers=workers, seeds=SMALL_SEEDS, later=False, delay=delay, name='timing%d' % workers)
            timings[workers] = time.monotonic() - start
            self.assertEqual(opener.overlap, [])
        self.assertEqual((opener.peak, report['peak_in_flight']), (4, 4))
        self.assertGreater(timings[1] / timings[4], 2.0, 'four overlapping requests: wall time is about total/N, not serial (%r)' % timings)

    # ---------------------------------------------------------------- 5: sequential chains under N=8
    def test_ten_page_chains_stay_sequential_and_validator_clean_under_n8(self):
        self.seeds_rows = [{'committee_id': 'C00000003', 'cycle': 2026, 'origins': ['industryA']}, {'committee_id': 'C00000002', 'cycle': 2026, 'origins': ['industryB']}]
        receipt, data = self.collect(['--workers', '8', '--max-requests', '5000'], self.adapter(world=World(big=True)), later=False)
        world = World(big=True)
        rows = world.rows(3, 2026, 'B')
        self.assertEqual(len(rows), 1000)
        pages = self.rows("SELECT page_no,request_query_json FROM pages WHERE committee_id='C00000003' AND cycle=2026 AND kind='B' ORDER BY page_no")
        self.assertEqual([p[0] for p in pages], list(range(1, 11)))
        for number, text in pages:
            query = json.loads(text)
            if number == 1:
                self.assertNotIn('last_index', query)
            else:
                self.assertEqual(query['last_index'], rows[100 * (number - 1) - 1]['sub_id'])
                self.assertEqual(query['last_disbursement_date'], rows[100 * (number - 1) - 1]['disbursement_date'])
        self.assertEqual(data['workers'], 8)
        self.assert_sound([receipt])

    # ---------------------------------------------------------------- 6: end to end through main(), two accepted batches
    def test_main_runs_two_concurrent_batches_and_the_validator_chain_is_clean(self):
        first, one = self.collect(['--workers', '4', '--max-requests', '37'], self.adapter())
        self.assertEqual((one['http_attempts'], one['workers'], one['stop']['error_type']), (37, 4, 'FECAdmissionRefused'))
        self.assert_sound([first], label='batch1')
        proof = self.snapshot(first)
        second, two = self.collect(['--workers', '4', '--max-requests', '5000'], self.adapter())
        self.assertIsNone(two['stop'])
        self.assertEqual(two['endpoint_status_counts'].get('E_notice:blocked'), 1, 'the scripted 503 endpoint stays cooling, as designed')
        self.assertEqual(self.rows('SELECT COALESCE(SUM(attempt_count),0) FROM endpoints')[0][0], one['http_attempts'] + two['http_attempts'])
        self.assert_sound([first, second], [proof], label='chain')
        self.assertEqual(self.budget_used(), one['http_attempts'] + two['http_attempts'])
        starts = sorted(self.state.glob('RUN-*.START.json'), key=lambda p: p.stat().st_mtime_ns)
        self.assertEqual(len(starts), 2)
        start = json.loads(starts[1].read_bytes())
        self.assertEqual((start['workers'], start['max_requests'], start['ledger_at_start']['sum_attempt_count']), (4, 5000, 37))

    # ---------------------------------------------------------------- 7: the physical cap is exact with N in flight
    def test_physical_cap_is_exact_when_it_is_not_a_multiple_of_the_worker_count(self):
        for workers, maximum in ((4, 37), (8, 43), (3, 10)):
            with self.subTest(workers=workers, maximum=maximum):
                self.fresh()
                receipt, data = self.collect(['--workers', str(workers), '--max-requests', str(maximum)], self.adapter(opener=ScriptedOpener(World(), delay=0.02)))
                self.assertEqual((data['http_attempts'], data['stop']['error_type']), (maximum, 'FECAdmissionRefused'))
                self.assertIn('Physical HTTP request cap', data['stop']['detail'])
                self.assertEqual(self.rows("SELECT COUNT(*) FROM failures WHERE error_type='FECAdmissionRefused'")[0][0], 1)
                self.assertEqual((self.budget_used(), data['attempts_recorded']), (maximum, maximum))
                self.assert_sound([receipt])

    # ---------------------------------------------------------------- 8: shared quota exhausted with N in flight
    def test_shared_quota_exhaustion_with_four_in_flight_records_every_admitted_attempt(self):
        events, lock = [], threading.Lock()
        real_acquire = accounted.ImmediateRequestPool.acquire

        def traced(pool, lane):
            try:
                index = real_acquire(pool, lane)
            except accounted.FECAdmissionRefused:
                with lock:
                    events.append('refuse')
                raise
            with lock:
                events.append('admit')
            return index
        with mock.patch.object(accounted.ImmediateRequestPool, 'acquire', traced):
            receipt, data = self.collect(['--workers', '4', '--max-requests', '5000'],
                                         self.adapter(opener=ScriptedOpener(World(), delay=0.05), keys=KEYS[:2], cap=5))
        self.assertEqual(data['stop']['error_type'], 'FECAdmissionRefused')
        self.assertIn('Shared FEC quota', data['stop']['detail'])
        first = events.index('refuse')
        self.assertLessEqual(events[first:].count('admit'), 3, 'at most N-1 admissions after the first refusal')
        self.assertEqual((data['http_attempts'], data['attempts_recorded'], self.budget_used()), (10, 10, 10))
        refused = self.rows("SELECT COUNT(*) FROM failures WHERE detail LIKE 'Shared FEC quota%'")[0][0]
        self.assertTrue(1 <= refused <= 4, refused)
        self.assertGreaterEqual(data['halted_unadmitted_jobs'], 0)
        self.assert_sound([receipt])

    # ---------------------------------------------------------------- 9: busy is not exhausted
    def test_a_busy_key_is_waited_for_and_only_true_exhaustion_refuses(self):
        _, transport, opener, report = run_world(self.root, workers=4, seeds=SMALL_SEEDS, later=False, keys=KEYS[:1], delay=0.03, name='busy')
        self.assertIsNone(report['stop'], 'one key (two in flight each), four workers: busy keys must be waited for, not refused')
        self.assertEqual((report['peak_in_flight'], opener.peak), (4, 2), 'per-key concurrency is tracked and capped')
        self.assertEqual(opener.overlap, [])
        _, transport, opener, report = run_world(self.root, workers=4, seeds=SMALL_SEEDS, later=False, keys=KEYS[:2], delay=0.03, cap=1, name='dry', ledger=self.root / 'dry.sqlite')
        self.assertEqual(report['stop']['error_type'], 'FECAdmissionRefused')
        self.assertEqual(transport.http_attempts, 2, 'each of the two keys admits exactly its cap, then true exhaustion refuses')

    # ---------------------------------------------------------------- 10: provider stops mid-flight
    def test_401_and_403_stop_globally_and_drain_every_job(self):
        for status in (401, 403):
            with self.subTest(status=status):
                self.fresh()
                opener = ScriptedOpener(World(), delay=0.03, overrides={9: (b'{}', status)})
                receipt, data = self.collect(['--workers', '4', '--max-requests', '5000'], self.adapter(opener=opener))
                self.assertEqual((data['stop']['error_type'], data['stop']['detail']), ('HTTPError', 'HTTP Error %d: FEC evidence response was not successful.' % status))
                hit = self.rows("SELECT status FROM endpoints WHERE last_error LIKE ?", '%%%d%%' % status)
                self.assertEqual(hit[0][0], 'blocked')
                self.assertEqual(data['attempts_recorded'], data['http_attempts'])
                self.assertLessEqual(len(opener.calls), 9 + 3, 'dispatch stops once the stop is recorded; at most the N-1 others were in flight')
                self.assert_sound([receipt])

    # ---------------------------------------------------------------- 20: a 429 cools one key, never the run
    def test_a_429_cools_only_its_own_key_and_the_other_keys_finish_the_run(self):
        opener = ScriptedOpener(World(failures=False), delay=0.03)
        poisoned = KEYS[2]
        opener.key_responses = {poisoned: (b'{}', 429), KEYS[5]: (b'{}', 429)}
        made, build = [], self.adapter(opener=opener, world=World(failures=False))

        def live(archive, requested, **options):
            made.append(build(archive, requested))
            return made[-1]
        # allowance 1: were 429s counted as failures, the second one would stop the run
        receipt, data = self.collect(['--workers', '6', '--max-requests', '5000', '--max-failed-attempts', '1'], live)
        pool = made[0].pool
        for index in (2, 5):
            self.assertGreaterEqual(pool.cooldown[index], pool.window_started[index] + pool.window - 1, 'cooled until the key window resets, not for a fixed short pause')
        self.assertIsNone(data['stop'], 'a 429 on one key must not stop the run')
        self.assertEqual(data['rate_limited_responses'], 2)
        for key in (poisoned, KEYS[5]):
            self.assertEqual(sum(1 for _, used in opener.calls if used == key), 1, 'the 429 key is cooled until its window resets')
        self.assertGreater(len(opener.calls), 60)
        rows = self.rows("SELECT error_type,detail FROM failures WHERE detail LIKE '%429%'")
        self.assertEqual(rows, [('HTTPError', 'HTTP Error 429: FEC evidence response was not successful.')] * 2)
        # R1b owner ruling: a 429'd endpoint returns to pending at once, so another key serves it; nothing stays parked, and each 429 left its failure row + archive pair.
        self.assertEqual(self.rows("SELECT COUNT(*) FROM endpoints WHERE status IN('blocked','pending')")[0][0], 0)
        self.assertGreaterEqual(self.rows("SELECT COUNT(*) FROM failures f JOIN pages p ON p.committee_id=f.committee_id AND p.cycle=f.cycle AND p.kind=f.kind WHERE f.detail LIKE '%429%'")[0][0], 2)
        self.assertEqual((data['attempts_recorded'], self.budget_used()), (data['http_attempts'], data['http_attempts']))
        self.assert_sound([receipt])

    def test_a_depleted_rate_limit_header_cools_only_that_key_and_the_page_still_commits(self):
        def header(number, api_key, query):
            return {'X-RateLimit-Remaining': '0'} if api_key == KEYS[3] else {}
        opener = ScriptedOpener(World(failures=False), delay=0.02, headers=header)
        made, build = [], self.adapter(opener=opener, world=World(failures=False))

        def live(archive, requested, **options):
            made.append(build(archive, requested))
            return made[-1]
        receipt, data = self.collect(['--workers', '6', '--max-requests', '5000'], live)
        pool = made[0].pool
        self.assertGreaterEqual(pool.cooldown[3], pool.window_started[3] + pool.window - 1)
        self.assertIsNone(data['stop'])
        self.assertEqual((data['rate_limited_responses'], self.rows('SELECT COUNT(*) FROM failures')[0][0]), (0, 0))
        self.assertEqual(sum(1 for _, key in opener.calls if key == KEYS[3]), 1, 'allowance exhausted per the header: no second request on that key this window')
        self.assert_sound([receipt])

    # ---------------------------------------------------------------- 21: rebalance and saturation across 21 keys at N=32
    def test_dispatch_rebalances_to_keys_that_still_have_allowance(self):
        cap, nearly = 10, KEYS[1]
        with closing(sqlite3.connect(self.root / 'quota.sqlite')) as db, db:
            ensure_schema(db)
            db.execute('INSERT INTO api_request_budget VALUES (?,?,?)', (budget_id(nearly), time.time(), cap - 1))
        opener = ScriptedOpener(World(failures=False), delay=0.02)
        receipt, data = self.collect(['--workers', '6', '--max-requests', '5000'], self.adapter(opener=opener, world=World(failures=False), cap=cap))
        used = {key: n for key, n in self.rows_at(self.root / 'quota.sqlite', 'SELECT budget_id,requests_used FROM api_request_budget')}
        self.assertEqual(used[budget_id(nearly)], cap)
        self.assertEqual(sum(1 for _, key in opener.calls if key == nearly), 1, 'its one remaining request was spent, then the run moved on')
        self.assertGreater(sum(1 for _, key in opener.calls if key != nearly), 60)
        self.assert_sound([receipt])

    def test_thirty_two_workers_saturate_twenty_one_keys_and_every_key_spends_its_whole_allowance(self):
        self.seeds_rows = ALL_SEEDS
        opener = ScriptedOpener(World(failures=False), delay=2.0)
        receipt, data = self.collect(['--workers', '32', '--max-requests', '5000'], self.adapter(opener=opener, keys=KEYS21, cap=4, world=World(failures=False)), later=False)
        self.assertEqual(data['stop']['error_type'], 'FECAdmissionRefused', 'all 21 keys exhausted is the global stop')
        self.assertIn('Shared FEC quota', data['stop']['detail'])
        used = [n for _, n in self.rows_at(self.root / 'quota.sqlite', 'SELECT budget_id,requests_used FROM api_request_budget')]
        self.assertEqual((len(used), set(used), data['http_attempts']), (21, {4}, 84), 'every key spent exactly its allowance')
        self.assertEqual(data['peak_in_flight'], 32)
        self.assertGreaterEqual(opener.peak, 28, 'in-flight HTTP stayed near the worker count (refill batch of 4)')
        self.assertEqual(opener.overlap, [])
        self.assertGreater(data['achieved_requests_per_hour'], 0)
        self.assertEqual(data['attempts_recorded'], 84)
        self.assert_sound([receipt])

    def test_worker_cap_is_32(self):
        frontier = driver.Frontier(self.root / 'cap.sqlite')
        self.addCleanup(frontier.close)
        with self.assertRaises(ValueError):
            frontier.run(LegacyTransport(self.root / 'raw', World()), workers=33)
        with self.assertRaises(ValueError):
            frontier.run(LegacyTransport(self.root / 'raw2', World()), workers=0)
        self.write_seeds()
        with self.assertRaises(SystemExit):
            self.invoke(['--seeds', str(self.seeds), '--max-requests', '5', '--live', '--workers', '33'], live=lambda *a, **k: self.fail('never reached'))
        self.assertEqual(driver.MAX_WORKERS, 32)

    # ---------------------------------------------------------------- 22: telemetry never carries a key value
    def test_progress_telemetry_reports_rates_in_flight_and_per_key_allowance_by_hash_only(self):
        lines = []
        frontier = driver.Frontier(self.root / 'telemetry.sqlite')
        frontier.seed(ALL_SEEDS[:12])
        transport = accounted.AccountedTransport(list(KEYS), self.root / 'quota.sqlite', self.root / 'archive', 5000, opener=ScriptedOpener(World(failures=False), delay=0.03), cap=50)
        try:
            report = frontier.run(transport, workers=8, progress=lambda snapshot: lines.append(json.loads(json.dumps(snapshot))), progress_seconds=0.1)
        finally:
            transport.close()
            frontier.close()
        self.assertGreaterEqual(len(lines), 2)
        text = json.dumps(lines)
        for key in KEYS:
            self.assertNotIn(key, text, 'a progress line must never contain a key value')
        last = lines[-1]
        for name in ('elapsed_s', 'physical_attempts', 'in_flight', 'requests_per_hour_60s', 'requests_per_hour_10min', 'pages_committed', 'rate_limited_responses'):
            self.assertIn(name, last)
        self.assertEqual(len(last['transport']['keys']), len(KEYS))
        for entry in last['transport']['keys']:
            self.assertRegex(entry['key'], r'^[0-9a-f]{12}$')
            self.assertTrue(entry['remaining'] is None or 0 <= entry['remaining'] <= 50)
        self.assertGreater(max(line['requests_per_hour_60s'] for line in lines), 0)
        self.assertTrue(any(line['in_flight'] > 1 for line in lines))
        for name in ('achieved_requests_per_hour', 'rolling_60s_requests_per_hour_peak', 'rolling_10min_requests_per_hour_last', 'run_seconds'):
            self.assertIn(name, report)
        self.assertGreater(report['achieved_requests_per_hour'], 0)

    # ---------------------------------------------------------------- 11: archive failure mid-flight
    def test_archive_failure_mid_flight_stops_globally_and_other_pairs_still_commit(self):
        real = accounted.durable_new

        def failing(path, body):
            if str(path).endswith('.json') and 'attempt-000007-' in str(path):
                raise OSError('inert storage failure')
            return real(path, body)
        with mock.patch.object(accounted, 'durable_new', failing):
            receipt, data = self.collect(['--workers', '4', '--max-requests', '5000'], self.adapter(opener=ScriptedOpener(World(), delay=0.02)))
        self.assertEqual(data['stop']['error_type'], 'FECArchiveFailed')
        self.assertGreater(data['pages'], 5)
        problems = conservation(self.state)
        self.assertEqual(problems, {'unpaired_files': 1}, 'only the pre-existing unpaired-body event, nothing else')
        issues = self.validate('archive-failure', [receipt])
        if issues is not None:
            self.assertEqual([issue['reason'] for issue in issues], ['Archive body and metadata do not pair'])
        self.assertEqual(data['attempts_recorded'], data['http_attempts'])

    # ---------------------------------------------------------------- 12: failure allowance
    def test_failure_allowance_overshoots_by_at_most_n_minus_one_and_cooldowns_survive(self):
        world, workers, allowance = World(fail_all_b=True, failures=False), 4, 3
        frontier, transport, opener, report = run_world(self.root, workers=workers, world=world, later=False, max_failed=allowance, delay=0.03, name='allow')
        failures = self.rows_at(frontier.path, 'SELECT COUNT(*) FROM failures')[0][0]
        self.assertTrue(allowance <= failures <= allowance + workers - 1, failures)
        self.assertIsNotNone(report['stop'])
        blocked = {tuple(r[:3]): r[3] for r in self.rows_at(frontier.path, "SELECT committee_id,cycle,kind,attempt_count,retry_after FROM endpoints WHERE status='blocked'")}
        self.assertTrue(blocked)
        for row in self.rows_at(frontier.path, "SELECT retry_after FROM endpoints WHERE status='blocked'"):
            self.assertGreater(row[0], time.time(), 'the persisted cooldown is in the future')
        again = driver.Frontier(frontier.path, include_later_cycle_context=False)
        try:
            second = accounted.AccountedTransport([KEY], self.root / 'quota.sqlite', self.root / 'archive-second', 100000, opener=ScriptedOpener(world))
            again.run(second, max_failed_attempts=100000, workers=4)
            second.close()
        finally:
            again.close()
        after = {tuple(r[:3]): r[3] for r in self.rows_at(frontier.path, "SELECT committee_id,cycle,kind,attempt_count FROM endpoints WHERE status='blocked'")}
        for key, attempts in blocked.items():
            self.assertEqual(after.get(key, attempts), attempts, 'a cooling endpoint is not re-attempted on resume')

    def rows_at(self, path, sql, *args):
        with closing(read_only(path)) as db:
            return db.execute(sql, args).fetchall()

    # ---------------------------------------------------------------- 14: run lease
    def test_a_second_invocation_is_refused_before_any_write_while_the_first_runs(self):
        refusals = []

        def live(archive, requested, **options):
            before = tree_listing(self.state)
            with self.assertRaises(SystemExit) as caught:
                self.invoke(['--seeds', str(self.seeds), '--max-requests', '5', '--live'], live=lambda *a, **k: self.fail('a refused invocation must not reach the transport'))
            refusals.append((str(caught.exception), tree_listing(self.state) == before))
            with self.assertRaises(SystemExit):
                self.invoke(['--recover-crashed-runs'])
            return self.adapter()(archive, requested)
        receipt, data = self.collect(['--max-requests', '5'], live)
        self.assertEqual(len(refusals), 1)
        self.assertIn('run lease', refusals[0][0])
        self.assertTrue(refusals[0][1], 'the refused invocation changed nothing in the state directory')
        self.assertEqual(data['http_attempts'], 5)
        with driver.run_lease(self.state):
            pass   # released when the first invocation ended

    # ---------------------------------------------------------------- 15: the transport under contention
    def test_transport_admits_exactly_the_cap_under_eight_contending_threads(self):
        cap = 137
        transport = accounted.AccountedTransport(list(KEYS), self.root / 'quota.sqlite', self.root / 'archive', cap, opener=ScriptedOpener(World(failures=False), delay=0.05), cap=100000)
        self.addCleanup(transport.close)
        calls, errors, lock = [], [], threading.Lock()

        def worker(thread):
            for j in range(50):
                call = {}
                try:
                    transport.fetch('schedules/schedule_b/', {'committee_id': cid(thread * 50 + j + 1), 'per_page': 100, 'two_year_transaction_period': 2026}, exact_decimal=True, call=call)
                except accounted.FECAdmissionRefused:
                    pass
                except BaseException as error:
                    with lock:
                        errors.append(repr(error))
                with lock:
                    calls.append(call.get('attempts', 0))
        with ThreadPoolExecutor(8) as pool:
            list(pool.map(worker, range(8)))
        self.assertEqual(errors, [])
        self.assertEqual((len(calls), sum(calls), transport.http_attempts, transport.reserved), (400, cap, cap, 0))
        self.assertEqual(sorted(record['attempt'] for record in transport.records), list(range(1, cap + 1)))
        self.assertEqual(self.budget_used(), cap)
        self.assertEqual((len(list(transport.archive.glob('*.body.bin'))), len(list(transport.archive.glob('*.json')))), (cap, cap))

    # ---------------------------------------------------------------- 16: the ledger has exactly one writer thread
    def test_only_the_main_thread_touches_the_ledger_connection(self):
        class Recorder:
            def __init__(self, db):
                self._db, self.idents = db, set()

            def execute(self, *args, **kwargs):
                self.idents.add(threading.get_ident())
                return self._db.execute(*args, **kwargs)

            def __getattr__(self, name):
                return getattr(self._db, name)
        frontier = driver.Frontier(self.root / 'one-writer.sqlite')
        frontier.seed(SMALL_SEEDS)
        frontier.db = recorder = Recorder(frontier.db)
        opener = ScriptedOpener(World())
        transport = accounted.AccountedTransport([KEY], self.root / 'quota.sqlite', self.root / 'archive', 1000, opener=opener)
        try:
            frontier.run(transport, workers=4)
        finally:
            transport.close()
            frontier.close()
        self.assertEqual(recorder.idents, {threading.get_ident()})
        self.assertNotIn(threading.get_ident(), opener.threads - {threading.get_ident()})
        self.assertTrue(opener.threads - {threading.get_ident()}, 'the fetches ran on worker threads')

    # ---------------------------------------------------------------- 19: the validator catches the bug class this design prevents
    def test_validator_and_main_catch_a_dropped_outcome(self):
        class Dropping(driver.Frontier):
            def _commit(self, job, outcome):
                self.seen = getattr(self, 'seen', 0) + 1
                return None if self.seen == 5 else super()._commit(job, outcome)
        # main() refuses to write a receipt for a ledger that did not record every physical attempt.
        with mock.patch.object(driver, 'Frontier', Dropping):
            with self.assertRaisesRegex(RuntimeError, 'ledger did not record every physical attempt'):
                self.collect(['--workers', '4', '--max-requests', '5000'], self.adapter())
        self.assertEqual(list(self.state.glob('RECEIPT-*.json')), [], 'no receipt for an unreconciled run')
        self.assertEqual(len(list(self.state.glob('RUN-*.START.json'))), 1, 'the unresolved START is what recovery keys on')
        # A receipt for the same ledger written anyway (as a faulty collector would) is caught by the validator and by the equalities.
        broken = self.root / 'broken'
        broken.mkdir()
        frontier = Dropping(broken / 'frontier.sqlite', include_later_cycle_context=True)
        frontier.seed(SEEDS)
        transport = accounted.AccountedTransport([KEY], self.root / 'quota2.sqlite', broken / 'responses-x', 5000, opener=ScriptedOpener(World()))
        report = frontier.run(transport, workers=4)
        report.update(http_attempts=transport.http_attempts, physical_request_cap=5000, failed_attempt_allowance=8, raw_archive_directory=str(transport.archive),
                      ledger_sha256=driver.sha(frontier.path), seed_receipt={'path': 'x', 'sha256': 'f' * 64, 'bytes': 1})
        transport.close()
        frontier.close()
        (broken / 'RECEIPT-x.json').write_text(json.dumps(report, default=str))
        problems = conservation(broken)
        self.assertIn('unreferenced_pairs', problems)
        self.assertIn('attempts_vs_receipts', problems)
        module = validator()
        if module is not None:
            result = module.validate(broken / 'frontier.sqlite', broken, [str(broken / 'RECEIPT-x.json')], None)
            reasons = {issue['reason'] for issue in result['issues']}
            self.assertIn('Archive pair is not referenced by exactly one page or failure row', reasons)
            self.assertIn('Receipt http_attempts differs from the endpoint attempt_count delta', reasons)
            self.assertIn('Archived response pairs differ from new pages plus new archived failure bodies', reasons)


if __name__ == '__main__':
    unittest.main()

"""Records sidecar (peer finding 2) and the reported-number memo (review M6), batch 2, 2026-09-25.

/api/contributions/records pages observations by (date DESC, source_id). The sidecar keeps them in
that order so a page no longer sorts and counts the whole index. Every answer here is compared with
`reference()`, a verbatim copy of the records query as it stood before the sidecar (the oracle), on
an index whose physical row order is shuffled against the record order and whose dates tie often.
"""
from contextlib import closing
import json
from pathlib import Path
import random
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import contribution_data as data
import contribution_query as query
import contribution_query_store as store
import contribution_semantics as semantics
import proclock
import records_sidecar as sidecar

CONDUIT = 'C00401224'


def make_index(path, rows=600, seed=11):
    """A shuffled analytical index: dates tie in groups, source IDs sort as text, money has zeros and
    refunds, some observations are unresolved; physical rowids do not follow record order."""
    rng = random.Random(seed)
    days = ['2024-%02d-%02d' % (m, d) for m in (1, 2, 3, 6, 11) for d in (1, 2, 15, 28)] + ['2025-01-05', '2025-07-04']
    companies = ['Alpha Bank', 'Beta Credit', 'Gamma Trust', 'Delta Savings']
    committees = [CONDUIT, 'C00004812', 'C00694323', 'C00123456']
    ids = set()
    while len(ids) < rows:
        ids.add(str(rng.choice([1, 40, 407, 9]) * 10 ** rng.randrange(8, 12) + rng.randrange(10 ** 7)))
    ids = list(ids)
    rng.shuffle(ids)
    facts = []
    with closing(sqlite3.connect(path)) as db:
        data._schema(db)
        for number, source_id in enumerate(ids):
            day = rng.choice(days)
            raw = rng.choice([0, 500, 2500, 10000, 100000, -2500, 7300])
            value = {key: '' for key in data.DIMENSIONS}
            value.update(source_id=source_id, industry='Example', company=rng.choice(companies),
                         cycle='2023-2024' if day < '2025' else '2025-2026', year=day[:4], date=day,
                         zip5=rng.choice(['01234', '10001', '99999', '', '10025']), state=rng.choice(['MA', 'NY', 'CA', '']),
                         committee_id=rng.choice(committees), recipient=rng.choice(['Committee A', 'Committee B', 'ActBlue']),
                         employer=rng.choice(['Example Bank', 'Self', '', 'Retired']), occupation=rng.choice(['Banker', 'Teacher', '']),
                         city=rng.choice(['Boston', 'New York', '']), contributor='Donor %d' % rng.randrange(80),
                         party=rng.choice(['DEM', 'REP', '']), funding_source=rng.choice(['employee_original', 'corporate_original']),
                         decision_status=rng.choice(['included', 'excluded', 'review']), receipt_role=rng.choice(['original', 'conduit']),
                         recipient_location_status='Recorded location', represented_location_evidence='Long note ' * 3,
                         contributor_key=rng.choice(['k1', 'k2', None, 'k3']), raw_zip=rng.choice(['01234-5678', '10001', '']),
                         raw_cents=raw, receipt_cents=raw if rng.random() < .8 else 0, original_cents=raw if rng.random() < .6 else 0,
                         supported_cents=max(raw, 0) if rng.random() < .5 else 0, pac_cents=rng.choice([0, 0, 1000]),
                         unresolved=int(rng.random() < .3), reasons=json.dumps(['reason %d' % rng.randrange(3)]),
                         evidence_ids=json.dumps([number]), source_url='https://docquery.fec.gov/cgi-bin/fecimg/?%d' % number,
                         source_table=rng.choice(['contributions', 'corporate_giving']))
            facts.append(value)
            db.execute('INSERT INTO facts (' + ','.join('"' + k + '"' for k in value) + ') VALUES (' + ','.join('?' for _ in value) + ')', list(value.values()))
        metadata = {'provenance': {}, 'totals': {'receipt': 0}, 'expected_receipt_cents': 0, 'ledger_run_id': None, 'identity_basis': 'Reported donor identities'}
        db.execute('INSERT INTO metadata VALUES (?,?)', ('manifest', json.dumps(metadata)))
        db.commit()
    return facts


def make_source(path, facts, seed=5):
    """Reported metadata: one row for most observations, two conflicting rows for some, none for some."""
    rng = random.Random(seed)
    with closing(sqlite3.connect(path)) as db:
        db.execute('CREATE TABLE contributions(sub_id TEXT,industry TEXT,file_num TEXT,memo_code TEXT)')
        db.execute('CREATE TABLE corporate_giving(sub_id TEXT,industry TEXT,file_num TEXT)')
        for fact in facts:
            roll = rng.random()
            if roll < .1:
                continue
            if fact['source_table'] == 'contributions':
                db.execute('INSERT INTO contributions VALUES (?,?,?,?)', (fact['source_id'], 'Example', str(1600000 + rng.randrange(9000)), rng.choice(['X', None])))
                if roll > .9:
                    db.execute('INSERT INTO contributions VALUES (?,?,?,?)', (fact['source_id'], 'Example', str(1700000 + rng.randrange(9000)), 'X'))
            else:
                db.execute('INSERT INTO corporate_giving VALUES (?,?,?)', (fact['source_id'], 'Example', str(1800000 + rng.randrange(9000))))
        db.commit()


def reference(path, source, request):
    """The records query before the sidecar (contribution_query.records at 3cec98524), verbatim."""
    basis, _, _, limit, offset = semantics.validate(request)
    column = data.BASES[basis][1]
    filters = request.get('filters', []); where, params = semantics.row_filter(filters, column)
    if not request.get('include_excluded', False) and basis != 'raw': where += ' AND ' + column + ' != 0'
    refs = request.get('dimensions', []) + [f.get('field') for f in filters if isinstance(f, dict)]
    with closing(data._ro(path)) as db, query._metadata_workspace(db, source, refs):
        relation = semantics.raw_relation(db, source, refs)
        table = semantics.table('facts', refs, request, original=True, raw_relation=relation)
        count = db.execute('SELECT COUNT(*) FROM ' + table + ' WHERE ' + where, params).fetchone()[0]
        rows = [dict(r) for r in db.execute('SELECT *,' + column + ' AS amount_cents FROM ' + table + ' WHERE ' + where + ' ORDER BY date DESC,source_id LIMIT ? OFFSET ?', params + [limit, offset])]
    for row in rows:
        row['reasons'] = json.loads(row['reasons']); row['evidence_ids'] = json.loads(row['evidence_ids']); row.pop('contributor_key', None)
    return {'rows': rows, 'total_records': count, 'has_more': offset + len(rows) < count}


def settle():
    """Join the background builds this test started, so its temporary folder can be removed. Only the
    build threads contribution_query._job starts ('mentat-' + phase): other 'mentat-' daemons, such as
    mentat-access-stats, live for the whole run and would hold every test 30 s each."""
    for thread in threading.enumerate():
        if thread is not threading.current_thread() and thread.name in ('mentat-query-view', 'mentat-records-sidecar'):
            thread.join(30)


class Fixture(unittest.TestCase):
    rows = 600

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); self.addCleanup(settle)
        folder = Path(self.tmp.name)
        self.path = folder / 'facts.sqlite'
        self.facts = make_index(self.path, self.rows)
        self.source = {'id': 'Example', 'industry': 'Example', 'database': folder / 'source.sqlite'}
        make_source(self.source['database'], self.facts)
        self.token = 'fixture-token'
        for target, name, value in ((store, 'ROOT', folder / 'query-cache'), (sidecar, 'MIN_INDEX_BYTES', 0),
                                    (sidecar, '_verified', {}), (sidecar, '_totals', {}), (query, '_jobs', {}),
                                    (query, '_raw_memo', query.OrderedDict())):
            self.enterContext(patch.object(target, name, value))
        self.enterContext(patch.object(data, '_source', side_effect=lambda directory, request, sources=None: self.source))
        self.enterContext(patch.object(data, '_ready', side_effect=lambda source, wait=False, retry=False: (self.path, None)))
        self.enterContext(patch.object(data, '_token', side_effect=lambda source: self.token))
        self.enterContext(patch.object(data, '_provenance', return_value={'workbook_sha256': 'w'}))

    def request(self, **extra):
        return {'dataset': 'Example', 'basis': 'receipt', 'filters': [], 'dimensions': [], 'limit': 25, 'offset': 0, **extra}

    def ready(self, request, seconds=30):
        """records() until it stops building (the background build finishes)."""
        deadline = time.monotonic() + seconds
        while True:
            answer = query.records(self.path.parent, json.loads(json.dumps(request)))
            if answer.get('status') != 'building':
                return answer
            self.assertLess(time.monotonic(), deadline, 'the records sidecar never finished')
            time.sleep(.02)

    def assertSameList(self, got, want, what):
        # The first difference only: unittest's difflib over thousands of rows takes minutes.
        if got == want:
            return
        at = next((i for i, (a, b) in enumerate(zip(got, want)) if a != b), min(len(got), len(want)))
        self.fail('%s differ at %d of %d/%d: %r != %r' % (what, at, len(got), len(want),
                  got[at] if at < len(got) else None, want[at] if at < len(want) else None))

    def assertSameAnswer(self, answer, request):
        expected = reference(self.path, self.source, json.loads(json.dumps(request)))
        self.assertEqual(answer['status'], 'ready')
        self.assertEqual(answer['total_records'], expected['total_records'])
        self.assertEqual(answer['has_more'], expected['has_more'])
        self.assertSameList([row['source_id'] for row in answer['rows']], [row['source_id'] for row in expected['rows']], 'record order')
        self.assertSameList(answer['rows'], expected['rows'], 'rows')
        self.assertSameList([list(row) for row in answer['rows']], [list(row) for row in expected['rows']], 'field order')


class SidecarAnswerTests(Fixture):
    def test_every_selection_pages_exactly_like_the_index(self):
        self.ready(self.request())
        pages = []
        original_page = sidecar.page
        def spy(*args, **kwargs):
            pages.append(1); return original_page(*args, **kwargs)
        cases = [
            {}, {'offset': 25}, {'offset': 590}, {'limit': 5000}, {'basis': 'raw'}, {'include_excluded': True},
            {'basis': 'original_supported', 'limit': 7, 'offset': 3}, {'basis': 'pac_activity'},
            {'filters': [{'field': 'company', 'op': 'eq', 'value': 'Beta Credit'}]},
            {'filters': [{'field': 'company', 'op': 'in', 'value': ['Alpha Bank', 'Delta Savings']}], 'offset': 11},
            {'filters': [{'field': 'zip5', 'op': 'contains', 'value': '10'}, {'field': 'state', 'op': 'ne', 'value': 'CA'}]},
            {'filters': [{'field': 'amount', 'op': 'gte', 'value': 2500}]},
            {'filters': [{'field': 'amount_cents', 'op': 'lt', 'value': 0}], 'basis': 'raw'},
            {'filters': [{'field': 'month', 'op': 'eq', 'value': '2024-02'}], 'dimensions': ['quarter', 'weekday']},
            {'filters': [{'field': 'review_required', 'op': 'eq', 'value': 'True'}]},
            {'filters': [{'field': 'amount_bin', 'op': 'eq', 'value': '0'}], 'bin_width_cents': 5000},
            {'filters': [{'field': 'date', 'op': 'gte', 'value': '2024-03-01'}, {'field': 'date', 'op': 'lte', 'value': '2024-11-15'}]},
            {'filters': [{'field': 'cycle', 'op': 'eq', 'value': '2025-2026'}, {'field': 'employer', 'op': 'contains', 'value': 'bank'}]},
            {'filters': [{'field': 'committee_id', 'op': 'eq', 'value': CONDUIT}, {'field': 'recipient', 'op': 'eq', 'value': 'ActBlue'}]},
            {'filters': [{'field': 'source_id', 'op': 'in', 'value': [f['source_id'] for f in self.facts[:40]]}]},
            {'filters': [{'field': 'company', 'op': 'eq', 'value': 'No such company'}]},
            {'dimensions': ['amount_sign', 'month']},
        ]
        with patch.object(sidecar, 'page', side_effect=spy), self.assertNoLogs('mentat.contribution_query', 'WARNING'):
            for case in cases:
                request = self.request(**case)
                with self.subTest(case=case):
                    self.assertSameAnswer(query.records(self.path.parent, request), request)
        self.assertEqual(len(pages), len(cases), 'every covered selection was paged by the sidecar, with no fallback')

    def test_a_page_the_index_disagrees_with_is_answered_from_the_index(self):
        self.ready(self.request())
        original = sidecar.page
        def reversed_page(*args, **kwargs):
            count, ids = original(*args, **kwargs)
            return count, ids[::-1]
        request = self.request(limit=10, offset=4)
        with patch.object(sidecar, 'page', side_effect=reversed_page), self.assertLogs('mentat.contribution_query', 'WARNING'):
            self.assertSameAnswer(query.records(self.path.parent, request), request)

    def test_fields_the_sidecar_lacks_are_answered_by_the_index(self):
        self.ready(self.request())
        with patch.object(sidecar, 'page', side_effect=AssertionError('not covered')):
            for case in ({'filters': [{'field': 'raw_zip', 'op': 'contains', 'value': '01234'}]},
                         {'filters': [{'field': 'recipient_location_status', 'op': 'eq', 'value': 'Recorded location'}]},
                         {'filters': [{'field': 'reasons', 'op': 'contains', 'value': 'reason 1'}]},
                         {'filters': [{'field': 'reported_file_num', 'op': 'contains', 'value': '16'}], 'dimensions': ['reported_memo_code']}):
                request = self.request(**case)
                with self.subTest(case=case):
                    self.assertSameAnswer(query.records(self.path.parent, request), request)

    def test_the_sidecar_is_proven_before_use_and_a_changed_one_is_rebuilt(self):
        self.ready(self.request())
        ident = sidecar.identity(self.path)
        target, proof, _, _ = sidecar.location(ident)
        self.assertTrue(sidecar.valid(target, proof, ident))
        stored = json.loads(proof.read_text())
        self.assertEqual(stored['rows'], len(self.facts))
        self.assertEqual(stored['totals']['all'], len(self.facts))
        self.assertEqual(stored['totals']['receipt_cents'], sum(1 for f in self.facts if f['receipt_cents'] != 0))
        with closing(sqlite3.connect(target)) as db:
            db.execute("UPDATE ordered SET receipt_cents=receipt_cents+1 WHERE record_rank=1"); db.commit()
        sidecar._verified.clear()
        self.assertFalse(sidecar.valid(target, proof, ident))
        request = self.request()
        first = query.records(self.path.parent, request)
        self.assertEqual(first['status'], 'building', 'a sidecar that fails its hash is rebuilt, never read')
        self.assertSameAnswer(self.ready(request), request)
        self.assertTrue(sidecar.valid(target, proof, ident))

    def test_a_changed_index_gets_a_new_sidecar_and_never_the_old_order(self):
        request = self.request(basis='raw')
        self.assertSameAnswer(self.ready(request), request)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE facts SET date='2030-01-01' WHERE source_id=?", (self.facts[-1]['source_id'],)); db.commit()
        answer = self.ready(request)
        self.assertEqual(answer['rows'][0]['source_id'], self.facts[-1]['source_id'])
        self.assertSameAnswer(answer, request)

    def test_a_build_whose_index_changes_publishes_nothing(self):
        ident = sidecar.identity(self.path)
        with patch.object(store, 'source_stable', side_effect=[True, False]):
            with self.assertRaisesRegex(ValueError, 'changed'):
                sidecar.build(self.path, ident)
        self.assertFalse(list(sidecar.root().glob('*.sqlite')))
        self.assertFalse(list(sidecar.root().glob('*.pending')))

    def test_the_analytical_index_file_is_never_modified(self):
        before = data._hash(self.path), self.path.stat().st_mtime_ns
        self.ready(self.request(filters=[{'field': 'company', 'op': 'eq', 'value': 'Beta Credit'}]))
        self.assertEqual((data._hash(self.path), self.path.stat().st_mtime_ns), before)


class BuildingContractTests(Fixture):
    KEYS = ['schema_version', 'status', 'phase', 'message', 'processed_records', 'total_records', 'elapsed_ms',
            'retry_after_ms', 'dataset', 'provenance']

    def test_a_missing_sidecar_answers_building_at_once_with_progress_then_ready(self):
        gate = threading.Event(); original = sidecar.build
        def slow(path, ident, report=lambda *a: None, should_stop=None):
            report(0, 600, 'Copying the source records for inspection.'); report(300, 600)
            gate.wait(10); return original(path, ident, report, should_stop)
        request = self.request()
        with patch.object(sidecar, 'build', side_effect=slow):
            started = time.monotonic()
            answer = query.records(self.path.parent, request)
            self.assertLess(time.monotonic() - started, 2, 'the request did not wait for the build')
            self.assertEqual(list(answer), self.KEYS)
            self.assertEqual(answer['status'], 'building'); self.assertEqual(answer['phase'], 'records_sidecar')
            self.assertEqual(answer['schema_version'], data.VERSION); self.assertEqual(answer['dataset'], 'Example')
            self.assertEqual(answer['provenance'], {'workbook_sha256': 'w'}); self.assertEqual(answer['retry_after_ms'], 300)
            self.assertIsInstance(answer['elapsed_ms'], int); self.assertIsInstance(answer['processed_records'], int)
            self.assertTrue(answer['message'])
            for _ in range(100):
                answer = query.records(self.path.parent, request)
                if answer['processed_records'] == 300: break
                time.sleep(.01)
            self.assertEqual((answer['processed_records'], answer['total_records']), (300, 600))
            gate.set()
            self.assertSameAnswer(self.ready(request), request)

    def test_one_build_across_threads(self):
        calls = []; original = sidecar.build
        def counted(*args, **kwargs):
            calls.append(1); time.sleep(.2); return original(*args, **kwargs)
        with patch.object(sidecar, 'build', side_effect=counted):
            answers = []
            threads = [threading.Thread(target=lambda: answers.append(query.records(self.path.parent, self.request()))) for _ in range(8)]
            for thread in threads: thread.start()
            for thread in threads: thread.join()
            self.assertEqual({answer['status'] for answer in answers}, {'building'})
            self.ready(self.request())
        self.assertEqual(len(calls), 1)

    def test_a_failed_build_is_reported_on_the_next_poll_and_retry_restarts_it(self):
        original = sidecar.build; attempts = []
        def flaky(*args, **kwargs):
            attempts.append(1)
            if len(attempts) == 1: raise ValueError('The records sidecar could not be copied.')
            return original(*args, **kwargs)
        request = self.request()
        with patch.object(sidecar, 'build', side_effect=flaky):
            self.assertEqual(query.records(self.path.parent, request)['status'], 'building')
            for _ in range(200):
                if query._jobs and next(iter(query._jobs.values()))['status'] == 'failed': break
                time.sleep(.01)
            with self.assertRaisesRegex(ValueError, 'could not be copied'):
                query.records(self.path.parent, request)
            with self.assertRaisesRegex(ValueError, 'could not be copied'):
                query.records(self.path.parent, request)
            self.assertEqual(len(attempts), 1, 'a failed build is not restarted without a retry')
            self.assertEqual(query.records(self.path.parent, {**request, 'retry': True})['status'], 'building')
            self.assertSameAnswer(self.ready(request), request)
        self.assertEqual(len(attempts), 2)

    def test_a_sidecar_that_never_verifies_is_an_error_not_an_endless_build(self):
        with patch.object(query, '_ready_sidecar', return_value=None):
            self.assertEqual(query.records(self.path.parent, self.request())['status'], 'building')
            for _ in range(500):
                if query._jobs and next(iter(query._jobs.values()))['status'] == 'failed': break
                time.sleep(.01)
            with self.assertRaisesRegex(ValueError, 'could not be verified'):
                query.records(self.path.parent, self.request())

    def test_a_small_index_never_builds_and_answers_from_the_index(self):
        with patch.object(sidecar, 'MIN_INDEX_BYTES', 1 << 40), patch.object(sidecar, 'build', side_effect=AssertionError('built')):
            request = self.request(offset=5)
            self.assertSameAnswer(query.records(self.path.parent, request), request)
        self.assertFalse(sidecar.root().exists() and list(sidecar.root().glob('*.sqlite')))

    def test_prebuild_for_the_warmer(self):
        with patch.object(sidecar, 'MIN_INDEX_BYTES', 1 << 40):
            self.assertEqual(query.prebuild_records(self.path.parent, 'Example')['status'], 'index_missing')
        with patch.object(data, 'CACHE_ROOT', self.path.parent), patch.object(data, '_token', return_value='facts'):
            with patch.object(sidecar, 'MIN_INDEX_BYTES', 1 << 40):
                self.assertEqual(query.prebuild_records(self.path.parent, 'Example')['status'], 'skipped')
            built = query.prebuild_records(self.path.parent, 'Example')
            self.assertEqual((built['status'], built['built']), ('ready', True))
            again = query.prebuild_records(self.path.parent, 'Example')
            self.assertEqual((again['status'], again['built']), ('ready', False))
        with patch.object(sidecar, 'build', side_effect=AssertionError('rebuilt')):
            request = self.request(offset=3)
            self.assertSameAnswer(query.records(self.path.parent, request), request)


class ReportedNumberMemoTests(Fixture):
    def conduit_request(self, ids, **extra):
        return self.request(dimensions=['reported_file_num'], limit=len(ids),
                            filters=[{'field': 'source_id', 'op': 'in', 'value': ids}], **extra)

    def test_a_revisited_page_reads_no_reported_metadata(self):
        self.ready(self.request())
        ids = [f['source_id'] for f in self.facts if f['committee_id'] == CONDUIT][:30]
        request = self.conduit_request(ids)
        reads = []; original = semantics.raw_relation
        def counted(db, source, refs):
            relation = original(db, source, refs)
            if relation: reads.append(sorted(set(refs) & set(semantics.RAW)))
            return relation
        with patch.object(semantics, 'raw_relation', side_effect=counted):
            first = query.records(self.path.parent, request)
            self.assertEqual(len(reads), 1)
            second = query.records(self.path.parent, request)
            self.assertEqual(len(reads), 1, 'the revisited page was answered from the memo')
            self.assertEqual(first, second)
            wider = self.conduit_request(ids + [f['source_id'] for f in self.facts[:10]])
            query.records(self.path.parent, wider)
            self.assertEqual(len(reads), 2)
            query.records(self.path.parent, wider)
            self.assertEqual(len(reads), 2)
            self.token = 'another-token'
            query.records(self.path.parent, request)
            self.assertEqual(len(reads), 3, 'a new index token reads its numbers again')
        self.assertSameAnswer(first, request)

    def test_memo_answers_equal_the_full_join_in_values_and_field_order(self):
        self.ready(self.request())
        for case in ({'dimensions': ['reported_file_num']}, {'dimensions': ['month', 'reported_memo_code', 'reported_file_num'], 'offset': 40},
                     {'dimensions': ['reported_file_num'], 'filters': [{'field': 'raw_zip', 'op': 'eq', 'value': '10001'}]},
                     {'dimensions': ['reported_file_num'], 'basis': 'raw', 'limit': 5000}):
            request = self.request(**case)
            with self.subTest(case=case):
                self.assertSameAnswer(query.records(self.path.parent, request), request)
                self.assertSameAnswer(query.records(self.path.parent, request), request)

    def test_a_metadata_row_that_would_repeat_an_observation_keeps_the_full_path(self):
        with closing(sqlite3.connect(self.source['database'])) as db:
            db.execute('DROP TABLE corporate_giving')
            db.execute('CREATE TABLE corporate_giving(sub_id PRIMARY KEY,industry TEXT,file_num TEXT)')
            target = next(f for f in self.facts if f['source_table'] == 'corporate_giving')
            db.execute('INSERT INTO corporate_giving VALUES (?,?,?)', (int(target['source_id']), 'Example', '1'))
            db.execute('INSERT INTO corporate_giving VALUES (?,?,?)', (target['source_id'], 'Example', '2'))
            db.commit()
        request = self.request(basis='raw', dimensions=['reported_file_num'], filters=[{'field': 'source_id', 'op': 'eq', 'value': target['source_id']}])
        self.ready(self.request())
        answer = query.records(self.path.parent, request)
        self.assertSameAnswer(answer, request)
        self.assertEqual(len(answer['rows']), 2, 'the full path repeats the observation; the memo path must not hide that')


class ReadyStatusTests(Fixture):
    def test_every_ready_records_answer_says_ready(self):
        self.ready(self.request())
        for case in ({}, {'filters': [{'field': 'raw_zip', 'op': 'eq', 'value': '10001'}]}, {'dimensions': ['reported_file_num']}):
            with self.subTest(case=case):
                self.assertEqual(query.records(self.path.parent, self.request(**case))['status'], 'ready')
        with patch.object(sidecar, 'MIN_INDEX_BYTES', 1 << 40):
            self.assertEqual(query.records(self.path.parent, self.request())['status'], 'ready')


class PrebuildVerifyTests(Fixture):
    """prebuild_records verifies and prunes what it built, as _sidecar_job does (review finding 3, 2026-09-25)."""

    def prebuild(self, **kwargs):
        with patch.object(data, 'CACHE_ROOT', self.path.parent), patch.object(data, '_token', return_value='facts'):
            return query.prebuild_records(self.path.parent, 'Example', **kwargs)

    def assertReleased(self):
        _, _, lock, progress = sidecar.location(sidecar.identity(self.path))
        self.assertIsNone(proclock.inspect(lock))
        self.assertFalse(progress.exists())

    def test_a_built_sidecar_that_does_not_verify_is_an_error(self):
        with patch.object(sidecar, 'valid', return_value=False):
            with self.assertRaisesRegex(ValueError, 'could not be verified'):
                self.prebuild()
        self.assertReleased()

    def test_a_stopped_build_is_held_elsewhere(self):
        self.assertEqual(self.prebuild(should_stop=lambda: True), {'status': 'held_elsewhere', 'dataset': 'Example'})
        self.assertFalse(list(sidecar.root().glob('*.sqlite')))
        self.assertReleased()
        with patch.object(sidecar, 'build', return_value=None):   # returned without publishing a sidecar
            self.assertEqual(self.prebuild(should_stop=lambda: True), {'status': 'held_elsewhere', 'dataset': 'Example'})

    def test_a_successful_build_prunes_the_superseded_sidecar(self):
        first = self.prebuild()
        self.assertEqual((first['status'], first['built']), ('ready', True))
        old = Path(first['path'])
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE facts SET date='2030-01-01' WHERE source_id=?", (self.facts[-1]['source_id'],)); db.commit()
        second = self.prebuild()
        self.assertEqual((second['status'], second['built']), ('ready', True))
        self.assertNotEqual(Path(second['path']), old)
        self.assertFalse(old.exists() or old.with_suffix('.json').exists(), 'the superseded sidecar was pruned')
        self.assertTrue(Path(second['path']).exists())
        self.assertReleased()


class LoadedCodeTests(Fixture):
    def test_a_changed_sidecar_module_asks_for_a_restart(self):
        """Review finding 9 (2026-09-25): records_sidecar.py is checked like contribution_query.py."""
        sidecar.check()
        with patch.object(sidecar, 'LOADED_STAMP', ('changed', 0, 0, 0)):
            with self.assertRaisesRegex(ValueError, 'Restart the app'):
                sidecar.check()
            for route in (query.records, query.query):
                with self.subTest(route=route.__name__), self.assertRaisesRegex(ValueError, 'Restart the app'):
                    route(self.path.parent, self.request())


if __name__ == '__main__':
    unittest.main()

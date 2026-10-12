"""No /api/contributions/query request holds for a query-view build (batch 2 P3/P5, 2026-09-25).

The agreed contract (Chart Studio polls on it): while the view builds, the route answers HTTP 200
{"schema_version", "status": "building", "phase": "query_view", "message", "processed_records",
"total_records", "elapsed_ms", "retry_after_ms", "dataset", "provenance"}; one build runs across threads
and processes; a failed build is the error of the next poll and "retry": true restarts it; every ready
answer carries "status": "ready".
"""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import contribution_data as data
import contribution_query as query
import contribution_query_store as store
import proclock
import test_contribution_data as fixtures

def settle():
    """Join the background builds this test started, so its temporary folder can be removed. Only the
    build threads contribution_query._job starts ('mentat-' + phase): other 'mentat-' daemons, such as
    mentat-access-stats, live for the whole run and would hold every test 30 s each."""
    for thread in threading.enumerate():
        if thread is not threading.current_thread() and thread.name in ('mentat-query-view', 'mentat-records-sidecar'):
            thread.join(30)


KEYS = ['schema_version', 'status', 'phase', 'message', 'processed_records', 'total_records', 'elapsed_ms',
        'retry_after_ms', 'dataset', 'provenance']


class QueryBuildingTests(unittest.TestCase):
    def setUp(self):
        fixtures.ContributionQueryTests.setUp(self)
        self.addCleanup(settle)
        for target, name, value in ((store, 'ROOT', Path(self.tmp.name) / 'query-cache'), (store, '_memory', store.OrderedDict()),
                                    (store, '_memory_bytes', 0), (store, '_verified', {}), (query, '_jobs', {})):
            self.enterContext(patch.object(target, name, value))
        self.enterContext(patch.object(data, '_source', side_effect=lambda directory, request, sources=None: self.source))
        self.enterContext(patch.object(data, '_ready', side_effect=lambda source, wait=False, retry=False: (self.path, None)))
        self.enterContext(patch.object(data, '_token', return_value='fixture'))
        self.enterContext(patch.object(data, '_provenance', return_value={'workbook_sha256': 'w', 'database_sha256': 'd'}))

    def request(self, **extra):
        return {'dataset': 'Example', 'basis': 'receipt', 'dimensions': ['zip5'], 'measures': ['amount', 'records'], **extra}

    def ask(self, **extra):
        return query.query(self.path.parent, self.request(**extra))

    def held(self, request):
        """The answer the old route gave by holding: _query_ready builds the view in the request."""
        from contribution_semantics import validate
        return query._query_ready(self.path, request, self.source, *validate(request))

    def wait_ready(self, seconds=20, **extra):
        deadline = time.monotonic() + seconds
        while True:
            answer = self.ask(**extra)
            if answer.get('status') != 'building':
                return answer
            self.assertLess(time.monotonic(), deadline)
            time.sleep(.02)

    def wait_job(self, status):
        for _ in range(500):
            if query._jobs and next(iter(query._jobs.values()))['status'] == status:
                return
            time.sleep(.01)
        self.fail('the background job never became ' + status)

    def test_a_missing_view_answers_building_at_once_from_every_thread_and_builds_once(self):
        gate = threading.Event(); calls = []; original = store.view
        def slow(path):
            # The route itself calls store.view only once the view verifies (an instant read);
            # the build is the call made on the background thread.
            if threading.current_thread().name.startswith('mentat-query-view'):
                calls.append(1); gate.wait(10)
            return original(path)
        with patch.object(store, 'view', side_effect=slow):
            answers = []; started = time.monotonic()
            threads = [threading.Thread(target=lambda: answers.append(self.ask())) for _ in range(6)]
            for thread in threads: thread.start()
            for thread in threads: thread.join(5)
            self.assertLess(time.monotonic() - started, 3, 'no request waited for the build')
            self.assertEqual(len(answers), 6)
            for answer in answers:
                self.assertEqual(list(answer), KEYS)
                self.assertEqual((answer['status'], answer['phase'], answer['schema_version']), ('building', 'query_view', data.VERSION))
                self.assertEqual((answer['processed_records'], answer['total_records']), (0, None))
                self.assertEqual(answer['retry_after_ms'], 300)
                self.assertIsInstance(answer['elapsed_ms'], int)
                self.assertEqual(answer['dataset'], 'Example')
                self.assertEqual(answer['provenance'], {'workbook_sha256': 'w', 'database_sha256': 'd'})
                self.assertTrue(answer['message'].endswith('.'))
            for _ in range(200):
                if calls: break
                time.sleep(.01)
            self.assertEqual(len(calls), 1, 'one build for six requests')
            gate.set()
            ready = self.wait_ready()
        self.assertEqual(len(calls), 1)
        self.assertEqual(ready['status'], 'ready')
        self.assertEqual(ready['rows'], self.held(self.request())['rows'])

    def test_a_view_that_never_verifies_is_an_error_not_an_endless_build(self):
        with patch.object(query, '_view_ready', return_value=False):
            self.assertEqual(self.ask()['status'], 'building')
            self.wait_job('failed')
            with self.assertRaisesRegex(ValueError, 'could not be verified'):
                self.ask()

    def test_retry_after_follows_the_agreed_schedule(self):
        for elapsed, expected in ((0, 300), (1999, 300), (2000, 750), (9999, 750), (10000, 1500), (600000, 1500)):
            self.assertEqual(query._retry_after(elapsed), expected, elapsed)
        job = {'phase': 'query_view', 'started': time.time() - 5, 'processed': 0, 'total': None, 'message': 'x.'}
        answer = query._building(job, self.source)
        self.assertGreaterEqual(answer['elapsed_ms'], 5000)
        self.assertEqual(answer['retry_after_ms'], 750)
        job['started'] = time.time() - 30
        self.assertEqual(query._building(job, self.source)['retry_after_ms'], 1500)

    def test_a_failed_build_is_the_next_polls_error_and_retry_restarts_it(self):
        original = store.view; attempts = []
        def flaky(path):
            if threading.current_thread().name.startswith('mentat-query-view'):
                attempts.append(1)
                if len(attempts) == 1: raise ValueError('The query projection failed integrity verification.')
            return original(path)
        with patch.object(store, 'view', side_effect=flaky):
            self.assertEqual(self.ask()['status'], 'building')
            self.wait_job('failed')
            for _ in range(2):
                with self.assertRaisesRegex(ValueError, 'failed integrity verification'):
                    self.ask()
            self.assertEqual(len(attempts), 1)
            again = self.ask(retry=True)
            self.assertEqual((again['status'], again['phase']), ('building', 'query_view'))
            self.assertEqual(self.wait_ready()['status'], 'ready')
        self.assertEqual(len(attempts), 2)

    def test_an_unexpected_build_error_is_reported_in_a_plain_sentence(self):
        with patch.object(store, 'view', side_effect=MemoryError()):
            self.assertEqual(self.ask()['status'], 'building')
            self.wait_job('failed')
            with self.assertRaisesRegex(ValueError, 'The query view could not be prepared: MemoryError'):
                self.ask()

    def test_a_build_held_by_another_process_is_reported_and_not_repeated(self):
        key = 'view-' + store._key(query._view_identity(self.path))
        marker = query._building_root() / (key + '.lock')
        ready = Path(self.tmp.name) / 'claimed'
        script = ('import sys,time\nfrom pathlib import Path\nsys.path.insert(0,%r)\nimport proclock\n'
                  'assert proclock.claim(Path(sys.argv[1]),sys.argv[2],extra={"phase":"query_view"})\n'
                  'Path(sys.argv[3]).write_text("x")\ntime.sleep(60)\n') % str(Path(proclock.__file__).parent)
        child = subprocess.Popen([sys.executable, '-c', script, str(marker), key, str(ready)])
        self.addCleanup(lambda: child.poll() is None and child.kill())
        for _ in range(500):
            if ready.exists(): break
            time.sleep(.02)
        self.assertTrue(ready.exists(), 'the other process never claimed the build')
        with patch.object(store, 'view', side_effect=AssertionError('built while another process builds')):
            answer = self.ask()
            self.assertEqual((answer['status'], answer['phase']), ('building', 'query_view'))
            self.assertEqual(self.ask()['status'], 'building')
        self.assertFalse(query._jobs, 'no local build was started')
        child.kill(); child.wait(10)
        self.assertEqual(self.wait_ready()['status'], 'ready')

    def test_a_cached_answer_is_served_without_its_view(self):
        first = self.wait_ready()
        for view in (store.ROOT / 'views').glob('*'):
            view.unlink()
        store._verified.clear()
        with patch.object(store, 'view', side_effect=AssertionError('rebuilt for a cached answer')):
            self.assertEqual(self.ask(), first)

    def test_the_index_build_keeps_its_preparing_answer(self):
        preparing = {'schema_version': data.VERSION, 'status': 'preparing', 'message': 'Preparing contribution data.',
                     'processed_records': 10, 'retry_after_ms': 1500, 'dataset': 'Example', 'provenance': {}}
        with patch.object(data, '_ready', return_value=(None, preparing)):
            self.assertIs(self.ask(), preparing)

    def test_every_ready_query_answer_says_ready(self):
        fresh = self.wait_ready()
        self.assertEqual(fresh['status'], 'ready')
        self.assertEqual(self.ask()['status'], 'ready')
        store._memory.clear()
        self.assertEqual(self.ask()['status'], 'ready')
        with patch.object(store, 'cached', return_value={'rows': [], 'schema_version': data.VERSION}):
            self.assertEqual(self.ask()['status'], 'ready')

    def test_in_process_callers_still_get_the_held_answer(self):
        request = self.request()
        answer = self.held(request)
        self.assertEqual(answer['status'], 'ready')
        self.assertTrue(query._view_ready(self.path))

    def age_failed_job(self):
        """Move each failed job's failure FAILED_RESTART_SECONDS into the past."""
        with query._jobs_lock:
            for job in query._jobs.values():
                job['failed_at'] -= query.FAILED_RESTART_SECONDS + 1

    def test_the_readiness_check_reads_the_view_store_view_builds(self):
        """Drift alarm (review finding 2, 2026-09-25): until store.view_ready lands, _view_ready rebuilds
        store.view's identity and location by hand. If they ever differ, every query answers 'building'."""
        built = store.view(self.path)
        self.assertTrue(query._view_ready(self.path))
        checked = []; original = store._valid
        def spy(path, proof_path, identity):
            checked.append((Path(path), Path(proof_path), identity)); return original(path, proof_path, identity)
        with patch.object(store, 'view_ready', None, create=True), patch.object(store, '_valid', side_effect=spy):
            self.assertTrue(query._view_ready(self.path))
        proof = built.with_suffix('.json')
        self.assertEqual(checked, [(built, proof, json.loads(proof.read_text())['identity'])])

    def test_a_built_view_that_does_not_verify_stays_failed_until_an_explicit_retry(self):
        """Review finding 2 (2026-09-25): a restart would build and fail the same way every 30 s."""
        builds = []; original = store.view
        def counted(path):
            if threading.current_thread().name.startswith('mentat-query-view'): builds.append(1)
            return original(path)
        with patch.object(query, '_view_ready', return_value=False), patch.object(store, 'view', side_effect=counted):
            self.assertEqual(self.ask()['status'], 'building')
            self.wait_job('failed')
            self.age_failed_job()
            for _ in range(2):
                with self.assertRaisesRegex(ValueError, 'did not match the expected identity'):
                    self.ask()
            self.assertEqual(len(builds), 1, 'no automatic restart after FAILED_RESTART_SECONDS')
            self.assertEqual(self.ask(retry=True)['status'], 'building')
            self.wait_job('failed')
        self.assertEqual(len(builds), 2)

    def test_an_ordinary_failed_build_still_restarts_after_the_failed_window(self):
        original = store.view; attempts = []
        def flaky(path):
            if threading.current_thread().name.startswith('mentat-query-view'):
                attempts.append(1)
                if len(attempts) == 1: raise ValueError('The query projection failed integrity verification.')
            return original(path)
        with patch.object(store, 'view', side_effect=flaky):
            self.assertEqual(self.ask()['status'], 'building')
            self.wait_job('failed')
            with self.assertRaisesRegex(ValueError, 'failed integrity verification'):
                self.ask()
            self.age_failed_job()
            self.assertEqual(self.ask()['status'], 'building')
            self.assertEqual(self.wait_ready()['status'], 'ready')
        self.assertEqual(len(attempts), 2)

    def test_a_retry_request_shares_the_cached_answer_of_the_same_request(self):
        """Review finding 8 (2026-09-25): 'retry' stays out of the results-cache identity but still
        reaches data._ready."""
        first = self.wait_ready()
        seen = []; original = store.cached
        def spy(namespace, identity, compute, stable=lambda: True):
            if namespace == 'results': seen.append(json.loads(json.dumps(identity)))
            return original(namespace, identity, compute, stable)
        with patch.object(store, 'cached', side_effect=spy), \
                patch.object(query, '_query_uncached', side_effect=AssertionError('recomputed for a retry')):
            self.assertEqual(self.ask(), first)
            self.assertEqual(self.ask(retry=True), first)
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[0], seen[1])
        self.assertNotIn('retry', seen[1]['request'])
        self.assertIs(data._ready.call_args.kwargs['retry'], True)


if __name__ == '__main__':
    unittest.main()

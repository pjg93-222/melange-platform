"""Insights request protocol: stored results without the pool, adaptive polling,
cross-process single-flight build markers and prewarm hints (perf H5, S2, S3).

Everything runs on the test_analysis_data fixture in a temporary folder; the
two-process tests start child interpreters of this file (--child) against one
shared scratch state root.
"""
from concurrent.futures import Future
from datetime import datetime, timedelta, timezone
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
# Negative controls only: a child can be told to run another analysis_data build.
CHILD_MODULE = None
if __name__ == '__main__' and sys.argv[1:2] == ['--child']:
    _config = json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))
    if _config.get('module'):
        sys.dont_write_bytecode = True
        _loader = importlib.machinery.SourceFileLoader('analysis_data', _config['module'])
        _module = importlib.util.module_from_spec(importlib.util.spec_from_loader('analysis_data', _loader))
        sys.modules['analysis_data'] = _module
        _loader.exec_module(_module)
import analysis_data as a  # noqa: E402
import python_child  # noqa: E402
import test_analysis_data as fixture  # noqa: E402

REQUEST = {'dataset': 'Example', 'as_of': '2026-09-20'}


def utc(seconds_ago=0):
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()


class _Protocol(unittest.TestCase):
    ready = fixture.AnalysisAdapterTests.ready

    def setUp(self):
        fixture.AnalysisAdapterTests.setUp(self)
        self.addCleanup(a._jobs.clear)
        self.addCleanup(self.drain)  # cleanups run last-in first-out: drain, then clear

    def drain(self):
        """No pool job or hint write may still touch the folder when it is removed."""
        deadline = time.time() + 30
        for future in list(a._jobs.values()):
            if getattr(future, 'mentat_timing', None) is not None and getattr(future, 'fake', False) is False:
                try:
                    future.result(timeout=max(.1, deadline - time.time()))
                except Exception:  # noqa: BLE001 - only waiting
                    pass
        a._hint_writer.submit(lambda: None).result(timeout=30)

    def request(self, **extra):
        return {**REQUEST, **extra}

    def key_for(self, **extra):
        """The cache key query() produces for a request, taken from its job submission."""
        submitted = []

        def fake(function, *args):
            submitted.append(args)
            future = Future()
            future.fake = True
            return future

        with patch.object(a._pool, 'submit', fake):
            response = a.query(self.tmp.name, self.request(**extra))
        a._jobs.clear()
        self.assertEqual(response['status'], 'preparing')
        return submitted[0][0]

    def marker(self, key, pid, started=None, body=None, age=0):
        path = a.building_dir() / (key + '.lock')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body if body is not None else
                         json.dumps({'pid': pid, 'started_utc': started or utc(), 'key': key}).encode('utf-8'))
        if age:
            moment = time.time() - age
            os.utime(path, (moment, moment))
        return path

    def process(self, seconds):
        child = subprocess.Popen([sys.executable, '-c', f'import time; time.sleep({seconds})'])
        self.addCleanup(lambda: (child.kill(), child.wait()))
        return child


class StoredResultTests(_Protocol):
    def test_a_stored_result_is_answered_without_the_pool_after_a_restart(self):
        first = self.ready()
        a._jobs.clear()  # a restart forgets every job and the store's memory
        with a.store._mutex:
            a.store._memory.clear()
            a.store._memory_bytes = 0
        with patch.object(a._pool, 'submit', side_effect=AssertionError('pool used for a stored result')):
            second = a.query(self.tmp.name, self.request())
            third = a.query(self.tmp.name, self.request())  # now from store memory
        self.assertEqual(second['status'], 'ready')
        self.assertEqual(second, first)
        self.assertEqual(third, first)

    def test_panel_narrowing_still_applies_to_a_stored_result(self):
        self.ready()
        a._jobs.clear()
        with patch.object(a._pool, 'submit', side_effect=AssertionError('pool used for a stored result')):
            narrowed = a.query(self.tmp.name, self.request(panels=['trends']))
        self.assertEqual(set(narrowed['panels']), {'trends'})
        self.assertEqual(narrowed['scope']['panels'], ['trends'])

    def test_polling_is_adaptive_while_a_build_runs(self):
        release = threading.Event()
        real = a._prepare

        def held(*args, **kwargs):
            release.wait(30)
            yield from real(*args, **kwargs)

        with patch.object(a, '_prepare', held):
            first = a.query(self.tmp.name, self.request())
            self.assertEqual((first['status'], first['retry_after_ms']), ('preparing', 300))
            timing = next(iter(a._jobs.values())).mentat_timing
            deadline = time.time() + 10
            while timing['started'] is None and time.time() < deadline:
                time.sleep(.01)
            for moved, expected in ((3, 750), (8, 1500)):
                timing['started'] -= moved
                self.assertEqual(a.query(self.tmp.name, self.request())['retry_after_ms'], expected)
            release.set()
            self.assertEqual(self.ready()['status'], 'ready')

    def test_retry_after_counts_a_queued_job_from_submission(self):
        now = time.monotonic()
        for timing, expected in (({'submitted': now, 'started': None}, 300), ({'submitted': now - 5, 'started': None}, 750),
                                 ({'submitted': now - 60, 'started': now - 1}, 300), ({'submitted': now - 60, 'started': now - 30}, 1500)):
            with self.subTest(timing=timing):
                self.assertEqual(a._retry_after(SimpleNamespace(mentat_timing=timing)), expected)

    def test_other_requests_finishing_still_answers_2000(self):
        for number in range(6):
            future = Future()
            future.fake = True
            a._jobs['busy-' + str(number)] = future
        with patch.object(a._pool, 'submit', side_effect=AssertionError('over capacity')):
            response = a.query(self.tmp.name, self.request())
        self.assertEqual((response['status'], response['retry_after_ms']), ('preparing', 2000))
        self.assertIn('Other Insights requests are finishing', response['message'])


class BuildMarkerTests(_Protocol):
    def test_a_build_holds_its_marker_and_removes_it_on_success_and_on_error(self):
        seen = []
        real = a._prepare

        def watched(*args, **kwargs):
            seen.extend(a.building_dir().glob('*.lock'))
            yield from real(*args, **kwargs)

        with patch.object(a, '_prepare', watched):
            self.ready()
        self.assertEqual(len(seen), 1, 'exactly one marker while the build runs')
        self.assertFalse(seen[0].exists(), 'the marker must be gone once the result is answered')
        self.assertEqual(list(a.building_dir().glob('*')), [])

        captured = []

        def failing(*args, **kwargs):
            path = next(a.building_dir().glob('*.lock'))
            captured.append((path.stem, json.loads(path.read_bytes())))
            raise ValueError('fixture build failure')
            yield  # noqa: unreachable - keeps this a generator like _prepare

        with patch.object(a, '_prepare', failing):
            with self.assertRaisesRegex(ValueError, 'fixture build failure'):
                self.ready(top_n=7)
        stem, info = captured[0]
        self.assertEqual(set(info), {'pid', 'pid_started', 'started_utc', 'key'})
        self.assertEqual((info['pid'], info['pid_started'], info['key']), (os.getpid(), a.proclock.own_started(), stem))
        self.assertLess(abs(datetime.fromisoformat(info['started_utc']).timestamp() - time.time()), 60)
        self.assertEqual(list(a.building_dir().glob('*')), [])

    def test_a_live_marker_of_another_process_defers_without_building(self):
        key = self.key_for()
        first = self.ready()
        response_file = a.store.ROOT / 'responses' / 'insights' / (a.store._key([a.store.LOADED_SHA, 'insights', {'request_key': key}]) + '.json')
        saved = response_file.read_bytes()
        response_file.unlink()  # as if this process never had the result
        a._jobs.clear()
        with a.store._mutex:
            a.store._memory.clear()
            a.store._memory_bytes = 0
        other = self.process(120)
        marker = self.marker(key, other.pid)
        with patch.object(a._pool, 'submit', side_effect=AssertionError('built although another process holds the key')):
            for _ in range(3):
                response = a.query(self.tmp.name, self.request())
                self.assertEqual((response['status'], response['retry_after_ms'], response['message']),
                                 ('preparing', 500, a.ELSEWHERE_MESSAGE))
                self.assertEqual(dict(a._jobs), {})
            response_file.write_bytes(saved)  # the other process stores its result
            self.assertEqual(a.query(self.tmp.name, self.request()), first)
        self.assertTrue(marker.exists(), 'another process owns its marker')

    def test_stale_markers_are_replaced_and_live_ones_defer(self):
        other = self.process(120)
        gone = subprocess.Popen([sys.executable, '-c', 'pass'])
        gone.wait()
        time.sleep(3)  # the live process is now older than any marker it could have written
        started = a.proclock.process_started(other.pid)
        recycled = started - (1 if os.name == 'nt' else 3 * a.proclock.POSIX_START_TOLERANCE_TICKS)

        def body(pid_started, written):
            return json.dumps({'pid': other.pid, 'pid_started': pid_started, 'started_utc': written, 'key': 'k'}).encode()
        cases = [
            ('process gone', dict(pid=gone.pid), True),
            ('this process, no job of its own', dict(pid=os.getpid()), True),
            ('written before its process was created (a recycled pid)', dict(pid=other.pid, started=utc(31 * 60)), True),
            ('pid recycled after the marker', dict(pid=other.pid, started=utc(90)), True),
            ('pid recycled: another creation time', dict(pid=None, body=body(recycled, utc())), True),
            ('abandoned unreadable marker', dict(pid=None, body=b'', age=120), True),
            ('marker being written', dict(pid=None, body=b''), False),
            ('live process', dict(pid=other.pid), False),
            # Review H2: a live builder's marker defers others at any age (it used to expire after 30 minutes).
            ('live process, marker 31 minutes old', dict(pid=None, body=body(started, utc(31 * 60)), age=31 * 60), False),
        ]
        for top_n, (label, marker, stale) in enumerate(cases, 11):
            with self.subTest(case=label):
                key = self.key_for(top_n=top_n)
                path = self.marker(key, **marker)
                if stale:
                    self.assertEqual(self.ready(top_n=top_n)['status'], 'ready')
                    self.assertFalse(path.exists())
                else:
                    with patch.object(a._pool, 'submit', side_effect=AssertionError('built past a live marker')):
                        response = a.query(self.tmp.name, self.request(top_n=top_n))
                    self.assertEqual((response['status'], response['retry_after_ms']), ('preparing', 500))
                    self.assertTrue(path.exists())
                    path.unlink()

    def test_a_job_that_loses_the_marker_race_answers_elsewhere_then_recovers(self):
        # Another process claims the key after this request's check but before its
        # job does: the job must not build, and the next poll must start over.
        key = self.key_for()

        def synchronous(function, *args):
            future = Future()
            future.set_result(function(*args))
            return future

        with patch.object(a, '_claim', return_value=None), patch.object(a._pool, 'submit', synchronous):
            response = a.query(self.tmp.name, self.request())
        self.assertEqual((response['status'], response['retry_after_ms'], response['message']),
                         ('preparing', 500, a.ELSEWHERE_MESSAGE))
        self.assertIs(a._jobs[key].result(), a._ELSEWHERE)
        self.assertEqual(self.ready()['status'], 'ready')
        self.assertEqual(list(a.building_dir().glob('*')), [])


class TwoProcessTests(_Protocol):
    """One shared scratch state root, two console-like processes, one build."""

    def spawn(self, name, delay):
        shared = Path(self.tmp.name) / 'shared'
        config = {'directory': self.tmp.name, 'index': str(self.path), 'store': str(shared / 'query-cache'),
                  'history': str(shared / 'history'), 'delay': delay, 'timeout': 60, 'module': CHILD_MODULE,
                  'log': str(shared / (name + '.jsonl')), 'result': str(shared / (name + '.json'))}
        shared.mkdir(exist_ok=True)
        path = shared / (name + '.config.json')
        path.write_text(json.dumps(config), encoding='utf-8')
        output = open(shared / (name + '.out'), 'wb')
        # One process (python_child): the pid its marker names is this child's own, under Mentat's venv too.
        command, env = python_child.argv([sys.executable, str(Path(__file__).resolve()), '--child', str(path)])
        child = subprocess.Popen(command, env=env, stdout=output, stderr=subprocess.STDOUT,
                                 cwd=str(Path(__file__).resolve().parent))
        self.addCleanup(lambda: (child.poll() is None and child.kill(), child.wait(), output.close()))
        return child, config

    def markers(self):
        return list((Path(self.tmp.name) / 'shared' / 'insights' / 'building').glob('*.lock'))

    def wait_for_marker(self):
        deadline = time.time() + 30
        while not self.markers():
            self.assertLess(time.time(), deadline, 'the first process never claimed the build')
            time.sleep(.02)

    def finish(self, child, config):
        try:
            code = child.wait(timeout=90)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
            self.fail('child did not finish: ' + config['log'])
        log = [json.loads(line) for line in Path(config['log']).read_text(encoding='utf-8').splitlines()]
        self.assertEqual(code, 0, (Path(config['log']).parent / (Path(config['log']).stem + '.out')).read_text(errors='replace'))
        return log

    def test_the_second_process_waits_for_the_first_ones_build(self):
        first, first_config = self.spawn('first', delay=4)
        self.wait_for_marker()
        second, second_config = self.spawn('second', delay=0)
        first_log, second_log = self.finish(first, first_config), self.finish(second, second_config)
        self.assertEqual(first_log[-1]['status'], 'ready')
        self.assertEqual(first_log[-1]['prepared'], 1)
        self.assertEqual(second_log[-1]['status'], 'ready')
        self.assertEqual(second_log[-1]['prepared'], 0, 'the second process must never build')
        finished = first_log[-1]['at']
        waiting = [entry for entry in second_log if entry['at'] < finished - .25]
        self.assertTrue(waiting)
        for entry in waiting:
            self.assertEqual((entry['status'], entry['retry_after_ms'], entry['message'], entry['jobs'], entry['submits']),
                             ('preparing', 500, a.ELSEWHERE_MESSAGE, 0, 0))
        self.assertEqual(Path(second_config['result']).read_text(), Path(first_config['result']).read_text())
        self.assertEqual(self.markers(), [])

    def test_a_live_builders_marker_defers_at_any_age_and_a_dead_or_recycled_ones_does_not(self):
        # Review H2, across two processes: the first process holds its real marker while it builds.
        first, _ = self.spawn('first', delay=90)
        self.wait_for_marker()
        marker = self.markers()[0]
        body = marker.read_bytes()
        info = json.loads(body)
        self.assertEqual((info['pid'], info['pid_started']), (first.pid, a.proclock.process_started(first.pid)))
        later = 31 * 60
        moment = time.time() - later
        os.utime(marker, (moment, moment))
        shared = Path(self.tmp.name) / 'shared'
        with patch.object(a.store, 'ROOT', shared / 'query-cache'), \
                patch.object(a.proclock, '_now', lambda: time.time() + later):
            self.assertEqual(a.building_dir(), marker.parent)
            self.assertEqual(a._inspect(marker), (True, body))
            self.assertTrue(a._building_elsewhere(marker.stem), 'a live builder must defer others after 30 minutes')
            self.assertEqual(marker.read_bytes(), body)
            # The same pid with another creation time is a recycled pid: stale, removed.
            other = info['pid_started'] - (1 if os.name == 'nt' else 3 * a.proclock.POSIX_START_TOLERANCE_TICKS)
            marker.write_bytes(json.dumps({**info, 'pid_started': other}).encode('utf-8'))
            self.assertFalse(a._building_elsewhere(marker.stem))
            self.assertFalse(marker.exists())
            marker.write_bytes(body)
            first.kill()
            first.wait()
            deadline = time.time() + 30
            while a.proclock.process_started(first.pid) is not None and time.time() < deadline:
                time.sleep(.05)
            self.assertEqual(a._inspect(marker), (False, body))
            self.assertFalse(a._building_elsewhere(marker.stem), 'a dead builder defers no one')
            self.assertFalse(marker.exists())

    def test_a_killed_builder_leaves_a_stale_marker_the_other_process_replaces(self):
        first, first_config = self.spawn('first', delay=60)
        self.wait_for_marker()
        first.kill()
        first.wait()
        self.assertEqual(len(self.markers()), 1, "the killed process's marker stays behind")
        second, second_config = self.spawn('second', delay=0)
        second_log = self.finish(second, second_config)
        self.assertEqual(second_log[-1]['status'], 'ready')
        self.assertEqual(second_log[-1]['prepared'], 1)
        self.assertEqual(self.markers(), [])


class HintTests(_Protocol):
    def setUp(self):
        super().setUp()
        self.hints = Path(self.tmp.name) / 'prewarm' / 'hints.json'
        for name, value in (('HINTS_ENABLED', True), ('HINTS_PATH', self.hints)):
            started = patch.object(a, name, value)
            started.start()
            self.addCleanup(started.stop)

    def read(self):
        a._hint_writer.submit(lambda: None).result(timeout=30)
        return json.loads(self.hints.read_text(encoding='utf-8'))

    def test_hints_are_newest_first_one_per_key_capped_and_replaced_atomically(self):
        sent = []
        for top_n in range(1, 28):
            body = self.request(top_n=top_n, panels=['favorites'], **({'retry': True} if top_n % 5 == 0 else {}))
            sent.append(body)
            for _ in range(200):
                if a.query(self.tmp.name, dict(body))['status'] == 'ready':
                    break
                time.sleep(.01)
        document = self.read()
        self.assertEqual(set(document), {'version', 'updated_utc', 'requests'})
        self.assertEqual(document['version'], 1)
        datetime.fromisoformat(document['updated_utc'])
        requests = document['requests']
        self.assertEqual(len(requests), 25)
        self.assertEqual([r['body']['top_n'] for r in requests], list(range(27, 2, -1)))
        for entry in requests:
            self.assertEqual(set(entry), {'body', 'key', 'dataset', 'seen_utc'})
            self.assertEqual(entry['body'], {k: v for k, v in sent[entry['body']['top_n'] - 1].items() if k != 'retry'})
            self.assertEqual(entry['dataset'], 'Example')
            datetime.fromisoformat(entry['seen_utc'])
            stored = a.store.ROOT / 'responses' / 'insights' / (a.store._key([a.store.LOADED_SHA, 'insights', {'request_key': entry['key']}]) + '.json')
            self.assertTrue(stored.exists(), 'a hint key must be the key of the stored response')
        self.assertEqual(len({entry['key'] for entry in requests}), 25)
        a.query(self.tmp.name, dict(sent[4]))
        again = self.read()['requests']
        self.assertEqual((len(again), again[0]['body']['top_n']), (25, 5))
        self.assertEqual(len({entry['key'] for entry in again}), 25)
        self.assertEqual(sorted(p.name for p in self.hints.parent.iterdir()), ['hints.json'])

    def test_a_hint_failure_never_fails_or_waits_for_the_request(self):
        self.ready()
        blocked = Path(self.tmp.name) / 'a file'
        blocked.write_text('not a folder')
        with patch.object(a, 'HINTS_PATH', blocked / 'hints.json'), self.assertLogs('mentat.insights', 'WARNING'):
            self.assertEqual(a.query(self.tmp.name, self.request())['status'], 'ready')
            a._hint_writer.submit(lambda: None).result(timeout=30)
        writes = []

        def slow(entry):
            time.sleep(1)
            writes.append(entry)

        with patch.object(a, '_write_hint', slow):
            started = time.perf_counter()
            self.assertEqual(a.query(self.tmp.name, self.request())['status'], 'ready')
            self.assertLess(time.perf_counter() - started, .5)
            self.assertEqual(writes, [])
            a._hint_writer.submit(lambda: None).result(timeout=30)
        self.assertEqual(len(writes), 1)

    def test_hints_are_off_unless_the_console_turns_them_on(self):
        with patch.object(a, 'HINTS_ENABLED', False):
            self.ready()
            a._hint_writer.submit(lambda: None).result(timeout=30)
        self.assertFalse(self.hints.exists())


def _child(config):
    """One console-like process: poll one Insights scope until it is ready."""
    source = {'id': 'Example', 'industry': 'Example'}
    for target, name, value in ((a.history, 'ROOT', Path(config['history'])), (a.store, 'ROOT', Path(config['store'])),
                                (a.data, '_source', lambda directory, request, sources=None: source),
                                (a.data, '_ready', lambda source, retry=False: (Path(config['index']), None)),
                                (a.data, '_token', lambda source: 'fixture-token'),
                                (a.data, '_provenance', lambda source: {}),
                                (a.integrations, 'sources_fingerprint', lambda context: 'integration-v1')):
        patch.object(target, name, value).start()
    real_prepare, prepared = a._prepare, []

    def slow_prepare(*args, **kwargs):
        prepared.append(time.time())
        time.sleep(config['delay'])
        yield from real_prepare(*args, **kwargs)

    patch.object(a, '_prepare', slow_prepare).start()
    real_submit, submits = a._pool.submit, []

    def counted(*args, **kwargs):
        submits.append(time.time())
        return real_submit(*args, **kwargs)

    patch.object(a._pool, 'submit', counted).start()
    deadline = time.time() + config['timeout']
    with open(config['log'], 'w', encoding='utf-8') as log:
        while time.time() < deadline:
            response = a.query(config['directory'], dict(REQUEST))
            log.write(json.dumps({'status': response['status'], 'retry_after_ms': response.get('retry_after_ms'),
                                  'message': response.get('message'), 'jobs': len(a._jobs), 'submits': len(submits),
                                  'prepared': len(prepared), 'at': time.time()}) + '\n')
            log.flush()
            if response['status'] == 'ready':
                Path(config['result']).write_text(json.dumps(response['panels'], sort_keys=True), encoding='utf-8')
                return 0
            time.sleep(.1)
    return 3


if __name__ == '__main__':
    if sys.argv[1:2] == ['--child']:
        sys.exit(_child(_config))
    unittest.main()

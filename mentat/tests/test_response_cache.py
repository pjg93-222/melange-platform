"""response_cache: memory LRU, disk tier, single flight across threads and processes, HTTP helpers.

Child interpreters of this file stand in for a second console or the warmer. Their output goes
to real files and every child is waited on with a timeout and tree-killed (brain DE-026).
MENTAT_CACHE_TEST_OVERLAY puts a folder of variant modules in front of mentat/app (the negative
controls in response_cache_negative_controls.py use it; children inherit it).
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1] / 'app'
sys.path.insert(0, str(APP))
if os.environ.get('MENTAT_CACHE_TEST_OVERLAY'):
    sys.path.insert(0, os.environ['MENTAT_CACHE_TEST_OVERLAY'])
import response_cache as rc  # noqa: E402

NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def kill_tree(process):
    if process.poll() is None:
        if os.name == 'nt':
            killer = subprocess.Popen(['taskkill', '/PID', str(process.pid), '/T', '/F'], stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
            try:
                killer.wait(timeout=15)
            except subprocess.TimeoutExpired:
                killer.kill()
        else:
            process.kill()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()


def iso(moment):
    return moment.astimezone(timezone.utc).isoformat()


class CacheCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.root = self.dir / 'response-cache'
        saved = (rc.ROOT, rc.MEMORY_BYTES, rc.DISK_BYTES, dict(rc.DISK_LIMITS))

        def restore():
            rc.ROOT, rc.MEMORY_BYTES, rc.DISK_BYTES = saved[:3]
            rc.DISK_LIMITS.clear()
            rc.DISK_LIMITS.update(saved[3])
            rc.clear_memory()
        self.addCleanup(restore)
        rc.clear_memory()
        rc.configure(self.root)
        self.children = 0

    def counter(self, namespace, name):
        return rc.stats(namespace)['namespaces'][namespace][name]

    def spawn(self, *args):
        self.children += 1
        log = open(self.dir / f'child-{self.children}.log', 'wb')
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), 'child', *map(str, args)],
                                   stdout=log, stderr=subprocess.STDOUT, cwd=str(Path(__file__).resolve().parent),
                                   creationflags=NO_WINDOW)
        self.addCleanup(log.close)
        self.addCleanup(kill_tree, process)
        return process

    def wait_for(self, predicate, seconds, message):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(.02)
        logs = '\n'.join(p.read_text(errors='replace') for p in sorted(self.dir.glob('child-*.log')))
        self.fail(f'{message}\n{logs}')

    def files(self, namespace):
        folder = self.root / namespace
        return sorted(p.name for p in folder.iterdir()) if folder.exists() else []


class SingleFlightTests(CacheCase):
    def test_threads_racing_one_key_compute_once(self):
        rc.ROOT = None  # in-process flight alone; the lock file is exercised below
        calls, gate = [], threading.Barrier(8)

        def compute():
            calls.append(threading.get_ident())
            time.sleep(.2)
            return {'answer': 42}

        def read(_):
            gate.wait()
            return rc.get_or_compute('flight-threads', {'q': 1}, {'v': 1}, compute)

        with ThreadPoolExecutor(max_workers=8) as pool:
            entries = list(pool.map(read, range(8)))
        self.assertEqual(len(calls), 1)
        self.assertEqual(sorted(e.hit for e in entries), ['built'] + ['memory'] * 7)
        self.assertEqual({e.body for e in entries}, {b'{"answer":42}'})
        # Only the builder gets the computed object; the others decode their own copies.
        self.assertEqual([e.value for e in entries if e.hit == 'built'], [{'answer': 42}])
        self.assertTrue(all(e.value is None for e in entries if e.hit != 'built'))

    def test_threads_racing_with_disk_tier_compute_once(self):
        calls, gate = [], threading.Barrier(6)

        def compute():
            calls.append(1)
            time.sleep(.2)
            return {'answer': 'disk'}

        def read(_):
            gate.wait()
            return rc.get_or_compute('flight-disk', {'q': 1}, {'v': 1}, compute)

        with ThreadPoolExecutor(max_workers=6) as pool:
            entries = list(pool.map(read, range(6)))
        self.assertEqual(len(calls), 1)
        self.assertEqual(len({e.key for e in entries}), 1)
        key = entries[0].key
        self.assertEqual(self.files('flight-disk'), [key + '.gz', key + '.json'])  # lock released

    def test_two_processes_race_one_key_compute_once(self):
        computes, go = self.dir / 'computes.txt', self.dir / 'go'
        outs = [self.dir / 'out-1.json', self.dir / 'out-2.json']
        processes = [self.spawn('race', self.root, self.dir, go, computes, out) for out in outs]
        self.wait_for(lambda: len(list(self.dir.glob('ready-*'))) == 2, 60, 'children did not start')
        go.write_text('go')
        for process in processes:
            process.wait(timeout=90)
        results = [json.loads(out.read_text()) for out in outs]
        self.assertEqual(len(computes.read_text().split()), 1, results)
        self.assertEqual(sorted(r['hit'] for r in results), ['built', 'disk'])
        self.assertEqual(len({r['sha256'] for r in results}), 1)
        self.assertEqual(results[0]['body'], '{"computed":"once"}')
        self.assertFalse(list((self.root / 'race').glob('*.lock')))

    def test_builder_killed_mid_build_leaves_a_stale_lock_that_is_broken(self):
        started = self.dir / 'started'
        process = self.spawn('hold', self.root, started)
        self.wait_for(started.exists, 60, 'the builder never started')
        self.assertEqual(len(list((self.root / 'held').glob('*.lock'))), 1)
        kill_tree(process)
        began = time.monotonic()
        entry = rc.get_or_compute('held', {'q': 'hold'}, {'v': 1}, lambda: {'rebuilt': True}, wait_seconds=5)
        self.assertEqual(entry.hit, 'built')
        self.assertLess(time.monotonic() - began, 4)
        self.assertEqual(entry.body, b'{"rebuilt":true}')

    def test_live_foreign_lock_defers_until_the_entry_is_stored(self):
        namespace, parts, deps = 'foreign', {'q': 1}, {'v': 1}
        key = rc.entry_key(namespace, parts, deps)
        folder = self.root / namespace
        folder.mkdir(parents=True)
        holder = self.spawn('sleep', 60)
        time.sleep(.3)  # the holder's lock must postdate its creation
        lock = folder / (key + '.lock')
        lock.write_text(json.dumps({'pid': holder.pid, 'started_utc': iso(datetime.now(timezone.utc)), 'key': key}))
        calls = []

        def store_as_the_other_process():
            time.sleep(.6)
            other = rc.Entry(namespace, key, body=b'{"from":"elsewhere"}', hit='built')
            rc._disk_put(folder, namespace, key, other, parts, deps)
            lock.unlink()
        writer = threading.Thread(target=store_as_the_other_process)
        writer.start()
        entry = rc.get_or_compute(namespace, parts, deps, lambda: calls.append(1) or {'from': 'here'}, wait_seconds=20)
        writer.join()
        self.assertEqual(calls, [])
        self.assertEqual(entry.hit, 'disk')
        self.assertEqual(entry.body, b'{"from":"elsewhere"}')
        self.assertGreaterEqual(self.counter(namespace, 'waits'), 1)

    def test_live_foreign_lock_past_the_deadline_is_still_building(self):
        namespace, parts, deps = 'foreign-slow', {'q': 1}, {'v': 1}
        key = rc.entry_key(namespace, parts, deps)
        (self.root / namespace).mkdir(parents=True)
        holder = self.spawn('sleep', 60)
        time.sleep(.3)
        (self.root / namespace / (key + '.lock')).write_text(
            json.dumps({'pid': holder.pid, 'started_utc': iso(datetime.now(timezone.utc)), 'key': key}))
        calls = []
        with self.assertRaisesRegex(ValueError, 'still being prepared'):
            rc.get_or_compute(namespace, parts, deps, lambda: calls.append(1) or {}, wait_seconds=.8)
        self.assertEqual(calls, [])


class LockRuleTests(CacheCase):
    def lock(self, name, pid, started, age=0.0, raw=None):
        path = self.dir / (name + '.lock')
        path.write_bytes(raw if raw is not None else json.dumps({'pid': pid, 'started_utc': iso(started), 'key': name}).encode())
        if age:
            moment = time.time() - age
            os.utime(path, (moment, moment))
        return path

    def holder(self):
        """A live foreign process and a moment no earlier than its creation."""
        process = self.spawn('sleep', 60)
        return process, datetime.now(timezone.utc)

    def test_lock_of_a_dead_process_is_stale_and_broken_by_the_next_claim(self):
        finished = subprocess.Popen([sys.executable, '-c', 'pass'], creationflags=NO_WINDOW,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        finished.wait(timeout=30)
        path = self.lock('dead', finished.pid, datetime.now(timezone.utc))
        self.assertFalse(rc._inspect(path)[0])
        self.assertIsNotNone(rc._claim(path))
        self.assertEqual(json.loads(path.read_text())['pid'], os.getpid())

    def test_lock_whose_pid_was_recycled_is_stale(self):
        process, created = self.holder()
        path = self.lock('recycled', process.pid, created - timedelta(seconds=30))  # older than the process
        self.assertFalse(rc._inspect(path)[0])

    def test_lock_of_a_live_process_is_never_stale_by_its_age(self):
        # Review H2: only a dead or recycled holder makes a lock stale; its age never does.
        process, created = self.holder()
        path = self.lock('old', process.pid, created, age=31 * 60)
        with patch.object(rc.proclock, '_now', lambda: time.time() + 31 * 60):
            self.assertTrue(rc._inspect(path)[0])
            self.assertIsNone(rc._claim(path))
        self.assertTrue(path.exists())

    def test_lock_naming_this_process_is_stale(self):
        path = self.lock('mine', os.getpid(), datetime.now(timezone.utc))
        self.assertFalse(rc._inspect(path)[0])

    def test_live_foreign_lock_is_respected(self):
        process, created = self.holder()
        path = self.lock('live', process.pid, created)
        self.assertTrue(rc._inspect(path)[0])
        self.assertIsNone(rc._claim(path))
        self.assertIsNone(rc._inspect(self.dir / 'absent.lock'))

    def test_unreadable_lock_is_live_only_while_it_can_still_be_being_written(self):
        self.assertTrue(rc._inspect(self.lock('fresh', 0, datetime.now(timezone.utc), raw=b'{"pid'))[0])
        self.assertFalse(rc._inspect(self.lock('abandoned', 0, datetime.now(timezone.utc), age=120, raw=b'{"pid'))[0])

    def test_unlink_only_removes_the_lock_it_wrote(self):
        path = self.dir / 'mine.lock'
        claimed = rc._claim(path)
        path.write_bytes(b'{"pid": 1, "started_utc": "2026-01-01T00:00:00+00:00", "key": "mine"}')
        rc._unlink(path, claimed)
        self.assertTrue(path.exists())

class TierTests(CacheCase):
    def test_memory_lru_is_bounded_by_bytes_and_keeps_recent_use(self):
        rc.ROOT = None
        # Incompressible bodies of one length: every stored gzip has the same, known size.
        bodies = {i: os.urandom(1000) for i in range(6)}
        sizes = {i: len(gzip.compress(b, compresslevel=rc.GZIP_LEVEL, mtime=0)) for i, b in bodies.items()}
        rc.MEMORY_BYTES = sizes[0] + sizes[1] + sizes[2] + 16
        for i in range(3):
            rc.get_or_compute('lru', {'i': i}, {}, lambda i=i: bodies[i])
        self.assertEqual(rc.get_or_compute('lru', {'i': 0}, {}, lambda: b'rebuilt').hit, 'memory')  # 0 is now recent
        rc.get_or_compute('lru', {'i': 3}, {}, lambda: bodies[3])
        present = {i for i in range(4) if rc.lookup('lru', {'i': i}, {}) is not None}
        self.assertEqual(present, {0, 2, 3})  # 1 was least recently used
        memory = rc.stats('lru')['memory']
        self.assertLessEqual(memory['bytes'], rc.MEMORY_BYTES)
        self.assertEqual(memory['bytes'], sizes[0] + sizes[2] + sizes[3])
        self.assertGreaterEqual(self.counter('lru', 'memory_evictions'), 1)

    def test_disk_cap_evicts_least_recently_used(self):
        namespace = 'cap'
        bodies = {i: json.dumps({'i': i, 'noise': os.urandom(800).hex()}).encode() for i in range(4)}
        keys = {i: rc.get_or_compute(namespace, {'i': i}, {}, lambda i=i: bodies[i]).key for i in range(3)}
        folder = self.root / namespace
        entry_bytes = {i: (folder / (keys[i] + '.gz')).stat().st_size + (folder / (keys[i] + '.json')).stat().st_size
                       for i in range(3)}
        base = time.time() - 1000
        for order, i in enumerate((0, 1, 2)):
            os.utime(folder / (keys[i] + '.json'), (base + order * 10, base + order * 10))
        rc.clear_memory()
        self.assertEqual(rc.get_or_compute(namespace, {'i': 0}, {}, lambda: b'x').hit, 'disk')  # 0 used again
        rc.DISK_LIMITS[namespace] = sum(entry_bytes.values()) + 200  # room for three, not four
        keys[3] = rc.get_or_compute(namespace, {'i': 3}, {}, lambda: bodies[3]).key
        names = set(self.files(namespace))
        self.assertNotIn(keys[1] + '.json', names)  # least recently used
        self.assertNotIn(keys[1] + '.gz', names)
        for i in (0, 2, 3):
            self.assertIn(keys[i] + '.gz', names)
        usage = rc.disk_usage(namespace)
        self.assertLessEqual(usage['bytes'], rc.DISK_LIMITS[namespace])
        self.assertEqual(usage['entries'], 3)

    def test_disk_tier_is_off_until_configured(self):
        rc.ROOT = None
        entry = rc.get_or_compute('off', {'q': 1}, {}, lambda: {'a': 1})
        self.assertEqual(entry.hit, 'built')
        self.assertFalse(rc.enabled())
        self.assertFalse(self.root.exists())
        rc.configure(self.root)
        rc.clear_memory()
        rc.get_or_compute('off', {'q': 1}, {}, lambda: {'a': 1})
        self.assertEqual(len(self.files('off')), 2)

    def test_restart_is_served_from_disk_with_identical_bytes(self):
        value = {'text': 'Independent spending · support', 'amount': 1234.5, 'rows': [1, 2.25, None, True]}
        built = rc.get_or_compute('restart', {'q': 1}, {'v': 1}, lambda: value)
        rc.clear_memory()  # what a restart loses
        calls = []
        again = rc.get_or_compute('restart', {'q': 1}, {'v': 1}, lambda: calls.append(1) or value)
        self.assertEqual(calls, [])
        self.assertEqual(again.hit, 'disk')
        self.assertEqual(again.body, built.body)
        self.assertEqual(again.gzip, built.gzip)
        self.assertEqual(again.etag, built.etag)
        self.assertEqual(rc.get_or_compute('restart', {'q': 1}, {'v': 1}, lambda: value).hit, 'memory')

    def test_deps_change_is_a_miss(self):
        calls = []

        def compute(version):
            calls.append(version)
            return {'version': version}
        first = rc.get_or_compute('deps', {'q': 1}, {'input_sha256': 'a' * 64, 'code': 'x'}, lambda: compute(1))
        second = rc.get_or_compute('deps', {'q': 1}, {'input_sha256': 'b' * 64, 'code': 'x'}, lambda: compute(2))
        third = rc.get_or_compute('deps', {'q': 1}, {'input_sha256': 'b' * 64, 'code': 'y'}, lambda: compute(3))
        self.assertEqual(calls, [1, 2, 3])
        self.assertEqual(len({first.key, second.key, third.key}), 3)
        rc.clear_memory()
        self.assertEqual(json.loads(rc.lookup('deps', {'q': 1}, {'input_sha256': 'a' * 64, 'code': 'x'}).body), {'version': 1})

    def test_persist_false_answers_without_storing(self):
        calls = []
        for _ in range(2):
            entry = rc.get_or_compute('kept-out', {'q': 1}, {}, lambda: calls.append(1) or {'issues': ['x']},
                                      persist=lambda value: not value['issues'])
            self.assertEqual(entry.hit, 'built')
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.files('kept-out'), [])

    def test_compute_error_stores_nothing_and_releases_the_lock(self):
        def fail():
            raise ValueError('source changed while building')
        with self.assertRaisesRegex(ValueError, 'source changed'):
            rc.get_or_compute('failing', {'q': 1}, {}, fail)
        self.assertEqual(self.files('failing'), [])
        self.assertEqual(rc.get_or_compute('failing', {'q': 1}, {}, lambda: {'ok': True}).hit, 'built')


class IntegrityTests(CacheCase):
    def stored(self, namespace):
        entry = rc.get_or_compute(namespace, {'q': 1}, {'v': 1}, lambda: {'payload': list(range(400))})
        rc.clear_memory()
        folder = self.root / namespace
        return entry, folder / (entry.key + '.gz'), folder / (entry.key + '.json')

    def rebuilt(self, namespace):
        calls = []
        entry = rc.get_or_compute(namespace, {'q': 1}, {'v': 1},
                                  lambda: calls.append(1) or {'payload': list(range(400))})
        return entry, calls

    def test_corrupt_or_truncated_entries_are_rebuilt_never_served(self):
        def truncate(body, meta):
            body.write_bytes(body.read_bytes()[:-7])

        def flip(body, meta):
            data = bytearray(body.read_bytes())
            data[len(data) // 2] ^= 0xFF
            body.write_bytes(bytes(data))

        def bad_meta(body, meta):
            meta.write_bytes(b'{"format": 1, "key": ')

        def foreign_meta(body, meta):
            record = json.loads(meta.read_bytes())
            record['key'] = 'f' * 64
            meta.write_text(json.dumps(record))

        def other_format(body, meta):
            record = json.loads(meta.read_bytes())
            record['format'] = rc.FORMAT + 1
            meta.write_text(json.dumps(record))

        def gzip_header(body, meta):
            # The gzip MTIME field is outside its CRC: only the recorded SHA-256 sees this change.
            data = bytearray(body.read_bytes())
            data[4] ^= 0xFF
            body.write_bytes(bytes(data))

        def body_missing(body, meta):
            body.unlink()

        def consistent_but_wrong_size(body, meta):
            # The gzip is intact and matches its recorded hash, but the recorded body size is not
            # what it inflates to: never served.
            record = json.loads(meta.read_bytes())
            record['body_bytes'] += 1
            meta.write_text(json.dumps(record))

        for damage in (truncate, flip, gzip_header, bad_meta, foreign_meta, other_format, body_missing,
                       consistent_but_wrong_size):
            with self.subTest(damage.__name__):
                namespace = 'corrupt-' + damage.__name__.replace('_', '-')
                built, body, meta = self.stored(namespace)
                damage(body, meta)
                entry, calls = self.rebuilt(namespace)
                self.assertEqual(calls, [1])
                self.assertEqual(entry.hit, 'built')
                self.assertEqual(entry.body, built.body)
                self.assertGreaterEqual(self.counter(namespace, 'corrupt'), 1)
                rc.clear_memory()
                again, calls = self.rebuilt(namespace)  # the rebuild replaced it with a sound entry
                self.assertEqual((again.hit, calls), ('disk', []))

    def test_writes_are_atomic(self):
        namespace = 'atomic'
        real_write = rc._write_atomic

        def die_half_way(target, data):
            if target.suffix == '.json':
                raise OSError('disk full')
            return real_write(target, data)
        with patch.object(rc, '_write_atomic', side_effect=die_half_way):
            entry = rc.get_or_compute(namespace, {'q': 1}, {}, lambda: {'a': 1})
        self.assertEqual(entry.hit, 'built')  # the answer is still given
        self.assertEqual(self.counter(namespace, 'disk_errors'), 1)
        rc.clear_memory()
        calls = []
        self.assertEqual(rc.get_or_compute(namespace, {'q': 1}, {}, lambda: calls.append(1) or {'a': 1}).hit, 'built')
        self.assertEqual(calls, [1])  # a body without its meta is never served

        # A writer that dies inside a write leaves nothing under the entry's own names.
        namespace = 'atomic-partial'
        original_open = open

        class Torn(io.FileIO):
            def write(self, data):
                super().write(data[:len(data) // 2])
                raise OSError('power cut')

        def torn_open(file, mode='r', *args, **kwargs):
            if 'w' in mode and str(file).startswith(str(self.root)) and not str(file).endswith('.lock'):
                return Torn(file, 'w')
            return original_open(file, mode, *args, **kwargs)
        with patch('builtins.open', torn_open):
            rc.get_or_compute(namespace, {'q': 1}, {}, lambda: {'b': 2})
        self.assertEqual(self.files(namespace), [])


class CodeIdentityTests(CacheCase):
    def setUp(self):
        super().setUp()
        self.modules = self.dir / 'modules'
        self.modules.mkdir()
        (self.modules / 'rc_probe_root.py').write_text('def run():\n    import rc_probe_leaf\n    return rc_probe_leaf.VALUE\n')
        (self.modules / 'rc_probe_leaf.py').write_text('VALUE = 1\n')
        (self.modules / 'rc_probe_unused.py').write_text('VALUE = 2\n')
        sys.path.insert(0, str(self.modules))
        self.addCleanup(sys.path.remove, str(self.modules))
        rc._code.clear()
        self.addCleanup(rc._code.clear)
        # The files above were written by this process; judge them as a process started later would.
        later = time.time_ns() + 1_000_000_000
        patcher = patch.object(rc, 'process_started_ns', return_value=later)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_closure_follows_function_level_imports_of_local_modules_only(self):
        found = rc.module_closure('rc_probe_root')
        self.assertIn('rc_probe_root', found)
        self.assertIn('rc_probe_leaf', found)
        self.assertIn('response_cache', found)
        self.assertNotIn('rc_probe_unused', found)
        self.assertNotIn('json', found)

    def test_identity_moves_with_code_and_extra(self):
        first = rc.code_identity('rc_probe_root', extra={'native': 'a'})
        self.assertRegex(first, r'^[0-9a-f]{64}$')
        self.assertEqual(rc.code_identity('rc_probe_root', extra={'native': 'a'}), first)
        self.assertNotEqual(rc.code_identity('rc_probe_root', extra={'native': 'b'}), first)

    def test_code_changed_under_this_process_turns_the_disk_tier_off(self):
        self.assertIsNotNone(rc.code_identity('rc_probe_root'))
        time.sleep(.02)
        (self.modules / 'rc_probe_leaf.py').write_text('VALUE = 3\n')
        self.assertIsNone(rc.code_identity('rc_probe_root'))

    def test_code_written_after_this_process_started_is_not_trusted(self):
        with patch.object(rc, 'process_started_ns', return_value=time.time_ns() - 60_000_000_000):
            self.assertIsNone(rc.code_identity('rc_probe_root'))


class HttpTests(CacheCase):
    def test_etag_is_strong_and_from_the_body_hash(self):
        entry = rc.from_value({'a': 'é', 'b': [1.5, None]})
        digest = hashlib.sha256(entry.body).hexdigest()
        self.assertEqual(entry.etag, '"' + digest + '"')
        self.assertEqual(rc.etag(entry, gzipped=True), '"' + digest + '-gzip"')
        self.assertFalse(entry.etag.startswith('W/'))
        cached = rc.get_or_compute('etag', {'q': 1}, {}, lambda: {'a': 'é', 'b': [1.5, None]})
        self.assertEqual(cached.etag, entry.etag)  # same body, same tag, cached or not

    def test_not_modified(self):
        entry = rc.from_value({'x': 1})
        tag, zipped = entry.etag, rc.etag(entry, True)
        for header, expected in [(tag, True), (zipped, True), ('W/' + tag, True), (f'"nope", {tag}', True),
                                 ('*', True), ('"nope"', False), ('', False), (None, False),
                                 (tag.strip('"'), False)]:
            with self.subTest(header=header):
                self.assertEqual(rc.not_modified(header, entry), expected)

    def test_respond_matches_handler_send_bytes_and_answers_304(self):
        value = {'rows': [{'label': 'Independent spending · support', 'amount': 12.5}] * 200}
        entry = rc.get_or_compute('respond', {'q': 1}, {}, lambda: value)
        status, headers, body = rc.respond(entry, None, 'gzip, deflate, br')
        headers = dict(headers)
        self.assertEqual((status, headers['Content-Encoding'], headers['ETag']), (200, 'gzip', rc.etag(entry, True)))
        self.assertEqual(body, gzip.compress(rc.json_body(value), compresslevel=1, mtime=0))
        self.assertEqual(gzip.decompress(body), json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode())
        status, headers, body = rc.respond(entry, None, 'identity')
        self.assertEqual((status, body, 'Content-Encoding' in dict(headers)), (200, rc.json_body(value), False))
        status, headers, body = rc.respond(entry, rc.etag(entry, True), 'gzip')
        self.assertEqual((status, body, dict(headers)['ETag']), (304, b'', rc.etag(entry, True)))
        small = rc.from_value({'tiny': True})
        status, headers, body = rc.respond(small, None, 'gzip')
        self.assertEqual((body, 'Content-Encoding' in dict(headers)), (b'{"tiny":true}', False))

    def test_gzip_rule_and_bytes_match_server_send(self):
        try:
            import server
        except Exception as error:  # pragma: no cover - server imports the whole app
            self.skipTest(f'server.py not importable here: {error}')
        for header in ['gzip', 'GZIP', 'gzip;q=0', 'gzip; q=0.5', 'br, gzip;q=1.0', 'deflate', '', None, 'gzip;q=x']:
            with self.subTest(header=header):
                self.assertEqual(rc.accepts_gzip(header), server.accepts_gzip(header))

        class Fake:
            access_cfg = server.Handler.access_cfg   # send() reads whether Access is enforced (hosted: scrubs paths)

            def __init__(self, accept):
                self.headers, self.wfile, self.sent = {'Accept-Encoding': accept}, io.BytesIO(), []

            def send_response(self, status):
                self.sent.append(('status', status))

            def send_header(self, name, value):
                self.sent.append((name, value))

            def end_headers(self):
                pass
        value = {'label': 'Committee activity · unknown', 'amounts': [0.1, 2.0, -0.0, 1e21],
                 'rows': ['Independent spending · opposition'] * 120}  # over send()'s 2048-byte gzip floor
        entry = rc.from_value(value)
        for accept in ('gzip', ''):
            fake = Fake(accept)
            server.Handler.send(fake, value)
            status, headers, body = rc.respond(entry, None, accept)
            self.assertEqual(body, fake.wfile.getvalue())


class FreshProcessTests(CacheCase):
    def test_disk_tier_is_off_in_a_fresh_process(self):
        out = self.dir / 'fresh.json'
        process = self.spawn('enabled', out)
        process.wait(timeout=60)
        self.assertTrue(out.exists(), (self.dir / 'child-1.log').read_text(errors='replace'))
        self.assertEqual(json.loads(out.read_text()), {'enabled': False, 'root': None})


class StatsTests(CacheCase):
    def test_stats_per_namespace(self):
        rc.get_or_compute('stats', {'q': 1}, {}, lambda: {'a': 1})
        rc.get_or_compute('stats', {'q': 1}, {}, lambda: {'a': 1})
        rc.clear_memory()
        rc.get_or_compute('stats', {'q': 1}, {}, lambda: {'a': 1})
        row = rc.stats('stats')['namespaces']['stats']
        self.assertEqual((row['builds'], row['memory_hits'], row['disk_hits'], row['disk_writes']), (1, 1, 1, 1))
        self.assertEqual(row['disk']['entries'], 1)
        self.assertGreater(row['disk']['bytes'], 0)
        self.assertEqual(row['memory_entries'], 1)


# ----------------------------------------------------------------------------- child processes
def child(args):
    mode = args[0]
    if mode == 'sleep':
        time.sleep(float(args[1]))
        return
    if mode == 'race':
        root, folder, go, computes, out = map(Path, args[1:6])
        rc.configure(root)
        (folder / f'ready-{os.getpid()}').write_text('ready')
        deadline = time.monotonic() + 60
        while not go.exists() and time.monotonic() < deadline:
            time.sleep(.005)

        def compute():
            with open(computes, 'a') as stream:
                stream.write(f'{os.getpid()}\n')
            time.sleep(1.5)
            return {'computed': 'once'}
        entry = rc.get_or_compute('race', {'q': 'race'}, {'v': 1}, compute, wait_seconds=60)
        out.write_text(json.dumps({'hit': entry.hit, 'sha256': hashlib.sha256(entry.body).hexdigest(),
                                   'body': entry.body.decode(), 'pid': os.getpid()}))
        return
    if mode == 'enabled':
        import view_cache  # noqa: F401 - importing the Graph cache must not turn the disk tier on
        Path(args[1]).write_text(json.dumps({'enabled': rc.enabled(), 'root': None if rc.ROOT is None else str(rc.ROOT)}))
        return
    if mode == 'hold':
        root, started = Path(args[1]), Path(args[2])
        rc.configure(root)

        def compute():
            started.write_text(str(os.getpid()))
            time.sleep(120)
            return {'never': True}
        rc.get_or_compute('held', {'q': 'hold'}, {'v': 1}, compute)
        return
    raise SystemExit(f'unknown child mode {mode}')


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'child':
        child(sys.argv[2:])
    else:
        unittest.main()

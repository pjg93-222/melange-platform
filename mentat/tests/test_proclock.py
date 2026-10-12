"""proclock: the one cross-process lock rule of analysis_data, response_cache, drawer_sidecars and prewarm
(review findings M3 and H2, 2026-09-24).

Child interpreters of the CHILD script below stand in for another console or the warmer: each one claims a
lock through a module's own claim and holds it. Their output goes to real files, and every child is killed
and waited on with a timeout (brain DE-026). PROCLOCK_UNDER_TEST points this suite, and its children, at a
variant of proclock.py (the negative controls).
"""
from datetime import datetime, timedelta, timezone
import importlib.util
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
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))
if os.environ.get('PROCLOCK_UNDER_TEST'):
    _spec = importlib.util.spec_from_file_location('proclock', os.environ['PROCLOCK_UNDER_TEST'])
    _module = importlib.util.module_from_spec(_spec)
    sys.modules['proclock'] = _module
    _spec.loader.exec_module(_module)
import proclock  # noqa: E402
import python_child  # noqa: E402
import analysis_data as a  # noqa: E402
import drawer_sidecars  # noqa: E402
import response_cache as rc  # noqa: E402

NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
LATER = 31 * 60   # older than the 30-minute age limit the old modules applied to a live holder

CHILD = r'''
import json, os, sys, time
from pathlib import Path
app, mode, target, hold = sys.argv[1], sys.argv[2], Path(sys.argv[3]), float(sys.argv[4])
sys.path.insert(0, app)
if os.environ.get('PROCLOCK_UNDER_TEST'):
    import importlib.util
    spec = importlib.util.spec_from_file_location('proclock', os.environ['PROCLOCK_UNDER_TEST'])
    module = importlib.util.module_from_spec(spec)
    sys.modules['proclock'] = module
    spec.loader.exec_module(module)
if mode == 'proclock':
    import proclock
    body = proclock.claim(target, 'child')
elif mode == 'cache':
    import response_cache
    body = response_cache._claim(target)
elif mode == 'drawer':
    import drawer_sidecars
    body = drawer_sidecars._acquire(target)[1]
elif mode == 'analysis':
    import analysis_data
    analysis_data.store.ROOT = target.parents[2] / 'query-cache'   # building_dir(): <root>/insights/building
    assert analysis_data.building_dir() == target.parent, analysis_data.building_dir()
    body = analysis_data._claim(target.stem)
elif mode == 'race':
    import proclock
    start = float(sys.argv[5])
    while time.time() < start:
        pass
    body = proclock.claim(target, 'race')
elif mode == 'reader':      # reads the lock as proclock reads it, and keeps its handle open for `hold` seconds
    import proclock
    opener = getattr(proclock, '_open_shared', None) or (lambda path: open(path, 'rb'))   # before: open()
    stream = opener(target)
    body = stream.read()
elif mode == 'oldreader':   # reads it as a console on older code does: open(), without FILE_SHARE_DELETE
    stream = open(target, 'rb')
    body = stream.read()
print(json.dumps({'pid': os.getpid(), 'body': body.decode('utf-8') if body else None}), flush=True)
time.sleep(hold)
'''


def kill_tree(child):
    if child.poll() is None:
        if os.name == 'nt':
            killer = subprocess.Popen(['taskkill', '/F', '/T', '/PID', str(child.pid)], stdin=subprocess.DEVNULL,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
            try:
                killer.wait(timeout=30)
            except subprocess.TimeoutExpired:
                killer.kill()
        else:
            child.kill()
    try:
        child.wait(timeout=30)
    except subprocess.TimeoutExpired:
        pass


def utc(moment):
    return datetime.fromtimestamp(moment, timezone.utc).isoformat()


def backdate(path, seconds):
    moment = time.time() - seconds
    os.utime(path, (moment, moment))


class Case(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(proclock.release_all)   # runs first: no test leaves a lock held for the next
        self.dir = Path(self._tmp.name)
        self.count = 0

    def spawn(self, *arguments):
        """A child interpreter with its stdout on a real file; killed at cleanup."""
        self.count += 1
        out = self.dir / f'child-{self.count}.out'
        # One process (python_child): under Mentat's venv sys.executable is CPython's venv launcher, and these
        # tests hold the interpreter's own pid, the one it writes into its locks.
        command, env = python_child.argv([sys.executable, *map(str, arguments)])
        with open(out, 'wb') as stream:
            child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=stream, env=env,
                                     stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
        self.addCleanup(kill_tree, child)
        return child, out

    def sleeper(self, seconds=120):
        child, _ = self.spawn('-c', f'import time; time.sleep({seconds})')
        deadline = time.monotonic() + 30
        while not proclock.process_started(child.pid) and time.monotonic() < deadline:
            time.sleep(.02)
        return child

    def first_line(self, out, timeout=60):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            text = out.read_text(encoding='utf-8', errors='replace')
            if '\n' in text:
                return json.loads(text.splitlines()[0])
            time.sleep(.05)
        self.fail('the child printed nothing: ' + out.read_text(encoding='utf-8', errors='replace')[-2000:])

    def holder(self, mode, path, hold=120):
        """A child process holding the lock at `path`, claimed through `mode`'s own claim."""
        child, out = self.spawn('-c', CHILD, APP, mode, path, hold)
        line = self.first_line(out)
        self.assertEqual(line['pid'], child.pid)
        self.assertIsNotNone(line['body'], f'{mode}: the child could not claim {path}')
        self.assertEqual(Path(path).read_bytes(), line['body'].encode('utf-8'))
        return child, line['body'].encode('utf-8')

    def gone(self, child):
        kill_tree(child)
        deadline = time.monotonic() + 30
        while proclock.process_started(child.pid) is not None and time.monotonic() < deadline:
            time.sleep(.05)


# The four ways the app takes a lock, and each module's own reading of it (live?).
def analysis_live(path):
    found = a._inspect(path)
    return None if found is None else found[0]


def cache_live(path):
    found = rc._inspect(path)
    return None if found is None else found[0]


def drawer_live(path):
    return None if not path.exists() else not drawer_sidecars._stale(path)


WRITERS = ('analysis', 'cache', 'drawer', 'proclock')
READERS = {'analysis_data': analysis_live, 'response_cache': cache_live, 'drawer_sidecars': drawer_live,
           'proclock': lambda path: (lambda found: None if found is None else found.live)(proclock.inspect(path))}


def lock_path(root, mode):
    return root / 'insights' / 'building' / f'{mode}-key.lock' if mode == 'analysis' else root / f'{mode}-key.lock'


class ProcessTests(Case):
    def test_creation_time_names_a_running_process_and_none_once_it_exits(self):
        child = self.sleeper()
        started = proclock.process_started(child.pid)
        self.assertIsInstance(started, int)
        if os.name == 'nt':
            self.assertGreater(started, proclock.EPOCH_TICKS)
            self.assertEqual(proclock.process_started(child.pid), started)   # exact and stable
        self.assertLess(abs((started - proclock.EPOCH_TICKS) / proclock.TICKS_PER_SECOND - time.time()), 120)
        self.assertEqual(proclock.own_started(), proclock.process_started(os.getpid()))
        self.assertTrue(proclock.alive(child.pid))
        self.assertTrue(proclock.alive(child.pid, started))
        self.assertIsNone(child.poll(), 'the probe must not signal the process')
        self.gone(child)
        self.assertIsNone(proclock.process_started(child.pid))
        self.assertFalse(proclock.alive(child.pid))
        self.assertFalse(proclock.alive(child.pid, started))
        for nonsense in (None, 0, -5, 'x', 2 ** 40, True):
            self.assertFalse(proclock.alive(nonsense), nonsense)

    def test_a_recycled_pid_is_told_apart_by_its_creation_time(self):
        child = self.sleeper()
        started = proclock.process_started(child.pid)
        other = started - (1 if os.name == 'nt' else 3 * proclock.POSIX_START_TOLERANCE_TICKS)
        self.assertTrue(proclock.alive(child.pid, started))
        self.assertFalse(proclock.alive(child.pid, other))
        self.assertTrue(proclock.alive(child.pid, None))


class OneRuleTests(Case):
    """The staleness rule, as every module reads it, over locks another process claimed."""

    def root(self):
        return self.dir / 'root'

    def test_a_live_holders_lock_is_never_stale_by_age(self):
        # H2: a lock held by a live process is live 31 minutes on, whoever wrote it and whoever reads it.
        for mode in WRITERS:
            with self.subTest(writer=mode):
                path = lock_path(self.root(), mode)
                child, body = self.holder(mode, path)
                info = json.loads(body)
                self.assertEqual(info['pid'], child.pid)
                self.assertEqual(info['pid_started'], proclock.process_started(child.pid))
                for name, live in READERS.items():
                    self.assertTrue(live(path), f'{name} broke a fresh live lock')
                backdate(path, LATER)
                with patch.object(proclock, '_now', lambda: time.time() + LATER):
                    for name, live in READERS.items():
                        self.assertTrue(live(path), f'{name} expired a live lock by its age')
                    self.assertIsNone(proclock.claim(path))
                    self.assertIsNone(rc._claim(path))
                    self.assertEqual(drawer_sidecars._acquire(path), ('held', None))
                self.assertEqual(path.read_bytes(), body, 'a live lock must never be removed')
                self.gone(child)

    def test_a_dead_holders_lock_is_stale_and_taken_over(self):
        for mode in WRITERS:
            with self.subTest(writer=mode):
                path = lock_path(self.root(), mode)
                child, body = self.holder(mode, path)
                self.gone(child)
                for name, live in READERS.items():
                    self.assertFalse(live(path), f'{name} kept the lock of a dead process')
                self.assertEqual(proclock.inspect(path).reason, 'process gone')
                claimed = proclock.claim(path, 'taken over')
                self.assertIsNotNone(claimed)
                self.assertEqual(json.loads(path.read_bytes())['pid'], os.getpid())
                self.assertTrue(proclock.release(path, claimed))
                self.assertFalse(path.exists())

    def test_a_recycled_pid_makes_a_lock_stale_in_every_body_format(self):
        child = self.sleeper()
        started = proclock.process_started(child.pid)
        created = (started - proclock.EPOCH_TICKS) / proclock.TICKS_PER_SECOND
        other = started - (1 if os.name == 'nt' else 3 * proclock.POSIX_START_TOLERANCE_TICKS)
        live = {
            'new format': {'pid': child.pid, 'pid_started': started, 'started_utc': utc(created + 1), 'key': 'k'},
            'old analysis/cache format': {'pid': child.pid, 'started_utc': utc(created + 1), 'key': 'k'},
            'old drawer format': {'pid': child.pid, 'started': started, 'created': created + 1, 'token': 't'},
            'old drawer format without a start': {'pid': child.pid, 'started': 0, 'created': created + 1, 'token': 't'},
        }
        recycled = {
            'new format': {'pid': child.pid, 'pid_started': other, 'started_utc': utc(created + 1), 'key': 'k'},
            'old analysis/cache format': {'pid': child.pid, 'started_utc': utc(created - 90), 'key': 'k'},
            'old drawer format': {'pid': child.pid, 'started': 12345, 'created': created + 1, 'token': 't'},
            'old drawer format without a start': {'pid': child.pid, 'started': None, 'created': created - 90, 'token': 't'},
        }
        path = self.dir / 'formats.lock'
        for label, bodies, expected in (('live', live, True), ('recycled', recycled, False)):
            for name, body in bodies.items():
                with self.subTest(case=f'{label}: {name}'):
                    path.write_bytes(json.dumps(body).encode('utf-8'))
                    for reader, is_live in READERS.items():
                        self.assertEqual(is_live(path), expected, reader)

    def test_a_lock_of_this_process_is_live_exactly_while_this_process_holds_it(self):
        path = self.dir / 'mine.lock'
        body = proclock.claim(path, 'mine')
        self.assertIsNotNone(body)
        self.assertEqual(proclock.inspect(path).reason, 'held by this process')
        for name, live in READERS.items():
            self.assertTrue(live(path), name)
        self.assertIsNone(proclock.claim(path, 'again'))
        self.assertIn(os.path.normcase(os.path.abspath(path)), proclock.held())
        self.assertTrue(proclock.release(path, body))
        self.assertEqual(proclock.held(), {})
        path.write_bytes(body)   # the same bytes, no longer held: left behind by this process
        for name, live in READERS.items():
            self.assertFalse(live(path), name)
        path.write_bytes(json.dumps({'pid': os.getpid(), 'started_utc': utc(time.time()), 'key': 'hand'}).encode())
        self.assertFalse(proclock.inspect(path).live)
        again = proclock.claim(path, 'after')
        self.assertIsNotNone(again)
        self.assertEqual(proclock.release_all(), [os.path.normcase(os.path.abspath(path))])
        self.assertFalse(path.exists())

    def test_an_unreadable_body_is_live_for_a_minute_then_stale(self):
        path = self.dir / 'partial.lock'
        for raw in (b'', b'{"pid', b'[1, 2]', b'{"pid": 0}', b'{"pid": "12"}', b'{"pid": true}'):
            with self.subTest(raw=raw):
                path.write_bytes(raw)
                for name, live in READERS.items():
                    self.assertTrue(live(path), f'{name}: a body being written this instant')
                self.assertFalse(proclock.release(path, None), 'a fresh partial body is not abandoned')
                backdate(path, proclock.UNREADABLE_STALE_SECONDS + 5)
                for name, live in READERS.items():
                    self.assertFalse(live(path), f'{name}: an abandoned partial body')
                self.assertTrue(proclock.release(path, None))
                self.assertFalse(path.exists())
        path.write_bytes(b'{"pid')
        backdate(path, proclock.UNREADABLE_STALE_SECONDS + 5)
        self.assertIsNotNone(proclock.claim(path, 'over a partial lock'))
        proclock.release_all()

    def test_release_removes_only_the_lock_it_names(self):
        path = self.dir / 'replaced.lock'
        body = proclock.claim(path, 'first')
        other = json.dumps({'pid': 1, 'pid_started': 1, 'started_utc': utc(time.time()), 'key': 'other'}).encode()
        path.write_bytes(other)
        self.assertFalse(proclock.release(path, body))
        self.assertEqual(path.read_bytes(), other)
        self.assertEqual(proclock.held(), {}, 'released, whatever the file now holds')
        self.assertFalse(proclock.release(path, None), 'a readable lock is never removed as abandoned')
        self.assertTrue(proclock.release(path, other))
        self.assertFalse(path.exists())
        self.assertFalse(proclock.release(path, other))

    def test_release_never_removes_a_lock_that_replaced_the_one_it_judged(self):
        # Between release()'s read and its delete, another process puts a new lock at the path. On Windows
        # the release holds the file under an exclusive handle, so the replacement is refused; on POSIX it
        # judges a renamed copy, so the replacement lands and survives. Either way it is never deleted.
        path = self.dir / 'judged.lock'
        body = proclock.claim(path, 'judged')
        replacement = json.dumps({'pid': 1, 'pid_started': 1, 'started_utc': utc(time.time()), 'key': 'new'}).encode()
        attempts, real = [], proclock._wanted

        def judging(raw, expected, written):
            staged = self.dir / 'replacement.tmp'
            staged.write_bytes(replacement)
            try:
                os.replace(staged, path)
                attempts.append('replaced')
            except PermissionError:
                attempts.append('refused')
            return real(raw, expected, written)

        with patch.object(proclock, '_wanted', judging):
            removed = proclock.release(path, body)
        self.assertTrue(removed, 'the lock it named was removed')
        if os.name == 'nt':
            self.assertEqual(attempts, ['refused'])
            self.assertFalse(path.exists())
        if 'replaced' in attempts:
            self.assertEqual(path.read_bytes(), replacement, 'a lock that replaced the judged one was deleted')

    def test_racing_claims_over_a_dead_holders_lock_admit_exactly_one(self):
        path = self.dir / 'race.lock'
        child, _ = self.holder('proclock', path)
        self.gone(child)
        start = time.time() + 3
        racers = [self.spawn('-c', CHILD, APP, 'race', path, 15, start) for _ in range(6)]
        lines = [self.first_line(out, timeout=60) for _, out in racers]
        winners = [line for line in lines if line['body']]
        self.assertEqual(len(winners), 1, lines)
        self.assertEqual(path.read_bytes(), winners[0]['body'].encode('utf-8'))

    def test_every_module_takes_its_locks_through_proclock(self):
        import prewarm
        for module in (a, rc, drawer_sidecars, prewarm):
            self.assertIs(module.proclock, proclock, module.__name__)
        for module, name in ((a, 'MARKER_STALE_SECONDS'), (rc, 'LOCK_STALE_SECONDS'),
                             (drawer_sidecars, 'LOCK_STALE_SECONDS'), (drawer_sidecars, 'LOCK_GRACE_SECONDS')):
            self.assertFalse(hasattr(module, name), f'{module.__name__}.{name}: an age rule of its own')
        body = proclock.claim(self.dir / 'k.lock', 'k')
        self.assertEqual(set(json.loads(body)), {'pid', 'pid_started', 'started_utc', 'key'})
        state, drawer_body = drawer_sidecars._acquire(self.dir / 'd.lock')
        self.assertEqual(state, 'ours')
        self.assertEqual(set(json.loads(drawer_body)),
                         {'pid', 'pid_started', 'started_utc', 'key', 'started', 'created', 'token'})
        self.assertEqual(json.loads(drawer_body)['started'], proclock.own_started())
        proclock.release_all()
        # Lock code decides who builds, never what an answer is: it is outside every code identity.
        self.assertNotIn('proclock', rc.module_closure('graph_data'))
        self.assertIn('drawer_sidecars', rc.module_closure('graph_data'))

    def test_the_warmer_reports_only_live_foreign_builders(self):
        import prewarm
        root = self.root()
        live_path = lock_path(root, 'analysis')
        child, _ = self.holder('analysis', live_path)
        dead, _ = self.holder('proclock', live_path.with_name('dead.lock'))
        self.gone(dead)
        mine = proclock.claim(live_path.with_name('mine.lock'), 'mine')
        self.addCleanup(proclock.release_all)
        backdate(live_path, LATER)
        backend = type('Backend', (), {'analysis': a})()
        with patch.object(a.store, 'ROOT', root / 'query-cache'), \
                patch.object(proclock, '_now', lambda: time.time() + LATER):
            holders = prewarm.AppBackend.building_locks_held_elsewhere(backend)
        self.assertEqual([(item['pid'], item['key']) for item in holders], [(child.pid, 'analysis-key')])
        self.assertIsNotNone(mine)


# ----------------------------------------------------------------------------- processes this user cannot open
def unopenable_processes(folder):
    """{pid: (creation time in FILETIME ticks, image name)} of the running processes this user cannot open, found without
    proclock: psapi.EnumProcesses, OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION) answering
    ERROR_ACCESS_DENIED, and the creation time WMI reports (Win32_Process.CreationDate, to the microsecond).
    The PowerShell child writes to a real file and is waited on with a timeout (brain DE-026)."""
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    psapi = ctypes.WinDLL('psapi')
    pids, needed = (wintypes.DWORD * 16384)(), wintypes.DWORD()
    if not psapi.EnumProcesses(pids, ctypes.sizeof(pids), ctypes.byref(needed)):
        return {}
    denied = set()
    for pid in pids[:needed.value // 4]:
        handle = kernel.OpenProcess(0x1000, False, pid) if pid else None
        if handle:
            kernel.CloseHandle(handle)
        elif pid and ctypes.get_last_error() == 5:
            denied.add(pid)
    if not denied:
        return {}
    out = Path(folder) / 'wmi-processes.txt'
    script = ('Get-CimInstance Win32_Process | Where-Object { $_.CreationDate } | '
              'ForEach-Object { "$($_.ProcessId) $($_.CreationDate.ToFileTimeUtc()) $($_.Name)" }')
    with open(out, 'wb') as stream:
        child = subprocess.Popen(['powershell', '-NoProfile', '-NonInteractive', '-Command', script],
                                 stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
                                 creationflags=NO_WINDOW)
    try:
        child.wait(timeout=120)
    finally:
        kill_tree(child)
    created = {}
    for line in out.read_text(encoding='utf-8', errors='replace').splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit() and int(parts[0]) in denied:
            created[int(parts[0])] = (int(parts[1]), parts[2].strip())
    return created


@unittest.skipUnless(os.name == 'nt', 'OpenProcess access rights and the system process list')
class UnopenableProcessTests(Case):
    """Review round 1 of batch 1: a lock whose pid now names a process this user cannot open (a session-0
    service, csrss) was live forever ('its creation time cannot be read'). proclock now reads that
    creation time from the system's process list."""

    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            processes = unopenable_processes(folder)
        if not processes:
            raise unittest.SkipTest('this user can open every process here (an elevated session?)')
        # csrss, the verifier's case, when it is listed; else the oldest. Either one outlives the test.
        chosen = sorted(processes, key=lambda pid: (processes[pid][1].lower() != 'csrss.exe', processes[pid][0]))
        cls.pid = chosen[0]
        cls.created, cls.name = processes[cls.pid]

    def test_an_unopenable_process_has_the_creation_time_windows_reports(self):
        started = proclock.process_started(self.pid)
        self.assertIsInstance(started, int)
        self.assertLess(abs(started - self.created), 10,
                        f'pid {self.pid} ({self.name}): {started} against WMI {self.created} (to the microsecond)')
        self.assertEqual(proclock.process_started(self.pid), started)
        self.assertTrue(proclock.alive(self.pid, started))
        self.assertFalse(proclock.alive(self.pid, started - 1), 'another creation time is a recycled pid')

    def test_the_process_list_agrees_with_getprocesstimes_and_drops_an_exited_process(self):
        child = self.sleeper()
        for pid in (os.getpid(), child.pid):
            self.assertEqual(proclock.started_listed(pid), proclock.process_started(pid), pid)
        self.gone(child)
        self.assertIsNone(proclock.started_listed(child.pid))

    def test_a_dead_builders_lock_whose_pid_an_unopenable_process_now_holds_is_stale(self):
        # The verifier's case: a dead builder's lock whose pid Windows has since given to csrss or a service.
        # The bodies are built from WMI's creation time (to the microsecond), not from proclock's.
        created = (self.created - proclock.EPOCH_TICKS) / proclock.TICKS_PER_SECOND
        another = self.created - 10 ** 7   # the dead builder's creation time, a second earlier
        now = time.time()
        stale = {   # name: (body, age of the file in seconds)
            'new format, claimed a minute ago by another process': (
                {'pid': self.pid, 'pid_started': another, 'started_utc': utc(now - 60), 'key': 'k'}, 60),
            'new format, days old': (
                {'pid': self.pid, 'pid_started': another, 'started_utc': utc(now - 4 * 86400), 'key': 'k'}, 4 * 86400),
            'old analysis/cache format, written before this process existed': (
                {'pid': self.pid, 'started_utc': utc(created - 3600), 'key': 'k'}, 60),
            'old drawer format, claimed a minute ago by another process': (
                {'pid': self.pid, 'started': another, 'created': now - 60, 'token': 't'}, 60),
        }
        live = {
            'new format naming this very process': (
                {'pid': self.pid, 'pid_started': proclock.process_started(self.pid), 'started_utc': utc(now - 60),
                 'key': 'k'}, 60),
            'old format written while this process ran': (
                {'pid': self.pid, 'started_utc': utc(now - 60), 'key': 'k'}, 60),
        }
        path = self.dir / 'unopenable.lock'
        for label, bodies, expected in (('stale', stale, False), ('live', live, True)):
            for name, (body, age) in bodies.items():
                with self.subTest(case=f'{label}: {name}'):
                    path.write_bytes(json.dumps(body).encode('utf-8'))
                    backdate(path, age)
                    for reader, is_live in READERS.items():
                        self.assertEqual(is_live(path), expected, reader)
        path.write_bytes(json.dumps(stale['new format, claimed a minute ago by another process'][0]).encode('utf-8'))
        claimed = proclock.claim(path, 'taken over')
        self.assertIsNotNone(claimed, "a dead builder's lock must not hold its key forever")
        self.assertEqual(json.loads(path.read_bytes())['pid'], os.getpid())
        self.assertTrue(proclock.release(path, claimed))


class UnknownStartTests(Case):
    def test_a_lock_whose_holders_creation_time_cannot_be_read_ages_out_after_30_minutes(self):
        # Where no creation time is readable at all (the process list failed; POSIX without /proc), a
        # recycled pid cannot be told apart: the lock is live for UNKNOWN_START_STALE_SECONDS, then stale.
        child = self.sleeper()
        real = proclock.process_started
        path = self.dir / 'unknown.lock'
        limit = proclock.UNKNOWN_START_STALE_SECONDS
        now = time.time()
        bodies = {'new format': {'pid': child.pid, 'pid_started': 12345, 'started_utc': utc(now), 'key': 'k'},
                  'old drawer format': {'pid': child.pid, 'started': 0, 'created': now, 'token': 't'},
                  "no claim time: the file's last write": {'pid': child.pid, 'key': 'k'}}
        with patch.object(proclock, 'process_started', lambda pid: 0 if pid == child.pid else real(pid)):
            for name, body in bodies.items():
                with self.subTest(body=name):
                    path.write_bytes(json.dumps(body).encode('utf-8'))
                    for reader, live in READERS.items():
                        self.assertTrue(live(path), f'{reader}: a fresh lock of a running process')
                    with patch.object(proclock, '_now', lambda: time.time() + limit - 60):
                        for reader, live in READERS.items():
                            self.assertTrue(live(path), f'{reader}: younger than the limit')
                    backdate(path, limit + 60)
                    with patch.object(proclock, '_now', lambda: time.time() + limit + 60):
                        for reader, live in READERS.items():
                            self.assertFalse(live(path), f'{reader}: older than the limit')
        self.assertEqual(limit, 30 * 60)


# ----------------------------------------------------------------------------- a release meets a reader
def open_like_a_release(path):
    """A handle on `path` as proclock's release opens it on Windows: GENERIC_READ | DELETE, shared for reading
    only (_remove_if_windows). Close it with close_handle."""
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                   wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    handle = kernel.CreateFileW(str(path), 0x80000000 | 0x00010000, 0x1, None, 3, 0x80, None)
    if handle is None or handle == ctypes.c_void_p(-1).value:
        raise OSError(ctypes.get_last_error(), 'CreateFileW failed', str(path))
    return handle


def close_handle(handle):
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle(handle)


def leftovers():
    return getattr(proclock, 'leftovers', dict)()   # the code before round 2 kept no leftovers


@unittest.skipUnless(os.name == 'nt', 'Windows share modes: an open handle without FILE_SHARE_DELETE refuses a delete')
class ReleaseMeetsReaderTests(Case):
    """Review round 2 of batch 1: a release that met a reader in another process gave up after about 0.2 s,
    and the lock it left named a running process, so every other process judged it live for as long as that
    process ran. The reader in another process is a CHILD ('reader': as proclock reads a lock; 'oldreader': as
    a console on older code reads it, with open()), holding its handle open while this process releases."""

    def reader(self, mode, path, hold):
        child, out = self.spawn('-c', CHILD, APP, mode, path, hold)
        line = self.first_line(out)
        self.assertEqual(line['pid'], child.pid)
        self.assertEqual(line['body'].encode('utf-8'), path.read_bytes(), 'the reader read the lock')
        return child

    def wait_until(self, condition, seconds=10):
        """Polls `condition` without calling proclock, so only proclock's own background work can make it true."""
        deadline = time.monotonic() + seconds
        while not condition() and time.monotonic() < deadline:
            time.sleep(.05)
        return condition()

    def test_a_reader_never_stands_in_the_way_of_a_release(self):
        path = self.dir / 'read.lock'
        body = proclock.claim(path, 'read')
        child = self.reader('reader', path, 60)
        self.assertTrue(proclock.release(path, body), "a reader through proclock refused the release")
        self.assertFalse(path.exists())
        self.assertIsNone(proclock.inspect(path))
        self.assertEqual(leftovers(), {})
        self.assertIsNone(child.poll(), 'the reader still holds its handle')
        # And the other way round: while a release holds the file, a reader reads it and judges it.
        body = proclock.claim(path, 'again')
        handle = open_like_a_release(path)
        try:
            found = proclock.inspect(path)
        finally:
            close_handle(handle)
        self.assertIsNotNone(found.info, f'a reader failed while a release held the file: {found.reason}')
        self.assertEqual((found.live, found.reason), (True, 'held by this process'))
        self.assertTrue(proclock.release(path, body))

    def test_a_release_an_old_reader_refused_is_completed_once_the_reader_closes(self):
        path = self.dir / 'refused.lock'
        body = proclock.claim(path, 'refused')
        child = self.reader('oldreader', path, 3)
        self.assertFalse(proclock.release(path, body), 'an open() handle refuses the delete')
        self.assertTrue(path.exists())
        self.assertEqual(proclock.held(), {}, 'released, whatever the file')
        self.assertEqual(json.loads(path.read_bytes())['pid'], os.getpid())   # it still names a running process
        child.wait(timeout=30)   # the reader's handle closes as it exits
        self.assertTrue(self.wait_until(lambda: not path.exists()),
                        'the refused release was never completed: the lock stays live to every other process')
        self.assertTrue(self.wait_until(lambda: leftovers() == {}), leftovers())

    def test_a_refused_release_never_removes_a_lock_that_took_its_place(self):
        path = self.dir / 'changed.lock'
        body = proclock.claim(path, 'changed')
        child = self.reader('oldreader', path, 3)
        self.assertFalse(proclock.release(path, body))
        other = json.dumps({'pid': 1, 'pid_started': 1, 'started_utc': utc(time.time()), 'key': 'other'}).encode()
        with open(path, 'r+b') as stream:   # the file now holds another lock (the open() reader shares writing)
            stream.truncate(0)
            stream.write(other)
        child.wait(timeout=30)
        self.assertTrue(self.wait_until(lambda: leftovers() == {}), leftovers())
        time.sleep(.5)
        self.assertTrue(path.exists(), 'the retry removed a lock that was not the one it released')
        self.assertEqual(path.read_bytes(), other, 'the retry changed a lock that was not the one it released')


# ----------------------------------------------------------------------------- compatibility
# The rules of the previous release, verbatim (analysis_data._inspect/_alive and response_cache's, which
# were the same rule; drawer_sidecars._stale/process_started), as the oracle of what a console still on
# that code does with a lock this code writes, and the reverse.
def old_alive(pid, started):
    if os.name != 'nt':
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return ctypes.get_last_error() == 5
    try:
        code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True
        if code.value != 259:
            return False
        times = [wintypes.FILETIME() for _ in range(4)]
        if kernel.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            created = ((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime) / 1e7 - 11644473600
            if created > started + 2:
                return False
        return True
    finally:
        kernel.CloseHandle(handle)


def old_inspect(path, stale_seconds=30 * 60):
    try:
        raw = path.read_bytes()
        written = path.stat().st_mtime
    except FileNotFoundError:
        return None
    except OSError:
        return True, None
    now = time.time()
    try:
        info = json.loads(raw)
        pid, started = int(info['pid']), datetime.fromisoformat(info['started_utc']).timestamp()
    except (ValueError, KeyError, TypeError, OverflowError, OSError):
        return now - written < 60, raw
    if (not 0 < pid < 2**32 or pid == os.getpid()
            or max(now - started, now - written) > stale_seconds):
        return False, raw
    return old_alive(pid, started), raw


def old_drawer_stale(path, stale_seconds=30 * 60, grace_seconds=10):
    try:
        info = json.loads(path.read_text(encoding='utf-8'))
        created, pid = float(info['created']), int(info['pid'])
    except FileNotFoundError:
        return False
    except (OSError, ValueError, KeyError, TypeError):
        try:
            return time.time() - path.stat().st_mtime > grace_seconds
        except OSError:
            return False
    if time.time() - created > stale_seconds:
        return True
    started = proclock.process_started(pid)   # the old drawer probe: same values (tests above)
    if started is None:
        return True
    recorded = info.get('started')
    return bool(recorded) and bool(started) and started != recorded


class CompatibilityTests(Case):
    def test_a_console_on_the_old_code_respects_a_lock_this_code_wrote(self):
        root = self.dir / 'root'
        for mode, old_live in (('analysis', lambda p: old_inspect(p)[0]), ('cache', lambda p: old_inspect(p)[0]),
                               ('drawer', lambda p: not old_drawer_stale(p))):
            with self.subTest(writer=mode):
                path = lock_path(root, mode)
                child, body = self.holder(mode, path)
                backdate(path, 120)   # past both old grace periods for an unreadable body
                self.assertTrue(old_live(path), f'old code broke a live {mode} lock written by new code')
                self.gone(child)
                self.assertFalse(old_live(path), f'old code kept a dead {mode} lock written by new code')

    def test_this_code_respects_a_lock_the_old_code_wrote(self):
        child = self.sleeper()
        time.sleep(.3)   # an old lock names a moment after its holder was created
        path = self.dir / 'old.lock'
        bodies = {'old analysis/cache': {'pid': child.pid, 'started_utc': utc(time.time()), 'key': 'old'},
                  'old drawer': {'pid': child.pid, 'started': proclock.process_started(child.pid),
                                 'created': time.time(), 'token': 'old'}}
        for name, body in bodies.items():
            with self.subTest(body=name):
                path.write_bytes(json.dumps(body).encode('utf-8'))
                for reader, live in READERS.items():
                    self.assertTrue(live(path), reader)
                self.assertIsNone(proclock.claim(path))
        self.gone(child)
        for name, body in bodies.items():
            with self.subTest(body=name, holder='gone'):
                path.write_bytes(json.dumps(body).encode('utf-8'))
                for reader, live in READERS.items():
                    self.assertFalse(live(path), reader)


if __name__ == '__main__':
    unittest.main()

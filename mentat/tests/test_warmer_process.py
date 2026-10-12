"""The warmer process: below-normal priority without EcoQoS (review finding H1), for its native scanners too
(review round 2 of batch 1), and a prompt exit once its console is gone, even while a build thread is still
running (review finding H3) or the warmer itself is in a synchronous step that never polls (review round 1 of
batch 1: the watchdog, Warmer._watch_parent). A prompt exit skips a builder's `finally`, so the warmer sweeps
the projection files an ended build left behind (review round 2 of batch 1: sweep_pending).

PREWARM_UNDER_TEST points this suite, and its children, at a variant of prewarm.py (the negative controls).
Children write to real files and are killed and waited on with a timeout (brain DE-026).
"""
import contextlib
import hashlib
import importlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock
import uuid

TESTS = Path(__file__).resolve().parent
APP = TESTS.parent / 'app'
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))
PREWARM = Path(os.environ.get('PREWARM_UNDER_TEST') or APP / 'prewarm.py')
_spec = importlib.util.spec_from_file_location('prewarm', str(PREWARM))
prewarm = importlib.util.module_from_spec(_spec)
sys.modules['prewarm'] = prewarm
_spec.loader.exec_module(prewarm)
import proclock  # noqa: E402
import python_child  # noqa: E402

WINDOWS = os.name == 'nt'
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
BELOW_NORMAL = 0x00004000
UNTHROTTLED = {'control': 1, 'state': 0}   # ControlMask EXECUTION_SPEED, StateMask 0: EcoQoS off

# A warmer-like process: prewarm under test lowers its own priority, as Warmer.run does, then reports.
PRIORITY_CHILD = r'''
import importlib.util, json, sys, time
spec = importlib.util.spec_from_file_location('prewarm', sys.argv[1])
module = importlib.util.module_from_spec(spec)
sys.modules['prewarm'] = module
spec.loader.exec_module(module)
print(json.dumps({'set': module.set_below_normal_priority(), 'throttling': module.power_throttling()}), flush=True)
time.sleep(60)
'''

# An orphaned warmer: prewarm.main as the CLI runs it, a Warmer whose task starts a long build on a
# non-daemon pool thread (analysis_data._pool) under a cross-process marker (analysis_data._run), and
# polls until its console is gone (warm_insights). The build would run for ten minutes.
ORPHAN_CHILD = r'''
import sys, threading, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
tests, state_root, marker, parent = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3]), int(sys.argv[4])
sys.path.insert(0, tests)
import test_prewarm as base   # prewarm from PREWARM_UNDER_TEST or the app folder; FakeBackend
prewarm = base.prewarm
import proclock
pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='mentat-insights')


class Backend(base.FakeBackend):
    def warm(self, task, deadline=None, should_stop=None, retry=False, sleep=None):
        claimed = threading.Event()

        def build():
            body = proclock.claim(marker, 'build')
            claimed.set()
            try:
                end = time.monotonic() + 600
                while time.monotonic() < end:
                    sum(range(20000))
            finally:
                proclock.release(marker, body)
        pool.submit(build)
        claimed.wait(60)
        while True:
            if should_stop and should_stop():
                raise prewarm.Stop('parent_gone', 'The warmer is stopping.')
            time.sleep(0.1)


def op_run(options):
    state = prewarm.StateDir(Path(options.state_root))
    warmer = prewarm.Warmer(Backend(state_root, datasets=('Alpha',)), state, op='run', parent_pid=options.parent_pid,
                            once=True, set_priority=False, free_bytes=lambda: 8 * 1024 ** 3,
                            console_probe=lambda url: (False, {}), code_stamps=lambda: {},
                            emitter=lambda *args, **fields: None)
    return warmer.run()


prewarm.OPS['run'] = op_run
sys.exit(prewarm.main(['run', '--parent-pid', str(parent), '--state-root', str(state_root)], hard_exit=True))
'''

# An orphaned warmer in a synchronous step: its task builds on the warmer's own thread for ten minutes and
# never polls, as a query view computed in-line, planning, or a comparison-flows or drawer build does.
SYNC_CHILD = ORPHAN_CHILD.replace(r'''        pool.submit(build)
        claimed.wait(60)
        while True:
            if should_stop and should_stop():
                raise prewarm.Stop('parent_gone', 'The warmer is stopping.')
            time.sleep(0.1)
''', r'''        build()
        return {'status': 'ready'}
''')
assert SYNC_CHILD != ORPHAN_CHILD

# A warmer (Warmer.run with set_priority, as the CLI warmer runs it) whose task starts a native scanner as
# native_scan._scan does, with one scanner already running from before the run lowered its priority. The
# interpreter stands in for mentat-scan.exe: like the scanner it waits on its stdin. It reports how Windows
# schedules both scanners and itself.
NATIVE_CHILD = r'''
import ctypes, json, sys
from pathlib import Path
tests, state_root, result = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
sys.path.insert(0, tests)
import test_prewarm as base   # prewarm from PREWARM_UNDER_TEST or the app folder; FakeBackend; the app on sys.path
prewarm = base.prewarm
import native_scan
kernel = ctypes.WinDLL('kernel32', use_last_error=True)
kernel.GetPriorityClass.restype = ctypes.c_ulong
kernel.GetPriorityClass.argtypes = (ctypes.c_void_p,)
kernel.GetCurrentProcess.restype = ctypes.c_void_p
stand_in = Path(sys.executable)
early = native_scan.Worker(stand_in)
native_scan._all.append(early)


def scheduling(handle):
    return {'throttling': prewarm.power_throttling(handle), 'priority': kernel.GetPriorityClass(handle)}


class Backend(base.FakeBackend):
    def warm(self, task, deadline=None, should_stop=None, retry=False, sleep=None):
        worker = native_scan.Worker(stand_in)
        try:
            result.write_text(json.dumps({'started_during_the_run': scheduling(worker.process._handle),
                                          'running_before_the_run': scheduling(early.process._handle),
                                          'warmer': scheduling(kernel.GetCurrentProcess())}), encoding='utf-8')
        finally:
            close(worker)
        return super().warm(task, deadline, should_stop, retry, sleep)


def close(worker):
    try:
        worker.close()
    except Exception:   # a throttled stand-in can take seconds to be torn down; the test kills the tree anyway
        pass


warmer = prewarm.Warmer(Backend(state_root, datasets=('Alpha',)), prewarm.StateDir(state_root), op='run', once=True,
                        max_tasks=1, set_priority=True, free_bytes=lambda: 8 * 1024 ** 3,
                        console_probe=lambda url: (False, {}), code_stamps=lambda: {},
                        emitter=lambda *args, **fields: None)
report = warmer.run()
print(json.dumps({'status': report['status']}), flush=True)
native_scan._all.remove(early)
close(early)
'''

# A projection builder of another process: contribution_query_store.view's order (the key's <key>.lock, then its
# <key>.<uuid>.pending file under it, renamed to <key>.sqlite before the lock is let go), with a file old enough
# for the sweep to consider it. It reports whether its file was still there when it finished.
BUILDER_CHILD = r'''
import os, sys, time
from pathlib import Path
app, directory, key, hold = sys.argv[1], Path(sys.argv[2]), sys.argv[3], float(sys.argv[4])
sys.path.insert(0, app)
import contribution_query_store as store
pending = directory / (key + '.' + 'b' * 32 + '.pending')
with store._lock(directory / (key + '.lock')):
    pending.write_bytes(b'a projection being built')
    old = time.time() - 3600
    os.utime(pending, (old, old))
    print('building', flush=True)
    time.sleep(hold)
    intact = pending.exists()
    if intact:
        os.replace(pending, directory / (key + '.sqlite'))
print('finished' if intact else 'its file was removed while it built', flush=True)
'''


def kill_tree(child):
    if child.poll() is None:
        if WINDOWS:
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


def priority_class(handle):
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetPriorityClass.restype = wintypes.DWORD
    kernel.GetPriorityClass.argtypes = (wintypes.HANDLE,)
    return kernel.GetPriorityClass(int(handle))


class Case(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.count = 0

    def spawn(self, arguments, **kwargs):
        """(child, stdout file, stderr file); the child is killed at cleanup. A child of this interpreter
        is one process (python_child): these tests read the interpreter's own pid, handle and scheduling,
        which Mentat's venv's sys.executable (CPython's venv launcher, a second process) would not give."""
        self.count += 1
        out, err = self.dir / f'child-{self.count}.out', self.dir / f'child-{self.count}.err'
        arguments, kwargs['env'] = python_child.argv(arguments, kwargs.get('env'))
        with open(out, 'wb') as stdout, open(err, 'wb') as stderr:
            child = subprocess.Popen(arguments, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, **kwargs)
        self.addCleanup(kill_tree, child)
        return child, out, err

    def first_line(self, out, err, timeout=60):
        return json.loads(self.first_line_text(out, err, timeout))

    def first_line_text(self, out, err, timeout=60):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            text = out.read_text(encoding='utf-8', errors='replace')
            if '\n' in text:
                return text.splitlines()[0].strip()
            time.sleep(.05)
        self.fail('the child printed nothing: ' + err.read_text(encoding='utf-8', errors='replace')[-2000:])


@unittest.skipUnless(WINDOWS, 'Windows priority classes and power throttling')
class SchedulingTests(Case):
    def test_a_process_has_no_explicit_policy_until_one_is_set(self):
        # The read-back itself: a below-normal child nobody configured has no explicit policy.
        child, _, _ = self.spawn([sys.executable, '-c', 'import time; time.sleep(60)'],
                                 creationflags=BELOW_NORMAL | NO_WINDOW)
        self.assertEqual(prewarm.power_throttling(child._handle), {'control': 0, 'state': 0})
        self.assertEqual(priority_class(child._handle), BELOW_NORMAL)

    def test_the_warmer_runs_below_normal_with_ecoqos_off(self):
        child, out, err = self.spawn([sys.executable, '-c', PRIORITY_CHILD, str(PREWARM)], creationflags=NO_WINDOW)
        line = self.first_line(out, err)
        self.assertEqual(line['set'], {'priority': 'below_normal', 'ecoqos_off': True})
        self.assertEqual(line['throttling'], UNTHROTTLED)
        self.assertEqual(prewarm.power_throttling(child._handle), UNTHROTTLED)   # read back by another process
        self.assertEqual(priority_class(child._handle), BELOW_NORMAL)

    def test_the_console_turns_ecoqos_off_for_the_warmer_it_launches(self):
        # The warmer sets it itself only after its imports; the console sets it at launch. A stand-in
        # child that never sets anything shows the console's call alone.
        launched, real = [], subprocess.Popen

        def popen(command, **kwargs):
            # launch_warmer's own interpreter and environment, so under Mentat's venv too the handle it sets
            # EcoQoS on is the stand-in interpreter's.
            child = real([command[0], '-c', 'import time; time.sleep(60)'], **kwargs)
            launched.append((command, child))
            return child

        with mock.patch.object(prewarm.subprocess, 'Popen', popen), mock.patch.object(prewarm, 'emit') as emitted:
            pid = prewarm.launch_warmer(data_root=self.dir / 'data', read_only=False, console_port=8899,
                                        state_root=self.dir / 'state')
        self.assertEqual(len(launched), 1)
        command, child = launched[0]
        self.addCleanup(kill_tree, child)
        self.assertEqual((pid, command[2]), (child.pid, 'run'))
        self.assertEqual(prewarm.power_throttling(child._handle), UNTHROTTLED)
        self.assertEqual(priority_class(child._handle), BELOW_NORMAL)
        self.assertIs(emitted.call_args.kwargs['metadata']['ecoqos_off'], True)

    def test_the_warmers_native_scanners_run_without_ecoqos_too(self):
        # Review round 2 of batch 1: a scanner the warmer starts inherits its below-normal priority but not its
        # EcoQoS opt-out (0.08-0.45 of a core in a spin; a comparison build's scan timed out after 120 s).
        result = self.dir / 'native.json'
        child, out, err = self.spawn([sys.executable, '-c', NATIVE_CHILD, str(TESTS), str(self.dir / 'state'),
                                      str(result)], creationflags=NO_WINDOW)
        self.assertEqual(self.first_line(out, err, timeout=120), {'status': 'success'},
                         err.read_text(encoding='utf-8', errors='replace')[-2000:])
        seen = json.loads(result.read_text(encoding='utf-8'))
        self.assertEqual(seen['warmer'], {'throttling': UNTHROTTLED, 'priority': BELOW_NORMAL})
        self.assertEqual(seen['started_during_the_run'], {'throttling': UNTHROTTLED, 'priority': BELOW_NORMAL},
                         'a scanner the warmer starts must run without EcoQoS too')
        self.assertEqual(seen['running_before_the_run']['throttling'], UNTHROTTLED,
                         'a scanner already running when the warmer lowers its priority must be opted out too')


def projection_key(number):
    return hashlib.sha256(str(number).encode()).hexdigest()


def backdate(path, seconds):
    moment = time.time() - seconds
    os.utime(path, (moment, moment))


class PendingSweepTests(Case):
    """Review round 2 of batch 1: the warmer's prompt exit (exit_now, the watchdog) skips the `finally` that
    removes a projection build's <key>.<uuid>.pending file (a query view is 409-433 MB); the warmer sweeps them."""

    def store(self, root):
        import contribution_query_store   # its own _lock: the builders' lock, taken through the same function
        return types.SimpleNamespace(ROOT=root, _lock=contribution_query_store._lock)

    def test_the_sweep_removes_only_files_no_live_builder_holds(self):
        root = self.dir / 'cache'
        views, insights, responses = root / 'views', root / 'insights-views', root / 'responses' / 'insights'
        for folder in (views, insights, responses):
            folder.mkdir(parents=True)
        orphans = [views / f'{projection_key(1)}.{uuid.uuid4().hex}.pending',
                   insights / f'{projection_key(2)}.{uuid.uuid4().hex}.pending']
        young = views / f'{projection_key(4)}.{uuid.uuid4().hex}.pending'
        others = [views / f'{projection_key(1)}.sqlite', views / f'{projection_key(1)}.json',
                  views / f'{projection_key(1)}.lock', views / 'notakey.pending', views / f'{projection_key(5)}.pending',
                  responses / f'{projection_key(6)}.{uuid.uuid4().hex}.pending']
        for path in (*orphans, young, *others):
            path.write_bytes(b'0123456789')
        for path in (*orphans, *others):
            backdate(path, 3600)
        builder, out, err = self.spawn([sys.executable, '-c', BUILDER_CHILD, str(APP), str(views), projection_key(3),
                                        '3'], creationflags=NO_WINDOW)
        self.assertEqual(self.first_line_text(out, err), 'building')
        began = time.monotonic()
        result = prewarm.sweep_pending(self.store(root))
        waited = time.monotonic() - began
        builder.wait(timeout=60)
        lines = out.read_text(encoding='utf-8').splitlines()
        self.assertEqual(lines[-1].strip(), 'finished', "the sweep removed a live build's file")
        self.assertTrue((views / f'{projection_key(3)}.sqlite').exists())
        self.assertGreater(waited, 1.0, 'the sweep did not wait for the live build to let its key go')
        self.assertEqual(sorted(result['removed']), sorted(map(str, orphans)))
        self.assertEqual((result['bytes'], result['errors']), (20, []))
        for path in orphans:
            self.assertFalse(path.exists(), path.name)
        self.assertTrue(young.exists(), f'a file younger than {prewarm.PENDING_MIN_AGE_SECONDS} s is left alone')
        for path in others:
            self.assertTrue(path.exists(), f'{path.parent.name}/{path.name} is no projection build file')

    def test_a_run_warmer_sweeps_when_it_starts(self):
        sys.path.insert(0, str(TESTS))
        self.addCleanup(sys.path.remove, str(TESTS))
        base = importlib.import_module('test_prewarm')   # FakeBackend; it loads its own copy of prewarm
        sys.modules['prewarm'] = prewarm
        root = self.dir / 'cache'
        (root / 'views').mkdir(parents=True)
        orphan = root / 'views' / f'{projection_key(7)}.{uuid.uuid4().hex}.pending'
        orphan.write_bytes(b'left by an ended build')
        backdate(orphan, 3600)
        backend = base.FakeBackend(self.dir, datasets=('Alpha',))
        backend.store = self.store(root)
        events = []
        warmer = prewarm.Warmer(backend, prewarm.StateDir(self.dir / 'state'), op='run', once=True,
                                set_priority=False, free_bytes=lambda: 8 * 1024 ** 3,
                                console_probe=lambda url: (False, {}), code_stamps=lambda: {},
                                emitter=lambda kind, status, summary, **fields: events.append((kind, status, summary)))
        result = {}
        runner = threading.Thread(target=lambda: result.update(report=warmer.run()), daemon=True)
        runner.start()
        runner.join(60)
        self.assertEqual(result.get('report', {}).get('status'), 'success', result)
        deadline = time.monotonic() + 10
        while orphan.exists() and time.monotonic() < deadline:
            time.sleep(.05)
        self.assertFalse(orphan.exists(), 'the warmer never swept the file an ended build left')
        self.assertIn(('mentat.prewarm.sweep', 'completed'), [(kind, status) for kind, status, _ in events])


class OrphanTests(Case):
    def assert_orphan_exits_promptly(self, script, cause):
        """Run `script` (a warmer-like child of the console stand-in), kill the console once the child's build
        holds its marker, and check the child ends within seconds as a parent_gone exit."""
        console, _, _ = self.spawn([sys.executable, '-c', 'import time; time.sleep(120)'], creationflags=NO_WINDOW)
        deadline = time.monotonic() + 30
        while not proclock.process_started(console.pid) and time.monotonic() < deadline:
            time.sleep(.02)
        marker = self.dir / 'building' / 'build.lock'
        state_root = self.dir / 'state'
        environment = {**os.environ, 'GLASSBOX_EVENTS_DIR': str(self.dir / 'events')}
        warmer, out, err = self.spawn([sys.executable, '-c', script, str(TESTS), str(state_root), str(marker),
                                       str(console.pid)], creationflags=NO_WINDOW, env=environment)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            with contextlib.suppress(OSError, ValueError):
                if json.loads(marker.read_bytes())['pid'] == warmer.pid:
                    break
            self.assertIsNone(warmer.poll(), err.read_text(encoding='utf-8', errors='replace')[-2000:])
            time.sleep(.05)
        else:
            self.fail('the build never started: ' + err.read_text(encoding='utf-8', errors='replace')[-2000:])
        time.sleep(2.5)   # more than two watchdog checks while the console runs
        self.assertIsNone(warmer.poll(), 'the warmer must run while its console does')
        kill_tree(console)
        began = time.monotonic()
        try:
            code = warmer.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.fail(f'the orphaned warmer outlived its console by 30 s: {cause} kept it alive')
        self.assertLess(time.monotonic() - began, 10)
        self.assertEqual(code, 0, err.read_text(encoding='utf-8', errors='replace')[-2000:])
        reports = [line for line in out.read_text(encoding='utf-8').splitlines() if line.startswith('{')]
        self.assertEqual(len(reports), 1, 'the op prints exactly one JSON object')
        report = json.loads(reports[0])
        self.assertEqual((report['status'], report['metrics']['root_cause_hint']), ('success', 'parent_gone'))
        self.assertFalse(marker.exists(), 'the build marker the warmer held must be released before it exits')
        lock = json.loads((state_root / 'prewarm' / 'warmer.lock').read_text(encoding='utf-8'))
        self.assertEqual(lock['pid'], warmer.pid)
        self.assertIn('released_utc', lock)
        rows = prewarm.Ledger(prewarm.StateDir(state_root)).read()
        interrupted = [row for row in rows if row.get('root_cause_hint') == 'parent_gone' and row.get('task_id')]
        self.assertTrue(interrupted and all(row['outcome'] == 'failed' for row in interrupted), rows)
        self.assertIn('exiting now after parent_gone', err.read_text(encoding='utf-8', errors='replace'))

    def test_an_orphaned_warmer_exits_within_seconds_while_its_build_thread_runs(self):
        self.assert_orphan_exits_promptly(ORPHAN_CHILD, 'its build thread')

    def test_an_orphaned_warmer_exits_within_seconds_during_a_synchronous_step(self):
        # Review round 1 of batch 1: the verifier's kills of a warmer mid-task (a query view computed in-line)
        # and mid-plan ended it only when the step did, 38.2 s and 14.8 s later.
        self.assert_orphan_exits_promptly(SYNC_CHILD, 'a synchronous step')

    def test_only_the_cli_warmer_watches_its_console_from_a_second_thread(self):
        # In-process (the console's own warm, tests) nothing ends the process: a Warmer whose console goes
        # away during a synchronous step finishes the step and stops through its own guard.
        sys.path.insert(0, str(TESTS))
        self.addCleanup(sys.path.remove, str(TESTS))
        base = importlib.import_module('test_prewarm')   # FakeBackend; it loads its own copy of prewarm
        sys.modules['prewarm'] = prewarm                  # this suite's module under test stays the one imported
        gone, threads = threading.Event(), []

        class Backend(base.FakeBackend):
            def warm(self, task, deadline=None, should_stop=None, retry=False, sleep=None):
                gone.set()
                time.sleep(2.5 * prewarm.PARENT_WATCH_SECONDS)
                threads.extend(thread.name for thread in threading.enumerate())
                return super().warm(task, deadline, should_stop, retry, sleep)

        state = prewarm.StateDir(self.dir / 'state')
        warmer = prewarm.Warmer(Backend(self.dir, datasets=('Alpha',)), state, op='run', parent_pid=os.getpid(),
                                parent_alive=lambda: not gone.is_set(), once=True, set_priority=False,
                                free_bytes=lambda: 8 * 1024 ** 3, console_probe=lambda url: (False, {}),
                                code_stamps=lambda: {}, emitter=lambda *args, **fields: None)
        result = {}
        runner = threading.Thread(target=lambda: result.update(report=warmer.run()), daemon=True)
        with mock.patch.object(prewarm, 'exit_now') as ending, mock.patch.object(prewarm.os, '_exit') as hard:
            runner.start()
            runner.join(20)
        ending.assert_not_called()
        hard.assert_not_called()
        self.assertNotIn('mentat-prewarm-parent-watch', threads)
        self.assertFalse(runner.is_alive(), 'the run was left blocked by a watchdog that ended it')
        self.assertEqual(result['report']['metrics']['root_cause_hint'], 'parent_gone')
        self.assertEqual(result['report']['metrics']['succeeded'], 1, 'the step in progress finishes')
        # main(hard_exit=True) marks the process as the CLI warmer only while its op runs.
        seen = []
        ops = {'run': lambda options: (seen.append(prewarm._CLI_RUN),
                                       prewarm.op_result('success', 'done', metrics={'root_cause_hint': None}))[1]}
        with mock.patch.dict(prewarm.OPS, ops), mock.patch.object(prewarm, 'exit_now'), \
                contextlib.redirect_stdout(io.StringIO()):
            prewarm.main(['run'])
            prewarm.main(['run'], hard_exit=True)
        self.assertIsNone(seen[0])
        self.assertIsNotNone(seen[1])
        self.assertIsNone(prewarm._CLI_RUN)

    def test_main_never_ends_an_in_process_caller(self):
        stopped = {'run': lambda options: prewarm.op_result('success', 'stopped', metrics={'root_cause_hint': 'parent_gone'})}
        with mock.patch.dict(prewarm.OPS, stopped), mock.patch.object(prewarm, 'exit_now') as ending, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(prewarm.main(['run']), 0)
            ending.assert_not_called()
            prewarm.main(['run'], hard_exit=True)
            ending.assert_called_once_with(0, 'parent_gone', None)
            ending.reset_mock()
            finished = {'run': lambda options: prewarm.op_result('success', 'done', metrics={'root_cause_hint': None})}
            with mock.patch.dict(prewarm.OPS, finished):
                prewarm.main(['run'], hard_exit=True)
            ending.assert_not_called()


if __name__ == '__main__':
    unittest.main()

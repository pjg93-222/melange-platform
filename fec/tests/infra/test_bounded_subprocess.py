# -*- coding: utf-8 -*-
"""Contract suite for fec/bounded_subprocess.py and its three production call sites.

On 2026-09-19 a kernel run sat 14 minutes because one suite's child froze inside
``import scipy`` -- no CPU, no I/O, no output -- and nothing in the launch path had a
timeout.  The suite itself was fixed that day; the three launchers that remained unbounded
are fec/run_pipeline.py's chain-step launch (where a frozen child ALSO holds
.pipeline_run.lock for as long as it lasts), fec/tests/run_tests.py's suite launch, and
fec/weekly_refresh.py's step() -- the UNATTENDED Sunday refresh, whose own kill was an
unbounded taskkill followed by an unbounded ``proc.wait()``.  This suite pins the shared
helper they now all three go through.

What it pins: the DE-026 shape (Popen + REAL files + wait(timeout) + kill, never
``subprocess.run(capture_output=True, timeout=...)``, whose timeout path re-enters
communicate() and can block); text identical to the text-mode capture it replaced
(utf-8 with replacement, universal newlines); 10 MB across the two streams without a
deadlock; a timeout as a FAILURE WITH ITS OWN NAME (returncode 124, a note carrying the
pid, that note at the end of stderr) rather than a silent skip; the process TREE dying
with the child, which is the orphan case taskkill /T exists for; the launch flags and the
``on_start`` adoption hook the weekly refresh needs; and all three call sites --
run_pipeline driven for real with a fake one-step chain, weekly_refresh's step() and
_kill_live_child exec'd from their REAL source against a sandbox (importing that script
runs the whole refresh), run_tests pinned as text because importing it executes the
whole kernel.

Synthetic only: every child is a tiny ``python -c`` program or a script written into a
tempfile.mkdtemp().  No producer runs, no pipeline lock is taken, no Glass Box event
reaches the real stream (``_gb`` and ``_emit`` are recorders), and nothing outside the
temp tree is written.  Whole suite well under 120 s.

Plain unittest, no dependencies:  python fec/tests/infra/test_bounded_subprocess.py
"""
import ast
import contextlib
import datetime
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
FEC = os.path.dirname(os.path.dirname(_HERE))
_MODULE_PATH = os.path.normpath(os.path.join(_HERE, os.pardir, os.pardir, 'bounded_subprocess.py'))
_RUN_PIPELINE_PATH = os.path.join(FEC, 'run_pipeline.py')
_RUN_TESTS_PATH = os.path.join(FEC, 'tests', 'run_tests.py')
_WEEKLY_REFRESH_PATH = os.path.join(FEC, 'weekly_refresh.py')


def _load_module(name, path):
    """Load by file path so the suite depends on neither sys.path nor the runner."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BS = _load_module('bounded_subprocess_under_test', _MODULE_PATH)
sys.path.insert(0, FEC)   # compute_engine imports the flat `paths` module; weekly_refresh's step() calls compute_engine.launch / reap
try:
    CE = _load_module('compute_engine_under_test', os.path.join(FEC, 'compute_engine.py'))
finally:
    sys.path.remove(FEC)


def _read_source(path):
    """Read a source file as text with its EOLs normalised.

    Both call sites are PURE_CRLF on disk (the 2026-09-19 census: 312 of 2,892 tracked
    text files are MIXED, and a pin that spans a line break must not depend on which).
    """
    with open(path, 'rb') as handle:
        return handle.read().decode('utf-8').replace('\r\n', '\n').replace('\r', '\n')


def _process_alive(pid):
    """True while the OS still knows this pid.

    Never ``os.kill(pid, 0)``: on Windows os.kill TERMINATES the target for any signal
    that is not CTRL_C/CTRL_BREAK, which would make the liveness probe the killer and
    let a broken kill_tree pass.  Both probes are themselves bounded.
    """
    if sys.platform == 'win32':
        probe = BS.run_bounded(['tasklist', '/FI', 'PID eq %d' % pid, '/NH'], timeout=60)
        return str(pid) in probe.stdout
    probe = BS.run_bounded(['ps', '-p', str(pid)], timeout=60)
    return probe.returncode == 0


def _py(code):
    return [sys.executable, '-c', code]


class TempTreeCase(unittest.TestCase):
    """One mkdtemp per test; nothing is written anywhere else."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='bounded_subprocess_tests_')
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def write_script(self, name, body):
        path = os.path.join(self.tmp, name)
        with open(path, 'w', encoding='utf-8') as handle:
            handle.write(body)
        return path

    def heartbeat_fixture(self):
        """A child that spawns a grandchild which says it is alive by growing a file.

        The heartbeat loop is BOUNDED at 60 s: if the tree kill ever regresses, this suite
        must report it, not leave a runaway process behind on the box it ran on.
        """
        beat = os.path.join(self.tmp, 'heartbeat.txt')
        self.write_script(
            'grandchild.py',
            "import sys,time\n"
            "for _ in range(300):\n"
            "    with open(sys.argv[1],'a') as handle: handle.write('beat\\n')\n"
            "    time.sleep(0.2)\n")
        parent = self.write_script(
            'parent.py',
            "import os,subprocess,sys,time\n"
            "grandchild=os.path.join(os.path.dirname(os.path.abspath(__file__)),'grandchild.py')\n"
            "subprocess.Popen([sys.executable,grandchild,sys.argv[1]])\n"
            "time.sleep(120)\n")
        return parent, beat

    def wait_for_heartbeat(self, beat, seconds=10):
        """Block until the grandchild is demonstrably alive, so a kill proves something."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if os.path.exists(beat) and os.path.getsize(beat) > 0:
                return True
            time.sleep(0.05)
        return False

    def assert_heartbeat_stopped(self, beat):
        first = os.path.getsize(beat)
        self.assertGreater(first, 0, 'the grandchild never ran, so this proves nothing')
        time.sleep(1.5)
        self.assertEqual(first, os.path.getsize(beat),
                         'the grandchild outlived the kill: an orphan holding files and locks')


# --------------------------------------------------------------------- [1-5] the healthy path

class BoundedRunCapture(TempTreeCase):

    def test_1_healthy_child_returns_both_streams_separately(self):
        result = BS.run_bounded(
            _py("import sys;sys.stdout.write('on stdout');sys.stderr.write('on stderr')"),
            timeout=60)
        self.assertEqual(0, result.returncode)
        self.assertEqual('on stdout', result.stdout)
        self.assertEqual('on stderr', result.stderr)
        self.assertFalse(result.timed_out)
        self.assertEqual('', result.timeout_note)
        self.assertGreater(result.seconds, 0)
        self.assertIsInstance(result.pid, int)
        self.assertGreater(result.pid, 0)

    def test_2_exit_codes_are_preserved_including_the_tolerated_2(self):
        # The chain's tolerance rule keys on exactly 2 (run_pipeline.py TOLERANT_STEPS), so a
        # helper that flattened exit codes would silently widen or close that door.
        self.assertEqual(3, BS.run_bounded(_py('import sys;sys.exit(3)'), timeout=60).returncode)
        self.assertEqual(2, BS.run_bounded(_py('import sys;sys.exit(2)'), timeout=60).returncode)

    def test_3_text_is_what_the_old_text_mode_capture_produced(self):
        # capture_output=True,text=True,encoding='utf-8',errors='replace' meant: decode with
        # replacement, then universal newlines.  b'a\r\nb\rc\n' came back 'a\nb\nc\n', and an
        # invalid byte came back as U+FFFD rather than raising.  Both consumers print this text.
        result = BS.run_bounded(
            _py("import sys;sys.stdout.buffer.write(b'a\\r\\nb\\rc\\n\\xff');"
                "sys.stdout.buffer.flush()"),
            timeout=60)
        self.assertEqual('a\nb\nc\n�', result.stdout)

    def test_4_five_megabytes_on_each_stream_does_not_deadlock(self):
        # The reason the streams are REAL FILES and not pipes: a pipe that fills blocks the
        # child until someone drains it, and draining it on the timeout path is exactly the
        # re-entrant communicate() DE-026 forbids.  Chain steps do print megabytes.
        block = 1 << 16
        blocks = 80
        result = BS.run_bounded(
            _py("import sys\n"
                "b=b'x'*%d\n"
                "for _ in range(%d): sys.stdout.buffer.write(b);sys.stderr.buffer.write(b)\n"
                "sys.stdout.buffer.flush();sys.stderr.buffer.flush()\n" % (block, blocks)),
            timeout=120)
        self.assertEqual(0, result.returncode)
        self.assertEqual(block * blocks, len(result.stdout))
        self.assertEqual(block * blocks, len(result.stderr))
        self.assertEqual({'x'}, set(result.stdout))
        self.assertEqual({'x'}, set(result.stderr))

    def test_5_env_and_cwd_are_passed_through(self):
        env = dict(os.environ)
        env['BOUNDED_SUBPROCESS_PROBE'] = 'probe-value'
        result = BS.run_bounded(
            _py("import os,sys;sys.stdout.write(os.environ.get('BOUNDED_SUBPROCESS_PROBE','')"
                "+'|'+os.getcwd())"),
            timeout=60, env=env, cwd=self.tmp)
        probe, _, cwd = result.stdout.partition('|')
        self.assertEqual('probe-value', probe)
        self.assertEqual(os.path.realpath(self.tmp), os.path.realpath(cwd))
        # env=None must still mean "inherit", which is what both call sites pass for most launches.
        inherited = BS.run_bounded(
            _py("import os,sys;sys.stdout.write(os.environ.get('BOUNDED_SUBPROCESS_PROBE','-'))"),
            timeout=60)
        self.assertEqual('-', inherited.stdout)


# ------------------------------------------------------------------------ [6-7] the timeout

class BoundedRunTimeout(TempTreeCase):

    def test_6_a_frozen_child_is_killed_and_named(self):
        start = time.monotonic()
        result = BS.run_bounded(_py('import time;time.sleep(120)'), timeout=1)
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 15, 'the bound must return promptly, not wait out the child')
        self.assertTrue(result.timed_out)
        self.assertEqual(BS.TIMED_OUT_RETURNCODE, result.returncode)
        self.assertEqual(124, result.returncode)
        self.assertIn('TIMED OUT', result.timeout_note)
        self.assertIn(str(result.pid), result.timeout_note)
        # Appended to stderr so every consumer that already prints a stderr tail says so
        # without being changed.
        self.assertTrue(result.stderr.endswith(result.timeout_note), result.stderr[-200:])
        self.assertGreater(result.seconds, 0)
        self.assertFalse(_process_alive(result.pid), 'the child outlived its own timeout')

    @unittest.skipUnless(sys.platform == 'win32', 'process-tree kill is the Windows path')
    def test_7_the_process_tree_dies_with_the_child(self):
        # The orphan case taskkill /T exists for: chain steps spawn R, node and PowerShell
        # children, and killing only the parent leaves those running, holding the files and
        # locks the next step needs.
        parent, beat = self.heartbeat_fixture()
        result = BS.run_bounded([sys.executable, parent, beat], timeout=3)
        self.assertTrue(result.timed_out)
        self.assert_heartbeat_stopped(beat)

    @unittest.skipUnless(sys.platform == 'win32', 'process-tree kill is the Windows path')
    def test_12_an_interrupted_wait_kills_the_tree_before_it_propagates(self):
        # The parity the subprocess.run this replaced provided through its bare
        # `except: process.kill(); raise`.  A Ctrl-C on a console chain run unwinds
        # `with pipeline_run_lock()`, which REMOVES the lock file -- so a child left running
        # would keep writing while a second chain is free to start, the mixed-vintage publish
        # the lock exists to prevent.  No real signal is sent: the interrupt is raised out of
        # the child's own wait(), which is where a real Ctrl-C would surface.
        parent, beat = self.heartbeat_fixture()
        real_subprocess = BS.subprocess

        class InterruptingPopen(real_subprocess.Popen):
            """Raises KeyboardInterrupt once, on the first wait for the LAUNCHED CHILD.

            The killer taskkill runs through this same patched name, and it must be allowed
            to wait normally or the kill it performs could not be observed here.
            """
            fired = False
            ready = staticmethod(lambda: True)

            def wait(self, timeout=None):
                if 'taskkill' in str(self.args[0]).lower() or InterruptingPopen.fired:
                    return real_subprocess.Popen.wait(self, timeout=timeout)
                # Interrupt only once the grandchild is demonstrably alive; interrupting at
                # t=0 would kill the parent before it ever spawned one and prove nothing.
                deadline = time.monotonic() + 10
                while not InterruptingPopen.ready() and time.monotonic() < deadline:
                    time.sleep(0.05)
                InterruptingPopen.fired = True
                raise KeyboardInterrupt('simulated Ctrl-C while waiting on the child')

        InterruptingPopen.ready = staticmethod(
            lambda: os.path.exists(beat) and os.path.getsize(beat) > 0)

        class SubprocessShim(object):
            """The real subprocess module with only Popen replaced."""

            Popen = InterruptingPopen

            def __getattr__(self, name):
                return getattr(real_subprocess, name)

        BS.subprocess = SubprocessShim()
        try:
            start = time.monotonic()
            with self.assertRaises(KeyboardInterrupt):
                BS.run_bounded([sys.executable, parent, beat], timeout=60)
            elapsed = time.monotonic() - start
        finally:
            BS.subprocess = real_subprocess
        self.assertTrue(InterruptingPopen.fired, 'the interrupt never fired; nothing was tested')
        self.assertLess(elapsed, 30, 'the interrupt must not wait out the 60 s bound')
        self.assert_heartbeat_stopped(beat)


# ---------------------------------------------------------------------- [8] argument contract

class BoundedRunArguments(TempTreeCase):

    def test_8a_timeout_must_be_a_positive_number(self):
        for bad in (0, -1, -0.5, None, '60'):
            with self.assertRaises(ValueError):
                BS.run_bounded(_py('pass'), timeout=bad)

    def test_8b_a_missing_executable_still_raises_filenotfound(self):
        # subprocess.run raised it; anything that swallowed it would turn a typo in a chain
        # entry into a silent pass.
        missing = os.path.join(self.tmp, 'definitely-not-an-executable-here.exe')
        with self.assertRaises(FileNotFoundError):
            BS.run_bounded([missing], timeout=60)


# ------------------------------------------------------------------- [9] call site: the chain

class ChainStepCallSite(TempTreeCase):
    """fec/run_pipeline.py driven for real over a one-step fake chain.

    Importing run_pipeline is side-effect free and is what three existing suites already do
    (test_collection_stream_wiring, test_channel_gold_queues, test_speech_channel): its
    module level resolves paths, validates that every CHAIN producer exists by walking fec/,
    and defines functions.  It runs no producer, takes no lock and writes nothing -- measured
    at 0.15 s with no .pipeline_run.lock created.  Everything main() would otherwise reach is
    replaced below, including MB_ROOT and the tolerated-failure receipt, so nothing outside
    the temp tree can be written and no Glass Box event reaches the real stream.
    """

    RP = None

    @classmethod
    def setUpClass(cls):
        # Imported BEFORE any stdout redirection: run_pipeline's module level calls
        # sys.stdout.reconfigure, which a StringIO does not have.
        cls.RP = _load_module('run_pipeline_under_test', _RUN_PIPELINE_PATH)

    def setUp(self):
        TempTreeCase.setUp(self)
        self.RP = type(self).RP
        self.saved = {}
        self.lock_entered = False
        self.events = []

        @contextlib.contextmanager
        def fake_lock(*args, **kwargs):
            self.lock_entered = True
            yield {'adopted': False, 'path': '<test: the real lock is never touched>'}

        def recorder(kind, status, summary, **kwargs):
            self.events.append({'kind': kind, 'status': status, 'summary': summary,
                                'kwargs': kwargs})
            return None

        self.producer_path = None
        self.patch('_producer_path', lambda name: self.producer_path)
        self.patch('pipeline_run_lock', fake_lock)
        self.patch('preflight_dependencies', lambda: None)
        self.patch('_gb', recorder)
        self.patch('OPTIONAL_INPUTS', {})
        self.patch('TOLERANT_STEPS', set())
        self.patch('LAST_GOOD', {})
        self.patch('LAST_GOOD_RECEIPT', {})
        self.patch('MB_ROOT', self.tmp)
        self.patch('TOLERATED_RECEIPT', 'chain_tolerated_failures.json')
        self.patch('CHAIN', [('fake_step.py', ['--fake'])])
        self.saved_timeout = self.RP.bounded_subprocess.CHAIN_STEP_TIMEOUT_SECONDS
        self.RP.bounded_subprocess.CHAIN_STEP_TIMEOUT_SECONDS = 1
        # The chain transaction (chain_transaction.py) snapshots MB_ROOT and refuses to start under a 30 GB free-disk floor;
        # that floor is the host's business, not this call-site test's, so it is lifted and the staging stays inside the temp tree.
        self.saved_env = {name: os.environ.get(name) for name in ('MELANGE_CHAIN_TXN_MIN_FREE_GB', 'MELANGE_CHAIN_TXN_DIR')}
        os.environ['MELANGE_CHAIN_TXN_MIN_FREE_GB'] = '0'
        os.environ['MELANGE_CHAIN_TXN_DIR'] = os.path.join(self.tmp, '_chain_txn')

    def tearDown(self):
        for name, value in self.saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        self.RP.bounded_subprocess.CHAIN_STEP_TIMEOUT_SECONDS = self.saved_timeout
        for name, value in self.saved.items():
            setattr(self.RP, name, value)

    def patch(self, name, value):
        if name not in self.saved:
            self.saved[name] = getattr(self.RP, name)
        setattr(self.RP, name, value)

    def run_main(self):
        buffer = io.StringIO()
        start = time.monotonic()
        with contextlib.redirect_stdout(buffer):
            code = self.RP.main([])
        return code, buffer.getvalue(), time.monotonic() - start

    def sleeper(self):
        return self.write_script('fake_step.py', "import time\nprint('fake step started')\n"
                                                 "time.sleep(120)\n")

    def events_of(self, kind, status):
        return [event for event in self.events
                if event['kind'] == kind and event['status'] == status]

    def test_9a_a_frozen_chain_step_halts_the_chain_and_says_TIMED_OUT(self):
        self.producer_path = self.sleeper()
        code, output, elapsed = self.run_main()
        self.assertEqual(1, code, output)
        self.assertLess(elapsed, 20, 'the chain must not wait out a frozen step')
        self.assertTrue(self.lock_entered, 'main() did not go through the (patched) run lock')
        step_line = [line for line in output.splitlines() if 'fake_step.py' in line]
        self.assertTrue(step_line, output)
        self.assertIn('!!', step_line[0])
        self.assertIn('TIMED OUT', step_line[0])
        failed = self.events_of('pipeline.step', 'failed')
        self.assertEqual(1, len(failed), self.events)
        self.assertIn('TIMED OUT', failed[0]['summary'])
        halted = self.events_of('pipeline.run', 'failed')
        self.assertEqual(1, len(halted), self.events)
        self.assertIn('chain halted', halted[0]['summary'])
        self.assertNotIn('PIPELINE COMPLETE', output)

    def test_9b_a_tolerant_step_is_not_tolerated_when_it_times_out(self):
        # The control first: with a bound last-good artifact in place, exit code 2 IS
        # tolerated and the chain finishes.  Then the same step frozen: 124 is not 2, so the
        # existing failure path halts the chain anyway -- fail closed.
        artifact = os.path.join(self.tmp, 'last_good.json')
        with open(artifact, 'w', encoding='utf-8') as handle:
            handle.write('{"kept":true}')
        import hashlib
        with open(artifact, 'rb') as handle:
            digest = hashlib.sha256(handle.read()).hexdigest()
        receipt = os.path.join(self.tmp, 'last_good.receipt.json')
        with open(receipt, 'w', encoding='utf-8') as handle:
            json.dump({'status': 'PASS', 'artifact_sha256': digest}, handle)
        self.patch('TOLERANT_STEPS', {'fake_step.py'})
        self.patch('LAST_GOOD', {'fake_step.py': artifact})
        self.patch('LAST_GOOD_RECEIPT', {'fake_step.py': receipt})

        self.producer_path = self.write_script(
            'fake_step.py', "import sys\nprint('contract refusal')\nsys.exit(2)\n")
        code, output, _elapsed = self.run_main()
        self.assertEqual(0, code, output)
        self.assertIn('tolerated', output)
        self.assertIn('PIPELINE COMPLETE', output)
        self.assertTrue(os.path.exists(os.path.join(self.tmp, 'chain_tolerated_failures.json')))

        self.events = []
        self.producer_path = self.sleeper()
        code, output, elapsed = self.run_main()
        self.assertEqual(1, code, output)
        self.assertLess(elapsed, 20)
        self.assertNotIn('tolerated:', output)
        self.assertNotIn('PIPELINE COMPLETE', output)
        self.assertIn('TIMED OUT', output)
        self.assertEqual(1, len(self.events_of('pipeline.run', 'failed')), self.events)

    def test_9c_a_healthy_step_still_completes_the_chain(self):
        self.producer_path = self.write_script('fake_step.py', "print('fake step complete')\n")
        code, output, _elapsed = self.run_main()
        self.assertEqual(0, code, output)
        self.assertIn('PIPELINE COMPLETE', output)
        self.assertIn('OK', output)
        self.assertIn('fake step complete', output)
        self.assertEqual(1, len(self.events_of('pipeline.step', 'completed')), self.events)
        self.assertEqual(1, len(self.events_of('pipeline.run', 'completed')), self.events)

    def test_9d_the_launch_is_the_bounded_one(self):
        source = _read_source(_RUN_PIPELINE_PATH)
        launch = [line for line in source.splitlines() if 'run=' in line and 'run_bounded(' in line]
        self.assertEqual(1, len(launch), launch)
        self.assertIn('bounded_subprocess.CHAIN_STEP_TIMEOUT_SECONDS', launch[0])
        self.assertNotIn('subprocess.run(', source[source.index('for script,script_args in CHAIN'):])
        self.assertIn('if run.timed_out', source)
        self.assertIn('tail=[run.timeout_note]', source)


# ------------------------------------------------------------------- [10] call site: the kernel

class KernelSuiteCallSite(unittest.TestCase):
    """fec/tests/run_tests.py, pinned as TEXT.

    It cannot be imported: the whole kernel -- every registered suite, ~20 minutes of
    subprocesses -- runs at import, and it ends in sys.exit().
    """

    def setUp(self):
        self.source = _read_source(_RUN_TESTS_PATH)

    def test_10a_the_suite_loop_launches_bounded(self):
        self.assertIn('import bounded_subprocess as _bounded', self.source)
        self.assertIn('_r=_bounded.run_bounded(_argv,timeout=_bounded.KERNEL_SUITE_TIMEOUT_SECONDS,'
                      'env=_env)', self.source)
        self.assertNotIn('_sp.run(_argv,capture_output=True', self.source)

    def test_10b_a_timed_out_suite_reports_its_own_name(self):
        loop = self.source[self.source.index('for _t,_argv,_env in _suite_specs:'):]
        self.assertIn('_r.timed_out', loop)
        self.assertIn('_r.timeout_note', loop)
        # The verdict itself is unchanged: returncode 124 is not 0, so the suite is FAILED,
        # never skipped.
        self.assertIn('_r.returncode==0', loop)

    def test_10c_this_suite_is_registered_exactly_once(self):
        self.assertEqual(1, self.source.count("'test_bounded_subprocess'"), self.source.count(
            "'test_bounded_subprocess'"))
        lanes = self.source[self.source.index('_LANE_MODULES=('):self.source.index('_mb_root=')]
        self.assertIn("'test_bounded_subprocess'", lanes)


# ------------------------------------------------------------------------- [11] the two bounds

class Bounds(unittest.TestCase):

    def test_11_the_bounds_are_what_the_docstring_says(self):
        # Measured 2026-09-19 over 677 recorded chain step-runs: slowest step ever 4,148.7 s,
        # so 3 h is 2.6x the worst case; a healthy kernel run is ~18 min for all ~247 suites
        # together, so no single suite can legitimately need 30.  The docstring carries both
        # numbers, so an edit to a constant that leaves the rationale behind fails here.
        self.assertEqual(3 * 60 * 60, BS.CHAIN_STEP_TIMEOUT_SECONDS)
        self.assertEqual(10800, BS.CHAIN_STEP_TIMEOUT_SECONDS)
        self.assertEqual(30 * 60, BS.KERNEL_SUITE_TIMEOUT_SECONDS)
        self.assertEqual(1800, BS.KERNEL_SUITE_TIMEOUT_SECONDS)
        self.assertEqual(124, BS.TIMED_OUT_RETURNCODE)
        self.assertGreater(BS.KILL_WAIT_SECONDS, 0)
        self.assertIn('%d s' % BS.CHAIN_STEP_TIMEOUT_SECONDS, BS.__doc__)
        self.assertIn('%d s' % BS.KERNEL_SUITE_TIMEOUT_SECONDS, BS.__doc__)

    def test_11b_the_module_is_import_safe_and_stdlib_only(self):
        # run_pipeline.py imports this before it does anything else, so its module level must
        # not read the environment, open a file, spawn anything, or drag in a repo sibling
        # (which would make the chain runner's import order load-bearing).  No env dial either:
        # the repo catalogues every os.environ dial and this change adds none.
        import ast
        head = []
        for node in ast.parse(_read_source(_MODULE_PATH)).body:
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                break
            head.append(node)
        self.assertTrue(head)
        modules = []
        for node in head:
            for child in ast.walk(node):
                self.assertNotIsInstance(child, ast.Call,
                                         'module level must call nothing at import')
                if isinstance(child, ast.Import):
                    modules += [alias.name.split('.')[0] for alias in child.names]
                elif isinstance(child, ast.ImportFrom):
                    modules.append((child.module or '').split('.')[0])
        self.assertTrue(modules)
        for module in modules:
            self.assertIn(module, ('subprocess', 'sys', 'tempfile', 'time', 'os', 'errno',
                                   'signal'),
                          'bounded_subprocess must stay stdlib-only: %s' % module)


# -------------------------------------------- [13-14] launch flags and the on_start adoption hook

class BoundedRunLaunchHooks(TempTreeCase):
    """What the weekly refresh needs on top of the chain's and the kernel's launches.

    It creates every child in its OWN process group so that a console CTRL+C never reaches a
    producer mid-write, and it must publish the live child to its atexit orphan guard BEFORE the
    wait begins -- an abort between launch and the first wait still has to find a tree to kill.
    """

    def recording_subprocess(self, seen):
        """The real subprocess module with Popen replaced by a recorder.

        The killer taskkill goes through this same patched name, so its own kwargs are skipped:
        what is under test is the kwargs of the LAUNCHED CHILD.
        """
        real_subprocess = BS.subprocess

        class RecordingPopen(real_subprocess.Popen):

            def __init__(self, args, **kwargs):
                if 'taskkill' not in str(args[0]).lower():
                    seen.update(kwargs)
                real_subprocess.Popen.__init__(self, args, **kwargs)

        class SubprocessShim(object):

            Popen = RecordingPopen

            def __getattr__(self, name):
                return getattr(real_subprocess, name)

        BS.subprocess = SubprocessShim()
        self.addCleanup(setattr, BS, 'subprocess', real_subprocess)

    @unittest.skipUnless(sys.platform == 'win32', 'creationflags is only valid on Windows')
    def test_13a_creationflags_reach_popen_and_a_real_child_still_runs(self):
        # weekly_refresh.py has launched every step with CREATE_NEW_PROCESS_GROUP since the
        # orphan-guard lane: a console CTRL+C must NOT reach a collector or a mid-write
        # run_pipeline, because the guard's ordered tree kill is what protects the writer lease.
        # A flag silently dropped on the way into this helper would re-open that door.
        seen = {}
        self.recording_subprocess(seen)
        result = BS.run_bounded(
            _py("import sys;sys.stdout.write('flagged')"), timeout=60,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        self.assertEqual(subprocess.CREATE_NEW_PROCESS_GROUP, seen.get('creationflags'))
        self.assertIs(False, seen.get('start_new_session'))
        # Recorded is not enough: the child launched WITH the flag must still run to completion
        # and hand back its output.
        self.assertEqual(0, result.returncode)
        self.assertEqual('flagged', result.stdout)
        self.assertFalse(result.timed_out)

    def test_13b_the_default_launch_is_unchanged_on_every_platform(self):
        # The chain and the kernel pass neither flag; their launches must be exactly what they
        # were before the two parameters existed.
        seen = {}
        self.recording_subprocess(seen)
        result = BS.run_bounded(_py("import sys;sys.stdout.write('plain')"), timeout=60)
        self.assertEqual('plain', result.stdout)
        self.assertIs(False, seen.get('start_new_session'))
        if sys.platform == 'win32':
            self.assertEqual(0, seen.get('creationflags'))
        else:
            # POSIX Popen REJECTS a creationflags keyword outright, so it must not be passed.
            self.assertNotIn('creationflags', seen)

    def test_14a_on_start_receives_the_live_popen_before_the_wait(self):
        seen = {}

        def on_start(process):
            seen['pid'] = process.pid
            seen['poll'] = process.poll()

        result = BS.run_bounded(_py('import time;time.sleep(1)'), timeout=60, on_start=on_start)
        self.assertEqual(0, result.returncode)
        self.assertEqual(result.pid, seen.get('pid'),
                         'on_start saw a different process than the result reports')
        self.assertIsNone(seen.get('poll'),
                          'on_start ran after the child had already exited, too late to adopt it')

    @unittest.skipUnless(sys.platform == 'win32', 'process-tree kill is the Windows path')
    def test_14b_an_on_start_that_raises_kills_the_tree_and_propagates(self):
        # on_start is weekly_refresh's adoption point, and it runs INSIDE the guarded region, so
        # a failure there takes the same path as an interrupt: the TREE dies before the exception
        # leaves.  If it did not, the refresh would unwind -- releasing the pipeline writer lease
        # in atexit -- while a child that inherited that lease kept writing.
        parent, beat = self.heartbeat_fixture()

        def on_start(process):
            # Raise only once the grandchild is demonstrably alive; raising at t=0 would prove
            # nothing about the tree.
            self.wait_for_heartbeat(beat)
            raise ZeroDivisionError('simulated adoption failure')

        start = time.monotonic()
        with self.assertRaises(ZeroDivisionError):
            BS.run_bounded([sys.executable, parent, beat], timeout=60, on_start=on_start)
        self.assertLess(time.monotonic() - start, 30,
                        'the failure must not wait out the 60 s bound')
        self.assert_heartbeat_stopped(beat)


# ------------------------------------------------------- [18] log_path: the child's output is kept

class BoundedRunLog(TempTreeCase):
    """run_bounded(log_path=...) directly.  The weekly step() path (timeout + exit 1) is test_15n."""

    CHILD = "import sys\nprint('out line')\nsys.stderr.write('err line\\n')\nsys.exit(3)\n"

    def test_18a_an_interrupt_still_raises_and_the_log_is_written(self):
        marker = os.path.join(self.tmp, 'printed')
        path = os.path.join(self.tmp, 'logs', 'interrupted.log')
        child = ("import time\nprint('before the interrupt', flush=True)\n"
                 "open(%r, 'w').close()\ntime.sleep(60)\n" % marker)

        def on_start(process):
            deadline = time.monotonic() + 30
            while not os.path.exists(marker) and time.monotonic() < deadline:
                time.sleep(0.05)
            raise KeyboardInterrupt()

        with self.assertRaises(KeyboardInterrupt):
            BS.run_bounded(_py(child), timeout=60, on_start=on_start, log_path=path)
        with open(path, encoding='utf-8') as handle:
            text = handle.read()
        self.assertIn('# status: INTERRUPTED; process tree killed', text)
        self.assertIn('before the interrupt', text)

    def test_18b_an_unwritable_log_leaves_the_result_unchanged(self):
        blocker = os.path.join(self.tmp, 'afile')
        with open(blocker, 'w') as handle:
            handle.write('x')
        plain = BS.run_bounded(_py(self.CHILD), timeout=60)
        blocked = BS.run_bounded(_py(self.CHILD), timeout=60, log_path=os.path.join(blocker, 'x.log'))
        self.assertIsNone(blocked.log_path)
        self.assertEqual((plain.returncode, plain.stdout, plain.stderr, plain.timed_out),
                         (blocked.returncode, blocked.stdout, blocked.stderr, blocked.timed_out))

    def test_18c_each_stream_keeps_its_tail_under_the_cap_and_says_so(self):
        path = os.path.join(self.tmp, 'big.log')
        filler = BS.LOG_KEEP_BYTES + 1024 * 1024
        child = "import sys\nsys.stdout.write('HEAD' + '-MARK' + 'x' * %d + 'TAIL' + '-MARK')\n" % filler
        result = BS.run_bounded(_py(child), timeout=60, log_path=path)
        total = len('HEAD-MARK') + filler + len('TAIL-MARK')
        self.assertEqual(path, result.log_path)
        with open(path, 'rb') as handle:
            data = handle.read()
        self.assertIn(b'===== stdout (last %d of %d bytes) =====' % (BS.LOG_KEEP_BYTES, total), data)
        self.assertIn(b'TAIL-MARK', data)
        self.assertNotIn(b'HEAD-MARK', data)
        self.assertLess(len(data), BS.LOG_KEEP_BYTES + 4096)
        self.assertEqual(total, len(result.stdout), 'the in-memory result is never capped')

    def test_18d_the_log_is_additive_the_result_is_identical_with_and_without_it(self):
        path = os.path.join(self.tmp, 'logs', 'kept.log')
        plain = BS.run_bounded(_py(self.CHILD), timeout=60)
        logged = BS.run_bounded(_py(self.CHILD), timeout=60, log_path=path)
        self.assertIsNone(plain.log_path)
        self.assertEqual(path, logged.log_path)
        self.assertEqual((plain.returncode, plain.stdout, plain.stderr, plain.timed_out),
                         (logged.returncode, logged.stdout, logged.stderr, logged.timed_out))
        self.assertEqual((3, 'out line\n', 'err line\n'), (plain.returncode, plain.stdout, plain.stderr))
        with open(path, encoding='utf-8') as handle:
            text = handle.read()
        for needle in ('# status: exit 3', 'out line', 'err line'):
            self.assertIn(needle, text)
        self.assertEqual(['kept.log'], os.listdir(os.path.join(self.tmp, 'logs')))


# ------------------------------------------- [15] call site: the weekly refresh's own step()

class _HardAbort(BaseException):
    """Stand-in for _hard_abort's sys.exit(1).

    It derives from BaseException, NOT Exception, exactly as SystemExit does.  A stub deriving
    from Exception would be swallowed by step()'s own ``except Exception``, turning every hard
    abort in these tests into a logged ERROR -- which is the one failure mode the sandbox must
    not be able to hide.
    """


def _weekly_function_source(name):
    """The REAL source text of one weekly_refresh.py function.

    weekly_refresh.py is NEVER imported here: its module level takes the weekly lock and the
    pipeline writer lease, emits to the Glass Box, and then runs the entire refresh -- every
    collector, the whole chain, publication.  ast.get_source_segment hands back the genuine bytes
    of the function under test with none of that, so these are behavioural tests of the shipped
    code rather than a re-implementation of it.
    """
    source = _read_source(_WEEKLY_REFRESH_PATH)
    node = next(n for n in ast.parse(source).body
                if isinstance(n, ast.FunctionDef) and n.name == name)
    return ast.get_source_segment(source, node)


class WeeklyRefreshStepCallSite(TempTreeCase):
    """weekly_refresh.py's step(), _adopt_live_child() and _kill_live_child(), run for real.

    Real children, real timeouts, real tree kills; everything else is a recorder.  FE is a temp
    dir, _producer_path returns a scratch script, and log / _write_status / _emit / _hard_abort
    record instead of writing.  Nothing outside the temp tree is touched and no Glass Box event
    reaches the real stream.
    """

    def setUp(self):
        TempTreeCase.setUp(self)
        self.logs = []
        self.status = []
        self.emits = []
        self.aborts = []
        self.adoption = []
        self.producer = None
        self.ns = {
            'bounded_subprocess': self.observing_module(),
            'compute_engine': CE,
            'subprocess': subprocess, 'sys': sys, 'os': os, 'time': time, 'datetime': datetime,
            'FE': self.tmp,
            '_LIVE_CHILD': [None],
            '_tolerated': [],
            '_soft_failures': [],
            '_producer_path': self.producer_path,
            '_step_log': lambda name: None,    # no persisted log here; test_15n supplies a temp one
            '_chain_tail_is_audit_full': lambda: True,
            'log': self.logs.append,
            '_write_status': lambda **kwargs: self.status.append(kwargs),
            '_emit': lambda kind, status, summary, **kwargs: self.emits.append(
                (kind, status, summary, kwargs)),
            '_hard_abort': self.hard_abort,
        }
        for name in ('_adopt_live_child', '_kill_live_child', '_soft_fail', 'step'):
            exec(_weekly_function_source(name), self.ns)

    def producer_path(self, name):
        """Production raises RuntimeError for an unknown basename; so does this."""
        if self.producer is None:
            raise RuntimeError('weekly refresh producer script not found: %s' % name)
        return self.producer

    def hard_abort(self, name, failures, why):
        self.aborts.append((name, list(failures), why))
        raise _HardAbort(why)

    def observing_module(self):
        """The real bounded_subprocess, with run_bounded wrapped to watch the adoption slot.

        The wrapper's own on_start runs AFTER step()'s, while the child is provably alive, so it
        can assert that _LIVE_CHILD really held this process during the wait -- which is what the
        atexit orphan guard depends on and what no after-the-fact check could show.
        """
        case = self

        class ObservingBoundedSubprocess(object):

            def run_bounded(self, argv, **kwargs):
                caller_on_start = kwargs.pop('on_start', None)

                def on_start(process):
                    if caller_on_start is not None:
                        caller_on_start(process)
                    case.adoption.append((case.ns['_LIVE_CHILD'][0] is process,
                                          process.poll() is None))

                return BS.run_bounded(argv, on_start=on_start, **kwargs)

            def __getattr__(self, name):
                return getattr(BS, name)

        return ObservingBoundedSubprocess()

    @staticmethod
    def timeout_line(name, timeout, hard):
        """The line weekly_refresh.py has always printed, rebuilt from its own format string."""
        return '%-28s TIMEOUT after %ds%s' % (
            name, timeout, ' — aborting' if hard else ' — continuing')

    # ---- healthy, failing and tolerated paths: byte-identical to what they logged before ----

    def test_15a_a_healthy_step_returns_true_and_releases_the_orphan_slot(self):
        self.producer = self.write_script(
            'fake_producer.py',
            "import time\nprint('first line')\ntime.sleep(1)\nprint('done: 12 rows')\n")
        self.assertTrue(self.ns['step']('healthy step', ['fake_producer.py'], timeout=60))
        self.assertEqual(1, len(self.logs), self.logs)
        self.assertTrue(self.logs[0].startswith('healthy step' + ' ' * 16), repr(self.logs[0]))
        self.assertIn(' OK ', self.logs[0])
        self.assertIn('done: 12 rows', self.logs[0])
        self.assertNotIn('first line', self.logs[0], 'the OK line carries the LAST stdout line')
        # _write_status(active_step=...) still comes first, before anything is launched.
        self.assertEqual('healthy step', self.status[0].get('active_step'))
        # Adopted while alive, released afterwards -- both halves matter.
        self.assertEqual([(True, True)], self.adoption,
                         'the orphan guard never held the live child')
        self.assertIsNone(self.ns['_LIVE_CHILD'][0])
        self.assertEqual([], self.aborts)

    def test_15b_a_soft_failing_step_returns_false_and_logs_its_stderr(self):
        self.producer = self.write_script(
            'fake_producer.py',
            "import sys\nprint('partial output')\nsys.stderr.write('boom: bad row 7\\n')\n"
            "sys.exit(3)\n")
        self.assertFalse(self.ns['step']('soft step', ['fake_producer.py'], timeout=60))
        self.assertEqual(2, len(self.logs), self.logs)
        self.assertIn(' !! ', self.logs[0])
        self.assertIn('partial output', self.logs[0])
        self.assertTrue(self.logs[1].startswith('   stderr: '), repr(self.logs[1]))
        self.assertIn('boom: bad row 7', self.logs[1])
        self.assertEqual([], self.aborts)
        self.assertIsNone(self.ns['_LIVE_CHILD'][0])

    def test_15c_a_hard_failing_step_without_a_gate_aborts_with_its_exit_code(self):
        self.producer = self.write_script('fake_producer.py', 'import sys\nsys.exit(3)\n')
        with self.assertRaises(_HardAbort):
            self.ns['step']('hard step', ['fake_producer.py'], timeout=60, hard=True)
        self.assertEqual([('hard step', [], 'step exited 3')], self.aborts)
        self.assertIsNone(self.ns['_LIVE_CHILD'][0])

    def test_15d_a_tolerating_gate_still_soft_completes_a_hard_step(self):
        # The Paul-gated audit path: audit_full.py is the chain's LAST step, so a lone audit
        # dissent means every producer already wrote.  Keeping this alive matters as much as the
        # timeout work -- a refresh that stopped tolerating would hard-abort every Sunday.
        self.producer = self.write_script(
            'fake_producer.py',
            "import sys\nsys.stderr.write('ck FAIL culling\\n')\nsys.exit(1)\n")
        self.assertTrue(self.ns['step']('gated step', ['fake_producer.py'], timeout=60, hard=True,
                                        gate=lambda out, err: (True, ['culling'])))
        self.assertEqual([], self.aborts)
        self.assertEqual(['culling'], self.ns['_tolerated'])
        self.assertTrue(any('tolerated: culling' in entry for entry in self.logs), self.logs)
        self.assertEqual(1, len(self.emits), self.emits)
        self.assertEqual(('refresh.tolerated_failure', 'completed'), self.emits[0][:2])
        self.assertEqual('RUNNING_AFTER_KNOWN_FAILURE', self.status[-1].get('last_outcome'))

    # ---- the timeout: bounded, named, and the tree dead ------------------------------------

    def test_15e_a_soft_timeout_names_itself_kills_the_tree_and_continues(self):
        self.producer, beat = self.heartbeat_fixture()
        start = time.monotonic()
        self.assertFalse(self.ns['step']('frozen step', ['parent.py', beat], timeout=1))
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 20, 'the refresh must not wait out a frozen step')
        self.assertEqual(2, len(self.logs), self.logs)
        self.assertEqual(self.timeout_line('frozen step', 1, False), self.logs[0])
        self.assertTrue(self.logs[1].startswith('   TIMED OUT after 1 s'), repr(self.logs[1]))
        self.assertIn('process tree killed', self.logs[1])
        self.assertEqual([], self.aborts, 'a soft timeout continues; it does not abort')
        self.assertIsNone(self.ns['_LIVE_CHILD'][0])
        if sys.platform == 'win32':
            self.assert_heartbeat_stopped(beat)

    def test_15f_a_hard_timeout_aborts_the_refresh(self):
        self.producer = self.write_script('fake_producer.py', 'import time\ntime.sleep(120)\n')
        start = time.monotonic()
        with self.assertRaises(_HardAbort):
            self.ns['step']('frozen hard step', ['fake_producer.py'], timeout=1, hard=True)
        self.assertLess(time.monotonic() - start, 20)
        self.assertEqual([('frozen hard step', [], 'timeout after 1s')], self.aborts)
        self.assertEqual(self.timeout_line('frozen hard step', 1, True), self.logs[0])
        self.assertIsNone(self.ns['_LIVE_CHILD'][0])

    def test_15g_a_missing_producer_is_a_named_error_not_a_timeout(self):
        self.producer = None
        self.assertFalse(self.ns['step']('absent step', ['gone.py'], timeout=60))
        self.assertEqual(1, len(self.logs), self.logs)
        self.assertIn('ERROR', self.logs[0])
        self.assertIn('weekly refresh producer script not found', self.logs[0])
        self.assertIsNone(self.ns['_LIVE_CHILD'][0])
        self.assertEqual([], self.adoption, 'nothing was ever launched, so nothing was adopted')
        del self.logs[:]
        with self.assertRaises(_HardAbort):
            self.ns['step']('absent hard step', ['gone.py'], timeout=60, hard=True)
        self.assertEqual(1, len(self.aborts))
        self.assertEqual('absent hard step', self.aborts[0][0])
        self.assertEqual([], self.aborts[0][1])
        self.assertIn('RuntimeError', self.aborts[0][2])

    # ---- the atexit orphan guard -----------------------------------------------------------

    def test_15h_the_orphan_guard_kills_a_live_child_and_ignores_a_finished_one(self):
        kill = self.ns['_kill_live_child']
        kill()                                   # nothing adopted: a silent no-op
        self.assertEqual([], self.logs, self.logs)

        finished = subprocess.Popen(_py('pass'))
        finished.wait(timeout=60)
        self.ns['_LIVE_CHILD'][0] = finished
        kill()
        self.assertEqual([], self.logs, 'a finished child is not an orphan and must not be named')
        self.assertIs(finished, self.ns['_LIVE_CHILD'][0], 'the finished-child path changes nothing')

        parent, beat = self.heartbeat_fixture()
        # Its own session/group on POSIX, matching how step() launches: _kill_live_child signals
        # the GROUP there, and the caller's own group must never be the target.
        live = subprocess.Popen([sys.executable, parent, beat],
                                start_new_session=os.name != 'nt')
        self.addCleanup(live.kill)
        self.assertTrue(self.wait_for_heartbeat(beat), 'the grandchild never started')
        self.ns['_LIVE_CHILD'][0] = live
        start = time.monotonic()
        kill()
        self.assertLess(time.monotonic() - start, 90, 'the atexit guard must stay bounded')
        self.assertTrue(any('KILLING orphaned step child' in entry for entry in self.logs),
                        self.logs)
        self.assertIsNotNone(live.poll(), 'the orphan guard left the adopted child running')
        self.assertIsNone(self.ns['_LIVE_CHILD'][0], 'the guard must release the slot')
        if sys.platform == 'win32':
            self.assert_heartbeat_stopped(beat)

    # ---- the partial= hook: the press step's one excused exit 3 (letters continue 15h) ------

    def test_15i_a_hard_exit_3_the_hook_excuses_returns_true_and_logs_as_a_failure(self):
        self.producer = self.write_script(
            'fake_producer.py',
            "import sys\nprint('status=PARTIAL')\nsys.stderr.write('pending_body_urls=4\\n')\n"
            "sys.exit(3)\n")
        calls = []
        before = time.time()
        self.assertIs(True, self.ns['step'](
            'press delta refresh', ['fake_producer.py'], timeout=60, hard=True,
            partial=lambda returncode, started: calls.append((returncode, started)) or True))
        after = time.time()
        self.assertEqual([], self.aborts)
        # Exactly the two lines a failing step has always logged; the excusal adds none of its own
        # here (the real hook logs and emits itself).
        self.assertEqual(2, len(self.logs), self.logs)
        self.assertIn(' !! ', self.logs[0])
        self.assertTrue(self.logs[1].startswith('   stderr: '), repr(self.logs[1]))
        self.assertEqual(1, len(calls), calls)
        self.assertEqual(3, calls[0][0])
        self.assertTrue(before <= calls[0][1] <= after, 'the hook gets the step start time t')
        self.assertEqual([], self.ns['_tolerated'], 'an excused PARTIAL is not a Paul-gated tolerance')
        self.assertEqual([], self.emits)
        self.assertIsNone(self.ns['_LIVE_CHILD'][0])

    def test_15j_a_hook_that_refuses_leaves_the_legacy_hard_abort_untouched(self):
        self.producer = self.write_script('fake_producer.py', 'import sys\nsys.exit(3)\n')
        calls = []
        with self.assertRaises(_HardAbort):
            self.ns['step']('press delta refresh', ['fake_producer.py'], timeout=60, hard=True,
                            partial=lambda returncode, started: calls.append(returncode) or False)
        self.assertEqual([3], calls)
        self.assertEqual([('press delta refresh', [], 'step exited 3')], self.aborts)

    def test_15k_the_hook_is_consulted_only_by_a_hard_step_that_exited_nonzero(self):
        calls = []

        def hook(returncode, started):
            calls.append(returncode)
            return True

        def run(name, hard=False, timeout=60):
            return self.ns['step'](name, ['fake_producer.py'], timeout=timeout, hard=hard,
                                   partial=hook)

        self.producer = self.write_script('fake_producer.py', "print('done')\n")
        self.assertTrue(run('hard exit 0', hard=True))
        self.producer = self.write_script('fake_producer.py', 'import sys\nsys.exit(3)\n')
        self.assertFalse(run('soft exit 3'))
        self.producer = self.write_script('fake_producer.py', 'import time\ntime.sleep(120)\n')
        with self.assertRaises(_HardAbort):
            run('frozen hard step', hard=True, timeout=1)
        self.producer = None
        with self.assertRaises(_HardAbort):
            run('absent hard step', hard=True)
        self.assertEqual([], calls, 'exit 0, a soft step, a timeout and a launch failure never ask')
        self.assertEqual('timeout after 1s', self.aborts[0][2])

    def test_15l_an_excused_exit_never_runs_the_gate_or_records_a_tolerance(self):
        self.producer = self.write_script(
            'fake_producer.py', "import sys\nsys.stderr.write('ck FAIL culling\\n')\nsys.exit(3)\n")
        gate_calls = []

        def gate(out, err):
            gate_calls.append(1)
            return True, ['culling']

        self.assertTrue(self.ns['step']('gated step', ['fake_producer.py'], timeout=60, hard=True,
                                        gate=gate, partial=lambda returncode, started: True))
        self.assertEqual([], gate_calls)
        self.assertEqual([], self.ns['_tolerated'])
        self.assertEqual([], self.emits)
        # And a refusing hook leaves the gate exactly where 15d found it.
        self.assertTrue(self.ns['step']('gated step', ['fake_producer.py'], timeout=60, hard=True,
                                        gate=gate, partial=lambda returncode, started: False))
        self.assertEqual([1], gate_calls)
        self.assertEqual(['culling'], self.ns['_tolerated'])

    def test_15m_exactly_one_step_call_carries_partial_and_it_is_the_press_delta_row(self):
        tree = ast.parse(_read_source(_WEEKLY_REFRESH_PATH))
        hooked = [node for node in ast.walk(tree)
                  if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                  and node.func.id == 'step' and any(kw.arg == 'partial' for kw in node.keywords)]
        self.assertEqual(1, len(hooked), [node.lineno for node in hooked])
        row = hooked[0]
        keywords = {kw.arg: kw.value for kw in row.keywords}
        self.assertEqual('press delta refresh', row.args[0].value)
        self.assertEqual('refresh_press_stream.py', row.args[1].elts[0].value)
        self.assertEqual('_press_partial_excused', keywords['partial'].id)
        self.assertIs(True, keywords['hard'].value, 'the hook only ever acts on a hard step')

    # ---- the per-step log: what 300 chars of stderr and a bare timeout note could not diagnose ----

    def test_15n_a_failed_or_timed_out_step_keeps_its_full_output_and_logs_where(self):
        # 2026-10-18: a soft timeout (nothing survived the kill) and an exit 1 whose R child's own error
        # text sat above a long traceback (only stderr[-300:] survived) were both undiagnosable.
        self.ns['_step_log'] = lambda name: os.path.join(self.tmp, 'logs', name.replace(' ', '_') + '.log')
        self.write_script('r_stand_in.py', "import sys\nsys.stderr.write('R-OWN-ERROR-TEXT\\n')\nsys.exit(1)\n")
        # exit 1; the failure text is in a GRANDCHILD that inherits the streams, as run_checked's Rscript does
        self.producer = self.write_script(
            'fake_producer.py',
            "import os, subprocess, sys\nprint('stdout evidence')\n"
            "subprocess.run([sys.executable, os.path.join(os.path.dirname(__file__), 'r_stand_in.py')])\n"
            "sys.stderr.write('x' * 5000 + '\\nlast line\\n')\nsys.exit(1)\n")
        path = os.path.join(self.tmp, 'logs', 'failing_step.log')
        self.assertFalse(self.ns['step']('failing step', ['fake_producer.py'], timeout=60))
        self.assertEqual(3, len(self.logs), self.logs)
        self.assertEqual('   full output: ' + path, self.logs[2])
        with open(path, encoding='utf-8') as handle:
            text = handle.read()
        for needle in ('# status: exit 1', 'stdout evidence', 'R-OWN-ERROR-TEXT', 'last line'):
            self.assertIn(needle, text)
        # killed at the timeout: the child's UNFLUSHED print must survive too (PYTHONUNBUFFERED)
        del self.logs[:]
        self.producer = self.write_script(
            'fake_producer.py',
            "import sys, time\nprint('progress before the kill')\nsys.stderr.write('err before the kill\\n')\n"
            "time.sleep(120)\n")
        path = os.path.join(self.tmp, 'logs', 'frozen_step.log')
        self.assertFalse(self.ns['step']('frozen step', ['fake_producer.py'], timeout=5))
        self.assertEqual(3, len(self.logs), self.logs)
        self.assertEqual(self.timeout_line('frozen step', 5, False), self.logs[0])
        self.assertEqual('   full output: ' + path, self.logs[2])
        with open(path, encoding='utf-8') as handle:
            text = handle.read()
        for needle in ('# status: TIMED OUT after 5 s', 'progress before the kill', 'err before the kill'):
            self.assertIn(needle, text)
        self.assertEqual([], self.aborts)


# --------------------------------------------------- [16-17] the wiring and the untouched bounds

class WeeklyRefreshWiring(unittest.TestCase):
    """Structural pins on weekly_refresh.py, which cannot be imported (module scope runs it)."""

    def setUp(self):
        self.source = _read_source(_WEEKLY_REFRESH_PATH)
        self.tree = ast.parse(self.source)

    def function(self, name):
        node = next(n for n in self.tree.body
                    if isinstance(n, ast.FunctionDef) and n.name == name)
        return ast.get_source_segment(self.source, node)

    def test_16a_the_import_precedes_the_first_step_call(self):
        # Every step() call in this script is at MODULE level: the import has to be above the
        # first of them or the refresh dies on its own first collector.
        self.assertIn('\nimport bounded_subprocess', self.source)
        imported = min(node.lineno for node in self.tree.body
                       if isinstance(node, ast.Import)
                       and any(alias.name == 'bounded_subprocess' for alias in node.names))
        first_step = min(node.lineno for node in ast.walk(self.tree)
                         if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                         and node.func.id == 'step')
        self.assertLess(imported, first_step)

    def test_16b_step_launches_bounded_and_no_longer_drains_pipes(self):
        body = self.function('step')
        self.assertIn('bounded_subprocess.run_bounded(', body)
        self.assertIn('on_start=', body)
        # communicate() is the DE-026 hazard AND the pipe hazard: a detached grandchild that
        # inherited the pipe keeps the reader threads alive long after the child is gone.
        self.assertNotIn('communicate(', body)
        self.assertNotIn('proc.wait()', body)
        self.assertNotIn('subprocess.Popen(', body)

    def test_16c_the_orphan_guard_kills_through_the_bounded_helper(self):
        body = self.function('_kill_live_child')
        self.assertIn('bounded_subprocess.kill_tree(', body)
        # It runs inside atexit, so it may neither raise nor wait unbounded.  An unbounded
        # subprocess.run(taskkill) here was the second copy of the same stall.
        self.assertNotIn('subprocess.run(', body)
        self.assertIn('timeout=60', body)
        self.assertIn('except BaseException', body)

    def test_16d_no_unbounded_wait_survives_anywhere_in_the_file(self):
        # This is the whole point.  The refresh holds the pipeline writer lease for its entire
        # run; a wait that never returns keeps that lease forever, and Task Scheduler then skips
        # every later run.  Measured on this box: a process wedged on OneDrive placeholder I/O
        # stayed unkillable for about an hour, which the old proc.wait() would have sat through.
        unbounded = sorted(node.lineno for node in ast.walk(self.tree)
                           if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                           and node.func.attr == 'wait'
                           and not any(kw.arg == 'timeout' for kw in node.keywords))
        self.assertEqual([], unbounded,
                         'unbounded .wait() at weekly_refresh.py line(s) %s' % (unbounded,))


class WeeklyRefreshStepTimeouts(unittest.TestCase):

    # Derived ONCE, 2026-09-19, by AST-walking ``git show HEAD:fec/weekly_refresh.py`` for every
    # Call to the name ``step`` and reading its ``timeout=`` integer literal: 49 calls, every one
    # of them explicit.  Pinned as a literal so this suite never needs git at run time.  Routing
    # the launcher through the bounded helper is about the SHAPE of the wait, never its length --
    # a bound that moved here moved by accident, and these are per-step numbers Paul set from
    # measurement (600 s for a CSV regenerator up to 129600 s for the full press walk).
    HEAD_TIMEOUTS = (600, 600, 600, 600, 600, 600,
                     900, 900, 900, 900, 900, 900, 900, 900, 900, 900, 900, 900, 900,
                     1200, 1200, 1200,
                     1800, 1800, 1800, 1800, 1800, 1800, 1800, 1800, 1800, 1800, 1800, 1800,
                     1800, 1800,
                     3000, 3000,
                     3600, 3600, 3600,
                     5400,
                     7200, 7200,
                     14400, 14400, 14400,
                     21600,
                     129600)

    def test_17_every_step_timeout_is_exactly_what_it_was(self):
        tree = ast.parse(_read_source(_WEEKLY_REFRESH_PATH))
        found = []
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == 'step'):
                continue
            keyword = next((kw for kw in node.keywords if kw.arg == 'timeout'), None)
            self.assertIsNotNone(keyword, 'a step() call left its timeout to the default')
            self.assertIsInstance(keyword.value, ast.Constant)
            found.append(keyword.value.value)
        self.assertEqual(49, len(self.HEAD_TIMEOUTS))
        self.assertEqual(len(self.HEAD_TIMEOUTS), len(found))
        self.assertEqual(sorted(self.HEAD_TIMEOUTS), sorted(found))


if __name__ == '__main__':
    unittest.main()

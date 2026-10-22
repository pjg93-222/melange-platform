# -*- coding: utf-8 -*-
"""Bounded child-process launches for the chain, the kernel runner and the weekly refresh.

An unbounded launcher turns one frozen child into a silent, unbounded stall.  On
2026-09-19 a kernel run sat 14 minutes because one suite's child froze inside
``import scipy`` -- no CPU, no I/O, no output -- and nothing in the launch path had a
timeout.  In the chain the same freeze is worse: the frozen step also holds
``.pipeline_run.lock`` for as long as it lasts, so nothing else may publish either.  Worst of
the three is fec/weekly_refresh.py, the UNATTENDED Sunday job: its timeout path ran an unbounded
taskkill and then an unbounded ``proc.wait()``, so a child wedged in kernel I/O (measured here: a
process stuck on OneDrive placeholder I/O stayed unkillable for about an hour) held the refresh
open, holding the writer lease, until the scheduler skipped every later run.  Nobody was watching.

The shape is brain DE-026, measured on this box: NEVER
``subprocess.run(capture_output=True, timeout=...)``.  Its timeout path re-enters
``communicate()`` to drain the pipes and can block there, so that timeout bounds the wait
but not the call.  Here both streams go to REAL temporary files (a pipe that fills would
block the child and force exactly that drain), the wait is ``Popen.wait(timeout)``, and a
child that overruns is killed -- process TREE first, because chain steps spawn their own
children (R, node, PowerShell) and an orphan keeps holding files and locks.

The two bounds are sized from measurement, not taste (2026-09-19, over the 677 recorded
chain step-runs in the Glass Box ``pipeline.step`` stream, 78 steps):

  * The slowest chain step EVER recorded took 4,148.7 s (``personal_texture_layer.py``,
    whose own median is 99.7 s); the whole chain's median is 13.1 min and its maximum
    261.6 min.  CHAIN_STEP_TIMEOUT_SECONDS is 10800 s -- 2.6x that worst step -- so no
    step that has ever run legitimately can trip it.
  * A healthy kernel run is about 18 minutes for all ~247 suites together, so no single
    suite can legitimately need 30.  KERNEL_SUITE_TIMEOUT_SECONDS is 1800 s: more than a
    whole healthy kernel run, spent on one suite.

On Windows 8 and later the wait is a ``WaitForSingleObject`` timeout, which does NOT count
time the machine spends asleep, so a closed laptop lid cannot trip either bound.

A timeout is a FAILURE WITH ITS OWN NAME, never a skip.  The result carries returncode 124,
``timed_out`` True, and a ``timeout_note`` naming the pid and the arguments; the note is
also appended to stderr, so every consumer that already prints a stderr tail says TIMED OUT
without being changed, and no timeout can pass for a refusal some caller expects.

A caller that passes ``log_path`` keeps the child's output.  The streams are otherwise gone
once the verdict is read, and the unattended callers print only a 300-character stderr tail:
on 2026-10-18 a step killed at its 3,600 s bound and a step whose R child exited 1 were both
undiagnosable because nothing but that tail survived.  The log is written on every exit --
success, failure, the timeout kill, an interrupt -- from the same temp files the verdict is read
from.  A child's own stdout buffer dies with it, so a killed child shows only what it had
flushed; the weekly refresh sets PYTHONUNBUFFERED for its children for exactly that reason.

Import-safe (no I/O, no subprocess, no environment read at import) and stdlib-only, so the
chain runner can import it before anything else exists.  No environment dial: the repo
catalogues every ``os.environ`` dial and this module adds none -- both bounds are module
constants, and a caller that needs a different one passes ``timeout`` explicitly.
"""
import os
import signal
import subprocess
import sys
import tempfile
import time

# 2.6x the slowest chain step ever recorded (4,148.7 s), for fec/run_pipeline.py.
CHAIN_STEP_TIMEOUT_SECONDS = 3 * 60 * 60
# More than a whole healthy kernel run (~18 min for ~247 suites), for ONE suite,
# for fec/tests/run_tests.py.
KERNEL_SUITE_TIMEOUT_SECONDS = 30 * 60
# The conventional shell code for "killed by a timeout".  Deliberately NOT 2: the chain
# tolerates exit code 2 from a display-only step, and a timeout must never be tolerated.
TIMED_OUT_RETURNCODE = 124
# Every wait after the bound has expired is itself bounded; an unbounded wait here would
# reintroduce the stall this module exists to remove.
KILL_WAIT_SECONDS = 30
# The note quotes the arguments, not the interpreter path, and stays short enough to sit in
# a printed step line and a Glass Box summary.
NOTE_ARGV_CHARS = 300
# Per stream, per persisted log.  The TAIL is kept: a failing or hung child explains itself last.
LOG_KEEP_BYTES = 2 * 1024 * 1024


class BoundedResult(object):
    """What the old text-mode capture returned, plus what the bound did.

    ``stdout``/``stderr`` are str, exactly what
    ``capture_output=True, text=True, encoding='utf-8', errors='replace'`` produced.
    ``timeout_note`` is '' unless the child was killed for overrunning ``timeout``.
    ``log_path`` is the persisted log (see run_bounded), or None when none was asked for or it
    could not be written.
    """

    def __init__(self, returncode, stdout, stderr, timed_out, pid, seconds, timeout_note,
                 log_path=None):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.timed_out = timed_out
        self.pid = pid
        self.seconds = seconds
        self.timeout_note = timeout_note
        self.log_path = log_path

    def __repr__(self):
        return ('BoundedResult(returncode=%r, timed_out=%r, pid=%r, seconds=%.3f)'
                % (self.returncode, self.timed_out, self.pid, self.seconds))


def _universal_text(raw):
    """Decode exactly as the text-mode capture did: utf-8 with replacement, then universal
    newlines.  A producer that prints an undecodable byte must not raise here -- it did not
    raise before, and a decoding error in the reporting path would hide the real failure."""
    return raw.decode('utf-8', 'replace').replace('\r\n', '\n').replace('\r', '\n')


def _persist_log(path, argv, status, out_file, err_file):
    """Copy both streams, tail-capped at LOG_KEEP_BYTES each, into ``path``; return ``path``.

    Best effort, returns None on ANY exception: a full disk or a locked folder must never turn the
    child's verdict into an exception (on the interrupt path it would also replace the
    KeyboardInterrupt in flight), and the caller must not name a file that is not there.
    Raw bytes, not decoded text -- the log is the evidence, so it carries what the child wrote.
    """
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, 'wb') as log:
            log.write(('# argv: %s\n# status: %s\n' % (' '.join(str(part) for part in argv), status))
                      .encode('utf-8', 'replace'))
            for name, stream in (('stdout', out_file), ('stderr', err_file)):
                size = stream.seek(0, os.SEEK_END)
                kept = min(size, LOG_KEEP_BYTES)
                stream.seek(size - kept)
                log.write(('\n===== %s (%s of %d bytes) =====\n'
                           % (name, 'all' if kept == size else 'last %d' % kept, size)).encode('ascii'))
                log.write(stream.read())
    except Exception:
        return None
    return path


def kill_tree(process, own_group=False):
    """Kill the child AND everything it spawned.

    Chain steps spawn their own children (R, node, PowerShell), and killing only the parent
    would leave those running as orphans, holding open the files and locks the next step
    needs.  Windows has no process group to signal here, so ``taskkill /F /T`` walks the
    tree.  That call is itself bounded -- a hung killer would reintroduce the unbounded wait
    this module exists to remove -- and its return code is ignored on purpose: by the time
    it runs the tree may already be gone, which is success, not failure.  ``process.kill()``
    then runs on every platform as the backstop.

    ``own_group`` says whether the child LEADS its own process group, which it does only when
    it was launched with ``start_new_session=True``.  Then, and only then, may the group be
    signalled: with own_group False the group is the CALLER'S OWN, and killing it would kill
    the chain runner, the kernel runner or the weekly refresh itself.  UNTESTED on this box
    (Windows only); it does not touch the Windows path above.
    """
    if sys.platform == 'win32':
        killer = None
        try:
            killer = subprocess.Popen(['taskkill', '/F', '/T', '/PID', str(process.pid)],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            killer = None
        if killer is not None:
            try:
                killer.wait(timeout=KILL_WAIT_SECONDS)
            except subprocess.TimeoutExpired:
                try:
                    killer.kill()
                    killer.wait(timeout=KILL_WAIT_SECONDS)
                except (OSError, subprocess.TimeoutExpired):
                    pass
    elif own_group:
        # Already gone, or not ours to signal: either way the backstop below is what is left.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    try:
        process.kill()
    except OSError:
        pass


def run_bounded(argv, timeout, env=None, cwd=None, creationflags=0, start_new_session=False,
                on_start=None, log_path=None):
    """Run ``argv`` to completion, or kill its process tree after ``timeout`` seconds.

    Returns a BoundedResult.  ``stdin`` is deliberately not passed, so the child inherits it
    exactly as it did from the ``subprocess.run`` calls this replaced.  A Popen failure (a
    missing executable, say) propagates unchanged, as it did from ``subprocess.run``: a
    mistyped command must stay a loud error, not become a timeout.

    Any OTHER exception raised while waiting -- a Ctrl-C on a console chain run, SystemExit --
    kills the tree before it propagates, which is what ``subprocess.run``'s own
    ``except: process.kill(); raise`` did.  It matters most in the chain: that exception
    unwinds ``with pipeline_run_lock()``, so the lock file is REMOVED, and an orphaned step
    still writing while a second chain is free to start is the mixed-vintage publish that lock
    exists to prevent.

    ``creationflags`` reaches Popen only on Windows, where that parameter exists; POSIX Popen
    rejects a non-zero value outright, and passing it at all would make every POSIX launch a
    ValueError.  ``start_new_session`` is passed everywhere (Windows ignores it) and is what
    decides whether the overrun and interrupt kills may signal the child's process GROUP; see
    kill_tree.  The weekly refresh needs both: it launches every step in its own process group
    so a console CTRL+C cannot reach a producer mid-write.

    ``on_start`` is called with the live Popen the instant it exists, INSIDE the guarded region
    and BEFORE the wait begins, which is how a caller adopts the child into its own orphan
    guard.  Adopting it any later would leave a window in which an abort finds no tree to kill.
    Because it is inside the guard, an on_start that raises takes the interrupt path: the tree
    dies first, then the exception propagates.

    ``log_path``, when given, receives a log of the child's argv, its outcome and both streams
    (tail-capped, see _persist_log) on EVERY way out of the wait: normal exit, the timeout kill
    and the interrupt kill.  It changes nothing else: the result, the stderr text and the
    exceptions are the same with or without it, and a log that cannot be written leaves
    ``BoundedResult.log_path`` None instead of raising.
    """
    if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or not 0 < timeout < float('inf')):
        raise ValueError('timeout must be a positive finite number of seconds, got %r' % (timeout,))
    started = time.monotonic()
    timed_out = False
    launch = {}
    if sys.platform == 'win32':
        launch['creationflags'] = creationflags
    # Real files, never pipes: a full pipe blocks the child, and draining it on the timeout
    # path is the re-entrant communicate() DE-026 forbids.  A pipe is also what lets a detached
    # GRANDCHILD that inherited it keep the reader alive long after the child itself is gone.
    with tempfile.TemporaryFile() as out_file, tempfile.TemporaryFile() as err_file:
        process = subprocess.Popen(argv, stdout=out_file, stderr=err_file, env=env, cwd=cwd,
                                   start_new_session=start_new_session, **launch)
        try:
            if on_start is not None:
                on_start(process)
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            returncode = TIMED_OUT_RETURNCODE
            kill_tree(process, own_group=start_new_session)
            try:
                process.wait(timeout=KILL_WAIT_SECONDS)
            except subprocess.TimeoutExpired:
                # An unkillable child is still not allowed to hold this call open.
                try:
                    process.kill()
                    process.wait(timeout=KILL_WAIT_SECONDS)
                except (OSError, subprocess.TimeoutExpired):
                    pass
        except BaseException:
            # Parity with the subprocess.run this replaced (its bare `except: kill(); raise`).
            # An interrupt here unwinds `with pipeline_run_lock()` in the chain, removing the
            # lock file, so a child left running would keep writing while a second chain is
            # free to start.  The TREE dies, not just the parent: an interrupted step's
            # grandchildren hold the same files.  A failing on_start lands here too.
            kill_tree(process, own_group=start_new_session)
            try:
                process.wait(timeout=KILL_WAIT_SECONDS)
            except (OSError, subprocess.TimeoutExpired):
                pass
            if log_path:
                _persist_log(log_path, argv, 'INTERRUPTED; process tree killed (pid %d)' % process.pid,
                             out_file, err_file)
            raise
        timeout_note = ''
        if timed_out:
            timeout_note = ('TIMED OUT after %s s; process tree killed (pid %d): %s'
                            % (timeout, process.pid,
                               ' '.join(str(part) for part in argv[1:])[:NOTE_ARGV_CHARS]))
        saved = (_persist_log(log_path, argv, timeout_note or 'exit %d' % returncode, out_file, err_file)
                 if log_path else None)
        out_file.seek(0)
        err_file.seek(0)
        stdout = _universal_text(out_file.read())
        stderr = _universal_text(err_file.read())
    if timed_out:
        stderr += '\n' + timeout_note
    return BoundedResult(returncode, stdout, stderr, timed_out, process.pid,
                         time.monotonic() - started, timeout_note, saved)

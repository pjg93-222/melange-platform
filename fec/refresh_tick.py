# -*- coding: utf-8 -*-
"""REFRESH TICK — run whatever refresh work is DUE and not good, then publish if it may.

WHY THIS EXISTS.  ``fec/weekly_refresh.py`` is ONE run of 49 steps that either finishes or is
lost.  Its worst path budgets 76.4 h, this laptop dies about every two days, and it has not
completed since 2026-07-19: it cannot finish even in principle.  Owner, 2026-09-19, choosing this
over repairing that run: *"Yeah go forward"*.

WHAT A TICK IS.  Every fire -- the weekly task, a boot, a logon, the collector supervisor -- is
SHORT.  It runs the steps that are due and not already good, each one isolated and bounded, under
one hard total budget, and then computes and publishes only if the publication contract passes.
There is no run that spans days, because state lives PER STEP, not per run: a crash costs one
step and the next tick continues from there.  Owner, on the same day: *"just make sure that the
checkpoint/fail-save for one worker doesn't apply to every single one and stop the whole
process"*, *"make the weekly poll that continuously updates"*, *"The lock collision needs
verification logic and retry"*, *"if a fail is detected it is filed and then returned too once
collection for that session is complete"*, *"We will let it run, making sure it is independent
of AI"*.

STRANGLER.  This runner does not modify, import or run ``weekly_refresh.py``, ``run_pipeline.py``
or any producer (import == execution in this repo).  The scheduled task keeps launching the old
orchestrator until Paul flips it; the step table is pinned to that script by an AST test so the
two cannot drift while both exist.

NOT IN v1, deliberately: no backup/restore of last-good outputs -- a torn source waits for its
next success; no per-source coverage thresholds beyond the zero floor -- every other shrink ratio
is recorded so thresholds can be set from data later; the daily congress pipeline stays a separate
orchestrator; press-walk internals, the AI degrade and the Excel dependency are separate changes;
no scheduled-task changes, and the legacy ``weekly_refresh.log`` / ``weekly_refresh_status.json``
are NOT written here (cutover needs collector_supervisor's GATE-1 to read this runner's marker).

  python fec/refresh_tick.py --dry-run      what is due, why, and whether it could publish
  python fec/refresh_tick.py --status       the current state, rendered; no locks, no writes
  python fec/refresh_tick.py                one tick

Nothing happens at import: ``main(argv)`` is real, and a test may build :class:`Tick` directly.
"""
import argparse
import atexit
import datetime
import json
import os
import signal
import subprocess
import sys
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import bounded_subprocess
import refresh_state as state_store
import refresh_table as table_module
from refresh_state import (COVERAGE_OK, COVERAGE_SUSPECT, DEPENDENCY, EXIT, FAILED, INTERRUPTED,
                           KILLED, NEVER, NOT_CONFIGURED, OK, PARTIAL, RUNNING, SKIPPED,
                           SUCCEEDED, TIMEOUT)

# ---- constants ------------------------------------------------------------------------------
SCHEMA = 1
# The project's own existing vintage number: nothing older than this may be published as current.
WINDOW_DAYS = 14
# One tick's whole budget.  18 h leaves the weekly cadence room for a second fire in the same day.
BUDGET_HOURS = 18.0
# The daily job's 5 h 40 m hold on 2026-09-13 is the longest ever measured here, so six hours of
# polling is longer than any real hold, and a minute between attempts costs nothing.
LEASE_WAIT_HOURS = 6.0
LEASE_POLL_SECONDS = 60.0
LEASE_EVENT_SECONDS = 900.0
# A step launched with less than this left would be killed before it could do anything useful,
# but a step already chosen still deserves a floor rather than a one-second timeout.
MIN_STEP_TIMEOUT_SECONDS = 60
# No single step may spend more than this share of one tick's budget.  The press walk's table
# timeout is 36 h against an 18 h tick, so its effective timeout was always "whatever is left":
# a hung walk ate the whole tick and starved every later step, on every tick, where the old
# orchestrator at least passed it after 36 h.  The walk's measured runtime is 3 h 13 m; half of
# 18 h is nearly three times that, and it leaves the other half for everything else.
MAX_STEP_BUDGET_FRACTION = 0.5
# Extra runs of a FILED failure once the collection pass is complete.
RETRY_ATTEMPTS = 2
# A step attempted this recently is not retried by cadence alone; a new dependency result or
# --force-due overrides this, because those are new information rather than the same clock.
MIN_ATTEMPT_GAP_HOURS = 1.0
GLASSBOX_SOURCE = 'moneyball-refresh-tick'
CRASH_ENV = 'REFRESH_TICK_CRASH_AT'

CHAIN_STEP = 'FULL PIPELINE + dashboards'
PUBLISH_STEP = 'prepare dashboard deployment'

# Steps whose requirement is satisfied by an ATTEMPT rather than a success, because they report on
# what is ON DISK: the press intake audit must audit whatever the walk left behind, including a
# walk that failed, and the zero-row review must review whatever the fold promoted.
ATTEMPT_TRIGGERED = {
    'press intake audit': ('press delta refresh',),
    'press zero-row review': ('fold promoted press runs',),
}

# ---- outcomes and their exit codes ------------------------------------------------------------
# N1: an unpublished tick NEVER exits 0 (the one exception, NOTHING_DUE, is "nothing changed, the
# last publication still stands").  N2: a hard failure after the chain reported COMPLETE is never
# reported as success -- the publication record is written only when the chain AND the publication
# step are both satisfied in the same tick.
PUBLISHED = 'PUBLISHED'
PUBLISHED_DEGRADED = 'PUBLISHED_DEGRADED'
NOTHING_DUE = 'NOTHING_DUE'
COLLECTED_NOT_PUBLISHED = 'COLLECTED_NOT_PUBLISHED'
COMPUTE_FAILED = 'COMPUTE_FAILED'
BLOCKED = 'BLOCKED'
FATAL_PRESTEP = 'FATAL_PRESTEP'
EXIT_CODES = {PUBLISHED: 0, PUBLISHED_DEGRADED: 0, NOTHING_DUE: 0,
              COLLECTED_NOT_PUBLISHED: 1, COMPUTE_FAILED: 1, BLOCKED: 75, FATAL_PRESTEP: 1}

# ---- phases (what the marker says while a tick is running) ------------------------------------
PHASE_STARTING = 'STARTING'
PHASE_WAITING = 'WAITING_FOR_LEASE'
PHASE_RECONCILE = 'RECONCILING'
PHASE_COLLECT = 'COLLECTING'
PHASE_RETRY = 'RETRYING_FILED'
PHASE_COMPUTE = 'COMPUTING'
PHASE_IDLE = 'IDLE'

# ---- the runner's own files (never counted as a step's output) --------------------------------
ENGINE_DIR = 'data/engine'
STATE_NAME = 'refresh_state.json'
STATUS_NAME = 'refresh_tick_status.json'
TICKS_NAME = 'refresh_ticks.jsonl'
LOG_NAME = 'refresh_tick.log'
REVIEW_NAME = 'refresh_review.json'
MANIFEST_DIR = 'refresh_manifests'
INSTANCE_LOCK_NAME = '.refresh_tick.lock'
_RUNNER_FILES = frozenset(
    '%s/%s' % (ENGINE_DIR, name)
    for name in (STATE_NAME, STATUS_NAME, TICKS_NAME, LOG_NAME, REVIEW_NAME, INSTANCE_LOCK_NAME))
# Which of the project ROOT's own files can be a producer's output (social_media.db,
# press_text_cache.jsonl, ranks_python.json, the workbook, the dashboards).  Everything else at
# the root -- notes, documents, scripts -- is a human's, and is never evidence that a step wrote.
ROOT_OUTPUT_SUFFIXES = ('.db', '.sqlite', '.jsonl', '.ndjson', '.json', '.csv', '.parquet',
                        '.xlsx', '.xlsm', '.html')


class FatalPreStep(Exception):
    """Something that makes the whole table unrunnable; nothing is launched."""


class MissingProducer(Exception):
    """A table producer that is not on disk.  One FILED step, never a fatal pre-step error.

    The tick-only rows are written by other lanes in this same tree, and a row whose producer has
    not landed yet must cost exactly that row -- killing the whole refresh because one new script
    is missing is the 2026-09-10 failure (two unrelated __init__.py stopped the refresh in 0.2 s
    before step 1) in a different costume.
    """


# The one file a producer writes OUTSIDE the project: the production press URL baseline the fold
# appends to -- ``DEFAULT_BASELINE`` in fec/collect/press_baseline_fold.py, which is
# ``paths.DRAFT`` plus this name.  It is RESTATED here, not imported: that module imports
# build_press_engine_feed.py, which is step 21's own producer, and this runner imports no
# producer.  ``--dry-run`` and ``--status`` promise to change no byte, and that promise must not
# rest on another lane keeping its imports free of side effects.  test_refresh_tick pins the two
# definitions against each other, so they cannot drift silently.
PRESS_BASELINE_NAME = 'all_press_release_index.csv'


def external_files(root):
    """Absolute paths of named producer outputs that live outside ``root``.

    ``paths.DRAFT`` is resolved against the ``paths.ROOT`` that was live at import, so the
    baseline is RE-ANCHORED on this tick's root: a test driving a temp root must not reach into
    the real ``Draft Systems``.  Anything unreadable yields no extra file at all, which costs
    coverage of one file and can never wrongly block a publication.
    """
    try:
        from paths import DRAFT, ROOT as paths_root
        baseline = os.path.join(DRAFT, PRESS_BASELINE_NAME)
    except Exception:
        return ()
    root = os.path.abspath(str(root))
    if os.path.abspath(paths_root) == root:
        return (baseline,)
    try:
        return (os.path.normpath(os.path.join(root, os.path.relpath(baseline, paths_root))),)
    except ValueError:
        return (baseline,)


def scan_spec(root):
    """What counts as a step's OUTPUT: (recursive trees, top-level dirs, extra files).

    Producers write in three shapes, so the scan has three.  ``data/`` and ``outputs/`` are walked
    in full -- the sealed press run directories under ``outputs/`` are themselves evidence.  The
    project ROOT contributes its OWN data-shaped files only (:data:`ROOT_OUTPUT_SUFFIXES`):
    ``social_media.db``, ``press_text_cache.jsonl`` and ``ranks_python.json`` live there, while
    walking the root recursively would pull in the entire checkout for nothing, and the notes and
    documents a human keeps there are excluded by :func:`runner_owned`.  Outside the project
    there is one named file, the press fold's URL baseline.

    Everything here is paid for on every step of every tick (two snapshots per step, the second
    reused as the next step's first), so the shape is deliberately narrow rather than "walk
    everything and let the excludes sort it out".
    """
    return ((os.path.join(root, 'data'), os.path.join(root, 'outputs')),
            (root,),
            external_files(root))


def runner_owned(key):
    """True for what is excluded from every snapshot.

    Counting the runner's own state write as evidence that a step wrote would make every step
    torn.  Logs and lock files change for reasons that have nothing to do with a producer's
    outputs, ``.git`` and ``__pycache__`` are not outputs at all, and a temp file is by definition
    not the thing a step produced.
    """
    if key in _RUNNER_FILES:
        return True
    if key.startswith('%s/%s/' % (ENGINE_DIR, MANIFEST_DIR)):
        return True
    path = key[len(state_store.EXTERNAL_PREFIX):] if key.startswith(
        state_store.EXTERNAL_PREFIX) else key
    parts = path.replace('\\', '/').split('/')
    if '.git' in parts or '__pycache__' in parts:
        return True
    name = parts[-1]
    if name.endswith(('.lock', '.tmp', '.part', '.log')):
        return True
    # Foreign noise that is never a producer's output.  An open Office document drops a ``~$``
    # lock file beside itself, and SQLite's ``-wal``/``-shm``/``-journal`` siblings churn while ANY
    # process has the database open (a killed writer cannot tear a WAL database; the ``.db`` file
    # itself still counts).  Counting either would mark whatever step happened to fail at that
    # moment TORN, and a torn evidence step blocks publication until its next success.
    if name.startswith('~$') or name.lower().endswith(('-wal', '-shm', '-journal')):
        return True
    # The project ROOT's own files: data-shaped ones only.  The root also holds 100+ notes and
    # documents a human edits at any hour, and a note saved during a failing step is not that
    # step writing before it died.  ``data/`` and ``outputs/`` stay watched in full.
    if len(parts) == 1 and not key.startswith(state_store.EXTERNAL_PREFIX):
        if not name.lower().endswith(ROOT_OUTPUT_SUFFIXES):
            return True
    return name.startswith('refresh_state.corrupt-') or name.startswith('.refresh')


def render_argv(argv, today=None):
    """Resolve the table's date tokens, in LOCAL date, exactly as weekly_refresh.py does.

    That script computes ``today = datetime.date.today()`` once and interpolates it; the tokens
    exist so the table can be static, and they are rendered HERE, at launch.
    """
    today = today if today is not None else datetime.date.today()
    out = []
    for item in argv:
        if isinstance(item, str):
            out.append(item)
            continue
        kind = item[0]
        if kind == 'yesterday':
            out.append(str(today - datetime.timedelta(days=1)))
        elif kind == 'today':
            out.append(str(today))
        elif kind == 'days_ago':
            out.append(str(today - datetime.timedelta(days=int(item[1]))))
        else:
            raise ValueError('unknown date token %r' % (item,))
    return out


def _crashpoint(name):
    """Die HARD at a named point, and only when the environment asks for it.

    ``os._exit`` skips atexit, the locks and every buffer, which is the only honest simulation of
    the power loss and the TerminateProcess this runner is built to survive.  The tests drive each
    point and then assert the next tick converges; nothing else ever sets this variable.
    """
    if os.environ.get(CRASH_ENV) == name:
        os._exit(137)


# =============================================================================================
# gates and configuration predicates the table names by string
# =============================================================================================
def _chain_tail_is_audit_full(run_pipeline_path, log):
    """True only if run_pipeline.py's CHAIN list still ENDS with audit_full.py.

    Tolerating the chain's non-zero exit is safe only because audit_full is its LAST step: every
    producer has already written, so a lone audit dissent cannot be masking unrun work.  Parsed
    TEXTUALLY -- importing run_pipeline would execute it -- and any uncertainty fails CLOSED.
    Same rule and same regexes as weekly_refresh.py:151-171; lifted, not imported.
    """
    import re
    block_re = re.compile(r'^CHAIN\s*=\s*\[(.*?)\n\]', re.M | re.S)
    entry_re = re.compile(r"\(\s*'([^']+)'\s*,\s*\[[^\]]*\]\s*\)")
    try:
        with open(run_pipeline_path, encoding='utf-8') as handle:
            source = handle.read()
    except OSError as error:
        log('   chain-tail guard: cannot read run_pipeline.py (%s)' % type(error).__name__)
        return False
    block = block_re.search(source)
    if not block:
        log('   chain-tail guard: no "CHAIN = [ ... ]" block found in run_pipeline.py')
        return False
    entries = entry_re.findall(block.group(1))
    if not entries:
        log('   chain-tail guard: no (\'name\',[...]) tuples parsed from the CHAIN block')
        return False
    return entries[-1] == 'audit_full.py'


def audit_failure_gate(stdout, stderr, log, run_pipeline_path):
    """(tolerate, names) for the chain step, with weekly_refresh.py's exact meaning.

    The ratified list lives in fec/audit_failure_gate.py, the ONE source both unattended gates
    share.  Tolerance additionally requires the chain tail to still be audit_full.py.
    """
    try:
        from audit_failure_gate import tolerate_known_gated
    except Exception as error:                       # pragma: no cover - the module is shipped
        log('   audit gate unavailable (%s) — abort stands' % type(error).__name__)
        return False, []
    tolerate, names = tolerate_known_gated(stdout, stderr, log=log)
    if tolerate and not _chain_tail_is_audit_full(run_pipeline_path, log):
        log('   chain tail is no longer audit_full.py — tolerance refused')
        return False, names
    return tolerate, names


def deployment_not_configured():
    """Reason string when this box cannot deploy, or '' when it can.

    Paul, on the deployment step: *"Don't need to do that"*.  A box without the password file is
    a box that was never meant to deploy, so the step is SKIPPED_NOT_CONFIGURED -- recorded, and
    explicitly NOT a failure, because a publication that skipped deployment is still a publication.
    """
    configured = os.environ.get('DASHBOARD_PASSWORD_FILE')
    if not configured:
        return 'no DASHBOARD_PASSWORD_FILE configured'
    if not os.path.exists(configured):
        return 'DASHBOARD_PASSWORD_FILE %s does not exist' % configured
    return ''


GATES = {'audit_failure_gate': audit_failure_gate}
SKIPS = {'deployment_not_configured': deployment_not_configured}


# =============================================================================================
# the tick
# =============================================================================================
class Tick(object):
    """One fire.  Everything it touches is under ``root``; everything timed is an argument.

    The timings are constructor arguments and not module constants so a test can scale the lease
    poll down to milliseconds without editing a shipped bound -- editing the constant would test
    a runner nobody runs.
    """

    def __init__(self, root, budget_hours=BUDGET_HOURS, lease_wait_hours=LEASE_WAIT_HOURS,
                 only=(), skip_compute=False, force_due=(), force_compute=False,
                 accept_zero=(),
                 table=None, emit=None, log=None, now=None, sleep=None, python=None,
                 lease_poll_seconds=LEASE_POLL_SECONDS,
                 lease_event_seconds=LEASE_EVENT_SECONDS,
                 window_days=WINDOW_DAYS, retry_attempts=RETRY_ATTEMPTS,
                 min_attempt_gap_hours=MIN_ATTEMPT_GAP_HOURS):
        self.root = os.path.abspath(str(root))
        self.fec_dir = os.path.join(self.root, 'fec')
        self.engine_dir = os.path.join(self.root, ENGINE_DIR.replace('/', os.sep))
        self.manifest_dir = os.path.join(self.engine_dir, MANIFEST_DIR)
        self.state_path = os.path.join(self.engine_dir, STATE_NAME)
        self.status_path = os.path.join(self.engine_dir, STATUS_NAME)
        self.ticks_path = os.path.join(self.engine_dir, TICKS_NAME)
        self.log_path = os.path.join(self.engine_dir, LOG_NAME)
        self.review_path = os.path.join(self.engine_dir, REVIEW_NAME)
        self.instance_lock_path = os.path.join(self.engine_dir, INSTANCE_LOCK_NAME)
        self.pipeline_lock_path = os.path.join(self.root, '.pipeline_run.lock')
        self.run_pipeline_path = os.path.join(self.fec_dir, 'run_pipeline.py')
        self.scan_recursive, self.scan_top_level, self.scan_extra = scan_spec(self.root)

        self.budget_hours = float(budget_hours)
        self.lease_wait_hours = float(lease_wait_hours)
        self.lease_poll_seconds = float(lease_poll_seconds)
        self.lease_event_seconds = float(lease_event_seconds)
        self.window_days = int(window_days)
        self.retry_attempts = int(retry_attempts)
        self.min_attempt_gap_hours = float(min_attempt_gap_hours)
        self.only = tuple(only or ())
        self.skip_compute = bool(skip_compute)
        self.force_due = tuple(force_due or ())
        self.force_compute = bool(force_compute)
        # Steps whose NEXT zero-floor verdict the owner has accepted in advance.  A source that
        # has truly gone quiet would otherwise block publication until someone edited the state
        # file by hand, which this project forbids; the acceptance is recorded for Paul to see.
        self.accept_zero = tuple(accept_zero or ())
        self.table = tuple(table if table is not None else table_module.TABLE)
        self.by_name = table_module.by_name(self.table)
        self.python = python or sys.executable
        self.now = now if now is not None else state_store.utcnow
        self.sleep = sleep if sleep is not None else time.sleep

        self._log_sink = log
        self._log_handle = None
        self._emit_sink = emit
        self._gb_ids = None
        self.state = state_store.RefreshState(self.state_path, now=self.now)
        self.marker = None
        self.deadline = None
        self.started_utc = None
        self.phase = PHASE_STARTING
        self.current_step = None
        self.waited_seconds = 0

        self.producers = {}
        self.missing_producers = []
        self.manifests = {}
        self.unavailable_manifests = set()
        self.manifest_shas = {}
        self.manifest_dirty = set()
        self.owner = {}
        self._fresh_snapshot = None

        # Steps the previous tick died inside.  The contract makes them due NOW: the one-hour
        # attempt gap exists to stop a healthy runner hammering a collector that just failed, and
        # applying it to a step killed by a power loss would stall convergence for an hour after
        # every crash -- on a laptop that dies about every two days, that is the whole point lost.
        self.due_now = set()
        self.attempted = []
        self.succeeded = []
        self.skipped_not_configured = set()
        self.partial = []
        self.filed = []
        self.blockers = []
        self.compute_failure = None
        self.unhandled = None
        self.published = None
        self.review_added = 0

        self._live_child = [None]
        # Set the instant a console event starts killing the tree, so the step the main thread is
        # about to report is recorded as KILLED rather than as its own exit code: the child died
        # because WE killed it, and a step recorded "exit 1" would look like a producer that
        # refused, which is the one thing the next tick must not believe.
        self._killing = False
        # The native console handler runs on a Windows-owned THREAD while the main thread may be
        # mid-commit.  Two threads serializing json.dumps over the same dict is a corrupt state
        # file in an unattended job, so every commit takes this lock -- and the handler gives up
        # after a second rather than eating the ~5 s grace, because a main thread that holds the
        # lock is already writing the record the handler wanted to write.
        self._commit_lock = threading.Lock()
        self._lease_context = None
        self._lease = None
        self._instance_handle = None
        self._shutdown = [False, False]
        self._console_handler = None

    # ---- observation -------------------------------------------------------------------
    def log(self, message):
        line = '[%s] %s' % (self.now().strftime('%Y-%m-%d %H:%M:%SZ'), message)
        if self._log_sink is not None:
            self._log_sink(line)
            return line
        try:
            print(line)
        except Exception:
            pass
        if self._log_handle is not None:
            try:
                self._log_handle.write(line + '\n')
                self._log_handle.flush()
            except Exception:
                pass
        return line

    def emit(self, kind, status, summary, **fields):
        """One Glass Box event.  House rule: every process emits, or it is not acceptable."""
        if self._emit_sink is not None:
            try:
                return self._emit_sink(kind, status, summary, **fields)
            except Exception:
                return None
        if self._gb_ids is None:
            try:
                from surfaces.glassbox_emit import emit as gb, new_run_id, new_correlation_id
                self._gb_ids = (gb, new_run_id('moneyball-refresh-tick'), new_correlation_id())
            except Exception:
                self._gb_ids = (None, '', '')
        gb, run_id, correlation_id = self._gb_ids
        if gb is None:
            return None
        fields.setdefault('run_id', run_id)
        fields.setdefault('correlation_id', correlation_id)
        fields.setdefault('source', GLASSBOX_SOURCE)
        try:
            return gb(kind, status, summary, **fields)
        except Exception:
            return None

    def open_observation(self):
        """Log, marker and Glass Box FIRST, before any lock: a blocked fire is never blind."""
        try:
            os.makedirs(self.engine_dir, exist_ok=True)
            os.makedirs(self.manifest_dir, exist_ok=True)
        except OSError:
            pass
        if self._log_sink is None:
            try:
                self._log_handle = open(self.log_path, 'a', encoding='utf-8', newline='\n')
            except OSError:
                self._log_handle = None
        self.marker = state_store.StatusMarker(self.status_path, log=self.log)

    def commit_state(self):
        """The one durable state write, serialized against the console handler's thread."""
        with self._commit_lock:
            return self.state.commit()

    def write_status(self, tick=None):
        if self.marker is None:
            return False
        return self.marker.write(state_store.render_status(
            self.state, self.phase, current_step=self.current_step,
            waited_seconds=self.waited_seconds, tick=tick))

    def set_phase(self, phase, current_step=None):
        self.phase = phase
        self.current_step = current_step
        self.write_status()

    # ---- locks -------------------------------------------------------------------------
    def acquire_instance_lock(self):
        """One tick at a time on this box.  A second fire says so and leaves; it never waits.

        It deliberately does NOT write the marker: the marker belongs to the instance that is
        actually running, and overwriting it would tell an unattended reader the live tick had
        stopped.  The Glass Box event IS this fire's record.
        """
        try:
            handle = open(self.instance_lock_path, 'a+b')
        except OSError as error:
            self.log('single-instance guard unavailable (%s) — continuing' % type(error).__name__)
            return True
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, IOError):
            handle.close()
            return False
        self._instance_handle = handle
        return True

    def release_instance_lock(self):
        handle, self._instance_handle = self._instance_handle, None
        if handle is None:
            return
        try:
            handle.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except BaseException:
            pass
        try:
            handle.close()
        except BaseException:
            pass

    def acquire_lease(self):
        """Poll for the writer lease instead of dying on contention.

        Owner: *"make the weekly poll that continuously updates"* and *"The lock collision needs
        verification logic and retry"*.  The VERIFICATION is in pipeline_lock (a recycled pid can
        no longer wedge the lease, and no clock step can make a live holder look stale); the RETRY
        is here.  Every poll republishes the marker with a growing ``waited_seconds``, so a reader
        can tell a tick that is waiting from one that has frozen.
        """
        from pipeline_lock import (LOCK_OWNER_PID_ENV, LOCK_TOKEN_ENV, pipeline_run_lock,
                                   valid_inherited_lease)
        if os.environ.get(LOCK_TOKEN_ENV) or os.environ.get(LOCK_OWNER_PID_ENV):
            # An INHERITED lease claim validates now or never: it names a lease file that is gone
            # or belongs to someone else, and nothing a poll can wait for will change that.
            # Polling it burned the whole six-hour wait on a lease NOBODY held, with the
            # single-instance lock taken, so every other fire exited 75 as well.  A tick is a
            # top-level job; a claim that does not validate is a broken launch environment, and
            # that is fatal before step 1, loudly, in well under a second.
            if not valid_inherited_lease(self.pipeline_lock_path, direct_parent=True):
                raise FatalPreStep(
                    '%s / %s are set in the environment but do not name a live writer lease '
                    'held by this process\'s parent; clear them and run the tick again'
                    % (LOCK_TOKEN_ENV, LOCK_OWNER_PID_ENV))
        waited = 0.0
        next_event = self.lease_event_seconds
        why = ''
        while True:
            try:
                context = pipeline_run_lock(self.pipeline_lock_path, owner_kind='refresh_tick')
                lease = context.__enter__()
            except RuntimeError as error:
                why = str(error)
            else:
                if waited:
                    self.log('writer lease acquired after %.0fs of waiting' % waited)
                self._lease_context, self._lease = context, lease
                self._export_lease(lease)
                return True
            if not waited:
                self.log('WAITING FOR THE WRITER LEASE — %s' % why)
                self.emit('refresh.lease', 'running', 'waiting for the writer lease — %s' % why,
                          stage='lease', detail=why[:2000],
                          metadata={'blocked_by': why[:1000], 'waited_seconds': 0})
            self.waited_seconds = int(waited)
            self.set_phase(PHASE_WAITING, 'waiting for the pipeline writer lease')
            if waited >= self.lease_wait_hours * 3600:
                self.log('BLOCKED — writer lease still held after %.0fs: %s' % (waited, why))
                self.blockers.append(('writer lease', why[:300]))
                return False
            self.sleep(self.lease_poll_seconds)
            waited += self.lease_poll_seconds
            if waited >= next_event:
                self.emit('refresh.lease', 'running',
                          'still waiting for the writer lease — %.0fs' % waited, stage='lease',
                          detail=why[:2000], metadata={'waited_seconds': int(waited)})
                next_event += self.lease_event_seconds

    def _export_lease(self, lease):
        from pipeline_lock import child_lock_environment
        try:
            os.environ.update(child_lock_environment(lease))
        except Exception:
            pass

    def release_lease(self):
        from pipeline_lock import LOCK_OWNER_PID_ENV, LOCK_TOKEN_ENV
        os.environ.pop(LOCK_TOKEN_ENV, None)
        os.environ.pop(LOCK_OWNER_PID_ENV, None)
        context, self._lease_context = self._lease_context, None
        if context is None:
            return
        try:
            context.__exit__(None, None, None)
        except BaseException:
            pass

    # ---- the exit guard ----------------------------------------------------------------
    def adopt_child(self, process):
        """run_bounded's on_start hook: publish the live child BEFORE the wait begins."""
        self._live_child[0] = process

    def kill_live_child(self):
        """Kill the live child TREE before either lock is released.

        Without this, an abort deletes the writer lease while the child that inherited it keeps
        running: the next fire acquires a free lease and a second writer starts on the same
        artifacts.  The tree kill is bounded_subprocess's bounded ``taskkill /F /T``.
        """
        process = self._live_child[0]
        if process is None or process.poll() is not None:
            self._live_child[0] = None
            return
        try:
            self.log('KILLING step child pid %d before the writer lease is released' % process.pid)
        except BaseException:
            pass
        try:
            if os.name == 'nt':
                bounded_subprocess.kill_tree(process)
            else:
                bounded_subprocess.kill_tree(process, own_group=True)
            process.wait(timeout=bounded_subprocess.KILL_WAIT_SECONDS)
        except BaseException:
            pass
        finally:
            self._live_child[0] = None

    def console_shutdown(self, reason):
        """The console is going away: kill the tree, then record the step as killed.

        ``torn`` is left UNDETERMINED (None) rather than guessed: resolving it means walking the
        data roots, which does not fit in the ~5 s grace Windows allows.  The next tick's
        reconcile does exactly that walk against the persisted pre-snapshot.
        """
        if not self._shutdown[0]:
            self._shutdown[0] = True          # set BEFORE the kill: two threads must not both kill
            self._killing = True
            try:
                self.log('KILLED — console close/logoff/shutdown (%s)' % reason)
                self.kill_live_child()
            except BaseException:
                pass
        if not self._shutdown[1]:
            try:
                # One second, not forever: a main thread holding the lock is already writing this
                # step's record, and blocking here would spend the grace Windows allows.
                if (self.current_step and self.state.state_of(self.current_step) == RUNNING
                        and self._commit_lock.acquire(timeout=1.0)):
                    try:
                        self.state.record(self.current_step, state=FAILED, torn=None,
                                          failure={'kind': KILLED, 'exit': None,
                                                   'detail': 'console %s' % reason})
                        self.state.commit()
                    finally:
                        self._commit_lock.release()
                self.emit('refresh.tick', 'failed', 'KILLED — console %s' % reason,
                          stage=self.current_step or 'tick',
                          metadata={'step': self.current_step, 'reason': reason})
                self._shutdown[1] = True      # set AFTER: an interrupted write must be retried
            except BaseException:
                pass

    def install_console_control_handler(self):
        """Also take the NATIVE handler, which Windows calls on its OWN thread.

        This is the half a signal handler cannot do: the OS thread runs while the main thread is
        still blocked inside the step's wait (that wait releases the GIL), so the tree kill
        happens inside the grace instead of after it.  Returning FALSE leaves the handler chain
        intact, so Python's SIGBREAK handling still runs; console_shutdown is idempotent.
        """
        if os.name != 'nt':
            return False
        try:
            import ctypes
            from ctypes import wintypes
            events = {0: 'CTRL_C', 1: 'CTRL_BREAK', 2: 'CTRL_CLOSE', 5: 'CTRL_LOGOFF',
                      6: 'CTRL_SHUTDOWN'}
            prototype = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)

            def handler(event):
                self.console_shutdown(events.get(event, 'CTRL_%d' % event))
                return False

            self._console_handler = prototype(handler)   # must outlive the call: kernel32 keeps it
            return bool(ctypes.windll.kernel32.SetConsoleCtrlHandler(self._console_handler, True))
        except Exception as error:
            self.log('native console control handler unavailable: %s' % str(error)[:80])
            return False

    # ---- producers ---------------------------------------------------------------------
    def index_producers(self):
        """Index ONLY the basenames this table launches; a duplicate REAL producer is fatal.

        Indexing every .py under fec/ is what killed the refresh on 2026-09-10 (two unrelated
        __init__.py made the walk a duplicate-basename error and it died in 0.2 s before step 1).
        A table producer that is simply ABSENT is not fatal either: it fails its own step.
        """
        # The table names its gate and configuration hooks by STRING, so a typo would otherwise
        # surface as a KeyError in the middle of the compute phase, after hours of collection.
        for step in self.table:
            if step.gate and step.gate not in GATES:
                raise FatalPreStep('%s names an unknown gate %r' % (step.name, step.gate))
            if step.skip_if and step.skip_if not in SKIPS:
                raise FatalPreStep('%s names an unknown skip_if %r' % (step.name, step.skip_if))
        wanted = table_module.producer_names(self.table)
        found = {}
        for directory, subdirectories, files in os.walk(self.fec_dir):
            subdirectories[:] = sorted(
                name for name in subdirectories
                if name not in ('tests', 'attic', '__pycache__')
                and not name.startswith('_quarantine'))
            for name in sorted(files):
                if not name.endswith('.py') or name not in wanted:
                    continue
                full = os.path.join(directory, name)
                if name in found:
                    raise FatalPreStep('duplicate producer basename %s: %s and %s'
                                       % (name, found[name], full))
                found[name] = full
        self.producers = found
        self.missing_producers = sorted(wanted - set(found))
        if self.missing_producers:
            self.log('table producer(s) not on disk, each will FAIL its own step: %s'
                     % ', '.join(self.missing_producers))

    def producer_path(self, step):
        basename = table_module.producer_basename(step)
        if basename not in self.producers:
            raise MissingProducer('producer script not found: %s' % step.argv[0])
        return self.producers[basename]

    # ---- snapshots and manifests -------------------------------------------------------
    def snapshot(self):
        return state_store.snapshot(self.root, recursive=self.scan_recursive,
                                    top_level=self.scan_top_level,
                                    extra_files=self.scan_extra, exclude=runner_owned)

    def pre_snapshot_path(self, step):
        return os.path.join(self.manifest_dir, '_pre.%s.json' % step.slug)

    def manifest_path(self, step_name, sha):
        return os.path.join(self.manifest_dir,
                            '%s.%s.json' % (table_module.slug(step_name), sha))

    def load_manifests(self):
        """Read every manifest the state references, once, and build the ownership map.

        Ownership is "last writer owns": when a step's outputs include a file another step used
        to claim, that file leaves the other step's manifest.  Doing that at COMMIT time against
        an in-memory index costs one load pass per tick instead of 48 loads per step.
        """
        self.manifests, self.manifest_shas, self.owner = {}, {}, {}
        self.unavailable_manifests.clear()
        for step in self.table:
            sha = self.state.step(step.name).get('manifest_sha')
            if not sha:
                continue
            payload, problem, transient = state_store.read_manifest(
                self.manifest_path(step.name, sha))
            if transient:
                # Could not be OPENED.  That is this workstation's lock window (OneDrive, the
                # antivirus scanner), not a verdict on the manifest, and it used to erase the
                # step's recorded success durably. Preserve that history, but defer this step
                # and publication until its baseline can be read; otherwise a due collector
                # can silently replace a 60-row baseline with an accepted empty manifest.
                self.log('%-30s manifest %s could not be opened (%s) — the step keeps its '
                         'recorded state; its baseline is unknown for this tick'
                         % (step.name, sha, problem))
                self.unavailable_manifests.add(step.name)
                continue
            if problem:
                # Missing or not a manifest: no step is OK without a manifest that verifies it.
                # Due NOW -- the one-hour attempt gap is for a collector that refused, and this
                # collector never did.
                self.log('%-30s manifest %s %s — the step is unverified and due now'
                         % (step.name, sha, problem))
                self.state.record(step.name, manifest_sha=None, state=NEVER, last_ok_utc=None)
                self.due_now.add(step.name)
                continue
            self.manifests[step.name] = payload
            self.manifest_shas[step.name] = sha
            for relative in payload['files']:
                self.owner[relative] = step.name

    def verify_outputs(self):
        """Reconcile manifested outputs changed outside a successful step commit.

        Stat unchanged files first; hash only a changed stat. This includes other orchestrators
        and interrupted writers between ticks, while a timestamp-only touch is harmless.
        A damaged source is due immediately and cannot be certified by a successful no-op.
        """
        for name, manifest in self.manifests.items():
            bad = []
            for relative, expected in manifest.get('files', {}).items():
                actual = state_store.describe_file(self.root, relative, carry=expected)
                if (actual is None or not actual.get('sha256')
                        or actual['sha256'] != expected.get('sha256')):
                    bad.append(relative)
            if bad:
                entry = self.state.step(name)
                pending = set(entry.get('unverified_files') or ()) | set(bad)
                self.state.record(name, state=FAILED, torn=True,
                                  unverified_files=sorted(pending),
                                  failure={'kind': state_store.TORN, 'exit': None,
                                           'detail': 'manifested outputs changed: '
                                                     + ', '.join(bad)[:300]})
                self.due_now.add(name)
                self.log('%-30s outputs differ from the committed manifest; due now' % name)

    def referenced_manifests(self):
        keep = set()
        for step in self.table:
            sha = self.state.step(step.name).get('manifest_sha')
            if sha:
                keep.add(os.path.basename(self.manifest_path(step.name, sha)))
            keep.add('_pre.%s.json' % step.slug)
        return keep

    # ---- dueness -----------------------------------------------------------------------
    def _forced(self, name):
        # Accepting a step's zero-floor verdict means running it: the acceptance applies to the
        # outputs that run produces, never to whatever happens to be on disk.
        return 'all' in self.force_due or name in self.force_due or name in self.accept_zero

    def satisfied(self, name):
        """A step whose outputs stand: succeeded, not torn, coverage OK, inside the window."""
        entry = self.state.step(name)
        if entry.get('state') not in SUCCEEDED:
            return False
        if entry.get('torn') or entry.get('coverage') != COVERAGE_OK:
            return False
        age = state_store.hours_since(entry.get('last_ok_utc'), self.now())
        return age is not None and age <= self.window_days * 24

    def requirements_met(self, step):
        """(met, unmet-name).  A requirement is met when it succeeded in THIS tick, when it was
        merely ATTEMPTED this tick for the on-disk auditors, or when its last success still
        stands from an earlier tick."""
        triggers = ATTEMPT_TRIGGERED.get(step.name, ())
        for required in step.requires:
            if required in self.succeeded:
                continue
            if required in triggers and required in self.attempted:
                continue
            if self.satisfied(required):
                continue
            return False, required
        return True, None

    def due(self, step):
        """(due, why).  Dueness is per step and depends on nothing another step did, except an
        explicit ``requires`` that produced new information in this very tick."""
        entry = self.state.step(step.name)
        if self._forced(step.name):
            return True, 'forced'
        if step.name in self.due_now:
            return True, 'the previous tick died inside it'
        if entry.get('state') == RUNNING or entry.get('failure_pending_tick_end'):
            return True, 'the previous tick did not finish this attempt'
        failure = entry.get('failure')
        if isinstance(failure, dict) and failure.get('kind') in (KILLED, INTERRUPTED):
            # The contract's "due now".  The one-hour attempt gap exists to stop the runner
            # hammering a collector that refused on its own terms; a step whose process was
            # killed never got to refuse, and making it wait an hour after every crash is the
            # all-or-nothing stall this runner exists to remove.
            return True, 'the previous tick was killed inside it'
        triggered = ''
        triggers = ATTEMPT_TRIGGERED.get(step.name, ())
        for required in step.requires:
            if required in self.succeeded:
                triggered = '%s succeeded this tick' % required
                break
            if required in triggers and required in self.attempted:
                triggered = '%s was attempted this tick' % required
                break
        status = entry.get('state') or NEVER
        age = state_store.hours_since(entry.get('last_ok_utc'), self.now())
        if status not in SUCCEEDED:
            why = 'state %s' % status
        elif entry.get('torn'):
            why = 'outputs torn'
        elif entry.get('coverage') != COVERAGE_OK:
            why = 'coverage %s' % entry.get('coverage')
        elif age is None:
            why = 'no readable last success'
        elif step.cadence_h and age >= step.cadence_h:
            why = 'last success %.1f h ago, cadence %d h' % (age, step.cadence_h)
        elif triggered:
            why = triggered
        else:
            return False, 'fresh (%.1f h old)' % age
        if not triggered:
            gap = state_store.hours_since(entry.get('last_attempt_utc'), self.now())
            if gap is not None and gap < self.min_attempt_gap_hours:
                return False, 'attempted %.2f h ago (gap %.0f h)' % (gap,
                                                                     self.min_attempt_gap_hours)
        return True, why

    # ---- budget ------------------------------------------------------------------------
    def remaining_seconds(self):
        if self.deadline is None:
            return self.budget_hours * 3600
        return self.deadline - time.monotonic()

    def budget_left(self):
        return self.remaining_seconds() > 0

    def step_timeout(self, step):
        """The table's timeout, capped by what is left AND by one step's share of the tick."""
        share = self.budget_hours * 3600 * MAX_STEP_BUDGET_FRACTION
        return max(MIN_STEP_TIMEOUT_SECONDS,
                   int(min(step.timeout_s, share, max(0.0, self.remaining_seconds()))))

    # ---- running one step --------------------------------------------------------------
    def run_step(self, step, phase):
        """Launch one step and COMMIT its result.  Returns True when its outputs now stand.

        The protocol, in order: persist the pre-snapshot and write RUNNING; launch; snapshot
        again; on success build the manifest, run the zero floor, write the manifest files, then
        ONE atomic state write -- that write is the commit point.  On failure, diff the snapshots:
        anything that changed means the step wrote before it died, which is TORN.
        """
        if step.name in self.unavailable_manifests:
            self._note_blocker(step.name, 'manifest cannot be read; preserving its baseline')
            return False
        self.set_phase(phase, step.name)
        pre = self._fresh_snapshot if self._fresh_snapshot is not None else self.snapshot()
        try:
            state_store.write_json_atomic(self.pre_snapshot_path(step),
                                          {k: list(v) for k, v in pre.items()}, indent=None)
        except OSError as error:
            self.log('%-30s pre-snapshot not persisted (%s) — torn detection degrades to the '
                     'in-process copy' % (step.name, type(error).__name__))
        entry = self.state.step(step.name)
        started = time.monotonic()
        self.state.record(step.name, state=RUNNING, torn=bool(entry.get('torn')),
                          last_attempt_utc=state_store.stamp(self.now()),
                          attempts_since_ok=int(entry.get('attempts_since_ok') or 0) + 1,
                          failure=None, duration_s=None)
        self.commit_state()
        if step.name not in self.attempted:
            self.attempted.append(step.name)
        timeout = self.step_timeout(step)
        self.emit('refresh.step', 'running', '%s started' % step.name, stage=step.name,
                  metadata={'step': step.name, 'kind': step.kind, 'timeout_s': timeout,
                            'phase': phase})
        self.log('%-30s START   (timeout %ds, %s)' % (step.name, timeout, phase))

        launch_error = None
        run = None
        try:
            run = self._launch(step, timeout)
        except MissingProducer as error:
            launch_error = ('%s' % error)
        except Exception as error:                   # a launch failure is this step's failure
            launch_error = '%s: %s' % (type(error).__name__, str(error)[:160])
        _crashpoint('after_step_exit')
        post = self.snapshot()
        self._fresh_snapshot = post
        duration = time.monotonic() - started

        if launch_error is not None:
            return self._fail_step(step, pre, post, EXIT, None, launch_error, duration)
        if run.timed_out:
            return self._fail_step(step, pre, post, TIMEOUT, run.returncode,
                                   run.timeout_note[:400], duration)
        tail = (run.stdout or '').strip().split('\n')[-1:] or ['']
        if run.returncode == 0:
            self.log('%-30s OK    %5.0fs  %s' % (step.name, duration, tail[0][:100]))
            return self._commit_step(step, pre, post, OK, duration, run)
        if run.returncode in step.partial_codes:
            self.log('%-30s PARTIAL %3.0fs  exit %s — committed, not retried; the deferred work '
                     'is the downstream gates\' decision' % (step.name, duration, run.returncode))
            return self._commit_step(step, pre, post, PARTIAL, duration, run)
        if step.gate:
            tolerate, names = GATES[step.gate](run.stdout, run.stderr, self.log,
                                               self.run_pipeline_path)
            if tolerate:
                self.log('%-30s OK    %5.0fs  known Paul-gated audit dissent only (%d item(s))'
                         % (step.name, duration, len(names)))
                self.emit('refresh.tolerated_failure', 'completed',
                          'tolerated %d known Paul-gated audit failure(s) at %s'
                          % (len(names), step.name), stage=step.name,
                          detail='; '.join(names)[:2000],
                          metadata={'step': step.name, 'failures': list(names),
                                    'tolerated': True})
                return self._commit_step(step, pre, post, OK, duration, run, tolerated=names)
        self.log('%-30s FAIL  %5.0fs  exit %s' % (step.name, duration, run.returncode))
        self.log('   stderr: ' + (run.stderr or '')[-300:].replace('\n', ' | '))
        return self._fail_step(step, pre, post, EXIT, run.returncode,
                               (run.stderr or run.stdout or '')[-400:], duration)

    def _launch(self, step, timeout):
        """The ONE launch path.  Never ``subprocess.run(capture_output=True, timeout=...)``:
        its timeout path re-enters communicate() and can block there (DE-026)."""
        argv = [self.python, self.producer_path(step)] + render_argv(step.argv)[1:]
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
        # UTF-8 for the child: producers inherited a cp1252 stdout, so one that printed a check
        # mark died with UnicodeEncodeError and accented member names came back as U+FFFD.
        environment = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
        try:
            return bounded_subprocess.run_bounded(
                argv, timeout=timeout, cwd=self.fec_dir, env=environment, creationflags=flags,
                start_new_session=os.name != 'nt', on_start=self.adopt_child)
        finally:
            self._live_child[0] = None       # released on EVERY path: success, timeout, failure

    def _fail_step(self, step, pre, post, kind, returncode, detail, duration):
        if self._killing and kind == EXIT:
            kind, detail = KILLED, 'the console went away and this step\'s tree was killed'
        changed = state_store.diff(pre, post)
        entry = self.state.step(step.name)
        unverified = set(entry.get('unverified_files') or ()) | set(changed)
        torn = bool(entry.get('torn')) or bool(unverified)
        self.state.record(step.name, state=FAILED, torn=torn, duration_s=round(duration, 3),
                          unverified_files=sorted(unverified),
                          failure_pending_tick_end=True,
                          failure={'kind': kind, 'exit': returncode,
                                   'detail': (detail or '')[:400]})
        self.commit_state()
        _crashpoint('after_failed_step_commit')
        self.write_status()
        self.emit('refresh.step', 'failed', '%s failed (%s)' % (step.name, kind),
                  stage=step.name, detail=(detail or '')[:2000],
                  duration_ms=int(duration * 1000),
                  metadata={'step': step.name, 'kind': kind, 'exit': returncode, 'torn': torn})
        if torn:
            self.log('   outputs TORN — this step wrote before it died; publication waits for '
                     'its next success')
        if step.kind in table_module.COLLECT_KINDS and step.name not in self.filed:
            self.filed.append(step.name)
            self.log('%-30s FILED — returned to once this tick\'s collection is complete'
                     % step.name)
        return False

    def _commit_step(self, step, pre, post, result_state, duration, run, tolerated=()):
        """Build the manifest, run the zero floor, then ONE atomic state write."""
        changed = state_store.diff(pre, post)
        # Membership, not scanning: a step that owns 20,000 files and a tick that changed 20,000
        # would otherwise be 400 million comparisons on the live tree's 29,631 files.
        changed_set = set(changed)
        previous = self.manifests.get(step.name) or {}
        # A later failure that wrote nothing, or a successful no-op, cannot repair bytes left
        # by an earlier failed writer. A rewrite or restoration of the committed bytes can.
        for relative in self.state.step(step.name).get('unverified_files') or ():
            if relative in changed_set:
                continue
            expected = (previous.get('files') or {}).get(relative) or {}
            actual = state_store.describe_file(self.root, relative)
            if not (actual and expected.get('sha256')
                    and actual.get('sha256') == expected['sha256']):
                return self._fail_step(step, pre, post, state_store.TORN, run.returncode,
                                       'successful step did not repair ' + relative, duration)
        # What this step still OWNS of what it owned last time.  A file another step has since
        # taken over (last writer owns) is not this step's output any more, and leaving it in the
        # baseline would make the zero floor read a handover as a VANISHED output and block
        # publication on it -- a false block, which the contract is explicit is worse than no
        # threshold at all.  Computed before the reassignment below, which is this step's own.
        previous_files = {relative: entry
                          for relative, entry in (previous.get('files') or {}).items()
                          if self.owner.get(relative) in (None, step.name)}
        files = {}
        for relative, carried in previous_files.items():
            if relative in changed_set:
                continue
            described = state_store.describe_file(self.root, relative, carry=carried)
            if described is not None:
                files[relative] = described
        for relative in changed:
            described = state_store.describe_file(self.root, relative)
            if described is not None:
                files[relative] = described

        # THE ZERO FLOOR FOLLOWS WHAT THIS STEP ITSELF LAST PRODUCED, not only what it still owns.
        # "Last writer owns" strips a file from this step's manifest the moment a later step
        # rewrites it, so a baseline built from ownership alone goes blind on exactly the files
        # two steps share: a collector's 60 rows became 0, nothing noticed, and the tick
        # published it as clean.  ``produced`` survives the hand-over.  It is consulted only for
        # files THIS run rewrote, so a file another step legitimately keeps is never read as gone.
        produced_before = previous.get('produced')
        produced_before = produced_before if isinstance(produced_before, dict) else {}
        baseline = dict(previous_files)
        for relative in changed:
            if relative not in baseline and isinstance(produced_before.get(relative), int):
                baseline[relative] = {'rows': produced_before[relative], 'size': None}
        comparison_files = dict(files)
        # Keep a failed zero-floor verdict across hand-overs and successful no-op retries.
        # Ownership is separate: reading a shared file here does not take it from its writer.
        for zero in self.state.step(step.name).get('coverage_zeros') or ():
            relative = zero['file']
            baseline[relative] = {'rows': zero['was_rows'], 'size': None}
            actual = state_store.describe_file(self.root, relative)
            if actual is not None:
                comparison_files[relative] = actual
        zeros, shrinks = state_store.coverage_review(baseline, comparison_files)
        accepted_zero_files = set()
        if zeros and step.name in self.accept_zero:
            accepted_zero_files = {zero['file'] for zero in zeros}
            self.log('%-30s ZERO FLOOR ACCEPTED BY THE OWNER (--accept-zero) — %d file(s) that '
                     'carried rows now carry none; recorded, not blocking'
                     % (step.name, len(zeros)))
            self.review_added += state_store.append_review(
                self.review_path,
                [{'step': step.name, 'blocking': False,
                  'reason': 'zero floor ACCEPTED by the owner (--accept-zero)', **zero}
                 for zero in zeros], now=self.now())
            zeros = []
        if zeros:
            self.log('%-30s COVERAGE SUSPECT — %d file(s) that carried >= %d rows now carry none'
                     % (step.name, len(zeros), state_store.ZERO_FLOOR_ROWS))
            self.review_added += state_store.append_review(
                self.review_path,
                [{'step': step.name, 'blocking': True, 'reason': 'zero floor', **zero}
                 for zero in zeros], now=self.now())
            self.state.record(step.name, state=FAILED, torn=False, coverage=COVERAGE_SUSPECT,
                              coverage_zeros=zeros,
                              duration_s=round(duration, 3),
                              failure={'kind': state_store.COVERAGE, 'exit': run.returncode,
                                       'detail': 'zero floor: %s'
                                                 % ', '.join(z['file'] for z in zeros)[:300]})
            self.commit_state()
            self.write_status()
            self.emit('refresh.review', 'failed',
                      '%s: %d output(s) collapsed to zero rows' % (step.name, len(zeros)),
                      stage=step.name, detail=json.dumps(zeros)[:2000],
                      metadata={'step': step.name, 'zeros': zeros})
            # Deliberately NOT filed for the retry pass: a collector that just emitted a plausible
            # zero is not a flaky socket, and re-running it would emit the same zero twice more
            # and put three identical entries in front of Paul.  It is due again next tick.
            return False
        if shrinks:
            self.review_added += state_store.append_review(
                self.review_path,
                [{'step': step.name, 'blocking': False, 'reason': 'shrink recorded', **shrink}
                 for shrink in shrinks], now=self.now())

        generation = int(self.state.step(step.name).get('generation') or 0) + 1
        payload = state_store.build_manifest(step.name, generation, self.root, files,
                                             now=self.now())
        if shrinks:
            payload['shrinks'] = shrinks
        if tolerated:
            payload['tolerated'] = list(tolerated)
        # What this step ITSELF wrote, by row count, for the next run's zero floor.  Only tables
        # big enough to trip the floor are kept, so this stays a handful of entries; an entry
        # this run did not rewrite is carried forward, because the step's last word on that file
        # is still its last word whoever has written to it since.
        produced = dict(produced_before)
        for relative in accepted_zero_files:
            produced.pop(relative, None)
        for relative in changed:
            rows = (files.get(relative) or {}).get('rows')
            if isinstance(rows, int) and rows >= state_store.ZERO_FLOOR_ROWS:
                produced[relative] = rows
            else:
                produced.pop(relative, None)
        if produced:
            payload['produced'] = produced

        # Last writer owns: every changed file leaves every other step's manifest.
        for relative in changed:
            prior = self.owner.get(relative)
            if prior is not None and prior != step.name and prior in self.manifests:
                self.manifests[prior]['files'].pop(relative, None)
                self.manifest_dirty.add(prior)
            self.owner[relative] = step.name

        written = {}
        for other in sorted(self.manifest_dirty):
            other_payload = self.manifests[other]
            other_sha = state_store.manifest_filename(
                table_module.slug(other), other_payload).rsplit('.', 2)[-2]
            state_store.write_json_atomic(self.manifest_path(other, other_sha), other_payload)
            written[other] = other_sha
        sha = state_store.manifest_filename(step.slug, payload).rsplit('.', 2)[-2]
        state_store.write_json_atomic(self.manifest_path(step.name, sha), payload)
        _crashpoint('after_manifest_write')

        # THE COMMIT.  Until this single write lands, the step is not OK and the manifests above
        # are garbage the tick-end collector removes.
        for other, other_sha in written.items():
            self.state.record(other, manifest_sha=other_sha)
            self.manifest_shas[other] = other_sha
        self.state.record(step.name, state=result_state, torn=False, coverage=COVERAGE_OK,
                          coverage_zeros=[], unverified_files=[],
                          last_ok_utc=state_store.stamp(self.now()), attempts_since_ok=0,
                          failure=None, duration_s=round(duration, 3), generation=generation,
                          manifest_sha=sha)
        self.state.step(step.name).pop('failure_pending_tick_end', None)
        self.commit_state()
        _crashpoint('after_state_commit')
        self.manifests[step.name] = payload
        self.manifest_shas[step.name] = sha
        self.manifest_dirty.clear()
        if step.name not in self.succeeded:
            self.succeeded.append(step.name)
        if result_state == PARTIAL and step.name not in self.partial:
            self.partial.append(step.name)
        if step.name in self.filed:
            self.filed.remove(step.name)
        self.write_status()
        self.emit('refresh.step', 'partial' if result_state == PARTIAL else 'completed',
                  '%s %s (%d file(s) manifested)' % (step.name, result_state.lower(), len(files)),
                  stage=step.name, duration_ms=int(duration * 1000),
                  metadata={'step': step.name, 'state': result_state, 'generation': generation,
                            'manifest_sha': sha, 'changed': len(changed),
                            'shrinks': len(shrinks)})
        return True

    def skip_step(self, step, why, kind):
        self.state.record(step.name, state=SKIPPED, skip=kind, skip_reason=why)
        if kind == NOT_CONFIGURED:
            self.skipped_not_configured.add(step.name)
        self.commit_state()
        self.log('%-30s SKIPPED — %s' % (step.name, why))

    # ---- reconcile ---------------------------------------------------------------------
    def reconcile(self):
        """Resolve what the last tick left behind, before anything new is launched.

        A step found RUNNING is a process that DIED inside it, and one recorded killed by the
        console left its torn verdict undetermined.  Both are settled the same way: diff the
        persisted pre-snapshot against the tree as it is now.  Non-empty means the step wrote
        before it died, so its outputs are half of one run and half of another.
        """
        self.set_phase(PHASE_RECONCILE)
        now = self.snapshot()
        self._fresh_snapshot = now
        for step in self.table:
            entry = self.state.step(step.name)
            status = entry.get('state')
            # A process-tree kill can reach the child first. The parent may file an ordinary
            # exit failure before dying itself, so RUNNING/killed alone miss the crash. This
            # flag is cleared atomically with a normal terminal tick record, never earlier.
            if status == FAILED and entry.get('failure_pending_tick_end'):
                self.due_now.add(step.name)
                self.log('%-30s failure belonged to an unfinished tick; due now' % step.name)
            if status != RUNNING and not (status == FAILED and entry.get('torn') is None):
                continue
            pre = state_store.load_snapshot(self.pre_snapshot_path(step))
            changed = state_store.diff(pre, now) if pre is not None else []
            torn = (bool(entry.get('torn')) or bool(entry.get('unverified_files'))
                    or bool(changed) or pre is None)
            self.state.record(step.name, unverified_files=sorted(
                set(entry.get('unverified_files') or ()) | set(changed)))
            self.due_now.add(step.name)
            if status == RUNNING:
                entry_failure = {'kind': INTERRUPTED, 'exit': None,
                                 'detail': 'the tick died while this step was running'}
                self.state.record(step.name, state=FAILED, torn=torn, failure=entry_failure)
                self.log('%-30s INTERRUPTED — the previous tick died inside it%s'
                         % (step.name, '; outputs TORN' if torn else '; nothing was written'))
            else:
                self.state.record(step.name, torn=torn)
                self.log('%-30s was killed — torn=%s (resolved against its pre-snapshot)'
                         % (step.name, torn))
            self.emit('refresh.step', 'failed', '%s did not finish' % step.name, stage=step.name,
                      metadata={'step': step.name, 'kind': entry.get('failure', {}).get('kind')
                                if isinstance(entry.get('failure'), dict) else INTERRUPTED,
                                'torn': torn})
        self.commit_state()

    # ---- phases ------------------------------------------------------------------------
    def collect_phase(self):
        self.set_phase(PHASE_COLLECT)
        for step in self.table:
            if step.kind not in table_module.COLLECT_KINDS:
                continue
            if self.only and step.name not in self.only:
                continue
            if not self.budget_left():
                self._note_blocker('budget', 'the %.1f h budget ran out during collection'
                                   % self.budget_hours)
                return
            due, why = self.due(step)
            if not due:
                continue
            met, unmet = self.requirements_met(step)
            if not met:
                self.skip_step(step, 'requires %s (%s)' % (unmet, self.state.state_of(unmet)),
                               DEPENDENCY)
                continue
            self.run_step(step, PHASE_COLLECT)

    def retry_pass(self):
        """Go back through the FILED failures once collection is complete.

        Owner: *"if a fail is detected it is filed and then returned too once collection for that
        session is complete"*.  A PARTIAL is never filed: re-walking for the same deferred URLs
        would burn three more three-hour walks to defer them again.
        """
        if not self.filed:
            return
        self.set_phase(PHASE_RETRY)
        for name in list(self.filed):
            step = self.by_name.get(name)
            if step is None:
                continue
            for attempt in range(1, self.retry_attempts + 1):
                if not self.budget_left():
                    self._note_blocker('budget', 'the budget ran out during the retry pass')
                    return
                entry = self.state.step(name)
                failure = entry.get('failure') if isinstance(entry.get('failure'), dict) else {}
                burned = float(entry.get('duration_s') or 0.0)
                if failure.get('kind') == TIMEOUT and self.remaining_seconds() < 2 * burned:
                    # A step that HUNG is not a flaky socket.  Returning to it with less than
                    # twice what it just burned would spend the rest of the tick on a second
                    # hang and starve the compute phase; it is due again on the next fire.
                    self.log('%-30s NOT RETRIED — it timed out after %.0fs and %.0fs of budget '
                             'would not survive a second hang; due again next tick'
                             % (name, burned, max(0.0, self.remaining_seconds())))
                    break
                self.log('%-30s RETRY %d of %d' % (name, attempt, self.retry_attempts))
                if self.run_step(step, PHASE_RETRY):
                    self._run_dependents(step)
                    break

    def _run_dependents(self, recovered):
        """Anything in the collect phase that was waiting on a step which just recovered."""
        for step in self.table:
            if step.kind not in table_module.COLLECT_KINDS or step.name in self.succeeded:
                continue
            if recovered.name not in step.requires:
                continue
            if self.only and step.name not in self.only:
                continue
            met, _unmet = self.requirements_met(step)
            if met and self.budget_left():
                self.log('%-30s DEPENDENCY RECOVERED — running now' % step.name)
                self.run_step(step, PHASE_RETRY)

    def eligibility(self):
        """(eligible, blockers).  Every evidence row must be present, intact and in-window."""
        blockers = []
        now = self.now()
        for name in sorted(self.unavailable_manifests):
            blockers.append((name, 'manifest cannot be read; evidence is unverified'))
        for step in table_module.evidence_steps(self.table):
            entry = self.state.step(step.name)
            age = state_store.hours_since(entry.get('last_ok_utc'), now)
            if not entry.get('last_ok_utc') or age is None:
                blockers.append((step.name, 'has never succeeded'))
            elif entry.get('torn'):
                blockers.append((step.name, 'outputs are torn'))
            elif entry.get('coverage') != COVERAGE_OK:
                blockers.append((step.name, 'coverage is %s' % entry.get('coverage')))
            elif age > self.window_days * 24:
                blockers.append((step.name, 'last success %.1f d ago, older than the %d-day '
                                            'window' % (age / 24.0, self.window_days)))
        return (not blockers), blockers

    def compute_wanted(self):
        if self.force_compute:
            return True, '--force-compute'
        # NOTHING_DUE is the ONLY unpublished outcome that exits 0, and it means exactly
        # "nothing changed and the last publication still stands".  If any evidence row is torn,
        # coverage-suspect, out of window or has never succeeded, the last publication no longer
        # describes a healthy system, so this tick must evaluate and REPORT that rather than
        # reporting quiet success -- a zero-row collector that exits 0 is the failure this whole
        # runner exists to make visible.
        eligible, blockers = self.eligibility()
        if not eligible:
            return True, 'evidence is not currently publishable (%s: %s)' % blockers[0]
        published = self.state.last_publish_utc()
        if not published:
            return True, 'nothing has been published yet'
        moment = state_store.parse_stamp(published)
        if moment is None:
            return True, 'the publication record is unreadable'
        # "Is there new evidence?" must not be a comparison of two wall-clock stamps: after a
        # backward clock step (this laptop's standby and time sync do that) fresh evidence carried
        # an OLDER stamp than the last publication, and every later tick exited 0 NOTHING_DUE
        # over data nobody had computed.  Evidence collected in THIS tick always counts, and
        # across ticks the question is asked of each source's GENERATION -- a counter that only
        # its own successful commit advances -- against the generation the last publication
        # recorded.  The stamp comparison remains only for a publication record written before
        # generations were recorded.
        inputs = (self.state.publication() or {}).get('inputs')
        inputs = inputs if isinstance(inputs, dict) else {}
        for step in table_module.evidence_steps(self.table):
            if step.name in self.succeeded:
                return True, 'new evidence this tick: %s' % step.name
            entry = self.state.step(step.name)
            recorded = inputs.get(step.name)
            if isinstance(recorded, dict) and 'generation' in recorded:
                if int(entry.get('generation') or 0) != int(recorded.get('generation') or 0):
                    return True, 'new evidence since the last publication: %s' % step.name
                continue
            last_ok = state_store.parse_stamp(entry.get('last_ok_utc'))
            if last_ok is not None and last_ok > moment:
                return True, 'new evidence since the last publication: %s' % step.name
        if self.state.state_of(CHAIN_STEP) not in SUCCEEDED:
            return True, 'the last compute did not succeed'
        return False, ''

    def compute_phase(self):
        """Run the compute transaction in table order.  A hard failure stops THIS PHASE.

        It does not stop the tick: the tick still writes its state, its ledger line and its
        outcome, and the phase is retried on the next fire.  Diagnostics run only when the chain
        itself succeeded in this same tick -- they read the bundle it just wrote.
        """
        self.set_phase(PHASE_COMPUTE)
        _crashpoint('before_compute')
        for step in self.table:
            if step.kind not in table_module.COMPUTE_KINDS:
                continue
            if self.only and step.name not in self.only:
                continue
            if step.kind == table_module.DIAG and CHAIN_STEP not in self.succeeded:
                self.skip_step(step, 'the chain did not succeed in this tick', DEPENDENCY)
                continue
            if not self.budget_left():
                self._note_blocker('budget', 'the %.1f h budget ran out during the compute phase'
                                   % self.budget_hours)
                return False
            met, unmet = self.requirements_met(step)
            if not met:
                self.skip_step(step, 'requires %s (%s)' % (unmet, self.state.state_of(unmet)),
                               DEPENDENCY)
                if step.hard:
                    self.compute_failure = '%s (requires %s)' % (step.name, unmet)
                    self.log('COMPUTE PHASE STOPPED — %s' % self.compute_failure)
                    return False
                continue
            if step.skip_if:
                reason = SKIPS[step.skip_if]()
                if reason:
                    self.skip_step(step, reason, NOT_CONFIGURED)
                    continue
            if not self.run_step(step, PHASE_COMPUTE) and step.hard:
                self.compute_failure = step.name
                self.log('COMPUTE PHASE STOPPED — hard step failed: %s' % step.name)
                return False
        return True

    def publication_record(self):
        """Write the publication record: the EXACT inputs this publication was computed from.

        Only after the chain succeeded IN THIS TICK and the deployment step either succeeded or
        was skipped as not configured.  A hard failure after the chain reported COMPLETE
        therefore cannot be reported as a publication (N2).
        """
        if CHAIN_STEP not in self.succeeded:
            return None
        # Both verdicts must be from THIS tick.  Reading the deployment step's state alone would
        # let a SKIPPED_NOT_CONFIGURED recorded weeks ago stand in for a step this tick never
        # reached, which is the "reported as published without publishing" failure in miniature.
        published_ok = (PUBLISH_STEP in self.succeeded
                        or PUBLISH_STEP in self.skipped_not_configured)
        if not published_ok:
            self.compute_failure = self.compute_failure or PUBLISH_STEP
            return None
        now = self.now()
        inputs, stale = {}, []
        for step in table_module.evidence_steps(self.table):
            entry = self.state.step(step.name)
            age = state_store.hours_since(entry.get('last_ok_utc'), now)
            inputs[step.name] = {'last_ok_utc': entry.get('last_ok_utc'),
                                 'manifest_sha': entry.get('manifest_sha'),
                                 # The clock-free identity of "which run of this source":
                                 # compute_wanted() compares it, never the stamps.
                                 'generation': int(entry.get('generation') or 0),
                                 'age_h': None if age is None else round(age, 2)}
            if age is None or (step.cadence_h and age >= step.cadence_h):
                stale.append(step.name)
        record = self.state.set_publication(inputs, bool(stale), stale, now=now)
        record['partial_steps'] = list(self.partial)
        self.commit_state()
        self.published = record
        return record

    def _note_blocker(self, what, why):
        if (what, why) not in self.blockers:
            self.blockers.append((what, why))
            self.log('BLOCKER — %s: %s' % (what, why))

    # ---- the whole tick ----------------------------------------------------------------
    def run(self):
        """One tick.  Returns its outcome name; ``EXIT_CODES`` maps it to the process code."""
        self.started_utc = state_store.stamp(self.now())
        self.deadline = time.monotonic() + self.budget_hours * 3600
        self.open_observation()
        self.log('===== REFRESH TICK START (budget %.1f h) =====' % self.budget_hours)
        self.emit('refresh.tick', 'running', 'refresh tick start', stage='tick',
                  metadata={'budget_hours': self.budget_hours, 'root': self.root})
        if not self.acquire_instance_lock():
            # Deliberately no marker write and no state write: the running instance owns those.
            self.log('BLOCKED — another refresh tick already holds the single-instance lock')
            self.emit('refresh.tick', 'failed',
                      'blocked — another refresh tick is already running', stage='tick',
                      metadata={'blocked_by': 'single-instance lock'})
            return BLOCKED
        try:
            self.state.load()
            if self.state.rotated:
                self.log('refresh state %s — everything is due' % self.state.rotated)
                self.emit('refresh.tick', 'running',
                          'refresh state %s' % self.state.rotated, stage='tick')
            self.set_phase(PHASE_STARTING)
            problems = table_module.validate(self.table)
            if problems:
                raise FatalPreStep('; '.join(problems))
            self.index_producers()
            if not self.acquire_lease():
                return self._finish(BLOCKED)
            self.load_manifests()
            self.reconcile()
            self.verify_outputs()
            self.collect_phase()
            self.retry_pass()
            return self._finish(self._compute_and_publish())
        except FatalPreStep as error:
            self.log('FATAL pre-step failure: %s' % str(error)[:300])
            self.emit('refresh.tick', 'failed', 'FATAL pre-step failure: %s' % str(error)[:300],
                      stage='tick', detail=str(error)[:2000])
            return self._finish(FATAL_PRESTEP)
        except Exception as error:
            # A BUG in this runner must still leave a terminal record and a non-zero exit: an
            # unattended job that dies with a traceback tells the supervisor nothing, which is the
            # failure mode this whole runner exists to remove.  It is never hidden -- the type,
            # the message and the full traceback go to the log, the Glass Box detail and the tick
            # ledger.  KeyboardInterrupt and SystemExit are deliberately NOT caught: those belong
            # to the exit guard.
            import traceback
            self.unhandled = '%s: %s' % (type(error).__name__, str(error)[:200])
            self.log('UNHANDLED ERROR in the tick — %s' % self.unhandled)
            for line in traceback.format_exc().split('\n'):
                if line.strip():
                    self.log('   ' + line)
            self.emit('refresh.tick', 'failed', 'unhandled error in the tick — %s'
                      % self.unhandled, stage=self.current_step or 'tick',
                      detail=traceback.format_exc()[:2000])
            self.compute_failure = self.compute_failure or self.unhandled
            return self._finish(COMPUTE_FAILED if self.phase == PHASE_COMPUTE
                                else COLLECTED_NOT_PUBLISHED)
        finally:
            self.kill_live_child()
            self.release_lease()
            self.release_instance_lock()
            if self._log_handle is not None:
                try:
                    self._log_handle.close()
                except Exception:
                    pass
                self._log_handle = None

    def _compute_and_publish(self):
        wanted, why = self.compute_wanted()
        if self.skip_compute:
            if not self.attempted and not wanted:
                return NOTHING_DUE
            self._note_blocker('--skip-compute', 'the compute phase was skipped by request')
            return COLLECTED_NOT_PUBLISHED
        if not wanted:
            if self.attempted:
                self.log('compute phase not needed: no evidence changed since the last '
                         'publication')
            return NOTHING_DUE if not self.filed else COLLECTED_NOT_PUBLISHED
        self.log('compute phase wanted: %s' % why)
        eligible, blockers = self.eligibility()
        if not eligible:
            for name, reason in blockers:
                self._note_blocker(name, reason)
            self.log('NOT ELIGIBLE — %d evidence source(s) block publication' % len(blockers))
            return COLLECTED_NOT_PUBLISHED
        self.compute_phase()
        _crashpoint('after_compute_before_publication_record')
        record = self.publication_record()
        if record is None:
            if self.compute_failure:
                return COMPUTE_FAILED
            return COLLECTED_NOT_PUBLISHED
        if record['degraded']:
            self.log('PUBLISHED (DEGRADED) — stale source(s): %s'
                     % ', '.join(record['stale_sources']))
            return PUBLISHED_DEGRADED
        self.log('PUBLISHED — every evidence source refreshed inside its cadence window')
        return PUBLISHED

    def _finish(self, outcome):
        """The ONE place a terminal outcome is written (N4).

        A BLOCKED or failed tick records itself WITHOUT touching ``publication`` (N5): that block
        is the last good tick's, and erasing it would tell every reader the system had never
        published.
        """
        ended = state_store.stamp(self.now())
        exit_code = EXIT_CODES[outcome]
        record = {'outcome': outcome, 'exit_code': exit_code, 'started_utc': self.started_utc,
                  'ended_utc': ended, 'budget_hours': self.budget_hours,
                  'steps_run': list(self.attempted), 'steps_ok': list(self.succeeded),
                  'partial_steps': list(self.partial), 'filed': list(self.filed),
                  'blockers': [{'what': what, 'why': why} for what, why in self.blockers],
                  'review_entries': self.review_added,
                  'compute_failure': self.compute_failure,
                  'unhandled': self.unhandled,
                  'published': bool(self.published)}
        self.state.set_last_tick(outcome, exit_code, self.started_utc, ended,
                                 steps_run=len(self.attempted), steps_ok=len(self.succeeded),
                                 partial_steps=list(self.partial), filed=list(self.filed),
                                 blockers=record['blockers'],
                                 compute_failure=self.compute_failure)
        for name in self.attempted:
            self.state.step(name).pop('failure_pending_tick_end', None)
        try:
            self.commit_state()
        except OSError as error:
            self.log('state commit FAILED at tick end (%s)' % type(error).__name__)
        self.phase = PHASE_IDLE
        self.current_step = None
        self.write_status(tick=record)
        state_store.append_tick(self.ticks_path, record)
        removed = state_store.collect_manifests(self.manifest_dir, self.referenced_manifests())
        if removed:
            self.log('collected %d superseded manifest file(s)' % removed)
        self.log('===== REFRESH TICK %s (exit %d) — %d step(s) run, %d ok, %d filed =====\n'
                 % (outcome, exit_code, len(self.attempted), len(self.succeeded),
                    len(self.filed)))
        self.emit('refresh.tick',
                  'completed' if exit_code == 0 else 'failed',
                  'refresh tick %s — %d step(s) run, %d ok%s'
                  % (outcome, len(self.attempted), len(self.succeeded),
                     ', %d partial' % len(self.partial) if self.partial else ''),
                  stage='tick', detail=json.dumps(record['blockers'])[:2000], metadata=record)
        return outcome

    # ---- read-only modes ---------------------------------------------------------------
    def plan(self):
        """What a tick would do, computed WITHOUT locks, writes or launches."""
        self.state.load(rotate=False)
        problems = table_module.validate(self.table)
        rows = []
        for step in self.table:
            if step.kind in table_module.COLLECT_KINDS:
                due, why = self.due(step)
            else:
                due, why = (None, 'compute phase')
            entry = self.state.step(step.name)
            rows.append({'name': step.name, 'kind': step.kind, 'due': due, 'why': why,
                         'state': entry.get('state'), 'last_ok_utc': entry.get('last_ok_utc'),
                         'torn': entry.get('torn'), 'coverage': entry.get('coverage')})
        eligible, blockers = self.eligibility()
        wanted, why = self.compute_wanted()
        return {'root': self.root, 'table_problems': problems, 'steps': rows,
                'eligible': eligible,
                'blockers': [{'source': name, 'why': reason} for name, reason in blockers],
                'compute_wanted': wanted, 'compute_reason': why,
                'publication': self.state.publication(), 'last_tick': self.state.last_tick()}


# =============================================================================================
# CLI
# =============================================================================================
def _default_root():
    from paths import ROOT
    return ROOT


def build_parser():
    parser = argparse.ArgumentParser(
        prog='refresh_tick', description='Run the refresh work that is due, then publish if the '
                                         'publication contract passes.')
    parser.add_argument('--budget-hours', type=float, default=BUDGET_HOURS,
                        help='hard total budget for this tick (default %(default)s)')
    parser.add_argument('--lease-wait-hours', type=float, default=LEASE_WAIT_HOURS,
                        help='how long to poll for the writer lease (default %(default)s)')
    parser.add_argument('--only', action='append', default=[], metavar='NAME',
                        help='run only this step (repeatable)')
    parser.add_argument('--skip-compute', action='store_true',
                        help='collect only; do not compute or publish')
    parser.add_argument('--force-due', action='append', default=[], metavar='NAME',
                        help='treat this step as due regardless of cadence; "all" for every step')
    parser.add_argument('--force-compute', action='store_true',
                        help='run the compute phase even when no evidence changed')
    parser.add_argument('--accept-zero', action='append', default=[], metavar='NAME',
                        help='run this step now and ACCEPT its zero-floor verdict once: a file '
                             'that carried rows and now carries none is recorded for review '
                             'instead of blocking publication (repeatable; the owner\'s call)')
    parser.add_argument('--dry-run', action='store_true',
                        help='print what is due and why, and the eligibility verdict; takes no '
                             'lock, writes nothing, launches nothing')
    parser.add_argument('--status', action='store_true',
                        help='render the current state; takes no lock and writes nothing')
    return parser


def main(argv=None):
    """Real entry point.  Importing this module does none of it."""
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    args = build_parser().parse_args(argv)
    root = _default_root()
    tick = Tick(root, budget_hours=args.budget_hours, lease_wait_hours=args.lease_wait_hours,
                only=args.only, skip_compute=args.skip_compute, force_due=args.force_due,
                force_compute=args.force_compute, accept_zero=args.accept_zero)
    if args.status:
        tick.state.load(rotate=False)
        print(json.dumps(state_store.render_status(tick.state, PHASE_IDLE), indent=1,
                         ensure_ascii=False))
        return 0
    if args.dry_run:
        plan = tick.plan()
        print('REFRESH TICK — DRY RUN (no locks, no writes, no launches)')
        print('root: %s' % plan['root'])
        for problem in plan['table_problems']:
            print('  TABLE PROBLEM: %s' % problem)
        due = [row for row in plan['steps'] if row['due']]
        print('due now: %d of %d collect step(s)'
              % (len(due), sum(1 for row in plan['steps'] if row['due'] is not None)))
        for row in plan['steps']:
            if row['due'] is None:
                continue
            print('  %-8s %-30s %s' % ('DUE' if row['due'] else 'skip', row['name'][:30],
                                       row['why']))
        print('compute wanted: %s%s' % (plan['compute_wanted'],
                                        ' (%s)' % plan['compute_reason']
                                        if plan['compute_reason'] else ''))
        print('eligible to publish: %s' % plan['eligible'])
        for blocker in plan['blockers']:
            print('  BLOCKER %-30s %s' % (blocker['source'][:30], blocker['why']))
        print('last publication: %s' % ((plan['publication'] or {}).get('last_publish_utc')
                                        or 'never'))
        print('last tick: %s' % ((plan['last_tick'] or {}).get('outcome') or 'never'))
        return 0
    # The exit guard, registered BEFORE the locks are taken.  atexit is LIFO, so the child tree
    # dies first and the lease is released only afterwards: an orphan that outlived the lease is
    # a second writer on the same artifacts.
    atexit.register(tick.release_instance_lock)
    atexit.register(tick.release_lease)
    atexit.register(tick.kill_live_child)
    if tick.install_console_control_handler():
        tick.log('console control handler installed — a console death is handled on its own '
                 'thread')
    if hasattr(signal, 'SIGBREAK'):
        def _on_break(signum, frame):
            tick.console_shutdown('SIGBREAK')
            sys.exit(1)
        signal.signal(signal.SIGBREAK, _on_break)
    return EXIT_CODES[tick.run()]


if __name__ == '__main__':
    raise SystemExit(main())

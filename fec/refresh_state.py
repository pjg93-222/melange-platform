# -*- coding: utf-8 -*-
"""Per-STEP state, output manifests and torn detection for the refresh tick runner.

THE STATE FILE IS THE ONLY TRUTH.  ``data/engine/refresh_state.json`` carries one record per step
-- when it last succeeded, whether its outputs are intact, which manifest verifies them -- plus
the publication record.  ``data/engine/refresh_tick_status.json`` is a human/supervisor MARKER
RENDERED from that state by :func:`render_status` and by nothing else.  The weekly refresh's
predecessor kept two independent truths (a ledger and a status marker, each written on its own
paths) and they disagreed; here the marker cannot disagree, because it is a projection.

WHAT A COMMIT IS.  A step is OK only after ONE atomic state write that names a manifest which
verifies its outputs.  Manifests are written first; a manifest the state does not reference is
garbage and is collected at the end of the tick.  So a crash between the two costs the step, not
the truth: the next tick sees the step still not-OK and runs it again.

TORN OUTPUTS.  Before a step is launched its pre-snapshot of the data roots is persisted.  If the
step then fails, times out, is killed, or the whole process dies inside it, the post-snapshot is
compared with that pre-snapshot: if anything under the data roots changed, the step wrote before
it died and its outputs are TORN -- half of this run, half of the last.  A torn step blocks
publication until its next success, which is the only thing that can make its outputs whole again.
Another writer working under the same roots can raise a false torn; that costs one delayed
(or degraded) publication and heals on the step's next success, which is the safe direction.

THE ZERO FLOOR.  The measured failure mode in this repo is COLLECTION EMITTING PLAUSIBLE ZEROS: a
collector "succeeds", writes an empty file, and every downstream sum still adds up.  A file that
had at least :data:`ZERO_FLOOR_ROWS` rows in the step's previous manifest and now has none -- or
is empty, or is gone -- ends the step FAILED with ``coverage=SUSPECT`` and an entry in
``data/engine/refresh_review.json`` for Paul.  Every OTHER shrink is recorded, in the manifest and
in the review file, as information only: there is no per-source data yet from which to set a
threshold, and a guessed threshold that blocks publication is worse than no threshold at all.

FAILURE CLASSES AND FRESHNESS LABELS (2026-10-15 tiered degrade).  A failed step records WHAT KIND
of failure it was (``failure_class``: TRANSIENT, SOURCE_OUTAGE, BUDGET_QUOTA, DATA_CONTRACT -- the
classifier is ``fec/refresh_failure.py``), when it may next be tried (``next_attempt_utc``), the
attempts of the last 24 h (``recent_attempts_utc``), and for a data-contract refusal the input
generations it was refused on (``dead_letter``).  :func:`freshness_record` turns one entry into the
label the dashboard reads from ``data/engine/source_freshness.json``.

Import-safe and stdlib-only: nothing here reads the environment, launches anything or touches a
path the caller did not pass in.
"""
import datetime
import hashlib
import json
import os
import random
import tempfile
import time

SCHEMA = 1

# ---- step states -----------------------------------------------------------------------------
NEVER = 'NEVER'
RUNNING = 'RUNNING'
OK = 'OK'
# "Ran to the end, some work deferred": the press walk's exit 3 (measured 2026-09-13 -- 3 h 13 m,
# 2,200 URLs in 9 members never attempted because the per-member budget ran out).  A PARTIAL is
# COMMITTED exactly like an OK -- manifest, last_ok_utc, generation -- satisfies ``requires``,
# counts as fresh for cadence and eligibility, and is NOT retried: three more three-hour walks in
# the same tick would defer the same URLs again.  Whether the deferral is acceptable is the
# downstream press gates' decision.  It is reported as partial in the tick line, the marker and
# the Glass Box event, so it is never mistaken for a clean run.
PARTIAL = 'PARTIAL'
FAILED = 'FAILED'
SKIPPED = 'SKIPPED'

# The states that mean "this step produced its outputs".  Everything that asks "is this step
# satisfied?" -- requires, freshness, eligibility, dueness -- asks about this tuple, never OK.
SUCCEEDED = (OK, PARTIAL)

# ---- why a step failed -----------------------------------------------------------------------
EXIT = 'exit'
TIMEOUT = 'timeout'
KILLED = 'killed'
INTERRUPTED = 'interrupted'
TORN = 'torn'
COVERAGE = 'coverage'

# ---- why a step was skipped ------------------------------------------------------------------
NOT_CONFIGURED = 'NOT_CONFIGURED'
DEPENDENCY = 'DEPENDENCY'

# ---- coverage verdicts -----------------------------------------------------------------------
COVERAGE_OK = 'OK'
COVERAGE_SUSPECT = 'SUSPECT'

# ---- failure classes: what a failed step MEANS, and so what the runner may do next ----------------
TRANSIENT = 'TRANSIENT'              # timeout, 5xx, network reset, sharing violation: backoff + daily cap
SOURCE_OUTAGE = 'SOURCE_OUTAGE'      # the source is down: keep last-good, probe on cadence
BUDGET_QUOTA = 'BUDGET_QUOTA'        # a provider quota/spend limit: the step FAILED (D-211); one attempt per daily window
DATA_CONTRACT = 'DATA_CONTRACT'      # a contract refusal: dead-letter, never retried automatically
FAILURE_CLASSES = (TRANSIENT, SOURCE_OUTAGE, BUDGET_QUOTA, DATA_CONTRACT)
# The two stops the admissions producer reports, and they are NOT the same thing (D-211):
#   BUDGET_QUOTA  aborted_reason CLASSIFIER_QUOTA_EXHAUSTED, exit non-zero: the provider refused, the
#                 generation was NOT installed.  A FAILED step -- never PASS, never PARTIAL -- whose
#                 last-good generation is served and labelled STALE_LABELLED; not retried before the
#                 next daily window (a retry would only meet the same limit), then one attempt per window.
#   BUDGET_CALLS  aborted_reason CLASSIFIER_CALL_BUDGET, exit 0: an intentional bounded run that
#                 INSTALLED its generation.  A PASS at the normal cadence; the unattempted keys stay in
#                 the pool.  It is not a failure class, only a note on a success (``budget_stop``).
BUDGET_CALLS = 'BUDGET_CALLS'
STOP_QUOTA = 'CLASSIFIER_QUOTA_EXHAUSTED'
STOP_CALLS = 'CLASSIFIER_CALL_BUDGET'

# ---- freshness labels (data/engine/source_freshness.json) -----------------------------------------
FRESH = 'FRESH'
STALE_LABELLED = 'STALE_LABELLED'    # last-good served past its cadence/window; nothing known to be failing
DEGRADED = 'DEGRADED'                # last-good (or nothing) served because the latest attempt failed
DEAD_LETTER = 'DEAD_LETTER'          # quarantined data-contract refusal; waits for an input to change
FRESHNESS_STATUSES = (FRESH, STALE_LABELLED, DEGRADED, DEAD_LETTER)
FRESHNESS_SCHEMA = 1
# How many attempts a step may start inside one rolling day, and how many stamps are remembered.
ATTEMPT_WINDOW_HOURS = 24.0
ATTEMPTS_REMEMBERED = 12

# A file that carried this many rows and now carries none is the plausible-zeros failure.
ZERO_FLOOR_ROWS = 50
# Row counts are read for text tables and small JSON documents only; above this a file is
# manifested by size and digest alone.  50 MB is already far past anything worth counting.
ROW_COUNT_MAX_BYTES = 50 * 1024 * 1024
# Enough shrink records per step to explain a run without letting the review file grow forever.
MAX_RECORDED_SHRINKS = 50
# The review file's own ceiling.  It is Paul's reading list, not an archive: the tick ledger and
# the manifests keep the full history.
REVIEW_MAX_ENTRIES = 2000

# The same measured lock window fec/lane_file_io.py's replace_atomic covers: on this workstation
# OneDrive, the antivirus scanner and a live poller each hold a destination open for a few
# milliseconds.  STATE is the commit point, so it retries properly; the STATUS MARKER is a
# projection nobody depends on, so it gives up fast and never delays a step.
STATE_REPLACE_ATTEMPTS = 12
STATE_REPLACE_BACKOFF = (0.05, 0.25)
STATUS_REPLACE_ATTEMPTS = 4
STATUS_REPLACE_BACKOFF = (0.01, 0.05)
# After this many consecutive marker failures the marker is abandoned for the rest of the tick.
# An unwritable marker used to cost 6.2 s per write; the outcome lives in the state file anyway.
STATUS_FAILURE_BUDGET = 3
# 5 ERROR_ACCESS_DENIED, 32 ERROR_SHARING_VIOLATION, 33 ERROR_LOCK_VIOLATION.
TRANSIENT_REPLACE_WINERRORS = (5, 32, 33)

_STAMP_FORMAT = '%Y-%m-%dT%H:%M:%SZ'


# =============================================================================================
# time
# =============================================================================================
def utcnow():
    """Timezone-AWARE UTC, to the second.

    ``datetime.utcnow()`` returns a naive value that claims to be local time; comparing it with
    anything aware is a TypeError and comparing it with a local timestamp is silently wrong twice
    a year.  Every timestamp in this module comes from here.
    """
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)


def stamp(moment):
    return moment.strftime(_STAMP_FORMAT)


def parse_stamp(text):
    """A timestamp written by :func:`stamp`, or None for anything else (a hand edit, a truncated
    write, a future schema).  None always means "treat it as unknown", never "treat it as now"."""
    try:
        parsed = datetime.datetime.strptime(str(text), _STAMP_FORMAT)
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=datetime.timezone.utc)


def hours_since(text, now):
    """Hours between a stamped instant and ``now``, or None when the stamp is unreadable."""
    moment = parse_stamp(text)
    if moment is None:
        return None
    return (now - moment).total_seconds() / 3600.0


# =============================================================================================
# durable writes
# =============================================================================================
def transient_replace_error(error):
    """True when a rename failed because something briefly held the target."""
    if isinstance(error, PermissionError):
        return True
    return getattr(error, 'winerror', None) in TRANSIENT_REPLACE_WINERRORS


def replace_atomic(scratch, path, attempts=STATE_REPLACE_ATTEMPTS, backoff=STATE_REPLACE_BACKOFF,
                   replace=None, sleep=None):
    """``os.replace(scratch, path)``, retried through the lock window.

    On the final failure the scratch file is removed before the error propagates, so a lost race
    never leaves a sibling behind for OneDrive to sync or a later run to mistake for the real file.
    """
    replace = replace if replace is not None else os.replace
    pause = sleep if sleep is not None else time.sleep
    attempts = max(1, int(attempts))
    for attempt in range(1, attempts + 1):
        try:
            replace(scratch, path)
            return attempt
        except OSError as error:
            if attempt >= attempts or not transient_replace_error(error):
                try:
                    os.unlink(scratch)
                except OSError:
                    pass
                raise
            pause(random.uniform(*backoff))
    raise AssertionError('unreachable: attempts must be >= 1')


def write_json_atomic(path, payload, attempts=STATE_REPLACE_ATTEMPTS,
                      backoff=STATE_REPLACE_BACKOFF, indent=1):
    """Publish one JSON document: temp file in the SAME directory, fsync, then atomic replace."""
    path = str(path)
    body = json.dumps(payload, ensure_ascii=False, indent=indent).encode('utf-8')
    directory = os.path.dirname(os.path.abspath(path)) or '.'
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError:
        pass
    descriptor, scratch = tempfile.mkstemp(dir=directory, prefix='.refresh.tmp-%d-' % os.getpid(),
                                           suffix='.part')
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        replace_atomic(scratch, path, attempts=attempts, backoff=backoff)
    except BaseException:
        try:
            os.unlink(scratch)
        except OSError:
            pass
        raise
    return len(body)


def read_json(path):
    """(payload, problem).  A missing file is not a problem; an unreadable one is."""
    try:
        with open(str(path), encoding='utf-8') as handle:
            return json.load(handle), ''
    except FileNotFoundError:
        return None, ''
    except (OSError, ValueError, UnicodeDecodeError) as error:
        return None, '%s: %s' % (type(error).__name__, str(error)[:160])


def read_manifest(path, attempts=5, backoff=(0.05, 0.25), sleep=None):
    """(payload, problem, transient) for one manifest file the state references.

    ``transient`` is True when the file could not be OPENED -- a sharing violation or an access
    denial.  OneDrive, the antivirus scanner and a live poller each hold a file for a few
    milliseconds on this workstation, and that says nothing about the manifest: the read is
    retried through the same window :func:`replace_atomic` covers, and a manifest that still
    cannot be opened must NOT cost the step its recorded success (one locked file used to erase
    it durably).  A missing or unparseable manifest is a real verdict and is not retried.
    """
    pause = sleep if sleep is not None else time.sleep
    attempts = max(1, int(attempts))
    problem = ''
    for attempt in range(1, attempts + 1):
        try:
            with open(str(path), encoding='utf-8') as handle:
                payload = json.load(handle)
        except FileNotFoundError:
            return None, 'missing', False
        except (ValueError, UnicodeDecodeError) as error:
            return None, '%s: %s' % (type(error).__name__, str(error)[:160]), False
        except OSError as error:
            problem = '%s: %s' % (type(error).__name__, str(error)[:160])
            if attempt < attempts:
                pause(random.uniform(*backoff))
            continue
        if not isinstance(payload, dict) or not isinstance(payload.get('files'), dict):
            return None, 'not a manifest document', False
        return payload, '', False
    return None, problem, True


def append_line(path, text):
    """One UTF-8 line appended, LF only.  Best effort: a lost log line is not a lost tick."""
    try:
        directory = os.path.dirname(os.path.abspath(str(path))) or '.'
        os.makedirs(directory, exist_ok=True)
        with open(str(path), 'a', encoding='utf-8', newline='\n') as handle:
            handle.write(text.rstrip('\n') + '\n')
        return True
    except OSError:
        return False


# =============================================================================================
# snapshots and manifests
# =============================================================================================
EXTERNAL_PREFIX = 'ext:'


def key_for(base, path):
    """The manifest/snapshot key for one absolute path.

    Inside ``base`` it is the forward-slash relative path, so a snapshot persisted on one run is
    comparable with the next and a manifest survives the project moving.  Outside ``base`` there
    is no relative form that means anything, so the key is ``ext:`` plus the absolute path --
    unmistakably not a repository path, and never colliding with one.
    """
    base = os.path.abspath(str(base))
    path = os.path.abspath(str(path))
    try:
        if os.path.commonpath([base, path]) == base:
            return os.path.relpath(path, base).replace('\\', '/')
    except ValueError:
        pass                                  # different drive: definitely external
    return EXTERNAL_PREFIX + path


def resolve_key(base, key):
    """The absolute path a snapshot/manifest key names.  Inverse of :func:`key_for`."""
    if key.startswith(EXTERNAL_PREFIX):
        return key[len(EXTERNAL_PREFIX):]
    return os.path.join(str(base), key.replace('/', os.sep))


def snapshot(base, recursive=(), top_level=(), extra_files=(), exclude=None):
    """{key: (size, mtime_ns)} for the files a step's outputs could be.

    Three shapes, because the producers use three:
      ``recursive``   trees walked in full -- ``data/`` and ``outputs/``, where the sealed press
                      run directories are themselves evidence.
      ``top_level``   directories whose OWN files count but whose subdirectories do not.  The
                      project root is one: ``social_media.db`` (with its ``-wal``/``-shm``),
                      ``press_text_cache.jsonl`` and ``ranks_python.json`` live there, and
                      walking the root recursively would pull in the whole checkout.
      ``extra_files`` named files outside the project entirely -- today the one the press fold
                      writes, the production URL baseline under ``Draft Systems``.

    ``exclude`` is a predicate over the key.  Counting the runner's own writes as evidence that a
    step wrote would make every step torn, so it excludes them, and logs and lock files change for
    reasons that have nothing to do with a producer's outputs.
    """
    base = os.path.abspath(str(base))
    found = {}

    def take(full):
        key = key_for(base, full)
        if exclude is not None and exclude(key):
            return
        try:
            info = os.stat(full)
        except FileNotFoundError:
            # A file that vanished between the walk and the stat is not in this snapshot; if it
            # mattered it will show as a difference against the other side.
            return
        found[key] = (info.st_size, info.st_mtime_ns)

    def walk_error(error):
        if not isinstance(error, FileNotFoundError):
            raise error

    for root in recursive:
        for directory, subdirectories, files in os.walk(os.path.abspath(str(root)), onerror=walk_error):
            subdirectories[:] = sorted(name for name in subdirectories
                                       if name not in ('.git', '__pycache__'))
            for name in sorted(files):
                take(os.path.join(directory, name))
    for root in top_level:
        root = os.path.abspath(str(root))
        try:
            entries = sorted(os.scandir(root), key=lambda entry: entry.name)
        except FileNotFoundError:
            continue
        for entry in entries:
            try:
                if entry.is_file(follow_symlinks=False):
                    take(entry.path)
            except FileNotFoundError:
                continue
    for path in extra_files:
        take(os.path.abspath(str(path)))
    return found


def load_snapshot(path):
    """Read a persisted snapshot back into the tuple form :func:`snapshot` returns."""
    payload, problem = read_json(path)
    if problem or not isinstance(payload, dict):
        return None
    out = {}
    for relative, value in payload.items():
        if isinstance(value, (list, tuple)) and len(value) == 2:
            out[relative] = (int(value[0]), int(value[1]))
    return out


def diff(pre, post):
    """Relative paths that are new in ``post`` or whose (size, mtime_ns) changed, sorted."""
    if pre is None:
        pre = {}
    return sorted(name for name, value in post.items() if pre.get(name) != value)


def removed(pre, post):
    """Relative paths present in ``pre`` but gone from ``post``, sorted.

    Kept apart from :func:`diff`: the commit path treats ``diff`` as "files this step wrote".
    The failure and reconcile paths union the two, because a step that deleted a file before it
    died wrote to the tree and its outputs are TORN.
    """
    if pre is None:
        return []
    return sorted(name for name in pre if name not in post)


def file_rows(path):
    """Row count for a table, top-level length for a small JSON document, else None.

    Split on b'\\n' only: ``str.splitlines`` also breaks on form feed and U+2028, which a CSV
    field legitimately contains, and would report a row count no reader of the file agrees with.
    """
    path = str(path)
    lower = path.lower()
    try:
        size = os.path.getsize(path)
    except OSError:
        return None
    if size > ROW_COUNT_MAX_BYTES:
        return None
    if lower.endswith(('.csv', '.jsonl', '.ndjson')):
        try:
            with open(path, 'rb') as handle:
                data = handle.read()
        except OSError:
            return None
        if not data:
            return 0
        lines = data.split(b'\n')
        if lines and lines[-1] == b'':
            lines.pop()
        return len(lines)
    if lower.endswith('.json'):
        payload, problem = read_json(path)
        if problem or payload is None:
            return None
        try:
            return len(payload)
        except TypeError:
            return None
    return None


def sha256_file(path):
    """Hex digest, or None when the file cannot be read."""
    digest = hashlib.sha256()
    try:
        with open(str(path), 'rb') as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def describe_file(base, key, carry=None):
    """One manifest entry: {size, sha256, rows, mtime_ns}.

    ``carry`` is the same file's entry in the step's PREVIOUS manifest.  When size and mtime_ns
    still equal what that manifest recorded, the file is byte-for-byte what was hashed then and
    the digest is carried forward instead of re-read: a step that owns 20,000 unchanged files
    must not cost gigabytes of hashing per tick just to say they are unchanged.  Anything else is
    hashed.  ``mtime_ns`` is recorded for exactly this comparison -- carrying a digest forward on
    a file some other writer touched between ticks would put a WRONG sha256 in the manifest.
    """
    full = resolve_key(base, key)
    try:
        info = os.stat(full)
    except OSError:
        return None
    if (carry and carry.get('sha256') and carry.get('size') == info.st_size
            and carry.get('mtime_ns') == info.st_mtime_ns):
        return {'size': info.st_size, 'sha256': carry['sha256'], 'rows': carry.get('rows'),
                'mtime_ns': info.st_mtime_ns}
    return {'size': info.st_size, 'sha256': sha256_file(full), 'rows': file_rows(full),
            'mtime_ns': info.st_mtime_ns}


def build_manifest(step_name, generation, base, files, now=None):
    """{step, generation, finished_utc, files:{relative: {...}}}."""
    return {'schema': SCHEMA, 'step': step_name, 'generation': int(generation),
            'finished_utc': stamp(now or utcnow()), 'files': files}


def manifest_filename(step_slug, payload):
    """Content-addressed: ``<step-slug>.<sha12>.json``.

    The digest is over the manifest's own canonical bytes, so the name IS the verification of
    which manifest the state means, and two runs that produced identical outputs reuse one file.
    """
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':')).encode('utf-8')
    return '%s.%s.json' % (step_slug, hashlib.sha256(body).hexdigest()[:12])


def verify_manifest(base, payload):
    """Names of the manifested files that are missing or whose bytes no longer match."""
    bad = []
    for key, entry in sorted((payload.get('files') or {}).items()):
        full = resolve_key(base, key)
        if not os.path.exists(full):
            bad.append(key)
            continue
        expected = (entry or {}).get('sha256')
        if not expected or sha256_file(full) != expected:
            bad.append(key)
    return bad


def coverage_review(previous_files, current_files):
    """(zeros, shrinks) between a step's previous manifest and its new one.

    ``zeros`` is the blocking list: files that carried at least ZERO_FLOOR_ROWS rows and now carry
    none, are empty, or have vanished.  ``shrinks`` is everything else that got smaller, recorded
    as information and never a block -- there is no per-source data from which to set a threshold.
    """
    zeros, shrinks = [], []
    for relative, before in sorted((previous_files or {}).items()):
        prior_rows = (before or {}).get('rows')
        prior_size = (before or {}).get('size') or 0
        after = (current_files or {}).get(relative)
        if after is None:
            if prior_rows is not None and prior_rows >= ZERO_FLOOR_ROWS:
                zeros.append({'file': relative, 'was_rows': prior_rows, 'now': 'missing'})
            continue
        rows = after.get('rows')
        size = after.get('size') or 0
        # A CSV's row count includes its header line, so the commonest "plausible zero" -- a
        # collector that found nothing and still wrote its header -- counts ONE row, not none.
        floor = 1 if relative.lower().endswith('.csv') else 0
        collapsed = size == 0 or (rows is not None and rows <= floor)
        if prior_rows is not None and prior_rows >= ZERO_FLOOR_ROWS and collapsed:
            zeros.append({'file': relative, 'was_rows': prior_rows, 'now_rows': rows,
                          'now_size': size})
            continue
        if prior_rows and rows is not None and 0 < rows < prior_rows:
            shrinks.append({'file': relative, 'was_rows': prior_rows, 'now_rows': rows,
                            'ratio': round(float(rows) / float(prior_rows), 4)})
        elif prior_size and 0 < size < prior_size and rows is None:
            shrinks.append({'file': relative, 'was_bytes': prior_size, 'now_bytes': size,
                            'ratio': round(float(size) / float(prior_size), 4)})
    return zeros, shrinks[:MAX_RECORDED_SHRINKS]


# =============================================================================================
# the state store
# =============================================================================================
def blank_step():
    return {'state': NEVER, 'last_ok_utc': None, 'last_attempt_utc': None,
            'attempts_since_ok': 0, 'failure': None, 'duration_s': None, 'generation': 0,
            'manifest_sha': None, 'torn': False, 'coverage': COVERAGE_OK,
            'failure_class': None, 'next_attempt_utc': None, 'recent_attempts_utc': [],
            'dead_letter': None, 'budget_stop': None, 'quota_stop_utc': None}


def recent_attempts(stamps, now, window_hours=ATTEMPT_WINDOW_HOURS):
    """The attempt stamps still inside the rolling window.

    A stamp up to an hour in the FUTURE still counts (the clock stepped back a little, and the
    conservative reading is "that attempt happened"); one further ahead is clock damage, not an
    attempt, and is dropped so a bad step cannot hold a step off for days.
    """
    kept = []
    for text in stamps or ():
        age = hours_since(text, now)
        if age is not None and -1.0 <= age <= window_hours:
            kept.append(text)
    return kept


def record_attempt(stamps, now):
    """``stamps`` pruned to the rolling window, plus ``now``; bounded so the state stays small."""
    kept = recent_attempts(stamps, now) + [stamp(now)]
    return kept[-ATTEMPTS_REMEMBERED:]


def freshness_record(tier, cadence_h, entry, now, window_days, manifest_unreadable=False):
    """One source's label for ``source_freshness.json``.  Pure: it reads one state entry.

    The rule, in order.  A quarantined contract refusal is DEAD_LETTER whatever its age.  A source
    with no readable last success, torn or unreconciled outputs or a suspect coverage is DEGRADED:
    what is served is not a current, whole generation.  A provider quota stop (BUDGET_QUOTA) is
    STALE_LABELLED whatever its age: the latest generation was never installed and the last-good one
    is served knowingly (D-211).  Past its cadence it is DEGRADED when the latest attempt failed (the
    system KNOWS it is behind) and STALE_LABELLED when nothing is known to be failing; inside its
    cadence it is FRESH even if the latest attempt failed, because the data still describes the
    interval it promises.  A bounded call-budget run (BUDGET_CALLS) is a PASS at the normal
    cadence; ``partial`` says its backlog is not drained.  A source whose manifest could not be
    opened this tick (``manifest_unreadable``) is DEGRADED: its last recorded generation is what is
    served, and nothing this tick could verify it (D-212).
    """
    last_ok = entry.get('last_ok_utc')
    age = hours_since(last_ok, now)
    state = entry.get('state') or NEVER
    failed = state in (FAILED, RUNNING)
    failure = entry.get('failure') if isinstance(entry.get('failure'), dict) else {}
    # Only a classified exit or timeout carries a class; a killed or interrupted step's stale class
    # from an earlier failure would otherwise be reported as the reason it is behind.
    klass = (entry.get('failure_class')
             if state == FAILED and failure.get('kind') in (EXIT, TIMEOUT) else None)
    budget = entry.get('budget_stop') if isinstance(entry.get('budget_stop'), dict) else None
    dead = entry.get('dead_letter') if isinstance(entry.get('dead_letter'), dict) else None
    detail = ''
    if state == FAILED and klass == DATA_CONTRACT and dead:
        status = DEAD_LETTER
        detail = ('data-contract refusal dead-lettered at %s; no automatic retry until an input '
                  'generation changes' % dead.get('at'))
    elif age is None:
        status = DEGRADED
        detail = 'has never succeeded' if not last_ok else 'the last success stamp is unreadable'
    elif manifest_unreadable:
        status = DEGRADED
        detail = ('its manifest could not be opened this tick: the last recorded generation is '
                  'served UNVERIFIED')
    elif entry.get('torn') is not False:
        status = DEGRADED
        detail = ('outputs are torn' if entry.get('torn')
                  else 'outputs await complete snapshot reconciliation')
    elif entry.get('coverage') != COVERAGE_OK:
        status, detail = DEGRADED, 'coverage is %s' % entry.get('coverage')
    elif klass == BUDGET_QUOTA:
        status = STALE_LABELLED
        detail = ('classifier quota exhausted (%s): the new generation was NOT installed, the '
                  'last-good one is served; one attempt per daily window'
                  % (budget or {}).get('reason', STOP_QUOTA))
    elif cadence_h and age >= cadence_h:
        status = DEGRADED if failed else STALE_LABELLED
        detail = 'last success %.1f h ago, cadence %d h' % (age, cadence_h)
        if failed:
            detail += '; the latest attempt failed (%s)' % (klass or 'unclassified')
        if age > window_days * 24:
            detail += '; past the %d-day window' % window_days
    else:
        status = FRESH
    return {'tier': tier, 'cadence_h': cadence_h, 'last_success_utc': last_ok,
            'age_h': None if age is None else round(age, 2), 'status': status, 'detail': detail,
            'state': state, 'generation': int(entry.get('generation') or 0),
            'failure_class': klass, 'next_attempt_utc': entry.get('next_attempt_utc') if failed
            else None,
            'attempts_24h': len(recent_attempts(entry.get('recent_attempts_utc'), now)),
            'partial': state == PARTIAL or bool(budget and budget.get('reason') == STOP_CALLS),
            'budget_stop': budget['reason'] if budget else None,
            'served_last_good': status != FRESH and last_ok is not None}


class RefreshState(object):
    """``data/engine/refresh_state.json``: per-step records plus the publication record.

    Every write is one atomic replace of the whole document, and that write is the commit point
    of a step.  ``rotated`` says the file on disk was unreadable and was moved aside; the caller
    logs it and everything becomes due, which is the only honest thing to do with state we cannot
    read -- resuming on it would be resuming on a guess.
    """

    def __init__(self, path, now=None):
        self.path = str(path)
        self.now = now if now is not None else utcnow
        self.rotated = ''
        self.data = self._fresh()

    def _fresh(self):
        return {'schema': SCHEMA, 'updated_utc': stamp(self.now()), 'steps': {},
                'publication': None, 'last_tick': None}

    def load(self, rotate=True):
        """Read the state file, or rotate an unreadable one and start clean.  Returns self.

        ``rotate=False`` is for the read-only modes (``--dry-run``, ``--status``), which promise
        to change no byte: rotating is a WRITE, and a corrupt file would be moved by a command the
        operator ran precisely because they did not want anything touched.
        """
        payload, problem = read_json(self.path)
        if payload is None and not problem:
            self.data = self._fresh()
            return self
        if problem or not isinstance(payload, dict) or payload.get('schema') != SCHEMA \
                or not isinstance(payload.get('steps'), dict):
            self.rotated = (self._rotate(problem or 'not a schema-%d state document' % SCHEMA)
                            if rotate
                            else 'unreadable (%s) — NOT rotated (read-only mode)'
                                 % (problem or 'not a schema-%d state document' % SCHEMA))
            self.data = self._fresh()
            return self
        payload.setdefault('publication', None)
        payload.setdefault('last_tick', None)
        self.data = payload
        return self

    def _rotate(self, problem):
        target = '%s.corrupt-%s.json' % (self.path[:-5] if self.path.endswith('.json')
                                         else self.path,
                                         self.now().strftime('%Y%m%dT%H%M%SZ'))
        try:
            os.replace(self.path, target)
        except OSError as error:
            return 'unreadable (%s) and could not be rotated (%s)' % (problem,
                                                                      type(error).__name__)
        return 'unreadable (%s) — rotated to %s' % (problem, os.path.basename(target))

    # ---- per-step access ----------------------------------------------------------------
    def step(self, name):
        entry = self.data['steps'].get(name)
        if not isinstance(entry, dict):
            entry = blank_step()
            self.data['steps'][name] = entry
        for key, value in blank_step().items():
            entry.setdefault(key, value)
        return entry

    def state_of(self, name):
        return self.step(name).get('state') or NEVER

    def record(self, name, **fields):
        """Update one step IN MEMORY.  Nothing is durable until :meth:`commit`."""
        entry = self.step(name)
        entry.update(fields)
        return entry

    def running_steps(self):
        return [name for name, entry in self.data['steps'].items()
                if isinstance(entry, dict) and entry.get('state') == RUNNING]

    # ---- publication --------------------------------------------------------------------
    def publication(self):
        value = self.data.get('publication')
        return value if isinstance(value, dict) else None

    def last_publish_utc(self):
        return (self.publication() or {}).get('last_publish_utc')

    def set_publication(self, inputs, degraded, stale_sources, now=None):
        self.data['publication'] = {
            'last_publish_utc': stamp(now or self.now()),
            'degraded': bool(degraded),
            'inputs': inputs,
            'stale_sources': list(stale_sources),
        }
        return self.data['publication']

    # ---- tick record --------------------------------------------------------------------
    def set_last_tick(self, outcome, exit_code, started_utc, ended_utc, **fields):
        record = {'outcome': outcome, 'exit_code': int(exit_code),
                  'started_utc': started_utc, 'ended_utc': ended_utc}
        record.update(fields)
        self.data['last_tick'] = record
        return record

    def last_tick(self):
        value = self.data.get('last_tick')
        return value if isinstance(value, dict) else None

    # ---- the commit ---------------------------------------------------------------------
    def commit(self):
        """ONE atomic write of the whole document.  This is what makes a step OK."""
        self.data['schema'] = SCHEMA
        self.data['updated_utc'] = stamp(self.now())
        return write_json_atomic(self.path, self.data)


# =============================================================================================
# the status marker: a PROJECTION of the state, written by nobody else
# =============================================================================================
def render_status(state, phase, current_step=None, waited_seconds=0, tick=None):
    """Build the marker payload from state.  Pure -- it writes nothing.

    ``last_tick`` and ``last_publication`` are SEPARATE blocks carried from state, so a blocked
    or failed fire reports itself without erasing what the last good tick did, and no terminal
    outcome appears while ``phase`` still says a tick is running.
    """
    payload = {
        'schema': SCHEMA,
        'phase': phase,
        'current_step': current_step,
        'waited_seconds': int(waited_seconds or 0),
        'pid': os.getpid(),
        'updated_utc': stamp(utcnow()),
        'last_tick': state.last_tick(),
        'last_publication': state.publication(),
    }
    if tick is not None:
        payload['tick'] = tick
    return payload


class StatusMarker(object):
    """The marker writer, with a budget.

    It is a projection nobody depends on, so it may never delay a step: at most
    :data:`STATUS_REPLACE_ATTEMPTS` attempts with a short backoff, and after
    :data:`STATUS_FAILURE_BUDGET` consecutive failures it gives up for the rest of the tick and
    says so once.  The outcome is in the state file either way.
    """

    def __init__(self, path, log=None):
        self.path = str(path)
        self.log = log
        self.failures = 0
        self.abandoned = False
        self.last = None

    def write(self, payload):
        self.last = payload
        if self.abandoned:
            return False
        try:
            write_json_atomic(self.path, payload, attempts=STATUS_REPLACE_ATTEMPTS,
                              backoff=STATUS_REPLACE_BACKOFF)
        except (OSError, ValueError) as error:
            self.failures += 1
            if self.failures >= STATUS_FAILURE_BUDGET:
                self.abandoned = True
                if self.log:
                    self.log('status marker unwritable (%s) — abandoned for this tick; the '
                             'outcome is in refresh_state.json' % type(error).__name__)
            return False
        self.failures = 0
        return True


# =============================================================================================
# the review list and the tick ledger
# =============================================================================================
def append_review(path, entries, now=None):
    """Append coverage findings to ``data/engine/refresh_review.json`` for Paul.

    An unreadable review file is rewritten rather than lost: this is a report, and refusing to
    record a new finding because an old one cannot be parsed would hide exactly the thing the
    file exists to surface.
    """
    if not entries:
        return 0
    payload, _problem = read_json(path)
    if not isinstance(payload, list):
        payload = []
    moment = stamp(now or utcnow())
    for entry in entries:
        record = dict(entry)
        record.setdefault('recorded_utc', moment)
        payload.append(record)
    overflow = len(payload) - REVIEW_MAX_ENTRIES
    if overflow > 0:
        # The file is re-read and rewritten whole on every append, and shrink records alone can
        # add thousands of rows a tick.  The OLDEST INFORMATION-ONLY rows go first; a blocking
        # row is dropped only when the file holds nothing but blocking rows.
        kept = []
        for record in payload:
            if overflow > 0 and not (isinstance(record, dict) and record.get('blocking')):
                overflow -= 1
                continue
            kept.append(record)
        payload = kept[-REVIEW_MAX_ENTRIES:]
    write_json_atomic(path, payload)
    return len(entries)


def append_tick(path, record):
    """One line per tick in ``data/engine/refresh_ticks.jsonl``.  Metrics are read from here."""
    return append_line(path, json.dumps(record, ensure_ascii=False, sort_keys=True))


def collect_manifests(directory, keep):
    """Delete manifest files the state does not reference.  Returns how many went.

    Run at the END of a tick, never during one: a manifest written but not yet committed is the
    next state write's target, and collecting it mid-tick would delete the thing about to be
    referenced.
    """
    removed = 0
    try:
        names = sorted(os.listdir(str(directory)))
    except OSError:
        return 0
    for name in names:
        if not name.endswith('.json') or name in keep:
            continue
        try:
            os.unlink(os.path.join(str(directory), name))
            removed += 1
        except OSError:
            pass
    return removed

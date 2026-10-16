# -*- coding: utf-8 -*-
import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))); from paths import ROOT as MB_ROOT
"""COLLECTOR SUPERVISOR (user 2026-07-06 'Do whatever is best for the data collection').
Session-independent resilience: my Claude Code session restarted twice and each restart killed
the collectors (they were session-child processes) and orphaned the execution-queue gates. This
runs under Windows Task Scheduler (every 10 min), which survives session death, machine sleep
(wake timers on), and reboots. It is IDEMPOTENT and self-terminating.

Each tick:
  1. For every target leg not currently done (no sentinel, or one older than SENTINEL_FRESH_S --
     sentinels EXPIRE, Paul 2026-08-29): if no live process is running it, relaunch
     it DETACHED (survives THIS supervisor's exit). Collectors are resumable via iv_fetch_log, so
     a relaunch never repeats paid/finished work. A leg that IS running but has made no progress
     (neither its collector-log rows NOR its process CPU have advanced) for STALL_SECONDS is
     treated as WEDGED and killed+relaunched -- the process-exists check alone cannot see a hung
     socket that the 90s timeout never fired on (observed twice on the Fox leg, 2026-07-06).
     A leg that keeps being relaunched WITHOUT ever committing a sentinel is rate-limited per
     target (REFIRE_BACKOFF_S: 30 min, doubling, 24 h cap) and says so in the log every skipped
     tick -- it still counts as PENDING, so the gates stay blocked exactly as before.
  2. When ALL targets hold a FRESH completion sentinel -> run the execution-queue gates ONCE:
       GATE-1 (free): weekly_refresh (corpus absorption + audit_full at the chain tail).
       GATE-2 (everything-done): the USER-APPROVED full big5 re-score (CREC + interviews) +
              the ONE pre-registered C retest + reference/co-appearance increments + desks.
     Each gate is guarded by its own done-flag so a later tick never re-fires it.
  3. When GATE-2 is done, self-unregister the scheduled task and exit.

Targets are read from data/stance/collector_targets.json (written by the launcher) so the schedule can be
re-pointed without editing code. Spend note: GATE-2 runs big5_llm --go (approved); the script's
own cost gate prints first and a dry account stops cleanly (cache preserves paid work)."""
import os, sys, json, re, subprocess, datetime, time, glob, threading, traceback

B = MB_ROOT
FE = os.path.join(B, 'fec')
SENT = os.path.join(B, 'collector_sentinels')
LOG = os.path.join(B, 'data/stance/collector_supervisor.log')
# LOUD IMPORT FAILURE (review 2026-08-29). audit_failure_gate.py holds the ONE ratified
# KNOWN_GATED_FAILURES list and the parser GATE-2 uses to judge audit_full's verdict, so a missing
# module MUST stay FATAL — there is deliberately no fallback that lets a tick proceed without it.
# What was not acceptable is that it was fatal SILENTLY: this import runs before log() exists, Task
# Scheduler discards the child's stderr, and the supervisor log — the only unattended diagnostic
# surface — would show nothing at all. Every 10-minute tick would die at import and the operator's
# evidence would be an empty log plus stalled collection. So write one self-contained line straight
# to the log file (no helpers, no imports beyond os/sys/datetime, all already bound above) and
# RE-RAISE. Both writes are individually guarded: a failure to report must not replace the real
# ImportError with a second, less informative one.
try:
    from audit_failure_gate import tolerate_known_gated   # shared with weekly_refresh's GATE-1 verdict
except ImportError as _gate_import_error:
    _fatal_line = ('[%s] FATAL: audit_failure_gate import failed — supervisor cannot run: %s'
                   % (datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), _gate_import_error))
    try:
        sys.stderr.write(_fatal_line + '\n')
    except Exception:
        pass
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, 'a', encoding='utf-8') as _fatal_handle:
            _fatal_handle.write(_fatal_line + '\n')
    except Exception:
        pass
    raise
TARGETS_F = os.path.join(B, 'data/stance/collector_targets.json')
PROG_F = os.path.join(B, 'data/stance/collector_progress.json')  # per-leg progress snapshot for wedge detection
DBF = os.path.join(B, 'social_media.db')
STALL_SECONDS = 2700   # 45 min flat on BOTH db-progress AND CPU on a LIVE leg = wedged -> kill+relaunch
CPU_EPS = 1.0          # CPU-seconds; a healthy leg clears this in a tick, a wedged one accrues ~0.003
DETACHED = 0x00000008 | 0x00000200   # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
# SENTINEL FRESHNESS WINDOW (Paul ruling 2026-08-29: "the whole point of the sentinels is that they
# fire on a timed basis"). The completion predicate used to be EXISTENCE ONLY -- one
# collector_sentinels/<tag>.done file marked its leg done FOREVER, so after the first successful
# walk the target was never collected again and the interview corpus silently froze at that date.
# A sentinel now EXPIRES: a target counts done only while its sentinel's mtime is inside this
# window; a stale one puts the target back in the pending set and the leg re-fires. Default is one
# week, matching the system's synchronized weekly cadence (weekly_refresh). Legs are checkpointed
# and resumable through iv_fetch_log, so a re-fire is a cheap incremental walk, never repeated
# paid/finished work. Env dial: SENTINEL_FRESH_S=<seconds>.
SENTINEL_FRESH_S = float(os.environ.get('SENTINEL_FRESH_S', '604800'))   # 7 days
# Paul ruling 2026-08-28 "There should be no cap": gate steps run UNCAPPED by default. The old fixed
# caps killed real work mid-flight twice (reference --score 2026-07-07 at 6000s, 9000/10665 scored;
# weekly_refresh measured ~107 min on 2026-08-25 vs the 6000s cap). Revert switch: set
# GATE_STEP_TIMEOUT=<seconds> to restore a uniform cap. Leg wedge detection (STALL_SECONDS) is
# unchanged - it is progress-based, not a timer.
GATE_TIMEOUT = float(os.environ.get('GATE_STEP_TIMEOUT', '0')) or None
GATE_RECEIPT_SCHEMA = 'collector-gate-receipt-v1'
# GATE-1 RETRY COOLDOWN (2026-08-28). weekly_refresh is a 30-120 minute unattended job that re-walks
# every collector before it can fail. On a hard failure the supervisor used to relaunch it on the
# VERY NEXT 10-minute tick: data/stance/collector_supervisor.log shows ~15 back-to-back full
# attempts between 16:26 and 22:06 that day, each burning real scrape work to re-reach the same
# failure. Back off instead — 15 min after the first failure, doubling, capped at 4 h — and say in
# the log why a tick is skipping. Cleared the moment a refresh completes.
# SCOPE: this gates the weekly_refresh RELAUNCH only. Leg supervision and the 10-minute tick
# cadence are untouched: _tick() returns before run_gates() whenever any collector is still pending.
GATE1_COOLDOWN_S = float(os.environ.get('GATE1_RETRY_COOLDOWN_S', '900'))        # 15 min, first retry
GATE1_COOLDOWN_MAX_S = float(os.environ.get('GATE1_RETRY_COOLDOWN_MAX_S', '14400'))   # 4 h ceiling
GATE1_COOLDOWN_SCHEMA = 'collector-gate1-cooldown-v1'

# GATE-2 COOLDOWN + STEP RECEIPTS (2026-10-14, Paul authorized). GATE-2 holds the paid big5 --go, the
# paid reference --score and the pre-registered ONE-LOOK C retest, and used to re-enter all of them from
# the top on every 10-minute tick after any later failure. Now: (1) every GATE-2 stop backs off like
# GATE-1 (15 min doubling, 4 h cap; env GATE2_RETRY_COOLDOWN_S / GATE2_RETRY_COOLDOWN_MAX_S);
# (2) each completed step is receipted in _gate2_steps_path() and never re-runs; (3) the C retest records a
# marker BEFORE it launches and is refused forever after. To deliberately redo a step, Paul deletes its
# entry (or the file); nothing here ever does. The steps file authorizes spend, so an unreadable or
# wrong-schema file fails CLOSED (the cooldown file fails open, like GATE-1's).
GATE2_COOLDOWN_S = float(os.environ.get('GATE2_RETRY_COOLDOWN_S', '900'))
GATE2_COOLDOWN_MAX_S = float(os.environ.get('GATE2_RETRY_COOLDOWN_MAX_S', '14400'))
GATE2_COOLDOWN_SCHEMA = 'collector-gate2-cooldown-v1'
GATE2_STEPS_SCHEMA = 'collector-gate2-steps-v1'
# PER-TARGET REFIRE BACKOFF (review 2026-08-29). The 7-day freshness window above expires EVERY
# target on the same weekly boundary. A leg that completes without committing its sentinel — the
# 2026-08-28 dispatch-bug class, where only the cnn branch called _sentinel() — therefore comes up
# pending on every subsequent tick and is relaunched every ~10 minutes forever, blocking the gates
# invisibly and burning real scrape work to re-reach the same non-commit.
# The gates MUST stay blocked: a target without a fresh sentinel is not done, and no amount of
# retrying changes that (there is no gates-proceed path here). What changes is that the RELAUNCH
# becomes rate-limited and LOUD. A target's first fire in a pending episode is immediate (unchanged
# behaviour); each later fire that follows a fire which never produced a sentinel waits 30 min,
# doubling, capped at 24 h, and every skipped tick names the target and the remaining wait.
# SCOPE: this paces the DEAD-leg relaunch only, and it is PER TARGET — one broken leg never delays
# another leg's first fire. It deliberately does not touch the wedge path (kill+relaunch of a LIVE
# hung process), which already has its own progress-based STALL_SECONDS limiter and is recovery
# from a hang, not a refire storm. Env dials: COLLECTOR_REFIRE_BACKOFF_S /
# COLLECTOR_REFIRE_BACKOFF_MAX_S.
REFIRE_BACKOFF_S = float(os.environ.get('COLLECTOR_REFIRE_BACKOFF_S', '1800'))            # 30 min
REFIRE_BACKOFF_MAX_S = float(os.environ.get('COLLECTOR_REFIRE_BACKOFF_MAX_S', '86400'))   # 24 h
REFIRE_SCHEMA = 'collector-refire-state-v1'

def _gate_receipt_path(gate):
    return os.path.join(B, 'data', 'engine', 'collector_%s_receipt.json' % gate.lower().replace('-', ''))

def _gate_receipt_valid(gate):
    try:
        receipt = json.load(open(_gate_receipt_path(gate), encoding='utf-8'))
        return (receipt.get('schema') == GATE_RECEIPT_SCHEMA and
                receipt.get('gate') == gate and receipt.get('status') == 'COMPLETE')
    except Exception:
        return False

def _write_gate_receipt(gate, steps):
    path = _gate_receipt_path(gate)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {'schema': GATE_RECEIPT_SCHEMA, 'gate': gate, 'status': 'COMPLETE',
               'completed_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
               'steps': steps}
    temporary = '%s.%d.tmp' % (path, os.getpid())
    with open(temporary, 'w', encoding='utf-8') as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write('\n')
    os.replace(temporary, path)

def _gate1_cooldown_path():
    return os.path.join(B, 'data', 'engine', 'collector_gate1_cooldown.json')

def _gate1_backoff_seconds(failures):
    """Doubling backoff, clamped. The exponent is capped before it is applied so a long-running
    failure streak can never overflow the float multiply."""
    if failures < 1: return 0.0
    return min(GATE1_COOLDOWN_S * (2 ** min(int(failures) - 1, 20)), GATE1_COOLDOWN_MAX_S)

def _gate1_cooldown_remaining():
    """(remaining_seconds, consecutive_failures). 0 remaining = clear to relaunch.
    An absent, unreadable, wrong-schema or future-dated marker is treated as CLEAR. This cooldown
    exists to stop the machine burning identical work; it must never become the thing that
    permanently prevents a refresh, so every uncertain path fails OPEN (unlike the gate receipts,
    which fail closed because they authorize spend)."""
    try:
        state = json.load(open(_gate1_cooldown_path(), encoding='utf-8'))
    except Exception:
        return 0.0, 0
    if not isinstance(state, dict) or state.get('schema') != GATE1_COOLDOWN_SCHEMA:
        return 0.0, 0
    try:
        failures = int(state.get('consecutive_failures') or 0)
        last = float(state.get('last_failure_at') or 0)
    except (TypeError, ValueError):
        return 0.0, 0
    if failures < 1: return 0.0, 0
    elapsed = time.time() - last
    if elapsed < 0: return 0.0, failures        # clock/DST moved backwards -> do not wedge
    return max(0.0, _gate1_backoff_seconds(failures) - elapsed), failures

def _gate1_write_cooldown(failures):
    path = _gate1_cooldown_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        payload = {'schema': GATE1_COOLDOWN_SCHEMA, 'consecutive_failures': int(failures),
                   'last_failure_at': time.time(),
                   'last_failure_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                   'next_retry_after_s': _gate1_backoff_seconds(failures)}
        temporary = '%s.%d.tmp' % (path, os.getpid())
        with open(temporary, 'w', encoding='utf-8') as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write('\n')
        os.replace(temporary, path)
    except Exception as e:
        log('  cooldown marker write failed (%s) — retry pacing degrades to every tick' % str(e)[:60])

def _gate1_record_failure():
    """Bump the consecutive-failure count and return the seconds the next relaunch must wait."""
    _remaining, failures = _gate1_cooldown_remaining()
    failures += 1
    _gate1_write_cooldown(failures)
    return _gate1_backoff_seconds(failures)

def _gate1_clear_cooldown():
    try:
        os.remove(_gate1_cooldown_path())
    except OSError:
        pass

def _gate2_steps_path():   # next to PROG_F; resolved late so B can be re-pointed
    return os.path.join(B, 'data', 'stance', 'collector_gate2_steps.json')

def _atomic_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = '%s.%d.tmp' % (path, os.getpid())
    with open(temporary, 'w', encoding='utf-8') as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write('\n')
    os.replace(temporary, path)

def _gate2_cooldown_path():
    return os.path.join(B, 'data', 'engine', 'collector_gate2_cooldown.json')

def _gate2_backoff_seconds(failures):
    if failures < 1: return 0.0
    return min(GATE2_COOLDOWN_S * (2 ** min(int(failures) - 1, 20)), GATE2_COOLDOWN_MAX_S)

def _gate2_cooldown_remaining():
    """(remaining_seconds, consecutive_failures); fails OPEN on any uncertainty, like GATE-1's."""
    try:
        state = json.load(open(_gate2_cooldown_path(), encoding='utf-8'))
        if state.get('schema') != GATE2_COOLDOWN_SCHEMA: return 0.0, 0
        failures, last = int(state.get('consecutive_failures') or 0), float(state.get('last_failure_at') or 0)
    except Exception:
        return 0.0, 0
    if failures < 1: return 0.0, 0
    elapsed = time.time() - last
    if elapsed < 0: return 0.0, failures
    return max(0.0, _gate2_backoff_seconds(failures) - elapsed), failures

def _gate2_stop(msg):
    """Log why GATE-2 stopped this tick and start/extend the retry backoff. Always returns None."""
    _remaining, failures = _gate2_cooldown_remaining()
    failures += 1
    wait = _gate2_backoff_seconds(failures)
    try:
        _atomic_json(_gate2_cooldown_path(), {'schema': GATE2_COOLDOWN_SCHEMA, 'consecutive_failures': failures,
                     'last_failure_at': time.time(), 'next_retry_after_s': wait,
                     'last_failure_utc': datetime.datetime.now(datetime.timezone.utc).isoformat()})
    except Exception as e:
        log('  gate2 cooldown marker write failed (%s) — retry pacing degrades to every tick' % str(e)[:60])
    log('GATE-2 %s; next attempt no sooner than %.0f min from now' % (msg, wait / 60.0))

def _gate2_load():
    """(state, error). Absent file = fresh state; unreadable/wrong-shaped = error (fail CLOSED)."""
    try:
        state = json.load(open(_gate2_steps_path(), encoding='utf-8'))
    except FileNotFoundError:
        return {'schema': GATE2_STEPS_SCHEMA, 'steps': {}, 'c_retest': None}, None
    except Exception as e:
        return None, 'unreadable (%s)' % str(e)[:60]
    if not isinstance(state, dict) or state.get('schema') != GATE2_STEPS_SCHEMA or not isinstance(state.get('steps'), dict):
        return None, 'wrong schema/shape'
    return state, None

def _gate2_save(state):
    try:
        _atomic_json(_gate2_steps_path(), state)
        return True
    except Exception as e:
        log('  GATE-2 receipt write FAILED (%s)' % str(e)[:80])
        return False

def _gate2_mark(state, name, **extra):
    """Receipt one completed step. False = could not persist (caller must stop: it would re-run)."""
    state['steps'][name] = dict(extra, done_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
    return _gate2_save(state)

def _refire_state_path():
    return os.path.join(B, 'data', 'engine', 'collector_refire_state.json')

def _refire_backoff_seconds(fires):
    """Seconds a target must wait AFTER `fires` consecutive uncommitted fires before it may fire
    again. fires<1 -> 0: the FIRST fire of a pending episode is always immediate. The exponent is
    clamped before it is applied so a long streak can never overflow the float multiply (same
    posture as _gate1_backoff_seconds)."""
    if fires < 1: return 0.0
    return min(REFIRE_BACKOFF_S * (2 ** min(int(fires) - 1, 20)), REFIRE_BACKOFF_MAX_S)

def _refire_load():
    """tag -> {'consecutive_fires', 'last_fire_at', ...}. Absent, unreadable, wrong-schema or
    wrong-shaped state returns {} — i.e. FIRE. Fail-OPEN mirrors the GATE-1 cooldown: this state
    exists to stop the machine burning identical relaunches and must never become the thing that
    permanently prevents collection. Firing is the safe direction anyway: the legs are checkpointed
    through iv_fetch_log, so a spurious fire costs one cheap incremental walk."""
    try:
        state = json.load(open(_refire_state_path(), encoding='utf-8'))
    except Exception:
        return {}
    if not isinstance(state, dict) or state.get('schema') != REFIRE_SCHEMA:
        return {}
    entries = state.get('targets')
    return entries if isinstance(entries, dict) else {}

def _refire_save(entries):
    path = _refire_state_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        payload = {'schema': REFIRE_SCHEMA, 'targets': entries,
                   'updated_utc': datetime.datetime.now(datetime.timezone.utc).isoformat()}
        temporary = '%s.%d.tmp' % (path, os.getpid())
        with open(temporary, 'w', encoding='utf-8') as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write('\n')
        os.replace(temporary, path)
    except Exception as e:
        log('  refire state write failed (%s) — refire pacing degrades to every tick' % str(e)[:60])

def _refire_remaining(entries, tag):
    """(remaining_seconds, consecutive_fires) for ONE target. 0 remaining = clear to fire.
    A malformed entry, or one dated in the FUTURE (clock skew, a DST jump), reads CLEAR — same
    fail-OPEN posture as _refire_load and _gate1_cooldown_remaining."""
    entry = entries.get(tag)
    if not isinstance(entry, dict):
        return 0.0, 0
    try:
        fires = int(entry.get('consecutive_fires') or 0)
        last = float(entry.get('last_fire_at') or 0)
    except (TypeError, ValueError):
        return 0.0, 0
    if fires < 1: return 0.0, 0
    elapsed = time.time() - last
    if elapsed < 0: return 0.0, fires       # clock/DST moved backwards -> do not wedge the target
    return max(0.0, _refire_backoff_seconds(fires) - elapsed), fires

def _refire_record(entries, tag):
    """Count one fire for `tag` (mutating `entries`) and return the new consecutive total."""
    _remaining, fires = _refire_remaining(entries, tag)
    fires += 1
    entries[tag] = {'consecutive_fires': fires, 'last_fire_at': time.time(),
                    'last_fire_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    'next_refire_after_s': _refire_backoff_seconds(fires)}
    return fires

def log(m):
    line = '[%s] %s' % (datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), m)
    print(line, flush=True)
    try:
        with open(LOG, 'a', encoding='utf-8') as f: f.write(line + '\n')
    except Exception: pass

def running_procs():
    """List of {pid, cpu, cmd} for running python processes (Windows CIM). cpu = cumulative CPU seconds.
    A rare multi-char delimiter avoids collisions with the many separators inside command lines.
    CPU is emitted as RAW INTEGER 100ns ticks and divided here — review 2026-07-07: PowerShell's -f
    formats floats with the LOCALE decimal separator (comma on non-US), which float() rejects,
    silently emptying the scan and making every live leg look dead (mass false-relaunch)."""
    try:
        out = subprocess.run(['powershell', '-NoProfile', '-Command',
                              "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
                              "ForEach-Object { '{0}~@~{1}~@~{2}' -f $_.ProcessId, "
                              "($_.KernelModeTime + $_.UserModeTime), $_.CommandLine }"],
                             capture_output=True, text=True, timeout=60).stdout or ''
    except Exception as e:
        # Fail closed.  Treating an unavailable process inventory as "nothing is running"
        # relaunches every pending collector and can duplicate work or spend.
        log('process scan failed (%s) — tick deferred; no collectors relaunched' % str(e)[:60]); return None
    procs = []
    for ln in out.splitlines():
        parts = ln.split('~@~', 2)
        if len(parts) == 3 and parts[0].strip().isdigit():
            try: procs.append({'pid': int(parts[0]), 'cpu': int(parts[1]) / 1e7, 'cmd': parts[2]})
            except Exception: pass
    return procs

def leg_db_metric(args):
    """A monotonically-increasing progress scalar for a leg, from its collector-log keys. CNN also
    commits cnnseg markers constantly (fast signal); Fox in resume-skip mode commits nothing while
    it legitimately walks already-done transcripts -- that case is caught by CPU, not this. -1 on error."""
    leg = args[0]
    try:
        import sqlite3
        c = sqlite3.connect(DBF, timeout=30); cur = c.cursor()
        n = cur.execute("SELECT count(*) FROM iv_fetch_log WHERE key LIKE ?", (leg + ':%',)).fetchone()[0]
        if leg == 'cnn':
            n += cur.execute("SELECT count(*) FROM iv_fetch_log WHERE key LIKE 'cnnseg:%'").fetchone()[0]
        c.close(); return n
    except Exception as e:
        log('db metric failed for %s (%s)' % (leg, str(e)[:50])); return -1

def _leg_args(cmd):
    """The argument list after fetch_interviews.py in a process command line, or None when the
    command line is not a fetch_interviews process.  Real lines look like
    "C:\\...\\python.exe" "C:\\...\\Melange Systems\\fec\\members\\fetch_interviews.py" cnn --since X --until Y
    -- the script path is quoted and contains spaces, the leg arguments never do."""
    m = re.search(r'fetch_interviews\.py["\']?(?=\s|$)(.*)', str(cmd or ''))
    return m.group(1).split() if m else None

def kill_leg(match, tag):
    for p in match:
        try:
            subprocess.run(['taskkill', '/PID', str(p['pid']), '/F', '/T'], capture_output=True, text=True, timeout=30)
            log('  KILLED wedged pid %d (%s)' % (p['pid'], tag))
        except Exception as e:
            log('  kill failed pid %d (%s)' % (p['pid'], str(e)[:40]))

def _load_prog():
    try: return json.load(open(PROG_F, encoding='utf-8'))
    except Exception: return {}

def _save_prog(p):
    # tmp + os.replace (same pattern as _refire_save): a crash mid-dump must never leave a truncated
    # progress file that _load_prog reads as {} and resets every wedge timer.
    try:
        temporary = '%s.%d.tmp' % (PROG_F, os.getpid())
        with open(temporary, 'w', encoding='utf-8') as handle:
            json.dump(p, handle)
        os.replace(temporary, PROG_F)
    except Exception: pass

def sentinel_state(tag, window=None):
    """Classify one target's completion sentinel -> (state, age_seconds).

    state is one of:
      'fresh'      inside the freshness window -> the target counts DONE this tick
      'stale'      present but older than the window -> the target is PENDING and re-fires
      'missing'    never written, or cleared by hand -> PENDING (the pre-2026-08-29 behaviour)
      'unreadable' os.path.exists() said it was there but its mtime could not be read -> PENDING
    age_seconds is None for 'missing' and 'unreadable'.

    Two deliberate asymmetries:
    (a) FUTURE-dated mtimes (clock skew, a DST jump, a stamp copied off another machine) clamp to
        age 0 and read FRESH. A clock that ran backwards must never trigger a re-fire storm, and
        must never raise inside a tick.
    (b) An unreadable mtime -- a racing deletion, a transient sharing violation on OneDrive -- is
        treated as STALE, not as done. Re-firing is the safe direction: the legs resume from
        iv_fetch_log, so a spurious re-fire costs one cheap incremental walk, while wrongly holding
        a target 'done' starves collection until a human notices."""
    if window is None:
        window = SENTINEL_FRESH_S
    path = os.path.join(SENT, tag + '.done')
    if not os.path.exists(path):
        return 'missing', None
    try:
        age = max(0.0, time.time() - os.path.getmtime(path))
    except Exception:          # OSError in practice; never let one unreadable file kill the tick
        return 'unreadable', None
    return ('fresh' if age <= window else 'stale'), age

def launch_detached(args, tag):
    logf = open(os.path.join(B, 'collector_%s.out' % tag), 'a', encoding='utf-8')
    subprocess.Popen([sys.executable, os.path.join(FE, 'members', 'fetch_interviews.py')] + args,
                     stdout=logf, stderr=subprocess.STDOUT, cwd=FE, creationflags=DETACHED, close_fds=True)
    log('RELAUNCHED %s detached: %s' % (tag, ' '.join(args)))

def _joined(stdout, stderr):
    return stdout + (('\n' if stdout and stderr else '') + stderr if stderr else '')

def gate_step_error_path(name):
    """One file per step NAME, overwritten on each failure: the freshest failure is always the one
    on disk, and a step that fails on every 10-minute tick can never fill the volume."""
    slug = re.sub(r'[^a-z0-9]+', '_', (name or '').lower()).strip('_') or 'unnamed'
    return os.path.join(B, 'data', 'engine', 'gate_step_%s.err' % slug)

def _persist_gate_failure(name, out):
    """Keep a failing step's FULL captured output on disk and return the path (None if unwritable).
    The log only ever holds a tail; the bodies that matter most are the ones that do not fit in one
    — a batch-level 400 enumerating every invalid request, or a chain that printed 300 lines before
    it died. Losing those costs a whole retry cycle to reproduce."""
    path = gate_step_error_path(name)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        temporary = '%s.%d.tmp' % (path, os.getpid())
        with open(temporary, 'w', encoding='utf-8', errors='replace') as handle:
            handle.write('# gate step %r FAILED at %s\n# %d chars captured (stdout + stderr)\n\n'
                         % (name, datetime.datetime.now().isoformat(), len(out)))
            handle.write(out)
        os.replace(temporary, path)
        return path
    except Exception as e:
        log('      could not persist failure output (%s)' % str(e)[:80])
        return None

def _run_capped(argv, timeout):
    """subprocess.run replacement for a CAPPED gate step.  On Windows, run(..., timeout=) kills only
    the direct child and then waits on communicate() with no bound, so a grandchild that still holds
    the stdout/stderr pipes hangs the supervisor past its own timeout.  Kill the whole tree with
    taskkill /T /F, then drain the pipes with a bounded wait.  Raises TimeoutExpired carrying
    whatever output was captured, so gate_step's existing timeout branch handles it unchanged."""
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            encoding='utf-8', errors='replace', cwd=FE)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'], capture_output=True, timeout=30)
        except Exception:
            try: proc.kill()          # taskkill itself unavailable: at least take the direct child
            except Exception: pass
        try:
            stdout, stderr = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            stdout, stderr = '', ''
        raise subprocess.TimeoutExpired(argv, timeout, output=stdout, stderr=stderr)
    return subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)

def gate_step(name, script, args, timeout=None):
    """Run one gate step. Accepts absolute script paths (review 2026-07-07: c_prereg_test.py lives at
    the repo ROOT — the old FE-join launched a nonexistent path and the prereg retest silently skipped).
    Never lets a step exception kill the tick: a timeout/crash returns (False, output) so run_gates can
    abort-or-continue deliberately instead of leaving the gate half-fired."""
    t = time.time()
    spath = script if os.path.isabs(script) else os.path.join(FE, script)
    try:
        if timeout is None:      # Paul 2026-08-28 "no cap": the uncapped path is the plain run, unchanged
            r = subprocess.run([sys.executable, spath] + args,
                               capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout, cwd=FE)
        else:
            r = _run_capped([sys.executable, spath] + args, timeout)
        ok = r.returncode == 0
        stdout = r.stdout or ''
        stderr = r.stderr or ''
        # The supervisor log is the unattended diagnostic surface, so on FAILURE stderr is preserved
        # alongside stdout — an exception raised before a producer's first print must never be
        # reduced to a blank tail.  On SUCCESS the returned text is stdout ONLY.  Both consumers of
        # this value parse it: spend_guard takes the LAST 'est ~$' match out of it to authorize real
        # money, and run_gates tests it for 'ALL MODULES CLEAN'.  A producer that writes anything at
        # all to stderr on a successful run — a warning, a progress bar, a deprecation notice — could
        # otherwise contribute a stray dollar figure to a spend decision.  Failure output cannot
        # reach that path: spend_guard returns (False, 0.0) on a failed dry step BEFORE it parses
        # any est line, so a failed step's text is never read as an estimate.
        out = stdout if ok else _joined(stdout, stderr)
    except subprocess.TimeoutExpired as e:
        def _text(value):
            if value is None: return ''
            if isinstance(value, bytes): return value.decode('utf-8', errors='replace')
            return str(value)
        stdout, stderr = _text(e.stdout), _text(e.stderr)
        ok = False
        out = _joined(stdout, stderr)
        log('  %-26s !! TIMEOUT after %ds' % (name, timeout))
    except Exception as e:
        ok, out = False, '%s: %s' % (type(e).__name__, str(e))
        log('  %-26s !! %s' % (name, str(e)[:100]))
    lines = [line for line in out.strip().splitlines() if line.strip()]
    log('  %-26s %s %5.0fs  %s' % (name, 'OK' if ok else '!!', time.time() - t,
                                   (lines[-1] if lines else '')[:1000]))
    if not ok:
        # The last line alone is routinely the wrong line: an aborting producer's final stdout is
        # often an unrelated epilogue.  Every weekly_refresh failure on 2026-08-28 logged only its
        # atexit 'EMAIL REPORT SKIPPED' notice, and the actual failing step appeared nowhere in the
        # supervisor log.  Show the run-up, and keep the whole body on disk.
        for line in lines[-5:-1]:
            log('      prior | %s' % line[:1000])
        path = _persist_gate_failure(name, out)
        if path:
            log('      full output (%d chars) -> %s' % (len(out), path))
    return ok, out

def spend_guard(name, script, base_args, cap_env, cap_default, timeout=None):
    """Two-phase spend control (review 2026-07-07): run the script WITHOUT --go so its own cost gate
    prints 'est ~$X' and exits unspent; parse X; proceed with --go only if X <= cap. An unparseable
    estimate fails CLOSED (no spend). Both big5_llm and member_reference_layer print this format.
    Paul ruling 2026-08-28 "Lift the spend caps too": caps default UNBOUNDED (call sites pass 'inf').
    The dry run and the fail-closed unparseable-estimate guard are kept - they prove the script runs
    and print the cost before --go; only the dollar ceiling is lifted. Revert switch: set
    B5_GATE_CAP / REF_GATE_CAP to a dollar figure to restore a ceiling."""
    cap = float(os.environ.get(cap_env, cap_default))
    ok, out = gate_step(name + ' (dry est)', script, base_args, timeout=timeout if timeout is not None else GATE_TIMEOUT)
    if not ok:
        # Both paid scripts exit 0 on a dry run ('COST GATE: add --go to spend.' then sys.exit(0)),
        # so a non-zero dry run is a real failure -- never authorize --go off a failed step's text.
        log('  %s: dry run failed — failing CLOSED (no spend)' % name); return False, 0.0
    m = re.findall(r'est ~\$([0-9][0-9.,]*)', out)
    if not m:
        log('  %s: no est line found — failing CLOSED (no spend)' % name); return False, 0.0
    est = float(m[-1].replace(',', ''))
    if est > cap:
        log('  %s: est $%.2f EXCEEDS cap $%.0f (%s) — skipping spend; raise the env cap to proceed'
            % (name, est, cap, cap_env))
        return False, est
    log('  %s: est $%.2f within cap $%.0f — proceeding' % (name, est, cap))
    return True, est

def run_gates():
    # GATE-1: free corpus absorption + audit (checkpoint task #1). reference --rebuild inside
    # weekly_refresh is the FREE cache-only path (member_reference_layer.py:187) — GATE-1 spends $0.
    if not _gate_receipt_valid('GATE-1'):
        cooling, consecutive = _gate1_cooldown_remaining()
        if cooling > 0:
            log('GATE-1 COOLDOWN: %d consecutive weekly_refresh failure(s); %.0f of %.0f backoff '
                'minutes remain — weekly_refresh NOT relaunched this tick'
                % (consecutive, cooling / 60.0, _gate1_backoff_seconds(consecutive) / 60.0))
            return
        log('===== GATE-1 (free refresh + audit) =====')
        started = time.time()
        ok_refresh, out = gate_step('weekly_refresh', 'weekly_refresh.py', [], timeout=GATE_TIMEOUT)
        weekly_status_path = os.path.join(B, 'data', 'engine', 'weekly_refresh_status.json')
        try:
            weekly_status = json.load(open(weekly_status_path, encoding='utf-8'))
            status_fresh = os.path.getmtime(weekly_status_path) >= started - 2
            status_complete = weekly_status.get('last_outcome') in (
                'COMPLETE', 'COMPLETE_WITH_KNOWN_GATED_FAILURES')
        except Exception:
            weekly_status, status_fresh, status_complete = {}, False, False
        if not (ok_refresh and status_fresh and status_complete):
            wait = _gate1_record_failure()
            log('GATE-1 INCOMPLETE: weekly_refresh ok=%s status=%s fresh=%s failures=%s — receipt NOT '
                'written; next relaunch no sooner than %.0f min from now'
                % (ok_refresh, weekly_status.get('last_outcome'), status_fresh,
                   weekly_status.get('failures') or [], wait / 60.0))
            return
        _gate1_clear_cooldown()
        _write_gate_receipt('GATE-1', {'weekly_refresh': True,
                                      'weekly_outcome': weekly_status.get('last_outcome'),
                                      'known_failures': weekly_status.get('failures') or []})
        # Retain the legacy marker for older observers, but never trust it as authorization.
        legacy = os.path.join(B, 'gate1.done')
        if not os.path.exists(legacy):
            open(legacy, 'w').write(datetime.datetime.now().isoformat())
        log('GATE-1 complete (weekly outcome=%s)' % weekly_status.get('last_outcome'))
    # GATE-2: everything-done — approved full re-score + the ONE pre-registered C retest.
    # Review 2026-07-07 hardening: (a) --batch = flat 50% off, the right mode unattended (fresh-cache
    # est ~$158 batch vs ~$316 standard vs ~$632 if fast-mode ever activates); (b) B5_FAST pinned off;
    # (c) spend guards with env caps fail CLOSED; (d) if the re-score fails or the artifact is not
    # fresh, ABORT before validate/C-retest — the one-look prereg test must never run on stale data.
    # 2026-10-14: every stop now backs off (_gate2_stop), every finished step is receipted in
    # the GATE-2 steps file and skipped on later ticks (no re-paying big5/reference), all prerequisite scripts
    # must exist BEFORE any paid step, and the C retest runs at most once ever (marker written first).
    if not _gate_receipt_valid('GATE-2'):
        cooling, consecutive = _gate2_cooldown_remaining()
        if cooling > 0:
            log('GATE-2 COOLDOWN: %d consecutive stop(s); %.0f of %.0f backoff minutes remain — NOT '
                'relaunched this tick' % (consecutive, cooling / 60.0, _gate2_backoff_seconds(consecutive) / 60.0))
            return
        log('===== GATE-2 (everything-done: re-score + C retest) =====')
        state, err = _gate2_load()
        if err:
            return _gate2_stop('BLOCKED: step receipt file %s %s — refusing to spend' % (_gate2_steps_path(), err))
        S = state['steps']
        os.environ['B5_FAST'] = '0'   # bulk batch job: never pay the 2x fast-mode premium unattended
        art = os.path.join(B, 'data', 'members', 'big5_llm_opus.json')
        big5 = os.path.join('members', 'big5_llm.py')
        ref = os.path.join('members', 'member_reference_layer.py')
        needed = [('big5 re-score', os.path.join(FE, big5)),
                  ('validate_big5', os.path.join(FE, 'members', 'validate_big5.py')),
                  ('reference --score', os.path.join(FE, ref)),
                  ('co_appearance', os.path.join(FE, 'network', 'co_appearance_layer.py')),
                  ('member desk', os.path.join(FE, 'surfaces', 'make_member_desk.py')),
                  ('combined', os.path.join(FE, 'surfaces', 'make_combined.py')),
                  ('audit_full', os.path.join(FE, 'audit', 'audit_full.py'))]
        if state.get('c_retest') is None:   # a retest already taken never needs its script again
            needed.append(('C retest', os.path.join(B, 'c_prereg_test.py')))
        missing = ['%s (%s)' % (n, p) for n, p in needed if n not in S and not os.path.exists(p)]
        if missing:
            return _gate2_stop('BLOCKED: prerequisite script(s) missing: %s — nothing spent' % '; '.join(missing))
        if 'big5 re-score' not in S:
            t_gate = time.time()
            go, est = spend_guard('big5 re-score', big5, ['--opus', '--batch'], 'B5_GATE_CAP', 'inf')
            if not go:
                return _gate2_stop('HALTED at big5 spend guard — nothing spent, retest untouched')
            ok, out = gate_step('big5 full re-score', big5, ['--opus', '--batch', '--go'], timeout=GATE_TIMEOUT)
            fresh = os.path.exists(art) and os.path.getmtime(art) >= t_gate - 60
            if not (ok and fresh):
                return _gate2_stop('ABORTED: big5 step ok=%s artifact-fresh=%s — retest NOT run (no stale-data '
                                   'look); batch PEND/cache resume, no double-spend' % (ok, fresh))
            if not _gate2_mark(state, 'big5 re-score'):
                return _gate2_stop('STOPPED: could not persist big5 receipt')
        if 'validate_big5' not in S:
            ok_validate, _ = gate_step('validate_big5', os.path.join('members', 'validate_big5.py'),
                                       [art, '--traits-key', 'traits'], timeout=GATE_TIMEOUT)
            if not ok_validate:
                return _gate2_stop('INCOMPLETE: validate_big5 failed — prereg retest NOT run, receipt NOT written')
            if not _gate2_mark(state, 'validate_big5'):
                return _gate2_stop('STOPPED: could not persist validate_big5 receipt')
        # THE pre-registered C retest (bars locked 2026-07-05) — ONE LOOK, ever. The marker is
        # persisted BEFORE the launch, so a crash, timeout or failing verdict still spends the look.
        if 'C retest' not in S:
            taken = state.get('c_retest')
            if taken is not None and taken.get('ok') is True:
                if not _gate2_mark(state, 'C retest', recovered_from_marker=True):
                    return _gate2_stop('STOPPED: could not persist C retest receipt')
            elif taken is not None:
                return _gate2_stop('REFUSED: the one-look C retest was already taken (started %s, ok=%s) and '
                                   'will NEVER re-run; Paul must adjudicate' % (taken.get('started_utc'), taken.get('ok')))
            else:
                state['c_retest'] = {'started_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'ok': None}
                if not _gate2_save(state):
                    return _gate2_stop('STOPPED: could not persist one-look marker — C retest NOT run')
                # absolute path: it lives at repo ROOT
                ok_c, _ = gate_step('C retest (prereg)', os.path.join(B, 'c_prereg_test.py'), [], timeout=GATE_TIMEOUT)
                state['c_retest'].update(ok=bool(ok_c), finished_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
                if not ok_c:
                    _gate2_save(state)
                    return _gate2_stop('INCOMPLETE: C retest did not pass — the one look is spent and will not '
                                       're-run; Paul must adjudicate')
                if not _gate2_mark(state, 'C retest'):
                    return _gate2_stop('STOPPED: could not persist C retest receipt')
        if 'reference --score' not in S:
            go, est = spend_guard('reference --score', ref, ['--score'], 'REF_GATE_CAP', 'inf')
            if not go:
                return _gate2_stop('INCOMPLETE: reference spend guard failed — receipt NOT written')
            ok_reference, _ = gate_step('reference --score', ref, ['--score', '--go'], timeout=GATE_TIMEOUT)
            if not ok_reference:
                return _gate2_stop('INCOMPLETE: reference --score failed — receipt NOT written')
            if not _gate2_mark(state, 'reference --score'):
                return _gate2_stop('STOPPED: could not persist reference receipt')
        failed = []
        for name, script in (('co_appearance', os.path.join('network', 'co_appearance_layer.py')),
                             ('member desk', os.path.join('surfaces', 'make_member_desk.py')),
                             ('combined', os.path.join('surfaces', 'make_combined.py'))):
            if name in S: continue
            ok_step, _ = gate_step(name, script, [], timeout=GATE_TIMEOUT)
            # Stop at the FIRST failed producer: a later step (and audit_full) must never be receipted
            # on top of a stale upstream output, or a later tick would skip it and pass on stale data.
            if not ok_step:
                return _gate2_stop('INCOMPLETE: %s failed — later producers and audit_full NOT run, so none '
                                   'is receipted on stale upstream output' % name)
            if not _gate2_mark(state, name):
                return _gate2_stop('STOPPED: could not persist %s receipt' % name)
        if 'audit_full' not in S:
            ok_audit, out = gate_step('audit_full', os.path.join('audit', 'audit_full.py'), [], timeout=GATE_TIMEOUT)
            # STANDING-DISSENT TOLERANCE (2026-08-28). audit_full exits 1 if ANY check fails
            # (fec\audit\audit_full.py:2214-2217) and TWO checks are standing, already-adjudicated,
            # Paul-gated open items: V-1 (:1513) and the legfix5 culling promotion (:1642). Requiring
            # returncode==0 here therefore made GATE-2 UNPASSABLE: once GATE-1 closed, every 10-minute
            # tick would re-run the whole gate forever, and each retry re-enters the paid big5 and
            # reference steps (their caches/PEND make that cheap, not free). weekly_refresh already
            # makes exactly this judgement for GATE-1; audit_failure_gate.py is the ONE ratified list
            # and parser both use, and it fails CLOSED — an unreadable report, a count/tally mismatch,
            # or ANY name outside the ratified two still hard-fails GATE-2 exactly as before.
            # Sound here for the same reason as in GATE-1: audit_full is the LAST step of this gate, so
            # every producer above already SUCCEEDED (the loop above stops at the first failed producer, so
            # audit_full is never run or receipted past one) — a tolerated audit verdict cannot be masking
            # unrun work. audit_full itself is untouched: it
            # keeps exiting 1 honestly and the dissent stays visible in the log and the receipt.
            audit_known_gated = []
            if not ok_audit:
                tolerate, names = tolerate_known_gated(out, '', log=log, prefix='  ')
                if tolerate:
                    audit_known_gated = list(names)
                    ok_audit = True
                    log('  audit_full: %d KNOWN Paul-gated item(s) only — tolerated for gating: %s'
                        % (len(names), '; '.join(names)))
            if not ok_audit: failed.append('audit_full')
            elif not _gate2_mark(state, 'audit_full', known_gated=audit_known_gated, clean='ALL MODULES CLEAN' in out):
                return _gate2_stop('STOPPED: could not persist audit_full receipt')
        # C-1 (audit 2026-07-08): only mark GATE-2 done (which makes _tick self-unregister the scheduled
        # task) when the dashboard-producing chain AND the audit actually succeeded. On failure, return
        # WITHOUT the done-flag so the next tick retries only the unreceipted steps.
        if failed:
            return _gate2_stop('INCOMPLETE: %s failed -- gate2.done NOT written, self-unregister deferred' % ', '.join(failed))
        audit_known_gated = S['audit_full'].get('known_gated') or []
        # The receipt records WHICH standing items were tolerated, so a clean gate and a
        # gate-that-passed-over-known-dissent are never indistinguishable after the fact.
        required = dict.fromkeys(('validate_big5', 'C retest', 'reference --score', 'co_appearance',
                                  'member desk', 'combined', 'audit_full'), True)
        # NOTE: the steps file is deliberately left in place. If Paul deletes the GATE-2 receipt to start a
        # new cycle he MUST also delete the steps file (keep its c_retest marker -- the one look never
        # re-runs), or every step is skipped and a COMPLETE receipt is written having run nothing.
        _write_gate_receipt('GATE-2', dict(required, audit_known_gated_failures=audit_known_gated))
        try: os.remove(_gate2_cooldown_path())
        except OSError: pass
        legacy = os.path.join(B, 'gate2.done')
        if not os.path.exists(legacy):
            open(legacy, 'w').write(datetime.datetime.now().isoformat())
        log('GATE-2 complete (audit clean=%s%s)'
            % (bool(S['audit_full'].get('clean')),
               '; %d known Paul-gated item(s) tolerated' % len(audit_known_gated)
               if audit_known_gated else ''))

LOCK = os.path.join(B, 'collector_supervisor.lock')

def _acquire_lock():
    """Atomically take LOCK (O_CREAT|O_EXCL).  True = this tick owns it.  A fresh lock, a lock that
    vanished/was unreadable mid-check, or a lost takeover race all return False (skip the tick)."""
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        fd = os.open(LOCK, flags)
    except (FileExistsError, PermissionError):    # Windows: a delete-pending lock raises EACCES
        try:
            age = time.time() - os.path.getmtime(LOCK)
        except OSError:
            log('supervisor lock vanished or unreadable while checking its age — skipping this tick'); return False
        if age < 5400:
            log('another supervisor tick active (%.0f min) — skipping' % (age / 60)); return False
        log('stale lock (%.0f min) — taking over' % (age / 60))
        # ponytail: two takers can both pass the getmtime check and the second remove() can delete the
        # first taker's fresh lock (remove-then-O_EXCL narrows the window, it does not close it).
        # Task Scheduler runs a single instance, so this is accepted; upgrade to msvcrt/portalocker if not.
        try: os.remove(LOCK)
        except OSError: pass
        try:
            fd = os.open(LOCK, flags)
        except OSError:                           # FileExistsError: someone else won the takeover
            log('lost lock takeover race — skipping this tick'); return False
    try:
        os.write(fd, str(datetime.datetime.now()).encode('utf-8'))
    finally:
        os.close(fd)
    return True

def main():
    if not os.path.exists(TARGETS_F):
        log('no data/stance/collector_targets.json — nothing to supervise'); return
    # SINGLE-TICK LOCK (critical: gates run 30-60 min but the schedule fires every 10 — without
    # this a second tick would start a PARALLEL big5 re-score and DOUBLE-SPEND). Stale after 90
    # min so a crashed tick never wedges the loop forever.
    if not _acquire_lock():
        return
    # Review 2026-07-07 (S1 double-spend): the old design heartbeat the lock only BETWEEN gate steps,
    # so any single step longer than the 90-min stale window (the batch re-score certainly) had its
    # lock stolen by the next tick MID-STEP -> a second concurrent gate -> double batch submission.
    # A daemon thread now refreshes the lock every 60s for the life of the tick; it dies with us, so
    # a genuinely crashed tick still goes stale and recovers as before.
    _hb_stop = threading.Event()
    def _hb():
        while not _hb_stop.wait(60):
            try: os.utime(LOCK, None)
            except Exception: pass
    threading.Thread(target=_hb, daemon=True).start()
    try:
        try:
            _tick()
        except Exception:
            # Task Scheduler discards stderr, so the supervisor log is the only place a crashed tick
            # can say why; re-raise so the exit code still reports it.
            log('TICK CRASHED:\n' + traceback.format_exc())
            raise
    finally:
        _hb_stop.set()
        try: os.remove(LOCK)
        except Exception: pass

def _tick():
    targets = json.load(open(TARGETS_F, encoding='utf-8'))   # {tag: [args...]}
    os.makedirs(SENT, exist_ok=True)
    # TIMED COMPLETION (Paul ruling 2026-08-29). Existence alone is not completion: a sentinel is
    # done-ness with an expiry date, so every tick re-classifies each target by its sentinel's age.
    refire = _refire_load()
    refire_dirty = False
    done, pending, notes = set(), [], []
    for t in targets:
        state, age = sentinel_state(t)
        if state == 'fresh':
            done.add(t)
            if t in refire:      # a committed sentinel ENDS the episode -> next expiry fires at once
                del refire[t]; refire_dirty = True
            continue
        pending.append(t)
        if state == 'stale':
            notes.append('  sentinel stale for %s (%.1f days old, window %.1f days) — refiring'
                         % (t, age / 86400.0, SENTINEL_FRESH_S / 86400.0))
        elif state == 'unreadable':
            notes.append('  sentinel unreadable for %s — treated as stale — refiring' % t)
        else:
            notes.append('  sentinel missing for %s — never completed (or cleared) — firing' % t)
    # A target dropped from collector_targets.json must not leave a fossil counter behind that would
    # back off its FIRST fire if it is ever re-added.
    for gone in [t for t in refire if t not in targets]:
        del refire[gone]; refire_dirty = True
    # CENSUS: classification-only. done/pending come from the sentinels alone, so a target held back
    # by refire backoff below is still counted (correctly) as PENDING on this line.
    log('tick: %d targets, %d done (%s), %d pending' % (len(targets), len(done), ','.join(sorted(done)) or '-', len(pending)))
    for note in notes:
        log(note)
    if refire_dirty:
        _refire_save(refire); refire_dirty = False
    if pending:
        procs = running_procs()
        if procs is None:
            return
        prog = _load_prog()
        now = time.time()
        for tag in pending:
            args = targets[tag]
            # liveness = a python fetch_interviews process whose argument list (everything after
            # fetch_interviews.py) EQUALS this leg's configured args exactly.  Substring or
            # whitespace-token containment is not enough: a live `nprfeed` satisfied the `npr`
            # target, a live `cnn --until ...` satisfied a plain `cnn` target, and daily_media_refresh's
            # `fox --strict` satisfied the `fox` target -- so a dead leg read ALIVE and stayed PENDING
            # forever, and when the wedge timer fired kill_leg terminated the OTHER run's process.
            # Only an exact-args process is ours (wedge detection and kill_leg touch `match` alone).
            # A same-leg process with DIFFERENT args is foreign: it is logged, never relaunched over
            # and never killed.
            want = [str(a) for a in args]
            match, foreign = [], []
            for p in procs:
                got = _leg_args(p['cmd'])
                if got is None:
                    continue
                if got == want:
                    match.append(p)
                elif got and got[:1] == want[:1]:
                    foreign.append(p)
            if not match and foreign:
                log('  %s: running outside the supervisor (pid %s, args %s) — not relaunched, not killed '
                    '(target stays PENDING)' % (tag, ','.join(str(p['pid']) for p in foreign),
                                                ' | '.join(' '.join(_leg_args(p['cmd'])) for p in foreign)))
                continue
            if not match:                                    # dead -> relaunch (original behavior)
                # RATE LIMIT, NOT A GATE BYPASS. The target stays PENDING either way, so this block
                # only decides whether we spend another launch on it this tick; the `return` below
                # still fires and run_gates() is still not reached.
                cooling, fires = _refire_remaining(refire, tag)
                if cooling > 0:
                    log('REFIRE BACKOFF for %s: %d consecutive fire(s) without a sentinel commit; '
                        '%.0f of %.0f backoff minutes remain — NOT relaunched this tick (target '
                        'stays PENDING and the gates stay blocked)'
                        % (tag, fires, cooling / 60.0, _refire_backoff_seconds(fires) / 60.0))
                    continue
                launch_detached(args, tag)
                fires = _refire_record(refire, tag); refire_dirty = True
                if fires > 1:
                    log('  DISTRESS: %s has now been fired %d consecutive times without committing '
                        'a sentinel — next relaunch no sooner than %.0f min from now'
                        % (tag, fires, _refire_backoff_seconds(fires) / 60.0))
                prog[tag] = {'pid': 0, 'db': leg_db_metric(args), 'cpu': 0.0, 'since': now}
                continue
            # ALIVE -> wedge detection. Progress = collector-log rows grew OR the process burned CPU.
            pid = max(match, key=lambda p: p['cpu'])['pid']
            cpu = sum(p['cpu'] for p in match)
            db = leg_db_metric(args)
            prev = prog.get(tag)
            if (not prev) or prev.get('pid') != pid:         # first sight / restarted -> fresh baseline+timer
                prog[tag] = {'pid': pid, 'db': db, 'cpu': cpu, 'since': now}
                log('  %s alive (pid %d) — baseline set' % (tag, pid)); continue
            advanced = (db >= 0 and prev.get('db', -1) >= 0 and db > prev['db']) \
                       or (cpu - prev.get('cpu', 0.0) >= CPU_EPS)
            if advanced:
                log('  %s alive, progressing (db %s->%s, cpu +%.1f)' % (tag, prev.get('db'), db, cpu - prev.get('cpu', 0.0)))
                prog[tag] = {'pid': pid, 'db': db, 'cpu': cpu, 'since': now}   # reset the stall timer
            else:
                stalled = now - prev.get('since', now)
                if stalled >= STALL_SECONDS:
                    log('  %s WEDGED: flat %.0f min (db %s, cpu %.1f) — kill+relaunch' % (tag, stalled / 60, db, cpu))
                    kill_leg(match, tag); time.sleep(2); launch_detached(args, tag)
                    prog[tag] = {'pid': 0, 'db': db, 'cpu': 0.0, 'since': now}
                else:                                        # keep baseline so the timer accumulates
                    log('  %s alive but flat %.0f/%.0f min (watching)' % (tag, stalled / 60, STALL_SECONDS / 60))
        _save_prog(prog)
        if refire_dirty:
            _refire_save(refire)
        return   # gates only after everything is done
    # all collectors done -> gates
    run_gates()
    if _gate_receipt_valid('GATE-2'):
        log('all gates complete — unregistering scheduled task')
        try:
            subprocess.run(['schtasks', '/Delete', '/TN', 'MoneyballCollectorSupervisor', '/F'],
                           capture_output=True, text=True, timeout=60)
        except Exception as e:
            log('unregister failed (%s) — delete MoneyballCollectorSupervisor manually' % str(e)[:60])

if __name__ == '__main__':
    main()
